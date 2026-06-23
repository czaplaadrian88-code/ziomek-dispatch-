#!/usr/bin/env python3
"""distance_reshape_replay — replay przeważenia DYSTANSU (Adrian 2026-06-23).

READ-ONLY. Dopełnienie load_reshape_replay dla pytania „czy wagi przekalibrować". TEST B
flagował dystans jako prze-ważony (nie przewiduje on-time, 4+km najlepszy). Sprawdzamy dwa
warianty tą samą DOKŁADNĄ metodą (zmiana rusza final_score o jeden człon, reszta bez zmian):

  s_dystans(km, decay) = 100·exp(−km/decay).  Człon = W_DYSTANS · s_dystans.
  Wariant A: W_DYSTANS 0.30→0.20  → Δscore = (0.20−0.30)·s_dyst(km,5) = −0.10·s_dyst.
  Wariant B: DIST_DECAY 5→10      → Δscore = 0.30·(s_dyst(km,10) − s_dyst(km,5)).

Kandydaci bez realnej pozycji (km_to_pickup=None, no_gps→fleet-avg) → Δ=0 (pokrycie raportowane).
Metryki jak w load replay: %zmian #1, mediana km #1 (czy rośnie = dalsi wygrywają), zgodność
z koordynatorem stary vs nowy, gained/lost/NET + on-time gained.

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/distance_reshape_replay.py
"""
import json
import math
import os
import statistics as st
import sys

BASE = "/root/.openclaw/workspace"
CLEAN = f"{BASE}/dispatch_state/calibration_set_june.jsonl"
SHADOW_LOGS = [f"{BASE}/scripts/logs/shadow_decisions.jsonl", f"{BASE}/scripts/logs/shadow_decisions.jsonl.1"]
W_DYSTANS = 0.30


def s_dyst(km, decay):
    if km is None:
        return None
    return max(0.0, min(100.0, 100.0 * math.exp(-km / decay)))


def delta_A(km):  # W 0.30->0.20
    sd = s_dyst(km, 5.0)
    return None if sd is None else (0.20 - 0.30) * sd


def delta_B(km):  # decay 5->10
    a, b = s_dyst(km, 5.0), s_dyst(km, 10.0)
    return None if a is None else W_DYSTANS * (b - a)


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue


def _cid(v):
    return None if v is None else str(v).strip()


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 2) if xs else None


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def main():
    clean = {str(r.get("oid")): r for r in _read_jsonl(CLEAN)}
    clean_oids = set(clean)
    # zbierz feasible kandydatów per oid (cid, score, km)
    decs = {}
    seen = set()
    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            oid = str(r.get("order_id"))
            if oid not in clean_oids or oid in seen or r.get("verdict") != "PROPOSE":
                continue
            cands = []
            for c in [r.get("best") or {}] + (r.get("alternatives") or []):
                if c.get("feasibility") != "MAYBE":
                    continue
                cid = _cid(c.get("courier_id"))
                sc = _num(c.get("score"))
                if cid and sc is not None:
                    cands.append((cid, sc, _num(c.get("km_to_pickup"))))
            if len(cands) >= 2:
                seen.add(oid)
                decs[oid] = cands

    print(f"[distance_reshape_replay]  decyzji z ≥2 feasible: {len(decs)}")
    cov = sum(1 for cs in decs.values() if all(k is not None for _, _, k in cs))
    print(f"  decyzje gdzie WSZYSCY kandydaci mają realne km: {cov} ({_pct(cov,len(decs))}%)  (reszta: część no_gps→Δ=0)")

    def run(name, delta_fn):
        analyzed = changed = old_ag = new_ag = gained = lost = gk = gok = 0
        old_km, new_km = [], []
        for oid, cands in decs.items():
            analyzed += 1
            real = _cid(clean[oid].get("real_cid"))
            old_top = max(cands, key=lambda x: x[1])
            def nscore(x):
                d = delta_fn(x[2])
                return x[1] + (d or 0.0)
            new_top = max(cands, key=nscore)
            if old_top[2] is not None:
                old_km.append(old_top[2])
            if new_top[2] is not None:
                new_km.append(new_top[2])
            if old_top[0] != new_top[0]:
                changed += 1
            oa, na = (old_top[0] == real), (new_top[0] == real)
            old_ag += int(oa); new_ag += int(na)
            if not oa and na:
                gained += 1
                if clean[oid].get("real_ontime") is not None:
                    gk += 1; gok += int(clean[oid].get("real_ontime") is True)
            if oa and not na:
                lost += 1
        print(f"\n=== {name} (n={analyzed}) ===")
        print(f"  zmieniło #1: {changed} = {_pct(changed,analyzed)}%")
        print(f"  mediana km #1: STARY {_med(old_km)} → NOWY {_med(new_km)}  [↑ = dalsi wygrywają = dystans mniej waży]")
        print(f"  zgodność z koordynatorem: STARY {_pct(old_ag,analyzed)}% → NOWY {_pct(new_ag,analyzed)}%")
        print(f"  gained {gained} | lost {lost} | NET {gained-lost:+}" + (f"  | on-time gained {gok}/{gk}={_pct(gok,gk)}%" if gk else ""))

    run("WARIANT A — W_DYSTANS 0.30→0.20", delta_A)
    run("WARIANT B — DIST_DECAY 5→10 (spłaszczenie)", delta_B)

    print("\n  WERDYKT (do oceny): %zmian ~0 + agreement płaski → dystans też NIE-dźwignia (wagi inert).")
    print("   km #1 nie rośnie → nawet słabszy dystans nie przestawia picków (zdławiony marginesami/bonusami).")
    print("  ⚠ re-rank po score (pierwszorzędny efekt); km_to_pickup≈road_km; no_gps Δ=0.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
