#!/usr/bin/env python3
"""eta_calibration_replay.py — READ-ONLY ETA calibration replay / forward-validation.

ZERO writes, ZERO flips. Uses the already-logged per-order errors in eta_calibration_log.jsonl:
  eta_error_min                = real_delivered - predicted_delivered (signed; +ve = Ziomek underestimated)
  eta_r3_corrected_error_min   = error after the (shadow) ML R3 residual correction
real_delivery_min is picked_up->delivered (the DELIVERY LEG), so this is drive+dwell error, NOT prep
time — it sidesteps the drive_min_v2 prep-contamination artefact (verdict 2026-06-05).

Three correctors compared on a HELD-OUT split (train = earlier 60%, test = later 40%, chronological):
  BASE      — raw prediction (no correction)
  R3 (ML)   — the shadow LightGBM residual (already logged per order)
  ADDITIVE  — flip-SAFE per-bucket median-bias table learned on TRAIN, applied on TEST (= the
              eta_quantile_map idea, but a plain lookup → no train/serve skew, interpretable)

Robust metrics (median, outlier-filtered): mean-MAE here is dominated by reconcile-batch artifacts
(some real_delivery_min are 100+min reconciliation ghosts), so we cap |error| and report medians.

Run: PYTHONPATH=/root/.openclaw/workspace/scripts \
     /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-06-21/eta_calibration_replay.py
"""
import json
import statistics as st

LOG = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
OUTLIER_CAP = 45.0   # |error|>45min = reconcile/ghost artifact, not a drive-leg miss


def med_abs(x):
    return st.median([abs(v) for v in x]) if x else float("nan")


def med(x):
    return st.median(x) if x else float("nan")


def acc(x, thr=5.0):
    return 100.0 * sum(1 for v in x if abs(v) <= thr) / len(x) if x else float("nan")


def main():
    rows = []
    dropped = 0
    for line in open(LOG):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        e = d.get("eta_error_min")
        if e is None or d.get("matched_courier") is not True or d.get("was_czasowka"):
            continue
        if abs(e) > OUTLIER_CAP:
            dropped += 1
            continue
        rows.append(d)

    n = len(rows)
    print(f"=== CORPUS ===  usable matched non-czasowka deliveries: {n}  (dropped {dropped} |err|>{OUTLIER_CAP}min reconcile-artifacts)\n")

    # (A0) DECOMPOSITION — the headline. eta_error_min is TIMESTAMP-based (real_delivered_at -
    # predicted_delivered_at) so it includes PICKUP-TIMING SLIP (prep/declared-time). The PURE,
    # prep-independent route error is real_delivery_min - predicted_delivery_min (both anchored at
    # pickup). If the drive leg is ~unbiased, the headline "+8min" is pickup slip, NOT drive error,
    # and recalibrating the route ETA is a no-op (drive_min_v2 verdict, 2026-06-05, reconfirmed).
    def _st(x, lbl):
        x = [v for v in x if abs(v) <= OUTLIER_CAP]
        print(f"  {lbl:<46} n={len(x)} median signed={st.median(x):+.2f} median|e|={med_abs(x):.2f} "
              f"P(underest)={100*sum(1 for v in x if v>0)/len(x):.0f}%")
    ts = [r["eta_error_min"] for r in rows if r.get("real_delivery_min") is not None and r.get("predicted_delivery_min") is not None]
    dl = [r["real_delivery_min"] - r["predicted_delivery_min"] for r in rows
          if r.get("real_delivery_min") is not None and r.get("predicted_delivery_min") is not None]
    slip = [r["eta_error_min"] - (r["real_delivery_min"] - r["predicted_delivery_min"]) for r in rows
            if r.get("real_delivery_min") is not None and r.get("predicted_delivery_min") is not None]
    print("=== (A0) DECOMPOSITION — is the bias DRIVE error or PICKUP-timing slip? ===")
    _st(ts,   "timestamp error eta_error_min (incl pickup slip)")
    _st(dl,   "PURE drive+dwell leg (real-pred delivery_min)")
    _st(slip, "implied pickup-timing slip (ts - drive)")
    print("  -> if drive-leg ~0 and slip ~+8: route ETA is calibrated; the bias is prep/pickup, not drive.\n")

    base = [r["eta_error_min"] for r in rows]
    print("=== (A) BASE ETA BIAS (delivery leg = drive+dwell; +ve = underestimated) ===")
    print(f"  n={n}  median signed={med(base):+.2f}  median|e|={med_abs(base):.2f}  "
          f"|err|<=5min={acc(base):.1f}%  P(underest)={100*sum(1 for v in base if v>0)/n:.1f}%\n")

    # (B) decompose base bias by bucket x is_bundle
    print("=== (B) WHERE THE BIAS LIVES (median signed bias per cell) ===")
    cells = {}
    for r in rows:
        k = (r.get("bucket", "?"), bool(r.get("is_bundle")))
        cells.setdefault(k, []).append(r["eta_error_min"])
    print(f"  {'bucket':<12} {'bundle':<7} {'n':>5} {'med signed':>11} {'med|e|':>8}")
    for k in sorted(cells, key=lambda k: -len(cells[k])):
        v = cells[k]
        print(f"  {str(k[0]):<12} {str(k[1]):<7} {len(v):>5} {med(v):>+11.2f} {med_abs(v):>8.2f}")
    print()

    # (C) held-out comparison: BASE vs R3 vs ADDITIVE (per-bucket median-bias table from TRAIN)
    split = int(n * 0.6)
    train, test = rows[:split], rows[split:]
    table = {}
    tcells = {}
    for r in train:
        k = (r.get("bucket", "?"), bool(r.get("is_bundle")))
        tcells.setdefault(k, []).append(r["eta_error_min"])
    for k, v in tcells.items():
        table[k] = med(v)
    glob_bias = med([r["eta_error_min"] for r in train])

    t_base, t_add, t_r3 = [], [], []
    for r in test:
        e = r["eta_error_min"]
        t_base.append(e)
        k = (r.get("bucket", "?"), bool(r.get("is_bundle")))
        t_add.append(e - table.get(k, glob_bias))         # subtract learned bias
        c = r.get("eta_r3_corrected_error_min")
        if c is not None:
            t_r3.append(c)
    print(f"=== (C) HELD-OUT (train={len(train)} earlier / test={len(test)} later) ===")
    print(f"  {'corrector':<22} {'n':>5} {'median|e|':>10} {'med signed':>11} {'|err|<=5':>9}")
    print(f"  {'BASE (no correction)':<22} {len(t_base):>5} {med_abs(t_base):>10.2f} {med(t_base):>+11.2f} {acc(t_base):>8.1f}%")
    print(f"  {'ADDITIVE (flip-safe)':<22} {len(t_add):>5} {med_abs(t_add):>10.2f} {med(t_add):>+11.2f} {acc(t_add):>8.1f}%")
    if t_r3:
        print(f"  {'R3 (ML, shadow)':<22} {len(t_r3):>5} {med_abs(t_r3):>10.2f} {med(t_r3):>+11.2f} {acc(t_r3):>8.1f}%")
    print()

    # (D) regression guard: among already-accurate base predictions on TEST, who breaks them?
    print("=== (D) REGRESSION GUARD (of TEST records already accurate |base|<=5min, % pushed to >5) ===")
    idx_ok = [i for i, r in enumerate(test) if abs(r["eta_error_min"]) <= 5.0]
    if idx_ok:
        add_break = 100 * sum(1 for i in idx_ok if abs(t_add[i]) > 5.0) / len(idx_ok)
        print(f"  already-accurate base: {len(idx_ok)}  | ADDITIVE breaks {add_break:.1f}%")
        # R3 alignment by oid subset
        ok_oids = {test[i].get("oid") for i in idx_ok}
        r3_ok = [r.get("eta_r3_corrected_error_min") for r in test
                 if r.get("oid") in ok_oids and r.get("eta_r3_corrected_error_min") is not None]
        if r3_ok:
            r3_break = 100 * sum(1 for v in r3_ok if abs(v) > 5.0) / len(r3_ok)
            print(f"  (R3 on same already-accurate subset, n={len(r3_ok)}): breaks {r3_break:.1f}%")
    print("\n(Interpretation: ADDITIVE that lowers median|e| AND breaks few accurate ones = flip-safe lever.\n"
          " R3 with skews + inconsistent windows = not flip-ready. Both vs BASE quantify the real ETA gain.)")


if __name__ == "__main__":
    main()
