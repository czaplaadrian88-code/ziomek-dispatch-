# AUDYT ZIOMKA 2026-06-03 — STATUS + ROADMAP (handoff dla następnej sesji CC)

**Co to:** autonomiczny audyt dispatchera Ziomek (118 findingów: 17 P0 / 51 P1 / 36 P2 / 14 P3) + co z niego zrobiono dnia 2026-06-03. Pełny raport: `AUDIT_2026-06-03/ZIOMEK_AUDYT_2026-06-03.md`. Korpus findingów: `AUDIT_2026-06-03/_ziomek_audit_extract.md`. Pamięć: `[[ziomek-autonomy-audit-2026-06-03]]`.

**TL;DR dla nowej sesji:** audyt jest w ~90% OTWARTY. Zamknięto 2 P0 (FAIL-02, SELECT-01) + zbudowano OFFLINE aparat uczenia/pomiaru (shadow). Reszta czeka. Najwyższy ROI teraz = **paczka cichych bezpieczników + quick-winów** (sekcja „NASTĘPNY KROK").

---

## ✅ ZAMKNIĘTE (live, 2026-06-03)
- **FAIL-02** (P0) — porzucony kurier-widmo nie podbiera nowych zleceń. `courier_resolver.py:597` filtr stale na pozycji. Commit `774293d`, tag `fail02-stale-pos-consistency-2026-06-03`, restart dispatch-shadow 13:44 UTC. Test `tests/test_fail02_stale_pos_consistency.py`.
- **SELECT-01** (P0→P2) — „0% zgodności" obalone jako artefakt pomiarowy (`actual_courier_id` populowany tylko przy override). Realna zgodność ~15-18%.
- **(bonus, nie z audytu) shadow `.get()` fix** — early_bird KOORD nie failuje już (KeyError `shadow_dispatcher.py:960`). Commit `5c0b8cb`, restart 14:24 UTC. Efekt: failed-rate 10.1%→0%.

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
PANEL-SCRAPE-01 (P0, sekwencyjny fetch — pęka ~2x), OSRM-TABLE-03 (brak cache table()), THREADPOOL-04 (pre-filtr puli), TICK-OVERLAP-05, LATENCY-TREND-08, STATE→SQLite.

### D. JAKOŚĆ / BUNDLING (większe, zmierzone)
BUNDLE-02..06 (bundle_fit score zamiast samej odległości; 80% worków bez sygnału wartości), SEL-01/FEAS-02 (kierunek do klucza selekcji; no_gps-empty z fikcyjnej pozycji), GEO-01/02/03 (OSRM w scoringu zamiast haversine×1.37; model barier rzeka/tory; geo-ślepa kalibracja drive_min), SCORE-01..05 (sprzeczne wagi scoringu). **Świeżo skwantyfikowane (rule_deviation_report): R5 odbiory >1.8km = 58% worków, R8 span >cap = 46%, fleet top-3 Ziomek 43% vs człowiek 31%.**

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
