"""Every test run gets its own config and data folders: nothing touches the
real cameras.json, recordings or a running server's HLS output."""
import os
import tempfile

_ROOT = tempfile.mkdtemp(prefix="tapo-web-tests-")
os.environ["TAPO_CONFIG_DIR"] = os.path.join(_ROOT, "config")
os.environ["TAPO_DATA_DIR"] = os.path.join(_ROOT, "data")
os.environ["TAPO_HLS_DIR"] = os.path.join(_ROOT, "hls")
for _var in ("TAPO_HOST", "TAPO_USER", "TAPO_PASSWORD", "TAPO_CLOUD_PASSWORD", "TAPO_SUBNET",
             "TAPO_CONTROL_PROTOCOL"):
    os.environ.pop(_var, None)
