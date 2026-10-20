"""Finding Tapo cameras on the LAN.

Two probes, both harmless (no login, nothing a camera counts as an attempt):

* TCP 443 + the TP-Link device certificate (CN "TPRI-DEVICE") and the RTSP port:
  tells a camera from anything else, works through WSL's NAT;
* the TP-Link discovery query on UDP 20002, sent to one address: the camera
  answers with its model, MAC, firmware and how it wants to be logged into
  (``encrypt_type``, ``tpap``). python-kasa builds and decodes that query.

The subnet is scanned host by host rather than with a broadcast, which does not
leave a WSL or Docker Desktop network.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor

from . import config
from .cameras import normalize_mac

logging.getLogger("kasa").setLevel(logging.CRITICAL)


def port_open(ip: str, port: int, timeout: float = 1.0) -> bool:
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


def is_camera(ip: str) -> bool:
    """A host is a Tapo camera if it presents the TPRI cert AND speaks RTSP."""
    der = _cert_der(ip)
    if not der or b"TPRI" not in der:
        return False
    return port_open(ip, config.RTSP_PORT, timeout=1.5)


def scan_subnet(subnet: str) -> list[str]:
    """Every Tapo camera of ``subnet`` (e.g. "192.168.1"), by address."""
    candidates = [f"{subnet}.{h}" for h in range(1, 255)]
    # Stage 1: who even has 443 open (cheap), then cert-check only those.
    with ThreadPoolExecutor(max_workers=64) as ex:
        live = [ip for ip, ok in zip(candidates, ex.map(lambda i: port_open(i, 443, 0.8), candidates)) if ok]
    with ThreadPoolExecutor(max_workers=16) as ex:
        return [ip for ip, ok in zip(live, ex.map(is_camera, live)) if ok]


def describe(raw: dict, ip: str) -> dict:
    """The parts of a discovery answer the app uses. ``protocol_hint``: "4" when
    the camera advertises the newer login (TPAP / encrypt_type 4), else "3"."""
    res = raw.get("result", raw) if isinstance(raw, dict) else {}
    enc = res.get("encrypt_type") or []
    enc = [str(e) for e in (enc if isinstance(enc, list) else [enc])]
    tpap = res.get("tpap") if isinstance(res.get("tpap"), dict) else None
    mgt = res.get("mgt_encrypt_schm") if isinstance(res.get("mgt_encrypt_schm"), dict) else {}
    return {
        "ip": res.get("ip") or ip,
        "mac": normalize_mac(res.get("mac") or ""),
        "model": res.get("device_model") or "",
        "name": res.get("device_name") or "",
        "device_type": res.get("device_type") or "",
        "firmware": res.get("firmware_version") or "",
        "hardware": res.get("hardware_version") or "",
        "encrypt_type": enc,
        "tpap": tpap,
        "https": bool(mgt.get("is_support_https")),
        "protocol_hint": "4" if (tpap or "4" in enc) else "3",
    }


def probe(ip: str, timeout: int = 3) -> dict | None:
    """One discovery query to ``ip``. None when nothing answered."""
    try:
        from kasa import Discover
    except ImportError:
        return None
    raw: dict = {}

    def keep(r):
        raw.update(r.get("discovery_response") or {})

    async def ask():
        try:
            await Discover.discover_single(ip, discovery_timeout=timeout, on_discovered_raw=keep)
        except Exception:  # noqa: BLE001 - unsupported model, timeout...: the raw answer is what counts
            pass

    try:
        asyncio.run(ask())
    except RuntimeError:                 # called from inside an event loop: use a thread
        with ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(asyncio.run, ask()).result()
    return describe(raw, ip) if raw else None


def scan(subnet: str) -> list[dict]:
    """Cameras of the subnet, each with what discovery says about it (or only its
    address when the UDP query got no answer)."""
    ips = scan_subnet(subnet)
    with ThreadPoolExecutor(max_workers=8) as ex:
        found = list(ex.map(probe, ips))
    return [info or {"ip": ip, "mac": "", "model": "", "name": "", "firmware": "", "protocol_hint": ""}
            for ip, info in zip(ips, found)]
