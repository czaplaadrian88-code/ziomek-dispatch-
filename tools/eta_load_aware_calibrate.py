#!/usr/bin/env python3
"""L5.1 — generator tabeli kalibracji ETA load-aware (jedyny WRITER
dispatch_state/eta_load_aware_calib.json).

READ źródło prawdy = eta_truth_map.build_rows (ten sam join predykcja↔realny
kurier↔sla_log, rotation-aware, kanoniczny parse stref — ZERO drugiej kopii
logiki pomiaru; bramka „zero kopii" L5.1).

Segmenty: (tier × solo/bundle) + (tier) + _global; per segment mediana
pickup_err (znak − = optymizm) i n. Konsument (eta_load_aware.pickup_buffer_min)
sam robi hierarchię fallbacku, clamp [0, CAP] i regułę „med ≥ 0 → bufor 0".

Anty-leakage: kalibruj na oknie A, waliduj replayem (eta_load_aware_replay.py)
na ROZŁĄCZNYM oknie B.

Użycie:
    /root/.openclaw/venvs/dispatch/bin/python tools/eta_load_aware_calibrate.py \
        --since 2026-06-28 --until 2026-07-03 [--min-n 30] [--out PATH] [--dry]
"""
import argparse
import json
import os
import statistics
import sys
import tempfile
from collections import defaultdict

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.tools.eta_truth_map import build_rows, _parse_day  # noqa: E402
from dispatch_v2.eta_load_aware import CALIB_PATH  # noqa: E402


def build_calib(rows, min_n: int) -> dict:
    by_seg = defaultdict(list)
    for r in rows:
        err = r.get("pickup_err")
        if err is None:
            continue
        tier = r.get("tier") or "unknown"
        sb = r.get("solo_bundle")
        by_seg["_global"].append(err)
        by_seg[tier].append(err)
        if sb in ("solo", "bundle"):
            by_seg[f"{tier}|{sb}"].append(err)
    segments = {}
    for key, vals in sorted(by_seg.items()):
        segments[key] = {
            "med_err_min": round(statistics.median(vals), 2),
            "n": len(vals),
        }
    return {"min_n": min_n, "segments": segments}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--out", default=CALIB_PATH)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    since = _parse_day(args.since)
    until = _parse_day(args.until)
    rows, stats = build_rows(since, until, include_czasowka=False)
    calib = build_calib(rows, args.min_n)
    calib["meta"] = {
        "window_since": args.since, "window_until": args.until,
        "rows_matched": stats.get("matched"),
        "pickup_ok": stats.get("pickup_ok"),
        "generator": "tools/eta_load_aware_calibrate.py",
    }
    payload = json.dumps(calib, ensure_ascii=False, indent=1)
    if args.dry:
        print(payload)
        return 0
    d = os.path.dirname(args.out)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".eta_la_calib_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, args.out)
    print(f"OK → {args.out} (segments={len(calib['segments'])}, "
          f"pickup_ok={stats.get('pickup_ok')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
