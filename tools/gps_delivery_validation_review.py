#!/usr/bin/env python3
"""gps_delivery_validation_review — UCZCIWA LINIJKA dostawy (#5b geofence GPS).

Łączy FIZYCZNY przyjazd kuriera (z GPS, liczony przez `customer_dwell_detector`
→ customer_dwell.json, pole `arrived_at_customer`, _source=gps_geofence) z
PRZYCISKOWYM `delivered_at` (klik kuriera / skrap panelu w orders_state) i:

  1. pisze KONSUMOWALNY artefakt `gps_delivery_truth.jsonl` — per zlecenie
     fizyczny `physical_delivered_at` + delta klik-vs-fizyka + pewność. Z tego
     korzystają przyszłe werdykty (#2 bundle-calib, feas-carry) zamiast ufać
     samemu klikowi (prawda-przyciskowa ± bias fizyczny ~2 min, ORACLE-CAVEAT).
  2. liczy KALIBRACJĘ linijki — coverage (ile dostaw ma fizyczną prawdę),
     mediana/p90 delty „klik − fizyczny przyjazd", % |delta|>3min, % klik-przed.

READ-ONLY na customer_dwell.json + orders_state.json. Pisze WYŁĄCZNIE własne
artefakty (atomic temp+fsync+rename). Stdlib only. Zero wpływu na decyzje Ziomka.

Fundament #5b: rozbraja ORACLE-CAVEAT „delivered_at = prawda-przyciskowa, 0/377
fizycznych GT" — daje fizyczne GT per zlecenie + zmierzony bias przycisku.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WAW = ZoneInfo("Europe/Warsaw")
CUSTOMER_DWELL = "/root/.openclaw/workspace/dispatch_state/customer_dwell.json"
ORDERS_STATE = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
OUT_TRUTH = "/root/.openclaw/workspace/dispatch_state/gps_delivery_truth.jsonl"
OUT_VERDICT = "/root/.openclaw/workspace/dispatch_state/gps_delivery_validation_verdict.txt"

HIGH_CONF_MIN_NIN = 2  # n_in>=2 punkty w geofence = wysoka pewność przyjazdu


def to_epoch(ts) -> float | None:
    """ISO z offsetem/Z → epoch; naive 'YYYY-MM-DD HH:MM:SS' → Warsaw → epoch."""
    if not ts:
        return None
    s = str(ts).strip()
    try:
        if "+" in s or s.endswith("Z") or (("T" in s) and s[-6] in "+-"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        return datetime.strptime(s.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=WAW).timestamp()
    except Exception:
        return None


def iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(p / 100 * len(sorted_vals)))]


def main() -> int:
    ap = argparse.ArgumentParser(description="Linijka dostawy: fizyczny GPS vs klik (#5b)")
    ap.add_argument("--customer-dwell", default=CUSTOMER_DWELL)
    ap.add_argument("--orders-state", default=ORDERS_STATE)
    ap.add_argument("--out-truth", default=OUT_TRUTH)
    ap.add_argument("--out-verdict", default=OUT_VERDICT)
    ap.add_argument("--since", default=None, help="tylko delivered_day >= YYYY-MM-DD")
    ap.add_argument("--write", action="store_true", help="zapisz artefakty (domyślnie dry-run)")
    args = ap.parse_args()

    with open(args.customer_dwell, encoding="utf-8") as f:
        dwell = json.load(f)
    with open(args.orders_state, encoding="utf-8") as f:
        state = json.load(f)

    truth_rows = []
    deltas = []          # min, + = klik PO fizycznym przyjeździe (przycisk zawyża)
    deltas_high = []     # tylko wysoka pewność (n_in>=2)
    for oid, v in dwell.items():
        if not isinstance(v, dict) or v.get("_source") != "gps_geofence":
            continue
        if not v.get("arrived_at_customer"):
            continue
        delivered_day = v.get("delivered_day") or ""
        if args.since and delivered_day and delivered_day < args.since:
            continue
        physical = to_epoch(v.get("arrived_at_customer"))
        if physical is None:
            continue
        o = state.get(oid) if isinstance(state.get(oid), dict) else {}
        button = to_epoch(o.get("delivered_at")) or to_epoch(v.get("delivered_at"))
        n_in = v.get("_n_in_geofence") or 0
        min_dist = v.get("_min_dist_m")
        radius = v.get("_radius_m")
        high_conf = bool(n_in >= HIGH_CONF_MIN_NIN and (min_dist is None or radius is None or min_dist <= radius))
        delta_min = round((button - physical) / 60.0, 2) if button else None
        row = {
            "order_id": oid,
            "courier_id": v.get("courier_id"),
            "delivery_address": v.get("delivery_address"),
            "physical_delivered_at": iso_utc(physical),
            "button_delivered_at": (iso_utc(button) if button else None),
            "delta_button_minus_physical_min": delta_min,
            "dwell_min": v.get("dwell_min"),
            "n_in_geofence": n_in,
            "min_dist_m": min_dist,
            "confidence": "high" if high_conf else "low",
            "delivered_day": delivered_day,
        }
        truth_rows.append(row)
        if delta_min is not None:
            deltas.append(delta_min)
            if high_conf:
                deltas_high.append(delta_min)

    # coverage: ile dostaw w oknie ma fizyczną prawdę
    delivered_in_window = 0
    for oid, o in state.items():
        if not isinstance(o, dict):
            continue
        b = to_epoch(o.get("delivered_at"))
        if b is None:
            continue
        day = datetime.fromtimestamp(b, WAW).strftime("%Y-%m-%d")
        if args.since and day < args.since:
            continue
        delivered_in_window += 1

    deltas.sort()
    deltas_high.sort()
    n = len(deltas)
    # Coverage ma sens TYLKO w oknie --since: orders_state jest przycinany do ~kilkuset
    # świeżych, a customer_dwell akumuluje miesiącami → bez okna denominator zaniżony
    # (coverage >100%). Z --since (świeże dni) oba źródła pokrywają ten sam zbiór.
    cov_pct = (round(100 * len(truth_rows) / delivered_in_window, 1)
               if (args.since and delivered_in_window) else None)

    lines = []
    lines.append("📏 LINIJKA DOSTAWY — fizyczny GPS vs klik (#5b geofence, READ-ONLY)")
    cov_txt = (f"(coverage {cov_pct}%)" if cov_pct is not None
               else "(coverage: podaj --since dla porównywalnego okna)")
    lines.append(f"okno: {'od ' + args.since if args.since else 'całość'} | dostaw w oknie: {delivered_in_window} "
                 f"| z fizyczną prawdą (gps_geofence): {len(truth_rows)} {cov_txt}")
    if n:
        lines.append(f"delta 'klik − fizyczny przyjazd' [min, + = przycisk ZAWYŻA]:")
        lines.append(f"  WSZYSTKIE (n={n}): median={statistics.median(deltas):+.2f}  mean={statistics.mean(deltas):+.2f} "
                     f"| p10={_pct(deltas,10):+.1f} p90={_pct(deltas,90):+.1f} | min={deltas[0]:+.1f} max={deltas[-1]:+.1f}")
        bad = sum(1 for d in deltas if abs(d) > 3)
        before = sum(1 for d in deltas if d < 0)
        lines.append(f"  |delta|>3min: {bad}/{n} ({100*bad/n:.0f}%) | klik PRZED przyjazdem: {before}/{n} ({100*before/n:.0f}%)")
        if deltas_high:
            nh = len(deltas_high)
            lines.append(f"  WYSOKA PEWNOŚĆ (n_in>={HIGH_CONF_MIN_NIN}, n={nh}): "
                         f"median={statistics.median(deltas_high):+.2f}  p90={_pct(deltas_high,90):+.1f}")
        lines.append("WNIOSEK: przycisk = stoper systematycznie przesunięty o medianę powyżej; "
                     "fizyczny przyjazd = prawda do walidacji #2/feas-carry. gps_delivery_truth.jsonl = źródło per-zlecenie.")
    else:
        lines.append("⚪ BRAK dopasowań fizyczny↔klik w oknie (mało gps_geofence / brak delivered_at).")
    report = "\n".join(lines)
    print(report)
    print(f"\ntruth_rows: {len(truth_rows)} | tryb: {'WRITE' if args.write else 'DRY-RUN'}")

    if args.write:
        # gps_delivery_truth.jsonl — pełny rebuild (derived index), atomic
        d = os.path.dirname(args.out_truth)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in sorted(truth_rows, key=lambda r: r["order_id"]):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, args.out_truth)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(args.out_verdict), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(report + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, args.out_verdict)
        print(f"zapisano: {args.out_truth} ({len(truth_rows)} zleceń) + {args.out_verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
