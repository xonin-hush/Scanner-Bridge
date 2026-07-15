"""Crash-restart supervision loop."""

import pytest

import final


class FakeBridge:
    released = 0

    def release_scanner(self):
        FakeBridge.released += 1


def test_supervisor_restarts_after_crashes(monkeypatch):
    runs = []
    sleeps = []

    def run_fn(bridge):
        runs.append(bridge)
        if len(runs) < 3:
            raise RuntimeError("boom")

    final.run_supervised(
        FakeBridge, run_fn=run_fn, sleep_fn=sleeps.append, monotonic_fn=lambda: 0.0
    )

    assert len(runs) == 3
    assert sleeps == [1, 2]
    # every attempt gets a fresh bridge
    assert len(set(id(b) for b in runs)) == 3


def test_supervisor_backoff_caps_and_resets_after_healthy_uptime():
    runs = []
    sleeps = []
    clock = {"t": 0.0}

    def run_fn(bridge):
        runs.append(1)
        n = len(runs)
        if n <= 3:
            raise RuntimeError("crash loop")  # instant crashes
        if n == 4:
            clock["t"] += 700  # healthy for >10 min, then crash
            raise RuntimeError("late crash")
        return  # clean exit ends the loop

    final.run_supervised(
        FakeBridge, run_fn=run_fn, sleep_fn=sleeps.append,
        monotonic_fn=lambda: clock["t"],
    )

    assert sleeps == [1, 2, 4, 1]


def test_supervisor_stops_on_keyboard_interrupt():
    runs = []

    def run_fn(bridge):
        runs.append(1)
        raise KeyboardInterrupt

    final.run_supervised(
        FakeBridge, run_fn=run_fn,
        sleep_fn=lambda s: pytest.fail("must not restart after Ctrl+C"),
    )

    assert runs == [1]


def test_supervisor_releases_scanner_on_every_exit_path():
    FakeBridge.released = 0

    def run_fn(bridge):
        if FakeBridge.released == 0:
            raise RuntimeError("boom")
        return

    final.run_supervised(FakeBridge, run_fn=run_fn, sleep_fn=lambda s: None)

    assert FakeBridge.released == 2
