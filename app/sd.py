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
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import camera, config
from .tapo_media import (BUSY_CODES, PLAYER_ID, ClipDemuxer, MediaError, MediaSession,
                         fetch_snapshot, stream_clip)
from .tapo_v4 import TapoV4, TapoV4Error

SD_DIR = config.RECORDINGS_DIR / "sd"
SD_THUMBS = SD_DIR / ".thumbs"
SD_PART = SD_DIR / ".part"
SD_HLS = config.HLS_DIR / "sdplay"
for _d in (SD_DIR, SD_THUMBS, SD_PART, SD_HLS):
    _d.mkdir(parents=True, exist_ok=True)

_ID_RE = re.compile(r"^(\d{9,11})-(\d{9,11})$")
_DATE_RE = re.compile(r"^\d{8}$")
MAX_CLIP_SECONDS = 4 * 3600
HLS_KEEP_SECONDS = 600          # keep a finished clip's HLS around this long after last access
# PlayBackEventType of the official app ("video_type"/"vedio_type" of a recording)
VIDEO_TYPES = {1: "continu", 2: "mouvement", 3: "sabotage", 4: "franchissement de ligne",
               5: "intrusion de zone", 6: "personne", 7: "pleurs de bébé", 8: "véhicule",
               9: "animal", 10: "sonnette", 11: "aboiement", 12: "miaulement", 13: "bris de verre",
               14: "alarme fumée", 33: "animal"}
_media_lock = threading.Lock()   # one media session at a time, clips and thumbnails alike
_clip_waiting = threading.Event()  # a clip fetch wants _media_lock (threading.Lock is not FIFO:
                                   # without this the thumbnail worker re-takes it at once)


class SdError(Exception):
    """User-facing failure (message is shown in the UI).
    ``code`` = the camera's error code when the control API answered, else None."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
#  Control API session (one per camera host, shared, re-login handled inside)
# --------------------------------------------------------------------------- #
_client_lock = threading.Lock()
_client: TapoV4 | None = None
_user_id: int | None = None


def _host() -> str:
    st = camera.locate()
    if not st.online or not st.host:
        raise SdError("Caméra hors ligne.")
    return st.host


def _api() -> TapoV4:
    global _client, _user_id
    host = _host()
    if not config.CLOUD_PASSWORD:
        raise SdError("TAPO_CLOUD_PASSWORD manquant dans .env (mot de passe du compte TP-Link).")
    with _client_lock:
        if _client is None or _client.host != host:
            _client = TapoV4(host, config.CLOUD_PASSWORD, username="admin")
            _user_id = None
        return _client


def _call(fn):
    """Run a control-API call, mapping low-level failures to SdError."""
    try:
        return fn(_api())
    except TapoV4Error as e:
        if e.code in (-40404, -40408):
            raise SdError("La caméra a temporairement bloqué les connexions (trop d'essais). "
                          "Réessayez dans quelques minutes.") from e
        raise SdError(f"API de contrôle : {e}", code=e.code) from e
    except OSError as e:          # requests' exceptions derive from OSError
        camera.invalidate()
        raise SdError(f"Caméra injoignable : {e.__class__.__name__}") from e


def _uid() -> int:
    global _user_id
    if _user_id is None:
        _user_id = int(_call(lambda c: c.get_user_id()))
    return _user_id


# --------------------------------------------------------------------------- #
#  Small TTL cache (the camera dislikes being polled)
# --------------------------------------------------------------------------- #
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, producer, force: bool = False):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not force and now - hit[0] < ttl:
            return hit[1]
    value = producer()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


_tz: ZoneInfo | None = None


def _zone():
    """The camera's own time zone: the day boundaries of its listings use it."""
    global _tz
    if _tz is None:
        try:
            _tz = ZoneInfo(_call(lambda c: c.get_clock()).get("zone_id") or "")
        except Exception:  # noqa: BLE001 - any failure -> server-local time
            return None
    return _tz


def _local(ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, tz=_zone())


# --------------------------------------------------------------------------- #
#  Status / listing
# --------------------------------------------------------------------------- #
def _size_bytes(s: str | None) -> int | None:
    m = re.match(r"^(\d+)B$", s or "")
    return int(m.group(1)) if m else None


def status(force: bool = False) -> dict:
    try:                                  # only a successful card read is cached
        card = _cached("card", 30.0, lambda: _call(lambda c: c.get_sd_status()), force)
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
        "cached": cache_usage(),
    }


def days(force: bool = False) -> dict:
    def produce():
        today = dt.datetime.now(tz=_zone()).date()
        found = _call(lambda c: c.search_days((today - dt.timedelta(days=730)).strftime("%Y%m%d"),
                                              (today + dt.timedelta(days=1)).strftime("%Y%m%d")))
        return sorted(d for d in found if _DATE_RE.match(d))
    return {"days": _cached("days", 120.0, produce, force)}


def _day_bounds(date: str) -> tuple[int, int]:
    day = dt.datetime.strptime(date, "%Y%m%d").replace(tzinfo=_zone())
    return int(day.timestamp()), int((day + dt.timedelta(days=1)).timestamp()) - 1


def _list_day(date: str) -> list[dict]:
    lo, hi = _day_bounds(date)
    try:                                  # what the app uses on this firmware (DST-proof)
        found = _call(lambda c: c.search_videos_utc(lo, hi, PLAYER_ID))
        if found:
            return found
    except SdError as e:
        # fall back only if the camera refuses the METHOD; offline / lockout / dead
        # session must not trigger more logins through a second attempt
        if e.code is None or e.code in (-40401, -40404, -40408, -40421):
            raise
    uid = _uid()                          # legacy listing by camera-local date
    return _call(lambda c: c.get_recordings(date, user_id=uid))


def recordings(date: str, force: bool = False) -> dict:
    if not _DATE_RE.match(date or ""):
        raise SdError("Date invalide (attendu : AAAAMMJJ).")
    try:
        day_start, _ = _day_bounds(date)
    except ValueError:
        raise SdError("Date invalide (attendu : AAAAMMJJ).")
    raw = _cached(f"rec:{date}", 20.0, lambda: _list_day(date), force)
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
        path = _mp4_path(start, end)
        clips.append({
            "id": cid, "start": start, "end": end, "duration": end - start,
            "time": _local(start).strftime("%H:%M:%S"),
            "time_end": _local(end).strftime("%H:%M:%S"),
            # wall-clock position on the 24 h axis (NOT elapsed time: 23 h / 25 h DST days)
            "day_second": (lambda t: t.hour * 3600 + t.minute * 60 + t.second)(_local(start))
                          if start >= day_start else 0,
            "type": vt, "type_label": VIDEO_TYPES.get(vt, f"type {vt}"),
            "cached": path.is_file(),
            "size": path.stat().st_size if path.is_file() else None,
            "job": fetcher.snapshot(cid),
        })
    clips.sort(key=lambda c: c["start"])
    return {"date": date, "clips": clips}


# --------------------------------------------------------------------------- #
#  Local copies
# --------------------------------------------------------------------------- #
def parse_id(clip_id: str) -> tuple[int, int]:
    m = _ID_RE.match(clip_id or "")
    if not m:
        raise SdError("Identifiant de clip invalide.")
    start, end = int(m.group(1)), int(m.group(2))
    if not 0 < end - start <= MAX_CLIP_SECONDS:
        raise SdError("Durée de clip invalide.")
    return start, end


def _mp4_path(start: int, end: int) -> Path:
    return SD_DIR / f"sd_{start}_{end}.mp4"


def local_file(clip_id: str) -> Path | None:
    p = _mp4_path(*parse_id(clip_id))
    return p if p.is_file() else None


def download_name(clip_id: str) -> str:
    start, end = parse_id(clip_id)
    return f"tapo_sd_{_local(start).strftime('%Y-%m-%d_%H-%M-%S')}_{end - start}s.mp4"


class _Thumbs:
    """Per-recording snapshots straight from the camera (640x360 JPEG), cached on
    disk. Requests are served in batches over ONE media session: opening a session
    costs ~1.5 s, each further snapshot ~0.3 s."""

    BATCH = 24                 # snapshots per media session
    LINGER = 1.0               # keep the session open this long for follow-up requests
    RETRY_AFTER = 120.0

    def __init__(self):
        self._lock = threading.Lock()
        self._waiters: dict[int, threading.Event] = {}
        self._failed: dict[int, float] = {}      # start -> do not retry before (epoch)
        self._q: queue.Queue[int] = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def path(start: int) -> Path:
        return SD_THUMBS / f"cam_{start}.jpg"

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
        while not ev.is_set() and not fetcher.busy() and time.monotonic() < deadline:
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
        while True:
            start = self._q.get()
            batch_left = self.BATCH
            try:
                while _clip_waiting.is_set():         # a clip fetch goes first
                    time.sleep(0.1)
                with _media_lock, MediaSession(_host(), config.CLOUD_PASSWORD, timeout=8.0) as sess:
                    while start is not None:
                        jpg = fetch_snapshot(sess, start)
                        if jpg:
                            tmp = self.path(start).with_suffix(".tmp")
                            tmp.write_bytes(jpg)
                            os.replace(tmp, self.path(start))
                        self._done(start, None if jpg else self.RETRY_AFTER)
                        start, batch_left = None, batch_left - 1
                        if batch_left > 0 and not _clip_waiting.is_set():
                            try:
                                start = self._q.get(timeout=self.LINGER)
                            except queue.Empty:
                                pass
            except Exception:  # noqa: BLE001 - camera offline, auth, timeout...
                if start is not None:
                    self._done(start, 15.0)
                time.sleep(2.0)


thumbs = _Thumbs()


def thumbnail(clip_id: str) -> Path | None:
    start, _end = parse_id(clip_id)
    return thumbs.get(start)


def delete_local(clip_id: str) -> bool:
    """Remove OUR copy only. The recording on the camera's SD card is untouched."""
    p = local_file(clip_id)
    if not p:
        return False
    p.unlink(missing_ok=True)
    job = fetcher.get(clip_id)
    if job is None or not job.active:
        shutil.rmtree(SD_HLS / clip_id, ignore_errors=True)
    return True


def cache_usage() -> dict:
    files = [f for f in SD_DIR.glob("sd_*.mp4") if f.is_file()]
    return {"count": len(files), "bytes": sum(f.stat().st_size for f in files)}


# --------------------------------------------------------------------------- #
#  Fetch jobs
# --------------------------------------------------------------------------- #
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
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "mpegts", "-i", "pipe:0"]
        a_r = a_w = None
        if has_audio:
            a_r, a_w = os.pipe()
            cmd += ["-itsoffset", f"{audio_offset:.3f}",
                    "-f", "alaw", "-ar", "8000", "-ac", "1", "-i", f"pipe:{a_r}"]
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
                                             stderr=log, pass_fds=(a_r,) if has_audio else ())
        except OSError as e:
            for fd in (a_r, a_w):
                if fd is not None:
                    os.close(fd)
            raise SdError(f"Impossible de lancer ffmpeg : {e}") from e
        feeds = [("v", self.proc.stdin)]
        if has_audio:
            os.close(a_r)                         # ffmpeg holds its own copy
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
    def __init__(self, clip_id: str, kind: str):
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
        self.hls_dir = SD_HLS / clip_id

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
    def __init__(self):
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
        if local_file(clip_id):
            return {"id": clip_id, "kind": kind, "state": "done", "progress": 0, "total": 0,
                    "error": None, "hls_ready": False, "cached": True}
        with self._wake:
            job = self._jobs.get(clip_id)
            # a running job whose cancel flag is set is doomed (the worker has not noticed
            # yet); "finalizing" no longer honours cancel and will end "done" -> reusable
            if job and job.active and (not job.cancel.is_set() or job.state == "finalizing"):
                job.kinds.add(kind)
            else:
                job = Job(clip_id, kind)
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
        if local_file(job.id):                # a late-cancelled predecessor produced the file
            job.state = "done"
            return
        host = _host()
        job.state = "connecting"
        shutil.rmtree(job.hls_dir, ignore_errors=True)
        job.hls_dir.mkdir(parents=True, exist_ok=True)
        part = SD_PART / f"{job.id}.mp4"
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
            os.replace(part, _mp4_path(job.start, job.end))
            job.progress = job.total
            job.state = "done"
            ok = True
        finally:
            if not ok:                        # error or cancel: no orphan ffmpeg/threads/partials
                mux.abort()
                part.unlink(missing_ok=True)

    @staticmethod
    def _pull(host: str, job: Job, demux: ClipDemuxer, on_data):
        _clip_waiting.set()                   # thumbnails yield: see _Thumbs._loop
        try:
            _media_lock.acquire()
        finally:
            _clip_waiting.clear()
        try:
            for attempt in range(4):
                try:
                    with MediaSession(host, config.CLOUD_PASSWORD, timeout=20.0) as sess:
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
            _media_lock.release()

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


def startup():
    """Server start (FastAPI lifespan) - NOT at import: a second process importing
    this module (tests, scripts, another instance) must not wipe live HLS output."""
    shutil.rmtree(SD_HLS, ignore_errors=True)
    SD_HLS.mkdir(parents=True, exist_ok=True)
    for f in SD_PART.glob("*.mp4"):
        f.unlink(missing_ok=True)


fetcher = Fetcher()
