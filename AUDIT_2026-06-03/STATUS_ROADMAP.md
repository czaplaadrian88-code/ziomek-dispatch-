# AUDYT ZIOMKA 2026-06-03 — STATUS + ROADMAP (handoff dla następnej sesji CC)

**Co to:** autonomiczny audyt dispatchera Ziomek (118 findingów: 17 P0 / 51 P1 / 36 P2 / 14 P3) + co z niego zrobiono dnia 2026-06-03. Pełny raport: `AUDIT_2026-06-03/ZIOMEK_AUDYT_2026-06-03.md`. Korpus findingów: `AUDIT_2026-06-03/_ziomek_audit_extract.md`. Pamięć: `[[ziomek-autonomy-audit-2026-06-03]]`.

**TL;DR dla nowej sesji:** Grupa **A+B ZAMKNIĘTA 2026-06-04** (ciche bezpieczniki + quick-winy — patrz sekcja niżej). Zostały 3 duże fronty: **C skalowalność** (`PANEL-SCRAPE-01` P0), **D jakość/bundling** (`SEL-01/FEAS-02` selekcja bez GPS, `BUNDLE-02..06`), **E autonomia** (cel czerwiec'26 — `AUTON-01` ścieżka auto-assign NIE istnieje) + reszta A (FAIL-12 P1, PARSER-DEGRADED, PACKS, GPS-02, FAIL-04/06/07/09). **Rekomendacja kolejności:** D/`SEL-01`+`FEAS-02` (propozycje bez koordów — mierzone teraz w cieniu przez FAIL-03-K1) → C/`PANEL-SCRAPE-01` → E (dopiero po zamknięciu luk jakościowych).

---

## ✅ ZAMKNIĘTE (live, 2026-06-03)
- **FAIL-02** (P0) — porzucony kurier-widmo nie podbiera nowych zleceń. `courier_resolver.py:597` filtr stale na pozycji. Commit `774293d`, tag `fail02-stale-pos-consistency-2026-06-03`, restart dispatch-shadow 13:44 UTC. Test `tests/test_fail02_stale_pos_consistency.py`.
- **SELECT-01** (P0→P2) — „0% zgodności" obalone jako artefakt pomiarowy (`actual_courier_id` populowany tylko przy override). Realna zgodność ~15-18%.
- **(bonus, nie z audytu) shadow `.get()` fix** — early_bird KOORD nie failuje już (KeyError `shadow_dispatcher.py:960`). Commit `5c0b8cb`, restart 14:24 UTC. Efekt: failed-rate 10.1%→0%.

## ✅ ZAMKNIĘTE 2026-06-04 (sesja A+B — ciche bezpieczniki + quick-winy)
Workflow weryfikacji `wz1xefz57` + codegen `w5xgmvahh`. Commity na master (auto-push + tagi pushnięte na origin). 44 nowe testy, regresja 45=baseline (zero regresji).
- **STATE-RMW-02** (P0) — `prune_terminal_orders()` + `prune_orders_state.py` + timer `dispatch-orders-state-prune` **LIVE** (03:30 UTC, po snapshocie). Prune live 3727→177 (8,4MB→0,4MB), 0 aktywnych utraconych. Commit `78ba075`, tag `state-rmw-02-prune-live-config-hygiene-2026-06-04`.
- **CONFIG-HYGIENE** (DEADFLAGS-01 + SHADOW-NAMING-01 + CONFIG-DUAL-01) — flags.json 108→100 (−15 martwych +9 nowych), `flags_admin effective` ujawnia 21 flag env z override.conf. Commit `78ba075`.
- **JSONL-UNBOUNDED-06** — `/etc/logrotate.d/dispatch-v2` GRUPA B-2 (8 plików, copytruncate). LIVE, `.bak` lokalny.
- **GPS-01** (P0) — `monitoring/gps_feed_health.py` + hook HEARTBEAT (fresh_ratio wzgl. AKTYWNEJ floty). **DOMYŚLNIE INERT.** ⚠️ KOREKTA Adriana 06-04: brak GPS = CELOWY stan testowy (apka GPS na kilku kontach, debug), NIE incydent — aktywacja flagi przy autonomicznym starcie. Commit `c431ade`, tag `groupB-shadow-gps01-parse01-fail03k1-2026-06-04`.
- **PARSE-01** (P0) — `parse_continuity_guard.py` + straż pre-emit w panel_watcher. Shadow LIVE (flaga OFF, log-only). **Flip live: at-job 114 @ 2026-06-05 12:00 UTC** (gated: 0 false-pos w obserwacji) + niezależna weryfikacja at-job 115 @ 12:15. Commit `c431ade`.
- **FAIL-03-K1** (P0) — shadow licznik near-term KOORD-cisza (log-only, ZERO mutacji verdiktu); mierzy „propozycje bez koordów". **KROK 2 live (KOORD→PROPOSE) = osobny sprint + ACK + restart dispatch-telegram.** Commit `c431ade`.

**Odroczone/pominięte świadomie:**
- **FAIL-01** — orphan-watchdog na wieku GPS bezużyteczny przy celowo-nieobecnym GPS (Z2). Wróci przy autonomii z innym sygnałem (panel-status/events).
- **RECON-01** — audyt mylny („flip 1 flagi" — wymaga `chat_id` w env), 0 ghostów od 31.05 + dubluje v328 → niska wartość, pominięty.
- **VETO-RETIRE-01 + R6 dublet** — PO 2026-06-08 (at-job #110 czyta selection_veto_shadow do digestu).

**Korekty audytu (znalezione w weryfikacji):** anchor `delivered_at`→`updated_at` (STATE-RMW); PARSE-01 wektor 02.05 już naprawiony (`\d{5,7}`); `commitment_level` to pole stanu nie flaga; GPS feed „martwy" = celowy nie incydent.

---

## 🟡 W TOKU (offline/shadow, metodycznie — NIE „nietknięte")
Klaster breach/autonomia/reliability — zbudowano POMIAR, flip czeka na walidację:
- **Pętla retro-uczenia** (commit `06c1157`): `tools/retro_learning.py` (A1-A5), `eta_calibration_shadow.py`, `courier_reliability.py`, `a2_selection_shadow.py`. Timer `dispatch-retro-learning.timer` LIVE (04:30 UTC dziennie).
- **Kluczowe odkrycie:** breach 35min NIE z predykcji trasy (AUC 0.50), tylko z TOŻSAMOŚCI KURIERA (AUC 0.64). Kalibracja ETA poprawia ETA/selekcję, ale NIE breach.
- **A2 soft-score niezawodności** (dźwignia na 14% breach) — shadow A/B (key_aware_v2 + confidence-gating): ~7-10% selekcji by się zmieniło, breach zwycięzcy halves 0.18→0.10, better:worse 4.7:1 @COEFF100. Adresuje: R6BREACH-01, AUTON-03, AUTONOMY-02, BUNDLE-01(breach), CB-01, DATA-03, BIAS-01.
- **Raport odchyleń** (commit `65d15d9`): `tools/rule_deviation_report.py`. **Cotygodniowy digest** → DM Adriana (8765130486 POTWIERDZONY): `at` job 113 → 2026-06-10 07:00 UTC, `tools/weekly_a2_digest.py` (commit `4f73346`). Baseline zamrożony: `dispatch_state/rule_deviation_baseline_2026-06-03.json`.

### ⏰ DECYZJA ZA TYDZIEŃ (2026-06-10, po digeście na DM)
**Jeśli trend A2 się trzyma** (changed_rate stabilny ~7-10% + breach halving + better:worse korzystne przez 5-7 dni) → **flip A2 soft-score live (COEFF 60-100), hot-path, z baseline jako control.** To pierwszy mierzalny pozytyw JAKOŚCIOWY. Jeśli trend kruchy → model wielo-cechowy (LGBM, AUTON-09).

---

## 🔴 OTWARTE — priorytetyzowane (to robimy dalej)

### A. CICHE BEZPIECZNIKI (groźne przy 10x bez człowieka) — NAJWYŻSZY priorytet
| ID | Problem | Effort |
|---|---|---|
| **PARSE-01** (P0) | częściowy/pusty parse HTML (HTTP 200) → cichy blackout dispatchu, brak straży „nagły spadek do 0" | medium |
| **FAIL-01** (P0) | porzucony worek nigdy nie wraca do puli + ZERO detekcji offline kuriera | medium |
| **GPS-01** (P0) | brak alarmu „cały feed GPS zamarł" → cicha degradacja floty do proxy | **quick** |
| **FAIL-03** (P0) | KOORD=cisza, 7,3% zleceń bez propozycji (łamie ZAWSZE-PROPONUJ) | medium |
| FAIL-12 (P1) | awaria grafiku (Google Sheet) → NO_ACTIVE_SHIFT fail-CLOSED całej floty | medium |
| RECON-01 (P1) | reconciliation alerty Telegram WYŁĄCZONE (flip 1 flagi) | **quick** |
| PARSER-DEGRADED-01, PACKS-01, GPS-02, FAIL-04/06/07/09 | patrz extract | mix |

### B. QUICK-WINY HIGIENY (tanie, zero/niskie ryzyko)
| ID | Problem | Effort |
|---|---|---|
| **STATE-RMW-02** (P0) | orders_state.json 8MB full-rewrite+fsync/zapis, brak prune (3505 zleceń) → prune terminalnych | **quick** |
| CONFIG-DUAL-01, SHADOW-NAMING-01, DEADFLAGS-01 | flags.json vs override.conf rozjazd; shadow_mode martwa; 14 martwych flag | quick |
| VETO-RETIRE-01 | SELECTION_VETO_SHADOW zmienia 0.9% → retire | quick |
| JSONL-UNBOUNDED-06 | jsonl rosną bez logrotate (consumer_stuck 27MB) | quick |

### C. SKALOWALNOŚĆ (pęka 2x-5x)
~~PANEL-SCRAPE-01~~ ~~OSRM-TABLE-03~~ ~~TICK-OVERLAP-05~~ — **DONE 2026-06-12 (nocna sesja)**. Zostały: THREADPOOL-04 (pre-filtr puli), LATENCY-TREND-08 (częściowo adresowany przez TABLE-03), STATE→SQLite.

**✔ PANEL-SCRAPE-01 LIVE 12.06** (`panel-scrape01-prefetch-2026-06-12`): równoległy pre-fetch detali osobnymi sesjami per wątek (panel_detail_prefetch, NIGDY-reguła głównej sesji nienaruszona), miss→sekwencyjny fallback; kill-switch `ENABLE_PANEL_DETAIL_PREFETCH` (ON) + `PANEL_DETAIL_PREFETCH_WORKERS=4` w flags.json. Baseline: tick p50=7.4s p95=23.7s, peak 11.06 p50=20.5s>interwał. Watch po peaku: at#135 12.06 13:30 UTC.
**✔ TICK-OVERLAP-05 LIVE 12.06** (`tick-overlap05-metric-2026-06-12`): ratio elapsed/interval w SUMMARY (ratio_last/ratio_max/over0.8=n/N) + WARNING rate-limited 1/5min przy >0.8, zero Telegrama.
**✔ OSRM-TABLE-03 LIVE 12.06** (`osrm-table03-cell-cache-2026-06-12`): per-cell cache table() (raw przed multiplierem, TTL 1h) + dekompozycja missów na ≤2 cienkie prostokąty (ruch kuriera: 2N zamiast N² komórek); kill-switch `ENABLE_OSRM_TABLE_CELL_CACHE` (ON); probe live: zimny 24ms → full-hit 0ms → dekompozycja 3ms, wyniki identyczne; hit-rate w logu hourly (dispatch.log).
**✔ GPS-04 12.06** (`gps04-positions-gc-2026-06-12`): GC wpisów GPS >24h, cron 04:50; pierwszy apply pwa 14→4, legacy 11→0.
**✔ OSRM-01 12.06** (`osrm01-fallback-smoke-2026-06-12`): smoke fallbacku — bias_med +1.3min MAE 2.09 ratio_med 1.173 (przeszacowanie = bezpieczne), circuit-breaker 7/7; cron miesięczny 1. dnia 05:10.

### D. JAKOŚĆ / BUNDLING (większe, zmierzone)
BUNDLE-02..06 (bundle_fit score zamiast samej odległości; 80% worków bez sygnału wartości), ~~SEL-01~~/FEAS-02 (no_gps-empty z fikcyjnej pozycji), GEO-01/02/03 (OSRM w scoringu zamiast haversine×1.37; model barier rzeka/tory; geo-ślepa kalibracja drive_min), SCORE-01..05 (sprzeczne wagi scoringu). **Świeżo skwantyfikowane (rule_deviation_report): R5 odbiory >1.8km = 58% worków, R8 span >cap = 46%, fleet top-3 Ziomek 43% vs człowiek 31%.**

**✖ SEL-01 ROZSTRZYGNIĘTY 2026-06-12: WERDYKT NIE-ROBIĆ** (`eod_drafts/2026-06-12/SEL01_VERDICT_2026-06-12.md`) — replay 1802 PROPOSE (02-11.06): wariant dir-bucket w kluczu dubluje błąd SELECTION_VETO (23/24 flipów na ujemny score, 2/24 na sentinel −1e9, 16/24 nadpisuje tier 0→1 late-pickup, 14/24 ucieka w cos=None); wariant tie-break bezpieczny ale pusty (0,1-0,3% decyzji); 57% cross-zwycięzców = scarcity (brak alternatyw — naprawia podaż, nie klucz). Kierunek wzmacniać przez wagi `bonus_r1_corridor` w E7 re-tune (at#131 17.06).

**✖ FEAS-02 ROZSTRZYGNIĘTY 2026-06-12: WERDYKT NIE-ROBIĆ w kluczu** (`eod_drafts/2026-06-12/FEAS02_VERDICT_2026-06-12.md`, tag `feas02-verdict-no-go-2026-06-12`) — replay 2024 PROPOSE: blind-best 15,7%, 60% scarcity; flip na informed = mediana 111 pkt, gorszy tier 126/126. Store LAST-KNOWN-POS bez paliwa (gps w best 368→13 — adopcja GPS się zawaliła). Dźwignie: ops adopcja GPS 18→60% (PRIORYTET), flip `ENABLE_GPS_AGE_DISCOUNT` po rolloucie apki v2.

**✔ BUNDLE-06 Faza 1 + BUNDLE-02 + BUNDLE-03 SHADOW LIVE 2026-06-12** (tag `bundle-fit-shadow-2026-06-12`): `bundle_fit` compute-zawsze (kierunek/świeżość/rozstrzał z istniejących sygnałów, zero nowych OSRM; solo→None) + `bundle_fit_marginal_min` (plan_total−free_at, telemetria) + `fix_c_additive_pen_shadow` (BUNDLE-03: kara addytywna zamiast no-op zerowania; cos<−0.3 → kara od pełnego spreadu). Flagi w kanonie ETAP4 flags.json=false: `ENABLE_BUNDLE_VALUE_SCORING` (reaktywowana per BUNDLE-08, tym razem z konsumentem) + `ENABLE_FIX_C_ADDITIVE_PENALTY`; wagi hot w NUMERIC_OVERRIDES. **Kalibracja wag + decyzja o flipie = E7 (at#131 17.06)** na zebranych polach shadow.

**✔ BUNDLE-05 ZAMKNIĘTY 2026-06-12 BEZ FLIPÓW — finding NIEAKTUALNY** (`eod_drafts/2026-06-12/BUNDLE05_GATES_RETEST_2026-06-12.md`, re-test na zlecenie Adriana): wszystkie 3 bramki **SĄ ON od dawna** (env-defaulty "1", zero override w environ shadow — audyt 03.06 czytał stan przez stary override.conf sprzed ETAP4). Dowód korpusowy 2153 PROPOSE: V327 mult 724 kandydatów/143 zwycięzców (sign-guard Z-02 675×), wave veto 1825×, intra-rest 1×; kolateral na spójnych workach realnie 13 (0,6%, 9/13 i tak wygrało — notatka dla E7, nie bug). +22 testy utwardzające (`test_bundle05_gates_hardening.py`: macierz brzegowa multa z sentinelem, granice progów STRICT, tz-naive/garbage timestampy, kontrakt „uzbrojone domyślnie"). Lekcja #189.

**Zostało z D:** BUNDLE-04 (twardy cap span — walidacja na sla_log przy E7) · GEO-01/02/03 (OSRM w S_dystans / bariery / kalibracja drive_min — osobny sprint, effort medium-large) · SCORE-01..05 (= E7 17.06).

### E. AUTONOMIA (cel czerwiec'26 — ścieżka zaczęta)
AUTON-01 (ścieżka auto-assign NIE ISTNIEJE w kodzie — `AUTO_APPROVE_*` zero call-site), AUTON-02 (4% AUTO), AUTON-04 (próg C2 placeholder), AUTON-08 (batching/continuous re-opt), AUTON-09 (wyuczony model ETA). A2 shadow = pierwszy krok Fazy 1.

---

## ▶ NASTĘPNY KROK (rekomendacja)
**Paczka „ciche bezpieczniki + quick-winy" (A+B)** następnym fan-outem agentów:
GPS-01 alarm · STATE prune · RECON-01 flip · FAIL-01 orphan-watchdog (shadow→alert) · PARSE-01 sudden-drop guard · config hygiene (DEADFLAGS/SHADOW-NAMING) · VETO-RETIRE.
Tanie, większość zero-ryzyka, łatają realne dziury bezpieczeństwa które bolą najbardziej przy 10x bez człowieka.

## Droga do autonomii (z raportu, Faza 0→4)
0. Zamknij luki które dziś łapie człowiek (R6-guard via A2, FAIL-01/02, ETA, ALWAYS-PROPOSE, CB-01) ← warunek konieczny
1. KPI AUTO-rate + korpus override → kalibracja progu C2 (A2 shadow = tu jesteśmy)
2. Zbuduj egzekucję auto-assign + knock-back 60s + kill-switch + canary
3. Zastąp heurystyki modelami (ETA, LGBM re-ranker)
4. Enterprise (offer/accept, predictive positioning)

## Workflow / zasady (z dispatch_v2/CLAUDE.md — evergreen)
Per-step ACK · `.bak` → edit → py_compile → import → test → commit+tag → restart → verify · NIE restartuj dispatch-telegram bez ACK · restart produkcji = explicit ACK (klasyfikator blokuje bez tego) · offline/shadow first dla zmian decyzyjnych · venv `/root/.openclaw/venvs/dispatch/bin/python`.

## Commity sesji 2026-06-03 (master, pushed)
`774293d` FAIL-02 · `06c1157` pętla uczenia · `5c0b8cb` .get() fix · `65d15d9` raport reguł+digest · `4f73346` dowód .get() w digeście · (ten dok = kolejny commit).
