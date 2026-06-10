"""Shared test isolation helpers for shift_notifications + shift_telegram_router
custom-runner tests (TASK B Phase 2 unification, 2026-05-05).

Custom-runner compatible — NIE pytest fixtures. Each test wraps work in
`with isolated_shift_state():` so module-level state pointers (STATE_FILE,
LEARNING_LOG) get redirected to a per-test tmpdir, restored + cleaned up
in __exit__ even on AssertionError mid-test.

See: memory/lekcje_71_xx_2026-05-05.md (Decoupled State Lifecycles + Test
Isolation) — orig path snapshot MUSI być w __enter__ (NIE __init__) bo
inny test może zmienić module-level constants między init a enter.
"""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator


@contextmanager
def isolated_shift_state() -> Iterator[SimpleNamespace]:
    """Redirect shift_notifications.state STATE_FILE + LEARNING_LOG to tmpdir.

    Yields a SimpleNamespace exposing `tmpdir`, `state_file`, `learning_log`
    (all `Path`). Restores originals + rmtree on exit (try/finally → safe
    even if test body raises).

    Usage:
        with isolated_shift_state():
            # state_mod.STATE_FILE now points at fresh tmp file
            ...

        # OR jeśli test potrzebuje paths:
        with isolated_shift_state() as paths:
            assert paths.state_file.parent.exists()
            ...
    """
    from dispatch_v2.shift_notifications import state as shift_state

    tmpdir = tempfile.mkdtemp(prefix="shift_test_")
    orig_state = shift_state.STATE_FILE
    orig_log = shift_state.LEARNING_LOG
    orig_match_debug = shift_state.MATCH_DEBUG_LOG

    state_file = Path(tmpdir) / "shift_confirmations.json"
    learning_log = Path(tmpdir) / "learning_log.jsonl"
    match_debug_log = Path(tmpdir) / "courier_match_debug.jsonl"
    shift_state.STATE_FILE = state_file
    shift_state.LEARNING_LOG = learning_log
    shift_state.MATCH_DEBUG_LOG = match_debug_log

    paths = SimpleNamespace(
        tmpdir=Path(tmpdir),
        state_file=state_file,
        learning_log=learning_log,
        match_debug_log=match_debug_log,
    )
    try:
        yield paths
    finally:
        shift_state.STATE_FILE = orig_state
        shift_state.LEARNING_LOG = orig_log
        shift_state.MATCH_DEBUG_LOG = orig_match_debug
        shutil.rmtree(tmpdir, ignore_errors=True)
