#!/usr/bin/env python3
"""address_pin_aggregator.py — READ-ONLY obserwator budujący pamięć pinezek adresów.

Co robi (Etap 1+2, additive):
  1. Czyta `orders_state.json` → utrzymuje TRWAŁY indeks zlecenie→adres
     (+ delivered_at/courier_id dla doręczonych), żeby NIE tracić mapowania po
     wypadnięciu zlecenia z okna stanu.
  2. Źródło A (geofence, najlepsze): NOWE „doręczone" (status 7) z GPS z
     `courier_status_events` (kursor po `id`) → punkt-kandydat trigger=auto_geofence.
  3. Źródło B (trail, bramkowane): dla doręczonych zleceń szuka pozycji POSTOJU
     kuriera w chwili delivered_at w `gps_history` (±90s, dokładność≤25m, speed≈0)
     → punkt-kandydat trigger=trail. Słabsze (prawda-przyciskowa), więc rdzeń
     i tak preferuje geofence; trail tylko bootstrapuje adresy bez geofence.
  4. Przelicza najlepszą pinezkę per adres (robust median + odrzut odstających)
     i zapisuje `address_pins.json`.

NIE mutuje stanu Ziomka, NIE woła panelu/Telegrama, NIE dotyka feasibility/
scoringu/selekcji. Magazyn nie ma jeszcze konsumentów decyzyjnych. Fail-soft.
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

WARSAW = ZoneInfo("Europe/Warsaw")

# Źródło B (trail) — ostre sito jakości (z pomiaru 29.06: spread mediana ~32m)
TRAIL_WINDOW_S = 90          # okno wokół delivered_at
TRAIL_MAX_ACCURACY_M = 25.0  # tylko dobre fixy
TRAIL_MAX_SPEED = 1.0        # postój (m/s) — kurier stoi pod klatką, nie w biegu
TRAIL_STALE_S = 3600         # po godzinie bez punktu odpuść (gps już nie dojdzie)

_log = logging.getLogger("address_pin_aggregator")

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
ORDERS_STATE = os.path.join(STATE_DIR, "orders_state.json")
COURIER_DB = os.path.join(STATE_DIR, "courier_api.db")
STORE_PATH = os.path.join(STATE_DIR, "address_pins.json")
INDEX_PATH = os.path.join(STATE_DIR, "address_pin_index.json")
CURSOR_PATH = os.path.join(STATE_DIR, "address_pin_cursor.json")

DELIVERED_STATUS = 7

# Jednorazowy seed indeksu zlecenie→adres z historycznych snapshotów orders_state
# (żeby historyczne punkty GPS, których zlecenia wypadły z bieżącego okna, też
# się zmapowały). order_id→adres jest stabilne, więc snapshot point-in-time jest OK.
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


def update_order_index(index: dict, orders_state: dict) -> int:
    """Dokłada/odświeża mapowanie order_id→{address_text} z orders_state.

    Klucz adresu liczymy z TEKSTU (`delivery_address`), bo address_id panelu jest
    recyklingowany (patrz address_pin_memory.normalize_address). Trwałe — raz
    poznany adres zlecenia zostaje, nawet gdy zlecenie wypadnie ze stanu.
    Zwraca liczbę nowo poznanych zleceń.
    """
    new = 0
    for oid, o in orders_state.items():
        if not isinstance(o, dict):
            continue
        text = o.get("delivery_address")
        if not text:
            continue
        entry = index.get(str(oid))
        if entry is None:
            entry = {}
            new += 1
        entry["address_text"] = text
        # dane dla źródła B (trail): czas + kurier dostawy
        if o.get("status") == "delivered" and o.get("courier_id") and o.get("delivered_at"):
            ep = _delivered_epoch(o["delivered_at"])
            if ep:
                entry.setdefault("courier_id", str(o["courier_id"]))
                entry.setdefault("delivered_epoch", ep)
        index[str(oid)] = entry
    return new


def _delivered_epoch(s):
    """orders_state delivered_at ('YYYY-MM-DD HH:MM:SS', czas Warszawy) → epoch UTC."""
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=WARSAW).timestamp()
    except (ValueError, TypeError):
        return None


def seed_index_from_history(index: dict, files: list) -> int:
    """Jednorazowo dokłada order_id→adres z historycznych snapshotów (nie nadpisuje
    już znanych). Zwraca liczbę nowo dołożonych mapowań."""
    added = 0
    for f in files:
        try:
            with open(f) as fh:
                d = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            continue
        for oid, o in d.items():
            if not isinstance(o, dict) or not o.get("delivery_address"):
                continue
            if str(oid) not in index:
                entry = {"address_text": o["delivery_address"]}
                if o.get("status") == "delivered" and o.get("courier_id") and o.get("delivered_at"):
                    ep = _delivered_epoch(o["delivered_at"])
                    if ep:
                        entry["courier_id"] = str(o["courier_id"])
                        entry["delivered_epoch"] = ep
                index[str(oid)] = entry
                added += 1
    return added


def _load_gps_by_courier(db_path: str, courier_ids: set) -> dict:
    """gps_history dla wskazanych kurierów → {cid: posortowana lista (epoch,lat,lon,acc,speed)}."""
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
    """Najlepszy punkt POSTOJU w oknie wokół delivered_at (ostre sito) lub None."""
    if not arr:
        return None
    ts = [a[0] for a in arr]
    lo = bisect.bisect_left(ts, epoch - TRAIL_WINDOW_S)
    hi = bisect.bisect_right(ts, epoch + TRAIL_WINDOW_S)
    cand = [a for a in arr[lo:hi]
            if a[3] <= TRAIL_MAX_ACCURACY_M and a[4] <= TRAIL_MAX_SPEED]
    if not cand:
        return None
    cand.sort(key=lambda a: (a[3], abs(a[0] - epoch)))  # najlepsza dokładność, bliżej czasu
    return cand[0]  # (epoch,lat,lon,acc,speed)


def fetch_delivered_gps_events(db_path: str, after_id: int) -> list:
    """Zdarzenia status 7 z poprawnym GPS, id > after_id, rosnąco po id."""
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, order_id, lat, lon, accuracy, trigger, recorded_at "
            "FROM courier_status_events "
            "WHERE status_code=? AND id>? AND lat IS NOT NULL AND lat!=0 "
            "      AND lon IS NOT NULL AND lon!=0 "
            "ORDER BY id ASC",
            (DELIVERED_STATUS, int(after_id)),
        ).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        out.append({
            "event_id": r[0], "order_id": str(r[1]), "lat": r[2], "lon": r[3],
            "accuracy": r[4], "trigger": r[5], "ts": r[6],
        })
    return out


def run(dry_run: bool = False, seed_history: bool = False,
        trail_cap: int = 1500) -> dict:
    """Jeden przebieg: odśwież indeks, dołącz punkty (geofence + trail), przelicz, zapisz."""
    now = time.time()
    orders_state = _load_json(ORDERS_STATE)
    index = _load_json(INDEX_PATH)
    store = apm.load_store(STORE_PATH)
    cursor = _load_json(CURSOR_PATH)
    last_id = int(cursor.get("last_event_id", 0))

    seeded = seed_index_from_history(index, HISTORY_FILES) if seed_history else 0
    new_orders = update_order_index(index, orders_state)

    # --- Źródło A: geofence z courier_status_events (kursor po id) ---
    events = fetch_delivered_gps_events(COURIER_DB, last_id)
    applied, skipped_no_addr = 0, 0
    max_id = last_id
    for ev in events:
        max_id = max(max_id, int(ev["event_id"]))
        meta = index.get(ev["order_id"])
        if not meta or not meta.get("address_text"):
            skipped_no_addr += 1
            continue
        apm.add_sample(store, meta["address_text"], {
            "lat": ev["lat"], "lon": ev["lon"], "accuracy": ev["accuracy"],
            "trigger": ev["trigger"] or apm.GEOFENCE_TRIGGER, "ts": ev["ts"],
            "order_id": ev["order_id"],
        }, now=now)
        applied += 1

    # --- Źródło B: trail z gps_history dla doręczonych (bramkowane) ---
    pending = [(oid, e) for oid, e in index.items()
               if not e.get("trail_done") and e.get("address_text")
               and e.get("courier_id") and e.get("delivered_epoch")]
    cap = trail_cap if (trail_cap and not seed_history) else None
    if cap:
        pending = pending[:cap]
    trail_by_c = _load_gps_by_courier(COURIER_DB, {e["courier_id"] for _, e in pending})
    trail_applied = 0
    for oid, e in pending:
        pt = best_trail_point(trail_by_c.get(e["courier_id"]), e["delivered_epoch"])
        if pt:
            apm.add_sample(store, e["address_text"], {
                "lat": pt[1], "lon": pt[2], "accuracy": pt[3],
                "trigger": apm.TRAIL_TRIGGER, "ts": e["delivered_epoch"], "order_id": oid,
            }, now=now)
            trail_applied += 1
        # oznacz przetworzone gdy znaleziono LUB dostawa już stara (gps nie dojdzie)
        if pt or (now - e["delivered_epoch"] > TRAIL_STALE_S):
            e["trail_done"] = True

    if not dry_run:
        apm.save_store(STORE_PATH, store)
        apm.save_store(INDEX_PATH, index)
        apm.save_store(CURSOR_PATH, {"last_event_id": max_id, "updated_at": int(now)})

    high = sum(1 for e in store.values() if e.get("confidence") == "high")
    return {
        "seeded_from_history": seeded,
        "new_orders_indexed": new_orders,
        "events_seen": len(events),
        "points_applied": applied,
        "skipped_no_address": skipped_no_addr,
        "trail_pending": len(pending),
        "trail_applied": trail_applied,
        "addresses_total": len(store),
        "addresses_high_conf": high,
        "cursor_from": last_id, "cursor_to": max_id,
        "dry_run": dry_run,
    }


def _report() -> None:
    store = apm.load_store(STORE_PATH)
    pins = [apm.public_pin(e) for e in store.values()]
    pins = [p for p in pins if p]
    pins.sort(key=lambda p: (p["confidence"] != "high", -(p["deliveries"] or 0)))
    print(f"Adresy z pinezką: {len(pins)} "
          f"(pewne: {sum(1 for p in pins if p['confidence']=='high')})")
    for p in pins[:30]:
        print(f"  [{p['confidence']:4}] {p['deliveries']:2}× "
              f"{p['lat']},{p['lon']}  {p.get('address_text') or p['address_key']}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Agregator pamięci pinezek adresów (Etap 1)")
    ap.add_argument("--dry-run", action="store_true", help="policz, nie zapisuj")
    ap.add_argument("--report", action="store_true", help="wypisz bieżący magazyn")
    ap.add_argument("--reset", action="store_true", help="wyczyść magazyn/indeks/kursor")
    ap.add_argument("--seed-history", action="store_true",
                    help="jednorazowo dołóż order→adres z historycznych snapshotów")
    args = ap.parse_args()

    if args.reset:
        for p in (STORE_PATH, INDEX_PATH, CURSOR_PATH):
            if os.path.exists(p):
                os.unlink(p)
        print("reset done")
        return 0
    if args.report:
        _report()
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
