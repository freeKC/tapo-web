"""Central configuration, loaded from .env (never hard-coded, never sent anywhere)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# --- Camera credentials (the "Camera Account" set in the Tapo app) ---
CAM_USER = _get("TAPO_USER")
CAM_PASSWORD = _get("TAPO_PASSWORD")
# TP-Link cloud account password: the secret behind the camera's local control
# API ("V4" login, user "admin") and its media port. Often equal to CAM_PASSWORD.
CLOUD_PASSWORD = _get("TAPO_CLOUD_PASSWORD") or CAM_PASSWORD

# --- Discovery hints ---
# Last-known host (used first; discovery falls back to a subnet scan on failure).
CAM_HOST_HINT = _get("TAPO_HOST")
# The subnet the camera lives on. WSL sits on its own NAT subnet, so this
# cannot be derived from our own IP - it is pinned to the real LAN.
CAM_SUBNET = _get("TAPO_SUBNET", "192.168.0")

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
DATA_DIR = Path(_get("TAPO_DATA_DIR") or BASE_DIR)
RECORDINGS_DIR = DATA_DIR / "recordings"
THUMBS_DIR = RECORDINGS_DIR / ".thumbs"
HLS_DIR = BASE_DIR / "hls"  # transient live segments
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
THUMBS_DIR.mkdir(parents=True, exist_ok=True)
HLS_DIR.mkdir(parents=True, exist_ok=True)

# --- Continuous DVR ---
# Length of each rolling archive segment, in seconds.
SEGMENT_SECONDS = int(_get("TAPO_SEGMENT_SECONDS", "600"))

# --- Server ---
HOST = _get("TAPO_WEB_HOST", "0.0.0.0")
PORT = int(_get("TAPO_WEB_PORT", "8088"))


def rtsp_url(host: str, stream_path: str, *, redacted: bool = False) -> str:
    """Build an RTSP URL with URL-encoded credentials."""
    from urllib.parse import quote

    if redacted:
        return f"rtsp://***:***@{host}:{RTSP_PORT}/{stream_path}"
    user = quote(CAM_USER, safe="")
    pw = quote(CAM_PASSWORD, safe="")
    return f"rtsp://{user}:{pw}@{host}:{RTSP_PORT}/{stream_path}"


# --- First-run / settings form (no secret ever lives in the code or in git) ---
_FIELDS = {"host": "TAPO_HOST", "subnet": "TAPO_SUBNET", "user": "TAPO_USER",
           "password": "TAPO_PASSWORD", "cloud_password": "TAPO_CLOUD_PASSWORD"}


def configured() -> dict:
    """Which credentials are present (never their values)."""
    return {"camera_account": bool(CAM_USER and CAM_PASSWORD), "cloud_password": bool(CLOUD_PASSWORD),
            "host": CAM_HOST_HINT, "subnet": CAM_SUBNET, "user": CAM_USER}


def save_credentials(values: dict) -> None:
    """Write the given fields to .env (chmod 600) and apply them to the running process.
    Empty password fields keep the stored value."""
    global CAM_USER, CAM_PASSWORD, CLOUD_PASSWORD, CAM_HOST_HINT, CAM_SUBNET
    env_path = BASE_DIR / ".env"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    for field, key in _FIELDS.items():
        val = (values.get(field) or "").strip()
        if not val or "\n" in val or "\r" in val:
            continue
        os.environ[key] = val
        lines = [ln for ln in lines if not ln.startswith(key + "=")] + [f"{key}={val}"]
    env_path.write_text("\n".join(lines) + "\n")
    env_path.chmod(0o600)
    CAM_USER, CAM_PASSWORD = _get("TAPO_USER"), _get("TAPO_PASSWORD")
    CLOUD_PASSWORD = _get("TAPO_CLOUD_PASSWORD") or CAM_PASSWORD
    CAM_HOST_HINT, CAM_SUBNET = _get("TAPO_HOST"), _get("TAPO_SUBNET", "192.168.0")
