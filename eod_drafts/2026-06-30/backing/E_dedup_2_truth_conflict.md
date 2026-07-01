# FAZA E DEDUP — klaster R3-R7 koherencja (truth + conflict)

**Sesja tmux 2 · 2026-06-30 · TRYB READ-ONLY.** Wejście: WF2_DIGEST (FAZA C 49 werdyktów + FAZA D 81 konfliktów + findingi B klas D/E/N/L/I/test/M-instrument) + A3_flag_registry + A4_instrument_registry. Świeże grepy HEAD potwierdzone (numery linii dryfują — zweryfikowane 2026-06-30).

**Zakres klastra:** dryf-flag (D), kłamiące-przyrządy (E: 19 VOID + 6 UNTESTED z oracle), rozsyp-progów (N), test-integrity (B19), KONFLIKTY (Faza D, 64 problematyczne = 35 inconsistent + 15 silent-inversion + 14 undefined), koherencja (I). **NIE-mój** (xref tylko): R1 one-source A1/A2/J, R2 placement B/C, czysty K-deadcode, czysta produkcja-sentineli K5, oś-kalibracji G/K3.

**merged_count_note:** ≈230 surowych findingów w klastrze (FAZA C: 19 VOID + 6 UNTESTED + ~25 VALIDATED-z-sub-findingami E/D/N/H/M/I ≈ 95 linii; FAZA D: 81 konfliktów; FAZA B: klasy D~9 / E~15 / N~10 / L~12 / I~3 / test~10 ≈ 55) **→ 28 distinct-rootów**. Anti-double-count: 19 VOID przyrządów → 11 instrument-truth rootów (feas_carry×3→1; reassign-ghost+a2+best_effort_fastest+wiring 4-instancje→1; objm peak+G2b+shadow 3→1; conftest 2→1; **wspólny M `_append_jsonl`-swallow ≥8 instancji→1**; **wspólny H stale-.txt ≥6→1**; verdict-source-trap 3→1). 64 problematyczne konflikty → 10 conflict-rootów (każda rodzina co powtarza się 3-6× w D01-D05 = 1 root). 8 N-progów → 1 meta-root + 2 wyróżnione (R6-cap, czasówka-60).

**⚠ ADVERSARIAL (zaznaczone):** `post_shift_overrun_forward_replay` = **VALIDATED tu (C18)** vs **VOID w allocation_family + B13-K-14 (0/282)** → świeży grep ledgera: `post_shift_overrun_min` **454×/2000 linii = OBECNY** → VOID-claims STALE (pre-unify/zła próbka). Root R13.

**⚠ C9/C11 caveat:** „naprawione 29.06" bez oracle-PASS = PLAUSIBLE. Oracle Fazy C OBALIŁ część: bug4_reseq (R4), conftest-257d315 (R7), feas_carry (R1) — te 3 „naprawione" są VOID. POTWIERDZIŁ: drive_speed (N/A by-design), objm-G2c-monitor (per-decyzja OK), would_hard_cap (serialize d23d8a1), checkpoint_tz, gps_delivery.

---

## A. INSTRUMENT-LIE ROOTY (klasa E — VOID/UNTESTED; „przyrząd kłamie")

### R1 · feas-carry-instruments-predict-not-outcome `P1 OPEN SOURCE`
**3 VOID przyrządy feas_carry liczą benefit/regret/true_delta z PREDYKCJI silnika (`objm_r6_breach_max_min`), ZERO joina z `decision_outcomes`/`delivered_at`/`gps_truth`.** „Pozytywny wpływ" zadeklarowany 27.06 napędził flip ENABLE_FEAS_CARRY_READMIT ON → rollback. Sentinel ~10000-min objm (niefizyczny dla R6=35) DOMINUJE raportowany SUM regret (most do K5). Tripwire `bad_regret=regret<=0` strukturalnie martwy (regret=chosen−rej>0 z definicji). Akcja LIVE promuje HARD-NO→MAYBE na top[0] (most do R24).
- instance_refs: `eod_drafts/2026-06-27/feas_carry_readmit_replay.py:75,101`, `tools/feas_carry_readmit_postflip.py:81`, `dispatch_pipeline.py:1228,1267`, `dispatch_pipeline.py:6266-6293`
- klasy: E,G,M,C,B,F · seed: K5 + K1 + void-instrument · sibling UNTESTED: `pickup_lateness_shadow` (przeszacowuje ~2.6×, brak outcome-join — ta sama rodzina prediction-space, C13)
- consolidation_target: każdy przyrząd-werdykt „dowód pozytywnego wpływu" MUSI joinować `gps_delivery_truth.jsonl`/`decision_outcomes.jsonl` PRZED flip-justyfikacją; jedno źródło prawdy-fizycznej.
- why_recurs: instrument czyta WŁASNY predykcyjny shadow-log; „naprawione 29.06" = deklaracja, oracle obalił (PLAUSIBLE→VOID).

### R2 · out-of-engine-position-classifier-drift `P1 OPEN SOURCE`
**Pozycja-równość scalona w silniku (`_selection_bucket`, equal-treatment 22-24.06) ale ≥4 przyrządy/gate'y POZA silnikiem trzymają WŁASNĄ taksonomię pozycji niezrównaną → kłamią o ratunkach/selekcji.** `reassignment_forward_shadow._SYNTH_POS` (59% fałszywych ratunków; niespójny na 7/10 żywych tokenów) + `a2_selection_shadow._pos_bucket` (demotuje no_gps/pre_shift BEZWARUNKOWO, zamrożony model sprzed equal-treatment) + `best_effort_fastest_pickup_shadow` (VOID, blind-check martwy) + `auto_assign_gate G7` + `feed.py` bez `_pos_trusted`. + would×quality WIRING: Telegram-notify=would (margines 70%), konsola=quality (7.7%), at-193 waliduje quality NIE would.
- instance_refs: `tools/reassignment_forward_shadow.py:64,205,260,353,457`, `tools/a2_selection_shadow.py:182`, `dispatch_pipeline.py:6812`, `auto_assign_gate.py:163`, `nadajesz_clone/panel/backend/app/integrations/ziomek/feed.py:258`
- klasy: E,F,B · seed: K1 position-twin (8 bliźniaków) + R1-one-selection-key (xref allocation-agent — NIE re-derywować silnik) + K5
- consolidation_target: out-of-engine/shadow position-klasyfikatory importują/wiążą `_selection_bucket`; golden-test out-of-engine vs engine na wspólnym wejściu; would/quality jedną bramką.
- why_recurs: twin-scatter — łatana ≥4× (path-asymmetry), żaden test nie wiąże out-of-engine z `_selection_bucket`; reassignment-ramię RATUNEK VALIDATED po env-flip 29.06 (53.9%→20.3%), ale klasyfikator-źródło wciąż zdryfowany.

### R3 · objm-shadow-canary-twins-alltick `P1 OPEN SOURCE`
**Rodzina objm_lexr6: monitor-G2c naprawiony per-decyzja (397a665 VALIDATED), ale BLIŹNIAK `peak_verdict._g2c_note` wciąż headlineuje ALL-TICK (×7-11 zawyżka) — werdykt Fazy-4 na zawyżonej metryce.** Durable .txt 29.06 SAM SOBIE PRZECZY (gate per-decyzja 3.7% vs headline all-tick). G2b-auto-route porównuje do 5-dniowego single-day OFF-baseline (89.13% z 25.06) = stale-axis. + `_objm_lexr6_shadow._lex_qual` ZAMROŻONA 3-krotka vs kanon `objm_lexr6.lex_qual` warunkowo 4-krotka → zgodne TYLKO bo POST_SHIFT OFF; **flip C7 (POST_SHIFT ON) rozjedzie cień = kłamiący przyrząd** (jest też silent-inversion D01/D02/D03 — most do konfliktu).
- instance_refs: `tools/objm_lexr6_peak_verdict.py:71`, `tools/objm_lexr6_canary_monitor.py:327,339`, `dispatch_pipeline.py:1097-1126`, `objm_lexr6.py:40-46`
- klasy: E,B,G,I,N,M · seed: C7-bramka (NIE bug, świadoma) + R1 frozen _lex_qual (A6 grupa1) + void-instrument
- consolidation_target: peak_verdict używa per-decyzja (jak monitor); shadow-twin = kanon (warunkowa krotka) albo usunięty; baseline G2b odświeżany.
- why_recurs: twin-scatter (fix trafił monitor nie peak_verdict); fragile-twin uzbrojony na flipie POST_SHIFT.

### R4 · bug4-reseq-invariant-misspec `P1 OPEN SOURCE`
**VOID: inwariant `delta>=0` ZLE ZDEFINIOWANY** (świeży solve minimalizuje `(sla_violations, total_duration)`, nie OSRM-drive → 123/1074=11.5% „naruszeń" to mis-spec, nie bug); **własny health-gate przyrządu PADA na żywej próbie (suspect 11.5%>10% → GO=False) i instrument sam pisze „pomiar wciąż skażony"**. `fresh_drive` liczony nad sortem-po-predicted-ts (resztka proxy-sort #8). Stale verdict.txt kłamie „logger nic nie zapisał" gdy jsonl ma 1074 świeże rekordy.
- instance_refs: `plan_recheck.py:1708,1726`, `tools/bug4_reseq_verdict.py:42,87,97`
- klasy: E,F,O,H · seed: A4-smell-E (11 „naprawionych" = PLAUSIBLE) — **NOWY distinct root, NIE K1-N-kopii**
- consolidation_target: inwariant zdefiniowany na TYM SAMYM objektywie co solve (total_duration/sla), nie na proxy-drive; health-gate jako bramka GO.
- why_recurs: „naprawione 29.06 (5623122)" = deklaracja; oracle CONFIRMED VOID własnym gate'em na danych (C9/C11).

### R5 · global-allocate-geometry-blind-certification `P1 OPEN SOURCE`
**VOID: `reassign_global_select_review` certyfikuje LICZBĘ de-pile (maxpile redukcja VALIDATED) ale jest ŚLEPY na GEOMETRIĘ — deklaruje „worek kept = feasibility-validated 3-4 OK" gdy ground-truth: 35.2% (710/2019) multi-drop ma spread>8km (R1!).** De-pile redukuje pile-on ale TWORZY worki R1-łamiące. Over-hide guard koarsy (konflacja benign z bug) + stale one-shot werdykt. `no_courier=0/3044` → nigdy nie eskaluje KOORD pod scarcity.
- instance_refs: `tools/reassign_global_select_review.py:71,100`, `objm_lexr6.py:29`, `tools/pending_global_resweep.py:200,419`
- klasy: E,G,H,M · seed: P0-A geometria-ślepa (xref allocation-agent — runtime-oracle POTWIERDZA istniejące P0-A, NIE nowy) + K5 + P0-B
- consolidation_target: review certyfikuje ground-truth geometrię (deliv_spread przez feasibility), nie tylko count; MUSI ZABLOKOWAĆ flip PENDING_RESWEEP_LIVE póki geometryczny człon brak.
- why_recurs: instrument certyfikuje proxy (count) zamiast ground-truth (geometria) — wzorzec C11.

### R6 · carried-first-guard-empty-env-void `P1 OPEN SOURCE`
**VOID: strażnik #1 biegnie z PUSTYM env systemd → reużyte funkcje silnika (`_start_anchor`/`_apply_canon_order_invariants`) czytają 14 route/canon flag jako default-OFF (`os.environ.get` at-import) → N-procesów=N-konfiguracji.** Detektor liczy względem OKROJONEGO kanonu (pomija `_relax_*`/no-return). Fikcyjne `no_position` dominuje 1025/1177=87% (sentinel rozjeżdżający się z realną pozycją konsumentów). Claim „liczy IDENTYCZNIE jak silnik" = fałsz.
- instance_refs: `tools/carried_first_guard.py:5,100-101,121`, `plan_recheck.py:347,570-594`, `dispatch-carried-first-guard.service Environment=∅`
- klasy: D,E · seed: K1 brak-jednego-źródła-flag + D flag env-frozen (NOWA instancja „reused-engine-instrument under default-OFF env", bliźniacza do R3 frozen _lex_qual + R6-flag-drift) + K5
- consolidation_target: przyrząd reużywający funkcje silnika MUSI dziedziczyć env silnika (drop-in lub jawny config), nie pusty default; jedno źródło stanu-flag dla N procesów.
- why_recurs: env-frozen route/canon flagi czytane os.environ.get at-import → każdy proces=własna konfiguracja (= R14 flag-drift, manifestacja w przyrządzie).

### R7 · conftest-flag-leak-not-fixed `P1 OPEN SOURCE`
**VOID×2: (a) `conftest._isolate_flags_json` strippuje TYLKO ETAP4+NUMERIC+INFRA → 62 decyzyjno-kształtne flagi przeciekają prod-ON (test „OFF" cicho biegnie ON); (b) status „NAPRAWIONE 257d315" + ledger „11 kłamstw naprawione 29.06" ZAWYŻA — 257d315 dodał TYLKO 3 stałe (łatka-na-instancje), 62 survivors zostają.** + `flag_effect_coverage_check` skanuje TYLKO ETAP4 (91.5% zielono DOKŁADNIE omijając klasę co przecieka) + `flag_doc_baseline` CZERWONY (ENABLE_AUTO_ASSIGN stale). + `flag_fingerprint` pokrywa 63/≥90 flag (instrument który ma WYKRYWAĆ D2 sam <70%).
- instance_refs: `tests/conftest.py:307,190`, `common.py:133`, `tools/flag_effect_coverage_check.py:18`, `tools/flag_doc_baseline.json:9`, `common.py:370` (fingerprint)
- klasy: D,E · seed: K1 brak-jednego-źródła-flag (ETAP4 = single keying-point dla 3 mechanizmów; co poza = ślepe)
- consolidation_target: jeden rejestr flag → conftest-strip + fingerprint + flag_effect + doc-baseline keyowane z tego samego źródła; flaga decyzyjna = automatycznie objęta.
- why_recurs: edge-patch (łatka na 3 instancje nie u źródła) — „wzorzec carried-first naprawiane 10×"; klasa cicho wraca przy NASTĘPNEJ fladze poza ETAP4.

### R8 · serializer-allowlist-metrics-vanish `P1 OPEN SOURCE`
**META-root klasy E: `_AUTO_PROP_PREFIXES` (shadow_dispatcher.py:190) to allowlist explicit-lub-prefiks BEZ kontroli kompletności → każdy nowy klucz metrics GINIE z master-ledgera dopóki ktoś ręcznie nie doda prefiksu. 38 kluczy nadal ginie (14 HARD).** Świeży grep: `eta_source`=0/2000 (prowenancja ETA: realny route vs FIKCJA — z ledgera nie wiadomo czy zwycięska ETA=BIALYSTOK_CENTER). Ginie też: G-2 `r6_gold4_gate_recovered` (jedyna ścieżka >35 przechodzi — luzowanie R6 niemierzalne), sla_violations-detail (kalibracja O2 ślepa), V328 mass-fail-diag (scarcity→pile-on nieidentyfikowalny), R6-internal, pickup_dist_km, threshold-prov. **TEN SAM root co 11 compute-but-vanish 28.06.** would_hard_cap był instancją — NAPRAWIONY (d23d8a1, VALIDATED) → dowód że root żyje, fix per-klucz nie u źródła.
- instance_refs: `shadow_dispatcher.py:190,272`, `dispatch_pipeline.py:5289` (eta_source), `feasibility_v2.py:1098` (G-2), `feasibility_v2.py:1182` (sla-detail), `dispatch_pipeline.py:5815` (V328)
- klasy: E,N,B · seed: void-instrument + most do unified-audit K5 (V328 pool-shrink) + A6-grupa4 (SLA-detail O2 02.07)
- consolidation_target: serializer = deny-list lub kompletność-kontrola (każde pole metrics serializowane lub jawnie wykluczone z powodem), nie explicit-allowlist; jeden punkt prawdy ledgera.
- why_recurs: edge-patch — każdy fix dodaje 1 prefiks/klucz (would_hard_cap), root (brak kontroli kompletności) zostaje → następna metryka ginie.

### R9 · instrument-append-jsonl-silent-swallow `P2 OPEN SOURCE`
**Wspólny M-root (≥8 instancji): `_append_jsonl` łapie wyjątek zapisu i `_log.warning` POŁYKA → utrata danych przyrządu NIEWIDOCZNA (instrument może „milczeć" zamiast krzyczeć).** Świeży grep potwierdza wzorzec w: bundle_calib_shadow, reassignment_forward_shadow, b_route_shadow, pending_global_resweep, prep_bias_shadow, fleet_position_snapshot, carried_first_guard, checkpoint_tz_shadow, ziomek_time_route_monitor, address_mismatch, min_delivered (mda compute).
- instance_refs: `tools/bundle_calib_shadow.py:523`, `tools/reassignment_forward_shadow.py:413`, `tools/b_route_shadow.py:336`, `tools/checkpoint_tz_shadow.py:144`, `nadajesz_clone/panel/backend/tools/ziomek_time_route_monitor.py:360`, `address_mismatch.py:118`, `dispatch_pipeline.py:6047`
- klasy: M · seed: A4 §8 M-cluster
- consolidation_target: jeden helper `_append_jsonl` fail-loud (alert/counter na utratę), nie per-tool swallow.
- why_recurs: skopiowany wzorzec defensywny (try/except: warning) w każdym nowym przyrządzie — twin-scatter.

### R10 · stale-txt-verdict-no-ttl `P2 OPEN SOURCE`
**Wspólny H-root (≥6 instancji): werdykt-pliki .txt = ZAMROŻONE snapshoty bez TTL/„stale" markera → sesja czytająca je jako „bieżący werdykt" dostaje nieaktualny stan.** address_mismatch_review.txt (07:00 deklaruje max 7598m, żywy log 14036m), drive_speed_overshoot_verdict.txt (29.06 07:14), bug4_reseq_verdict.txt (29.06 07:41), objm_lexr6_peak_verdict_*.txt, min_delivered result.txt (27.06), checkpoint_tz jsonl (27.06 STALE obok żywych — mylące przy ślepym `ls`).
- instance_refs: `dispatch_state/address_mismatch_review_verdict.txt`, `dispatch_state/drive_speed_overshoot_verdict.txt`, `tools/bug4_reseq_verdict.py:97`, `eod_drafts/2026-06-25/min_delivered_at_verdict_result.txt:2`, `tools/checkpoint_tz_shadow.py:43`
- klasy: H,L · seed: A4 §8 H-class
- consolidation_target: każdy .txt werdykt z timestamp+TTL+„stale gdy mtime>kadencja" markerem; albo emit do durable jsonl z ts.
- why_recurs: brak konwencji stale-marker — point-in-time snapshot nadpisywany/nieodświeżany.

### R11 · verdict-reader-wrong-stale-partial-source `P1 OPEN SOURCE`
**Werdykty czytają ZŁE/NIEPEŁNE źródło prawdy → strukturalnie ślepe.** (a) `min_delivered_at_verdict` ROTATION-BLIND: czyta TYLKO żywy `shadow_decisions.jsonl` whole-file (brak .1/.gz) → ślepy na dane >1 dzień → FAŁSZYWE „INCONCLUSIVE/mało danych" (re-run daje materialność 33%); (b) `b_route_shadow_review` czyta ZAMROŻONY `dispatch_state/sla_log.jsonl` (mtime 20.06) zamiast żywego `scripts/logs/` → real_joined=0 (powinno ~289), GO-kandydat nieosiągalny — **TRAP master-ledger: shadow_decisions leży w `scripts/logs/` NIE `dispatch_state/`**.
- instance_refs: `eod_drafts/2026-06-25/min_delivered_at_verdict.py:16,85`, `tools/b_route_shadow_review.py:18-20,55-69`
- klasy: E,H · seed: A4 handoff#1 (master-ledger=scripts/logs/shadow_decisions.jsonl, NIE dispatch_state/) + rotation-blindness
- consolidation_target: każdy werdykt czytający shadow_decisions = rotation-aware (.1/.gz) + repoint na żywe scripts/logs; jeden helper-loader ledgera.
- why_recurs: dual-path źródła (dispatch_state vs scripts/logs) + whole-file-bez-rotacji — każdy nowy werdykt powiela pułapkę.

### R12 · dead-producer-orphan-consumer-shadow-logs `P2 OPEN`
**VOID: shadow-logi z MARTWYM producentem lub OSIEROCONYM konsumentem udają żywy dowód.** `c5_shadow_log.jsonl` = 100% test-pollution (producent `wave_scoring.compute_wave_adjustment` DEAD Z-22, 0 prod-callerów; testy piszą do PROD-ścieżki bez monkeypatch — mtime świeży MYLI). `c2_shadow_log` (20280 wiernych rekordów hot-path) czytany WYŁĄCZNIE przez `analyze_shadow_logs.py`; `a2_selection_shadow` czytany tylko przez `weekly_a2_digest.py`.
- instance_refs: `dispatch_state/c5_shadow_log.jsonl`, `tests/test_wave_scoring.py:253`, `tools/analyze_shadow_logs.py:31`, `tools/weekly_a2_digest.py:25`
- klasy: E,K,M · seed: shadow-jobs-registry backlog (instrument-hygiene)
- consolidation_target: producent DEAD → log usunięty/oznaczony; test→prod state-bleed zablokowany (monkeypatch path); orphan-consumer udokumentowany.
- why_recurs: test pisze do prod-ścieżki (state-bleed); martwy producent zostawia świeży-mtime artefakt.

### R13 · post-shift-replay-validated-vs-void-ADVERSARIAL `P2 OPEN`
**⚠ SPRZECZNOŚĆ CROSS-AUDYT: `post_shift_overrun_forward_replay` = VALIDATED tu (C18: świeży ledger 1699/438, NIE 0) vs VOID w `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family` + B13-K-14 (0/282).** Świeży grep ROZSTRZYGA: `post_shift_overrun_min` 454×/2000 = OBECNY → VOID-claims STALE (audyt-analiza nieświeża po F2-fix/unify). + `best_effort_fastest_pickup_shadow` analogicznie void-claimed-ale-żywy (a8cdb95 unify 29.06). + `sequential_replay._determine_verdict` UNTESTED (ETAP-5 fleet-gate bez testu/żywego wołacza + hardcoded lower-better = I-inwersja dla celu higher-better).
- instance_refs: `tools/post_shift_overrun_forward_replay.py:38`, `eod_drafts/2026-06-30/ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md:68`, `tools/sequential_replay.py:742,775`
- klasy: E,I,H · seed: void-instrument + C9/C11 (nie ufać void-claimom na słowo — re-grep świeży)
- consolidation_target: void-claim wymaga świeżego grepa ledgera PRZED zapisem; rozstrzygnięcie: VALIDATED (świeży stan), VOID-claims wycofać.
- why_recurs: audyt-analiza nieświeża po unify/serialize-fix — meta-finding, NIE silnik-bug.

---

## B. FLAG-DRIFT ROOT (klasa D)

### R14 · flag-state-3-layer-no-single-source `P1 OPEN SOURCE`
**Stan decyzyjny ≠ jeden plik: 3 warstwy (flags.json hot / drop-in env-frozen / stała modułu) różne per-proces; `flag_fingerprint` pokrywa 63/≥90 → fałszywe zapewnienie parytetu (instrument-lie meta).** 23 route/canon env-frozen (plan_recheck.py, odczyt at-import) + OR_TOOLS_TSP/SAME_RESTAURANT_GROUPING (sprzężone, flip jednej→double-insert) + USE_V2_PARSER (=1 tylko panel-watcher → shadow=V1, dwa parsery na ten sam panel) POZA fingerprintem/flags.json. Dead-but-ON: `ENABLE_PANEL_IS_FREE_AUTHORITATIVE` (env-default ON, 0 konsumentów), `ENABLE_TRANSPARENCY_SCORING` (True, 0 konsumentów). Override-path-chaos (bare/env/flags.json bez reguły). 71 ENABLE_* + 41 bool POZA rejestrami.
- instance_refs: `common.py:370` (fingerprint), `common.py:2356,3159` (OR_TOOLS/GROUPING), `panel_client.py:93` (USE_V2_PARSER), `common.py:1144` (panel_is_free), `common.py:270` (override-chaos), `tests/conftest.py:307`
- klasy: D,E,J · seed: K1 brak-jednego-źródła-flag + K7 cross-proces (USE_V2_PARSER, env-frozen twin)
- consolidation_target: jeden rejestr flag = kanon hot-reload (ETAP4-styl) obejmujący route/canon+solver+parser; fingerprint=wszystkie decyzyjne; brak module-const-env-frozen dla decyzji.
- why_recurs: każda nowa flaga env-frozen omija fingerprint+conftest+doc; twin dodany do 1 serwisu nie do bliźniaka (PLAN_SEQUENCE_LOCK tylko plan-recheck).

---

## C. KONFLIKT-ROOTY (Faza D — klasa I / sprzeczności; każda rodzina = 1 root)

### R15 · r6-anchor-vs-sla-anchor `P1 OPEN SOURCE` (6 instancji)
**DWIE HARD-bramki tej samej decyzji „spóźnienie" kotwiczą RÓŻNY anchor: R6-thermal=`pickup_ready_at` (gotowość) vs SLA=`pickup_at` (TSP-projected).** Dla późnego dojazdu R6 ostrzejszy, SLA optymistyczny (gdy kurier zajęty pickup_at later). `_count_sla_violations` NIE woła `r6_thermal_anchor` mimo docstringu „JEDNO źródło". Precedencja gdy rozjazd = NIEROZSTRZYGNIĘTA (undefined). Produkt uboczny `plan.sla_violations` (luźny pickup_at) przecieka do `_o2_key` rankingu → kanon rankowany na kotwicy którą R6-gate nazywa BŁĘDNĄ.
- instance_refs: `route_simulator_v2.py:635,663`, `feasibility_v2.py:1135,1156`, `plan_recheck.py:683,1670`, `bundle_calib_shadow.py:224` (4. wariant carried=min())
- klasy: I,A1 · seed: K1 one-SLA/R6-anchor (3-4 bliźniaki) · konflikt-natura: undefined + defined-inconsistent (D01/D02/D03/D04/D05)
- consolidation_target: jeden `r6_thermal_anchor` helper konsumowany przez R6+SLA+O2+bundle_calib; golden-test równoważności kotwicy.
- why_recurs: twin-scatter (route_simulator/feasibility/plan_recheck/bundle_calib 4 kopie) + brak runtime-inwariantu anchor-consistency.

### R16 · r6-cap-35-flat-vs-40-tier-plus-quantile `P1 OPEN SOURCE` (5+ instancji)
**Ta sama reguła R6 (cap świeżości) — 3 progi + rozluźnienie: 35 płaski (feasibility HARD-reject) / 40 tier-3-stretch (best_effort/objm cap_min, always-propose) / 35 płaski (bundle_calib over-penalizuje T3) / p80-quantile-recovery (ETA_QUANTILE_R6_BAGCAP ON luzuje HARD R6 dla gold≤4 — jedyne >35 ready-anchored co przechodzi).** Kandydat carry 38min: feasibility=NO ale best_effort go bierze. Kanon C5: „40=TYLKO ALARM, normalnie 35 dla każdego — kod NIEZGODNE". Quantile-recovery = inwersja HARD↔SOFT vs kanon D3 „35 bez wyjątków" + kalibracja na ZŁEJ osi (G-1: p80 delivery-pesymizm, realny błąd=poślizg odbioru). N1: 35/40 w 6 niezsynchronizowanych miejscach, baza HARD=35 bare-literał (code-edit only), wyjątek 40 hot-tunable.
- instance_refs: `common.py:763` (35), `common.py:2651` (40), `feasibility_v2.py:1089,1105`, `tools/bundle_calib_shadow.py:56,280`, `dispatch_pipeline.py:633,666,6859`
- klasy: I,N,G · seed: K1-N-kopii-progu + K3 optymistyczny poślizg (quantile axis) · konflikt-natura: defined-inconsistent + inversion HARD↔SOFT (D01/D03/D04/D05)
- consolidation_target: `r6_cap_for_tier()` jeden helper (35 normalnie / 40 TYLKO tryb ALARM z bramką); quantile-recovery na osi poślizgu-odbioru nie delivery-pesymizmu; flip O2 02.07 rusza WSZYSTKIE 6 razem.
- why_recurs: rozsyp-progów (N-kopii) + tuning wyjątku (40 hot) desynchronizuje bazę (35 bare).

### R17 · paczka-r6-exempt-inverted-in-ranking `P2 OPEN SOURCE` (3 instancje)
**`PACZKA_R6_THERMAL_EXEMPT` zwalnia paczkę z HARD-R6 w 3 sites (feasibility termik :1050 + SLA-detail :1152 + is_paczka), ale ranking/objektyw NIE ma exempt: `_count_sla_violations` (route_simulator) + O2-sweep liczą paczkę jako spóźnioną → exempt ODWRÓCONY w warstwie selekcji 7/9.** `plan.sla_violations` „kłamie" na paczce (B liczy ją, A nie). 4. site exempt na flipie O2 02.07 (protokół Załącznik B).
- instance_refs: `feasibility_v2.py:1050-1055,1152`, `route_simulator_v2.py:635-660`, `plan_recheck.py:690`, `common.py:3479`
- klasy: I,B · seed: K1 one-SLA-anchor (paczka-asymetria) + most O2 02.07 · konflikt-natura: silent-inversion (D03/D04/D05)
- consolidation_target: exempt w jednym anchor-helperze (R15) → automatycznie spójny w feasibility+SLA-count+O2.
- why_recurs: edge-patch (exempt dodany do HARD-sites, pominięty w count/O2) — path-asymmetry.

### R18 · equal-treatment-vs-discriminate-position `P1 OPEN SOURCE` (4+ instancje)
**Silnik: no_gps/pre_shift RÓWNO (3 flagi NO_GPS_EQUAL+EQUAL_BUCKET+PRE_SHIFT_EQUAL ON, konkurują po score) <> out-of-engine gates DYSKRYMINUJĄ pozycję (R2) + V3.16 `_demote_blind_empty` (oś OBCIĄŻENIA, własne klasyfikatory NIE `_selection_bucket`) + EQUAL_NO_PENALTY zachowuje FAR-veto −1000.** Sprzeczność osi-krzyżowej: flaga osi-pozycji wyłącza ochronę osi-obciążenia (pusty bag s_obciazenie≈100 baseline ~82 wygrywa z realnym GPS — regresja V3.16 tylnymi drzwiami). Trójca równości gated 3 osobnymi C.flag() — częściowy flip = niespójna równość (8 kombinacji). Kanon §4:86 „ZAWSZE równo" vs §7-T4:151 „kara −20 wciąż w kodzie" = sprzeczność WEWNĄTRZ kanonu.
- instance_refs: `dispatch_pipeline.py:2451,2467,2504`, `feasibility_v2.py:763`, `dispatch_pipeline.py:3283` (FAR-veto), `ZIOMEK_REGULY_KANON.md:86,151`
- klasy: I,D · seed: K1 position-twin + equality-inversion-stack (D04) · konflikt-natura: silent-inversion + defined-inconsistent (D02/D03/D04)
- consolidation_target: jedna oś-pozycji (`_selection_bucket`) + oś-obciążenia ortogonalna; trójca flag → jedna bramka równości; usunąć FAR-veto −1000 albo udokumentować jako świadomy wyjątek.
- why_recurs: spiętrzone inwersje równości (22-24.06) zdjęły tarcie → V3.16 demote wraca; kod-default = stan PRZED inwersją, równość trzymana 3 flagami (1 flip→regres).

### R19 · frozen-committed-vs-preshift-floor `P1 OPEN SOURCE` (3 instancje)
**frozen-R27 committed pickup NIETYKALNY (TSP soft-window + OSRM-never-overrides + PIN_AGREED plan_pv-przed-OSRM) <> pre-shift floor pickup≥shift_start (clamp na ścieżce OSRM/departure).** Floor żyje na ścieżce OSRM, frozen ją OMIJA (plan_pv wybrany przed osrm[i]) → floor=no-op gdy committed<shift_start; **frozen AKTYWNIE broni złego pre-shift czasu** (czasówka/elastyk committed pre-shift legalne). Precedencja 4 clampów (frozen>floor>OSRM) — każda powierzchnia inny podzbiór/kolejność, brak chokepointu. Debias PICKUP_DEBIAS=4.5 shadow-only NIE dożywa do żywego floor → floor floruje SUROWY optymistyczny estymat (~18min slip).
- instance_refs: `route_simulator_v2.py:1086`, `courier_orders.py:872`, `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:519,857`, `feasibility_v2.py:798`, `common.py:3131` (debias)
- klasy: I,M · seed: K2 plan_recheck-cofacz + R4 floor (xref pre-shift-agent — TU os frozen↔floor precedencja) · konflikt-natura: silent-inversion + undefined (D02/D05)
- consolidation_target: jeden chokepoint clampów odbioru (frozen>floor>OSRM jawna kolejność); floor obejmuje frozen gdy committed<shift_start; debias dożywa do żywego eta.
- why_recurs: floor=render-clamp/edge-patch (16. powierzchnia) zamiast u źródła; frozen omija ścieżkę OSRM gdzie floor żyje.

### R20 · commit-divergence-masking-and-silent-off `P1 OPEN SOURCE` (5 instancji)
**JEDYNA prawdziwa inwersja maskująca: `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` const env-default '1'=True (common.py:2805) maskowane flags.json=False → usunięcie KLUCZA z flags.json → `decision_flag` spada na const=True → verdict-gate CICHO flipuje ON → utrata ALWAYS-PROPOSE (KOORD-redirect wraca).** Rodzina silent-OFF: „bezpieczny fallback OFF" jest MISNOMER dla flag fail-open/floor/exempt — FAIL12_SCHEDULE_FAILOPEN / PRE_SHIFT_DEPARTURE_CLAMP / PACZKA_R6_THERMAL_EXEMPT na utracie klucza cicho spadają na const=OFF → COFAJĄ świadomą decyzję (fail-CLOSED / brak floor / paczki R6-reject).
- instance_refs: `common.py:2805-2806`, `dispatch_pipeline.py:6523`, `feasibility_v2.py:686` (silent-OFF), `common.py:264` (PRE_SHIFT_EQUAL)
- klasy: M,I,D · seed: void-instrument + D5-silent-OFF · konflikt-natura: silent-inversion (D01/D02/D03/D04)
- consolidation_target: flagi fail-open/floor/exempt = const env-default ON jawnie + obecne w flags.json jako kanon (nie maskowanie); usunięcie klucza ≠ cichy flip.
- why_recurs: precedencja decision_flag (json>const>False) + const-default niezgodny z intencją = utrata klucza odwraca decyzję.

### R21 · name-vs-behavior-hard-misnomers `P2 OPEN SOURCE` (6 instancji)
**Nazwa sugeruje HARD-bramkę feasibility, zachowanie = metryka/SELEKCJA-tier → ryzyko że przyszła sesja potraktuje jako bramkę i złamie always-propose.** `R_RETURN_TO_RESTAURANT_VETO` (nazwa VETO=HARD, feasibility metric-only „NIGDY nie przerywa"; realny zakaz w kanonie no-return plan_recheck = split-enforcement). `LATE_PICKUP_HARD_GATE` (nazwa HARD_GATE, zachowanie=demote tieru „NIE hard-reject—ZAWSZE propozycja"; próg 5min koliduje z R27±5).
- instance_refs: `feasibility_v2.py:905-914`, `plan_recheck.py:1518-1519`, `dispatch_pipeline.py:4615,5953`, `common.py:2822`
- klasy: L,I,B · seed: L słownictwo + split-enforcement · konflikt-natura: defined-inconsistent + inversion-pozorna (D01/D02/D03/D04)
- consolidation_target: nazwa odzwierciedla warstwę (zdejmij VETO/HARD_GATE z nazwy metryki/selekcji); egzekucja w jednej warstwie albo jawnie udokumentowany split.
- why_recurs: nazwa-vs-zachowanie myli precedencję; egzekucja rozdzielona (feasibility metryka, kanon zakaz).

### R22 · fleet-load-multi-mechanism-tax `P2 OPEN` (4+ instancje)
**2-3 reguły OBCIĄŻENIA żywe RAZEM, która rządzi NIEOKREŚLONE: R-10 FLEET_LOAD_BALANCE (±15 score, ON) + FLEET_LOAD_GOVERNOR (EFEKTYWNY ON — flags.json:165 nadpisuje const OFF; A2 błędnie podał OFF = dodatkowo flag-drift) + stopover bonus_r9 + bug4-cap → potrójna kara możliwa (odebrać LEPSZEMU obciążonemu).** loadgov_ewma karmi relaksację FAR-veto pre_shift MIMO flagi LOADGOV (sprzężenie nieoczywiste). N5: bag-cap tier DWIE tabele rozbieżne (std 4 vs 5, slow 3 vs 4) — BUG4 SOFT pora-aware vs HARD_TIER flat.
- instance_refs: `dispatch_pipeline.py:1462,2303,3404`, `common.py:2103,2238,2556`, `common.py:1310,1326` (bag-cap 2 matryce)
- klasy: I,N,D · seed: undefined precedence + N rozsyp · konflikt-natura: defined-inconsistent + undefined (D01/D02/D04)
- consolidation_target: jedna reguła load (która warstwa rządzi); jedna tabela bag-cap-per-tier; A3 efektywny-stan governor sprostowany.
- why_recurs: wiele mechanizmów tego samego pojęcia (load) + flag-drift (A2 czytał const nie efektywny).

### R23 · r-declared-time-hard-no-runtime-invariant `P2 OPEN SOURCE` (3 instancje)
**R-DECLARED-TIME deklarowana HARD/najwyższa-precedencja („czas_kuriera ≥ czas_odbioru zawsze") ale ZERO runtime-bramki/inwariantu — TYLKO komentarze; egzekucja EMERGENTNA z R27+czasówka+`pickup_ready_at=czas_kuriera`.** Przyszła zmiana R27 cicho złamie bez tripwire. Kanon C-DT definiuje precedencję (nadrzędne nad R6 → propozycja przesunięcia ≥15min) ale brak strażnika.
- instance_refs: `common.py:3410,3494`, `dispatch_pipeline.py:3168` (TYLKO komentarz), grep runtime-gate=∅
- klasy: I · seed: K4 SOFT-only+brak-inwariantu · konflikt-natura: undefined + HARD-bez-egzekutora (D01/D02/D04)
- consolidation_target: runtime-inwariant `czas_kuriera>=czas_odbioru` jako tripwire (fail-loud); egzekucja jawna nie emergentna.
- why_recurs: HARD deklarowana komentarzem; brak gate = N-D (najwyższa reguła bez strażnika).

### R24 · feas-first-guard-blind-and-koord-valves-masked `P2 OPEN` (4 instancje)
**Sieć bezpieczeństwa wokół always-propose/readmit ILUZORYCZNA.** `_assert_feasibility_first` P0-INV = strażnik JEDNORAZOWY @5938, broni stanu w tym punkcie NIE emitowanego top[0] — łańcuch mutacji selekcji ciągnie do :6301 bez re-assert. FEAS_CARRY_READMIT promuje verdict=NO→MAYBE na top[0] ZA guardem (HARD-bypass, wzorzec #10, ACK-SAFE bo flaga OFF — latentna mina na flipie). 4 zawory KOORD prawie wszystkie wyłączone/zamaskowane przez always-propose (iluzja defense-in-depth); geometry_blind_fallback eskaluje KOORD BEZ checka always-propose (asymetria pokrycia). Backstop readmit aktualnie WYŁĄCZONY → flip readmit ON bez re-enable zaworów = re-dopuszczony HARD-NO bez siatki.
- instance_refs: `dispatch_pipeline.py:5938,6266-6293,6278,6491,6453`
- klasy: I,C · seed: void-instrument + wzorzec#10 HARD-bypass (xref allocation-agent R2/C placement) · konflikt-natura: silent-inversion (D02/D03)
- consolidation_target: runtime-inwariant re-assert na top[0] emitowanym (nie jednorazowy); flip readmit sprzężony z re-enable zaworów; geometry_blind spójny z always-propose.
- why_recurs: guard jednorazowy nie pokrywa łańcucha mutacji; zawory zamaskowane always-propose (latentne do flipu).

---

## D. ROZSYP-PROGÓW + LYING-DOC (klasa N / L)

### R25 · numeric-threshold-scatter-mixed-override `P2 OPEN SOURCE` (8 instancji)
**Ten sam próg skopiowany w N miejscach z mieszanymi ścieżkami override (bare-literał / env / flags.json-hot) BEZ reguły który-którym; strojenie wyjątku rozsynchronizowuje bazę.** R27 ±5 w 4-5 stałych (committed-tol/late-pickup-hard/late-pickup-soft/V3274-frozen). Pre-shift floor 30 = DWA progi (HARD-reject-distance vs gradient-near). margin=15 w 5 podsystemach (3× flags.json + 2× hardcode); min_score 50/40/30/60/-100. deliv-spread 8km w 2 stałych (R1 vs BUNDLE Fix-C). DWELL fallback twin (common vs route_simulator 1.0/3.5). dropoff-after-shift V324 żywa vs V325 martwy duplikat. META-override-path-chaos.
- instance_refs: `common.py:2554,2824,2840,3122` (R27±5), `common.py:1972,1989` (preshift-30), `flags.json:102,152,214` (margin-15), `feasibility_v2.py:90`+`common.py:2280` (8km), `common.py:1820,1997` (V324/V325)
- klasy: N · seed: K1-N-kopii-progu (META-override + A3 §efektywny-stan)
- consolidation_target: jedna nazwana stała per pojęcie + jedna ścieżka override (flags.json kanon); baza i wyjątek w tym samym mechanizmie.
- why_recurs: kopiuj-progu + tuning wyjątku hot zostawia bazę bare-stale (path-asymmetry override).

### R26 · czasowka-60-threshold-silent-desync `P1 OPEN SOURCE` (6 instancji)
**Próg „czasówka=60min" skopiowany 6× , TYLKO `EARLY_BIRD_THRESHOLD_MIN` hot-tunable (flags.json) — podbicie na 45 cicho desynchronizuje early-bird-KOORD-defer od czasówka-klasyfikacji → zlecenia w [45,60) wiszą w KOORD bez ścieżki czasówki; brak runtime-inwariantu early_bird==czasowka.** + early-bird/czasówka KOORD ORDER-LEVEL przed pulą zwiera obwód → pre-shift floor NIGDY nie biegnie dla ≥60-ahead; po release czasówki committed<shift_start wpada w R19 frozen.
- instance_refs: `common.py:430,1895,3413`, `auto_koord.py:32`, `panel_client.py:53`, `czasowka_scheduler.py:128`, `dispatch_pipeline.py:3503-3548`
- klasy: N,I · seed: K1-N-kopii-progu + I cicha-inwersja-po-hot-knob (D05 early-bird vs pre-shift floor undefined)
- consolidation_target: jedna stała czasówka-próg konsumowana przez early-bird+klasyfikację; runtime-inwariant early_bird==czasowka.
- why_recurs: hot-knob (EARLY_BIRD) rozsynchronizowuje 5 bare-60 (twin-scatter).

### R27 · lying-docstrings-and-stale-protocol-seeds `P2 OPEN SOURCE` (3 instancje)
**Docstring/seed deklaruje WARTOŚCI ≠ żywa stała → ZATRUWA audyt.** `compute_wait_courier_penalty` docstring: sweet≤5 / per-min −5 / HARD-REJECT>20, efektywne stałe: 3.0 / −8.0 / 15.0 — lying-doc który JUŻ zatruł audyt A2 (powtórzył „20" z docstringa). Seedy A2 + protokół STALE twierdzą `_best_effort_fastest_pickup_key` ma HARDCODED bucket i `_best_effort_objm_pick` jest 4-krotką z _ps_pen; świeży kod: OBA zunifikowane (selection_bucket/kanon lex_qual). Diagnostyczny literal „ZAWYŻONE ×~3,5" zahardkodowany w objm format-stringu.
- instance_refs: `scoring.py:126-129`+`common.py:2514,2519,2521`, `dispatch_pipeline.py:618` (anti-fałszywka), `tools/objm_lexr6_canary_monitor.py:339`
- klasy: E,L · seed: lying-doc (osobny od K1) + C9/C11 (czytaj świeży grep nie protokół-tekst)
- consolidation_target: docstring generowany ze stałej (lub test docstring==const); seedy/protokół oznaczone STALE+data.
- why_recurs: docstring/seed niereaktualizowany po rekalibracji stałej → audyt powtarza martwą wartość.

---

## E. KOHERENCJA ŹRÓDŁA-DANYCH (klasa I)

### R28 · schedule-data-3way-failopen-failclose `P1 OPEN SOURCE` (1 root, 3-way)
**Te SAME zepsute dane grafiku traktowane SPRZECZNIE w 3 miejscach: `is_on_shift` fail-OPEN (True 24/7, 4 ciche returny bez log.warning) vs `_shift_start_dt`/`_shift_end_dt` fail-CLOSE (None) vs feasibility FAIL12 (głośny open/close).** Jeden zły grafik → kurier jednocześnie „na zmianie 24/7" (selekcja) I „brak shift_start" (floor=None) I „FAIL12 alert" — niespójna decyzja per powierzchnia.
- instance_refs: `schedule_utils.py:401`, `courier_resolver.py:1252`, `feasibility_v2.py` (FAIL12)
- klasy: I,M · seed: K4/M-schedule-failopen-cichy
- consolidation_target: jedna polityka fail (open LUB close) dla zepsutego grafiku, spójna cross-warstwa; fail-loud (log.warning) zamiast cichego 24/7.
- why_recurs: 3 niezależne fallbacki tej samej awarii danych (path-asymmetry) — żaden nie wie o pozostałych.

---

## F. XREF / GRANICE (anti-double-count z innymi agentami Fazy E)
- **R1 one-selection-key / position-twin (8 bliźniaków):** allocation-agent. TU tylko instrument-manifestacje (R2 reassign-ghost/a2/best_effort, R3 frozen _lex_qual).
- **P0-A geometria-ślepa w lex_qual / P0-B global de-pile:** allocation-agent. TU instrument-cert (R5) + serializer-diag (R8 V328).
- **R4 one-earliest-pickup-floor (17 powierzchni):** pre-shift-agent. TU tylko konflikt frozen↔floor (R19) + clamp-precedence.
- **K5 sentinele-produkcja (0,0)/BIALYSTOK_CENTER/V328:** sentinel-agent. TU most: sentinele-JAKO-DANE wpadające do przyrządów (R1 objm~10000, R6 no_position 87%, R8 V328-diag-ginie).
- **G/K3 oś-kalibracji (poślizg odbioru):** calibration-agent. TU tylko instrument-blind (R8 G-2) + quantile-loosen-konflikt (R16).
- **Czysty K-deadcode (skeleton C4/C6/C7, R7 long-haul 99km, r6_legacy):** lifecycle-agent. TU tylko dead-but-ON flagi jako flag-drift (R14) + dead-producer shadow (R12).
- **R2 placement / wrong-layer (C class, B20):** placement-agent. TU tylko koherencja-aspekt feas-first-guard (R24).
