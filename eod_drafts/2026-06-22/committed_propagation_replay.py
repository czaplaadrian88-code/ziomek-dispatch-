#!/usr/bin/env python3
"""REPLAY: efekt propagacji czas_kuriera_warsaw na OrderSim w re-sekwencerze.

Cel (Adrian 2026-06-22, case Michał K. Goodboy+Sushi): zmierzyć PRZED flipem, czy
doklejenie `czas_kuriera_warsaw` (gated ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION)
realnie poprawia punktualność odbioru committed i jakim kosztem (przestawienia
worków / SLA dostaw / INFEASIBLE-degradacja).

Metoda: rekonstrukcja realnych worków z plan_recheck.log (BAG_PLAN_GENERATED,
21-22.06) + orders_state.json (coords, czas_kuriera) + GPS z courier_api.db jako
kotwica. Dla każdego worka uruchamiamy DOKŁADNIE ten sam sweep co
plan_recheck._gen_one_bag_plan, dwa razy:
  BASELINE — sims bez czas_kuriera_warsaw (stan produkcyjny dziś)
  FIXED    — sims z czas_kuriera_warsaw (po fixie)
i porównujemy sekwencję stopów + punktualność committed + SLA + czas trasy.

ZERO zapisu do produkcji. Read-only.
"""
import os, sys, json, glob, sqlite3
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

# Penalty/flags muszą być widoczne ZANIM zaimportujemy moduł symulatora (flagi env
# czytane na import). Replikujemy środowisko plan_recheck: OR-Tools ON, kara ON.
os.environ.setdefault("ENABLE_V326_OR_TOOLS_TSP", "1")
# decision_flag(ENABLE_OBJ_COMMITTED_PICKUP_PENALTY) czyta flags.json (=True live).

from dispatch_v2 import route_simulator_v2 as R
from dispatch_v2 import common as C

STATE = "/root/.openclaw/workspace/dispatch_state"
LOG = "/root/.openclaw/workspace/scripts/logs/plan_recheck.log"
WARSAW = timezone(timedelta(hours=2))  # 21-22.06 = CEST

orders_state = json.load(open(f"{STATE}/orders_state.json"))
db = sqlite3.connect(f"{STATE}/courier_api.db")


def parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def gps_at(cid, ts_utc):
    """GPS kuriera najbliżej ts (±15 min), z courier_api.db."""
    e = int(ts_utc.timestamp())
    rows = list(db.execute(
        "select lat,lon,recorded_at from gps_history where courier_id=? "
        "and recorded_at between ? and ? order by abs(recorded_at-?) limit 1",
        (str(cid), e - 900, e + 900, e)))
    if rows:
        return (float(rows[0][0]), float(rows[0][1]))
    return None


def coords_ok(c):
    return (isinstance(c, (list, tuple)) and len(c) == 2
            and c[0] not in (None, 0, 0.0) and c[1] not in (None, 0, 0.0))


def build_sims(oids, fixed):
    """Buduje sims jak _gen_one_bag_plan; wszystko 'assigned' (moment uformowania
    worka). fixed=True → doklej czas_kuriera_warsaw (jak fix)."""
    sims = {}
    for oid in oids:
        rec = orders_state.get(oid) or {}
        dc, pc = rec.get("delivery_coords"), rec.get("pickup_coords")
        if not coords_ok(dc) or not coords_ok(pc):
            return None
        s = R.OrderSim(
            order_id=oid,
            pickup_coords=(float(pc[0]), float(pc[1])),
            delivery_coords=(float(dc[0]), float(dc[1])),
            picked_up_at=None,
            status="assigned",
            pickup_ready_at=parse_dt(rec.get("czas_kuriera_warsaw")),
        )
        if fixed:
            s.czas_kuriera_warsaw = rec.get("czas_kuriera_warsaw")
        sims[oid] = s
    return sims


def sweep(pos, sims, now):
    """Mirror _gen_one_bag_plan sweep — wybór najlepszej designacji."""
    ordered = list(sims.keys())
    best = None
    for newoid in ordered:
        bag = [sims[o] for o in ordered if o != newoid]
        p = R.simulate_bag_route_v2(pos, bag, sims[newoid], now=now,
                                    sla_minutes=35, earliest_departure=None)
        key = (p.sla_violations, round(p.total_duration_min, 3), tuple(p.sequence))
        if best is None or key < best[0]:
            best = (key, p)
    return best[1]


def stop_order(plan):
    """Kolejność stopów (kind,oid) wg czasu — jak zapis w courier_plans."""
    ev = []
    for oid, t in plan.pickup_at.items():
        ev.append((t, "P", oid))
    for oid, t in plan.predicted_delivered_at.items():
        ev.append((t, "D", oid))
    ev.sort(key=lambda x: x[0])
    return [(k, o) for _, k, o in ev]


def committed_lateness(plan, oids):
    """Per committed-pickup: (pickup_at - czas_kuriera) w minutach."""
    out = {}
    for oid in oids:
        ck = parse_dt((orders_state.get(oid) or {}).get("czas_kuriera_warsaw"))
        pu = plan.pickup_at.get(oid)
        if ck is None or pu is None:
            continue
        out[oid] = (pu - ck.astimezone(timezone.utc)).total_seconds() / 60.0
    return out


# ---- zbierz korpus z logu (dedup po cid+frozenset(oids), pierwsze wystąpienie) ----
import re
seen = set()
corpus = []  # (ts_utc, cid, oids)
pat = re.compile(r"^(\S+ \S+) .*BAG_PLAN_GENERATED cid=(\d+) .*seq=\[([^\]]*)\]")
for line in open(LOG, encoding="utf-8", errors="ignore"):
    if "BAG_PLAN_GENERATED" not in line or "2026-06-2" not in line:
        continue
    m = pat.search(line)
    if not m:
        continue
    ts_s, cid, seq_s = m.groups()
    if not (ts_s.startswith("2026-06-21") or ts_s.startswith("2026-06-22")):
        continue
    oids = re.findall(r"'(\d+)'", seq_s)
    if len(oids) < 2:
        continue
    key = (cid, frozenset(oids))
    if key in seen:
        continue
    seen.add(key)
    ts_utc = datetime.strptime(ts_s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    corpus.append((ts_utc, cid, oids))

# ---- replay ----
results = []
skip = {"coords": 0, "no_gps": 0, "lt2_committed": 0, "sim_err": 0}
for ts_utc, cid, oids in corpus:
    # tylko worki gdzie >=2 odbiorów ma committed (punktualność w grze)
    n_ck = sum(1 for o in oids
               if (orders_state.get(o) or {}).get("czas_kuriera_warsaw"))
    if n_ck < 2:
        skip["lt2_committed"] += 1
        continue
    pos = gps_at(cid, ts_utc)
    if pos is None:
        skip["no_gps"] += 1
        continue
    sims_b = build_sims(oids, fixed=False)
    sims_f = build_sims(oids, fixed=True)
    if sims_b is None or sims_f is None:
        skip["coords"] += 1
        continue
    try:
        pb = sweep(pos, sims_b, ts_utc)
        pf = sweep(pos, sims_f, ts_utc)
    except Exception as e:
        skip["sim_err"] += 1
        continue
    sob, sof = stop_order(pb), stop_order(pf)
    lat_b = committed_lateness(pb, oids)
    lat_f = committed_lateness(pf, oids)
    # "świeży" worek = wszystkie committed w [ts-5, ts+45] → realne że jeszcze
    # nieodebrane (rekonstrukcja "all assigned" wierna; filtr na artefakt
    # sprasowanych worków wielogodzinnych).
    cks = [parse_dt((orders_state.get(o) or {}).get("czas_kuriera_warsaw")) for o in oids]
    cks = [c for c in cks if c]
    fresh = bool(cks) and all(
        -5 <= (c.astimezone(timezone.utc) - ts_utc).total_seconds() / 60.0 <= 45
        for c in cks)
    results.append({
        "cid": cid, "oids": oids, "n": len(oids), "fresh": fresh,
        "reordered": sob != sof,
        "seq_b": sob, "seq_f": sof,
        "lat_b": lat_b, "lat_f": lat_f,
        "sla_b": pb.sla_violations, "sla_f": pf.sla_violations,
        "dur_b": pb.total_duration_min, "dur_f": pf.total_duration_min,
        "strat_b": getattr(pb, "strategy", "?"), "strat_f": getattr(pf, "strategy", "?"),
    })

json.dump(results, open("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-22/replay_results.json", "w"))

# ---- agregaty ----
N = len(results)
print(f"KORPUS: {len(corpus)} unikalnych worków ≥2 z logu 21-22.06")
print(f"SKIP: {skip}")
print(f"REPLAY OK (≥2 committed, GPS, coords): {N} worków\n")
if N == 0:
    sys.exit(0)

reord = [r for r in results if r["reordered"]]
print(f"PRZESTAWIONE przez fix: {len(reord)}/{N} = {100*len(reord)/N:.1f}%")

# punktualność committed: per-pickup lateness, baseline vs fixed
def flat_late(key):
    out = []
    for r in results:
        out += list(r[key].values())
    return out
lb, lf = flat_late("lat_b"), flat_late("lat_f")
def pct_within(xs, tol=5.0):
    return 100 * sum(1 for x in xs if x <= tol) / len(xs) if xs else 0
def med(xs):
    xs = sorted(xs); n = len(xs)
    return (xs[n//2] if n % 2 else (xs[n//2-1]+xs[n//2])/2) if xs else 0
def p90(xs):
    xs = sorted(xs); return xs[min(len(xs)-1, int(0.9*len(xs)))] if xs else 0
print(f"\nPUNKTUALNOŚĆ ODBIORU committed (lateness = odbiór − czas_kuriera, min):")
print(f"  odbiorów committed łącznie: {len(lb)}")
print(f"  ≤+5 min (w regule):  baseline {pct_within(lb):.1f}%  →  fixed {pct_within(lf):.1f}%")
print(f"  ≤0 (na czas/wcześniej): baseline {pct_within(lb,0):.1f}%  →  fixed {pct_within(lf,0):.1f}%")
print(f"  mediana lateness:    baseline {med(lb):+.1f}  →  fixed {med(lf):+.1f}")
print(f"  p90 lateness:        baseline {p90(lb):+.1f}  →  fixed {p90(lf):+.1f}")
print(f"  max lateness:        baseline {max(lb):+.1f}  →  fixed {max(lf):+.1f}")

# koszt: SLA dostaw + czas trasy
dsla = sum(r["sla_f"] - r["sla_b"] for r in results)
worse_sla = sum(1 for r in results if r["sla_f"] > r["sla_b"])
better_sla = sum(1 for r in results if r["sla_f"] < r["sla_b"])
ddur = sum(r["dur_f"] - r["dur_b"] for r in results) / N
print(f"\nKOSZT:")
print(f"  Δ SLA dostaw (suma fixed−baseline): {dsla:+d}  (gorzej:{worse_sla} lepiej:{better_sla})")
print(f"  Δ czas trasy średnio: {ddur:+.2f} min/worek")
degraded = sum(1 for r in results if r["strat_f"] in ("greedy_fallback", "ortools_rejected_v3274"))
print(f"  degradacja solvera (greedy_fallback/rejected) fixed: {degraded}/{N}")

# --- WIARYGODNY CUT: tylko świeże worki (bez artefaktu sprasowania) ---
fr = [r for r in results if r["fresh"]]
print(f"\n=== CUT: ŚWIEŻE worki (committed ∈ [ts-5,ts+45]) = {len(fr)} ===")
if fr:
    frl_b = [v for r in fr for v in r["lat_b"].values()]
    frl_f = [v for r in fr for v in r["lat_f"].values()]
    fr_reord = sum(1 for r in fr if r["reordered"])
    fr_dsla = sum(r["sla_f"] - r["sla_b"] for r in fr)
    fr_worse = sum(1 for r in fr if r["sla_f"] > r["sla_b"])
    fr_better = sum(1 for r in fr if r["sla_f"] < r["sla_b"])
    print(f"  przestawione: {fr_reord}/{len(fr)} = {100*fr_reord/len(fr):.0f}%")
    print(f"  ≤+5 min: baseline {pct_within(frl_b):.1f}% → fixed {pct_within(frl_f):.1f}%")
    print(f"  mediana lateness: {med(frl_b):+.1f} → {med(frl_f):+.1f}  | p90 {p90(frl_b):+.1f} → {p90(frl_f):+.1f}")
    print(f"  Δ SLA dostaw: {fr_dsla:+d} (gorzej:{fr_worse} lepiej:{fr_better})")

# przykłady przestawień z poprawą punktualności
print(f"\nPRZYKŁADY przestawień (max poprawa punktualności):")
def improve(r):
    return (max(r["lat_b"].values()) - max(r["lat_f"].values())) if r["lat_b"] and r["lat_f"] else 0
for r in sorted(reord, key=improve, reverse=True)[:6]:
    rests = [(orders_state.get(o) or {}).get("restaurant", "?")[:14] for o in r["oids"]]
    print(f"  cid={r['cid']} oids={r['oids']} {rests}")
    print(f"    late max: {max(r['lat_b'].values()):+.1f} → {max(r['lat_f'].values()):+.1f}"
          f" | SLA {r['sla_b']}→{r['sla_f']} | dur {r['dur_b']:.1f}→{r['dur_f']:.1f}")
