"""ONVIF control (port 2020). ONVIF authenticates with the Camera Account and
works with the plain camera account. We use it for
device identity and, on pan/tilt models like the C510W, PTZ moves.

Everything is best-effort and returns None / False rather than raising, so the
UI degrades gracefully on models or firmwares that don't expose a given service.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.request
from xml.etree import ElementTree as ET

from . import config

_NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
}


def _wsse(user: str, pw: str) -> str:
    nonce = secrets.token_bytes(16)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + pw.encode()).digest()
    ).decode()
    b64nonce = base64.b64encode(nonce).decode()
    return (
        '<s:Header><Security s:mustUnderstand="1" '
        'xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        "<UsernameToken><Username>{u}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{d}</Password>'
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{n}</Nonce>'
        "<Created xmlns=\"http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd\">{c}</Created>"
        "</UsernameToken></Security></s:Header>"
    ).format(u=user, d=digest, n=b64nonce, c=created)


def _soap(host: str, body: str, *, auth: bool = True, timeout: float = 8.0) -> str | None:
    header = _wsse(config.CAM_USER, config.CAM_PASSWORD) if auth else ""
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
        'xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        f"{header}<s:Body>{body}</s:Body></s:Envelope>"
    )
    url = f"http://{host}:{config.ONVIF_PORT}/onvif/device_service"
    req = urllib.request.Request(
        url, data=envelope.encode(),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode(errors="replace")
    except Exception:
        return None


def onvif_device_information(host: str) -> dict | None:
    body = "<tds:GetDeviceInformation/>"
    xml = _soap(host, body)
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
        out = {}
        for tag in ("Manufacturer", "Model", "FirmwareVersion", "SerialNumber", "HardwareId"):
            el = root.find(f".//tds:{tag}", _NS)
            if el is not None and el.text:
                out[tag] = el.text
        return out or None
    except ET.ParseError:
        return None


# --- PTZ -------------------------------------------------------------------
_ptz_token_cache: dict[str, str] = {}


def _profile_token(host: str) -> str | None:
    if host in _ptz_token_cache:
        return _ptz_token_cache[host]
    xml = _soap(host, "<trt:GetProfiles xmlns:trt=\"http://www.onvif.org/ver10/media/wsdl\"/>")
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
        prof = root.find(".//trt:Profiles", _NS)
        if prof is None:
            prof = root.find(".//{http://www.onvif.org/ver10/media/wsdl}Profiles")
        if prof is not None:
            token = prof.get("token")
            if token:
                _ptz_token_cache[host] = token
                return token
    except ET.ParseError:
        return None
    return None


# Map a direction to a normalized ContinuousMove velocity vector.
_MOVES = {
    "left": (-0.6, 0.0), "right": (0.6, 0.0),
    "up": (0.0, 0.6), "down": (0.0, -0.6),
    "upleft": (-0.6, 0.6), "upright": (0.6, 0.6),
    "downleft": (-0.6, -0.6), "downright": (0.6, -0.6),
}


def ptz_move(host: str, direction: str, duration: float = 0.6) -> bool:
    if direction not in _MOVES:
        return False
    token = _profile_token(host)
    if not token:
        return False
    x, y = _MOVES[direction]
    body = (
        f'<tptz:ContinuousMove><tptz:ProfileToken>{token}</tptz:ProfileToken>'
        f'<tptz:Velocity><tt:PanTilt x="{x}" y="{y}" '
        'xmlns:tt="http://www.onvif.org/ver10/schema"/></tptz:Velocity></tptz:ContinuousMove>'
    )
    if _soap(host, body) is None:
        return False
    # Auto-stop after a short nudge so a click = a small step.
    time.sleep(min(duration, 2.0))
    ptz_stop(host)
    return True


def ptz_stop(host: str) -> bool:
    token = _profile_token(host)
    if not token:
        return False
    body = (
        f'<tptz:Stop><tptz:ProfileToken>{token}</tptz:ProfileToken>'
        '<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop>'
    )
    return _soap(host, body) is not None


def ptz_available(host: str) -> bool:
    return _profile_token(host) is not None
