# FAZA 1 — ETAP 0 RECON (sesja tmux 2, READ-ONLY) — stan na żywo 2026-06-30 ~13:12 UTC

Grounding dla roju audytowego. **Numery linii DRYFUJĄ — każdy agent re-grepuje świeżo, nie cytuje z seed-doców.**

## A. GIT / MULTI-SESJA (C1)
- Repo: `/root/.openclaw/workspace/scripts/dispatch_v2/` · branch **master** · HEAD **`8024705`** (AUTON-02 docs).
- Working tree: BRAK edycji plików silnika `.py`. Tylko `M` na logach/jsonl (epaka, tomtom_poc) + `??` audyt-docy `.md`. Czysto pod audyt.
- Ostatnie commity istotne: `78401ed` (AUTON-02 + wyścig indeksu — C1-git near-miss, `976afbf` provenancja), `5db5bab/6cf14da/b97c35f` (force-recheck czas_kuriera closest-day), `516ca8c` (#2 pickup-slip monitor), `03fd0d5` (#1 carried-first guard), `0eb7822/938d003/21d2247/59fd494` (top10 #3/#4/#5 fizyczna walidacja), paczki Etap 3 (`a0b21b0`/`ce29d44`/`31e4fb1`).
- **tmux — 3 żywe sesje `claude`:** `2` = JA (Faza 1 audyt, read-only) · `3` = "Audit delivery delays for Rana Julek" · `4` = "Debuguj logikę zaproponowanych tras" (= zleceniodawca, Audyt A alokacji, read-only). **Ryzyko kolizji = ZERO z mojej strony** (nic nie edytuję). Ryzyko: sesja 3/4 może edytować plik silnika → DRYF linii. Mitygacja: agenci grepują świeżo. **NIE deployować, NIE restartować, NIE `git add` z wyprzedzeniem (wspólny indeks).**

## B. ŻYWE SERWISY (active running)
| Serwis | Rola |
|---|---|
| `dispatch-shadow` | **silnik decyzyjny**: feasibility + scoring + selekcja + serializer (flagi z flags.json hot-reload) |
| `dispatch-panel-watcher` | recanon/redecide on write/pickup/override; reconcile; panel_packs |
| `dispatch-gps` | feed GPS |
| `dispatch-sla-tracker` | SLA/R6 + delivered_at miernik (#5a) |
| `dispatch-monitor-419` | health |
| `courier-api` | backend apki (cross-repo) |
| `nadajesz-panel`, `nadajesz-ordering` | konsola koordynatora (cross-repo) |
| `gate-audit` | — |
| **`dispatch-telegram`** | **inactive/dead** — Telegram MUTED (świadome, tag `liveness-telegram-intentional-off`). Skutek: `pending_proposals` 3-writer/no-lock „bezpieczny TYLKO bo muted"; re-enable = zmiana postury bez zmiany kodu. |
| **`dispatch-cod-weekly.service`** | **FAILED** (peryferyjne COD; w zakresie „okołosystem" COD — odnotować). |

**Timery LIVE (instrument/decyzja):** `plan-recheck` (5min — K2 „cofacz"), `reassign-global-select` (3min), `reassignment-shadow` (3min — „duch przerzutu"), `carried-first-guard` (3min), `ziomek-time-route-monitor` (10min — parytet konsola↔apka), `objm-lexr6-canary-monitor` (10min), `pickup-lateness-shadow` (5min), `bundle-calib-shadow` (5min), `b-route-shadow` (5min), `pending-resweep-shadow` (1min), `ziomek-pred-calibration` (3min), `prep-bias-shadow-monitor`, `freshness-shadow`, `state-reconcile` (15min), `postpone-sweeper`, `eta-calibration`, paczki (`parcel-merge` 30s, `nadajesz-parcel-shadow` 60s).

## C. STAN FLAG — **EFEKTYWNY W PROCESIE** (3-warstwowy merge; D2 potwierdzone)
Stan decyzyjny ≠ jeden plik. Trzy źródła, różne per-proces:
1. **`flags.json`** (~140 kluczy, hot-reload, czytany przez `dispatch-shadow`): kluczowe → `ENABLE_AUTO_ASSIGN=false`, `PENDING_RESWEEP_LIVE=false` (+`ENABLE_PENDING_RESWEEP=true` shadow), `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY=true`, `ENABLE_NO_GPS_EQUAL_TREATMENT=true`, `ENABLE_EQUAL_TREATMENT_BUCKET=true`, `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP=true`, `ENABLE_OBJM_LEXR6_SELECT=true`/`_SHADOW=false`, `ENABLE_BEST_EFFORT_OBJM_R6_KEY=true`, `ENABLE_PREP_BIAS_TABLE=false`, `ENABLE_DRIVE_MIN_CALIBRATION_V2=false`(main)/shadow=true, `ENABLE_BUNDLE_COLOC_CENTROID_GUARD=true`, `ENABLE_BUG4_RESEQ_SHADOW=true`, `ENABLE_FEAS_CARRY_READMIT=false`, `ENABLE_FAIL12_SCHEDULE_FAILOPEN=true`, `ENABLE_REASSIGN_GLOBAL_SELECT=true`, `ENABLE_REASSIGNMENT_FORWARD_SHADOW=true`, `ENABLE_O2_READY_ANCHOR_SWEEP` **brak w pliku** (env-default OFF), `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE=false`, `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT=false`.
2. **drop-iny `dispatch-plan-recheck.service.d` (13 conf)** — route/canon ENABLE_* env-frozen ON, **NIEOBECNE w flags.json**: `ENABLE_PLAN_CANON_ORDER_INVARIANTS=1`, `ENABLE_PLAN_SEQUENCE_LOCK=1`, `ENABLE_CARRIED_FIRST_RELAX=1`, `ENABLE_GPS_FREE_ANCHOR=1`, `ENABLE_GPS_FREE_ANCHOR_LAST_POS=1`, `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP=1`, `ENABLE_LEX_COMMITTED_WINDOW=1`, `ENABLE_NONCARRIED_DROPOFF_REORDER=1`, `ENABLE_RELAX_COLOC_PICKUP=1`, `ENABLE_PLAN_REAL_PICKED_UP_AT=1`, `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1`, `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH=1`, `ENABLE_CARRIED_AGE_TZ_FIX=1`.
3. **drop-iny `dispatch-panel-watcher.service.d`** — j.w. + `ENABLE_RECANON_ON_WRITE=1`, `ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE=1`, `ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP=1`. **`ENABLE_PANEL_BG_REFRESH=0` (vs `=1` na dispatch-shadow) = per-serwis dywergencja** (wzorzec config-do-1-serwisu).
- `dispatch-shadow.service.d`: `ENABLE_OBJ_REPLAY_CAPTURE=1`, `ENABLE_PANEL_BG_REFRESH=1`, `ENABLE_LGBM_SHADOW=1`, `ENABLE_LGBM_METRICS_READ=1`, `ENABLE_PENDING_POOL=1`.
- **Śmieci w drop-in dirs (klasa K):** `override.conf.bak-pre-veto-retire-coeff100-2026-06-11` (shadow), `unified-route-f1-f2.conf.bak-pre-noreturn-2026-06-13` (plan-recheck), `unified-route-f3.conf.bak-pre-noreturn-2026-06-13` (panel-watcher) — systemd czyta tylko `*.conf`, .bak inertne, ale clutter.
- **Reguła weryfikacji efektywna:** `systemctl show <svc> -p Environment` (agreguje WSZYSTKIE drop-iny) + `FLAG_FINGERPRINT` z logu procesu; NIE wnioskuj z `os.environ.get` modułu ani samego flags.json.

## D. PRZYRZĄDY (rejestr z [[shadow-jobs-registry]] — Faza C odpali oracle, NIE ufa tym statusom na słowo)
- **Świeże jsonl (13:10–13:11):** `b_route_shadow.jsonl`, `bundle_calib_shadow.jsonl`, `bug4_reseq_shadow.jsonl`, `carried_first_guard.jsonl`, `c2_shadow_log.jsonl`, `courier_gps_commitment_shadow.jsonl`, `consumer_stuck_alert_evaluations.jsonl` (64MB!), `courier_match_debug.jsonl` (21MB).
- **Status z rejestru (DO RE-WERYFIKACJI oracle):** 11 „kłamiących" znalezionych 28.06, wszystkie „naprawione" 29.06 (pomiar): `bug4_reseq`, `bundle_calib REVIEW`, `feas_carry_readmit`, `b_route_shadow`, `drive_speed_overshoot`, `objm_lexr6 G2c`, `reassignment_quality`, `would_hard_cap`(compute-but-vanish), `conftest flag-leak`. **VALIDATED:** `gps_delivery_validation`, `bundle_calib CORE`, `min_delivered_at`, `global_allocate`, twin kolejność konsola↔apka, OSRM route==table peak-cert, checkpoint-TZ, last-known-pos, F2 post_shift_overrun live.
- **at-joby:** `atq` = **168** (Jul 2 08:00 bundle-calib reminder), **193** (Jul 1 19:00 reassign Q-gate), **198** (Jul 1 17:00 address text↔pin review), **200** (Jul 3 18:10 objm_lexr6 peak verdict). **189 ODPALIŁ DZIŚ ~07:00** (address ulica↔miasto review) — werdykt do odczytania (Faza C reconcile).
- **FUNDAMENT-CAVEAT (oracle własny):** `delivered_at`/`picked_up_at` = **prawda-PRZYCISKOWA, nie fizyczna** (0/377 auto_geofence GT; odbiór panel ~192s przed GPS). Każdy „realny breach %" = button-truth ±~3min. Oznaczać `proxy-certyfikowany` vs `ground-truth`. OSRM+mult na osi PEAK = CERTYFIKOWANY CZYSTY (n=2644).

## E. ŚWIEŻOŚĆ DANYCH
- `orders_state.json`, `courier_plans.json` (13:10), `courier_last_pos.json` (13:11), `auto_koord_log.jsonl` (12:51), `coordinator_assign_audit.jsonl` (10:42). Peak lunch 11-14 Warsaw (09-12 UTC) świeżo za nami → korpus decyzji bogaty.

## F. BASELINE TESTY (kanoniczna ścieżka, venv dispatch, 95s)
**`3611 passed, 2 failed, 26 skipped, 6 xfailed`** (pytest-timeout brak w venv → bez `--timeout`). **DUŻO zieleńszy niż ostrzegane „~10 pre-existing FAIL"** (29.06 audyt-fix zredukował 10→1). 2 FAIL — OBA klasa „integralność test-suite":
1. **`test_flag_doc_coverage::test_baseline_is_not_stale`** (Assertion) — **ŻYWY DRYF flag-doc**: baseline doc-coverage znów nieświeży (29.06 był ZIELONY po `257d315`). Prawdopodobna przyczyna = dzisiejsze flagi AUTON-02 / force-recheck (`78401ed`, `5db5bab`, `6cf14da`) dorzucone przez inne sesje, niezreconciliowane do baseline. **REALNE FINDING (klasa D dryf flag + N).**
2. **`test_working_override::test_13_real_shift_wins_over_working`** — **znany time-flaky** (MEMORY: „baseline 10→1, tylko time-flaky working_override"). Nie-deterministyczny.

## G. PATH-y dla agentów (ground facts — nie re-derywować)
- venv: `/root/.openclaw/venvs/dispatch/bin/python` · OSRM: `localhost:5001` (route/table, read-only) · state: `/root/.openclaw/workspace/dispatch_state/` · flagi: `/root/.openclaw/workspace/scripts/flags.json` · logi: `/root/.openclaw/workspace/scripts/logs/`
- Cross-repo: konsola `nadajesz_clone/panel` (`fleet_state.py`, `feed.py`, `route_podjazdy`), `courier_api` (`courier_orders.py`, `status_store.py`, panelsync), `courier-app` (Kotlin), most paczki (`papu_dispatch_bridge`? + `parcel` lane). **GRANICA: STOP na dyspozytorni — NIE Mailek, NIE Papu.**

## ZAKRES / DoD (przypomnienie)
ZERO kodu/edycji silnika/restartów/deployów. Produkty = pliki-raporty w `eod_drafts/2026-06-30/`. Dedup-do-źródła PRZED liczeniem. Adversarial verify każdego rootu. Ledger MODUŁ×KLASA 100%. Oracle-lane obowiązkowy. Target+roadmapa = DRAFT. PoC = tylko PLAN. STOP przed naprawą.
