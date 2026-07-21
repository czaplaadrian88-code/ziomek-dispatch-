# Karta D5-ODBIÓR v2 — kanoniczny target GPS

**Stan pomiaru:** 2026-07-21 14:55 UTC  
**HEAD kodu:** `323034299fbba20a2fb33a45819e26c91f10a27a`  
**Werdykt:** **WAIT (fail-closed), nie GO i nie liczbowy NO-GO**

Nie ma dziś ważnego dowodu, że odbiór zdaje D5 na właściwym targecie.
Wartości `5,40 min / 51,6% / 18,3% / +0,54 / 11,0 / n=3129` zostają
wycofane z bramki D5-ODBIÓR: opisują target klikowy, nie owner-bound GPS proxy.
W tej sesji nie dało się wykonać nowego pomiaru, ponieważ sandbox nie może
przejść przez `/root/.openclaw` (`0700`, właściciel widoczny w namespace jako
`nobody:nogroup`), więc zarówno `eta_calib.db`, jak i wejścia
`eta_ground_truth` są nieczytelne. W repo i `/tmp` nie ma równoważnego,
hash-bound snapshotu tych wejść. Nie podstawiam starych agregatów ani nie
ekstrapoluję wyniku.

## Progi D5 kontra ważny pomiar GPS

| Kryterium | Próg owner-bound | Zmierzone na GPS-target | Status |
|---|---:|---:|---|
| MAE kalibratora P50 | `<= 6,0 min` | **N/D — brak odczytu kanonicznego snapshotu** | WAIT |
| Poprawa MAE vs silnik, exact paired support | `>= 25,0%` | **N/D** | WAIT |
| Spóźnienia względem serwowanego P80 | `15,0–22,0%` | **N/D** | WAIT |
| `abs(median(actual - P50))` | `<= 1,5 min` | **N/D** | WAIT |
| p90 `abs(actual - P50)` | `<= 20,0 min` | **N/D** | WAIT |
| Complete-case coverage | `>= 60,0%` oraz `n >= 200` | **N/D / N/D** | WAIT |
| **D5-ODBIÓR łącznie** | wszystkie bramki | brak ważnego pomiaru | **WAIT** |

`WAIT` jest jedynym poprawnym werdyktem: `NO-GO` wymagałby zmierzonej porażki
któregoś progu, a `GO` — zmierzonego przejścia wszystkich progów.

## Niezależna weryfikacja targetu

1. Binding właścicielski wskazuje `restaurant_last_inside_at`, klik jest tylko
   fallbackiem niższej rangi bez prawa promocji KPI
   (`tools/eta_ground_truth.py:53-84`). High-confidence wymaga co najmniej dwóch
   punktów w geofence, zgodnego kuriera i źródła `gps_geofence`
   (`tools/eta_ground_truth.py:327-387`).
2. Poprzedni target powstaje z `picked_up_at` i `czas_kuriera`:
   `slip = picked_up_at - czas_kuriera`
   (`tools/eta_calibration/features.py:347,365-369`). Oba modele uczą się pola
   `pickup_slip_koord_min` (`tools/eta_calibration/models.py:129-130,243-244`),
   a ewaluator nocny liczy na tym samym polu
   (`tools/eta_calibration/evaluate.py:181,190-192`).
3. Kanoniczny baseline silnika nie może pochodzić z click-residualu w DB.
   Musi używać ostatniej predykcji istniejącej przed decyzją assignment
   (`tools/eta_ground_truth.py:766-779`) i błędu
   `restaurant_last_inside_at - predicted_pickup_at`
   (`tools/eta_ground_truth.py:854-855`; znak opisany w `:1022`).
4. Poprzednia karta sama publikuje odrzucone liczby jako GO
   (`eod_drafts/2026-07-21/eta_d5_card/ETA_D5_CARD.md:37-46`) i błędnie nazywa
   feature store „prawdą referencyjną” (`:26-28`). Arytmetyka mogła być
   poprawna, ale semantyka targetu nie była.

## Dokładny kontrakt ponownego pomiaru

Pomiar należy wykonać w jednej transakcji na wspólnym, jawnie oznaczonym
`as_of`, bez emisji ID, adresów ani GPS:

1. Zamrozić read-only SQLite snapshot `eta_calib.db`, runtime artifact
   `eta_calib_pickup_map.json` wraz z SHA oraz wejścia używane przez
   `eta_ground_truth`: SLA, decision outcomes, pre-assignment shadow i
   `restaurant_dwell.json`.
2. Użyć końcowego 14-dniowego holdoutu (`tools/eta_calibration/config.yaml:29-35`).
   Mianownikiem są wszystkie ukończone, znane `non_paczka` z bazowej kohorty;
   `unknown` wypada z mianownika i jest raportowane osobno, zgodnie z
   `tools/eta_ground_truth.py:70-75`.
3. Łączyć wewnątrz procesu po `(order_id, actual_courier_id)`. GPS-target jest
   ważny wyłącznie przy `gps_geofence`, confidence `high` i zgodnym kurierze.
4. Na każdym wspólnym rekordzie liczyć:
   - `actual = restaurant_last_inside_at`;
   - `engine = predicted_pickup_at` z pre-assignment oracle;
   - `cal_P50/P80 = czas_kuriera + kwantyl` z dokładnie tego runtime championa,
     którego miałby konsumować APPLY;
   - błąd `actual - prediction`, dodatni = zdarzenie późniejsze od obietnicy.
5. MAE, median bias i p90 liczyć na identycznym, sparowanym supporcie kalibrator
   kontra silnik. Zgodnie z D5 winsoryzować raportowo każdą serię na p99 bez
   usuwania wierszy. Improvement = `(MAE_engine - MAE_cal) / MAE_engine`.
6. Late-band liczyć względem **faktycznie serwowanego P80**. Runtime dokleja
   surowe `q[0.8]` (`eta_calib_serving.py:126-129`), natomiast nocny evaluator
   raportuje obietnicę `P50 + conformal offset`
   (`tools/eta_calibration/evaluate.py:225-264`). Tych dwóch wielkości nie wolno
   mieszać; dla bramki przyszłego APPLY główny jest payload runtime, a wariant
   conformal powinien być pokazany diagnostycznie.
7. Coverage = liczba rekordów ze wspólnym GPS actual + cal P50/P80 + engine
   prediction podzielona przez bazowy znany `non_paczka` denominator. Samo
   `n=3129` z click-holdoutu nie jest coverage D4.

Uwaga implementacyjna: `_cohort_accepts()` obecnie wyklucza rozpoznane paczki,
ale nie `unknown` (`tools/eta_ground_truth.py:464-478`), mimo polityki z `:71`.
Ponowny pomiar musi jawnie wyłączyć i zaraportować `unknown`; nie wolno po cichu
dziedziczyć tego rozjazdu.

## Dowody pomocnicze, które nie zastępują bieżącego pomiaru

Historyczny, jednodniowy replay z 10.07 miał dla błędu silnika na
last-inside `n=99/188 = 52,660%`, czyli poniżej dzisiejszej bramki 60%, ale był
zdominowany przez skrajną predykcję około 14 dni w przyszłość
(`eod_drafts/2026-07-10/SPRINT3_PHASE_A_REPORT.md:392-426`). Jest za stary,
dotyczy innego okna i nie zawiera porównania runtime kalibratora, więc służy
wyłącznie jako ostrzeżenie metodologiczne — nie jako obecny werdykt D5.

## Zakres operacyjny

- Nie zmieniono kodu, flag, danych runtime, usług, timerów ani modeli.
- Nie wykonano restartu, deployu ani migracji.
- Flip/APPLY pozostaje osobną bramką i wymaga osobnego, jawnego ACK po ważnym
  wyniku D5. Ta karta nie nadaje zgody na flip.
- Jedyny zapis tej sesji znajduje się w
  `eod_drafts/2026-07-21/eta_d5_pickup_v2/`.

