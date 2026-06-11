#!/usr/bin/env python3
"""OSRM-01 (audyt 03.06, 2026-06-12): smoke ścieżki fallback OSRM.

Fallback (haversine × HAVERSINE_ROAD_FACTOR_BIALYSTOK + bucket-speed) NIE
odpalił się w produkcji od tygodni (0/2642 decyzji) — gdyby OSRM padł w peak,
flota weszłaby w NIETESTOWANĄ ścieżkę. Ten smoke:

  1. Porównuje fallback vs realny OSRM na próbce realnych par
     (restauracje z restaurant_coords.json × adresy z geocode_cache.json):
     bias/MAE duration_min — żeby fallback „nie zgnił" (dryft kalibracji 1.37).
  2. Ćwiczy MECHANIKĘ circuit-breakera w izolowanym procesie (ten skrypt,
     NIE żywe demony): 3×failure → circuit OPEN → route()/table() zwracają
     fallback bez HTTP → po cooldownie ponowna próba. Weryfikuje kształt
     komórek fallbacku (osrm_fallback=True, duration_min>0).

Czysto read-only wobec produkcji (mutuje TYLKO stan modułu we własnym
procesie). Cron: 1. dzień miesiąca 05:10 — okresowy smoke, wynik w logu.
Użycie: osrm_fallback_smoke.py [--sample N]
"""
import json
import random
import sys
import time

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import osrm_client as oc  # noqa: E402

RESTAURANTS = "/root/.openclaw/workspace/dispatch_state/restaurant_coords.json"
GEOCODE = "/root/.openclaw/workspace/dispatch_state/geocode_cache.json"


def _sample_pairs(n: int) -> list:
    with open(RESTAURANTS) as f:
        rests = json.load(f)
    with open(GEOCODE) as f:
        geo = json.load(f)
    origins = [(r["lat"], r["lng"]) for r in rests.values()
               if isinstance(r, dict) and r.get("lat") and r.get("lng")]
    dests = [(g["lat"], g["lon"]) for g in geo.values()
             if isinstance(g, dict) and g.get("lat") and g.get("lon")]
    dests = [d for d in dests if oc.coords_in_bialystok_bbox(d)]
    origins = [o for o in origins if oc.coords_in_bialystok_bbox(o)]
    rng = random.Random(42)  # deterministyczna próbka (porównywalna m/m)
    return [(rng.choice(origins), rng.choice(dests)) for _ in range(n)]


def smoke_accuracy(n: int) -> dict:
    """Fallback vs realny OSRM na próbce — bias/MAE minut jazdy."""
    from datetime import datetime, timezone
    pairs = _sample_pairs(n)
    diffs, ratios, skipped = [], [], 0
    for o, d in pairs:
        real = oc.route(o, d, use_cache=False)
        if real.get("osrm_fallback") or not real.get("duration_min"):
            skipped += 1
            continue
        fb = oc._haversine_fallback(o, d, datetime.now(timezone.utc))
        fb = oc._apply_traffic_multiplier(fb, datetime.now(timezone.utc))
        if not fb.get("duration_min"):
            skipped += 1
            continue
        diffs.append(fb["duration_min"] - real["duration_min"])
        ratios.append(fb["duration_min"] / real["duration_min"])
    diffs.sort()
    ratios.sort()

    def med(a):
        return a[len(a) // 2] if a else None

    out = {
        "n": len(diffs), "skipped": skipped,
        "bias_med_min": round(med(diffs), 2) if diffs else None,
        "mae_min": round(sum(abs(x) for x in diffs) / len(diffs), 2) if diffs else None,
        "ratio_med": round(med(ratios), 3) if ratios else None,
        "ratio_p90": round(ratios[int(len(ratios) * 0.9)], 3) if ratios else None,
    }
    return out


def smoke_circuit_breaker() -> dict:
    """Mechanika circuit-breakera w TYM procesie (produkcja nietknięta)."""
    res = {}
    # wymuś OPEN: 3 zarejestrowane fail-e
    for _ in range(oc.CIRCUIT_BREAKER_THRESHOLD):
        oc._osrm_record_failure()
    res["circuit_open_after_failures"] = oc._osrm_is_circuit_open()

    # route() pod otwartym circuitem → fallback bez HTTP
    r = oc.route((53.1300, 23.1600), (53.1400, 23.1700), use_cache=False)
    res["route_fallback_flag"] = bool(r.get("osrm_fallback"))
    res["route_fallback_duration_ok"] = bool((r.get("duration_min") or 0) > 0)

    # table() pod otwartym circuitem → macierz fallback
    pts = [(53.1300, 23.1600), (53.1400, 23.1700), (53.1500, 23.1800)]
    m = oc.table(pts, pts)
    cells = [c for row in m for c in row]
    res["table_fallback_all_cells"] = all(c.get("osrm_fallback") for c in cells)
    res["table_offdiag_durations_ok"] = all(
        (m[i][j].get("duration_min") or 0) > 0
        for i in range(3) for j in range(3) if i != j)

    # recovery: cofnij cooldown, success zamyka circuit
    with oc._module_lock:
        oc._osrm_circuit_open_until = time.time() - 1
    res["circuit_closed_after_cooldown"] = not oc._osrm_is_circuit_open()
    oc._osrm_record_success()
    res["failures_reset_after_success"] = (oc._osrm_failures == 0)
    return res


def main():
    n = 100
    if "--sample" in sys.argv:
        n = int(sys.argv[sys.argv.index("--sample") + 1])
    print("== OSRM-01 fallback smoke ==")
    acc = smoke_accuracy(n)
    print(f"accuracy (fallback vs OSRM, n={acc['n']}, skipped={acc['skipped']}): "
          f"bias_med={acc['bias_med_min']}min mae={acc['mae_min']}min "
          f"ratio_med={acc['ratio_med']} ratio_p90={acc['ratio_p90']}")
    cb = smoke_circuit_breaker()
    print(f"circuit-breaker: {cb}")
    ok = all(cb.values()) and (acc["n"] or 0) > 0
    print(f"VERDICT: {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
