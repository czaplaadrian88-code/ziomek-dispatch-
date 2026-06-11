# WERDYKT — shadow flagi BUG A (bag_time fairness) + BUG B (R5 pickup detour)

**Data analizy:** 2026-06-11 ~13:30 Warsaw · **Analityk:** sesja CC (read-only, zero zmian flag/usług)
**Okno danych:** 2026-06-02 07:11 UTC → 2026-06-11 11:20 UTC (~9.3 dnia; log zrotowany — rekordy 28.05-01.06 nie istnieją już na dysku), n=2254 decyzji (1930 PROPOSE / 324 KOORD).
**Skrypty:** `/tmp/verdict_ab.py` (pokrycie+outcome), `/tmp/verdict_ab_v2.py` (flip analysis eligible-pool), warianty stałych ad-hoc.

---

## WERDYKT (TL;DR — 5 linijek)

1. **BUG B → FLIP rekomendowany (opcja a)**, ale ze ZŁAGODZONĄ stałą na start: `ENABLE_R5_PICKUP_DETOUR_PENALTY=1` + `R5_DETOUR_PENALTY_PER_KM=4.0` (free 0.5 km bez zmian) → flip rate 7.4%, mediana kary 7 pkt, nowe-KOORD 0.2%; 199/199 flipów redukuje detour; outcome join monotoniczny (+5 min mediany realnego czasu przy detour>1 km). Eskalacja do default 8.0/km po ≥7 dniach czystych watch-metryk.
2. **BUG A → FLIP CZĘŚCIOWY (opcja a dla max+FIFO, świadome zamknięcie komponentu SUM w obecnej formie)**: `ENABLE_BAG_TIME_FAIRNESS_SCORING=1` + **`BAG_TIME_SUM_PENALTY_PER_MIN=0.0`** (env override; max 0.7/min i FIFO 5.0 zostają) → flip 7.7%, mediana kary 9.4 pkt, nowe-KOORD 0.3%.
3. **Komponent Σ bag_time (SUM=1.0/min) NIE flipować**: to liniowy podatek od ROZMIARU worka (śr. kara bag0→bag4: 20→64 pkt), nie od nierównomierności; sam generuje 3.3% nowych KOORD (łamie ALWAYS-PROPOSE przez `MIN_PROPOSE_SCORE=-100`) i flip 17.2% — do przeprojektowania (mean per order / nadwyżka ponad target) osobnym sprintem, NIE kolejny tydzień biernego shadow.
4. **ZAKAZ flipu obu flag naraz w defaultach** (1.0/0.7/5 + 8/0.5): 7.8% PROPOSE (150 decyzji) spadłoby pod MIN_PROPOSE_SCORE → nowe KOORD-y. Wariant soft (pkt 1+2 razem) = 0.5% — akceptowalny, ale sekwencyjnie: najpierw B, po ≥7 dniach A (zgodnie ze spec'em sprintu „B najprostszy najpierw").
5. **Flip wykonuje Adrian/sesja silnika** (NIE ta sesja): env w `/etc/systemd/system/dispatch-shadow.service.d/override.conf` + restart `dispatch-shadow` poza peakiem (⚠ shadow zasila realne propozycje — [[ziomek-shadow-is-live-proposals]]). Kill-switch = usunięcie env / `=0` + restart. Watch-metryki niżej (§7).

---

## 1. Metodologia + ograniczenia (przeczytać przed liczbami)

- **Flagi były OFF przez CAŁE okno** — zweryfikowane: 0/2254 rekordów ma niezerowe `bonus_bag_time_*` / `bonus_r5_pickup_detour_penalty` w best. **Wbrew założeniu zadania kary przy OFF NIE są liczone do pól bonus_*** (kod `dispatch_pipeline.py:2889/2901` liczy bonus tylko gdy flaga ON — inny wzorzec niż shadow-delta R1/V319H). Kary REKONSTRUOWANE ze surowych metryk logowanych zawsze: `kara_A = -(1.0·sum_bag_time_min + 0.7·max_bag_time_min + 5.0·fifo_violations)`, `kara_B = -8.0·max(0, r5_pickup_detour_total_km − 0.5)` (stałe = defaulty `common.py:2045-2064`, env nie nadpisany).
- **Struktura rekordu:** pola `candidates` BRAK; jest `best` + `alternatives` — alternatywy mają PEŁNY zestaw per-kandydat (score + surowe metryki A/B), więc analiza flipów jest wykonalna na pełnej puli (`alternatives` nie są przycinane względem `pool_feasible_count` — 0 przypadków truncation).
- **ALE** pool zawiera też kandydatów infeasible (`feasibility=NO`, ~12%) i demotowanych post-scoringowo: w surowym poolu best ≠ argmax(score) w 65.9% decyzji — w 100% wyjaśnione przez: no_gps/pre_shift blind-empty demote V3.16 (84%), feasibility=NO (12%), demote koordynatora (4%). Dlatego flip analysis liczona na **poolu ELIGIBLE** (feasibility≠NO, bez koordynatora i bez blind+empty, chyba że best sam taki jest). Sanity po filtrze: argmax(score)==best w 80.4% — pozostałe ~20% to dalsze warstwy (best_effort R6 redirect, selection veto, working-override), **więc podane flip raty to zmiana RANKINGU funkcji celu, nie dokładna zmiana finalnej propozycji — realny flip propozycji będzie nieco niższy.**
- Okno zaczyna się 02.06 (rotacja `shadow_decisions.jsonl.1`), nie 28.05 jak zakładało zadanie — i tak ≥7 dni (9.3 dnia). Log żywy — liczby ±2 rekordy między przebiegami.

## 2. Pokrycie + rozkłady kar (PROPOSE, pola w `best`)

| | BUG A | BUG B |
|---|---|---|
| nie-null | **99.9%** (1929/1930) | **46.8%** (903) — null to strukturalnie bag=0/1 bez trasy (474 z bag=0), nie defekt instrumentacji |
| niezerowa kara | 99.9% | **43.1%** (831; 92% policzonych detour > free 0.5 km!) |
| \|kara\| p50 / p90 / max | **26.2 / 70.3 / 130.4** pkt | (nonzero) **14.0 / 40.6 / 76.9** pkt |
| surowiec p50 / p90 / max | sum_bag_time 16.6 / 49.1 / 108.3 min | detour 2.01 / 5.45 / 10.11 km |

- FIFO violations w best: 0→86.3%, 1→12.2%, 2→1.2%, 3→0.3% (13.7% z ≥1).
- **Śr. \|kara_A\| wg bag_size best: 0→19.8, 1→28.7, 2→44.8, 3→59.8, 4→64.4** — dowód, że komponent SUM karze rozmiar worka (anty-bundling), nie nierównomierność.
- Mediana detour 2 km przy free 0.5 km → kara B w defaultach to nie „kara za wyjątek", tylko stały podatek na ~43% propozycji.
- Skala odniesienia: score'y best w oknie typowo −40…+10 pkt → defaultowe kary (p50 26 / p90 70) DOMINUJĄ score, nie są „soft nudge".

## 3. Analiza flipów (eligible pool, n=1606 decyzji z ≥2 kandydatami, w tym 1553 PROPOSE)

| wariant | flip rate (PROPOSE) | n flipów | mediana marginesu | nowe-KOORD (best_adj < −100) |
|---|---|---|---|---|
| **A baseline** 1.0/0.7/5.0 | **17.5%** | 271 | 16.6 pkt | **3.3% (64) ⚠** |
| **A max+FIFO** 0/0.7/5.0 | **7.7%** | 124 | — | 0.3% (5) ✓ |
| A mean zamiast sum | 15.7% | 252 | — | — |
| A soft 0.3/0.5/5.0 | 11.3% | 181 | — | — |
| **B baseline** 8.0/0.5 | **12.6%** | 195 | 7.9 pkt | 0.5% (10) |
| **B half** 4.0/0.5 | **7.4%** | 119 | — | 0.2% (4) ✓ |
| B 4.0/1.0 | 6.8% | 110 | — | — |
| B 2.0/0.5 | 4.9% | 79 | — | — |
| **A+B baseline naraz** | **21.2%** | 330 | 20.9 pkt | **7.8% (150) ⛔** |
| A+B soft (0/0.7/5 + 4/0.5) | — | — | — | 0.5% (9) ✓ |

(Na surowym poolu z demotowanymi: A 8.8% / B 5.3% / AB 10.0% — zaniżone przez rozcieńczenie; eligible = headline. Rozkłady kar wariantów: A max+FIFO p50=9.4/p90=22.2; B 4.0/0.5 p50=7.0/p90=20.3.)

**Kierunkowy sanity flipów: A — 275/277 nowych zwycięzców ma MNIEJSZY sum_bag_time; B — 199/199 ma MNIEJSZY detour.** Kara robi dokładnie to, co ma robić.

**Koncentracja flipów** (stary zwycięzca):
- A wg bag_size: **bag2=143, bag3=64**, bag1=60, bag4=10 → flipy tam, gdzie worki średnie/duże. Godziny Warsaw: 13:00 (39), 18:00 (41), 19:00 (40), 17:00 (31), 12/14/15/16 ~25 — lunch + wieczorny peak.
- B wg bag_size: bag2=92, bag1=72, bag3=29. Godziny: **18:00 (38)** wyraźny szczyt, reszta 12-19 równomiernie ~18-23.

## 4. Przegląd ręczny — przykłady flipów

### BUG A (top decyzyjne + reprezentatywne; old = obecny zwycięzca rankingu, new = po karze)

| oid | ts | old → new | score old/new | kara old/new | bag o/n | sum_bt o/n | max_bt o/n | fifo o/n | ocena |
|---|---|---|---|---|---|---|---|---|---|
| 478435 | 06-04 18:04 | Gabriel(179) → Dariusz M(509) | −14.9/−24.1 | −103.8/−10.2 | 3/3 | 76.5/6.0 | 31.8/6.0 | 1/0 | ✓ sensowny: old max_bt 31.8 = strefa danger R6 |
| 479135 | 06-07 18:39 | Bartek O.(123) → Michał Rom(520) | 0.1/−10.9 | −102.0/−12.3 | 3/2 | 72.8/7.2 | 27.5/7.2 | 2/0 | ✓ old: 2 naruszenia FIFO + 73 min sumy |
| 479134 | 06-07 18:38 | Bartek O. → Michał Rom | 0.2/−10.0 | −98.1/−10.4 | 3/2 | 69.6/6.1 | 26.4/6.1 | 2/0 | ✓ (siostrzany order j.w.) |
| 479013 | 06-07 14:58 | Gabriel J(503) → Andrei K(484) | −8.4/−8.4 | −102.8/−26.2 | 3/2 | 72.8/20.5 | 28.7/8.1 | 2/0 | ✓ remis score, kara rozstrzyga termicznie |
| 478422 | 06-04 17:37 | Michał Rom(520) → Gabriel(179) | −14.1/−31.9 | −109.2/−16.4 | 3/2 | 89.7/9.6 | 27.8/9.6 | 0/0 | ~ new jedzie 8 km do pickupu (km_pu 0.65→8.0) — kara SUM przepala −18 pkt score |
| 479198 | 06-07 20:25 | Grzegorz(500) → Mateusz Bro(409) | −20.9/−22.2 | −93.4/−17.4 | 3/4 | 70.1/10.2 | 26.1/10.2 | 1/0 | ✓ |
| 478410 | 06-04 17:41 (KOORD) | Michał Rom → Grzegorz W(289) | −18.8/−25.5 | −107.3/−29.7 | 3/3 | 70.1/19.9 | 31.8/14.0 | 3/0 | ✓ old: 3×FIFO + max_bt 31.8 |
| 478061 | 06-03 13:03 | Adrian Cit(457) → Dariusz M(509) | −10.0/−11.5 | −41.7/−23.8 | 1/3 | 28.7/16.7 | 18.5/10.2 | 0/0 | ✓ subtelny, margines mały |
| 478583 | 06-05 12:50 | Mateusz O(413) → Jakub OL(370) | −1.8/**−82.0** | −111.6/−14.9 | 3/4 | 88.3/8.8 | 26.0/8.8 | 1/0 | ⚠ kara −111.6 przeskakuje 80 pkt score — przykład DOMINACJI komponentu SUM |
| 479076 | 06-07 16:53 | Filip P(354) → Marek(207) | −23.7/−41.6 | −44.8/−10.2 | 2/2 | 34.7/6.0 | 14.4/6.0 | 0/0 | ~ old wcale nie był zły (max_bt 14.4); flip czysto od SUM |

Wzorzec: zdecydowana większość flipów A = odbieranie zleceń kurierom z workiem 3 zam. (sum 70-90 min, max_bt 26-32 — tuż pod hard 35) na rzecz luźniejszych. Termicznie słuszne, ale 2 ostatnie przykłady pokazują, że komponent SUM flipuje też wtedy, gdy nierównomierności NIE ma — stąd rekomendacja SUM=0 na start.

### BUG B

| oid | ts | old → new | score o/n | kara o/n | detour o/n km | km_pu o/n | ocena |
|---|---|---|---|---|---|---|---|
| 478088 | 06-03 14:22 | Tomasz Ch(514) → Adrian Cit(457) | −2.2/−2.6 | −62.8/0 | **8.35**/0.10 | 3.4/2.5 | ✓✓ podręcznikowy Case C: drive 15→0.4 min |
| 479313 | 06-08 16:50 | Piotr Zaw(470) → Andrei K(484) | −5.6/−7.1 | −49.8/−3.0 | 6.72/0.87 | 1.9/5.1 | ✓ drive 14→3.2 min |
| 478549 | 06-05 11:35 | Andrei K(484) → Adrian R(400) | 1.0/−6.0 | −52.2/0 | 7.02/0 | 0/**10.1** | ~ new aż 10 km od restauracji — kara zdominowała; po drodze vs daleko-ale-prosto |
| 478794 | 06-06 13:29 | Grzegorz W(289) → Andrei K(484) | 6.2/4.7 | −43.2/0 | 5.90/0 | 3.3/1.5 | ✓ |
| 478043 | 06-03 12:12 | Piotr Zaw(470) → Dariusz M(509) | −3.4/−4.6 | −42.2/0 | 5.78/0.19 | 1.2/6.8 | ✓ drive 4→1.2 min |
| 477801 | 06-02 12:22 | Piotr Zaw(470) → Bartek O.(123) | −0.1/−7.4 | −43.4/0 | 5.93/0 | 0.6/7.2 | ~ analogicznie 478549 |
| 478121 | 06-03 16:35 | Tomasz Ch(514) → Dariusz M(509) | −10.2/−14.1 | −38.2/0 | 5.27/0 | 11.1/8.0 | ✓ |
| 479289 | 06-08 15:24 | Jakub OL(370) → Piotr Ku(531) | −17.4/−39.4 | −29.9/0 | 4.24/0 | 6.5/0.9 | ✓ new pod restauracją |
| 478185 | 06-03 19:23 | Patryk(75) → Michał K.(393) | −19.8/−30.6 | −29.9/−11.3 | 4.24/1.91 | 7.0/4.9 | ✓ |
| 478445 | 06-04 18:26 | Michał Rom(520) → Mateusz O(413) | −5.2/−9.7 | −15.1/−2.7 | 2.39/0.84 | 6.9/5.1 | ✓ margines mały, oba OK |

Wzorzec: flipy B eliminują detoury 4-8 km (dokładnie case'y A/C ze speca). Ryzyko: w 2-3/10 przykładach nowy zwycięzca jest DALEKO od pickupu (7-10 km) — kara 8/km przepala karę dojazdu R4; argument za startem od 4.0/km.

## 5. Outcome join (backfill_decisions_outcomes_v1, join po oid; 1787 PROPOSE z realnym `pickup_to_delivery_min`, w tym 347 gdzie kurier finalny == best)

- **BUG A** (kwartyle \|kara_A\| best, granice 17/25/47 pkt; podzbiór same-courier n=346): mediana realnego pickup→delivery **Q1 9.7 → Q2 14.2 → Q3 19.3 → Q4 20.7 min** — w pełni monotoniczna. (Cały zbiór n=1786: 11.6/17.3/20.4/19.1 — prawie monotonia.)
- **BUG B** (buckety detour; same-courier n=157): **≤0.5 km → 13.9 min; 0.5-1 km → 17.2; >1 km → 19.0 min** — monotonia, detour>1 km kosztuje ~+5 min mediany realnego czasu doręczenia.
- ⚠ Interpretacja uczciwie: to walidacja PREDYKCYJNA (metryki shadow poprawnie przewidują realny czas), nie kauzalny dowód, że flip poprawi outcome — kandydatów alternatywnych nigdy nie wysłano. Razem z sanity flipów (§3) to najlepszy osiągalny sygnał bez flipu — kolejne tygodnie shadow NIE dadzą nic więcej.

## 6. Werdykt szczegółowy per flaga

### BUG B — (a) FLIP
- **Z czym:** `ENABLE_R5_PICKUP_DETOUR_PENALTY=1`, **`R5_DETOUR_PENALTY_PER_KM=4.0`** (env override; default w kodzie zostaje 8.0), `R5_DETOUR_FREE_THRESHOLD_KM=0.5` bez zmian. Oczekiwany efekt: ~7.4% propozycji zmienia kuriera, mediana kary 7 pkt / p90 20 pkt, nowe-KOORD 0.2% (≈4 dziennie⁄9 dni → <0.5/dzień). Po ≥7 dniach czystych metryk → podnieść do 8.0/km (flip rate ~12.6%) decyzją Adriana.
- **Dlaczego:** najczystszy sygnał całego sprintu — 199/199 flipów redukuje detour, outcome monotoniczny, wprost reguła Adriana („dowóz w żaden sposób nie jest po drodze"), spec i tak planował B jako pierwszy flip ~02.06.
- **Kill-switch:** usunąć env / `ENABLE_R5_PICKUP_DETOUR_PENALTY=0` w override.conf + restart dispatch-shadow.

### BUG A — (a) FLIP komponentów max+FIFO / świadome ZAMKNIĘCIE komponentu SUM w obecnej postaci
- **Z czym:** `ENABLE_BAG_TIME_FAIRNESS_SCORING=1` + **`BAG_TIME_SUM_PENALTY_PER_MIN=0.0`** + `BAG_TIME_MAX_PENALTY_PER_MIN=0.7` + `BAG_TIME_FIFO_TIE_PENALTY=5.0`. Efekt: flip 7.7%, kara p50 9.4 / p90 22.2 pkt, nowe-KOORD 0.3%. Flipować ≥7 dni PO flipie B (nie naraz).
- **Dlaczego max+FIFO tak:** mierzą dokładnie intencję Adriana („lepiej oba po 15 niż 25+8" = max; „najpierw to co wcześniej odebrane" = FIFO); 13.7% propozycji ma ≥1 naruszenie FIFO; flipy koncentrują się na workach z max_bt 26-32 min (strefa danger R6).
- **Dlaczego SUM nie:** Σ bag_time rośnie liniowo z liczbą zamówień w worku (śr. kara 20→64 pkt dla bag 0→4) — karze bundling jako taki, kolidując z ekonomiką worka (Bartek 2.0: funkcja celu PLN, bundling=marża) i NIE odróżnia worka „3×15 min" (dobry) od „25+8" (zły). Solo generuje 3.3% nowych KOORD. Wniosek: w tej formie zamknięty; jeśli Adrian chce komponent sumy — przeprojektować na średnią per order albo nadwyżkę ponad target (np. Σ max(0, bag_time−20)) i skalibrować nowym 7-dniowym shadow. To zmiana KODU, nie stałych — osobny ticket.

### Sekwencja i zakazy
1. Flip B (4.0/0.5) → 7 dni watch → ewentualna eskalacja 8.0.
2. Flip A max+FIFO (SUM=0) → 7 dni watch.
3. **NIE flipować obu naraz w defaultach** (7.8% nowych KOORD, flip 21.2%). Wariant soft obu naraz technicznie bezpieczny (0.5%), ale sekwencyjnie = czysta atrybucja.

## 7. Watch-metryki po flipie (3-7 dni, porównanie z baseline tego raportu)

| metryka | baseline | alarm |
|---|---|---|
| KOORD share (dziennie, shadow_decisions) | 14.4% (324/2254) | wzrost >+2 p.p. utrzymany 2 dni → rollback (ALWAYS-PROPOSE) |
| odsetek best z niezerowym `bonus_r5_pickup_detour_penalty` | 0% (OFF) → oczekiwane ~43% | 0% po flipie = env nie zadziałał |
| odsetek best z niezerowym `bonus_bag_time_max` | 0% → oczekiwane ~100% | j.w. |
| mediana `pickup_to_delivery_min` (backfill, dziennie) | ~17-18 min | wzrost >+3 min utrzymany |
| mediana detour best | 2.01 km | oczekiwany SPADEK (to cel B) |
| share best z max_bt > 25 min | (policzyć w dniu flipu) | oczekiwany spadek (cel A) |
| bundling: share best z r6_bag_size ≥ 2 | (policzyć w dniu flipu) | spadek >10 p.p. = kara za mocna ekonomicznie |
| PANEL_OVERRIDE rate (backfill `action`) | 1410/3456 hist. | wyraźny wzrost = operator nie ufa nowym wyborom |

**Wykonanie flipu (Adrian/sesja silnika, NIE ta sesja):** wpisy `Environment=` w `/etc/systemd/system/dispatch-shadow.service.d/override.conf` (wzorzec jak flipy a2/c2/etap4 — .bak przed edycją), `systemctl daemon-reload && systemctl restart dispatch-shadow` POZA peakiem 11-14/17-20; flagi NIE są w flags.json (env-only, brak hot-reload).

---
*Dane: shadow_decisions.jsonl(+.1) 2254 rekordów 02-11.06; backfill outcomes 3456 rekordów; stałe z common.py HEAD 11.06. Liczby ±2 rekordy (żywy log). Analiza read-only — żadna flaga/usługa/crontab nie zostały dotknięte.*
