"""Service-layer logic that needs no camera: ids, DST, job bookkeeping."""
import threading
import time
from zoneinfo import ZoneInfo

import pytest

from app import sd


@pytest.fixture(autouse=True)
def brussels(monkeypatch):
    monkeypatch.setattr(sd, "_tz", ZoneInfo("Europe/Brussels"))


@pytest.mark.parametrize("bad", ["", "abc", "1-2", "1789688911_1789688977", "../../etc/passwd",
                                 "1789688911-1789688911", "1789688977-1789688911",
                                 "1789688911-1799688911", "1789688911-1789688977/../x"])
def test_parse_id_rejects_garbage_and_traversal(bad):
    with pytest.raises(sd.SdError):
        sd.parse_id(bad)


def test_parse_id_and_download_name():
    assert sd.parse_id("1789688911-1789688977") == (1789688911, 1789688977)
    assert sd.download_name("1789688911-1789688977") == "tapo_sd_2026-09-18_01-48-31_66s.mp4"


def test_day_bounds_follow_dst():
    lo, hi = sd._day_bounds("20261025")          # 25-hour day in Brussels
    assert hi - lo + 1 == 25 * 3600
    lo, hi = sd._day_bounds("20260329")          # 23-hour day
    assert hi - lo + 1 == 23 * 3600


def test_timeline_position_is_wall_clock_on_dst_days(monkeypatch):
    lo, _ = sd._day_bounds("20261025")
    noon = lo + 13 * 3600                        # 12:00 local on the 25 h day = 13 h elapsed
    monkeypatch.setattr(sd, "_list_day", lambda d: [
        {"startTime": noon, "endTime": noon + 60, "video_type": "6"},
        {"startTime": lo - 30, "endTime": lo + 30, "vedio_type": 2}])      # spans midnight
    sd._cache.clear()
    clips = sd.recordings("20261025", force=True)["clips"]
    assert [c["day_second"] for c in clips] == [0, 12 * 3600]
    assert clips[1]["time"] == "12:00:00" and clips[1]["type_label"] == "personne"
    assert clips[0]["type_label"] == "mouvement"


def test_listing_falls_back_only_when_method_is_refused(monkeypatch):
    calls = []

    def fake_call(fn):
        calls.append(1)
        raise sd.SdError("offline")              # code None = connectivity
    monkeypatch.setattr(sd, "_call", fake_call)
    with pytest.raises(sd.SdError):
        sd._list_day("20260918")
    assert len(calls) == 1                       # no second camera round trip / login


class _BlockedFetcher(sd.Fetcher):
    """Worker that parks inside _run until released (no camera involved)."""

    def __init__(self):
        self.release = threading.Event()
        self.ran = []
        super().__init__()

    def _run(self, job):
        self.ran.append(job)
        job.state = "connecting"
        self.release.wait(5)
        if not job.cancel.is_set():
            job.state = "done"


def _wait(cond, t=3.0):
    end = time.time() + t
    while time.time() < end and not cond():
        time.sleep(0.01)
    assert cond()


def test_resubmit_after_cancel_gets_a_fresh_job(monkeypatch):
    monkeypatch.setattr(sd, "local_file", lambda cid: None)
    f = _BlockedFetcher()
    cid = "1789688911-1789688977"
    f.submit(cid, "play")
    _wait(lambda: f.ran and f.ran[0].state == "connecting")
    first = f.get(cid)
    assert f.cancel(cid) and first.cancel.is_set() and first.active   # doomed, not yet noticed
    snap = f.submit(cid, "play")                                        # user re-opens the clip
    second = f.get(cid)
    assert second is not first and not second.cancel.is_set() and snap["state"] == "queued"
    f.release.set()
    _wait(lambda: second.state == "done")
    assert first.state == "cancelled"


def test_new_play_request_preempts_other_play_jobs_but_not_downloads(monkeypatch):
    monkeypatch.setattr(sd, "local_file", lambda cid: None)
    f = _BlockedFetcher()
    a, b, c = "1789000000-1789000060", "1789000100-1789000160", "1789000200-1789000260"
    f.submit(a, "download")
    _wait(lambda: len(f.ran) == 1)
    f.submit(b, "play")
    f.submit(c, "play")
    assert f.get(b).state == "cancelled" and not f.get(a).cancel.is_set()
    f.release.set()
    _wait(lambda: f.get(c).state == "done")
    assert f.get(a).state == "done"


def test_snapshot_never_advertises_a_previous_jobs_playlist(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "SD_HLS", tmp_path)
    job = sd.Job("1789688911-1789688977", "play")
    job.hls_dir.mkdir(parents=True)
    job.playlist.write_text("#EXTM3U\n")          # leftover of an earlier, finished job
    assert job.snapshot()["hls_ready"] is False   # still queued
    job.state = "streaming"
    assert job.snapshot()["hls_ready"] is True


def test_importing_sd_has_no_filesystem_side_effects(tmp_path):
    marker = sd.SD_HLS / "_live_marker_for_test"
    marker.mkdir(parents=True, exist_ok=True)
    try:
        import importlib
        importlib.reload(sd)
        assert marker.is_dir()
    finally:
        marker.rmdir()
