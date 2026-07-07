# S27-B — Route-order: dowód 0-diff (⛔ ZABLOKOWANY) + mapa migracji

> **Autor:** Sprint 27-B · **Bieg:** 2026-07-07 ~19:20–20:25 UTC (wt) · **Tryb: READ-ONLY** (0 edycji kodu/flag; analiza + weryfikacja na żywo). Kod route-order MA powstać w worktree — ale **nie istnieje** (patrz §1).

## ⛔ 1. BLOKER KRYTYCZNY — dowód 0-diff NIE DO WYKONANIA (kod nie istnieje)

Handoff 27-B zakłada moduł `route_order.py` + flagę `ENABLE_ROUTE_ORDER_UNIFIED` na gałęzi `worktree-agent-a8a36495468ae05f0`. **Zweryfikowane — NIE ISTNIEJE:**
- `route_order.py` — **brak w JAKIEJKOLWIEK gałęzi, historii git i na dysku** (`git log --all -- route_order.py` puste; `find / -name route_order.py` = tylko pliki narzędziowe L6.A `route_order_golden_corpus_gen.py`/`route_order_live_parity_check.py`, NIE moduł).
- `ENABLE_ROUTE_ORDER_UNIFIED` — **0 referencji w kodzie** (grep po `rev-list --all`); występuje **wyłącznie w docach planistycznych** `eod_drafts/2026-07-07/` (plan multiagent + handoff + runbook §9).
- Gałąź `worktree-agent-a8a36495468ae05f0` jest na **`39fb1c9`** (mode-layer T2.4) — **bez route_order.py**.
- Plan multiagent (`SPRINT_MULTIAGENT_PLAN_0707.md:40`) deklaruje, że komponent **C** wyprodukował „route_order.py PURE + mapa 5 luster + parytet dziesiątki tys. porównań 0 rozjazdów, canon 10/10 sond KILLED" oraz raport `C_route_order_unified.md`. **Ani moduł, ani raport nie zostały zacommitowane/utrwalone** (`C_route_order_unified.md` też nie istnieje na dysku).

**Wniosek:** replay „0-diff ON vs OFF" jest **bezprzedmiotowy — nie ma kodu, który by flipować.** To klasa **C15** (status handoffu = hipoteza z chwili T; praca komponentu C zaginęła). **Zgłoszone koordynatorowi.** ⚠ `ZIOMEK_INVARIANTS.md:13/20` notuje „INV-SRC-ROUTE-ORDER w toku u tmux 15 (Sprint 0)" — ale (a) tmux 15 **nie działa** (aktywne: 11/25/27/28), (b) kodu nigdzie nie ma → notatka nieaktualna/niedomknięta. **Koordynator musi rozstrzygnąć: odbudować moduł czy odzyskać z utraconego worktree.**

## 2. Stan parytetu DZIŚ (C10 — liczba z monitora, nie lektura)
Mimo braku unifikacji, trzy powierzchnie **produkują dziś identyczną kolejność** — bo flagi trust-canon są ON wszędzie:
- Monitor `ziomek_time_route_monitor.jsonl` (Q3 = route-order parytet, co ~10 min), 5 ostatnich biegów **19:41→20:21 UTC: `q3_route_mismatches=[]` × 5** (0 mismatch, 0 errors). `ZIOMEK_INVARIANTS.md:20`: **mismatch=0/d od 01.07** przy 100–619 sprawdzeniach/d. „44–75/d" = NIEAKTUALNE.
- **ALE INV-SRC-ROUTE-ORDER = 🔴 RED strukturalnie:** *„4 kopie/3 repa trzymane FLAGAMI, nie konstrukcją"*. Parytet jest własnością konfiguracji flag, nie jednego źródła. ⏰ monitor wygasa **10-07**; następcy bez wygaśnięcia = testy `test_route_order_golden` (silnik) + `test_route_order_parity_golden` (panel) + `test_route_order_live_parity` (gated `ENABLE_ROUTE_ORDER_LIVE_PARITY`, ON od 05-07).
- **Narzędzia parytetu (L6.A, już w master):** `tools/route_order_live_parity_check.py` (KANON=`route_podjazdy.order_podjazdy` vs KONSOLA=`fleet_state._build_route`, projekcja `[(typ, sorted(order_ids))]`, exclude ETA/coords; + drift-check flag live vs corpus) · `tools/route_order_golden_corpus_gen.py` (17 syntetyków + żywe bagi). **Te narzędzia = domena tmux 28; TU tylko czytane.**

## 3. MAPA MIGRACJI — 5 luster / 3 repa (nie 4)
Rdzeń: **`dispatch_v2/route_podjazdy.py` JUŻ jest czystym (OSRM-free), deterministycznym, wspólnym źródłem** (`order_podjazdy` l.190 „JEDYNE źródło kolejności"; `_canon_order_from_plan` l.141; `PICKUP_MERGE_MIN=10` l.30 „= fleet_state") — używany przez apkę (courier_api) i harness parytetu. **`route_order.py` powinien być PROMOCJĄ `route_podjazdy`, nie nowym konkurentem** (inaczej wzorzec #2 — 2. kopia reguły).

| Lustro | Plik : symbol | Jak dziś ustala kolejność | Kontrakt do migracji | Ryzyko |
|---|---|---|---|---|
| **A — silnik (ŹRÓDŁO)** | `plan_recheck._apply_canon_order_invariants` (~1782) | carried-first → pickupy wg committed `czas_kuriera` → no-return-Z → **relax OSRM** (`_relax_carried_first`) → lex/noncarried. Pisze `courier_plans.json` który reszta czyta. | ZOSTAJE źródłem. Tylko *renderujące* inwarianty (carried-first + committed-sort + merge same-rest) = klasa do unifikacji. **Relax/lex/no-return = logika DECYZYJNA (R6/SLA) — NIE do czystego renderera.** | najwyższe do „złożenia", ale to źródło — nie ruszać decyzyjnej części |
| **C — wspólny czysty** | `route_podjazdy.order_podjazdy` (190) / `_canon_order_from_plan` (141) | trust-canon → render planu wprost (skip pickup carried, merge same-rest, coverage-gate); else carried-first fallback | **Nucleus unifikacji.** Pure, już pinowany goldenem. Promować do `route_order.py`, dowieść bajt-identyczności na korpusie. | **najniższe** |
| **B — konsola (panel)** | `nadajesz_clone/panel/.../fleet_state._build_route` (395) / `_order_from_plan_seq` (342) | trust-canon+coverage → render planu; else carried-first rebuild. **Splata kolejność z ETA** (OSRM `_eta_chain`, floory, monotonic). | *Kolejność* deleguje do C; *ETA/floory/monotonic* ZOSTAJĄ lokalne. | **wysokie, CROSS-REPO** — osobny deploy (`nadajesz-panel.service`, własny venv, 4 kopie repo), edytowany przez RÓWNOLEGŁE sesje CC → migrować OSTATNIE, monitor+live-parity jako siatka |
| **D — apka backend** | `courier_api/courier_orders.py` build-view (~1128) | 3 gałęzie: `APP_ROUTE_FROM_CONSOLE`→C (już!) / `ziomek_plan`→`_plan_stop_sequence`+carried+committed / fallback→`optimize_route` NN | wszystkie 3 gałęzie → moduł zunifikowany (gałąź 1 już deleguje) | średnie (ten sam repo/venv co silnik = najtaniej) |
| **5. prymityw** | `courier_orders._repair_dropoffs_after_pickups(kind_key="kind")` (424) | naprawia dropoff przed swoim pickupem po re-sort committed | **BLIŹNIAK** `plan_recheck.py:1203` (ten sam algorytm, klucz `type`) → skonwergować przy migracji D | średnie |
| **Kotlin render** | `RouteLogic.kt buildSteps` (27) | **konsumuje `stopSequence` WPROST** — 0 sortu, 0 OSRM; tylko wizualny merge sąsiednich pickupów `PICKUP_MERGE_MIN=10` | brak (nie ustala kolejności). Jedyny kontrakt cross-language = **pin `PICKUP_MERGE_MIN=10`** = `route_podjazdy.PICKUP_MERGE_MIN` (test panel-side już pinuje). | **brak dla kolejności** (apka = osobny release APK vc72 — kontrakt tylko przez wartość, nigdy code-share) |

**Rekomendowana kolejność (dźwignia P3, największa — koniec „carried-first 10×"):**
1. **Zamroź kontrakt/progi** (już w goldenie: `PICKUP_MERGE_MIN=10`, coverage-gate, carried-first+committed-ascending).
2. **Promuj C → `route_order.py`** (pure, flaga OFF), dowód bajt-identyczności na korpusie.
3. **Migruj D** (apka backend, wszystkie 3 gałęzie; skonwerguj bliźniaka `_repair_dropoffs_after_pickups`) — ten sam repo/venv = najtaniej.
4. **Migruj B ostatnie** (panel — cross-repo, osobny deploy, sesje równoległe; tylko kolejność, ETA zostaje). Monitor+live-parity = siatka.
5. **A zostaje źródłem** (`_apply_canon_order_invariants` pisze plan; relax/lex/no-return = decyzja, nie renderer).

**Dowód 0-diff (gdy moduł powstanie):** replay `ENABLE_ROUTE_ORDER_UNIFIED` ON vs OFF na żywym korpusie 2d = identyczna projekcja `[(typ, sorted(order_ids))]`. „warto" = jedno źródło (koniec 4-kopiowego długu, INV-SRC domknięty konstrukcyjnie); „bez regresji" = 0 diff. Flaga = **MERGE+restart plan-recheck+shadow (NIE hot)** — runbook §9.

## 4. Cross-repo hazard (wprost)
Panel (`nadajesz_clone/panel/backend` + 3 kopie repa) = **osobny deployable**, własny venv, edytowany przez **równoległe sesje CC** ([[feedback-multisession-shared-deploy]]). Apka = **osobny build/release APK (vc72)** — zmiana kontraktu jedzie w cyklu release apki, nie restartem serwera. To dlatego „jedno źródło" jest trudne z konstrukcji: wspólny import Pythona nie przekracza granicy Kotlina — granicę apki da się tylko **pinować kontraktem** (`PICKUP_MERGE_MIN` + „konsumuj kolejność wprost"), nigdy współdzielić kodem.

## 5. DoD 27-B
- ✅ Mapa migracji 5 luster + kontrakty + ryzyko + kolejność. ✅ Charakterystyka parytetu (monitor 0-mismatch, INV RED konstrukcyjnie). ✅ Bloker udowodniony+zgłoszony.
- ⛔ **Dowód 0-diff NIEWYKONANY** — kod nie istnieje (nie wina wykonania; brak artefaktu). ⛔ ZERO merge/flip (zgodnie z zakresem).

---
**Powiązane:** `PAS0_FLIPMASTER_RUNBOOK.md §9` · `ZIOMEK_INVARIANTS.md:20` (INV-SRC-ROUTE-ORDER) · `tools/route_order_{golden_corpus_gen,live_parity_check}.py` (L6.A, tmux 28) · `SPRINT_MULTIAGENT_PLAN_0707.md:40` (deklaracja komponentu C) · [[route-order-golden-l6a-2026-07-01]] · [[feedback-multisession-shared-deploy]].
