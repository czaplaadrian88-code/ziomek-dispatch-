"""Tests dla core/flags_io.py — atomic flags.json write helper.

Master plan TOP-15 #6 acceptance criteria:
- AC: atomic write basic CRUD
- AC: atomic_write_resists_partial_fail (mock OSError → original preserved + no orphan tempfiles)
- AC: concurrent write 4 procesy × 100 updates → final flags.json valid JSON, all 400 updates present (no lost-update race via LOCK_EX)
- AC: load_flags pure read
- AC: file permissions preserved (0600)
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from dispatch_v2.core.flags_io import (
    _atomic_write_json,
    delete_flag,
    load_flags,
    set_flags,
    update_flag,
)


@pytest.fixture
def tmp_flags(tmp_path):
    """Empty flags.json w tmp_path."""
    p = tmp_path / "flags.json"
    p.write_text("{}", encoding="utf-8")
    return p


def test_atomic_write_basic(tmp_flags):
    data = {"foo": True, "bar": 42, "baz": [1, 2, 3], "nested": {"a": "b"}}
    _atomic_write_json(tmp_flags, data)

    assert json.loads(tmp_flags.read_text(encoding="utf-8")) == data


def test_atomic_write_resists_partial_fail(tmp_flags):
    """Mock OSError podczas json.dump → original preserved, no orphan tempfiles."""
    original = {"keep": True, "preserved": "yes"}
    set_flags(original, path=tmp_flags)

    with patch("dispatch_v2.core.flags_io.json.dump", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            _atomic_write_json(tmp_flags, {"replaced": True})

    # Original preserved
    assert json.loads(tmp_flags.read_text(encoding="utf-8")) == original
    # No orphan tempfiles
    orphans = list(tmp_flags.parent.glob(".flags_io_*"))
    assert orphans == [], f"orphan tempfiles found: {orphans}"


def test_update_flag_existing(tmp_flags):
    set_flags({"foo": False, "bar": "old"}, path=tmp_flags)

    result = update_flag("foo", True, path=tmp_flags)

    assert result == {"foo": True, "bar": "old"}
    assert json.loads(tmp_flags.read_text(encoding="utf-8")) == {"foo": True, "bar": "old"}


def test_update_flag_new(tmp_flags):
    set_flags({"existing": True}, path=tmp_flags)

    update_flag("new_flag", [1, 2, 3], path=tmp_flags)

    expected = {"existing": True, "new_flag": [1, 2, 3]}
    assert json.loads(tmp_flags.read_text(encoding="utf-8")) == expected


def test_delete_flag_existing(tmp_flags):
    set_flags({"foo": True, "bar": False}, path=tmp_flags)

    delete_flag("foo", path=tmp_flags)

    assert json.loads(tmp_flags.read_text(encoding="utf-8")) == {"bar": False}


def test_delete_flag_missing_no_op(tmp_flags):
    set_flags({"existing": True}, path=tmp_flags)

    delete_flag("does_not_exist", path=tmp_flags)

    assert json.loads(tmp_flags.read_text(encoding="utf-8")) == {"existing": True}


def test_load_flags_pure_read(tmp_flags):
    data = {"a": 1, "b": [1, 2]}
    set_flags(data, path=tmp_flags)

    pre_mtime = tmp_flags.stat().st_mtime
    result = load_flags(path=tmp_flags)
    post_mtime = tmp_flags.stat().st_mtime

    assert result == data
    assert pre_mtime == post_mtime  # Pure read, no mutation


def test_load_flags_missing_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist.json"

    assert load_flags(path=missing) == {}


def test_load_flags_empty_file_returns_empty(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")

    assert load_flags(path=empty) == {}


def test_atomic_write_preserves_mode(tmp_path):
    """flags.json is 0600 w produkcji; helper preserves mode on overwrite."""
    p = tmp_path / "flags.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)

    _atomic_write_json(p, {"foo": True})

    assert (p.stat().st_mode & 0o777) == 0o600


def _writer_process(args):
    """Helper dla concurrent test: każdy proces robi N updates."""
    flags_path, proc_id, n_updates = args
    for i in range(n_updates):
        update_flag(f"proc{proc_id}_key{i}", proc_id * 1000 + i, path=Path(flags_path))


def test_concurrent_writes_no_lost_update(tmp_flags):
    """4 procesy × 100 updates = 400 keys; final JSON valid + ALL 400 keys present.

    Master plan #6 AC: 'final flags.json valid JSON, all 400 updates present
    (no lost-update race)'. LOCK_EX gwarantuje strict serialization RMW.
    """
    n_procs = 4
    n_updates = 100
    args = [(str(tmp_flags), pid, n_updates) for pid in range(n_procs)]

    with mp.Pool(n_procs) as pool:
        pool.map(_writer_process, args)

    final = json.loads(tmp_flags.read_text(encoding="utf-8"))

    # File must be valid JSON (atomic guarantee)
    assert isinstance(final, dict)

    # All 400 keys present (LOCK_EX → no lost updates per master plan AC)
    expected_keys = {
        f"proc{pid}_key{i}"
        for pid in range(n_procs)
        for i in range(n_updates)
    }
    actual_keys = set(final.keys())

    missing = expected_keys - actual_keys
    assert not missing, f"Lost updates: {len(missing)}/400 keys missing (sample: {list(missing)[:5]})"

    # Wartości correct
    for pid in range(n_procs):
        for i in range(n_updates):
            key = f"proc{pid}_key{i}"
            assert final[key] == pid * 1000 + i, f"Wrong value for {key}: {final[key]}"


def test_lock_file_created_alongside(tmp_flags):
    """Lock file naming convention: <name>.json.lock."""
    update_flag("test", True, path=tmp_flags)

    lock_file = tmp_flags.parent / (tmp_flags.name + ".lock")
    assert lock_file.exists(), f"Lock file not created at {lock_file}"


def test_atomic_write_unicode_polish(tmp_flags):
    """ensure_ascii=False → polskie znaki preserved."""
    data = {"komunikat": "Łódź — żółć", "emoji": "🚦"}
    _atomic_write_json(tmp_flags, data)

    raw = tmp_flags.read_text(encoding="utf-8")
    assert "Łódź" in raw  # NIE Ś etc.
    assert json.loads(raw) == data
