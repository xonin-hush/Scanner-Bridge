"""Scanner lifecycle: lazy (re)initialization and release."""


def test_scan_reinitializes_missing_scanner(bridge, monkeypatch):
    inits = []

    def fake_init():
        inits.append(1)
        bridge.scanner = object()
        bridge.scanner_type = "twain"
        return True

    monkeypatch.setattr(bridge, "initialize_scanner", fake_init)
    monkeypatch.setattr(
        bridge, "scan_with_twain", lambda dpi, color_mode: "data:image/png;base64,Zg=="
    )

    assert bridge.scan_document(200, "RGB") == "data:image/png;base64,Zg=="
    assert inits == [1]


def test_scan_fails_cleanly_when_no_scanner_found(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "initialize_scanner", lambda: False)

    assert bridge.scan_document(200, "RGB") is None


def test_scan_recovers_after_init_starts_succeeding(bridge, monkeypatch):
    available = {"scanner": False}

    def fake_init():
        if not available["scanner"]:
            return False
        bridge.scanner = object()
        bridge.scanner_type = "twain"
        return True

    monkeypatch.setattr(bridge, "initialize_scanner", fake_init)
    monkeypatch.setattr(
        bridge, "scan_with_twain", lambda dpi, color_mode: "data:image/png;base64,Zg=="
    )

    assert bridge.scan_document(200, "RGB") is None  # scanner not plugged in yet
    available["scanner"] = True
    assert bridge.scan_document(200, "RGB") == "data:image/png;base64,Zg=="


def test_reinit_flag_forces_release_and_fresh_init(bridge, monkeypatch):
    bridge.scanner = object()
    bridge.scanner_type = "twain"
    bridge.scanner_needs_reinit = True
    inits = []

    def fake_init():
        inits.append(1)
        bridge.scanner = object()
        bridge.scanner_type = "twain"
        return True

    monkeypatch.setattr(bridge, "initialize_scanner", fake_init)
    monkeypatch.setattr(
        bridge, "scan_with_twain", lambda dpi, color_mode: "data:image/png;base64,Zg=="
    )

    assert bridge.scan_document(200, "RGB") == "data:image/png;base64,Zg=="
    assert inits == [1]
    assert bridge.scanner_needs_reinit is False


def test_release_scanner_clears_state_even_when_destroy_raises(bridge):
    class ExplodingScanner:
        def destroy(self):
            raise RuntimeError("driver gone")

    bridge.scanner = ExplodingScanner()
    bridge.scanner_type = "twain"
    bridge.scanner_name = "Fake TWAIN"
    bridge.scanner_needs_reinit = True

    bridge.release_scanner()

    assert bridge.scanner is None
    assert bridge.scanner_name is None
    assert bridge.scanner_type == "none"
    assert bridge.scanner_needs_reinit is False
