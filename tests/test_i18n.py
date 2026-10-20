"""Run the dependency-free frontend checks when Node is available."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is needed for frontend tests")
def test_browser_localization():
    subprocess.run(["node", "--test", str(Path(__file__).with_suffix(".cjs"))], check=True)
