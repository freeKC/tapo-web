"""SD-card recordings: list them over the camera's local control API ("V4",
see tapo_v4.py) and pull them over the media port (see tapo_media.py).

A fetch job pulls one recording with the camera's "download" stream request
(~10x realtime over Wi-Fi) and pipes it into a single ffmpeg that writes, at the
same time,

  * an HLS *event* playlist  -> the browser starts playing after a second or two,
    while the rest of the clip is still arriving (matters for long recordings),
  * a faststart MP4           -> kept in ``recordings/sd/`` as the local copy
    (instant replay with seeking, and the file served by "download").

Thumbnails are the camera's own per-recording snapshots, fetched in batches on
one media session and cached on disk.

Media sessions are strictly serialized (one at a time): the camera is fragile
under load. Nothing here ever writes to or deletes from the SD card.
"""
from __future__ import annotations

import datetime as dt
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import camera, discovery
from .control import create_control
from .tapo_media import (BUSY_CODES, PLAYER_ID, ClipDemuxer, MediaError, MediaSession,
                         fetch_snapshot, stream_clip)
from .tapo_v4 import TapoV4, TapoV4Error

_ID_RE = re.compile(r"^(\d{9,11})-(\d{9,11})$")
_DATE_RE = re.compile(r"^\d{8}$")
MAX_CLIP_SECONDS = 4 * 3600
HLS_KEEP_SECONDS = 600          # keep a finished clip's HLS around this long after last access
# PlayBackEventType of the official app ("video_type"/"vedio_type" of a recording)
VIDEO_TYPES = {1: "continu", 2: "mouvement", 3: "sabotage", 4: "franchissement de ligne",
               5: "intrusion de zone", 6: "personne", 7: "pleurs de bébé", 8: "véhicule",
               9: "animal", 10: "sonnette", 11: "aboiement", 12: "miaulement", 13: "bris de verre",
               14: "alarme fumée", 33: "animal"}


class SdError(Exception):
    """User-facing failure (message is shown in the UI).
    ``code`` = the camera's error code when the control API answered, else None."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
#  Clip ids and files (no camera needed)
# --------------------------------------------------------------------------- #
def parse_id(clip_id: str) -> tuple[int, int]:
    m = _ID_RE.match(clip_id or "")
    if not m:
        raise SdError("Identifiant de clip invalide.")
    start, end = int(m.group(1)), int(m.group(2))
    if not 0 < end - start <= MAX_CLIP_SECONDS:
        raise SdError("Durée de clip invalide.")
    return start, end


def _size_bytes(s: str | None) -> int | None:
    m = re.match(r"^(\d+)B$", s or "")
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
#  One camera's SD card
# --------------------------------------------------------------------------- #
class SdCard:
    """Everything the SD-card features need for one camera: its control-API
    session, a small cache (the camera dislikes being polled), the thumbnail
    worker, the fetch queue, its folders and its media-session lock.

    Media sessions are strictly serialized per camera (one at a time): a camera
    is fragile under load. Two cameras do not wait for each other."""

    def __init__(self, cam):
        self.cam = cam
        self.dir = cam.recordings_dir / "sd"
        self.thumbs_dir = self.dir / ".thumbs"
        self.part_dir = self.dir / ".part"
        self.hls_dir = cam.hls_dir / "sdplay"
        for d in (self.dir, self.thumbs_dir, self.part_dir, self.hls_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.media_lock = threading.Lock()      # clips and thumbnails alike
        self.clip_waiting = threading.Event()   # a clip fetch wants media_lock (threading.Lock is
                                                # not FIFO: without this the thumbnail worker
                                                # re-takes it at once)
        self._client_lock = threading.Lock()
        self._client: TapoV4 | None = None
        self._user_id: int | None = None
        self._cache: dict[str, tuple[float, object]] = {}
        self._cache_lock = threading.Lock()
        self._tz: ZoneInfo | None = None
        # local copies counter: counted once in the background (thousands of files on a
        # network or WSL drive take a minute), then kept up to date as files come and go
        self._usage: dict | None = None
        self._usage_lock = threading.Lock()
        self._usage_counting = False
        self.fetcher = Fetcher(self)
        self.thumbs = _Thumbs(self)

    # -- control API session (shared, re-login handled inside) ---------------
    def host(self) -> str:
        st = camera.locate(self.cam)
        if not st.online or not st.host:
            raise SdError("Caméra hors ligne.")
        return st.host

    def reset(self) -> None:
        """Credentials or protocol changed: the next call logs in again."""
        with self._client_lock:
            self._client, self._user_id = None, None

    def _learnt(self, protocol: str) -> None:
        self.cam.registry.remember(self.cam, detected=protocol)

    def api(self) -> TapoV4:
        host = self.host()
        if not self.cam.cloud_password:
            raise SdError("Mot de passe du compte TP-Link manquant (réglages de la caméra).")
        with self._client_lock:
            if self._client is None or self._client.host != host:
                cfg = self.cam.cfg
                hint = cfg.detected or None
                if cfg.protocol == "auto" and not hint:
                    hint = (discovery.probe(host) or {}).get("protocol_hint") or None
                self._client = create_control(host, self.cam.cloud_password, username="admin",
                                              protocol=cfg.protocol, hint=hint, on_detect=self._learnt)
                self._user_id = None
            return self._client

    def call(self, fn):
        """Run a control-API call, mapping low-level failures to SdError."""
        try:
            return fn(self.api())
        except TapoV4Error as e:
            if e.code in (-40404, -40408):
                raise SdError("La caméra a temporairement bloqué les connexions (trop d'essais). "
                              "Réessayez dans quelques minutes.") from e
            raise SdError(f"API de contrôle : {e}", code=e.code) from e
        except OSError as e:          # requests' exceptions derive from OSError
            camera.invalidate(self.cam)
            raise SdError(f"Caméra injoignable : {e.__class__.__name__}") from e

    def _uid(self) -> int:
        if self._user_id is None:
            self._user_id = int(self.call(lambda c: c.get_user_id()))
        return self._user_id

    def cached(self, key: str, ttl: float, producer, force: bool = False):
        now = time.time()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and not force and now - hit[0] < ttl:
                return hit[1]
        value = producer()
        with self._cache_lock:
            self._cache[key] = (time.time(), value)
        return value

    def zone(self):
        """The camera's own time zone: the day boundaries of its listings use it."""
        if self._tz is None:
            try:
                self._tz = ZoneInfo(self.call(lambda c: c.get_clock()).get("zone_id") or "")
            except Exception:  # noqa: BLE001 - any failure -> server-local time
                return None
        return self._tz

    def local(self, ts: int) -> dt.datetime:
        return dt.datetime.fromtimestamp(ts, tz=self.zone())

    # -- status / listing -----------------------------------------------------
    def status(self, force: bool = False) -> dict:
        try:                                  # only a successful card read is cached
            card = self.cached("card", 30.0, lambda: self.call(lambda c: c.get_sd_status()), force)
        except SdError as e:
            return {"available": False, "reason": str(e)}
        if not card:
            return {"available": False, "reason": "Aucune carte SD détectée par la caméra."}
        ok = card.get("status") == "normal"
        return {
            "available": ok,
            "reason": None if ok else f"État de la carte : {card.get('status')}",
            "card": {
                "status": card.get("status"),
                "total": _size_bytes(card.get("video_total_space_accurate")),
                "free": _size_bytes(card.get("video_free_space_accurate")),
                "loop": card.get("loop_record_status") == "1",
                "oldest": int(card["record_start_time"]) if str(card.get("record_start_time", "")).isdigit() else None,
            },
            "cached": self.cache_usage(),
        }

    def days(self, force: bool = False) -> dict:
        def produce():
            today = dt.datetime.now(tz=self.zone()).date()
            found = self.call(lambda c: c.search_days((today - dt.timedelta(days=730)).strftime("%Y%m%d"),
                                                      (today + dt.timedelta(days=1)).strftime("%Y%m%d")))
            return sorted(d for d in found if _DATE_RE.match(d))
        return {"days": self.cached("days", 120.0, produce, force)}

    def day_bounds(self, date: str) -> tuple[int, int]:
        day = dt.datetime.strptime(date, "%Y%m%d").replace(tzinfo=self.zone())
        return int(day.timestamp()), int((day + dt.timedelta(days=1)).timestamp()) - 1

    def list_day(self, date: str) -> list[dict]:
        lo, hi = self.day_bounds(date)
        try:                                  # what the app uses on this firmware (DST-proof)
            found = self.call(lambda c: c.search_videos_utc(lo, hi, PLAYER_ID))
            if found:
                return found
        except SdError as e:
            # fall back only if the camera refuses the METHOD; offline / lockout / dead
            # session must not trigger more logins through a second attempt
            if e.code is None or e.code in (-40401, -40404, -40408, -40421):
                raise
        uid = self._uid()                     # legacy listing by camera-local date
        return self.call(lambda c: c.get_recordings(date, user_id=uid))

    def recordings(self, date: str, force: bool = False) -> dict:
        if not _DATE_RE.match(date or ""):
            raise SdError("Date invalide (attendu : AAAAMMJJ).")
        try:
            day_start, _ = self.day_bounds(date)
        except ValueError:
            raise SdError("Date invalide (attendu : AAAAMMJJ).")
        raw = self.cached(f"rec:{date}", 20.0, lambda: self.list_day(date), force)
        clips = []
        for r in raw:
            try:
                start, end = int(r["startTime"]), int(r["endTime"])
                vt = int(r.get("video_type") or r.get("vedio_type") or 2)
            except (KeyError, TypeError, ValueError):
                continue
            if not 0 < end - start <= MAX_CLIP_SECONDS:
                continue
            cid = f"{start}-{end}"
            path = self.mp4_path(start, end)
            clips.append({
                "id": cid, "start": start, "end": end, "duration": end - start,
                "time": self.local(start).strftime("%H:%M:%S"),
                "time_end": self.local(end).strftime("%H:%M:%S"),
                # wall-clock position on the 24 h axis (NOT elapsed time: 23 h / 25 h DST days)
                "day_second": (lambda t: t.hour * 3600 + t.minute * 60 + t.second)(self.local(start))
                              if start >= day_start else 0,
                "type": vt, "type_label": VIDEO_TYPES.get(vt, f"type {vt}"),
                "cached": path.is_file(),
                "size": path.stat().st_size if path.is_file() else None,
                "job": self.fetcher.snapshot(cid),
            })
        clips.sort(key=lambda c: c["start"])
        return {"date": date, "clips": clips}

    # -- local copies ---------------------------------------------------------
    def mp4_path(self, start: int, end: int) -> Path:
        return self.dir / f"sd_{start}_{end}.mp4"

    def local_file(self, clip_id: str) -> Path | None:
        p = self.mp4_path(*parse_id(clip_id))
        return p if p.is_file() else None

    def download_name(self, clip_id: str) -> str:
        start, end = parse_id(clip_id)
        return f"tapo_sd_{self.local(start).strftime('%Y-%m-%d_%H-%M-%S')}_{end - start}s.mp4"

    def thumbnail(self, clip_id: str) -> Path | None:
        start, _end = parse_id(clip_id)
        return self.thumbs.get(start)

    def delete_local(self, clip_id: str) -> bool:
        """Remove OUR copy only. The recording on the camera's SD card is untouched."""
        p = self.local_file(clip_id)
        if not p:
            return False
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        p.unlink(missing_ok=True)
        self._usage_change(-1, -size)
        job = self.fetcher.get(clip_id)
        if job is None or not job.active:
            shutil.rmtree(self.hls_dir / clip_id, ignore_errors=True)
        return True

    def _count_usage(self) -> None:
        count = size = 0
        try:
            with os.scandir(self.dir) as it:
                for e in it:
                    if e.name.startswith("sd_") and e.name.endswith(".mp4") and e.is_file():
                        count += 1
                        size += e.stat().st_size
        except OSError:
            pass
        with self._usage_lock:
            self._usage, self._usage_counting = {"count": count, "bytes": size}, False

    def cache_usage(self) -> dict:
        """Local copies: count and bytes, or None for both while the first count runs."""
        with self._usage_lock:
            if self._usage is not None:
                return dict(self._usage)
            if not self._usage_counting:
                self._usage_counting = True
                threading.Thread(target=self._count_usage, daemon=True).start()
        return {"count": None, "bytes": None}

    def _usage_change(self, count: int, size: int) -> None:
        with self._usage_lock:
            if self._usage is not None:
                self._usage["count"] = max(0, self._usage["count"] + count)
                self._usage["bytes"] = max(0, self._usage["bytes"] + size)

    # -- lifecycle ------------------------------------------------------------
    def cleanup(self) -> None:
        """Server start: leftovers of a previous run (HLS output, partial MP4s)."""
        shutil.rmtree(self.hls_dir, ignore_errors=True)
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        for f in self.part_dir.glob("*.mp4"):
            f.unlink(missing_ok=True)

    def stop_all(self) -> None:
        self.fetcher.stop_all()


def card_for(cam) -> SdCard:
    return cam.service("sd", lambda: SdCard(cam))


class _Thumbs:
    """Per-recording snapshots straight from the camera (640x360 JPEG), cached on
    disk. Requests are served in batches over ONE media session: opening a session
    costs ~1.5 s, each further snapshot ~0.3 s."""

    BATCH = 24                 # snapshots per media session
    LINGER = 1.0               # keep the session open this long for follow-up requests
    RETRY_AFTER = 120.0

    def __init__(self, card: SdCard):
        self.card = card
        self._lock = threading.Lock()
        self._waiters: dict[int, threading.Event] = {}
        self._failed: dict[int, float] = {}      # start -> do not retry before (epoch)
        self._q: queue.Queue[int] = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def path(self, start: int) -> Path:
        return self.card.thumbs_dir / f"cam_{start}.jpg"

    def get(self, start: int, timeout: float = 8.0) -> Path | None:
        """The cached snapshot, fetching it if needed. None = not available *now*
        (the HTTP layer answers 503 and the browser retries): never park an HTTP
        worker for long - while a clip is being fetched the camera is busy anyway."""
        p = self.path(start)
        if p.is_file():
            return p
        with self._lock:
            if time.time() < self._failed.get(start, 0):
                return None
            ev = self._waiters.get(start)
            if ev is None:
                ev = self._waiters[start] = threading.Event()
                self._q.put(start)
        deadline = time.monotonic() + timeout
        while not ev.is_set() and not self.card.fetcher.busy() and time.monotonic() < deadline:
            ev.wait(0.25)                 # the snapshot stays queued; the page asks again later
        return p if p.is_file() else None

    def _done(self, start: int, retry_in: float | None):
        with self._lock:
            if retry_in is not None:
                if len(self._failed) > 2000:
                    self._failed.clear()
                self._failed[start] = time.time() + retry_in
            ev = self._waiters.pop(start, None)
        if ev:
            ev.set()

    def _loop(self):
        card = self.card
        while True:
            start = self._q.get()
            batch_left = self.BATCH
            try:
                while card.clip_waiting.is_set():     # a clip fetch goes first
                    time.sleep(0.1)
                with card.media_lock, MediaSession(card.host(), card.cam.cloud_password, timeout=8.0) as sess:
                    while start is not None:
                        jpg = fetch_snapshot(sess, start)
                        if jpg:
                            tmp = self.path(start).with_suffix(".tmp")
                            tmp.write_bytes(jpg)
                            os.replace(tmp, self.path(start))
                        self._done(start, None if jpg else self.RETRY_AFTER)
                        start, batch_left = None, batch_left - 1
                        if batch_left > 0 and not card.clip_waiting.is_set():
                            try:
                                start = self._q.get(timeout=self.LINGER)
                            except queue.Empty:
                                pass
            except Exception:  # noqa: BLE001 - camera offline, auth, timeout...
                if start is not None:
                    self._done(start, 15.0)
                time.sleep(2.0)


# --------------------------------------------------------------------------- #
#  Fetch jobs
# --------------------------------------------------------------------------- #
class _AudioSocket:
    """A local audio pipe for Windows, where pass_fds is unavailable.

    Accept in the pump thread: ffmpeg probes video before opening audio.
    """

    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(30)
        self.url = f"tcp://127.0.0.1:{self.listener.getsockname()[1]}"
        self.connection = None

    def write(self, chunk):
        if not chunk:
            return
        if self.connection is None:
            self.connection, _ = self.listener.accept()
            self.connection.settimeout(30)
            self.listener.close()
        self.connection.sendall(chunk)

    def close(self):
        self.listener.close()
        if self.connection is not None:
            self.connection.close()


class _Pump(threading.Thread):
    """Drains a queue into a pipe so the network reader never blocks on ffmpeg."""

    def __init__(self, fd_or_file):
        super().__init__(daemon=True)
        self.q: queue.Queue[bytes | None] = queue.Queue(maxsize=256)   # back-pressure, <= ~16 MB
        self.f = fd_or_file
        self.broken = False

    def run(self):
        while True:
            chunk = self.q.get()
            if chunk is None:
                break
            if self.broken:
                continue
            try:
                self.f.write(chunk)
            except (BrokenPipeError, ValueError, OSError):
                self.broken = True
        try:
            self.f.close()
        except OSError:
            pass


CORRUPT_MESSAGE = "enregistrement corrompu sur la carte SD (vidéo illisible)"


def mp4_is_playable(path: Path) -> bool:
    """A quick ffprobe: at least one video stream with a duration."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=codec_name:format=duration",
                              "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return True                           # cannot tell: do not throw the file away
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    try:
        return out.returncode == 0 and len(lines) >= 2 and float(lines[-1]) > 0
    except ValueError:
        return False


class _Mux:
    """One ffmpeg per clip: video TS on stdin + raw A-law on an extra pipe ->
    HLS event playlist (play while fetching) and the faststart MP4, no video
    re-encode. Data sent before start() is buffered (pre-roll)."""

    def __init__(self, job: "Job", part: Path):
        self.job, self.part = job, part
        self.proc: subprocess.Popen | None = None
        self.pumps: list[_Pump] = []
        self.log = job.hls_dir / "ffmpeg.log"
        self._pre = {"v": bytearray(), "a": bytearray()}
        self._sink = {"v": self._pre["v"].extend, "a": self._pre["a"].extend}

    @property
    def started(self) -> bool:
        return self.proc is not None

    def video(self, b: bytes):
        self._sink["v"](b)

    def audio(self, b: bytes):
        self._sink["a"](b)

    def start(self, has_audio: bool, audio_offset: float):
        job = self.job
        # Bound probing on both inputs. With separate video/audio queues FFmpeg
        # can otherwise wait for more video while the producer is blocked on
        # a full audio queue, before FFmpeg has even opened the audio input.
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
               "-analyzeduration", "1000000", "-probesize", "262144",
               "-f", "mpegts", "-i", "pipe:0"]
        a_r = a_w = None
        audio_socket = None
        if has_audio:
            if os.name == "nt":
                audio_socket = _AudioSocket()
                audio_input = audio_socket.url
            else:
                a_r, a_w = os.pipe()
                audio_input = f"pipe:{a_r}"
            cmd += ["-itsoffset", f"{audio_offset:.3f}",
                    "-analyzeduration", "0", "-probesize", "32",
                    "-f", "alaw", "-ar", "8000", "-ac", "1", "-i", audio_input]
        maps = ["-map", "0:v:0"] + (["-map", "1:a:0"] if has_audio else [])
        codecs = ["-c:v", "copy"] + (["-c:a", "aac", "-ar", "16000", "-ac", "1", "-b:a", "48k"]
                                     if has_audio else [])
        cmd += maps + codecs + [
            "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "event",
            "-hls_flags", "independent_segments+temp_file",
            "-hls_segment_filename", str(job.hls_dir / "seg_%05d.ts"), str(job.playlist)]
        cmd += maps + codecs + ["-movflags", "+faststart", "-f", "mp4", str(self.part)]
        try:
            with open(self.log, "wb") as log:     # a file, not a PIPE nobody drains
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=log, pass_fds=(a_r,) if a_r is not None else ())
        except OSError as e:
            if audio_socket is not None:
                audio_socket.close()
            for fd in (a_r, a_w):
                if fd is not None:
                    os.close(fd)
            raise SdError(f"Impossible de lancer ffmpeg : {e}") from e
        feeds = [("v", self.proc.stdin)]
        if has_audio:
            if audio_socket is not None:
                feeds.append(("a", audio_socket))
            else:
                os.close(a_r)                     # ffmpeg holds its own copy
                feeds.append(("a", os.fdopen(a_w, "wb", buffering=0)))
        for kind, f in feeds:
            pump = _Pump(f)
            pump.q.put(bytes(self._pre[kind]))
            self._sink[kind] = pump.q.put
            self.pumps.append(pump)
            pump.start()
        if not has_audio:
            self._sink["a"] = lambda b: None
        self._pre = {"v": bytearray(), "a": bytearray()}

    def check(self):
        if self.proc is not None and self.proc.poll() is not None:
            raise SdError("ffmpeg s'est arrêté : " + self._err())

    def finish(self):
        for p in self.pumps:
            p.q.put(None)
        try:
            self.proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            raise SdError("ffmpeg ne termine pas la finalisation.")
        if self.proc.returncode != 0 or not self.part.is_file() or self.part.stat().st_size < 1024:
            raise SdError("Conversion MP4 échouée : " + self._err())
        if not mp4_is_playable(self.part):
            # ffmpeg happily copies garbage: some recordings are damaged on the card itself
            raise SdError(CORRUPT_MESSAGE)

    def abort(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()                      # pumps then hit EPIPE and drain
        for p in self.pumps:
            try:
                p.q.put(None, timeout=5)
            except queue.Full:
                pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def _err(self) -> str:
        try:
            return self.log.read_bytes().decode("utf-8", "replace").strip()[-300:] or "sans message"
        except OSError:
            return "sans message"


_KIND_RANK = {"play": 0, "download": 1, "analyze": 2}


class Job:
    def __init__(self, clip_id: str, kind: str, hls_root: Path):
        self.id = clip_id
        self.start, self.end = parse_id(clip_id)
        self.kinds = {kind}                   # who wants it: "play" | "download" | "analyze"
        self.state = "queued"                 # queued|connecting|streaming|finalizing|done|error|cancelled
        self.progress = 0.0
        self.total = float(self.end - self.start)
        self.error: str | None = None
        self.cancel = threading.Event()
        self.created = time.time()
        self.finished_at: float | None = None
        self.last_access = time.time()
        self.hls_dir = hls_root / clip_id

    @property
    def kind(self) -> str:
        """"play" only when nobody else needs the file: such a job is pre-emptible
        (a newer play request or closing the player cancels it)."""
        return "download" if "download" in self.kinds else "analyze" if "analyze" in self.kinds else "play"

    @property
    def rank(self) -> int:                    # queue priority: viewer first, analysis last
        return min(_KIND_RANK[k] for k in self.kinds)

    @property
    def active(self) -> bool:
        return self.state in ("queued", "connecting", "streaming", "finalizing")

    @property
    def playlist(self) -> Path:
        return self.hls_dir / "index.m3u8"

    def snapshot(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state,
                "progress": round(self.progress, 1), "total": self.total,
                "error": self.error,
                # a previous job's HLS dir may still be on disk until _run() wipes it
                "hls_ready": self.state in ("streaming", "finalizing", "done") and self.playlist.is_file()}


class Fetcher:
    """The clip fetch queue of one camera (SdCard.fetcher)."""

    def __init__(self, card: "SdCard"):
        self.card = card
        self._jobs: dict[str, Job] = {}
        self._queue: list[Job] = []
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._current: Job | None = None
        threading.Thread(target=self._work_loop, daemon=True).start()
        threading.Thread(target=self._reap_loop, daemon=True).start()

    # -- public ---------------------------------------------------------------
    def submit(self, clip_id: str, kind: str, internal: bool = False) -> dict:
        """Queue (or join) the fetch of a clip. ``analyze`` is reserved to the
        background analyser (``internal=True``) and always yields to viewers."""
        if kind not in (_KIND_RANK if internal else ("play", "download")):
            raise SdError("Type de tâche invalide.")
        parse_id(clip_id)
        if self.card.local_file(clip_id):
            return {"id": clip_id, "kind": kind, "state": "done", "progress": 0, "total": 0,
                    "error": None, "hls_ready": False, "cached": True}
        with self._wake:
            job = self._jobs.get(clip_id)
            # a running job whose cancel flag is set is doomed (the worker has not noticed
            # yet); "finalizing" no longer honours cancel and will end "done" -> reusable
            if job and job.active and (not job.cancel.is_set() or job.state == "finalizing"):
                job.kinds.add(kind)
            else:
                job = Job(clip_id, kind, self.card.hls_dir)
                self._jobs[clip_id] = job
                self._queue.append(job)
            if kind == "play":                # the viewer moved on: drop other play-only jobs
                for other in list(self._jobs.values()):
                    if other is not job and other.active and other.kind == "play":
                        self._cancel_locked(other)
            # viewers first (newest play request on top), then downloads, then analysis
            self._queue.sort(key=lambda j: (j.rank, j is not job if kind == "play" else False, j.created))
            job.last_access = time.time()
            self._wake.notify_all()
            return job.snapshot()

    def snapshot(self, clip_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(clip_id)
            return job.snapshot() if job and (job.active or job.state == "error") else None

    def get(self, clip_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(clip_id)

    def busy(self) -> bool:
        return self._current is not None

    def jobs(self) -> list[dict]:
        with self._lock:
            return [j.snapshot() for j in self._jobs.values() if j.active]

    def cancel(self, clip_id: str) -> bool:
        with self._wake:
            job = self._jobs.get(clip_id)
            if not job or not job.active:
                return False
            self._cancel_locked(job)
            return True

    def touch(self, clip_id: str):
        with self._lock:
            job = self._jobs.get(clip_id)
            if job:
                job.last_access = time.time()

    def stop_all(self):
        with self._wake:
            for job in self._jobs.values():
                if job.active:
                    self._cancel_locked(job)

    def _cancel_locked(self, job: Job):
        job.cancel.set()
        if job in self._queue:
            self._queue.remove(job)
            job.state, job.finished_at = "cancelled", time.time()

    # -- worker ---------------------------------------------------------------
    def _work_loop(self):
        while True:
            with self._wake:
                while not self._queue:
                    self._wake.wait()
                job = self._queue.pop(0)
                self._current = job
            try:
                self._run(job)
            except (SdError, MediaError) as e:
                job.state, job.error = "error", str(e)
            except Exception as e:  # noqa: BLE001 - a job must never kill the worker
                job.state, job.error = "error", f"{e.__class__.__name__}: {e}"
            finally:
                if job.cancel.is_set() and job.state != "done":
                    job.state = "cancelled"
                job.finished_at = time.time()
                if job.state != "done":
                    shutil.rmtree(job.hls_dir, ignore_errors=True)
                with self._lock:
                    self._current = None
                time.sleep(1.0)               # let the camera release the media session

    def _run(self, job: Job):
        card = self.card
        if card.local_file(job.id):           # a late-cancelled predecessor produced the file
            job.state = "done"
            return
        host = card.host()
        job.state = "connecting"
        shutil.rmtree(job.hls_dir, ignore_errors=True)
        job.hls_dir.mkdir(parents=True, exist_ok=True)
        part = card.part_dir / f"{job.id}.mp4"
        part.unlink(missing_ok=True)
        mux = _Mux(job, part)
        demux = ClipDemuxer(mux.video, mux.audio)

        def start_mux():
            mux.start(has_audio=demux.first_audio_pts is not None, audio_offset=demux.audio_offset)
            job.state = "streaming"

        def on_data():
            if not mux.started and demux.first_video_pts is not None and (
                    demux.first_audio_pts is not None or demux.video_seconds >= 1.5):
                start_mux()
            mux.check()
            job.progress = min(demux.video_seconds, job.total)

        ok = False
        try:
            self._pull(host, job, demux, on_data)
            if job.cancel.is_set():
                return
            if demux.first_video_pts is None:
                raise SdError("La caméra n'a envoyé aucune vidéo pour ce clip.")
            if not mux.started:
                start_mux()                   # clip shorter than the pre-roll window
            job.state = "finalizing"
            mux.finish()                      # EOF -> ffmpeg writes ENDLIST + moov
            os.replace(part, card.mp4_path(job.start, job.end))
            card._usage_change(1, card.mp4_path(job.start, job.end).stat().st_size)
            job.progress = job.total
            job.state = "done"
            ok = True
        finally:
            if not ok:                        # error or cancel: no orphan ffmpeg/threads/partials
                mux.abort()
                part.unlink(missing_ok=True)

    def _pull(self, host: str, job: Job, demux: ClipDemuxer, on_data):
        card = self.card
        card.clip_waiting.set()               # thumbnails yield: see _Thumbs._loop
        try:
            card.media_lock.acquire()
        finally:
            card.clip_waiting.clear()
        try:
            for attempt in range(4):
                try:
                    with MediaSession(host, card.cam.cloud_password, timeout=20.0) as sess:
                        try:
                            stream_clip(sess, job.start, job.end, demux,
                                        should_stop=job.cancel.is_set, on_data=on_data)
                        finally:
                            sess.stop()
                    break
                except MediaError as e:
                    # "device in use": another viewer (the Tapo app, a second instance of
                    # this server) holds the camera's media sessions -> wait and retry
                    if e.code not in BUSY_CODES or attempt == 3 or job.cancel.is_set():
                        raise
                    job.state = "connecting"
                    job.cancel.wait(4.0 * (attempt + 1))
        except (OSError, MediaError) as e:    # socket.timeout is an OSError
            if getattr(e, "code", None) in BUSY_CODES:
                raise SdError("Caméra occupée (" + BUSY_CODES[e.code] + ") : une autre lecture ou un autre "
                              "téléchargement est en cours (appli Tapo ouverte ?). Réessayez dans un instant.") from e
            if demux.video_seconds < job.total * 0.9:
                raise SdError(f"Flux interrompu par la caméra ({e}).") from e
            # else: we already have (almost) the whole clip -> keep it
        finally:
            card.media_lock.release()

    # -- housekeeping ---------------------------------------------------------
    def _reap_loop(self):
        while True:
            time.sleep(30)
            now = time.time()
            with self._lock:
                for cid, job in list(self._jobs.items()):
                    if job.active:
                        continue
                    idle = now - max(job.last_access, job.finished_at or 0)
                    if idle > HLS_KEEP_SECONDS:
                        shutil.rmtree(job.hls_dir, ignore_errors=True)
                        del self._jobs[cid]


def startup(cams) -> None:
    """Server start (FastAPI lifespan) - NOT at import: a second process importing
    this module (tests, scripts, another instance) must not wipe live HLS output."""
    for cam in cams:
        card_for(cam).cleanup()
