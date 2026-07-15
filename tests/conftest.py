"""Shared fixtures. Fakes the Windows-only modules so `final` imports anywhere."""

import json
import sys
from unittest.mock import MagicMock

import pytest

# Must happen before `import final` so tests can exercise TWAIN/WIA code paths
# on non-Windows hosts (final itself imports these lazily/guarded).
for _name in ("twain", "win32com", "win32com.client", "pythoncom"):
    sys.modules.setdefault(_name, MagicMock())

import final  # noqa: E402


@pytest.fixture
def bridge():
    return final.ScannerBridge()


class FakeWebSocket:
    """Stands in for a websockets connection in handle_client tests.

    Sent frames are decoded into `self.sent`; `incoming` is a scripted list of
    raw inbound messages iterated by the `async for` loop in handle_client.
    """

    def __init__(self, incoming=None, request_headers=None):
        self.sent = []
        self.closed = False
        self._incoming = list(incoming or [])
        self.request_headers = request_headers or {}

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def close(self, *args, **kwargs):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)


@pytest.fixture
def fake_ws_factory():
    return FakeWebSocket
