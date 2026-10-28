import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.main import app, shutdown_server


@pytest.mark.parametrize("host,header,allowed", [
    ("127.0.0.1", "shutdown", True), ("::1", "shutdown", True),
    ("192.0.2.50", "shutdown", False), ("127.0.0.1", "", False),
])
def test_shutdown_requires_local_client_and_explicit_header(monkeypatch, host, header, allowed):
    calls = []
    monkeypatch.setattr(app.state, "request_shutdown", lambda: calls.append(True), raising=False)
    request = Request({"type": "http", "client": (host, 1234),
                       "headers": [(b"x-tapo-action", header.encode())]})
    if allowed:
        assert asyncio.run(shutdown_server(request)) == {"stopping": True}
        assert calls == [True]
    else:
        with pytest.raises(HTTPException) as error:
            asyncio.run(shutdown_server(request))
        assert error.value.status_code == 403
        assert not calls


def test_shutdown_without_launcher(monkeypatch):
    monkeypatch.delattr(app.state, "request_shutdown", raising=False)
    request = Request({"type": "http", "client": ("127.0.0.1", 1234),
                       "headers": [(b"x-tapo-action", b"shutdown")]})
    with pytest.raises(HTTPException) as error:
        asyncio.run(shutdown_server(request))
    assert error.value.status_code == 503


def test_forwarded_header_cannot_grant_shutdown(monkeypatch):
    calls = []
    monkeypatch.setattr(app.state, "request_shutdown", lambda: calls.append(True), raising=False)
    request = Request({"type": "http", "client": ("192.0.2.50", 1234),
                       "headers": [(b"x-tapo-action", b"shutdown"),
                                   (b"x-forwarded-for", b"127.0.0.1")]})
    with pytest.raises(HTTPException) as error:
        asyncio.run(shutdown_server(request))
    assert error.value.status_code == 403
    assert not calls


def test_launcher_requests_graceful_exit(monkeypatch):
    from app import server as launcher

    class FakeServer:
        should_exit = False

        def __init__(self, config):
            assert config.proxy_headers is False
            assert config.timeout_graceful_shutdown == 5

        async def serve(self):
            launcher.app.state.request_shutdown()
            assert not self.should_exit  # Give the HTTP response time to leave.
            await asyncio.sleep(0.4)
            assert self.should_exit

    monkeypatch.setattr(launcher.uvicorn, "Server", FakeServer)
    monkeypatch.setattr("sys.argv", ["app.server"])
    monkeypatch.setattr(app.state, "request_shutdown", None, raising=False)
    launcher.main()
