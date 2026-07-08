# 02 — Research: kalibracja ETA per-kurier w dostawach last-mile

> Zadanie badawcze (WEB). Cel: zebrać wiarygodne, branżowe i naukowe podejścia do predykcji/kalibracji ETA
> — ze szczególnym naciskiem na przypadki personalizowane / per-kurier oraz na sytuację **małego sygnału
> per-kurier** (nasz problem: wariancja wewnątrz-kurierska ≫ sygnał między-kurierski).
>
> **Zasada źródeł:** cytujemy wyłącznie blogi inżynierskie operatorów (Uber/DoorDash/Lyft/Instacart/Baidu),
> peer-review (KDD/CIKM/NeurIPS) i arXiv. Każde twierdzenie ma tytuł + link + rok. Gdzie źródła nie ma —
> jest to zaznaczone wprost. Referencje klasyczne (James-Stein, empirical Bayes, walk-forward) podawane są
> jako ugruntowana literatura (tytuł+rok), a nie jako pobrane strony.

## Spis treści
1. [TL;DR — najważniejsze wnioski](#1-tldr)
2. [Konkretne podejścia branżowe do ETA](#2-podejscia-branzowe)
3. [Branżowe metryki sukcesu ETA](#3-metryki)
4. [Techniki do rozważenia (z „kiedy używać / pułapka")](#4-techniki)
5. [Pułapki i lekcje — co NIE działa](#5-pulapki)
6. [Wnioski dla NASZEGO przypadku (~50 kurierów)](#6-wnioski)
7. [Źródła](#7-zrodla)

---

<a name="1-tldr"></a>
## 1. TL;DR

- Branża **nie uczy ETA end-to-end od zera**. Dominuje wzorzec **residual post-processing**: fizyczny/routingowy
  model liczy bazę, a ML koryguje **residuum** (Uber DeepETA/DeeprETA). To dokładnie nasz układ (OSRM+tabela ruchu = baza).
- **Asymetria kosztu jest standardem**, nie egzotyką: Uber używa **asymmetric Huber loss**, Instacart/DoorDash
  **quantile loss** (q≈0.9), bo spóźnienie kosztuje więcej niż zbyt wczesna obietnica. Wybór kwantyla > 0.5 to
  świadoma decyzja biznesowa, nie „bug".
- **Personalizacja per-kierowca istnieje, ale przez adaptację/meta-learning i pooling, nie przez naiwny model
  per-osoba** (Baidu SSML). Naiwny addytywny residual per-kurier na małej próbie zawodzi — co pokrywa się z naszym
  wynikiem i z teorią shrinkage (James-Stein).
- **Walidacja MUSI być czasowa (walk-forward / rolling-origin)**, nigdy losowy split — inaczej wyciek z przyszłości
  daje fałszywie dobre metryki.
- Dla **małego sygnału per-kurier** właściwe narzędzia to: **partial pooling / empirical Bayes shrinkage**,
  **kondycjonowanie na obserwowalne cechy** (obciążenie, pora, leg odbiór/dostawa) zamiast „tożsamości kuriera",
  oraz **przedziały z gwarancją pokrycia (conformal / CQR)** zamiast punktu.

---

<a name="2-podejscia-branzowe"></a>
## 2. Konkretne podejścia branżowe do ETA / kalibracji ETA

### 2.1 Uber — DeepETA / DeeprETA (residual post-processing) ⭐ najbliższe nam
- **Kto / tytuł / link:** Uber, „DeepETA: How Uber Predicts Arrival Times Using Deep Learning" (2022),
  https://www.uber.com/en-IN/blog/deepeta-how-uber-predicts-arrival-times/ ; paper: Hu et al., „DeeprETA: An ETA
  Post-processing System at Scale" (2022), https://arxiv.org/abs/2206.02127
- **Architektura:** silnik routingu liczy ETA jako sumę czasów przejazdu po segmentach; **ML uczy się residuum
  między ETA routingu a rzeczywistym czasem** („ETA post-processing"). Model to **linear transformer** (kernel trick,
  by uniknąć kwadratowej macierzy uwagi), wejścia dyskretyzowane + embeddowane (feature hashing dla geo), a na końcu
  **segment bias adjustment layer** — czyli warstwa kalibracji korygująca surową predykcję per-segment/obszar.
- **Funkcja straty:** **asymmetric Huber loss** — dwa parametry: `delta` (odporność na outliery) i `omega` (stopień
  asymetrii). Asymetria pozwala świadomie karać niedoszacowanie inaczej niż przeszacowanie.
- **Metryki:** wymóg produkcyjny — MAE **istotnie lepsze niż inkumbent XGBoost** (blog/paper podają przewagę
  jakościową; twardych % w blogu brak). Latencja: **mediana 3.25 ms, p95 ~4 ms** — najwyższy QPS w Uberze.
- **Czego uczy nas:** to jest nasz szablon — nie zastępować OSRM, tylko **modelować korektę na bazie**; korekta
  powinna być **kondycjonowana geograficznie/segmentowo i asymetrycznie**, nie addytywnie „na kuriera".

### 2.2 DoorDash — NextGen ETA (multi-task, MoE, probabilistyczny + warstwa decyzyjna)
- **Kto / tytuł / link:** DoorDash, „Improving ETAs with multi-task models, deep learning, and probabilistic
  forecasts", https://careersatdoordash.com/blog/improving-etas-with-multi-task-models-deep-learning-and-probabilistic-forecasts/ ;
  „Precision in Motion: Deep learning for smarter ETA predictions" (2026),
  https://careersatdoordash.com/blog/deep-learning-for-smarter-eta-predictions/ ;
  „Improving ETA Prediction Accuracy for Long-tail Events",
  https://careersatdoordash.com/blog/improving-eta-prediction-accuracy-for-long-tail-events/
- **Architektura:** **MLP-gated mixture-of-experts (MoE)** z trzema enkoderami — **DeepNet + CrossNet + transformer**
  — trenowane **multi-task** sekwencyjnie (transfer wiedzy między zadaniami/etapami dostawy). Kluczowy rozdział:
  **probabilistyczna warstwa bazowa** (predykuje **rozkład**, nie punkt) + **osobna warstwa decyzyjna**, która z tego
  rozkładu rozwiązuje różne problemy optymalizacyjne per biznes (różne progi ryzyka/kwantyle dla różnych celów).
- **Funkcja straty:** **quantile loss** (predykcja wybranego percentyla czasu dostawy); modelowanie niepewności jest
  jawnym celem.
- **Metryki:** raportowane **~20% względnej poprawy dokładności ETA**; „dokładność" definiowana jako **jak często
  dostawa przychodzi on-time względem predykcji**.
- **Czego uczy nas:** (a) **rozdziel predykcję rozkładu od decyzji** — jeden model rozkładu, wiele progów; (b)
  **multi-task per etap** (odbiór vs dostawa) daje transfer i spójność; (c) long-tail (rzadkie, długie zdarzenia) trzeba
  traktować osobno.

### 2.3 Google Maps / DeepMind — Graph Neural Networks (Supersegments)
- **Kto / tytuł / link:** DeepMind, „Traffic prediction with advanced Graph Neural Networks" (2020),
  https://deepmind.google/blog/traffic-prediction-with-advanced-graph-neural-networks/ ; paper: Derrow-Pinion et al.,
  „ETA Prediction with Graph Neural Networks in Google Maps" (CIKM 2021), https://arxiv.org/abs/2108.11482
- **Architektura:** sieć drogowa jako **graf** (segment = węzeł, krawędzie = sąsiedztwo na drodze/skrzyżowaniu),
  agregowana w **Supersegmenty** dzielące wspólny ruch; GNN łączy **live + historyczny** ruch, prognozuje 15–45 min naprzód.
- **Metryki:** redukcja niedokładności ETA **do 50%** w wybranych miastach (Berlin, Dżakarta, Sydney, Tokio…);
  baza „ponad 97% tras trafnych" wg PM Google Maps.
- **Czego uczy nas:** dla nas mniej bezpośrednie (my mamy OSRM+tabela ruchu jako proxy grafu), ale potwierdza wartość
  **kondycjonowania czasu na stan sieci/ruchu** — u nas największy błąd to **noga ODBIORU zależna od OBCIĄŻENIA**, co jest
  odpowiednikiem „stanu systemu", nie tożsamości kuriera.

### 2.4 Instacart — quantile regression jako „obietnica on-time" ⭐ wzorzec dla asymetrii
- **Kto / tytuł / link:** M. Ripert, „How Instacart delivers on time (using quantile regression)", tech.instacart.com,
  https://tech.instacart.com/how-instacart-delivers-on-time-using-quantile-regression-2383e2e03edb
- **Podejście:** ETA to **wiążąca obietnica** okna dostawy; zamiast średniej liczą **q=0.9 quantile regression** jako
  **górne ograniczenie** czasu, tak by `ETA + błąd < termin` zachodziło ~90% razy. Zamiast **stałego bufora** (który był
  suboptymalny, bo ryzyko zależy od dystansu/warunków) kwantyl **adaptuje bufor** do kontekstu.
- **Metryki / impakt:** przy tym samym odsetku spóźnień udało się **planować bliżej terminu** i podnieść **efektywność
  o ~4%**; stały bufor dawał ~10% spóźnień w rynkach.
- **Czego uczy nas:** jeśli ETA jest obietnicą, **celuj w kwantyl > 0.5, nie w średnią**; bufor ma być **warunkowy**
  (u nas: warunkowany obciążeniem i etapem odbioru), nie płaski.

### 2.5 Lyft — „ETA Reliability" (niepewność jako klasyfikacja, korekta biasu)
- **Kto / tytuł / link:** R. Naik, „ETA (Estimated Time of Arrival) Reliability at Lyft" (2024), Lyft Engineering,
  https://eng.lyft.com/eta-estimated-time-of-arrival-reliability-at-lyft-d4ca2720bda8
- **Podejście:** definiują **reliability** = prawdopodobieństwo, że kierowca dojedzie **w rozsądnym oknie wokół ETA**;
  model drzewiasty **klasyfikacyjny** zwraca **surowe prawdopodobieństwa** (nie punkt). Obserwują, że **bias jest mały,
  ale rośnie dla większych ETA**; trik treningowy: **duplikacja przejazdu we wszystkich „koszykach" ETA**, by uniknąć
  pętli sprzężenia zwrotnego i wyrównać reprezentację.
- **Czego uczy nas:** (a) mierz **wiarygodność/pokrycie**, nie tylko punktowy błąd; (b) **bias rośnie w ogonie** (długie
  ETA / duże obciążenie) — trzeba go kalibrować warunkowo; (c) uważaj na **sprzężenie zwrotne** (ETA wpływa na decyzje,
  które wpływają na dane — patrz §5).

### 2.6 Baidu Maps — personalizacja przez meta-learning (SSML), GNN kontekstowy (ConSTGAT), propagacja korków (DuETA)
- **Kto / tytuł / link:** Fang et al., „ConSTGAT: Contextual Spatial-Temporal Graph Attention Network for Travel Time
  Estimation at Baidu Maps" (SIGKDD 2020), https://dl.acm.org/doi/10.1145/3394486.3403320 ; Fang et al., „SSML:
  Self-Supervised Meta-Learner for En Route Travel Time Estimation at Baidu Maps" (SIGKDD 2021),
  https://dl.acm.org/doi/10.1145/3447548.3467060 (PDF: https://huangjizhou.github.io/papers/SSML-KDD21.pdf) ; „DuETA…"
  (CIKM 2022), https://dl.acm.org/doi/10.1145/3511808.3557091
- **Podejście:** **SSML** to meta-learner uczący **meta-wiedzy, która szybko adaptuje się do preferencji/stylu jazdy
  konkretnego użytkownika** en-route — czyli **personalizacja bez trenowania osobnego modelu na osobę** (adaptacja z
  niewielu obserwacji). ConSTGAT modeluje kontekst przestrzenno-czasowy, DuETA — propagację korków.
- **Czego uczy nas:** **to jest właściwa forma per-kurier**: nie osobny model/residual na kuriera, tylko **wspólny model
  + lekka adaptacja** (meta-learning / pooling), która działa przy małej próbie na osobę.

### 2.7 Kontekst akademicki last-mile / food-delivery (do pogłębienia)
- Gao et al., „A Deep Learning Method for Route and Time Prediction in Food Delivery Service" (**FDNET**, SIGKDD 2021),
  https://dl.acm.org/doi/10.1145/3447548.3467068 — LSTM-enkoder + Pointer-dekoder przewiduje **trasę i czas naraz**
  (przydatne, bo u nas TSP+ETA są sprzężone).
- Wen et al., „LaDe: The First Comprehensive Last-mile Delivery Dataset from Industry" (2023),
  https://arxiv.org/abs/2306.10675 — 10M+ paczek, 21k kurierów; benchmark realiów last-mile.
- „DRL4Route: A Deep Reinforcement Learning Framework for Pick-up and Delivery Route Prediction" (2023),
  https://arxiv.org/abs/2307.16246 — predykcja tras odbiór+dostawa.

---

<a name="3-metryki"></a>
## 3. Branżowe metryki sukcesu ETA

Jak przemysł mierzy jakość ETA (z powyższych źródeł + praktyki):

| Metryka | Co mierzy | Uwaga / typowe wartości |
|---|---|---|
| **MAE** (mean absolute error) | średni błąd w minutach | główny cel Ubera (DeeprETA vs XGBoost); **stabilny, interpretowalny** — preferowany dla ETA |
| **RMSE** | błąd z karą za duże odchyłki | wrażliwy na outliery (długie ogony) — dobry gdy zależy nam na tail |
| **MAPE** | błąd procentowy | **niestabilny dla krótkich czasów** (dzielenie przez małe wartości → eksplozja) — patrz §5 |
| **on-time rate** | % dostaw w oknie / on-time wg predykcji | definicja „accuracy" DoorDash; Instacart celuje ~90% (q=0.9) |
| **reliability / interval coverage** | % przypadków w przedziale [lo, hi] | Lyft: prawdopodobieństwo dojazdu w oknie; conformal daje **gwarancję pokrycia** |
| **pinball / quantile loss** | jakość predykcji kwantyla | właściwa funkcja celu, gdy ETA to obietnica (Instacart, DoorDash) |
| **bias** (śr. sygnowany błąd) | systematyczny optymizm/pesymizm | Lyft: bias mały, ale **rośnie dla dużych ETA**; u nas optymizm +9 min |

**Wniosek metrologiczny:** raportuj **MAE + bias + pokrycie przedziału (calibration/reliability) + pinball loss**
łącznie. Sam MAE ukrywa asymetrię i systematyczny optymizm; samo on-time ukrywa, jak bardzo się mylimy.

---

<a name="4-techniki"></a>
## 4. Techniki do rozważenia (każda: kiedy używać / pułapka)

### 4.1 Regresja kwantylowa (quantile / pinball loss, LightGBM `objective=quantile`)
- **Źródła:** Instacart (§2.4); DoorDash quantile loss (§2.2); Koenker & Bassett, „Regression Quantiles" (1978,
  Econometrica) — kanon quantile regression.
- **Kiedy:** gdy ETA to **obietnica** i koszt jest asymetryczny → predykuj kwantyl (np. q=0.8–0.9) zamiast średniej;
  LightGBM wspiera to natywnie (pinball loss karze niedo- i przeszacowanie różnymi wagami τ / 1−τ).
- **Pułapka:** **crossing quantiles** (q90 < q50 przy osobnych modelach) — trzeba wymuszać monotoniczność; kwantyl
  wysoki „psuje" MAE (bo to nie jest estymator średniej — i tak ma być).

### 4.2 Modele hierarchiczne / partial pooling & shrinkage (empirical Bayes, James-Stein) ⭐ kluczowe dla nas
- **Źródła:** James & Stein (1961); Efron & Morris, „Stein's Paradox in Statistics" (Scientific American, 1977);
  Gelman & Hill, „Data Analysis Using Regression and Multilevel/Hierarchical Models" (2006) — kanon partial pooling.
- **Idea:** estymata per-grupa (per-kurier) = **ważona średnia własnych danych i średniej populacji**; waga zależy od
  **liczby obserwacji i stosunku wariancji wewnątrz- do między-grupowej**. Grupy z małą próbą są **mocno ściągane** do
  średniej; z dużą — prawie nie. **James-Stein:** przy k ≥ 3 grupach shrinkage ma **niższe łączne MSE** niż surowe średnie.
- **Kiedy:** **dokładnie nasz przypadek** — ~50 kurierów, mało danych/os., wariancja wewnątrz ≫ między. Empirical Bayes
  **automatycznie** dobiera siłę ściągania z danych.
- **Pułapka:** jeśli sygnał między-kurierski jest bliski zeru, shrinkage **słusznie** ściągnie prawie do zera —
  i wtedy „efekt kuriera" po prostu **nie istnieje**; nie wolno go sztucznie wzmacniać. To spójne z naszą porażką
  addytywnego residuala per-kurier.

### 4.3 Residual calibration (korekta residualna na predykcji bazowej)
- **Źródła:** Uber DeepETA/DeeprETA (§2.1) — ML uczy residuum nad routingiem; isotonic/Platt jako post-hoc kalibracja
  (klasyczne post-processing; przegląd np. „Post-Hoc Calibration Methods").
- **Kiedy:** gdy masz **dobry model fizyczny** (OSRM+tabela ruchu) — nie wyrzucaj go, **modeluj korektę**. Korektę
  kondycjonuj na cechy (obciążenie, pora, leg), nie tylko na tożsamość. Isotonic regression daje **monotoniczne**
  mapowanie surowej predykcji na skalibrowaną bez założeń parametrycznych.
- **Pułapka:** residual **end-to-end per-kurier** na małej próbie **przeuczą** (nasz wynik). Kalibracja musi być
  **na cesze o wysokim sygnale** (obciążenie/etap), a nie na cesze o niskim sygnale (kto to jest).

### 4.4 Conformal prediction / CQR (przedziały z gwarancją pokrycia)
- **Źródła:** Romano, Patterson, Candès, „Conformalized Quantile Regression" (NeurIPS 2019),
  https://arxiv.org/abs/1905.03222 ; „Conformal Predictive Distributions for Order Fulfillment Time Forecasting"
  (2025), https://arxiv.org/abs/2505.17340
- **Kiedy:** gdy chcesz **przedział ETA z gwarancją pokrycia** (np. 90%) **bez założeń o rozkładzie**. **Split
  conformal** kalibruje na osobnym zbiorze; **CQR** owija regresję kwantylową w conformal → przedziały **adaptują się do
  heteroskedastyczności** (szersze przy dużym obciążeniu) i **zachowują pokrycie**. Idealne, gdy niepewność zależy od
  obciążenia.
- **Pułapka:** conformal daje pokrycie **marginalne, nie warunkowe** — globalne 90% może być nierówne per-segment
  (np. gorsze w peak). Trzeba **kalibrować per-warstwa** (osobno peak/off-peak, osobno leg odbiór/dostawa) lub użyć
  wariantów Mondrian/grupowych. Wymaga **świeżego** zbioru kalibracyjnego (dryf w czasie).

### 4.5 Walk-forward / rolling-origin validation (NIE losowy split)
- **Źródła:** Bergmeir & Benítez, „On the use of cross-validation for time series predictor evaluation" (Information
  Sciences, 2012); López de Prado, „Advances in Financial Machine Learning" (2018) — purging/embargo.
- **Kiedy:** **zawsze** przy ETA. Trenuj na danych do czasu t, testuj na t+; okno przesuwaj naprzód. To symuluje realny
  deployment i mierzy **out-of-time** (nasz właściwy benchmark). Dodaj **gap/purging** między train a test, by odciąć
  wyciek przez cechy sąsiadujące w czasie.
- **Pułapka:** **losowy k-fold miesza czas → wyciek z przyszłości → fałszywie świetne metryki**, które walą się na
  produkcji. To najczęstszy powód „działało na walidacji, nie działa live".

### 4.6 Pokrycie przedziałów / reliability (calibration curve)
- **Źródła:** Lyft reliability (§2.5); conformal coverage (§4.4).
- **Kiedy:** raportuj **empiryczne pokrycie** (jaki % obserwacji wpadł w [lo,hi]) vs nominalne; rysuj krzywą kalibracji.
  To jedyny sposób sprawdzić, czy „90%" naprawdę znaczy 90%.
- **Pułapka:** dobra ostrość (wąski przedział) przy złym pokryciu = obietnica bez pokrycia; szeroki przedział z idealnym
  pokryciem = bezużyteczny. Optymalizuj **pinball / interval score**, nie samo pokrycie.

### 4.7 Asymetryczny koszt (spóźnienie droższe niż zbyt wczesna obietnica)
- **Źródła:** Uber asymmetric Huber (§2.1); Instacart q=0.9 (§2.4); DoorDash quantile loss (§2.2).
- **Kiedy:** gdy spóźnienie boli bardziej niż zapas → **wybierz kwantyl > 0.5** albo asymetryczną stratę
  (Huber z `omega`). Poziom kwantyla = **decyzja biznesowa** wyprowadzona z relacji kosztów (koszt_late / koszt_early).
- **Pułapka:** za wysoki kwantyl → ETA chronicznie zawyżone → utrata zaufania/efektywności (Instacart pokazuje, że
  chodzi o **najciaśniejszy** bufor trzymający target on-time, nie o „im później tym bezpieczniej").

---

<a name="5-pulapki"></a>
## 5. Pułapki i lekcje — co NIE działa

1. **Naiwny residual/model per-kurier na małej próbie → overfitting.** Wariancja wewnątrz-kurierska ≫ sygnał
   między-kurierski (nasz zmierzony wynik). Teoria (James-Stein / empirical Bayes, §4.2) mówi: bez ściągania estymaty
   per-grupa mają **wyższe MSE** niż model wspólny. **Lekcja:** pooling/shrinkage albo kondycjonowanie na cechę, nie na ID.
2. **Kalibracja do ludzkiej obietnicy zamiast do rzeczywistości uczy biasów.** Jeśli target to „co obiecał
   dyspozytor/silnik", a nie **fizyczny czas odbioru/dostawy**, model utrwala optymizm. **Lekcja:** ground-truth = zmierzony
   moment (u nas: „oczekiwanie na odbiór" z panelu Rutcom, nie deklaracja z apki).
3. **MAPE niestabilne przy krótkich czasach.** Dzielenie przez małe minuty (krótki dojazd) rozdmuchuje procent. **Lekcja:**
   raportuj **MAE (min) + bias**, MAPE tylko pomocniczo dla długich tras.
4. **Feature leakage z przyszłości / losowy split.** Cecha „znana dopiero po fakcie" (np. rzeczywisty czas postoju) albo
   k-fold mieszający czas → wyciek. **Lekcja:** walk-forward + purging; każda cecha musi być **dostępna w momencie
   predykcji @przypisanie**.
5. **Sprzężenie zwrotne ETA → decyzja → dane.** ETA wpływa na przydział, który wpływa na obserwowany czas (Lyft łagodzi to
   duplikacją koszyków). **Lekcja:** uważaj przy uczeniu na danych generowanych przez własny silnik.
6. **Conformal: pokrycie marginalne, nie warunkowe.** Globalne 90% może maskować niedopokrycie w peak. **Lekcja:**
   kalibruj per-warstwa (peak/off-peak, odbiór/dostawa).
7. **Optymalizacja jednej metryki.** Sam MAE ukrywa asymetrię; samo on-time ukrywa skalę pomyłek. **Lekcja:** panel metryk.
8. **Personalizacja „na siłę".** Baidu SSML pokazuje, że per-user robi się **adaptacją z meta-wiedzy**, nie osobnym modelem —
   inaczej rozbijasz próbę na 50 skrawków bez sygnału.

---

<a name="6-wnioski"></a>
## 6. Wnioski dla NASZEGO przypadku (~50 kurierów, OSRM+tabela ruchu, MAE ~12.5 min, optymizm +9, noga odbioru +18)

1. **Zostań przy residual post-processing nad OSRM (wzorzec Ubera), ale kondycjonuj korektę na OBCIĄŻENIE i ETAP, nie na
   tożsamość kuriera.** Nasz największy błąd (noga odbioru zależna od obciążenia) to sygnał **systemowy/kontekstowy**, nie
   osobowy — dokładnie tam, gdzie residual ma moc. (Uber §2.1, Google §2.3, Lyft „bias rośnie w ogonie" §2.5).
2. **Segmentuj ODBIÓR i DOSTAWA osobno (multi-task / dwa człony).** DoorDash trenuje etapy multi-task z transferem;
   u nas noga odbioru ma inny, większy błąd niż dowóz — osobny model/korekta per-leg zamiast jednego end-to-end. (§2.2)
3. **Dla efektu per-kurier użyj partial pooling / empirical Bayes shrinkage, nie addytywnego residuala.** Ściąganie do
   średniej populacji automatycznie wygasi kurierów z małą próbą; jeśli sygnał między-kurierski jest bliski zeru — shrinkage
   sam to pokaże i nie zaszkodzi (James-Stein, §4.2). To bezpośrednia odpowiedź na naszą wcześniejszą porażkę.
4. **Przejdź z punktu na kwantyl (asymetria kosztu).** Skoro obietnica dostawy to zobowiązanie i optymizm +9 min boli,
   celuj w kwantyl ~0.8–0.9 (LightGBM quantile / asymmetric Huber) zamiast średniej — to samo z siebie zdejmie systematyczny
   optymizm. Poziom kwantyla wyprowadź z relacji koszt_late/koszt_early. (Instacart §2.4, Uber §2.1)
5. **Dodaj przedziały z pokryciem przez CQR/conformal, kalibrowane per-warstwa (peak/off-peak, odbiór/dostawa).** Da to
   ETA z realną gwarancją (np. 90%) i szerszy zapas w peak, gdzie obciążenie rośnie — bez założeń o rozkładzie. (§4.4)
6. **Waliduj wyłącznie walk-forward / out-of-time z gap/purging.** Nasza wcześniejsza regresja addytywna „pogorszyła MAE
   out-of-time" — to właściwy, jedyny wiarygodny benchmark; nigdy losowy split. (§4.5)
7. **Ucz się do RZECZYWISTOŚCI, nie do obietnicy silnika.** Ground-truth = zmierzony czas odbioru/dostawy (panel Rutcom
   „oczekiwanie na odbiór"), inaczej utrwalimy optymizm. (§5.2)
8. **Raportuj panel metryk (MAE + bias + pokrycie + pinball), rozbity per-leg i per-okno.** Sam MAE ukrył nam asymetrię;
   docelowo mierz on-time-vs-predykcja jak DoorDash oraz reliability jak Lyft. (§3)

---

<a name="7-zrodla"></a>
## 7. Źródła

**Blogi inżynierskie operatorów (primary):**
- Uber — „DeepETA: How Uber Predicts Arrival Times Using Deep Learning" (2022) — https://www.uber.com/en-IN/blog/deepeta-how-uber-predicts-arrival-times/
- DoorDash — „Improving ETAs with multi-task models, deep learning, and probabilistic forecasts" — https://careersatdoordash.com/blog/improving-etas-with-multi-task-models-deep-learning-and-probabilistic-forecasts/
- DoorDash — „Precision in Motion: Deep learning for smarter ETA predictions" (2026) — https://careersatdoordash.com/blog/deep-learning-for-smarter-eta-predictions/
- DoorDash — „Improving ETA Prediction Accuracy for Long-tail Events" — https://careersatdoordash.com/blog/improving-eta-prediction-accuracy-for-long-tail-events/
- Instacart — M. Ripert, „How Instacart delivers on time (using quantile regression)" — https://tech.instacart.com/how-instacart-delivers-on-time-using-quantile-regression-2383e2e03edb
- Lyft — R. Naik, „ETA (Estimated Time of Arrival) Reliability at Lyft" (2024) — https://eng.lyft.com/eta-estimated-time-of-arrival-reliability-at-lyft-d4ca2720bda8
- DeepMind — „Traffic prediction with advanced Graph Neural Networks" (2020) — https://deepmind.google/blog/traffic-prediction-with-advanced-graph-neural-networks/

**Peer-review / arXiv:**
- Hu et al. — „DeeprETA: An ETA Post-processing System at Scale" (2022) — https://arxiv.org/abs/2206.02127
- Derrow-Pinion et al. — „ETA Prediction with Graph Neural Networks in Google Maps" (CIKM 2021) — https://arxiv.org/abs/2108.11482
- Fang et al. — „ConSTGAT: Contextual Spatial-Temporal Graph Attention Network… at Baidu Maps" (SIGKDD 2020) — https://dl.acm.org/doi/10.1145/3394486.3403320
- Fang et al. — „SSML: Self-Supervised Meta-Learner for En Route Travel Time Estimation at Baidu Maps" (SIGKDD 2021) — https://dl.acm.org/doi/10.1145/3447548.3467060 (PDF: https://huangjizhou.github.io/papers/SSML-KDD21.pdf)
- „DuETA: Traffic Congestion Propagation Pattern Modeling… at Baidu Maps" (CIKM 2022) — https://dl.acm.org/doi/10.1145/3511808.3557091
- Gao et al. — „A Deep Learning Method for Route and Time Prediction in Food Delivery Service" (FDNET, SIGKDD 2021) — https://dl.acm.org/doi/10.1145/3447548.3467068
- Wen et al. — „LaDe: The First Comprehensive Last-mile Delivery Dataset from Industry" (2023) — https://arxiv.org/abs/2306.10675
- „DRL4Route: A DRL Framework for Pick-up and Delivery Route Prediction" (2023) — https://arxiv.org/abs/2307.16246
- Romano, Patterson, Candès — „Conformalized Quantile Regression" (NeurIPS 2019) — https://arxiv.org/abs/1905.03222
- „Conformal Predictive Distributions for Order Fulfillment Time Forecasting" (2025) — https://arxiv.org/abs/2505.17340

**Literatura klasyczna (ugruntowana, cytowana tytuł+rok):**
- Koenker & Bassett — „Regression Quantiles" (Econometrica, 1978) — kanon regresji kwantylowej / pinball loss
- James & Stein (1961); Efron & Morris — „Stein's Paradox in Statistics" (Scientific American, 1977) — shrinkage / James-Stein
- Gelman & Hill — „Data Analysis Using Regression and Multilevel/Hierarchical Models" (2006) — partial pooling / empirical Bayes
- Bergmeir & Benítez — „On the use of cross-validation for time series predictor evaluation" (Information Sciences, 2012) — walidacja czasowa
- López de Prado — „Advances in Financial Machine Learning" (2018) — purging/embargo przeciw wyciekowi

**Uwaga o dostępności:** blogi DoorDash oraz PeerJ „Ten quick tips for improving ETA predictions using ML" (cs-3259,
2026, https://peerj.com/articles/cs-3259/) zwracały HTTP 403 przy pobieraniu — treść DoorDash zrekonstruowano z metadanych
wyszukiwania i tytułów; PeerJ podano jako dodatkową, wiarygodną referencję praktyczną bez cytowania konkretnych fragmentów
(nie udało się pobrać treści → nie przypisano jej konkretnych twierdzeń).
