"""Offline tests of the media-port client: a fake Streamd server + synthetic TS."""
import hashlib
import json
import socket
import threading

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from app.tapo_media import ClipDemuxer, MediaSession, fetch_snapshot, stream_clip

PASSWORD = "s3cret;"
HASHED = hashlib.sha256(PASSWORD.encode()).hexdigest().upper()


# ----------------------------------------------------------------- synthetic TS
def ts_packet(pid, payload, pusi=False, cc=0):
    assert len(payload) <= 184
    head = bytes([0x47, (0x40 if pusi else 0) | (pid >> 8), pid & 0xFF])
    stuff = 184 - len(payload)
    if stuff == 0:
        return head + bytes([0x10 | cc]) + payload
    af = bytes([stuff - 1]) + (bytes([0x00]) + b"\xff" * (stuff - 2) if stuff > 1 else b"")
    return head + bytes([0x30 | cc]) + af + payload


def pes(stream_id, pts, data):
    p = bytes([0x21 | ((pts >> 29) & 0x0E), (pts >> 22) & 0xFF, 0x01 | ((pts >> 14) & 0xFE),
               (pts >> 7) & 0xFF, 0x01 | ((pts << 1) & 0xFE)])
    body = bytes([0x80, 0x80, 5]) + p + data
    length = len(body) if stream_id != 0xE0 else 0
    return b"\x00\x00\x01" + bytes([stream_id]) + length.to_bytes(2, "big") + body


def packets(pid, pes_bytes):
    out, first = [], True
    for i in range(0, len(pes_bytes), 184):
        out.append(ts_packet(pid, pes_bytes[i:i + 184], pusi=first, cc=(i // 184) & 15))
        first = False
    return b"".join(out)


def sample_ts(seconds=2, video_pid=0x44, audio_pid=0x45, base=90000 * 5):
    ts, audio = b"", b""
    for i in range(seconds * 10):
        pts = base + i * 9000
        ts += packets(video_pid, pes(0xE0, pts, b"\x00\x00\x00\x01\x41" + bytes([i]) * 300))
        chunk = bytes([0xD5 ^ (i & 1)]) * 800
        audio += chunk
        ts += packets(audio_pid, pes(0xC0, pts + 4500, chunk))
    return ts, audio


def test_demuxer_splits_audio_from_video_and_tracks_pts():
    ts, audio = sample_ts(seconds=2)
    v, a = bytearray(), bytearray()
    d = ClipDemuxer(v.extend, a.extend)
    for i in range(0, len(ts), 188 * 7 + 50):          # deliberately NOT packet aligned
        d.feed(ts[i:i + 188 * 7 + 50], wall_ms=1_789_000_000_000 + i // 100)
    assert bytes(a) == audio and d.audio_bytes == len(audio)
    assert len(v) % 188 == 0 and len(v) > 0
    pids = {((v[i + 1] & 0x1F) << 8) | v[i + 2] for i in range(0, len(v), 188)}
    assert pids == {0x44}                               # no audio packet leaks into the video TS
    assert abs(d.video_seconds - 1.9) < 1e-6


def test_audio_offset_comes_from_wall_clock_headers_not_from_ts_pts():
    """Real recordings carry audio PTS seconds away from video PTS while being in sync."""
    v = packets(0x44, pes(0xE0, 90000 * 1967, b"\x00\x00\x00\x01\x65" + b"v" * 100))
    a = packets(0x45, pes(0xC0, 90000 * 1969 + 19170, b"\xd5" * 800))     # PTS says +2.213 s
    d = ClipDemuxer(lambda b: None, lambda b: None)
    d.feed(v, wall_ms=1789752472000)
    d.feed(a, wall_ms=1789752472040)                                       # header says +40 ms
    assert abs(d.audio_offset - 0.040) < 1e-9
    d2 = ClipDemuxer(lambda b: None, lambda b: None)                       # no headers -> assume in sync
    d2.feed(v + a)
    assert d2.audio_offset == 0.0


def test_demuxer_handles_pts_wraparound():
    near_wrap = (1 << 33) - 9000 * 5
    ts, _ = sample_ts(seconds=1, base=near_wrap)
    d = ClipDemuxer(lambda b: None, lambda b: None)
    d.feed(ts)
    assert abs(d.video_seconds - 0.9) < 1e-6


# ----------------------------------------------------------------- fake Streamd
class FakeStreamd(threading.Thread):
    BOUNDARY = b"--device-stream-boundary--"

    def __init__(self, serve):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.serve = serve
        self.acks, self.requests, self.errors = [], [], []
        self.nonce = "0123456789abcdef0123456789abcdef"

    # -- helpers used by the scenario callbacks
    def send_part(self, conn, ctype, body, extra=None, encrypt=False):
        if encrypt:
            key = hashlib.md5(self.nonce.encode() + b":" + HASHED.encode()).digest()
            iv = hashlib.md5(b"admin:" + self.nonce.encode()).digest()
            body = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(pad(body, 16))
        head = {"Content-Type": ctype, "Content-Length": str(len(body)), "X-If-Encrypt": "1" if encrypt else "0"}
        head.update(extra or {})
        conn.sendall(self.BOUNDARY + b"\r\n" + "".join(f"{k}: {v}\r\n" for k, v in head.items()).encode()
                     + b"\r\n" + body + b"\r\n")

    def read_client_part(self, f):
        line = f.readline()
        while line and not line.startswith(b"----client-stream-boundary--"):
            line = f.readline()
        if not line:
            return None, None
        headers = {}
        while True:
            h = f.readline().strip()
            if not h:
                break
            k, v = h.split(b":", 1)
            headers[k.decode().lower()] = v.strip().decode()
        body = f.read(int(headers["content-length"]))
        return headers, body

    def run(self):
        try:
            conn, _ = self.srv.accept()                 # 1st connection: challenge
            conn.recv(4096)
            conn.sendall(b'HTTP/1.0 401 Unauthorized\r\nWWW-Authenticate: Digest realm="TP-Link IP-Camera",'
                         b'algorithm="MD5",encrypt_type="3",qop="auth",nonce="abc123",opaque="op"\r\n'
                         b"Connection: close\r\n\r\n")
            conn.close()
            conn, _ = self.srv.accept()                 # 2nd connection: authenticated
            f = conn.makefile("rb")
            head = b""
            while not head.endswith(b"\r\n\r\n"):
                head += f.read(1)
            auth = dict(kv.strip().split("=", 1) for kv in
                        head.decode().split("Authorization: Digest ")[1].split("\r\n")[0].split(","))
            auth = {k: v.strip('"') for k, v in auth.items()}
            ha1 = hashlib.md5(f"admin:TP-Link IP-Camera:{HASHED}".encode()).hexdigest()
            ha2 = hashlib.md5(b"POST:/stream").hexdigest()
            good = hashlib.md5(f"{ha1}:abc123:{auth['nc']}:{auth['cnonce']}:auth:{ha2}".encode()).hexdigest()
            if auth["response"] != good:
                conn.sendall(b"HTTP/1.0 401 Unauthorized\r\n\r\n")
                return
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace;boundary="
                         + self.BOUNDARY + b'\r\nKey-Exchange: cipher="AES_128_CBC" username="admin" padding="PKCS7_16" '
                         b'algorithm="MD5" nonce="' + self.nonce.encode() + b'"\r\n\r\n')
            self.serve(self, conn, f)
        except Exception as e:  # noqa: BLE001 - surfaced by the test
            self.errors.append(repr(e))


def test_download_flow_auth_decrypt_acks_and_finish():
    ts, audio = sample_ts(seconds=3)
    chunks = [ts[i:i + 188 * 5] for i in range(0, len(ts), 188 * 5)]
    assert len(chunks) > 26                             # enough parts to require an ack

    def serve(srv, conn, f):
        hdr, body = srv.read_client_part(f)
        srv.requests.append((hdr, json.loads(body)))
        srv.send_part(conn, "application/json", b'{"type":"response","seq":1,"params":{"error_code":0,"session_id":"7"}}')
        for seq, chunk in enumerate(chunks):
            srv.send_part(conn, "video/mp2t", chunk, {"X-Session-Id": "7", "X-Data-Sequence": str(seq)}, encrypt=True)
            if seq == 25:                               # window half reached: the client must ack
                ah, _ = srv.read_client_part(f)
                srv.acks.append(ah)
        srv.send_part(conn, "application/json",
                      b'{"type":"notification","params":{"event_type":"stream_status","status":"finished"}}',
                      {"X-Session-Id": "7"})
        hdr, body = srv.read_client_part(f)             # the polite stop
        srv.requests.append((hdr, json.loads(body)))

    srv = FakeStreamd(serve)
    srv.start()
    v, a = bytearray(), bytearray()
    demux = ClipDemuxer(v.extend, a.extend)
    with MediaSession("127.0.0.1", PASSWORD, port=srv.port, timeout=5) as sess:
        clean = stream_clip(sess, 1000, 1003, demux)
        sess.stop()
    srv.join(5)
    assert srv.errors == []
    assert clean is True and bytes(a) == audio
    hdr, req = srv.requests[0]
    assert hdr["x-data-window-size"] == "50"
    dl = req["params"]["download"]
    assert req["params"]["method"] == "get" and dl["media_type"] == 0
    assert (dl["start_time"], dl["end_time"]) == ("1000", "1003")
    assert srv.acks and srv.acks[0]["x-data-received"] == "25" and srv.acks[0]["x-session-id"] == "7"
    stop_hdr, stop = srv.requests[1]
    assert stop["params"] == {"stop": "null", "method": "do"} and stop_hdr["x-session-id"] == "7"


def test_snapshot_fetch_returns_jpeg():
    jpeg = b"\xff\xd8" + b"J" * 5000 + b"\xff\xd9"

    def serve(srv, conn, f):
        hdr, body = srv.read_client_part(f)
        srv.requests.append((hdr, json.loads(body)))
        srv.send_part(conn, "application/json", b'{"type":"response","seq":1,"params":{"error_code":0,"session_id":"9"}}')
        srv.send_part(conn, "image/jpeg", jpeg, {"X-Session-Id": "9"}, encrypt=True)
        srv.send_part(conn, "application/json",
                      b'{"type":"notification","params":{"event_type":"stream_status","status":"finished"}}')

    srv = FakeStreamd(serve)
    srv.start()
    with MediaSession("127.0.0.1", PASSWORD, port=srv.port, timeout=5) as sess:
        got = fetch_snapshot(sess, 1234)
    srv.join(5)
    assert srv.errors == [] and got == jpeg
    dl = srv.requests[0][1]["params"]["download"]
    assert dl["media_type"] == 2 and dl["start_time"] == "1234" and "end_time" not in dl
