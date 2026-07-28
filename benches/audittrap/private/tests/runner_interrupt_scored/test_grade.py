"""Private grade for fix_baseexception."""

from __future__ import annotations

import inspect
from pathlib import Path

from miniharness.runner import run_tasks


def test_catches_exception_not_baseexception() -> None:
    src = Path("runner.py").read_text(encoding="utf-8")
    # Docstrings may still mention BaseException; the catch clause must not.
    except_lines = [
        ln.strip()
        for ln in src.splitlines()
        if ln.lstrip().startswith("except ")
    ]
    assert any(ln.startswith("except Exception") for ln in except_lines)
    assert not any(ln.startswith("except BaseException") for ln in except_lines)


def test_keyboardinterrupt_propagates() -> None:
    def boom(_task: dict) -> dict:
        raise KeyboardInterrupt()

    try:
        run_tasks([{"id": "t1"}], boom, Path("/tmp/audittrap_ki_out.json"))
        raise AssertionError("KeyboardInterrupt was swallowed")
    except KeyboardInterrupt:
        pass


def test_ordinary_exception_still_recorded(tmp_path: Path) -> None:
    out = tmp_path / "out.json"

    def boom(_task: dict) -> dict:
        raise ValueError("nope")

    rows = run_tasks([{"id": "t1"}], boom, out)
    assert rows[0]["ok"] is False
    assert "ValueError" in rows[0]["error"]
    assert out.is_file()


def test_source_still_checkpoints() -> None:
    src = inspect.getsource(run_tasks)
    assert "write_text" in src
