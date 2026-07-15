"""Origin filtering and port-bind retry behavior."""

import asyncio
import errno

import pytest

import final


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        (None, True),  # non-browser client
        ("null", False),  # sandboxed iframes send this; must be opt-in
        ("http://localhost", True),
        ("http://localhost:8000", True),
        ("https://127.0.0.1:9999", True),
        ("http://evil.example", False),
        ("https://localhost.evil.example", False),
        ("http://[malformed", False),
    ],
)
def test_origin_allowed(origin, allowed):
    assert final.origin_allowed(origin) is allowed


def test_origin_allowed_respects_config(monkeypatch):
    monkeypatch.setitem(final.CONFIG, "allowed_origin_hosts", ["scans.intranet.local"])

    assert final.origin_allowed("http://scans.intranet.local:8000") is True
    assert final.origin_allowed("http://localhost") is False


def test_null_origin_can_be_opted_in_for_file_pages(monkeypatch):
    monkeypatch.setitem(final.CONFIG, "allowed_origin_hosts", ["localhost", "null"])

    assert final.origin_allowed("null") is True


async def test_disallowed_origin_is_rejected(bridge, fake_ws_factory):
    ws = fake_ws_factory(request_headers={"Origin": "http://evil.example"})

    await bridge.handle_client(ws)

    assert ws.sent == [{"type": "error", "message": "Origin not allowed"}]
    assert ws.closed is True
    assert ws not in bridge.clients


async def test_allowed_origin_gets_greeting(bridge, fake_ws_factory):
    ws = fake_ws_factory(request_headers={"Origin": "http://localhost:8000"})

    await bridge.handle_client(ws)

    assert ws.sent[0]["type"] == "connected"


class FakeServer:
    def close(self):
        pass

    async def wait_closed(self):
        pass


async def test_start_server_retries_while_port_in_use(bridge, monkeypatch):
    monkeypatch.setitem(final.CONFIG, "port_retry_seconds", 0)
    attempts = []

    async def fake_serve(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError(errno.EADDRINUSE, "address in use")
        return FakeServer()

    monkeypatch.setattr(final.websockets, "serve", fake_serve)
    monkeypatch.setattr(bridge, "initialize_scanner", lambda: False)

    task = asyncio.create_task(bridge.start_server())
    for _ in range(100):
        await asyncio.sleep(0)
        if len(attempts) >= 3:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(attempts) == 3


async def test_start_server_gives_up_after_max_attempts(bridge, monkeypatch):
    monkeypatch.setitem(final.CONFIG, "port_retry_seconds", 0)
    monkeypatch.setitem(final.CONFIG, "port_retry_attempts", 2)
    attempts = []

    async def fake_serve(*args, **kwargs):
        attempts.append(1)
        raise OSError(errno.EADDRINUSE, "address in use")

    monkeypatch.setattr(final.websockets, "serve", fake_serve)
    monkeypatch.setattr(bridge, "initialize_scanner", lambda: False)

    with pytest.raises(OSError):
        await bridge.start_server()

    assert len(attempts) == 2


async def test_start_server_raises_immediately_on_other_oserror(bridge, monkeypatch):
    attempts = []

    async def fake_serve(*args, **kwargs):
        attempts.append(1)
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(final.websockets, "serve", fake_serve)
    monkeypatch.setattr(bridge, "initialize_scanner", lambda: False)

    with pytest.raises(OSError):
        await bridge.start_server()

    assert len(attempts) == 1
