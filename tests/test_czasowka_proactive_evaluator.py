"""TASK A CZASÓWKI PROACTIVE — evaluator tests (2026-05-05).

Coverage (14):
  Trigger window detection (4):
   1. test_trigger_fires_at_t50_exactly
   2. test_trigger_fires_with_tolerance_minus_1
   3. test_trigger_fires_with_tolerance_plus_1
   4. test_trigger_skips_outside_tolerance

  Flag gating (3):
   5. test_master_flag_off_returns_none
   6. test_t50_per_trigger_flag_off_returns_none
   7. test_t40_per_trigger_flag_off_returns_none

  Idempotency + state (2):
   8. test_idempotent_same_trigger_same_tick
   9. test_t50_does_not_block_t40_for_same_order

  Score threshold (1):
  10. test_t50_below_threshold_routes_to_no_candidate

  Excluded candidates (1):
  11. test_t40_excludes_t50_nie_candidate_via_excluded_list

  T-0 alert (1):
  12. test_t0_alert_fires_when_unassigned_at_pickup

  Edge cases (2):
  13. test_pickup_already_passed_skipped_unless_t0
  14. test_triggers_min_extension_via_flag_no_code_change

Custom-runner pattern (matches tests/test_shift_telegram_router.py).
"""
import sys
from pathlib import Path
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import json
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from dispatch_v2.czasowka_proactive import state as cp_state
from dispatch_v2.czasowka_proactive import evaluator as cp_eval
from dispatch_v2.czasowka_proactive import observability as cp_obs
from dispatch_v2 import czasowka_scheduler as cs

# De-erozja 2026-06-13 (auton/legacy-test-fixes): test_wait_branch dochodzi do
# assess_order/dispatchable_fleet → state_machine._state_path (Faza 2b guard) RZUCA pod
# pytest na ścieżce produkcyjnej. Izolujemy stan do tmpdir (monkeypatch _state_path).
import os as _os
_TMP_STATE_DIR = tempfile.mkdtemp(prefix="czasowka_proactive_state_")
_os.environ["DISPATCH_STATE_DIR"] = _TMP_STATE_DIR
from dispatch_v2 import state_machine as _sm
_sm._state_path = lambda: _os.path.join(_TMP_STATE_DIR, "orders_state.json")


passed, failed = 0, 0


def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  OK {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1
        print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1
        print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ---------- Test fixtures ----------

class _FakeCandidate:
    def __init__(self, courier_id, name, score, feasibility_verdict="MAYBE"):
        self.courier_id = str(courier_id)
        self.name = name
        self.score = score
        self.feasibility_verdict = feasibility_verdict
        self.feasibility_reason = ""
        self.metrics = {}


@contextmanager
def isolated_env(flag_overrides=None):
    """Redirect cp_state STATE_PATH/LOCK_PATH to tmpdir AND override flag()
    to controlled values. Stub tg_send + observability log to capture
    side effects.

    flag_overrides: dict — override flags.json values for test scope.
    """
    tmpdir = tempfile.mkdtemp(prefix="cp_eval_test_")
    orig_state = cp_state.STATE_PATH
    orig_lock = cp_state.LOCK_PATH
    orig_eval_flag = cp_eval.flag
    orig_load_flags = cp_eval.load_flags

    overrides = dict(flag_overrides or {})
    # Default: no flags set (all False/empty)
    flags_dict = dict(overrides)

    def fake_flag(name, default=False):
        return flags_dict.get(name, default)

    def fake_load_flags():
        # Returns dict — flag-list reads via load_flags().get("CZASOWKA_TRIGGERS_MIN")
        return flags_dict

    # Capture tg sends
    tg_calls = []

    def fake_tg_send(text, inline_keyboard, chat_id=None):
        tg_calls.append({
            "text": text, "inline_keyboard": inline_keyboard, "chat_id": chat_id,
        })
        return True

    # Patch cp_eval module-level + telegram_send module
    try:
        cp_state.STATE_PATH = Path(tmpdir) / "czasowka_proposals_state.json"
        cp_state.LOCK_PATH = Path(str(cp_state.STATE_PATH) + ".lock")
        cp_eval.flag = fake_flag
        cp_eval.load_flags = fake_load_flags

        # Monkey-patch telegram_send module's tg_send_text_with_keyboard
        from dispatch_v2.shift_notifications import telegram_send as ts_mod
        orig_tg = ts_mod.tg_send_text_with_keyboard
        ts_mod.tg_send_text_with_keyboard = fake_tg_send

        try:
            yield {"tg_calls": tg_calls, "tmpdir": Path(tmpdir), "flags": flags_dict}
        finally:
            ts_mod.tg_send_text_with_keyboard = orig_tg
    finally:
        cp_state.STATE_PATH = orig_state
        cp_state.LOCK_PATH = orig_lock
        cp_eval.flag = orig_eval_flag
        cp_eval.load_flags = orig_load_flags
        shutil.rmtree(tmpdir, ignore_errors=True)


def _make_osrec(restaurant="Mama Thai", delivery="Mickiewicza 17"):
    return {
        "czas_odbioru_timestamp": "2026-05-05T13:00:00+02:00",
        "courier_id": "26",
        "restaurant": restaurant,
        "delivery_address": delivery,
        "delivery_city": "Białystok",
        "pickup_at_warsaw": "2026-05-05T13:00:00",
    }


def _make_eval_result(best=None, alternatives=None):
    return {
        "best": best,
        "alternatives": alternatives or [],
        "decision": "WAIT",
        "reason": "test",
        "minutes_to_pickup": None,
        "match_quality": None,
    }


def _flags_full_on(extra=None):
    """Helper: produce flag overrides where T-50 + T-40 + master are ENABLED,
    plus tolerance=1, score=60, triggers [50,40] — extra dict overrides any."""
    base = {
        "CZASOWKA_PROACTIVE_ENABLED": True,
        "CZASOWKA_T50_ENABLED": True,
        "CZASOWKA_T40_ENABLED": True,
        "CZASOWKA_T0_ALERT_ENABLED": True,
        "CZASOWKA_TRIGGERS_MIN": [50, 40],
        "CZASOWKA_MIN_PROPOSAL_SCORE": 60,
        "CZASOWKA_TRIGGER_TOLERANCE_MIN": 1,
    }
    if extra:
        base.update(extra)
    return base


def _now():
    return datetime(2026, 5, 5, 11, 0, 0, tzinfo=timezone.utc)


# ============================================================
# Trigger window detection
# ============================================================

def test_trigger_fires_at_t50_exactly():
    with isolated_env(_flags_full_on()) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger(
            "470001", _make_osrec(), 50.0, result, _now(),
        )
        assert fired == 50, f"expected 50, got {fired}"
        assert len(env["tg_calls"]) == 1, f"expected 1 send, got {len(env['tg_calls'])}"
        # Verify state persisted
        st = cp_state.read_proposals_state()
        rec = st["orders"]["470001"]
        assert "50" in rec["triggers_fired"], f"trigger 50 not in {rec['triggers_fired']!r}"
        assert rec["triggers_fired"]["50"]["proposed_cid"] == "413"


t("trigger_fires_at_t50_exactly", test_trigger_fires_at_t50_exactly)


def test_trigger_fires_with_tolerance_minus_1():
    with isolated_env(_flags_full_on()) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger(
            "470002", _make_osrec(), 49.0, result, _now(),
        )
        assert fired == 50, f"|49-50|=1 within tolerance=1, got {fired}"


t("trigger_fires_with_tolerance_minus_1", test_trigger_fires_with_tolerance_minus_1)


def test_trigger_fires_with_tolerance_plus_1():
    with isolated_env(_flags_full_on()) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger(
            "470003", _make_osrec(), 51.0, result, _now(),
        )
        assert fired == 50, f"|51-50|=1 within tolerance=1, got {fired}"


t("trigger_fires_with_tolerance_plus_1", test_trigger_fires_with_tolerance_plus_1)


def test_trigger_skips_outside_tolerance():
    with isolated_env(_flags_full_on()) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        # 47 min — outside both T-50 (|47-50|=3>1) and T-40 (|47-40|=7>1)
        fired = cp_eval.maybe_fire_trigger(
            "470004", _make_osrec(), 47.0, result, _now(),
        )
        assert fired is None, f"expected None outside windows, got {fired}"
        assert len(env["tg_calls"]) == 0


t("trigger_skips_outside_tolerance", test_trigger_skips_outside_tolerance)


# ============================================================
# Flag gating
# ============================================================

def test_master_flag_off_returns_none():
    flags = _flags_full_on()
    flags["CZASOWKA_PROACTIVE_ENABLED"] = False
    with isolated_env(flags) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger(
            "470010", _make_osrec(), 50.0, result, _now(),
        )
        assert fired is None
        assert len(env["tg_calls"]) == 0


t("master_flag_off_returns_none", test_master_flag_off_returns_none)


def test_t50_per_trigger_flag_off_returns_none():
    flags = _flags_full_on()
    flags["CZASOWKA_T50_ENABLED"] = False
    with isolated_env(flags) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger(
            "470011", _make_osrec(), 50.0, result, _now(),
        )
        assert fired is None
        assert len(env["tg_calls"]) == 0


t("t50_per_trigger_flag_off_returns_none", test_t50_per_trigger_flag_off_returns_none)


def test_t40_per_trigger_flag_off_returns_none():
    flags = _flags_full_on()
    flags["CZASOWKA_T40_ENABLED"] = False
    with isolated_env(flags) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger(
            "470012", _make_osrec(), 40.0, result, _now(),
        )
        assert fired is None
        assert len(env["tg_calls"]) == 0


t("t40_per_trigger_flag_off_returns_none", test_t40_per_trigger_flag_off_returns_none)


# ============================================================
# Idempotency + state
# ============================================================

def test_idempotent_same_trigger_same_tick():
    with isolated_env(_flags_full_on()) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        f1 = cp_eval.maybe_fire_trigger("470020", _make_osrec(), 50.0, result, _now())
        f2 = cp_eval.maybe_fire_trigger("470020", _make_osrec(), 50.0, result, _now())
        assert f1 == 50, f"first fire should be 50, got {f1}"
        assert f2 is None, f"second fire idempotent → None, got {f2}"
        assert len(env["tg_calls"]) == 1, f"only 1 send expected, got {len(env['tg_calls'])}"


t("idempotent_same_trigger_same_tick", test_idempotent_same_trigger_same_tick)


def test_t50_does_not_block_t40_for_same_order():
    with isolated_env(_flags_full_on()) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        f50 = cp_eval.maybe_fire_trigger("470021", _make_osrec(), 50.0, result, _now())
        f40 = cp_eval.maybe_fire_trigger("470021", _make_osrec(), 40.0, result, _now())
        assert f50 == 50, f"T-50 fire expected, got {f50}"
        assert f40 == 40, f"T-40 fire expected, got {f40}"
        st = cp_state.read_proposals_state()
        rec = st["orders"]["470021"]
        assert "50" in rec["triggers_fired"]
        assert "40" in rec["triggers_fired"]


t("t50_does_not_block_t40_for_same_order", test_t50_does_not_block_t40_for_same_order)


# ============================================================
# Score threshold
# ============================================================

def test_t50_below_threshold_routes_to_no_candidate():
    flags = _flags_full_on({"CZASOWKA_MIN_PROPOSAL_SCORE": 80})
    with isolated_env(flags) as env:
        # score 50 < threshold 80 → NO_CANDIDATE
        cand = _FakeCandidate("413", "Mateusz O", 50.0)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger("470030", _make_osrec(), 50.0, result, _now())
        assert fired == 50, f"trigger should fire even on no_candidate, got {fired}"
        assert len(env["tg_calls"]) == 1, f"info-only send expected, got {len(env['tg_calls'])}"
        # State should record NO_CANDIDATE decision
        st = cp_state.read_proposals_state()
        rec = st["orders"]["470030"]
        assert rec["triggers_fired"]["50"]["decision"] == "NO_CANDIDATE"
        assert rec["triggers_fired"]["50"]["proposed_cid"] is None


t("t50_below_threshold_routes_to_no_candidate", test_t50_below_threshold_routes_to_no_candidate)


# ============================================================
# Excluded candidates
# ============================================================

def test_t40_excludes_t50_nie_candidate_via_excluded_list():
    """Workflow: T-50 fires + Adrian clicks NIE → callback router writes
    excluded_candidates += [cid]. T-40 must skip that cid."""
    with isolated_env(_flags_full_on()) as env:
        cand_a = _FakeCandidate("413", "Mateusz O", 80.0)
        cand_b = _FakeCandidate("502", "Kacper Sa", 75.0)
        result_t50 = _make_eval_result(best=cand_a, alternatives=[cand_b])
        f50 = cp_eval.maybe_fire_trigger("470040", _make_osrec(), 50.0, result_t50, _now())
        assert f50 == 50, f"T-50 fire expected, got {f50}"

        # Simulate Adrian clicked NIE → mutate state.excluded_candidates
        with cp_state.locked_write_proposals_state() as st:
            rec = st["orders"]["470040"]
            rec["excluded_candidates"].append("413")
            rec["triggers_fired"]["50"]["decision"] = "NIE"

        # T-40 fire — should pick 502 (413 excluded)
        result_t40 = _make_eval_result(best=cand_a, alternatives=[cand_b])
        f40 = cp_eval.maybe_fire_trigger("470040", _make_osrec(), 40.0, result_t40, _now())
        assert f40 == 40, f"T-40 fire expected, got {f40}"
        st_final = cp_state.read_proposals_state()
        rec_final = st_final["orders"]["470040"]
        assert rec_final["triggers_fired"]["40"]["proposed_cid"] == "502", \
            f"expected 502, got {rec_final['triggers_fired']['40']['proposed_cid']!r}"


t("t40_excludes_t50_nie_candidate_via_excluded_list",
  test_t40_excludes_t50_nie_candidate_via_excluded_list)


# ============================================================
# T-0 alert
# ============================================================

def test_t0_alert_fires_when_unassigned_at_pickup():
    flags = _flags_full_on()
    with isolated_env(flags) as env:
        # mins=0 (T-0), id_kurier=26 (Koord — unassigned)
        result = _make_eval_result(best=None)
        fired = cp_eval.maybe_fire_trigger("470050", _make_osrec(), 0.0, result, _now())
        assert fired == 0, f"expected T-0 alert (0), got {fired}"
        assert len(env["tg_calls"]) == 1, f"expected 1 send, got {len(env['tg_calls'])}"
        st = cp_state.read_proposals_state()
        rec = st["orders"]["470050"]
        assert "0" in rec["triggers_fired"]
        assert rec["triggers_fired"]["0"]["decision"] == "ALERT_T0"


t("t0_alert_fires_when_unassigned_at_pickup", test_t0_alert_fires_when_unassigned_at_pickup)


# ============================================================
# Edge cases
# ============================================================

def test_pickup_already_passed_skipped_unless_t0():
    """mins<0 (post-pickup, e.g. -5) should NOT fire normal triggers.
    T-0 only fires when |mins|<1."""
    flags = _flags_full_on()
    with isolated_env(flags) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger(
            "470060", _make_osrec(), -5.0, result, _now(),
        )
        assert fired is None, f"post-pickup should skip, got {fired}"
        assert len(env["tg_calls"]) == 0


t("pickup_already_passed_skipped_unless_t0", test_pickup_already_passed_skipped_unless_t0)


def test_triggers_min_extension_via_flag_no_code_change():
    """CZASOWKA_TRIGGERS_MIN=[70,60,50,45,40] → T-70 is detectable
    purely via flag config, no code change."""
    flags = _flags_full_on({
        "CZASOWKA_TRIGGERS_MIN": [70, 60, 50, 45, 40],
    })
    # Per-trigger flag for new triggers — we mark T70 via per-trigger flag
    flags["CZASOWKA_T70_ENABLED"] = True
    with isolated_env(flags) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        result = _make_eval_result(best=cand)
        fired = cp_eval.maybe_fire_trigger(
            "470070", _make_osrec(), 70.0, result, _now(),
        )
        assert fired == 70, f"T-70 should be detected from flag config, got {fired}"
        assert len(env["tg_calls"]) == 1
        st = cp_state.read_proposals_state()
        assert "70" in st["orders"]["470070"]["triggers_fired"]


t("triggers_min_extension_via_flag_no_code_change",
  test_triggers_min_extension_via_flag_no_code_change)


# ============================================================
# F3 tests — WAIT branch structural data loss fix
# ============================================================

def test_wait_branch_returns_all_candidates():
    """Verify that WAIT branch in czasowka_scheduler._eval_czasowka_impl
    includes 'all_candidates_for_proactive' with >=1 entry when
    result.candidates has entries."""
    # We'll call the internal function with a mock assess_order result
    # that has candidates but no MAYBE best.
    from dispatch_v2.dispatch_pipeline import PipelineResult
    from dispatch_v2.dispatch_pipeline import Candidate as PipeCandidate

    class FakeCand:
        def __init__(self, cid, name, score, feasibility_verdict="MAYBE"):
            self.courier_id = str(cid)
            self.name = name
            self.score = score
            self.feasibility_verdict = feasibility_verdict
            self.feasibility_reason = ""
            self.metrics = {}

    # Build a PipelineResult with candidates but best=None (no MAYBE)
    cands = [FakeCand("413", "Mateusz O", 78.4, "MAYBE"),
             FakeCand("502", "Kacper Sa", 75.0, "MAYBE")]
    # We need to simulate assess_order returning a PipelineResult
    # with .candidates and .best = None
    # We'll create a minimal PipelineResult
    # PipelineResult real schema (dispatch_pipeline.py:869-883):
    # order_id, verdict, reason, best, candidates, pickup_ready_at, restaurant
    pr = PipelineResult(
        order_id="470999",
        verdict="WAIT",
        reason="test",
        best=None,
        candidates=cands,
        pickup_ready_at=None,
        restaurant=None,
    )
    # Patch assess_order to return pr — K09: czasówka woła fasadę core.decide,
    # która robi call-time lookup dispatch_pipeline.assess_order (tam mock).
    import dispatch_v2.czasowka_scheduler as cs_mod
    import dispatch_v2.dispatch_pipeline as dp_mod
    orig_assess = dp_mod.assess_order
    dp_mod.assess_order = lambda *a, **kw: pr
    try:
        # Call _eval_czasowka_impl with a fake order_state
        order_state = {
            "pickup_at_warsaw": "2026-05-06T12:00:00",
            "prep_minutes": 60,
            "courier_id": "26",
            "restaurant": "Test",
            "delivery_address": "Mickiewicza 1",
            "delivery_city": "Białystok",
            # De-erozja 2026-06-13: doszedł guard no_pickup_geocode (czasowka_scheduler:273)
            # — early KOORD exit gdy brak pickup_coords (zanim assess_order zwróci kandydatów).
            # Dajemy realne coords pickup (centrum Białegostoku), żeby dojść do gałęzi WAIT
            # (faktyczna ścieżka testowana: all_candidates_for_proactive z result.candidates).
            "pickup_coords": [53.1325, 23.1688],
        }
        now = datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc)
        result = cs_mod._eval_czasowka_impl("470999", order_state, now)
        assert "all_candidates_for_proactive" in result, \
            f"missing key in {result}"
        assert len(result["all_candidates_for_proactive"]) >= 1, \
            f"expected >=1, got {len(result['all_candidates_for_proactive'])}"
    finally:
        dp_mod.assess_order = orig_assess


t("wait_branch_returns_all_candidates", test_wait_branch_returns_all_candidates)


def test_proactive_uses_all_candidates_when_flag_on():
    """flag=True → _filter_candidates uses all_candidates_for_proactive."""
    flags = _flags_full_on()
    flags["CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES"] = True
    with isolated_env(flags) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        # Build eval_result with all_candidates_for_proactive but empty best/alternatives
        result = {
            "best": None,
            "alternatives": [],
            "all_candidates_for_proactive": [cand],
            "decision": "WAIT",
            "reason": "test",
            "minutes_to_pickup": 50.0,
            "match_quality": "none",
        }
        fired = cp_eval.maybe_fire_trigger(
            "470800", _make_osrec(), 50.0, result, _now(),
        )
        assert fired == 50, f"expected 50, got {fired}"
        assert len(env["tg_calls"]) == 1, f"expected 1 send, got {len(env['tg_calls'])}"
        st = cp_state.read_proposals_state()
        rec = st["orders"]["470800"]
        assert rec["triggers_fired"]["50"]["proposed_cid"] == "413"


t("proactive_uses_all_candidates_when_flag_on",
  test_proactive_uses_all_candidates_when_flag_on)


def test_proactive_legacy_when_flag_off():
    """flag=False → _filter_candidates uses best+alternatives (legacy)."""
    flags = _flags_full_on()
    flags["CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES"] = False
    with isolated_env(flags) as env:
        cand = _FakeCandidate("413", "Mateusz O", 78.4)
        # all_candidates_for_proactive present but flag off → should be ignored
        result = {
            "best": cand,
            "alternatives": [],
            "all_candidates_for_proactive": [],
            "decision": "WAIT",
            "reason": "test",
            "minutes_to_pickup": 50.0,
            "match_quality": "none",
        }
        fired = cp_eval.maybe_fire_trigger(
            "470801", _make_osrec(), 50.0, result, _now(),
        )
        assert fired == 50, f"expected 50, got {fired}"
        assert len(env["tg_calls"]) == 1, f"expected 1 send, got {len(env['tg_calls'])}"
        st = cp_state.read_proposals_state()
        rec = st["orders"]["470801"]
        assert rec["triggers_fired"]["50"]["proposed_cid"] == "413"


t("proactive_legacy_when_flag_off", test_proactive_legacy_when_flag_off)


# ============================================================
# Final report
# ============================================================
print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
