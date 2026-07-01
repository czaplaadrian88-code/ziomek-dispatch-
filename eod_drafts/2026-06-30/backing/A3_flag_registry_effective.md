# A3 — REJESTR FLAG ze STANEM EFEKTYWNYM W PROCESIE (3-warstwowy merge)

**Faza A / OS-3 (inwentarz) · TRYB READ-ONLY · sesja tmux 2 · 2026-06-30**
**Zasila: klasę D (dryf flag deklarowane≠efektywne) + klasę E (kłamiące przyrządy: fingerprint) + Fazę D.**

Wszystkie cytaty `plik:linia` ze świeżego grepu 2026-06-30 (HEAD `8024705`). Wartości EFEKTYWNE zmierzone:
`systemctl show <svc> -p Environment` (agreguje WSZYSTKIE drop-iny) + import `common` w venv + `decision_flag()`/`C.flag()` semantyka.

---

## 0. TL;DR (dla Fazy B/C/D)

1. **Stan decyzyjny ≠ jeden plik.** 3 warstwy, różne per-proces: (1) `flags.json` (hot-reload, 198 kluczy nie-`_`), (2) drop-iny systemd `Environment=` (env-frozen, restart wymagany), (3) stała modułu w `common.py`/`plan_recheck.py`/`panel_client.py` (fallback, env-default).
2. **`decision_flag()` / `C.flag()` precedencja = flags.json → stała modułu → False** (common.py:348-361). Hot-reload działa TYLKO dla kluczy obecnych w flags.json. Stałe env-frozen = restart.
3. **`flag_fingerprint()` (common.py:364-371) widzi TYLKO 63 flagi** (ETAP4=59 + _FINGERPRINT_EXTRA=4). **~24 env-frozen route/canon + USE_V2_PARSER + OR_TOOLS/GROUPING SĄ POZA fingerprintem** → „fingerprinty MUSZĄ być identyczne cross-proces" to **fałszywe zapewnienie** (klasa E — kłamiący przyrząd).
4. **Conftest-leak D4 jest 3× większy niż „~24" z designu:** zmierzono **71 `ENABLE_*` + 41 nie-`ENABLE` bool** kluczy flags.json POZA wszystkimi rejestrami (ETAP4/INFRA/NUMERIC/FINGERPRINT_EXTRA). Spory podzbiór to **flagi DECYZYJNE** (selekcja/scoring/feasibility/filtr floty), nie tylko shadow.
5. **`ENABLE_BEST_EFFORT_OBJM_R6_KEY` NIE JEST już martwa (#1).** Gałąź realnie wpięta `dispatch_pipeline.py:6771`, flaga w ETAP4 (l.162), flags.json=True → **effective=True, LIVE**.
6. **1 prawdziwa inwersja maskująca:** `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` — stała modułu env-default **True** (common.py:2805), flags.json **False** maskuje → effective False. Usunięcie klucza z flags.json = cichy FLIP na ON (utrata dyrektywy ALWAYS-PROPOSE). Klasa M+I.

---

## 1. WARSTWA 2 — DROP-INY systemd (env-frozen, zmierzone `systemctl show`)

### 1a. dispatch-shadow.service (silnik decyzyjny)
| Źródło | Env ustawione |
|---|---|
| main unit `dispatch-shadow.service` | `ENABLE_OBJ_REPLAY_CAPTURE=1` |
| `…d/override.conf` | `ENABLE_PANEL_BG_REFRESH=1`, `ENABLE_LGBM_SHADOW=1`, `ENABLE_LGBM_METRICS_READ=1`, `ENABLE_PENDING_POOL=1` |

Pełny zmierzony Environment shadow = **5 flag** (`ENABLE_OBJ_REPLAY_CAPTURE ENABLE_PANEL_BG_REFRESH=1 ENABLE_LGBM_SHADOW ENABLE_LGBM_METRICS_READ ENABLE_PENDING_POOL`).
**Shadow NIE ustawia ŻADNEGO route/canon flagu ani USE_V2_PARSER.**

### 1b. dispatch-plan-recheck.service (timer 5min — K2 „cofacz")
Zmierzony Environment (14 flag, wszystkie z drop-inów `…d/*.conf`):
`ENABLE_CARRIED_AGE_TZ_FIX=1 ENABLE_CARRIED_FIRST_RELAX=1 ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1 ENABLE_GPS_FREE_ANCHOR=1 ENABLE_GPS_FREE_ANCHOR_LAST_POS=1 ENABLE_LEX_COMMITTED_WINDOW_SHADOW=1 ENABLE_LEX_COMMITTED_WINDOW=1 ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH=1 ENABLE_NONCARRIED_DROPOFF_REORDER=1 ENABLE_RELAX_COLOC_PICKUP=1 ENABLE_PLAN_REAL_PICKED_UP_AT=1 ENABLE_PLAN_SEQUENCE_LOCK=1 ENABLE_PLAN_CANON_ORDER_INVARIANTS=1 ENABLE_NO_RETURN_TO_DEPARTED_PICKUP=1`

### 1c. dispatch-panel-watcher.service (recanon/redecide on write/pickup/override)
Zmierzony Environment (16 flag + PYTHONPATH):
`ENABLE_CARRIED_AGE_TZ_FIX=1 ENABLE_CARRIED_FIRST_RELAX=1 ENABLE_GPS_FREE_ANCHOR_LAST_POS=1 ENABLE_LEX_COMMITTED_WINDOW_SHADOW=1 ENABLE_LEX_COMMITTED_WINDOW=1 ENABLE_PANEL_BG_REFRESH=0 USE_V2_PARSER=1 ENABLE_RECANON_ON_WRITE=1 ENABLE_NONCARRIED_DROPOFF_REORDER=1 ENABLE_RELAX_COLOC_PICKUP=1 ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE=1 ENABLE_GPS_FREE_ANCHOR=1 ENABLE_PLAN_REAL_PICKED_UP_AT=1 ENABLE_PLAN_CANON_ORDER_INVARIANTS=1 ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP=1 ENABLE_NO_RETURN_TO_DEPARTED_PICKUP=1`

### 1d. PER-SERWIS DYWERGENCJE (drop-in route/canon family) — tabela różnic
Wszystkie definiowane w `plan_recheck.py` jako module-level `os.environ.get(<name>,"0")=="1"` (default OFF, **odczyt RAZ przy imporcie = env-frozen, restart wymagany**). `panel_watcher.py` importuje `plan_recheck` w runtime (l.618/627/662/690/721) → dziedziczy te module-globals czytane względem **własnego** env procesu watchera.

| Flaga | def @plan_recheck.py | shadow | plan-recheck | panel-watcher | uwaga |
|---|---|---|---|---|---|
| ENABLE_CARRIED_AGE_TZ_FIX | :444 | (brak→OFF*) | **1** | **1** | |
| ENABLE_CARRIED_FIRST_RELAX | :425 | (brak→OFF*) | **1** | **1** | carried-first relax |
| ENABLE_GPS_FREE_ANCHOR | :347 | (brak→OFF*) | **1** | **1** | |
| ENABLE_GPS_FREE_ANCHOR_LAST_POS | :354 | (brak→OFF*) | **1** | **1** | |
| ENABLE_PLAN_REAL_PICKED_UP_AT | :359 | (brak→OFF*) | **1** | **1** | |
| ENABLE_PLAN_SEQUENCE_LOCK | :363 | (brak→OFF*) | **1** | **BRAK** | ⚠ tylko plan-recheck |
| ENABLE_PLAN_CANON_ORDER_INVARIANTS | :368 | (brak→OFF*) | **1** | **1** | |
| ENABLE_NO_RETURN_TO_DEPARTED_PICKUP | :377 | (brak→OFF*) | **1** | **1** | |
| ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE | :394 | (brak→OFF*) | **BRAK** | **1** | ⚠ tylko watcher |
| ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP | :401 | (brak→OFF*) | **BRAK** | **1** | ⚠ tylko watcher |
| ENABLE_RECANON_ON_WRITE | :412 | (brak→OFF*) | **BRAK** | **1** | ⚠ tylko watcher |
| ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION | :? (committed-propagation.conf) | (brak→OFF*) | **1** | **BRAK** | ⚠ tylko plan-recheck |
| ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH | :? (live-eta-refresh.conf) | (brak→OFF*) | **1** | **BRAK** | ⚠ tylko plan-recheck |
| ENABLE_NONCARRIED_DROPOFF_REORDER | :488 | (brak→OFF*) | **1** | **1** | route-reorder-fix-mk |
| ENABLE_RELAX_COLOC_PICKUP | :475 | (brak→OFF*) | **1** | **1** | route-reorder-fix-mk |
| ENABLE_LEX_COMMITTED_WINDOW | :458 | (brak→OFF*) | **1** | **1** | |
| ENABLE_LEX_COMMITTED_WINDOW_SHADOW | :457 | (brak→OFF*) | **1** | **1** | shadow twin OBOK live ON |

`*` **shadow „brak→OFF" = LATENTNE, nie aktywne:** zweryfikowano grepem że ŻADEN plik ścieżki decyzyjnej shadow (`dispatch_pipeline.py`, `scoring.py`, `feasibility_v2.py`, `route_simulator_v2.py`, `shadow_dispatcher.py`, `courier_resolver.py`) nie referuje tych nazw — reorder/canon żyje wyłącznie w `plan_recheck.py`, którego shadow nie wykonuje w hot-path. **Ryzyko = latentne:** gdyby kiedyś shadow zaimportował helper z `plan_recheck`, czytałby je z własnego (pustego) env = OFF → cicha dywergencja. Faza C: twin-trace.

### 1e. Inne per-serwis env (nie-route)
| Flaga | shadow | panel-watcher | plan-recheck | klasa |
|---|---|---|---|---|
| **ENABLE_PANEL_BG_REFRESH** | **1** | **0** | brak | per-proces ZAMIERZONE (override.conf komentarz: „watcher ma własny cykl loginu"). NIE w fingerprincie. |
| **USE_V2_PARSER** | brak→`panel_client.py:93` OFF | **1** | brak | ⚠ D2/J — patrz §5 |
| ENABLE_OBJ_REPLAY_CAPTURE | 1 (main unit) | brak | brak | telemetria replay (capture), shadow-only |
| ENABLE_LGBM_SHADOW / _METRICS_READ | 1 | brak | brak | telemetria LGBM cień |
| ENABLE_PENDING_POOL | 1 | brak | brak | late-binding pending_pool, shadow-only |

### 1f. Drop-in dirt (klasa K — szczątkowy)
- `dispatch-shadow.service.d/override.conf.bak-pre-veto-retire-coeff100-2026-06-11` (inertny, systemd czyta tylko `*.conf`)
- `dispatch-plan-recheck.service.d/unified-route-f1-f2.conf.bak-pre-noreturn-2026-06-13`
- `dispatch-panel-watcher.service.d/unified-route-f3.conf.bak-pre-noreturn-2026-06-13`

---

## 2. WARSTWA 1 — flags.json (KANON hot-reload) vs WARSTWA 3 (stała modułu)

**Liczby zmierzone (import common w venv):**
- flags.json nie-komentarz keys: **198**
- ETAP4_DECISION_FLAGS: **59** (52 obecnych w flags.json + 7 absent → fallback do stałej modułu)
- TEST_ISOLATED_INFRA_FLAGS: 3 · FLAGS_JSON_NUMERIC_OVERRIDES: 25 · _FINGERPRINT_EXTRA_FLAGS: 4

### 2a. ETAP4 (59) — wzorzec „flags.json=KANON, stała=fallback OFF"
**52/59 obecne w flags.json.** Dla większości: `const=False` w common.py (l.236-264), `json=True` → **effective=True**. To NIE jest bug — to zaprojektowany wzorzec (komentarz common.py:229-235: stała = bezpieczny fallback OFF + inwariant testu + izolacja conftest). Pełna lista effective w §2d.

**7 ETAP4 NIEOBECNE w flags.json (effective z stałej modułu, NIE hot-flippowalne bez edycji kodu lub dopisania klucza):**
| Flaga | const | effective | uwaga |
|---|---|---|---|
| AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO | **True** | **True** | AUTON-02 profil strict (default ON) |
| AUTO_ASSIGN_REQUIRE_MARGIN | **True** | **True** | AUTON-02 profil strict (default ON) |
| ENABLE_PLN_COURIER_PAY | False | False | |
| ENABLE_OBJ_FOOD_AGE_HARD_SLA | False | False | |
| ENABLE_POST_SHIFT_OVERRUN_PENALTY | False | False | |
| ENABLE_O2_READY_ANCHOR_SWEEP | False | False | recon „env-default OFF" — DOKŁADNIE: literał-const OFF (common.py:249), NIE env-read |
| ENABLE_CARRIED_FIRST_RELAX_READY_ANCHOR | False | False | case Rećki, default OFF |

### 2b. (b) Jedyna PRAWDZIWA inwersja json↔const (reszta = zaprojektowany wzorzec)
| Flaga | json | const (def) | effective | werdykt |
|---|---|---|---|---|
| **ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE** | **False** | **True** (env-default „1", common.py:2805) | **False** | ⚠ **MASKUJĄCA (klasa M+I).** flags.json=False maskuje const=True. Usunięcie klucza z flags.json → `decision_flag` spada na const=True → **verdict-gate FLIP na ON** = utrata dyrektywy ALWAYS-PROPOSE (KOORD-redirect wraca). Kruche. |

Wszystkie pozostałe „json≠const" (≈40 flag) to wzorzec ETAP4 (const=False fallback, json=True kanon) — **NIE inwersja**.

### 2c. flagi „default-OFF-w-kodzie ale flags.json wymusza OFF" (świadome, nie bug)
`ENABLE_OBJ_PICKUP_FRESHNESS=False`, `ENABLE_OBJ_DELIVERY_FOOD_AGE(_SHADOW)=False`, `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT=False` (const env-default „0", common.py:2881), `ENABLE_PREP_BIAS_TABLE/REPO_COST_LIVE/SOON_FREE_CANDIDATE/BAG_TIME_FAIRNESS_SCORING/GPS_AGE_DISCOUNT/PICKUP_COORDS_FROM_PANEL/BUNDLE_VALUE_SCORING/FIX_C_ADDITIVE_PENALTY/AUTO_ASSIGN/FEAS_CARRY_READMIT=False` — wszystkie świadomie OFF (shadow-first / czeka na ACK).

### 2d. ETAP4 — pełna tabela effective (zmierzona)
ON (effective=True, 32): BUNDLE_DELIV_SPREAD_CAP, R1_PROGRESSIVE_CLIP, V319H_CONTINUATION_GUARD, A2_RELIABILITY_SOFT_SCORE, FAIL12_SCHEDULE_FAILOPEN, F4_COURIER_POS_PICKUP_PROXY, F4_COURIER_POS_INTERP, CHECKPOINT_TS_WARSAW_PARSE, C2_NEG_GAP_DECAY, PRE_SHIFT_DEPARTURE_CLAMP, PRE_SHIFT_EQUAL_NO_PENALTY, OBJ_SPAN_COST, OBJ_R6_SOFT_DEADLINE, OBJ_F3_BEST_EFFORT_R6_KOORD, BUNDLE_SYNC_SPREAD, EQUAL_TREATMENT_BUCKET, FLEET_LOAD_GOVERNOR, R5_PICKUP_DETOUR_PENALTY, ETA_QUANTILE_R6_BAGCAP, R6_BREACH_SHADOW_LOG, E2_PLN_AB, ALWAYS_PROPOSE_ON_SATURATION, R_PACZKI_FLEX, PLN_QUALITY_AWARE, END_OF_DAY_SALVAGE, BEST_EFFORT_OBJM_R6_KEY, BUNDLE_DELIVERY_COLOCATION, BUNDLE_COLOC_CENTROID_GUARD, RESERVE_AWARE_TIEBREAK_SHADOW, HARD_TIER_BAG_CAP, PACZKA_R6_THERMAL_EXEMPT, R_RETURN_TO_RESTAURANT_VETO, PLAN_RECHECK_TIER_DWELL, NO_GPS_EQUAL_TREATMENT, OBJM_LEXR6_SELECT, CZASOWKA_UWAGI_DEADLINE_SHADOW, DELIVERED_RESURRECTION + AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO/_MARGIN (z const).
OFF (effective=False): OBJ_PICKUP_FRESHNESS, OBJ_DELIVERY_FOOD_AGE, OBJ_DELIVERY_FOOD_AGE_SHADOW, COMMIT_DIVERGENCE_VERDICT_GATE, DIFFICULT_CASE_KOORD_REDIRECT, PREP_BIAS_TABLE, REPO_COST_LIVE, SOON_FREE_CANDIDATE, BAG_TIME_FAIRNESS_SCORING, GPS_AGE_DISCOUNT, PICKUP_COORDS_FROM_PANEL, BUNDLE_VALUE_SCORING, FIX_C_ADDITIVE_PENALTY, AUTO_ASSIGN, PLN_COURIER_PAY, OBJ_FOOD_AGE_HARD_SLA, POST_SHIFT_OVERRUN_PENALTY, FEAS_CARRY_READMIT, O2_READY_ANCHOR_SWEEP, CARRIED_FIRST_RELAX_READY_ANCHOR.

---

## 3. (c) CONFTEST-LEAK D4 — `ENABLE_*` w flags.json POZA WSZYSTKIMI rejestrami

**ZMIERZONO 71 `ENABLE_*` (+ 41 nie-`ENABLE` bool).** Design mówił „~24" — **realny rozmiar 3×.** Te flagi:
nie są w ETAP4 → **conftest `_isolate_flags_json` ICH NIE STRIPUJE** → test mający stałą modułu OFF i tak dziedziczy żywy flags.json (prod-True). To dokładnie klasa, która przepuściła `ENABLE_BEST_EFFORT_OBJM_R6_KEY` (komentarz `tools/flag_effect_coverage_check.py:9`). **I nie są w fingerprincie** → brak parytetu cross-proces.

### 3a. Podzbiór DECYZYJNY (selekcja/scoring/feasibility/filtr — leak GROŹNY, kandydaci do ETAP4/Fazy D)
`ENABLE_BEST_EFFORT_POS_SOURCE_KEY` (selekcja bucket pos_source), `ENABLE_OBJ_COMMITTED_PICKUP_PENALTY` (scoring), `ENABLE_R6_SOFT_PEN_CAP` (scoring cap R6), `ENABLE_PLN_RESORT_WITHIN_TIER` (selekcja), `ENABLE_PLN_OBJECTIVE_SHADOW`, `ENABLE_KEBAB_KROL_DINNER_EXCLUSION` (HARD-reject warunkowy), `ENABLE_INACTIVE_COURIER_GUARD` (filtr floty), `ENABLE_ZOMBIE_PICKUP_AT_GUARD` (filtr bag), `ENABLE_GPS_BBOX_GUARD` (zaufanie pos_source), `ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION` (flota), `ENABLE_COURIER_LAST_KNOWN_POS` (pos), `ENABLE_REASSIGN_GLOBAL_SELECT` (selekcja reassign), `ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY` (feasibility/scoring), `ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY` (scoring), `ENABLE_EXCLUDE_BY_CID` (filtr floty HARD), `ENABLE_NEW_COURIER_RAMP` (scoring), `ENABLE_PICKUP_FROM_GROUND_TRUTH` (anchor odbioru), `ENABLE_PICKUP_TIME_MIRRORS_CK` (czas), `ENABLE_ELASTYK_CK_NO_BACKWARD` (czas), `ENABLE_LOAD_PLAN_PURE_READ` (plan read), `ENABLE_GEOCODE_VERIFICATION_ENFORCE` (geokod HARD), `ENABLE_REGEOCODE_SYNC_TEXT`, `ENABLE_NOTIFY_PRIORITY_ROUTING`, `ENABLE_OBJM_LEXR6_SELECT_SHADOW` (twin shadow OBJM — patrz §4), `ENABLE_R_PACZKI`... (paczka). + display: `ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN/_COMMITTED`.

### 3b. Podzbiór SHADOW/observability (lek łagodniejszy — log-only, ale wciąż poza parytetem)
ENABLE_PICKUP_DEBIAS_SHADOW, ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW, ENABLE_ETA_QUANTILE_SHADOW, ENABLE_ETA_R3_SHADOW, ENABLE_ETA_R3_DROP_SHADOW, ENABLE_PREP_BIAS_SHADOW, ENABLE_PREP_VARIANCE_ANOMALY_SHADOW, ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW, ENABLE_BEST_EFFORT_OBJM_SHADOW, ENABLE_MIN_DELIVERED_AT_SHADOW, ENABLE_REPO_COST_SHADOW, ENABLE_LGBM_TWOMODEL_SHADOW, ENABLE_FEAS_CARRY_BLIND_SHADOW, ENABLE_FAIL03_K2_SHADOW, ENABLE_EARLYBIRD_T30_SHADOW, ENABLE_BUG4_RESEQ_SHADOW, ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW, ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW, ENABLE_GPS_DELIVERY_VALIDATION, ENABLE_SAME_RESTAURANT_RACE_PROBE, ENABLE_F7_HIGH_RISK_BUCKET, ENABLE_READY_AT_INSTRUMENTATION, ENABLE_REASSIGNMENT_FORWARD_SHADOW, ENABLE_CZASOWKA_CK_PASSIVE_GUARD, ENABLE_PENDING_RESWEEP, ENABLE_PENDING_PROPOSALS_WRITE, ENABLE_GLOBAL_ALLOC_WRITE, ENABLE_WAITING_AT_PERSIST, ENABLE_STATE_WRITE_GUARD, ENABLE_STATE_PANEL_DIVERGENCE_ALERT, ENABLE_PANEL_PACKS_EMPTY_WRITE_GUARD, ENABLE_ORDERS_STATE_PRUNE, ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE, ENABLE_GEOCODE_NOMINATIM_FALLBACK, ENABLE_UWAGI_ADDRESS_PARSER, ENABLE_PARCEL_LANE_LIVE, ENABLE_COORDINATOR_FORCE_TIME_RECHECK, ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT, ENABLE_DRIVE_MIN_CALIBRATION_V2 (OFF), ENABLE_DRIVE_SPEED_TIER_CORRECTION (OFF), ENABLE_CARRY_CHAIN_PENALTY (OFF), ENABLE_BAG_TIME_ALERTS (OFF), ENABLE_FIRMOWE_KONTO_KOORD_ALERTS/_TELEGRAM_PROPOSALS (OFF), ENABLE_R1_CORRIDOR_GRADIENT (OFF).

### 3c. (c-bis) 41 nie-`ENABLE` bool POZA rejestrem (też leak-podatne)
`PENDING_RESWEEP_LIVE=False`, `REASSIGN_FWD_TELEGRAM_LIVE=False`, `ORDERS_STATE_PRUNE_DRY_RUN=False`, `PARSER_DEGRADED=False`, `AUTO_PROXIMITY_ENABLED=False`/`_SHADOW_ONLY=True`, `kill_switch_to_v1=False`, `commitment_level=False`, `A4_TEST_FLAG=False`, `ALWAYS_PROPOSE_WOULD_REDIRECT_SHADOW=True`, `PROPOSAL_FORMAT_V2=True`, `FAZA7_AGREEMENT_BUTTONS_ENABLED=True`, `RECONCILIATION_*` (4×), `CZASOWKA_*_ENABLED/SHADOW` (5×), `SHIFT_NOTIFY_*` (4×), `GPS_FEED_ALERT_*` (2×), `NEW_COURIER_AUTOPAIR_*` (2×), `AUTO_KOORD_*` (2×), `OBSERVABILITY_*` (2×), `MANUAL_*_COMMAND_ENABLED` (2×), `PARSE_CONTINUITY_GUARD_ENABLED`, `COORDINATOR_DM_ROUTING_ENABLED`, `REASSIGN_FWD_NOTIFY_TRUSTED_ONLY`. Większość peryferyjne (telegram/recon/shift), ale `kill_switch_to_v1`, `PARSER_DEGRADED`, `commitment_level`, `PENDING_RESWEEP_LIVE` są decyzyjne-krytyczne i poza parytetem.

---

## 4. (d) PARY SPRZĘŻONE (klasa C3) + bliźniaki (klasa B)

| Para / trójka | stan | klasa | uwaga |
|---|---|---|---|
| **ENABLE_V326_OR_TOOLS_TSP ↔ ENABLE_V326_SAME_RESTAURANT_GROUPING** | oba env-default „1"=**ON** (common.py:2356, :3159), **NIE w flags.json, NIE w ETAP4, NIE w fingerprincie** | C3+D | grouping karmi OR-Tools TSP; oba env-frozen, **nie hot-flippowalne**, parytet cross-proces niezweryfikowany. Konsumpcja `route_simulator_v2.py:299,438`. |
| **ENABLE_OBJM_LEXR6_SELECT (ETAP4, ON) ↔ ENABLE_OBJM_LEXR6_SELECT_SHADOW (leak, OFF)** | live=ON (l.2625), shadow=OFF (l.2617) | B+D4 | primary w rejestrze, twin shadow POZA. Konfiguracja poprawna (live XOR shadow), ale shadow-twin niewidoczny w fingerprincie. |
| **ENABLE_BEST_EFFORT_OBJM_R6_KEY (ETAP4, ON) ↔ ENABLE_FEAS_CARRY_READMIT (ETAP4, OFF)** | best-effort-path ON, feasible-path readmit OFF | B | bliźniacze ścieżki re-dopuszczenia carry-inclusive (selekcja vs feasibility). Protokół wymaga ruszać RAZEM. |
| **ENABLE_DRIVE_MIN_CALIBRATION_V2 (OFF) ↔ _V2_SHADOW (ON)** | main OFF, shadow ON | C3 | zamierzone (premisa = artefakt, NIE flipować main). |
| **ENABLE_OBJ_DELIVERY_FOOD_AGE (OFF) ↔ _SHADOW (OFF) ↔ ENABLE_OBJ_FOOD_AGE_HARD_SLA (absent/OFF)** | trójka food-age, cała OFF | C3 | + thread-local override `food_age_override` (common.py:337). |
| **ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY + ENABLE_NO_GPS_EQUAL_TREATMENT + ENABLE_EQUAL_TREATMENT_BUCKET** | wszystkie ON | C3 | equal-treatment trójca (pre-shift/no-gps równo + bucket selekcji). Sprzężone semantycznie. |
| **AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO + _MARGIN + ENABLE_AUTO_ASSIGN** | dwa strict-True (const), executor OFF | C3 | AUTON-02 profil; executor gate OFF = telemetria liczona, wykonanie nie. |
| **ENABLE_PENDING_RESWEEP (ON) ↔ PENDING_RESWEEP_LIVE (OFF)** | shadow ON, live OFF | C3 | shadow/live para (oba poza rejestrem). |
| **ENABLE_REPO_COST_SHADOW (ON) ↔ ENABLE_REPO_COST_LIVE (OFF, ETAP4)** | shadow ON, live OFF | C3 | live w ETAP4, shadow w leak. |
| **ENABLE_PANEL_BG_REFRESH** shadow=1 / watcher=0 | per-proces | D | zamierzone, ale poza fingerprintem. |

---

## 5. USE_V2_PARSER — kandydat dywergencji cross-proces (klasa J/D2) — PLAUSIBLE

`USE_V2_PARSER` def `panel_client.py:93` (module-level `os.environ.get("USE_V2_PARSER","0")=="1"`, env-frozen) + `parser_health_endpoint.py:689`.
**Ustawione `=1` TYLKO na panel-watcher** (override.conf). dispatch-shadow / inne procesy importujące `panel_client` czytają względem WŁASNEGO env → default „0" → **V1 parser**.
→ Jeśli shadow/inny proces realnie parsuje HTML panelu, **panel-watcher=V2 a shadow=V1** = dwa różne parsery na ten sam panel = potencjalna niespójność danych wejściowych (warstwa 1 silnika).
**Status: PLAUSIBLE — wymaga Fazy C** (czy shadow wykonuje `parse_panel_html`? jeśli tak → CONFIRMED J). NIE potwierdzone w tym OS (read-only inwentarz).

---

## 6. ENABLE_BEST_EFFORT_OBJM_R6_KEY — rozstrzygnięcie „martwa #1" → **ŻYWA**

- W ETAP4 (common.py:162). Stała env-default OFF (common.py:2645). flags.json=True.
- **Gałąź REALNIE wpięta:** `dispatch_pipeline.py:6771` `if C.flag("ENABLE_BEST_EFFORT_OBJM_R6_KEY", getattr(C, ...)):` → wybiera `_best_effort_objm_pick` (def `dispatch_pipeline.py:634`, PRIMARY=`objm_r6_breach_max_min`).
- **effective=True, LIVE.** Historyczny status „martwa/0 testów efektu" już naprawiony (testy `test_best_effort_objm_livekey_2026_06_24.py`, `test_flag_effect_coverage.py`).
**Werdykt: NIE martwa. NIE wymaga akcji od strony „dead-flag".**

---

## 7. (klasa E) FINGERPRINT = KŁAMIĄCY PRZYRZĄD parytetu

`flag_fingerprint()` (common.py:364-371): `names = ETAP4_DECISION_FLAGS + _FINGERPRINT_EXTRA_FLAGS` = **63 flagi**.
Logowany przy starcie każdego procesu „po unifikacji fingerprinty shadow/czasowka/plan-recheck MUSZĄ być identyczne".
**POZA fingerprintem (≥27 decyzyjnych/route):** 23 route/canon (plan_recheck.py), USE_V2_PARSER, OR_TOOLS_TSP, SAME_RESTAURANT_GROUPING, + 71 leak ENABLE_* (z czego ~25 decyzyjnych z §3a).
→ **Fingerprint daje fałszywe zapewnienie parytetu.** Drop-in dodany do jednego serwisu a nie do bliźniaka (np. PLAN_SEQUENCE_LOCK tylko plan-recheck) NIE zostanie złapany przez porównanie fingerprintów. Klasa E (przyrząd kłamie) + D.

---

## 8. SUSPICION MATRIX (DEAD / ENV-FROZEN / NIEOSIĄGALNA / MASKUJĄCA)

| Podejrzenie | Flagi | Dowód/uwaga |
|---|---|---|
| **MASKUJĄCA-inną** | ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE | json=False maskuje const=True (l.2805). Usunięcie klucza → cichy ON. |
| **ENV-FROZEN** (restart-only, nie hot) | cała route/canon 23 (plan_recheck.py:347-488), USE_V2_PARSER (panel_client:93), OR_TOOLS_TSP (2356), SAME_RESTAURANT_GROUPING (3159), ENABLE_OBJ_REPLAY_CAPTURE (2849), ENABLE_LGBM_SHADOW (2315), ENABLE_PENDING_POOL (926), 7 ETAP4-absent-from-json (const-frozen) | wartość ustalana env/const przy imporcie; flags.json ich nie ma → hot-reload bezskuteczny. |
| **DEAD (niepodpięta)** | ⚠ **żadnej NIE potwierdzono w tym OS** | `ENABLE_BEST_EFFORT_OBJM_R6_KEY` (hist. #1) = ŻYWA (§6). `A4_TEST_FLAG` (flags.json) = test-only artefakt (kandydat-K, do potwierdzenia grepem konsumenta w Fazie C/K). Pełen sweep „flaga bez konsumenta" = poza zakresem A3 (to OS dla K/Fazy K). |
| **NIEOSIĄGALNA (short-circuit)** | brak potwierdzonej w A3 | wymaga trace gałęzi (Faza C/E). Kandydaci: flagi pod `ENABLE_AUTO_ASSIGN=False` (executor gate) — telemetria liczona, gałąź wykonania nieosiągalna dopóki gate OFF. |

---

## 9. POKRYCIE / co NIE zrobione (jawnie)

- **NIE potwierdzono per-flaga „ma konsumenta / martwa":** A3 to rejestr STANU EFEKTYWNEGO, nie sweep-konsumpcji. Pełna mapa flaga→callsite (klasa K dead-code) = osobny OS / Faza K. Potwierdziłem konsumpcję tylko dla: route/canon (plan_recheck), BEST_EFFORT_OBJM_R6_KEY (6771), OR_TOOLS/GROUPING (route_simulator), USE_V2_PARSER (panel_client).
- **USE_V2_PARSER divergencja = PLAUSIBLE nie CONFIRMED** (nie potwierdziłem czy shadow parsuje HTML).
- **czasówka_scheduler proces (dispatch-czasowka) NIE zmierzony** `systemctl show` — recon/ETAP4 zakłada parytet z shadow przez flags.json; jego env drop-iny niezweryfikowane w tym OS (Faza C: dodać `systemctl show dispatch-czasowka*`).
- **reassign-global-select / reassignment-shadow / carried-first-guard timery** — ich env nie zmierzony (osobne procesy, mogą mieć własne drop-iny).
- **Wartości numeryczne** (FLAGS_JSON_NUMERIC_OVERRIDES 25 + progi) zinwentaryzowane tylko jako rejestr, bez efektywnych wartości per-proces — to OS dla klasy N (rozsyp progów), nie A3.
- **NIE odpalałem** nic z `--notify/--live/--apply`. Zero edycji/restartów/flipów.

---

## 10. HANDOFF dla Faz B/C/D/E

1. **Faza D (dryf flag):** kanon „stan flag" MUSI łączyć 3 warstwy — sam flags.json kłamie. Użyj §1 (drop-iny zmierzone) + §2 (json vs const). Jedyna inwersja maskująca = COMMIT_DIVERGENCE_VERDICT_GATE (§2b).
2. **Faza D/E (fingerprint):** §7 — fingerprint pokrywa 63/≥90 flag decyzyjnych. Rekomendacja-DRAFT: rozszerzyć `flag_fingerprint()` o route/canon + USE_V2_PARSER + OR_TOOLS/GROUPING, ALBO przenieść je do flags.json+ETAP4 (kanon hot-reload, jak ETAP4 zrobił z 13 flagami 10.06).
3. **Faza D4 (conftest-leak):** realny rozmiar = **71 ENABLE_* + 41 bool** (nie „~24"). Priorytet = podzbiór decyzyjny §3a (~25 flag: BEST_EFFORT_POS_SOURCE_KEY, OBJ_COMMITTED_PICKUP_PENALTY, R6_SOFT_PEN_CAP, KEBAB_KROL_DINNER_EXCLUSION, INACTIVE_COURIER_GUARD, ZOMBIE_PICKUP_AT_GUARD, GPS_BBOX_GUARD, EXCLUDE_BY_CID, REASSIGN_GLOBAL_SELECT, V3273_WAIT_REJECT, …) → kandydaci do ETAP4 (conftest strip + fingerprint).
4. **Faza C (cross-proces J):** zmierzyć env `dispatch-czasowka` + timery reassign/carried-guard; potwierdzić USE_V2_PARSER (shadow parsuje?); twin-trace route/canon (latentny OFF w shadow).
5. **Faza B (bliźniaki):** pary §4 — BEST_EFFORT_OBJM_R6_KEY↔FEAS_CARRY_READMIT, OBJM_LEXR6_SELECT↔_SHADOW, OR_TOOLS↔GROUPING — ruszać RAZEM (protokół MAPA KOMPLETNOŚCI).
6. **Klasa K (martwy):** A4_TEST_FLAG + drop-in `.bak` (§1f) = czyszczenie; pełen dead-flag sweep osobno.
7. **Baseline:** `test_flag_doc_coverage::test_baseline_is_not_stale` FAIL (recon §F) = żywy dryf flag-doc (dziś dorzucone AUTON-02/force-recheck niezreconciliowane) — klasa D, do domknięcia.

**Liczby kluczowe:** flags.json=198 keys · ETAP4=59 (52 w json + 7 const) · drop-in env-frozen poza json≈38 · fingerprint=63 · leak D4 = 71 ENABLE_* + 41 bool · 1 inwersja maskująca · 0 potwierdzonych martwych · BEST_EFFORT_OBJM_R6_KEY = ŻYWA.
