"""Results store + analyser bookkeeping (no camera, no ML stack)."""
import pytest

from app import animals


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(animals, "DB_PATH", tmp_path / "a.sqlite")
    monkeypatch.setattr(animals, "FRAMES_DIR", tmp_path)


def test_store_roundtrip_labels_and_day_summary():
    animals._store("1789778447-1789778512", "20260919", "done", "m", 12.0,
                   [{"label": "mustelid", "score": 1.0, "hits": 31, "first": 1.5, "last": 20.0}])
    animals._store("1789752472-1789752483", "20260919", "done", "m", 3.0,
                   [{"label": "person", "score": 0.9, "hits": 3, "first": 8.5, "last": 9.0}])
    animals._store("1789688911-1789688977", "20260918", "done", "m", 3.0, [])
    day = animals.results_for_day("20260919")
    r = day["1789778447-1789778512"]
    assert r["animal"] and r["detections"][0]["label"] == "fouine / mustélidé" and r["detections"][0]["group"] == "animal"
    assert not day["1789752472-1789752483"]["animal"]
    assert animals.days_with_animals() == {"20260919": {"analyzed": 2, "animals": 1},
                                           "20260918": {"analyzed": 1, "animals": 0}}


def test_unknown_label_is_still_an_animal():
    assert animals.describe("genette")["group"] == "animal"


def test_enqueue_skips_done_and_duplicates(monkeypatch):
    monkeypatch.setattr(animals.Analyzer, "_loop", lambda self: None)     # no worker thread work
    a = animals.Analyzer()
    animals._store("1789688911-1789688977", "20260918", "done", "m", 3.0, [])
    clips = [("1789688911-1789688977", "20260918"), ("1789695193-1789695258", "20260918")]
    assert a.enqueue(clips) == 1 and a.enqueue(clips) == 0
    assert a.enqueue(clips, redo=True) == 1                               # the done one is re-queued
    assert a.clear() == 2


def test_frame_path_rejects_bad_ids():
    with pytest.raises(animals.sd.SdError):
        animals.frame_path("../../etc/passwd")
