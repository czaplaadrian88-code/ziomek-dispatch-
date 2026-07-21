#!/usr/bin/env python3
"""GPS<->klik coverage measurement dla zszycia GPS->trening ETA (READ-ONLY).

Liczy pokrycie i rozjazd na DOKLADNIE tych rekordach, ktore trafiaja do trenera
kalibratora (eta_calib.db / eta_calib_features), bo to one dostana docelowy target
GPS. Zero zapisu poza coverage.json w tym katalogu.

Zrodla (RO):
  - dispatch_state/eta_calib.db           (trainer corpus; ts_pickup UTC, actual_deliver_min = klik-klik)
  - dispatch_state/gps_delivery_truth.jsonl (fizyczny przyjazd geofence vs klik)
  - scripts/logs/sla_log.jsonl            (mianownik: wszystkie dostawy)
"""
from __future__ import annotations
import json, os, sqlite3, statistics as st
from datetime import datetime, timezone

BASE = "/root/.openclaw/workspace"
DB = f"{BASE}/dispatch_state/eta_calib.db"
GPS = f"{BASE}/dispatch_state/gps_delivery_truth.jsonl"
SLA = f"{BASE}/scripts/logs/sla_log.jsonl"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coverage.json")


def read_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 3) if xs else None


def main():
    # --- trainer corpus (eta_calib.db) ---
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    trows = [dict(r) for r in con.execute(
        "SELECT order_id, day, ts_pickup, ts_deliver, actual_deliver_min, "
        "pickup_slip_koord_min FROM eta_calib_features")]
    con.close()
    trainer_by_oid = {str(r["order_id"]): r for r in trows}
    tdays = sorted({r["day"] for r in trows if r["day"]})

    # --- GPS truth ---
    gps = list(read_jsonl(GPS))
    gps_by_oid = {}
    for g in gps:
        gps_by_oid[str(g.get("order_id"))] = g  # last wins (chronological append)
    gps_days = sorted({g.get("delivered_day") for g in gps if g.get("delivered_day")})
    gps_high = {oid: g for oid, g in gps_by_oid.items() if g.get("confidence") == "high"}

    # --- sla_log (denominator) ---
    sla = list(read_jsonl(SLA))
    sla_oids = {str(r.get("order_id")) for r in sla if r.get("order_id")}

    # --- join: trainer ∩ GPS (delivery leg) ---
    join_any, join_high = [], []
    dur_shift = []       # actual_deliver_min(klik) - gps_corrected_deliver_min
    click_vs_gps = []    # delta_button_minus_physical (delivery endpoint), high-conf
    for oid, tr in trainer_by_oid.items():
        g = gps_by_oid.get(oid)
        if not g:
            continue
        join_any.append(oid)
        phys = parse_iso(g.get("physical_delivered_at"))
        pu = parse_iso(tr.get("ts_pickup"))
        if phys and pu:
            gps_dur = (phys - pu).total_seconds() / 60.0
            if tr.get("actual_deliver_min") is not None and 0 <= gps_dur <= 120:
                dur_shift.append(round(tr["actual_deliver_min"] - gps_dur, 3))
        if g.get("confidence") == "high":
            join_high.append(oid)
            d = g.get("delta_button_minus_physical_min")
            if d is not None:
                click_vs_gps.append(d)

    # --- freshness / gaps ---
    latest_gps = max((parse_iso(g.get("physical_delivered_at")) for g in gps
                      if parse_iso(g.get("physical_delivered_at"))), default=None)
    now = datetime.now(timezone.utc)

    # per-day GPS + trainer counts
    gps_day_counts = {}
    for g in gps:
        d = g.get("delivered_day")
        gps_day_counts[d] = gps_day_counts.get(d, 0) + 1
    trainer_day_counts = {}
    for r in trows:
        trainer_day_counts[r["day"]] = trainer_day_counts.get(r["day"], 0) + 1
    # trainer days with ZERO gps join
    join_day = {}
    for oid in join_any:
        d = trainer_by_oid[oid]["day"]
        join_day[d] = join_day.get(d, 0) + 1
    zero_gps_trainer_days = [d for d in tdays if join_day.get(d, 0) == 0]

    # confidence breakdown of ALL gps
    conf_counts = {}
    for g in gps:
        c = g.get("confidence")
        conf_counts[c] = conf_counts.get(c, 0) + 1

    # per-courier GPS join among trainer rows (>=15 pairs => usable per-courier)
    cc = {}
    for oid in join_high:
        cid = str(gps_by_oid[oid].get("courier_id"))
        cc[cid] = cc.get(cid, 0) + 1
    couriers_ge15 = sorted([(c, n) for c, n in cc.items() if n >= 15], key=lambda x: -x[1])

    out = {
        "generated_at": now.isoformat(),
        "sources": {"trainer_db": DB, "gps_truth": GPS, "sla_log": SLA},
        "trainer_corpus": {
            "n_rows": len(trows),
            "day_range": [tdays[0], tdays[-1]] if tdays else None,
            "n_days": len(tdays),
        },
        "gps_truth": {
            "n_records": len(gps),
            "n_unique_orders": len(gps_by_oid),
            "day_range": [gps_days[0], gps_days[-1]] if gps_days else None,
            "n_days": len(gps_days),
            "confidence_breakdown": conf_counts,
            "n_high_conf_unique": len(gps_high),
            "latest_physical_delivered_at": latest_gps.isoformat() if latest_gps else None,
            "freshness_minutes_ago": round((now - latest_gps).total_seconds() / 60.0, 1) if latest_gps else None,
        },
        "coverage_vs_sla": {
            "n_sla_orders": len(sla_oids),
            "gps_any_pct": round(100.0 * len(sla_oids & set(gps_by_oid)) / len(sla_oids), 1) if sla_oids else None,
            "gps_high_pct": round(100.0 * len(sla_oids & set(gps_high)) / len(sla_oids), 1) if sla_oids else None,
        },
        "coverage_vs_trainer": {
            "note": "Ile wierszy trenera (delivery leg) moze dostac GPS-target JUZ TERAZ.",
            "n_trainer_rows": len(trows),
            "n_join_any_conf": len(join_any),
            "pct_join_any_conf": round(100.0 * len(join_any) / len(trows), 1) if trows else None,
            "n_join_high_conf": len(join_high),
            "pct_join_high_conf": round(100.0 * len(join_high) / len(trows), 1) if trows else None,
            "zero_gps_trainer_days": zero_gps_trainer_days,
            "n_zero_gps_trainer_days": len(zero_gps_trainer_days),
        },
        "click_vs_gps_delivery": {
            "note": "delta_button_minus_physical_min (klik pozniej niz fizyczny), high-conf, join z trenerem.",
            "n": len(click_vs_gps),
            "median_min": med(click_vs_gps),
            "mean_min": round(st.mean(click_vs_gps), 3) if click_vs_gps else None,
            "pct_click_late": round(100.0 * sum(1 for d in click_vs_gps if d > 0) / len(click_vs_gps), 1) if click_vs_gps else None,
            "pct_abs_ge5": round(100.0 * sum(1 for d in click_vs_gps if abs(d) >= 5) / len(click_vs_gps), 1) if click_vs_gps else None,
        },
        "delivery_target_shift": {
            "note": "actual_deliver_min(klik-klik) - gps_corrected(physical - klik_pickup). Dodatni = target skroci sie po GPS.",
            "n": len(dur_shift),
            "median_min": med(dur_shift),
            "mean_min": round(st.mean(dur_shift), 3) if dur_shift else None,
        },
        "per_courier_high_conf_join": {
            "n_couriers_ge15_pairs": len(couriers_ge15),
            "top": couriers_ge15[:12],
        },
        "pickup_leg": {
            "physical_pickup_truth_available": False,
            "note": "gps_delivery_truth = DELIVERY only. Pickup leg (pickup_slip_koord_min) NIE ma fizycznej prawdy dzis. Poza zakresem GPS-stitch tej fazy.",
        },
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
