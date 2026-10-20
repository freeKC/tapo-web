"""Central configuration, loaded from .env (never hard-coded, never sent anywhere).

The cameras themselves (addresses, accounts) live in cameras.json, see cameras.py.
The TAPO_HOST / TAPO_USER / ... variables still work: they describe the first
camera as long as there is no cameras.json (older installs, Docker).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# Where .env and cameras.json live (the Docker image mounts a volume there).
CONFIG_DIR = Path(os.environ.get("TAPO_CONFIG_DIR", "").strip() or BASE_DIR)
load_dotenv(CONFIG_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_camera() -> dict:
    """The first camera as described by the environment (see cameras.Registry)."""
    return {"host": _get("TAPO_HOST"), "user": _get("TAPO_USER"), "password": _get("TAPO_PASSWORD"),
            "cloud_password": _get("TAPO_CLOUD_PASSWORD"), "subnet": _get("TAPO_SUBNET"),
            "protocol": _get("TAPO_CONTROL_PROTOCOL")}


# The subnet scanned when a camera changed address. WSL sits on its own NAT
# subnet, so this cannot be derived from our own IP: it is the real LAN.
DEFAULT_SUBNET = "192.168.0"

# --- Ports ---
RTSP_PORT = int(_get("TAPO_RTSP_PORT", "554"))
ONVIF_PORT = int(_get("TAPO_ONVIF_PORT", "2020"))

# --- Streams exposed by Tapo cameras ---
# stream1 = full resolution (HD), stream2 = lower resolution (SD).
STREAMS = {
    "hd": "stream1",
    "sd": "stream2",
}
DEFAULT_STREAM = "hd"

# --- Storage ---
# Videos (DVR + SD-card copies + analysis results) can live on another drive:
# TAPO_DATA_DIR=/mnt/f/tapo  ->  /mnt/f/tapo/recordings. HLS segments stay local (transient).
# The first camera uses recordings/ itself, the next ones a sub-folder each.
DATA_DIR = Path(_get("TAPO_DATA_DIR") or BASE_DIR)
RECORDINGS_DIR = DATA_DIR / "recordings"
HLS_DIR = Path(_get("TAPO_HLS_DIR") or BASE_DIR / "hls")  # transient live segments
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
HLS_DIR.mkdir(parents=True, exist_ok=True)

# --- Continuous DVR ---
# Length of each rolling archive segment, in seconds.
SEGMENT_SECONDS = int(_get("TAPO_SEGMENT_SECONDS", "600"))

# --- Server ---
HOST = _get("TAPO_WEB_HOST", "0.0.0.0")
PORT = int(_get("TAPO_WEB_PORT", "8088"))


def rtsp_url(host: str, stream_path: str, user: str, password: str, *, redacted: bool = False) -> str:
    """Build an RTSP URL with URL-encoded credentials."""
    from urllib.parse import quote

    if redacted:
        return f"rtsp://***:***@{host}:{RTSP_PORT}/{stream_path}"
    return f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{RTSP_PORT}/{stream_path}"
