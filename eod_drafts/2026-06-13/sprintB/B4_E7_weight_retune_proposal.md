# B4 / E7 — Re-tune hierarchii wag: propozycja do ACK (kickoff at#131, 2026-06-17)

**Data:** 2026-06-13 · **Tryb:** READ-ONLY (zero flipów flag / restartów / git / podmiany danych) · **Dla:** ACK Adriana 17.06
**Skrypt (uruchamialny, reprodukowalny):** `eod_drafts/2026-06-13/sprintB/b4_e7_weight_backtest.py`
**Dane:** `logs/shadow_decisions.jsonl` (06-11→06-13) + `logs/shadow_decisions.jsonl.1` (06-02→06-10) — **2290 PROPOSE z pełnym breakdownem**, po wykluczeniu skażonych okien **1257 clean**; outcome-join z `dispatch_state/backfill_decisions_outcomes_v1.jsonl` (3906 rek., 2204 clean PROPOSE z realnym pickup→delivery). Realna telemetria AUTON-01 `would_auto_assign`/`auto_block_reasons` od **2026-06-13 07:23** (144 rek.).
**Zakres E7 (AUDIT_FIX_PLAN, Z-07/08/14/15):** cap R4 · tabela R-NO-WASTE · normalizacja s_obciążenie · tie-break · kalibracja bramki AUTON-01 na rozkładzie `auto_block_reasons`.

> **To jest PROPOZYCJA, nie zmiana.** Każdy krok ma flagę + kill-switch; wszystkie zmiany scoringu = shadow-first / replay-first (zasada AUDIT_FIX_PLAN). Wykonanie = osobna sesja silnika za ACK.

---

## ⚠ PIERWSZE USTALENIA — premisy zadania zweryfikowane na żywo (zanim zaufasz)

1. **R4 NIE dominuje "wszystkich" propozycji — dominuje RZADKĄ ale skrajną mniejszość.** `bonus_r4>0` w **2,9% best** (36/1257), ale gdy się odpala, mediana = +75, a **9 best miało dokładnie +150** (raw=100, dev≤0,5 km). W 50% z tych 36 przypadków **R4 sam jest większy niż cały final_score** (mediana udziału R4/final_score = 0,64; ogon do 18×). Czyli Z-07 jest prawdziwe co do magnitudy (+150 łamie hierarchię R-PRIORYTETÓW gdzie dystans daje max ~30), ale fałszywe co do częstości ("dominuje hierarchię" → tak, ale lokalnie). **Wniosek: cap R4 to fix CHIRURGICZNY (mały zasięg, duży lokalny efekt), nie globalna rewizja.**

2. **`best` ≠ score-argmax w 52,3% decyzji** (re-confirm Z-10 na świeżym korpusie; 10.06 replay dawał 68%). Selekcja przechodzi przez warstwy late-pickup / best-effort / r6-pov (dispatch_pipeline.py: ~4763-4833, 5316-5351), nie czysty score. **Każdy backtest "czy zmiana wagi flipuje zwycięzcę" liczony na surowym best jest skażony** — pierwotny przebieg dał 55,7% flipów, z czego większość to artefakt Z-10, nie efekt capu. **Poprawna miara (trzyma bazę selekcji=score): 7,7% flip SCORE-ARGMAX po capie R4.** Tak liczę poniżej.

3. **Realna telemetria AUTON-01 już płynie i mówi wprost: `would_auto_assign=True` = 0 / 144 rekordów.** Pełny stos bramek przepuszcza **zero** — zgodnie z `AUTON01_ACCEPTANCE_SEGMENTS.md`. Kalibracja AUTO MUSI pracować na **rozkładzie `auto_block_reasons`** (która bramka ile wycina), nie na czekaniu aż stos przepuści 200 decyzji (≈rok). To nie jest opinia — to zmierzone (§5).

4. **Krzywa score↔wynik jest płaska/odwrócona (Bartek 2.0 §4.1, re-confirm tu).** breach (>35 min od pickup) per `proposed_score`: <0→6% · [0,30)→5% · [30,60)→4% · **[60,90)→13% · ≥90→13%**. **Najwyżej punktowane propozycje są empirycznie NAJGORSZE.** To jest fundament: re-tune wag NIE ma "podbijać score dobrych" — ma ZDJĄĆ inflację (R4 +150, timing_gap ogon −200), która tworzy tę górną, breachującą strefę.

---

## TL;DR — propozycja (6 linijek do ACK)

1. **Z-07 CAP R4: `bonus_r4 = min(60, raw×1.5)`.** Backtest: zmienia score-argmax w 7,7% decyzji z ≥2 feasible (85/1100); w zbiorach gdzie R4 realnie obecny — **22% (13/59)** flipów, wszystkie zdejmują przypadki "R4=+150 wypycha zwycięzcę". **ON za flagą `ENABLE_R4_CAP`, shadow-first.**
2. **Z-15 s_obciążenie: znormalizować do efektywnego capa tier×pora (`BUG4_TIER_CAP_MATRIX`), nie stałej 5.** Dziś dwie osie bag-load liczą się jednocześnie (s_obciążenie /5 w bazie + bug4_cap_soft) w 51 best; s_obciążenie traktuje gold (cap 4-6) i slow (cap 2-3) identycznie. **TUNE za flagą `ENABLE_S_OBCIAZENIE_TIER_NORM`, shadow-first.**
3. **Z-08 R-NO-WASTE: NIE wpinać drugiej tabeli; OGRANICZYĆ ogon i ZAMKNĄĆ overlap.** `timing_gap_bonus` (de-facto R-NO-WASTE) ma **nieograniczony ogon −3/min do −202** (247 best ≤ −30) i pokrywa się z r9_wait/v3273_wait w 193 best. **Propozycja: clamp dolny `timing_gap_bonus ≥ −45` (zgodnie z dolną granicą tabeli REGULY) + audyt, czy waste-min liczy się raz.** Pełna tabela REGULY (oś BUG-2 gap) to INNA oś niż free_at-gap — wpięcie obu = potrójne liczenie. **NIE robić "r_no_waste(gap) z REGULY:49-71" jako trzeciej osi — to było w pierwotnym planie E7 krok 2, REKOMENDUJĘ ODRZUCIĆ.**
4. **Z-15 tie-break: kwantyzacja klucza selekcji do ~2,5 pkt → realny tie-break corridor-dev→tier.** Dziś tie-break (`bundle_level3_dev`, potem nic) odpala się ~nigdy (float equality). Po capie R4 + clamp ogona rozrzut score maleje → kwantyzacja zacznie mieć sens. **TUNE za flagą, shadow-first; mierzyć ile decyzji realnie wpada w bin.**
5. **AUTON-01: kalibracja na rozkładzie `auto_block_reasons` (REALNY, od 06-13).** Dominują: `classifier_not_auto` 127/144 · `margin_ex_delta` 86 · `score_distrust_ceiling` 29 · `late_pickup_*` 52 · `pos_*` 43. **3 decyzje kalibracyjne (§5): (a) sufit G11=90 PRZELICZYĆ PO capie R4** (sufit jest obejściem inflacji R4 — po capie może być zbędny/za niski); **(b) T1-whitelist std/std+ NIE gold/std+** (gold acceptance 16% < std 38%); **(c) G7 pos-informed PRZEPROJEKTOWAĆ na wiarygodność kotwic czasowych** (brak GPS = celowa polityka, korekta Adriana 13.06).
6. **Sekwencja i zależności:** wykonać PO B1 (bag_time A częściowy + R5 detour B już LIVE) — bo R4/timing_gap NIE może karać tej samej osi co nowe kary bag_time/r5/sync/repo/loadgov (E7-DOKLEJKA #2). **Kolejność w sesji: (1) cap R4 → (2) re-ocena sufitu G11 → (3) s_obciążenie norm → (4) clamp timing_gap + audyt overlap → (5) tie-break kwantyzacja.** Każdy krok osobno shadow→replay→ACK.

---

## 1. Anatomia hierarchii wag — co kod robi NAPRAWDĘ (z `file:linia`)

`final_score` składa się tak (`dispatch_pipeline.py:4020-4030`):

```
final_score = score_result["total"]          # baza 4-komponentowa, CAP 100 (scoring.py:22-58)
            + bundle_bonus                    # = bonus_l1 + bonus_l2 + bonus_r4   (l.3107)
            + timing_gap_bonus                # R-NO-WASTE de-facto, oś free_at-gap (l.3152-3163)
            + wave_bonus
            + bonus_penalty_sum               # suma 19 kar R5/R6/R8/R9/bug4/... (l.3977)
            + bonus_bug2_continuation
            + v324a_extension_penalty
            + bonus_bag_time_* + bonus_fifo + bonus_r5_pickup_detour   (BUG A/B, l.4024-4030)
```

Komponenty wg zakresu E7:

| Reguła | Gdzie w kodzie | Skala teoretyczna | Skala zmierzona (clean, na best) |
|---|---|---|---|
| **Baza dystans** (R-PRIORYTETÓW #2) | `scoring.py:34` `s_dystans`, waga 0,30 | wkład max **30** | — (w `score_result.total`, cap 100) |
| **s_obciążenie** (R-PRIORYTETÓW #5) | `scoring.py:37-43`, waga 0,25, dzielnik `MAX_BAG_TSP_BRUTEFORCE=5` | wkład max **25**, strata −5/bag | strata mediana **−10 pkt** gdy bag≥1 (896 best) |
| **R4 corridor** (R-PRIORYTETÓW #3) | `dispatch_pipeline.py:3087-3098` `bonus_r4 = raw×1,5` | **0..+150** | gdy>0: mediana **+75**, max **+150** (9 best); aktywny 2,9% |
| **R-NO-WASTE** (R-PRIORYTETÓW #1) | `dispatch_pipeline.py:3152-3163` `timing_gap_bonus` | +25 / +15 / +5 / **−3·(gap−15)** (bez dołu!) | mediana +5, **min −202,6**, 247 best ≤ −30 |
| **bug4 tier-cap** (druga oś bag) | `dispatch_pipeline.py:3920-3936`, `common.py:1069-1100` | 0 / −20 / −60 / −120 / −9999 | aktywny 51 best, max −60 |
| **tie-break** | `dispatch_pipeline.py:4702-4706` `(-score, bundle_level3_dev)` | float-equality → martwy | ~nigdy nie rozstrzyga |

**Twardy wniosek (Z-07):** porządek wag w kodzie = **R4 (+150) ≫ R-NO-WASTE (+25/−∞) ≫ dystans (30) ≈ obciążenie (25) ≫ tier (tie-break)**. Deklarowana hierarchia R-PRIORYTETÓW (REGULY:88-111) = **waste(#1) > dystans(#2) > R4(#3) > tier(#4) > bag(#5)**. **R4 (#3) zjada #1 i #2; tie-break tier (#4) jest martwy.** To jest dokładnie problem E7.

---

## 2. Z-07 — CAP R4 (główny fix)

**Diagnoza.** `bonus_r4 = bonus_r4_raw × 1,5`, raw maxuje na 100 (dev≤0,5 km) → **R4 do +150**. Baza dystansu daje max wkład 30. Korytarz (drop "po drodze") jest realnym sygnałem, ale +150 czyni go nadrzędnym nad wszystkim — wbrew hierarchii (#3 nie #1).

**Backtest capu `min(60, raw×1,5)`** (skrypt §5, baza selekcji=score, zbiór kandydatów stały):
- decyzji z ≥2 feasible: **1100**; flip SCORE-ARGMAX po capie: **85 (7,7%)**.
- zbiorów z R4>0 obecnym: 59; **flipy spowodowane przez R4: 13 (22% z tych 59)**.
- przykłady flipów R4-driven (zwycięzca z R4=+150 ustępuje po capie):
  - `478109` Karczma Maciejówka: stary best cid470 score 96,4 (R4=150) → cap → 90,6, **przegrywa z cid515**.
  - `478135` Rukola Sienkiewicza: cid393 score 184,2 (R4=150) → 95,8, przegrywa z cid515.
  - `478884` Kebab Król: cid484 score 148,3 (R4=150) → 115,1, przegrywa z cid413.
  - `479802` Grill Kebab: cid484 score 142,9 (R4=150) → 74,1, przegrywa z cid514.

**Czy flipy są POPRAWĄ?** Wszystkie przykłady to przypadki, gdzie R4=+150 wpychał kuriera, którego *czysty* score (bez korytarza-bonusu) był wyraźnie niższy od rywala. Z §III (krzywa score↔wynik): strefa score>90 ma breach 13% (vs 4-6% niżej) — a to właśnie tam R4=+150 wynosi kandydatów. **Cap R4 redukuje populację breachującej górnej strefy.** To kierunkowo zgodne z Bartkiem 2.0 (ZDJĄĆ inflację, nie dodać).

**Propozycja (do ACK):**
- **Krok 1a:** flaga `ENABLE_R4_CAP` (kanon ETAP4 / flags.json, hot-reload), stała `R4_CAP=60.0` w common.py (env/numeric-override). `bonus_r4 = min(R4_CAP, bonus_r4_raw × 1,5)` w `dispatch_pipeline.py:3098`.
- **shadow-first:** dopisać `bonus_r4_capped_shadow_delta` (compute-always, lekcja #186) → serializer; aplikacja do score TYLKO za flagą.
- **Kalibracja capa:** start 60 (= przywraca R4 do rzędu dystansu×2, nadal #3 w hierarchii nad tier). Sensitivity replayem: 45 / 60 / 90 (flip-rate + ile breach-strefy znika). **NIE 100** (nadal dominuje dystans).

---

## 3. Z-15 — s_obciążenie: normalizacja do efektywnego capa (druga oś bag double-count)

**Diagnoza (zmierzona).** Dwie osie karzą głębokość worka jednocześnie:
1. `s_obciazenie(bag)` (scoring.py:37-43): liniowy decay przez **stałą 5** (`MAX_BAG_TSP_BRUTEFORCE`), w bazie waga 0,25 → strata do −25 pkt. Bag≥1 odpala w **896/1257 best** (mediana strata −10 pkt).
2. `bonus_bug4_cap_soft` (dispatch_pipeline.py:3920-3936): kara za przekroczenie capa **tier×pora** (`BUG4_TIER_CAP_MATRIX`, gold off-peak 4 … slow off-peak 2). Default **ON**. Odpala w 51 best, max −60.

**OBA naraz** (bag≥1 ∧ bug4<0) = **51 best** = czysty double-count na tej samej osi. Gorzej: s_obciążenie /5 jest **tier-ślepe** — gold (realny cap 4-6) i slow (cap 2-3) dostają identyczną stratę za ten sam bag, choć ich pojemność jest różna. Doktryna jest per-courier (FILOZ wave-matrix), kod nie.

**Propozycja (do ACK):**
- **Krok 3a:** `s_obciazenie(bag, cap_eff)` gdzie `cap_eff = BUG4_TIER_CAP_MATRIX[tier][pora]` zamiast stałej 5. Decay względem realnej pojemności kuriera. Flaga `ENABLE_S_OBCIAZENIE_TIER_NORM`, shadow-first (`s_obciazenie_tiernorm_shadow_delta`).
- **Anti-double-count:** po normalizacji s_obciążenie → **rozważyć przeniesienie kary "powyżej capa" WYŁĄCZNIE do bug4_cap_soft**, a s_obciążenie zostawić jako miękki sygnał DO capa (0 przy bag<cap_eff). To czyni osie ortogonalnymi: s_obciążenie = "elastyczność do limitu", bug4 = "kara za przekroczenie limitu". **Wymaga decyzji Adriana — to zmienia semantykę dwóch reguł.**
- **Ryzyko:** średnie. Dotyka 896 best (szeroki zasięg, w przeciwieństwie do R4). DLATEGO bezwzględnie shadow→replay→ACK; mierzyć flip-rate i czy nie faworyzuje goldów ponad fairness (B3-rotacja).

---

## 4. Z-08 — R-NO-WASTE: NIE druga tabela; clamp ogona + audyt overlap

**Diagnoza.** `timing_gap_bonus` (dispatch_pipeline.py:3152-3163) JEST de-facto implementacją R-NO-WASTE, ale:
- jego oś to `free_at_min − time_to_pickup_ready` (kiedy kurier wolny vs kiedy jedzenie gotowe) — **INNA niż oś tabeli REGULY** (R-NO-WASTE:49-71 = BUG-2 continuation gap "kurier wciąż dowozi gdy nowy pickup ready"). To są dwa różne zjawiska.
- ma **nieograniczony dolny ogon `−3·(gap−15)`** → zmierzone do **−202,6**; 247 best (20%) mają timing_gap ≤ −30. To pojedynczy komponent zdolny zdominować nawet R4.
- **pokrywa się** z `bonus_r9_wait_pen` + `bonus_v3273_wait_courier` (oba w `bonus_penalty_sum`): w **193 best** (15%) timing_gap<0 ORAZ któraś kara wait<0 odpala — częściowo to samo "czekanie" liczone 2-3×.

**Pierwotny plan E7 krok 2** ("`r_no_waste(gap)` — pełna tabela z REGULY:49-71, obie strony, kary −10/−20/−30; wycofać overlap timing_gap") — **REKOMENDUJĘ ODRZUCIĆ w tej formie.** Wpięcie tabeli REGULY jako TRZECIEJ osi (obok timing_gap free-at i obok r9/v3273 wait) potroiłoby liczenie tej samej intencji. REGULY:49-71 to *specyfikacja intencji*, nie sygnał do dodania na ślepo.

**Propozycja (do ACK):**
- **Krok 4a (małe, pewne):** clamp dolny `timing_gap_bonus ≥ −45` (= dolna granica tabeli REGULY ">+45 min → −30", z buforem). Usuwa patologiczny ogon −202, który łamie hierarchię. Flaga `ENABLE_TIMING_GAP_CLAMP`, shadow-first. **To samo w sobie jest sensownym, niskoryzykownym fixem.**
- **Krok 4b (audyt, nie kod od razu):** policzyć w shadow, czy w 193 nakładających się best "czekanie kuriera" trafia do timing_gap I do v3273_wait. Jeśli tak — **wybrać JEDNĄ oś** (rekomendacja: v3273_wait dla "kurier stoi pod restauracją", timing_gap dla "dopasowanie zwolnienia do gotowości"; rozdzielić definicje). To jest właściwa realizacja "wycofać nakładający się fragment" z planu — ale po pomiarze, nie hurtem.
- **NIE dodawać `r_no_waste(gap)` z REGULY jako nowej osi.** Intencja REGULY jest już pokryta przez timing_gap (free-at) + r9/v3273 (wait) + bug2_continuation (continuation gap). Brakuje DOMKNIĘCIA (clamp+rozdzielenie), nie nowego komponentu.

---

## 5. AUTON-01 — kalibracja bramki na rozkładzie `auto_block_reasons` (REALNE dane)

**Stan danych:** telemetria `would_auto_assign`+`auto_block_reasons` płynie od **2026-06-13 07:23** (commit `a7efd21`); na 17.06 będzie ~4 dni — najmłodszy strumień. **`would_auto_assign=True` = 0/144** (potwierdza `AUTON01_ACCEPTANCE_SEGMENTS.md`: pełny stos przepuszcza ~zero).

**Realny rozkład block-reasons (144 rek., każda bramka zliczana niezależnie):**

| Bramka (auto_block_reason) | n | % | Interpretacja kalibracyjna |
|---|---:|---:|---|
| `classifier_not_auto` | 127 | 88% | Faza 7 nie dała AUTO (margin/score/tier/pool/edge) — najszersza |
| `margin_ex_delta` (G12) | 86 | 60% | margin jakościowy Z-10 < próg (T1=15) — **to jest realny wąsk** |
| `score_distrust_ceiling` (G11) | 29 | 20% | score>90 — **obejście inflacji R4** |
| `late_pickup_extension`+`late_pickup_redirect` (G8) | 52 | 36% | propozycja przedłużenia czasu — wymaga człowieka (Adrian 31.05) |
| `pos_from_store`+`pos_not_informed` (G7) | 43 | 30% | brak żywego GPS — **patrz korekta Adriana** |
| `verdict_not_propose` (G1) | 11 | 8% | KOORD — nigdy auto |
| `new_courier_ramp` (G6) | 11 | 8% | rampa nowych |
| `plan_sla_violations` (G9) | 4 | 3% | R6 na finalnym zwycięzcy |

**Trzy decyzje kalibracyjne do ACK (kolejność zależna od capu R4):**

1. **Sufit G11 (`AUTO_ASSIGN_SCORE_DISTRUST_CEILING=90`) PRZELICZYĆ PO capie R4 — w TEJ kolejności.** Sufit jest jawnym obejściem inflacji R4 (AUTON01_DESIGN §3 G11). Po capie R4 (krok 2 §2) górna strefa score>90 skurczy się (R4 było głównym jej źródłem — 9 best z +150). **Hipoteza: po capie sufit 90 może być (a) zbędny, albo (b) trzeba go opuścić do ~75, bo "naturalna" górna strefa bez R4 jest niżej.** Replay: rozkład score PO capie → ustawić sufit na percentyl, gdzie breach zaczyna rosnąć (dziś to ~60-90). **To jest E7-DOKLEJKA #6d, domknięta tu danymi.**

2. **T1-whitelist: std/std+ NIE gold/std+** (AUTON01_ACCEPTANCE_SEGMENTS §1c, re-confirm backfill §7). Acceptance per tier: **gold 7%** < std 9% ≈ std+ 9% (backfill clean, n=2204); w żywym PANEL_AGREE (n=245) gold 16% < std 38%. Koordynator systematycznie NIE daje goldom tego, co Ziomek proponuje (hipoteza B3-rotacja: gold = najgłębsze worki → człowiek rotuje gdzie indziej). **AUTO na goldach aktywnie psułoby dystrybucję zarobków.** Decyzja: T1-AUTO start = std/std+; gold dopiero po wyjaśnieniu anomalii.

3. **G7 (pos-informed) PRZEPROJEKTOWAĆ — NIE czekać na GPS** (korekta Adriana 13.06, E7-DOKLEJKA #6f). Brak GPS = celowa polityka treningowa. Zamiast binarnego "informed albo nic": skalibrować **wiarygodność KOTWIC CZASOWYCH per pos_source** na `eta_calibration_shadow.jsonl` (czy ETA z kotwicy trzyma limit 5 min odbioru?) i dopuścić AUTO na kotwicach o zmierzonej wiarygodności z zaostrzonym marginem. **Dane są** (`dispatch_state/eta_calibration_shadow.jsonl`) — to osobny mini-blok kalibracyjny w E7.

**Metoda kalibracji (nie "czekaj na 200 would_auto"):** zdejmować bramki PO JEDNEJ i mierzyć acceptance w odblokowanym segmencie (AUTON01_ACCEPTANCE_SEGMENTS §3.1). Bramki o najwyższym koszcie populacyjnym i najniższej wartości predykcyjnej (G12 margin — "margin NIE przewiduje acceptance", §2 tego pliku potwierdza: margin ≥60 → 21% acc, [0,5) → 36%) rozważyć do **acceptance-first targeting**: startowy podzbiór AUTO = segmenty empirycznie wysokiego acceptance (std/std+ × off-peak × pos-informed/pre-shift = do 53%), margin jako bezpiecznik wtórny.

---

## 6. Plan wykonania w sesji E7 (17.06, za ACK) — kolejność i bramki

Wszystko shadow-first, compute-always (lekcja #186), flaga+killswitch (kanon ETAP4 flags.json), replay przed flipem, dispatch-telegram NIETKNIĘTY.

| # | Krok | Flaga | Zależność | Walidacja przed flipem |
|---|---|---|---|---|
| 1 | **Cap R4** = min(60, raw×1,5) | `ENABLE_R4_CAP` | po B1 (bag_time) | replay flip-rate + ile breach-strefy score>90 znika |
| 2 | **Re-ocena sufitu G11** po capie | (stała `..._CEILING`) | **PO kroku 1** | rozkład score po capie → sufit na percentylu wzrostu breach |
| 3 | **s_obciążenie tier-norm** | `ENABLE_S_OBCIAZENIE_TIER_NORM` | niezależne | replay flip-rate (szeroki zasięg!) + fairness gold vs reszta |
| 4 | **Clamp timing_gap ≥ −45** + audyt overlap wait | `ENABLE_TIMING_GAP_CLAMP` | niezależne | ile best z ogonem <−45 + pomiar 193 overlap |
| 5 | **Tie-break kwantyzacja ~2,5 pkt** | `ENABLE_SELECTION_QUANTIZE` | **PO 1+4** (mniejszy rozrzut) | ile decyzji realnie wpada w bin (dziś ~0) |
| 6 | **AUTON-01: T1 std/std+, G7 kotwice, kalibracja block-reasons** | stałe/flagi AUTON-01 | **PO 1+2** | acceptance per odblokowany segment; 0 flip would_auto (inwariancja #188) |

**Werdykt całości (wzór R1+CB / late-pickup Opcja B):** dla każdego kroku — replay 7-14d (po wykluczeniu skażonych okien) → liczba poprawek vs regresji + acceptance PANEL_AGREE z E3 przed/po. Próg jak historycznie (poprawki:regresje ≥ ~9:1). **Żaden krok nie idzie live bez tego werdyktu.**

---

## 7. Zależności i ANTY-double-count (E7-DOKLEJKA #2 — krytyczne)

E7 dotyka osi, które OSTATNIO dostały nowe kary. **Sprawdzić nakładanie PRZED każdą zmianą wagi:**

- **bag_time fairness (BUG-A, B1 rekomenduje flip max+FIFO, SUM=0)** — to NOWA oś "głębokość/wiek worka". **Koliduje z s_obciążenie i bug4_cap (oś bag).** Po wpięciu B1: s_obciążenie + bug4_cap + bag_time_max = TRZY osie bag. Krok 3 (§3) MUSI to uwzględnić — możliwe, że bag_time_max przejmuje rolę "kary za przeciążenie", a s_obciążenie wraca do roli czystego "elastyczność do capa". **Decyzja Adriana przy kroku 3.**
- **R5 detour (BUG-B, LIVE @4,0/km od 06-11)** — oś "objazd po pickup". Ortogonalna do R4 (corridor = drop "po drodze", detour = pickup z objazdem) — ale sprawdzić, czy cap R4 nie odsłania przypadków, gdzie detour i corridor mówią przeciwnie.
- **sync_spread (−150) / loadgov (LIVE od 06-11) / repo / pln (shadow)** — pola `bonus_sync_spread_*`, `bonus_loadgov_*`, `bonus_repo_*`, `pln_*` (Bartek 2.0). **Przy capie R4 i clampie timing_gap sprawdzić, czy nowe kary nie liczą tej samej geometrii (sync = spread gotowości, timing_gap = free-at gap — pokrewne!).** Pełen kontekst: `/root/ROADMAP_BARTEK2_2026-06-11.md`.
- **Logrotate-aware sweep (E7-DOKLEJKA #1)** — `r04_evaluator` (okno 30 dni!), `validation_gate_lgbm`, `learning_analyzer` czytają tylko żywy plik → po rotacji 100 MB widzą ~3 dni. **Zrobić PRZED kalibracją** (wzór `tools/_rotated_logs.py`), inaczej E7 liczy na obciętych danych. **Mój skrypt już czyta oba pliki (.jsonl + .jsonl.1) — ten konkretny backtest jest bezpieczny, ale narzędzia produkcyjne kalibracji nie.**
- **Lekcja #186 sweep:** każda flaga dotykana w E7 — upewnić się, że gate'uje APLIKACJĘ do score, nie OBLICZENIE pola shadow (wszystkie proponowane kroki tak skonstruowane).

---

## 8. Stan danych — czego NIE da się dziś rozstrzygnąć (uczciwie)

- **Acceptance w podzbiorze `would_auto`** — niemożliwy, bo would_auto=0 (§5). Kalibracja AUTO = na block-reasons, nie na acceptance-przez-stos. **To nie luka do uzupełnienia "więcej danych" — to strukturalne (stos za ciasny); rozstrzyga się przeprojektowaniem bramek, nie czekaniem.**
- **Atrybucja jakości score → realny czas dostawy per komponent** — backfill ma outcome (pickup→delivery) i `proposed_score`, ale NIE breakdown komponentów (jest tylko w shadow_decisions, które nie mają realnego outcome zlinkowanego per-komponent). Dlatego §III/§7-backfill pokazują breach per *score-bucket/tier*, nie per *R4/s_obciążenie osobno*. **Pełna atrybucja wymaga join shadow_decisions(komponenty) × backfill(outcome) per order_id — wykonalne, ~2h, REKOMENDUJĘ jako pierwszy krok analityczny sesji E7** (skrypt-szkielet: rozszerzyć §7 mojego backtestu o join po order_id).
- **timing_gap overlap z wait (193 best)** — wykryty, ale czy to PEŁNY double-count czy częściowy wymaga prześledzenia definicji per-case (krok 4b §4). Liczba (15%) jest pewna; "ile z tego to dokładnie ta sama minuta" — nie.
- **Okno czystych danych:** 1257 clean PROPOSE z 06-02→06-13 PO wykluczeniu PARSER_DEGRADED (06-06→06-10) i syncworki (06-11→06-12). To **wystarcza na replay wag** (rozkłady komponentów, flip-rate), ale realna telemetria AUTON-01 ma dopiero ~4 dni na 17.06 — **jeśli n za małe na segmenty AUTON-01, dosłać +3 dni** (E7-DOKLEJKA #6a). Cap R4 / s_obciążenie / timing_gap NIE czekają — dane są.

---

## 9. Rollback / bezpieczeństwo

- Wszystkie kroki = flaga w flags.json (hot-reload, kanon ETAP4 `decision_flag`, cross-proces). Kill-switch per krok = `=false`. Twardy = `git revert` taga kroku + restart shadow+panel-watcher (telegram NIETKNIĘTY).
- Zero zmiany zachowania przy OFF (compute-always pola shadow są addytywne, tailer czyta tylko znane klucze).
- **Ten dokument i skrypt NIE zmieniają niczego live.** Wykonanie = osobna sesja silnika za ACK 17.06.

---

*Reprodukcja: `/root/.openclaw/venvs/dispatch/bin/python eod_drafts/2026-06-13/sprintB/b4_e7_weight_backtest.py`. Cross-ref: B1 (bag_time/R5 — anty-double-count), AUTON01_DESIGN.md + AUTON01_ACCEPTANCE_SEGMENTS.md (bramki), bartek2-strategic-audit.md / BARTEK_2.0_RAPORT §4.1 (score↔wynik), REGULY_BIZNESOWE_2026-04-22.md (R-NO-WASTE:43-69 / R-PRIORYTETÓW:88-111), AUDIT_FIX_PLAN E7 (Z-07/08/14/15 + DOKLEJKI).*
