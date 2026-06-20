#!/usr/bin/env python3
"""SOON-FREE-COVERAGE — DIAGNOZA: dlaczego 104 deferral-avoidable KOORD idą na
KOORD mimo „zaraz wolnego" kuriera, i ile pokryłby istniejący `_soon_free_probe`?

TYLKO ODCZYT. Czytam silnik (NIE edytuję). Bez commita/flipa/restartu.

ROOT-CAUSE (z kodu + danych — zweryfikowane):
  Sygnał „free_at_min ≤ 15" z FLEET-T15 łączył DWA różne typy kandydatów:
    (1) BUSY kurier kończący worek ≤12 min  → cel `_soon_free_probe`
    (2) PRE_SHIFT kurier (free_at=0, jeszcze nie zaczął zmiany, pusty bag)
  W ~83% przypadków „zwalniający" kandydat to (2) PRE_SHIFT — `_is_pre_shift_cand`
  (dispatch_pipeline.py:523, Fix #7 2026-05-31) demotuje go do bucket=2 POD aktywnych
  kurierów NIEZALEŻNIE od score (nie da się przypisać komuś przed zmianą). Jedyny
  AKTYWNY kurier ma score −1e9/−139 → on jest top[0] → gate all_candidates_low_score
  → KOORD. Pre_shift +105 ląduje w `alternatives` (feasibility=MAYBE, ale zdemowany).

  `_soon_free_probe` (dispatch_pipeline.py:2001) czyta ZAPISANY plan kuriera
  (plan_manager) i wymaga dropoffów z coords → odpala się TYLKO dla busy kuriera
  z workiem. Dla pre_shift (pusty bag, brak planu) zwraca None → soon_free_eligible
  False. Flaga ENABLE_SOON_FREE_CANDIDATE = OFF (shadow). Więc probe to ZŁE
  narzędzie dla tej populacji — pokrywa znikomy ułamek.

POMIAR pokrycia (na 104 deferral z fleet_t15_replay):
  - soon_free_eligible=True na którymś kandydacie → probe wykrywa
  - AND ten kandydat R6-clean + not committed-late → probe→propose byłby FEASIBLE
  - residuum: free≤15 ale soon_free NIE odpala (pre_shift / brak planu)

Klasyfikacja „typu zwalniającego" dla każdego deferral:
  SOON_FREE_BUSY: zwalniający ma soon_free_eligible=True (busy kończy ≤12)
  PRE_SHIFT_START: zwalniający to pos_source=pre_shift (zmiana się zaczyna)
  OTHER_FREE: inne (np. free w (12,15], gps/last_* bez soon_free flag)

RYZYKO „poczekaj 5 min": sprawdzam czy zwalniający kandydat NIE tworzy nowego
R6-breach ani committed-late breach (deferral nie może psuć innej reguły).

Raport: pokrycie + root-cause + residuum + typ zwalniającego. Fail-soft.
"""
import json
import os
from collections import Counter

SENT = -1e8
R6_HARD_MAX = 35.0
LONGHAUL_KM = 4.5
COMMITTED_LATE = 10.0
DEFER_HORIZON_MIN = 15.0
SOON_FREE_MAX_MIN = 12.0     # common.py SOON_FREE_MAX_MIN

DEFAULT_LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]

PENALTY_COMPONENTS = [
    "bonus_r6_soft_pen", "bonus_r5_soft_pen", "bonus_r5_pickup_detour_penalty",
    "bonus_r1_soft_pen", "bonus_r8_soft_pen", "bonus_r9_wait_pen",
    "bonus_r9_stopover", "bonus_v3273_wait_courier", "bonus_r1_corridor",
    "bonus_r5_detour", "bonus_wave_clean", "bonus_inter_wave_deadhead",
    "v324a_extension_penalty", "bonus_bug4_cap_soft",
    "v325_pre_shift_soft_penalty", "bonus_r_return_rest",
]


def _num(d, k, default=None):
    v = d.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _dominant(best):
    wk, wv = None, 0.0
    for c in PENALTY_COMPONENTS:
        v = _num(best, c)
        if v is not None and v < wv:
            wk, wv = c, v
    return wk


def _is_structural(d, best):
    sc = _num(best, "score")
    pf = _num(d, "pool_feasible_count")
    if sc is not None and sc <= SENT:
        return True
    if pf is not None and pf <= 1:
        return True
    if _dominant(best) == "bonus_r6_soft_pen" and (
            (_num(best, "objm_r6_breach_count", 0) or 0) > 0
            or (_num(best, "r6_max_bag_time_min", 0) or 0) > R6_HARD_MAX):
        return True
    if (_num(best, "km_to_pickup", 0) or 0) > LONGHAUL_KM:
        return True
    if (_num(best, "late_pickup_committed_max", 0) or 0) > COMMITTED_LATE:
        return True
    return False


def _r6_clean(c):
    return not ((_num(c, "objm_r6_breach_count", 0) or 0) > 0
                or (_num(c, "r6_max_bag_time_min", 0) or 0) > R6_HARD_MAX)


def _not_late(c):
    return (_num(c, "late_pickup_committed_max", 0) or 0) <= COMMITTED_LATE


def _is_deferral(d, best):
    """Replikuje klasę AVOIDABLE_DEFERRAL z fleet_t15_replay. Zwraca
    (bool, lista_freeing_kandydatów) lub (False, [])."""
    alts = d.get("alternatives") or []
    srcs = [a.get("pos_source") for a in alts]
    if alts and all(s == "pre_shift" for s in srcs):
        return False, []          # STAFFING, nie deferral
    pf = _num(d, "pool_feasible_count")
    frees = [a for a in alts
             if _num(a, "free_at_min") is not None
             and _num(a, "free_at_min") <= DEFER_HORIZON_MIN]
    if pf is not None and pf <= 1 and not frees:
        return False, []          # SINGLE
    if not frees:
        return False, []          # LOAD
    return True, frees


def analyze(paths=None):
    paths = paths or DEFAULT_LOGS
    s = {
        "lines": 0, "parse_fail": 0, "deferral": 0,
        "covered_soon_free": 0,       # soon_free_eligible on some cand
        "covered_feasible": 0,        # AND that cand R6-clean + not-late
        "residuum": 0,
        "freeing_type": Counter(),    # SOON_FREE_BUSY / PRE_SHIFT_START / OTHER_FREE
        "defer_risk_new_breach": 0,   # freeing cand would itself breach R6/committed
        "examples_residuum": [],
    }
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
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
                if "all_candidates_low_score" not in str(d.get("reason") or ""):
                    continue
                best = d.get("best") or {}
                if _num(best, "score") is None or not _is_structural(d, best):
                    continue
                is_def, frees = _is_deferral(d, best)
                if not is_def:
                    continue
                s["deferral"] += 1
                alts = d.get("alternatives") or []
                cands = [best] + alts
                # coverage by soon_free probe
                sf = [c for c in cands if c.get("soon_free_eligible") is True]
                if sf:
                    s["covered_soon_free"] += 1
                    if any(_r6_clean(c) and _not_late(c) for c in sf):
                        s["covered_feasible"] += 1
                else:
                    s["residuum"] += 1
                # type of the soonest-freeing candidate
                fc = min(frees, key=lambda a: _num(a, "free_at_min", 1e9))
                if fc.get("soon_free_eligible") is True:
                    s["freeing_type"]["SOON_FREE_BUSY"] += 1
                elif fc.get("pos_source") == "pre_shift":
                    s["freeing_type"]["PRE_SHIFT_START"] += 1
                else:
                    s["freeing_type"]["OTHER_FREE"] += 1
                # deferral risk: would the freeing cand itself breach?
                if not (_r6_clean(fc) and _not_late(fc)):
                    s["defer_risk_new_breach"] += 1
                if not sf and len(s["examples_residuum"]) < 6:
                    s["examples_residuum"].append({
                        "oid": d.get("order_id"),
                        "best_score": round(_num(best, "score"), 1),
                        "best_possrc": best.get("pos_source"),
                        "freeing_cid": fc.get("courier_id"),
                        "freeing_score": _num(fc, "score"),
                        "freeing_possrc": fc.get("pos_source"),
                        "freeing_free_at": _num(fc, "free_at_min"),
                        "freeing_soon_free_elig": fc.get("soon_free_eligible"),
                    })
    return s


def _pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def main():
    s = analyze()
    tot = s["deferral"]
    print("=== soon_free_coverage — SOON-FREE-COVERAGE diagnoza ===")
    print(f"linie: {s['lines']}  parse_fail: {s['parse_fail']}")
    print(f"DEFERRAL-avoidable (z fleet_t15): {tot}")
    print()
    print(">>> POKRYCIE przez istniejący _soon_free_probe (≤12 min, czyta zapisany plan):")
    print(f"  soon_free_eligible na którymś kandydacie: {s['covered_soon_free']} "
          f"({_pct(s['covered_soon_free'], tot)})")
    print(f"  AND ten kandydat R6-clean + not-late (probe→propose FEASIBLE): "
          f"{s['covered_feasible']} ({_pct(s['covered_feasible'], tot)})")
    print(f">>> RESIDUUM (probe NIE pokrywa): {s['residuum']} "
          f"({_pct(s['residuum'], tot)})")
    print()
    print("TYP najszybciej-zwalniającego kandydata (root-cause):")
    for k in ["SOON_FREE_BUSY", "PRE_SHIFT_START", "OTHER_FREE"]:
        print(f"  {k:18s} {s['freeing_type'].get(k, 0):4d} "
              f"({_pct(s['freeing_type'].get(k, 0), tot)})")
    print()
    print(f"RYZYKO deferralu: zwalniający kandydat sam łamałby R6/committed: "
          f"{s['defer_risk_new_breach']} ({_pct(s['defer_risk_new_breach'], tot)})")
    print()
    print("przykłady RESIDUUM (probe nie odpala — zwykle pre_shift):")
    for e in s["examples_residuum"]:
        print(f"  oid={e['oid']} best={e['best_score']}({e['best_possrc']}) "
              f"freeing cid={e['freeing_cid']} score={e['freeing_score']} "
              f"src={e['freeing_possrc']} free_at={e['freeing_free_at']} "
              f"soon_free_elig={e['freeing_soon_free_elig']}")
    return s


if __name__ == "__main__":
    main()
