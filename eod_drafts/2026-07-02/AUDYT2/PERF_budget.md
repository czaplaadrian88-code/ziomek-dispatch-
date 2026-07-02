# PERF_budget — Budżet wydajności Ziomka (AUDYT 2.0, lane BUDŻET WYDAJNOŚCI)

**Data analizy:** 2026-07-01 (wieczór) · **Tryb:** READ-ONLY wobec produkcji (zero edycji/restartów/flag).
**Cel:** wyjaśnić regres p50 (838 ms teraz vs ~375 ms w kwietniu) i zaproponować SLO + budżet per warstwa.

**Źródła danych (odczyt):**
- `scripts/logs/shadow_decisions.jsonl` (65 MB, 2026-06-27→07-01) + `.jsonl.1` (113 MB, 2026-06-18→06-26) — **3535 rekordów decyzji, 14 dni**. Brak starszych `.gz` (logrotate: `delaycompress`, tylko `.1`).
- `journalctl -u dispatch-shadow` · `scripts/logs/dispatch.log` · `git log` w `dispatch_v2` (odczyt) · `sar`/`uptime`/`free`.
- Baseline kwietniowy: `CLAUDE.md` tabela latency (p50 375 ms **na CPX22 2 vCPU**, cel po CPX32 = 150–200 ms).

**Walidacja metody:** moja ekstrakcja ostatnich 2000 rek. = **p50 841 / p95 1906 / max 6005 ms** — zgodna z pomiarem team-leada (838 / 1880 / 6005). Skrypt: `scratchpad/perf_extract.py` (strumieniowy, `os.nice(10)`), dane surowe `scratchpad/perf_rows.csv`.

---

## 0. TL;DR

1. **Regres jest strukturalny, nie „jeden commit".** p50 jest **stałe ~800 ms przez całe 14 dni** (brak in-window breakpointu). Regres wydarzył się **PRZED oknem danych** (06-18 już p50 846). Względem kwietnia (375 ms na 2 vCPU) mamy **>2× gorzej mimo 2× mocniejszego sprzętu** (CPX32, 4 vCPU) — nowy compute zjadł cały zysk z upgrade'u i jeszcze więcej.
2. **~76% budżetu to STAŁY narzut per-decyzja (~665 ms), niezależny od liczby kandydatów** (dowód: latencja płaska względem `pool_feasible`, a przy `pool_total=0` nadal ~780 ms). To **compute CPU-bound wewnątrz `assess_order`**, nie I/O (login jest zbackgroundowany, OSRM cache'owany, pliki stanu module-cached).
3. **Przyczyna = akrecja obiektywów/„shadow" liczonych ZAWSZE (nawet przy fladze OFF).** 227 commitów shadow/obj od upgrade'u (73 jawnie „flaga OFF / shadow-first / log-only"); fala 2026-06-12→24 (`obj-food-age` w celu OR-Tools, `r6-breach-shadow`×3, `pln_v na całej puli`, `objm-lexr6`, `best_effort`×N, `two-model ML`, `reserve-aware`). Słowo „shadow" pada **242×** w `dispatch_pipeline.py` (7085 LOC).

**TOP-3 zjadacze budżetu** (z liczbami):
| # | Zjadacz | Udział p50 | Dowód |
|---|---|---|---|
| 1 | **Stały narzut per-decyzja = akrecja shadow/obj „compute-zawsze"** | **~665 ms (76%)** | fit `reszta = 667 + 23×pool_total`; płaska latencja vs `pool_feasible`; 227 commitów obj/shadow, 73 flag-OFF |
| 2 | **Kontencja CPU w peak (4 vCPU)** | ogon: peak p50 ~1000–1100 vs off-peak ~500–700 | udział `>=1500 ms` rośnie **2%→19%** od 9h do 16h (Warsaw) przy podobnym `pool` |
| 3 | **Koszt per-kurier = pełny per-candidate eval** | **~35 ms × pool_total** (peak pool 12–14 → ~420–490 ms) | fit `latency = 665 + 34.6×pool_total`; OSRM+route_sim+scoring+lgbm×2+r07 per kandydat |

**Proponowane SLO (per decyzja, mierzone z `latency_ms`):** peak 11–20h → **p50 ≤ 700 ms, p95 ≤ 1500 ms**; off-peak → **p50 ≤ 450 ms, p95 ≤ 900 ms**; twardy sufit alertu → **pojedyncza decyzja > 3000 ms** lub **p95 okna 30-min > 2500 ms**. Cel budżetowy warstw: fixed ≤ 350 ms, per-kurier ≤ 25 ms.

---

## 1. TREND DZIENNY

Percentyle `latency_ms` per data (Warsaw). `medReszta` = mediana `latency − Σr07 − Σlgbm` (= „wszystko poza policzonym routingiem i ML").

| data | n | p50 | p95 | p99 | max | med `pool_feasible` | med Σr07 | med Reszta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-18 | 197 | 846 | 1725 | 2642 | 3271 | 7.0 | 102.6 | 671 |
| 2026-06-19 | 265 | 888 | 1752 | 2445 | 2799 | 4.0 | 67.1 | 778 |
| 2026-06-20 | 199 | 650 | 1373 | 2418 | 3131 | 4.0 | 36.4 | 586 |
| 2026-06-21 | 381 | **1082** | 2728 | 3682 | 4917 | 2.0 | 31.2 | 977 |
| 2026-06-22 | 195 | 692 | 1595 | 2216 | 2398 | 1.0 | 21.2 | 620 |
| 2026-06-23 | 301 | 735 | 1766 | 2078 | 2603 | 2.0 | 17.9 | 688 |
| 2026-06-24 | 232 | 730 | 1733 | 2603 | 3440 | 2.0 | 29.9 | 665 |
| 2026-06-25 | 258 | 781 | 1796 | 2432 | 3622 | 4.0 | 37.3 | 710 |
| 2026-06-26 | 296 | **1003** | 2276 | 2770 | 4902 | 2.0 | 66.3 | 868 |
| 2026-06-27 | 217 | 806 | 1504 | 1987 | 2484 | 5.0 | 89.4 | 686 |
| 2026-06-28 | 275 | **1049** | 2068 | 2425 | **6005** | 5.0 | 112.9 | 872 |
| 2026-06-29 | 234 | 776 | 1887 | 2596 | 2720 | 2.0 | 22.9 | 713 |
| 2026-06-30 | 231 | 693 | 1572 | 1951 | 2537 | 4.0 | 31.4 | 627 |
| 2026-07-01 | 254 | 800 | 2082 | 2386 | 2921 | 3.0 | 38.4 | 729 |

Po odsianiu rekordów „noc-pusto" (`pool_feasible==0`) obraz się **nie zmienia** (p50 846/900/677/1086/699/…): puste decyzje nie zaniżają ani nie zawyżają p50 — narzut jest ten sam.

**Wnioski:**
- **Brak in-window breakpointu.** Poziom p50 ≈ 650–1082 ms oscyluje wokół ~800 przez całe 14 dni. Dni podwyższone (06-21, 06-26, 06-28) to dni z wyższym wolumenem / peak-heavy — kontencja, nie nowy regres.
- **`medReszta` (586–977 ms) dominuje nad `Σr07` (18–113 ms) każdego dnia** — routing kandydatów to margines; budżet zjada „reszta".
- **Regres nastąpił przed 06-18.** Kwiecień (dokumentacja): 375 ms na 2 vCPU. 06-18: 846 ms na 4 vCPU. Bisekcji wewnątrz kwiecień→czerwiec nie da się zrobić z danych (brak logów < 06-18) — patrz JAWNE LUKI.

---

## 2. DEKOMPOZYCJA

### 2a. Pola latencji w rekordzie
| pole | znaczenie | typ. wartość (mediana) |
|---|---|---|
| `latency_ms` (top-level) | **cały span decyzji** `t0→po result` (`shadow_dispatcher.py:1212`) | ~840 ms |
| `best.r07_compute_latency_ms` | chain-ETA/route recompute najlepszego kandydata | ~5–10 ms |
| `alternatives[].r07_compute_latency_ms` | to samo per alternatywa (lista ≈ `pool_feasible`) | ~5 ms |
| `best.lgbm_shadow.{latency_ms,feature_compute_ms,inference_ms}` | LGBM model 1 (shadow) | 0–6 ms |
| `best.lgbm_twomodel_shadow.latency_ms` | LGBM two-model (06-20) | 1–4 ms |
| `decision_meta.{degraded_osrm,osrm_cache_age_s}` | stan OSRM (nie latencja, ale koreluje z ogonem) | cache 50–300 ms |

**Uwaga architektoniczna:** span `latency_ms` **NIE obejmuje** budowy floty ani loginu — `fleet = dispatchable_fleet()` liczone jest **raz przed pętlą** (`shadow_dispatcher.py:1113`), a `t0` startuje per-event (`:1139`). Span = geokod braków (rzadko) + `process_event` → `assess_order` (**czysta funkcja** nad gotową flotą) + shadow-probe + `pending_pool`. Zatem narzut = **compute w `assess_order`**, nie fetch.

### 2b. Rozkład per warstwa — regresja liniowa
Dopasowanie `reszta = a + b·pool_total` (n=3346 z niepustym `pool_total`):

```
reszta (lat − Σr07 − Σlgbm) = 667 ms + 23.3 ms · pool_total
latency_total                = 665 ms + 34.6 ms · pool_total
```
Przy medianie `pool_total = 9`: latencja ≈ 977 ms, z czego **stały narzut 665 ms = 76%**, per-kurier 300 ms = 24%.

### 2c. Latencja vs `pool_total` (rdzeń dowodu)
| pool_total | n | p50 | p95 | med Reszta | med Σr07 |
|---:|---:|---:|---:|---:|---:|
| 0 | 221 | **781** | 1975 | **781** | 0.0 |
| 1–2 | 42 | ~400 | ~1100 | ~380 | 4–6 |
| 5 | 105 | 679 | 1436 | 636 | 27.7 |
| 8 | 510 | 760 | 1773 | 698 | 31.1 |
| 10 | 410 | 795 | 1781 | 687 | 76.6 |
| 12 | 330 | 1113 | 2100 | 960 | 108.1 |
| 14 | 168 | 1107 | 2247 | 1002 | 38.6 |

→ **Przy `pool_total=0` (brak kurierów do oceny) nadal ~780 ms.** To niezbity dowód: dominujący koszt jest **stały, ponoszony zanim/niezależnie od oceny kandydatów**.

### 2d. Latencja vs `pool_feasible` (kontr-dowód: to NIE kandydaci)
p50 jest **płaska 834–961 ms** dla `pool_feasible` od 0 do 12 (przy `pool_feasible=0` → 868 ms). `med Σr07` rośnie z liczbą feasible (6→400 ms), ale `med Reszta` trzyma ~600–830 ms niezależnie. → koszt routingu skaluje się, ale tonie w stałym narzucie.

### 2e. Latencja vs godzina (Warsaw)
| h | n | p50 | p95 | med `pool_feasible` |
|---:|---:|---:|---:|---:|
| 9 | 55 | 325 | 1093 | 1.0 |
| 11 | 195 | 663 | 1310 | 3.0 |
| 13 | 322 | **1011** | 2033 | 2.0 |
| 14 | 350 | **1028** | 2189 | 3.0 |
| 15 | 373 | 914 | 2249 | 2.0 |
| 16 | 334 | 923 | 2215 | 2.0 |
| 18 | 344 | 900 | 1861 | 4.0 |
| 21 | 146 | 676 | 1736 | 2.0 |

→ **Peak 13–16h ma p50 ~900–1030 przy `pool_feasible` 2–3** (identycznym jak off-peak). Wzrost nie bierze się z większej puli — to **kontencja współbieżnych decyzji na 4 rdzeniach**.

---

## 3. ATRYBUCJA

### 3a. Korelacja z git log (od upgrade CPX32 = 2026-04-27)
- **516 commitów** od 2026-06-01; **227 commitów** od 04-27 dotyka `shadow/obj/lgbm/breach/bundle/pln/…`; **73** jawnie oznaczonych `flaga OFF / shadow-first / log-only / observational` = **compute wykonywany mimo braku efektu na zachowanie**.
- **Fala akrecji 2026-06-12→24** (bezpośrednio przed oknem danych — stąd 06-18 już 846 ms):

| data | commit | co dokłada compute per-decyzja |
|---|---|---|
| 06-12 | `a7efd21` | AUTON-01 auto-assign gate — **„bramka shadow (compute-zawsze)"** |
| 06-13 | `cf12011` | gps-02 filtr jakości fixu (shadow-first) |
| 06-14 | `c51a423` | **obj-food-age: człon świeżości DOSTAWY w celu OR-Tools** (cięższy solve) |
| 06-14 | `3180c88`,`c7eb048`,`0e6b59a` | **r6-breach-shadow ×3** (kontrfaktyczny R6 hard-reject, kalibracja ETA p80, tier-aware) |
| 06-14 | `aae8166` | pln-courier-pay: term realnej płacy per-osoba w `pln_v` (shadow) |
| 06-14 | `f60586f` | tier-split V326 + P(breach)-gov + prep-variance + E4 econ-replay |
| 06-15 | `0833665` | best_effort „najszybszy odbiór" w SHADOW |
| 06-17 | `cf734e5` | objm-lexr6: **`pln_v` liczone na CAŁEJ puli feasible** + tie-breaker |
| 06-17 | `6acd21b` | food-age hard-SLA: twardy span + warm-start |
| 06-18 | `97a586a` | objm-lexr6 Faza 2 live-flip (`ENABLE_OBJM_LEXR6_SELECT=1`) |
| 06-20 | `94fb35b` | two-model ML solo/bundle (drugi model LGBM per decyzja) |
| 06-23 | `7863a39`,`0e28277`,`ef71875` | best_effort objm shadow ×3 (carry-inclusive, cap, 3-tier escalation) |
| 06-28/29 | `109e62e`,`21d2247` | reserve-aware tie-break shadow |

Flagi ON w żywym procesie (z `FLAG_FINGERPRINT` 07-01 21:27), które **liczą per-decyzja na puli feasible**: `ENABLE_R6_BREACH_SHADOW_LOG=1`, `ENABLE_E2_PLN_AB=1`, `ENABLE_OBJM_LEXR6_SELECT=1`, `ENABLE_BEST_EFFORT_OBJM_R6_KEY=1`, `ENABLE_RESERVE_AWARE_TIEBREAK_SHADOW=1`, `ENABLE_BUNDLE_SYNC_SPREAD=1`, `ENABLE_ETA_QUANTILE_R6_BAGCAP=1`, LGBM shadow ×2. To one tworzą stały narzut.

### 3b. Kandydaci wskazani przez team-leada — werdykt
- **A2 soft-score** (`ENABLE_A2_RELIABILITY_SOFT_SCORE=1`): tani człon scoringu, **nie** jest zjadaczem.
- **Global overlay / FLEET_LOAD_GOVERNOR** (`=1`): działa też jako osobny timer `pending_global_resweep` — **poza spanem** decyzji shadow; wpływ na `latency_ms` marginalny.
- **LGBM features / two-model** (06-20): udokumentowany w rekordzie, **mały** (Σlgbm 1–10 ms) — nie p50-driver, ale część akrecji.
- **L2.1 (restart 07-01 21:27)**: **nieobserwowalny** — `jsonl` kończy się 20:08, a po restarcie worker przetworzył **0 zdarzeń** (noc, `NEW_ORDER:0`, `worker_alive=False` w heartbeatach). Flaga `ENABLE_COORD_SENTINEL_INGEST_GUARD=0` (OFF) → i tak nie powinna zmienić compute. **Werdykt: brak wpływu na p50 do zmierzenia; sprawdzić po pierwszym peaku 07-02.** (Uwaga poboczna dla innego lane'a: po restarcie 21:27 heartbeat pokazuje `worker_alive=False`/`processed:0` — do potwierdzenia czy to tylko cisza nocna czy zawis workera.)
- **Panel login sync**: **zmitygowany** — V3.27.7 `panel_bg_refresh` (co 900 s, wątek w tle); login nie blokuje już spanu decyzji. Zostaje jako sporadyczny ogon.

### 3c. Werdykt atrybucji
> **Regres p50 375→838 ms to akrecja obiektywów „shadow/compute-zawsze" (fala 06-12→24, ~15 członów liczonych per-decyzja na puli feasible), która skonsumowała cały zysk z upgrade'u CPX22→CPX32 i dołożyła ~2×.** Sprzęt (4 vCPU) daje headroom off-peak, ale w peak współbieżność × cięższy per-decyzja compute nasyca rdzenie (ogon 2%→19%). Routing kandydatów (OR-Tools/OSRM) to **margines** p50, istotny dopiero w ogonie.

---

## 4. KOSZT INFRA

- **OR-Tools (cap 200 ms/solve):** warm-up przy starcie ~50–82 ms (widoczne przy każdym restarcie; 06-29 aż 4 restarty). Solve odpala się **tylko dla kandydatów `bag≥2`** (`V327_MIN_OR_TOOLS_BAG_AFTER=2`) — dlatego mediana `r07` niska (~5–10 ms), a 200 ms/solve trafia głównie w **ogon** (decyzje z kilkoma workami ≥2). Nie jest driverem p50. (Historyczne D1-D5 „200 ms każdy call = 87% TSP" dotyczyło 2 vCPU i bag-heavy okna — dziś fast-path bag<2 to odciąża.)
- **OSRM:** `degraded_osrm=True` w **0/3535** rekordów — brak degradacji w oknie. Zimny fetch matrycy (`osrm_cache_age_s` 1–5 s) → p50 **2124 ms**, ale tylko **33 rek. (~1%)**; w ogonie `>=1500 ms` odpowiada za **~6%**. `panel_watcher` prefetch (`prefetch_s` 0.3–5.8 s) jest w **innym serwisie** — nie w spanie decyzji.
- **CPU/RAM (`sar`/`free`/`uptime`, noc 22:13):** load `1.07` na 4 vCPU, `%idle 84%` — **headroom off-peak**. Ale **swap 3544 MB / 8192 użyte** (RAM: 212 MB free, 4021 MB available via cache) — ryzyko sporadycznych spike'ów przy wejściu na swap; kandydat na część ogona. Peak-contention potwierdzony pośrednio rozkładem godzinowym ogona (2%→19% do 16h).
- **Rozkład ogona `latency_ms`:** `>=500 ms: 85.5%` · `>=1000: 38.7%` · `>=1500: 13.1%` · `>=2000: 4.6%` · `>=3000: 0.5%`. (Kwiecień: 80% **<** 500 ms — dziś tylko 14.5% < 500 ms, pełna inwersja.)

---

## 5. PROPOZYCJA — SLO, alerty, budżet warstw (opis, zero kodu)

### 5a. SLO (mierzone z `latency_ms` w `shadow_decisions.jsonl`, okna 30-min)
| segment | p50 | p95 | sufit pojedynczej decyzji |
|---|---|---|---|
| **peak 11–14 i 17–20 Warsaw** | ≤ 700 ms | ≤ 1500 ms | > 3000 ms = incydent |
| **HIGH_RISK 14–17** | ≤ 800 ms | ≤ 1800 ms | > 3000 ms |
| **off-peak** | ≤ 450 ms | ≤ 900 ms | > 2500 ms |
Uzasadnienie: to poziomy „obecny minus ~25–30%" (realny cel redukcji akrecji), nie kwietniowe 375 (te wymagałyby cięcia stałego narzutu o połowę — cel długoterminowy).

### 5b. Gdzie alertować
- **Rozszerzyć istniejący `tools/objm_lexr6_canary_monitor.py`** — już czyta `latency_ms` (p50/p95, n) z `shadow_decisions.jsonl`. Dołożyć próg SLO per okno peak + alert **edge-triggered** na Telegram (wzór jak canary: alert przy przekroczeniu, nie co tick). Zero nowej infry.
- **Alternatywa:** dedykowany oneshot-timer `perf_budget_monitor` co 60 min liczący p50/p95 okna peak strumieniowo (jak mój `perf_extract.py`) i porównujący do progów SLO; stop-loss przy `p95 okna > 2500 ms`.
- **Fingerprint budżetu:** logować przy starcie shadow liczbę AKTYWNYCH członów „compute-zawsze" (rozszerzenie `FLAG_FINGERPRINT`) — regres wydajności = wzrost tej liczby; łatwo skorelować z p50.

### 5c. Budżet per warstwa (cel docelowy, dla porównania z obecnym fitem)
| warstwa | obecnie | cel | jak (kierunek, bez kodu) |
|---|---|---|---|
| **stały narzut / decyzja** | ~665 ms | **≤ 350 ms** | bramkować compute-zawsze za flagą (liczyć shadow **tylko** gdy `SHADOW_SAMPLE` np. 1/N albo gdy flaga naprawdę potrzebuje danych); memoizować liczby liczone raz na całą pulę |
| **per-kurier** | ~35 ms×pool | **≤ 25 ms×pool** | jedno wspólne OSRM-table na decyzję zamiast per-kandydat; wektoryzacja scoringu |
| **ogon (solve+OSRM cold+swap)** | p95 1880 | p95 ≤ 1500 | prewarm/utrzymanie cache OSRM w peak; ograniczyć swap (RAM budget); cap równoległości solverów |

### 5d. Rekomendowane następne kroki (diagnostyka, nie zmiana produkcji)
1. **Phase-timers w `assess_order`** (za flagą, próbkowane 1/N) — rozbić stały narzut 665 ms na człony (który obiektyw ile). Bez tego dokładny podział członów = luka.
2. **Profil offline** `assess_order` na zsyntetyzowanym evencie + repliki floty (cProfile) — potwierdzić top-3 funkcje stałego narzutu.
3. **Decyzja produktowa:** które z ~15 członów „shadow ON" nadal dają wartość (replay) — kandydaci do wyłączenia/próbkowania to najtańsze ~200–300 ms odzysku bez utraty jakości.

---

## POKRYCIE
- **Trend:** p50/p95/p99/max/n **dziennie za pełne 14 dni** (06-18→07-01, 3535 rek.) + wariant „bez noc-pusto". Wykrywanie breakpointu: wykonane — **brak in-window**, regres sprzed danych.
- **Dekompozycja:** wszystkie pola `*_ms`/`*latency` w rekordzie zinwentaryzowane; regresja liniowa fixed-vs-per-courier; latencja vs `pool_total`, vs `pool_feasible`, vs godzina; rekordy nocne-puste odsiane i porównane.
- **Atrybucja:** git log 04-27→teraz (227 commitów obj/shadow, 73 flag-OFF) z **datowaną chronologią fali 06-12→24**; werdykt na 5 kandydatów team-leada (A2, overlay, L2.1, LGBM, login); walidacja April→now vs sprzęt.
- **Infra:** OR-Tools (cap/warm-up/fast-path bag<2), OSRM (degradacja 0, cold-fetch ~1%/6% ogona), CPU (`sar` idle 84% noc, swap 3.5 GB), rozkład ogona, login zbackgroundowany.
- **SLO:** progi per segment peak + miejsce alertu (istniejący canary monitor) + budżet per warstwa + kroki diagnostyczne.
- Walidacja liczb: ekstrakcja zgodna z pomiarem team-leada (841 vs 838 p50).

## JAWNE LUKI
1. **Brak bisekcji kwiecień→06-18.** Logi `shadow_decisions` sięgają tylko 06-18 (logrotate skasował starsze; brak `.gz`). Nie mogę wskazać dokładnego dnia/commita, gdy p50 przeskoczyło 375→~800 — atrybucja opiera się na chronologii git + oknie danych już-podwyższonym. Aby domknąć: replay historycznych `learning_log`/`events.db` (jeśli mają latencję) lub A/B `git stash` członów shadow offline.
2. **Dokładny podział stałego 665 ms na człony — nieznany.** Rekord serializuje tylko `r07` i `lgbm`; reszta obiektywów nie ma własnych timerów. Wymaga phase-timerów lub cProfile (pkt 5d). Podział „76% fixed / 24% per-courier" jest twardy; wewnętrzny rozkład fixed = hipoteza (akrecja shadow) potwierdzona git-logiem i architekturą, nie pomiarem per-funkcja.
3. **`pool_total=0 → 780 ms` niedowyjaśnione co do joti.** Wiem, że to stały compute (nie kandydaci), ale nie potwierdziłem, czy `pool_total=0` to naprawdę pusta flota czy artefakt null-default pola na pewnej ścieżce. Nie zmienia wniosku (fit robust na całości), ale wart 1 sondy.
4. **L2.1 post-restart (21:27) i peak 07-02 — do zmierzenia.** Dane kończą się 20:08; nocny worker nie przetwarzał. Pierwszy peak 07-02 da odpowiedź, czy L2.1 (flaga OFF) i restart cokolwiek ruszyły. Dodatkowo `worker_alive=False` w nocnych heartbeatach — do weryfikacji przez inny lane (możliwy zawis, nie mój zakres).
5. **Kontencja CPU w peak — dowód pośredni.** Rozkład godzinowy ogona wskazuje kontencję, ale nie mam `sar` z okna peak (mierzone w nocy). Domknięcie: `sar -q`/`-u` snapshot 13–16h lub `pidstat` na `dispatch-shadow` w peak (read-only).
6. **Swap 3.5 GB jako źródło ogona — niepotwierdzone.** Korelacja swap↔spike nie zmierzona (brak per-decyzja RSS/major-faults). Hipoteza.
