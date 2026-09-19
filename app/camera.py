"""Camera location + reachability.

The camera uses DHCP and its IP drifts (we have already seen .142 -> .136).
Rather than hard-code an address, we locate it on the LAN by its TP-Link
self-signed TLS certificate (subject/issuer contain "TPRI"), confirming it is
really the camera by also requiring the RTSP port to be open.
"""
from __future__ import annotations

import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import config


def _port_open(ip: str, port: int, timeout: float = 1.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _cert_der(ip: str, timeout: float = 2.0) -> bytes | None:
    ctx = ssl._create_unverified_context()
    try:
        raw = socket.create_connection((ip, 443), timeout=timeout)
        try:
            ss = ctx.wrap_socket(raw, server_hostname=ip)
            der = ss.getpeercert(binary_form=True)
            ss.close()
            return der
        finally:
            try:
                raw.close()
            except OSError:
                pass
    except (OSError, ssl.SSLError):
        return None


def _is_camera(ip: str) -> bool:
    """A host is our camera if it presents the TPRI cert AND speaks RTSP."""
    der = _cert_der(ip)
    if not der:
        return False
    # The TP-Link device cert carries the ASCII marker "TPRI-DEVICE" / "TPRI".
    if b"TPRI" not in der:
        return False
    return _port_open(ip, config.RTSP_PORT, timeout=1.5)


@dataclass
class CameraState:
    host: str | None = None
    online: bool = False
    last_seen: float = 0.0
    model: str | None = None
    firmware: str | None = None
    checked_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


STATE = CameraState()


def _scan_subnet() -> str | None:
    subnet = config.CAM_SUBNET
    candidates = [f"{subnet}.{h}" for h in range(1, 255)]
    # Stage 1: who even has 443 open (cheap), then cert-check only those.
    with ThreadPoolExecutor(max_workers=64) as ex:
        live = [ip for ip, ok in zip(candidates, ex.map(lambda i: _port_open(i, 443, 0.8), candidates)) if ok]
    with ThreadPoolExecutor(max_workers=16) as ex:
        for ip, ok in zip(live, ex.map(_is_camera, live)):
            if ok:
                return ip
    return None


# Positive result cache (camera found) and negative (offline) cache windows.
# The camera DHCP-hops and flaps, so we re-check reasonably often when online
# and throttle rescans when offline to avoid scan storms under load.
_POS_TTL = 20.0
_NEG_TTL = 8.0
# Serialize the (slow) network scan so concurrent requests share one scan
# instead of each launching their own; the state lock is NOT held during it.
_scan_lock = threading.Lock()


def invalidate() -> None:
    """Force the next locate() to re-discover (call after an RTSP failure so a
    camera that moved IP or dropped is re-found promptly)."""
    with STATE.lock:
        STATE.checked_at = 0.0


def locate(force: bool = False) -> CameraState:
    """Return current camera location, (re)discovering as needed.

    Order: cached host (if fresh) -> configured hint -> subnet scan. The network
    probing runs WITHOUT the state lock held, so slow scans never block fast
    reads (e.g. /api/status) or serialize unrelated requests.
    """
    now = time.time()
    with STATE.lock:
        if not force:
            fresh = _POS_TTL if STATE.online else _NEG_TTL
            if (now - STATE.checked_at) < fresh and STATE.checked_at > 0:
                return STATE
        known, hint = STATE.host, config.CAM_HOST_HINT

    # Only one scan at a time; a waiter re-checks the cache the winner filled.
    with _scan_lock:
        now = time.time()
        with STATE.lock:
            if not force and (now - STATE.checked_at) < _NEG_TTL and STATE.checked_at > 0:
                return STATE

        host = None
        # 1) Trust the currently-known host / hint if it still answers (cheap).
        for candidate in (known, hint):
            if candidate and _is_camera(candidate):
                host = candidate
                break
        # 2) Otherwise scan the LAN by certificate.
        if host is None:
            host = _scan_subnet()

        with STATE.lock:
            STATE.checked_at = time.time()
            if host:
                STATE.host = host
                STATE.online = True
                STATE.last_seen = STATE.checked_at
            else:
                STATE.online = False
                # keep STATE.host as the last-known address for display
            return STATE


def enrich_identity() -> None:
    """Best-effort model/firmware via ONVIF (which works even though the
    control API needs the cloud password). Never raises."""
    st = STATE
    if not st.host or not st.online:
        return
    try:
        from .ptz import onvif_device_information

        info = onvif_device_information(st.host)
        if info:
            with st.lock:
                st.model = info.get("Model") or st.model
                st.firmware = info.get("FirmwareVersion") or st.firmware
    except Exception:
        pass
