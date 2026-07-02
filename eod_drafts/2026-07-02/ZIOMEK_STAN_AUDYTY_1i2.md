# ZIOMEK — STAN OBU AUDYTÓW: gdzie jesteśmy + co zostało (widok zunifikowany)

> 🟢 **ŻYWY TRACKER (Adrian 02.07): to jest źródło prawdy o postępie napraw obu audytów. AKTUALIZUJ PO KAŻDEJ FALI.**
> Protokół aktualizacji (część DoD fali): po zamknięciu fali/naprawy → (1) zmień jej status w §2 (fale L) lub §3 (findingi 2.0) na ✅/🟡/🔴 + commit/flaga/data; (2) przenieś pozycję z §4 „co zostało" jeśli domknięta; (3) bumpnij `Ostatnia aktualizacja` niżej + 1-linijka „co się zmieniło"; (4) jeśli finding 2.0 zamknęła fala L (jak L1.2→rotation-aware) — odnotuj krzyżowo w §3. Nie kasuj historii — dopisuj. Sesja, która zamknęła falę ale nie ruszyła trackera = NIEZAKOŃCZONA.
>
> **Ostatnia aktualizacja:** 2026-07-02 ~11:55 UTC (tmux 9: **OKNO DEPLOYOWE WYKONANE za GO Adriana (1/2/4/6)** — [6] żywy gastro_assign.py→ZoneInfo (bomba TZ #1 rozbrojona, backup .bak-pre-tz-zoneinfo); [2] 10 drop-inów watchdog zainstalowane + daemon-reload: 3 timery zakotwiczone OnCalendar (NEXT 11:50/12:00/12:00:56, zero zgubionych harmonogramów vs snapshot), OnFailure+ExecStopPost na cod-weekly, cron_health sync 6 progów + 3 false-failed wyczyszczone, dry-run checked=15/would_alert_stale=0/no_threshold=0; [4] D3 fale A+B: merge `3e5e6fb`, 17 kluczy=true w flags.json (backup .bak-pre-d3-ab), 38 tokenów env usuniętych z 3 serwisów (C-flagi+PANEL_BG_REFRESH+USE_V2_PARSER zostały), doc 11 flag `5a018fb`; [1] restart dispatch-shadow 11:45 czysty (FLAG_FINGERPRINT: 17×D3=1, nowe fale=0, AUTO_ASSIGN=0) + restart pw 11:46 (dowód behawioralny migracji: REDECIDE_ON_PICKUP z flags.json; plan-recheck tick 11:47 czysty). L2.2 aktywna w procesie; grep `v328_fail_causes` = pasywnie po 1. świeżej decyzji. Regresja przed oknem: **3907/0**. Decyzje #3/#5/#7/#8 = todo_master „DO OBGADANIA").
> Poprzednia: 2026-07-02 ~08:15 UTC (tmux 9: **FALA-1 PARALLEL-SAFE SCALONA** — 5 lane'ów zmergowanych seryjnie do master: perf-SLO `e9551f1` → TZ `872667f` → integracja `2e68a11` → cod-weekly `46e4867` → gc `a3ecf2f` → watchdog `aab1e17` → integracja `075dfe3`; regresja finalna zielona; ratchet TZ złapał cross-lane fixed-offset w perf (naprawione u źródła); worktree'y usunięte, gałęzie `fix/*` zostają; **wykonania na żywym systemie = lista DEPLOY-ZA-ACK** w raportach `FALA1_*_raport.md`, statusy niżej w §3).
> Poprzednia: 2026-07-02 ~06:45 UTC (tmux 9: FALA-1 PARALLEL-SAFE W TOKU — 5 lane'ów w worktree `wt-{tz,watchdog,perf,gc,cod}` branch `fix/*`: TZ-consolidate + watchdog-close + perf-SLO + gc-observability + cod-weekly-diag; baseline regresji 3709/0 zapisany; merge SERYJNY po zakończeniu agentów; NIE ruszać tych plików/worktree z innych sesji).
> Poprzednia: 2026-07-02 ~02:30 UTC (utworzenie: stan po L1.1/L1.2/L2.1/L6.A/L0.2 LIVE; audyt 2.0 zamknięty; re-enable 3 monitorów).

**Data snapshotu:** 2026-07-02 ~02:30 UTC · **Źródło stanu:** git log (ground-truth) + flags.json na żywo + master-syntezy obu audytów. ⚠ **Multi-sesja:** ≥2 sesje pchają Fazę 3 tej nocy — stan DRYFUJE, każda zmiana re-grepuje git.

> **📅 FLIPY L3/L4 MAJĄ KONKRETNE TERMINY (Adrian 02.07 „żeby nie czekało") — trwałe zadania `at` z BRAMKĄ (nie flipują na ślepo): tool `tools/scheduled_flip_gate.py` (flagi HOT=bez restartu; bramka=py_compile+testy+off-peak+strażnik+shadow-żywy; GC-real dodatkowo świeży dry-run zachowuje aktywne; fail→HOLD+telegram; log `dispatch_state/scheduled_flips_atjob.log`+`scheduled_flips.jsonl`). Kolejka: `at 202` So 04.07 12:35 UTC = ENABLE_PLAN_RECHECK_GATES→true + ENABLE_COURIER_PLANS_GC→true(dry) · `at 203` 04.07 12:50 = ENABLE_AVAILABLE_FROM_SINGLE_SOURCE→true · `at 204` 04.07 14:30 = verify L3+L4 · `at 205` Pn 06.07 12:40 = PLAN_GC_DRY_RUN→false (GC realny, gated) · `at 206` 06.07 14:30 = verify GC. Rollback dowolnego: flaga→poprzednia w flags.json (hot). Odwołanie terminu: `atrm <nr>`.**

> 🧭 **PO ZAKOŃCZENIU WSZYSTKICH NAPRAW OBU AUDYTÓW (decyzja Adriana 02.07): następny etap pracy = FALE A–D roadmapy deep-dive → `eod_drafts/2026-07-02/ROADMAPA_PO_NAPRAWACH_DEEPDIVE.md`** (Fala A: kalibracja czasów wg mapy `tools/eta_truth_map.py` — NAJPIERW odśwież mapę na ≥7-dniowym oknie po deployach 02.07 ~11:45, czyli od ~10.07; Fala B: histereza propozycji, baseline = timer `dispatch-proposal-churn` ZAINSTALOWANY 02.07, log `scripts/logs/proposal_churn.log`; Fala C: delay-dispatch par — wymaga werdyktu zasady od Adriana; Fala D: error-budget+dashboard; C3 globalna selekcja SKREŚLONA pomiarem 0b). Sesja, która zamknie ostatnią naprawę, ma wskazać Adrianowi tę roadmapę jako następny krok.

**Mapa dokumentów (co jest czym):**
- **Ten plik** = „na jakim etapie jesteśmy + co zostało" (oba audyty razem).
- `AUDYT2/MASTER_synteza.md` = szczegół audytu 2.0 (niezawodność/jakość/skala/security).
- `ZIOMEK_FINDINGS_LEDGER.md` = JEDEN rejestr wszystkich findingów (27.06+30.06+05.07+02.07) ze statusem/właścicielem.
- `2026-06-30/FAZA1_00..06` = szczegół audytu 1.0 (spójność). `ZIOMEK_ARCHITECTURE/INVARIANTS/DEFINITION_OF_DONE.md` = kanon docelowy.

---

## 1. GDZIE JESTEŚMY (jednym rzutem)

| Audyt | Co bada | Status |
|---|---|---|
| **1.0 spójność** (30.06) | czy Ziomek jest SPÓJNY, czy przyrządy mówią prawdę | ✅ Faza 1 (audyt) + Faza 2 (8 kontraktów ZATWIERDZONE, szkielet w git `76daf25`) DONE. **Faza 3 (naprawy) W TOKU** — ~połowa fal LIVE (§2). |
| **2.0 niezawodność/jakość/skala** (02.07) | czy decyzje są DOBRE, czy przeżyje AWARIE/CZAS/WZROST, + security | ✅ Audyt DONE (14 pasów). Findingi mają właścicieli; naprawy = mini-sprinty (§3-4). |

**Jednozdaniowo:** silnik decyzji zdrowy i aktywnie utwardzany (Faza 3 leci); realne żywe ryzyka są POZA rdzeniem — **security P0** (nowe, 2.0), regres wydajności, 2 bomby TZ (25.10), higiena obserwowalności. Autonomia = OFF (`ENABLE_AUTO_ASSIGN=False`, bezpiecznie).

---

## 2. FAZA 3 audytu 1.0 — fale naprawcze L0-L8 (status ze świeżego git 02.07)

| Fala | Co konsoliduje | Status (git/flags) |
|---|---|---|
| **L0** fundament wiarygodności (rejestr-flag, strażniki, env-parytet) | F6 | 🟡 CZĘŚCIOWO — **L0.2 parytet env carried-first-guard DONE** (`131b555`, de-void); L0.1 rejestr-flag/fingerprint = pending |
| **L1.1** serializer-kompletność | F6 | ✅ **LIVE 01.07** (`85d92f7`; `_METRICS_EXCLUDE` deny-lista, 38 kluczy/14 HARD) |
| **L1.2** prawda przyrządów (rotation-aware + żywy sla_log) | F6 | ✅ **DONE tej nocy** (`fec417e`/`3ba0fdc`/`97f27e9`/`da2fa9b`/`e8a95d2`) — werdykt-toole na `ledger_io`, `b_route real_joined 0→322`, 15+9 tooli rotation-aware |
| **L2.1** sentinel-ingest (most K5) | F3 | ✅ **LIVE 01.07 ~21:29** (`ENABLE_COORD_SENTINEL_INGEST_GUARD=True` potwierdzone; werdykt at-201 03.07) |
| **L2.2/L2.3** catch-all rozróżnia data_poison/real_bug + głośny fail-open grafiku | F3 | 🟡 **BUILD-ONLY, flaga OFF** (`f8ae4ce`) — czeka na flip |
| **L6.A** route-order golden (parytet konsola==kanon) | F5 | ✅ **DONE 01.07** (`tests/golden/route_order_corpus.json`, 13/13; zastępuje wygasający monitor) |
| **L3** plan_recheck nie-cofa (GC + pure-read + regen przez te same bramki) | F2 | 🟡 **SCALONA 02.07 ~10:20 (merge `7201ed8`, FLAGI OFF = bajt-w-bajt mimo hot-oneshota).** Bramka A `ENABLE_PLAN_RECHECK_GATES`: compare-and-keep na **R6 carried-age** (jedyny sekwencyjno-czuły HARD live; spread R1/R5=SOFT→metryka, ETAP-2 uszanowany) przy ZAPISIE regenu; NO_BASELINE (bag-change)→save. GC B `ENABLE_COURIER_PLANS_GC` (+`PLAN_GC_DRY_RUN` default True, MAX_AGE 48h): terminal-prune + zombie wyłącznie przez plan_manager API pod fcntl; **dry-run na kopii żywego pliku: 48 wpisów → 26 age-removed + 4 no-active + 6 aktywnych KEPT + 0 terminal**. ⭐ twin(recanon)→0 JUŻ spełnione (P-5 `0426706`: 4 handlery prune-before-recanon) → pw BEZ restartu; read-side-effect→0 potwierdzone (2 callery za PURE_READ=True, reszta pure z konstrukcji). 21 testów+mutation×2, golden 13/13 nietknięte, ETAP-0 fakty: zombie żywo 47-48; mina PURE_READ już rozbrojona. **FLIP za ACK, sekwencja hot** (oneshot łapie flags.json ≤5 min): A→false→obserwacja→true (2 dni `l3_regen_*`); B→true z DRY_RUN=true→przegląd GC_*→DRY_RUN=false. ⚠ przy wpisie do flags.json dopisać doc w ZIOMEK_LOGIC_REFERENCE.md (test_flag_doc_coverage) |
| **L4** dostępność 1 źródło `available_from=max(now,shift_start)` | F1 | 🟡 **SCALONA 02.07 ~10:00, FLAGA OFF** (merge po review tmux 9; GO Adriana na falę) — źródło `courier_resolver.resolve_available_from*` + konsumenci #1 (kandydat, domknięta luka no_gps) / #3 (feasibility; route_simulator=N-D generyczny konsument) / **#5 plan_recheck anchor-floor (leak!)** + chokepoint `effective_pickup_at` OBOK deklaracji (Q2/R27 nietykalna) + strażnik pickup_floor_guard NIE-ŚLEPY (resolucja shift_start kanonem; dzisiejsze 4 unknown = STALE plany 04-22..06-29 → dług ⑦ plan-GC, osobny). 25 testów, mutation ×2, parytet bliźniaków z konstrukcji; replay 14d/3538: pre_shift już sclampowany starą flagą → **wpływ strukturalny** (jedno źródło + leak #5 poza shadow + prewencja min flag), zmiana zwycięzcy=0, pula nietknięta. **FLIP = osobny ACK Adriana off-peak**: wpis flagi false→flags.json → restart shadow+plan-recheck (+panel-watcher dla chokepointu) → 2 dni OFF → flip true → grep-c `af_clamp_applied` + `L4_ANCHOR_FLOOR`. Zostawione: pas renderów (L3), Q2 feasibility „nie zdąży→nie dostaje" (na F4/L5), stale-plan GC |
| **L5** ETA load-aware (kalibracja na osi poślizgu odbioru) | F4 | 🔴 PENDING ⛔HARD — **bramka 04.07** |
| **L6.B/C/D** O2/geometria-de-pile/objm-frozen-lex | F5 | 🔴 PENDING — **bramki 02.07 (O2) / 03.07 (objm)** |
| **L7** hardening/koherencja (R-declared tripwire, 1 chokepoint clampów, concurrency) | F7 | 🔴 PENDING (L7.5 fcntl pending PRZED re-enable Telegrama) |
| **L8** sprzątanie (dead-code, cache, threshold) | cleanup | 🔴 PENDING |

**Zrobione z Fazy 3:** L1.1 · L1.2 · L2.1 · L6.A · L0.2 (+L2.2/L2.3 build-only). **Zostało:** L0.1 · L3 · L4 · L5 · L6.B/C/D · L7 · L8. Najgłębsze wciąż przed nami = **L4 (available_from) + L5 (ETA) + strażniki L0**.

---

## 3. FINDINGI 2.0 — status + czy już zamykane przez Fazę 3

| Finding 2.0 | Sev | Właściciel / status |
|---|---|---|
| **Security P0** (firewall host OFF, `/stop` bez auth, CDP :9222, wyciek hasła/tokenów) | **P0** | 🆕 NOWY PION — brak właściciela; krok 0 = potwierdź Hetzner Cloud FW; remediacja = osobny sprint pod kierunkiem Adriana |
| **Regres wydajności 2×** (p50 840ms; człony compute-zawsze) | P1 | 🟡 FALA-1 02.07: POMIAR+SLO scalone (`e9551f1`: `tools/perf_budget_report.py` + sekcja SLO w canary za flagą `ENABLE_PERF_SLO_ALERT` OFF, bajt-parytet → at-200 bezpieczny; baseline 02.07: p50 852 / p95 1939 / p99 2720 ms, ogon>1500=13,1%, 8 breachy §5a). ZOSTAJE: fix compute-zawsze (osobna fala, rdzeń) + flip alertu po okresie log-only (ACK) |
| **2 bomby TZ** (`gastro_assign:11` + `shadow_outcome_enricher:45`, +klaster) | P1 (od 25.10) | 🟡 FALA-1 02.07: 6 narzędzi repo → ZoneInfo (`872667f`, w tym 2. bliźniak w sequential_replay) + **grep-ratchet test** (od razu złapał cross-lane fallback +1 w perf → `2e68a11`); kill-test zimowy: fix 15 min vs bomba 1395 min. ZOSTAJE ZA ACK: **podmiana żywego `gastro_assign.py`** (staged `deploy_staging/scripts/`, diff = tylko l.11-12, subprocess per call → zero restartu) + `drive_speed_overshoot_verdict.py:29` (w allowliście ratcheta) — **przed 25-26.10** |
| **Blokery autonomii** (fałszywy-sukces exit-code + 1.flip-nie-no-op) | P1 (przed ON) | 🆕 przed 1. flipem AUTON — RAZEM (F+G+TOCTOU) |
| **Martwe monitory** (watchdog+2) | P1 | ✅ RE-ENABLE DONE (ACK) · 🟡 FALA-1 02.07: domknięcie PRZYGOTOWANE (`aab1e17`: rejestr progów w `cron_health.py` — cod-weekly 192h + 6 wpisów thr=None + CLI `--sync-thresholds`/`--record-success`/`--dry-run`; **10 drop-inów staged** `deploy_staging/etc/`: OnCalendar×3 [Persistent przy samym OnUnitActiveSec był NO-OPem!], OnFailure cod-weekly, ExecStartPost×3; burst-check 3→0). ZOSTAJE ZA ACK: `cp` drop-inów + `daemon-reload` + sync (sekwencja w `FALA1_watchdog_raport.md`) |
| **`cod-weekly` FAILED+silent** | P2 live | 🟡 FALA-1 02.07: hipoteza audytu POTWIERDZONA (brak bloku tygodnia w arkuszu; pada CO pn 08/15/22/29.06) + fix u źródła scalony (`46e4867`: aktionable błąd zamiast gołego fail + auto-create za flagą `COD_WEEKLY_AUTOCREATE_BLOCK` OFF + DRY_RUN; exit≠0 zostaje pod OnFailure). **PRZEPADŁE 4 tygodnie do backfillu: 18-24.05, 01-07.06, 08-14.06, 22-28.06** (15-21.06 był wypełniony — NIE ruszać); moduł umie `--week A:B --write`. ZOSTAJE ZA ACK Adriana: backfill (pieniądze!) + ewent. flip auto-create (najpierw DRY_RUN); OnFailure = drop-in z lane watchdog |
| **Alerty procesowe nie danowe / meta-strażnicy kłamią** | P1/P2 | częściowo → **L1.2 zamknęła część** (b_route live-sla, rotation-aware); reszta (danowe alerty) = 2.B |
| **Readerzy niespójnie rotation-aware** (L13) | P2 | ✅ **W DUŻEJ CZĘŚCI ZAMKNIĘTE tej nocy** (L1.2 T3/T3b: 15+9 tooli) — do potwierdzenia że komplet |
| **carried_first_guard VOID** (L09) | P2 | ✅ **ZAMKNIĘTE** (L0.2 env-parytet `131b555`) |
| **GC observability atrapa / events.db >90d ~10.07** | P2 | 🟡 FALA-1 02.07: `observability/log_rotation.py` scalony (`a3ecf2f`: dry-run default, DENYLIST ledgerów wygrywa, --max-delete; dry-run na żywo: 90/120 plików = 174 MB do zwolnienia; timer staged OnCalendar 03:00). ⭐ KOREKTA L13: retencja audit_log NIE jest widmem — `dispatch-event-bus-cleanup.timer` (90d) ŻYJE; **~10.07 = weryfikacja że delete odpali, nie klif**; realne luki: brak VACUUM + fałszywy komentarz logrotate l.130. ZOSTAJE ZA ACK: install+enable timera, 1. nadzorowany `--apply`, events.db kroki A-D (`FALA1_gc_eventsdb_plan.md`) |
| **Strażnicy feasibility cienkie/teatr** (verdict-gate polaryzacja) | P2 | 🆕 → dogęścić (L0/2.0 0.H) |
| **Mina flagi `ENABLE_LOAD_PLAN_PURE_READ`** (default False) | P2 | 🆕 → default True u callerów (powiązane z L3) |
| **pending 3-writer no-lock (O1) + klaster postpone** | P2 | → **L7.5** (przed re-enable Telegrama) |
| **Multi-city ~146 hardcode / brak city_id** | P2 skala | 🆕 → przed 2. miastem/Restimo (rejestr cities.json) |
| **`osrm-fallback-double-traffic`** | — | ✅ REFUTED — już naprawione 28.06 (był dziurą w rejestrze) |
| **grafik UTC vs Warsaw + literówka** | P2 | 🆕 → `today` z ZoneInfo |

---

## 4. CO ZOSTAŁO — backlog scalony (priorytet)

**A. Żywe/tanie (teraz):**
1. Security krok 0 (potwierdź Hetzner Cloud FW) → potem quick-wins (auth /stop, bind 9222/porty, .secrets→.gitignore+chmod, rotacja sekretów). ⬅ JEDYNA pozycja A nietknięta falą (Adrian-driven).
2. ~~Domknąć watchdog (OnCalendar) + dorejestrować cod-weekly (+ OnFailure) + diagnoza exit1~~ → 🟡 FALA-1: kod+staging+diagnoza GOTOWE (§3); zostało wykonanie deploy-za-ACK (cp drop-inów + daemon-reload + backfill COD).
3. ~~Bomby TZ → ZoneInfo~~ → 🟡 FALA-1: repo DONE + ratchet; zostało: podmiana żywego gastro_assign.py (ACK) + 1 plik z allowlisty — przed 25.10.
4. ~~Budżet wydajności + SLO + alert~~ → 🟡 FALA-1: zbudowane za flagą OFF; zostało: log-only → flip alertu (ACK). Fix samego regresu (compute-zawsze) = osobna fala rdzenia.

**B. Faza 3 pozostała (protokół ETAP 0→7 + ACK per fala):**
5. L4 available_from (najgłębsze) · L3 plan_recheck · L5 ETA load-aware (bramka 04.07) · L6.B/C/D (bramki 02-03.07) · L0.1 rejestr-flag · L7 (w tym L7.5 fcntl przed Telegramem) · L8 sprzątanie · flip L2.2/L2.3.

**C. Strukturalne (decyzja o ACK Pionów 2/3 z 2.0):**
6. Alerty danowe (2.B) · zasoby/GC/rotacja (2.D+L8) · governance systemd · security pełny · multi-city (przed skalą) · oś 05.07 (Postgres/DR/SLO — mierz KIEDY load-replay ×2/×5).

---

## 5. DATOWANY KALENDARZ (min czasowych)
- **02.07** bramka O2 (L6.B) — wyrównać kotwicę bundle_calib; bug4-gate WAIT z fałszywego powodu.
- **03.07** objm at-200 (L6.D).
- **04.07** load-aware ETA (L5) — **brać kotwicę `assign` 83%, NIE `last` 4%** (L07).
- **~10.07** events.db >90d (auto_vacuum=0) + wygasa monitor route-order (golden już zastąpił).
- **25-26.10** koniec DST → obie bomby TZ się uzbrajają.

---

## 6. UWAGA MULTI-SESJA (krytyczne dla następnej sesji)
Tej nocy ≥2 sesje pchają Fazę 3 (tmux 8 zrobił L1.2). **Przed KAŻDĄ zmianą: `git log --oneline -10` + `tmux ls` + sprawdź cudze `.bak-*`.** Część findingów 2.0 rozwiązuje się „w locie" przez fale L — przy podejmowaniu naprawy z §3-4 NAJPIERW re-grep czy już zamknięte (ETAP 0). Relay stanu: [[ziomek-audyt-2-wyniki-2026-07-02]] + [[ziomek-unified-audit-2026-06-30]] (Faza 3 relay).
