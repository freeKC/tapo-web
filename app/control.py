"""Local control clients sharing the V4 SD-card query helpers.

Cameras speak one of two local logins: the newer "V4" one (SPAKE2+, tapo_v4.py)
or the older type 3 one (pytapo). A camera set to "auto" gets the login its
discovery answer suggests, and the other one only when the camera says it does
not speak the first.
"""
import os
import re
import time

from pytapo import Tapo

from .tapo_v4 import TapoV4, TapoV4Error

# The camera's answer when a login is of the wrong kind (never a password problem):
_WANTS_V3 = (-40413,)       # to pake_register: "use the nonce login" (C520WS fw 1.3.5)
_WANTS_V4 = (-40211,)       # to the type 3 login: "missing parameters" (C510W fw 1.3.4)
_CODE_RE = re.compile(r"(-4\d{4})\b")


def _error_code(exc) -> int | None:
    """The camera error code inside a pytapo exception message, if any."""
    text = str(exc)
    if "MISSING_NECESSARY_PARAMS" in text:
        return -40211
    m = _CODE_RE.search(text)
    return int(m.group(1)) if m else None


class TapoV3(TapoV4):
    """Lazy, serialized pytapo transport with the existing recording helpers."""

    def __init__(self, host, password, username="admin"):
        super().__init__(host, password, username=username)
        self._legacy = None
        self._retry_at = 0.0
        self._retry_code = None

    def multiple(self, reqs):
        with self._lock:
            remaining = self._retry_at - time.monotonic()
            if remaining > 0:
                raise TapoV4Error(self._retry_code,
                                  f"Control connection on cooldown; retry in {int(remaining) + 1}s")
            try:
                if self._legacy is None:
                    self._legacy = Tapo(
                        self.host, self.username, self.password,
                        cloudPassword=self.password, isKLAP=False,
                        retryStok=False, printWarnInformation=False,
                    )
                response = self._legacy.performRequest({
                    "method": "multipleRequest", "params": {"requests": reqs},
                })
            except Exception as exc:
                self._legacy = None
                suspension = re.search(r"Try again in (\d+) seconds", str(exc))
                seconds = int(suspension.group(1)) if suspension else 60
                self._retry_at = time.monotonic() + seconds
                self._retry_code = -40404 if suspension else _error_code(exc)
                # Library exceptions can include full camera responses/session data.
                message = (f"Camera login temporarily suspended; retry in {seconds}s"
                           if suspension else
                           "Type-3 control connection failed. Check the TP-Link account "
                           "password and Third-Party Compatibility in the Tapo app.")
                raise TapoV4Error(self._retry_code, message) from None
            if not isinstance(response, dict) or response.get("error_code") not in (0, None):
                code = response.get("error_code") if isinstance(response, dict) else None
                raise TapoV4Error(code, "Type-3 multipleRequest failed")
            result = response.get("result")
            responses = result.get("responses") if isinstance(result, dict) else None
            if not isinstance(responses, list):
                raise TapoV4Error(None, "Type-3 multipleRequest returned no response list")
            return responses


_CLASSES = {"4": TapoV4, "3": TapoV3}


class AutoControl(TapoV4):
    """A camera whose protocol is not set: log in with the protocol ``hint``
    suggests (V4 when unknown), and with the other one only if the camera
    answers that it does not speak the first. A wrong password or a lockout
    never triggers the second login. ``on_detect(protocol)`` learns the answer."""

    def __init__(self, host, password, username="admin", hint=None, on_detect=None):
        super().__init__(host, password, username=username)
        self._order = ("3", "4") if hint == "3" else ("4", "3")
        self._impl: TapoV4 | None = None
        self._on_detect = on_detect

    @property
    def protocol(self) -> str | None:
        return next((p for p, cls in _CLASSES.items() if type(self._impl) is cls), None)

    def multiple(self, reqs):
        with self._lock:
            if self._impl is not None:
                return self._impl.multiple(reqs)
            first, second = self._order
            impl = _CLASSES[first](self.host, self.password, username=self.username)
            try:
                out = impl.multiple(reqs)
            except TapoV4Error as e:
                if e.code not in (_WANTS_V3 if first == "4" else _WANTS_V4):
                    raise
                impl = _CLASSES[second](self.host, self.password, username=self.username)
                out = impl.multiple(reqs)
                first = second
            self._impl = impl
        if self._on_detect:
            self._on_detect(first)
        return out


def create_control(host, password, username="admin", protocol="auto", hint=None, on_detect=None):
    """``protocol``: "4", "3" or "auto". TAPO_CONTROL_PROTOCOL=3 or 4 in .env
    applies to every camera left on "auto"."""
    if protocol == "auto":
        forced = os.environ.get("TAPO_CONTROL_PROTOCOL", "").strip() or "auto"
        if forced not in ("auto", *_CLASSES):
            raise TapoV4Error(None, "TAPO_CONTROL_PROTOCOL must be auto, 3 or 4")
        protocol = forced
    if protocol == "auto":
        return AutoControl(host, password, username=username, hint=hint, on_detect=on_detect)
    if protocol not in _CLASSES:
        raise TapoV4Error(None, "protocol must be auto, 3 or 4")
    return _CLASSES[protocol](host, password, username=username)
