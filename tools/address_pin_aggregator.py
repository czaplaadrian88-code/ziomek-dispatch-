#!/usr/bin/env python3
"""address_pin_aggregator.py — READ-ONLY obserwator budujący pamięć pinezek.

Dwie przestrzenie (additive, bez konsumentów decyzyjnych):
  • DOSTAWY (`address_pins.json`)      — klucz: znormalizowany `delivery_address`.
  • RESTAURACJE (`restaurant_pins.json`) — klucz: znormalizowany `pickup_address`.

Źródła punktów (rdzeń i tak preferuje lepszy tier — geofence > ręczne > trail):
  A. courier_status_events z GPS (kursor po `id`):
       status 7 (doręczone)               → pinezka DOSTAWY,
       status 4/5 (pod restauracją/odbiór) → pinezka RESTAURACJI.
  B. trail z `gps_history` (bramkowane: postój ±90s, dokładność≤25m, speed≈0):
       w chwili delivered_at → dostawa, w chwili picked_up_at → restauracja.

Indeks zlecenie→adresy (`address_pin_index.json`) jest trwały, żeby nie tracić
mapowania po wypadnięciu zlecenia z okna stanu. NIE mutuje stanu Ziomka, NIE woła
panelu/Telegrama, NIE dotyka feasibility/scoringu/selekcji. Fail-soft.
"""
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import argparse
import bisect
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dispatch_v2 import address_pin_memory as apm

_log = logging.getLogger("address_pin_aggregator")
WARSAW = ZoneInfo("Europe/Warsaw")

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
ORDERS_STATE = os.path.join(STATE_DIR, "orders_state.json")
COURIER_DB = os.path.join(STATE_DIR, "courier_api.db")
DELIV_STORE = os.path.join(STATE_DIR, "address_pins.json")
REST_STORE = os.path.join(STATE_DIR, "restaurant_pins.json")
INDEX_PATH = os.path.join(STATE_DIR, "address_pin_index.json")
CURSOR_PATH = os.path.join(STATE_DIR, "address_pin_cursor.json")

DELIVERED_STATUS = 7
PICKUP_STATUSES = (4, 5)        # 4=oczekiwanie pod restauracją (100% GPS), 5=odebrane
EVENT_STATUSES = (4, 5, 7)

# Trail (źródło B) — ostre sito jakości (pomiar 29.06: spread restauracji ~16m, dostaw ~32m)
TRAIL_WINDOW_S = 90
TRAIL_MAX_ACCURACY_M = 25.0
TRAIL_MAX_SPEED = 1.0
TRAIL_STALE_S = 3600
TRAIL_CAP_PER_RUN = 1500        # cap pracy trail na żywy tick (seed bez capa)

HISTORY_FILES = [
    os.path.join(STATE_DIR, "orders_state.json.prev"),
    os.path.join(STATE_DIR, "orders_state.json.bak-pre-backfill-delivered-20260613-143557"),
    os.path.join(STATE_DIR, "orders_state.pre-prune-2026-06-04.json"),
]


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _parse_warsaw_epoch(s):
    """'YYYY-MM-DD HH:MM:SS' (czas Warszawy) → epoch UTC, lub None."""
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=WARSAW).timestamp()
    except (ValueError, TypeError):
        return None


def _index_entry_from_order(entry: dict, o: dict) -> dict:
    """Wzbogaca wpis indeksu danymi zlecenia (oba adresy + czasy + kurier)."""
    if o.get("delivery_address"):
        entry["address_text"] = o["delivery_address"]
    if o.get("pickup_address"):
        entry["pickup_text"] = o["pickup_address"]
    if o.get("courier_id"):
        entry.setdefault("courier_id", str(o["courier_id"]))
    if o.get("delivered_at"):
        ep = _parse_warsaw_epoch(o["delivered_at"])
        if ep:
            entry.setdefault("delivered_epoch", ep)
    if o.get("picked_up_at"):
        ep = _parse_warsaw_epoch(o["picked_up_at"])
        if ep:
            entry.setdefault("picked_up_epoch", ep)
    return entry


def update_order_index(index: dict, orders_state: dict) -> int:
    """order_id → {address_text, pickup_text, courier_id, delivered_epoch, picked_up_epoch}.
    Trwałe (raz poznane zostaje). Zwraca liczbę nowo poznanych zleceń."""
    new = 0
    for oid, o in orders_state.items():
        if not isinstance(o, dict):
            continue
        if not (o.get("delivery_address") or o.get("pickup_address")):
            continue
        entry = index.get(str(oid))
        if entry is None:
            entry = {}
            new += 1
        index[str(oid)] = _index_entry_from_order(entry, o)
    return new


def seed_index_from_history(index: dict, files: list) -> int:
    """Jednorazowy seed indeksu z historycznych snapshotów (nie nadpisuje znanych)."""
    added = 0
    for f in files:
        try:
            with open(f) as fh:
                d = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            continue
        for oid, o in d.items():
            if not isinstance(o, dict) or not (o.get("delivery_address") or o.get("pickup_address")):
                continue
            if str(oid) not in index:
                index[str(oid)] = _index_entry_from_order({}, o)
                added += 1
    return added


def fetch_gps_events(db_path: str, after_id: int) -> list:
    """Zdarzenia status 4/5/7 z poprawnym GPS, id > after_id, rosnąco po id."""
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path)
    try:
        ph = ",".join("?" * len(EVENT_STATUSES))
        rows = con.execute(
            "SELECT id, order_id, lat, lon, accuracy, trigger, recorded_at, status_code "
            f"FROM courier_status_events WHERE status_code IN ({ph}) AND id>? "
            "AND lat IS NOT NULL AND lat!=0 AND lon IS NOT NULL AND lon!=0 ORDER BY id ASC",
            (*EVENT_STATUSES, int(after_id)),
        ).fetchall()
    finally:
        con.close()
    return [{"event_id": r[0], "order_id": str(r[1]), "lat": r[2], "lon": r[3],
             "accuracy": r[4], "trigger": r[5], "ts": r[6], "status_code": r[7]}
            for r in rows]


def _load_gps_by_courier(db_path: str, courier_ids: set) -> dict:
    """gps_history dla kurierów → {cid: posortowana lista (epoch,lat,lon,acc,speed)}."""
    if not courier_ids or not os.path.exists(db_path):
        return {}
    con = sqlite3.connect(db_path)
    try:
        ph = ",".join("?" * len(courier_ids))
        rows = con.execute(
            "SELECT courier_id,lat,lon,accuracy,speed,recorded_at FROM gps_history "
            f"WHERE lat IS NOT NULL AND lat!=0 AND courier_id IN ({ph})",
            tuple(courier_ids),
        ).fetchall()
    finally:
        con.close()
    by_c = {}
    for cid, lat, lon, acc, sp, ra in rows:
        by_c.setdefault(str(cid), []).append(
            (int(ra), lat, lon, acc if acc is not None else 999.0,
             sp if sp is not None else 0.0))
    for c in by_c:
        by_c[c].sort()
    return by_c


def best_trail_point(arr, epoch):
    """Najlepszy punkt POSTOJU w oknie wokół czasu zdarzenia (ostre sito) lub None."""
    if not arr:
        return None
    ts = [a[0] for a in arr]
    lo = bisect.bisect_left(ts, epoch - TRAIL_WINDOW_S)
    hi = bisect.bisect_right(ts, epoch + TRAIL_WINDOW_S)
    cand = [a for a in arr[lo:hi]
            if a[3] <= TRAIL_MAX_ACCURACY_M and a[4] <= TRAIL_MAX_SPEED]
    if not cand:
        return None
    cand.sort(key=lambda a: (a[3], abs(a[0] - epoch)))
    return cand[0]


def _apply_trail(index, store, key_field, epoch_field, done_field,
                 trail_by_c, now, applied_counter):
    """Wspólna pętla trail dla jednej przestrzeni (dostawa/restauracja)."""
    for oid, e in index.items():
        if e.get(done_field) or not e.get(key_field) or not e.get("courier_id") \
                or not e.get(epoch_field):
            continue
        pt = best_trail_point(trail_by_c.get(e["courier_id"]), e[epoch_field])
        if pt:
            apm.add_sample(store, e[key_field], {
                "lat": pt[1], "lon": pt[2], "accuracy": pt[3],
                "trigger": apm.TRAIL_TRIGGER, "ts": e[epoch_field], "order_id": oid,
            }, now=now)
            applied_counter[0] += 1
        if pt or (now - e[epoch_field] > TRAIL_STALE_S):
            e[done_field] = True


def run(dry_run: bool = False, seed_history: bool = False) -> dict:
    """Jeden przebieg: odśwież indeks, dołącz punkty (geofence + trail) do obu przestrzeni."""
    now = time.time()
    orders_state = _load_json(ORDERS_STATE)
    index = _load_json(INDEX_PATH)
    deliv = apm.load_store(DELIV_STORE)
    rest = apm.load_store(REST_STORE)
    cursor = _load_json(CURSOR_PATH)
    last_id = int(cursor.get("last_event_id", 0))

    seeded = seed_index_from_history(index, HISTORY_FILES) if seed_history else 0
    new_orders = update_order_index(index, orders_state)

    # --- Źródło A: geofence z courier_status_events (kursor po id) ---
    events = fetch_gps_events(COURIER_DB, last_id)
    d_applied = r_applied = skipped = 0
    max_id = last_id
    for ev in events:
        max_id = max(max_id, int(ev["event_id"]))
        meta = index.get(ev["order_id"])
        if not meta:
            skipped += 1
            continue
        if ev["status_code"] == DELIVERED_STATUS:
            store, key = deliv, meta.get("address_text")
        else:  # 4/5 → restauracja
            store, key = rest, meta.get("pickup_text")
        if not key:
            skipped += 1
            continue
        apm.add_sample(store, key, {
            "lat": ev["lat"], "lon": ev["lon"], "accuracy": ev["accuracy"],
            "trigger": ev["trigger"] or apm.GEOFENCE_TRIGGER, "ts": ev["ts"],
            "order_id": ev["order_id"],
        }, now=now)
        if ev["status_code"] == DELIVERED_STATUS:
            d_applied += 1
        else:
            r_applied += 1

    # --- Źródło B: trail z gps_history (bramkowane), obie przestrzenie ---
    need = {e["courier_id"] for e in index.values()
            if e.get("courier_id") and (
                (e.get("delivered_epoch") and not e.get("deliv_trail_done") and e.get("address_text"))
                or (e.get("picked_up_epoch") and not e.get("pickup_trail_done") and e.get("pickup_text")))}
    trail_by_c = _load_gps_by_courier(COURIER_DB, need)
    dt, rt = [0], [0]
    _apply_trail(index, deliv, "address_text", "delivered_epoch", "deliv_trail_done", trail_by_c, now, dt)
    _apply_trail(index, rest, "pickup_text", "picked_up_epoch", "pickup_trail_done", trail_by_c, now, rt)

    if not dry_run:
        apm.save_store(DELIV_STORE, deliv)
        apm.save_store(REST_STORE, rest)
        apm.save_store(INDEX_PATH, index)
        apm.save_store(CURSOR_PATH, {"last_event_id": max_id, "updated_at": int(now)})

    return {
        "seeded_from_history": seeded, "new_orders_indexed": new_orders,
        "events_seen": len(events), "skipped_no_address": skipped,
        "delivery": {"geofence_applied": d_applied, "trail_applied": dt[0],
                     "addresses": len(deliv),
                     "high_conf": sum(1 for e in deliv.values() if e.get("confidence") == "high")},
        "restaurant": {"geofence_applied": r_applied, "trail_applied": rt[0],
                       "restaurants": len(rest),
                       "high_conf": sum(1 for e in rest.values() if e.get("confidence") == "high")},
        "cursor_from": last_id, "cursor_to": max_id, "dry_run": dry_run,
    }


def _report(restaurants: bool) -> None:
    store = apm.load_store(REST_STORE if restaurants else DELIV_STORE)
    label = "RESTAURACJE" if restaurants else "DOSTAWY"
    pins = [p for p in (apm.public_pin(e) for e in store.values()) if p]
    pins.sort(key=lambda p: (p["confidence"] != "high", -(p["deliveries"] or 0)))
    print(f"[{label}] pinezek: {len(pins)} (pewne: {sum(1 for p in pins if p['confidence']=='high')})")
    for p in pins[:30]:
        print(f"  [{p['confidence']:4}] {p['deliveries']:2}× [{p.get('source') or '?':12}] "
              f"{p['lat']},{p['lon']}  {p.get('address_text') or p['address_key']}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Agregator pamięci pinezek (dostawy + restauracje)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--restaurants", action="store_true", help="raport dla restauracji")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--seed-history", action="store_true")
    args = ap.parse_args()

    if args.reset:
        for p in (DELIV_STORE, REST_STORE, INDEX_PATH, CURSOR_PATH):
            if os.path.exists(p):
                os.unlink(p)
        print("reset done")
        return 0
    if args.report:
        _report(args.restaurants)
        return 0
    try:
        res = run(dry_run=args.dry_run, seed_history=args.seed_history)
    except Exception as e:  # fail-soft — obserwator nie wywraca timera
        _log.warning("address_pin_aggregator run failed: %s: %s", type(e).__name__, e)
        return 0
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
