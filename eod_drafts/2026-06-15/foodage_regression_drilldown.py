#!/usr/bin/env python3
"""DRILL-DOWN regresji SLA z food-age OFF↔ON replay (BUG#5, follow-up at#142).

Bazowy foodage_offline_replay_review.py tylko LICZY regresje (sla_on>sla_off).
Tu dla KAŻDEJ regresji rozkładamy ją na czynniki:
  - które zlecenie breachuje pod ON a NIE pod OFF (newly-breaching),
  - o ile minut ponad 35 (magnituda) i o ile PÓŹNIEJ niż pod OFF (delta vs off),
  - czy to NOWE zlecenie czy z worka,
  - czy pod OFF było już near-miss (≥33 min) czy ON tworzy breach z niczego,
  - jaki thermal-zysk (max_thermal off−on) dostaliśmy w zamian.
Agregaty: rozkład magnitud, split marginalne(≤2)/umiarkowane(2-5)/poważne(>5),
new-vs-bag, near-miss-vs-genesis, thermal-trade, histogram po bag_size.

Replika logiki bazowej (ten sam filtr okna/bag, ten sam stride-sample, ten sam
sla=35, te same _mk/_dt/_stop_order). NIC nie flipuje, NIE wysyła Telegrama.
Użycie: foodage_regression_drilldown.py [--window-days 7] [--max 6000] [--coeff C] [--dump 40]
"""
import argparse
import json
import sys
import statistics as st
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim  # noqa: E402
from dispatch_v2.route_metrics import compute_plan_metrics  # noqa: E402

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
REPORT = SCRIPTS + "/logs/foodage_regression_drilldown.txt"
SLA = 35


def _dt(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return None


def _mk(d):
    pc, dc = d.get("pickup_coords"), d.get("delivery_coords")
    if not pc or not dc or len(pc) != 2 or len(dc) != 2:
        return None
    o = OrderSim(d.get("order_id"), tuple(pc), tuple(dc),
                 _dt(d.get("picked_up_at")), d.get("status") or "assigned",
                 pickup_ready_at=_dt(d.get("pickup_ready_at")))
    if d.get("czas_kuriera_warsaw"):
        o.czas_kuriera_warsaw = d["czas_kuriera_warsaw"]
    return o


def _stop_order(p):
    ev = [(t, "P", o) for o, t in (p.pickup_at or {}).items()]
    ev += [(t, "D", o) for o, t in (p.predicted_delivered_at or {}).items()]
    ev.sort(key=lambda e: e[0])
    return [f"{k}:{o}" for _, k, o in ev]


def _per_order_elapsed(plan, bag, new_order):
    """Replika _count_sla_violations per-order: {oid: (elapsed_min, breach_bool)}.
    Kotwica = pickup_at(TSP) | picked_up_at | now-fallback(brak → pomiń)."""
    out = {}
    da = plan.predicted_delivered_at or {}
    pa = plan.pickup_at or {}
    for o in list(bag) + [new_order]:
        pred = da.get(o.order_id)
        if pred is None:
            continue
        if o.order_id in pa:
            pu = pa[o.order_id]
        elif o.picked_up_at is not None:
            pu = o.picked_up_at
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
            pu = pu.astimezone(timezone.utc)
        else:
            continue  # now-fallback: brak twardej kotwicy, pomiń (jak w prod count i tak liczone od now — ale tu nie mamy stabilnego now per-oid; konserwatywnie pomiń)
        if pred.tzinfo is None:
            pred = pred.replace(tzinfo=timezone.utc)
        elapsed = (pred - pu).total_seconds() / 60.0
        out[o.order_id] = (round(elapsed, 1), elapsed > SLA)
    return out


def _p(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=float, default=7.0)
    ap.add_argument("--max", type=int, default=6000)
    ap.add_argument("--coeff", type=float, default=None)
    ap.add_argument("--dump", type=int, default=40, help="ile pełnych case'ów wypisać")
    a = ap.parse_args()
    if a.coeff is not None:
        C.OBJ_DELIVERY_FOOD_AGE_COEFF = a.coeff
    coeff = a.coeff if a.coeff is not None else getattr(C, "OBJ_DELIVERY_FOOD_AGE_COEFF", 6.0)

    cutoff = datetime.now(timezone.utc) - timedelta(days=a.window_days)
    recs = []
    with open(CAPTURE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = _dt(d.get("ts"))
            if ts is None or ts < cutoff:
                continue
            if len(d.get("bag") or []) < 1:
                continue
            recs.append(d)
    total_elig = len(recs)
    if len(recs) > a.max:
        stride = len(recs) / a.max
        recs = [recs[int(i * stride)] for i in range(a.max)]

    n = 0
    regs = []          # pełne rekordy regresji
    processed = 0
    for d in recs:
        processed += 1
        if processed % 2000 == 0:
            sys.stderr.write(f"[progress] processed={processed}/{len(recs)} ortools_n={n} regs={len(regs)}\n")
            sys.stderr.flush()
        try:
            cp = d.get("courier_pos")
            now = _dt(d.get("now"))
            if not cp or len(cp) != 2 or now is None:
                continue
            bag = [_mk(o) for o in d.get("bag") or []]
            no = _mk(d.get("new_order") or {})
            if no is None or any(b is None for b in bag):
                continue
            dp, dd = d.get("dwell_pickup"), d.get("dwell_dropoff")
            kw = dict(now=now, sla_minutes=SLA)
            if dp is not None:
                kw["dwell_pickup"] = dp
            if dd is not None:
                kw["dwell_dropoff"] = dd
            p_off = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            if p_off.strategy != "ortools":
                continue
            with C.food_age_override(True):
                p_on = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            n += 1
            sla_off = p_off.sla_violations or 0
            sla_on = p_on.sla_violations or 0
            if sla_on <= sla_off:
                continue
            # --- regresja: rozłóż ---
            eoff = _per_order_elapsed(p_off, bag, no)
            eon = _per_order_elapsed(p_on, bag, no)
            m_off = compute_plan_metrics(p_off, dp or 1.0)
            m_on = compute_plan_metrics(p_on, dp or 1.0)
            new_oid = no.order_id
            newly = []   # zlecenia breachujące pod ON a nie pod OFF
            for oid, (el_on, br_on) in eon.items():
                el_off, br_off = eoff.get(oid, (None, False))
                if br_on and not br_off:
                    newly.append({
                        "oid": oid,
                        "is_new": oid == new_oid,
                        "el_on": el_on,
                        "el_off": el_off,
                        "over35": round(el_on - SLA, 1),
                        "extra_vs_off": (round(el_on - el_off, 1) if el_off is not None else None),
                        "off_nearmiss": (el_off is not None and el_off >= 33.0),
                    })
            th_off = m_off.get("max_thermal_age_min")
            th_on = m_on.get("max_thermal_age_min")
            regs.append({
                "ts": d.get("ts"),
                "bag": len(bag),
                "new_oid": new_oid,
                "sla_off": sla_off, "sla_on": sla_on,
                "newly": newly,
                "th_off": th_off, "th_on": th_on,
                "th_gain": (round(th_off - th_on, 1) if isinstance(th_off, (int, float)) and isinstance(th_on, (int, float)) else None),
                "stop_off": _stop_order(p_off),
                "stop_on": _stop_order(p_on),
            })
        except Exception:
            continue

    # ---------- AGREGATY ----------
    R = len(regs)
    # najgorszy newly-breach per case
    worst = []
    extra = []
    new_breach_cases = 0
    bag_breach_cases = 0
    nearmiss_cases = 0
    genesis_cases = 0
    th_gains = []
    by_bag = Counter()
    marg = mod = sev = 0
    for r in regs:
        by_bag[r["bag"]] += 1
        if r["th_gain"] is not None:
            th_gains.append(r["th_gain"])
        if not r["newly"]:
            continue
        w = max(x["over35"] for x in r["newly"])
        worst.append(w)
        if w <= 2:
            marg += 1
        elif w <= 5:
            mod += 1
        else:
            sev += 1
        for x in r["newly"]:
            if x["extra_vs_off"] is not None:
                extra.append(x["extra_vs_off"])
            if x["is_new"]:
                new_breach_cases += 1
            else:
                bag_breach_cases += 1
            if x["off_nearmiss"]:
                nearmiss_cases += 1
            else:
                genesis_cases += 1

    L = []
    L.append("=== DRILL-DOWN regresji SLA food-age OFF↔ON (BUG#5) ===")
    L.append(f"źródło: {CAPTURE} | okno: {a.window_days}d | coeff: {coeff} | sla: {SLA}")
    L.append(f"ortools-eligible w oknie: {total_elig} | re-sim: {len(recs)} | ortools-decyzji n={n}")
    L.append(f"REGRESJE SLA (sla_on>sla_off): {R}  ({100.0*R/n:.2f}% ortools-decyzji)" if n else "n=0")
    L.append("")
    L.append("── Magnituda najgorszego NOWEGO breacha per case (min ponad 35) ──")
    if worst:
        L.append(f"  n={len(worst)} min={min(worst)} median={_p(worst,0.5)} "
                 f"p90={_p(worst,0.9)} max={max(worst)} mean={round(st.mean(worst),1)}")
        L.append(f"  split: marginalne ≤2min={marg} | umiarkowane 2-5min={mod} | POWAŻNE >5min={sev}")
    else:
        L.append("  (brak newly-breach — regresje to zmiana liczności na innych kotwicach)")
    L.append("")
    L.append("── O ile PÓŹNIEJ niż OFF dostarczane breachujące zlecenie ──")
    if extra:
        L.append(f"  n={len(extra)} median=+{_p(extra,0.5)} p90=+{_p(extra,0.9)} max=+{max(extra)} min={min(extra)}")
    L.append("")
    L.append("── Kto breachuje / charakter ──")
    L.append(f"  NOWE zlecenie: {new_breach_cases} | z WORKA: {bag_breach_cases}")
    L.append(f"  near-miss pod OFF (≥33min, ON dopycha): {nearmiss_cases} | genesis (OFF spokojnie <33→ON breach): {genesis_cases}")
    L.append("")
    L.append("── Thermal-trade na regresjach (max_thermal off−on, +=ON świeższe) ──")
    if th_gains:
        pos = [x for x in th_gains if x > 0]
        L.append(f"  n={len(th_gains)} median={_p(th_gains,0.5)} p90={_p(th_gains,0.9)} "
                 f"max={max(th_gains)} min={min(th_gains)} | zysk>0 w {len(pos)}/{len(th_gains)} przypadkach")
    L.append("")
    L.append("── Histogram regresji po bag_size ──")
    for b in sorted(by_bag):
        L.append(f"  bag={b}: {by_bag[b]}")
    L.append("")
    L.append(f"── PIERWSZE {min(a.dump, R)} case'ów (pełny rozkład) ──")
    for r in regs[:a.dump]:
        nb = "; ".join(
            f"{'NEW' if x['is_new'] else 'bag'} {x['oid']}: {x['el_off']}→{x['el_on']}min "
            f"(+{x['over35']} ponad35, {'+' if (x['extra_vs_off'] or 0)>=0 else ''}{x['extra_vs_off']} vs off"
            f"{', near-miss' if x['off_nearmiss'] else ''})"
            for x in r["newly"]
        ) or "(brak newly — przesunięcie kotwic)"
        L.append(f"  [{r['ts'][:19]}] bag={r['bag']} sla {r['sla_off']}→{r['sla_on']} "
                 f"thermal {r['th_off']}→{r['th_on']} (zysk {r['th_gain']})")
        L.append(f"      breach: {nb}")

    report = "\n".join(L)
    with open(REPORT, "w") as f:
        f.write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
