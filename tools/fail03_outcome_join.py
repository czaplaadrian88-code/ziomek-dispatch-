#!/usr/bin/env python3
"""FAIL-03-K1 OUTCOME JOIN (tor #2 nauki) — read-only, schedulowalny.

K1 loguje przy decyzji 'would-redirect near-term KOORD-cisza', ale realny outcome
(kto realnie dostał + czy dowiózł ≤35min) jest znany DOPIERO po dostarczeniu. Ten
joiner łączy K1-flagged zlecenia z shadow_decisions.jsonl z realnym przebiegiem z
events.db (assigned/picked_up/delivered) + koordami z NEW_ORDER + (gdy jest) pozycją
GPS floty — i dopisuje do fail03_outcomes.jsonl (append, dedup po oid).

Daje sygnał treningowy 'lepszy vs inny' dla modelu selekcji best_effort / M3:
- realny defer odbioru człowieka, realny leg dowozu, czy breach R6
- est_breach Ziomka vs realny leg (kalibracja pesymizmu)
- pozycje GPS floty w momencie T (going-forward, gdy apka się upowszechni)

ZERO mutacji verdiktu, ZERO dotykania hot-path. Uruchamiać po peaku (>2h po decyzji,
żeby dowozy były zamknięte). Idempotentny: pomija oid już z 'delivered' outcome.

Użycie: python3 -m dispatch_v2.tools.fail03_outcome_join [--since YYYY-MM-DD] [--min-age-min 120]
"""
import json, glob, sqlite3, bisect, argparse, os, tempfile
from datetime import datetime
from math import radians, sin, cos, asin, sqrt

BASE = "/root/.openclaw/workspace"
SHADOW_GLOB = f"{BASE}/scripts/logs/shadow_decisions.jsonl*"
EV_DB = f"{BASE}/dispatch_state/events.db"
GPS_DB = f"{BASE}/dispatch_state/courier_api.db"
OUT = f"{BASE}/scripts/logs/fail03_outcomes.jsonl"
R6_MAX = 35.0
GPS_TOL_S = 1200


def hav(a, b):
    if not a or not b or a[0] is None or b[0] is None:
        return None
    la1, lo1, la2, lo2 = map(radians, [a[0], a[1], b[0], b[1]])
    d = 2*asin(sqrt(sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2))
    return round(6371*d, 2)


def ep(iso):
    try: return datetime.fromisoformat(iso).timestamp()
    except Exception: return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-04")
    ap.add_argument("--min-age-min", type=float, default=120.0, help="pomiń decyzje młodsze niż X min (dowóz niezamknięty)")
    args = ap.parse_args()

    now = datetime.now().timestamp()
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
                if r.get("realized") in ("ok", "breach"):
                    done.add(str(r.get("oid")))
            except Exception: pass

    # K1-flagged silent orders (dedup)
    silent = {}
    for f in sorted(glob.glob(SHADOW_GLOB)):
        if f.endswith(".gz"): continue
        for line in open(f, encoding="utf-8", errors="replace"):
            try: d = json.loads(line)
            except Exception: continue
            apf = d.get("always_propose_would_redirect_shadow")
            if not apf: continue
            ts = d.get("ts");
            if not ts or ts[:10] < args.since: continue
            b = d.get("best") or {}
            silent[str(d.get("order_id"))] = {
                "oid": str(d.get("order_id")), "ts": ts, "path": apf.get("path"),
                "mtp": apf.get("minutes_to_pickup"),
                "z_best_cid": str(b.get("courier_id")), "z_best_breach": b.get("max_bag_time_min"),
                "z_best_score": round(b.get("score") or 0, 1),
            }

    con = sqlite3.connect(EV_DB); cur = con.cursor()
    # gps preload (recent)
    gps = {}
    try:
        g = sqlite3.connect(GPS_DB).cursor()
        for cid, lat, lon, rec in g.execute("SELECT courier_id,lat,lon,recorded_at FROM gps_history WHERE recorded_at>?", (int(now-7*86400),)):
            gps.setdefault(str(cid), []).append((rec, lat, lon))
        for c in gps: gps[c].sort()
    except Exception: pass

    def pos_at(cid, T):
        arr = gps.get(cid)
        if not arr: return None
        i = bisect.bisect_right([a[0] for a in arr], T)-1
        if i < 0 or T-arr[i][0] > GPS_TOL_S: return None
        return (arr[i][1], arr[i][2])

    written = 0; rows = []
    for oid, s in silent.items():
        T = ep(s["ts"])
        if T is None or (now-T)/60 < args.min_age_min: continue
        if oid in done: continue
        # coords + ready
        pc = pr = None
        nr = cur.execute("SELECT payload FROM events WHERE order_id=? AND event_type='NEW_ORDER' LIMIT 1", (oid,)).fetchone()
        if nr:
            try:
                p = json.loads(nr[0]) or {}
                pc = p.get("pickup_coords"); pr = ep(p.get("pickup_at_warsaw")) if p.get("pickup_at_warsaw") else None
            except Exception: pass
        # outcome
        evs = cur.execute("SELECT event_type,courier_id,created_at FROM audit_log WHERE order_id=? AND event_type IN ('COURIER_ASSIGNED','COURIER_PICKED_UP','COURIER_DELIVERED') ORDER BY created_at", (oid,)).fetchall()
        hcid = pu = dl = None
        for et, cid, ca in evs:
            e = ep(ca)
            if et == "COURIER_ASSIGNED" and e and e >= T-120 and hcid is None: hcid = str(cid)
            if et == "COURIER_PICKED_UP" and pu is None and e: pu = e
            if et == "COURIER_DELIVERED" and e: dl = e
        if hcid is None and evs: hcid = str(evs[0][1])
        defer = round((pu-pr)/60, 1) if (pu and pr) else None
        leg = round((dl-pu)/60, 1) if (pu and dl) else None
        realized = "breach" if (leg and leg > R6_MAX) else ("ok" if leg else "pending")
        hp = pos_at(hcid, T) if hcid else None
        rec = {**s, "human_cid": hcid, "human_defer_min": defer, "human_leg_min": leg,
               "realized": realized, "human_km": hav(hp, pc), "joined_at": datetime.now().isoformat()}
        rows.append(rec)
        if realized != "pending": written += 1

    # append (dedup: pomiń pending duplikaty już zamknięte; nadpisz pending nowym)
    existing = {}
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line); existing[str(r.get("oid"))] = r
            except Exception: pass
    for r in rows:
        existing[r["oid"]] = r
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in existing.values():
            fh.write(json.dumps(r, ensure_ascii=False, default=str)+"\n")
    os.replace(tmp, OUT)

    closed = [r for r in existing.values() if r.get("realized") in ("ok", "breach")]
    ok = sum(1 for r in closed if r["realized"] == "ok")
    print(f"FAIL-03 outcome-join: {len(silent)} K1 zleceń, dopisano/odświeżono {len(rows)} (nowo-zamkniętych {written}).")
    if closed:
        print(f"  zamknięte total: {len(closed)} | dowiezione ≤35min: {ok} ({100*ok/len(closed):.0f}%)")
    print(f"  -> {OUT}")


if __name__ == "__main__":
    main()
