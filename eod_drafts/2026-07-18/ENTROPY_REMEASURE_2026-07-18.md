# RE-POMIAR 8 MIERNIKÓW ENTROPII — 2026-07-18 wieczór (okno zamrożenia przed at#219)

> ## ⚠ KOREKTA (18.07 ~16:15, wykonanie GO „Dawaj falę ①")
> Pierwsza wersja tego raportu twierdziła „#2 POMIAR ŚLEPY od 8 dni" i rekomendowała
> budowę golden-testu route-order jako falę ①. **To było BŁĘDNE — na przeterminowanym
> opisie wiersza #2 w dashboardzie** (klasa C15/C68: STALE status przyrządu; ta sama
> klasa ugryzła Sprint C 08.07). Prawda: **siatka golden route-order ISTNIEJE i ŻYJE**
> — INV-SRC-ROUTE-ORDER + INV-TWIN-ROUTE-ORDER 🟢 (08.07, każda noga mutation-probed
> RED), `test_route_order_live_parity` biega w KAŻDEJ pełnej regresji pod venvem
> panelu (default ON za ACK 05.07; dziś 16/16 passed, zero skipów), konsola DELEGUJE
> do kanonu od deployu 08.07 19:51 (panel `9168cce`). Monitor wygasł 10.07 **ZGODNIE
> Z PLANEM** (świadomie zastąpiony siatką bez daty) — to nie luka. Wykonanie fali ①
> = weryfikacja żywości siatki + naprawa DWÓCH kłamiących wpisów u źródła (wiersz #2
> dashboardu + wiersz route-order w ARCHITECTURE §4 „konsola NIE importuje") +
> aktualizacja baseline'ów #1/#5/#6/#8 o dzisiejszy re-pomiar. Ranking fal niżej
> SKORYGOWANY (stare ① wypada jako zrobione).

Kontekst: baseline [AUDIT-BASELINE] w `tools/entropy_dashboard.py` = Faza 1 (30.06).
Od tego czasu: dedup `d96b793`, unifikacje K15/lex_qual, KANON v1.0→v1.3 (tabela
konfliktów + OD-01..07), L6.C, O2, B2 (#4→0), fala #7 (#7→0), D3-gold (cleanup
uzbrojony at#218). Ten plik = uczciwy stan DZIŚ + dowody + propozycja kolejnych fal.
Metoda: read-only (greppy + rejestry + mapy FAZA1 + KANON). Edycja baseline'ów w
dashboardzie = po weryfikacji restartu 19:05 (zamrożenie silnika).

## Tablica — stan po re-pomiarze

| # | Metryka | Baseline 30.06 | DZIŚ (re-pomiar 18.07) | Trend |
|---|---|---|---|---|
| 1 | copy-count | 17 reguł (≈90 inst.) | **~13-14 reguł żywych** — od baseline zamknięte: lex_qual ×6 → 1 def (✅ §4), generatory planów K15 → `core/planner` (✅ §4), R6-cap-quantile → flip OFF dziś + cięcie kodu at#218; route-order 5→4 kopie (korekta 06.07); OTWARTE m.in.: floor-odbioru **17 powierzchni / 4 z floorem**, 8 bliźniaków pozycji, SLA-anchor ×3, R6 35/40, czasówka 60. Pełne listy instancji: `backing/WF2_DIGEST.md` (nie re-liczone dziś) | ↓ |
| 2 | twin-divergence | ~13 DIVERGED | konstrukcyjnie bez zmian (8 bliźniaków pozycji wg §4, fala ① nie ruszona; route-order: 4 kopie, behawioralnie 0 mismatch/d przy wygaśnięciu). ⚠ **INSTRUMENT MARTWY**: `ziomek_time_route_monitor.jsonl` ostatni wpis **2026-07-10 23:54** — 8 dni bez żywego pomiaru (rejestr §4 ostrzegał „wygasa 07-10") | ⚠ WIERSZ NIEAKTUALNY — patrz KOREKTA na górze (siatka golden żyje; monitor wygasł planowo) |
| 3 | void-instrument | 19 VOID + 6 UNTESTED / 49 | NIE re-mierzone (lane runtime-oracle = osobna duża szychta; L1.2 zdjął wrong-source 3→0) | ? |
| 4 | dead-flag/rozjazdy | 5 (+112 poza rej.) | **0** [AUTO] — B2 18.07 | ✅ 0 |
| 5 | layer-violation | 7 (klasa C=16 findingów, rooty) | **ZAMKNIĘTE od baseline: 2 rooty** — (a) `calibration-on-wrong-axis` (ETA_QUANTILE_R6_BAGCAP 🔥LIVE u baseline) → **flip OFF dziś (D3-gold)** + kod ścięty at#218; (b) `geometry-blind-selection` → L6.C K2+K3 + `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK=True` (żywe). **OTWARTE:** `r-declared-time-hard-no-runtime-invariant` (HARD bez runtime-gate — sprawdzić vs sprint inwariantów 08.07), `paczka-r6-exempt-inverted` (`_count_sla` bez exemptu — L6.B3), `hard-feasibility-split` (FEAS_CARRY_READMIT bypass latentny, flaga OFF; `dispatch_pipeline:6266` — dryf linii możliwy) | ↓ (−2) |
| 6 | unresolved-conflict | 13 klastrów (64 par) | **7 otwartych** — pełny cross-check K-A..K-M × KANON v1.3 + kod na żywo: **ZAMKNIĘTE kodem** K-C (D3 dziś), K-H (L6.C + lex_qual T3/L3), K-I (01.07 L0.1 D.5: const „1"→„0", komentarz dokumentuje fix; json+const zgodne OFF — zweryfikowane dziś). **Precedencja ZDEFINIOWANA werdyktem właściciela, drift kodu śledzony w #1/#5/#8:** K-A (OD-07 kotwica), K-B (OD-07/C5: 40=Alarm-only), K-E (C3 równość HARD). **OTWARTE:** K-D (regen plan_recheck odclampowuje floor pre-shift), K-F (frozen-R27 omija floor), K-G (FEAS_CARRY_READMIT bypass — latentny, flaga OFF zweryfikowana), K-J (R-DECLARED-TIME BEZ runtime-inwariantu — grep na żywo pusty), K-K (podwójny load BALANCE↔GOVERNOR), K-L (nazwa-HARD/zachowanie-SOFT), K-M częściowo (L1: dwie flagi no-GPS — v1.3 jawnie zostawia L1/L2/L4/B0/B1) | ↓ (13→7) |
| 7 | sentinel-as-data | 12 (baseline) | **0 żywy silnik** [AUTO-oracle] — fala #7 18.07 (`cbd823a`); +4 instrumenty (osobna kategoria) | ✅ 0 |
| 8 | threshold-sprawl | 10 rodzin (≈40 sites) | rodzina R6=35: stała `BAG_TIME_HARD_MAX_MIN` JEST, ale **≥6 gołych literałów w plan_recheck** (`sla_minutes=35` :824/:830/:2034, `> 35.0` :1446/:1571/:1725) + fallbacki `35.0`; rodzina czasówka=60: **7 miejsc** (panel_watcher ×3, state_machine:137, czasowka_scheduler:131, auto_proximity_classifier:156, **eta_calib_serving `_was_czasowka` — NOWE 18.07, rodzina UROSŁA 6→7**); pozostałe rodziny z klasy N (nie re-liczone dziś): margin=15 ×5, R27=±5 ×5, 8km spread, R7=99km never-fires (`common.py:800`), `r6_soft_penalty_c3_legacy`. Po at#218 rodzina R6 traci gałąź quantile/tier-40 | ⚠ bez poprawy, +1 |

## Dowody (komendy powtarzalne)

- #2: `ls -la dispatch_state/ziomek_time_route_monitor.jsonl` → mtime Jul 10 23:54.
- #8 R6: `grep -n "sla_minutes=35\|> 35\.0" plan_recheck.py` → 6 trafień.
- #8 czasówka: `grep -rn ">= 60" panel_watcher.py state_machine.py czasowka_scheduler.py auto_proximity_classifier.py` + `_was_czasowka` w eta_calib_serving.py.
- #4/#7: `tools/entropy_dashboard.py` sekcja AUTO (0 / 0).

## Klastry konfliktów — pełna lista statusów (K-A..K-M, cross-check 18.07)

| Klaster | Konflikt (skrót) | Status 18.07 |
|---|---|---|
| K-A | R6 dwie kotwice (thermal ready vs SLA `_count_sla`) | intencja = OD-07 (possession→handoff); KOD hybrydowy (⚠ drift w KANON §3) |
| K-B | R6 35 vs 40 (6 stałych) | intencja = OD-07/C5 (40 TYLKO Alarm, nigdy klasa); sprawl w #8; at#218 zetnie quantile |
| K-C | ETA_QUANTILE_R6_BAGCAP luzuje HARD | ✅ ZAMKNIĘTY DZIŚ (flip OFF + cięcie kodu at#218) |
| K-D | regen plan_recheck odclampowuje floor pre-shift | ❌ OTWARTY (kandydat na falę) |
| K-E | equal-treatment: silnik vs bramki poza silnikiem | intencja = C3 (HARD równość); kod = fala ① bliźniaków (nie ruszona) |
| K-F | frozen-R27 broni złego czasu pre-shift (omija floor) | ❌ OTWARTY (para z K-D) |
| K-G | FEAS_CARRY_READMIT bypass za guardem P0 | latentny (flaga OFF — zweryfikowane dziś); nie flipować bez co-designu z `_assert_feasibility_first` |
| K-H | geometria-blind lex_qual | ✅ ZAMKNIĘTY (L6.C K2+K3 + `LEXQUAL_GEOMETRY_TIEBREAK=True` żywe; bliźniak shadow deleguje — T3/L3 v1.3) |
| K-I | COMMIT_DIVERGENCE const=True maskowany json=False | ✅ ZAMKNIĘTY 01.07 (L0.1 D.5, const→"0"; dziś zweryfikowane json+const zgodne) |
| K-J | R-DECLARED-TIME bez runtime-inwariantu | ❌ OTWARTY (tani tripwire — kandydat) |
| K-K | podwójny load: BALANCE ↔ GOVERNOR (+stopover/bug4) | ❌ OTWARTY (wymaga pomiaru sprzężenia) |
| K-L | nazwa-HARD vs zachowanie-SOFT (RETURN_VETO, LATE_PICKUP) | ❌ OTWARTY (higiena nazw + jawne miejsce realnego zakazu) |
| K-M | kanon sam ze sobą sprzeczny (równość vs −20 pre-shift) | częściowo: v1.3 jawnie zostawia L1 (dwie flagi no-GPS) — reszta rozstrzygnięta |

## Wnioski → propozycja kolejnych fal (RANKING SKORYGOWANY — patrz KOREKTA na górze)

~~1. Fala #2-instrument golden-test route-order~~ — **JUŻ ISTNIEJE (Sprint C 08.07)**; wykonanie tego GO = weryfikacja żywości (16/16, zero skipów) + naprawa 2 kłamiących wpisów (dashboard #2, ARCHITECTURE §4) + baseline'y zaktualizowane.

1. **Fala #8a (mechaniczna, parytetowa):** zszycie literałów R6=35 (6× plan_recheck → `BAG_TIME_HARD_MAX_MIN`) i czasówka=60 (7 miejsc → JEDNA stała + helper `is_czasowka(prep)`; w tym MÓJ dzisiejszy site w eta_calib_serving). Byte-parity semantyki.
2. **K-J tripwire (tani, wysoka wartość):** runtime-inwariant R-DECLARED-TIME (`czas_kuriera ≥ czas_odbioru` assert + metryka naruszeń w shadow) — chroni NAJWYŻSZY HARD przed cichym złamaniem przyszłą zmianą R27.
3. **Fala K-D+K-F (behawioralna, największa):** floor pre-shift jako CHOKEPOINT (frozen > floor > OSRM w JEDNYM miejscu) — zamyka parę klastrów i część floor-odbioru (17 powierzchni/4).
4. **Fala bliźniaków pozycji (8):** plan istnieje (xfail śledzi), K-E intencja rozstrzygnięta C3.
5. **Route-order kroki 6-7 z F_poc_plan D.4 (HARD, osobny pod-ACK):** P2 pełna ekstrakcja rdzenia kanonu z plan_recheck + P3 drugi producent `_save_plan_on_assign` (+guard (0,0)) — jedyne co ZOSTAŁO z rodziny route-order; do tego Kotlin golden-export (off-host).
6. **#3 lane oracle przyrządów:** osobna duża szychta (49 przyrządów) — planować jako samodzielny audyt.

Baseline'y #1/#2/#5/#6/#8 w `tools/entropy_dashboard.py` ZAKTUALIZOWANE 18.07 (ten commit);
sekcja AUTO nietknięta (night-guard parsuje bajt-zgodnie — zweryfikowane biegiem).

## DoD — tokeny (fala ①: weryfikacja + prawda przyrządów; zero zmian silnika)

regresja: 5188 passed / 0 failed / 27 skipped / 8 xfailed (EXIT=0; pełna suita PO edycji dashboardu) + celowane strażniki route-order 16/16 (golden+unify+live-parity, zero skipów) + konsumenci dashboardu test_s7+flag_registry_f3 16/16
e2e: test_route_order_live_parity = one-shot POD VENVEM PANELU na żywych workach (pełna ścieżka konsola↔kanon) — PASSED w biegu weryfikacyjnym
replay: N-D — zero zmian zachowania (edytowane wyłącznie teksty wierszy [AUDIT-BASELINE] przyrządu + doc rejestru); dowód nie-regresji przyrządu: AUTO sekcja bajt-zgodna (regexy night_guard sprawdzone na żywym biegu)
rollback: git revert (tools/entropy_dashboard.py + ZIOMEK_ARCHITECTURE.md + eod_drafts — addytywne/tekstowe)

N-D: plan_recheck.py / route_order.py / fleet_state.py (panel) — NIETKNIĘTE (fala okazała się weryfikacją istniejącej siatki; kroki 6-7 HARD = osobny pod-ACK)
N-D: tools/night_guard.py — parser AUTO niezmieniony, format wyjścia bajt-zgodny (zweryfikowane)
