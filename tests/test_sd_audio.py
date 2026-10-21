"""A clip through the real ffmpeg: the audio must keep its kind, rate and pitch.

The C510W sends G.711 A-law at 8 kHz, other models u-law and sometimes 16 kHz
(the two-lens C575D, with the audio in a program of its own). Decoding u-law as
A-law gives noise, and 16 kHz read as 8 kHz plays at half speed for twice as long.
"""
import array
import shutil
import subprocess

import pytest

from app import sd
from app.tapo_media import ClipDemuxer

from .test_tapo_media import packets, pat, pes, pmt

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="needs ffmpeg")
SECONDS, TONE = 3, 440


def _ffmpeg(*args) -> bytes:
    out = subprocess.run(["ffmpeg", "-v", "error", *args, "-"], capture_output=True, timeout=60)
    if out.returncode != 0:
        pytest.skip("ffmpeg cannot build the sample: " + out.stderr.decode()[-200:])
    return out.stdout


def camera_like_ts(audio_type: int, fmt: str, rate: int) -> bytes:
    """Real H.264 TS from ffmpeg, its tables replaced by camera-like ones, and the
    tone as G.711 PES on a PID of its own, interleaved like the camera does."""
    video = _ffmpeg("-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={SECONDS}",
                    "-c:v", "libx264", "-g", "10", "-bf", "0", "-f", "mpegts")
    tone = _ffmpeg("-f", "lavfi", "-i", f"sine=frequency={TONE}:sample_rate={rate}:duration={SECONDS}",
                   "-f", fmt, "-ar", str(rate), "-ac", "1")
    pkts = [video[i:i + 188] for i in range(0, len(video) - 187, 188)]
    vpid = 0x100
    pkts = [p for p in pkts if ((p[1] & 0x1F) << 8 | p[2]) == vpid]      # drop ffmpeg's PAT/PMT/SDT
    head = pat([(1, 0x1000), (2, 0x13)]) + pmt(0x1000, 1, [(0x1B, vpid)]) + pmt(0x13, 2, [(audio_type, 0x54)])
    step = rate // 50
    chunks = [tone[i:i + step] for i in range(0, len(tone), step)]
    out, per = bytearray(head), len(pkts) / len(chunks)
    for i, chunk in enumerate(chunks):
        out += b"".join(pkts[int(i * per):int((i + 1) * per)])
        out += packets(0x54, pes(0xC0, 90000 + i * 1800, chunk))
    return bytes(out)


def mux(tmp_path, ts: bytes):
    job = sd.Job("1789000000-1789000003", "download", tmp_path / "hls")
    job.hls_dir.mkdir(parents=True)
    part = tmp_path / "clip.mp4"
    m = sd._Mux(job, part)
    d = ClipDemuxer(m.video, m.audio)
    d.feed(ts)
    m.start(has_audio=d.audio_format is not None, audio_offset=d.audio_offset,
            audio_format=d.audio_format or "alaw", audio_rate=d.audio_rate or 8000)
    m.finish()
    return part


def audio_of(path):
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30).stdout.strip()
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "s16le",
                          "-ac", "1", "-ar", "16000", "-"], capture_output=True, timeout=30).stdout
    samples = array.array("h", pcm)[1600:-1600]                          # skip the AAC ramps
    crossings = sum(1 for a, b in zip(samples, samples[1:]) if (a < 0) != (b < 0))
    return float(probe), crossings / 2 / (len(samples) / 16000)


@pytest.mark.parametrize("audio_type,fmt,rate", [(0x90, "alaw", 8000),     # C510W
                                                 (0x91, "mulaw", 8000),    # C310
                                                 (0x91, "mulaw", 16000)])  # C575D
def test_clip_audio_keeps_its_length_and_pitch(tmp_path, audio_type, fmt, rate):
    duration, pitch = audio_of(mux(tmp_path, camera_like_ts(audio_type, fmt, rate)))
    assert abs(duration - SECONDS) < 0.25
    assert abs(pitch - TONE) < 15
