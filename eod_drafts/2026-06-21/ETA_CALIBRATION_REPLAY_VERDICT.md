# ETA calibration — replay verdict (read-only, 2026-06-21)

**Request:** prepare a read-only replay of ETA calibration (the lever the rule_weights verdict pointed
to: realized deliveries showed 83.6% underestimation, +9.3 min median bias).

**TL;DR — the route ETA is already well-calibrated; the "+8 min underestimation" is PICKUP-TIMING SLIP
(prep / declared-time), NOT drive error. Recalibrating the route ETA is a no-op / mildly harmful. No
flip-safe corrector exists today: the simple additive correction OVER-corrects (breaks 95% of
already-accurate predictions), and the ML R3 model has unresolved train/serve skews + inconsistent
windows. The real lever is pickup-timing / prep-readiness prediction — a separate problem.** Zero
production changes made; nothing flipped.

---

## The decisive decomposition (avoids the drive_min_v2 artefact)

`eta_error_min` is **timestamp-based** (`real_delivered_at − predicted_delivered_at`), so it includes
**pickup-timing slip**: if the courier picks up later than the prediction assumed (prep not ready,
declared-time slip), the whole delivery slips even with a perfect drive leg. The **prep-independent**
route error is `real_delivery_min − predicted_delivery_min` (both anchored at pickup).

| signal | median signed | median |e| | P(underest) | n |
|---|---|---|---|---|
| timestamp error (incl pickup slip) | **+8.06** | 9.76 | 77% | 5385 |
| **pure drive+dwell leg** (real−pred) | **−1.21** | 6.44 | 44% | 5341 |
| implied pickup-timing slip (ts − drive) | **+9.00** | 9.28 | 87% | 5299 |

→ The drive+dwell leg is **slightly over-estimated** (−1.2 min, ~centered). The entire +8 min headline
is pickup-timing slip. This **reconfirms the `drive_min_v2` verdict (2026-06-05)** with fresh data:
"the apparent underestimate = food-prep / declared pickup time (`czas_kuriera`), not drive error."
(`ENABLE_DRIVE_MIN_CALIBRATION_V2` is correctly OFF.)

---

## Why the corrections aren't flip-safe (held-out, train=earlier 60% / test=later 40%)

| corrector (on timestamp error) | median |e| | median signed | |err|≤5 | breaks already-accurate |
|---|---|---|---|---|
| BASE (no correction) | 8.23 | +5.72 | 32.1% | — |
| **ADDITIVE** (per-bucket median-bias table = the `eta_quantile_map` idea) | **8.98 (worse)** | −4.04 (overshoot) | 28.6% | **95.0%** |
| **R3 (ML, shadow)** | 6.72 | −1.64 | 39.6% | **49.6%** |

- **ADDITIVE is net-harmful**: subtracting a flat ~8 min bias fixes the median but the error
  distribution is wide, so it overcorrects — median|e| gets *worse* (8.23→8.98), the signed bias
  overshoots to −4, and it **destroys 95% of the predictions that were already accurate.** This is the
  "Sprint-1 calib OVERCORRECTS" failure mode, quantified — and explains why the quantile shadow has
  never been flipped.
- **R3 (ML)** is the only thing that lowers median|e| (8.23→6.72, conditional per-order) — but it
  still breaks ~50% of accurate cases, and the existing forward-validation (`eta_r3_forward_val.py`)
  shows **mean-MAE only +6.6%, inconsistent across days (+18.8% / +3.4% / +5.8%)** and **serious
  train/serve skews** (`pool_feasible` D=0.42, `pred_delivery_min` D=0.18, `is_weekend` D=0.38). The
  skew-removed variant (R3-drop) forward-validated worse. → **not flip-ready.**

## Where the bias lives (it's broad, not peak-specific)
Median signed bias is ~uniform across buckets: peak +7.8, shoulder +8.6, peak-solo +9.1, offpeak +6.2.
A broad systematic pickup-timing slip — not a peak-traffic problem (so traffic-table tuning won't fix
it either; the traffic tables were already recalibrated 2026-06-05/06-12).

---

## Verdict & recommendation

1. **Do not recalibrate the route ETA (drive/dwell/traffic/speed).** The drive+dwell leg is already
   accurate (median −1.2 min, slightly over). DWELL_BY_TIER + V326_SPEED_MULTIPLIER were correctly
   recalibrated 2026-06-10; nothing to gain, and pushing further risks over-estimation.
2. **Do not flip a flat ETA bias correction** (additive / `eta_quantile_map`): proven net-harmful
   (median|e| 8.23→8.98, breaks 95% of accurate predictions). Keep it shadow.
3. **Do not flip ML R3 yet.** It's the only conditional corrector that helps the median, but the
   train/serve skews (esp. `pool_feasible`) + day-to-day inconsistency + 50% break-rate make it unsafe.
   Path to flip (a real ML task, needs ACK): retrain on a non-leaky feature set (the `pool_feasible`
   leak is the known blocker), add a confidence gate so it only corrects when sure (leave
   already-accurate predictions alone), and require ≥2 consistent forward windows.
4. **The genuine lever is pickup-timing / prep-readiness prediction** (the +9 min slip): how well
   Ziomek predicts WHEN the courier actually picks up — governed by `czas_kuriera` realism and
   restaurant prep readiness (`prep_bias` table, currently shadow OFF). That is a distinct calibration
   target with real force; if you want a next read-only replay, that's where it should point (does a
   per-restaurant prep-bias correction reduce the pickup-slip without breaking on-time pickups?).

**Net:** like rule_weights, the headline number was misleading once decomposed — but here the lever is
*real*, just mis-located. It's not the route ETA (calibrated) — it's pickup timing. No safe flip today.

---

## Reproduce (read-only)
```
PYTHONPATH=/root/.openclaw/workspace/scripts /root/.openclaw/venvs/dispatch/bin/python \
  dispatch_v2/eod_drafts/2026-06-21/eta_calibration_replay.py      # decomposition + held-out correctors
PYTHONPATH=/root/.openclaw/workspace/scripts /root/.openclaw/venvs/dispatch/bin/python \
  dispatch_v2/tools/eta_r3_forward_val.py                          # R3 MAE windows + KS skew parity
```
No writes, no flips. `eta_quantile_map`/`prep_bias`/R3 remain shadow; DWELL/speed tables unchanged.
