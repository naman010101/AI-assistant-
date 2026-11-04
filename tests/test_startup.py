"""Step 1.9: one running copy, and Retry/Cancel on a stopped migration.
No real message box is ever shown (HYBRIDAI_NO_DIALOGS / a fake `ask`)."""
import os
import subprocess
import sys
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from test_core import env  # noqa: E402,F401,F811  (fixture)

import startup  # noqa: E402

windows_only = pytest.mark.skipif(os.name != "nt", reason="named mutex is Windows-only")

HOLDER = (
    "import sys, time; sys.path.insert(0, sys.argv[1]); import startup\n"
    "ok = startup.acquire_single(sys.argv[2]); print('HELD' if ok else 'BUSY', flush=True)\n"
    "time.sleep(60)\n"
)


def _start_holder(name):
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, ROOT, name], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "HELD"
    return proc


def _kill_tree(proc):
    # In a venv, sys.executable is a launcher that starts the real python as a
    # child; killing only the launcher would leave the mutex holder alive.
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
    proc.kill()


def _stop(proc):
    _kill_tree(proc)
    proc.wait(timeout=10)
    proc.stdout.close()


def test_mutex_name_depends_on_data_dir(tmp_path):
    a, b = startup.mutex_name(str(tmp_path / "a")), startup.mutex_name(str(tmp_path / "b"))
    assert a != b and a.startswith("Local\\HybridAI-")
    assert a == startup.mutex_name(str(tmp_path / "a"))


@windows_only
def test_second_acquire_fails_and_no_stale_lock_after_kill():
    name = "Local\\HybridAI-test-" + uuid.uuid4().hex
    proc = _start_holder(name)
    try:
        assert startup.acquire_single(name) is False
        _kill_tree(proc)
        proc.wait(timeout=10)
        assert startup.acquire_single(name) is True
    finally:
        _stop(proc)


@windows_only
def test_second_app_copy_exits_quietly_without_touching_data(tmp_path):
    data = tmp_path / "data" / "HybridAI"
    proc = _start_holder(startup.mutex_name(str(data)))
    try:
        child_env = dict(os.environ, HYBRID_AI_DATA_DIR=str(data), HYBRIDAI_NO_DIALOGS="1")
        run = subprocess.run([sys.executable, os.path.join(ROOT, "app.py")], env=child_env,
                             capture_output=True, text=True, timeout=10, cwd=str(tmp_path))
        assert run.returncode == 0
        assert "already running" in run.stderr
        assert not data.exists()
    finally:
        _stop(proc)


def test_migrate_or_exit_retry_then_success(env, monkeypatch):
    config = env["config"]
    monkeypatch.setattr(startup, "config", config)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise config.MigrationStopped("held")
        return ["ok"]
    monkeypatch.setattr(config, "migrate_legacy", flaky)
    asked = []
    result = startup.migrate_or_exit(ask=lambda text, retry=False: asked.append(text) or "retry")
    assert result == ["ok"] and len(calls) == 2 and asked == ["held"]


def test_migrate_or_exit_cancel_exits_and_creates_no_chats_db(env, monkeypatch):
    config = env["config"]
    monkeypatch.setattr(startup, "config", config)

    def stopped():
        raise config.MigrationStopped("held")
    monkeypatch.setattr(config, "migrate_legacy", stopped)
    with pytest.raises(SystemExit):
        startup.migrate_or_exit(ask=lambda text, retry=False: "cancel")
    assert not os.path.exists(config.DB_PATH)


def test_message_box_uses_stderr_when_dialogs_are_off(monkeypatch, capsys):
    monkeypatch.setenv("HYBRIDAI_NO_DIALOGS", "1")
    assert startup.message_box("hello") == "ok"
    assert startup.message_box("again", retry=True) == "cancel"
    assert "hello" in capsys.readouterr().err
