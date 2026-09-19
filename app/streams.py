"""Live view: transcode the camera RTSP into browser-playable HLS on demand.

We copy the H.264 video (cheap, no re-encode) and transcode the camera's
PCM a-law audio to AAC (required for HLS/MSE). ffmpeg processes are started
lazily on first playlist request and reaped after a period of inactivity.
"""
from __future__ import annotations

import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import config

# Stop a live ffmpeg this many seconds after the last playlist/segment hit.
IDLE_TIMEOUT = 30.0


class _LiveStream:
    def __init__(self, key: str, stream_path: str):
        self.key = key
        self.stream_path = stream_path
        self.dir: Path = config.HLS_DIR / key
        self.proc: subprocess.Popen | None = None
        self.last_access = 0.0
        self.lock = threading.Lock()

    @property
    def playlist(self) -> Path:
        return self.dir / "index.m3u8"

    def _spawn(self, host: str) -> None:
        # Clean prior segments so the player never sees a stale playlist.
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        url = config.rtsp_url(host, self.stream_path)
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-rtsp_transport", "tcp", "-i", url,
            "-map", "0:v:0", "-c:v", "copy",
            "-map", "0:a:0?", "-c:a", "aac", "-ar", "16000", "-ac", "1",
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "6",
            "-hls_flags", "delete_segments+append_list+omit_endlist+independent_segments",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", str(self.dir / "seg_%05d.ts"),
            str(self.playlist),
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def ensure_running(self, host: str) -> bool:
        """Start ffmpeg if needed; wait briefly for the playlist to appear."""
        with self.lock:
            self.last_access = time.time()
            if self.proc is not None and self.proc.poll() is None:
                return True
            self._spawn(host)
        # Wait (outside the lock) for the first playlist to be written.
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if self.playlist.exists() and self.playlist.stat().st_size > 0:
                return True
            if self.proc is not None and self.proc.poll() is not None:
                return False  # ffmpeg died (bad creds / camera gone)
            time.sleep(0.2)
        return self.playlist.exists()

    def touch(self) -> None:
        self.last_access = time.time()

    def stop(self) -> None:
        with self.lock:
            p = self.proc
            self.proc = None
        if p and p.poll() is None:
            p.send_signal(signal.SIGINT)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(self.dir, ignore_errors=True)

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


class LiveManager:
    def __init__(self):
        self._streams: dict[str, _LiveStream] = {}
        self._lock = threading.Lock()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    def _get(self, key: str) -> _LiveStream:
        stream_path = config.STREAMS.get(key)
        if not stream_path:
            raise KeyError(key)
        with self._lock:
            s = self._streams.get(key)
            if s is None:
                s = _LiveStream(key, stream_path)
                self._streams[key] = s
            return s

    def ensure(self, key: str, host: str) -> _LiveStream:
        s = self._get(key)
        ok = s.ensure_running(host)
        if not ok:
            raise RuntimeError("ffmpeg failed to start the live stream")
        return s

    def touch(self, key: str) -> None:
        with self._lock:
            s = self._streams.get(key)
        if s:
            s.touch()

    def _reap_loop(self) -> None:
        while True:
            time.sleep(5)
            now = time.time()
            for s in list(self._streams.values()):
                if s.is_running() and (now - s.last_access) > IDLE_TIMEOUT:
                    s.stop()

    def stop_all(self) -> None:
        for s in list(self._streams.values()):
            s.stop()


live_manager = LiveManager()
