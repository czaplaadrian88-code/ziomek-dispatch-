import json
import sqlite3
import gzip
import os
import argparse
import datetime
import statistics
import math
import sys
from zoneinfo import ZoneInfo

EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"
LEARNING_LOGS = [
    "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl",
    "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl.1",
    "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl.2.gz",
]
OUT_JSONL = "/root/.openclaw/workspace/dispatch_state/decision_outcomes.jsonl"

def _parse_ts(ts: str):
    """Parsuj ISO UTC; zamień końcówkę 'Z' na '+00:00'."""
    if ts is None:
        return None
    s = ts.strip()
    if not s:
        return None
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt

def _minutes(a_iso, b_iso):
    """Różnica (a-b) w minutach; None gdy którykolwiek brak."""
    dt_a = _parse_ts(a_iso)
    dt_b = _parse_ts(b_iso)
    if dt_a is None or dt_b is None:
        return None
    return (dt_a - dt_b).total_seconds() / 60.0

def _pickup_lateness_min(picked_up_at, czas_kuriera_warsaw, order_created_at):
    """Opóźnienie odbioru względem planowanego czasu kuriera."""
    if not picked_up_at or not czas_kuriera_warsaw or not order_created_at:
        return None
    try:
        picked_dt = _parse_ts(picked_up_at)
        created_dt = _parse_ts(order_created_at)
        if picked_dt is None or created_dt is None:
            return None
        warsaw = ZoneInfo("Europe/Warsaw")
        warsaw_dt = created_dt.astimezone(warsaw)
        h, m = map(int, czas_kuriera_warsaw.split(':'))
        target_naive = datetime.datetime.combine(warsaw_dt.date(), datetime.time(h, m))
        target_warsaw = target_naive.replace(tzinfo=warsaw)
        target_utc = target_warsaw.astimezone(datetime.timezone.utc)
        diff = (picked_dt - target_utc).total_seconds() / 60.0
        return diff
    except Exception:
        return None

def load_decisions():
    """Wczytaj wszystkie dzienniki uczenia i wybierz najlepszy rekord decyzji per order_id."""
    best = {}  # order_id -> (prio, ts_dt, record)
    priority_map = {
        "PANEL_OVERRIDE": 0,
        "PANEL_AGREE": 0,
        "ASSIGN_DIRECT": 0,
        "F7AGREE": 0,
        "TIMEOUT_SUPERSEDED": 1,
    }

    for fpath in LEARNING_LOGS:
        if not os.path.isfile(fpath):
            continue
        if fpath.endswith('.gz'):
            f = gzip.open(fpath, 'rt', encoding='utf-8')
        else:
            f = open(fpath, 'r', encoding='utf-8')
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                oid = rec.get('order_id')
                if not oid:
                    continue
                action = rec.get('action')
                prio = priority_map.get(action, 2)
                ts_dt = _parse_ts(rec.get('ts'))
                if ts_dt is None:
                    ts_dt = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
                if oid not in best:
                    best[oid] = (prio, ts_dt, rec)
                else:
                    cur_prio, cur_ts, _ = best[oid]
                    if prio < cur_prio or (prio == cur_prio and ts_dt > cur_ts):
                        best[oid] = (prio, ts_dt, rec)
        finally:
            f.close()

    out = {}
    for oid, (_, _, rec) in best.items():
        out[oid] = {
            'ts_decision': rec.get('ts'),
            'proposed_cid': str(rec['proposed_courier_id']) if rec.get('proposed_courier_id') not in (None,'') else None,
            'proposed_score': rec.get('proposed_score'),
            'actual_cid': str(rec['actual_courier_id']) if rec.get('actual_courier_id') not in (None,'') else None,
            'action': rec.get('action'),
        }
    return out

def load_outcomes(events_db):
    """Zbierz informacje o przebiegu dostawy dla ukończonych zleceń."""
    conn = sqlite3.connect(events_db)
    try:
        cur = conn.cursor()

        # NEW_ORDER
        cur.execute("SELECT order_id, created_at, payload FROM events WHERE event_type='NEW_ORDER'")
        new_order = {}
        for oid, created, payload_str in cur.fetchall():
            czas = None
            if payload_str:
                try:
                    pl = json.loads(payload_str)
                    czas = pl.get('czas_kuriera_warsaw')
                except Exception:
                    pass
            new_order[oid] = (created, czas)

        # DELIVERED (najwcześniejszy)
        cur.execute("SELECT order_id, MIN(created_at) FROM events WHERE event_type='COURIER_DELIVERED' GROUP BY order_id")
        delivered = {oid: created for oid, created in cur.fetchall()}

        # PICKED_UP (najwcześniejszy, razem z kurierem)
        cur.execute("""
            SELECT e.order_id, e.created_at, e.courier_id
            FROM events e
            JOIN (
                SELECT order_id, MIN(created_at) AS min_ca
                FROM events
                WHERE event_type='COURIER_PICKED_UP'
                GROUP BY order_id
            ) sub ON e.order_id = sub.order_id AND e.created_at = sub.min_ca
            WHERE e.event_type='COURIER_PICKED_UP'
        """)
        pickups = {}
        for oid, created, courier in cur.fetchall():
            pickups[oid] = (created, courier)

        outcomes = {}
        for oid in delivered:
            created, czas = new_order.get(oid, (None, None))
            picked_up_at, picked_up_courier = pickups.get(oid, (None, None))
            outcomes[oid] = {
                'order_created_at': created,
                'czas_kuriera_warsaw': czas,
                'picked_up_at': picked_up_at,
                'picked_up_courier': picked_up_courier,
                'delivered_at': delivered[oid],
            }
        return outcomes
    finally:
        conn.close()

def join_and_compute(decisions, outcomes):
    """Połącz decyzje i wyniki, oblicz metryki, zwróć listę rekordów."""
    records = []
    for oid, out in outcomes.items():
        dec = decisions.get(oid)
        ts_decision = dec['ts_decision'] if dec else None
        proposed_cid = dec['proposed_cid'] if dec else None
        proposed_score = dec['proposed_score'] if dec else None
        actual_cid = dec['actual_cid'] if dec else None
        action = dec['action'] if dec else None

        if proposed_cid is not None and actual_cid is not None:
            verdict = 'agree' if proposed_cid == actual_cid else 'override'
        else:
            verdict = 'no_verdict'

        order_created_at = out['order_created_at']
        czas_kuriera_warsaw = out['czas_kuriera_warsaw']
        picked_up_at = out['picked_up_at']
        picked_up_courier = out['picked_up_courier']
        delivered_at = out['delivered_at']

        r6 = _minutes(delivered_at, picked_up_at)
        r6_breach = (r6 is not None and r6 > 35.0)
        pl = _pickup_lateness_min(picked_up_at, czas_kuriera_warsaw, order_created_at)

        rec = {
            'order_id': oid,
            'ts_decision': ts_decision,
            'proposed_cid': proposed_cid,
            'proposed_score': proposed_score,
            'actual_cid': actual_cid,
            'action': action,
            'verdict': verdict,
            'order_created_at': order_created_at,
            'czas_kuriera_warsaw': czas_kuriera_warsaw,
            'picked_up_at': picked_up_at,
            'picked_up_courier': picked_up_courier,
            'delivered_at': delivered_at,
            'r6_actual_min': r6,
            'r6_breach': r6_breach,
            'pickup_lateness_min': pl,
            'written_at': None,
        }
        records.append(rec)
    return records

def _existing_ids(path):
    """Odczytaj zbiór order_id już zapisanych w pliku wyjściowym."""
    ids = set()
    if not os.path.isfile(path):
        return ids
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    oid = obj.get('order_id')
                    if oid:
                        ids.add(oid)
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return ids

def append_jsonl(records, path):
    """Dopisz nowe rekordy do pliku JSONL, zwróć liczbę dodanych."""
    existing = _existing_ids(path)
    count = 0
    try:
        with open(path, 'a', encoding='utf-8') as f:
            for rec in records:
                oid = rec['order_id']
                if oid in existing:
                    continue
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                rec['written_at'] = now
                line = json.dumps(rec, ensure_ascii=False)
                f.write(line + '\n')
                f.flush()
                os.fsync(f.fileno())
                count += 1
    except Exception as e:
        print(f"Błąd podczas dopisywania: {e}", file=sys.stderr)
    return count

def print_stats(records):
    """Wypisz statystyki na podstawie wszystkich rekordów."""
    n = len(records)
    if n == 0:
        print("Brak rekordów do podsumowania.")
        return
    print(f"Liczba wszystkich rekordów: {n}")

    vcount = {'agree': 0, 'override': 0, 'no_verdict': 0}
    for r in records:
        v = r.get('verdict', 'no_verdict')
        vcount[v] = vcount.get(v, 0) + 1

    print("Rozkład werdyktów:")
    for v in ('agree', 'override', 'no_verdict'):
        cnt = vcount.get(v, 0)
        pct = cnt / n * 100 if n else 0.0
        print(f"  {v}: {cnt} ({pct:.1f}%)")

    agr = vcount.get('agree', 0)
    ovr = vcount.get('override', 0)
    if agr + ovr > 0:
        agreement = agr / (agr + ovr) * 100
        print(f"Agreement rate (agree/(agree+override)): {agreement:.1f}%")
    else:
        print("Agreement rate: N/A (brak agree/override)")

    r6_vals = []
    breach = 0
    total_r6 = 0
    for r in records:
        v = r.get('r6_actual_min')
        if v is not None:
            total_r6 += 1
            r6_vals.append(v)
            if v > 35.0:
                breach += 1
    if total_r6:
        breach_pct = breach / total_r6 * 100
        print(f"R6 breach (>35 min): {breach} ({breach_pct:.1f}% z {total_r6})")
        r6_vals.sort()
        med = statistics.median(r6_vals)
        idx90 = max(0, min(math.ceil(0.9 * len(r6_vals)) - 1, len(r6_vals)-1))
        p90 = r6_vals[idx90]
        print(f"R6 actual min  – mediana: {med:.1f}, p90: {p90:.1f}")
    else:
        print("Brak wartości r6_actual_min.")

    pickup_vals = [r['pickup_lateness_min'] for r in records if r.get('pickup_lateness_min') is not None]
    print(f"Rekordów z pickup_lateness_min: {len(pickup_vals)}")
    if pickup_vals:
        print(f"Mediana pickup_lateness_min: {statistics.median(pickup_vals):.1f} min")

def main():
    parser = argparse.ArgumentParser(description="Narzędzie offline do budowy datasetu decision_outcomes.")
    parser.add_argument('--backfill', action='store_true', help="przetwórz całą historię (domyślnie)")
    parser.add_argument('--since', type=str, default=None, help="tylko rekordy z delivered_at >= SINCE (ISO UTC)")
    parser.add_argument('--dry-run', action='store_true', help="nie zapisuj, wypisz liczbę nowych rekordów")
    parser.add_argument('--stats', action='store_true', help="wypisz statystyki po przetworzeniu")
    args = parser.parse_args()

    decisions = load_decisions()
    outcomes = load_outcomes(EVENTS_DB)
    records = join_and_compute(decisions, outcomes)

    if args.since:
        since_dt = _parse_ts(args.since)
        if since_dt is None:
            print("Nieprawidłowy format --since", file=sys.stderr)
            return 1
        filtered = []
        for r in records:
            dt = _parse_ts(r.get('delivered_at'))
            if dt is not None and dt >= since_dt:
                filtered.append(r)
        records = filtered

    if args.dry_run:
        existing = _existing_ids(OUT_JSONL)
        new = sum(1 for r in records if r['order_id'] not in existing)
        print(f"Zostałoby dopisanych {new} nowych rekordów.")
        if args.stats:
            print_stats(records)
        return 0

    added = append_jsonl(records, OUT_JSONL)
    print(f"Dopisano {added} nowych rekordów do {OUT_JSONL}")

    if args.stats:
        print_stats(records)

    return 0

if __name__ == '__main__':
    sys.exit(main())
