"""
tapo_v4.py -- Local "V4" (TSLP + SPAKE2+) client for Tapo cameras such as the
C510W (firmware 1.3.4), reverse-engineered from the official Tapo app 3.21.111
(com.tplink.tls / com.tplink.libtapocameranetwork) and verified live.

The camera advertises encrypt_type ["4"]; its control API on 443 speaks a
SPAKE2+ password-authenticated handshake (RFC 9383 M/N points, P-256), then an
AES-128-CCM secure channel for business requests. This is what pytapo/python-kasa
(which only speak the older "V3") cannot do -> they get error_code -40211.

Handshake (all POST "/" as {"method":"login","params":{...}}):
  pake_register -> device returns dev_salt, dev_random, dev_share(Y), iterations
  (derive w0,w1 = PBKDF2(md5hex(pwd), dev_salt, iters); pick x; X = x*G + w0*M)
  pake_share    -> send user_share=X, user_confirm=cA; device returns
                   dev_confirm, stok, start_seq
Business (POST "/stok={stok}/ds", application/octet-stream)  -- hm1/a0.java:
  body  = int32_be(seq) || AES-128-CCM(realKey, nonce=realNonce[:8]||seq_be32,
          plaintext=inner JSON, tag=16, no AAD)          (ciphertext || tag)
  seq   = start_seq for the first request, then +1 per request (getAndIncrement)
  inner = {"method":"multipleRequest","params":{"requests":[{method,params},..]}}
          -- a bare single method is refused with plaintext -40209.
  The reply has the same layout (int32_be(seq) || ct || tag). A plaintext JSON
  reply means the request was refused before/at decryption: -40401 = bad seq or
  undecryptable payload (the session is dropped), -40209 = bad inner shape.
  NOTE: there is NO 24-byte "TSLP" frame on the HTTP path (dm1/a.java is the
  TCP/netty framing); sending one makes the camera read 0x01020200 as the seq.

Deps: requests, pycryptodome.  (Pure-python P-256 math below -- no extra deps.)

Source citations use  file:line  from the decompiled app.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import threading
import time

import requests
from Crypto.Cipher import AES

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# --------------------------------------------------------------------------- #
#  secp256r1 / P-256 minimal point arithmetic (affine)
# --------------------------------------------------------------------------- #
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5


def _inv(x: int) -> int:
    return pow(x % P, P - 2, P)


def pt_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        m = (3 * x1 * x1 + A) * _inv(2 * y1) % P
    else:
        m = (y2 - y1) * _inv(x2 - x1) % P
    x3 = (m * m - x1 - x2) % P
    y3 = (m * (x1 - x3) - y1) % P
    return (x3, y3)


def pt_mul(k: int, p):
    k %= N
    r = None
    addend = p
    while k:
        if k & 1:
            r = pt_add(r, addend)
        addend = pt_add(addend, addend)
        k >>= 1
    return r


def pt_neg(p):
    if p is None:
        return None
    return (p[0], (-p[1]) % P)


def decode_point(b: bytes):
    """Decode an SEC1 point (0x04 uncompressed or 0x02/0x03 compressed)."""
    if b[0] == 0x04:
        x = int.from_bytes(b[1:33], "big")
        y = int.from_bytes(b[33:65], "big")
        return (x, y)
    if b[0] in (0x02, 0x03):
        x = int.from_bytes(b[1:33], "big")
        alpha = (pow(x, 3, P) + A * x + B) % P
        y = pow(alpha, (P + 1) // 4, P)
        if (y & 1) != (b[0] & 1):
            y = P - y
        return (x, y)
    raise ValueError("bad point encoding")


def encode_uncompressed(p) -> bytes:
    x, y = p
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


# Standard RFC 9383 SPAKE2+ seed points for P-256 (matches kl1/d.java:11-12).
M_POINT = decode_point(bytes.fromhex(
    "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f"))
N_POINT = decode_point(bytes.fromhex(
    "03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49"))
G_POINT = (GX, GY)


# --------------------------------------------------------------------------- #
#  KDFs
# --------------------------------------------------------------------------- #
def hkdf_sha256(ikm: bytes, salt: bytes | None, info: bytes, length: int) -> bytes:
    # BouncyCastle HKDFBytesGenerator: salt None -> zeros(hashLen); extract+expand.
    if salt is None:
        salt = b"\x00" * 32
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def _len_prefixed(*chunks: bytes) -> bytes:
    # jl1/a.java g()/o(): each element prefixed by its 8-byte little-endian length.
    out = b""
    for c in chunks:
        out += struct.pack("<Q", len(c)) + c
    return out


# --------------------------------------------------------------------------- #
#  Errors
# --------------------------------------------------------------------------- #
class TapoV4Error(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"error_code={code} {message}")
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
#  Client
# --------------------------------------------------------------------------- #
CONTEXT_TAG = b"PAKE V1"  # Spake2pBean.SPAKE2P_CONTEXT_TAG


class TapoV4:
    def __init__(self, host, password, username="admin", port=443, timeout=10):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.base = f"https://{host}:{port}"
        self.session = requests.Session()
        self.session.verify = False
        self.stok = None
        self.seq = None      # next sequence number to use (starts at start_seq)
        self.key = None      # AES-128 key (16)
        self.nonce0 = None   # base nonce (12), last 4 bytes replaced by seq
        self.shared_key = None
        self.expires_at = 0.0
        self._lock = threading.RLock()   # one request at a time: seq must stay ordered

    # -- HTTP -----------------------------------------------------------------
    def _headers(self):
        return {"requestByApp": "true", "Referer": f"{self.base}:{self.port}",
                "User-Agent": "Tapo CameraClient Android"}

    def _post_login(self, params: dict) -> dict:
        body = {"method": "login", "params": params}
        h = self._headers(); h["Content-Type"] = "application/json"
        r = self.session.post(self.base + "/", data=json.dumps(body).encode(),
                              headers=h, timeout=self.timeout)
        return r.json()

    # -- SPAKE2+ handshake ----------------------------------------------------
    def login(self):
        # ---- pake_register (j2.java: username="admin", cipher_suites=[1], passcode_type="userpw")
        user_random = base64.b64encode(os.urandom(32)).decode()
        reg = self._post_login({
            "sub_method": "pake_register",
            "username": self.username,
            "user_random": user_random,
            "cipher_suites": [1],
            "passcode_type": "userpw",
        })
        if "result" not in reg:
            raise TapoV4Error(reg.get("error_code"), "pake_register failed")
        res = reg["result"]
        dev_salt = base64.b64decode(res["dev_salt"])
        dev_random = res["dev_random"]
        iterations = int(res["iterations"])
        Y = decode_point(base64.b64decode(res["dev_share"]))

        # ---- w0, w1  (jl1/d.java: PBKDF2-SHA256(md5hex(pwd), dev_salt, iters) -> 80 bytes)
        credential = hashlib.md5(self.password.encode()).hexdigest()  # rb1/a.h
        dk = hashlib.pbkdf2_hmac("sha256", credential.encode(), dev_salt, iterations, 80)
        w0 = int.from_bytes(dk[0:40], "big") % N
        w1 = int.from_bytes(dk[40:80], "big") % N

        # ---- our share  X = x*G + w0*M   (jl1/c.q)
        x = int.from_bytes(os.urandom(32), "big") % (N - 1) + 1
        X = pt_add(pt_mul(x, G_POINT), pt_mul(w0, M_POINT))
        # ---- shared points  Z = x*(Y - w0*N),  V = w1*(Y - w0*N)   (jl1/c.r)
        H = pt_add(Y, pt_neg(pt_mul(w0, N_POINT)))
        Z = pt_mul(x, H)
        V = pt_mul(w1, H)

        Xb = encode_uncompressed(X)
        Yb = encode_uncompressed(Y)
        Mb = encode_uncompressed(M_POINT)
        Nb = encode_uncompressed(N_POINT)
        Zb = encode_uncompressed(Z)
        Vb = encode_uncompressed(V)
        w0b = w0.to_bytes(32, "big")

        # context stored by em1/b.java is SHA256("PAKE V1"||user_random||dev_random),
        # NOT the raw bytes (em1/b.java:36-40 hashes it before getContext()).
        context = hashlib.sha256(
            CONTEXT_TAG + base64.b64decode(user_random) + base64.b64decode(dev_random)
        ).digest()
        # ---- transcript TT  (jl1/a.d): len-prefixed context,idP,idV,M,N,X,Y,Z,V,w0
        TT = _len_prefixed(context, b"", b"", Mb, Nb, Xb, Yb, Zb, Vb, w0b)

        # ---- key schedule  (jl1/a.b + kl1/k.c)
        ke = hashlib.sha256(TT).digest()
        conf = hkdf_sha256(ke, None, b"ConfirmationKeys", 64)
        KcA, KcB = conf[:32], conf[32:]
        self.shared_key = hkdf_sha256(ke, None, b"SharedKey", 32)

        # ---- confirmations  cA = HMAC(KcA, Y), verify cB = HMAC(KcB, X)
        cA = hmac.new(KcA, Yb, hashlib.sha256).digest()

        share = self._post_login({
            "sub_method": "pake_share",
            "user_share": base64.b64encode(Xb).decode(),
            "user_confirm": base64.b64encode(cA).decode(),
        })
        if "result" not in share:
            raise TapoV4Error(share.get("error_code"), "pake_share failed")
        sres = share["result"]
        dev_confirm = base64.b64decode(sres["dev_confirm"])
        expected_cB = hmac.new(KcB, Xb, hashlib.sha256).digest()
        if not hmac.compare_digest(dev_confirm, expected_cB):
            raise TapoV4Error(0, "dev_confirm mismatch (wrong password?)")

        self.stok = sres["stok"]
        self.seq = int(sres["start_seq"])
        self.expires_at = time.time() + int(sres.get("expired") or 3600)
        # ---- session cipher keys  (SecSessionCipher: HKDF over shared_key)
        bk = hkdf_sha256(self.shared_key, b"tp-kdf-salt-aes128-key", b"tp-kdf-info-aes128-key", 32)
        bn = hkdf_sha256(self.shared_key, b"tp-kdf-salt-aes128-iv", b"tp-kdf-info-aes128-iv", 32)
        self.key = bk[:16]
        self.nonce0 = bn[:12]
        return True

    # -- business layer (AES-128-CCM over /stok/ds) ---------------------------
    def _nonce(self, seq: int) -> bytes:
        return self.nonce0[:8] + struct.pack(">I", seq & 0xFFFFFFFF)

    def logged_in(self) -> bool:
        # Renew a little before the camera-side expiry (observed: 3600 s).
        return self.stok is not None and time.time() < self.expires_at - 60

    def _drop_session(self):
        self.stok = None
        self.key = self.nonce0 = self.shared_key = None
        self.expires_at = 0.0

    def _ds(self, inner: bytes) -> dict:
        """One encrypted round-trip on /ds (hm1/a0.java b()/o())."""
        seq = self.seq                     # getAndIncrement: first request = start_seq
        self.seq += 1
        c = AES.new(self.key, AES.MODE_CCM, nonce=self._nonce(seq), mac_len=16)
        body = struct.pack(">I", seq & 0xFFFFFFFF) + c.encrypt(inner) + c.digest()
        h = self._headers()
        h["Content-Type"] = "application/octet-stream"
        h["Accept"] = "application/octet-stream"
        r = self.session.post(f"{self.base}/stok={self.stok}/ds", data=body,
                              headers=h, timeout=self.timeout)
        raw = r.content
        if raw[:1] == b"{":                # refused before/at decryption
            try:
                code = json.loads(raw).get("error_code")
            except ValueError:
                code = None
            raise TapoV4Error(code, "/ds refused (plaintext reply)")
        if len(raw) < 4 + 16:
            raise TapoV4Error(None, f"/ds short reply ({len(raw)} bytes, HTTP {r.status_code})")
        resp_seq = struct.unpack(">I", raw[:4])[0]
        d = AES.new(self.key, AES.MODE_CCM, nonce=self._nonce(resp_seq), mac_len=16)
        try:
            pt = d.decrypt_and_verify(raw[4:-16], raw[-16:])
        except ValueError as e:
            raise TapoV4Error(None, f"/ds reply failed authentication: {e}") from e
        return json.loads(pt)

    def multiple(self, reqs: list[dict]) -> list[dict]:
        """Send several {"method","params"} in one multipleRequest.

        Returns the list of per-request responses ({"method","result","error_code"}).
        Logs in on demand and transparently re-logs in once if the camera dropped
        the session (-40401) or the connection broke.
        """
        inner = json.dumps({"method": "multipleRequest", "params": {"requests": reqs}},
                           separators=(",", ":")).encode()
        with self._lock:
            for attempt in (0, 1):
                try:
                    if not self.logged_in():
                        self.login()              # a Wi-Fi blip here deserves the retry too
                    resp = self._ds(inner)
                    break
                except TapoV4Error as e:
                    self._drop_session()   # a refused /ds kills the session camera-side
                    if attempt or e.code not in (-40401, -40421):
                        raise
                except requests.RequestException:
                    self._drop_session()
                    if attempt:
                        raise
        if resp.get("error_code") not in (0, None):
            raise TapoV4Error(resp.get("error_code"), "multipleRequest failed")
        return resp.get("result", {}).get("responses", [])

    def request(self, method, params=None):
        """Single call; returns its `result` dict or raises TapoV4Error."""
        out = self.multiple([{"method": method, "params": params or {}}])
        if not out:
            raise TapoV4Error(None, f"method {method}: empty response")
        r = out[0]
        if r.get("error_code") not in (0, None):
            raise TapoV4Error(r.get("error_code"), f"method {method} failed")
        return r.get("result", {})

    # -- SD helpers -----------------------------------------------------------
    @staticmethod
    def _unwrap(items):
        """[{"name_1": {...}}, {"name_2": {...}}] -> [{...}, {...}]"""
        return [v for it in (items or []) for v in it.values()]

    def get_device_info(self):
        return self.request("getDeviceInfo", {"device_info": {"name": ["basic_info"]}})["device_info"]["basic_info"]

    def get_sd_status(self):
        r = self.request("getSdCardStatus", {"harddisk_manage": {"table": ["hd_info"]}})
        disks = self._unwrap(r["harddisk_manage"]["hd_info"])
        return disks[0] if disks else None

    def get_user_id(self):
        return self.request("getUserID", {"system": {"get_user_id": "null"}})["user_id"]

    def get_clock(self):
        r = self.multiple([
            {"method": "getClockStatus", "params": {"system": {"name": "clock_status"}}},
            {"method": "getTimezone", "params": {"system": {"name": ["basic"]}}},
        ])
        clock = r[0].get("result", {}).get("system", {}).get("clock_status", {})
        tz = r[1].get("result", {}).get("system", {}).get("basic", {}) if len(r) > 1 else {}
        return {**clock, **tz}

    def search_days(self, start_date: str, end_date: str):
        """Days (YYYYMMDD) that have at least one recording, within [start, end]."""
        r = self.request("searchDateWithVideo", {"playback": {"search_year_utility": {
            "channel": [0], "start_date": start_date, "end_date": end_date}}})
        return [d["date"] for d in self._unwrap(r["playback"]["search_results"])]

    def search_videos_utc(self, start_ts: int, end_ts: int, player_id: str, page: int = 100):
        """Clips within [start_ts, end_ts] (UTC epoch) - the listing the official app
        uses on cameras with component playback >= 6 (it wants "player_id"; the older
        "id": <user id> form is refused there with -71103).
        -> [{"startTime","endTime","video_type": "2"}, ...]"""
        clips, start = [], 0
        while True:
            r = self.request("searchVideoWithUTC", {"playback": {"search_video_with_utc": {
                "channel": 0, "start_time": int(start_ts), "end_time": int(end_ts),
                "start_index": start, "end_index": start + page - 1, "player_id": player_id}}})
            pb = r["playback"]
            batch = self._unwrap(pb.get("search_video_results"))
            clips += batch
            if not batch or not pb.get("to_be_continued"):
                return clips
            start += page

    def get_recordings(self, date: str, user_id: int | None = None, page: int = 100):
        """Legacy per-day listing (camera-local date YYYYMMDD):
        [{"startTime","endTime","vedio_type": 2}, ...] (epoch seconds)."""
        if user_id is None:
            user_id = self.get_user_id()
        clips, start = [], 0
        while True:
            r = self.request("searchVideoOfDay", {"playback": {"search_video_utility": {
                "channel": 0, "date": date, "start_index": start,
                "end_index": start + page - 1, "id": user_id}}})
            batch = self._unwrap(r["playback"].get("search_video_results"))
            clips += batch
            if len(batch) < page:
                return clips
            start += page
