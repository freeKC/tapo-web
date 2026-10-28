"""tapo_media.py -- the camera's media port (8800): pull an SD-card recording.

The media port speaks an HTTP-like, long-lived ``POST /stream`` exchange:

  * Digest auth (realm "TP-Link IP-Camera"). The challenge carries
    ``encrypt_type="3"`` -> the password fed to the digest is the UPPER-case hex
    SHA-256 of the cloud password (MD5 on older firmware). Username ``admin``.
  * The 200 reply carries ``Key-Exchange: ... nonce="..." username="..."``; every
    encrypted part is AES-128-CBC/PKCS7 with
    ``key = md5(nonce ":" hashed_pwd)``, ``iv = md5(username ":" nonce)``
    (the cipher restarts from that IV for every part).
  * Both directions are ``multipart/mixed``: we send parts delimited by
    ``--client-stream-boundary--``, the camera answers with
    ``--device-stream-boundary--`` parts (JSON control + ``video/mp2t`` data).
  * Flow control: we announce ``X-Data-Window-Size: 50`` and acknowledge every
    25th data part (``X-Data-Received: <seq>``), like the official app; without
    acks the camera stalls once the window is full.

Two ways to get a recording [start, end] (epoch seconds, from the control API):

  * ``{"download": {...}, "method": "get"}``  <- what the official app's download
    button sends, and what we use. Delivered as fast as the link allows (~10x
    realtime over Wi-Fi), stops exactly at ``end`` with a ``stream_status:
    finished`` notification. With ``media_type: 2`` and only a ``start_time`` the
    same request returns the recording's snapshot as one ``image/jpeg`` part.
  * ``{"playback": {...}, "method": "get"}``  <- the player path (pytapo uses it):
    paced at 1x realtime (``scale`` > 1 is a keyframe-only fast-forward) and it
    does NOT stop at ``end`` - it rolls on into the next recordings.

Either way the payload is MPEG-TS: H.264 or H.265 video + G.711 mono audio, in
Tapo's private stream types 0x90 (A-law) or 0x91 (u-law), at 8 or 16 kHz. ffmpeg
knows neither type, so ``ClipDemuxer`` splits the audio PES out and ffmpeg is
fed the video TS + the raw G.711 samples. The C510W sends A-law at 8 kHz; the
C310 u-law; the two-lens C575D u-law at 16 kHz, in a program of its own.

Wire format first documented by pytapo's media_stream; the download request,
ack cadence and stop message come from the official app (GetDownloadRequest,
zb1/b.java). This is a lean synchronous client suited to piping into ffmpeg.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import uuid
from typing import Callable, Iterator

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

CLIENT_BOUNDARY = b"--client-stream-boundary--"
DEFAULT_DEVICE_BOUNDARY = b"--device-stream-boundary--"
TS_PACKET = 188


class MediaError(Exception):
    """``code`` = the camera's stream-layer error code, when it sent one."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# The camera serves very few media sessions at once; these mean "busy, retry later".
BUSY_CODES = {-52405: "trop de requêtes", -52407: "trop de clients", -52417: "session de lecture occupée",
              -52435: "sessions de lecture saturées"}


def _parse_headers(block: bytes) -> dict[str, str]:
    out = {}
    for line in block.split(b"\r\n"):
        if b":" in line:
            k, v = line.split(b":", 1)
            out[k.strip().decode("latin-1").lower()] = v.strip().decode("latin-1")
    return out


def _parse_kv(s: str, sep: str) -> dict[str, str]:
    """'a="1", b=2' -> {"a": "1", "b": "2"} (values may be quoted)."""
    out = {}
    for item in s.split(sep):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


class MediaSession:
    """One authenticated connection to the camera's media port."""

    def __init__(self, host: str, cloud_password: str, port: int = 8800,
                 username: str = "admin", window: int = 50, timeout: float = 15.0):
        # window/ack cadence = the official app's (50 / every 25th part)
        self.host, self.port = host, port
        self.username = username
        self.cloud_password = cloud_password
        self.window = window
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._buf = b""
        self._boundary = DEFAULT_DEVICE_BOUNDARY
        self._key = self._iv = None
        self.session_id: str | None = None
        self._ack_every = max(1, window // 2)
        self._seq = 0

    # -- socket helpers -------------------------------------------------------
    def _read_until(self, marker: bytes) -> bytes:
        while True:
            i = self._buf.find(marker)
            if i >= 0:
                out, self._buf = self._buf[:i], self._buf[i + len(marker):]
                return out
            chunk = self.sock.recv(65536)
            if not chunk:
                raise MediaError("connexion média fermée par la caméra")
            self._buf += chunk

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise MediaError("connexion média fermée par la caméra")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._buf = b""

    def _send_request_head(self, authorization: str | None):
        head = [b"POST /stream HTTP/1.1",
                b"Content-Type: multipart/mixed;boundary=" + CLIENT_BOUNDARY,
                b"Connection: keep-alive",
                b"Content-Length: -1"]
        if authorization:
            head.append(b"Authorization: " + authorization.encode())
        self.sock.sendall(b"\r\n".join(head) + b"\r\n\r\n")

    def _read_response_head(self) -> tuple[int, dict[str, str]]:
        block = self._read_until(b"\r\n\r\n")
        status_line, _, rest = block.partition(b"\r\n")
        try:
            status = int(status_line.split()[1])
        except (IndexError, ValueError) as e:
            raise MediaError(f"réponse média invalide: {status_line[:60]!r}") from e
        return status, _parse_headers(rest)

    # -- open: digest auth + key exchange ------------------------------------
    def open(self) -> "MediaSession":
        self._connect()
        self._send_request_head(None)
        status, hdr = self._read_response_head()
        if status != 401 or "www-authenticate" not in hdr:
            raise MediaError(f"défi d'authentification attendu, reçu HTTP {status}")
        chal = _parse_kv(hdr["www-authenticate"].split(" ", 1)[1], ",")
        digest = hashlib.sha256 if chal.get("encrypt_type") == "3" else hashlib.md5
        hashed_pwd = digest(self.cloud_password.encode()).hexdigest().upper()
        # The camera answers the 401 with "Connection: close" -> reconnect.
        self.close()
        self._connect()

        cnonce = "".join(random.choice("0123456789abcdef") for _ in range(24))
        nc, qop, uri = "00000001", "auth", "/stream"
        ha1 = hashlib.md5(f"{self.username}:{chal['realm']}:{hashed_pwd}".encode()).hexdigest()
        ha2 = hashlib.md5(f"POST:{uri}".encode()).hexdigest()
        response = hashlib.md5(
            f"{ha1}:{chal['nonce']}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
        auth = (f'Digest username="{self.username}",realm="{chal["realm"]}",uri="{uri}",'
                f'algorithm=MD5,nonce="{chal["nonce"]}",nc={nc},cnonce="{cnonce}",qop={qop},'
                f'response="{response}",opaque="{chal.get("opaque", "")}"')
        self._send_request_head(auth)
        status, hdr = self._read_response_head()
        if status == 401:
            raise MediaError("authentification média refusée (mot de passe cloud ?)")
        if status != 200:
            raise MediaError(f"port média: HTTP {status}")
        if "key-exchange" not in hdr:
            raise MediaError("en-tête Key-Exchange absent")
        for piece in hdr.get("content-type", "").split(";"):
            if piece.strip().startswith("boundary="):
                self._boundary = piece.strip()[len("boundary="):].encode()
        kx = _parse_kv(hdr["key-exchange"], " ")
        nonce, kx_user = kx["nonce"].encode(), kx.get("username", self.username).encode()
        self._key = hashlib.md5(nonce + b":" + hashed_pwd.encode()).digest()
        self._iv = hashlib.md5(kx_user + b":" + nonce).digest()
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # -- send -----------------------------------------------------------------
    def _send_part(self, headers: dict[str, str], body: bytes):
        head = CLIENT_BOUNDARY + b"\r\n" + b"".join(
            f"{k}: {v}\r\n".encode() for k, v in headers.items()) + b"\r\n"
        self.sock.sendall(b"--" + head + body + b"\r\n")

    def request(self, params: dict, with_session: bool = False):
        """Send ``{"type":"request","seq":n,"params":params}`` as one JSON part."""
        self._seq += 1
        body = json.dumps({"type": "request", "seq": self._seq, "params": params},
                          separators=(",", ":")).encode()
        headers = {"X-Data-Window-Size": str(self.window), "Content-Type": "application/json"}
        if with_session and self.session_id is not None:
            headers["X-Session-Id"] = self.session_id
        headers["Content-Length"] = str(len(body))
        self._send_part(headers, body)

    def stop(self):
        """Tell the camera to end the stream (frees its session slot); best effort."""
        if self.sock is None or self.session_id is None:
            return
        try:
            self.request({"stop": "null", "method": "do"}, with_session=True)
        except OSError:
            pass

    def _ack(self, session_id: str, received: int):
        body = b'{"type":"notification","params":{"event_type":"stream_sequence"}}'
        self._send_part({"X-Data-Received": str(received), "X-Session-Id": session_id,
                         "Content-Type": "application/json", "Content-Length": str(len(body))}, body)

    # -- receive --------------------------------------------------------------
    def parts(self) -> Iterator[tuple[str, dict[str, str], bytes]]:
        """Yield (mimetype, headers, plaintext) for every part; acks data windows."""
        while True:
            self._read_until(self._boundary)
            hdr = _parse_headers(self._read_until(b"\r\n\r\n"))
            data = self._read_exact(int(hdr.get("content-length", "0")))
            if hdr.get("x-if-encrypt", "0").strip() == "1" and data:
                cipher = AES.new(self._key, AES.MODE_CBC, iv=self._iv)
                try:
                    data = unpad(cipher.decrypt(data), 16)
                except ValueError as e:
                    raise MediaError("déchiffrement média impossible (mot de passe cloud ?)") from e
            sid, seq = hdr.get("x-session-id"), hdr.get("x-data-sequence")
            if sid is not None:
                self.session_id = sid
            if sid is not None and seq is not None and int(seq) and int(seq) % self._ack_every == 0:
                self._ack(sid, int(seq))
            yield hdr.get("content-type", ""), hdr, data


# --------------------------------------------------------------------------- #
#  MPEG-TS: split video (pass-through TS) from the G.711 audio PES
# --------------------------------------------------------------------------- #
def _pes_pts(p: bytes) -> int:
    return (((p[0] >> 1) & 7) << 30) | (p[1] << 22) | ((p[2] >> 1) << 15) | (p[3] << 7) | (p[4] >> 1)


_AUDIO_TYPES = {0x90: "alaw", 0x91: "mulaw"}     # Tapo's private G.711 stream types


def _psi_section(payload: bytes) -> bytes:
    """The table section starting in a PSI packet (after its pointer field)."""
    return payload[1 + payload[0]:] if payload else b""


class ClipDemuxer:
    """Feeds on aligned TS packets; hands video TS and raw G.711 audio to sinks.

    ``on_video(ts_bytes)`` receives every TS packet that is not audio (PAT/PMT/
    video), i.e. a valid TS that ffmpeg reads as video-only. ``on_audio(raw)``
    receives the bare G.711 samples, whose kind (``audio_format``) comes from the
    PMT and whose rate (``audio_rate``) from the audio PTS. Video PTS bookkeeping
    lets the caller bound the clip.

    A/V alignment must NOT be derived from the TS timestamps: on this camera the
    audio and video PTS clocks of a recording can sit seconds apart (+2.2 s, +4.2 s
    observed) although the streams are synchronous - the audio arrives interleaved
    from the first frames. The per-part ``X-Data-PTS`` header (wall clock, ms) is
    the trustworthy reference: pass it as ``wall_ms`` and read ``audio_offset``.
    """

    def __init__(self, on_video: Callable[[bytes], None], on_audio: Callable[[bytes], None]):
        self.on_video, self.on_audio = on_video, on_audio
        self._rest = b""
        self._kind: dict[int, str] = {}      # pid -> "v" | "a"
        self.first_video_pts: int | None = None
        self.last_video_pts: int | None = None
        self.first_audio_pts: int | None = None
        self.first_video_wall: int | None = None
        self.first_audio_wall: int | None = None
        self.audio_bytes = 0
        self.audio_pid: int | None = None
        self._pmt_pids: set[int] = set()
        self._stream_type: dict[int, int] = {}          # elementary pid -> PMT stream_type
        self._audio_first: tuple[int, int] | None = None   # (PTS, audio bytes before that PES)
        self._audio_last: tuple[int, int] | None = None

    @property
    def audio_format(self) -> str | None:
        """ffmpeg's name for the audio samples ("alaw" or "mulaw"), or None when
        the PMT announces an audio type this demuxer cannot pass on raw."""
        st = self._stream_type.get(self.audio_pid)
        if st is None:
            return "alaw"                    # no PMT seen: the C510W's format
        return _AUDIO_TYPES.get(st)

    @property
    def audio_rate(self) -> int | None:
        """8000 or 16000 (Hz), measured: G.711 is one byte per sample. None until
        the audio PTS span 0.4 s (one PES of jitter cannot fool it), or until the
        video says how long it has been."""
        rate = None
        if self._audio_first and self._audio_last:
            (p0, b0), (p1, b1) = self._audio_first, self._audio_last
            dt = ((p1 - p0) & 0x1FFFFFFFF) / 90000.0
            if 0.4 <= dt < 60 and b1 > b0:
                rate = (b1 - b0) / dt
        if rate is None and self.audio_bytes and self.video_seconds >= 1.0:
            rate = self.audio_bytes / self.video_seconds
        if rate is None:
            return None
        return 16000 if rate > 12000 else 8000

    def _read_psi(self, pid: int, payload: bytes):
        sec = _psi_section(payload)
        try:
            length = ((sec[1] & 0x0F) << 8) | sec[2]
            end = min(len(sec), 3 + length - 4)          # minus the CRC
            if pid == 0 and sec[0] == 0x00:              # PAT: one PMT per program
                for i in range(8, end - 3, 4):
                    if (sec[i] << 8) | sec[i + 1]:       # program 0 is the network PID
                        self._pmt_pids.add(((sec[i + 2] & 0x1F) << 8) | sec[i + 3])
            elif sec[0] == 0x02:                         # PMT
                i = 12 + (((sec[10] & 0x0F) << 8) | sec[11])
                while i + 5 <= end:
                    es_pid = ((sec[i + 1] & 0x1F) << 8) | sec[i + 2]
                    self._stream_type[es_pid] = sec[i]
                    i += 5 + (((sec[i + 3] & 0x0F) << 8) | sec[i + 4])
        except IndexError:
            pass                                         # a truncated table: ignore it

    @property
    def video_seconds(self) -> float:
        if self.first_video_pts is None:
            return 0.0
        return ((self.last_video_pts - self.first_video_pts) & 0x1FFFFFFFF) / 90000.0

    @property
    def audio_offset(self) -> float:
        """Seconds the first audio sample lags the first video frame, per the
        camera's wall-clock part headers; 0 when unknown or implausible."""
        if self.first_audio_wall is None or self.first_video_wall is None:
            return 0.0
        d = (self.first_audio_wall - self.first_video_wall) / 1000.0
        return d if -0.5 <= d <= 1.0 else 0.0

    def feed(self, data: bytes, wall_ms: int | None = None):
        if self._rest:
            data, self._rest = self._rest + data, b""
        video = bytearray()
        n = len(data) - len(data) % TS_PACKET
        for off in range(0, n, TS_PACKET):
            pk = data[off:off + TS_PACKET]
            if pk[0] != 0x47:
                continue                      # lost sync inside a part: drop the packet
            pid = ((pk[1] & 0x1F) << 8) | pk[2]
            pusi = pk[1] & 0x40
            afc = (pk[3] >> 4) & 3
            pay = 4 + (1 + pk[4] if afc & 2 else 0)
            payload = pk[pay:] if (afc & 1 and pay < TS_PACKET) else b""
            if pusi and (pid == 0 or pid in self._pmt_pids):
                self._read_psi(pid, payload)
            if pusi and payload[:3] == b"\x00\x00\x01":
                stream_id = payload[3]
                kind = "a" if 0xC0 <= stream_id <= 0xDF else "v" if 0xE0 <= stream_id <= 0xEF else None
                if kind:
                    self._kind[pid] = kind
                has_pts = len(payload) >= 14 and payload[7] & 0x80
                if kind == "v" and has_pts:
                    pts = _pes_pts(payload[9:14])
                    if self.first_video_pts is None:
                        self.first_video_pts, self.first_video_wall = pts, wall_ms
                    self.last_video_pts = pts
                elif kind == "a":
                    self.audio_pid = pid if self.audio_pid is None else self.audio_pid
                    if has_pts and self.first_audio_pts is None:
                        self.first_audio_pts, self.first_audio_wall = _pes_pts(payload[9:14]), wall_ms
                    if has_pts:
                        mark = (_pes_pts(payload[9:14]), self.audio_bytes)
                        self._audio_first = self._audio_first or mark
                        self._audio_last = mark
                    payload = payload[9 + payload[8]:]      # strip the PES header
            if self._kind.get(pid) == "a":
                if payload:
                    self.audio_bytes += len(payload)
                    self.on_audio(bytes(payload))
            else:
                video += pk
        self._rest = data[n:]
        if video:
            self.on_video(bytes(video))


PLAYER_ID = uuid.uuid4().hex.upper()      # the app sends its install UUID


def download_params(start: int, end: int | None = None, media_type: int = 0) -> dict:
    """The official app's download request. media_type 0 = video, 2 = snapshot."""
    d = {"client_id": 1, "channels": [0], "media_type": media_type,
         "start_time": str(int(start)), "player_id": PLAYER_ID}
    if end is not None:
        d["end_time"] = str(int(end))
    return {"download": d, "method": "get"}


def _control(data: bytes) -> tuple[bool, int | None]:
    """Decode a JSON control part -> (stream finished?, error code or None)."""
    try:
        msg = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return False, None
    params = msg.get("params") if isinstance(msg, dict) else None
    if not isinstance(params, dict):
        return False, None
    code = params.get("error_code")
    if msg.get("type") == "response" and code not in (0, None):
        return False, int(code)
    finished = params.get("event_type") == "stream_status" and params.get("status") == "finished"
    return finished, None


def fetch_snapshot(sess: MediaSession, start: int) -> bytes | None:
    """The camera's own JPEG for the recording that starts at ``start`` (or None).

    Several snapshots can be fetched back to back on one session, but a session
    is bound to the media type of its first request: never mix with video."""
    sess.request(download_params(start, media_type=2))
    image = None
    for mimetype, _hdr, data in sess.parts():
        if mimetype == "image/jpeg":
            image = data
        elif mimetype == "application/json":
            finished, code = _control(data)
            if finished or code is not None:
                break
    return image


def stream_clip(sess: MediaSession, start: int, end: int, demux: ClipDemuxer, *,
                should_stop: Callable[[], bool] = lambda: False,
                on_data: Callable[[], None] | None = None, overrun: float = 5.0,
                on_bytes: Callable[[int], None] | None = None) -> bool:
    """Pull the recording [start, end] through ``demux``. True = the camera
    signalled a clean end of clip; False = stopped by the caller/overrun guard."""
    limit = float(end - start) + overrun     # safety net: never roll into later recordings
    sess.request(download_params(start, end))
    for mimetype, _hdr, data in sess.parts():
        if should_stop():
            return False
        if mimetype == "video/mp2t":
            if on_bytes:
                on_bytes(len(data))
            wall = _hdr.get("x-data-pts", "")
            demux.feed(data, int(wall) if wall.isdigit() else None)
            if on_data:
                on_data()
            if demux.video_seconds >= limit:
                return False
        elif mimetype == "application/json":
            finished, code = _control(data)
            if code is not None:
                raise MediaError(f"lecture refusée par la caméra (code {code})", code=code)
            if finished:
                return True
    return False


if __name__ == "__main__":      # smoke test: python -m app.tapo_media START END OUT_PREFIX
    import sys
    import time

    from dotenv import dotenv_values

    env = dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env"))
    s, e, prefix = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    t0 = time.time()
    # NB: a media session is bound to the media type of its FIRST request, so the
    # snapshot and the video need separate connections.
    with MediaSession(env["TAPO_HOST"], env["TAPO_CLOUD_PASSWORD"]) as sess:
        jpg = fetch_snapshot(sess, s)
        open(prefix + ".jpg", "wb").write(jpg or b"")
    with open(prefix + ".ts", "wb") as fv, open(prefix + ".g711", "wb") as fa, \
            MediaSession(env["TAPO_HOST"], env["TAPO_CLOUD_PASSWORD"]) as sess:
        st = ClipDemuxer(fv.write, fa.write)
        clean = stream_clip(sess, s, e, st)
        sess.stop()
    rate = st.audio_rate or 8000
    print(f"snapshot {len(jpg or b'')} B; video {st.video_seconds:.1f}s audio {st.audio_bytes / rate:.1f}s "
          f"({st.audio_format} {rate} Hz) offset {st.audio_offset * 1000:.0f}ms clean_end={clean} "
          f"in {time.time() - t0:.1f}s")
