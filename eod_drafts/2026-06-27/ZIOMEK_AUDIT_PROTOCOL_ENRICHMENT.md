# ZIOMEK — AUDIT KNOWLEDGE ENRICHMENT (proposed additive to Przykazanie #0, 2026-06-27)

> Scope note: every claim below is grounded in the 2026-06-27 read-only audit (navigation_notes + rule_interactions + verified findings). Items marked ⚠UNCERTAIN were not fully closed in-audit. Findings that FELL on verification (refuted / corrected to "none") are listed in §B/§E so future sessions stop re-reporting them.

---

## A. System navigation map — the 10 layers → real entry-point functions

Grep symbols, NOT line numbers. Line refs drift heavily; many in-code comments cite stale lines (`linia 285/450/920/1564/2596...`). Trust the symbol.

| Layer | Real entry point (symbol) | Hosting service / process |
|---|---|---|
| Panel ingest → state | `panel_watcher.tick → _diff_and_emit`; all order-state writes via `state_machine.update_from_event` (fcntl LOCK_EX + `_read_state_strict` + `_guarded_write`) | `dispatch-panel-watcher.service` |
| Fleet / GPS resolution | `courier_resolver.build_fleet_snapshot` (single entry) → `dispatchable_fleet` (schedule/override enrichment) | shared (panel-watcher AND shadow) |
| Feasibility (HARD) | `feasibility_v2.check_feasibility_v2` — single HARD gate; returns `('MAYBE','ok_sla_fits')` at success | `dispatch-shadow.service` (`python -m dispatch_v2.shadow_dispatcher`) |
| Route/TSP simulate | `route_simulator_v2.simulate_bag_route_v2`; strategy dispatch: sticky → OR-Tools (`bag_after≥2`) → bruteforce (`bag≤3`) → greedy | called from feasibility_v2 AND plan_recheck |
| Drive-time | `osrm_client.route()` (pair) / `table()` (matrix); traffic applied centrally in `_apply_traffic_multiplier` | shared |
| Per-candidate scoring (SOFT) | nested closure `_v327_eval_courier_inner(cid,cs)` wrapped by `_v327_eval_courier`, run in ThreadPoolExecutor | dispatch-shadow |
| Selection | long in-place reorder pipeline on `feasible` (see §B order) ending at verdict gates | dispatch-shadow |
| Plan re-sequence (twin of feasibility) | `plan_recheck._gen_one_bag_plan` (sweep+canon+stamp+save); `_retime_one_bag_plan` (fixed seq); `_apply_canon_order_invariants` (shared ordering) | `dispatch-plan-recheck.service` (5-min oneshot) AND in-process inside panel-watcher via `redecide_courier`/`recanon_courier` |
| Shadow serialization | `shadow_dispatcher._serialize_result` — ONLY writer of `shadow_decisions.jsonl` (called once, main loop) | dispatch-shadow |
| Telegram/KOORD render | `proposal_sender → format_proposal → _format_proposal_v2` (PROPOSAL_FORMAT_V2=true) | `dispatch-telegram.service` — **INACTIVE+DISABLED (intentionally muted)** |

**The dual/multi-service reality (critical):**
- **`dispatch-shadow`** hosts feasibility + scoring + selection + serialization + LGBM. Its effective decision flags now come almost entirely from `flags.json` (the `override.conf` was gutted by ETAP4 to PANEL_BG_REFRESH + LGBM-shadow + PENDING_POOL).
- **`dispatch-plan-recheck`** (5-min oneshot) and **`dispatch-panel-watcher`** run the SAME `plan_recheck` code but the big **env-frozen route/canon flag surface** lives on THEIR drop-ins, not flags.json. `recanon_courier`/`redecide_courier` execute under panel-watcher's env; the periodic tick under plan-recheck's env — different processes, different env.
- **Console** = `nadajesz_clone/panel/backend/.../fleet_state.py::_build_route` under `nadajesz-panel.service` (uvicorn :8000), flags via `PANEL_FLAG_*` env + drop-ins.
- **App** = `courier_api/courier_orders.py` under `courier-api.service` (:8767), flags via `ENABLE_*` env.
- **czasowka** = `dispatch-czasowka.timer` (1-min oneshot) → `czasowka_scheduler.main`, builds fleet via `dispatchable_fleet` (NOT raw snapshot — #471036 fix). Currently `CZASOWKA_TELEGRAM_DRYRUN=1`.

Three small JSON stores in `dispatch_state/`: `orders_state.json` (flat `{order_id:record}`, locked, courier key = `courier_id` NOT `cid`, NO `orders` wrapper), `pending_proposals.json` (multi-writer, see §E), `global_alloc.json` (single writer, TTL 120s).

---

## B. Rule-interaction graph — how the invariants couple

**Selection pipeline order (memorize; mutations are in-place on `feasible`):**
sort by −score → v325 new-courier → v326 speed/load/multistop → gps_age → `_demote_blind_empty` (MUST be last demote per Sprint-5) → **`_assert_feasibility_first` guard** → late_pickup tiering → OBJM_LEXR6 reorder → `top=feasible[:16]` → PLN shadow → E2 PLN A/B resort → objm/feas-carry shadows → **`FEAS_CARRY_READMIT` LIVE mutation** → ML shadows → verdict gates (`state_likely_stale` → `geometry_blind_fallback` → `MIN_PROPOSE` → `commit_divergence`), first-match-wins early-returns.

**Couplings that must be respected:**

1. **HARD-before-SOFT** is enforced by `_assert_feasibility_first`, BUT only up to its call site. **`FEAS_CARRY_READMIT` (LIVE) deliberately mutates `feasible`/`top[0]` AFTER the guard**, promoting a `feasibility_verdict='NO'` candidate to `'MAYBE'`. This is ACK'd, replay-positive (#483000), and SAFE — it fires only when the live winner already carries a forgiven R6 breach and the readmit's `lex_qual` (R6-breach-primary) is strictly LOWER, i.e. it swaps in the LEAST-breaching candidate (strictly improves HARD-R6). **`feasibility_reason` is preserved** and the `feas_carry_*` markers serialize (auto-prefix). ⇒ Treat `top[0].feasibility_verdict` post-readmit as NOT a reliable hard-pass; check `feasibility_reason`/`objm_r6_breach_max_min`. The two "this breaks feasibility-first" findings were REFUTED as harm — the mutation only lowers breach. Downstream `MIN_PROPOSE` + `commit_divergence` are the residual gates.

2. **R6 cap is TIER-AWARE (T1/2=35 HARD, T3 stretch=40)** and is the *dominant* thermal HARD gate. It **MASKS** the TSP-anchored SLA gate (`feasibility_v2 ~1135`), so the **O2 ready-anchor gap is a metric/selection issue, NOT a live safety hole** — but only as long as R6 stays primary. `r6_thermal_anchor` (route_simulator_v2) is the single ready-anchor source, imported by feasibility. `_count_sla_violations` (route_sim) + the feasibility SLA loop (~1156) + `plan_recheck._o2_key` OFF-branch are **THREE twins anchored on TSP pickup_at + flat-35** — all must move together if O2 lands.

3. **`ENABLE_ETA_QUANTILE_R6_BAGCAP=1` (LIVE)** is the ONE place a >35 ready-anchored order passes R6 (gold bag≤4, p80). A naive ready-anchoring of the SLA gate would re-reject exactly these gold recoveries — co-design required. **This flag is LIVE, gates a HARD path, and has ZERO ON≠OFF test (P2).**

4. **`ENABLE_PACZKA_R6_THERMAL_EXEMPT=1` (LIVE)** spans 3 HARD sites (R6 gate, SLA block, SLA classification) but is ABSENT from the O2 objective (`_compute_o2_metrics`, `_compute_per_order_delivery_minutes`). Exemption incomplete across the SOFT/objective layer; surfaces only on O2 flip. **R6-paczka-exempt now logically spans 4 sites — add O2 overage + cap-Z at the 02.07 flip.**

5. **Equal-treatment** (`ENABLE_NO_GPS_EQUAL_TREATMENT` + `ENABLE_EQUAL_TREATMENT_BUCKET`, both LIVE) couples `_selection_bucket`, `_is_demotable_blind_empty`, `_demote_blind_empty`: no_gps/pre_shift compete by score (bucket 0, not demoted); `'none'` (off-schedule) is deliberately NOT equalized. Change no-gps policy in the SHARED helper `_selection_bucket`, never one sort site — there are stale inline bucket copies (`_objm_lexr6_shadow._bucket`, `_best_effort_fastest_pickup_key`, `_pln_pure_resort._bucket`) that invert this for their consumers.

6. **Committed window R27 ±5 is SOFT.** `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION` is a SOFT tie-break that respects HARD R6 (gated never to increase SLA violations). It is **tick-only** (see §D #10). `ENABLE_LEX_COMMITTED_WINDOW` runs AFTER `CARRIED_FIRST_RELAX` inside `_apply_canon_order_invariants`, anchored on the relaxed sequence (does not regress relax). Both flags ON on both services.

7. **E2 PLN A/B** (`ENABLE_E2_PLN_AB=true`, 20% of orders, `order_id%5==0`) makes `pln_objective` a LIVE input. Its safety depends on `ENABLE_PLN_RESORT_WITHIN_TIER=true` + `ENABLE_PLN_QUALITY_AWARE=true` (both ON) — **flipping either OFF while E2 is ON reintroduces the documented committed-pickup tier-2/GPS-bucket breach.**

8. **Known inversions / do-not-naively-revert (P-1..P-7):** carried-vs-coloc pickup priority (P-1, Opcja 3 with hard freshness cap Z pending); `state_machine COURIER_ASSIGNED` preserves terminal status against post-pickup ASSIGNED races (V3.27.5 Path B); `CARRIED_FIRST_RELAX` honored by panel (TRUST_CANON) but reverted by app (route_podjazdy force carried-first) — see §D #11; P-5 cancel/return recanon is CLOSED (commit `0426706`) — do NOT re-report cancel-recanon as open.

9. **MIN_PROPOSE silence gate is currently NEUTERED**: `ENABLE_ALWAYS_PROPOSE_ON_SATURATION=true` makes `_always_propose_on()` True and the gate condition includes `and not _always_propose_on()`. Any reasoning about a SOFT penalty causing silence must check BOTH this flag and the per-penalty `_GATE_RANKING_DELTA_EXCLUSIONS` tuple.

**REFUTED couplings (stop re-reporting):** prep-bias R6 anchor "twin-path gap" (it is a DELIBERATE feasibility-only gate-stricter conservatism on an already-1:1 baseline, documented `route_simulator_v2 r6_thermal_anchor` docstring); O2 cap "not escalation-aware" as a bug (O2 is a continuous SOFT freshness objective that never gates feasibility — penalizing the 35-40 band uniformly is correct for a minimize-overage objective); czasówka 3-definition "divergence" (`order_type=='czasowka'` ⟺ prep≥60 at source `panel_client`, harm path unreachable); reassign-loser "console phantom" (console bag-filters by `courier_id` exactly like engine, phantom never renders).

---

## C. Czego NIE wolno pominąć — non-skippable checks per change class

Distilled from real near-misses this audit surfaced.

**Any decision_flag-gated penalty subtracted from `final_score`:**
- Add it to `_GATE_RANKING_DELTA_EXCLUSIONS` (INV-GATE-SCORE-DELTA) — but note the mechanism assumes `metric == amount ADDED` (`sc = sc − m.get(key)` with `final_score += negative var`); a POSITIVE-stored, SUBTRACTED penalty (like `post_shift_overrun`) is OUTSIDE the contract and must be reframed, not naively appended.
- **Serialize it** — `post_shift_overrun_*` is computed-always "for shadow visibility" but `grep shadow_decisions.jsonl = 0`; its dedicated forward-replay verdict tool buckets everything to `no_pen` and can NEVER produce a GO/NO-GO. A compute-always-for-shadow metric that never serializes **defeats ETAP-5 replay-validation** before you even flip.

**Any R6 / feasibility / SLA / O2 change:**
- Move the THREE SLA-anchor twins together: `route_simulator._count_sla_violations`, `feasibility_v2` SLA loop (~1156), `plan_recheck._o2_key`.
- Co-design with `ENABLE_ETA_QUANTILE_R6_BAGCAP` (gold4 recovery) and `ENABLE_PACZKA_R6_THERMAL_EXEMPT` (4 sites incl. O2).
- Add an **ON≠OFF feasibility test** (borderline 36-min gold bag≤4 recovered ON / rejected OFF) AND register the flag in `ETAP4_DECISION_FLAGS` (see §C registry rule). `ENABLE_ETA_QUANTILE_R6_BAGCAP` and `ENABLE_PLAN_RECHECK_TIER_DWELL` are LIVE with NO such test today.
- Verify R6 twin parity across feasibility ↔ greedy/bruteforce ↔ plan_recheck (`plan_recheck` uses flat-35 reorder gate, no eta_quantile recovery — bounded but divergent).

**Any new flag:**
- If read via `C.flag(name, default)` at a real decision site and non-default in `flags.json` → it MUST be in `ETAP4_DECISION_FLAGS`, or conftest leaks production flag values into the WHOLE test suite AND it is invisible to `flag_fingerprint`. This is the **3rd recurrence of this class** (BEST_EFFORT_OBJM_R6_KEY twice). 24 live wired flags currently miss the registry. Use `tools/flag_registry.py` (parses env from units + drop-ins, last-wins).

**Any serializer touch:**
- Edit BOTH **LOCATION A** (`_serialize_candidate`, alternatives) and **LOCATION B** (inline best dict in `_serialize_result`). They are the called-out A/B twin; both end with `_propagate_prefixed_metrics`.
- A new metric reaches the log ONLY if its key prefix is in `_AUTO_PROP_PREFIXES` OR it is an explicit key in BOTH A and B. Families `v324a_/c2_/d2_/sla_violations_*/end_of_day_salvage_` have NO covering prefix — explicit-or-vanish. Verify presence by regenerating a record and grepping (ETAP-4 evidence).

**Any plan/recanon/route change:**
- The recanon completeness map is "4 handlers: assign/deliver/pickup/cancel" (`panel_watcher._save_plan_on_assign_signal / _advance_plan_on_deliver / _remove_stops_on_return / _update_plan_on_picked_up`, each → `recanon_courier`). assign+pickup ALSO call `redecide_courier`; deliver+return do not (intentional).
- `recanon_courier` CANNOT prune (retime-only, `set(oids) ⊆ covered`); any bag-SHRINKING transition (cancel/deliver/reassign-loser) must call `plan_manager.remove_stops`/`advance_plan` BEFORE recanon.
- Stop-order logic lives in THREE files that must change together: engine canon (`plan_recheck._apply_canon_order_invariants`), app (`route_podjazdy.order_podjazdy` via courier_orders), panel (`fleet_state._build_route`/`_order_from_plan_seq`). `route_podjazdy` is shared by the APP ONLY — the panel is a hand-synced PARALLEL re-implementation (its docstring "deleguje tutaj" is FALSE). The only equivalence enforcer is `ziomek_time_route_monitor` (runtime, 44-75 mismatches/day, stops 2026-07-10).
- Module-level `plan_recheck` ENABLE_* are env-frozen-at-import → mirror route/canon flags in BOTH `dispatch-plan-recheck.service.d` AND `dispatch-panel-watcher.service.d`, else the same function behaves differently per process.

**Any traffic / drive-time change:**
- Treat the OSRM **fallback base as ALREADY traffic-adjusted** (`FALLBACK_BASE_SPEEDS_KMH` is "oparte na KORKACH"). Touch both twins (`osrm_client` fallback + `dispatch_pipeline` haversine fallback) and re-run a degraded-mode replay (force circuit open) — see §D #12.

---

## D. New partial-change anti-patterns (extend the #1-#9 list)

**#10 — Selection-layer HARD-bypass after the feasibility-first guard.** A mutation (`FEAS_CARRY_READMIT`) re-admits a `verdict='NO'` candidate to `top[0]` and relabels `→'MAYBE'` AFTER `_assert_feasibility_first` already ran. The guard is structurally blind to anything past its call site. *Lens:* `_assert_feasibility_first` is NOT a complete guarantee — any "no NO in pool" reasoning must account for post-guard mutations. (This instance is SAFE/ACK'd, but the pattern is the trap.)

**#11 — Twin-path render divergence, fixed in 1 of 2 display surfaces.** Console `_build_route` got the carried-first-relax raw-canon branch (`_order_from_plan_seq`, 2026-06-22); the app's `route_podjazdy.order_podjazdy` did NOT and still force-front-loads carried. **Live, P2** (`ziomek_time_route_monitor.jsonl` 2026-06-28 shows cid 509/471/370 console=pickup-en-route-first vs app=deliver-carried-first). The courier and coordinator see different stop order; this is the courier-facing driving order, not display-only. The code comment asserting app==console verbatim is now false.

**#12 — Multiplier bolted onto a fallback whose base was never re-baselined.** OSRM fallback path applies BOTH the congestion-encoded bucket speed (20 km/h rush) AND `get_traffic_multiplier` (×1.55) → ~1.5× drive_min inflation **exactly at peak**, feeding R6/committed/ETA → spurious breaches → more KOORD/best-effort-ALERT. Latent (fallback ~0 when OSRM healthy) but amplifies at the worst moment. **P2.**

**#13 — Same-restaurant grouping double-inserts the super-pickup in legacy planners.** `bag_pickup_idxs_by_oid` maps grouped oids to ONE super-pickup idx; `_bruteforce_plan.to_place.extend(values())` and `_greedy_plan` Step-1.5 insert it twice; `_simulate_sequence` walks with no dedup → double drive leg + double dwell + later `pickup_at` → inflated duration / false `feasibility=NO`. Masked by OR-Tools ON, **un-masked by the documented 1-flag rollback** `ENABLE_V326_OR_TOOLS_TSP=False` (grouping stays default-ON). Also fires today on the rare OR-Tools INFEASIBLE→greedy fallback. **P2.** *Rule:* if you flip OR-Tools off, ALSO flip `ENABLE_V326_SAME_RESTAURANT_GROUPING` off.

**#14 — Config schema fixed on the read-call but not the consumer's assumed shape.** `postpone_sweeper` reads `orders_state.get('orders',{})` (no such wrapper — flat dict) and `current.get('cid')` (field is `courier_id`). Commit `4c3c140` fixed the read-call but left both schema assumptions → POSTPONE_RESOLVED + re-emit paths are structurally dead (0× in 235k log lines). **P2 latent** (dormant only because Telegram postpone is muted). *Lens:* when you migrate a load-call, re-validate every `.get()` against the live on-disk schema.

**#15 — Shadow twin validating a live change applies different guards than the live path.** `_feas_carry_blind_shadow` ranks over an UNCAPPED blocking pool while live `_feas_carry_readmit_pick` uses a cap≤40 pool. (Verdict: this specific case was REFUTED as harmful because the live path has its own audit trail and the pre-flip replay applied the cap explicitly — but the PATTERN stands: a shadow that omits a live guard mismeasures. Always apply the SAME guards in the shadow twin.)

**#16 — Compute-always-for-shadow metric never serialized → flip-validation gate silently broken.** `post_shift_overrun_*`, `would_hard_cap`/`hard_tier_bag_cap` (+ `cs_tier_label/cs_tier_bag`), `end_of_day_salvage*` (HARD-relaxation activation, effectively LIVE via flags.json), and the `c2_/d2_/sla_violations_*` families are computed (some "ZAWSZE widoczność w shadow") but dropped by the serializer. Breaks the "metryka w shadow_decisions.jsonl" proof for those exact pre-flip contracts. **Systematically biases the decision log toward SOFT (`bonus_` prefix survives) and against HARD.** Mostly P3 (much is reconstructable from constants/serialized siblings), but `post_shift_overrun` is the sharpest (P2, drives best_effort sort, no proxy).

---

## E. Gotchas & landmines — quick reference

| Trap | Reality | Where |
|---|---|---|
| Flag "live" from flags.json / env-default | For `flag()`/`decision_flag()` keys: flags.json > module const > False (hot-reload). For env-frozen module consts (all `plan_recheck` route/canon flags, `common.ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER`, `ENABLE_V326_R07_CHAIN_ETA`, czasowka, geocode infra, LGBM_SHADOW/READ) → env only, NOT hot-reloadable, NOT in fingerprint. **Read the running process.** | `common.py` flag machinery; `tools/flag_registry.py` |
| `live_eta_cache.py` "feeds decisions" | Display-only — written by shadow + plan_recheck, read by app build_view + console; does NOT feed score/feasibility. Verified. | — |
| App `eta_committed`/`eta_pickup` (FROZEN_PICKUP_ETA) | Display-only on courier surface; status write-back via `panel_sync.py` (status codes only), never re-derives committed time into engine. | courier_orders |
| `BUILD_VIEW_TRUST_CANON_ORDER` "controls app order" | DEAD/masked: `ENABLE_APP_ROUTE_FROM_CONSOLE=1` short-circuits via `route_podjazdy` (sets `_console_done=True`) before the TRUST_CANON branch. Check `route_podjazdy`, not this flag. | courier_orders ~1105/1146 |
| `ENABLE_LGBM_PRIMARY` flip = LGBM becomes decider | NO — no selection consumer; logging-only, same as TWOMODEL_SHADOW. With TWOMODEL off, flipping PRIMARY alone = zero compute. | ml_inference / dispatch_pipeline ~6202 |
| `wave_scoring.py` looks live | Confirmed DEAD (module header Z-22). Wave realized by `v319h_bug2_continuation`. CLAUDE.md forbids touching without ACK. | — |
| `ENABLE_V326_R07_CHAIN_ETA` flip | High blast radius: `chain_eta` overwrites `eta_pickup_utc` (decision var) + becomes R-01 pickup_ref. Env-frozen OFF, not in fingerprint. Full protocol, not a shadow experiment. | chain_eta / feasibility_v2 ~665 |
| `inv_feasibility_first_violation` | P0 invariant marker written to metrics but NOT serialized — only in journalctl (primary `log.error` works). Marker is a tripwire that should never fire. | dispatch_pipeline ~2465 |
| `effective_start_at`/`pre_shift_clamp_applied` | LOCATION-B only (best), missing from LOCATION-A (alternatives). | shadow_dispatcher ~698 |
| `pending_proposals.json` atomicity | 3 RMW writers (telegram, shadow store, postpone), NO cross-process lock, telegram + store SHARE `{path}.tmp`. Safe ONLY because telegram is muted. Re-enabling telegram flips data-integrity posture with no code change. Entries removed by 30-min TTL only (never pop-on-assign) — every consumer must re-validate `status=='planned'` vs orders_state (only `pending_global_resweep` does). | pending_proposals_store / telegram_approver |
| traffic_v2 shadow route totals | Aggregate the WHOLE NxN OSRM matrix, not the chosen plan legs (~N× inflated). Per-leg + avg/max/min v2 mult are valid; route totals are not. Shadow/offline only. | osrm_client / traffic_v2_aggregator |
| last-known-pos TTL | Measured from tick SAVE-time, not observation-time → reported `pos_age` understates true age (can reach ~50min). Latent (GPS_AGE_DISCOUNT OFF). | courier_resolver ~1223 |
| Checkpoint timestamps | `picked_up_at`/`delivered_at` are Warsaw-NAIVE → parse via `_parse_checkpoint_ts` (`ENABLE_CHECKPOINT_TS_WARSAW_PARSE` LIVE). GPS ts + store ts are aware-UTC (plain fromisoformat) — asymmetry intentional. | courier_resolver ~42 |
| `pos_source` | DECISION variable, not display (gates FAIL-12 schedule-failopen, R06 carry penalty, bucket keys, demote). Any relabel is cross-layer. | feasibility_v2 / dispatch_pipeline |
| `drive_min_calibration` MAIN | Intentionally OFF (the +13min was prep/declared-pickup, an artifact). Module docstring is STALE; trust the flags.json comment. Do NOT flip MAIN=true. | drive_min_calibration |
| Repo `systemd/` vs `/etc` | Partial drifting mirror. `dispatch-shadow override.conf` is a stale pre-ETAP4 snapshot. ALWAYS diff before re-applying. Deploys edit `/etc` in place with `.bak`, never `cp` from repo. | — |
| Test pollution in dispatch.log | pytest writes to PRODUCTION `dispatch.log`; "OSRM circuit OPEN" / "synthetic" bursts are TEST artifacts. Filter synthetic timestamps when assessing OSRM health. | — |

**REFUTED landmines (do not chase):** `ziomek_time_route_monitor` TRUST_CANON env "unexplained" (set by `.service.d/trust-canon.conf`, the author missed the drop-in dir); `flags-repo-shadow-override-stale` impact (decision_flag reads flags.json first → env re-add is inert; `OBJ_SPAN_COST_COEFF` is not a flags.json key); `cap-build-view-trust-canon-dead-flag` as P2 (it's a harmless fail-soft fallback consumer, parity still achieved on the live path).

---

## F. Verification recipe — exact commands

**Is a flag LIVE in-process?**
1. `grep -rln "<run_module>" /etc/systemd/system` → which service runs the code.
2. `systemctl show <unit> -p Environment` (aggregates ALL `.service.d/*.conf` — do NOT grep the bare `.service` or individual drop-ins; you WILL miss flags). Or `cat /proc/<MainPID>/environ | tr '\0' '\n'`.
3. Read `flags.json`.
4. Resolution: `flag()`/`decision_flag()` → flags.json > module const > False. Env-frozen module const (`= os.environ.get(...,'0')=='1'`) → env ONLY, ignores flags.json.
5. For the authoritative shadow set, read the `FLAG_FINGERPRINT` line in `shadow.log` at restart — BUT it only covers `ETAP4_DECISION_FLAGS + _FINGERPRINT_EXTRA_FLAGS`; ~24 wired flags + all `*_THERMAL_EXEMPT`/`*_R6_BAGCAP`/`FEAS_CARRY_READMIT` are read hot and may NOT appear. Confirm those in flags.json + process env.
6. Use `tools/flag_registry.py` to cross-check env-frozen partial-coverage flags across units.

**Twin parity (route/canon flags):**
- `diff <(systemctl show dispatch-plan-recheck.service -p Environment) <(systemctl show dispatch-panel-watcher.service -p Environment)` — every route/canon flag must appear in BOTH. `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH` is legitimately plan-recheck-only (consumed only in the tick, never in `redecide_courier`).
- App↔console parity: `tail dispatch_state/ziomek_time_route_monitor.jsonl` field `q3_route_mismatches`.

**Recanon symmetry:**
- For each of the 4 handlers in `panel_watcher`, confirm bag-SHRINKING transitions call a `plan_manager` prune BEFORE `recanon_courier`. `bag_signature` = `sorted(oid, picked_up_bool)` only — it ignores committed time and courier identity, so committed-edit and reassign-loser are NOT detected by signature equality.

**Metric serialization (does a new key reach shadow_decisions.jsonl?):**
1. Does the key prefix match `_AUTO_PROP_PREFIXES` (`shadow_dispatcher.py:190-251`)? If yes → auto-propagates to A and B.
2. If no → it MUST be an explicit key in BOTH `_serialize_candidate` (A) and the inline best dict (B). Families `v324a_/c2_/d2_/sla_violations_*/end_of_day_salvage_` have NO prefix.
3. Confirm empirically: `grep -c "<key>" logs/shadow_decisions.jsonl` over a fresh window. `0` = dropped.
4. Result-level fields (verdict, `*_redirect`, `*_shadow`) come via `getattr(result, ...)`, NOT metrics — separate hand-built dicts, do not rescue dropped candidate.metrics keys.

**Flag has a real ON≠OFF behavioral test?**
- `grep -rn "<FLAG_NAME>" tests/` (the NAME, not the underlying function). A function-level math test is a coverage illusion; you need a gate test toggling the flag and asserting different behavior. Beware `_KNOWN_XFAIL_SCRIPTS` whole-subprocess xfail (`test_proposal_selection_v316`, `test_v319d_read_integration`) — their passing assertions are non-gating; rely on the native-pytest `_demote_blind_empty`/equal-treatment files for the real golden.

**Replay/job reconciliation:** `atq` + `systemctl list-timers | grep -E 'review|monitor'` vs `shadow-jobs-registry.md`; an odd-numbered `at` job that fired → read its verdict file, mark DONE, don't re-create.