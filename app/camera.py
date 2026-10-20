"""Where each camera is, and whether it answers.

Cameras use DHCP and their IP drifts (.142 -> .136 has been seen). Rather than
trust an address, a camera is checked by its TP-Link certificate and RTSP port
(see discovery.py) and, when it is not where it was, looked for on the subnet.
With several cameras the MAC address (from the UDP discovery answer) tells
which one is which; it is learnt the first time and kept in cameras.json.
"""
from __future__ import annotations

import threading
import time

from . import discovery
from .cameras import Camera, CameraState, registry

# Positive result cache (camera found) and negative (offline) cache windows.
# A camera DHCP-hops and flaps, so we re-check reasonably often when online
# and throttle rescans when offline to avoid scan storms under load.
_POS_TTL = 20.0
_NEG_TTL = 8.0
# One subnet scan at a time for the whole app: concurrent requests (and several
# cameras) share it instead of each launching their own. No state lock is held
# during it.
_scan_lock = threading.Lock()


def invalidate(cam: Camera) -> None:
    """Force the next locate() to re-discover (call after an RTSP failure so a
    camera that moved IP or dropped is re-found promptly)."""
    with cam.state.lock:
        cam.state.checked_at = 0.0


def _identity(cam: Camera, ip: str) -> dict | None:
    """The discovery answer at ``ip``, learnt into the camera's config."""
    info = discovery.probe(ip)
    if info and info.get("mac") and not cam.cfg.mac:
        cam.registry.remember(cam, mac=info["mac"], model=info.get("model") or "")
    return info


def _is_ours(cam: Camera, ip: str) -> bool:
    """Is the camera at ``ip`` this one? Unknown MAC or no UDP answer: yes."""
    if not cam.cfg.mac:
        return True
    info = discovery.probe(ip)
    return not (info and info.get("mac") and info["mac"] != cam.cfg.mac)


def _claimed(cam: Camera) -> set[str]:
    """Addresses where the other cameras currently are."""
    out = set()
    for other in registry().list():
        if other is not cam and other.state.online and other.state.host:
            out.add(other.state.host)
    return out


def _find(cam: Camera) -> str | None:
    ips = discovery.scan_subnet(cam.subnet)
    if cam.cfg.mac:
        for ip in ips:
            info = discovery.probe(ip)
            if info and info.get("mac") == cam.cfg.mac:
                return ip
        return None
    taken = _claimed(cam)
    for ip in ips:
        if ip not in taken:
            return ip
    return None


def locate(cam: Camera, force: bool = False) -> CameraState:
    """Return the camera's location, (re)discovering as needed.

    Order: cached host (if fresh) -> configured / last-known host -> subnet scan.
    The network probing runs WITHOUT the state lock held, so slow scans never
    block fast reads (e.g. /api/status) or serialize unrelated requests.
    """
    st = cam.state
    now = time.time()
    with st.lock:
        if not force:
            fresh = _POS_TTL if st.online else _NEG_TTL
            if (now - st.checked_at) < fresh and st.checked_at > 0:
                return st
        known, hint, verified = st.host, cam.cfg.host, st.verified

    host = None
    # 1) Trust the currently-known host / configured address if it still answers (cheap);
    #    after the camera was away, also check it is the same camera (DHCP may swap two).
    for candidate in dict.fromkeys(c for c in (known, hint) if c):
        if discovery.is_camera(candidate) and (verified and candidate == known or _is_ours(cam, candidate)):
            host = candidate
            break
    # 2) Otherwise scan the LAN (one scan at a time; a waiter re-checks the winner's result).
    if host is None:
        asked = time.time()
        with _scan_lock:
            with st.lock:
                if not force and st.checked_at >= asked:
                    return st          # another request scanned for this camera meanwhile
            host = _find(cam)

    with st.lock:
        st.checked_at = time.time()
        was_online = st.online
        if host:
            st.host, st.online, st.last_seen = host, True, st.checked_at
            st.verified = True
        else:
            st.online, st.verified = False, False
            # keep st.host as the last-known address for display
    if host and (host != cam.cfg.host or not cam.cfg.mac or not was_online):
        if host != cam.cfg.host:
            cam.registry.remember(cam, host=host)
        if not cam.cfg.mac:
            _identity(cam, host)
    return st


def enrich_identity(cam: Camera) -> None:
    """Best-effort model/firmware via ONVIF (which works with the plain camera
    account). Never raises."""
    st = cam.state
    if not st.host or not st.online:
        return
    try:
        from .ptz import onvif_device_information

        info = onvif_device_information(cam, st.host)
        if info:
            with st.lock:
                st.model = info.get("Model") or st.model
                st.firmware = info.get("FirmwareVersion") or st.firmware
            if st.model and not cam.cfg.model:
                cam.registry.remember(cam, model=st.model)
    except Exception:
        pass
