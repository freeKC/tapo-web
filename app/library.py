"""The recordings library: index MP4 files, probe metadata, make thumbnails."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

from . import config

_SAFE = re.compile(r"^[A-Za-z0-9._-]+\.mp4$")
_probe_cache: dict[str, dict] = {}
_probe_lock = threading.Lock()


def safe_name(name: str) -> str | None:
    """Reject anything that is not a plain MP4 basename (no path traversal)."""
    name = (name or "").strip()
    if not _SAFE.match(name):
        return None
    if "/" in name or "\\" in name or ".." in name:
        return None
    return name


def resolve(name: str) -> Path | None:
    sn = safe_name(name)
    if not sn:
        return None
    p = (config.RECORDINGS_DIR / sn).resolve()
    try:
        p.relative_to(config.RECORDINGS_DIR.resolve())
    except ValueError:
        return None
    if not p.is_file():
        return None
    return p


def _probe(path: Path) -> dict:
    key = f"{path.name}:{path.stat().st_mtime_ns}:{path.stat().st_size}"
    with _probe_lock:
        cached = _probe_cache.get(path.name)
        if cached and cached.get("_key") == key:
            return cached
    info = {"_key": key, "duration": None, "width": None, "height": None}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=15,
        ).stdout
        data = json.loads(out or "{}")
        fmt = data.get("format", {})
        if fmt.get("duration"):
            info["duration"] = round(float(fmt["duration"]), 1)
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                info["width"] = s.get("width")
                info["height"] = s.get("height")
                break
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        pass
    with _probe_lock:
        _probe_cache[path.name] = info
    return info


def thumbnail(name: str) -> Path | None:
    p = resolve(name)
    if not p:
        return None
    thumb = config.THUMBS_DIR / (p.stem + ".jpg")
    if thumb.exists() and thumb.stat().st_mtime >= p.stat().st_mtime:
        return thumb
    try:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y",
             "-ss", "1", "-i", str(p),
             "-frames:v", "1", "-vf", "scale=320:-2", str(thumb)],
            timeout=20, check=False,
        )
    except subprocess.SubprocessError:
        return None
    return thumb if thumb.exists() else None


def listing() -> list[dict]:
    items = []
    for p in config.RECORDINGS_DIR.glob("*.mp4"):
        if not p.is_file():
            continue
        stat = p.stat()
        meta = _probe(p)
        kind = "continuous" if p.name.startswith("cont_") else "manual"
        stream = "hd"
        m = re.match(r"(?:cont_)?(hd|sd|stream1|stream2)_", p.name)
        if m:
            stream = "sd" if m.group(1) in ("sd", "stream2") else "hd"
        items.append({
            "name": p.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            "duration": meta.get("duration"),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "kind": kind,
            "stream": stream,
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def delete(name: str) -> bool:
    p = resolve(name)
    if not p:
        return False
    thumb = config.THUMBS_DIR / (p.stem + ".jpg")
    try:
        p.unlink()
    except OSError:
        return False
    thumb.unlink(missing_ok=True)
    with _probe_lock:
        _probe_cache.pop(p.name, None)
    return True
