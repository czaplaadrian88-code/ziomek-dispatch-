# KONSOLIDACJA STANU ZIOMKA — 2026-07-05 ~17:15 UTC

> Pełna inwentaryzacja + weryfikacja audytów + gap analysis + plan sprintów wieloagentowych.
> Metoda: 3 agentów read-only (repo/architektura · audyty · backlog) + weryfikacja NA ŻYWO (flags.json, git, atq, timery). **Zero zmian w systemie w trakcie analizy.**
> Memory: `ziomek-status-konsolidacja-2026-07-05.md`. Tracker zaktualizowany (nagłówek 05.07 + §2 L3/L4/L5/V328).

---

## 1. STATUS KOMPONENTÓW (skrót — pełna tabela w memory)

Tryb: CIEŃ z propozycjami (autonomia zbudowana, `ENABLE_AUTO_ASSIGN=false`). Baseline testów 4109/0 (venv dispatch). 10 warstw pipeline LIVE.
- **L3+L4 FLIP LIVE 04.07** (at-202/203): `ENABLE_PLAN_RECHECK_GATES`+`ENABLE_COURIER_PLANS_GC`+`ENABLE_AVAILABLE_FROM_SINGLE_SOURCE`=true. GC nadal DRY (realny = at-205 Pn 06.07 12:40; `PLAN_GC_DRY_RUN` brak w flags.json = default True).
- **L6.C zbudowany** (`d8328b2`+tag, LOKALNIE — push za Adrianem), flagi OFF; replay quant=0 = PARETO. Flip za ACK off-peak.
- **GPS-5b kod LIVE end-to-end** (apka vc60 + `e5b3dc0` + `9b1e30c`), ale **pokrycie `gps_arrived_at` = 0/546** — stan sprzed restartu courier-api (~18:05 05.07) i adopcji vc60. Werdykt ~07-08.07 liczy się OD adopcji.
- **Nocny strażnik P2** zainstalowany, timer active (1. bieg nd 05.07).
- **Security-P0**: B4 LIVE (`313e6e5`), B1/B3 done 04.07; zostaje Adrian (Hetzner FW krok 0, rotacja tokenów C1/C2, @reboot CDP). ⚠ tokeny TG żyją w HISTORII git na GitHubie.
- ⚠ `dispatch-cod-weekly` żywo FAILED od 02.07 (WD-13). ⚠ duplikat GPS legacy @reboot (PID 1006/1010). ⚠ master ahead 2 vs origin.

## 2. WERYFIKACJA AUDYTÓW — kluczowe rozbieżności dokument↔żywo

1. Tracker był opóźniony ~1,5 dnia vs flagi (L3/L4/V328 pokazywane jako „za ACK", a już ON) → poprawione 05.07.
2. **L5 = jedyna NIEZBUDOWANA fala Fazy 3, bramka 04.07 minęła** — najgłębszy dług P3 (K3: bias odbioru med −3,6 min), otoczony ✅-kami, łatwy do przeoczenia.
3. 4 inwarianty ⚠️VOID (carried_first_guard, global_allocate geometria, **serializer −38 kluczy** [bramkuje kalibrację O2], INV-FLAG-CONFTEST-STRIP) + 21 pustych slotów (kontrakty ①②③). Flip na liczbach VOID = ZAKAZANY.
4. ⏰ **INV-SRC-ROUTE-ORDER deadline 07-10.07** — monitor `ziomek_time_route_monitor` wygasa; dziś 44-75 rozjazdów kolejności/dzień; 4 kopie logiki w 3 repach.
5. Przy flipach O2: klucze `ENABLE_O2_CAPZ_RESEQ`/`ENABLE_SLA_GATE_READY_ANCHOR` NIE istnieją w flags.json (czytane defaultem OFF) — trzeba DOPISAĆ, nie „przestawić".
6. Wycofane (nie wracać bez nowych dowodów): feas_carry_readmit (re-flip #483000 tylko po werdykcie 5b + outcome-join 83,7% wybaczeń nieszkodliwych), B3 no_gps, quant=1, LAP/Hungarian, martwa R7, „R6 płaska" (obalona), P1 intermittent-cold (kłamał przyrząd).

## 3. KALENDARZ BRAMEK

| Data | Co | Zależy |
|---|---|---|
| Pn 06.07 | at-205 12:40 GC realny · at-206 14:30 verify · at-208 19:30 review λ=0 · weryfikacja H2 Parys | bezpieczeństwo GC planów |
| ~07-08.07 | werdykt pokrycia GPS-5b (od adopcji vc60!) | flip O2, feas_carry #483000, dowód dla autonomii |
| 07-10.07 | ⏰ INV-SRC-ROUTE-ORDER (monitor wygasa) | widoczność rozjazdów trasa silnik↔konsola↔apka |
| ~10.07 | Fala A deep-dive (świeża mapa 0a) · events.db 90d | start fal A-D |
| ~17.07 | flota na GPS → flip GPS-02 | telemetria W4 |
| 25-26.10 | DST — podmiana żywego gastro_assign DUŻO wcześniej | 2 bomby TZ |

## 4. PLAN SPRINTÓW WIELOAGENTOWYCH (propozycja; każdy wg protokołu #0, worktree per agent [ADR-007], ZERO flipów/restartów/pushów bez ACK, poza peakiem)

### SPRINT 0 „Prawda i bramki" (06-08.07) — szczegóły: `SPRINT0_HANDOFF_tmux15.md`
- **A0-GEOFENCE** (Wysoki): weryfikacja adopcji 5b po restarcie courier-api — licznik `gps_arrived_at` w ground_truth musi ruszyć z 0; jeśli nie rusza → diagnoza łańcucha apka vc60→POST /arrival→ground_truth. Zakres: courier_api/ + logi; NIE silnik/panel.
- **A0-ROUTEORDER** (Wysoki): INV-SRC-ROUTE-ORDER przed 10.07 — golden-fixture parytetu kolejności silnik==konsola==apka (rozszerzenie L6.A) + następca monitora. Zakres: tests/ + tools/ziomek_time_route_monitor*; dispatch_pipeline/plan_manager READ-ONLY.
- **A0-OPS** (Średni): `dispatch-cod-weekly` FAILED (WD-13) diagnoza+patch staged (restart za ACK) + wygaszenie duplikatu GPS legacy @reboot (plan, wykonanie za ACK). Zakres: unity systemd cod-weekly + skrypt COD; NIE demony silnika.
- **A0-DOCS** (Niski): ✅ GROS WYKONANE 05.07 przez sesję konsolidacyjną (tracker+memory+timeline+todo). Rezyduum: nic pilnego.

### SPRINT 1 „Fundament pod flipy" (08-12.07)
- **A1-SERIALIZER** (Śr): serializer −38 kluczy (A+B RAZEM w `shadow_dispatcher._serialize_result`) + INV-FLAG-CONFTEST-STRIP; test parytetu kluczy. Odblokowuje kalibrację O2.
- **A1-INVARIANTS** (Wys): carried_first_guard + global_allocate geometria VOID→🟢TEST (odblokowuje PENDING_RESWEEP_LIVE); potem sloty kontraktu ①. Tylko asercje/testy, zero zmiany zachowania.
- **A1-L5** (Wys): budowa L5 ETA load-aware jako SHADOW (flaga OFF + metryka w shadow_decisions) + replay-dowód. Zakres: scoring.py + estymator; feasibility_v2 NIETYKALNE.
- **A1-SECURITY** (Śr): wykonanie staged skryptów PO ACK per-krok (auth /stop :8765, apply_token_rotation.sh); plan BFG na historię git = osobna decyzja Adriana.
- Kolizja SERIALIZER↔INVARIANTS: merge seryjny (najpierw SERIALIZER).

### SPRINT 2 „Okno flipów" (12-18.07; wymaga zielonego S1 + werdyktu 5b)
- **A2-FLIPMASTER** (jedyny dotyka flags.json; wszystko za ACK, off-peak, 1 flip na raz, rollback gotowy): restart shadow → `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK=true` (quant=0) → 2 dni → `ENABLE_ENGINE_CLAIM_LEDGER` osobno → GPS-02 ~17.07.
- **A2-O2** (Wys): sprint „wąskiej reguły O2" (GO na budowę, NIE surowy O2 — łamie carried-first); nowa flaga OFF + replay; bliźniaki `_selection_bucket`/`_best_effort_*` ↔ `objm_lexr6.py` RAZEM; wymaga naprawionego serializera.
- **A2-TZ** (Niski): podmiana żywego gastro_assign (ZoneInfo, staged) deploy za ACK.
- **A2-FEASCARRY** (Śr): po werdykcie 5b raport `feas_carry_outcome_join.py` → rekomendacja re-flip #483000 (tylko raport).

### SPRINT 3 „Skala i autonomia" (18-31.07; warunkowe)
- **A3-PERF** (Wys): ogon peak p95 (OR-Tools/OSRM/pool, sufit 4 vCPU) — measure-first.
- **A3-FALE-AD** (Wys): fale A-D deep-dive od ~10.07 (flicker 83% top-1, missed-bundle ~10/dz).
- **A3-AUTONOMIA** (Śr): przygotowanie 1. włączenia (test E2E executora na zleceniu testowym, monitor+stop-loss, runbook off-peak max_per_hour=1); WŁĄCZENIE = Adrian przy konsoli; `ENABLE_AUTO_ASSIGN` zostaje false.
- **A3-HYGIENE** (N-Śr): bare-except partiami, rejestr flag (112 poza rejestrem, 5 dead), WD-14.

## 5. DECYZJE WYŁĄCZNIE ADRIANA
KROK 0 Hetzner FW · rotacja tokenów + BFG historia git · backfill COD · termin sprintu O2 · B-lite (rekom. NIE budować) · fale C/D migracji flag · „objm ON na stałe" (Faza 4) · 1. włączenie autonomii · 2 klucze DR off-machine (KRYTYCZNE) · drugi serwer HA · push master→origin (ahead 2) · @reboot cdp_drop.
