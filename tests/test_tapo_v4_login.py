"""Registration failures must remain API errors, not missing-field crashes."""
import pytest

from app.tapo_v4 import TapoV4, TapoV4Error


@pytest.mark.parametrize("response, code", [
    ({"error_code": -40413, "result": {"data": {"encrypt_type": ["3"]}}}, -40413),
    ({"error_code": -40404, "result": {"data": {"sec_left": 1800}}}, -40404),
    ({"error_code": -40211}, -40211),
])
def test_registration_error_preserves_camera_code(monkeypatch, response, code):
    camera = TapoV4("camera.invalid", "unused")
    calls = []

    def register(params):
        calls.append(params["sub_method"])
        return response

    monkeypatch.setattr(camera, "_post_login", register)
    with pytest.raises(TapoV4Error) as error:
        camera.login()
    assert error.value.code == code
    assert calls == ["pake_register"]
    assert not camera.logged_in()


@pytest.mark.parametrize("result", [None, [], "unexpected", {},
    *[{k: "unused" for k in ("dev_salt", "dev_random", "iterations", "dev_share") if k != missing}
      for missing in ("dev_salt", "dev_random", "iterations", "dev_share")],
])
def test_incomplete_registration_is_a_controlled_error(monkeypatch, result):
    camera = TapoV4("camera.invalid", "unused")
    monkeypatch.setattr(camera, "_post_login", lambda _: {"error_code": 0, "result": result})
    with pytest.raises(TapoV4Error, match="incomplete handshake"):
        camera.login()
