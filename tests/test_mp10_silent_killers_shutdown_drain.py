"""MP-#10 silent killers + _shutdown_drain regression coverage (2026-05-08).

Per audit `AUDIT_2026-05-07/TELEGRAM_APPROVER_GOD_OBJECT_ASYNC_AUDIT_2026-05-07.md`
Kategoria A — 10 silent killer except handlers + shutdown drain pattern.

Cada fix wymaga test "OLD silent → NEW logged" (Lekcja #32). Dedup cap'y też testowane.

Coverage:
  Fix #1  _authorized_user_ids (HIGH)        — flags load fail → log warning + dedup
  Fix #2  _pickup_ready_warsaw (MED)          — malformed ISO → log warn + (None,None)
  Fix #3  _reason_line rationale (LOW)        — exception → log warn dedup-by-class
  Fix #4  _iso_to_warsaw_hhmm (LOW)           — parse fail → log warn dedup
  Fix #5/6 _parse_pickup_ready_prep_min (MED) — shared helper logged + dedup cap
  Fix #5  _build_keyboard_v2_grid uses helper — bad input → 0.0 + log
  Fix #6  build_keyboard uses helper          — bad input → 0.0 + log
  Fix #7  _prep_minutes_remaining (LOW)       — malformed ISO → log warn dedup
  Fix #8  _systemd_status (HIGH)              — discriminate Timeout/FNF/Permission
  Fix #9  format_status state_machine.stats() — fail → log warn + manual Counter fallback
  Fix #10 _handle_new_courier_callback        — tg_request fail → log ERROR (lost audit)
  Fix #11 _shutdown_drain                     — happy path + error path logged
"""
import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from dispatch_v2 import telegram_approver as ta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_dedup_caches():
    """Each test gets clean dedup state (warnings fire fresh)."""
    for fn_name in (
        "_authorized_user_ids",
        "_reason_line",
        "_iso_to_warsaw_hhmm",
        "_parse_pickup_ready_prep_min",
        "_prep_minutes_remaining",
    ):
        fn = getattr(ta, fn_name, None)
        if fn is None:
            continue
        for attr in ("_warned", "_warned_classes", "_warned_isos"):
            if hasattr(fn, attr):
                delattr(fn, attr)
    yield


@pytest.fixture
def caplog_warn(caplog):
    caplog.set_level(logging.WARNING, logger="telegram_approver")
    return caplog


# ---------------------------------------------------------------------------
# Fix #1 _authorized_user_ids
# ---------------------------------------------------------------------------


def test_authorized_user_ids_flags_corrupt_logs_warning(monkeypatch, caplog_warn, _reset_dedup_caches):
    def _raise(*_a, **_kw):
        raise ValueError("corrupt JSON")

    monkeypatch.setattr(ta, "load_flags", _raise)
    out = ta._authorized_user_ids()
    assert out == ta._KONIEC_AUTHORIZED_USER_IDS_DEFAULT
    assert any(
        "_authorized_user_ids" in r.message and "ValueError" in r.message
        for r in caplog_warn.records
    ), f"expected warning, got: {[r.message for r in caplog_warn.records]}"


def test_authorized_user_ids_dedups_per_process(monkeypatch, caplog_warn, _reset_dedup_caches):
    monkeypatch.setattr(ta, "load_flags", lambda: (_ for _ in ()).throw(IOError("disk")))
    ta._authorized_user_ids()
    ta._authorized_user_ids()
    ta._authorized_user_ids()
    warnings = [r for r in caplog_warn.records if "_authorized_user_ids" in r.message]
    assert len(warnings) == 1, f"expected 1 warn (dedup), got {len(warnings)}"


def test_authorized_user_ids_happy_path_no_warning(monkeypatch, caplog_warn, _reset_dedup_caches):
    monkeypatch.setattr(ta, "load_flags", lambda: {"KONIEC_AUTHORIZED_USER_IDS": [111, 222]})
    out = ta._authorized_user_ids()
    assert out == [111, 222]
    assert not any("_authorized_user_ids" in r.message for r in caplog_warn.records)


# ---------------------------------------------------------------------------
# Fix #2 _pickup_ready_warsaw
# ---------------------------------------------------------------------------


def test_pickup_ready_warsaw_malformed_iso_logs_warning(caplog_warn):
    decision = {"order_id": "469999", "pickup_ready_at": "not-an-iso-string"}
    now_utc = datetime(2026, 5, 8, 17, 0, tzinfo=timezone.utc)
    out = ta._pickup_ready_warsaw(decision, now_utc)
    assert out == (None, None)
    msgs = [r.message for r in caplog_warn.records if "_pickup_ready_warsaw" in r.message]
    assert msgs, "expected warning"
    assert "469999" in msgs[0]
    assert "not-an-iso-string" in msgs[0]


def test_pickup_ready_warsaw_empty_returns_none_no_warning(caplog_warn):
    decision = {"pickup_ready_at": None}
    now_utc = datetime(2026, 5, 8, tzinfo=timezone.utc)
    assert ta._pickup_ready_warsaw(decision, now_utc) == (None, None)
    assert not [r for r in caplog_warn.records if "_pickup_ready_warsaw" in r.message]


# ---------------------------------------------------------------------------
# Fix #3 _reason_line rationale
# ---------------------------------------------------------------------------


def test_reason_line_rationale_exception_logs_dedup_by_class(monkeypatch, caplog_warn, _reset_dedup_caches):
    # Force ENABLE_V326_TRANSPARENCY_RATIONALE on, then make rat.get crash
    from dispatch_v2 import common as _C
    monkeypatch.setattr(_C, "ENABLE_V326_TRANSPARENCY_RATIONALE", True, raising=False)

    class _BadDict:
        def get(self, _k):
            raise RuntimeError("synthetic")

    cand = {"courier_id": 555, "v326_rationale": _BadDict()}
    # Must not crash; must return string (legacy reasons-list path)
    out = ta._reason_line(cand, [cand])
    assert isinstance(out, str)
    # Trigger same exception twice → dedup
    ta._reason_line(cand, [cand])
    warns = [r for r in caplog_warn.records if "_reason_line" in r.message]
    assert len(warns) == 1, f"expected 1 dedup warn, got {len(warns)}"
    assert "RuntimeError" in warns[0].message
    assert "cid=555" in warns[0].message


# ---------------------------------------------------------------------------
# Fix #4 _iso_to_warsaw_hhmm
# ---------------------------------------------------------------------------


def test_iso_to_warsaw_hhmm_malformed_logs_dedup_by_value(caplog_warn, _reset_dedup_caches):
    assert ta._iso_to_warsaw_hhmm("xxx-bad") is None
    assert ta._iso_to_warsaw_hhmm("xxx-bad") is None  # same val → dedup
    assert ta._iso_to_warsaw_hhmm("yyy-bad") is None  # diff val → second warn
    warns = [r for r in caplog_warn.records if "_iso_to_warsaw_hhmm" in r.message]
    assert len(warns) == 2, f"expected 2 warns (per-value dedup), got {len(warns)}"


def test_iso_to_warsaw_hhmm_empty_no_warning(caplog_warn):
    assert ta._iso_to_warsaw_hhmm(None) is None
    assert ta._iso_to_warsaw_hhmm("") is None
    assert not [r for r in caplog_warn.records if "_iso_to_warsaw_hhmm" in r.message]


# ---------------------------------------------------------------------------
# Fix #5/6 _parse_pickup_ready_prep_min (shared helper)
# ---------------------------------------------------------------------------


def test_parse_pickup_ready_prep_min_malformed_logs_warning_returns_zero(caplog_warn, _reset_dedup_caches):
    out = ta._parse_pickup_ready_prep_min("not-iso", oid="469100")
    assert out == 0.0
    msgs = [r.message for r in caplog_warn.records if "_parse_pickup_ready_prep_min" in r.message]
    assert msgs
    assert "469100" in msgs[0]
    assert "not-iso" in msgs[0]


def test_parse_pickup_ready_prep_min_dedup_per_value(caplog_warn, _reset_dedup_caches):
    for _ in range(5):
        ta._parse_pickup_ready_prep_min("bad-1", oid="X")
    for _ in range(5):
        ta._parse_pickup_ready_prep_min("bad-2", oid="X")
    warns = [r for r in caplog_warn.records if "_parse_pickup_ready_prep_min" in r.message]
    assert len(warns) == 2, f"expected 2 dedup warns, got {len(warns)}"


def test_parse_pickup_ready_prep_min_dedup_cap_50(caplog_warn, _reset_dedup_caches):
    for i in range(60):
        ta._parse_pickup_ready_prep_min(f"bad-{i}", oid="X")
    warns = [r for r in caplog_warn.records if "_parse_pickup_ready_prep_min" in r.message]
    # Cap = 50, więc po 50 unique inputów dedup blokuje (zero spam w peak burst)
    assert len(warns) == 50, f"expected cap=50 warns, got {len(warns)}"


def test_parse_pickup_ready_prep_min_empty_returns_zero_no_warning(caplog_warn):
    assert ta._parse_pickup_ready_prep_min(None) == 0.0
    assert ta._parse_pickup_ready_prep_min("") == 0.0
    assert not [r for r in caplog_warn.records if "_parse_pickup_ready_prep_min" in r.message]


def test_parse_pickup_ready_prep_min_valid_iso_returns_positive():
    # 30 min in future → expect positive (close to 30, allow ±1)
    future = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta
    future_iso = (future + timedelta(minutes=30)).isoformat()
    out = ta._parse_pickup_ready_prep_min(future_iso, oid="X")
    assert 28.0 < out < 31.0


def test_build_keyboard_v2_grid_uses_helper_logs_on_bad_pickup(caplog_warn, _reset_dedup_caches):
    cands = [{"courier_id": 100, "name": "X", "travel_min": 10.0}]
    rows = ta._build_keyboard_v2_grid("469200", cands, pickup_ready_at="garbage")
    assert isinstance(rows, list)
    assert any(
        "_parse_pickup_ready_prep_min" in r.message and "469200" in r.message
        for r in caplog_warn.records
    ), "build_keyboard_v2_grid must propagate oid via shared helper"


def test_build_keyboard_uses_helper_logs_on_bad_pickup(caplog_warn, _reset_dedup_caches):
    cands = [{"courier_id": 100, "name": "X", "travel_min": 10.0}]
    out = ta.build_keyboard("469300", cands, pickup_ready_at="garbage")
    assert isinstance(out, dict) and "inline_keyboard" in out
    assert any(
        "_parse_pickup_ready_prep_min" in r.message and "469300" in r.message
        for r in caplog_warn.records
    ), "build_keyboard must propagate oid via shared helper"


# ---------------------------------------------------------------------------
# Fix #7 _prep_minutes_remaining
# ---------------------------------------------------------------------------


def test_prep_minutes_remaining_malformed_logs_warning(caplog_warn, _reset_dedup_caches):
    out = ta._prep_minutes_remaining({"order_id": "469400", "pickup_ready_at": "bad"})
    assert out is None
    msgs = [r.message for r in caplog_warn.records if "_prep_minutes_remaining" in r.message]
    assert msgs
    assert "469400" in msgs[0]


# ---------------------------------------------------------------------------
# Fix #8 _systemd_status
# ---------------------------------------------------------------------------


def test_systemd_status_timeout_logs_dedicated(monkeypatch, caplog_warn):
    import subprocess as _sp

    def _run_timeout(*_a, **_kw):
        raise _sp.TimeoutExpired(cmd="systemctl", timeout=5)

    monkeypatch.setattr(_sp, "run", _run_timeout)
    out = ta._systemd_status()
    assert all(v is False for v in out.values())
    msgs = [r.message for r in caplog_warn.records if "_systemd_status" in r.message]
    assert msgs and any("TIMEOUT" in m for m in msgs), \
        f"expected discriminated TIMEOUT log, got: {msgs}"


def test_systemd_status_filenotfound_logs_dedicated(monkeypatch, caplog_warn):
    import subprocess as _sp

    def _run_fnf(*_a, **_kw):
        raise FileNotFoundError("systemctl: not found")

    monkeypatch.setattr(_sp, "run", _run_fnf)
    out = ta._systemd_status()
    assert all(v is False for v in out.values())
    assert any("missing" in r.message for r in caplog_warn.records if "_systemd_status" in r.message)


def test_systemd_status_permission_logs_dedicated(monkeypatch, caplog_warn):
    import subprocess as _sp

    def _run_perm(*_a, **_kw):
        raise PermissionError("denied")

    monkeypatch.setattr(_sp, "run", _run_perm)
    out = ta._systemd_status()
    assert all(v is False for v in out.values())
    assert any("permission" in r.message for r in caplog_warn.records if "_systemd_status" in r.message)


# ---------------------------------------------------------------------------
# Fix #9 format_status state_machine.stats fallback
# ---------------------------------------------------------------------------


def test_format_status_state_machine_stats_fail_logs_warning(monkeypatch, caplog_warn):
    from dispatch_v2 import state_machine

    def _bad_stats():
        raise AttributeError("synthetic regression")

    monkeypatch.setattr(state_machine, "stats", _bad_stats, raising=False)
    monkeypatch.setattr(state_machine, "get_all", lambda: {}, raising=False)
    monkeypatch.setattr(ta, "_systemd_status", lambda: {})
    monkeypatch.setattr(ta, "_count_delivered_today", lambda *_a, **_kw: 0)
    monkeypatch.setattr(ta, "_count_learning_today", lambda *_a, **_kw: {"TAK": 0, "NIE": 0})
    monkeypatch.setattr(ta, "load_pending", lambda *_a, **_kw: {})
    # format_status will try other helpers — make them safe
    monkeypatch.setattr(ta, "_yesterday_warsaw_range_utc", lambda: (datetime.now(timezone.utc), datetime.now(timezone.utc)))
    monkeypatch.setattr(ta, "_today_warsaw_start_utc", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(ta, "_sla_records_in_range", lambda *_a, **_kw: [])

    try:
        ta.format_status()
    except Exception:
        # Other format_status pieces may need more mocking — we only care that
        # the stats() exception path warning fired before any later crash.
        pass

    msgs = [r.message for r in caplog_warn.records if "format_status" in r.message]
    assert msgs and any("AttributeError" in m for m in msgs), \
        f"expected stats() fail warning, got: {msgs}"


# ---------------------------------------------------------------------------
# Fix #10 _handle_new_courier_callback editMessageReplyMarkup fail
# ---------------------------------------------------------------------------


def test_handle_new_courier_skip_keyboard_clear_fail_logs_error(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="telegram_approver")

    def _tg_request_fail(*_a, **_kw):
        raise ConnectionError("network down")

    monkeypatch.setattr(ta, "tg_request", _tg_request_fail)
    monkeypatch.setattr(ta, "_shift_callback_answer", lambda *_a, **_kw: None)

    state = {"token": "T", "admin_id": "1"}
    cb = {"message": {"message_id": 99}}
    payload = "skip:Albert%20Dec"
    ta._handle_new_courier_callback(state, payload, cb)

    msgs = [r.message for r in caplog.records if r.levelno == logging.ERROR and "_handle_new_courier_callback" in r.message]
    assert msgs, f"expected ERROR log, got: {[r.message for r in caplog.records]}"
    assert "Albert Dec" in msgs[0]
    assert "99" in msgs[0]
    assert "ConnectionError" in msgs[0]


# ---------------------------------------------------------------------------
# Fix #11 _shutdown_drain
# ---------------------------------------------------------------------------


def test_shutdown_drain_happy_path_logs_info(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="telegram_approver")
    pending = {"oid_1": {"x": 1}, "oid_2": {"y": 2}}
    state = {
        "pending_path": str(tmp_path / "pending.json"),
        "pending": pending,
    }

    asyncio.run(ta._shutdown_drain(state))

    # L7.5 delta-fix: drain robi ADDITIVE reconcile (locked_merge_missing) zamiast
    # blind save_pending. Z pustego dysku additive dołoży wszystkie wpisy → saved==pending;
    # log zmieniony "flushed" → "reconciled (delta additive)" (nie kasuje cudzych wpisów shadow).
    import json as _json
    with open(state["pending_path"]) as f:
        saved = _json.load(f)
    assert saved == pending
    msgs = [r.message for r in caplog.records if "shutdown drain" in r.message]
    assert msgs and "pending=2 reconciled" in msgs[0]


def test_shutdown_drain_save_fail_logs_error_does_not_raise(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="telegram_approver")

    # L7.5 delta-fix: drain woła pending_proposals_store.locked_merge_missing (nie save_pending).
    from dispatch_v2 import pending_proposals_store as _pps

    def _bad_merge(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(_pps, "locked_merge_missing", _bad_merge)
    state = {"pending_path": "/tmp/dummy", "pending": {}}

    # MUST NOT raise
    asyncio.run(ta._shutdown_drain(state))

    msgs = [r.message for r in caplog.records if r.levelno == logging.ERROR and "shutdown drain FAIL" in r.message]
    assert msgs and "OSError" in msgs[0]


def test_shutdown_drain_idempotent(tmp_path):
    pending = {"a": 1}
    state = {"pending_path": str(tmp_path / "p.json"), "pending": pending}
    asyncio.run(ta._shutdown_drain(state))
    asyncio.run(ta._shutdown_drain(state))  # second call must succeed
    import json as _json
    with open(state["pending_path"]) as f:
        assert _json.load(f) == pending


# ---------------------------------------------------------------------------
# Fix #11 main_async try/finally integration
# ---------------------------------------------------------------------------


def test_main_async_finally_drains_on_gather_exception(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="telegram_approver")

    drain_called = {"n": 0}

    async def _fake_drain(state):
        drain_called["n"] += 1

    async def _crash(*_a, **_kw):
        raise RuntimeError("synthetic mid-flight crash")

    monkeypatch.setattr(ta, "_shutdown_drain", _fake_drain)
    monkeypatch.setattr(ta, "shadow_tailer", _crash)
    monkeypatch.setattr(ta, "proposal_sender", _crash)
    monkeypatch.setattr(ta, "updates_poller", _crash)
    monkeypatch.setattr(ta, "watchdog", _crash)
    monkeypatch.setattr(ta, "load_config", lambda: {
        "telegram": {"admin_id": "1"},
        "paths": {"shadow_log": str(tmp_path / "shadow.log")},
    })
    monkeypatch.setattr(ta, "_load_env", lambda *_a, **_kw: {"TELEGRAM_BOT_TOKEN": "T"})
    monkeypatch.setattr(ta, "load_pending", lambda *_a, **_kw: {})

    with pytest.raises(RuntimeError, match="synthetic"):
        asyncio.run(ta.main_async())

    assert drain_called["n"] == 1, "finally must invoke _shutdown_drain even on crash"
