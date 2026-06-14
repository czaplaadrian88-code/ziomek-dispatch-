#!/usr/bin/env python3
"""OFFLINE replay food-age OFF↔ON na obj_replay_capture.jsonl (BUG#5, Fala food-age).

Zastępuje wycofany inline shadow comparator (ten ~2× latencję bo robił drugi solve
OR-Tools per-kandydat w gorącej ścieżce). Tu: produkcja TYLKO zapisuje wejścia
solvera (obj_replay_capture, ENABLE_OBJ_REPLAY_CAPTURE=1 w unicie dispatch-shadow,
zero solve → zero latencji), a re-sim OFF↔ON liczymy POZA ścieżką — jak obj_harness
/ eta_calib replay. Każdy rekord = wierne wejście simulate_bag_route_v2 (coords,
pickup_ready_at, picked_up_at, status, czas_kuriera, dwell, tier).

Dla każdego ortools-eligible (bag≥1 → bag_after_add≥2) re-sim OFF (R6) i ON
(food_age_override), porównanie pełnej kolejności PRZYSTANKÓW (interleaved
pickup+drop — NIE plan.sequence, bo to kolejność DOSTAW, w BUG#5 identyczna),
delta thermal/idle/span + KRYTYCZNIE regresja SLA (on_sla>off_sla = blocker flipa).

at#141 (21.06) odpala to z defaultami na oknie ostatnich N dni → bardzo dobre
wnioski na tysiącach realnych decyzji, zero wpływu na prod.
Użycie: foodage_offline_replay_review.py [--window-days 7] [--max 2000] [--coeff 6.0] [--quiet]
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
REPORT = SCRIPTS + "/logs/foodage_offline_replay_review.txt"
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


def _p(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=float, default=7.0)
    ap.add_argument("--max", type=int, default=2000)
    ap.add_argument("--coeff", type=float, default=None, help="override OBJ_DELIVERY_FOOD_AGE_COEFF")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    if a.coeff is not None:
        C.OBJ_DELIVERY_FOOD_AGE_COEFF = a.coeff

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
    if len(recs) > a.max:                      # deterministyczny stride-sample
        stride = len(recs) / a.max
        recs = [recs[int(i * stride)] for i in range(a.max)]

    results, skipped = [], 0
    for d in recs:
        try:
            cp = d.get("courier_pos")
            now = _dt(d.get("now"))
            if not cp or len(cp) != 2 or now is None:
                skipped += 1
                continue
            bag = [_mk(o) for o in d.get("bag") or []]
            no = _mk(d.get("new_order") or {})
            if no is None or any(b is None for b in bag):
                skipped += 1
                continue
            dp, dd = d.get("dwell_pickup"), d.get("dwell_dropoff")
            kw = dict(now=now, sla_minutes=SLA)
            if dp is not None:
                kw["dwell_pickup"] = dp
            if dd is not None:
                kw["dwell_dropoff"] = dd
            p_off = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            if p_off.strategy != "ortools":     # food-age dotyczy tylko ortools
                continue
            with C.food_age_override(True):
                p_on = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            m_off, m_on = compute_plan_metrics(p_off, dp or 1.0), compute_plan_metrics(p_on, dp or 1.0)
            results.append({
                "changed": _stop_order(p_off) != _stop_order(p_on),
                "bag": len(bag),
                "th_off": m_off.get("max_thermal_age_min"), "th_on": m_on.get("max_thermal_age_min"),
                "idle_off": m_off.get("idle_total_min"), "idle_on": m_on.get("idle_total_min"),
                "sla_off": p_off.sla_violations or 0, "sla_on": p_on.sla_violations or 0,
            })
        except Exception:
            skipped += 1
            continue

    n = len(results)
    changed = [r for r in results if r["changed"]]
    sla_reg = [r for r in results if r["sla_on"] > r["sla_off"]]
    sla_improve = [r for r in results if r["sla_on"] < r["sla_off"]]
    th_d = [r["th_off"] - r["th_on"] for r in changed
            if isinstance(r["th_off"], (int, float)) and isinstance(r["th_on"], (int, float))]
    idle_d = [r["idle_off"] - r["idle_on"] for r in changed
              if isinstance(r["idle_off"], (int, float)) and isinstance(r["idle_on"], (int, float))]
    cr = (100.0 * len(changed) / n) if n else 0.0
    th_mean = round(st.mean(th_d), 2) if th_d else None
    by_bag = defaultdict(lambda: [0, 0])
    for r in results:
        by_bag[r["bag"]][0] += 1
        if r["changed"]:
            by_bag[r["bag"]][1] += 1

    coeff = a.coeff if a.coeff is not None else getattr(C, "OBJ_DELIVERY_FOOD_AGE_COEFF", 6.0)
    # Reguła NET-aware (additive R6+food-age): blokuj tylko gdy regresje dominują
    # lub rate wysoki — NIE na jakąkolwiek regresję (pojedyncze są normalne, liczy
    # się NETTO i magnituda ogona). Stary blanket-block był za sztywny.
    _reg_rate = (100.0 * len(sla_reg) / n) if n else 0.0
    _net_sla = len(sla_improve) - len(sla_reg)
    if n < 200:
        rec = f"⏳ ZA MAŁO ortools-decyzji (n={n}). Poszerz --window-days / --max."
    elif len(sla_reg) >= len(sla_improve) or _reg_rate > 2.0:
        rec = (f"🛑 NIE FLIPOWAĆ. Regresja SLA dominuje/za wysoka: {len(sla_reg)} regresji vs "
               f"{len(sla_improve)} poprawy ({_reg_rate:.1f}%). Zbadać przyczynę PRZED flipem.")
    elif cr < 5:
        rec = (f"🔧 NISKI ZASIĘG (changed={cr:.1f}%). Rozważ podkręcenie coeff (jest {coeff}) "
               f"i powtórz, albo uznać BUG#5 za rzadki.")
    elif th_mean is not None and th_mean > 0:
        rec = (f"✅ KANDYDAT DO FLIPA (do ACK Adriana). changed={cr:.1f}%, NETTO SLA {_net_sla:+d} "
               f"({len(sla_improve)} poprawy vs {len(sla_reg)} regresji = {_reg_rate:.1f}%), "
               f"thermal na zmienionych +{th_mean} min. ⚠ Zbadać {len(sla_reg)} regresji (ogon) "
               f"przed flipem. Po ACK: flip ENABLE_OBJ_DELIVERY_FOOD_AGE (hot) + obs prod 48h.")
    else:
        rec = (f"🟡 NIEJEDNOZNACZNE. changed={cr:.1f}%, netto SLA {_net_sla:+d}, poprawa thermal "
               f"nieoczywista (mean={th_mean}). Sweep coeff / ręczny przegląd zmienionych tras.")

    lines = [
        "=== OFFLINE REPLAY food-age OFF↔ON (BUG#5) ===",
        f"źródło: {CAPTURE} | okno: {a.window_days}d | coeff: {coeff} | sla: {SLA}",
        f"ortools-eligible w oknie: {total_elig} | re-sim: {len(recs)} | skipped: {skipped}",
        f"ORTOOLS-decyzji (po filtrze strategy): n={n}",
        f"  changed (zmiana kolejności przystanków): {len(changed)} ({cr:.1f}%)",
        f"  regresja SLA (on>off): {len(sla_reg)}  <-- 0=OK, >0=BLOCKER",
        f"  poprawa SLA (on<off):  {len(sla_improve)}",
        "",
        "Delta thermal na ZMIENIONYCH (off−on, +=świeższe):",
        f"  n={len(th_d)} mean={th_mean} median={_p(th_d,0.5)} p90={_p(th_d,0.9)} "
        f"min={round(min(th_d),1) if th_d else None} max={round(max(th_d),1) if th_d else None}",
        "Delta idle na ZMIENIONYCH (off−on, +=mniej postoju):",
        f"  n={len(idle_d)} median={_p(idle_d,0.5)} p90={_p(idle_d,0.9)} max={round(max(idle_d),1) if idle_d else None}",
        "",
        "changed-rate per bag_size:",
    ]
    for b in sorted(by_bag):
        tot, ch = by_bag[b]
        lines.append(f"  bag={b}: {ch}/{tot} ({100*ch/tot:.0f}%)")
    lines += ["", "REKOMENDACJA (do ACK Adriana — skrypt NIC nie flipuje):", f"  {rec}"]
    report = "\n".join(lines)

    with open(REPORT, "w") as f:
        f.write(report + "\n")
    if not a.quiet:
        print(report)
    if a.no_telegram:
        return
    try:
        from dispatch_v2 import telegram_utils
        telegram_utils.send_admin_alert(
            f"📊 *Offline replay food-age (BUG#5)*\n"
            f"ortools n={n}, changed={cr:.1f}%, regresja SLA={len(sla_reg)}, thermal+{th_mean}\n"
            f"{rec}\nPełny: {REPORT}")
    except Exception as e:
        if not a.quiet:
            print(f"[telegram skip] {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
