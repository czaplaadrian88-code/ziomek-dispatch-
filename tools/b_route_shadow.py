"""b_route_shadow — READ-ONLY shadow opcji B (Adrian 2026-06-23: „zbierać dane, zbadać czy coś da").

Po naprawie kolejności (recanon + opcja A LIVE 23.06, [[recanon-on-write-2026-06-23]]) pytanie
otwarte: czy NATYCHMIASTOWE pełne przeliczenie trasy (B = `_gen_one_bag_plan` re-TSP) przy zmianie
worka dałoby LEPSZE realne wyniki niż serwowany kanon (carried-first+committed+relax)? Statycznie
A≡B w 95,9%; B-lite vs re-TSP +1,4 min med — ale to TRASA, nie WYNIK. Rozstrzyga TYLKO outcome-join.

Ten proces (osobny timer, NIE hot-path — doktryna V3.27.4): dla każdego multi-order worka, gdy
zmieni się jego sygnatura (override/nowe zlecenie/odbiór), liczy 3 trasy + metryki świeżości i
punktualności, i APPENDUJE do `b_route_shadow.jsonl`. ZERO mutacji żywego stanu:
  - plan_manager.PLANS_FILE przekierowany na temp (in-proc, NIE rusza courier_plans.json),
  - nie pisze orders_state, nie emituje eventów, nie woła Telegrama.
Outcome-join (po N dniach) = osobny krok: join `order_ids` → `sla_log.jsonl`/ground-truth →
czy worki gdzie B≠served kończyły się gorzej przy serwowanej trasie.

Flaga `ENABLE_B_ROUTE_SHADOW` (default OFF). Uruchamiany: `python -m dispatch_v2.tools.b_route_shadow`.
"""
import os
import sys
import json
import pathlib
import tempfile
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] b_route_shadow: %(message)s")
_log = logging.getLogger("b_route_shadow")

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
ORDERS_STATE = f"{STATE_DIR}/orders_state.json"
PLANS_LIVE = f"{STATE_DIR}/courier_plans.json"
GPS_PATH = f"{STATE_DIR}/gps_positions_pwa.json"
OUT_JSONL = f"{STATE_DIR}/b_route_shadow.jsonl"
STATE_PATH = f"{STATE_DIR}/b_route_shadow_state.json"
FLAG = "ENABLE_B_ROUTE_SHADOW"
MAX_PER_RUN = int(os.environ.get("B_ROUTE_SHADOW_MAX_PER_RUN", "25"))
MAXN = int(os.environ.get("B_ROUTE_SHADOW_MAXN", "8"))
ACTIVE = {"assigned", "picked_up"}
# #4 audyt-fix (28.06 → naprawa 29.06): trasa B = _gen_one_bag_plan, ktore GAŁEZIUJE
# na tych flagach route/canon. Serwis MUSI miec env-PARYTET z LIVE dispatch-plan-recheck
# (drop-in route-flag-parity.conf), inaczej B liczy INNA geometrie niz serwowany kanon →
# differs_b/delta = fantomy (pre-fix 45% differs, delta −30..+35). Stempel route_env na
# KAZDYM rekordzie = provenance (C9: przyrzad niesie semantyke pod ktora liczyl); rekordy
# bez route_env = epoka PRZED-parytet (widmo, zarchiwizowane). Re-weryfikacja parytetu:
#   diff <(systemctl show dispatch-plan-recheck.service -p Environment) \
#        <(systemctl show dispatch-b-route-shadow.service -p Environment)
ROUTE_PARITY_FLAGS = (
    "ENABLE_GPS_FREE_ANCHOR", "ENABLE_GPS_FREE_ANCHOR_LAST_POS",
    "ENABLE_CARRIED_FIRST_RELAX", "ENABLE_CARRIED_AGE_TZ_FIX",
    "ENABLE_LEX_COMMITTED_WINDOW", "ENABLE_LEX_COMMITTED_WINDOW_SHADOW",
    "ENABLE_NONCARRIED_DROPOFF_REORDER", "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP",
    "ENABLE_PLAN_CANON_ORDER_INVARIANTS", "ENABLE_PLAN_REAL_PICKED_UP_AT",
    "ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION", "ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH",
    "ENABLE_PLAN_SEQUENCE_LOCK", "ENABLE_RELAX_COLOC_PICKUP",
)

# --- IZOLACJA: zapisy plan_manager (z _gen_one_bag_plan) idą na temp, NIE na żywy courier_plans ---
from dispatch_v2 import plan_manager as PM          # noqa: E402
_SHADOW_PLANS = pathlib.Path(tempfile.gettempdir()) / "b_route_shadow_plans.json"
PM.PLANS_FILE = _SHADOW_PLANS
PM.LOCK_FILE = pathlib.Path(str(_SHADOW_PLANS) + ".lock")
if not _SHADOW_PLANS.exists():
    _SHADOW_PLANS.write_text("{}")

from dispatch_v2 import plan_recheck as P            # noqa: E402
from dispatch_v2 import route_simulator_v2 as R      # noqa: E402
from dispatch_v2 import osrm_client                  # noqa: E402

DWELL_P = 1.0
DWELL_D = 3.5


def _load(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {} if default is None else default


def _cok(c):
    return (isinstance(c, (list, tuple)) and len(c) == 2
            and all(isinstance(x, (int, float)) for x in c)
            and abs(c[0]) > 1.0 and abs(c[1]) > 1.0)


def _parse(ts):
    """ISO → aware UTC. Naive (np. picked_up_at) traktujemy jak Warsaw (konwencja stanu)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WARSAW)
    return dt.astimezone(timezone.utc)


def _bag_sig(oids, orders_state):
    parts = []
    for oid in sorted(oids):
        st = (orders_state.get(oid) or {}).get("status")
        parts.append(f"{oid}:{1 if st == 'picked_up' else 0}")
    return "|".join(parts)


def _mine_from_bag(oids, orders_state):
    mine = {}
    for oid in oids:
        o = orders_state.get(oid) or {}
        dc = o.get("delivery_coords")
        pc = o.get("pickup_coords")
        if not _cok(dc):
            return None
        if o.get("status") != "picked_up" and not _cok(pc):
            return None
        mine[oid] = {"status": o.get("status") or "assigned",
                     "courier_id": o.get("courier_id"),
                     "czas_kuriera_warsaw": o.get("czas_kuriera_warsaw"),
                     "picked_up_at": o.get("picked_up_at"),
                     "assigned_at": o.get("assigned_at"),
                     "pickup_coords": pc, "delivery_coords": dc}
    return mine


def _stops_from_mine(mine):
    """Panel-format stopy: niesione = tylko dropoff; reszta pickup+dropoff."""
    st = []
    for oid, o in mine.items():
        if o.get("status") != "picked_up":
            st.append({"order_id": oid, "type": "pickup",
                       "coords": {"lat": o["pickup_coords"][0], "lng": o["pickup_coords"][1]},
                       "dwell_min": DWELL_P, "status_at_plan_time": "assigned"})
        st.append({"order_id": oid, "type": "dropoff",
                   "coords": {"lat": o["delivery_coords"][0], "lng": o["delivery_coords"][1]},
                   "dwell_min": DWELL_D, "status_at_plan_time": o.get("status") or "assigned"})
    return st


def _coords_of(s, mine):
    o = mine[str(s["order_id"])]
    c = o["pickup_coords"] if s["type"] == "pickup" else o["delivery_coords"]
    return (float(c[0]), float(c[1]))


def _walk_metrics(seq, mine, pos, now):
    """Łańcuch OSRM od pos: drive_min + per-order czasy. Zwraca metryki świeżości/punktualności.
    carried_age_max = max wiek niesionego jedzenia w aucie do jego dostawy (min).
    pickup_late_max = max spóźnienie odbioru vs committed (min). deliver_span = ostatnia dostawa od now."""
    pts = [(float(pos[0]), float(pos[1]))] + [_coords_of(s, mine) for s in seq]
    m = osrm_client.table(pts, pts)
    if not m:
        return None
    t = now
    drive = 0.0
    pick_at, deliv_at = {}, {}
    for i, s in enumerate(seq):
        v = (m[i][i + 1] or {}).get("duration_s")
        if v is None or v >= 9e8:
            return None
        leg = v / 60.0
        drive += leg
        t = t + timedelta(minutes=leg)
        oid = str(s["order_id"])
        if s["type"] == "pickup":
            ck = _parse(mine[oid].get("czas_kuriera_warsaw"))
            if ck is not None and ck > t:
                t = ck
            pick_at[oid] = t
            t = t + timedelta(minutes=DWELL_P)
        else:
            deliv_at[oid] = t
            t = t + timedelta(minutes=DWELL_D)
    carried_ages, pickup_lates = [], []
    for oid, o in mine.items():
        d = deliv_at.get(oid)
        if d is None:
            continue
        if o.get("status") == "picked_up":
            pa = _parse(o.get("picked_up_at"))
            if pa is not None:
                carried_ages.append((d - pa).total_seconds() / 60.0)
        else:
            p = pick_at.get(oid)
            if p is not None:
                carried_ages.append((d - p).total_seconds() / 60.0)
            ck = _parse(o.get("czas_kuriera_warsaw"))
            if p is not None and ck is not None:
                pickup_lates.append((p - ck).total_seconds() / 60.0)
    last_deliv = max(deliv_at.values()) if deliv_at else now
    return {
        "drive_min": round(drive, 2),
        "carried_age_max_min": round(max(carried_ages), 1) if carried_ages else None,
        "pickup_late_max_min": round(max(pickup_lates), 1) if pickup_lates else None,
        "finish_in_min": round((last_deliv - now).total_seconds() / 60.0, 1),
    }


def _oid_seq(seq):
    return [(s["type"], str(s["order_id"])) for s in seq]


def _served_order(plan_doc, mine):
    """Trasa SERWOWANA = kolejność stopów z żywego courier_plans (kanon po recanon),
    odsiana do aktywnego worka; niesione = sam dropoff. Fallback gdy plan nie pokrywa: carried-first."""
    if isinstance(plan_doc, dict) and plan_doc.get("stops"):
        out, seen = [], set()
        for s in plan_doc["stops"]:
            oid = str(s.get("order_id"))
            if oid not in mine:
                continue
            typ = "pickup" if s.get("type") == "pickup" else "dropoff"
            if typ == "pickup" and mine[oid].get("status") == "picked_up":
                continue
            key = (typ, oid)
            if key in seen:
                continue
            seen.add(key)
            out.append({"order_id": oid, "type": typ})
        # czy pokrywa cały worek?
        cov_d = {o for (t, o) in [(x["type"], x["order_id"]) for x in out] if t == "dropoff"}
        if cov_d >= set(mine.keys()):
            return out
    # fallback carried-first (jak panel opcja A)
    carried = [o for o in mine if mine[o].get("status") == "picked_up"]
    topick = [o for o in mine if mine[o].get("status") != "picked_up"]
    out = [{"order_id": o, "type": "dropoff"} for o in carried]
    topick.sort(key=lambda o: (mine[o].get("czas_kuriera_warsaw") or "~"))
    for o in topick:
        out.append({"order_id": o, "type": "pickup"})
    for o in topick:
        out.append({"order_id": o, "type": "dropoff"})
    return out


def _b_full_retsp(cid, oids, mine, pos, now):
    """B = pełne re-TSP _gen_one_bag_plan (zapis na shadow-temp). Zwraca stopy lub None."""
    osd = {oid: dict(o) for oid, o in mine.items()}
    for o in osd.values():
        o["courier_id"] = cid
    gps = {str(cid): {"lat": float(pos[0]), "lon": float(pos[1]), "timestamp": now.isoformat()}}
    try:
        _SHADOW_PLANS.write_text("{}")
        ok = P._gen_one_bag_plan(str(cid), list(oids), osd, gps, now, R)
        if not ok:
            return None
        plan = PM.load_plan(str(cid))
        if not plan or not plan.get("stops"):
            return None
        return [{"order_id": str(s["order_id"]),
                 "type": "pickup" if s["type"] == "pickup" else "dropoff"}
                for s in plan["stops"]]
    except Exception as e:
        _log.warning(f"B re-TSP fail cid={cid}: {type(e).__name__}: {e}")
        return None


def _b_lite(served_seq, mine, pos, now):
    """B-lite = wstaw NAJNOWSZE zlecenie (max assigned_at) do trasy bez niego + canon. Tanio."""
    newest = None
    newest_ts = ""
    for oid, o in mine.items():
        ts = str(o.get("assigned_at") or "")
        if ts > newest_ts:
            newest_ts, newest = ts, oid
    if newest is None or len(mine) < 2:
        return None
    try:
        pre_stops = [dict(s) for s in served_seq if str(s["order_id"]) != newest]
        for s in pre_stops:
            o = mine[str(s["order_id"])]
            c = o["pickup_coords"] if s["type"] == "pickup" else o["delivery_coords"]
            s["coords"] = {"lat": c[0], "lng": c[1]}
            s["dwell_min"] = DWELL_P if s["type"] == "pickup" else DWELL_D
            s["status_at_plan_time"] = o.get("status") or "assigned"
        no = mine[newest]
        nstops = []
        if no.get("status") != "picked_up":
            nstops.append({"order_id": newest, "type": "pickup",
                           "coords": {"lat": no["pickup_coords"][0], "lng": no["pickup_coords"][1]},
                           "dwell_min": DWELL_P, "status_at_plan_time": "assigned"})
        nstops.append({"order_id": newest, "type": "dropoff",
                       "coords": {"lat": no["delivery_coords"][0], "lng": no["delivery_coords"][1]},
                       "dwell_min": DWELL_D, "status_at_plan_time": no.get("status") or "assigned"})
        plan = {"start_pos": {"lat": float(pos[0]), "lng": float(pos[1])},
                "start_ts": now.isoformat(), "stops": pre_stops, "optimization_method": "incremental"}
        allc = [(float(pos[0]), float(pos[1]))] + [(s["coords"]["lat"], s["coords"]["lng"]) for s in pre_stops + nstops]
        pts = [(float(a), float(b)) for a, b in allc]
        M = osrm_client.table(pts, pts)
        idx = {(round(a, 6), round(b, 6)): i for i, (a, b) in enumerate(pts)}

        def lf(a, b):
            ia = idx.get((round(a[0], 6), round(a[1], 6)))
            ib = idx.get((round(b[0], 6), round(b[1], 6)))
            if ia is None or ib is None:
                return 0.0
            v = (M[ia][ib] or {}).get("duration_s")
            return (v / 60.0) if (v is not None and v < 9e8) else 9e3
        merged = PM.insert_stop_optimal(plan, nstops, now, lf)
        canon = P._apply_canon_order_invariants([dict(s) for s in merged["stops"]], mine,
                                                 (float(pos[0]), float(pos[1])), now)
        return [{"order_id": str(s["order_id"]),
                 "type": "pickup" if s["type"] == "pickup" else "dropoff"} for s in canon]
    except Exception as e:
        _log.warning(f"B-lite fail: {type(e).__name__}: {e}")
        return None


def _append_jsonl(rows, path=OUT_JSONL):
    if not rows:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as e:
        _log.warning(f"append_jsonl fail: {e}")


def run_once(now=None, dry_run=False):
    if os.environ.get(FLAG, "0") != "1":
        _log.info(f"{FLAG} != 1 → no-op")
        return []
    now = now or datetime.now(timezone.utc)
    orders_state = _load(ORDERS_STATE)
    plans = _load(PLANS_LIVE)
    gps = _load(GPS_PATH)
    state = _load(STATE_PATH)
    if not isinstance(state, dict):
        state = {}

    by_cid = {}
    for oid, o in orders_state.items():
        if isinstance(o, dict) and o.get("status") in ACTIVE and o.get("courier_id") is not None:
            by_cid.setdefault(str(o["courier_id"]), []).append(str(oid))

    rows = []
    new_state = dict(state)
    processed = 0
    for cid, oids in by_cid.items():
        if len(oids) < 2:
            new_state[cid] = ""        # pojedyncze worki nie interesują (brak kolejności do sporu)
            continue
        sig = _bag_sig(oids, orders_state)
        if state.get(cid) == sig:
            continue                   # już zalogowane dla tej sygnatury (dedup)
        new_state[cid] = sig
        if processed >= MAX_PER_RUN:
            continue                   # zapisz sygnaturę (nie re-loguj), ale ogranicz liczenie/run
        mine = _mine_from_bag(oids, orders_state)
        if mine is None:
            continue
        # Pozycja startu JAK SILNIK: świeży GPS / last-event / committed-pickup — obejmuje też
        # kurierów BEZ GPS (Piotr/Grzesiek/Mateusz byli no-GPS, to ICH dotyczył bug). Bez tego
        # korpus byłby skrzywiony do floty z GPS. None = silnik też by nie policzył → pomiń.
        try:
            anchor = P._start_anchor(cid, oids, orders_state, gps, now)
        except Exception:
            anchor = None
        if anchor is None or not _cok(anchor[0]):
            continue
        pos = anchor[0]
        carried = [o for o in mine if mine[o].get("status") == "picked_up"]
        served = _served_order(plans.get(cid), mine)
        b = _b_full_retsp(cid, oids, mine, pos, now)
        blite = _b_lite(served, mine, pos, now)
        m_served = _walk_metrics(served, mine, pos, now)
        m_b = _walk_metrics(b, mine, pos, now) if b else None
        m_blite = _walk_metrics(blite, mine, pos, now) if blite else None
        differs_b = bool(b) and _oid_seq(b) != _oid_seq(served)
        differs_blite = bool(blite) and _oid_seq(blite) != _oid_seq(served)
        rows.append({
            "ts": now.isoformat(),
            "cid": cid,
            # provenance epoki flag (C9): pod jakimi flagami route/canon liczona ta trasa B.
            # Brak klucza = rekord PRZED-parytet (29.06) = NIE ufac differs/delta.
            "route_env": {k: os.environ.get(k, "0") for k in ROUTE_PARITY_FLAGS},
            "bag_sig": sig,
            "order_ids": sorted(oids),
            "n_orders": len(oids),
            "n_carried": len(carried),
            "served": _oid_seq(served),
            "b": _oid_seq(b) if b else None,
            "blite": _oid_seq(blite) if blite else None,
            "differs_b": differs_b,
            "differs_blite": differs_blite,
            "m_served": m_served,
            "m_b": m_b,
            "m_blite": m_blite,
            # gotowe delty (B vs served): >0 = B lepszy (krótsza jazda / świeższe / mniej spóźnień)
            "delta_drive_b": round(m_served["drive_min"] - m_b["drive_min"], 2) if (m_served and m_b) else None,
            "delta_carried_age_b": (round(m_served["carried_age_max_min"] - m_b["carried_age_max_min"], 1)
                                    if (m_served and m_b and m_served.get("carried_age_max_min") is not None
                                        and m_b.get("carried_age_max_min") is not None) else None),
        })
        processed += 1

    if dry_run:
        _log.info(f"DRY-RUN: {len(rows)} rekordów (processed={processed}, couriers={len(by_cid)})")
        return rows
    _append_jsonl(rows)
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(new_state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        _log.warning(f"state save fail: {e}")
    n_diff = sum(1 for r in rows if r["differs_b"])
    _log.info(f"logged={len(rows)} differs_b={n_diff} (couriers={len(by_cid)}, processed={processed})")
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
