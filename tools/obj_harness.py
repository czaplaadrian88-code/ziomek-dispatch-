#!/usr/bin/env python3
"""obj_harness — offline replay harness, sprint OBJ (kalibracja objective OR-Tools).

Buduje deklaratywne przypadki (Case), uruchamia route_simulator
(simulate_bag_route_v2), liczy metryki (route_metrics.compute_plan_metrics) i
emituje raport JSON. Ten sam zestaw Case'ów puszczony PRZED i PO F1/F2 → `diff`
pokazuje wpływ zmiany objective (sekwencja + idle/thermal/r6_breach/span).

Zestaw WIERNY: 3 zdiagnozowane patologie (474266 / 474253 / 474297). Coords z
produkcyjnych źródeł (restaurant_coords.json + geocoding) — faithful.
courier_pos resolvowany z courier_api.db/gps_history albo jako drop odebranego
zlecenia (pos_source=last_picked_up_delivery). picked_up_at = proxy czas_kuriera
(przybliżone, oznaczone) — patologie są strukturalne, nie zależą od precyzji minut.

Zestaw MASOWY (regresja/breadth) — F0.3: replay-capture solver inputs;
podłączany tu jako dodatkowe Case'y gdy log capture narośnie.

Użycie:
  python obj_harness.py run  [--out raport.json]
  python obj_harness.py diff --a baseline.json --b nowy.json
"""
import argparse
import html
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import geocoding  # noqa: E402
from dispatch_v2.route_metrics import compute_plan_metrics  # noqa: E402
from dispatch_v2.route_simulator_v2 import OrderSim, simulate_bag_route_v2  # noqa: E402

COURIER_DB = "/root/.openclaw/workspace/dispatch_state/courier_api.db"
RESTAURANT_COORDS = "/root/.openclaw/workspace/dispatch_state/restaurant_coords.json"


# ─── model przypadku ─────────────────────────────────────────────────

@dataclass
class Order:
    oid: str
    restaurant: str
    delivery_addr: str
    ck_warsaw: Optional[str]      # czas_kuriera ISO (frozen pickup) lub None
    picked_up: bool
    ready_utc: Optional[str] = None       # pickup_ready_at ISO (jedzenie gotowe)
    picked_up_at_utc: Optional[str] = None  # ISO; proxy = czas_kuriera


@dataclass
class Case:
    case_id: str
    label: str
    now_utc: str                 # decyzja, ISO UTC
    tier: str
    courier_pos: str             # "gps:CID" | "drop:OID" | "lat,lon"
    orders: List[Order]
    new_oid: str
    notes: str = ""


# ─── rozwiązywanie współrzędnych (faithful — produkcyjne źródła) ─────

def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", html.unescape(s or "").lower())


_RESTO_CACHE: Optional[dict] = None


def _resto_coord(name: str) -> Optional[Tuple[float, float]]:
    """Coords restauracji: restaurant_coords.json (match po company), fallback
    geocoding.geocode_restaurant. None gdy nie da się rozwiązać."""
    global _RESTO_CACHE
    if _RESTO_CACHE is None:
        with open(RESTAURANT_COORDS) as f:
            raw = json.load(f)
        _RESTO_CACHE = {_norm_name(v.get("company", "")): (v["lat"], v["lng"])
                        for v in raw.values() if v.get("lat") and v.get("lng")}
    hit = _RESTO_CACHE.get(_norm_name(name))
    if hit:
        return hit
    try:
        g = geocoding.geocode_restaurant(html.unescape(name), city="Białystok")
        if g:
            return (g[0], g[1])
    except Exception:
        pass
    return None


def _addr_coord(addr: str) -> Optional[Tuple[float, float]]:
    """Coords adresu dostawy z produkcyjnego geocoding (cache geocode_cache.json)."""
    try:
        g = geocoding.geocode(addr, city="Białystok")
        if g:
            return (g[0], g[1])
    except Exception:
        pass
    return None


def _courier_pos(spec: str, resolved_drops: dict) -> Optional[Tuple[float, float]]:
    """Pozycja kuriera wg specyfikacji Case.
      gps:CID  → najbliższy GPS z courier_api.db do decyzji (resolved_drops['now_epoch'])
      drop:OID → delivery_coords zlecenia OID (pos_source=last_picked_up_delivery)
      lat,lon  → wprost
    """
    if spec.startswith("drop:"):
        return resolved_drops.get(spec[5:])
    if spec.startswith("gps:"):
        cid = spec[4:]
        now_epoch = resolved_drops["__now_epoch__"]
        con = sqlite3.connect(COURIER_DB)
        rows = con.execute(
            "SELECT lat, lon, recorded_at FROM gps_history WHERE courier_id=? "
            "ORDER BY ABS(recorded_at-?) LIMIT 1", (cid, now_epoch)).fetchall()
        con.close()
        return (rows[0][0], rows[0][1]) if rows else None
    if "," in spec:
        a, b = spec.split(",", 1)
        return (float(a), float(b))
    return None


# ─── uruchomienie przypadku ──────────────────────────────────────────

def _dt(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def run_case(case: Case) -> dict:
    """Buduje wejścia, uruchamia simulate_bag_route_v2, liczy metryki.
    Zwraca dict raportu (nigdy nie rzuca — błędy w polu 'error')."""
    now = _dt(case.now_utc)
    out = {"case_id": case.case_id, "label": case.label, "now_utc": case.now_utc}
    try:
        # coords + drop map (dla courier_pos drop:OID)
        drops: dict = {"__now_epoch__": int(now.timestamp())}
        sims: dict = {}
        coord_miss: List[str] = []
        for o in case.orders:
            rc = _resto_coord(o.restaurant)
            dc = _addr_coord(o.delivery_addr)
            if rc is None:
                coord_miss.append(f"resto:{o.restaurant}")
            if dc is None:
                coord_miss.append(f"addr:{o.delivery_addr}")
            drops[o.oid] = dc
            sim = OrderSim(
                order_id=o.oid,
                pickup_coords=rc or (0.0, 0.0),
                delivery_coords=dc or (0.0, 0.0),
                picked_up_at=_dt(o.picked_up_at_utc) if o.picked_up else None,
                status="picked_up" if o.picked_up else "assigned",
                pickup_ready_at=_dt(o.ready_utc),
            )
            sim.czas_kuriera_warsaw = o.ck_warsaw  # frozen-window detection
            sims[o.oid] = sim
        if coord_miss:
            out["error"] = "coord_miss: " + ", ".join(sorted(set(coord_miss)))
            return out

        courier_pos = _courier_pos(case.courier_pos, drops)
        if courier_pos is None:
            out["error"] = f"courier_pos unresolved: {case.courier_pos}"
            return out

        new_order = sims[case.new_oid]
        bag = [sims[o.oid] for o in case.orders if o.oid != case.new_oid]
        dwell_p, dwell_d = C.dwell_for_tier(case.tier)

        plan = simulate_bag_route_v2(
            courier_pos, bag, new_order, now=now,
            dwell_pickup=dwell_p, dwell_dropoff=dwell_d,
        )
        out.update({
            "strategy": plan.strategy,
            "sequence": plan.sequence,
            "sla_violations": plan.sla_violations,
            "metrics": compute_plan_metrics(plan, dwell_p),
        })
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def run_all(cases: List[Case]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_cases": len(cases),
        "cases": [run_case(c) for c in cases],
    }


def diff(report_a: dict, report_b: dict) -> None:
    """Porównuje dwa raporty po case_id — sekwencja + metryki."""
    by_b = {c["case_id"]: c for c in report_b.get("cases", [])}
    for ca in report_a.get("cases", []):
        cid = ca["case_id"]
        cb = by_b.get(cid)
        print(f"\n── {cid}  {ca.get('label','')}")
        if cb is None:
            print("  brak w raporcie B")
            continue
        if ca.get("error") or cb.get("error"):
            print(f"  A.error={ca.get('error')}  B.error={cb.get('error')}")
            continue
        seq_chg = ca["sequence"] != cb["sequence"]
        print(f"  sequence: A={ca['sequence']}  B={cb['sequence']}"
              f"{'   ← ZMIANA' if seq_chg else ''}")
        print(f"  strategy: {ca['strategy']} → {cb['strategy']}")
        for k in ("idle_total_min", "max_thermal_age_min",
                  "r6_breach_max_min", "route_span_min"):
            va, vb = ca["metrics"].get(k), cb["metrics"].get(k)
            d = (vb or 0) - (va or 0)
            mark = "" if abs(d) < 0.01 else f"  Δ{d:+.2f}"
            print(f"  {k:22s} {va} → {vb}{mark}")


# ─── ZESTAW WIERNY — 3 zdiagnozowane patologie ───────────────────────

FAITHFUL_CASES: List[Case] = [
    Case(
        case_id="474266", label="Borsucza-przed-Młynową (frozen window)",
        now_utc="2026-05-17T15:47:53+00:00", tier="std", courier_pos="drop:474235",
        new_oid="474266",
        notes="diagnoza /tmp/diagnoza_474266_or_tools_2026-05-17.md",
        orders=[
            Order("474235", "_500 stopni", "Borsucza 10/33",
                  "2026-05-17T17:41:00+02:00", True,
                  picked_up_at_utc="2026-05-17T15:41:00+00:00"),
            Order("474239", "Restauracja Sioux", "Młynowa 70/11",
                  "2026-05-17T17:49:00+02:00", False,
                  ready_utc="2026-05-17T15:49:00+00:00"),
            Order("474266", "Pani Pierożek", "Aleja Józefa Piłsudskiego",
                  None, False, ready_utc="2026-05-17T16:17:24+00:00"),
        ],
    ),
    Case(
        case_id="474253", label="kurier stoi 15 min pod Ranym Julkiem (idle)",
        now_utc="2026-05-17T15:58:56+00:00", tier="std", courier_pos="drop:474251",
        new_oid="474253",
        notes="diagnoza /tmp/diagnoza_474253_idle_objective_2026-05-17.md",
        orders=[
            Order("474251", "Rukola Kaczorowskiego", "Sadowa 174/4",
                  "2026-05-17T17:26:00+02:00", True,
                  picked_up_at_utc="2026-05-17T15:26:00+00:00"),
            Order("474274", "Pan Schabowy", "Stołeczna 14D/47",
                  "2026-05-17T18:17:00+02:00", False,
                  ready_utc="2026-05-17T16:17:00+00:00"),
            Order("474253", "Rany Julek", "Sybiraków 8/1",
                  None, False, ready_utc="2026-05-17T16:42:00+00:00"),
        ],
    ),
    Case(
        case_id="474297", label="thermal 82 min + R6 łamane (Kumar's)",
        now_utc="2026-05-17T16:38:46+00:00", tier="std+", courier_pos="gps:400",
        new_oid="474297",
        notes="diagnoza /tmp/diagnoza_474297_kumars_2026-05-17.md",
        orders=[
            Order("474252", "Gym Fit Food", "Pułku Piechoty 72G",
                  "2026-05-17T18:09:00+02:00", True,
                  picked_up_at_utc="2026-05-17T16:09:00+00:00"),
            Order("474261", "Pizza Dealer", "Sybiraków 3/34",
                  "2026-05-17T18:20:00+02:00", True,
                  picked_up_at_utc="2026-05-17T16:20:00+00:00"),
            Order("474291", "Grill Kebab", "Ciepła 36/123",
                  "2026-05-17T19:06:00+02:00", False,
                  ready_utc="2026-05-17T17:06:00+00:00"),
            Order("474297", "Restauracja Kumar's", "Wspólna 2/25",
                  None, False, ready_utc="2026-05-17T16:53:30+00:00"),
        ],
    ),
]


def main():
    ap = argparse.ArgumentParser(description="obj_harness — replay sprint OBJ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="uruchom zestaw wierny, raport JSON")
    r.add_argument("--out", default="/tmp/obj_report.json")
    d = sub.add_parser("diff", help="porównaj dwa raporty")
    d.add_argument("--a", required=True)
    d.add_argument("--b", required=True)
    args = ap.parse_args()

    if args.cmd == "run":
        report = run_all(FAITHFUL_CASES)
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        for c in report["cases"]:
            if c.get("error"):
                print(f"  {c['case_id']}  ERROR: {c['error']}")
            else:
                m = c["metrics"]
                print(f"  {c['case_id']}  strat={c['strategy']:9s} "
                      f"seq={c['sequence']}  idle={m['idle_total_min']} "
                      f"thermal={m['max_thermal_age_min']} "
                      f"r6_breach={m['r6_breach_max_min']} span={m['route_span_min']}")
        print(f"\nraport → {args.out}")
    elif args.cmd == "diff":
        with open(args.a) as f:
            ra = json.load(f)
        with open(args.b) as f:
            rb = json.load(f)
        diff(ra, rb)


if __name__ == "__main__":
    main()
