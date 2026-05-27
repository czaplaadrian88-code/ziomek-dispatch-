"""Sprint 2 Etap 2.2 tests — carry / bag-stack visibility feature.

Forensic Agent D (/tmp/kebab_krol_diagnostic.md):
  KK dinner R6 breach 22.5% — root cause = carry chain (bag z innej
  restauracji + długi ETA do nowego pickup → carry 15-30 min). Pure helpers
  z common.py + integration w score loop dispatch_pipeline (penalty +
  optional hard reject feasibility).

Default flag OFF — needs 14d shadow. Tests pokrywają:
  1. bag_size=0 (pusty bag) → no penalty
  2. bag_size>=1 same restaurant → no penalty (chain stops=0)
  3. bag_size>=1 different restaurant + ETA <= threshold → no penalty
  4. bag_size>=1 different restaurant + ETA > threshold → soft penalty applied
  5. KK dinner + chain_stops>=2 → HARD REJECT (carry_chain_hard_reject True)
  6. Hard reject lunch → False (poza dinner window)
  7. Hard reject inna restauracja w dinner → False (nie w CARRY_RISK_LIST)
  8. Pure determinism + None defensive
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.common import (
    carry_chain_penalty,
    carry_chain_hard_reject,
    is_carry_risk_restaurant,
    CARRY_CHAIN_PENALTY_COEFF,
    CARRY_CHAIN_ETA_THRESHOLD_MIN,
    CARRY_CHAIN_HARD_REJECT_STOPS,
    CARRY_RISK_LIST,
    ENABLE_CARRY_CHAIN_PENALTY,
)


_WARSAW_TZ = ZoneInfo("Europe/Warsaw")


def _warsaw_utc_at(hour, minute=0):
    today = datetime.now(_WARSAW_TZ).date()
    return datetime(today.year, today.month, today.day, hour, minute, tzinfo=_WARSAW_TZ).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Helper sanity
# ---------------------------------------------------------------------------

def test_default_flag_is_off():
    """Carry chain feature ma być default OFF (wymaga 14d shadow przed flip)."""
    # Module-level constant reflects env default; default no env override = OFF
    # (ENABLE_CARRY_CHAIN_PENALTY env="0" by default → False).
    assert ENABLE_CARRY_CHAIN_PENALTY is False, (
        f"ENABLE_CARRY_CHAIN_PENALTY must default OFF, got {ENABLE_CARRY_CHAIN_PENALTY}"
    )


def test_carry_risk_list_starts_with_kk_only():
    """CARRY_RISK_LIST rozszerzalne — start tylko KK."""
    assert "kebab król" in CARRY_RISK_LIST, "Kebab Król must be in CARRY_RISK_LIST"
    assert len(CARRY_RISK_LIST) == 1, (
        f"start with 1 entry (KK), got {len(CARRY_RISK_LIST)}: {CARRY_RISK_LIST}"
    )


def test_is_carry_risk_restaurant_matches():
    assert is_carry_risk_restaurant("Kebab Król - Sienkiewicza") is True
    assert is_carry_risk_restaurant("kebab król 2") is True
    assert is_carry_risk_restaurant("KEBAB KRÓL") is True
    assert is_carry_risk_restaurant("Pizza Hut") is False
    assert is_carry_risk_restaurant(None) is False
    assert is_carry_risk_restaurant("") is False


# ---------------------------------------------------------------------------
# carry_chain_penalty unit tests (Adrian spec: 6+ testów)
# ---------------------------------------------------------------------------

def test_carry_penalty_bag_size_0_no_penalty():
    """bag pusty → no penalty (chain_stops=0, applied=False)."""
    pen, stops, applied = carry_chain_penalty([], "Kebab Król", 25.0)
    assert pen == 0.0
    assert stops == 0
    assert applied is False


def test_carry_penalty_bag_same_restaurant_no_penalty():
    """bag jeden item z TĄ SAMĄ restauracją → chain_stops=0 → no penalty."""
    pen, stops, applied = carry_chain_penalty(
        ["Kebab Król"], "kebab król", 30.0,
    )
    assert pen == 0.0
    assert stops == 0
    assert applied is False


def test_carry_penalty_bag_diff_restaurant_eta_below_threshold_no_penalty():
    """bag inna restauracja, ETA < 15 min próg → chain_stops=1 ale no penalty."""
    pen, stops, applied = carry_chain_penalty(
        ["Pizza Hut"], "Kebab Król", 10.0, threshold_min=15.0,
    )
    assert pen == 0.0
    assert stops == 1
    assert applied is False


def test_carry_penalty_bag_diff_restaurant_eta_above_threshold_applied():
    """bag inna restauracja, ETA > threshold → penalty proporcjonalny do ETA."""
    pen, stops, applied = carry_chain_penalty(
        ["Pizza Hut"], "Kebab Król", 25.0, coeff=1.5, threshold_min=15.0,
    )
    assert applied is True
    assert stops == 1
    expected = -1.5 * 25.0  # -37.5
    assert abs(pen - expected) < 0.001, f"expected {expected}, got {pen}"


def test_carry_penalty_multiple_chain_stops_count_distinct():
    """bag wiele itemów z różnymi restauracjami → chain_stops liczy te !=new."""
    pen, stops, applied = carry_chain_penalty(
        ["Pizza Hut", "Sushi Yo", "Kebab Król"],  # 2 diff (Pizza, Sushi) + 1 same (KK)
        "Kebab Król", 20.0, coeff=1.0, threshold_min=15.0,
    )
    assert stops == 2, f"expected 2 diff stops, got {stops}"
    assert applied is True
    expected = -1.0 * 20.0
    assert abs(pen - expected) < 0.001, f"expected {expected}, got {pen}"


def test_carry_penalty_defensive_none_and_invalid_eta():
    """None/invalid inputs → defensive zero (no crash)."""
    pen, stops, applied = carry_chain_penalty(None, "Kebab Król", None)
    assert pen == 0.0 and stops == 0 and applied is False

    pen, stops, applied = carry_chain_penalty(["Pizza"], "Kebab Król", "garbage")
    # ETA=0 by safety → below threshold → no penalty
    assert pen == 0.0 and applied is False


def test_carry_penalty_uses_module_defaults_when_args_none():
    """coeff/threshold None → moduły default (CARRY_CHAIN_PENALTY_COEFF/THRESHOLD)."""
    pen, stops, applied = carry_chain_penalty(
        ["Pizza Hut"], "Kebab Król", 20.0,  # 20 > default threshold 15
    )
    assert applied is True
    expected = -CARRY_CHAIN_PENALTY_COEFF * 20.0
    assert abs(pen - expected) < 0.001


# ---------------------------------------------------------------------------
# carry_chain_hard_reject tests
# ---------------------------------------------------------------------------

def test_hard_reject_kk_dinner_chain_2plus_True():
    """KK + dinner peak Warsaw + chain_stops>=2 → True."""
    now = _warsaw_utc_at(19, 0)  # Warsaw 19:00 dinner
    assert carry_chain_hard_reject(2, "Kebab Król", now_utc=now) is True
    assert carry_chain_hard_reject(3, "kebab król 2", now_utc=now) is True


def test_hard_reject_kk_lunch_False():
    """KK w lunch (12 Warsaw) → False (poza dinner window)."""
    now = _warsaw_utc_at(12, 0)
    assert carry_chain_hard_reject(2, "Kebab Król", now_utc=now) is False


def test_hard_reject_chain_stops_below_min_False():
    """chain_stops=1 (poniżej min=2) → False nawet w dinner KK."""
    now = _warsaw_utc_at(19, 0)
    assert carry_chain_hard_reject(1, "Kebab Król", now_utc=now) is False


def test_hard_reject_non_kk_restaurant_dinner_False():
    """Restauracja NIE w CARRY_RISK_LIST → False mimo dinner+chain>=2."""
    now = _warsaw_utc_at(19, 0)
    assert carry_chain_hard_reject(3, "Pizza Hut", now_utc=now) is False


def test_hard_reject_kk_boundary_2100_warsaw_False():
    """21:00 Warsaw → False (end exclusive)."""
    now = _warsaw_utc_at(21, 0)
    assert carry_chain_hard_reject(2, "Kebab Król", now_utc=now) is False


def test_hard_reject_defensive_none_restaurant():
    """restaurant_name=None → False (defensive)."""
    now = _warsaw_utc_at(19, 0)
    assert carry_chain_hard_reject(5, None, now_utc=now) is False


# ---------------------------------------------------------------------------
# Integration smoke — pipeline import + helpers wired
# ---------------------------------------------------------------------------

def test_pipeline_imports_carry_chain():
    """dispatch_pipeline import nie crashuje + common helpers callable z pipeline."""
    import dispatch_v2.dispatch_pipeline as dp
    import dispatch_v2.common as C
    assert callable(C.carry_chain_penalty)
    assert callable(C.carry_chain_hard_reject)
    # Pipeline module loaded → score loop refers C.ENABLE_CARRY_CHAIN_PENALTY
    # i C.carry_chain_penalty (py_compile guard upstream — tu zero-side-effect smoke).


def test_shadow_dispatcher_auto_prop_includes_carry_chain():
    """shadow_dispatcher._AUTO_PROP_PREFIXES zawiera 'carry_chain_' → metryki
    auto-propagują się do shadow log (location B)."""
    from dispatch_v2 import shadow_dispatcher
    assert "carry_chain_" in shadow_dispatcher._AUTO_PROP_PREFIXES, (
        f"missing carry_chain_ prefix in _AUTO_PROP_PREFIXES: "
        f"{shadow_dispatcher._AUTO_PROP_PREFIXES}"
    )


# ---------------------------------------------------------------------------
# Module-level smoke (script-style runner)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    tests = [
        test_default_flag_is_off,
        test_carry_risk_list_starts_with_kk_only,
        test_is_carry_risk_restaurant_matches,
        # carry_chain_penalty
        test_carry_penalty_bag_size_0_no_penalty,
        test_carry_penalty_bag_same_restaurant_no_penalty,
        test_carry_penalty_bag_diff_restaurant_eta_below_threshold_no_penalty,
        test_carry_penalty_bag_diff_restaurant_eta_above_threshold_applied,
        test_carry_penalty_multiple_chain_stops_count_distinct,
        test_carry_penalty_defensive_none_and_invalid_eta,
        test_carry_penalty_uses_module_defaults_when_args_none,
        # carry_chain_hard_reject
        test_hard_reject_kk_dinner_chain_2plus_True,
        test_hard_reject_kk_lunch_False,
        test_hard_reject_chain_stops_below_min_False,
        test_hard_reject_non_kk_restaurant_dinner_False,
        test_hard_reject_kk_boundary_2100_warsaw_False,
        test_hard_reject_defensive_none_restaurant,
        # Integration smoke
        test_pipeline_imports_carry_chain,
        test_shadow_dispatcher_auto_prop_includes_carry_chain,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
    if failed:
        sys.exit(1)
    print(f"{len(tests)}/{len(tests)} PASS")
    sys.exit(0)
