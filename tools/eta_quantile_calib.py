"""eta_quantile_calib — kalibracja kwantylowa ETA pred→real (SP-B2-ETAQ).

Problem (raport Bartek 2.0 §4.1.4, QW4): ETA pipeline'u jest pesymistyczna —
mediana bias na sparowanych parach (matched_courier): pred 25-30 → -6 min,
pred 30-40 → -10 min, pred 40+ → -25 min (4456 par, okno 28d). Pesymizm
zatruwa margin/R6/czasówki (KOORD-y "nie zdąży", które człowiek dowoził w SLA).

Generator (TEN plik, sesja B): z eta_calibration_log.jsonl buduje mapping
kwantylowy pred→real per koszyk pred × slot i zapisuje
dispatch_state/eta_quantile_map.json (atomic). Cron daily 04:35 Warsaw.

FORMAT WYJŚCIA = KONTRAKT SESJI A (eod_drafts/2026-06-11/
MAP_CONTRACT_calib_maps_sesjaA.md; konsument: dispatch_v2/calib_maps.py,
flaga ENABLE_ETA_QUANTILE_SHADOW, fail-soft):

    {"version": 1, "generated_at": iso, "buckets": [
        {"slot": s, "pred_lo": lo, "pred_hi": hi, "p50": x, "p80": y, "n": n}]}

  - sloty z calib_maps.time_slot_warsaw: peak_lunch 11-14 / high_risk 14-17 /
    peak_dinner 17-20 / off; dodatkowo "all" = fallback bez podziału;
  - slot rekordu liczony z pola hour_warsaw (godzina ZAMÓWIENIA, nadana przy
    logowaniu) przez tę samą funkcję co konsument — identyczne granice;
  - komórki n < MIN_N (30) NIE są emitowane (konsument → None → identity);
  - pred_lo <= pred < pred_hi; ostatni koszyk otwarty (40 → 999).

Metodologia:
  - pary predicted_delivery_min ≠ null ∧ real_delivery_min ≠ null,
    0 < oba ≤ MAX_MIN (120);
  - DEFAULT tylko matched_courier=True (predykcja dla kuriera, który
    faktycznie dowiózł — pary unmatched mieszają szum selekcji);
  - czytanie logrotate-aware (tools/_rotated_logs), okno --days 28.

Użycie:
  python3 -m dispatch_v2.tools.eta_quantile_calib [--days 28] [--dry-run]
  # cron (CRON_TZ=Europe/Warsaw): 35 4 * * *

Testy: dispatch_v2/tests/test_b2_eta_quantile_calib.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from dispatch_v2.tools._rotated_logs import iter_jsonl_records
    from dispatch_v2.calib_maps import SLOT_ALL, time_slot_warsaw
except ImportError:  # uruchomienie bezpośrednie: python tools/<plik>.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from dispatch_v2.tools._rotated_logs import iter_jsonl_records
    from dispatch_v2.calib_maps import SLOT_ALL, time_slot_warsaw

try:
    from zoneinfo import ZoneInfo
    _WARSAW = ZoneInfo("Europe/Warsaw")
except Exception:  # pragma: no cover
    _WARSAW = timezone.utc

CALIB_LOG = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
OUT_PATH = "/root/.openclaw/workspace/dispatch_state/eta_quantile_map.json"

# Granice koszyków pred (min); ostatni otwarty do PRED_HI_OPEN.
PRED_BINS = [0.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0]
PRED_HI_OPEN = 999.0
MIN_N = 30          # komórki poniżej nie są emitowane (konsument → None)
MAX_MIN = 120.0     # sanity cap pred/real
DEFAULT_DAYS = 28   # okno świeżości (pipeline ewoluuje — nie kalibruj na antyku)


def slot_for_hour_warsaw(hour: int) -> str:
    """Slot kontraktu dla godziny Warsaw — przez time_slot_warsaw (identyczne
    granice co konsument; konstruujemy datetime Warsaw o tej godzinie)."""
    dt = datetime(2026, 1, 5, int(hour) % 24, 30, tzinfo=_WARSAW)
    return time_slot_warsaw(dt)


def _bin_edges(pred: float):
    """(lo, hi) koszyka dla pred; ostatni koszyk otwarty do PRED_HI_OPEN."""
    for lo, hi in zip(PRED_BINS, PRED_BINS[1:]):
        if lo <= pred < hi:
            return lo, hi
    return PRED_BINS[-1], PRED_HI_OPEN


def _quantile(sorted_vals: list, q: float) -> float:
    """Kwantyl liniowo interpolowany."""
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_vals):
        return float(sorted_vals[-1])
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[lo + 1] * frac)


def _parse_logged_at(s: object) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _hour_from_record(r: dict) -> Optional[int]:
    """hour_warsaw z rekordu; fallback: godzina z picked_up_at (Warsaw naive)."""
    h = r.get("hour_warsaw")
    try:
        if h is not None:
            return int(h) % 24
    except (TypeError, ValueError):
        pass
    pu = r.get("picked_up_at")
    if pu:
        try:
            return int(str(pu)[11:13])
        except (TypeError, ValueError):
            return None
    return None


def collect_pairs(days: int):
    """Zwraca (matched_pairs, n_all); para = (pred, real, slot kontraktu)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    matched = []
    n_all = 0
    for r in iter_jsonl_records(CALIB_LOG, cutoff_dt=cutoff):
        ts = _parse_logged_at(r.get("logged_at"))
        if ts is None or ts < cutoff:
            continue
        try:
            pred = float(r.get("predicted_delivery_min"))
            real = float(r.get("real_delivery_min"))
        except (TypeError, ValueError):
            continue
        if not (0.0 < pred <= MAX_MIN and 0.0 < real <= MAX_MIN):
            continue
        hour = _hour_from_record(r)
        if hour is None:
            continue
        n_all += 1
        if r.get("matched_courier"):
            matched.append((pred, real, slot_for_hour_warsaw(hour)))
    return matched, n_all


def build_buckets(pairs) -> list:
    """Płaska lista bucketów kontraktu (sloty + 'all'); tylko n >= MIN_N."""
    cells: dict = {}
    for pred, real, slot in pairs:
        lo, hi = _bin_edges(pred)
        for s in (slot, SLOT_ALL):
            cells.setdefault((s, lo, hi), []).append((pred, real))

    out = []
    for (slot, lo, hi), vals in sorted(cells.items()):
        if len(vals) < MIN_N:
            continue
        reals = sorted(v[1] for v in vals)
        preds = sorted(v[0] for v in vals)
        p50 = _quantile(reals, 0.5)
        pred_med = _quantile(preds, 0.5)
        out.append({
            "slot": slot,
            "pred_lo": lo,
            "pred_hi": hi,
            "p50": round(p50, 1),
            "p80": round(_quantile(reals, 0.8), 1),
            "n": len(vals),
            # diagnostyka (konsument ignoruje):
            "pred_med": round(pred_med, 1),
            "bias_med": round(p50 - pred_med, 1),
        })
    return out


def atomic_write_json(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".eta_quantile_map.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run(days: int = DEFAULT_DAYS, dry_run: bool = False) -> dict:
    matched, n_all = collect_pairs(days)
    buckets = build_buckets(matched)
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buckets": buckets,
        # diagnostyka (konsument ignoruje):
        "source": CALIB_LOG,
        "window_days": days,
        "n_pairs_matched": len(matched),
        "n_pairs_all": n_all,
        "min_n": MIN_N,
        "pred_bins": PRED_BINS + [PRED_HI_OPEN],
    }
    if not dry_run:
        atomic_write_json(OUT_PATH, payload)
    return payload


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--days", type=int, default=DEFAULT_DAYS)
    p.add_argument("--dry-run", action="store_true", help="bez zapisu")
    args = p.parse_args(argv)

    payload = run(days=args.days, dry_run=args.dry_run)
    print(f"pary: matched={payload['n_pairs_matched']} all={payload['n_pairs_all']} "
          f"buckets={len(payload['buckets'])} (okno {args.days}d)"
          f"{' DRY-RUN' if args.dry_run else ''}")
    if payload["n_pairs_matched"] < MIN_N:
        print("UWAGA: za mało par — mapa praktycznie pusta (konsument: identity)",
              file=sys.stderr)
        return 0  # nie alarmuj crona; konsument fail-soft
    for b in payload["buckets"]:
        if b["slot"] == SLOT_ALL:
            print(f"  all {b['pred_lo']:5.0f}-{b['pred_hi']:3.0f}: n={b['n']:4d} "
                  f"pred_med={b['pred_med']:5.1f} p50={b['p50']:5.1f} "
                  f"p80={b['p80']:5.1f} bias={b['bias_med']:+5.1f}")
    if not args.dry_run:
        print(f"zapisano: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
