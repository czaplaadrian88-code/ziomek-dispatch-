"""FIX 1 (gap z realnego odbioru) + FIX 2 (R-09 oś nowej dostawy) — 2026-05-22.

Diagnoza incydentu 475235 (Raj → Hallera 48/3, peak 12:19 Warsaw):
Ziomek zaproponował Gabriela (cid=179, score -10.86) na zlecenie jadące 3.25 km na NW,
podczas gdy jego bag dostarczał ciasny klaster na WSCHODZIE. Koordynator nadpisał na
393 (Michał K). Root cause = phantom bonus „trajektoria" +30:

  FIX 1 — bug2 interleave gap liczony z gotowości jedzenia (12:39), nie z realnego
          zaplanowanego odbioru TSP. Dla Michała (nowa fala, real odbiór 12:56 vs free
          12:46 = +10 min) ready-time dawał -6.5 → phantom +30.
  FIX 2 — bonus +30 ślepy na kierunek nowej DOSTAWY. R-09 mierzy odbiór (0.98km OK),
          FIX_C cały spread (5.01km<8 OK), a pojedyncza daleka rozbieżna dostawa
          (Hallera) wpada między progi. Średni cosinus R1 (0.304) rozcieńcza outlier.

Real coords z events.db (2026-05-22).
"""

from __future__ import annotations

from datetime import datetime, timezone

from dispatch_v2 import common as C
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _mk(oid, p, d, ready):
    return OrderSim(
        order_id=oid,
        pickup_coords=p,
        delivery_coords=d,
        pickup_ready_at=ready,
        picked_up_at=None,
        status="assigned",
    )


# --- Real geometry of incident 475235 ---
NEW_RAJ = (53.132464, 23.165517)        # Raj pickup (centrum)
NEW_HALLERA = (53.1540003, 23.1327405)  # Hallera 48/3 drop (NW)
READY = _utc("2026-05-22T10:39:28")
NOW = _utc("2026-05-22T10:19:00")
SHIFT_START = _utc("2026-05-22T06:00:00")
SHIFT_END = _utc("2026-05-22T20:00:00")

# Gabriel (179) — bag dostarcza WSCHÓD (Bojary/Skorupy), kurier ~ ostatni odbiór Trzy Po Trzy
GABRIEL_POS = (53.134319, 23.162849)
GABRIEL_BAG = [
    _mk("475220", (53.128252, 23.15241), (53.13924189, 23.1748729), _utc("2026-05-22T10:28:00")),   # Sweet Fit -> Sobieskiego
    _mk("475221", (53.134319, 23.162849), (53.1318486, 23.1724775), _utc("2026-05-22T10:32:00")),   # Trzy Po Trzy -> Warszawska
    _mk("475222", (53.114585, 23.147187), (53.1323114, 23.1738974), _utc("2026-05-22T10:23:00")),   # Retrospekcja -> Bukowskiego
]

# Michał K (393) — bag dostarcza PÓŁNOC (Antoniuk), kurier ~ ostatni odbiór Rany Julek
MICHAL_POS = (53.134203, 23.148828)
MICHAL_BAG = [
    _mk("475214", (53.132464, 23.165517), (53.1428749, 23.1333964), _utc("2026-05-22T10:00:00")),   # Grill Kebab -> Antoniukowska
    _mk("475215", (53.140716, 23.17265), (53.1402744, 23.1364927), _utc("2026-05-22T10:05:00")),    # Chinkali -> Antoniuk Fabryczny
    _mk("475224", (53.134203, 23.148828), (53.1346815, 23.1211054), _utc("2026-05-22T10:14:00")),   # Rany Julek -> Konduktorska
]


def _metrics(courier_pos, bag):
    _, _, metrics, _ = check_feasibility_v2(
        courier_pos=courier_pos,
        bag=bag,
        new_order=_mk("475235", NEW_RAJ, NEW_HALLERA, READY),
        shift_end=SHIFT_END,
        shift_start=SHIFT_START,
        now=NOW,
        pickup_ready_at=READY,
    )
    return metrics


# ============================================================
# FIX 2 — kierunkowa bramka nowej DOSTAWY (metryki + warunek veto)
# ============================================================

def test_fix2_metrics_present():
    m = _metrics(GABRIEL_POS, GABRIEL_BAG)
    assert "r1_new_drop_dist_km" in m, f"missing dist; have {sorted(m)}"
    assert "r1_new_drop_cosine" in m, f"missing cosine; have {sorted(m)}"


def test_fix2_gabriel_newdrop_diverges_would_veto():
    """Gabriel: Hallera NW poza wschodnim klastrem → daleko I rozbieżny → veto."""
    m = _metrics(GABRIEL_POS, GABRIEL_BAG)
    km = m["r1_new_drop_dist_km"]
    cos = m["r1_new_drop_cosine"]
    assert km > C.V326_WAVE_VETO_NEW_DROP_KM, f"dist {km} should exceed {C.V326_WAVE_VETO_NEW_DROP_KM}"
    assert cos < C.V326_WAVE_VETO_NEW_DROP_COS, f"cos {cos} should be below {C.V326_WAVE_VETO_NEW_DROP_COS}"
    would_veto = km > C.V326_WAVE_VETO_NEW_DROP_KM and cos < C.V326_WAVE_VETO_NEW_DROP_COS
    assert would_veto is True


def test_fix2_michal_newdrop_coherent_no_veto():
    """Michał K: bag północny, Hallera północna → blisko I spójny → BEZ veto."""
    m = _metrics(MICHAL_POS, MICHAL_BAG)
    km = m["r1_new_drop_dist_km"]
    cos = m["r1_new_drop_cosine"]
    would_veto = km > C.V326_WAVE_VETO_NEW_DROP_KM and cos < C.V326_WAVE_VETO_NEW_DROP_COS
    assert would_veto is False, f"Michał nie powinien dostać veto (km={km}, cos={cos})"


# ============================================================
# FIX 1 — gap z realnego zaplanowanego odbioru, nie z gotowości jedzenia
# ============================================================

def _gap_min(plan_pickup_iso: str, free_iso: str) -> float:
    pu = datetime.fromisoformat(plan_pickup_iso)
    fa = datetime.fromisoformat(free_iso)
    return (pu - fa).total_seconds() / 60.0


def test_fix1_michal_newwave_zeroes_bonus():
    """Michał: realny odbiór 12:56 vs free 12:46 = +10 min (nowa fala) → bonus 0.

    Ready-time (12:39 vs 12:46 = -6.5) dawał phantom +30.
    """
    real_gap = _gap_min("2026-05-22T10:56:14+00:00", "2026-05-22T10:45:56+00:00")
    ready_gap = _gap_min("2026-05-22T10:39:28+00:00", "2026-05-22T10:45:56+00:00")
    assert real_gap > C.BUG2_INTERLEAVE_GATE_MIN, f"real_gap {real_gap} should be a new wave"
    assert C.bug2_wave_continuation_bonus(real_gap) == 0.0
    # stary (błędny) sygnał dawał pełny bonus:
    assert ready_gap < 0
    assert C.bug2_wave_continuation_bonus(ready_gap) == C.BUG2_WAVE_CONTINUATION_BONUS


def test_fix1_gabriel_true_interleave_keeps_bonus():
    """Gabriel: realny odbiór 12:52 vs free 12:59 = -7 min (faktyczny interleave) → bonus zostaje.

    Gabriel jest karany kierunkowo przez FIX 2, nie czasowo.
    """
    real_gap = _gap_min("2026-05-22T10:52:34+00:00", "2026-05-22T10:59:51+00:00")
    assert real_gap < 0, f"Gabriel realnie wplata odbiór (gap {real_gap})"
    assert C.bug2_wave_continuation_bonus(real_gap) == C.BUG2_WAVE_CONTINUATION_BONUS


if __name__ == "__main__":
    import sys
    import traceback

    tests = [
        test_fix2_metrics_present,
        test_fix2_gabriel_newdrop_diverges_would_veto,
        test_fix2_michal_newdrop_coherent_no_veto,
        test_fix1_michal_newwave_zeroes_bonus,
        test_fix1_gabriel_true_interleave_keeps_bonus,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
