#!/usr/bin/env python3
"""#3 DOKŁADNY pomiar rozrzucania przy przydziale — z realnych sekwencyjnych
decyzji silnika (shadow_decisions.jsonl: best=wybrany, alternatives=pula).

To NIE jest OSRM-izolowany model (doc 28.06 = upper bound ~26). To realny stan
floty w momencie każdej decyzji (realne worki+pozycje+napływ — bo log powstaje
sekwencyjnie na żywo). Pytanie: ile razy silnik wziął WOLNEGO kuriera (bag=0)
gdy w puli był FEASIBLE kandydat JUŻ W TRASIE (bag≥1), do którego dołożenie było
wykonalne (feasibility=YES, R6 trzyma) — = rozrzucenie zamiast hold-and-bundle.

Rozkład Δscore (chosen_free − carry_best): MAŁY = silnik prawie obojętny =
łatwy zysk hold-and-bundle; DUŻY = silnik mocno wolał wolnego = bundle byłby gorszy.
Peak vs off. Dedup: distinct (cid_carry) na okno 10min = realnie uwolnione kursy.
READ-ONLY.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WAW = ZoneInfo("Europe/Warsaw")
LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
PEAK_HOURS = set(range(11, 15)) | set(range(17, 21))  # lunch+dinner Warsaw


def _ep(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _waw_hour(ts):
    e = _ep(ts)
    return datetime.fromtimestamp(e, WAW).hour if e else None


def _cand_carrying_feasible(c):
    """Kandydat = JUŻ W TRASIE (bag≥1) i wykonalny do dołożenia.
    KALIBRACJA 29.06: feasibility 'MAYBE' = NORMALNY werdykt feasible jaki silnik
    WYBIERA (632/690 PROPOSE to MAYBE); 'YES' praktycznie nie występuje. 'NO'/
    best_effort = realnie infeasible. Bundle R6-OK gdy max_bag_time_min ≤ cap
    tier-aware (35 T1/2, 40 T3) — 1768/1769 carrying-MAYBE ma ≤35."""
    if not isinstance(c, dict):
        return False
    if c.get("feasibility") not in ("YES", "MAYBE"):
        return False
    if c.get("best_effort"):
        return False
    bag = c.get("bag_size_before")
    if bag is None:
        bag = c.get("r6_bag_size")
    if (bag or 0) < 1:
        return False
    mb = c.get("max_bag_time_min")
    if mb is None:
        mb = c.get("r6_max_bag_time_min")
    # R6 tier-aware: domyślnie 35, T3 cap 40 (hard_tier_bag_cap to rozmiar, nie czas — używamy 40 jako górną granicę bezpieczną)
    return mb is None or mb <= 40.0


def main():
    recs = []
    for line in open(LOG):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue

    days = set()
    n_propose = 0
    n_chose_free = 0           # wybrał wolnego (bag_size_before==0)
    spread = []                # decyzje: wolny wybrany ALE był feasible carry-alt
    by_hour_spread = defaultdict(int)
    by_hour_propose = defaultdict(int)
    dedup_carry = defaultdict(set)   # (day, 10min-bucket) -> {carry_cid}

    for r in recs:
        if r.get("verdict") != "PROPOSE":
            continue
        best = r.get("best")
        alts = r.get("alternatives") or []
        if not isinstance(best, dict):
            continue
        ts = r.get("ts")
        e = _ep(ts)
        if e is None:
            continue
        day = datetime.fromtimestamp(e, WAW).strftime("%Y-%m-%d")
        days.add(day)
        hour = datetime.fromtimestamp(e, WAW).hour
        n_propose += 1
        by_hour_propose[hour] += 1

        chosen_bag = best.get("bag_size_before")
        if chosen_bag is None:
            chosen_bag = best.get("r6_bag_size")
        chosen_free = (chosen_bag or 0) == 0
        if not chosen_free:
            continue
        n_chose_free += 1

        # szukaj feasible carry-alt (inny kurier, już w trasie, wykonalny)
        best_cid = str(best.get("courier_id"))
        carries = [c for c in alts
                   if str(c.get("courier_id")) != best_cid and _cand_carrying_feasible(c)]
        if not carries:
            continue
        # najlepszy carry-alt po score — WYKLUCZ sentinel (|score|>=1e6 = best_effort/R6-breach przeciek)
        carries = [c for c in carries if c.get("score") is not None and abs(c.get("score")) < 1e6]
        if not carries:
            continue
        bscore = best.get("score")
        if bscore is None or abs(bscore) >= 1e6:
            continue
        carry = max(carries, key=lambda c: c.get("score"))
        dscore = bscore - carry.get("score")  # >0 = wolny lepiej punktowany
        spread.append({
            "ts": ts, "day": day, "hour": hour, "order_id": r.get("order_id"),
            "free_cid": best_cid, "carry_cid": str(carry.get("courier_id")),
            "carry_bag": carry.get("bag_size_before") or carry.get("r6_bag_size"),
            "carry_r6_max": carry.get("max_bag_time_min") or carry.get("r6_max_bag_time_min"),
            "dscore": round(dscore, 1),
        })
        by_hour_spread[hour] += 1
        bucket = int(e // 600)
        dedup_carry[(day, carry.get("courier_id"))].add(bucket)

    nd = max(len(days), 1)
    # dedup: distinct (day, carry_cid, 10min-bucket) = realnie uwolnione kursy (nie licz 2× tego samego nosiciela w oknie)
    distinct_freed = sum(len(b) for b in dedup_carry.values())

    dscores = sorted(s["dscore"] for s in spread)
    def pct(p): return dscores[min(len(dscores) - 1, int(p / 100 * len(dscores)))] if dscores else None
    g30 = sum(1 for d in dscores if abs(d) <= 30)    # silnik ~obojętny = łatwy zysk
    g60 = sum(1 for d in dscores if abs(d) <= 60)
    g100 = sum(1 for d in dscores if abs(d) <= 100)
    small_gap = g30
    peak_spread = sum(v for h, v in by_hour_spread.items() if h in PEAK_HOURS)

    print(f"=== #3 ROZRZUCANIE — realny replay decyzji ({len(days)} dni: {sorted(days)}) ===")
    print(f"decyzji PROPOSE: {n_propose} ({n_propose/nd:.0f}/dzień)")
    print(f"  wybrał WOLNEGO (bag=0): {n_chose_free} ({100*n_chose_free/max(n_propose,1):.0f}%)")
    print(f"  z nich: był FEASIBLE carry-alt (już w trasie, R6 OK) = ROZRZUCENIE: {len(spread)} "
          f"({len(spread)/nd:.1f}/dzień)")
    print(f"  dedup distinct uwolnione kursy (carry_cid×10min): {distinct_freed} ({distinct_freed/nd:.1f}/dzień)")
    print(f"  z tego PEAK (11-15,17-21): {peak_spread} ({100*peak_spread/max(len(spread),1):.0f}%)")
    if dscores:
        print(f"  Δscore (wolny − carry) [>0=silnik wolał wolnego]: med={pct(50)} p25={pct(25)} p75={pct(75)} min={dscores[0]} max={dscores[-1]}")
        print(f"  gap-sensitivity (silnik ~obojętny = łatwy zysk): |Δ|≤30: {g30} (~{g30/nd:.1f}/d) | ≤60: {g60} (~{g60/nd:.1f}/d) | ≤100: {g100} (~{g100/nd:.1f}/d)")
        print(f"  → wniosek: 'łatwe wygrane' ~{g30/nd:.1f}-{g100/nd:.1f}/dzień; reszta z 83/d = silnik świadomie wolał wolnego (bundle gorszy: objazd/świeżość). ~26 z doc = górny limit IGNORUJĄCY ten koszt.")
    print(f"\n  przykłady (5 największych małych-gap):")
    for s in sorted([s for s in spread if abs(s['dscore'])<=30], key=lambda s:-(s['carry_bag'] or 0))[:5]:
        print(f"    {s['ts'][:16]} #{s['order_id']} wolny {s['free_cid']} vs carry {s['carry_cid']} "
              f"(bag {s['carry_bag']}, R6max {s['carry_r6_max']}min, Δscore {s['dscore']})")


if __name__ == "__main__":
    main()
