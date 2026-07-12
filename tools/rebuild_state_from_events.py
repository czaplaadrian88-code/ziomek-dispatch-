#!/usr/bin/env python3
"""rebuild_state_from_events.py — odtwarza orders_state.json z trwałego event-logu.

Faza 3 (2026-05-18) disaster-recovery. Gdy orders_state.json zostanie utracony
lub uszkodzony, ten skrypt rekonstruuje go z events.db przez replay przez
istniejącą logikę state_machine.update_from_event w kolejności czasowej.

Legacy event-log jest rozdzielony na 2 tabele (architektura „Opcja C"):
  - `events`     — queue lifecycle: NEW_ORDER, COURIER_PICKED_UP, COURIER_DELIVERED
  - `audit_log`  — append-only: COURIER_ASSIGNED, CZAS_KURIERA_UPDATED,
                   ORDER_RETURNED_TO_POOL (+ mirror PICKED_UP/DELIVERED)
Skrypt czyta OBIE i dedupuje po event_id.

A360-E1 dodaje jawny tryb ``--durable``. Ten tryb czyta wylacznie kompletne
rekordy ``event_envelopes`` i odtwarza stan pure reducerem. Nie uzupelnia
brakujacych pol zegarem ani danymi z tabel legacy. Wymaga jawnie wybranej,
zatwierdzonej polityki retencji, aby limit receiptow replayu byl tym samym
kontraktem co zapis stanu.

BEZPIECZEŃSTWO: pisze WYŁĄCZNIE do katalogu --target (izolacja przez
DISPATCH_STATE_DIR — Faza 2b). NIGDY nie dotyka produkcyjnego orders_state.json.
Operator inspekcjonuje wynik i sam decyduje o restore (ręczny cp).

Użycie:
  python3 -m dispatch_v2.tools.rebuild_state_from_events [--target DIR]
          [--db PATH] [--since ISO8601] [--quiet]
          [--durable --retention-policy-id POLICY]

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
import stat
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
_DURABLE_FIELDS = (
    "event_id",
    "event_type",
    "order_id",
    "courier_id",
    "payload",
    "created_at",
    "source",
    "envelope_version",
    "policy_version",
    "producer_key",
    "identity_scheme",
)


def _safe_atomic_write_state(target_file, state):
    """Publikuje nowy snapshot 0600 bez podazania za linkiem/cudzego overwrite.

    Target musi nie istniec. Staly ``.tmp`` jest celowy: ``O_EXCL`` zamienia
    pozostawiony plik lub symlink w jawny konflikt operatorski zamiast go
    nadpisac. Plik i katalog sa fsyncowane przed zwrotem sukcesu.
    """
    target = os.path.abspath(os.fspath(target_file))
    parent = os.path.dirname(target)
    if not parent or not os.path.isdir(parent):
        raise FileNotFoundError("rebuild target directory does not exist")
    if os.path.realpath(parent) != parent:
        raise RuntimeError("rebuild target directory cannot contain symlinks")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(parent, directory_flags)
    target_name = os.path.basename(target)
    temp_name = target_name + ".tmp"
    fd = None
    created_identity = None

    def entry_stat(name):
        try:
            return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    try:
        if entry_stat(target_name) is not None:
            raise FileExistsError("rebuild target already exists")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
        created_stat = os.fstat(fd)
        created_identity = (created_stat.st_dev, created_stat.st_ino)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        # link(2) jest atomowym NOREPLACE publish: target utworzony w race
        # powoduje EEXIST i nigdy nie jest nadpisywany. Obie nazwy sa liczone
        # wzgledem tego samego otwartego directory_fd co koncowy fsync.
        os.link(
            temp_name,
            target_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.unlink(temp_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if fd is not None:
            os.close(fd)
        if created_identity is not None:
            temp_stat = entry_stat(temp_name)
            if (
                temp_stat is not None
                and stat.S_ISREG(temp_stat.st_mode)
                and (temp_stat.st_dev, temp_stat.st_ino) == created_identity
            ):
                os.unlink(temp_name, dir_fd=directory_fd)
        os.close(directory_fd)


def _default_since(db_path, *, durable=False):
    """Domyślny --since = najstarszy created_at w tabeli `events`.

    Poniżej tej granicy NEW_ORDER jest wypruty (retencja `events` ~2 dni) →
    rebuild dałby tysiące cienkich rekordów (sam COURIER_ASSIGNED z audit_log,
    bez danych zlecenia, status na wieki `assigned`). Recovery dotyczy zleceń
    AKTYWNYCH (świeże, godziny) — granica `events` to właściwy domyślny zakres.
    Operator może cofnąć: --since 1970-01-01 dla pełnej DB."""
    table = "event_envelopes" if durable else "events"
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        existing = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if table not in existing or (durable and "event_outbox" not in existing):
            if durable:
                raise SystemExit(
                    "Brak kompletnego envelope/outbox — migracja durable nie zostala zastosowana."
                )
            return "1970-01-01"
        if durable:
            row = conn.execute(
                """SELECT min(e.created_at)
                   FROM event_envelopes e
                   JOIN event_outbox o ON o.event_id=e.event_id
                   WHERE o.consumer_id='order_state'
                     AND o.effect_type='reduce_order_state'"""
            ).fetchone()
        else:
            row = conn.execute(f"SELECT min(created_at) FROM {table}").fetchone()
    finally:
        conn.close()
    return (row[0] if row and row[0] else None) or "1970-01-01"


def _read_events(db_path, since=None, *, durable=False):
    """Czyta legacy log albo kompletne koperty durable.

    Durable celowo nie laczy rekordow z ``events``/``audit_log``. Rekord bez
    pelnej koperty ma pozostac widocznym brakiem migracji, a nie zostac
    niejawnie uwierzytelniony przez replay.
    """
    if not os.path.exists(db_path):
        raise SystemExit(f"events.db nie istnieje: {db_path}")
    # Połączenie zwykłe (tylko SELECT) — WAL pozwala czytać równolegle z produkcją.
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if durable:
        from dispatch_v2.migrations import durable_event_outbox

        inspection = durable_event_outbox.inspect_connection(conn)
        if not inspection["ready"]:
            conn.close()
            raise SystemExit(
                "Durable envelope/outbox schema lub integralnosc danych nie jest gotowa."
            )
        required_tables = {"event_envelopes", "event_outbox"}
        if not required_tables <= existing:
            conn.close()
            raise SystemExit(
                "Brak kompletnego envelope/outbox — migracja durable nie zostala zastosowana."
            )
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(event_envelopes)")
        }
        missing = sorted(set(_DURABLE_FIELDS) - columns)
        if missing:
            conn.close()
            raise SystemExit(
                "Niekompletna schema event_envelopes: " + ",".join(missing)
            )
        outbox_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(event_outbox)")
        }
        required_outbox = {"event_id", "consumer_id", "effect_type"}
        missing_outbox = sorted(required_outbox - outbox_columns)
        if missing_outbox:
            conn.close()
            raise SystemExit(
                "Niekompletna schema event_outbox: " + ",".join(missing_outbox)
            )
        fields = ",".join(f"e.{field}" for field in _DURABLE_FIELDS)
        query = (
            "SELECT " + fields + " FROM event_envelopes e "
            "JOIN event_outbox o ON o.event_id=e.event_id "
            "WHERE o.consumer_id='order_state' "
            "AND o.effect_type='reduce_order_state'"
        )
        params = ()
        if since:
            query += " AND e.created_at >= ?"
            params = (since,)
        result = [dict(row) for row in conn.execute(query, params)]
        conn.close()
        return sorted(
            result,
            key=lambda event: (event["created_at"], event["event_id"]),
        )

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
    return sorted(
        rows.values(),
        key=lambda event: (event.get("created_at") or "", event.get("event_id") or ""),
    )


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

    _safe_atomic_write_state(target_file, mem)
    return mem, ok, skipped, fail, errors


def _replay_durable(events, target_file, *, max_receipts_per_order):
    """Odtwarza kanoniczne koperty pure reducerem, bez I/O per event."""
    from dispatch_v2.event_envelope import EventEnvelope
    from dispatch_v2.order_event_reducer import (
        DURABLE_RECEIPTS_FIELD,
        reduce_order_event,
    )

    state = {}
    ok = skipped = fail = 0
    errors = []
    for raw in events:
        event_id = raw.get("event_id") if isinstance(raw, dict) else None
        event_type = raw.get("event_type") if isinstance(raw, dict) else None
        try:
            envelope = EventEnvelope.from_record(raw)
            current = state.get(envelope.order_id)
            if current is not None:
                # Replay jest sekwencyjny i nie wykonuje efektow; poprzedni
                # state-fence zostal juz odtworzony. Finalny snapshot potrzebuje
                # tylko fence ostatniej koperty, tak jak runtime per-order claim.
                current = dict(current)
                current[DURABLE_RECEIPTS_FIELD] = []
            result = reduce_order_event(
                current,
                envelope,
                max_receipts_per_order=max_receipts_per_order,
            )
            if result.record is None:
                skipped += 1
                continue
            state[str(envelope.order_id)] = result.record
            if result.domain_changed or result.receipt_changed:
                ok += 1
            else:
                skipped += 1
        except Exception as exc:
            fail += 1
            errors.append((
                event_id,
                event_type,
                f"{type(exc).__name__}: {exc}",
            ))

    _safe_atomic_write_state(target_file, state)
    return state, ok, skipped, fail, errors


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
    ap.add_argument(
        "--durable",
        action="store_true",
        help="replay tylko kanonicznych event_envelopes przez pure reducer",
    )
    ap.add_argument(
        "--retention-policy-id",
        help="zatwierdzona polityka retencji wymagana przez --durable",
    )
    args = ap.parse_args()

    if args.durable and not args.retention_policy_id:
        raise SystemExit("--durable wymaga --retention-policy-id")
    if args.retention_policy_id and not args.durable:
        raise SystemExit("--retention-policy-id ma znaczenie tylko z --durable")

    target = os.path.abspath(
        args.target or tempfile.mkdtemp(prefix="state_rebuild_")
    )
    if os.path.lexists(target):
        if os.path.islink(target) or not os.path.isdir(target):
            raise SystemExit("ODMOWA: --target musi byc rzeczywistym katalogiem")
    else:
        os.makedirs(target, mode=0o700)
    target_file = os.path.join(target, "orders_state.json")
    if os.path.abspath(target_file) == os.path.abspath(PROD_STATE):
        raise SystemExit(
            "ODMOWA: --target wskazuje produkcyjny orders_state.json. Rebuild "
            "pisze do izolowanego katalogu; restore = ręczny cp po inspekcji.")

    # Izolacja Faza 2b: state_machine._state_path() honoruje DISPATCH_STATE_DIR
    # → wszystkie zapisy idą do target, NIE do produkcji.
    os.environ["DISPATCH_STATE_DIR"] = target
    since = args.since
    if since is None:
        since = _default_since(args.db, durable=args.durable)
        table = "event_envelopes" if args.durable else "events"
        print(f"--since nie podany → domyślnie granica retencji `{table}`: {since}")
    events = _read_events(args.db, since=since, durable=args.durable)
    print(f"Wczytano {len(events)} eventów z {args.db} (since {since})")
    if not events:
        raise SystemExit("Brak eventów — nic do odtworzenia.")

    if args.durable:
        from dispatch_v2 import event_outbox

        conn = sqlite3.connect(args.db, timeout=10.0)
        try:
            contract = event_outbox.load_retention_contract(
                conn,
                args.retention_policy_id,
            )
        finally:
            conn.close()
        state, ok, skipped, fail, errors = _replay_durable(
            events,
            target_file,
            max_receipts_per_order=contract.max_receipts_per_order,
        )
    else:
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
