"""Connection test and diagnostic report of one camera.

The test answers the three questions a first setup usually trips on:
is the camera on the network, is the camera account right (RTSP / ONVIF), and
is the TP-Link account password accepted by the camera's local API (SD card).
It logs in once at most and refuses to run again within a few seconds: a camera
locks logins for a while after a few wrong passwords.

The report is meant to be pasted in a GitHub issue: model, firmware, protocol
and what works, never an address, a MAC, a serial number or a password.
"""
from __future__ import annotations

import subprocess
import threading
import time

from . import camera, config, discovery, sd
from .cameras import Camera

APP_VERSION = "0.3.0"
MIN_INTERVAL = 20.0
# what a wrong TP-Link password gets (pake_share refused, see the protocol notes, 4.8)
REFUSED = (-40401,)
_last: dict[str, float] = {}
_results: dict[str, dict] = {}
_lock = threading.Lock()


class TooSoon(Exception):
    def __init__(self, wait: int):
        super().__init__("Nouveau test possible dans quelques secondes (la caméra bloque les connexions "
                         f"répétées) : {wait} s")


def _rtsp(cam: Camera, host: str) -> tuple[bool, str]:
    """Open the low-resolution stream with the camera account."""
    url = cam.rtsp_url(host, config.STREAMS["sd"])
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-i", url,
                              "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height",
                              "-of", "csv=p=0"], capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"flux RTSP injoignable ({e.__class__.__name__})"
    if out.returncode == 0 and out.stdout.strip():
        codec, *size = out.stdout.strip().split(",")
        return True, f"{codec} {'x'.join(size)}".strip()
    err = (out.stderr or "").lower()
    if "401" in err or "unauthorized" in err:
        return False, "compte caméra refusé"
    return False, "flux RTSP injoignable"


def run(cam: Camera) -> dict:
    """The connection test. Raises TooSoon when asked again too quickly."""
    now = time.monotonic()
    with _lock:
        wait = MIN_INTERVAL - (now - _last.get(cam.id, -MIN_INTERVAL))
        if wait > 0:
            raise TooSoon(int(wait) + 1)
        _last[cam.id] = now

    checks = []
    st = camera.locate(cam, force=True)
    if not st.online or not st.host:
        checks.append({"key": "network", "ok": False, "detail": "caméra introuvable sur le réseau"})
        result = {"checks": checks, "at": time.time()}
        _results[cam.id] = result
        return result
    info = discovery.probe(st.host) or {}
    checks.append({"key": "network", "ok": True,
                   "detail": " · ".join(x for x in (info.get("model") or st.model, st.host) if x)})

    if cam.cfg.user and cam.cfg.password:
        ok, detail = _rtsp(cam, st.host)
    else:
        ok, detail = False, "compte caméra non renseigné"
    checks.append({"key": "camera_account", "ok": ok, "detail": detail})

    card = sd.card_for(cam)
    if cam.cloud_password:
        card.reset()
        try:
            # through the SD cache, so the report copied right after the test has the card
            disk = card.cached("card", 30.0, lambda: card.call(lambda c: c.get_sd_status()), force=True)
            ok, detail = True, f"connecté, carte SD : {disk.get('status')}" if disk else "connecté, pas de carte SD"
        except sd.SdError as e:
            ok, detail = False, ("mot de passe du compte TP-Link refusé" if e.code in REFUSED else str(e))
    else:
        ok, detail = False, "mot de passe du compte TP-Link non renseigné"
    checks.append({"key": "tplink_account", "ok": ok, "detail": detail})

    camera.enrich_identity(cam)
    result = {"checks": checks, "at": time.time(), "protocol": cam.cfg.detected or
              (cam.cfg.protocol if cam.cfg.protocol != "auto" else None)}
    _results[cam.id] = result
    return result


def report(cam: Camera) -> dict:
    """Facts to paste in an issue. Nothing that identifies the camera or the network."""
    st = camera.locate(cam)
    info = (discovery.probe(st.host) if st.online and st.host else None) or {}
    card = cam.peek("sd")
    sd_state = None
    if card is not None:
        hit = card._cache.get("card")
        if hit and hit[1]:
            sd_state = hit[1].get("status")
    last = _results.get(cam.id)
    return {
        "app_version": APP_VERSION,
        "model": info.get("model") or st.model or cam.cfg.model or None,
        "hardware": info.get("hardware") or None,
        "firmware": info.get("firmware") or st.firmware or None,
        "device_type": info.get("device_type") or None,
        "encrypt_type": info.get("encrypt_type") or None,
        "tpap": info.get("tpap"),
        "https": info.get("https"),
        "protocol_setting": cam.cfg.protocol,
        "protocol_working": cam.cfg.detected or None,
        "online": st.online,
        "sd_card": sd_state,
        "checks": [{k: c[k] for k in ("key", "ok")} | ({"detail": c["detail"]} if c["key"] != "network" else {})
                   for c in (last or {}).get("checks", [])],
    }
