# PERF_TAIL_DIAGNOSIS — dekompozycja ogona p95 (Sprint 2.5-PREP tmux18, Zadanie 1)

> 2026-07-05 ~19:15 UTC. READ-ONLY (zero zmian w kodzie produkcyjnym; profil = offline replay `perf_lazy_harness`,
> `nice -19`, PO peaku sobotnim). Kontynuacja werdyktu at-207 (`2026-07-03/perf_verdict_at207.md`): po flipie
> PERF_LAZY p50 −27%, ale „peak p95 1810 ≈ baseline 1847 → ogon w peaku ma inne źródło niż IO". To jest ta diagnoza.

## 0. TL;DR — gdzie siedzi ogon

**Ogon >1500 ms to NIE „za dużo kandydatów/za duże worki" i NIE sieć panelu. To złożenie dwóch rzeczy:**
1. **KONTENCJA CPU na 4 vCPU** (współlokatorzy + własna nadsubskrypcja wątków): ta sama decyzja liczona offline na
   pustej maszynie ma **p50 313 ms / p95 492 ms**; na żywo w godzinach ruchu p50 ~790 / p95 ~1740 (×2,5-3,5).
   Ogon skaluje z godziną obciążenia (off-peak 4,9% → peak 11,0% → high-risk 14-17 **15,0%**, godz. 17 Warsaw
   **28,6%**), a NIE z rozmiarem decyzji (Spearman lat↔pool_total 0,18; lat↔n_bag≥2 0,12; decyzje pool 0-2 też
   łapią 1,8-2,7 s). 5 ciężkich timerów shadow (reassign-global-select / reassignment-shadow / pred-calibration
   co 3 min + b-route-shadow / bundle-calib-shadow co 5 min) odpala się **W TEJ SAMEJ SEKUNDZIE** (zero
   RandomizedDelaySec, AccuracySec default 1 min koalescencja) — burst OR-Tools/OSRM co 3-5 min obok żywych decyzji;
   panel-watcher ma skumulowane CPU **5× większe niż sam shadow** (3201 s vs 658 s).
2. **BAZA COMPUTE PODATNA NA INFLACJĘ: ~61% czasu decyzji = ścieżka TSP**, a w niej dominują **callbacki Pythona
   per-łuk** — na 226 decyzji profilu: 53,7 mln wywołań `RoutingIndexManager.IndexToNode` (21,0 s self),
   19 mln `time_cb` (33,4 s cum), 8 mln `dist_cb` (22,0 s cum); `_solve` cum 44,0 s / 72,4 s całości.
   610 solve'ów / 226 decyzji = **~2,7 solve/decyzję**. Pod kontencją każdy taki solve rozciąga się wielokrotnie
   (GIL + wywłaszczenia), stąd luki 1-2,3 s na ewaluacji JEDNEGO kandydata widoczne w journalu.

## 1. Metodologia i okna

- **Żywe logi (odczyt):** `shadow_decisions` przez `ledger_io` (rotation-aware); okno post-flip PERF_LAZY
  **03.07 00:30 → 05.07 17:40 UTC, n=670** (54% peaku wg SLO §5a: 11-14+17-20 Warsaw). Kontrola 14 dni (n=2071)
  dla trendu. Ogon = latency_ms > 1500 (próg SLO peak-p95).
- **Atrybucja luk:** journald `dispatch-shadow` post-flip (12 769 linii); dla 76 wolnych decyzji (>1500 ms) okno
  [t_end−latency, t_end], luki >200 ms między liniami sklasyfikowane po linii ZAMYKAJĄCEJ lukę.
- **Profil offline:** `tools/perf_lazy_harness profile --n 120 --repeats 2 --fleet 10` (replay realnych NEW_ORDER
  z events.db, syntetyczna deterministyczna flota, OSRM :5001 realnie, pre-proposal recheck OFF w harnessie) —
  pełny wynik: `perf_profile_offline_120x2.txt`.
- Skrypty analityczne (deterministyczne, scratchpad sesji): `perf_tail_analysis.py`, `perf_gap_attribution.py`.

## 2. Liczby

### 2.1 Segmenty SLO, okno post-flip (n=670)
| segment | n | p50 | p95 | ogon >1500 ms |
|---|---|---|---|---|
| peak (11-14+17-20 W) | 354 | 790 | 1738 | **11,0%** |
| high-risk (14-17 W) | 214 | 915 | **1977** | **15,0%** |
| off-peak | 102 | 556 | 1384 | 4,9% |

Per godzina (Warsaw): 09-11 ogon ~0%; 12→5,9%; 13→9,1%; 14→11,4%; 15→**18,8%**; 16→14,7%; 17→**28,6%**;
18→7,0%; 19→8,9%; 20→7,5%. **Najgorsze są godziny 15-17 — formalnie POZA segmentem „peak" SLO.**

### 2.2 Ogon vs cechy decyzji (peak, post-flip)
- TAIL (n=39) vs BODY (n=315): pool_total med 12 vs 11; n_bag≥2 med 2 vs 2; max_bag med 2 vs 2 — **profil
  ogona ≈ profil ciała**. Spearman: pool_total 0,18 / n_bag≥2 0,12 / pool_feasible 0,03.
- Decyzje z n_bag≥2=0 (zero wywołań OR-Tools na kandydatach z workiem): ogon 9,4%, p95 1647 — **ogon istnieje
  bez OR-Tools**, więc sam solver nie wystarcza za wyjaśnienie; wolne bywa wszystko (scoring, OSRM, GC, I/O logów)
  gdy CPU wysycone.
- `r07_compute_latency_ms` med ~5 ms; `degraded_osrm` 0/670 — R-07 i degradacja OSRM niewinne.

### 2.3 Atrybucja luk w 76 wolnych decyzjach (journald)
| klasa linii zamykającej lukę >200 ms | udział czasu luk |
|---|---|
| ewaluacja kandydata — scoring/veto/rampa (`V326_WAVE_VETO`/`V325_NEW_COURIER`/`SP-B2`) | 53,9% |
| „other" = w większości też ewaluacja (`FIX_C bundle_cap`, `BUNDLE_DELIV_COLOC`, event_bus AUDIT) | 34,9% |
| R-07 chain | 4,6% |
| panel fetch_order_details (Timeout 2 s — `V327_PRE_PROPOSAL_RECHECK_FETCH_TIMEOUT_SEC`) | 2,3% |
| OR-Tools linie jawne / LGBM / login / inne | ~4,3% |

→ **~85-90% czasu ogona = luki kończące się na liniach ewaluacji POJEDYNCZEGO kandydata (0,3-2,3 s/kandydat).**
Sieć panelu w ścieżce decyzji = margines (6 timeoutów fetch w 2,7 dnia; login refresh działa w tle
`panel_bg_refresh` co ~15 min — YELLOW z V3.27.1 już rozwiązany).

### 2.4 Profil offline (baza compute, bez kontencji)
- **WALL n=226: p50 313 ms / p95 492 ms / mean 321 ms** (żywo peak: 790/1738 → **inflacja ×2,5/×3,5**).
- Z 72,4 s CPU: ścieżka TSP (`route_simulator_v2._solve` cum) **44,0 s ≈ 61%**; w tym callbacki Pythona per-łuk
  (`IndexToNode` 53,7 mln = 21,0 s self; `time_cb` 19 mln; `dist_cb` 8 mln) — to jest ~80% kosztu TSP,
  sam solver (`SolveWithParameters` self) tylko 3,2 s. **2,7 solve/decyzję** (610/226).
- Reszta: zone-mapping 1,6 s; LGBM 1,0 s; fsync 0,7 s; OSRM offline ~0 (cache; żywo hit-rate 67,5%,
  ~5 decomposed calls/decyzję × p50 11 ms → <100 ms — nie ogon).

### 2.5 Współlokatorzy CPU (4 vCPU, load ~3,7 w peaku, 81 timerów dispatch/nadajesz)
- **5 ciężkich shadow-jobów zsynchronizowanych co do sekundy** (obserwowane odpalenia 17:58:06.000 ×3 i
  17:57:16.000 ×2): `reassign-global-select`+`reassignment-shadow`+`pred-calibration` (OnUnitActiveSec=3min)
  oraz `b-route-shadow`+`bundle-calib-shadow` (5min). Wszystkie robią realne przeliczenia klasy assess_order.
  **Żaden nie ma RandomizedDelaySec.** Co 15 min wszystkie 5 naraz.
- CPU skumulowane: panel-watcher 3201 s, shadow 658 s, nadajesz-panel 335 s (+OSRM docker, postgres, courier-api).

## 3. Hipotezy naprawcze (2-3 + bonus) z szacunkiem zysku

| # | co | zysk (szacunek) | koszt/ryzyko |
|---|---|---|---|
| **H1** | **Desynchronizacja i dławienie współlokatorów**: `RandomizedDelaySec=45-90s` na 5 shadow-timerach + `Nice=`/`CPUWeight=` (shadow-joby niżej, dispatch-shadow wyżej) + ew. przesunięcie najcięższych poza 12-20 W | ogon peak/high 11-15% → okolice off-peak ~5%; p95 peak ku ~1400 | **ops-only, ZERO kodu silnika**; drop-iny za ACK; ryzyko niskie (shadow-joby są measurement-only, opóźnienie 90 s nieistotne) |
| **H2** | **TSP bez callbacków Pythona**: prekomputowana macierz int → `RegisterTransitMatrix`/`RegisterTransitCallback` na gotowej tablicy w `tsp_solver.py` (dziś `time_cb`/`dist_cb` per-łuk w Pythonie) | −40-60% kosztu ścieżki TSP ⇒ baza p50 313→~200 ms; mniejsza podatność na kontencję (krócej w solverze) | fala rdzenia (protokół #0): wymaga **bajt-parytetu planów** na replayu (tryb `parity` harnessu już istnieje); średnie |
| **H3** | **Nadsubskrypcja wątków**: ThreadPoolExecutor 10 workerów na 4 vCPU (lekcja #27: efficiency 13,4%) → workers=min(4, nproc) + pomiar | mniejsza inflacja wall-time per kandydat w peaku; measure-first (replay + 1 dzień live) | 1 stała; ryzyko: wydłużenie p50 przy dużych poolach — dlatego pomiar przed/po |
| H4 (bonus) | 2,7 solve/decyzję — memoizacja/warm-start identycznych podproblemów kandydatów | umiarkowany | po H2, niżej |

**Rekomendacja dla fali perf (Sprint 3 / P4):** kolejność **H1 → pomiar tygodnia → H2 → H3**. H1 jest tanie,
bez dotykania silnika i adresuje bezpośrednio zmierzoną naturę ogona (load-dependence). H2 to właściwa fala
„compute-zawsze" rdzenia — największy trwały zysk bazy. Dodatkowo: **segmentacja SLO do rewizji** — godziny
15-17 Warsaw (high-risk) mają DZIŚ gorsze p95 (1977) niż oficjalny peak; alert SLO ich nie bramkuje.

## 4. Zastrzeżenia
- Profil offline: syntetyczna flota (10), recheck panelu wyłączony w harnessie, OSRM na ciepłym cache —
  liczby bazy są DOLNĄ granicą; dekompozycja procentowa TSP/inne wiarygodna.
- Atrybucja luk klasyfikuje linię ZAMYKAJĄCĄ lukę — wewnątrz luki nie widać, czy wisiał solver, OSRM czy scheduler
  (to rozstrzyga dopiero profil + load-dependence).
- n=670 (2,7 dnia post-flip); godziny 15-17 częściowo z soboty (inny profil ruchu). Re-run tabel §2.1 po pełnym
  tygodniu: `perf_tail_analysis.py` (scratchpad) lub `tools/perf_budget_report.py --since 2026-07-03T00:30`.
- Werdykt at-207 „off-peak p95 765" był na n=18 — na n=102 post-flip off-peak p95 = 1384 (nadal najlepszy segment,
  ale tamta liczba była optymistyczna przez małą próbę).
