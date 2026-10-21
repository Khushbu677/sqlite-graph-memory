"""Tests for the Stop hook's per-turn SQLite ledger write path."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlite_graph_memory import turnstate_hook  # noqa: E402


class _Stdin:
    def __init__(self, payload):
        self.buffer = io.BytesIO(payload)


def _event(kind, content):
    return {"type": kind, "message": {"content": content}}


def _run_hook(monkeypatch, payload):
    monkeypatch.setattr(turnstate_hook.sys, "stdin", _Stdin(json.dumps(payload).encode()))
    turnstate_hook.main()


def test_main_writes_extracted_turn_to_ledger(tmp_path, monkeypatch):
    db_path = tmp_path / "turns.db"
    cwd = tmp_path / "my-project"
    cwd.mkdir()
    transcript_path = tmp_path / "transcript.jsonl"
    events = [
        _event("user", "How should we test the ledger write path?"),
        _event("assistant", [
            {"type": "text", "text": "Decision: we will write one pytest file."},
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": str(cwd / "test_turnstate_hook.py")}},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "python -m pytest -q"}},
        ]),
    ]
    transcript_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    monkeypatch.setenv("TURNSTATE_DB", str(db_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _run_hook(monkeypatch, {
        "session_id": "test-session",
        "transcript_path": str(transcript_path),
        "cwd": str(cwd),
    })

    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT ask, files, tools, commands, decisions, project FROM turns"
        ).fetchone()

    assert row is not None
    ask, files, tools, commands, decisions, project = row
    assert ask == "How should we test the ledger write path?"
    assert json.loads(files) == [str(cwd / "test_turnstate_hook.py")]
    assert json.loads(tools) == ["Write", "Bash"]
    assert json.loads(commands) == ["python -m pytest -q"]
    assert json.loads(decisions) == ["Decision: we will write one pytest file."]
    assert project == "my-project"


def test_main_does_not_write_again_without_new_transcript_bytes(tmp_path, monkeypatch):
    db_path = tmp_path / "turns.db"
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        json.dumps(_event("user", "Record this turn in the ledger.")) + "\n",
        encoding="utf-8")
    payload = {
        "session_id": "repeat-session",
        "transcript_path": str(transcript_path),
        "cwd": str(tmp_path),
    }
    monkeypatch.setenv("TURNSTATE_DB", str(db_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    _run_hook(monkeypatch, payload)
    _run_hook(monkeypatch, payload)

    with sqlite3.connect(db_path) as con:
        count = con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    assert count == 1


def test_module_wrapper_exits_successfully_on_bad_input(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["TURNSTATE_DB"] = str(tmp_path / "turns.db")
    result = subprocess.run(
        [sys.executable, "-m", "sqlite_graph_memory.turnstate_hook"],
        input=b"not json",
        capture_output=True,
        cwd=ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == 0


def test_module_wrapper_exits_successfully_when_transcript_is_missing(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["TURNSTATE_DB"] = str(tmp_path / "turns.db")
    payload = {
        "session_id": "missing-session",
        "transcript_path": str(tmp_path / "missing.jsonl"),
        "cwd": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "-m", "sqlite_graph_memory.turnstate_hook"],
        input=json.dumps(payload).encode(),
        capture_output=True,
        cwd=ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == 0
