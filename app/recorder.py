"""Local DVR: record the camera's RTSP stream to MP4 files on this machine.

A local DVR next to the SD card browser: instead
of pulling clips the camera already stored, we capture the live stream to a
local, browsable, downloadable archive.

Two modes:
  * on-demand  -> one MP4, started/stopped by the user.
  * continuous -> rolling fixed-length MP4 segments, always-on until stopped.

Video is copied (no re-encode); audio is transcoded a-law -> AAC so the MP4 is
universally playable. ffmpeg is stopped with 'q' on stdin so the MP4 trailer
(moov atom) is written and the file stays seekable.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config


def _finalize(proc: subprocess.Popen) -> None:
    """Ask ffmpeg to quit cleanly so the MP4 is properly closed."""
    if proc.poll() is not None:
        return
    try:
        if proc.stdin:
            proc.stdin.write(b"q")
            proc.stdin.flush()
    except (OSError, ValueError):
        pass
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()


@dataclass
class Recording:
    id: str
    kind: str            # "manual" | "continuous"
    stream: str          # "hd" | "sd"
    started: float
    proc: subprocess.Popen = field(repr=False)
    target: str          # file (manual) or glob-ish label (continuous)


class Recorder:
    def __init__(self):
        self._active: dict[str, Recording] = {}
        self._lock = threading.Lock()
        self._counter = 0

    # -- naming ---------------------------------------------------------------
    def _stamp(self) -> str:
        return time.strftime("%Y%m%d_%H%M%S", time.localtime())

    def _next_id(self) -> str:
        self._counter += 1
        return f"rec{self._counter}_{int(time.time())}"

    # -- on-demand ------------------------------------------------------------
    def start_manual(self, host: str, stream: str) -> Recording:
        stream_path = config.STREAMS.get(stream)
        if not stream_path:
            raise KeyError(stream)
        out = config.RECORDINGS_DIR / f"{stream}_{self._stamp()}.mp4"
        url = config.rtsp_url(host, stream_path)
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", url,
            "-map", "0:v:0", "-c:v", "copy",
            "-map", "0:a:0?", "-c:a", "aac",
            "-movflags", "+faststart",
            str(out),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rec = Recording(self._next_id(), "manual", stream, time.time(), proc, out.name)
        with self._lock:
            self._active[rec.id] = rec
        return rec

    # -- continuous rolling archive ------------------------------------------
    def start_continuous(self, host: str, stream: str) -> Recording:
        stream_path = config.STREAMS.get(stream)
        if not stream_path:
            raise KeyError(stream)
        with self._lock:
            for r in self._active.values():
                if r.kind == "continuous":
                    return r  # already running; single instance
        url = config.rtsp_url(host, stream_path)
        pattern = str(config.RECORDINGS_DIR / f"cont_{stream}_%Y%m%d_%H%M%S.mp4")
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", url,
            "-map", "0:v:0", "-c:v", "copy",
            "-map", "0:a:0?", "-c:a", "aac",
            "-f", "segment",
            "-segment_time", str(config.SEGMENT_SECONDS),
            "-segment_format", "mp4",
            "-segment_format_options", "movflags=+faststart",
            "-reset_timestamps", "1",
            "-strftime", "1",
            pattern,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rec = Recording(self._next_id(), "continuous", stream, time.time(), proc,
                        f"cont_{stream}_*.mp4")
        with self._lock:
            self._active[rec.id] = rec
        return rec

    # -- control --------------------------------------------------------------
    def stop(self, rec_id: str) -> bool:
        with self._lock:
            rec = self._active.pop(rec_id, None)
        if not rec:
            return False
        _finalize(rec.proc)
        return True

    def _prune_dead(self) -> None:
        with self._lock:
            dead = [rid for rid, r in self._active.items() if r.proc.poll() is not None]
            for rid in dead:
                self._active.pop(rid, None)

    def status(self) -> list[dict]:
        self._prune_dead()
        now = time.time()
        with self._lock:
            recs = list(self._active.values())
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "stream": r.stream,
                "target": r.target,
                "elapsed": round(now - r.started, 1),
                "alive": r.proc.poll() is None,
            }
            for r in recs
        ]

    def continuous_running(self) -> bool:
        self._prune_dead()
        with self._lock:
            return any(r.kind == "continuous" for r in self._active.values())

    def stop_all(self) -> None:
        with self._lock:
            recs = list(self._active.values())
            self._active.clear()
        for r in recs:
            _finalize(r.proc)


recorder = Recorder()
