# Reassignment FORWARD shadow v2 — design / handoff (2026-06-22)

**Status:** units DRAFTED only (`dispatch_v2/deploy/`), NOT installed, NOT enabled, NOT started.
Module `dispatch_v2/tools/reassignment_forward_shadow.py` exists (read-only). Flag default OFF.
This is a **measure-first** harness: prove the geometry/route-fit reassignment lever is real
*before* anyone considers building autonomous (auto-)reassignment of already-assigned orders.

---

## (a) Problem — why v1 was inconclusive

v1 (`reassignment_shadow.py`, 2026-06-07) ran **offline over dead shadow logs** and returned a
"niejednoznaczny" (inconclusive) verdict. Root cause: **~85% of historical reassignments were
un-scorable** because the archived log records had **no delivery geocode** — the offline replay
could not reconstruct `delivery_coords`/`pickup_coords`, so `assess_order` had no geometry to rank
on and most candidates hard-rejected. We measured noise, not the lever.

Conclusion: an offline log replay cannot answer "would Ziomek have picked a different courier?"
We need **live state** (which already carries per-order coords) and the **real engine**.

## (b) v2 architecture — own-process timer, real engine, zero hot-path

- **Own process / own timer.** Project doctrine: shadow logic in the dispatch hot-path once took
  prod down (V3.27.4 `NameError`). v2 runs in its **own process** on its **own timer**, calling
  `assess_order` read-only. Latency is isolated; **zero risk to the live dispatch**.
- **Real `assess_order`, not a private scorer.** For every **un-picked-up** order `O` (status
  `assigned`, real courier `A`, not Koordynator/None, with both pickup+delivery coords) the sweep
  asks the counterfactual: *"if O were unassigned right now, who would Ziomek pick?"* — by calling
  the **production** `dispatch_pipeline.assess_order(order_event, fleet_cf, _bypass_early_bird=True)`
  over the **full dispatchable fleet** with `O` removed from `A`'s bag. Same engine as prod
  (`feasibility_v2` + scoring + OSRM + R6 + A2) → **zero scoring drift**.
- **Fleet = `courier_resolver.dispatchable_fleet()`** (NOT raw `build_fleet_snapshot`). It enriches
  `shift_end`; otherwise feasibility hard-rejects the whole fleet (czasówka bug #471036 / Lekcja #80).
- **`would_reassign`** = `best_cid != holder_cid` AND (`a_score is None` OR
  `b_score - a_score >= REASSIGN_FWD_MARGIN`). If no feasible candidate (`best is None`) the order
  is a **KOORD situation, not a reassignment** → skipped (separate topic).
- **ZERO mutation.** Does not write `orders_state`, emits no events, never calls Telegram (orders
  without `pickup_coords` are filtered out, which also avoids the admin-alert path inside
  `assess_order`). Only side effect: append to the jsonl below.
- **Fleet copy is shallow & non-mutating.** `_fleet_without_order` copies only the holder's
  `CourierState` + its `bag` list; the live snapshot is untouched.

## (c) Flag table (all read live; margin/cap via `C.load_flags()`, hot-reload)

| Key | Default | Where | Meaning |
|---|---|---|---|
| `ENABLE_REASSIGNMENT_FORWARD_SHADOW` | **OFF** (false) | `C.flag(...)` gate at top of `run_once` | Master gate. OFF → `run_once` returns `{"skipped":"flag_off"}` **instantly** (no state load, no fleet build, no OSRM). Timer can run every 3 min with zero cost. |
| `REASSIGN_FWD_MARGIN` | `15.0` | `flags.json` via `C.load_flags()` | Min score advantage (`b_score - a_score`) for `would_reassign=True`. ~same order of magnitude as AUTO_PROXIMITY `min_score_margin`. Raise to be more conservative. |
| `REASSIGN_FWD_MAX_ORDERS` | `60` | `flags.json` via `C.load_flags()` | Cap of orders scored per sweep (latency guard on 2-vCPU in peak). Sweep prioritizes **oldest** assigned orders first, then truncates. |

Defaults live in code (`DEFAULT_MARGIN`/`DEFAULT_MAX_ORDERS`); keys need NOT exist in `flags.json`
until/unless you want to override.

## (d) Output — jsonl path + record schema

Path: **`/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl`**
(= `../dispatch_state/reassignment_shadow.jsonl` relative to WorkingDirectory `…/scripts`).
Append-only, `flush()`+`fsync()` per sweep (like `shadow_decisions.jsonl`). One JSON object per
evaluated order. `run_once` also prints a one-line summary to stdout/log:
`{"active", "evaluated", "would_reassign", "margin", "ts"}`.

Per-order record schema (from `evaluate_order`):

| field | type | note |
|---|---|---|
| `ts` | ISO8601 UTC | sweep timestamp |
| `order_id` | str | |
| `restaurant` | str/null | |
| `holder_cid` | str | courier `A` currently holding `O` |
| `best_cid` | str | courier the engine would pick now (O removed from A's bag) |
| `would_reassign` | bool | the verdict (see margin rule above) |
| `a_in_pool` | bool | was holder A still a feasible candidate at all |
| `a_score` | float/null | A's score this sweep (null if A not in candidate pool) |
| `b_score` | float | best candidate's score |
| `delta_score` | float/null | `b_score - a_score` |
| `verdict` | str/null | engine verdict (e.g. PROPOSE/KOORD) |
| `pool_feasible` | int | feasible candidate count |
| `a_pos_source` / `b_pos_source` | str/null | GPS source for A / B (gps vs store vs none) |
| `a_bag_size` / `b_bag_size` | int/null | bag sizes |
| `b_tier` | str/null | `tier_bag` of best courier |
| `pickup_coords` / `delivery_coords` | list | for offline geo joins during EVAL |

## (e) DEPLOY (timer runs but flag OFF ⇒ no-op; safe to do anytime)

```
sudo cp /root/.openclaw/workspace/scripts/dispatch_v2/deploy/dispatch-reassignment-shadow.service /etc/systemd/system/
sudo cp /root/.openclaw/workspace/scripts/dispatch_v2/deploy/dispatch-reassignment-shadow.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dispatch-reassignment-shadow.timer
# verify it ticks (each tick = instant no-op while flag OFF):
systemctl list-timers dispatch-reassignment-shadow.timer
journalctl -u dispatch-reassignment-shadow.service -n 20 --no-pager   # expect {"skipped":"flag_off"}
```

OnFailure is already declared in the unit (`dispatch-onfailure-alert@%n.service`) — no drop-in
needed (same as `dispatch-pickup-lateness-shadow` / `dispatch-freshness-shadow`).

## (f) FLIP — start logging (hot-reload, no restart)

Set the flag in `flags.json` and the **next 3-min tick** begins logging — no `systemctl` needed:

```
# edit /root/.openclaw/workspace/scripts/flags.json:  "ENABLE_REASSIGNMENT_FORWARD_SHADOW": true
# (optional) tune "REASSIGN_FWD_MARGIN" / "REASSIGN_FWD_MAX_ORDERS"
# confirm next sweep writes records:
tail -f /root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl
```

> NOTE: do **not** edit `flags.json` during this drafting task — flip is an operator step at EVAL time.

## (g) ROLLBACK

- **Soft (preferred):** set `"ENABLE_REASSIGNMENT_FORWARD_SHADOW": false` in `flags.json`
  (hot-reload → next tick is instant no-op again). Timer may keep running harmlessly.
- **Hard:**
  ```
  sudo systemctl disable --now dispatch-reassignment-shadow.timer
  sudo rm /etc/systemd/system/dispatch-reassignment-shadow.{service,timer}
  sudo systemctl daemon-reload
  ```

## (h) EVAL plan (after N days of logging)

1. **Collect both sides.**
   - *Shadow*: `would_reassign=true` records from `reassignment_shadow.jsonl`.
   - *Ground truth*: real human reassignments — `COURIER_ASSIGNED` events whose `previous_cid`
     (a.k.a. prior holder) differs from the new courier, i.e. an already-assigned order moved
     hands. Join shadow↔truth on `order_id` (+ nearest timestamp window).
2. **Confusion / agreement.** For the same order around the same time, does the shadow's
   `best_cid` match the human's new courier? Compute:
   - precision/recall of `would_reassign` vs actual human reassigns,
   - on matches, agreement between `best_cid` and the human's chosen courier,
   - `delta_score` distribution on agreed vs disagreed cases (is a big margin predictive?).
3. **Coverage sanity.** Confirm v1's 85% blind spot is gone — i.e. most live orders are scorable
   (have coords + feasible pool). Report `evaluated/active` ratio.
4. **Decide the lever.** Is the geometry/route-fit signal strong & well-calibrated enough that
   an autonomous reassignment would *agree with good human moves and avoid bad churn*?
   - **GO** → design a guarded auto-reassignment (margin + carried/NO-RETURN guards, just like
     `_relax_carried_first`), still behind its own flag + shadow trial.
   - **NO-GO** → keep humans in the loop; record the measured numbers in the verdict doc.
   Per project doctrine (MEMORY 14.06): **net-harmful / no-op ⇒ don't build it**, and the verdict
   must come with numbers.

---

### Convention sources matched
- `.service`: Type=oneshot · User/Group=root · WorkingDirectory=`…/scripts` ·
  ExecStart=venv `-m dispatch_v2.tools.reassignment_forward_shadow` · Nice=10 · IOSchedulingClass=
  best-effort · MemoryMax=400M · OnFailure=`dispatch-onfailure-alert@%n.service` · append log to
  `…/scripts/logs/` — mirrors `dispatch-pickup-lateness-shadow.service` + `dispatch-prep-bias-
  shadow-monitor.service` (module-style `-m`) + `dispatch-shadow.service.d` resource-limit style.
- `.timer`: OnBootSec=3min · OnUnitActiveSec=3min · Persistent=false · `[Install] WantedBy=
  timers.target` — mirrors `dispatch-pickup-lateness-shadow.timer` cadence/template.
