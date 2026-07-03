# HANDOFF → następne sesje: stan po dniu 02.07 (tmux 11) + co robić dalej

**Od:** tmux 11 (koordynator dnia 02.07: FALA-2 7 pasów + fala SERIAL S1/S2 + fala wieczorna A-D + sprint O2 cap-Z + deploye za GO Adriana) · **Data:** 2026-07-02 ~21:35 UTC.
**ŹRÓDŁO PRAWDY:** `ZIOMEK_STAN_AUDYTY_1i2.md` (tracker, góra) + `ZIOMEK_FINDINGS_LEDGER.md` §11-16. Zawsze zaczynaj od nich + `git log --oneline -15` + `tmux ls` + `atq`.

## 1. STAN NA KONIEC 02.07 (jednym rzutem)
- **Regresja kanonu: 4064/0/26sk/9xf/2xp** (start dnia 3907/0). HEAD `3fa89f6`+. 14 pasów scalonych.
- **ŻYWE od dziś:** data_alerts (log-only, timer 5 min) · grafik H1/H1b (today=Warsaw) · flip **S1** `ENABLE_SLA_ANCHOR_UNIFIED=1` (metryka `sla_anchor_source` w decyzjach — werdykt obs ~04.07) · flip **H2** `ENABLE_GRAFIK_ENTRY_SALVAGE=1` (weryfikacja live: 06.07 Parys, 08.07 Citko — grep `parse_degraded` w logu fetch) · **gastro_assign fail-closed + `--verify`** (deploy poz-1, .bak-pre-auton-blockers) · restart shadow 21:22 (executor hardening inert AUTO_ASSIGN=0; kanon locka L7.5 aktywny; sweeper na locku).
- **SCALONE NA CIEMNO (flagi OFF):** O2 cap-Z Krok1 `ENABLE_O2_CAPZ_RESEQ` + Krok2 `ENABLE_SLA_GATE_READY_ANCHOR` (ledger §15; flip dwuetapowy) · L3/L4 (auto-flipy at-202/203 sobota).
- **2 xpass w regresji** = L-TEATR non-strict, które zaczęły przechodzić — przy następnym dotknięciu tych testów przełączyć na `strict=True`.

## 2. KOLEJKA ACK (dla Adriana, w kolejności)
⭐ **UPDATE noc 03.07 ~02:00 (ledger §18-19):** pakiet nocny WYKONANY — flipy PERF_LAZY+V328_POISON LIVE (pomiar perf = rano przed peakiem!) · log-rotation 174MB+timer · COD backfill=NO-OP (wypełnione ręcznie) · telegram-delta+bug4-logger(schema-2, LIVE od ticku)+L0.1(fingerprint-check, rozjazdy 6→1)+L8-iter2(−345 LOC; sprint2=STOP żywy fixture TZ) SCALONE. 🚨 **NOWY P1-LIVE: `dispatch-czasowka` INTERMITTENTNIE (~22-40% ticków) liczy defaultami common.py zamiast flags.json** — osobny pas protokół+ACK PILNY. Nowe za-ACK: migracja USE_V2_PARSER do ETAP4 · fingerprint-check jako timer-strażnik · frozen-objektyw P0 · iter3 fixture-move.
0. **RANO: flip `ENABLE_PERF_LAZY_MEMBERS=true` + restart shadow + `perf_budget_report` 30 min przed peakiem** (ledger §17; p50 −22% na replayu, parytet 580/580; rollback hot). Osobno: decyzje samplingowe §7 raportu perf-lazy.
1. **Flip O2 Krok 1** `ENABLE_O2_CAPZ_RESEQ=true` — PO czystym werdykcie S1 (~04.07 po 2 dniach obs: grep `sla_anchor_source` + brak dryfu `sla_violations`), off-peak; przy flipie zmierz p95 latencji w shadow przed peakiem (reseq bounded MAX_STOPS=8). Rollback hot.
2. **Flip O2 Krok 2** `ENABLE_SLA_GATE_READY_ANCHOR` — OSOBNO, prereq: przegląd downstream `_kind()` (48% reason-churn) + real-shadow replay. NIE bundlować z K1.
3. **Logger bug4** (plan_recheck `_bug4_reseq_shadow`: frozen/fresh total_duration+sla) + **re-collect λ=0** (instrukcja w `o2-capz_raport.md` §5) — mały pas rdzenia, podnosi bug4+O2 na ground-truth.
4. **Telegram-delta** (per-op locked_mutate zamiast blind-overwrite dict) — OBOWIĄZKOWO przed re-enable Telegrama (C2); gotowy diff w `pending-fcntl_raport.md`.
5. Drobne flipy: `DATA_ALERTS_TELEGRAM` (po 1-2 dniach czystego `data_alerts.log`) · `ENABLE_PERF_SLO_ALERT` (po log-only) · `ENABLE_V328_POISON_ALERT` · log-rotation `--apply` (174 MB) · events.db kroki A-D (~10.07).
6. **Backfill 4 tyg COD** (pieniądze!) · **security krok 0 = Adrian** (Hetzner Cloud FW → potem quick-wins auth /stop, bind 9222, .secrets, rotacja).
7. Przed 1. ON autonomii (poza flipami wyżej): kontrolowane E2E gastro_assign na żywym panelu + monitor/stop-loss auto-assign (NIE ISTNIEJE — do zbudowania) + 1. wykonanie MAX_PER_HOUR=1 z Adrianem.

## 3. KALENDARZ (samo się odpala — TYLKO ODCZYTY werdyktów, nie ruszać)
- **03.07:** at-200 18:10 (objm L6.D) · at-201 19:00 (werdykt L2.1 sentinel).
- **04.07:** at-202 12:35 (flip L3) · at-203 12:50 (flip L4) · at-204 14:30 (verify) · **werdykt S1** (→ odblokowuje ACK poz.1) · bramka L5 pickup-slip 07:00.
- **06.07:** at-205 12:40 (GC real) · at-206 14:30 (verify) · **żywa weryfikacja H2** (Parys w grafiku, grep `parse_degraded`).
- **~09-10.07:** events.db >90d · **refresh mapy 0a (eta_truth_map) na ≥7-dniowym oknie → BRAMKA startu Fali A roadmapy**.
- Werdykty wpisywać do notatek tematycznych + tracker (sesja bez update = niezakończona).

## 4. ROADMAPA POAUDYTOWA (deep-dive, `ROADMAPA_PO_NAPRAWACH_DEEPDIVE.md`) — STATUS BRAMEK 02.07 wieczór
- **KROK 0 = WYKONANY** (0a mapa czasów: odbiór med −3,6 opt., dostawa bias≈0/rozrzut±17; 0b LAP: C3 SKREŚLONE; 0c churn 83,7%, timer baseline LIVE).
- **Fala A (kalibracja czasów): NIE STARTOWAĆ przed ~09-10.07** — bramka = świeża mapa 0a na ≥7 dniach PO deployach 02.07 (restart silnika 21:22 = początek czystego okna). Start wcześniej = łamanie zasady „nic na starych liczbach".
- **Fala B (histereza): po A** — baseline churn zbiera się timerem od 02.07; przy starcie uwzględnić rozkład: ~41-43% czysty flicker (cel), ~36% pool_shrank (NIE ruszać), 14,1% churn pozycyjny pos_source (osobny wątek, styk K5/L2).
- **Fala C (delay-dispatch par): czeka na WERDYKT ZASADY od Adriana** (decyzja biznesowa, nie kod).
- **Fala D (error-budget+dashboard): technicznie bez bramki danych** — można budować read-only/za flagą, ale formalnie roadmapa startuje PO zakończeniu napraw audytów (zostały: L5, L6.C, L0.1, L7-reszta, L8-iter2, perf compute-zawsze, security P0).
- **Werdykt: poaudytowy upgrade JESZCZE NIE** — najbliższy realny start = Fala A po bramce ~10.07; do tego czasu priorytet = dokończyć naprawy (⭐ UPDATE 22:40: perf compute-zawsze ZROBIONE — scalone OFF, ledger §17, flip za ACK rano [flaga+restart+pomiar przed peakiem]; kolejność dalszych: L0.1 → L6.C → L7-reszta → L8-iter2).

## 5. MINY / LEKCJE DNIA (dla następnych fal wieloagentowych)
- C12: agent o2-capz zaczął edycje w KANONIE (czytał kanon-ścieżki) — do promptów fal dodawać samokontrolę `git -C <kanon> status` w DoD agenta. Koordynator: merge ZAWSZE z kanonu (2× dziś odruch `cd worktree && git merge` = "Already up to date" bez merge!).
- Ratchety baseline'ów flag (doc/effect) po każdej fali z nowymi flagami/testami → kurczyć baseline, nie dopisywać.
- Regresja z worktree ma ~23 stałe artefakty path-layout (a2_selection+courier_reliability) — porównywać wewnątrz-worktree; finalna prawda ZAWSZE z kanonu po `worktree remove`.
- Przyrządy: 4× dziś kalibracja oracle zmieniła werdykt (O2 review, replay H2 zła kolumna, bug4 zła oś, entropy-heur) — C9 przed każdą liczbą do decyzji.
