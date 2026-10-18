"""Exercise the real Windows ffmpeg audio transport without a camera."""
import json
import os
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest

from app.sd import _Mux


@pytest.mark.skipif(os.name != "nt" or not shutil.which("ffmpeg"), reason="Windows FFmpeg integration")
@pytest.mark.parametrize("duration", [3, 62])
def test_sd_mux_keeps_video_and_audio(tmp_path, duration):
    video = tmp_path / "video.ts"
    audio = tmp_path / "audio.alaw"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=c=black:s=128x128:r=10:d={duration}", "-c:v", "libx264",
        "-f", "mpegts", str(video),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=8000:duration={duration}",
        "-c:a", "pcm_alaw", "-f", "alaw", str(audio),
    ], check=True)
    output = tmp_path / "clip.mp4"
    mux = _Mux(SimpleNamespace(hls_dir=tmp_path, playlist=tmp_path / "index.m3u8"), output)
    try:
        mux.start(has_audio=True, audio_offset=0)
        video_bytes, audio_bytes = video.read_bytes(), audio.read_bytes()
        errors = []

        def feed():
            try:
                # Small interleaved packets exercise bounded queues rather than
                # pre-buffering the whole clip (which hides probing deadlocks).
                count = (len(video_bytes) + 187) // 188
                for i in range(count):
                    mux.video(video_bytes[i * 188:(i + 1) * 188])
                    mux.audio(audio_bytes[i * len(audio_bytes) // count:
                                          (i + 1) * len(audio_bytes) // count])
            except Exception as exc:
                errors.append(exc)

        producer = threading.Thread(target=feed, daemon=True)
        producer.start()
        producer.join(timeout=15)
        assert not producer.is_alive(), "Interleaved input stalled during FFmpeg probing"
        assert not errors
        mux.finish()
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
            "-of", "json", str(output),
        ], check=True, capture_output=True, text=True)
        assert {s["codec_name"] for s in json.loads(result.stdout)["streams"]} == {"h264", "aac"}
        assert "#EXT-X-ENDLIST" in (tmp_path / "index.m3u8").read_text()
    finally:
        mux.abort()
