# GO/NO-GO — kandydat `ENABLE_NO_GPS_NEUTRAL_SCORE_DIST` (v1 `88acde3` REJECTED, v2 `15ecc79` committed)

**Status na 19.07 wieczór (2 aktualizacje po starcie tego zadania):**
- **v1 (`88acde3`) — REJECT** od niezależnej adwersaryjnej recenzji Sol (9
  punktów dowodowych, `plik:linia` każdy). Pełny werdykt:
  `/tmp/claude-0/-root/d17bde9a-a389-46c0-8f2d-440cd023165d/scratchpad/sol_nogps_candidate_review.log`.
- CTO zweryfikował 3 z 9 punktów Sol na kodzie+żywym shadow (866 decyzji):
  **pkt 2 (filtr donorów mediany nie ogranicza się do `feasibility_verdict=="MAYBE"`,
  wciąga też HARD-NO)** — POTWIERDZONY, prawdziwy bug. **pkt 3 (edge-case'y
  niedopokryte testami)** — POTWIERDZONY. **pkt 4 (`post_wave`-dziura:
  `pos_source` może zostać przemianowany na `post_wave`, poza
  `POSITION_UNKNOWN_SOURCES`, więc pass NIE neutralizuje mimo że road_km bywa
  dalej syntetyczny)** — POTWIERDZONY jako REALNY, ale oceniony jako
  MARGINALNY (post_wave wymaga niepustego bagu z konstrukcji, więc
  strukturalnie nie może wchłonąć głównego wzorca no_gps+pusty-worek) →
  decyzja: świadomy wyjątek F2.1c, zostaje, wymaga tylko monitoringu w 48h
  cieniu (sekcja 5a niżej). Pozostałe 6 punktów Sol (#1 OFF-parytet-telemetrii,
  #5 display no_gps-z-kotwicą/pre_shift, #6 timing PRZED selekcją, #7
  tie-break bias, #8 lokalność per-order) rozstrzygnięte jako ŚWIADOME/
  pre-existing/poza zakresem — patrz `eod_drafts/2026-07-19/
  NOGPS_NEUTRAL_SCORE_CANDIDATE.md` sekcja "v2 PO RECENZJI ADWERSARYJNEJ".
- **v2 (`15ecc79`) — ZACOMMITOWANY** (subagent nogps-v2, ta sama gałąź
  `fix/nogps-neutral-score-dist`), naprawia WYŁĄCZNIE punkty potwierdzone
  (#2 donor-filter, #3 edge-case testy, #9 testy funkcjonalne E2E). Pełna
  regresja: **5228 passed / 0 failed** (27 skipped, 8 xfailed) vs v1
  baseline 5222/0 — delta +6 (dokładnie nowe testy). Zero live, flaga nadal
  OFF. **NIE zdeployowany** — `bonus_nogps_neutral_*` telemetria wciąż
  nieobecna w `logs/shadow_decisions.jsonl` na czas pisania tego dokumentu
  (zweryfikowałem: `grep -c bonus_nogps_neutral` = 0).
- **KOREKTA #2 (team-lead, po raporcie nogps-v2):** mój pierwszy
  `donor_filter_match_rate` (sekcja 5b) miał błędną formułę względem
  rzeczywistego kodu silnika — NAPRAWIONE, opisane w sekcji 5b niżej wraz z
  weryfikacją na syntetycznym fixture.
- **NIEZALEŻNA WERYFIKACJA (ja, subagent nogps-measure) punktu 4 Sol/CTO** —
  patrz sekcja 5a: przeliczyłem z surowych danych `pos_source=="post_wave"`
  (pole JUŻ istnieje w logu, niezależnie od telemetrii kandydata) i dostałem
  DOKŁADNIE te same liczby co CTO (8 zwycięstw / 421 zwycięstw target cids)
  — potwierdzenie z osobnego przeliczenia, nie przepisanie cudzej liczby.

**Status pierwotny (nadal aktualny): PRE-STAGE, ZERO zmian w repo.** Ten
dokument proponuje progi decyzyjne dla kroku "48h cienia" opisanego w
`eod_drafts/2026-07-19/NOGPS_NEUTRAL_SCORE_CANDIDATE.md` (sekcja "PLAN
POMIARU SHADOW"), z liczbami policzonymi na REALNYCH danych
`shadow_decisions.jsonl` (16–19.07, 866 decyzji) narzędziem
`measure_nogps_shadow.py` z tego samego katalogu. Progi i metodyka NIE zależą
od tego, czy wdrożona wersja to v1 czy v2 (tylko od kontraktu telemetrii,
który powinien zostać niezmieniony) — ale **flip może dotyczyć WYŁĄCZNIE v2**
(v1 ma status REJECT, nie kwalifikuje się do 48h cienia w obecnej formie).

**Ważne zastrzeżenie:** telemetria kandydata (`bonus_nogps_neutral_*`) jest
NIEOBECNA w dzisiejszych danych, bo kod kandydata jeszcze nie jest
zdeployowany. Progi niżej są przygotowane NA PRZYSZŁOŚĆ — zadziałają dopiero
gdy kod zostanie zmergowany (osobna decyzja/ACK, poza zakresem tego
dokumentu) i 48h realnego cienia (flaga OFF, telemetria ZAWSZE emitowana wg
kontraktu "SHADOW ZAWSZE" z planu kandydata) zbierze się w logu.

## 1. Zmierzony obecny rozmiar buga (baseline, 16–19.07, n=866 decyzji)

Kanoniczna klasyfikacja "pozycja nieznana" = `POSITION_UNKNOWN_SOURCES`
(`courier_resolver.py:842` — no_gps / pre_shift / none / pin /
post_shift_start_synthetic / working_override_synthetic), SZERSZA niż samo
`no_gps`:

| Metryka | Wartość |
|---|---|
| pool-share (unknown, kanon) | 31.5% |
| winner-share (unknown, kanon) | 63.5% |
| **GAP (winner − pool)** | **+32.0pp** |
| pool-share no_gps only | 24.1% |
| winner-share no_gps only | 48.7% |
| GAP no_gps only | +24.6pp |
| mixed-pool head-to-head win-rate (unknown) | 74.0% (n=635 mixed pools) |
| top-2 koncentracja zwycięstw (179+413) | 48.6% |
| cid=179: winner-share vs pool-share | 26.9% vs 11.6% (2.3×) |
| cid=413: winner-share vs pool-share | 21.7% vs 11.9% (1.8×) |

Zgadza się co do rzędu wielkości z memory bug-notu (24.8%/50.5%) — mała
różnica (24.1/48.7 vs 24.8/50.5) wynika z lekko innych granic okna (866 vs
835 decyzji, ten sam log, inny punkt odcięcia). **Kanoniczna (szersza) klasa
unknown pokazuje WIĘKSZY gap (32.0pp) niż wąski no_gps-only (24.6pp)** — fix
kandydata neutralizuje CAŁĄ klasę `POSITION_UNKNOWN_SOURCES`, więc to
kanoniczna liczba jest właściwym celem konwergencji, nie tylko no_gps.

## 2. Wariancja historyczna (dlaczego akurat te progi, nie inne)

Bucket 6h jest ZA SZUMNY na progi decyzyjne: stdev winner-share unknown
30.2pp, zdominowany efektem pory dnia (wieczorne buckety niskiego wolumenu
mają skrajne wartości 1.8%/14.8% albo 87–93%). Bucket dobowy (24h, n=4 dni)
jest stabilniejszy i jest bazą progów poniżej:

| Seria (bucket 24h, n=4 dni) | mean | stdev | zakres |
|---|---|---|---|
| **GAP unknown (winner − pool)** | **33.0%** | **±7.07pp** | [24.6%, 43.9%] |
| winner-share unknown | 64.4% | ±10.89pp | [55.5%, 82.2%] |
| pool-share unknown | 31.4% | ±4.47pp | [25.7%, 38.3%] |
| mixed-pool win-rate unknown | 73.3% | ±9.12pp | [60.2%, 82.9%] |
| KOORD-rate | 5.5% | ±1.15pp | [3.9%, 6.8%] |
| best_effort rate (zwycięzca) | 3.6% | ±5.97pp | [0.0%, 14.0%] |
| mediana km_to_pickup zwycięzców | 3.59km | ±0.194km (5.4% wzgl.) | [3.38, 3.89]km |
| 'cisza' rate (best_effort_low_score i pokrewne) | 0.0% | ±0.00pp | [0.0%, 0.0%] |

**Uwaga n=4 dni:** stdev z n=4 ma sam w sobie szeroki przedział ufności —
progi niżej celowo mają margines bezpieczeństwa (nie tną "na styk" ze
zmierzonym stdev) i powinny być PRZELICZONE gdy 48h realnego cienia +
kolejne dni po flipie dołożą więcej bucketów (docelowo n≥10-14 dni, zanim
stdev będzie traktowany jako "twardy" — to jest PIERWSZE przybliżenie).

## 3. Warunek wstępny (wolumen)

Zanim cokolwiek ocenimy: **min. 300 decyzji** z ≥1 kandydatem
unknown-position w puli, w oknie 48h (zgodnie z planem kandydata "min ~300
decyzji"). Przy obecnym wolumenie (~200–300 decyzji/dzień, prawie każda ma
≥1 kandydata unknown przy pool-share ~31%) 48h powinno bez trudu przekroczyć
ten próg. Jeśli NIE przekroczy — **wydłuż okno**, nie decyduj na
niedostatecznej próbce.

## 4. GŁÓWNE kryterium: konwergencja GAP (winner-share − pool-share)

Liczone na PEŁNYM zagregowanym oknie 48h (nie per-bucket), kontrfaktycznie:
`score_on = score ± bonus_nogps_neutral_dist_delta` wg `bonus_applied` —
dokładnie to, co `measure_nogps_shadow.py` liczy w sekcji "TELEMETRIA
KANDYDATA" → `counterfactual_winner_share_unknown_on`.

| Wynik | gap_on (kanon, unknown) | Decyzja |
|---|---|---|
| SILNE GO | ≤ +5pp | Zbiega tak, jak zakładał oryginalny plan kandydata. |
| GO (akceptowalne) | +5pp < gap_on ≤ +10pp | Prawdziwa poprawa, nie szum: 10pp to ~1.4× zmierzonego stdev GAP (7.07pp) i mniej niż POŁOWA najmniejszego historycznie zaobserwowanego dobowego GAP (24.6pp) → (33−10)/7.07 ≈ 3.2σ poprawy, nie przypadek. |
| NO-GO / do przeanalizowania | gap_on > +10pp | Fix działa w dobrym kierunku, ale niewystarczająco. Sprawdź `bonus_neutral_km_stat`/`bonus_raw_km_stat` per okno (podejrzany #1: mediana liczona z za małej puli "znanych" kotwic w niektórych porach dnia) zanim proponujesz flip. |

**Dodatkowo wymagaj OBU naraz** (nie tylko progu absolutnego): `gap_on ≤
10pp` ORAZ `gap_on ≤ 0.35 × gap_off` (gap_off = rzeczywisty winner-share
minus pool-share w TYM SAMYM 48h oknie, bez neutralizacji). Efektywny próg
to `min(10pp, 0.35×gap_off)` — broni przed przypadkiem, gdy akurat w danym
48h oknie `gap_off` jest niezwykle mały (np. mało no-gps na zmianie) i próg
absolutny przeszedłby "za darmo" bez realnej pracy fixu.

Ten sam wzorzec (własny GAP, ~24.6pp baseline) dla wąskiego widoku
no_gps-only jest RAPORTOWANY dla ciągłości z memory bug-notu, ale NIE jest
samodzielnym kryterium decyzyjnym — kandydat neutralizuje całą klasę
`POSITION_UNKNOWN_SOURCES`, kanon jest właściwym celem.

## 5. POMOCNICZE kryterium: mixed-pool head-to-head win-rate

Cel: 50% (neutralnie). Obecnie 74.0% (n=635), dobowa wariancja mean=73.3%
stdev=9.12pp. Kandydat w planie proponował ±7pp wokół 50%; **przy wsparciu
danych proponuję poluzować do ±10pp** (pasmo 40–60%) jako GO, bo: (a) przy
p≈0.5 wariancja dwumianowa jest MAKSYMALNA — sama zmiana punktu równowagi
może zwiększyć naturalny szum niezależnie od jakości fixu; (b) 7pp to mniej
niż zmierzony dobowy stdev (9.12pp) obecnego, zabugowanego stanu — zbyt
ciasny próg ryzykuje fałszywy NO-GO od zwykłego szumu dnia. Wynik poza
[40%, 60%] → do przeanalizowania, NIE automatyczny NO-GO (to metryka
pomocnicza; GAP z sekcji 4 jest rozstrzygający).

## 5a. OBSERWACYJNE (nie blokujące): residual `post_wave` — Sol pkt 4 / CTO "świadomy wyjątek"

Mechanizm (Sol REJECT pkt 4, `core/candidates.py:1737-1754`): `pos_source`
bywa przemianowany na `"post_wave"` niezależnie od tego, czy leżący pod spodem
`road_km` jest wciąż syntetyczny. Pass neutralizujący wymaga
`pos_source ∈ POSITION_UNKNOWN_SOURCES`, a `post_wave` jest w
`INFORMED_POS_SOURCES` (`dispatch_pipeline.py:568-577`) — więc taki kandydat
NIE zostanie zneutralizowany, nawet jeśli jego pozycja jest fikcją. CTO ocenił
to jako REALNE ale MARGINALNE, bo `post_wave` wymaga NIEPUSTEGO bagu z
konstrukcji (kurier musi mieć za sobą falę) — kurier z PUSTYM workiem (główny,
dominujący wzorzec buga) NIGDY nie może być sklasyfikowany jako `post_wave`.
Decyzja: świadomy wyjątek F2.1c, zostaje, wymaga monitoringu.

**Niezależna weryfikacja (ja, nie tylko powtórzenie liczby CTO):**
`pos_source=="post_wave"` jest polem obecnym JUŻ w dzisiejszych danych
(niezależnie od telemetrii kandydata) — policzyłem bezpośrednio z
`shadow_decisions.jsonl`:

| Metryka | Wartość |
|---|---|
| zwycięstwa `post_wave` (wszyscy kurierzy) | 21 / 866 = 2.4% |
| zwycięstwa `post_wave` w target cids (179+413) | **8 / 421 = 1.9%** |
| `post_wave` / (`post_wave`+`no_gps`) w target cids | 8 / (8+292) = **2.7%** |

Liczba **8/421 zgadza się DOKŁADNIE** z cytowaną przez CTO ("8/421 wygranych
179-413") — niezależne przeliczenie z surowego logu, nie przepisanie. Cytowane
przez CTO "2.6% zwycięzców" odpowiada trzeciemu wierszowi tabeli (mianownik =
tylko ścieżki potencjalnie-syntetyczne `post_wave`+`no_gps`, nie wszystkie 421
zwycięstw) — różnica w % to różnica w mianowniku, NIE sprzeczność w liczniku
(8 w obu przypadkach).

**Metryka do 48h cienia** (dodana do `measure_nogps_shadow.py` — sekcja
"POST_WAVE residual" w każdym raporcie, działa NA KAŻDYM oknie, nie wymaga
telemetrii kandydata): `post_wave_share_of_target_wins`. **Próg**: nie rośnie
powyżej **2×** baseline (>3.8% zamiast obecnych 1.9%) w 48h cieniu PO fixie.
Wzrost oznaczałby, że relabeling na `post_wave` zaczyna kompensować to, co fix
próbuje wyeliminować gdzie indziej (np. gdyby po fixie silnik/kurierzy
"uciekali" w stronę ścieżki post_wave) — mało prawdopodobne mechanicznie, ale
tani do sprawdzenia, więc wart pilnowania. NIE blokuje GO samodzielnie (to
obserwacja, nie hard gate) — chyba że wzrost jest duży I współwystępuje z
gap_on > progu z sekcji 4 (wtedy razem sugerują, że post_wave stał się nowym
kanałem ucieczki od neutralizacji, wart głębszej analizy przed ACK).

## 5b. WYMAGANY GATE dla v2: donor-filter validation (Sol pkt 2, POTWIERDZONY przez CTO) — **formuła SKORYGOWANA 19.07 wieczór**

**Korekta (team-lead → CTO/nogps-v2, 19.07 wieczór):** moja pierwsza wersja
tego gate'u testowała donora jako `is_position_known(pos_source) AND
feasibility=="MAYBE"`. To NIE jest tożsame z kanoniczną definicją SILNIKA
(v2, `dispatch_pipeline.py:776-786`, commit `15ecc79`, zweryfikowałem w
realnym diffie, nie tylko w opisie):

```python
if m.get("road_km_from_synthetic_pos"):        # donor NIE, jeśli synth
    continue
if getattr(c, "feasibility_verdict", None) != "MAYBE":   # donor NIE, jeśli nie MAYBE
    continue
km = m.get("km_to_pickup")
```

Czyli: **`NOT road_km_from_synthetic_pos AND feasibility_verdict=="MAYBE" AND
km_to_pickup liczbowy`** — pole `road_km_from_synthetic_pos` wprost, NIE
pochodna klasyfikacja `pos_source`. Rozjazd mojej pierwszej wersji był w 2
klasach brzegowych (opisanych też przez nogps-v2 w
`eod_drafts/2026-07-19/NOGPS_NEUTRAL_SCORE_CANDIDATE.md` sekcja "ROZBIEŻNOŚĆ
WZORÓW"):

1. **no_gps/pre_shift Z KOTWICĄ** (anchor/bag-tail): `road_km_from_synthetic_pos=False`
   (realny) → SILNIK: donor. `is_position_known("no_gps")=False` → MOJA
   PIERWSZA WERSJA: błędnie wykluczała. Dodatkowo: pętla display F1.7
   NADPISUJE ich `km_to_pickup` w LOGU (no_gps→fleet_avg/mediana,
   pre_shift→None) PO passie mediany — więc nawet wiedząc, że są donorami,
   ich PRAWDZIWY km jest **nieodtwarzalny wprost z samego logu**.
2. **post_wave po przemianowaniu** (F2.1c rename, źródłowa pozycja była
   Unknown, road z centrum): `road_km_from_synthetic_pos=True` → SILNIK:
   NIE-donor. `is_position_known("post_wave")=True` → MOJA PIERWSZA WERSJA:
   błędnie WŁĄCZAŁA fikcyjny km do mediany.

**Naprawa w `measure_nogps_shadow.py`:** nowe pole `synth` (=
`road_km_from_synthetic_pos` z logu) w `_cand_view`, nowe funkcje
`_is_engine_donor(c)` (klasa 2 — filtr wprost na `synth`, nie na
`pos_source`) i `_donor_km_reconstructable(c)` (klasa 1 — czy `pos_source`
kandydata jest w `{no_gps, pre_shift}`, czyli czy jego km w logu mógł zostać
nadpisany). Decyzje, których pula zawiera donora klasy 1, są WYŁĄCZONE z
twardego `donor_filter_match_rate` i liczone osobno jako
`donor_filter_unmeasurable_n` → `donor_filter_coverage_rate` (mierzalne /
sprawdzone). **Bez tego wyłączenia `match_rate<100%` byłby ARTEFAKTEM tego
narzędzia, nie defektem silnika** — dokładnie ostrzeżenie z evidence doc
nogps-v2.

**Zweryfikowane na 4-scenariuszowym syntetycznym fixture** (usunięty po
weryfikacji, nie zaśmieca deliverable): (A) normalny przypadek z HARD-NO
donorem do wykluczenia — zgodne; (B) no_gps-z-kotwicą jako prawdziwy donor —
poprawnie oznaczone `unmeasurable`, NIE liczone jako mismatch; (C) post_wave
z `synth=True` (fikcyjny km) — poprawnie WYKLUCZONY z donorów (moja stara
formuła by go wciągnęła i dała fałszywy mismatch — sprawdzone ręcznie: stara
formuła dałaby medianę 8.5 zamiast poprawnych 2.0); (D) post_wave z
`synth=False` (realna kotwica) — poprawnie WŁĄCZONY jako donor z
nieuszkodzonym km. Wynik testu: `coverage=75% [3/4], match_rate=100%` —
mechanika działa zgodnie z korektą.

**GATE (dodatkowy do sekcji 4, wymagany PRZED ACK, niezależnie od GAP):**
`donor_filter_match_rate` (liczone WYŁĄCZNIE na mierzalnej podpuli) w 48h
cieniu **musi być ~100%** (dopuszczam ≥99% na zaokrąglenia float — nie
mniej). Raportuj OBOK `donor_filter_coverage_rate` — jeśli coverage jest
niska (np. <50%, dużo pul ma kotwiczonych no_gps/pre_shift), gate ma mało
sygnału i match_rate=100% na małej próbce jest słabszym dowodem niż przy
wysokiej coverage; nie blokuje GO samo z siebie, ale odnotuj w raporcie do
Adriana. Jeśli match_rate <99%: Sol pkt 2 NIE został faktycznie naprawiony w
v2 (albo naprawiony inaczej niż zakłada powyższa formuła — sprawdź
`donor_filter_mismatch_examples`), **STOP przed flipem**.

**Dodatkowa nota od nogps-v2 (przekazana przez team-lead):** mediana ON w v2
może być SYSTEMATYCZNIE WYŻSZA niż byłaby w v1 w pulach, gdzie v1 błędnie
wciągał tanie (blisko położone) kandydatów HARD-NO do mediany — v1 miał
sztucznie ZANIŻONĄ medianę w takich pulach, v2 ją poprawnie podnosi do
prawdziwego poziomu floty. **NIE porównywać okien cienia v1 i v2 wprost
przez `--window-a`/`--window-b`** (np. "przed" = okno z v1 telemetrią, "po" =
okno z v2 telemetrią) — różnica w `bonus_neutral_km_stat` między takimi
oknami może być mechanicznym artefaktem naprawy formuły, nie realną zmianą
zachowania systemu. Porównania czasowe mają sens WEWNĄTRZ jednej wersji
kodu (np. dwa okna 24h obu w ramach 48h cienia v2), nie MIĘDZY v1 a v2.

## 6. Koncentracja top-2 (179+413) i per-cid

Brak twardego progu (kandydat też go nie podał w swoim planie) — raportuj
trend. Dziś 48.6% wszystkich zwycięstw idzie do 2 kurierów przy 23.5%
pool-share tej pary. Oczekiwany kierunek PO fixie: spadek w stronę ~23–25%
(parytet z pool-share). Duży spadek + stabilne inne metryki = dodatkowy
dowód, że fix usuwa konkretną koncentrację, która wywołała zgłoszenie ownera
("znajdź przyczynę 30% wyboru jednego kuriera, to bug") — nie tylko
poprawia średnią.

## 7. REGRESJA-GUARDY (twarde stopy — łamią GO niezależnie od punktu 4)

| Guard | Baseline (24h bucket) | Próg kandydata | Ocena progu na tle wariancji |
|---|---|---|---|
| KOORD-rate wzrost | mean 5.5%, stdev ±1.15pp | nie rośnie >2pp | 2pp ≈ 1.7× stdev — dobrze skalibrowany (ani za ciasny, ani za luźny). **Zatwierdzam bez zmian.** |
| mediana km_to_pickup zwycięzców wzrost | mean 3.59km, stdev ±0.194km (5.4% wzgl.) | nie rośnie >10% | 10% ≈ 1.85× względnego stdev — podobnie dobrze skalibrowany. **Zatwierdzam bez zmian.** |
| 'cisza' / best_effort_low_score rate | mean 0.0%, stdev 0.00pp (ZERO wystąpień w całym 4-dniowym oknie) | brak wzrostu (jakościowo, w planie kandydata) | Baseline=0% czyni ten guard BARDZO czuły — każde pojawienie się w >0.5% decyzji to już 100% wzrost względem zera. **Rekomendacja: KAŻDE wystąpienie w 48h cieniu = STOP i ręcznie sprawdź**, czy to kandydaci, których `bonus_nogps_neutral_dist_delta` jest ujemny i zbił ich PONIŻEJ MIN_PROPOSE — dokładnie ryzyko nazwane w sekcji "MAPA KOMPLETNOŚCI" kandydata (`_GATE_RANKING_DELTA_EXCLUSIONS` świadomie NIE włączone). Jeśli tak: albo dopisz do exclusions, albo zaakceptuj świadomie z ownerem przed flipem. |
| best_effort rate (zwycięzca) | mean 3.6%, stdev ±5.97pp (SZUMNY — zdominowany przez 1 wieczorny bucket 60%) | (brak w planie kandydata) | Dodaję jako informacyjny, NIE twardy guard — zbyt szumny przy obecnym n. Alarm jeśli >20% w którymś dniu (2× historyczne maksimum). |

## 8. Jak uruchomić pomiar 48h (po deployu kodu kandydata, flaga OFF)

```bash
python3 measure_nogps_shadow.py \
  --since <START_48H_ISO> --until <KONIEC_48H_ISO> \
  --target-cids 179,413 \
  --json-out /sciezka/do/raport_48h.json
```

Jeśli okno 48h przecina rotację pliku logu, dołóż oba pliki do `--input`:

```bash
python3 measure_nogps_shadow.py \
  --input logs/shadow_decisions.jsonl.1 logs/shadow_decisions.jsonl \
  --since <START> --until <KONIEC>
```

Albo porównaj bezpośrednio okno PRZED (dziś, bez telemetrii) vs PO
(post-deploy, z telemetrią) jedną komendą — deltę policzy sam skrypt:

```bash
python3 measure_nogps_shadow.py \
  --window-a "<PRZED_START>,<PRZED_KONIEC>" \
  --window-b "<PO_START>,<PO_KONIEC>"
```

Sekcja "TELEMETRIA KANDYDATA" w oknie B da bezpośrednio
`counterfactual_winner_share_unknown_on` i `counterfactual_would_flip_rate`
— to są liczby do podstawienia w tabeli z sekcji 4 powyżej.

## 9. Co dalej

**Warunek wstępny przed czymkolwiek z poniższego: flip dotyczy WYŁĄCZNIE v2**
(`15ecc79`), nigdy v1 (`88acde3` ma status REJECT — patrz status na górze
dokumentu). v2 naprawia punkty #2/#3/#9 z recenzji Sol, potwierdzone przez
CTO; czy to domyka sprawę bez OSOBNEJ pełnej ponownej adwersaryjnej recenzji
(vs. tylko docelowej weryfikacji punktów naprawionych) to decyzja
team-lead/ownera, poza zakresem tego dokumentu. Ten dokument mierzy WYNIKI,
nie zastępuje recenzji kodu — GO tutaj przy kodzie, który sam w sobie ma
nienaprawiony defekt (np. Sol pkt 2 nadal obecny w innej postaci), jest
fałszywym bezpieczeństwem, dlatego sekcja 5b robi się TWARDYM gate'em, nie
tylko obserwacją — to jedyny punkt z 9, który to narzędzie umie sprawdzić
automatycznie na danych, nie tylko w kodzie.

- **GO** (sekcja 4 SILNE lub akceptowalne + sekcja 5b `donor_filter_match_rate`
  ~100% + WSZYSTKIE guardy z sekcji 7 zielone): raport do Adriana z liczbami
  z realnego pomiaru → ACK ownera → flip
  `ENABLE_NO_GPS_NEUTRAL_SCORE_DIST=true` w `flags.json` (hot-reload, bez
  restartu, per plan kandydata) → krótki canary (kontrfaktyczny re-sort był
  APROKSYMACJĄ — patrz ograniczenie #2 niżej) → kolejne 24–48h live
  monitoring tych samych metryk, teraz na ON (w tym `post_wave_share_of_target_wins`
  z sekcji 5a — nadal tylko obserwacyjnie).
- **NO-GO**: nie flipuj. Wróć do kandydata (worktree `wt-nogps-pkgroot`,
  gałąź `fix/nogps-neutral-score-dist`, subagent nogps-v2) z konkretną
  liczbą, która zawiodła, i zdiagnozuj. Najczęstsi podejrzani: (a) mediana
  liczona z za małej puli "znanych" kotwic w konkretnych porach dnia —
  sprawdź `bonus_raw_km_stat`/`bonus_neutral_km_stat` per okno; (b) sekcja
  5b `donor_filter_mismatch_examples` — jeśli niepuste, Sol pkt 2 nadal żyje
  w innej postaci.
- Rollback flagi (gdyby coś poszło nie tak PO flipie): `flags.json` →
  `ENABLE_NO_GPS_NEUTRAL_SCORE_DIST=false`, hot-reload, bez restartu.

## 10. Ograniczenia tej metodologii (czytaj przed użyciem wyników)

1. **n=4 dni** dla wariancji dobowej — stdev jest PIERWSZYM przybliżeniem,
   nie ostateczną kalibracją. Gdy przybędzie danych (docelowo ≥10-14 dni),
   przelicz progi ponownie tym samym skryptem.
2. **Kontrfaktyczny re-sort jest APROKSYMACJĄ** silnika: używa `score ±
   delta` i `max()` po cid, ale prawdziwy silnik ma selekcję bucket-based
   (`_selection_bucket`, tiebreaki po kolejności floty — `core/selection.py`)
   której ten skrypt NIE replikuje 1:1. `counterfactual_would_flip_winner`
   jest więc raczej DOLNYM oszacowaniem realnego efektu przy remisach blisko
   granicy — OK dla decyzji GO/NO-GO (kierunek i rząd wielkości są
   wiarygodne), ale nie oczekuj identycznych liczb po realnym flipie.
3. **km_to_pickup DISPLAY w danych SPRZED deployu kandydata NIE JEST** tym
   samym km, które zasiliło score (to jest cała treść buga — rozjazd
   score↔display). Sekcje 1–4 tego dokumentu opierają "GAP" na
   WINNER-SHARE (niezależnym od tego zniekształcenia), nie na km-display,
   właśnie żeby to obejść.
4. **`pool_feasible_count` bywa MNIEJSZY niż liczba serializowanych
   kandydatów** (best_effort/MAYBE bywają dokładane do `alternatives` poza
   ścisłą definicją "feasible" — potwierdzone: 0% przypadków odwrotnych,
   czyli nigdy NIE brakuje serializowanych kandydatów względem
   `pool_feasible_count`). Pool-share liczony jest więc na SERIALIZOWANEJ
   puli (best+alternatives), lekko SZERSZEJ niż ścisłe "feasible" — ale
   konsystentnie w czasie (ten sam efekt PRZED i PO), więc nie zniekształca
   porównania.
5. Ten dokument dotyczy WYŁĄCZNIE decyzji "czy flipować flagę po 48h cienia
   PO deployu kodu". Sam deploy kodu kandydata (merge do master + restart
   `dispatch-shadow` z flagą OFF) to OSOBNA decyzja/ACK poza zakresem tego
   dokumentu — patrz Przykazanie #0 / `ziomek-change-protocol.md`
   (backup → py_compile → test → git log → ACK → 1 restart).
6. **v1 (`88acde3`) ma status REJECT** (Sol, 19.07 wieczór, 9 punktów
   dowodowych) — sekcje 4-8 tego dokumentu mierzą WYNIKI działania kodu i są
   ślepe na to, CZY kod jest poprawny wewnętrznie. Sekcja 5b (donor-filter
   validation) to jedyny automatyczny check w tym narzędziu, który wprost
   testuje jeden z 9 punktów Sol na danych — pozostałe 8 punktów (w tym pkt 1
   OFF-parytet, pkt 7 tie-break bias, pkt 9 pokrycie testów) wymagają
   OSOBNEJ recenzji kodu v2, nie tylko pomiaru z tego dokumentu.

---
Autor: pre-stage subagent "nogps-measure" (zadanie zlecone przez team-lead,
zaktualizowane po napłynięciu werdyktu Sol + oceny CTO), 2026-07-19.
Narzędzie: `measure_nogps_shadow.py` w tym samym katalogu (stdlib only, 3
niezależne od telemetrii sekcje działają NA DZISIEJSZYCH danych: bug baseline,
wariancja historyczna, post_wave residual; 3 sekcje czekają na telemetrię v2:
kontrfaktyczny ON/OFF, donor-filter validation, bonus_* statystyki). Wyjście
testowego przebiegu na realnych danych: `run_output_final.txt` +
`report_final.json` (ten sam katalog). Werdykt Sol w pełni:
`/tmp/claude-0/-root/d17bde9a-a389-46c0-8f2d-440cd023165d/scratchpad/sol_nogps_candidate_review.log`.
