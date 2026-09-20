"""Animal detection on the SD-card clips: find the videos worth watching.

Pipeline (everything stays on this machine):

    analyser thread --(needs the MP4)--> sd.fetcher  (kind "analyze": lowest
         |                                priority, always yields to a viewer)
         |--(JSON line)--> ML worker subprocess  (own virtualenv with PyTorch,
         |                                see ml/worker.py; loaded on demand,
         |                                exits when idle to give RAM/VRAM back)
         '--> SQLite  recordings/sd/analysis.sqlite  (+ best frame JPEG per clip)

The web process never imports the ML stack. Results are keyed by clip id
("<start>-<end>") so they survive deleting the local copy of a clip.

Retention of clips fetched *only* for analysis (TAPO_ANALYZE_KEEP):
    animals (default) keep the MP4 when something alive was found, else delete it
    all               keep everything        none   always delete after analysis
Clips the user fetched himself are never deleted here.
"""
from __future__ import annotations

import json
import os
import queue
import select
import sqlite3
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from . import config, sd

ML_DIR = config.BASE_DIR / "ml"
ML_PYTHON = Path(os.environ.get("TAPO_ML_PYTHON", str(config.BASE_DIR / ".mlvenv" / "bin" / "python")))
DB_PATH = sd.SD_DIR / "analysis.sqlite"
FRAMES_DIR = sd.SD_DIR / ".animals"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
KEEP = os.environ.get("TAPO_ANALYZE_KEEP", "animals").strip().lower()
WORKER_IDLE_EXIT = 300.0        # stop the ML process after this long without work
CLIP_TIMEOUT = 900.0            # one clip must never block the queue forever
SCHEMA_VERSION = 1

# model label -> (French label, emoji, group). Groups drive the UI filters.
LABELS = {
    "cat": ("chat", "🐱", "animal"), "fox": ("renard", "🦊", "animal"),
    "mustelid": ("fouine / mustélidé", "🦦", "animal"), "marten": ("fouine", "🦦", "animal"),
    "badger": ("blaireau", "🦡", "animal"), "hedgehog": ("hérisson", "🦔", "animal"),
    "dog": ("chien", "🐕", "animal"), "bird": ("oiseau", "🐦", "animal"),
    "squirrel": ("écureuil", "🐿️", "animal"), "roe_deer": ("chevreuil", "🦌", "animal"),
    "deer": ("cervidé", "🦌", "animal"), "wild_boar": ("sanglier", "🐗", "animal"),
    "lagomorph": ("lapin / lièvre", "🐇", "animal"), "rodent": ("rongeur", "🐀", "animal"),
    "bat": ("chauve-souris", "🦇", "animal"), "animal": ("animal", "🐾", "animal"),
    "person": ("personne", "🚶", "person"), "vehicle": ("véhicule", "🚗", "vehicle"),
}


def describe(label: str) -> dict:
    fr, emoji, group = LABELS.get(label, (label, "🐾", "animal"))
    return {"key": label, "label": fr, "emoji": emoji, "group": group}


# --------------------------------------------------------------------------- #
#  Results store
# --------------------------------------------------------------------------- #
_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS analysis (
        clip_id TEXT PRIMARY KEY, start INTEGER NOT NULL, day TEXT NOT NULL,
        status TEXT NOT NULL,            -- done | error
        analyzed_at REAL NOT NULL, model TEXT, seconds REAL,
        detections TEXT NOT NULL,        -- JSON list, best first
        error TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS analysis_day ON analysis(day)")
    # manual corrections survive a re-analysis: labels the user removed from a clip
    con.execute("CREATE TABLE IF NOT EXISTS rejected (clip_id TEXT NOT NULL, label TEXT NOT NULL, "
                "PRIMARY KEY (clip_id, label))")
    return con


def _rejected(con) -> dict[str, set]:
    out: dict[str, set] = {}
    for cid, label in con.execute("SELECT clip_id, label FROM rejected"):
        out.setdefault(cid, set()).add(label)
    return out


def _clean(cid: str, dets: list, rejected: dict) -> list:
    bad = rejected.get(cid)
    return [d for d in dets if d.get("label") not in bad] if bad else dets


def reject(clip_id: str, label: str, undo: bool = False) -> None:
    """The user says this label is wrong for this clip (or takes that back)."""
    sd.parse_id(clip_id)
    if label not in LABELS:
        raise sd.SdError("Étiquette inconnue.")
    with _db_lock, _db() as con:
        if undo:
            con.execute("DELETE FROM rejected WHERE clip_id=? AND label=?", (clip_id, label))
        else:
            con.execute("INSERT OR IGNORE INTO rejected VALUES (?,?)", (clip_id, label))


_COLS = "clip_id,start,day,status,analyzed_at,model,seconds,detections,error"


def _row(r, rejected=None) -> dict:
    dets = _clean(r[0], json.loads(r[7] or "[]"), rejected or {})
    for d in dets:
        d.update(describe(d.get("label", "animal")))
    return {"clip_id": r[0], "status": r[3], "analyzed_at": r[4], "model": r[5], "seconds": r[6],
            "detections": dets, "error": r[8],
            "animal": any(d["group"] == "animal" for d in dets),
            "frame": (FRAMES_DIR / f"{r[0]}.jpg").is_file()}


def results_for_day(day: str) -> dict[str, dict]:
    with _db_lock, _db() as con:
        rows = con.execute(f"SELECT {_COLS} FROM analysis WHERE day=?", (day,)).fetchall()
        rej = _rejected(con)
    return {r[0]: _row(r, rej) for r in rows}


def result(clip_id: str) -> dict | None:
    with _db_lock, _db() as con:
        r = con.execute(f"SELECT {_COLS} FROM analysis WHERE clip_id=?", (clip_id,)).fetchone()
        rej = _rejected(con)
    return _row(r, rej) if r else None


def days_with_animals() -> dict[str, dict]:
    """day -> {"analyzed": n, "animals": n} for the calendar."""
    out: dict[str, dict] = {}
    with _db_lock, _db() as con:
        rej = _rejected(con)
        for cid, day, dets in con.execute("SELECT clip_id, day, detections FROM analysis WHERE status='done'").fetchall():
            o = out.setdefault(day, {"analyzed": 0, "animals": 0})
            o["analyzed"] += 1
            if any(describe(d.get("label", ""))["group"] == "animal" for d in _clean(cid, json.loads(dets or "[]"), rej)):
                o["animals"] += 1
    return out


def _store(clip_id: str, day: str, status: str, model: str | None, seconds: float | None,
           detections: list, error: str | None = None):
    start, _ = sd.parse_id(clip_id)
    with _db_lock, _db() as con:
        con.execute("INSERT OR REPLACE INTO analysis VALUES (?,?,?,?,?,?,?,?,?)",
                    (clip_id, start, day, status, time.time(), model, seconds,
                     json.dumps(detections, ensure_ascii=False), error))


def frame_path(clip_id: str) -> Path | None:
    sd.parse_id(clip_id)
    p = FRAMES_DIR / f"{clip_id}.jpg"
    return p if p.is_file() else None


# --------------------------------------------------------------------------- #
#  ML worker subprocess (JSON lines over stdin/stdout)
# --------------------------------------------------------------------------- #
class WorkerError(Exception):
    pass


class _Worker:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.info: dict = {}

    @staticmethod
    def installed() -> bool:
        return ML_PYTHON.is_file() and (ML_DIR / "worker.py").is_file()

    def _readline(self, timeout: float) -> dict:
        end = time.time() + timeout
        fd = self.proc.stdout
        while True:
            left = end - time.time()
            if left <= 0:
                raise WorkerError("le moteur d'analyse ne répond plus")
            if self.proc.poll() is not None:
                raise WorkerError(f"le moteur d'analyse s'est arrêté (code {self.proc.returncode})")
            ready, _, _ = select.select([fd], [], [], min(left, 1.0))
            if ready:
                line = fd.readline()
                if not line:
                    raise WorkerError("le moteur d'analyse s'est arrêté")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue                      # stray library output
                if isinstance(msg, dict) and msg.get("type") in ("ready", "result", "error"):
                    return msg

    def ensure(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        if not self.installed():
            raise WorkerError("environnement d'analyse non installé (lancer ./ml/setup.sh)")
        log = open(config.BASE_DIR / "logs" / "ml_worker.log", "ab") if (config.BASE_DIR / "logs").is_dir() \
            else subprocess.DEVNULL
        self.proc = subprocess.Popen([str(ML_PYTHON), "-u", str(ML_DIR / "worker.py")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                     cwd=str(ML_DIR), preexec_fn=lambda: os.nice(10))
        msg = self._readline(300.0)               # model loading can take a while
        if msg.get("type") != "ready":
            self.stop()
            raise WorkerError(msg.get("error") or "démarrage du moteur d'analyse impossible")
        self.info = msg

    def analyze(self, clip_id: str, path: Path, frame_out: Path) -> dict:
        self.ensure()
        req = {"id": clip_id, "path": str(path), "frame_out": str(frame_out)}
        try:
            self.proc.stdin.write((json.dumps(req) + "\n").encode())
            self.proc.stdin.flush()
        except OSError as e:
            self.stop()
            raise WorkerError(f"moteur d'analyse injoignable ({e})") from e
        msg = self._readline(CLIP_TIMEOUT)
        if msg.get("type") == "error":
            raise WorkerError(msg.get("error") or "analyse échouée")
        return msg

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                p.stdin.close()
                p.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                p.kill()


# --------------------------------------------------------------------------- #
#  Analyser: one clip at a time, in the background
# --------------------------------------------------------------------------- #
class Analyzer:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue: deque[tuple[str, str]] = deque()     # (clip_id, day)
        self._queued: set[str] = set()
        self._wake = threading.Event()
        self._worker = _Worker()
        self.current: str | None = None
        self.last_error: str | None = None
        self.done_count = 0
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    # -- public ---------------------------------------------------------------
    def enqueue(self, clips: list[tuple[str, str]], redo: bool = False, front: bool = False) -> int:
        """clips = [(clip_id, day)]; returns how many were actually queued.
        Done clips are skipped, and so are clips that failed less than 6 h ago."""
        known = set()
        if not redo:
            with _db_lock, _db() as con:
                known = {r[0] for r in con.execute(
                    "SELECT clip_id FROM analysis WHERE status='done' OR analyzed_at > ?", (time.time() - 6 * 3600,))}
        n = 0
        with self._lock:
            for cid, day in clips:
                if cid in known or cid in self._queued or cid == self.current:
                    continue
                (self._queue.appendleft if front else self._queue.append)((cid, day))
                self._queued.add(cid)
                n += 1
        self._wake.set()
        return n

    def clear(self) -> int:
        with self._lock:
            n = len(self._queue)
            self._queue.clear()
            self._queued.clear()
        return n

    def status(self) -> dict:
        with self._lock:
            return {"available": _Worker.installed(), "queued": len(self._queue), "current": self.current,
                    "done": self.done_count, "error": self.last_error,
                    "model": self._worker.info.get("model"), "device": self._worker.info.get("device"),
                    "queued_ids": list(self._queued)[:500]}

    def stop(self):
        self._stop = True
        self._wake.set()
        self._worker.stop()

    # -- worker loop ----------------------------------------------------------
    def _loop(self):
        idle_since = time.time()
        while not self._stop:
            with self._lock:
                item = self._queue.popleft() if self._queue else None
                if item:
                    self._queued.discard(item[0])
                    self.current = item[0]
            if item is None:
                if time.time() - idle_since > WORKER_IDLE_EXIT:
                    self._worker.stop()           # give the RAM / VRAM back
                self._wake.wait(30)
                self._wake.clear()
                continue
            try:
                self._analyze(*item)
                self.last_error = None
                self.done_count += 1
            except Exception as e:  # noqa: BLE001 - never kill the analyser thread
                self.last_error = str(e)
                if isinstance(e, WorkerError) and not _Worker.installed():
                    self.clear()                  # nothing can work until the ML env exists
                time.sleep(5)
            finally:
                with self._lock:
                    self.current = None
                idle_since = time.time()

    def _fetch(self, clip_id: str) -> tuple[Path, bool]:
        """-> (local MP4, fetched_by_us). Goes through the shared camera queue."""
        p = sd.local_file(clip_id)
        if p:
            return p, False
        sd.fetcher.submit(clip_id, "analyze", internal=True)
        deadline = time.time() + 3600
        while time.time() < deadline and not self._stop:
            p = sd.local_file(clip_id)
            if p:
                job = sd.fetcher.get(clip_id)
                return p, not (job and job.kinds - {"analyze"})
            job = sd.fetcher.get(clip_id)
            if job is None or job.state in ("error", "cancelled"):
                raise sd.SdError((job and job.error) or "récupération du clip annulée")
            time.sleep(1.0)
        raise sd.SdError("récupération du clip trop longue")

    def _analyze(self, clip_id: str, day: str):
        path, ours = self._fetch(clip_id)
        frame_out = FRAMES_DIR / f"{clip_id}.jpg"
        t0 = time.time()
        try:
            res = self._worker.analyze(clip_id, path, frame_out)
        except WorkerError as e:
            _store(clip_id, day, "error", None, None, [], str(e))
            raise
        dets = res.get("detections") or []
        _store(clip_id, day, "done", res.get("model"), round(time.time() - t0, 1), dets)
        alive = any(describe(d.get("label", ""))["group"] == "animal" for d in dets)
        if ours and (KEEP == "none" or (KEEP == "animals" and not alive)):
            job = sd.fetcher.get(clip_id)
            if not (job and job.kinds - {"analyze"}):     # nobody asked for it meanwhile
                sd.delete_local(clip_id)


analyzer = Analyzer()


# --------------------------------------------------------------------------- #
#  Cross-day views (the "Animaux" tab)
# --------------------------------------------------------------------------- #
def species() -> list[dict]:
    """Animals seen so far: [{key,label,emoji,count,last}] - most recent first."""
    out: dict[str, dict] = {}
    with _db_lock, _db() as con:
        rows = con.execute("SELECT clip_id, start, detections FROM analysis WHERE status='done' AND detections != '[]'").fetchall()
        rej = _rejected(con)
    for cid, start, dets in rows:
        for d in _clean(cid, json.loads(dets), rej):
            info = describe(d.get("label", "animal"))
            if info["group"] != "animal":
                continue
            o = out.setdefault(info["key"], {**info, "count": 0, "last": 0})
            o["count"] += 1
            o["last"] = max(o["last"], start)
    return sorted(out.values(), key=lambda o: -o["last"])


def clips_with(key: str) -> list[dict]:
    """Every analysed clip showing ``key`` (or any animal for "all"), newest first.
    Works for clips that have long since rolled off the SD card: we kept the MP4."""
    out = []
    with _db_lock, _db() as con:
        rows = con.execute("SELECT clip_id, start, day, detections FROM analysis "
                           "WHERE status='done' AND detections != '[]' ORDER BY start DESC").fetchall()
        rej = _rejected(con)
    for cid, start, day, dets in rows:
        dets = [{**d, **describe(d.get("label", "animal"))} for d in _clean(cid, json.loads(dets), rej)]
        hit = next((d for d in dets if d["key"] == key or (key == "all" and d["group"] == "animal")), None)
        if not hit:
            continue
        _s, end = sd.parse_id(cid)
        t = sd._local(start)
        out.append({"id": cid, "start": start, "end": end, "duration": end - start, "day": day,
                    "date": t.strftime("%d/%m/%Y"), "time": t.strftime("%H:%M:%S"),
                    "detections": dets, "first": hit.get("first", 0), "score": hit.get("score"),
                    "cached": sd.local_file(cid) is not None, "frame": (FRAMES_DIR / f"{cid}.jpg").is_file()})
    return out


# --------------------------------------------------------------------------- #
#  Auto mode: every recording gets fetched and analysed, history included
# --------------------------------------------------------------------------- #
AUTO = os.environ.get("TAPO_ANALYZE_AUTO", "1").strip() not in ("0", "false", "no", "")
AUTO_PERIOD = 600.0             # look for new recordings this often
_complete_days: set[str] = set()


def _scan_once():
    """Queue what is missing: today/yesterday first (front of the queue), then the whole
    history on the card, newest day first. A past day whose clips are all analysed is
    remembered and never listed again (one control-API call per day otherwise)."""
    days = sd.days()["days"]
    recent = set(days[-2:])
    for day in reversed(days):
        if day in _complete_days:
            continue
        clips = sd.recordings(day)["clips"]
        done = results_for_day(day)
        missing = [(c["id"], day) for c in clips if done.get(c["id"], {}).get("status") != "done"]
        if not missing and day not in recent and (KEEP != "all" or all(c["cached"] for c in clips)):
            _complete_days.add(day)
        if missing:
            analyzer.enqueue(missing, front=day in recent)
        if KEEP == "all":                         # archive mode: also bring back clips that were
            backlog = [c["id"] for c in clips     # analysed earlier and not kept (still on the card)
                       if not c["cached"] and done.get(c["id"], {}).get("status") == "done"]
            for cid in backlog[:40]:
                sd.fetcher.submit(cid, "analyze", internal=True)
            if backlog:
                _complete_days.discard(day)
        if day not in recent:
            time.sleep(2.0)                       # be gentle with the control API
        if analyzer.status()["queued"] > 400:     # enough work for now; continue next round
            break


def _auto_loop():
    time.sleep(20)
    while not analyzer._stop:
        try:
            if _Worker.installed():
                _scan_once()
        except Exception as e:  # noqa: BLE001 - camera offline etc.: try again next round
            analyzer.last_error = f"scan : {e}"
        time.sleep(AUTO_PERIOD)


def startup():
    """Called from the FastAPI lifespan (never at import)."""
    if AUTO:
        threading.Thread(target=_auto_loop, daemon=True).start()
