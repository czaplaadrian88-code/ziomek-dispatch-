# HANDOFF → sesja tmux 11: kontynuuj naprawy audytu Ziomka (sprint WIELO-AGENTOWY)

**Od:** sesja tmux 9 (02.07, dzień pracy: FALA-1 + L4 + L3 + D3-A/B scalone i wdrożone, okno deployowe wykonane, flipy L3/L4 zaplanowane) · **Data:** 2026-07-02 ~12:15 UTC · **Dla:** sesja tmux 11, która przejmuje jako driver napraw.

**Masz GO Adriana:** pracuj dalej nad backlogiem audytu, używaj agentów do kilku zadań naraz GDY BEZPIECZNE (rozłączne pliki), deploy/flip/restart silnika = ACK Adriana. Poniżej gotowy plan.

---

## 1. GDZIE JESTEŚMY (przeczytaj to najpierw — 5 min)
**ŹRÓDŁO PRAWDY O POSTĘPIE = `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` (żywy tracker; góra = najnowszy stan + kalendarz + zaplanowane at-joby). Zawsze zaczynaj od niego + `git log --oneline -15` + `tmux ls`.**

Skrót: audyty 1.0 (spójność) + 2.0 (niezawodność/jakość/skala) ZAMKNIĘTE. **Faza 3 (naprawy) ~2/3 zrobiona.**
- **LIVE / scalone (flagi OFF, bajt-w-bajt):** L0 · L1.1 · L1.2 · L2.1 · L6.A · **L3 (`7201ed8`)** · **L4 (`27d8ef8`)**.
- **WDROŻONE na żywo 02.07 ~11:45 (GO Adriana):** watchdog-close (10 drop-inów, OnCalendar/OnFailure, cron_health) · gastro_assign→ZoneInfo (bomba TZ #1) · D3 fale A+B (17 flag env→flags.json + env-cleanup) · restart shadow+pw · L2.2 aktywna w procesie.
- **Baseline regresji (kanon, HEAD `e623fe9`): `3907 passed / 0 failed / 23 skipped / 11 xfailed`** (~98 s). To Twój punkt odniesienia — każdy agent porównuje do niego.

**READ ORDER:** (1) ten plik; (2) tracker `ZIOMEK_STAN_AUDYTY_1i2.md`; (3) `memory/ziomek-change-protocol.md` — **WKLEJ PROMPT + C12 (multi-agent) + C13 (mutation-test strażników)**; (4) `AUDYT2/MASTER_synteza.md` (findingi 2.0) + `ZIOMEK_FINDINGS_LEDGER.md`; (5) `2026-06-30/FAZA1_05_roadmapa_poc.md` (detal fal L).

---

## 2. ZASADA SPRINTU WIELO-AGENTOWEGO (C12 — sprawdzona dziś 5×)
Równoległy jest KOD+TESTY+REPLAY; DEPLOY (merge→flip/restart) zawsze SERYJNY za ACK. Mechanika (obowiązkowa):
1. **Partycja po ROZŁĄCZNYCH plikach** — dwóch agentów NIGDY na tym samym pliku (koordynator sprawdza pusty przekrój PRZED startem).
2. **Worktree per agent:** `git worktree add /root/.openclaw/workspace/wt-<lane> -b fix/<lane>` (HEAD `e623fe9`). Agent pracuje TYLKO tam.
3. **Agent produkuje, NIE deployuje:** kod + testy (C13 behawioralne + mutation ×2) + py_compile + pełna regresja vs baseline (overlay — conftest pinuje kanon, wzór `tests/test_cod_weekly_missing_block.py` po `075dfe3`). ZERO flipów/restartów/push.
4. **Bramka scalenia (Ty, seryjnie):** review diff → merge → py_compile → **pełna regresja z KANONU po `git worktree remove`** (C12(e): hardcode ścieżki worktree = bomba) → tracker+ledger. Flip/restart = ACK Adriana.
5. ⚠ **Ratchet-strażnik łapie cross-lane PO merge (C12(f))** — dziś 3× (TZ-offset w perf, flaga-coverage GC, doc-coverage). Fail ratcheta po merge = fix U ŹRÓDŁA w złapanym pliku, nie poszerzaj allowlisty.

**Baseline (ETAP 0):** `cd /root/.openclaw/workspace/scripts/dispatch_v2 && nice -n 15 /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q -p no:cacheprovider 2>&1 | tail -2` (pytest BEZ `--timeout` — plugin nie zainstalowany).

---

## 3. FALA RÓWNOLEGŁA (🟢 PARALLEL-SAFE — 4 pasy, pliki rozłączne ZWERYFIKOWANE, gotowe do fan-outu)
Wszystkie POZA rdzeniem silnika (feasibility_v2/dispatch_pipeline/plan_recheck/courier_resolver/route_simulator_v2/scoring/state_machine — TYCH nie ruszać, to SERIAL §4). Adresują największe otwarte motywy audytu 2.0.

| Lane | Pliki (rozłączne) | Zadanie | Finding 2.0 |
|---|---|---|---|
| **alerty-danowe** | NOWY `observability/data_alerts.py` + `deploy_staging/` systemd + `tests/test_data_alerts.py` | monitor DANOWY (nie procesowy): sentinel-rate w shadow_decisions, pusta pula feasible, stale-grafik (fetch>Xh), stale-pozycje GPS, ledger-stall (brak nowych decyzji Xmin). Edge-triggered, flaga OFF, log+telegram za flagą. Czyta ledger kanonem `ledger_io`/`_rotated_logs`. | Motyw #1 „alerty procesowe nie danowe, pokrycie ciszy ≈0%" (2.B) |
| **cron-health-truth** | `observability/cron_health.py` (+ jego testy) | koniec false-positive „failed": czytaj systemd `is-active`/`show -p Result` zamiast ufać failure-only ledgerowi; oneshoty sukcesu → `record_run_success` (ExecStopPost już jest z watchdog-close — potwierdź komplet). Behawioralne testy + mutation. | Motyw #2 „meta-strażnicy sami kłamią; cron_health znaczy 3 zdrowe jako failed" |
| **feasibility-guard-teatr** | `tests/test_verdict_gate_guards.py` + NOWY `tests/test_feasibility_guards_behavioral.py` + `tools/guard_mutation_probe.py` (harness) — **BEZ dotykania feasibility_v2.py** | ODSŁOŃ teatr: verdict-gate sprawdza OBECNOŚĆ tokenu nie POLARYTET (mutacja `not` przechodzi), bag-cap off-by-one przeżywa. Napisz BEHAWIORALNE testy (wywołaj `check_feasibility_v2` realnym workiem → asercja werdyktu) + mutation-probe pokazujący które guardy są teatralne. Deliverable = czerwone/xfail testy MARKUJĄCE luki + raport „które guardy wymagają fix U ŹRÓDŁA" (sam fix = SERIAL §4, osobno). | Motyw #6 „strażnicy feasibility cienkie/teatr" |
| **multi-city-recon** | NOWY `eod_drafts/2026-07-02/MULTICITY_plan.md` + NOWY `config/cities.json` (szkielet) + read-only inwentarz | zmapuj ~37 plików z hardcode Białystok + bbox-walidator-prawdy + OSRM jednoregionowy; zaprojektuj `cities.json` (city_id 1. klasy) + plan migracji PRZED 2. miastem/Restimo. ZERO zmian zachowania — inwentarz+szkielet+doc. | Motyw #8 „skala niezaadresowana, ~146 hardcode, brak city_id" |

**Sugerowana komenda:** dla każdego pasu agent z `isolation:'worktree'`, prompt = „napraw {lane} wg HANDOFF §3 + protokół #0 (WKLEJ) + C12/C13, STOP przed flipem/restartem, zwróć branch+dowód+raport `eod_drafts/2026-07-02/<lane>_raport.md`". Recon (ETAP 0) możesz zrobić 1× wspólnie przed fan-outem.

**NIE w tej fali:** rdzeń silnika (§4), security remediacja (Adrian-driven, krok 0 = Hetzner Cloud FW), events.db execution (ACK), L8 dead-code (wymaga mapy — osobny recon).

---

## 4. PO FALI RÓWNOLEGŁEJ: rdzeń silnika (🟡 SERIAL+ACK, jeden właściciel/fala, ETAP 0→7, off-peak)
Kolejność zatwierdzona (`FAZA1_05_roadmapa_poc.md` + bramki):
- **L6.D objm/frozen-lex** — checkpoint **at-200 (pt 03.07 18:10 UTC)** = odczyt werdyktu + decyzja (NIE „przy okazji"); [[top10-progressive-potential-2026-06-29]] #6.
- **L5 ETA load-aware (F4, ⛔HARD)** — bramka **pickup-slip-review sob 04.07 07:00 UTC**. ⚠ UWAGA: deep-dive #9 mówi „kalibracja odbioru miarodajna dopiero ~10.07 (≥7 dni po deployach 02.07)" → L5 prawdopodobnie CZEKA na dane; najpierw odczytaj monitor, potem decyzja czy budować teraz czy po 10.07. Kotwica `assign` (83%), NIE `last` (4%).
- **L0.1** rejestr-flag/fingerprint completion (domknięcie L0).
- **L7** hardening (R-declared tripwire, 1 chokepoint clampów, concurrency) — **L7.5 fcntl na `pending_proposals` (3-writer) MUSI być PRZED re-enable Telegrama (C2)**.
- **L8** sprzątanie (dead-code/cache/threshold — po recon mapie).

Każda: prosty polski „co/wpływ/jak bezpiecznie" → ETAP 0 recon (linie dryfują) → kod+testy+replay-dowód → **FLIP tylko za ACK Adriana**, off-peak.

---

## 5. ZAPLANOWANE AUTO-FLIPY L3/L4 — NIE RUSZAĆ RĘCZNIE, odczytać werdykty (Adrian 02.07 „konkretny termin")
Trwałe `at` z bramką (tool `tools/scheduled_flip_gate.py`; flagi HOT=bez restartu; bramka py_compile+testy+off-peak+strażnik+shadow-żywy; fail→HOLD+telegram):
- **at-202** So 04.07 12:35 UTC → `ENABLE_PLAN_RECHECK_GATES=true` + `ENABLE_COURIER_PLANS_GC=true` (dry)
- **at-203** So 04.07 12:50 UTC → `ENABLE_AVAILABLE_FROM_SINGLE_SOURCE=true`
- **at-204** So 04.07 14:30 UTC → verify L3+L4
- **at-205** Pn 06.07 12:40 UTC → `PLAN_GC_DRY_RUN=false` (GC realny, gated świeżym dry-run)
- **at-206** Pn 06.07 14:30 UTC → verify GC
Po odpaleniu: odczytaj `dispatch_state/scheduled_flips_atjob.log` + `scheduled_flips.jsonl` → wpisz werdykt do [[l4-available-from]]/[[l3-plan-recheck]] + tracker. Rollback: flaga→poprzednia w flags.json (hot). Odwołanie: `atrm <nr>`. Rejestr: [[shadow-jobs-registry]] „AUTO-FLIPY".
**Inne odpalające się (odczyt): at-200 (03.07 18:10 objm) · at-201 (03.07 19:00 werdykt L2.1 sentinel).**

---

## 6. KOLEJKA DEPLOY-ZA-ACK (gotowe, czekają na Adriana — NIE wykonuj sam)
backfill 4 tygodni COD (18-24.05/01-07.06/08-14.06/22-28.06; `FALA1_codweekly_raport.md` §4; 15-21.06 NIE ruszać — pieniądze!) · timer log-rotation + 1. `--apply` (~174 MB) · flip `ENABLE_PERF_SLO_ALERT` (po log-only) · flip `ENABLE_V328_POISON_ALERT` (hot) · events.db kroki A-D (~10.07) · fale C/D migracji flag (pod-ACK — OPEN-1: unifikacja = zmiana zachowania pw) · **security krok 0 = Adrian (Hetzner Cloud FW)** → potem quick-wins.

## 7. MINY (nie potknij się)
- ⛔ **NIE flipuj `PENDING_RESWEEP_LIVE`** (`global_allocate` geometria VOID).
- **C2 re-enable Telegrama** dopiero po L7.5 fcntl + naprawie klastra postpone.
- **Multi-sesja (C1):** `tmux ls` + `git log -15` PRZED każdą zmianą; cudze `.bak`/worktree — nie ruszaj. Dziś na masterze: fale L3/L4/D3 + tool flip + docsy innych sesji (deep-dive #9, proposal-churn).
- **gastro_assign.py jest ŻYWY z ZoneInfo** (poza repo, `/root/.openclaw/workspace/scripts/`), backup `.bak-pre-tz-zoneinfo-2026-07-02`.
- **17 flag D3 = KANON flags.json** (env martwy) — flip którejkolwiek = hot, bez restartu.

## 8. PO KAŻDEJ FALI (DoD)
tracker `ZIOMEK_STAN_AUDYTY_1i2.md` (§2/§3 + „Ostatnia aktualizacja") → `ZIOMEK_FINDINGS_LEDGER.md` → `entropy_dashboard.py` re-run (metryki MALEJĄ) → relay memory ([[ziomek-audyt-2-wyniki-2026-07-02]] / [[ziomek-unified-audit-2026-06-30]]). Sesja bez update trackera = niezakończona.

## 9. NA KONIEC — prosta lista dla Adriana
Po każdej fali/decyzji: krótko PROSTYM JĘZYKIEM per pozycja **CO / WPŁYW / JAK BEZPIECZNIE (rollback)** — czekaj na GO per pozycja, nic nie flipuj/restartuj bez ACK ([[feedback-explain-before-work-plain-language]]).
