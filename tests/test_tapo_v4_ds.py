"""Offline tests of the /ds business layer against a fake camera.

The fake implements exactly what the real C510W was observed to do:
  body = int32_be(seq) || AES-128-CCM(ct || tag16), nonce = base[:8] || seq_be32,
  inner must be a multipleRequest (a bare method -> plaintext -40209),
  bad seq / undecryptable -> plaintext -40401 and the session is dead.
"""
import json
import os
import struct
import time

import pytest
from Crypto.Cipher import AES

from app.tapo_v4 import TapoV4, TapoV4Error, apply_extra_crypt, sha256_crypt


class FakeResponse:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status


class FakeCamera:
    def __init__(self):
        self.key, self.nonce0 = os.urandom(16), os.urandom(12)
        self.expect_seq = 1000
        self.alive = True
        self.inner_seen = []
        self.urls = []

    def _nonce(self, seq):
        return self.nonce0[:8] + struct.pack(">I", seq)

    def post(self, url, data=None, headers=None, timeout=None):
        self.urls.append(url)
        assert headers["Content-Type"] == "application/octet-stream"
        seq = struct.unpack(">I", data[:4])[0]
        if not self.alive or seq != self.expect_seq:
            self.alive = False
            return FakeResponse(b'{"error_code":-40401}')
        c = AES.new(self.key, AES.MODE_CCM, nonce=self._nonce(seq), mac_len=16)
        try:
            inner = json.loads(c.decrypt_and_verify(data[4:-16], data[-16:]))
        except ValueError:
            self.alive = False
            return FakeResponse(b'{"error_code":-40401}')
        self.expect_seq += 1
        self.inner_seen.append(inner)
        if inner.get("method") != "multipleRequest":
            return FakeResponse(b'{"error_code":-40209}')
        responses = [{"method": r["method"], "result": {"echo": r["params"]}, "error_code": 0}
                     if r["method"] != "boom" else {"method": "boom", "error_code": -40106}
                     for r in inner["params"]["requests"]]
        out = json.dumps({"result": {"responses": responses}, "error_code": 0}).encode()
        e = AES.new(self.key, AES.MODE_CCM, nonce=self._nonce(seq), mac_len=16)
        return FakeResponse(struct.pack(">I", seq) + e.encrypt(out) + e.digest())


def make_client(cam, monkeypatch, logins):
    c = TapoV4("192.0.2.1", "pw")

    def fake_login():
        logins.append(1)
        cam.alive, cam.expect_seq = True, 5000 + 100 * len(logins)
        c.stok, c.seq = "S" * 32, cam.expect_seq
        c.key, c.nonce0 = cam.key, cam.nonce0
        c.expires_at = time.time() + 3600
        return True

    monkeypatch.setattr(c, "login", fake_login)
    monkeypatch.setattr(c.session, "post", cam.post)
    return c


def test_first_request_uses_start_seq_and_wraps_in_multiple_request(monkeypatch):
    cam, logins = FakeCamera(), []
    c = make_client(cam, monkeypatch, logins)
    res = c.request("getDeviceInfo", {"device_info": {"name": ["basic_info"]}})
    assert res == {"echo": {"device_info": {"name": ["basic_info"]}}}
    assert logins == [1]                              # logged in on demand, once
    assert cam.inner_seen[0]["method"] == "multipleRequest"
    assert cam.urls[0].endswith("/stok=" + "S" * 32 + "/ds")
    assert c.seq == 5100 + 1                          # getAndIncrement


def test_sequence_increments_per_request(monkeypatch):
    cam, logins = FakeCamera(), []
    c = make_client(cam, monkeypatch, logins)
    for _ in range(3):
        c.request("getUserID", {"system": {"get_user_id": "null"}})
    assert logins == [1] and cam.expect_seq == 5100 + 3


def test_dead_session_triggers_exactly_one_relogin(monkeypatch):
    cam, logins = FakeCamera(), []
    c = make_client(cam, monkeypatch, logins)
    c.request("a")
    cam.alive = False                                 # camera dropped the session
    assert c.request("b") == {"echo": {}}
    assert logins == [1, 1]


def test_persistent_refusal_raises_and_drops_session(monkeypatch):
    cam, logins = FakeCamera(), []
    c = make_client(cam, monkeypatch, logins)
    monkeypatch.setattr(c.session, "post", lambda *a, **k: FakeResponse(b'{"error_code":-40401}'))
    with pytest.raises(TapoV4Error) as ei:
        c.request("a")
    assert ei.value.code == -40401 and len(logins) == 2 and c.stok is None


def test_per_method_error_is_raised(monkeypatch):
    cam, logins = FakeCamera(), []
    c = make_client(cam, monkeypatch, logins)
    with pytest.raises(TapoV4Error) as ei:
        c.request("boom")
    assert ei.value.code == -40106
    assert c.stok is not None                         # a method error does not kill the session


def test_tampered_reply_is_rejected(monkeypatch):
    cam, logins = FakeCamera(), []
    c = make_client(cam, monkeypatch, logins)
    real = cam.post

    def tamper(*a, **k):
        r = real(*a, **k)
        return FakeResponse(r.content[:-1] + bytes([r.content[-1] ^ 1]))

    monkeypatch.setattr(c.session, "post", tamper)
    with pytest.raises(TapoV4Error):
        c.request("a")


def test_listing_helpers_unwrap_and_paginate(monkeypatch):
    c = TapoV4("192.0.2.1", "pw")
    calls = []

    def fake_request(method, params=None):
        q = params["playback"]["search_video_with_utc"]
        calls.append((q["start_index"], q["end_index"]))
        n = 3 if q["start_index"] == 0 else 1
        items = [{f"search_video_results_{i}": {"startTime": q["start_index"] + i, "endTime": 9, "video_type": "2"}}
                 for i in range(n)]
        return {"playback": {"search_video_results": items, "to_be_continued": 1 if q["start_index"] == 0 else 0}}

    monkeypatch.setattr(c, "request", fake_request)
    clips = c.search_videos_utc(0, 10, "PLAYER", page=3)
    assert calls == [(0, 2), (3, 5)] and len(clips) == 4 and clips[0]["startTime"] == 0


def test_sha256_crypt_matches_the_glibc_reference_vectors():
    assert sha256_crypt("Hello world!", "$5$saltstring") == "$5$saltstring$5B8vYYiY.CVt1RlTTf8KbXBH3hsxY/GNooZaBBGWEc5"
    assert sha256_crypt("Hello world!", "$5$rounds=10000$saltstringsaltstring") == \
        "$5$rounds=10000$saltstringsaltst$3xv.VbSHBb41AL9AvLeujZkZRBAwqFMz2.opqey6IcA"
    assert sha256_crypt("a very much longer text to encrypt.  This one even stretches over morethan one line.",
                        "$5$rounds=1400$anotherlongsaltstring") == \
        "$5$rounds=1400$anotherlongsalts$Rx.j8H.h8HjEDGomFU8bDkXm3XIUnzyxf12oP84Bnq1"
    # the camera sends the prefix with a trailing "$"
    assert sha256_crypt("x", "$5$x1hYMevsEYq2APg+$").startswith("$5$x1hYMevsEYq2APg+$")


def test_extra_crypt_only_applies_when_the_camera_asks():
    assert apply_extra_crypt("abc", None) == "abc"
    shadow = {"type": "password_shadow", "params": {"passwd_id": 5, "passwd_prefix": "$5$saltstring$"}}
    assert apply_extra_crypt("Hello world!", shadow) == sha256_crypt("Hello world!", "$5$saltstring")
    with pytest.raises(TapoV4Error):
        apply_extra_crypt("abc", {"type": "password_shadow", "params": {"passwd_id": 9}})


def test_search_days_is_chunked(monkeypatch):
    c = TapoV4("192.0.2.1", "pw")
    ranges = []

    def fake_request(method, params=None):
        q = params["playback"]["search_year_utility"]
        ranges.append((q["start_date"], q["end_date"]))
        return {"playback": {"search_results": [{"search_results_1": {"date": q["start_date"]}}]}}
    monkeypatch.setattr(c, "request", fake_request)
    days = c.search_days("20260101", "20260315")
    assert ranges == [("20260101", "20260131"), ("20260201", "20260303"), ("20260304", "20260315")]
    assert days == ["20260101", "20260201", "20260304"]
