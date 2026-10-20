"""Camera settings: what is read, what is sent (no camera: a fake control client)."""
import pytest

from app import features, sd
from app.tapo_v4 import TapoV4Error

from .helpers import make_camera

COMPONENTS = {"app_component": {"app_component_list": [
    {"name": n, "version": 1} for n in ("lensMask", "led", "nightVisionMode", "dayNightMode", "detection",
                                         "personDetection", "whiteLamp", "ptz")]}}
REPLIES = {
    "getLensMaskConfig": {"lens_mask": {"lens_mask_info": {"enabled": "off"}}},
    "getLedStatus": {"led": {"config": {"enabled": "on"}}},
    "getNightVisionModeConfig": {"image": {"switch": {"night_vision_mode": "md_night_vision"}}},
    "getDetectionConfig": {"motion_detection": {"motion_det": {"enabled": "on", "digital_sensitivity": "80"}}},
    "getWhitelampStatus": {"status": 0, "rest_time": 0},
    "getPresetConfig": {"preset": {"preset": {"id": ["1", "2"], "name": ["Gate", "Lawn"]}}},
}


class FakeClient:
    def __init__(self):
        self.sent = []

    def request(self, method, params=None):
        self.sent.append((method, params))
        if method == "getAppComponentList":
            return COMPONENTS
        return {}

    def multiple(self, reqs):
        out = []
        for r in reqs:
            if r["method"] in REPLIES:
                out.append({"method": r["method"], "result": REPLIES[r["method"]], "error_code": 0})
            else:
                out.append({"method": r["method"], "error_code": -40106})   # personDetection: missing here
        return out


@pytest.fixture
def card(tmp_path, monkeypatch):
    c = sd.SdCard(make_camera(tmp_path))
    client = FakeClient()
    monkeypatch.setattr(c, "api", lambda: client)
    c.client = client
    return c


def test_reads_what_the_camera_offers(card):
    v = features.read(card)["values"]
    assert v["privacy"] is False and v["led"] is True and v["white_lamp"] is False
    assert v["night_vision"] == "md_night_vision" and "day_night" not in v     # one night setting is enough
    assert v["motion"] == {"enabled": True, "sensitivity": "high"}
    assert "person" not in v and "auto_track" not in v                            # refused / not listed
    assert v["presets"] == [{"id": "1", "name": "Gate"}, {"id": "2", "name": "Lawn"}]


@pytest.mark.parametrize("key,value,method,params", [
    ("privacy", True, "setLensMaskConfig", {"lens_mask": {"lens_mask_info": {"enabled": "on"}}}),
    ("led", False, "setLedStatus", {"led": {"config": {"enabled": "off"}}}),
    ("night_vision", "wtl_night_vision", "setNightVisionModeConfig", {"image": {"switch": {"night_vision_mode": "wtl_night_vision"}}}),
    ("motion", {"sensitivity": "low"}, "setDetectionConfig", {"motion_detection": {"motion_det": {"digital_sensitivity": "20"}}}),
    ("person", {"enabled": True}, "setPersonDetectionConfig", {"people_detection": {"detection": {"enabled": "on"}}}),
    ("white_lamp", True, "reverseWhitelampStatus", {"image": {"reverse_wtl_status": ["null"]}}),
])
def test_writes_the_apps_own_requests(card, key, value, method, params):
    features.write(card, key, value)
    assert card.client.sent[-1] == (method, params)


def test_white_lamp_is_not_toggled_when_already_as_asked(card):
    features.write(card, "white_lamp", False)                 # it is off already
    assert not any(m == "reverseWhitelampStatus" for m, _ in card.client.sent)


@pytest.mark.parametrize("key,value", [("night_vision", "disco"), ("motion", {"sensitivity": "max"}),
                                       ("motion", "on"), ("nope", True), ("day_night", "dusk")])
def test_invalid_values_never_reach_the_camera(card, key, value):
    with pytest.raises(features.SettingsError):
        features.write(card, key, value)
    assert not any(m.startswith("set") for m, _ in card.client.sent)


def test_presets(card):
    features.goto_preset(card, "2")
    assert card.client.sent[-1] == ("motorMoveToPreset", {"preset": {"goto_preset": {"id": "2"}}})
    features.save_preset(card, "Gate")
    assert card.client.sent[-1] == ("addMotorPostion", {"preset": {"set_preset": {"name": "Gate", "save_ptz": "1"}}})
    with pytest.raises(features.SettingsError):
        features.goto_preset(card, "../1")
    with pytest.raises(features.SettingsError):
        features.save_preset(card, "")


def test_unsupported_setting_is_a_clear_error(card, monkeypatch):
    def refuse(method, params=None):
        raise TapoV4Error(-40210, "method failed")
    monkeypatch.setattr(card.client, "request", refuse)
    with pytest.raises(features.SettingsError, match="non pris en charge"):
        features.write(card, "led", True)
