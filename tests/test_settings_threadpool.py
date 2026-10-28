"""Pytapo's synchronous bridge and the subnet scan must not run inside the ASGI event loop."""
import asyncio
import json

from starlette.requests import Request

from app import main


def request(body):
    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode()}
    return Request({"type": "http", "headers": []}, receive)


def test_settings_and_presets_execute_control_off_event_loop(monkeypatch):
    calls = []
    def check_thread(*args):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            calls.append(args)
            return {"values": {"led": True}}
        raise AssertionError("Camera control is running inside the ASGI event loop")
    monkeypatch.setattr(main.sd, "card_for", lambda _: object())
    for method in ("read", "write", "save_preset"):
        monkeypatch.setattr(main.features, method, check_thread)
    assert asyncio.run(main.camera_settings_set(request({"led": True}), object())) == {"values": {"led": True}}
    assert asyncio.run(main.preset_save(request({"name": "test"}), object())) == {"values": {"led": True}}
    assert len(calls) == 4


def test_discovery_scan_runs_off_event_loop(monkeypatch):
    def scan(subnet):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return [{"ip": subnet + ".20"}]
        raise AssertionError("The subnet scan is running inside the ASGI event loop")
    monkeypatch.setattr(main, "_check_edit", lambda request: None)
    monkeypatch.setattr(main.discovery, "scan", scan)
    result = asyncio.run(main.discovery_scan(request({"subnet": "192.0.2"})))
    assert result["subnet"] == "192.0.2"
    assert [d["ip"] for d in result["devices"]] == ["192.0.2.20"]
