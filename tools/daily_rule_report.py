#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dzienny raport metryk reguł Ziomka (offline, READ-ONLY).

L1.2 (2026-07-01): odczyt shadow/outcomes przepięty na kanon `ledger_io`
(rotation-aware) — stary odczyt TYLKO żywych plików po rotacji (logrotate
size 100M / daily) po cichu przycinał okno raportu (np. --tail-days 21 widział
tylko dni od ostatniej rotacji). Metryki/format raportu BEZ ZMIAN.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

try:
    from dispatch_v2.tools import ledger_io
except ImportError:  # uruchomienie z katalogu tools/
    _PKG_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _PKG_PARENT not in sys.path:
        sys.path.insert(0, _PKG_PARENT)
    from dispatch_v2.tools import ledger_io

OUTCOMES = ledger_io.LEDGER["outcomes"]
OUT_DEFAULT = "/root/.openclaw/workspace/scripts/logs/reports/daily_rule_report.json"


def _gini(values: list) -> float:
    """Gini coefficient – identyczny wzór jak w sequential_replay."""
    if not values:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    total = sum(vals)
    if n <= 1 or total == 0:
        return 0.0
    num = 2.0 * sum((i + 1) * x for i, x in enumerate(vals))
    return num / (n * total) - (n + 1) / n


def load_shadow_df(since=None, days=None):
    """Strumieniowe wczytanie shadow_decisions.jsonl -> DataFrame z datą Warsaw."""
    warsaw_tz = ZoneInfo("Europe/Warsaw")

    def _parse_date(ts_str):
        try:
            s = ts_str
            if isinstance(s, str) and s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            loc = dt.astimezone(warsaw_tz)
            return loc.strftime("%Y-%m-%d")
        except Exception:
            return None

    # cutoff (pruning całych plików + per-rekord w kanonie): północ `since` Warsaw.
    # Per-rekord filtr dat Warsaw niżej zostaje autorytatywny (identyczna semantyka).
    cutoff_dt = None
    if since is not None:
        try:
            cutoff_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=warsaw_tz)
        except ValueError:
            cutoff_dt = None

    records = []
    for data in ledger_io.iter_shadow_decisions(cutoff_dt):
        ts_val = data.get("ts")
        if ts_val is None:
            continue
        date_w = _parse_date(ts_val)
        if date_w is None:
            continue
        # stream-filtr: stare dni odrzucamy JUZ tu (pamiec — 106 MB pliku)
        if since is not None and date_w < since:
            continue

        order_id = str(data["order_id"]) if data.get("order_id") is not None else None
        verdict = data.get("verdict")
        latency_ms = data.get("latency_ms")
        pool_feasible_count = data.get("pool_feasible_count")
        pool_total_count = data.get("pool_total_count")

        best = data.get("best") if isinstance(data.get("best"), dict) else None
        if best and isinstance(best, dict):
            best_courier_id = str(best["courier_id"]) if best.get("courier_id") is not None else None
            best_score = best.get("score")
            best_r6_pred = best.get("r6_max_bag_time_min")
            best_effort = bool(best.get("best_effort")) if best.get("best_effort") is not None else False
        else:
            best_courier_id = best_score = best_r6_pred = None
            best_effort = False

        records.append({
            "date": date_w,
            "order_id": order_id,
            "verdict": verdict,
            "latency_ms": latency_ms,
            "pool_feasible_count": pool_feasible_count,
            "pool_total_count": pool_total_count,
            "best_courier_id": best_courier_id,
            "best_score": best_score,
            "best_r6_pred": best_r6_pred,
            "best_effort": best_effort,
        })

    df = pd.DataFrame(records)
    if df.empty:
        return df

    if since is not None:
        df = df[df["date"] >= since]
    if days is not None:
        unique_dates = sorted(df["date"].unique())
        if len(unique_dates) > days:
            df = df[df["date"].isin(unique_dates[-days:])]
    return df


def load_outcomes_map():
    """Wczytaj decision_outcomes (kanon ledger_io, rotation-aware)
    -> {order_id: r6_actual_min} (ostatni rekord per oid wygrywa, jak dotąd)."""
    outcomes = {}
    if not os.path.exists(OUTCOMES):
        print("Warning: outcomes file not found", file=sys.stderr)
        return outcomes
    for oid, data in ledger_io.load_outcomes(None).items():
        val = data.get("r6_actual_min")
        outcomes[str(oid)] = val if isinstance(val, (int, float)) else None
    return outcomes


def build_report(df, outcomes_map):
    """Buduje listę dziennych rekordów z metrykami."""
    rows = []
    for d in sorted(df["date"].unique()):
        day = df[df["date"] == d]
        n = len(day)
        if n == 0:
            continue

        vc = day["verdict"].value_counts().to_dict()
        koord_n = vc.get("KOORD", 0)
        propose_n = vc.get("PROPOSE", 0)
        skip_n = vc.get("SKIP", 0)

        koord_rate = koord_n / n
        propose_rate = propose_n / n
        skip_rate = skip_n / n

        lat = day["latency_ms"].dropna()
        if not lat.empty:
            q = lat.quantile([0.50, 0.95, 0.99]).to_dict()
            p50, p95, p99 = q.get(0.50), q.get(0.95), q.get(0.99)
        else:
            p50 = p95 = p99 = None

        zero_feasible_rate = (day["pool_feasible_count"] == 0).sum() / n

        propose_df = day[day["verdict"] == "PROPOSE"]
        if not propose_df.empty:
            be_rate = propose_df["best_effort"].fillna(False).mean()
            r6p = propose_df["best_r6_pred"].dropna()
            if not r6p.empty:
                r6_pred_med = r6p.median()
                r6_pred_p90 = r6p.quantile(0.90)
                r6_pred_over35 = (r6p > 35).mean()
            else:
                r6_pred_med = r6_pred_p90 = r6_pred_over35 = None

            courier = propose_df["best_courier_id"].dropna()
            cnt = courier.value_counts()
            couriers_used = len(cnt)
            if couriers_used:
                max_pile = cnt.max() / cnt.sum()
                gini = _gini(list(cnt.values))
            else:
                max_pile = 0.0
                gini = 0.0

            # actual outcomes matching
            propose_oids = propose_df["order_id"].dropna().tolist()
            matched = [outcomes_map[str(oid)] for oid in propose_oids if outcomes_map.get(str(oid)) is not None]
            n_matched = len(matched)
            if n_matched:
                r6_act_med = pd.Series(matched).median()
                r6_act_breach = sum(1 for v in matched if v > 35) / n_matched
            else:
                r6_act_med = r6_act_breach = None
        else:
            be_rate = r6_pred_med = r6_pred_p90 = r6_pred_over35 = None
            couriers_used = 0
            max_pile = 0.0
            gini = 0.0
            n_matched = 0
            r6_act_med = r6_act_breach = None

        def _r4(v):
            return round(v, 4) if v is not None else None

        def _r1(v):
            return round(v, 1) if v is not None else None

        rows.append({
            "date": d,
            "n": n,
            "verdict_counts": {"KOORD": koord_n, "PROPOSE": propose_n, "SKIP": skip_n},
            "koord_rate": _r4(koord_rate),
            "propose_rate": _r4(propose_rate),
            "skip_rate": _r4(skip_rate),
            "latency_p50": _r1(p50),
            "latency_p95": _r1(p95),
            "latency_p99": _r1(p99),
            "zero_feasible_rate": _r4(zero_feasible_rate),
            "best_effort_rate": _r4(be_rate),
            "r6_pred_median": _r1(r6_pred_med),
            "r6_pred_p90": _r1(r6_pred_p90),
            "r6_pred_over35_rate": _r4(r6_pred_over35),
            "fleet_couriers_used": couriers_used,
            "fleet_max_pile_share": _r4(max_pile) if max_pile is not None else None,
            "fleet_gini_load": round(gini, 4) if gini is not None else None,
            "n_matched": n_matched,
            "r6_actual_median": _r1(r6_act_med),
            "r6_actual_breach_rate": _r4(r6_act_breach),
        })
    return rows


def print_report(rows):
    """Tabela dzienna."""
    if not rows:
        print("No data")
        return
    header = (f"{'date':<12} {'n':>5} {'KOORD%':>7} {'p95ms':>8} {'BE%':>6} "
              f"{'R6pred>35%':>10} {'gini':>8} {'R6act_breach%':>13} {'matched':>8}")
    print(header)
    for row in rows:
        d = row["date"]
        n = row["n"]
        koord_s = f"{row['koord_rate']*100:.1f}%" if row["koord_rate"] is not None else "-"
        p95_s = f"{row['latency_p95']:8.1f}" if row["latency_p95"] is not None else "-"
        be_s = f"{row['best_effort_rate']*100:.1f}%" if row["best_effort_rate"] is not None else "-"
        r6po_s = f"{row['r6_pred_over35_rate']*100:.1f}%" if row["r6_pred_over35_rate"] is not None else "-"
        gini_s = f"{float(row['fleet_gini_load']):.4f}" if row["fleet_gini_load"] is not None else "-"
        breach_s = f"{row['r6_actual_breach_rate']*100:.1f}%" if row["r6_actual_breach_rate"] is not None else "-"
        matched_s = row["n_matched"]
        print(f"{d:<12} {n:5d} {koord_s:>7} {p95_s:>8} {be_s:>6} {r6po_s:>10} "
              f"{gini_s:>8} {breach_s:>13} {matched_s:8d}")


def save_json(rows, path):
    """Zapis JSON do pliku (tworzy katalog)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1, ensure_ascii=False, default=str)


def main():
    parser = argparse.ArgumentParser(description="Dzienny raport reguł Ziomka")
    parser.add_argument("--since", type=str, default=None, help="YYYY-MM-DD – zostaw daty od (włącznie)")
    parser.add_argument("--days", type=int, default=None, help="Liczba ostatnich dni (różnych dat)")
    parser.add_argument("--out", type=str, default=OUT_DEFAULT)
    parser.add_argument("--no-save", action="store_true", help="Nie zapisuj JSON, tylko drukuj")
    parser.add_argument("--tail-days", type=int, default=None,
                        help="kroczace okno N ostatnich dni (od dzis, Warsaw) — pamieciowo bezpieczne dla timera")
    args = parser.parse_args()

    since = args.since
    if since is None and args.tail_days is not None:
        since = (datetime.now(ZoneInfo("Europe/Warsaw")) - timedelta(days=args.tail_days)).strftime("%Y-%m-%d")

    df = load_shadow_df(since=since, days=args.days)
    outcomes = load_outcomes_map()
    rows = build_report(df, outcomes)
    print_report(rows)

    if not args.no_save:
        save_json(rows, args.out)
        print(f"Report saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
