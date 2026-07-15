import json

import final


async def test_connect_sends_greeting(bridge, fake_ws_factory):
    ws = fake_ws_factory()

    await bridge.handle_client(ws)

    assert ws.sent[0]["type"] == "connected"
    assert ws.sent[0]["scanner_available"] is False
    assert ws.sent[0]["scanner_name"] is None


async def test_ping_pong(bridge, fake_ws_factory):
    ws = fake_ws_factory(incoming=[json.dumps({"type": "ping"})])

    await bridge.handle_client(ws)

    assert ws.sent[-1]["type"] == "pong"


async def test_unknown_type_returns_error(bridge, fake_ws_factory):
    ws = fake_ws_factory(incoming=[json.dumps({"type": "bogus"})])

    await bridge.handle_client(ws)

    assert ws.sent[-1]["type"] == "error"
    assert "bogus" in ws.sent[-1]["message"]


async def test_missing_type_returns_error(bridge, fake_ws_factory):
    ws = fake_ws_factory(incoming=[json.dumps({"dpi": 200})])

    await bridge.handle_client(ws)

    assert ws.sent[-1]["type"] == "error"


async def test_invalid_json_returns_error(bridge, fake_ws_factory):
    ws = fake_ws_factory(incoming=["{not json"])

    await bridge.handle_client(ws)

    assert ws.sent[-1]["type"] == "error"
    assert ws.sent[-1]["message"] == "Invalid message format"


async def test_scan_happy_path(bridge, fake_ws_factory, monkeypatch):
    monkeypatch.setattr(
        bridge, "scan_document", lambda dpi, color_mode: "data:image/png;base64,Zg=="
    )
    ws = fake_ws_factory(incoming=[json.dumps({"type": "scan", "dpi": 200})])

    await bridge.handle_client(ws)

    types = [m["type"] for m in ws.sent]
    assert types == ["connected", "scanning", "scan_complete"]
    assert ws.sent[-1]["image"] == "data:image/png;base64,Zg=="
    assert bridge.is_scanning is False


async def test_scan_failure_returns_error(bridge, fake_ws_factory, monkeypatch):
    monkeypatch.setattr(bridge, "scan_document", lambda dpi, color_mode: None)
    ws = fake_ws_factory(incoming=[json.dumps({"type": "scan"})])

    await bridge.handle_client(ws)

    assert ws.sent[-1]["type"] == "error"
    assert bridge.is_scanning is False


async def test_scan_rejected_while_scan_in_progress(bridge, fake_ws_factory):
    bridge.is_scanning = True
    ws = fake_ws_factory(incoming=[json.dumps({"type": "scan"})])

    await bridge.handle_client(ws)

    assert ws.sent[-1]["type"] == "error"
    assert "in progress" in ws.sent[-1]["message"]


async def test_client_limit_rejects_new_connections(bridge, fake_ws_factory, monkeypatch):
    monkeypatch.setitem(final.CONFIG, "max_clients", 1)
    bridge.clients.add(object())
    ws = fake_ws_factory()

    await bridge.handle_client(ws)

    assert ws.sent == [{"type": "error", "message": "Maximum clients reached"}]
    assert ws.closed is True


async def test_client_removed_after_disconnect(bridge, fake_ws_factory):
    ws = fake_ws_factory(incoming=[json.dumps({"type": "ping"})])

    await bridge.handle_client(ws)

    assert ws not in bridge.clients
