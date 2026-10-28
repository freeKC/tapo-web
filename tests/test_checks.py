"""Connection test and diagnostic report, with the camera faked out."""
from app import camera, checks, discovery, sd
from app.cameras import CameraState

from .helpers import make_camera


def test_report_has_the_card_read_by_the_connection_test(tmp_path, monkeypatch):
    cam = make_camera(tmp_path, cloud_password="secret")
    monkeypatch.setattr(camera, "locate", lambda c, force=False: CameraState(host="192.0.2.10", online=True))
    monkeypatch.setattr(camera, "enrich_identity", lambda c: None)
    monkeypatch.setattr(discovery, "probe", lambda host: {"model": "C520WS"})
    monkeypatch.setattr(checks, "_rtsp", lambda c, host: (True, "h264 640x360"))
    card = sd.card_for(cam)
    monkeypatch.setattr(card, "call", lambda fn: {"status": "normal"})

    result = checks.run(cam)
    assert result["checks"][-1] == {"key": "tplink_account", "ok": True, "detail": "connecté, carte SD : normal"}
    assert checks.report(cam)["sd_card"] == "normal"
