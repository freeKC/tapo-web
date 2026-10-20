"""The cameras this app knows about.

cameras.json (in config.CONFIG_DIR, chmod 600) holds them:

    {"version": 1, "subnet": "192.168.1", "cloud_password": "...",
     "cameras": [{"id": "cam1", "name": "Garden", "host": "192.168.1.20", "mac": "...",
                  "user": "...", "password": "...", "protocol": "auto", "data_dir": ""}]}

``cloud_password`` is the TP-Link account password: one per account, so it is
stored once and a camera only overrides it when it belongs to another account.
``user`` / ``password`` are the camera account (RTSP, ONVIF), set per camera in
the Tapo app. ``host``, ``mac`` and ``detected`` are learnt: the address is re-found by the
MAC when DHCP moves the camera, and ``detected`` is the login protocol that
worked last on a camera left on "auto". ``data_dir`` is the camera's sub-folder of
recordings/; the first camera keeps recordings/ itself, which is where a
single-camera install already has its files.

Without a cameras.json, the TAPO_* variables of .env describe the first camera
and are copied into a new cameras.json (the .env file is left as it is).

Each Camera also carries what runs for it (live streams, recorder, SD card,
...), created on first use by the modules that own them, see Camera.service().
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import config

FILE_NAME = "cameras.json"
PROTOCOLS = ("auto", "4", "3")
SECRETS = ("password", "cloud_password")
_EDITABLE = ("name", "host", "user", "password", "cloud_password", "protocol")


class CameraError(Exception):
    """User-facing configuration error (shown in the UI)."""


@dataclass
class CameraConfig:
    id: str
    name: str = ""
    host: str = ""
    mac: str = ""
    model: str = ""
    user: str = ""
    password: str = ""
    cloud_password: str = ""
    protocol: str = "auto"
    detected: str = ""              # the protocol that last worked on "auto" (tried first next time)
    data_dir: str = ""


_FIELDS = {f.name for f in fields(CameraConfig)}


@dataclass
class CameraState:
    host: str | None = None
    online: bool = False
    last_seen: float = 0.0
    model: str | None = None
    firmware: str | None = None
    checked_at: float = 0.0
    verified: bool = False          # the MAC at ``host`` was checked since it came online
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class Camera:
    def __init__(self, cfg: CameraConfig, registry: "Registry"):
        self.cfg = cfg
        self.registry = registry
        self.state = CameraState(host=cfg.host or None, model=cfg.model or None)
        self._services: dict[str, object] = {}
        self._services_lock = threading.RLock()   # a service may need another one (animals -> sd)

    @property
    def id(self) -> str:
        return self.cfg.id

    @property
    def name(self) -> str:
        return self.cfg.name or self.state.model or self.cfg.model or self.cfg.id

    @property
    def cloud_password(self) -> str:
        """TP-Link account password: control API ("admin") and media port."""
        return self.cfg.cloud_password or self.registry.cloud_password or self.cfg.password

    @property
    def subnet(self) -> str:
        return self.registry.subnet or config.DEFAULT_SUBNET

    @property
    def recordings_dir(self) -> Path:
        d = config.RECORDINGS_DIR / self.cfg.data_dir if self.cfg.data_dir else config.RECORDINGS_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def hls_dir(self) -> Path:
        return config.HLS_DIR / self.cfg.id

    def rtsp_url(self, host: str, stream_path: str, *, redacted: bool = False) -> str:
        return config.rtsp_url(host, stream_path, self.cfg.user, self.cfg.password, redacted=redacted)

    def configured(self) -> bool:
        return bool(self.cfg.user and self.cfg.password and self.cloud_password)

    def service(self, key: str, factory):
        """The camera's ``key`` service (sd card, recorder, ...), created once."""
        with self._services_lock:
            svc = self._services.get(key)
            if svc is None:
                svc = self._services[key] = factory()
            return svc

    def peek(self, key: str):
        with self._services_lock:
            return self._services.get(key)

    def stop_services(self) -> None:
        with self._services_lock:
            services = list(self._services.values())
        for svc in services:
            for name in ("stop_all", "stop"):
                stop = getattr(svc, name, None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:  # noqa: BLE001 - shutting down, best effort
                        pass
                    break

    def public(self) -> dict:
        """What the browser may see: never a password."""
        st = self.state
        return {"id": self.id, "name": self.name, "custom_name": self.cfg.name,
                "host": st.host or self.cfg.host or None, "online": st.online,
                "model": st.model or self.cfg.model or None, "firmware": st.firmware,
                "user": self.cfg.user, "protocol": self.cfg.protocol, "detected": self.cfg.detected or None,
                "has_password": bool(self.cfg.password),
                "has_cloud_password": bool(self.cfg.cloud_password or self.registry.cloud_password),
                "own_cloud_password": bool(self.cfg.cloud_password), "configured": self.configured()}


def _clean(value) -> str:
    value = str(value if value is not None else "").strip()
    if "\n" in value or "\r" in value or "\x00" in value:
        raise CameraError("requête invalide")
    return value


def normalize_mac(mac: str) -> str:
    hexes = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2)).upper() if len(hexes) == 12 else ""


class Registry:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.subnet = ""
        self.cloud_password = ""
        self._cams: dict[str, Camera] = {}
        self._load()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.subnet = str(data.get("subnet") or "")
            self.cloud_password = str(data.get("cloud_password") or "")
            for raw in data.get("cameras") or []:
                if isinstance(raw, dict) and raw.get("id"):
                    cfg = CameraConfig(**{k: str(v) for k, v in raw.items() if k in _FIELDS})
                    self._cams[cfg.id] = Camera(cfg, self)
            return
        env = config.env_camera()
        self.subnet = env["subnet"]
        self.cloud_password = env["cloud_password"]
        if env["host"] or env["user"] or env["cloud_password"]:
            protocol = env["protocol"] if env["protocol"] in PROTOCOLS else "auto"
            cfg = CameraConfig(id="cam1", host=env["host"], user=env["user"], password=env["password"],
                               protocol=protocol, data_dir="")
            self._cams[cfg.id] = Camera(cfg, self)
            self.save()

    def save(self) -> None:
        with self._lock:
            data = {"version": 1, "subnet": self.subnet, "cloud_password": self.cloud_password,
                    "cameras": [asdict(c.cfg) for c in self._cams.values()]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)       # Windows: the folder's ACL is what protects it
            except OSError:
                pass
            os.replace(tmp, self.path)

    # -- queries --------------------------------------------------------------
    def list(self) -> list[Camera]:
        with self._lock:
            return list(self._cams.values())

    def get(self, cid: str | None) -> Camera | None:
        with self._lock:
            return self._cams.get(cid or "")

    def default(self) -> Camera | None:
        with self._lock:
            return next(iter(self._cams.values()), None)

    def needs_setup(self) -> bool:
        cams = self.list()
        return not cams or not any(c.configured() for c in cams)

    # -- changes --------------------------------------------------------------
    def _next_id(self) -> str:
        n = 1
        while f"cam{n}" in self._cams:
            n += 1
        return f"cam{n}"

    def add(self, values: dict) -> Camera:
        with self._lock:
            cid = self._next_id()
            # the first camera of an install keeps recordings/ itself
            root_taken = any(not c.cfg.data_dir for c in self._cams.values())
            cfg = CameraConfig(id=cid, data_dir=cid if root_taken else "")
            cam = Camera(cfg, self)
            self._apply(cam, values, new=True)
            self._cams[cid] = cam
            self.save()
            return cam

    def update(self, cid: str, values: dict) -> Camera:
        with self._lock:
            cam = self._cams.get(cid)
            if cam is None:
                raise CameraError("caméra inconnue")
            self._apply(cam, values, new=False)
            self.save()
            return cam

    def _apply(self, cam: Camera, values: dict, new: bool) -> None:
        cfg = cam.cfg
        for key in _EDITABLE:
            if key not in values:
                continue
            val = _clean(values[key])
            if key in SECRETS and not val:
                continue                   # an empty password field keeps the stored one
            if key == "protocol" and val not in PROTOCOLS:
                raise CameraError("protocole inconnu")
            if key == "host" and val != cfg.host:
                cam.state.host, cam.state.checked_at, cam.state.verified = (val or None), 0.0, False
            setattr(cfg, key, val)
        if values.get("mac"):
            cfg.mac = normalize_mac(_clean(values["mac"])) or cfg.mac
        if values.get("model") and new:
            cfg.model = _clean(values["model"])
        if values.get("clear_cloud_password"):
            cfg.cloud_password = ""

    def remove(self, cid: str) -> bool:
        """Forget a camera. Its recordings stay on disk."""
        with self._lock:
            cam = self._cams.pop(cid, None)
            if cam is None:
                return False
            self.save()
        cam.stop_services()
        return True

    def set_account(self, values: dict) -> None:
        with self._lock:
            if "subnet" in values:
                subnet = _clean(values["subnet"]).rstrip(".")
                if subnet and not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", subnet):
                    raise CameraError("Sous-réseau invalide (attendu : 192.168.1).")
                self.subnet = subnet
            if values.get("cloud_password"):
                self.cloud_password = _clean(values["cloud_password"])
            self.save()

    def remember(self, cam: Camera, **found) -> None:
        """Store what was learnt about a camera (address, MAC, model) when it changed."""
        changed = False
        with self._lock:
            for key, val in found.items():
                if key == "mac":
                    val = normalize_mac(val or "")
                if val and getattr(cam.cfg, key) != val:
                    setattr(cam.cfg, key, val)
                    changed = True
            if changed and cam.id in self._cams:
                self.save()

    def stop_all(self) -> None:
        for cam in self.list():
            cam.stop_services()


_registry: Registry | None = None
_registry_lock = threading.Lock()


def registry() -> Registry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = Registry(config.CONFIG_DIR / FILE_NAME)
        return _registry


def set_registry(reg: Registry | None) -> None:
    """Tests (and scripts) point the app at another camera list."""
    global _registry
    with _registry_lock:
        _registry = reg

