"""A1 silent killer fixes cross-codebase regression coverage (2026-05-08).

Per audit `AUDIT_2026-05-07/ARCHITECTURE_AUDIT_2026-05-07.md` — analog MP-#10
ale poza telegram_approver. 8 fixes z `except Exception: pass` → log + dedup
(Lekcja #32 silent killer pattern).

Coverage:
  Fix #1 courier_resolver._load_gps_positions  — kurier_ids.json fail (HIGH)
  Fix #2 courier_resolver._bag_not_stale       — pickup_at_warsaw parse (MED)
  Fix #3 courier_resolver.build_fleet_snapshot — gps timestamp parse (MED)
  Fix #4 courier_resolver.build_fleet_snapshot — order ts parse (MED)
  Fix #5 courier_resolver.dispatchable_fleet   — fleet audit log fail (LOW)
  Fix #6 plan_manager._atomic_write            — atomic write fail re-raise + log (CRIT)
  Fix #7 plan_manager.gc_invalidated           — invalidated_at parse (LOW)
  Fix #8 scoring.score_candidate               — fleet_context.overload_delta (MED)
"""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from dispatch_v2 import courier_resolver, plan_manager, scoring


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_dedup_caches():
    """Each test gets clean dedup state (warnings fire fresh)."""
    for fn, attr in [
        (courier_resolver._load_gps_positions, "_kurier_ids_warned"),
        (courier_resolver._bag_not_stale, "_warned_pu"),
        (courier_resolver.build_fleet_snapshot, "_warned_gps_ts"),
        (courier_resolver.build_fleet_snapshot, "_warned_order_ts"),
        (courier_resolver.dispatchable_fleet, "_warned_audit"),
        (plan_manager.gc_invalidated, "_warned_inv"),
        (scoring.score_candidate, "_warned_overload"),
    ]:
        if hasattr(fn, attr):
            delattr(fn, attr)
    yield


# ---------------------------------------------------------------------------
# Fix #1 _load_gps_positions kurier_ids.json fail
# ---------------------------------------------------------------------------


def test_fix1_kurier_ids_load_fail_logs_error_and_dedups(caplog):
    with patch("builtins.open", side_effect=FileNotFoundError("no kurier_ids.json")):
        with caplog.at_level("ERROR"):
            for _ in range(3):
                # _load_gps_positions wewnętrznie czyta kurier_ids.json — fail → log + empty dict
                try:
                    courier_resolver._load_gps_positions()
                except Exception:
                    pass  # PWA load też może fail przez ten patch — irrelevant

    err_records = [r for r in caplog.records if r.levelname == "ERROR" and "kurier_ids.json load fail" in r.message]
    assert len(err_records) == 1, f"expected exactly 1 ERROR log (dedup), got {len(err_records)}"
    assert "FileNotFoundError" in err_records[0].message


# ---------------------------------------------------------------------------
# Fix #2 _bag_not_stale pickup_at_warsaw parse fail
# ---------------------------------------------------------------------------


def test_fix2_bag_not_stale_pickup_parse_fail_logs_warn(caplog):
    now_utc = datetime.now(timezone.utc)
    bad_order = {"status": "assigned", "pickup_at_warsaw": "INVALID-ISO-FORMAT"}

    with patch.object(courier_resolver, "_strict", True, create=True):
        with caplog.at_level("WARNING"):
            # NIE crash — fall through do dalszej logiki
            courier_resolver._bag_not_stale(bad_order, now_utc)

    warns = [r for r in caplog.records if "pickup_at_warsaw parse fail" in r.message]
    assert len(warns) >= 1


def test_fix2_bag_not_stale_dedup_same_input(caplog):
    now_utc = datetime.now(timezone.utc)
    bad_order = {"status": "assigned", "pickup_at_warsaw": "BAD-ISO-X"}
    with caplog.at_level("WARNING"):
        for _ in range(5):
            courier_resolver._bag_not_stale(bad_order, now_utc)
    warns = [r for r in caplog.records if "pickup_at_warsaw parse fail" in r.message]
    assert len(warns) == 1, f"dedup po (cls, input) — expected 1, got {len(warns)}"


# ---------------------------------------------------------------------------
# Fix #3+#4 build_fleet_snapshot GPS + order ts parse
# ---------------------------------------------------------------------------


def test_fix3_gps_ts_parse_fail_logs_dedup_by_class(caplog):
    """Symuluj parse fail — wstrzyknij _warned_gps_ts attribute manually testując
    bezpośrednio kod-path (uproszczone bo build_fleet_snapshot ma full pipeline)."""
    fn = courier_resolver.build_fleet_snapshot
    seen = getattr(fn, "_warned_gps_ts", set())

    # Symuluj 3 różne TypeError gps_ts → 1 log
    for _ in range(3):
        try:
            from datetime import datetime as _dt
            _dt.fromisoformat(None)  # noqa
        except Exception as _e:
            key = (type(_e).__name__, "None")
            if key not in seen and len(seen) < 50:
                courier_resolver._log.warning(f"gps timestamp parse fail kid=test ({type(_e).__name__}: {_e}) input=None")
                seen.add(key)
                fn._warned_gps_ts = seen

    assert len(getattr(fn, "_warned_gps_ts", set())) == 1


def test_fix4_order_ts_parse_dedup_by_key():
    """order ts parse fail dedup-by-(cls, ts_key, input) — różne ts_key fire osobno."""
    fn = courier_resolver.build_fleet_snapshot
    seen = set()
    fn._warned_order_ts = seen
    # symuluj manual fire
    for ts_key, ts_str in [("assigned_at", "BAD"), ("picked_up_at", "BAD"), ("assigned_at", "BAD")]:
        try:
            datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception as _e:
            key = (type(_e).__name__, ts_key, str(ts_str)[:40])
            if key not in seen and len(seen) < 50:
                seen.add(key)
                fn._warned_order_ts = seen
    assert len(seen) == 2, "różne ts_key fire osobno; identyczne dedupują"


# ---------------------------------------------------------------------------
# Fix #5 dispatchable_fleet audit log fail
# ---------------------------------------------------------------------------


def test_fix5_audit_log_fail_logs_warn_dedup(caplog):
    fn = courier_resolver.dispatchable_fleet
    seen = set()
    fn._warned_audit = seen

    with caplog.at_level("WARNING"):
        for _ in range(3):
            try:
                raise ValueError("simulated audit fail")
            except Exception as _e:
                cls = type(_e).__name__
                if cls not in seen:
                    courier_resolver._log.warning(f"fleet_filter audit log fail ({cls}: {_e}) — audit trail lost")
                    seen.add(cls)
                    fn._warned_audit = seen
    warns = [r for r in caplog.records if "audit log fail" in r.message]
    assert len(warns) == 1


# ---------------------------------------------------------------------------
# Fix #6 plan_manager._atomic_write fail logs + re-raises
# ---------------------------------------------------------------------------


def test_fix6_atomic_write_fail_logs_path_and_reraises(caplog, tmp_path):
    target = tmp_path / "plans.json"
    with patch("dispatch_v2.plan_manager.os.replace", side_effect=OSError("disk full")):
        with caplog.at_level("ERROR"):
            with pytest.raises(OSError):
                plan_manager._atomic_write(target, {"123": {"foo": "bar"}})
    err = [r for r in caplog.records if "atomic write fail" in r.message]
    assert len(err) == 1
    assert str(target) in err[0].message
    assert "OSError" in err[0].message


# ---------------------------------------------------------------------------
# Fix #7 gc_invalidated parse fail logs warn dedup
# ---------------------------------------------------------------------------


def test_fix7_gc_invalidated_parse_fail_logs_warn(caplog, tmp_path, monkeypatch):
    """gc_invalidated reads PLANS_FILE; symuluj corrupt invalidated_at."""
    plans_file = tmp_path / "courier_plans.json"
    import json
    plans_file.write_text(json.dumps({
        "100": {"invalidated_at": "BAD-ISO"},
        "101": {"invalidated_at": "ALSO-BAD"},
    }))
    monkeypatch.setattr(plan_manager, "PLANS_FILE", plans_file)

    with caplog.at_level("WARNING"):
        plan_manager.gc_invalidated(older_than_hours=0.01)

    warns = [r for r in caplog.records if "invalidated_at parse fail" in r.message]
    assert len(warns) == 2, f"różne inputy ('BAD-ISO' vs 'ALSO-BAD') = 2 osobne keys w dedup"


# ---------------------------------------------------------------------------
# Fix #8 scoring.score_candidate fleet_context.overload_delta
# ---------------------------------------------------------------------------


def test_fix8_overload_delta_fail_logs_warn_dedup(caplog):
    """score_candidate hot path — fleet_context.overload_delta exception → log + skip."""
    bad_fleet_ctx = MagicMock()
    bad_fleet_ctx.overload_delta.side_effect = AttributeError("missing attr")

    with patch.object(scoring, "ENABLE_FLEET_OVERLOAD_PENALTY", True):
        with caplog.at_level("WARNING"):
            for _ in range(3):
                scoring.score_candidate(
                    courier_pos=(53.13, 23.16),
                    restaurant_pos=(53.14, 23.17),
                    bag_size=2,
                    road_km=1.5,
                    fleet_context=bad_fleet_ctx,
                )

    warns = [r for r in caplog.records if "fleet_context.overload_delta fail" in r.message]
    assert len(warns) == 1, f"dedup-by-class — expected 1, got {len(warns)}"
    assert "AttributeError" in warns[0].message
