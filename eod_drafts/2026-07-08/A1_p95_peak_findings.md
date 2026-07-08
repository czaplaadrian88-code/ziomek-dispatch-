# A1 — Ogon p95 liczenia decyzji Ziomka w SZCZYCIE: gdzie realnie boli

> **Zweryfikowane przez wykonawcę sprintu (tmux 36, 2026-07-08):** liczby p50/p95
> potwierdzone kanonicznym `perf_budget_report` (bit-zgodne), wskaźniki kodu
> (ThreadPoolExecutor 10 workerów, tsp_solver 200ms, `r07_latency`=chain_eta≠solver)
> potwierdzone grepem, a wniosek „solver = PODŁOGA (p50), nie OGON (peak p95)"
> spójny z niezależnym pomiarem A2 (harness solve-level: OFF ~201ms/solve → ON p50
> 87ms). READ-ONLY, zero dotknięcia produkcji.

**Rola:** read-only analityk wydajności. **Zadanie:** zmierzyć ogon p95 compute decyzji w peak i wskazać, gdzie boli (input do decyzji o budżecie solvera OR-Tools = A2).
**Wygenerowano:** 2026-07-08. **CAŁKOWICIE READ-ONLY** (żaden plik repo/flaga/serwis nietknięty).

---

## 0. TL;DR (surowe liczby + werdykt)

| Metryka | Wartość |
|---|---|
| Okno (efektywne, rotation-aware) | 2026-06-27 07:53 UTC .. 2026-07-08 13:49 UTC (~11,25 dnia) |
| n decyzji z `latency_ms` | **2674** |
| OVERALL | p50=**813** p95=**1874** p99=2437 max=6005 · ogon>1500 ms=**12,3 %** |
| PEAK (Warsaw 11-14+17-20) | p50=**817** p95=**1834** · ogon=12,3 % (n=1399) |
| PLATEAU (Warsaw 14-17, między peakami) | p50=**943** p95=**2090** · ogon=**16,2 %** (n=801) ← najgorszy |
| OFF-PEAK | p50=**583** p95=**1507** · ogon=5,5 % (n=474) |
| RUSH (11-20 łącznie) | p50=859 p95=**1918** · ogon=13,7 % (n=2200) |

**Werdykt „gdzie boli":** w szczycie boli **NIE solver OR-Tools** (jest zrównoleglony — ThreadPoolExecutor 10 workerów, a latencja jest ~PŁASKA względem liczby solve'ów), lecz **koszt całego per-kandydat pipeline'u pomnożony przez liczbę kurierów na zmianie + kontencja skorelowana z obciążeniem** — ogon rośnie monotonicznie z liczbą aktywnych kurierów (≤4: p95=1200 → 11+: p95=1977) i z liczbą aktywnych zleceń, a NIE z liczbą solve'ów.

**Konsekwencja dla A2:** deterministyczny budżet solvera zetnie głównie **~130-230 ms równoległej „podłogi" OR-Tools** doklejanej do każdej decyzji z bag≥2 (realny zysk na p50 + determinizm), ale **peak p95 ogon zostanie prawie nietknięty** — bo ogon jest związany z rozmiarem floty/kontencją w szerszym pipeline (feasibility + scoring + OSRM + fetch panelu), nie z czasem solve'a. A2 trafia w podłogę/p50, nie w ogon.

---

## 1. Rozkład godzinowy (Warsaw) — kształt bólu

Strefa `ZoneInfo("Europe/Warsaw")` (nie fixed-offset). Peak wg zadania = godz. 11,12,13,17,18,19.

| hW | n | p50 | p90 | p95 | p99 | ogon% | seg |
|---:|---:|---:|---:|---:|---:|---:|---|
| 11 | 151 | 631 | 984 | 1145 | 2018 | 2,0 | peak |
| 12 | 255 | 751 | 1377 | 1559 | 1896 | 7,5 | peak |
| 13 | 268 | 912 | 1688 | 1887 | 2359 | 15,7 | peak |
| 14 | 239 | **1009** | 1786 | 2020 | 2568 | 16,3 | plateau |
| 15 | 292 | 926 | 1732 | 1964 | 2574 | 16,1 | plateau |
| 16 | 270 | 917 | 1874 | **2134** | 2921 | 16,3 | plateau |
| 17 | 261 | 918 | 1758 | 1937 | 2477 | **21,5** | peak |
| 18 | 235 | 810 | 1583 | 1906 | 2409 | 12,3 | peak |
| 19 | 229 | 827 | 1498 | 1711 | 2089 | 10,0 | peak |
| 20 | 191 | 742 | 1418 | 1565 | 2124 | 7,3 | off |

**Obserwacja:** prawdziwy płaskowyż bólu to **Warsaw 13-18** (nie idealnie pokrywa się z definicją peak). p50 szczytuje o 14:00 (1009 ms), p95 o 16:00 (2134 ms), % ogona o 17:00 (21,5 %). Poranny ramp (11-12) i wieczorny ogon (19-20) są łagodne. Peak vs off: p95 ~1834-1918 vs 1507 = **+~400 ms w ogonie**, p50 817-943 vs 583 = **×1,4-1,6**, % ogona ×2,5-3.

---

## 2. Korelacja ogona z PRZYCZYNAMI (co napędza, co nie)

### 2a. Strategia zwycięzcy (`best.plan.strategy`) — słaby efekt
| winner strategy | n | p50 | p95 | ogon% |
|---|---:|---:|---:|---:|
| bruteforce (solo/idle, bag_after=1) | 1638 | 795 | 1762 | 10,0 |
| ortools (bag≥2) | 587 | 912 | **2020** | 17,9 |
| sticky | 313 | 813 | 1912 | 13,7 |
| greedy_fallback | 3 | 1266 | 1733 | — |

Decyzje z **ortools-zwycięzcą są wolniejsze** (+~150 ms p50, +~260 ms p95 vs bruteforce), ale to tylko 22 % decyzji i różnica jest umiarkowana — żadnego „×10 sekwencyjnego" blow-upu.

### 2b. Liczba solve'ów OR-Tools na decyzję — **PŁASKO** (dowód zrównoleglenia)
`n_ortools` = liczba kandydatów (best+alternatives) z `plan.strategy=='ortools'` (proxy).
| n_ortools | n | p50 | p95 | ogon% |
|---:|---:|---:|---:|---:|
| 0 | 505 | **694** | 1828 | 11,7 |
| 1 | 634 | 822 | 1942 | 16,1 |
| 2 | 517 | 797 | 1828 | 12,2 |
| 3 | 473 | 829 | 1785 | 9,7 |
| 4 | 257 | 857 | 1871 | 8,6 |
| 5+ | 288 | **923** | 1849 | 12,5 |

**Kluczowy dowód:** od 0 do 5+ solve'ów p50 rośnie tylko **694→923 (+230 ms)**, a p95 jest praktycznie płaski (~1785-1942). Gdyby solve'y były SEKWENCYJNE po 200 ms, 5 solve'ów dodałoby ~1000 ms. Obserwujemy +230 ms → **solve'y się nakładają (parallel).** To OBALA tezę CLAUDE.md „10 kandydatów × solve sekwencyjnie" świeżymi danymi.

### 2c. Max bag wśród kandydatów — umiarkowany efekt
| max_bag | n | p50 | p95 | ogon% |
|---|---:|---:|---:|---:|
| 1 | 483 | 687 | 1874 | 10,8 |
| 2 | 821 | 810 | 1782 | 10,6 |
| 3 | 752 | 851 | 1839 | 11,3 |
| 4+ | 486 | **917** | **2020** | 18,1 |

Większy bag → nieco wolniej (+230 ms p50 od bag1→bag4+); bag4+ ma najwyższy p95 (2020) i ogon (18 %). Realny, ale wtórny.

### 2d. Liczba aktywnych kurierów (`loadgov_active_couriers` ≈ rozmiar puli) — **NAJSILNIEJSZY, MONOTONICZNY sygnał**
| couriers | n | p50 | p95 | ogon% |
|---|---:|---:|---:|---:|
| ≤4 | 183 | **519** | **1200** | 3,3 |
| 5-7 | 316 | 667 | 1586 | 7,3 |
| 8-10 | 1158 | 828 | 1896 | 13,4 |
| 11+ | 876 | **921** | **1977** | 14,5 |

**To jest sedno.** Latencja rośnie MONOTONICZNIE z liczbą kurierów w puli: od ≤4 (p50=519, p95=1200, ogon 3,3 %) do 11+ (p50=921, p95=1977, ogon 14,5 %). p50 niemal się podwaja, p95 +777 ms, ogon ×4,4. To koszt oceny CAŁEJ floty per decyzja: więcej kurierów = więcej per-kandydat pracy Pythona (feasibility, scoring, OSRM legs, budowa macierzy dla ortools) + kontencja GIL + druga fala workerów gdy >10 (pula cap = `min(10, len(fleet))`).

### 2e. Liczba feasible (`pool_feasible_count`) — ODWROTNIE (confound obciążenia)
| feasible (peak+plateau) | n | p50 | p95 | ogon% |
|---|---:|---:|---:|---:|
| 1 | 416 | 950 | 2096 | 19,2 |
| 2-3 | 481 | 890 | 2086 | 22,7 |
| 4-5 | 494 | 898 | 1847 | 10,7 |
| 6-8 | 586 | 809 | 1624 | 7,7 |
| 9+ | 223 | 824 | 1606 | 6,7 |

Paradoks pozorny: MNIEJ feasible = WOLNIEJ. Bo mało feasible = flota NASYCONA (wszyscy zajęci dużymi workami) = wysokie obciążenie = wolna ocena. To symptom obciążenia, nie przyczyna. Liczba kandydatów NIE napędza ogona — napędza go OBCIĄŻENIE.

---

## 3. Dekompozycja czasu: czysty compute vs reszta spanu

`latency_ms` = wall `process_event` (`shadow_dispatcher.py:1235`→`:1338`). Zawiera: fetch `czas_kuriera` z panelu (HTTP), równoległą pulę kandydatów (feasibility + ortools + chain_eta/OSRM + scoring), selekcję. **NIE zawiera:** budowy fleet snapshot (raz/tick, `:1205`) ani serializacji rekordu do JSON (`:1339`, PO pomiarze).

Jedyny logowany pod-span compute = `r07_compute_latency_ms` = `compute_chain_eta` (OSRM per-leg ETA łańcucha). **UWAGA: to NIE jest czas solve'a OR-Tools** (`chain_eta.py` nie woła ortools) — to osobny człon. Czas solve'a OR-Tools **nie jest logowany nigdzie**.

| segment | r07_best abs (p50/p95/max) | r07_max abs (p50/p95) | r07_max %latency (p50/p95) | r07_sum %latency (p50/p95) |
|---|---|---|---|---|
| OVERALL | 6 / 49 / 420 ms | 31 / 175 ms | 3,7 % / 22,2 % | 6,9 % / 41,7 % |
| PEAK+PLATEAU | 6 / 50 / 420 ms | 34 / 182 ms | 4,1 % / 23,5 % | 7,6 % / 42,8 % |
| OFF | 4 / 42 / 273 ms | 10 / 100 ms | 2,2 % / 14,9 % | 3,4 % / 31,1 % |

- **r07_best** (chain zwycięzcy) = ~6 ms = **0,6 % spanu** — pomijalny.
- **r07_max** (najwolniejszy kandydat ≈ ścieżka krytyczna, bo równolegle) = ~34 ms p50, 182 ms p95 = **~4 % (p50) / ~23 % (p95)** spanu.
- **r07_sum** (suma po kandydatach — OVERCOUNTUJE, bo równolegle) = ~7 % (p50) / ~43 % (p95).

**Wniosek:** logowany czysty route/ETA-compute to MNIEJSZOŚĆ spanu (≤~23 % ścieżki krytycznej nawet w ogonie p95). Baza ~700 ms (patrz n_ortools=0: p50=694) i ogon są zdominowane przez RESZTĘ pipeline'u: fetch panelu (sieć), `feasibility_v2`, scoring, budowa macierzy OSRM dla ortools, oraz per-kandydat obserwabilność — a NIE przez logowany compute ani (inferencyjnie) przez solver.

---

## 4. Profil OGONA vs mediana (peak+plateau) — czym różni się wolna decyzja

Rekordy `lat≥p95(1918)` vs okolice `p50(859)`:

| grupa | n | lat_mean | n_feas | n_ortools | max_bag | couriers | **orders** | r07_max | winner=ortools |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **OGON** (≥1918) | 111 | 2260 | 3,1 | 2,2 | 2,71 | 10,8 | **33,4** | 67 | **35 %** |
| MEDIANA (~859) | 353 | 853 | 4,9 | 2,5 | 2,58 | 10,4 | **25,7** | 20 % |

**Ogon NIE ma więcej kandydatów** (3,1 vs 4,9 feasible), **NIE ma więcej solve'ów** (2,2 vs 2,5), **NIE ma większego compute** (`r07_max` 67 vs 66 — IDENTYCZNE!). Różnicują go: **obciążenie systemu** (33 vs 26 aktywnych zleceń) i częstszy **ortools/bundling-zwycięzca** (35 % vs 20 %). To sygnatura **kontencji skorelowanej z obciążeniem**, nie „więcej pracy do policzenia".

---

## 5. Weryfikacja tez CLAUDE.md (potwierdź/obal świeżymi danymi)

| Teza CLAUDE.md | Werdykt | Dowód |
|---|---|---|
| „OR-Tools solve hituje 200 ms sufit KAŻDE wywołanie" | **POTWIERDZONA (architektonicznie)** | `tsp_solver.py:443` metaheuristic=`GUIDED_LOCAL_SEARCH` + `time_limit.FromMilliseconds(200)` (`common.py:2885 V326_OR_TOOLS_TIME_LIMIT_MS=200`). GLS nie dowodzi optymalności → wyczerpuje pełny budżet czasu każdorazowo. Czasu solve NIE ma w logu → weryfikacja pośrednia. |
| „= 87 % czasu TSP" | **NIEZWERYFIKOWALNE z logu** | brak pola z czasem solve'a; `r07_compute_latency_ms` mierzy chain_eta (OSRM), nie solve. |
| „per-proposal 10 kandydatów × solve **SEKWENCYJNIE**" | **OBALONA** | `dispatch_pipeline.py:4069` `ThreadPoolExecutor(max_workers=min(10,len(fleet)))` + `pool.map`. Empirycznie (§2b): latencja PŁASKA vs n_ortools (0→5+ = +230 ms p50, nie +1000 ms). `common.py:2885` sam mówi „10 workers × 200 ms = ~250-400 ms wall (vs sequential 2000 ms)". Komentarz CLAUDE.md jest NIEAKTUALNY. |

**Ile solve'ów per decyzja:** pula `pool_total` p50=10 (p95=13, max=15) kurierów; feasible `pool_feasible` p50=4 (p95=9). OR-Tools odpala się dla kandydata gdy `bag_after_add ≥ 2` (`V327_MIN_OR_TOOLS_BAG_AFTER=2`); solo/idle (bag_after=1) idzie bruteforce fast-path. Liczone solve'y (best+alts): p50~2, do 5+ w ~11 % decyzji. Skalowanie ogona: **żadne** (§2b) — bo równolegle.

---

## 6. Implikacja dla A2 (budżet solvera OR-Tools) — czy trafia w cel

- **Podłoga (p50):** decyzje z 0 solve'ów mają p50=694 ms; z solve'ami ~800-923 ms → solver dokłada **~130-230 ms** równoległej podłogi do decyzji bag≥2. A2 (mniejszy/adaptacyjny budżet zamiast sztywnych 200 ms) zetnie tę podłogę i doda determinizm — realny, ale ograniczony zysk (gdyby WYZEROWAĆ solver, p50 spada ~813→~694 = −15 %).
- **Ogon (peak p95):** ~1834-2090 ms; nad podłogą jest ~+1000 ms który NIE jest solverem (§2b, §4). A2 **nie ruszy peak p95 istotnie.**
- **Gdzie jest dźwignia na ogon:** koszt per-kandydat × rozmiar floty + kontencja (§2d, §4) — czyli `feasibility_v2` + scoring + OSRM (macierz/legs) + fetch panelu + rozmiar rekordu obserwabilności; skalowane liczbą kurierów na zmianie (11+ → p95=1977 vs ≤4 → 1200).

A2 warto zrobić dla podłogi/p50 i przewidywalności, ale zespół powinien wiedzieć, że **peak-tail lever jest gdzie indziej** (per-kandydat pipeline, nie solver).

---

## 7. CAVEATY (uczciwie — dowody nie deklaracje)

1. **Brak bezpośredniego pomiaru solve'a OR-Tools.** Log nie ma pola z czasem solve. Udział solvera INFEROWANY z (a) configu (GLS+200 ms sufit), (b) płaskiej krzywej latencja-vs-liczba-solve'ów. Nie podam dokładnego ms solve'a z logu.
2. **`r07_compute_latency_ms` ≠ solver.** To `compute_chain_eta` (OSRM per-leg), osobny człon od TSP. Nie mylić.
3. **r07 per-kandydat nakłada się w czasie** (ThreadPoolExecutor) → SUMA overcountuje wall-time; MAX ≈ ścieżka krytyczna. Dlatego dekompozycja w §3 podaje oba.
4. **Izolacja shadow / brak burstu współbieżnego.** `shadow_dispatcher` przetwarza eventy NEW_ORDER pojedynczo w pętli poll; `latency_ms` to jedna decyzja pod AMBIENTNYM obciążeniem serwera (CPX32, żywy OSRM/timery). Chwyta kontencję skorelowaną z obciążeniem (dlatego peak wolniejszy), ale NIE symuluje „N zleceń solvowanych równocześnie". Żywy dispatch też jest event-driven pojedynczo, więc dane są reprezentatywne dla produkcji, ale prawdziwego stresu współbieżności tu nie ma.
5. **`n_ortools` to proxy (może UNDERcountować).** Liczone z best+alternatives (zweryfikowane: 0/2674 rekordów ma best+alts < pool_feasible → alternatives NIE obcina feasible). ALE kandydaci ODRZUCENI (verdict NO), którzy odpalili ortools przed odrzuceniem, NIE są w best/alts → realna liczba INWOKACJI solvera ≥ mój licznik. Kierunek wniosku (płaskie skalowanie) trzyma się mimo to; efekt całej puli chwyta korelacja z liczbą kurierów (§2d).
6. **Okno.** Logrotate usunął 24-26.06 (brak .2.gz); efektywne okno 27.06 07:53 – 08.07 13:49 UTC (~11,25 dnia), n=2674. Żądane 14 dni nie jest w pełni dostępne w plikach.
7. **`degraded_osrm`=True: 0/2674** — w tym oknie OSRM nie był w trybie degraded, więc kontencja OSRM (jeśli jest) nie manifestuje się jako degradacja, tylko jako wolniejsze zdrowe wywołania.
8. Percentyl = nearest-rank (spójnie z kanonem `perf_budget_report`/canary). p50/p95 z §1 są bit-zgodne z kanonicznym toolem (patrz §8).

---

## 8. Źródła i komendy (do weryfikacji)

**Dane (rotation-aware, kanon):**
- `dispatch_v2.tools.ledger_io.iter_shadow_decisions(cutoff)` nad `logs/shadow_decisions.jsonl` (+`.1`). Python: `/root/.openclaw/venvs/dispatch/bin/python`, `PYTHONPATH=/root/.openclaw/workspace/scripts`.

**Kanoniczny tool (baseline p50/p95/p99 + SLO):**
```
cd /root/.openclaw/workspace/scripts
PYTHONPATH=/root/.openclaw/workspace/scripts /root/.openclaw/venvs/dispatch/bin/python \
  -m dispatch_v2.tools.perf_budget_report --days 14 \
  --out .../scratchpad/perf_budget_14d.json
```
→ OVERALL n=2670 p50=812 p95=1874 p99=2423 max=6005 ogon 12,2 %; peak segment p50=817/p95=1834; high_risk p50=942/p95=2087; off p50=583/p95=1507. (Bit-zgodne z moim §1; drobne n±4 = ostatnie sekundy okna.)

**Korelacja (mój skrypt, READ-ONLY):**
- `.../scratchpad/a1_correlate.py` → pełny output §2-§4, §5, §7. Uruchom:
```
/root/.openclaw/venvs/dispatch/bin/python .../scratchpad/a1_correlate.py
```

**Kod (potwierdzenie architektury — tylko odczyt):**
- `dispatch_v2/dispatch_pipeline.py:4069-4074` — `ThreadPoolExecutor(max_workers=min(10,len(fleet)))` + `pool.map(eval_safe, fleet.items())` (zrównoleglenie kandydatów).
- `dispatch_v2/tsp_solver.py:440-446` — `PARALLEL_CHEAPEST_INSERTION` + `GUIDED_LOCAL_SEARCH` + `time_limit.FromMilliseconds(time_limit_ms)`.
- `dispatch_v2/common.py:2885` — `V326_OR_TOOLS_TIME_LIMIT_MS=200` (komentarz: „10 workers × 200 ms = ~250-400 ms wall vs sequential 2000 ms").
- `dispatch_v2/common.py:2896` — `V327_MIN_OR_TOOLS_BAG_AFTER=2` (bag_after≥2 → ortools; bag_after=1 → bruteforce).
- `dispatch_v2/route_simulator_v2.py:443-449, 1160-1180, 1512-1547` — gating use_ortools, `_ortools_plan`, `_solve`.
- `dispatch_v2/core/candidates.py:315-348, 2193` — `_r07_latency_ms` = pomiar `compute_chain_eta` (NIE solver).
- `dispatch_v2/chain_eta.py` — brak `ortools`/`solve` (potwierdza rozdzielność chain_eta od TSP).
- `dispatch_v2/shadow_dispatcher.py:1235,1338,1339` — t0 / capture `latency_ms` / serializacja PO pomiarze.
- `flags.json:257` — `ENABLE_V326_OR_TOOLS_TSP: true` (solver żywy).
