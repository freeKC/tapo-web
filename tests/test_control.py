"""Offline protocol selection, query reuse and retry protection."""
import pytest

from app import control
from app.tapo_v4 import TapoV4, TapoV4Error


def install_client(monkeypatch, response):
    instances, requests = [], []

    class FakeTapo:
        def __init__(self, *args, **kwargs):
            instances.append((args, kwargs))

        def performRequest(self, request):
            requests.append(request)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(control, "Tapo", FakeTapo)
    return instances, requests


def test_default_remains_v4(monkeypatch):
    monkeypatch.delenv("TAPO_CONTROL_PROTOCOL", raising=False)
    assert type(control.create_control("camera", "unused")) is TapoV4


def test_type3_is_lazy_and_reuses_client_and_sd_helpers(monkeypatch):
    monkeypatch.setenv("TAPO_CONTROL_PROTOCOL", "3")
    instances, requests = install_client(monkeypatch, {"error_code": 0, "result": {
        "responses": [{"error_code": 0, "result": {
            "harddisk_manage": {"hd_info": [{"hd1": {"status": "normal"}}]},
        }}],
    }})
    camera = control.create_control("camera", "unused")
    assert not instances
    assert camera.get_sd_status() == {"status": "normal"}
    assert camera.get_sd_status() == {"status": "normal"}
    assert len(instances) == 1
    assert instances[0][0] == ("camera", "admin", "unused")
    assert instances[0][1]["retryStok"] is False
    assert requests[0]["params"]["requests"][0]["method"] == "getSdCardStatus"


def test_invalid_selection_does_not_connect(monkeypatch):
    monkeypatch.setenv("TAPO_CONTROL_PROTOCOL", "auto")
    with pytest.raises(TapoV4Error, match="must be 3 or 4"):
        control.create_control("camera", "unused")


@pytest.mark.parametrize("message,delay,code", [
    ("Temporary Suspension: Try again in 1800 seconds", 1800, -40404),
    ("Invalid authentication data; private-session-value", 60, None),
])
def test_connection_failure_cools_down_and_redacts(monkeypatch, message, delay, code):
    now = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: now[0])
    instances, _ = install_client(monkeypatch, Exception(message))
    camera = control.TapoV3("camera", "unused")
    with pytest.raises(TapoV4Error) as first:
        camera.get_sd_status()
    assert first.value.code == code
    assert "private-session-value" not in str(first.value)
    assert first.value.__suppress_context__
    now[0] += delay - 1
    with pytest.raises(TapoV4Error, match="cooldown"):
        camera.get_sd_status()
    assert len(instances) == 1
    now[0] += 2
    with pytest.raises(TapoV4Error):
        camera.get_sd_status()
    assert len(instances) == 2


def test_per_method_error_preserves_code(monkeypatch):
    install_client(monkeypatch, {"result": {"responses": [{"error_code": -71103}]}})
    with pytest.raises(TapoV4Error) as error:
        control.TapoV3("camera", "unused").get_sd_status()
    assert error.value.code == -71103


@pytest.mark.parametrize("response", [None, {}, {"result": None},
    {"result": {"responses": "invalid"}}, {"error_code": -40401},
])
def test_invalid_envelope_is_controlled(monkeypatch, response):
    install_client(monkeypatch, response)
    with pytest.raises(TapoV4Error):
        control.TapoV3("camera", "unused").get_sd_status()
