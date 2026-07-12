#!/usr/bin/env python3
"""rebuild_state_from_events.py — odtwarza orders_state.json z trwałego event-logu.

Faza 3 (2026-05-18) disaster-recovery. Gdy orders_state.json zostanie utracony
lub uszkodzony, ten skrypt rekonstruuje go z events.db przez replay przez
istniejącą logikę state_machine.update_from_event w kolejności czasowej.

Event-log jest KOMPLETNY, rozdzielony na 2 tabele (architektura „Opcja C"):
  - `events`     — queue lifecycle: NEW_ORDER, COURIER_PICKED_UP, COURIER_DELIVERED
  - `audit_log`  — append-only: COURIER_ASSIGNED, CZAS_KURIERA_UPDATED,
                   ORDER_RETURNED_TO_POOL (+ mirror PICKED_UP/DELIVERED)
Skrypt czyta OBIE i dedupuje po event_id.

BEZPIECZEŃSTWO: pisze WYŁĄCZNIE do katalogu --target (izolacja przez
DISPATCH_STATE_DIR — Faza 2b). NIGDY nie dotyka produkcyjnego orders_state.json.
Operator inspekcjonuje wynik i sam decyduje o restore (ręczny cp).

Użycie:
  python3 -m dispatch_v2.tools.rebuild_state_from_events [--target DIR]
          [--db PATH] [--since ISO8601] [--quiet]

Restore po inspekcji wyniku:
  systemctl stop dispatch-panel-watcher dispatch-shadow      # opcjonalnie
  cp <target>/orders_state.json /root/.openclaw/workspace/dispatch_state/orders_state.json
  systemctl start dispatch-panel-watcher dispatch-shadow
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

DEFAULT_DB = "/root/.openclaw/workspace/dispatch_state/events.db"
PROD_STATE = "/root/.openclaw/workspace/dispatch_state/orders_state.json"

# Tabele event-logu (Opcja C). PICKED_UP/DELIVERED są mirrorowane w obu →
# dedup po event_id usuwa duplikaty.
_EVENT_TABLES = ("events", "audit_log")


def _default_since(db_path):
    """Domyślny --since = najstarszy created_at w tabeli `events`.

    Poniżej tej granicy NEW_ORDER jest wypruty (retencja `events` ~2 dni) →
    rebuild dałby tysiące cienkich rekordów (sam COURIER_ASSIGNED z audit_log,
    bez danych zlecenia, status na wieki `assigned`). Recovery dotyczy zleceń
    AKTYWNYCH (świeże, godziny) — granica `events` to właściwy domyślny zakres.
    Operator może cofnąć: --since 1970-01-01 dla pełnej DB."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        row = conn.execute("SELECT min(created_at) FROM events").fetchone()
    finally:
        conn.close()
    return (row[0] if row and row[0] else None) or "1970-01-01"


def _read_events(db_path, since=None):
    """Czyta eventy z `events` + `audit_log`, dedup po event_id, sort po created_at."""
    if not os.path.exists(db_path):
        raise SystemExit(f"events.db nie istnieje: {db_path}")
    # Połączenie zwykłe (tylko SELECT) — WAL pozwala czytać równolegle z produkcją.
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    rows = {}
    for table in _EVENT_TABLES:
        if table not in existing:
            continue
        q = (f"SELECT event_id, event_type, order_id, courier_id, payload, "
             f"created_at FROM {table}")
        params = ()
        if since:
            q += " WHERE created_at >= ?"
            params = (since,)
        for r in conn.execute(q, params):
            rows.setdefault(r["event_id"], dict(r))  # pierwszy wygrywa = dedup
    conn.close()
    return sorted(rows.values(), key=lambda e: e.get("created_at") or "")


def _replay(events, target_file):
    """Replay przez state_machine.update_from_event do stanu w PAMIĘCI,
    zapis pliku raz na końcu. Zwraca (state, ok, skipped, fail, errors).

    In-memory: czytanie zwraca współdzielony dict (upsert mutuje go w miejscu),
    zapis = no-op. Bez tego 28k eventów × atomic-write całego rosnącego pliku
    = O(n²) (minuty). Reużywa CAŁEJ logiki update_from_event (zero duplikacji
    przejść stanu)."""
    from dispatch_v2 import state_machine
    # Wycisz INFO spam (tysiące „upsert ..." do dispatch.log mylą forensykę).
    state_machine._log.setLevel(logging.ERROR)

    mem = {}
    state_machine._read_state_strict = lambda: mem
    state_machine._read_state = lambda: mem
    state_machine._guarded_write = lambda *a, **k: None
    state_machine._atomic_write = lambda *a, **k: None

    ok = skipped = fail = 0
    errors = []
    for e in events:
        try:
            payload = json.loads(e["payload"]) if e.get("payload") else {}
        except (ValueError, TypeError):
            payload = {}
        event = {
            "event_id": e.get("event_id"),
            "event_type": e["event_type"],
            "order_id": e["order_id"],
            "courier_id": e["courier_id"],
            "payload": payload,
            "created_at": e.get("created_at"),
        }
        try:
            res = state_machine.update_from_event(event)
            if res is None:
                skipped += 1            # event_type nie zmienia stanu zlecenia
            else:
                ok += 1
        except state_machine.CorruptedTimestampError:
            # NEW_ORDER persistuje stan, potem raise dla sanity czas_kuriera —
            # zlecenie JEST odtworzone (bez pól czas_kuriera). Liczymy jako ok.
            ok += 1
        except Exception as ex:
            fail += 1
            errors.append((e.get("event_id"), e["event_type"],
                           f"{type(ex).__name__}: {ex}"))

    # Zapis raz, atomowo (temp → fsync → replace).
    tmp = target_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target_file)
    return mem, ok, skipped, fail, errors


def main():
    ap = argparse.ArgumentParser(
        description="Odtwórz orders_state.json z event-logu (disaster recovery).")
    ap.add_argument("--target", help="katalog docelowy (default: świeży tmp). "
                                     "Skrypt pisze tam orders_state.json.")
    ap.add_argument("--db", default=DEFAULT_DB,
                    help=f"ścieżka events.db (default {DEFAULT_DB})")
    ap.add_argument("--since", help="tylko eventy created_at >= ISO8601 "
                                    "(default: granica retencji tabeli `events`; "
                                    "--since 1970-01-01 = cała DB)")
    ap.add_argument("--quiet", action="store_true", help="bez listy błędów")
    args = ap.parse_args()

    target = args.target or tempfile.mkdtemp(prefix="state_rebuild_")
    os.makedirs(target, exist_ok=True)
    target_file = os.path.join(target, "orders_state.json")
    if os.path.abspath(target_file) == os.path.abspath(PROD_STATE):
        raise SystemExit(
            "ODMOWA: --target wskazuje produkcyjny orders_state.json. Rebuild "
            "pisze do izolowanego katalogu; restore = ręczny cp po inspekcji.")

    # Izolacja Faza 2b: state_machine._state_path() honoruje DISPATCH_STATE_DIR
    # → wszystkie zapisy idą do target, NIE do produkcji.
    os.environ["DISPATCH_STATE_DIR"] = target
    for suffix in ("", ".prev", ".lock"):  # świeży start w target
        try:
            os.unlink(target_file + suffix)
        except FileNotFoundError:
            pass

    since = args.since
    if since is None:
        since = _default_since(args.db)
        print(f"--since nie podany → domyślnie granica retencji `events`: {since}")
    events = _read_events(args.db, since=since)
    print(f"Wczytano {len(events)} eventów z {args.db} (since {since})")
    if not events:
        raise SystemExit("Brak eventów — nic do odtworzenia.")

    state, ok, skipped, fail, errors = _replay(events, target_file)

    by_status = {}
    for o in state.values():
        s = o.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1

    print(f"Replay: {ok} zastosowanych, {skipped} bez zmiany stanu, {fail} błędów")
    if errors and not args.quiet:
        print(f"Błędy ({len(errors)}, pierwsze 10):")
        for eid, et, msg in errors[:10]:
            print(f"  {eid} [{et}]: {msg}")
    print(f"Odtworzono {len(state)} zleceń → {target_file}")
    print("Statusy: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print("\nRestore po inspekcji wyniku:")
    print(f"  cp {target_file} {PROD_STATE}")
    print("  (rozważ stop dispatch-panel-watcher/dispatch-shadow na czas cp)")


if __name__ == "__main__":
    main()
