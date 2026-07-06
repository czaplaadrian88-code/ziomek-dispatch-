#!/usr/bin/env python3
"""eta_cell_residual_build — generator mapy korekty ETA per-KOMÓRKA FLOTY (W0.5).

Werdykt E-7-GO (advisory Faza 6.1): addytywna korekta predykcji silnika per
komórka (slot Warsaw × solo/worek), residual = median(real − predicted) po
CAŁEJ flocie (NIE per-kurier — to NO-GO), shrunk wagą n/(n+k). OOS (train 37d /
test 14d): MAE 10,39→10,04 (+3,4%), underestymacja 31,0→30,2 (−0,8 p.p.).

Sygnał: silnik SYSTEMATYCZNIE niedoszacowuje solo (real dłuższy, resid +3..+5)
i lekko przeszacowuje worki (resid −1..−2). Korekta = na OBIETNICĘ (uczciwość),
NIE na twardą bramkę R6 (SOFT nie osłabia HARD — konsument nie rusza feasibility).

Slot = calib_maps.time_slot_warsaw (peak_lunch/high_risk/peak_dinner/off) — TEN
SAM podział co konsument (parytet instrument↔silnik). Źródło: eta_calibration
log (rotation-aware) LUB zamrożony korpus (--source PLIK dla walidacji).

Użycie:
  venvs/dispatch/bin/python -m dispatch_v2.tools.eta_cell_residual_build \
     [--source PLIK] [--out PLIK] [--min-n 20] [--shrink-k 15] [--days 51]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict

SCRIPTS = "/root/.openclaw/workspace/scripts"
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

DEFAULT_OUT = "/root/.openclaw/workspace/dispatch_state/eta_cell_residual_map.json"


def _slot(h: int) -> str:
    """Mirror calib_maps.time_slot_warsaw (na godzinie Warsaw z logu)."""
    if 11 <= h < 14:
        return "peak_lunch"
    if 14 <= h < 17:
        return "high_risk"
    if 17 <= h < 20:
        return "peak_dinner"
    return "off"


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def _iter_source(source):
    if source:
        with open(source, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        yield json.loads(ln)
                    except json.JSONDecodeError:
                        continue
        return
    # live: rotation-aware eta_calibration log
    from dispatch_v2.tools import _rotated_logs
    base = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
    for p in _rotated_logs.files_in_window(base):
        if not os.path.exists(p):
            continue
        with _rotated_logs.open_maybe_gz(p) as f:
            for ln in f:
                try:
                    yield json.loads(ln)
                except (json.JSONDecodeError, TypeError):
                    continue


def build_map(source=None, min_n=20, shrink_k=15):
    cells = defaultdict(list)
    all_resid = []
    n_used = 0
    for r in _iter_source(source):
        p = r.get("predicted_delivery_min")
        rl = r.get("real_delivery_min")
        h = r.get("hour_warsaw")
        if not (isinstance(p, (int, float)) and isinstance(rl, (int, float))
                and isinstance(h, int)):
            continue
        if rl <= 0 or rl > 180 or p <= 0 or p > 300:
            continue
        resid = rl - p
        key = (_slot(h), bool(r.get("is_bundle")))
        cells[key].append(resid)
        all_resid.append(resid)
        n_used += 1
    global_resid = round(_median(all_resid), 2) if all_resid else 0.0
    out_cells = []
    for (slot, bundle), vals in sorted(cells.items()):
        if len(vals) < min_n:
            continue
        n = len(vals)
        out_cells.append({
            "slot": slot,
            "bundle": bundle,
            "resid_min": round(_median(vals), 2),
            "n": n,
            "weight": round(n / (n + shrink_k), 4),  # shrinkage floty
        })
    return {
        "schema": "eta_cell_residual_v1",
        "min_n": min_n,
        "shrink_k": shrink_k,
        "n_records": n_used,
        "global_resid_min": global_resid,
        "cells": out_cells,
    }


def _atomic_write(path, obj):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="zamrożony korpus jsonl (domyślnie: live rotation-aware)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--min-n", type=int, default=20)
    ap.add_argument("--shrink-k", type=int, default=15)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    m = build_map(a.source, a.min_n, a.shrink_k)
    print(json.dumps(m, ensure_ascii=False, indent=1))
    if not a.dry_run:
        _atomic_write(a.out, m)
        print(f"→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
