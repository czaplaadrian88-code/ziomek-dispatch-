# HANDOFF → sesja tmux 9: kontynuuj naprawy Ziomka (sprint WIELO-AGENTOWY bez kolizji)

**Od:** sesja główna (audyt 2.0 + tracker) · **Data:** 2026-07-02 ~02:35 UTC · **Dla:** sesja w tmux 9, która przejmuje naprawy.

---

## 1. GDZIE JESTEŚMY (przeczytaj to najpierw)

**Źródło prawdy o postępie = `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` (ŻYWY TRACKER — aktualizuj po każdej fali).**

Skrót: audyt 1.0 (spójność) i 2.0 (niezawodność/jakość/skala) ZAMKNIĘTE. **Faza 3 (naprawy) ~w połowie.**
- **LIVE:** L1.1 serializer · L1.2 prawda-przyrządów (rotation-aware+żywy sla) · L2.1 sentinel-ingest · L6.A route-order golden · L0.2 env-parytet carried-first-guard.
- **Build-only (flaga OFF):** L2.2/L2.3.
- **Zostało (rdzeń, SERYJNIE):** L4 available_from · L3 plan_recheck · L5 ETA load-aware (bramka 04.07) · L6.B/C/D (bramki 02-03.07) · L0.1 rejestr-flag · L7 · L8.
- **Poza rdzeniem (z audytu 2.0):** security P0 (firewall/`/stop`/CDP/sekrety — Adrian-driven), 2 bomby TZ (~25.10), regres wydajności 2×, watchdog-close, cod-weekly, GC observability, multi-city.

**READ ORDER:** (1) ten plik; (2) `ZIOMEK_STAN_AUDYTY_1i2.md` (tracker); (3) `memory/ziomek-change-protocol.md` — **WKLEJ PROMPT + przeczytaj NOWE C12 (multi-agent) i C13 (mutation-test strażników)**; (4) `AUDYT2/MASTER_synteza.md` (findingi 2.0) + `ZIOMEK_FINDINGS_LEDGER.md`; (5) `2026-06-30/FAZA1_05_roadmapa_poc.md` (detal fal L).

---

## 2. ZASADA SPRINTU WIELO-AGENTOWEGO (bezpieczna równoległość — C12)

**Cel:** wielu agentów naprawia JEDNOCZEŚNIE, posuwając naprawy do przodu, NIE kolidując i NIE psując produkcji. Klucz: równoległy jest KOD+TESTY+REPLAY; DEPLOY (flip/restart) zawsze seryjny za ACK.

**3 klasy lane'ów:**
| Klasa | Co | Jak równolegle |
|---|---|---|
| 🟢 **PARALLEL-SAFE** | rozłączne pliki POZA rdzeniem silnika | wielu agentów naraz, KAŻDY w own `git worktree`; merge seryjny |
| 🟡 **SERIAL+ACK** | rdzeń silnika (feasibility_v2/dispatch_pipeline/plan_recheck/courier_resolver/route_simulator_v2/scoring) — fale L3/L4/L5/L6/L7 | JEDEN właściciel/fala, jedna fala na raz, ETAP 0→7, off-peak, ACK; NIE równoleglić między agentami (wspólne bliźniaki) |
| 🔴 **NIGDY-PARALLEL** | flip flagi, `systemctl restart`, Telegram, deploy w peak | pojedynczo, ręczna bramka ACK (C2) |

**Mechanika bez kolizji (obowiązkowa dla każdego mutującego agenta):**
1. **Partycja po plikach:** koordynator (tmux 9) przydziela lane→ZBIÓR PLIKÓW; **pusty przekrój między agentami** (sprawdź PRZED startem). Dwóch agentów NIGDY na tym samym pliku.
2. **Worktree per agent:** `git worktree add /root/.openclaw/workspace/wt-<lane> -b fix/<lane>` → osobny working-tree + osobny indeks → zero wyścigu `git add`/`.bak` (rozwiązuje C1-git u źródła). Agent pracuje TYLKO w swoim worktree. `opts.isolation:'worktree'` jeśli używasz Workflow.
3. **Agent produkuje, NIE deployuje:** kod + testy + `py_compile` + replay ON↔OFF z dowodem POZYTYWNEGO wpływu (ETAP 5) + pełna regresja vs baseline. Zwraca diff/branch + raport. **ZERO flipów/restartów/git-push do master.**
4. **Bramka scalenia (Ty, seryjnie):** review → merge worktree→master pojedynczo (`git show`+testy po każdym) → flip/restart za ACK Adriana, off-peak. Po każdym: `entropy_dashboard.py` re-run + **update trackera** + wpis do `ZIOMEK_FINDINGS_LEDGER.md`.
5. **Sprzątanie:** `git worktree remove` po merge (auto-clean jeśli bez zmian).

**Baseline regresji (ETAP 0):** odpal `pytest tests/` z KANONICZNEJ ścieżki (nie z worktree) i zapisz licznik PRZED sprintem — każdy agent porównuje do niego.

---

## 3. PIERWSZA FALA RÓWNOLEGŁA (🟢 PARALLEL-SAFE — rozłączne pliki, gotowa do puszczenia)

Te lane'y mają rozłączne pliki i NIE dotykają rdzenia — bezpieczne naraz w worktree (każdy: ETAP 0→7, dowód, ale STOP przed flipem/restartem = ACK):

| Lane | Pliki (rozłączne) | Zadanie | Bramka/uwaga |
|---|---|---|---|
| **TZ-consolidate** | `gastro_assign.py`, `tools/shadow_outcome_enricher.py`, `tools/{freshness_shadow_monitor,reassignment_shadow,sequential_replay,monitor_refloor_peak_2026_05_31}.py`, `sprint2_analysis/_common.py` | fixed-offset `+2`/`"+02:00"`/`WARSAW_OFFSET_HOURS` → `ZoneInfo("Europe/Warsaw")` (wzór `tools/ontime_lib.py`); + grep-strażnik „fixed CEST offset". Test: HH:MM zima vs lato ON≠buggy | data twarda 25-26.10; dziś lato=0 wpływu → bezpieczny replay |
| **watchdog-close** | `/etc/systemd/system/dispatch-watchdog.timer` (+ `.d/`), `observability/cron_health.py` rejestracja, drop-in OnFailure dla `dispatch-cod-weekly` | `OnCalendar=*-*-* 00/4:00` do watchdoga; dorejestruj cod-weekly + 7×`thr=None` z progiem; oneshoty wołają `record_run_success` | 🔴 enable/restart = ACK (nie sam agent) |
| **perf-SLO** | `tools/objm_lexr6_canary_monitor.py` (rozszerzenie o próg SLO), nowy `tools/perf_budget_report.py` | metryka p95 per okno + alert edge-triggered wg SLO z `PERF_budget.md`; zero nowej infry | pomiar/tooling — niskie ryzyko |
| **gc-observability** | `observability/log_rotation.py` (NIE ISTNIEJE — stwórz) + hook logrotate; events.db VACUUM plan | retencja 14d observability (dziś atrapa); readerzy — potwierdź komplet po L1.2 | events.db >90d ~10.07 |
| **cod-weekly-diag** | diagnoza exit1 (read) + fix źródła (osobny od watchdog-close rejestracji) | czemu pada co poniedziałek (brak bloku tygodnia w arkuszu) | pieniądze COD |

**NIE w tej fali (bo rdzeń/ryzyko):** L3/L4/L5/L6.B-D/L7 (SERIAL+ACK, po tej fali), security remediacja (Adrian-driven, krok 0 = Hetzner Cloud FW), L2.2/L2.3 flip (ACK).

**Sugerowana komenda startu (Workflow lub Agent):** dla każdego lane'u agent z `isolation:'worktree'`, prompt = „napraw {lane} wg HANDOFF §3 + protokół #0 (WKLEJ), STOP przed flipem/restartem, zwróć branch+dowód". Read-only recon (ETAP 0 wspólny) możesz zrobić 1× przed fan-outem.

---

## 4. PO PIERWSZEJ FALI: rdzeń silnika (SERIAL+ACK, kolejność zatwierdzona)
`L4 available_from (najgłębsze, F1) → L3 plan_recheck (F2) → L5 ETA load-aware (F4, bramka 04.07 — kotwica `assign` 83% NIE `last`) → L6.B O2 (02.07) / L6.D objm (03.07) / L6.C geometria → L0.1 rejestr-flag → L7 (L7.5 fcntl PRZED re-enable Telegrama) → L8`. Każda = jeden właściciel, ETAP 0→7, off-peak, replay-dowód, parytet bliźniaków, ACK. Detal: `FAZA1_05_roadmapa_poc.md`.

## 5. MINY (nie potknij się)
- **NIE flipuj `PENDING_RESWEEP_LIVE`** — `global_allocate` geometria VOID.
- **C2 re-enable Telegrama** dopiero po L7.5 (pending 3-writer fcntl) + naprawie klastra postpone (5 dead-paths).
- **Multi-sesja (C1):** `tmux ls` + `git log --oneline -10` PRZED każdą zmianą; ≥2 sesje pchają Fazę 3. Cudze `.bak`/worktree — nie ruszaj.
- **Instrument może kłamać (C9-C11):** flip na liczbie przyrządu → najpierw status w `FAZA1_03_rejestr_przyrzadow.md` (VOID=nie dowód).
- **Strażnik może być teatrem (C13):** zielony ≠ łapie regres; mutation-testuj.

## 6. PO KAŻDEJ FALI (DoD trackera)
`entropy_dashboard.py` re-run (metryki MALEJĄ) → status w `ZIOMEK_STAN_AUDYTY_1i2.md` (§2/§3 + Ostatnia aktualizacja) → wpis w `ZIOMEK_FINDINGS_LEDGER.md` → relay memory `[[ziomek-audyt-2-wyniki-2026-07-02]]`/`[[ziomek-unified-audit-2026-06-30]]`. Sesja bez update trackera = niezakończona.
