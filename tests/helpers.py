"""Shared test helpers: a camera list in a temporary folder."""
from app import cameras


def make_camera(tmp_path, **values):
    """A registered camera whose files all live under ``tmp_path``."""
    reg = cameras.Registry(tmp_path / "cameras.json")
    cam = reg.add({"name": "test", "host": "192.0.2.10", "user": "u", "password": "p", **values})
    cam.cfg.id = cam.cfg.data_dir = "test-" + tmp_path.name    # its own folders
    return cam
