"""Results store + analyser bookkeeping (no camera, no ML stack)."""
from zoneinfo import ZoneInfo

import pytest

from app import animals, sd

from .helpers import make_camera


@pytest.fixture
def cam(tmp_path):
    c = make_camera(tmp_path)
    sd.card_for(c)._tz = ZoneInfo("Europe/Brussels")      # never ask the camera
    return c


@pytest.fixture
def store(cam):
    return animals.store_for(cam)


@pytest.fixture
def analyzer(monkeypatch):
    monkeypatch.setattr(animals.Analyzer, "_loop", lambda self: None)     # no worker thread work
    return animals.Analyzer()


def test_store_roundtrip_labels_and_day_summary(store):
    store.store("1789778447-1789778512", "20260919", "done", "m", 12.0,
                [{"label": "mustelid", "score": 1.0, "hits": 31, "first": 1.5, "last": 20.0}])
    store.store("1789752472-1789752483", "20260919", "done", "m", 3.0,
                [{"label": "person", "score": 0.9, "hits": 3, "first": 8.5, "last": 9.0}])
    store.store("1789688911-1789688977", "20260918", "done", "m", 3.0, [])
    day = store.results_for_day("20260919")
    r = day["1789778447-1789778512"]
    assert r["animal"] and r["detections"][0]["label"] == "fouine / mustélidé" and r["detections"][0]["group"] == "animal"
    assert not day["1789752472-1789752483"]["animal"]
    assert store.days_with_animals() == {"20260919": {"analyzed": 2, "animals": 1},
                                         "20260918": {"analyzed": 1, "animals": 0}}


def test_unknown_label_is_still_an_animal():
    assert animals.describe("genette")["group"] == "animal"


def test_enqueue_skips_done_and_duplicates(cam, store, analyzer):
    store.store("1789688911-1789688977", "20260918", "done", "m", 3.0, [])
    clips = [("1789688911-1789688977", "20260918"), ("1789695193-1789695258", "20260918")]
    assert analyzer.enqueue(cam, clips) == 1 and analyzer.enqueue(cam, clips) == 0
    assert analyzer.enqueue(cam, clips, redo=True) == 1                   # the done one is re-queued
    assert analyzer.clear() == 2


def test_queue_is_per_camera(tmp_path, analyzer):
    a, b = make_camera(tmp_path / "a"), make_camera(tmp_path / "b")
    b.cfg.id = "camb"
    clip = [("1789688911-1789688977", "20260918")]
    assert analyzer.enqueue(a, clip) == 1 and analyzer.enqueue(b, clip) == 1   # same clip id, two cameras
    assert analyzer.status(a)["queued_ids"] == ["1789688911-1789688977"]
    assert analyzer.clear(b) == 1 and analyzer.status(a)["queued"] == 1


def test_frame_path_rejects_bad_ids(store):
    with pytest.raises(sd.SdError):
        store.frame_path("../../etc/passwd")


def test_rejected_label_disappears_everywhere_and_survives_reanalysis(store):
    cid = "1789447084-1789447150"
    dets = [{"label": "dog", "score": 1.0, "hits": 40, "first": 1, "last": 30},
            {"label": "fox", "score": 0.92, "hits": 6, "first": 46, "last": 50}]
    store.store(cid, "20260915", "done", "m", 10.0, dets)
    store.reject(cid, "fox")
    assert [d["key"] for d in store.result(cid)["detections"]] == ["dog"]
    assert [s["key"] for s in store.species()] == ["dog"]
    assert store.clips_with("fox") == [] and len(store.clips_with("dog")) == 1
    store.store(cid, "20260915", "done", "m2", 10.0, dets)                # analysed again later
    assert [d["key"] for d in store.result(cid)["detections"]] == ["dog"]
    store.reject(cid, "fox", undo=True)
    assert len(store.result(cid)["detections"]) == 2


def test_corrupt_clips_are_never_requeued(cam, store, analyzer, monkeypatch):
    cid = "1790356971-1790357013"
    store.store(cid, "20260925", "error", None, None, [], sd.CORRUPT_MESSAGE)
    assert analyzer.enqueue(cam, [(cid, "20260925")]) == 0
    store.store(cid, "20260925", "error", None, None, [], "Caméra hors ligne.")
    monkeypatch.setattr(animals.time, "time", lambda: 10 ** 10)           # long after the failure
    assert analyzer.enqueue(cam, [(cid, "20260925")]) == 1
