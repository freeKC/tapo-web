"""The camera list, its migration from .env, locating by MAC, and the routes."""
import json

import pytest

from app import camera, cameras, config, discovery


def test_env_camera_becomes_the_first_camera(tmp_path, monkeypatch):
    for k, v in {"TAPO_HOST": "192.0.2.5", "TAPO_USER": "cam", "TAPO_PASSWORD": "a;b",
                 "TAPO_CLOUD_PASSWORD": "cloud", "TAPO_SUBNET": "192.0.2", "TAPO_CONTROL_PROTOCOL": "3"}.items():
        monkeypatch.setenv(k, v)
    reg = cameras.Registry(tmp_path / "cameras.json")
    cam = reg.default()
    assert (cam.id, cam.cfg.host, cam.cfg.user, cam.cfg.password, cam.cfg.protocol) == ("cam1", "192.0.2.5", "cam", "a;b", "3")
    assert cam.cloud_password == "cloud" and cam.subnet == "192.0.2"
    assert cam.recordings_dir == config.RECORDINGS_DIR          # where a single-camera install has its files
    saved = json.loads((tmp_path / "cameras.json").read_text())
    assert saved["cameras"][0]["password"] == "a;b"
    assert cameras.Registry(tmp_path / "cameras.json").default().cfg.host == "192.0.2.5"   # read back


def test_nothing_configured_means_setup(tmp_path):
    reg = cameras.Registry(tmp_path / "cameras.json")
    assert reg.list() == [] and reg.needs_setup() and not (tmp_path / "cameras.json").exists()


def test_add_update_remove(tmp_path):
    reg = cameras.Registry(tmp_path / "cameras.json")
    a = reg.add({"name": "Garden", "host": "192.0.2.10", "user": "u", "password": "p"})
    b = reg.add({"name": "Garage", "user": "u2", "password": "p2", "cloud_password": "other"})
    assert (a.id, b.id) == ("cam1", "cam2")
    assert a.cfg.data_dir == "" and b.cfg.data_dir == "cam2"   # only the first one uses recordings/ itself
    reg.set_account({"cloud_password": "tp", "subnet": "192.0.2."})
    assert a.cloud_password == "tp" and b.cloud_password == "other" and reg.subnet == "192.0.2"
    reg.update("cam1", {"password": "", "name": "Front"})      # an empty password keeps the old one
    assert a.cfg.password == "p" and a.name == "Front"
    with pytest.raises(cameras.CameraError):
        reg.update("cam1", {"protocol": "5"})
    with pytest.raises(cameras.CameraError):
        reg.update("cam1", {"name": "two\nlines"})
    assert reg.remove("cam1") and not reg.remove("cam1")
    assert [c.id for c in cameras.Registry(tmp_path / "cameras.json").list()] == ["cam2"]
    assert reg.add({"user": "x", "password": "y"}).id == "cam1"


def test_public_view_never_contains_a_password(tmp_path):
    reg = cameras.Registry(tmp_path / "cameras.json")
    cam = reg.add({"user": "u", "password": "secret-1", "cloud_password": "secret-2"})
    assert "secret" not in json.dumps(cam.public())


def test_located_by_mac_after_a_dhcp_change(tmp_path, monkeypatch):
    reg = cameras.Registry(tmp_path / "cameras.json")
    cam = reg.add({"host": "192.0.2.10", "user": "u", "password": "p", "mac": "aa-bb-cc-dd-ee-02"})
    cameras_at = {"192.0.2.10": "AA:BB:CC:DD:EE:01", "192.0.2.11": "AA:BB:CC:DD:EE:02"}   # it moved to .11
    monkeypatch.setattr(discovery, "is_camera", lambda ip: ip in cameras_at)
    monkeypatch.setattr(discovery, "scan_subnet", lambda subnet: list(cameras_at))
    monkeypatch.setattr(discovery, "probe", lambda ip: {"mac": cameras_at[ip], "model": "C510W"})
    monkeypatch.setattr(cameras, "registry", lambda: reg)
    monkeypatch.setattr(camera, "registry", lambda: reg)
    st = camera.locate(cam, force=True)
    assert st.online and st.host == "192.0.2.11" and cam.cfg.host == "192.0.2.11"


def test_mac_is_learnt_the_first_time(tmp_path, monkeypatch):
    reg = cameras.Registry(tmp_path / "cameras.json")
    cam = reg.add({"host": "192.0.2.10", "user": "u", "password": "p"})
    monkeypatch.setattr(discovery, "is_camera", lambda ip: ip == "192.0.2.10")
    monkeypatch.setattr(discovery, "probe", lambda ip: {"mac": "AA-BB-CC-DD-EE-01", "model": "C200"})
    camera.locate(cam, force=True)
    assert cam.cfg.mac == "AA:BB:CC:DD:EE:01" and cam.cfg.model == "C200"


def test_discovery_answer_is_reduced_to_what_the_app_needs():
    raw = {"result": {"device_model": "C510W", "mac": "AA-BB-CC-DD-EE-FF", "encrypt_type": ["4"],
                      "tpap": {"pake": [2], "tls": 1, "port": 443}, "firmware_version": "1.3.4",
                      "mgt_encrypt_schm": {"is_support_https": True}, "ip": "192.0.2.7"}}
    info = discovery.describe(raw, "192.0.2.7")
    assert info["protocol_hint"] == "4" and info["mac"] == "AA:BB:CC:DD:EE:FF" and info["https"]
    assert discovery.describe({"result": {"encrypt_type": ["3"]}}, "x")["protocol_hint"] == "3"


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx2")
    from fastapi.testclient import TestClient
    from app import main

    reg = cameras.Registry(tmp_path / "cameras.json")
    cameras.set_registry(reg)
    monkeypatch.setattr(main, "_is_local", lambda request: True)
    yield TestClient(main.app), reg, monkeypatch
    cameras.set_registry(None)


def test_routes_per_camera_and_for_the_first_one(client):
    c, reg, _ = client
    assert c.get("/api/status").status_code == 503              # nothing configured yet
    r = c.post("/api/cameras", json={"name": "Garden", "user": "u", "password": "p"})
    assert r.status_code == 200 and r.json()["id"] == "cam1" and "password" not in r.json()
    c.post("/api/cameras", json={"name": "Garage", "user": "u", "password": "p"})
    assert [x["name"] for x in c.get("/api/cameras").json()["cameras"]] == ["Garden", "Garage"]
    assert c.get("/cams/cam2/api/recordings").json() == {"recordings": []}
    assert c.get("/api/recordings").json() == {"recordings": []}
    assert c.get("/cams/nope/api/recordings").status_code == 404
    assert c.put("/api/cameras/cam2", json={"name": "Shed"}).json()["name"] == "Shed"
    assert c.delete("/api/cameras/cam2").status_code == 200
    assert c.delete("/api/cameras/cam2").status_code == 404


def test_lan_visitors_cannot_change_anything_once_configured(client):
    c, reg, monkeypatch = client
    from app import main
    reg.add({"user": "u", "password": "p"})
    monkeypatch.setattr(main, "_is_local", lambda request: False)
    assert c.get("/api/cameras").json()["can_edit"] is False
    assert c.post("/api/cameras", json={"user": "x", "password": "y"}).status_code == 403
    assert c.put("/api/cameras/cam1", json={"host": "192.0.2.66"}).status_code == 403
    assert c.delete("/api/cameras/cam1").status_code == 403
    assert c.post("/api/setup", json={"cloud_password": "x"}).status_code == 403
    assert c.post("/api/discovery", json={}).status_code == 403
