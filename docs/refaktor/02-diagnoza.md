# 02 — Diagnoza (Faza 2: problemy systemowe + priorytetyzacja)

**Data:** 2026-07-06 · **Baza:** `01-stan-obecny.md` + raporty `raw/01a-01e` + **pomiary własne 06.07** (read-only): pile-on z `shadow_decisions.jsonl` (okno od 2026-07-03T13:19Z, po L1.1), monitor `ziomek_time_route_monitor.jsonl`, diff env drop-inów, efektywne flagi courier-api, raport perf 04.07.
**Skala ocen:** Wpływ 1-5 · Ryzyko pozostawienia 1-5 · Koszt S=1/M=2/L=3 · **Priorytet = Wpływ × Ryzyko / Koszt**. Kontekst skali: dziś ~240 zam/d, cel ~400 zam/d + multi-tenant (Faza 0).

## POMIARY WYKONANE W TEJ FAZIE (liczby, nie lektura — reguła C4)

| Pomiar | Wynik | Źródło |
|---|---|---|
| Pile-on propozycji (ten sam best-kurier ≥2 zlecenia w tej samej minucie) | **28 zdarzeń, 67/310 propozycji (22%), maxpile=4**, 28/256 minut | `shadow_decisions.jsonl` od 03.07T13:19Z |
| Rozjazdy route-order konsola↔apka | **0 mismatch od 01.07** (checked/dzień 100-619, match 100%); q2_drift 2-29/d, q1_missing_time 1-19/d (inna klasa — czasy, nie kolejność) | `ziomek_time_route_monitor.jsonl` 01-06.07 |
| Env-rozjazd recanon | **POTWIERDZONY**: plan-recheck `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1` + `LIVE_ETA_REFRESH=1`; panel-watcher — brak (inny zestaw: `USE_V2_PARSER=1`, `ENABLE_PANEL_BG_REFRESH=0`); drop-iny f1-f2 vs f3 | `systemctl show -p Environment` + ls service.d |
| Flagi apki LIVE | `ENABLE_APP_ROUTE_FROM_CONSOLE=1`, `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=1` (kod-default „0" mylił — hipoteza §7.3 rozstrzygnięta) | systemctl show courier-api |
| Perf assess_order | **p50=711 ms, p95=1613, p99=2050, ogon>1500ms=8,8%; SLO peak p50 790>700 i p95 1725>1500 🔴; ×1,9 vs kwiecień** | `eod_drafts/2026-07-04/perf_budget_report_pre_slo_flip.txt` |
| at-joby 205/206/208 | flip-gate GC + bundle_calib_review — brak „dzikich" pisarzy stanu | `at -c` |
| Werdykty od 03.07 | 310 PROPOSE / 14 KOORD (95,7% propozycji) | shadow log |

---

## TOP-10 PROBLEMÓW SYSTEMOWYCH

### D1. Miny „armed-on-flip" wokół propozycji (postpone/Telegram/ledger) — najtańsze zdjęcie największego odroczonego ryzyka
- **Dowód:** `postpone_sweeper.py:103-110` — `.get("orders")` na PŁASKIM dict + pole `cid` zamiast `courier_id` → gałąź `POSTPONE_RESOLVED` nieosiągalna; **timer biega co ~1 min** (uzbrojony). `telegram_approver.save_pending` = blind-overwrite poza kanonem fcntl (`pending_proposals_store.py:149-157`). Pile-on **zmierzony: 22% propozycji** — bez claim-ledgera (`ENABLE_ENGINE_CLAIM_LEDGER=false`) przy przyszłej autonomii = podwójne obłożenie kuriera.
- **Skutek:** dziś ~0 (postponed pusty, Telegram OFF, człowiek w pętli). Przy re-enable Telegram/postpone → duplikaty propozycji dla przypisanych zleceń; przy autonomii → double-booking. Klasa C2: defekt uzbraja się dokładnie tą dźwignią, którą operator pociągnie pod stresem.
- **Ocena:** Wpływ 4 · Ryzyko 5 · Koszt **S** → **20,0** · **Zmiana lokalna** (fix schematu / wygaszenie timera / delta-kanon dla Telegrama; flip ledgera = osobno, ścieżką FLIPMASTERA z dowodem ETAP 5).

### D2. Rdzeń decyzyjny nieodcięty od środowiska (luka F-2 kanonu)
- **Dowód:** flagi czytane z DYSKU ~700×/decyzję (`common.py:54-77`, komentarz `:25-27`) — zmiana flags.json w środku ticku zmienia zachowanie między kandydatami; OSRM na żywo w 4 punktach (`route_simulator_v2.py:405/638`, `dispatch_pipeline.py:4077/4321`); żywy HTTP fetch panelu w ocenie (`:3913`); zapisy shadow-logów + load-governor (plik+Telegram) WEWNĄTRZ assess (`feasibility_v2.py:368/409`, `dispatch_pipeline.py:3694-3706`); frozen-clock nie istnieje (grep). Monolit `_assess_order_impl` ~3785 l.
- **Skutek:** decyzje niedeterministyczne i nietestowalne charakteryzująco; replay tylko kontrfaktyczny; każdy flip-werdykt obciążony szumem; koszt każdej zmiany rdzenia wysoki (brak izolacji).
- **Łagodzi:** stan wchodzi argumentami; `scoring`/`route_sim`/`objm_lexr6` już czyste; efekty w nazwanych helperach = gotowe punkty cięcia (raw/01b TOP-5).
- **Ocena:** Wpływ 5 · Ryzyko 4 · Koszt **M** → **10,0** · **Architektura** (filar F-2; rdzeń programu migracji — Faza 3/4).

### D3. Brak lintera i typecheckera (zero siatki na refaktor)
- **Dowód:** Faza 0 §1 — brak narzędzi w venv i configów w repo.
- **Skutek:** refaktor „na lata" bez automatycznej detekcji martwego kodu/literówek/typów; klasa błędów #202 (env-frozen default) niewykrywalna mechanicznie.
- **Ocena:** Wpływ 3 · Ryzyko 3 · Koszt **S** → **9,0** · **Lokalna** (ACK Adriana już jest: osobny venv narzędziowy, dev-only; baseline ruff+mypy jako ratchet, nie big-bang).

### D4. `plan_recheck` = drugi rdzeń omijający HARD + ten sam kod pod dwoma env
- **Dowód:** komentarz wprost `plan_recheck.py:1019-1020` („regeneracja woła simulate_bag_route_v2, NIE check_feasibility_v2 → sekwencja może być GORSZA R6"); własny generator `_gen_one_bag_plan:658` obok `route_simulator_v2._simulate_sequence:559`; **zmierzony** env-rozjazd plan-recheck↔panel-watcher (tabela pomiarów) — ta sama funkcja recanon działa różnie w 2 procesach.
- **Skutek:** re-sekwencja może pogorszyć R6 (termika jedzenia = reguła nr 1 biznesu); oscylacje kolejności widoczne w konsoli/apce („trasa się przestawia"); q2_drift 2-29/d to prawdopodobnie częściowo ten mechanizm (HIPOTEZA — do potwierdzenia korelacją).
- **Ocena:** Wpływ 4 · Ryzyko 4 · Koszt **M** → **8,0** · **Pół-architektoniczna**: docelowo wspólny rdzeń (F-2), krótkoterminowo lokalnie — bramka feasibility na wyjściu sweep + parytet env drop-inów.

### D5. Flagi: populacja ~438 w 3 światach, podwójne źródła, env-frozen
- **Dowód:** `ETAP4_DECISION_FLAGS`=106 (common.py:95-391) vs flags.json=229 vs populacja ~438 (flag_registry 03.07); dziesiątki module-frozen `os.environ.get` (raw/01c §5b); **pułapka zweryfikowana na żywo:** `ENABLE_SLA_ANCHOR_UNIFIED` kod-default False/docstring „OFF", a `flags.json:260=true` → LIVE (agent główny, 06.07).
- **Skutek:** stan systemu nieodczytywalny bez śledztwa per-proces (koszt każdej sesji/zmiany); klasa nawracających błędów #9/C15/C16/#202; werdykty budowane na złym założeniu stanu.
- **Ocena:** Wpływ 4 · Ryzyko 4 · Koszt **M** → **8,0** · **Pół-architektoniczna**: snapshot flag per tick (część D2) + jeden rejestr z sondą efektywną (filar F-5) + ratchet „zero nowych env-frozen".

### D6. Regresja wydajności assess ×1,9, SLO czerwone już przy 240/d
- **Dowód:** raport perf 04.07 (tabela pomiarów): peak p50 790>700, p95 1725>1500, off-peak też czerwony; ×1,9 vs kwiecień. Kandydat przyczynowy nr 1 zbieżny z D2: `load_flags()` ze `stat()`+parse ~700×/decyzję + inline `open+json.load` w hot-path (`dispatch_pipeline.py:878/1628/2952`, `courier_resolver` 8×).
- **Skutek:** przy 400/d (+67% wolumenu) ogon urośnie; opóźnione propozycje w peaku = gorsze okna dla koordynatora. (Tick 3 min pozostaje OK per Adrian — problem to ogon per-decyzja, nie kadencja.)
- **Ocena:** Wpływ 3 · Ryzyko 4 · Koszt **M** → **6,0** · **Lokalna** (profiling + snapshot flag/cachowanie odczytów; duża synergia z D2 — jeden krok może zamknąć oba).

### D7. Pozycja kuriera w ≥4 magazynach z różnymi writerami
- **Dowód:** `gps_positions.json` (gps_server) + `gps_positions_pwa.json` (dual-write apki) + `courier_last_pos.json` (courier_resolver, bez fcntl — raw/01d R6) + `courier_api.db` (apka) + `fleet_position_history.jsonl` (raw/01b §3).
- **Skutek:** to podglebie klasy „dyskryminacja no-GPS/pre-shift" naprawianej ≥4× (8 bliźniaków); każdy konsument może czytać inną prawdę o pozycji; utrudnia typ `Known|Unknown` (filar F-3).
- **Ocena:** Wpływ 3 · Ryzyko 4 · Koszt **M** → **6,0** · **Architektura danych** (jeden store pozycji z jednym writerem lub jawną hierarchią źródeł w WorldState — filar F-1).

### D8. Blokery skali docelowej: przypisanie przez HTML gastro + single-tenant w kodzie
- **Dowód:** pętla przypisania przechodzi przez scraping/POST zewnętrznego panelu (raw/01a kroki 5-5b, „prawda o przypisaniu należy do gastro"); bbox Białystok jako HARD w common (`coords_in_bialystok_bbox:844`); state-files bez wymiaru tenanta; pakiet integracji IR v1 gotowy w `docs/integracje/` (Faza 0: cel = multi-tenant, integracje wg tych artefaktów).
- **Skutek:** 400/d w jednym mieście przejdzie (HTML poll ~liniowy), ale **multi-tenant/integracje API są niewykonalne bez decyzji architektonicznej** — to główny motor Wariantów B/C w Fazie 3, nie szybka naprawa.
- **Ocena:** Wpływ 5 · Ryzyko 3 · Koszt **L** → **5,0** · **Architektura** (Faza 3; nietykalne kontrakty — każda zmiana granicy za zgodą Adriana).

### D9. Replay nie-bit-w-bit — „staging" stoi na kontrfaktach
- **Dowód:** OSRM nie nagrywany (obj_harness re-woła :5001), `picked_up_at`=proxy (`obj_harness.py:12-13`), brak frozen-clock, logrotate gubi ~29% okna bez `ledger_io`, prawda fizyczna ~11,5% okna, paczki 0% (raw/01e §5).
- **Skutek:** decyzja Adriana „shadow+replay = staging" jest dziś spełniona tylko dla re-scoringu zapisanych kandydatów; pełna re-symulacja (potrzebna do testów charakteryzujących rdzenia i werdyktów sekwencji) niemożliwa.
- **Ocena:** Wpływ 3 · Ryzyko 3 · Koszt **M** → **4,5** · **Pół-architektoniczna** — w większości rozwiązywana przez D2 (WorldState-snapshot per tick = naturalny format nagrania; dopisać macierz OSRM).

### D10. Route-order: 4 kopie w 3 repach — parytet z FLAG, nie z konstrukcji
- **Dowód:** kanon `plan_recheck._apply_canon_order_invariants:1739` + `route_podjazdy` + `courier_orders.build_view:1096` + `fleet_state._build_route` (konsola); parytet pilnowany flagami trust-canon (wszystkie dziś ON — zmierzone) + golden-testem L6.A. **Świeży pomiar: mismatch=0 od 01.07** — żywy rozjazd USTAŁ (notatka memory „44-75/d" przeterminowana).
- **Skutek:** dziś zerowy rozjazd, ale każda przyszła zmiana kolejności musi trafić w 4 miejsca/3 repa (koszt+ryzyko dryfu); wyłączenie którejkolwiek flagi = powrót rozjazdów; monitor tymczasowy.
- **Ocena:** Wpływ 3 · Ryzyko 4 · Koszt **L** → **4,0** · **Architektura** (1 moduł + golden parytet z konstrukcji; koordynacja z pracą tmux 15 / Sprint 0 — NIE dublować).

---

## TABELA PRIORYTETÓW (Wpływ × Ryzyko / Koszt)

| # | Problem | Wpływ | Ryzyko | Koszt | **Prio** | Typ |
|---|---|---|---|---|---|---|
| D1 | Miny armed-on-flip (postpone/Telegram/ledger) | 4 | 5 | S | **20,0** | lokalna |
| D2 | Rdzeń nieodcięty od środowiska (F-2) | 5 | 4 | M | **10,0** | architektura |
| D3 | Brak lint/typecheck | 3 | 3 | S | **9,0** | lokalna |
| D4 | plan_recheck omija HARD + 2 env | 4 | 4 | M | **8,0** | pół-arch |
| D5 | Flagi 3 światy / podwójne źródła | 4 | 4 | M | **8,0** | pół-arch |
| D6 | Perf ×1,9 / SLO czerwone | 3 | 4 | M | **6,0** | lokalna (synergia D2) |
| D7 | Pozycja kuriera ×≥4 magazyny | 3 | 4 | M | **6,0** | architektura danych |
| D8 | Multi-tenant / HTML-gastro granica | 5 | 3 | L | **5,0** | architektura (Faza 3) |
| D9 | Replay nie-bit-w-bit | 3 | 3 | M | **4,5** | pół-arch (via D2) |
| D10 | Route-order ×4 (parytet z flag) | 3 | 4 | L | **4,0** | architektura |

Uwaga metodyczna: formuła premiuje tanie zdjęcia dużego ryzyka (D1, D3) przed drogimi przebudowami — zgodnie z zasadą małych odwracalnych kroków. Kolejność **implementacji** w Fazie 4 dodatkowo uwzględni zasadę „najpierw odcinanie rdzenia od I/O" (D2 jako szkielet, D1/D3 jako kroki zerowe).

## BACKLOG (poza top-10 — świadomie odłożone)
- `courier_last_pos.json` bez fcntl (raw/01d R6) i `global_alloc_store` wspólny `.tmp` (R7) — tanie, niska waga; wciągnąć do planu jako kroki „przy okazji".
- Shadow-jsonl pisane surowym `open('a')` z pominięciem `core/jsonl_appender` (raw/01c §2c).
- Cykle importów rozbrojone leniwością (`auto_assign_gate↔dispatch_pipeline`, `panel_client↔panel_html_parser`) — nietykać do czasu rozbiórki monolitu.
- `scoring.py` — mylna nazwa (realny scoring w pipeline+common); naturalnie rozwiąże się przy D2.
- Rozdwojenie lokalizacji logów (shadow w `scripts/logs/`, reszta w `dispatch_state/`).
- `telegram_approver.py` 4348 l. martwego kodu w korzeniu (WD-12 — decyzja Adriana osobno).
- Anomalie q1_missing_time (1-19/d) i q2_drift (2-29/d) z monitora — inna klasa niż route-order; do diagnozy przy D4.
- Korekty żywych docs (8 pozycji z `01-stan-obecny.md` §6) — **czekają na osobne OK Adriana** (pytanie #2 STOP-u Fazy 1, bez odpowiedzi).
- Rozjazd sterowania `ENABLE_LGBM_SHADOW` (env-frozen zamiast C.flag) — kosmetyka spójności (raw/01e HIPOTEZA).

## HIPOTEZY DOMKNIĘTE W TEJ FAZIE
§7.3 APP_ROUTE_FROM_CONSOLE → LIVE=1 (zmierzone) · §7.4 env-rozjazd recanon → POTWIERDZONY (diff) · §7.5 pile-on → 22%/maxpile 4 (zmierzone) · §7.7 pisarze spoza serwisów → at-joby czyste. Otwarte pozostają: §7.1 (kolejność fleet_snapshot — częściowo: iteracja po dict deterministyczna, źródła merge do potwierdzenia testem), §7.2 (parytet generatorów planów — wymaga golden-fixture, zaplanować jako test charakteryzujący w Fazie 4/5), §7.6 (leniwe importy w fan-in).

---
*Artefakt Fazy 2. Następny: `03-architektura.md` + ADR-y (Faza 3 — warianty A/B/C na kanonie 6 filarów, z decyzją multi-tenant), po „dalej" od Adriana.*
