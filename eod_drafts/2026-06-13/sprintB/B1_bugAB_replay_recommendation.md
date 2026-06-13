# B1 — BUG-A + BUG-B replay calibration → flip recommendation

**Data:** 2026-06-13 · **Tryb:** READ-ONLY (zero flipów flag / restartów / git / podmiany danych) · **Dla:** ACK Adriana
**Skrypt analizy:** `eod_drafts/2026-06-13/sprintB/b1_bugAB_replay.py` (uruchamialny, reprodukowalny)
**Dane:** `shadow_decisions.jsonl` (06-11→06-13) + `shadow_decisions.jsonl.1` (06-02→06-10); outcome-join z `dispatch_state/backfill_decisions_outcomes_v1.jsonl` (3906 rek., 1678 z realnym `pickup_to_delivery_min`).

---

## ⚠ PIERWSZE USTALENIE — premisa zadania jest NIEAKTUALNA (zweryfikowane na żywo)

Zadanie zakłada „oba SHADOW-first, flagi OFF, czekają 7-14d kalibracji". **Tak NIE jest:**

| Flaga | Stan LIVE 13.06 | Kiedy | Źródło |
|---|---|---|---|
| **BUG-B** `ENABLE_R5_PICKUP_DETOUR_PENALTY` | **ON, `R5_DETOUR_PENALTY_PER_KM=4.0`** | flipnięta **2026-06-11 ~20:48 UTC** (hot, flags.json) | `flags.json` + `sprint_timeline.md` HANDOFF 06-11; backup `flags.json.bak-pre-bugb-flip-2026-06-11` |
| **BUG-A** `ENABLE_BAG_TIME_FAIRNESS_SCORING` | **OFF** (celowo, sekwencja B→7d→A) | — | `flags.json` |

Kalibracja replay **już się odbyła** (sesja CC 06-11, `eod_drafts/2026-06-11/VERDICT_bug_a_b.md`) i Adrian/sesja silnika flipnęła B. **Ta analiza = follow-up na ŚWIEŻYCH danych** (potwierdzić/skorygować werdykt B po 2 dniach LIVE + przeliczyć A na już-zapełnionych polach shadow). Obie flagi są w `ETAP4_DECISION_FLAGS` → kanon = flags.json, hot-reload, cross-proces (`common.decision_flag`).

**Drugie ustalenie (lekcja #186, fix 06-11):** kary liczone są TERAZ ZAWSZE do pól `*_shadow` (`bonus_bag_time_max_shadow`, `bonus_r5_pickup_detour_penalty_shadow`), a flaga gate'uje tylko aplikację do `score`. To dało kontrfaktyk dla A bez flipu. ALE compute-always wszedł 06-11 → **pola A w pliku `.1` (przed 06-11) są zerowe; ważne okno kontrfaktyka A = od 06-11.** Surowce (`sum_bag_time_min`/`max_bag_time_min`/`fifo_violations`/`r5_pickup_detour_total_km`) były logowane zawsze → replay rekonstruuje kary na całym korpusie.

---

## TL;DR — rekomendacje (5 linijek)

1. **BUG-B: ZOSTAW WŁĄCZONY @4.0/km — NIE eskalować do 8.0/km.** Flip był słuszny (199/199 flipów redukuje detour), ale (a) outcome-join na świeżym korpusie **przestał być monotoniczny** (≤0,5km wolniej niż >1km), (b) @8.0/km generuje **27 szkodliwych flipów „daleko-ale-prosto"** (kurier +6-10 km od restauracji, by ściąć ~2 km detouru). 4.0/km to bezpieczny punkt. **Werdykt: WAIT z eskalacją — zostań na 4.0, NIE rób 8.0 zaplanowanego ~18-19.06 bez przeprojektowania kary.**
2. **BUG-A: TUNE → flip CZĘŚCIOWY max+FIFO (SUM=0), zgodnie z werdyktem 06-11 — potwierdzony na świeżych danych.** `ENABLE_BAG_TIME_FAIRNESS_SCORING=1` + **`BAG_TIME_SUM_PENALTY_PER_MIN=0.0`** + `BAG_TIME_MAX_PENALTY_PER_MIN=0.7` + `BAG_TIME_FIFO_TIE_PENALTY=5.0`. Flip rate 7,9%, kierunkowość na właściwym celu (max_bag_time) **147/3 OK**, 70 flipów zdejmuje naruszenia FIFO, 0 nowych-KOORD, outcome **monotoniczny** (Q1→Q4: 9,8→20,7 min). **Komponent Σ (SUM=1.0) zamknąć** — liniowy podatek od rozmiaru worka (anty-bundling), 17,9% flip + 0,4% nowe-KOORD, łamie ALWAYS-PROPOSE i ekonomię Bartka 2.0.
3. **Sekwencja i zakaz:** NIE flipować A+B w defaultach naraz (replay: 21,2% flip / **7,8% nowe-KOORD ⛔**). A flipnąć dopiero **≥7 dni czystych metryk PO** ustabilizowaniu B — a „czyste" liczone od **2026-06-12 18:32** (koniec incydentu syncworki), nie od flipu B.
4. **Live-weryfikacja B = NIEROZSTRZYGAJĄCA (za mało + skażone danych).** Okno post-flip B (06-11 20:48→13.06) prawie w całości pokrywa **incydent syncworki 06-11 14:28→06-12 18:32** (kara −150 we wszystkich silnikach, lekcja #188). KOORD 06-11/06-12 = 54-56% (syncworka, NIE B). Czyste okno B = tylko **06-13** (118 elig. decyzji, KOORD 15,5% ≈ baseline 14,4% ✓). Robustny sygnał = REPLAY na pełnym korpusie, nie 2 dni live.
5. **Wykonanie flipu A = sesja silnika za ACK** (hot przez flags.json — flaga już w kanonie ETAP4). Kill-switch = `=false` w flags.json (hot). Watch-metryki §6.

---

## 1. Metodologia (przeczytać przed liczbami)

- **Model flipu:** dla każdej decyzji z ≥2 kandydatami ELIGIBLE: `score_adj(c) = score(c) + (kara_z_feature(c) − kara_obecnie_aplikowana(c))`, re-argmax, porównaj ze starym argmaxem. Dla B w oknie post-06-11 `kara_obecnie_aplikowana = −4,0·max(0,detour−0,5)` (B jest LIVE); dla A zawsze 0 (OFF).
- **Pula ELIGIBLE** = `feasibility≠NO`, bez koordynatora (cid=26), bez blind+empty demote V3.16 (no_gps/pre_shift + bag=0) — chyba że best sam taki jest. Wzorzec identyczny jak `VERDICT_bug_a_b.md` (sanity: argmax(score)==best ~80% po filtrze; reszta to dalsze warstwy best_effort/working-override → **podane flip raty = zmiana RANKINGU funkcji celu, realny flip PROPOZYCJI nieco niższy**).
- **Rekonstrukcja kar** (zgodna z `dispatch_pipeline.py:3240-3276` + `common.py:2261-2285`):
  `kara_A = −(w_sum·sum_bag_time_min + w_max·max_bag_time_min + w_fifo·fifo_violations)`;
  `kara_B = −(per_km·max(0, r5_pickup_detour_total_km − 0,5))`.
- **Okna skażone WYKLUCZONE** z analiz „live/clean" (E7-doklejka #8 + lekcja #188): PARSER_DEGRADED 06-06 17:53→06-10 18:24; **SYNCWORKA 06-11 14:28→06-12 18:32**. Replay pełnego korpusu ich nie wyklucza (mierzy ranking, nie outcome live), ale dni KOORD i live-B liczone z wykluczeniem.
- **Ograniczenie uczciwie:** outcome-join to walidacja PREDYKCYJNA (czy metryka shadow przewiduje realny czas), NIE dowód kauzalny że flip poprawi outcome — alternatywnych kandydatów nigdy nie wysłano. To najmocniejszy osiągalny sygnał bez A/B w produkcji.

## 2. Wielkości próby / okna (świeże, ten przebieg)

| | wartość |
|---|---|
| decyzji w replayu (eligible, ≥2 kand.) | **1891** (1708 PROPOSE) |
| PROPOSE pre-flip B (06-02→06-11, B OFF) | 2023 (+429 KOORD) |
| PROPOSE post-flip B (06-11 20:48→13.06) | 264 (+156 KOORD) — **≈połowa skażona syncworką** |
| **czyste** eligible post-flip B (syncworka+parser out) | **118** (głównie 06-13) |
| pokrycie pól A na best (post-fix) | **264/264 = 100%** |
| outcome-join (PROPOSE z realnym czasem) | A: n=1780 (same-courier 371) · B: n=822 (same-courier 170) |

> **Sample-size note:** replay na 1891 decyzjach (~11 dni) = wiarygodny dla decyzji o flipie. Live-okno B (118 czystych) = ZA MAŁE na werdykt outcome — potwierdza tylko brak katastrofy (KOORD 15,5% ✓). Dla A: pełne pokrycie pól + 371 same-courier outcome = wystarczające.

## 3. BUG-B — szczegóły

### Flip-rate (replay, eligible pool)
| wariant | flip rate | n flipów | dir OK/bad (detour) | nowe-KOORD | szkodliwe „daleko-pickup" (>4 km) | margines p50/p90 |
|---|---|---|---|---|---|---|
| **B @4.0/0,5 (LIVE)** | **7,7%** | 145 | **145/0** | 0 (0,0%) | **18** | 4,4 / 14,4 |
| B @6.0/0,5 | 10,0% | 190 | 190/0 | 1 (0,1%) | 23 | 7,0 / 21,7 |
| **B @8.0/0,5 (default, eskalacja)** | **12,6%** | 239 | **239/0** | 1 (0,1%) | **27** ⚠ | 9,0 / 29,6 |
| B @2.0/0,5 | 5,0% | 94 | 93/1 | 0 | 14 | 2,2 / 7,3 |

### Magnitudy (best PROPOSE, post-fix)
`|kara_B| nonzero`: @4.0 → p50 7,5 / p90 19,8 / max 27,4 pkt · @8.0 → p50 15,0 / p90 39,7 / max 54,9 pkt.
Surowy detour best: p50 **2,0 km** / p90 5,5 km. Nonzero/z-detourem: 51/60 (większość detourów > free 0,5 km).
Skala score'ów best w oknie ~ −40…+10 pkt → @8.0/km (p90 ~40 pkt) **dominuje score**, @4.0 (p90 ~20) jest mocnym nudge'em, nie buldożerem.

### ⚠ Sygnał ostrzegawczy #1 — szkodliwe flipy „daleko-ale-prosto"
Przegląd 18-19 flipów B, gdzie nowy zwycięzca jest >4 km dalej od pickupu (kara detouru ≠ koszt absolutny dojazdu):
```
detour 5,9→0,0 km | pickup_dist 0,6→7,2 km | detour saved 5,9, pickup ADDED 6,6   (netto ujemny)
detour 1,4→0,0 km | pickup_dist 1,4→5,9 km | detour saved 1,4, pickup ADDED 4,5   (netto ujemny ⛔)
detour 1,6→0,0 km | pickup_dist 0,4→6,8 km | detour saved 0,7, pickup ADDED 6,4   (netto ujemny ⛔)
detour 6,5→3,0 km | pickup_dist 0,0→10,9 km| detour saved 3,5, pickup ADDED 10,9  (netto ujemny ⛔)
```
Kara R5 patrzy WYŁĄCZNIE na detour (zboczenie z trasy do 2. pickupu), nie na odległość do 1. pickupu → premiuje kuriera „w linii" ale b. daleko. Przy @4.0 to 18 przypadków/145 flipów (~12%), przy @8.0 — 27 (kara przepala karę dojazdu R4 mocniej). **To argument przeciw eskalacji 8.0 BEZ guarda** (np. odciąć karę gdy `km_to_pickup` nowego zwycięzcy > próg, albo liczyć detour jako nadwyżkę nad NAJKRÓTSZĄ trasą feasible, nie nad bieżącą trasą).

### ⚠ Sygnał ostrzegawczy #2 — outcome-join PRZESTAŁ być monotoniczny
| bucket detour | mediana realnego pickup→delivery (all) | (same-courier) |
|---|---|---|
| ≤0,5 km | **20,6 min** (n=63) | 19,6 (n=18) |
| 0,5-1 km | 16,9 min (n=87) | 15,0 (n=23) |
| >1 km | 17,2 min (n=672) | 18,3 (n=129) |

Werdykt 06-11 raportował czystą monotonię (13,9/17,2/19,0). Na świeżym korpusie **bucket ≤0,5 km jest WOLNIEJSZY niż >1 km** — detour sam w sobie NIE przewiduje czysto realnego czasu (bucket ≤0,5 to często solo-dalekie dostawy; >1 km to często bundle). Wniosek: sygnał B jest **słabszy/szumniejszy niż twierdził werdykt** — kolejny argument za konserwatyzmem (4.0, bez 8.0).

### Live post-flip (czyste okno 06-13)
B@4.0 zmienił zwycięzcę vs kontrfaktyk OFF w **0/118** decyzji; nonzero-applied na best **26,9%** (vs „~43%" baseline — deflacja przez syncworkę demotującą worki do solo, zgodne z at#134); detour na best 2,4 km (jeszcze BEZ spodziewanego spadku — okno za krótkie). **Nierozstrzygające — nie potwierdza ani nie obala korzyści; potwierdza brak regresji KOORD.**

### Werdykt B: **WAIT (na eskalacji) — zostań @4.0, NIE rób 8.0**
- **Co zrobić:** NIC teraz (B już LIVE @4.0 i zachowuje się bezpiecznie). **Anuluj/wstrzymaj** planowaną eskalację do 8.0/km (~18-19.06) — dwa świeże sygnały (szkodliwe daleko-pickup ×27, brak monotonii outcome) mówią że 8.0 jest za agresywne.
- **Warunek eskalacji 8.0 w przyszłości:** najpierw **przeprojektować detour** (nadwyżka nad najkrótszą trasą feasible LUB guard `km_to_pickup ≤ próg`) — to zmiana KODU, osobny ticket, nie zmiana stałej. Bez tego 8.0 wprowadzi ~27 net-ujemnych flipów.
- **Kill-switch (gdyby regresja):** `ENABLE_R5_PICKUP_DETOUR_PENALTY=false` w flags.json (hot).

## 4. BUG-A — szczegóły

### Flip-rate (replay, eligible pool)
| wariant | flip rate | n flipów | dir OK/bad (sum) | dir OK/bad (max — właściwy cel) | flipy zdejmujące FIFO | nowe-KOORD | margines p50/p90 |
|---|---|---|---|---|---|---|---|
| **A max+FIFO (SUM=0)** 0/0,7/5,0 | **7,9%** | 150 | 140/10 | **147/3** | **70** | **0 (0,0%)** | 4,4 / 14,0 |
| A soft 0,3/0,5/5,0 | 11,4% | 215 | 209/6 | — | — | 0 (0,0%) | 6,5 / 18,3 |
| **A default (z SUM)** 1,0/0,7/5,0 | **17,9%** | 339 | 337/2 | — | — | **6 (0,4%)** ⚠ | 16,3 / 44,1 |

> Uwaga do „dir bad" przy max+FIFO: 10 flipów ma WYŻSZY `sum_bag_time` — ale to **nie jest cel** wariantu max+FIFO. Na właściwym celu (`max_bag_time`) wynik = **147/3 OK**, plus 70 flipów redukuje FIFO. Wariant robi dokładnie to, co reguła Adriana („lepiej oba po 15 niż 25+8" = max; „najpierw wcześniej odebrane" = FIFO).

### Magnitudy + dowód że SUM = podatek od rozmiaru worka
`|kara_A|` na best (post-fix): default p50 21,4 / p90 47,7 · max+FIFO p50 8,3 / p90 15,7.
Śr. `|kara_A default|` wg rozmiaru worka best: **bag0=19,6 → bag1=26,3 → bag2=40,3 → bag3=47,2** (rośnie liniowo z liczbą zamówień) — to **anty-bundling**, nie kara nierównomierności. FIFO na best: 0→92%, 1→7%, 2→<1% (8% z ≥1 naruszeniem w tym oknie; szersze 13,7% w werdykcie 06-11).

### Outcome-join (świeży korpus — KLUCZOWY dowód dla A)
| kwartyl `|kara_A|` | mediana realnego pickup→delivery (all n=1780) | (same-courier n=371) |
|---|---|---|
| Q1 (najmniejsza kara) | 11,1 min | **9,8 min** |
| Q2 | 17,2 | 15,3 |
| Q3 | 20,0 | 19,0 |
| Q4 (największa) | 19,5 | **20,7 min** |

**Same-courier monotoniczny** (9,8→15,3→19,0→20,7) — reprodukuje werdykt 06-11 (9,7→14,2→19,3→20,7). Worki z dużym `|kara_A|` (duże/nierówne) **realnie** dostarczają ~+11 min wolniej. To najmocniejszy sygnał całego sprintu.

### Przykłady flipów (max+FIFO, old=obecny zwycięzca rankingu, new=po karze)
```
oid 477796  cid484→123  score 24,6→5,7   sum_bt 67,2→12,5  ✓ stary worek 3 zam. ~67 min sumy
oid 477801  cid470→123  score −0,1→−12,9 sum_bt 28,8→7,9   ✓
oid 477930  cid370→484  (default −112) max+FIFO łagodniejszy  sum_bt 72,2→45,5  ✓
```
Wzorzec: zdejmuje zlecenia kurierom z workiem 3 zam. (max_bt 26-32 min, tuż pod hard 35) na rzecz luźniejszych — termicznie słuszne.

### Werdykt A: **TUNE → FLIP CZĘŚCIOWY (max+FIFO, SUM=0)**
- **Co flipnąć (flags.json, hot, sesja silnika za ACK):**
  `ENABLE_BAG_TIME_FAIRNESS_SCORING=true` · `BAG_TIME_SUM_PENALTY_PER_MIN=0.0` · `BAG_TIME_MAX_PENALTY_PER_MIN=0.7` · `BAG_TIME_FIFO_TIE_PENALTY=5.0`.
- **Kiedy:** **≥7 dni czystych metryk PO ustabilizowaniu B** — „czyste" liczone od **2026-06-12 18:32** (koniec syncworki) → najwcześniej **~2026-06-19/20**, NIE od flipu B. (Zgodne z `sprint_timeline` „~18-19.06", ale przesunięte przez incydent syncworki.) Najpierw werdykt syncworki at#137 (06-14) + co najmniej kilka dni czystych.
- **Czego NIE flipować:** komponent Σ (SUM=1.0) — 17,9% flip, 0,4% nowe-KOORD (łamie ALWAYS-PROPOSE przez `MIN_PROPOSE_SCORE=−100`), karze bundling jako taki (kolizja z funkcją celu PLN Bartka 2.0). Jeśli Adrian chce „sumę" — przeprojektować na `Σ max(0, bag_time − target)` lub średnią per order = KOD + nowa kalibracja, osobny ticket.
- **Kill-switch:** `ENABLE_BAG_TIME_FAIRNESS_SCORING=false` (hot).

## 5. Sekwencja i zakazy (twarde)
1. **B już LIVE @4.0** — zostaw, NIE eskaluj do 8.0 (§3). Obserwuj na czystych dniach (od 06-13).
2. **A max+FIFO** — flipnąć po ≥7 dniach czystych metryk od końca syncworki (~19-20.06), za ACK.
3. **NIE flipować A+B w defaultach naraz** (replay: flip 21,2% / **nowe-KOORD 7,8% ⛔**). Wariant soft obu naraz technicznie bezpieczny (~0,5% KOORD), ale sekwencyjnie = czysta atrybucja.
4. **Obie flagi env→flags.json już zrobione** (ETAP4, klucze w kanonie) → flip/rollback/zmiana stałej = hot, bez restartu.

## 6. Watch-metryki po flipie A (3-7 dni, porównanie z baseline tego raportu)
| metryka | baseline | alarm |
|---|---|---|
| KOORD share dzienny (czysty dzień, np. 06-13) | **15,5%** (≈14,4% historyczny) | wzrost >+2 p.p. utrzymany 2 dni → rollback (ALWAYS-PROPOSE) |
| % best z niezerowym `bonus_bag_time_max` | 0% (OFF) → oczek. ~100% | 0% po flipie = flaga nie zadziałała |
| % best z `max_bag_time > 25 min` | (zmierzyć w dniu flipu, surowiec p90=20,2) | oczekiwany SPADEK (cel A) |
| naruszenia FIFO na best (≥1) | ~8% (okno) / 13,7% (06-11) | oczekiwany SPADEK |
| mediana realnego `pickup_to_delivery_min` | ~17-18 min | wzrost >+3 min utrzymany → rollback |
| bundling: % best z `r6_bag_size ≥ 2` | (zmierzyć) | spadek >10 p.p. = kara za mocna ekonomicznie (Bartek 2.0) |
| PANEL_OVERRIDE rate | 27,5% (66/240 od 10.06) | wyraźny wzrost = operator nie ufa nowym wyborom |

Dla B (gdyby kiedyś eskalacja 8.0): dod. „% szkodliwych daleko-pickup (zwycięzca +>4 km od pickupu)" — przy 8.0 oczek. ~27/239 flipów, musi być guarded.

## 7. Co jeszcze potrzeba / kiedy (dla pełnego domknięcia)
- **B live-werdykt:** ≥3-4 dni CZYSTYCH danych po syncworce (od 06-13) → przeliczyć detour na best + KOORD + szkodliwe daleko-pickup. Dziś n=118 za małe. (Nie blokuje — B jest bezpieczny @4.0.)
- **A:** gotowe do flipu po oknie czystym (~19-20.06) — wszystkie dowody są (pokrycie 100%, monotonia outcome, kierunkowość 147/3). Brak blokera danych, brak jest tylko CZASU (7 dni czystych) + ACK.
- **B @8.0:** zablokowane do czasu przeprojektowania kary detouru (kod). Bez tego NIE eskalować.
- **Cross-gate:** werdykt syncworki **at#137 (06-14 06:30 UTC)** musi potwierdzić, że score'y wróciły do normy, zanim liczymy „7 dni czystych" dla A.

---
*Dane: shadow_decisions.jsonl(+.1) 06-02→06-13; backfill_decisions_outcomes_v1 (1678 z realnym czasem); stałe z common.py/flags.json HEAD 13.06. Liczby ±kilka rek. (żywy log). Analiza read-only — żadna flaga/usługa/crontab/git nie zostały dotknięte. Skrypt: `b1_bugAB_replay.py` (obok).*
