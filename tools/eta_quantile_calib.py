"""eta_quantile_calib — kalibracja kwantylowa ETA pred→real (SP-B2-ETAQ).

Problem (raport Bartek 2.0 §4.1.4, QW4): ETA pipeline'u jest pesymistyczna —
mediana bias na sparowanych parach (matched_courier): pred 25-30 → -6 min,
pred 30-35 → -10 min, pred 35-60 → -17 min. Pesymizm zatruwa margin/R6/czasówki
(KOORD-y "nie zdąży", które człowiek dowoził w SLA).

Generator (TEN plik, sesja B): z eta_calibration_log.jsonl buduje mapping
kwantylowy pred→real per koszyk pred × slot i zapisuje
dispatch_state/eta_quantile_map.json (atomic). Cron daily 04:35 Warsaw.

Konsumpcja (sesja A, SHADOW za flagą ENABLE_ETA_QUANTILE_SHADOW): pipeline
liczy travel_min_cal i serializuje obok travel_min. Semantyka odczytu mapy:
  1. bin = pierwszy przedział pred_bins[i] <= pred < pred_bins[i+1]
     (pred >= ostatniej granicy → ostatni koszyk);
  2. cell = map[slot][bin] jeśli n >= min_n, inaczej global[bin] jeśli
     n >= min_n, inaczej IDENTITY (travel_min_cal = pred);
  3. travel_min_cal = cell["p50"]; wariant ostrożny (obietnica klientowi,
     raport §12.3.2) = cell["p80"]. Fail-soft: brak pliku/koszyka → identity.

Metodologia:
  - pary z predicted_delivery_min ≠ null ∧ real_delivery_min ≠ null,
    0 < oba ≤ MAX_MIN (120);
  - DEFAULT tylko matched_courier=True (predykcja dla kuriera, który
    faktycznie dowiózł) — pary unmatched mieszają szum selekcji (inny kurier
    = real nie odnosi się do pred). Bias na wszystkich parach raportowany
    w stdout dla porównania z raportem (§4.1.4 liczone na 8k par all);
  - slot = pole `bucket` z loga (peak/shoulder/offpeak — klasyfikacja w
    momencie logowania, spójna z resztą systemu);
  - czytanie logrotate-aware (tools/_rotated_logs) — log pod logrotate
    GRUPA B-2 (100M cap).

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
except ImportError:  # uruchomienie bezpośrednie: python tools/<plik>.py
    from _rotated_logs import iter_jsonl_records

CALIB_LOG = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
OUT_PATH = "/root/.openclaw/workspace/dispatch_state/eta_quantile_map.json"

# Granice koszyków pred (min). Ostatni koszyk = [40, +inf) etykieta "40+".
PRED_BINS = [0, 10, 15, 20, 25, 30, 40]
SLOTS = ("peak", "shoulder", "offpeak")
MIN_N = 30          # min par w komórce; poniżej → fallback global → identity
MAX_MIN = 120.0     # sanity cap pred/real
DEFAULT_DAYS = 28   # okno świeżości (pipeline ewoluuje — nie kalibruj na antyku)


def bin_label(pred: float) -> str:
    """Etykieta koszyka dla pred (ostatni koszyk otwarty: '40+')."""
    for lo, hi in zip(PRED_BINS, PRED_BINS[1:]):
        if lo <= pred < hi:
            return f"{lo}-{hi}"
    return f"{PRED_BINS[-1]}+"


def _quantile(sorted_vals: list, q: float) -> float:
    """Kwantyl liniowo interpolowany (jak statistics.quantiles inclusive)."""
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


def collect_pairs(days: int):
    """Zwraca (matched_pairs, all_pairs); para = (pred, real, slot)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    matched, allp = [], []
    for r in iter_jsonl_records(CALIB_LOG, cutoff_dt=cutoff):
        ts = _parse_logged_at(r.get("logged_at"))
        if ts is None or ts < cutoff:
            continue
        pred = r.get("predicted_delivery_min")
        real = r.get("real_delivery_min")
        try:
            pred = float(pred)
            real = float(real)
        except (TypeError, ValueError):
            continue
        if not (0.0 < pred <= MAX_MIN and 0.0 < real <= MAX_MIN):
            continue
        slot = r.get("bucket")
        if slot not in SLOTS:
            slot = "offpeak"
        pair = (pred, real, slot)
        allp.append(pair)
        if r.get("matched_courier"):
            matched.append(pair)
    return matched, allp


def build_map(pairs) -> dict:
    """{slot: {bin: {p50,p80,n,bias_med}}} + global per bin."""
    by_cell: dict = {s: {} for s in SLOTS}
    by_bin: dict = {}
    for pred, real, slot in pairs:
        b = bin_label(pred)
        by_cell[slot].setdefault(b, []).append((pred, real))
        by_bin.setdefault(b, []).append((pred, real))

    def cell_stats(vals):
        reals = sorted(v[1] for v in vals)
        preds = sorted(v[0] for v in vals)
        p50 = _quantile(reals, 0.5)
        return {
            "p50": round(p50, 1),
            "p80": round(_quantile(reals, 0.8), 1),
            "n": len(vals),
            "pred_med": round(_quantile(preds, 0.5), 1),
            "bias_med": round(p50 - _quantile(preds, 0.5), 1),
        }

    out_map = {s: {b: cell_stats(v) for b, v in bins.items()}
               for s, bins in by_cell.items()}
    out_global = {b: cell_stats(v) for b, v in by_bin.items()}
    return {"map": out_map, "global": out_global}


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
    matched, allp = collect_pairs(days)
    built = build_map(matched)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": CALIB_LOG,
        "window_days": days,
        "n_pairs_matched": len(matched),
        "n_pairs_all": len(allp),
        "filters": {"matched_courier": True, "max_min": MAX_MIN},
        "pred_bins": PRED_BINS,
        "slots": list(SLOTS),
        "min_n": MIN_N,
        "semantics": ("travel_min_cal = map[slot][bin].p50 jeśli n>=min_n, "
                      "inaczej global[bin].p50 jeśli n>=min_n, inaczej pred "
                      "(identity). p80 = wariant ostrożny/obietnica."),
        "map": built["map"],
        "global": built["global"],
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
          f"(okno {args.days}d){' DRY-RUN' if args.dry_run else ''}")
    if payload["n_pairs_matched"] < MIN_N:
        print("UWAGA: za mało par — mapa praktycznie pusta (konsument: identity)",
              file=sys.stderr)
        return 0  # nie alarmuj crona; konsument fail-soft
    for b in sorted(payload["global"], key=lambda x: float(x.split("-")[0].rstrip("+"))):
        g = payload["global"][b]
        print(f"  pred {b:>6}: n={g['n']:4d} pred_med={g['pred_med']:5.1f} "
              f"real_p50={g['p50']:5.1f} real_p80={g['p80']:5.1f} bias={g['bias_med']:+5.1f}")
    if not args.dry_run:
        print(f"zapisano: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
