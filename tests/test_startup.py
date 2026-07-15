"""Elevation and single-instance behavior (Windows APIs faked)."""

import sys
from types import SimpleNamespace

import pytest

import final


class FakeShell32:
    def __init__(self, is_admin, shell_exec_rc):
        self._is_admin = is_admin
        self._rc = shell_exec_rc
        self.exec_calls = []

    def IsUserAnAdmin(self):
        return self._is_admin

    def ShellExecuteW(self, *args):
        self.exec_calls.append(args)
        return self._rc


def install_fake_windll(monkeypatch, shell32):
    monkeypatch.setattr(
        final.ctypes, "windll", SimpleNamespace(shell32=shell32), raising=False
    )


def test_run_as_admin_noop_when_already_elevated(monkeypatch):
    shell32 = FakeShell32(is_admin=1, shell_exec_rc=42)
    install_fake_windll(monkeypatch, shell32)

    final.run_as_admin()

    assert shell32.exec_calls == []


def test_run_as_admin_hands_over_to_elevated_child(monkeypatch):
    shell32 = FakeShell32(is_admin=0, shell_exec_rc=42)
    install_fake_windll(monkeypatch, shell32)

    with pytest.raises(SystemExit) as exc:
        final.run_as_admin()

    assert exc.value.code == 0
    assert len(shell32.exec_calls) == 1


def test_run_as_admin_continues_when_uac_declined(monkeypatch):
    # ShellExecuteW <= 32 means the elevated relaunch never started
    # (5 = access denied, i.e. the user clicked "No" on the UAC prompt).
    shell32 = FakeShell32(is_admin=0, shell_exec_rc=5)
    install_fake_windll(monkeypatch, shell32)

    final.run_as_admin()  # must not raise SystemExit

    assert len(shell32.exec_calls) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows no-op path")
def test_ensure_single_instance_is_noop_off_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))

    final.ensure_single_instance()

    assert final._instance_lock_fd is None
    assert list(tmp_path.iterdir()) == []
