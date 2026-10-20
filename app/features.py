"""Camera settings over the local control API: privacy mode, status LED, night
vision, motion and person detection, auto tracking, the white light and PTZ
presets. The same requests work over the V4 and the type 3 login (they are the
Tapo app's own, as in pytapo).

What a camera offers comes from getAppComponentList: a setting whose component
the camera does not list is not shown. Everything is read in one multipleRequest
(the camera dislikes being polled).
"""
from __future__ import annotations

import time

from . import sd

SENSITIVITY = {"low": "20", "normal": "50", "high": "80"}
NIGHT_VISION = ("md_night_vision", "inf_night_vision", "wtl_night_vision")   # smart, IR (b&w), color
DAY_NIGHT = ("auto", "on", "off")
_UNSUPPORTED = (-40106, -40210)      # method missing / module missing on this model
_COMPONENTS_TTL = 600.0


def _onoff(v) -> bool | None:
    return None if v is None else str(v) == "on"


def _level(raw) -> str | None:
    """A 0-100 sensitivity read back as low / normal / high."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return "low" if n <= 35 else "normal" if n <= 65 else "high"


# setting -> (component it needs, getter request, how to read it)
_READERS = {
    "privacy": ("lensMask", {"method": "getLensMaskConfig", "params": {"lens_mask": {"name": ["lens_mask_info"]}}},
                lambda r: _onoff(r["lens_mask"]["lens_mask_info"].get("enabled"))),
    "led": ("led", {"method": "getLedStatus", "params": {"led": {"name": ["config"]}}},
            lambda r: _onoff(r["led"]["config"].get("enabled"))),
    "night_vision": ("nightVisionMode", {"method": "getNightVisionModeConfig", "params": {"image": {"name": ["switch"]}}},
                     lambda r: r["image"]["switch"].get("night_vision_mode")),
    "day_night": ("dayNightMode", {"method": "getLightFrequencyInfo", "params": {"image": {"name": "common"}}},
                  lambda r: r["image"]["common"].get("inf_type")),
    "motion": ("detection", {"method": "getDetectionConfig", "params": {"motion_detection": {"name": ["motion_det"]}}},
               lambda r: {"enabled": _onoff(r["motion_detection"]["motion_det"].get("enabled")),
                          "sensitivity": _level(r["motion_detection"]["motion_det"].get("digital_sensitivity"))}),
    "person": ("personDetection", {"method": "getPersonDetectionConfig", "params": {"people_detection": {"name": ["detection"]}}},
               lambda r: {"enabled": _onoff(r["people_detection"]["detection"].get("enabled")),
                          "sensitivity": _level(r["people_detection"]["detection"].get("sensitivity"))}),
    "auto_track": ("targetTrack", {"method": "getTargetTrackConfig", "params": {"target_track": {"name": ["target_track_info"]}}},
                   lambda r: _onoff(r["target_track"]["target_track_info"].get("enabled"))),
    "white_lamp": ("whiteLamp", {"method": "getWhitelampStatus", "params": {"image": {"get_wtl_status": ["null"]}}},
                   lambda r: str(r.get("status", "0")) == "1"),
    "presets": ("ptz", {"method": "getPresetConfig", "params": {"preset": {"name": ["preset"]}}},
                lambda r: [{"id": str(i), "name": str(n)} for i, n in
                           zip(r["preset"]["preset"].get("id") or [], r["preset"]["preset"].get("name") or [])]),
}
# the order in which the night settings replace each other: one is enough
_PREFERRED_OVER = {"day_night": "night_vision"}


class SettingsError(Exception):
    pass


def components(card: sd.SdCard, force: bool = False) -> dict[str, int]:
    def produce():
        res = card.call(lambda c: c.request("getAppComponentList", {"app_component": {"name": "app_component_list"}}))
        return {c["name"]: int(c.get("version") or 0)
                for c in res.get("app_component", {}).get("app_component_list", []) if "name" in c}
    return card.cached("components", _COMPONENTS_TTL, produce, force)


def known_components(cam) -> dict[str, int] | None:
    """The component list if it was already read (no login for it), else None."""
    card = cam.peek("sd")
    hit = card._cache.get("components") if card is not None else None
    return hit[1] if hit else None


def read(card: sd.SdCard, force: bool = False) -> dict:
    """Current values of everything this camera offers ({} entries it lacks)."""
    comps = components(card)
    wanted = [k for k, (comp, _req, _get) in _READERS.items() if comp in comps]
    wanted = [k for k in wanted if _PREFERRED_OVER.get(k) not in wanted]

    def produce():
        replies = card.call(lambda c: c.multiple([_READERS[k][1] for k in wanted]))
        out = {}
        for key, reply in zip(wanted, replies):
            if reply.get("error_code") not in (0, None):
                continue                  # this firmware lacks it after all
            try:
                out[key] = _READERS[key][2](reply.get("result") or {})
            except (KeyError, TypeError, AttributeError):
                pass
        return out
    values = card.cached("settings", 30.0, produce, force)
    return {"values": values, "at": time.time()}


def _set(card: sd.SdCard, method: str, params: dict):
    try:
        card.call(lambda c: c.request(method, params))
    except sd.SdError as e:
        if e.code in _UNSUPPORTED:
            raise SettingsError("Réglage non pris en charge par cette caméra.") from e
        raise
    with card._cache_lock:
        card._cache.pop("settings", None)


def _sensitivity(value) -> str:
    if value not in SENSITIVITY:
        raise SettingsError("Sensibilité invalide (low, normal ou high).")
    return SENSITIVITY[value]


def write(card: sd.SdCard, key: str, value) -> None:
    """Change one setting. ``value``: a bool, a mode name, or for the detections
    {"enabled": bool, "sensitivity": "low" | "normal" | "high"}."""
    onoff = lambda v: "on" if v else "off"  # noqa: E731
    if key == "privacy":
        _set(card, "setLensMaskConfig", {"lens_mask": {"lens_mask_info": {"enabled": onoff(value)}}})
    elif key == "led":
        _set(card, "setLedStatus", {"led": {"config": {"enabled": onoff(value)}}})
    elif key == "auto_track":
        _set(card, "setTargetTrackConfig", {"target_track": {"target_track_info": {"enabled": onoff(value)}}})
    elif key == "night_vision":
        if value not in NIGHT_VISION:
            raise SettingsError("Mode de vision nocturne inconnu.")
        _set(card, "setNightVisionModeConfig", {"image": {"switch": {"night_vision_mode": value}}})
    elif key == "day_night":
        if value not in DAY_NIGHT:
            raise SettingsError("Mode jour / nuit inconnu.")
        _set(card, "setDayNightModeConfig", {"image": {"common": {"inf_type": value}}})
    elif key in ("motion", "person"):
        if not isinstance(value, dict):
            raise SettingsError("requête invalide")
        fields = {}
        if "enabled" in value:
            fields["enabled"] = onoff(value["enabled"])
        if value.get("sensitivity"):
            fields["digital_sensitivity" if key == "motion" else "sensitivity"] = _sensitivity(value["sensitivity"])
        if not fields:
            raise SettingsError("requête invalide")
        if key == "motion":
            _set(card, "setDetectionConfig", {"motion_detection": {"motion_det": fields}})
        else:
            _set(card, "setPersonDetectionConfig", {"people_detection": {"detection": fields}})
    elif key == "white_lamp":
        current = read(card).get("values", {}).get("white_lamp")
        if current is None or bool(value) != current:     # the camera only knows "toggle"
            _set(card, "reverseWhitelampStatus", {"image": {"reverse_wtl_status": ["null"]}})
    else:
        raise SettingsError("Réglage inconnu.")


def goto_preset(card: sd.SdCard, preset_id: str) -> None:
    if not str(preset_id).isdigit():
        raise SettingsError("Position inconnue.")
    _set(card, "motorMoveToPreset", {"preset": {"goto_preset": {"id": str(preset_id)}}})


def save_preset(card: sd.SdCard, name: str) -> None:
    name = (name or "").strip()
    if not name or len(name) > 32 or "\n" in name:
        raise SettingsError("Nom de position invalide.")
    # "Postion" is the camera's own spelling
    _set(card, "addMotorPostion", {"preset": {"set_preset": {"name": name, "save_ptz": "1"}}})


def delete_preset(card: sd.SdCard, preset_id: str) -> None:
    if not str(preset_id).isdigit():
        raise SettingsError("Position inconnue.")
    _set(card, "deletePreset", {"preset": {"remove_preset": {"id": [str(preset_id)]}}})
