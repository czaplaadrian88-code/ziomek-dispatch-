# AUDYT 2.0 — ANEKS: surowe raporty 3 agentów (2026-07-01 wieczór)

Materiał źródłowy do `AUDYT_2.0_DESIGN.md`. Trzy niezależne pasy: (1) sweep 71/71 plików zaplecza audytu 1.0 pod JAWNE luki/capy, (2) diff pokrycia kod↔audyt (powierzchnie nieaudytowane), (3) digest wcześniejszych audytów (zamknięte/otwarte + oś 05.07). Zachowane niemal verbatim — liczby i linie wg stanu 01.07 wieczór (dryfują).

---

## RAPORT 1 — agent `backing-gaps`: jawne luki w 71 plikach backing (sweep KOMPLETNY)

Metoda: dedykowane sekcje „POKRYCIE / jawne luki / NIE zbadane / CAVEAT / coverage_gaps" każdego pliku (audyt był zdyscyplinowany — niemal każdy plik ma taką sekcję).

### FAZA A — inwentarz
- **A1**: ~60 modułów PERI/INSTR bez pełnej listy węzłów (l.282); „is-active nie sprawdzony dla wszystkich ~90 timerów" (l.284); osiągalność gałęzi flag-OFF (D3) = hipoteza (l.286).
- **A2**: stan flag per-proces NIE weryfikowany — ON/OFF z deklaracji, nie fingerprint (l.232); R9 wait HARD-reject tail bez oracle (l.234); paczka-exempt 3. site nie re-grepowany (l.236).
- **A3**: „NIE potwierdzono per-flaga ma-konsumenta/martwa" (tylko 4 flagi, l.192); USE_V2_PARSER divergencja PLAUSIBLE (l.193); env dispatch-czasowka/reassign/carried-guard NIE zmierzony `systemctl show` (l.194-195); nie odpalano `--notify/--live/--apply` (l.197).
- **A4** ⭐ FUNDAMENT-CAVEAT: `delivered_at`/`picked_up_at` = prawda-PRZYCISKOWA (0/377 auto_geofence GT; ~192 s przed GPS) → każdy „realny breach%" ±~3 min (l.89); „11 naprawionych 29.06 NIGDY nie przeszło oracle — deklaracja, NIE dowód" (l.94); treść werdyktów NIE odczytana (l.105); zero py_compile/importu narzędzi (l.108).
- **A5**: NIE czytano PEŁNYCH ciał `_build_route`/`build_view` (l.221); NIE odpalono runtime-diff kanon↔konsola↔apka (l.222-224); panelsync 665L nie zdiffowany.
- **A6**: courier-app Kotlin NIE czytany — lokalny re-sort/ETA niezweryfikowany (LUKA #1, l.286); parcel lane NIE prześwietlony (LUKA #2, l.287); wartości parytetu z lektury, NIE runtime (l.288); 13 NIE-floor nie re-zwalidowane linia-po-linii (l.290).

### FAZA B — sweep 15 klas (wspólny mianownik: read-only, ZERO oracle, magnituda=Faza C)
- **B01**: parytet lex_qual≡frozen „z lektury NIE z odpalenia"; scoring.py NIE czytany w całości.
- **B02**: wartości parytetu NIE runtime; panel_client:792 fetch pod shadow PLAUSIBLE; most paczki handoff.
- **B03**: Kotlin NIE czytany; „ile% eta-dostawy różni się gdy cache stale — NIE policzone runtime".
- **B04**: carried-first parytet / de-pile % / reassignment 59% — z seedów/memory, NIE re-zmierzone.
- **B05**: C1 zły pick — seed-oracle PROXY, nie ground-truth; C-adj-1 argument konstrukcyjny.
- **B06**: sprawdzono 6 z 59 ETAP4 (budżet); USE_V2_PARSER PLAUSIBLE; D2-2 recanon materialność PLAUSIBLE.
- **B07**: wave_scoring/pln_objective/ml_inference NIE prześwietlone pod własne zapisy metrics; LOCATION A vs B nie 1:1; nie prześledzono czy wartość realnie zmienia werdykt.
- **B08**: skew F1-C nie policzona runtime; konsumenci drop_zone spot-check 3 z ~10.
- **B09**: r6_gold4 live-fire trace NIE zrobiony (PLAUSIBLE); prep_bias_anchor — zaufanie do komentarza.
- **B10**: „ile kurierów tkwi — wymaga join"; events.db VACUUM nie zweryfikowane; brak pełnego sweepu `_read_*` pod side-effect.
- **B11**: runtime-diff order_podjazdy vs _build_route NIE zrobiony; env 3 timerów NIE zmierzony.
- **B12**: closures spot-checked; runtime-częstość martwych gałęzi NIE zmierzona; B3/soon_free porzucone-vs-zaplanowane → PYTAJ Adriana.
- **B13**: 64 zero-ref tools heurystyką (graveyard ≈45-50); treść graveyard NIE czytana; zero py_compile.
- **B14**: trace `_iso` vs `_parse_ts` nie traceowany; „czy realna wartość Warsaw-naive omija granicę DZIŚ — NIE zmierzone".
- **B15** ⭐: plan_recheck 37× `except Exception` POLICZONE nie zinspektowane (możliwy cichy drift kolejności); 119× except w dispatch_pipeline — tylko hot-path 1:1 (~110 nie); zero repro.
- **B16**: fleet_state/courier_api sentinel-handling nie per-linia; rate (0,0)/V328 302/d nie re-zwalidowane.
- **B17**: nie grepowano każdego literału w ~210 modułach; reachability ETA-quantile/HARD_TIER nie żywym tickiem.
- **B18**: lost-update częstość NIEZMIERZONA (severity ze struktury); timer-overlap nie zweryfikowany.
- **B19** ⭐: NIE uruchomiono PEŁNEJ `pytest tests/` (liczby z recon); brak CONFIRMED silent-false-green; whole-file false-PASS = wzorzec, spot-check 2 plików.
- **B20**: czy FEAS_CARRY_READMIT/ETA_QUANTILE realnie odpala — READ-ONLY.
- **B21**: fleet_state ciała pin/clamp LUKA; ordering F-4 runtime nie odpalony.
- **B22** ⭐ demaskuje własny monitor route-order: porównuje konsola↔apka NIGDY↔kanon silnika — wspólny dryf = mismatch 0 (fałszywy parytet); gałęzie start=None nie odpalają; `trust_canon_ok=True` hardkod POMIJA invalidated (case Jakub W poza zasięgiem). [uwaga syntezy: golden L6.A z 01.07 zastępuje ten monitor — ale luka pokazuje, że „zielony monitor" ≠ dowód]

### FAZA C — runtime-oracle (proxy vs ground-truth)
- **C01** bundle_calib VALIDATED, ale collector nie loguje picked_up_at; **O2 02.07 MUSI zrównać kotwicę min(ck,pu) vs engine pu-only**.
- **C02** bug4_reseq: inwariant delta≥0 ŹLE ZDEFINIOWANY (123/1074=11,5% legalnych „naruszeń"); własny health-gate suspect≤10% PADA; headline na proxy sort-ts.
- **C03** feas_carry VOID: zero joina z decision_outcomes; trigger fantomowy 99,7% pred vs 14,8% real; DALEJ emituje „benefit✅" → ryzyko re-flipu.
- **C05** objm_canary: G1-latencja nieatrybuowana (objm czy infra?); ~12% flip-rate nie odtworzone.
- **C06** b_route VOID: czyta ZAMROŻONĄ `dispatch_state/sla_log` (09-19.06) → real_joined=0 zamiast ~289; bramkuje decyzję B-lite.
- **C07** drive_speed: counterfactual flag-ON = monkeypatch, nie realny flip.
- **C08** would_hard_cap VALIDATED; **eta_source VOID** (compute-but-vanish) → kalibracja ETA ślepa na real-plan vs fiction; binding-rate capa NIEOBSERWOWALNY (rejected nie serializowani).
- **C09** gps_deliv VALIDATED (GT), ale button→physical OPTYMISTYCZNE ~2 min; 19% volatile coverage → na dniach low-GPS walidacja flipu niemożliwa.
- **C10** global_allocate: `no_courier=0/3044` — NIGDY nie eskaluje KOORD, upycha na 73-min/24-km worek; VALIDATED tylko dla COUNT (geometry-VOID).
- **C11** time_route: fallback tylko syntetycznie; równość worka monitor↔produkcja nie zdiffowana.
- **C12** checkpoint_tz VALIDATED (GT), mierzy MECHANIZM nie wynik; DST-zima nietestowane.
- **C13** pickup_slip VALIDATED(proxy): BUNDLE pooled BEZ de-konfundacji reseq → clean-bundle UNDER-buffered ~7-8 min; pickup_lateness ×2,6 przeszacowanie; [w dniu pomiaru timer jeszcze nie tiknął — zweryfikowane 01.07: tika od 30.06 22:30, na 04.07 będzie ~4-5 punktów].
- **C14** min_delivered: delivered_at=PREDYKCJA nie GPS; 2,8 min w podłodze szumu → flip A ryzykuje artefakty; join GPS-truth odroczony.
- **C15** carried_guard 🔴 VOID: biegnie z ODWROTNĄ konfiguracją flag → 87-90% `no_position` fikcyjne; prawdziwy nawrót przegapialny/utopiony w 1025 fikcyjnych.
- **C16** address: werdykt-D VOID (stale .txt, gubi 14 km); która współrzędna poprawna — niedeterminowalne bez GPS.
- **C17** shadow_selection: OSRM ground-truth NIE liczony (recompute z pól proxy); a2 na SLICE nie pełnych 47 MB; c5 mtime FRESH = pytest-artefakt.
- **C18** ⭐: `best_effort_fastest` pos_source MARTWE 81/81 — blind-check „fikcyjny ETA?" STRUKTURALNIE MARTWY (flip fastest-pickup poszedłby live ślepy na fikcję pozycji); `sequential_replay` LATENTNA INWERSJA (couriers_used higher-better→NO-GO), 0 testów.
- **C19** ⭐ conftest: `R6_SOFT_PEN_CAP` flags=True / const=False poza ETAP4 → KAŻDY test biegnie z capem ON (autor sądzi OFF) → regresja bez-capa NIEWIDOCZNA; **pytest CZĘŚCIOWO wiarygodny — NIEWIARYGODNY dla 62 flag survivorów**.

### FAZA D — konflikty
- D01: P-1..P-7 numery poza zasięgiem; „ile ETA_QUANTILE przepuszcza >35 — oracle nie lektura"; R6_DANGER doc=−24 vs kod=16.
- D02: materialność C2 = oracle nie A/D. D03: „ile RAZY konflikt przełączył decyzję — NIE z grep-c". D04: 14% pustych-bagów harm nie rozstrzygnięte. D05: „ile worków SLA-count≠R6-anchor; ile paczek przecieka O2 — NIE policzone".

### FAZA E/F — dedup/synteza
- E_dedup_2 ⚠: „naprawione 29.06 bez oracle = PLAUSIBLE; oracle OBALIŁ: bug4_reseq, conftest-257d315, feas_carry = VOID"; „sieć bezpieczeństwa wokół readmit ILUZORYCZNA — flip readmit ON bez re-enable zaworów = HARD-NO bez siatki".
- F: R2 replay 2d nie wykonany; R3 „harness MUSI joinować GT, inaczej goni artefakty"; R5 lost-update NIEZMIERZONA; R7 „ile RAZY triple-tax odbiera zlecenie LEPSZEMU — NIE policzone"; F_poc „nie dowodzi runtime proj≡proj; Kotlin poza hostem"; F_entropy „sentinel 2046+14456 nie re-policzone; twin dolna pewna=5"; F_roadmap data 07-10 nie potwierdzona grepem.

### TOP 15 wg agenta (skrót — pełne uzasadnienia wyżej)
1. Cała warstwa oracli = button-truth, nie fizyka (±3 min; 0/377 GT).
2. **Materialność (ile/dzień) NIGDY nie policzona — cały audyt deklaruje ISTNIENIE ścieżek z lektury.**
3. `best_effort_fastest` pos_source martwe 81/81 (blind-check fikcji martwy).
4. `eta_source` VOID → prowenancja ETA ślepa (kalibracja ślepa). [fix L1.1 LIVE 01.07 — weryfikacja rano 02.07]
5. Serializer gubi ~14 HARD-metryk. [fix L1.1 LIVE — jw.]
6. `carried_first_guard` VOID (pusty env, 87-90% fikcji).
7. bug4_reseq inwariant źle zdefiniowany + własny gate pada (bramkuje O2 z fałszywego powodu).
8. feas_carry VOID dalej emituje „benefit✅" (ryzyko re-flipu).
9. Frozen `_lex_qual`/objm shadow inertne TYLKO bo POST_SHIFT OFF (flip uzbraja rozjazd).
10. Floor: plan_recheck leak odclampowuje co 5 min; „ile kurierów tkwi" niezmierzone.
11. De-pile geometry-blind: certyfikat tylko COUNT; 35,2% multi-drop spread>8 km.
12. **pytest nie odpalony świeżo przez audyt + conftest-leak → ON≠OFF niegwarantowane dla 62 flag.**
13. Kotlin RouteLogic + parcel lane nigdy nie czytane (~15 plików).
14. Env per-proces kilku żywych serwisów NIE zmierzony (czasowka, reassign-timery, carried-guard).
15. b_route_shadow_review VOID (zła ścieżka sla_log → real_joined=0).
Tuż pod kreską: reassignment_quality precyzja n=7 (CI 36-92%) → at-193 decyzyjnie-void; sequential_replay inwersja werdyktu; C13 de-konfundacja bundli.

---

## RAPORT 2 — agent `coverage-diff`: powierzchnie NIE pokryte audytem 1.0

Klucz: audyt miał 3 głębokości — INWENTARZ (sklasyfikowany, zero sweepu/oracle) / KLASA-B (sweep) / PRZYRZĄD-C (oracle, 49 szt.) / BRAK. Rdzeń (~38 CORE-D + route-order + sentinele/alokacja) gęsto pokryty; luki = producenci danych, powierzchnie ZAPISU, instrumenty-bez-oracle.

### A. Rdzeń zinwentaryzowany, ale NIE sweepowany/oracle'owany
| Powierzchnia | Co robi | Ryzyko |
|---|---|---|
| `gps_server.py` / `dispatch-gps` | producent prawdy o pozycji | górny bieg CAŁEJ rodziny K5 (tknięto konsumentów, nie ingest) |
| Loader grafiku (Sheets→shift_start/end), `load_schedule()` | kotwica pre_shift dla 7+ modułów | TTL 10 min / fail-open / staleness niesweepowane → dryf grafiku fałszuje pre_shift flocie |
| `coordinator_time_recheck.py` | świeży (30.06) WRITE do committed czas_kuriera w OBIE strony, omija anti-wobble | rodzina HARD R27/frozen bez strażnika |
| `gastro_edit.py` | realny ZAPIS do panelu gastro | mis-aplikacja decyzji (COD/telefon/adres/kurier) |
| `manual_overrides.py` | HARD wykluczenia z puli + reset dzienny | klasa „BRAK KANDYDATÓW", bez oracle |
| `auto_koord.py` | eskalacja KOORD + klasyfikacja czasówki | błąd = utknięcie w KOORD albo eskalacja pominięta |
| `auto_assign_executor.py` | rate-cap+guard realnego auto-przydziału | „inert" tylko dzięki OFF; 1. flip odsłania niesweepowany kod |
| `panel_html_parser.py` | universal-ID regex HTML panelu | producent surowego stanu; parytet z panel_client niepilnowany |

### B. Genuinie BRAK (nawet w inwentarzu)
- **courier_api ops-internals**: `cost_calculator`, `cost_aggregator`, `schedule_service`, `schedule_escalation_cron`, `delivery_town`, `payment_override`, `vehicle_issues`, `fleet_aggregator`, `revenue_loader`, `earnings_history`, `panel_lite`, `gate_audit_poller` — żyją w LIVE procesie autorytetu apki.
- **Konsola API-endpointy**: `app/api/{coordinator,ziomek,dispatch,fleet,parcel_ops,notify_feed,fleet_overflow}.py` — warstwa przyjmująca KLIKNIĘCIA koordynatora (audytowano render, nie akcje).
- **Konsola `app.jobs.*`**: `roster_sync` (cid↔nazwa! LESSON-QA-11), econ-rollup, ksef-cost, customer-sms, payment-capture, fc21-eval, overflow-*.
- **Pakiet `telegram/`** (router TASK B + templates) — dziś muted; re-enable odsłania klasę O.

### C. Instrumenty LIVE bez oracle (najliczniejsza cicha luka)
~90 tools + ~15 ŻYWYCH timerów bez drugiej metody, m.in.: `ziomek_pred_calibration` (karmi validated drive_speed), `freshness_shadow_monitor`, `decision_outcomes`, `shadow_outcome_enricher`, `monitor_later_promises`, `fleet_position_snapshot`, `retro_learning`, `faza7_daily_kpi`(--telegram), `daily_rule_report`; **cała `observability/`** (koord_cascade_monitor, delivered_integrity_monitor, downstream_crosscheck_poll, liveness_probe, watchdog, gps_feed_health, consumer_stuck_alert) — „warstwa «czy system żyje» sama niezweryfikowana"; `r04_evaluator`, `validation_gate_lgbm`, `ml_inference`, `learning_analyzer`, `eta_residual_infer`.

### D. Świadome granice (NIE ścigać)
`papu_dispatch_bridge` (granica Adriana), PERI-pieniądze (daily_accounting, cod_weekly — ⚠ ale `dispatch-cod-weekly.service` FAILED!), `drtusz_bridge` (graniczny), `shift_notifications` (RETIRED, poprawnie), courier-app Kotlin (jawna luka ledger §3).

### TOP 10 agenta
1. `gps_server.py` 2. loader grafiku 3. `coordinator_time_recheck.py` 4. `gastro_edit.py` 5. ~90 tools/15 timerów bez oracle 6. `manual_overrides.py` 7. courier_api ops-internals 8. `auto_assign_executor.py` 9. konsola API-endpointy 10. `auto_koord.py`.

---

## RAPORT 3 — agent `prior-audits`: digest „co już zbadano" (anty-dublowanie)

### Zamknięte — NIE badać ponownie
load>clock (pf η² ~2× godziny) · optymizm = poślizg ODBIORU ~18 min, jazda ~0 (drive-speed correction wycofane — nie wskrzeszać) · wagi scoringu OK (zło wymuszone podażą; v327 cross-quad i freshness odrzucone) · akceptacja koordynatora = ZŁA bramka autonomii (agree≈override fizycznie) · 57% override ≠ widoczność (39% feasibility za ostra + 53% timing) · A2 coeff @100 od 11.06 · carried-first naprawione u źródła + strażnik · 11 kłamstw at-jobów naprawione 29.06 · feas-carry rolled back (0/515) · Bug#1 eta-realistic zrewertowany · EARLYBIRD redundantne · checkpoint-TZ CLEAN · pickup-floor peaki 26.06 PASS.

### Otwarte z właścicielem (Faza 3 / bramki)
load-aware ETA flip (review 04.07) · bundle-calib O2 (02.07, + gate-fix SLA-anchor w 3 bliźniakach + cap-Z=20) · auto-assign kroki 2-5 (1. wykonanie E2E nieprzetestowane!) · pre-shift L0-L6 · sentinel L2.1 (flip za ACK) · #3 reserve-pricing wstrzymane · #7 resweep engine-level P0-deferred · #9 LGBM eval · #5b geofence Phase 2 · objm at-200 03.07 · roadmapa L0-L8.

### Audyt 05.07 (Maintainability 5/10 · Scalability 3/10 · Production 6/10) — oś NIETKNIĘTA od 2 mies.
- ⚡ Wydajność: subprocess w asyncio (telegram_approver), ThreadPool 10w×2vCPU oversubscribe.
- 📈 Skala: single-server SPOF; **RC1 filesystem-as-IPC** (=K1 z 30.06 — ten sam korzeń znaleziony 2× w odstępie 2 mies.); RC4 JSONL unbounded; per-process cache drift; hardcoded BIALYSTOK; brak tenant_id. Scenariusz 10×: wykłada się PERSISTENCE, nie compute → Postgres+Redis pre-Restimo Q3.
- 🛡️ Odporność: brak HA (restart telegrama traci pending in-memory); F2/RC3 silent cron (wtedy: overrides-reset martwy 4 dni; DZIŚ: cod-weekly FAILED 2 dni — powtórka klasy); observability tylko anticipated failures; brak SLO; systemd bez WatchdogSec/MemoryMax; RC6 replay re-runs current code; brak logrotate części plików.
- 🔒 Bezpieczeństwo: **praktycznie nie było tematem ŻADNEGO audytu** (jedynie wzmianka M6 multi-tenant RLS) → biały obszar.
- Migracje M1-M5 (Postgres→Redis→event sourcing→state_io→liveness) NIE wykonane; czerwcowa obserwowalność rozbudowana AD-HOC — i sama okazała się w 19-25/49 kłamliwa (ścieżka „polished symptoms", przed którą 05.07 ostrzegał).
