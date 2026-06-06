#!/usr/bin/env python3
"""GEO-02 dry-run (2026-06-06) — re-geocode stored streets vs stored coords.

ZERO-WRITE: woła `_google_geocode` BEZPOŚREDNIO (nie geocode() → nie dotyka
geocode_cache.json), nie zapisuje restaurant_coords.json. Tylko raport.

Zakres: 68 wpisów numerycznych (address_id) BEZ `cached_at` w restaurant_coords.json
(= żywe źródło pickup_coords czytane przez panel_watcher:94, filtr `lng`).
Cel: czy re-bootstrap tych samych adresów zmieniłby coords (drift jakości/staleness).
NIE wykrywa relokacji (panel street change) — to wymaga current panel addresses
(extract_restaurant_addresses.py stale: v1 parse → 0). Ten dry-run = dolna granica.
"""
import sys, json, time
sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2.geocoding import _google_geocode, _in_service_bbox
from dispatch_v2.bootstrap_restaurants import build_query, haversine_m, MANUAL_COORDS_OVERRIDE

CACHE = "/root/.openclaw/workspace/dispatch_state/restaurant_coords.json"
OUT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-06/geo02_drift_report.json"


def main():
    d = json.load(open(CACHE))
    targets = {k: v for k, v in d.items()
               if str(k).isdigit() and isinstance(v, dict) and "cached_at" not in v}
    print(f"68-target check: {len(targets)} wpisów numerycznych bez cached_at")

    rows = []
    for i, (aid, info) in enumerate(sorted(targets.items(), key=lambda x: int(x[0])), 1):
        stored = (info.get("lat"), info.get("lng"))
        q = build_query(info)
        full_q = f"{q}, Białystok, Polska"
        is_override = int(aid) in MANUAL_COORDS_OVERRIDE
        new = _google_geocode(full_q)
        time.sleep(0.05)  # łagodnie dla API
        row = {
            "address_id": int(aid), "company": info.get("company"),
            "street": info.get("street"), "query": q,
            "stored": stored, "new": new,
            "manual_override": is_override,
        }
        if new is None:
            row["drift_m"] = None
            row["flag"] = "GEOCODE_FAIL"
        else:
            in_bbox = _in_service_bbox(new[0], new[1])
            drift = haversine_m(stored, new) if (stored[0] and stored[1]) else None
            row["drift_m"] = round(drift, 1) if drift is not None else None
            row["new_in_bbox"] = in_bbox
            if not in_bbox:
                row["flag"] = "NEW_OUT_OF_BBOX"  # nowy geokod = poison; stored bezpieczniejszy
            elif is_override:
                row["flag"] = "MANUAL_OVERRIDE (drift oczekiwany)"
            elif drift is not None and drift >= 200:
                row["flag"] = "DRIFT>=200m"
            elif drift is not None and drift >= 50:
                row["flag"] = "drift 50-200m"
            else:
                row["flag"] = "ok (<50m)"
        rows.append(row)
        print(f"[{i}/{len(targets)}] {aid:>3} {(info.get('company') or '')[:26]:26s} "
              f"drift={str(row['drift_m']):>8} {row['flag']}")

    json.dump({"generated_ts": int(time.time()), "n": len(rows), "rows": rows},
              open(OUT, "w"), ensure_ascii=False, indent=2)

    # Podsumowanie
    print("\n" + "=" * 64)
    by = {}
    for r in rows:
        by.setdefault(r["flag"].split(" ")[0], []).append(r)
    for k in sorted(by):
        print(f"  {k}: {len(by[k])}")
    print("=" * 64)
    big = sorted([r for r in rows if (r.get("drift_m") or 0) >= 200 and not r["manual_override"]],
                 key=lambda r: -(r["drift_m"] or 0))
    if big:
        print(f"\n🔴 DRIFT >=200m (nie-override) — kandydaci do weryfikacji ({len(big)}):")
        for r in big:
            print(f"  [{r['address_id']}] {r['company']} — {r['drift_m']}m | "
                  f"stored={r['stored']} new={r['new']} | q={r['query']!r}")
    fails = [r for r in rows if r["flag"] == "GEOCODE_FAIL"]
    if fails:
        print(f"\n⚠️ GEOCODE_FAIL ({len(fails)}): " + ", ".join(f"{r['address_id']}/{r['company']}" for r in fails))
    oob = [r for r in rows if r["flag"] == "NEW_OUT_OF_BBOX"]
    if oob:
        print(f"\n⚠️ NEW_OUT_OF_BBOX ({len(oob)}) — nowy geokod poza bbox, stored zostaje bezpieczniejszy: "
              + ", ".join(f"{r['address_id']}/{r['company']}" for r in oob))
    print(f"\nRaport JSON → {OUT}")


if __name__ == "__main__":
    main()
