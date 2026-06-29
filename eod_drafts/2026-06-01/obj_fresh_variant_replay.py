#!/usr/bin/env python3
"""OBJ FRESH variant replay — offline, read-only.

Re-solwuje DZISIEJSZE realne propozycje (winner decisions, bag>=1, ortools)
pod różnymi konfiguracjami objective OR-Tools i mierzy trade-off:
  - świeżość ODBIORU nowego zlecenia (cel OBJ FRESH) — niżej lepiej, zwł. ogon >10min
  - max_thermal_age = jak długo jedzenie jedzie (wożenie) — niżej lepiej
  - front-load = nowy odbiór PRZED >=1 dostawą już odebranego jedzenia (zygzak) — niżej
  - route_span / R6 breaches — efektywność / bezpieczeństwo

Zero wpływu na prod: osobny proces, monkeypatch flag common.* w pamięci, tylko
READ z capture jsonl. Match winner: (new_oid, frozenset(bag oids)) z shadow log.
"""
import sys, json, statistics
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import common as C
from dispatch_v2.route_simulator_v2 import OrderSim, simulate_bag_route_v2
from dispatch_v2.route_metrics import compute_plan_metrics

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"

# Wszystkie warianty dziedziczą prod span-cost (apples-to-apples)
C.ENABLE_OBJ_SPAN_COST = True
C.OBJ_SPAN_COST_COEFF = 1.0

VARIANTS = {
    "V0_prod_c20":   dict(fresh=True,  coeff=20, r6=False, r6c=100),  # CURRENT PROD
    "V1_off":        dict(fresh=False, coeff=0,  r6=False, r6c=100),  # rollback
    "V3_c5":         dict(fresh=True,  coeff=5,  r6=False, r6c=100),  # gentle freshness
    "V4_sym_c20":    dict(fresh=True,  coeff=20, r6=True,  r6c=100),  # symmetric (fresh + R6 deliv counterweight)
    "V6_r6only":     dict(fresh=False, coeff=0,  r6=True,  r6c=100),  # tylko R6 deliv counterweight, fresh OFF
}


def apply(v):
    C.ENABLE_OBJ_PICKUP_FRESHNESS = v["fresh"]
    C.OBJ_PICKUP_FRESHNESS_PENALTY_COEFF = float(v["coeff"])
    C.ENABLE_OBJ_R6_SOFT_DEADLINE = v["r6"]
    C.OBJ_R6_DEADLINE_PENALTY_COEFF = float(v["r6c"])


def _dt(iso):
    if not iso:
        return None
    d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _sim(d):
    s = OrderSim(
        order_id=d.get("order_id"),
        pickup_coords=tuple(d.get("pickup_coords") or (0.0, 0.0)),
        delivery_coords=tuple(d.get("delivery_coords") or (0.0, 0.0)),
        picked_up_at=_dt(d.get("picked_up_at")),
        status=d.get("status") or "assigned",
        pickup_ready_at=_dt(d.get("pickup_ready_at")),
    )
    s.czas_kuriera_warsaw = d.get("czas_kuriera_warsaw")
    return s


def load_winners(limit=0):
    """Winner decisions dziś, bag>=1, ortools, new ma pickup."""
    out = []
    with open(SHADOW) as f:
        for line in f:
            if '"2026-06-01' not in line[:40]:
                continue
            d = json.loads(line)
            b = d.get("best") or {}
            pl = b.get("plan") or {}
            if (b.get("bag_size_before") or 0) < 1:
                continue
            if pl.get("strategy") != "ortools":
                continue
            new = str(d.get("order_id"))
            if new not in (pl.get("pickup_at") or {}):
                continue
            out.append({
                "oid": new, "ts": d["ts"], "verdict": d["verdict"],
                "restaurant": d.get("restaurant"), "addr": d.get("delivery_address"),
                "seq": [str(x) for x in pl.get("sequence") or []],
            })
    return out[-limit:] if limit > 0 else out


def index_capture():
    """(new_oid, frozenset(bag_oids)) -> list of (ts, rec)."""
    idx = {}
    with open(CAPTURE) as f:
        for line in f:
            if '"2026-06-01' not in line[:60]:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            no = r.get("new_order") or {}
            new = str(no.get("order_id"))
            bag = frozenset(str((o or {}).get("order_id")) for o in (r.get("bag") or []))
            idx.setdefault((new, bag), []).append((r.get("ts"), r))
    return idx


def replay_record(rec, dwell_p, dwell_d, now, new_oid, bag_oids):
    bag = [_sim(o) for o in rec.get("bag", [])]
    new = _sim(rec["new_order"])
    cpos = tuple(rec.get("courier_pos") or ())
    ready_new = new.pickup_ready_at
    res = {}
    for name, v in VARIANTS.items():
        apply(v)
        try:
            plan = simulate_bag_route_v2(cpos, bag, new, now=now,
                                         dwell_pickup=dwell_p, dwell_dropoff=dwell_d)
        except Exception as e:
            res[name] = {"error": f"{type(e).__name__}:{e}"}
            continue
        m = compute_plan_metrics(plan, dwell_p or 2.0)
        pickup_at = getattr(plan, "pickup_at", None) or {}
        deliv = getattr(plan, "predicted_delivered_at", None) or {}
        pod = getattr(plan, "per_order_delivery_times", None) or {}
        new_thermal = pod.get(new_oid)
        exist_thermals = [float(v) for k, v in pod.items() if str(k) != new_oid and v is not None]
        exist_thermal_max = max(exist_thermals) if exist_thermals else None
        pu_new = _dt(pickup_at.get(new_oid))
        stale = None
        if pu_new and ready_new:
            stale = (pu_new - ready_new).total_seconds() / 60.0
        # front-load: nowy odbiór przed >=1 dostawą istniejącego zlecenia
        front = False
        if pu_new:
            for k, vv in deliv.items():
                if str(k) == new_oid:
                    continue
                dvt = _dt(vv)
                if dvt and dvt > pu_new:
                    front = True
                    break
        # NAJGORSZY wzorzec: istniejące (już odebrane) jedzenie dostarczone PO
        # dostawie świeżo-odebranego nowego zlecenia (case Mickiewicza/Kręta).
        new_dv = _dt(deliv.get(new_oid))
        exist_after_new = False
        if new_dv:
            for k, vv in deliv.items():
                if str(k) == new_oid:
                    continue
                dvt = _dt(vv)
                if dvt and dvt > new_dv:
                    exist_after_new = True
                    break
        # czy nowy odbiór jest ostatnim odbiorem
        new_last = True
        if pu_new:
            for k, vv in pickup_at.items():
                if str(k) == new_oid:
                    continue
                ot = _dt(vv)
                if ot and ot > pu_new:
                    new_last = False
                    break
        res[name] = {
            "seq": [str(x) for x in (plan.sequence or [])],
            "strategy": plan.strategy,
            "stale_min": None if stale is None else round(stale, 2),
            "front_load": front,
            "exist_after_new": exist_after_new,
            "new_last_pickup": new_last,
            "new_thermal": None if new_thermal is None else round(float(new_thermal), 2),
            "exist_thermal_max": None if exist_thermal_max is None else round(exist_thermal_max, 2),
            "thermal": m["max_thermal_age_min"],
            "r6_breach_max": m["r6_breach_max_min"],
            "r6_breach_n": m["r6_breach_count"],
            "span": m["route_span_min"],
            "idle": m["idle_total_min"],
            "fallback": bool(getattr(plan, "osrm_fallback_used", False)),
        }
    return res


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/obj_fresh_variant_report.json"
    winners = load_winners(limit)
    idx = index_capture()
    print(f"winners={len(winners)} capture_keys={len(idx)}", flush=True)

    rows = []
    matched = 0
    v0_seq_match = 0
    for i, w in enumerate(winners):
        new_oid = w["oid"]
        bag_set = frozenset(x for x in w["seq"] if x != new_oid)
        cands = idx.get((new_oid, bag_set))
        if not cands:
            continue
        # najbliższy czasowo do decyzji
        dec_ts = _dt(w["ts"])
        cands_sorted = sorted(cands, key=lambda tr: abs((_dt(tr[0]) - dec_ts).total_seconds()) if _dt(tr[0]) else 1e9)
        rec = cands_sorted[0][1]
        matched += 1
        now = _dt(rec.get("now"))
        res = replay_record(rec, rec.get("dwell_pickup"), rec.get("dwell_dropoff"),
                            now, new_oid, bag_set)
        # sanity: V0 reprodukuje logowaną sekwencję?
        if res.get("V0_prod_c20", {}).get("seq") == w["seq"]:
            v0_seq_match += 1
        rows.append({"oid": new_oid, "ts": w["ts"], "verdict": w["verdict"],
                     "restaurant": w["restaurant"], "logged_seq": w["seq"], "res": res})
        if (i + 1) % 100 == 0:
            print(f"  ...{i+1}/{len(winners)} matched={matched}", flush=True)

    with open(out_path, "w") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "n_winners": len(winners), "n_matched": matched,
                   "v0_seq_match": v0_seq_match, "rows": rows}, f, ensure_ascii=False)
    print(f"matched={matched} v0_seq_match={v0_seq_match} "
          f"({100*v0_seq_match/max(matched,1):.0f}%) → {out_path}", flush=True)

    # ── agregaty per wariant ──
    def pct(xs, p):
        xs = sorted(x for x in xs if x is not None)
        if not xs:
            return None
        k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
        return round(xs[k], 1)

    print("\n  ODBIÓR nowego (świeżość — cel OBJ FRESH, niżej=lepiej) | WOŻENIE istniejącego jedzenia (carry, niżej=lepiej) | bezpieczeństwo")
    print("=" * 126)
    print(f"{'variant':13s} {'n':>4s} | {'stale_md':>8s} {'stale_p90':>9s} {'odb>10m%':>8s} | "
          f"{'front%':>6s} {'existAfterNew%':>14s} {'carry>35m%':>10s} {'span_md':>7s} | "
          f"{'R6brk_n':>7s} {'seqΔ0%':>6s}")
    print("-" * 126)
    base_seq = {r["oid"] + r["ts"]: r["res"].get("V0_prod_c20", {}).get("seq") for r in rows}
    summ = {}
    for name in VARIANTS:
        stale = []; front = 0; carryx = []; span = []; r6n = 0; n = 0; seqchg = 0; tail10 = 0; carrybad = 0; eafter = 0
        for r in rows:
            x = r["res"].get(name)
            if not x or "error" in x:
                continue
            n += 1
            stale.append(x["stale_min"])
            if x["stale_min"] is not None and x["stale_min"] > 10:
                tail10 += 1
            front += 1 if x["front_load"] else 0
            eafter += 1 if x.get("exist_after_new") else 0
            cx = x.get("exist_thermal_max")
            if cx is not None:
                carryx.append(cx)
                if cx > 35:
                    carrybad += 1
            span.append(x["span"])
            r6n += x["r6_breach_n"]
            if x["seq"] != base_seq.get(r["oid"] + r["ts"]):
                seqchg += 1
        if n == 0:
            continue
        nx = len(carryx) or 1
        summ[name] = dict(n=n, stale_md=pct(stale, 50), stale_p90=pct(stale, 90),
                          tail10=100*tail10/n, front=100*front/n, exist_after_new=100*eafter/n,
                          carry_p90=pct(carryx, 90), carrybad=100*carrybad/nx,
                          span_md=pct(span, 50), r6n=r6n, seqchg=100*seqchg/n)
        s = summ[name]
        print(f"{name:13s} {n:>4d} | {s['stale_md']:>8} {s['stale_p90']:>9} {s['tail10']:>7.1f}% | "
              f"{s['front']:>5.1f}% {s['exist_after_new']:>13.1f}% {s['carrybad']:>9.1f}% {s['span_md']:>7} | "
              f"{r6n:>7d} {s['seqchg']:>5.1f}%")
    print("=" * 126)
    # zapis agregatów obok raportu surowego
    with open(out_path.replace(".json", "_summary.json"), "w") as f:
        json.dump(summ, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
