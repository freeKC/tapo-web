"""Local control clients sharing the V4 SD-card query helpers."""
import os
import re
import time

from pytapo import Tapo

from .tapo_v4 import TapoV4, TapoV4Error


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
                self._retry_code = -40404 if suspension else None
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


def create_control(host, password, username="admin"):
    """Explicit selection avoids speculative logins and camera lockouts."""
    protocol = os.environ.get("TAPO_CONTROL_PROTOCOL", "4").strip()
    if protocol not in ("3", "4"):
        raise TapoV4Error(None, "TAPO_CONTROL_PROTOCOL must be 3 or 4")
    cls = TapoV3 if protocol == "3" else TapoV4
    return cls(host, password, username=username)
