"""perf_lazy_harness — profiler + pomiar + bajt-parytet dla FALA perf compute-zawsze
(finding E audytu 2.0, flaga ENABLE_PERF_LAZY_MEMBERS).

Offline, READ-ONLY wobec żywych serwisów. Replay REALNYCH zdarzeń NEW_ORDER z
`dispatch_state/events.db` na deterministycznie-syntezowanej flocie (seed stabilny
międzyprocesowo — builtin hash() jest PYTHONHASHSEED-solony, więc NIE nadaje się).
OSRM :5001 wołany realnie (lokalny Docker, cache in-proc); pomiary pod `nice -19`.

Tryby:
  profile  — cProfile ranking członów hot-path (ms/decyzję per funkcja).
  measure  — wall p50/p95 OFF vs ON (in-proc monkeypatch) + rozbicie flags/plans.
  parity   — serializuje decyzje (REALNY _serialize_result) do JSONL; odpal 2× (env
             ENABLE_PERF_LAZY_MEMBERS 0 vs 1) i zdiffuj. Wyklucza WYŁĄCZNIE pola
             czysto-czasowe (latency/ts/cache-age/eval-ts) — potwierdzone kontrolą
             OFF vs OFF (identyczne). Użyj PYTHONHASHSEED=0 dla determinizmu proc.

Uwaga: to narzędzie diagnostyczne fali; nie jest importowane przez żywy silnik.
"""
from __future__ import annotations
import argparse
import cProfile
import hashlib
import json
import os
import pstats
import random
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = os.environ.get("ZIOMEK_SCRIPTS_ROOT") or str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import logging
logging.disable(logging.WARNING)

from dispatch_v2.core.decide import decide as _decide  # noqa: E402  # K09 fasada
from dispatch_v2.core.world_state import WorldState    # noqa: E402
from dispatch_v2 import common as C                    # noqa: E402
from dispatch_v2 import plan_manager as PM             # noqa: E402
from dispatch_v2 import shadow_dispatcher as SD        # noqa: E402
from dispatch_v2.courier_resolver import CourierState  # noqa: E402

EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"

# Pola CZYSTO-CZASOWE — jedyne dozwolone różnice ON↔OFF i run↔run (kontrola OFF/OFF).
_TIME_ONLY = {
    "ts", "latency_ms", "r07_compute_latency_ms",
    "osrm_cache_age_s", "osrm_degraded_since_ts",
    "evaluation_ts", "feature_compute_ms", "inference_ms", "generated_at",
}


def _det_seed(order_id, seed) -> int:
    return int(hashlib.md5(f"{order_id}-{seed}".encode()).hexdigest()[:8], 16)


def _load_events(limit):
    con = sqlite3.connect(EVENTS_DB)
    rows = con.execute(
        "select payload, created_at from events where event_type='NEW_ORDER' "
        "and payload is not null order by created_at desc limit ?", (limit,)).fetchall()
    con.close()
    out = []
    for payload, created in rows:
        p = json.loads(payload)
        if not p.get("pickup_coords") or not p.get("delivery_coords"):
            continue
        now = datetime.fromisoformat(created)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        oe = dict(p)
        oe.setdefault("order_created_at", p.get("created_at_utc"))
        oe["order_id"] = p.get("order_id") or f"EV{len(out)}"
        out.append((oe, now))
    return out


def _synth_fleet(oe, now, size, rng):
    plat, plon = oe["pickup_coords"]
    dlat, dlon = oe["delivery_coords"]
    tiers = ["gold", "std+", "std", "std", "slow"]
    psources = ["gps", "gps", "gps", "no_gps", "pre_shift"]
    fleet = {}
    for i in range(size):
        cs = CourierState(courier_id=str(8000 + i))
        base_lat = plat if i % 2 == 0 else dlat
        base_lon = plon if i % 2 == 0 else dlon
        ps = psources[i % len(psources)]
        cs.pos_source = ps
        cs.pos = None if ps == "pre_shift" else (base_lat + rng.uniform(-0.02, 0.02),
                                                 base_lon + rng.uniform(-0.03, 0.03))
        cs.pos_age_min = None if ps == "pre_shift" else rng.uniform(0, 8)
        nbag = rng.choice([0, 0, 0, 0, 0, 1, 1, 2])
        bag = []
        for b in range(nbag):
            bag.append({
                "order_id": f"{cs.courier_id}b{b}",
                "pickup_coords": [plat + rng.uniform(-0.005, 0.005), plon + rng.uniform(-0.005, 0.005)],
                "delivery_coords": [plat + rng.uniform(-0.02, 0.02), plon + rng.uniform(-0.03, 0.03)],
                "status": "assigned", "picked_up_at": None,
                "pickup_ready_at": (now - timedelta(minutes=rng.uniform(0, 10))).isoformat(),
            })
        cs.bag = bag
        cs.shift_start = (now - timedelta(minutes=rng.uniform(20, 240))) if ps != "pre_shift" \
            else (now + timedelta(minutes=rng.uniform(5, 25)))
        cs.shift_end = now + timedelta(hours=rng.uniform(1, 5))
        cs.name = f"C{i}"
        cs.tier_bag = cs.tier_label = tiers[i % len(tiers)]
        fleet[cs.courier_id] = cs
    return fleet


def _cases(n, seed, fleet):
    """fleet: int (fixed) lub None (cykl 0/3/5/8/10/12 dla parity coverage)."""
    cyc = [0, 3, 5, 8, 10, 12]
    out = []
    for i, (oe, now) in enumerate(_load_events(n)):
        fs = fleet if fleet is not None else cyc[i % len(cyc)]
        fl = _synth_fleet(oe, now, fs, random.Random(_det_seed(oe["order_id"], seed)))
        out.append((oe, now, fs, fl))
    return out


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in _TIME_ONLY}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


# ─────────────────────────────── modes ───────────────────────────────

def cmd_profile(a):
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False
    cases = _cases(a.n, a.seed, a.fleet)
    for oe, now, _fs, fl in cases[:3]:
        _decide(WorldState(fleet_snapshot=dict(fl), now=now), oe)
    wall, pr = [], cProfile.Profile()
    for _ in range(a.repeats):
        for oe, now, _fs, fl in cases:
            t0 = time.perf_counter()
            pr.enable()
            _decide(WorldState(fleet_snapshot=dict(fl), now=now), oe)
            pr.disable()
            wall.append((time.perf_counter() - t0) * 1000)
    wall.sort()
    p = lambda q: wall[min(len(wall) - 1, int(len(wall) * q))]
    print(f"WALL ms n={len(wall)} p50={p(.5):.1f} p95={p(.95):.1f} mean={statistics.mean(wall):.1f}")
    st = pstats.Stats(pr, stream=sys.stdout)
    st.sort_stats("tottime"); print("\n=== TOP by TOTTIME (self) ==="); st.print_stats(a.top)


def _measure(cases, repeats):
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False
    for oe, now, _fs, fl in cases[:3]:
        _decide(WorldState(fleet_snapshot=dict(fl), now=now), oe)
    wall = []
    for _ in range(repeats):
        for oe, now, _fs, fl in cases:
            t0 = time.perf_counter()
            _decide(WorldState(fleet_snapshot=dict(fl), now=now), oe)
            wall.append((time.perf_counter() - t0) * 1000)
    wall.sort()
    p = lambda q: wall[min(len(wall) - 1, int(len(wall) * q))]
    return dict(n=len(wall), p50=round(p(.5), 1), p95=round(p(.95), 1),
                mean=round(statistics.mean(wall), 1))


def cmd_measure(a):
    cases = _cases(a.n, a.seed, a.fleet)
    print("baseline    ", _measure(cases, a.repeats))
    o_lf = C.load_flags
    st = {"t": 0.0, "d": None}
    def frozen():
        if st["d"] is None or time.monotonic() - st["t"] > 1.0:
            st["d"] = o_lf(); st["t"] = time.monotonic()
        return st["d"]
    C.load_flags = frozen
    print("flags-frozen", _measure(cases, a.repeats)); C.load_flags = o_lf
    o_rr = PM._read_raw
    cache = {"d": None}
    PM._read_raw = lambda: (cache.__setitem__("d", o_rr()) or cache["d"]) if cache["d"] is None else cache["d"]
    print("plans-cached", _measure(cases, a.repeats)); PM._read_raw = o_rr
    print("baseline2   ", _measure(cases, a.repeats))


def cmd_parity(a):
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False
    cases = _cases(a.n, a.seed, None)
    with open(a.out, "w") as fh:
        for i, (oe, now, fs, fl) in enumerate(cases):
            try:
                res = _decide(WorldState(fleet_snapshot=fl, now=now), oe)
                line = json.dumps(_strip(SD._serialize_result(res, "PARITY", 0.0)),
                                  ensure_ascii=False, sort_keys=True, default=str)
            except Exception as e:
                line = "ERR:" + repr(e)
            fh.write(f"{i}\t{fs}\t{line}\n")
    print(f"wrote {a.out} n={len(cases)} perf_lazy_env={os.environ.get('ENABLE_PERF_LAZY_MEMBERS','0')}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("profile", "measure", "parity"):
        s = sub.add_parser(name)
        s.add_argument("--n", type=int, default=120)
        s.add_argument("--seed", type=int, default=7)
        s.add_argument("--repeats", type=int, default=3)
        if name == "parity":
            s.add_argument("--out", required=True)
        else:
            s.add_argument("--fleet", type=int, default=10)
        if name == "profile":
            s.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    {"profile": cmd_profile, "measure": cmd_measure, "parity": cmd_parity}[a.cmd](a)


if __name__ == "__main__":
    main()
