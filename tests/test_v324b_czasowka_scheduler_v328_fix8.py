"""V3.28 Fix 8 (incident 03.05.2026) — czasowka_scheduler integration tests.

Pre-Fix 8: czasowka_scheduler.py:441 used `state_machine.get_all().get("orders", {})`
ALE state_machine.get_all() returns FLAT dict {order_id: dict}. Wrapper `.get("orders")`
returned {} ZAWSZE → 0 czasówek scheduled przez 12+ dni od V3.24-B deploy.

Test coverage:
- Bug fix verification (state_machine.get_all() flat → main finds czasówki)
- Retroactive filter (CZASOWKA_RETROACTIVE_HOURS env override)
- DRYRUN mode (no Telegram send when DRYRUN_MODE=True)
- Module-level constants verify
"""
import os
import sys
import importlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _build_synthetic_state(now_utc: datetime, n_recent_czasowki: int = 1, n_old_czasowki: int = 1, n_elastyk: int = 1):
    """Build synthetic state_machine.get_all() return — flat dict {oid: order}."""
    state = {}
    for i in range(n_recent_czasowki):
        oid = f"50000{i}"
        state[oid] = {
            "order_id": oid,
            "status": "planned",
            "courier_id": "26",  # Koordynator (czasówka pending)
            "prep_minutes": 90,
            "pickup_at_warsaw": (now_utc + timedelta(minutes=60 + i*10)).isoformat(),
            "first_seen": (now_utc - timedelta(minutes=30)).isoformat(),  # recent
            "updated_at": now_utc.isoformat(),
            "restaurant": "Test Czasowka Restaurant",
            "delivery_address": "Test Adres",
            "delivery_coords": [53.13, 23.16],
            "pickup_coords": [53.14, 23.17],
        }
    for i in range(n_old_czasowki):
        oid = f"40000{i}"
        state[oid] = {
            "order_id": oid,
            "status": "planned",
            "courier_id": "26",
            "prep_minutes": 90,
            "pickup_at_warsaw": (now_utc + timedelta(hours=2)).isoformat(),
            "first_seen": (now_utc - timedelta(hours=5)).isoformat(),  # OLD = filtered out
            "updated_at": (now_utc - timedelta(hours=5)).isoformat(),
            "restaurant": "Stale Czasowka",
            "delivery_address": "Stale Adres",
            "delivery_coords": [53.13, 23.16],
            "pickup_coords": [53.14, 23.17],
        }
    for i in range(n_elastyk):
        oid = f"30000{i}"
        state[oid] = {
            "order_id": oid,
            "status": "planned",
            "courier_id": "26",
            "prep_minutes": 15,  # < 60 → NOT czasowka
            "pickup_at_warsaw": (now_utc + timedelta(minutes=15)).isoformat(),
            "first_seen": (now_utc - timedelta(minutes=10)).isoformat(),
            "updated_at": now_utc.isoformat(),
            "restaurant": "Elastyk Restaurant",
            "delivery_address": "Test",
            "delivery_coords": [53.13, 23.16],
            "pickup_coords": [53.14, 23.17],
        }
    return state


def test_constants_module_level_defaults():
    """V3.28 Fix 8 module-level constants z env-overridable defaults."""
    # Fresh import w isolated env (defaults active)
    if "dispatch_v2.czasowka_scheduler" in sys.modules:
        del sys.modules["dispatch_v2.czasowka_scheduler"]
    # Clear env overrides
    for k in ("CZASOWKA_TELEGRAM_DRYRUN", "CZASOWKA_RETROACTIVE_HOURS", "CZASOWKA_MAX_EMIT_PER_TICK"):
        os.environ.pop(k, None)
    from dispatch_v2 import czasowka_scheduler as cs
    importlib.reload(cs)

    assert cs.DRYRUN_MODE is True  # default ON (SAFE)
    assert cs.RETROACTIVE_HOURS == 2  # default cutoff
    assert cs.MAX_EMIT_PER_TICK == 3  # default rate limit


def test_constants_env_override():
    """Env vars override defaults — verify env.get() pattern correct.

    Note: module loads constants AT IMPORT. Test fresh subprocess do isolated env.
    """
    import subprocess
    out = subprocess.check_output([
        sys.executable, "-c",
        "import os; "
        "os.environ['CZASOWKA_TELEGRAM_DRYRUN']='0'; "
        "os.environ['CZASOWKA_RETROACTIVE_HOURS']='24'; "
        "os.environ['CZASOWKA_MAX_EMIT_PER_TICK']='10'; "
        "import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts'); "
        "from dispatch_v2 import czasowka_scheduler as cs; "
        f"print(f'{{cs.DRYRUN_MODE}}|{{cs.RETROACTIVE_HOURS}}|{{cs.MAX_EMIT_PER_TICK}}')"
    ], text=True).strip()
    assert out == "False|24|10", f"unexpected: {out!r}"


def test_is_czasowka_helper_unchanged():
    """V3.24-B _is_czasowka helper preserved — Fix 8 NIE zmienia decision logic.

    Test backward compat: prep>=60 AND courier_id in (26, '', 'None') → True.
    """
    if "dispatch_v2.czasowka_scheduler" in sys.modules:
        del sys.modules["dispatch_v2.czasowka_scheduler"]
    from dispatch_v2 import czasowka_scheduler as cs

    # Czasowka case
    assert cs._is_czasowka({"prep_minutes": 90, "courier_id": "26"}) is True
    assert cs._is_czasowka({"prep_minutes": 60, "courier_id": ""}) is True
    assert cs._is_czasowka({"prep_minutes": 90, "courier_id": None}) is True

    # NOT czasówka
    assert cs._is_czasowka({"prep_minutes": 30, "courier_id": "26"}) is False  # prep<60
    assert cs._is_czasowka({"prep_minutes": 90, "courier_id": "414"}) is False  # courier_id != 26
    assert cs._is_czasowka({"prep_minutes": None, "courier_id": "26"}) is False  # prep None


def test_main_finds_recent_czasowki_dryrun():
    """V3.28 Fix 8.A integration: main() finds czasówki w flat dict.

    Pre-fix: state_machine.get_all().get("orders", {}) → {} → 0 czasówek
    Post-fix: state_machine.get_all() → flat dict → finds czasówki via _is_czasowka

    Plus Fix 8.C retroactive filter (2h default) — old czasówka excluded,
    recent included. Fix 8.B DRYRUN — no real tg_request call.
    """
    os.environ["CZASOWKA_TELEGRAM_DRYRUN"] = "1"
    os.environ["CZASOWKA_RETROACTIVE_HOURS"] = "2"
    os.environ["CZASOWKA_MAX_EMIT_PER_TICK"] = "3"
    sys.modules.pop("dispatch_v2.czasowka_scheduler", None)
    from dispatch_v2 import czasowka_scheduler as cs

    now_utc = datetime.now(timezone.utc)
    synthetic_state = _build_synthetic_state(now_utc, n_recent_czasowki=2, n_old_czasowki=1, n_elastyk=1)

    # Mock state_machine.get_all() (returns flat dict)
    # Mock _load_state and _save_state to avoid file I/O
    # Mock eval_czasowka to avoid full dispatch_pipeline call
    # Mock _send_koord_alert and _emit_to_event_bus to verify NIE called (DRYRUN)
    with mock.patch.object(cs.state_machine, "get_all", return_value=synthetic_state), \
         mock.patch.object(cs, "_load_state", return_value={"orders": {}}), \
         mock.patch.object(cs, "_save_state"), \
         mock.patch.object(cs, "_cleanup_stale", return_value=0), \
         mock.patch.object(cs, "eval_czasowka") as mock_eval, \
         mock.patch.object(cs, "_send_koord_alert") as mock_koord, \
         mock.patch.object(cs, "_emit_to_event_bus") as mock_emit, \
         mock.patch.object(cs, "_append_eval_log"):
        # Mock eval returns KOORD verdict (most common dla synthetic test)
        mock_eval.return_value = {
            "decision": "KOORD",
            "reason": "synthetic_test",
            "minutes_to_pickup": 60.0,
            "match_quality": "TEST",
            "best": None,
        }

        result = cs.main()
        assert result == 0  # exit code 0 success

    # eval_czasowka called dla 2 recent czasówki (NOT 1 old, NOT 1 elastyk)
    assert mock_eval.call_count == 2, f"expected 2 eval calls (recent czasówki), got {mock_eval.call_count}"
    # DRYRUN ON → _send_koord_alert NIE called (mock_koord untouched)
    assert mock_koord.call_count == 0, "DRYRUN ON, _send_koord_alert MUSI NIE być wywołany"
    # _emit_to_event_bus also NIE called (DRYRUN)
    assert mock_emit.call_count == 0, "DRYRUN ON, _emit_to_event_bus MUSI NIE być wywołany"

    # Cleanup
    for k in ("CZASOWKA_TELEGRAM_DRYRUN", "CZASOWKA_RETROACTIVE_HOURS", "CZASOWKA_MAX_EMIT_PER_TICK"):
        os.environ.pop(k, None)


def test_main_dryrun_off_calls_telegram_send():
    """When DRYRUN=False, _send_koord_alert IS called.

    Direct attribute monkey-patch zamiast env reload (env eval-on-import limitation).
    """
    sys.modules.pop("dispatch_v2.czasowka_scheduler", None)
    from dispatch_v2 import czasowka_scheduler as cs

    now_utc = datetime.now(timezone.utc)
    synthetic_state = _build_synthetic_state(now_utc, n_recent_czasowki=1, n_old_czasowki=0, n_elastyk=0)

    with mock.patch.object(cs, "DRYRUN_MODE", False), \
         mock.patch.object(cs, "RETROACTIVE_HOURS", 24), \
         mock.patch.object(cs, "MAX_EMIT_PER_TICK", 5), \
         mock.patch.object(cs.state_machine, "get_all", return_value=synthetic_state), \
         mock.patch.object(cs, "_load_state", return_value={"orders": {}}), \
         mock.patch.object(cs, "_save_state"), \
         mock.patch.object(cs, "_cleanup_stale", return_value=0), \
         mock.patch.object(cs, "eval_czasowka") as mock_eval, \
         mock.patch.object(cs, "_send_koord_alert") as mock_koord, \
         mock.patch.object(cs, "_append_eval_log"):
        mock_eval.return_value = {
            "decision": "KOORD",
            "reason": "test",
            "minutes_to_pickup": 60.0,
            "match_quality": "TEST",
            "best": None,
        }
        cs.main()

    # DRYRUN=False → _send_koord_alert wywołany 1x
    assert mock_koord.call_count == 1, f"DRYRUN=False, _send_koord_alert powinien być wywołany 1x, got {mock_koord.call_count}"


def test_retroactive_filter_excludes_old_orders():
    """Fix 8.C retroactive filter: orders z first_seen > cutoff_h temu są excluded."""
    os.environ["CZASOWKA_TELEGRAM_DRYRUN"] = "1"
    os.environ["CZASOWKA_RETROACTIVE_HOURS"] = "1"  # 1h cutoff (restrictive)
    sys.modules.pop("dispatch_v2.czasowka_scheduler", None)
    from dispatch_v2 import czasowka_scheduler as cs

    now_utc = datetime.now(timezone.utc)
    synthetic_state = _build_synthetic_state(now_utc, n_recent_czasowki=1, n_old_czasowki=2, n_elastyk=0)
    # Recent: first_seen 30 min ago (within 1h cutoff)
    # Old: first_seen 5h ago (outside)

    with mock.patch.object(cs.state_machine, "get_all", return_value=synthetic_state), \
         mock.patch.object(cs, "_load_state", return_value={"orders": {}}), \
         mock.patch.object(cs, "_save_state"), \
         mock.patch.object(cs, "_cleanup_stale", return_value=0), \
         mock.patch.object(cs, "eval_czasowka") as mock_eval, \
         mock.patch.object(cs, "_send_koord_alert"), \
         mock.patch.object(cs, "_emit_to_event_bus"), \
         mock.patch.object(cs, "_append_eval_log"):
        mock_eval.return_value = {
            "decision": "KOORD",
            "reason": "test",
            "minutes_to_pickup": 60.0,
            "match_quality": "TEST",
            "best": None,
        }
        cs.main()

    # Tylko recent czasówka (1) eval, old (2) excluded by filter
    assert mock_eval.call_count == 1, f"expected 1 (recent), got {mock_eval.call_count}"

    # Cleanup
    for k in ("CZASOWKA_TELEGRAM_DRYRUN", "CZASOWKA_RETROACTIVE_HOURS"):
        os.environ.pop(k, None)
