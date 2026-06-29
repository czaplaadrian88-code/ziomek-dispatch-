#!/usr/bin/env python3
"""address_pin_aggregator.py — READ-ONLY obserwator budujący pamięć pinezek adresów.

Co robi (Etap 1, additive):
  1. Czyta `orders_state.json` → utrzymuje TRWAŁY indeks zlecenie→adres
     (`address_pin_index.json`), żeby NIE tracić mapowania po wypadnięciu
     zlecenia z okna stanu.
  2. Czyta NOWE zdarzenia „doręczone" (status 7) z GPS z `courier_status_events`
     (kursor po `id`) i dokłada punkt-kandydata do adresu (z indeksu).
  3. Przelicza najlepszą pinezkę per adres (robust median + odrzut odstających)
     i zapisuje `address_pins.json`.

NIE mutuje stanu Ziomka, NIE woła panelu/Telegrama, NIE dotyka feasibility/
scoringu/selekcji. Magazyn nie ma jeszcze konsumentów decyzyjnych. Fail-soft.
"""
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import argparse
import json
import logging
import os
import sqlite3
import time

from dispatch_v2 import address_pin_memory as apm

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
        if str(oid) not in index:
            new += 1
        index[str(oid)] = {"address_text": text}
    return new


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
                index[str(oid)] = {"address_text": o["delivery_address"]}
                added += 1
    return added


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


def run(dry_run: bool = False, seed_history: bool = False) -> dict:
    """Jeden przebieg: odśwież indeks, dołącz nowe punkty, przelicz, zapisz."""
    orders_state = _load_json(ORDERS_STATE)
    index = _load_json(INDEX_PATH)
    store = apm.load_store(STORE_PATH)
    cursor = _load_json(CURSOR_PATH)
    last_id = int(cursor.get("last_event_id", 0))

    seeded = seed_index_from_history(index, HISTORY_FILES) if seed_history else 0
    new_orders = update_order_index(index, orders_state)
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
            "trigger": ev["trigger"], "ts": ev["ts"], "order_id": ev["order_id"],
        })
        applied += 1

    if not dry_run:
        apm.save_store(STORE_PATH, store)
        apm.save_store(INDEX_PATH, index)
        apm.save_store(CURSOR_PATH, {"last_event_id": max_id, "updated_at": int(time.time())})

    high = sum(1 for e in store.values() if e.get("confidence") == "high")
    return {
        "seeded_from_history": seeded,
        "new_orders_indexed": new_orders,
        "events_seen": len(events),
        "points_applied": applied,
        "skipped_no_address": skipped_no_addr,
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
