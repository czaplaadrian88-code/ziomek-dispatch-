# WERDYKT SCORE-09/10 — proporcjonalna kara za R6-doomed picked_up>35: NIE WDRAŻAĆ (2026-06-14)

**Pytanie:** order JUŻ ODEBRANY (picked_up) z bag_time>35 min (R6 "doomed", jedzenie
za stare) przechodzi feasibility jako `feasible` (35-min hard reject działa tylko
przy INSERCJI nowego ordera, nie do ordera już niesionego). Kurier wiozący doomed
bag NIE dostaje kary score przy rozważaniu do NOWYCH przydziałów. Czy proporcjonalna
kara score za doomed picked_up>35 ma materialny efekt — czy to no-op jak odrzucony
06-11 carry-overlap cap?

**Skrypt:** `eod_drafts/2026-06-14/score09_doomed_pickup_penalty_measure.py` (READ-ONLY).
**Dane:** shadow_decisions.jsonl(+.1), n=1652 PROPOSE clean (02-14.06, okna skażone
PARSER_DEGRADED + SYNCWORKA wykluczone). Sygnał = `r6_picked_up_violations`
(lista [oid, bag_time] dla JUŻ-ODEBRANYCH >35; rozłączny od `r6_per_order_violations`
= insercyjny hard-reject nowego ordera).

## 1. FREQUENCY

| metryka | n | % PROPOSE |
|---|---|---|
| PROPOSE clean | 1652 | — |
| decyzji z >=1 FEASIBLE doomed picked_up>35 | 566 | **34.3%** |
| z czego doomed = ZWYCIĘZCA (best) | **98** | **5.9%** |
| doomed TYLKO u przegranego (loser-only) | 468 | 28.3% |

Doomed-carry zdarza się często w PULI (34%), ale gdy doomed jest u PRZEGRANEGO,
selekcja i tak go nie wybrała — kara nic nie zmienia. Materialne są tylko decyzje
gdzie doomed WYGRYWA: **98 = 5.9%** (poniżej progu istotności 20%, jak carry).

## 2. MAGNITUDE (worst doomed bag_time per kandydat)

- FEASIBLE doomed kandydaci (n=837): p50=41.5, p95=76.2, max=178.0 min
- Tylko ZWYCIĘZCY (n=98): p50=**39.2**, p95=64.5, max=68.3 min

Gdy doomed wygrywa, mediana to ~39 min — ledwo 4 min nad limitem. Te ordery są
już doomed (jedzenie stare), redirekcja niczego nie cofa — order i tak dojedzie
zimny. Kara zmienia tylko KTO wiezie kolejny order, nie ratuje doomed ordera.

## 3. COUNTERFACTUAL — czy kara FLIPNĘŁABY zwycięzcę

- best=doomed feasible: **98**
- best IS raw score-argmax (kara MOŻE zadziałać): **38**
- best NIE jest score-argmax (wybrany przez warstwę redirect/best-effort/late-pickup —
  kara = NO-OP): **60** (z czego 29 jawnie oflagowane redirect/best_effort)

Symulacja flipów (kara = -COEFF * sum(nadwyżka nad 35), tylko na doomed kandydatów):

| COEFF | flipów / 98 | na DALSZEGO od pickupa | na BARDZIEJ obciążonego |
|---|---|---|---|
| 5.0 | 7 | 2 | 5 |
| 10.0 | 7 | 2 | 5 |
| 20.0 | 9 | 2 | 7 |

Restrykcyjnie (best=doomed ∧ score-argmax-driven, COEFF=10): **7 flipów** w całym
~12-dniowym oknie czystym (1652 decyzji). Kara nasycona — z 5→20 prawie bez wzrostu
(7→9). To near-no-op: ~0.4% decyzji PROPOSE.

## 4. REGRESSION RISK — wysoki i skoncentrowany

7 realnych flipów (COEFF=10, score-argmax-driven), best_bag→new_bag:
`3→0, 3→4, 2→5, 2→3, 3→4, 2→3, 3→3`.

- **6 z 7 flipów przerzuca order na kuriera RÓWNIE lub BARDZIEJ obciążonego**
  (np. 478078: bag 2→5 — prawie pełny worek; tylko 477914 idzie 3→0).
- Część flipów na DALSZEGO od pickupa (478389: 0.5km→5.8km; 478330: 1.2→3.2km).
- Część flipów na MARGINALNYCH doomed (over35_sum 1.4 / 1.9 min = bag 36.4 / 36.9 —
  ledwo za linią): karanie tych = szum, nie sygnał. Non-flipped doomed-score-argmax
  mają nadwyżki gł. 0–1.5 min (mediana ~marginalna).

Karanie doomed-kuriera przerzuca order głównie na floty równie/ bardziej obciążone
lub dalsze — pogarsza, nie poprawia. To ta sama oś termiczna co V4 OBJ_R6_SOFT_DEADLINE
(podwójne liczenie kar czasu — anti-pattern SCORE-03/04).

## WERDYKT

**NIE wdrażać proporcjonalnej kary za R6-doomed picked_up>35.**

Argumenty (zbieżne z odrzuceniem carry-overlap 06-11):
1. **Częstotliwość materialna 5.9%** (doomed=zwycięzca) — daleko pod progiem 20%.
   Loser-only 28% jest nieistotne (selekcja i tak nie wybrała doomed).
2. **Near-no-op: 7 flipów / 1652 decyzji (~0.4%)** w całym oknie czystym; kara
   nasycona (COEFF 5→20 → 7→9 flipów). 60/98 best-doomed wybranych przez warstwę
   nie-score (redirect/best-effort) — tam kara score z definicji nie działa.
3. **Wysokie ryzyko regresji:** 6/7 flipów przerzuca na RÓWNIE/BARDZIEJ obciążonego,
   część na dalszego od pickupa; część fires na marginalnych (~1 min nad 35).
4. **Bezcelowe z natury:** order doomed jest już stary — redirekcja nie ratuje go,
   zmienia tylko kto wiezie KOLEJNY order, za cenę gorszego/obciążonego kuriera.
5. **Podwójne liczenie osi termicznej** z V4 OBJ_R6_SOFT_DEADLINE (anti-pattern
   SCORE-03/04) — sterowniki R6 już zbiły carry do 6.6% (06-11 werdykt).

**Domknięcie:** wpis SCORE-09/10 — część "carry cap" zamknięta 06-11; część
"R6 doomed score-penalty" zamknięta TYM werdyktem. Cała pozycja SCORE-09/10 →
ZAMKNIĘTA jako near-no-op + ryzyko over-correction. (Re-otwarcie tylko gdyby E7
re-tune podniósł doomed-as-winner z powrotem ≥20% — wtedy wrócić do pomiaru.)

*Pomiar READ-ONLY: zero zmian flag/usług/dispatch_state. Liczby ±kilka rekordów
(żywy log; ostatnie 06-14 wpisy dochodziły w trakcie).*
