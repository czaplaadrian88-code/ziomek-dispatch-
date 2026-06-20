#!/usr/bin/env python3
"""sla_join_worker — joinuje dostawy↔decyzje i materializuje `sla_log.jsonl`.

Cel: stworzyć log który DZIŚ nie istnieje (dokumentuje to `daily_briefing.py`),
żeby dało się policzyć % dostaw na czas (≤35 min). To enabler — bez tego cała
roadmapa SLA stroi na ślepo.

Co robi:
  1. Buduje indeksy z żywych logów (patrz ontime_lib.build_indices):
       dostawy   <- backfill_decisions_outcomes_v1.jsonl  (outcome.delivered_ts)
       ready     <- learning_log.jsonl (+ zrotowany .1)   (pickup_ready_at)
  2. Dla każdej ZAMKNIĘTEJ dostawy (delivered_ts wypełnione) w oknie
     --since-days liczy on_time przez wspólny kontrakt compute_on_time.
  3. Dopisuje rekordy do sla_log.jsonl IDEMPOTENTNIE po order_id (powtórny bieg
     nie dubluje; jeśli istniejący rekord ma inny delivered_at → aktualizuje).
  4. Raportuje: pokrycie (% zamkniętych dostaw z policzonym on_time; cel ≥95%),
     realne % on-time (peak vs off-peak), medianę delivery_time, oraz spójność
     z r6_breach_shadow.jsonl (czy ordery R6_HARD_REJECT mają sensowny on_time).

Strefa czasowa: timestampy w logach są UTC; delivery_time_minutes jest różnicą
chwil (niezależną od strefy), a peak/off-peak liczone po czasie Europe/Warsaw
(ontime_lib). Naiwne/null znaczniki naprawiane fail-soft (parse_ts).

Użycie:
    python3 tools/sla_join_worker.py --since-days 30 \
        --out /root/.openclaw/workspace/dispatch_state/sla_log.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from datetime import datetime, timezone, timedelta

# Import współdzielonego kontraktu. Działa zarówno gdy uruchamiane jako
# `python3 tools/sla_join_worker.py` (cwd=dispatch_v2) jak i jako moduł.
try:
    from tools import ontime_lib  # type: ignore
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ontime_lib  # type: ignore

DISPATCH_STATE_DIR = ontime_lib.DISPATCH_STATE_DIR
DEFAULT_OUT = f"{DISPATCH_STATE_DIR}/sla_log.jsonl"
R6_BREACH_LOG = f"{DISPATCH_STATE_DIR}/r6_breach_shadow.jsonl"

# Wersja schematu rekordu sla_log — bumpnij gdy zmieni się kontrakt pól.
SLA_LOG_SCHEMA_VERSION = 1


def _load_existing(out_path: str) -> dict:
    """Wczytuje istniejący sla_log.jsonl → order_id(str) -> rekord (idempotencja)."""
    existing: dict = {}
    if not os.path.exists(out_path):
        return existing
    for rec in ontime_lib._iter_jsonl(out_path):
        oid = rec.get("order_id")
        if oid is not None:
            existing[str(oid)] = rec
    return existing


def _atomic_write_jsonl(out_path: str, records: list) -> None:
    """Atomowy zapis całego logu (tmp + os.replace) — bez połówkowego pliku."""
    d = os.path.dirname(out_path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".sla_log.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _load_r6_reject_oids(path: str) -> set:
    """Zbiór order_id z eventem R6_HARD_REJECT (do kontroli spójności)."""
    oids: set = set()
    for rec in ontime_lib._iter_jsonl(path):
        if rec.get("event_type") == "R6_HARD_REJECT":
            oid = rec.get("new_order_id") or rec.get("worst_oid")
            if oid is not None:
                oids.add(str(oid))
    return oids


def run(
    out_path: str,
    since_days: int,
    decision_paths=None,
    delivery_paths=None,
    now: datetime | None = None,
) -> dict:
    """Główna logika. Zwraca słownik metryk (dla CLI i testów)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=since_days) if since_days and since_days > 0 else None

    dec_idx, deliv_idx = ontime_lib.build_indices(
        decision_paths=decision_paths,
        delivery_paths=delivery_paths,
        since=since,
    )

    # Mianownik pokrycia = wszystkie ZAMKNIĘTE dostawy w oknie.
    closed_oids = list(deliv_idx.keys())

    existing = _load_existing(out_path)
    # Kopia by zachować rekordy spoza bieżącego okna (idempotencja nie kasuje historii).
    merged = dict(existing)

    computed = 0
    grace = 0
    negative = 0
    on_time_vals = []        # (delivery_min, on_time_bool, is_peak)
    written_new = 0
    updated = 0

    run_ts = now.isoformat()
    for oid in closed_oids:
        res = ontime_lib.compute_on_time(oid, dec_idx, deliv_idx)
        rec = dict(res)
        rec["schema_version"] = SLA_LOG_SCHEMA_VERSION
        rec["sla_threshold_min"] = ontime_lib.ON_TIME_THRESHOLD_MIN
        rec["computed_at"] = run_ts

        prev = existing.get(oid)
        if prev is None:
            written_new += 1
        else:
            # idempotencja: ten sam delivered_at → bez zmiany licznika „updated"
            if prev.get("delivered_at") != rec.get("delivered_at") or \
               prev.get("delivery_time_minutes") != rec.get("delivery_time_minutes"):
                updated += 1
        merged[oid] = rec

        if res["grace"]:
            grace += 1
        elif res["on_time"] is not None:
            computed += 1
            on_time_vals.append(
                (res["delivery_time_minutes"], res["on_time"], res["is_peak"])
            )
            if res["reason"] == "negative_delivery_time":
                negative += 1

    # Zapis: stabilna kolejność po delivered_at (None na końcu), potem order_id.
    def _sortkey(r):
        return (r.get("delivered_at") or "9999", str(r.get("order_id")))
    records_out = sorted(merged.values(), key=_sortkey)
    _atomic_write_jsonl(out_path, records_out)

    # --- Metryki ---
    n_closed = len(closed_oids)
    coverage = (computed + grace) / n_closed if n_closed else 0.0
    # „policzony on_time" = nie-grace (mamy realną liczbę). Pokrycie SLA sensu
    # stricte = computed / n_closed (te z realnym werdyktem on/off-time).
    coverage_strict = computed / n_closed if n_closed else 0.0

    dmins = [v[0] for v in on_time_vals]
    on_cnt = sum(1 for v in on_time_vals if v[1])
    on_rate = on_cnt / len(on_time_vals) if on_time_vals else None

    peak = [v for v in on_time_vals if v[2] is True]
    off = [v for v in on_time_vals if v[2] is False]
    peak_rate = (sum(1 for v in peak if v[1]) / len(peak)) if peak else None
    off_rate = (sum(1 for v in off if v[1]) / len(off)) if off else None

    median_dt = statistics.median(dmins) if dmins else None
    mean_dt = statistics.mean(dmins) if dmins else None

    # Spójność z r6_breach_shadow: ordery R6_HARD_REJECT, które mamy w sla_log,
    # powinny mieć (a) sensowny on_time (nie None/grace) i (b) tendencyjnie
    # gorszy on-time niż reszta (bo projekcja czasu w worku przekraczała limit).
    r6_oids = _load_r6_reject_oids(R6_BREACH_LOG)
    r6_in_sla = [
        merged[o] for o in r6_oids
        if o in merged and merged[o].get("on_time") is not None
    ]
    r6_on_rate = (
        sum(1 for r in r6_in_sla if r.get("on_time")) / len(r6_in_sla)
        if r6_in_sla else None
    )
    r6_with_value = len(r6_in_sla)
    r6_total_in_sla = sum(1 for o in r6_oids if o in merged)

    metrics = {
        "out_path": out_path,
        "since_days": since_days,
        "now": run_ts,
        "n_closed_deliveries": n_closed,
        "n_with_ontime_value": computed,
        "n_grace": grace,
        "n_negative_delivery_time": negative,
        "coverage_incl_grace": round(coverage, 4),
        "coverage_strict": round(coverage_strict, 4),
        "records_written_new": written_new,
        "records_updated": updated,
        "records_total_in_log": len(records_out),
        "on_time_rate": round(on_rate, 4) if on_rate is not None else None,
        "on_time_rate_peak": round(peak_rate, 4) if peak_rate is not None else None,
        "on_time_rate_offpeak": round(off_rate, 4) if off_rate is not None else None,
        "n_peak": len(peak),
        "n_offpeak": len(off),
        "median_delivery_time_min": round(median_dt, 2) if median_dt is not None else None,
        "mean_delivery_time_min": round(mean_dt, 2) if mean_dt is not None else None,
        "r6_reject_oids_total": len(r6_oids),
        "r6_reject_in_sla_log": r6_total_in_sla,
        "r6_reject_with_ontime_value": r6_with_value,
        "r6_reject_on_time_rate": round(r6_on_rate, 4) if r6_on_rate is not None else None,
    }
    return metrics


def _fmt_pct(x):
    return f"{100*x:.1f}%" if isinstance(x, (int, float)) else "n/a"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since-days", type=int, default=30,
                    help="okno wstecz po dacie doręczenia (domyślnie 30)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"ścieżka sla_log.jsonl (domyślnie {DEFAULT_OUT})")
    ap.add_argument("--decision-log", action="append", default=None,
                    help="nadpisz log(i) decyzji (można podać wielokrotnie, "
                         "OD NAJSTARSZEGO do NAJNOWSZEGO)")
    ap.add_argument("--delivery-log", action="append", default=None,
                    help="nadpisz log(i) dostaw")
    ap.add_argument("--json", action="store_true",
                    help="wypisz metryki jako JSON (do automatyzacji)")
    args = ap.parse_args(argv)

    m = run(
        out_path=args.out,
        since_days=args.since_days,
        decision_paths=args.decision_log,
        delivery_paths=args.delivery_log,
    )

    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0

    print("=" * 64)
    print(f"SLA JOIN WORKER — okno {args.since_days} dni")
    print("=" * 64)
    print(f"sla_log:                  {m['out_path']}")
    print(f"zamknięte dostawy (okno): {m['n_closed_deliveries']}")
    print(f"  z policzonym on_time:   {m['n_with_ontime_value']}")
    print(f"  grace (brak ready_at):  {m['n_grace']}")
    print(f"  ujemny czas (DQ flag):  {m['n_negative_delivery_time']}")
    print(f"POKRYCIE (z policzonym werdyktem): {_fmt_pct(m['coverage_strict'])}"
          f"   [cel ≥95%]  {'OK' if m['coverage_strict'] >= 0.95 else 'PONIŻEJ'}")
    print(f"POKRYCIE (łącznie z grace):       {_fmt_pct(m['coverage_incl_grace'])}")
    print(f"zapisane nowe / zaktualizowane:   {m['records_written_new']} / {m['records_updated']}")
    print(f"rekordów w logu łącznie:          {m['records_total_in_log']}")
    print("-" * 64)
    print(f"REALNE % ON-TIME (≤35 min):       {_fmt_pct(m['on_time_rate'])}")
    print(f"  peak    (n={m['n_peak']}):           {_fmt_pct(m['on_time_rate_peak'])}")
    print(f"  off-peak(n={m['n_offpeak']}):           {_fmt_pct(m['on_time_rate_offpeak'])}")
    print(f"mediana delivery_time:            {m['median_delivery_time_min']} min")
    print(f"średni  delivery_time:            {m['mean_delivery_time_min']} min")
    print("-" * 64)
    print("Spójność z r6_breach_shadow (R6_HARD_REJECT):")
    print(f"  R6 reject oids łącznie:         {m['r6_reject_oids_total']}")
    print(f"  z nich w sla_log:               {m['r6_reject_in_sla_log']}")
    print(f"  z policzonym on_time:           {m['r6_reject_with_ontime_value']}")
    print(f"  on-time rate (R6 reject):       {_fmt_pct(m['r6_reject_on_time_rate'])}"
          f"   (oczek. NIŻSZY niż ogólny {_fmt_pct(m['on_time_rate'])})")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
