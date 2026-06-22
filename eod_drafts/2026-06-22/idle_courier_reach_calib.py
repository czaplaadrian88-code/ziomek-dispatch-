#!/usr/bin/env python3
"""Kalibracja "15 min na spokojnie" dla bezczynnego kuriera bez GPS (2026-06-22).

Teza Adriana: kurier bezczynny (pusty worek) w zwartym Białymstoku dojedzie pod
KAŻDĄ restaurację w ~15 min — więc bez-GPS-idle trzeba pewnie proponować z płaską
15-tką, nie karać fikcją centrum.

Pomiar: dla decyzji no_gps+empty, które zostały PRZYJĘTE (proponowany kurier ==
realny wykonawca), realny czas od decyzji do ODBIORU (picked_up_at − shadow_ts).
Czy klastruje ~15 min? Kontrola: to samo dla kotwiczonych (z pozycją) decyzji.

Łączy shadow (pos_source, proponowany cid) z eta_calibration_log (oid, real
courier, shadow_ts, picked_up_at). TZ: picked_up_at Warsaw naive −2h=UTC.
Read-only, fail-soft.
"""
import json
import os
import statistics as st
from datetime import datetime, timezone, timedelta

SHADOW = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]
ETA_CAL = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
WARSAW_OFFSET_H = 2


def _utc_warsaw(s):
    try:
        return (datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
                - timedelta(hours=WARSAW_OFFSET_H)).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _utc_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _pos_source(c):
    if not isinstance(c, dict):
        return None
    ps = c.get("pos_source")
    if ps is None and isinstance(c.get("metrics"), dict):
        ps = c["metrics"].get("pos_source")
    return ps


def _empty(c):
    m = c.get("metrics") if isinstance(c.get("metrics"), dict) else c
    b = (m.get("r6_bag_size") or m.get("bag_size_before") or m.get("r7_bag_size") or 0)
    return (b or 0) == 0


def _km_to_pickup(c):
    v = c.get("km_to_pickup")
    if v is None and isinstance(c.get("metrics"), dict):
        v = c["metrics"].get("km_to_pickup")
    return v if isinstance(v, (int, float)) else None


def main():
    # shadow: oid -> (pos_source, proposed_cid, km_to_pickup) dla EMPTY decyzji
    shadow_oid = {}
    for p in SHADOW:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                best = d.get("best") or {}
                if not _empty(best):
                    continue
                oid = str(d.get("order_id") or "")
                if not oid:
                    continue
                shadow_oid[oid] = (_pos_source(best), str(best.get("courier_id")),
                                   _km_to_pickup(best))

    # eta_cal: realny czas do odbioru dla przyjętych
    nogps_reach = []      # min od decyzji do odbioru, no_gps idle, przyjęte
    nogps_km = []
    anchored_reach = []   # kontrola
    n_nogps_join = 0
    n_nogps_accepted = 0
    if os.path.exists(ETA_CAL):
        with open(ETA_CAL, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                oid = str(d.get("oid") or "")
                if oid not in shadow_oid:
                    continue
                ps, prop_cid, km = shadow_oid[oid]
                real_cid = str(d.get("real_courier_id") or "")
                dec = _utc_iso(d.get("shadow_ts"))
                pick = _utc_warsaw(d.get("picked_up_at"))
                if dec is None or pick is None:
                    continue
                reach = (pick - dec).total_seconds() / 60.0
                if reach < 0 or reach > 180:
                    continue  # odrzuć absurdy (zła para/akceptacja po godzinach)
                if ps == "no_gps":
                    n_nogps_join += 1
                    if real_cid and real_cid == prop_cid:
                        n_nogps_accepted += 1
                        nogps_reach.append(reach)
                        if km is not None:
                            nogps_km.append(km)
                elif ps and ps != "no_gps":
                    if real_cid and real_cid == prop_cid:
                        anchored_reach.append(reach)

    def stats(xs):
        if not xs:
            return "brak danych"
        s = sorted(xs)
        return (f"n={len(s)} median={st.median(s):.1f} "
                f"p25={s[int(0.25*len(s))]:.1f} p75={s[int(0.75*len(s))]:.1f} "
                f"p90={s[min(len(s)-1,int(0.90*len(s)))]:.1f} max={s[-1]:.1f}")

    print("=== KALIBRACJA: realny czas decyzja→ODBIÓR dla PRZYJĘTYCH propozycji ===")
    print(f"no_gps+empty: zjoinowano {n_nogps_join}, przyjętych (prop==real) {n_nogps_accepted}")
    print()
    print(f"BEZCZYNNY no_gps (przyjęte) — realny dojazd do odbioru [min]:")
    print(f"  {stats(nogps_reach)}")
    if nogps_reach:
        u15 = sum(1 for x in nogps_reach if x <= 15)
        u20 = sum(1 for x in nogps_reach if x <= 20)
        u25 = sum(1 for x in nogps_reach if x <= 25)
        n = len(nogps_reach)
        print(f"  ≤15 min: {u15} ({100.0*u15/n:.0f}%)  ≤20: {u20} ({100.0*u20/n:.0f}%)  "
              f"≤25: {u25} ({100.0*u25/n:.0f}%)")
        print(f"  (uwaga: czas zawiera lag akceptacji operatora + jazdę — to GÓRNA granica jazdy)")
    if nogps_km:
        print(f"  km_to_pickup z fikcji centrum [km]: {stats(nogps_km)}")
    print()
    print(f"KONTROLA — kotwiczone (z pozycją), przyjęte — dojazd do odbioru [min]:")
    print(f"  {stats(anchored_reach)}")


if __name__ == "__main__":
    main()
