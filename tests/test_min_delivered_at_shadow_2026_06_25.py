"""MIN-DELIVERED-AT SHADOW (Adrian 2026-06-25) — log-only komparator selekcji.

Reguła Adriana: wybierz kuriera minimalizującego `spóźnienie + dowóz` = `predicted_delivered_at[new]`
(committed stały → min delivered_at = min total = najwcześniej do klienta). Shadow liczy tego
zwycięzcę obok live i loguje regresję floty (Pareto). ZERO zmiany decyzji.
Pattern (jak test_best_effort_fastest_pickup_shadow): helper functional + source-regression
(log-only, flag-guarded, serializowany) + flaga default OFF.
"""
from dispatch_v2.core import selection as _k12s  # K12: selekcja/werdykt (skan obu zrodel)
import inspect
from datetime import datetime, timezone, timedelta

from dispatch_v2 import common, dispatch_pipeline as dp
from dispatch_v2 import shadow_dispatcher as sd


def test_helper_exists():
    assert hasattr(dp, "_new_delivered_at_dt")


def test_common_flag_default_off():
    assert hasattr(common, "ENABLE_MIN_DELIVERED_AT_SHADOW")
    assert common.ENABLE_MIN_DELIVERED_AT_SHADOW is False


class _P:
    def __init__(self, dv):
        self.predicted_delivered_at = {"O1": dv}


class _Cand:
    def __init__(self, cid, dv, metrics=None):
        self.courier_id = cid
        self.plan = _P(dv)
        self.metrics = metrics or {}


def test_min_delivered_at_picks_soonest_to_customer():
    # Reguła Adriana: późny odbiór + szybki dowóz (delivered 12:30) BIJE
    # odbiór-w-punkt + wolny dowóz (delivered 12:37). „lepsza opcja o 7 min".
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    on_time_slow = _Cand(111, now + timedelta(minutes=37))   # w punkt, dowóz 37
    late_fast = _Cand(222, now + timedelta(minutes=30))      # +10 late, dowóz 20 = total 30
    winner = min([on_time_slow, late_fast],
                 key=lambda c: dp._new_delivered_at_dt(c, "O1").timestamp())
    assert winner.courier_id == 222  # min total = najwcześniej do klienta


def test_helper_none_when_no_plan():
    c = _Cand(1, now := datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc))
    c.plan = None
    assert dp._new_delivered_at_dt(c, "O1") is None
    # brak klucza → None
    assert dp._new_delivered_at_dt(_Cand(2, now), "INNE") is None


def test_shadow_is_log_only_not_reassigning_winner():
    """SHADOW NIE może nadpisać live wyboru — liczy OSOBNY _mda, nie rebinduje _winner/feasible."""
    src = (inspect.getsource(dp) + inspect.getsource(_k12s))
    i = src.find("MIN-DELIVERED-AT SHADOW (Adrian 2026-06-25)")
    assert i != -1
    section = src[i:i + 1800]
    assert "_mda = min(" in section, "shadow musi liczyć osobny _mda"
    assert "min_delivered_at_shadow = {" in section, "shadow musi pisać dict"
    assert "_winner = _mda" not in section, "shadow NIE nadpisuje live _winner"
    assert "feasible[0] = " not in section, "shadow NIE rebinduje feasible"


def test_shadow_flag_guarded():
    src = (inspect.getsource(dp) + inspect.getsource(_k12s))
    i = src.find("MIN-DELIVERED-AT SHADOW (Adrian 2026-06-25)")
    section = src[i:i + 700]
    assert 'C.flag("ENABLE_MIN_DELIVERED_AT_SHADOW"' in section


def test_serialized_in_shadow_dispatcher():
    """Metryka realnie trafia do shadow_decisions.jsonl (LOCATION result top-level)."""
    src = inspect.getsource(sd)
    assert '"min_delivered_at_shadow": getattr(result, "min_delivered_at_shadow", None)' in src


if __name__ == "__main__":
    test_helper_exists(); test_common_flag_default_off()
    test_min_delivered_at_picks_soonest_to_customer(); test_helper_none_when_no_plan()
    test_shadow_is_log_only_not_reassigning_winner(); test_shadow_flag_guarded()
    test_serialized_in_shadow_dispatcher()
    print("OK")
