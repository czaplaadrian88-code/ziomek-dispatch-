#!/usr/bin/env python3
"""B1b — DIAGNOZA REALNEJ DŹWIGNI KOORD: skąd biorą się głęboko ujemne base
score w `all_candidates_low_score` KOORD i czy to dźwignia ALGORYTMU czy FLOTY?

TYLKO ODCZYT. Nie dotyka żywych modułów silnika. Liczy na realnych decyzjach
z `shadow_decisions.jsonl` (+ rotacja `.1`).

Mechanika score (z kodu dispatch_pipeline.py:4441/4484):
  final_score = score_result["total"] + bundle_bonus + timing_gap_bonus
              + wave_bonus + bonus_penalty_sum + bonus_bug2_continuation
              + v324a_extension_penalty + (flag-gated deltas)
  bonus_penalty_sum = suma kar miękkich R6/R5/R1/R8/R9/wait/v324a/... (l.4441)
  sentinel -1e9 = v325_new_courier_penalty (kurs nowego kuriera POZA profilem,
    dispatch_pipeline.py:1532 NEG_INF) → kandydat fizycznie nie powinien jechać.

KLASYFIKACJA każdego KOORD na:
  STRUKTURA (brak realnie wykonalnego kuriera) — gdy CHOĆ JEDEN:
    - sentinel: score <= -1e8 (v325 nowy kurier poza profilem)
    - pool_feasible_count <= 1 (cała "pula" = 1 kandydat — flota pusta)
    - R6 dominuje: |bonus_r6_soft_pen| jest największą karą I R6 realnie łamany
      (objm_r6_breach_count>0 lub r6_max_bag_time_min > 35) — 35-min SLA fizyczny
    - kurier daleko: km_to_pickup > 4.5 (long-haul, R7)
    - committed-late: late_pickup_committed_max > 10 (deklarowany czas nie do
      dotrzymania — zimna potrawa gwarantowana)
  ALGORYTM (kara stackuje skądinąd OK kandydata) — reszta: kandydat blisko,
    R6 nie łamany fizycznie, pula >1, a mimo to suma miękkich kar < -100.

Raportuje: liczby+%, histogram score, dominujący komponent, peak/off-peak,
przykłady. Fail-soft.
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
MIN_PROPOSE = -100.0
SENTINEL_CUTOFF = -1e8          # v325 new-courier NEG_INF (-1e9) leaks here
R6_HARD_MAX_MIN = 35.0          # R-35MIN-MAX (common.py BAG_TIME_HARD_MAX)
LONGHAUL_KM = 4.5               # R7 long-haul threshold
COMMITTED_LATE_MIN = 10.0       # committed pickup miss → cold food
SINGLE_POOL = 1                 # pool_feasible_count <= 1 → flota pusta

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie; ścieżka = ledger_io.LEDGER. Agregaty
# są order-independent, metryki BEZ ZMIAN.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

DEFAULT_LOGS = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])

# Nazwane komponenty kary (bonus_penalty_sum + v324a), do analizy „co dominuje".
PENALTY_COMPONENTS = [
    "bonus_r6_soft_pen", "bonus_r5_soft_pen", "bonus_r5_pickup_detour_penalty",
    "bonus_r1_soft_pen", "bonus_r8_soft_pen", "bonus_r9_wait_pen",
    "bonus_r9_stopover", "bonus_v3273_wait_courier", "bonus_r1_corridor",
    "bonus_r5_detour", "bonus_wave_clean", "bonus_inter_wave_deadhead",
    "v324a_extension_penalty", "bonus_bug4_cap_soft",
    "v325_pre_shift_soft_penalty", "bonus_r_return_rest",
]


def _is_peak_warsaw(ts_iso):
    if not ts_iso:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        h = dt.astimezone(WARSAW).hour
        return (11 <= h < 14) or (17 <= h < 20)
    except Exception:
        return None


def _num(d, k, default=None):
    v = d.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _score_bucket(sc):
    if sc <= SENTINEL_CUTOFF:
        return "sentinel(<=-1e8)"
    if sc < -600:
        return "(-inf,-600)"
    if sc < -300:
        return "[-600,-300)"
    if sc < -150:
        return "[-300,-150)"
    if sc < -100:
        return "[-150,-100)"
    return ">=-100"


def _dominant_component(best):
    """Zwraca (nazwa_najgłębszej_kary, wartość) wśród nazwanych komponentów."""
    worst_k, worst_v = None, 0.0
    for c in PENALTY_COMPONENTS:
        v = _num(best, c)
        if v is not None and v < worst_v:
            worst_k, worst_v = c, v
    return worst_k, worst_v


def _classify(best, pool_feasible):
    """STRUKTURA vs ALGORYTM + lista powodów strukturalnych (dla audytu)."""
    reasons = []
    sc = _num(best, "score")
    if sc is not None and sc <= SENTINEL_CUTOFF:
        reasons.append("sentinel_new_courier")
    if pool_feasible is not None and pool_feasible <= SINGLE_POOL:
        reasons.append("pool<=1")
    # R6 dominuje I realnie łamany
    dom_k, _ = _dominant_component(best)
    r6_breach = (_num(best, "objm_r6_breach_count", 0) or 0) > 0
    r6_time = (_num(best, "r6_max_bag_time_min", 0) or 0) > R6_HARD_MAX_MIN
    if dom_k == "bonus_r6_soft_pen" and (r6_breach or r6_time):
        reasons.append("r6_hard_breach")
    if (_num(best, "km_to_pickup", 0) or 0) > LONGHAUL_KM:
        reasons.append("longhaul_pickup")
    if (_num(best, "late_pickup_committed_max", 0) or 0) > COMMITTED_LATE_MIN:
        reasons.append("committed_late")
    cls = "STRUKTURA" if reasons else "ALGORYTM"
    return cls, reasons


def analyze(paths=None):
    paths = paths or DEFAULT_LOGS
    s = {
        "lines": 0, "parse_fail": 0, "koord_total": 0, "low_score": 0,
        "struktura": 0, "algorytm": 0,
        "struct_reason_counts": Counter(),
        "score_hist": Counter(),
        "dominant_component": Counter(),
        "cls_peak": Counter(), "cls_offpeak": Counter(), "cls_unknown": Counter(),
        "examples_struktura": [], "examples_algorytm": [],
    }
    for p in paths:
        if not os.path.exists(p):
            continue
        with _rotated_logs.open_maybe_gz(p) as f:
            for line in f:
                s["lines"] += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    s["parse_fail"] += 1
                    continue
                if d.get("verdict") == "KOORD":
                    s["koord_total"] += 1
                if "all_candidates_low_score" not in str(d.get("reason") or ""):
                    continue
                best = d.get("best") or {}
                sc = _num(best, "score")
                if sc is None:
                    continue
                s["low_score"] += 1
                pool_feasible = _num(d, "pool_feasible_count")
                s["score_hist"][_score_bucket(sc)] += 1
                dom_k, dom_v = _dominant_component(best)
                s["dominant_component"][dom_k or "(none)"] += 1
                cls, reasons = _classify(best, pool_feasible)
                if cls == "STRUKTURA":
                    s["struktura"] += 1
                    for r in reasons:
                        s["struct_reason_counts"][r] += 1
                else:
                    s["algorytm"] += 1
                pk = _is_peak_warsaw(d.get("ts"))
                bucket = (s["cls_peak"] if pk is True
                          else s["cls_offpeak"] if pk is False
                          else s["cls_unknown"])
                bucket[cls] += 1
                ex = {
                    "score": round(sc, 1), "pool_feasible": pool_feasible,
                    "dominant": dom_k, "dominant_val": round(dom_v, 1),
                    "bag": _num(best, "r6_bag_size"),
                    "km_pickup": _num(best, "km_to_pickup"),
                    "r6_max_bag_min": _num(best, "r6_max_bag_time_min"),
                    "reasons": reasons, "peak": pk,
                }
                if cls == "STRUKTURA" and len(s["examples_struktura"]) < 6:
                    s["examples_struktura"].append(ex)
                elif cls == "ALGORYTM" and len(s["examples_algorytm"]) < 6:
                    s["examples_algorytm"].append(ex)
    return s


def _pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def main():
    s = analyze()
    print("=== base_score_decompose — DIAGNOZA B1b (dźwignia KOORD) ===")
    print(f"linie: {s['lines']}  parse_fail: {s['parse_fail']}")
    print(f"KOORD ogółem: {s['koord_total']}")
    print(f"all_candidates_low_score: {s['low_score']} "
          f"({_pct(s['low_score'], s['koord_total'])} KOORD)")
    print()
    print(f">>> STRUKTURA (brak realnie wykonalnego kuriera): {s['struktura']} "
          f"({_pct(s['struktura'], s['low_score'])})")
    print(f">>> ALGORYTM (kara stackuje OK kandydata):        {s['algorytm']} "
          f"({_pct(s['algorytm'], s['low_score'])})")
    print()
    print("powody strukturalne (rekord może mieć kilka):")
    for r, c in s["struct_reason_counts"].most_common():
        print(f"  {c:4d}  {r}")
    print()
    print("histogram best.score:")
    for k in ["sentinel(<=-1e8)", "(-inf,-600)", "[-600,-300)",
              "[-300,-150)", "[-150,-100)", ">=-100"]:
        print(f"  {k:18s} {s['score_hist'].get(k, 0)}")
    print()
    print("dominujący (najgłębszy) komponent kary, liczba rekordów:")
    for k, c in s["dominant_component"].most_common(12):
        print(f"  {c:4d}  {k}")
    print()
    print("peak vs off-peak (STRUKTURA / ALGORYTM):")
    print(f"  peak:     {dict(s['cls_peak'])}")
    print(f"  off-peak: {dict(s['cls_offpeak'])}")
    print(f"  unknown:  {dict(s['cls_unknown'])}")
    print()
    print("przykłady STRUKTURA:")
    for e in s["examples_struktura"]:
        print(f"  score={e['score']} pool={e['pool_feasible']} bag={e['bag']} "
              f"km_pickup={e['km_pickup']} r6_bag_min={e['r6_max_bag_min']} "
              f"dom={e['dominant']}({e['dominant_val']}) reasons={e['reasons']}")
    print("przykłady ALGORYTM:")
    for e in s["examples_algorytm"]:
        print(f"  score={e['score']} pool={e['pool_feasible']} bag={e['bag']} "
              f"km_pickup={e['km_pickup']} r6_bag_min={e['r6_max_bag_min']} "
              f"dom={e['dominant']}({e['dominant_val']})")
    return s


if __name__ == "__main__":
    main()
