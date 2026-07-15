"""Elevation and single-instance behavior (Windows APIs faked)."""

import os
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


def test_run_as_admin_frozen_relaunch_omits_argv0(monkeypatch):
    # Repeating the exe path as a parameter would make argparse kill the
    # elevated child instantly (unrecognized positional argument).
    shell32 = FakeShell32(is_admin=0, shell_exec_rc=42)
    install_fake_windll(monkeypatch, shell32)
    monkeypatch.setattr(final.sys, "frozen", True, raising=False)
    monkeypatch.setattr(final.sys, "argv", ["C:\\app\\ScannerBridge.exe", "--fake-scanner"])

    with pytest.raises(SystemExit):
        final.run_as_admin()

    params = shell32.exec_calls[0][3]
    assert params == '"--fake-scanner"'


def test_run_as_admin_source_relaunch_keeps_script_path(monkeypatch):
    shell32 = FakeShell32(is_admin=0, shell_exec_rc=42)
    install_fake_windll(monkeypatch, shell32)
    monkeypatch.delattr(final.sys, "frozen", raising=False)
    monkeypatch.setattr(final.sys, "argv", ["final.py", "--no-install"])

    with pytest.raises(SystemExit):
        final.run_as_admin()

    params = shell32.exec_calls[0][3]
    assert params == '"final.py" "--no-install"'


def test_ensure_installed_skips_source_runs(monkeypatch):
    # Would otherwise copy bare python.exe (no script) into the install dir
    # and relaunch a process that never starts the bridge.
    monkeypatch.delattr(final.sys, "frozen", raising=False)

    final.ensure_installed()  # must return without touching Windows APIs


def test_launch_command_source_run_includes_script(monkeypatch):
    monkeypatch.delattr(final.sys, "frozen", raising=False)

    cmd = final.launch_command()

    assert f'"{os.path.abspath(final.sys.executable)}"' in cmd
    assert f'"{os.path.abspath(final.__file__)}"' in cmd


def test_launch_command_frozen_is_exe_only(monkeypatch):
    monkeypatch.setattr(final.sys, "frozen", True, raising=False)

    cmd = final.launch_command()

    assert cmd == f'"{os.path.abspath(final.sys.executable)}"'


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows no-op path")
def test_ensure_single_instance_is_noop_off_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))

    final.ensure_single_instance()

    assert final._instance_lock_fd is None
    assert list(tmp_path.iterdir()) == []
