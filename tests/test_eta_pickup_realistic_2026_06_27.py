"""Bug #1 (2026-06-27): realistyczny target odbioru (free_at-aware) zamiast ślepego
eta_pickup_utc. Dowód ON≠OFF na helperze `_target_pickup_floor` (shadow_dispatcher).

Case spustowy: Adrian Citko cid 457, worek [483714 Parkowa carried, 483721 Eat Point].
Realne liczby z shadow_decisions (oid 483665 cid 457): blind target 10:03:30 < free_at
10:16:53 = 13,4 min za wcześnie. ON → target = realistyczny; OFF → blind (bez zmian).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dispatch_v2 import shadow_dispatcher as sd


def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class _FlagPatch:
    """Monkey-patch sd.C.flag → wymusza wartość ENABLE_ETA_PICKUP_REALISTIC."""
    def __init__(self, on):
        self.on = on
        self._orig = None

    def __enter__(self):
        self._orig = sd.C.flag

        def _fake(name, default=False):
            if name == "ENABLE_ETA_PICKUP_REALISTIC":
                return self.on
            return self._orig(name, default)
        sd.C.flag = _fake
        return self

    def __exit__(self, *a):
        sd.C.flag = self._orig


# eta_pickup_utc ślepe (gotowość jedzenia), free_at później (kurier wiezie carried)
ETA_BLIND = "2026-06-27T10:03:30"          # to co dziś floor → ready_time
READY = _dt("2026-06-27T09:58:00")          # gotowość jedzenia (wcześniej niż eta tu)
ETA_REAL = "2026-06-27T10:19:00"            # free_at 10:16:53 + ~2 min dojazd


def test_off_keeps_blind_target():
    best_m = {"eta_pickup_realistic_utc": ETA_REAL}
    with _FlagPatch(False):
        tgt = sd._target_pickup_floor(best_m, _dt(ETA_BLIND), READY)
    assert tgt == _dt(ETA_BLIND), f"OFF musi zostać blind, dostał {tgt}"


def test_on_uses_realistic_target():
    best_m = {"eta_pickup_realistic_utc": ETA_REAL}
    with _FlagPatch(True):
        tgt = sd._target_pickup_floor(best_m, _dt(ETA_BLIND), READY)
    assert tgt == _dt(ETA_REAL), f"ON musi użyć realistycznego, dostał {tgt}"


def test_on_off_differ():
    """Twardy dowód ON≠OFF na tych samych wejściach."""
    best_m = {"eta_pickup_realistic_utc": ETA_REAL}
    with _FlagPatch(False):
        off = sd._target_pickup_floor(best_m, _dt(ETA_BLIND), READY)
    with _FlagPatch(True):
        on = sd._target_pickup_floor(best_m, _dt(ETA_BLIND), READY)
    assert on != off and on > off, f"ON ({on}) musi być późniejszy niż OFF ({off})"


def test_on_idle_courier_no_change():
    """Kurier wolny → eta_pickup_realistic_utc == eta_pickup_utc (pipeline no-op) →
    target identyczny ON i OFF."""
    best_m = {"eta_pickup_realistic_utc": ETA_BLIND}  # realistic == blind (wolny)
    with _FlagPatch(True):
        on = sd._target_pickup_floor(best_m, _dt(ETA_BLIND), READY)
    with _FlagPatch(False):
        off = sd._target_pickup_floor(best_m, _dt(ETA_BLIND), READY)
    assert on == off == _dt(ETA_BLIND)


def test_on_missing_realistic_falls_back_to_blind():
    """Brak pola realistic (stary rekord) → ON nie wybucha, zostaje blind."""
    with _FlagPatch(True):
        tgt = sd._target_pickup_floor({}, _dt(ETA_BLIND), READY)
    assert tgt == _dt(ETA_BLIND)


def test_ready_floor_still_applies():
    """Gdy gotowość PÓŹNIEJ niż realistic → target = gotowość (R-DECLARED-TIME floor)."""
    late_ready = _dt("2026-06-27T10:30:00")
    best_m = {"eta_pickup_realistic_utc": ETA_REAL}
    with _FlagPatch(True):
        tgt = sd._target_pickup_floor(best_m, _dt(ETA_BLIND), late_ready)
    assert tgt == late_ready, f"gotowość-floor musi wygrać, dostał {tgt}"
