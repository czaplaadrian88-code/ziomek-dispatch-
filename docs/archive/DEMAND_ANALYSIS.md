# NadajeSz — Analiza popytu 2024-2026

**Źródło:** Google Sheet "Controlling — Średnie" (CSV export)
**Zakres:** 2024-03-29 → 2026-04-04 (477 dni operacyjnych)
**Total dowozów:** 121 540
**Przeznaczenie:** fundament dla Fazy 6 (scheduler + demand forecaster)

---

## 1. Średnia dzienna per dzień tygodnia

```
Pon: 206  (n=65, max=440)
Wto: 212  (n=64, max=442)
Śro: 219  (n=63, max=385)
Czw: 233  (n=62, max=396)
Pią: 274  (n=74, max=431)
Sob: 263  (n=76, max=486)
Nie: 358  (n=73, max=616)
```

**Wnioski:**
- Niedziela +74% vs średnia pn-czw (217). Dominuje.
- Sobota niższa niż piątek (263 vs 274).
- Czwartek startuje uptick tygodniowy.
- Max niedziela 616 = 1.74× mediany = "burst days" (hipoteza: offload od innych graczy).

---

## 2. Heatmap godzin × dni (mean orders/h)

```
Godz |  Pon   Wto   Śro   Czw   Pią   Sob   Nie
-------------------------------------------------
   9 |  1.9   1.9   2.1   1.9   1.7   0.6   0.7
  10 |  3.3   3.5   3.9   4.0   4.6   3.2   2.0
  11 |  8.6   9.8  10.1  11.4  12.7   7.2   8.5
  12 | 18.5  19.2  20.5  22.1  23.9  15.9  22.3
  13 | 18.4  20.7  20.4  20.9  23.3  23.8  37.0  ★ Nie peak start
  14 | 18.6  18.4  18.7  19.5  21.7  27.6  44.2  ★ Nie peak
  15 | 20.5  21.9  20.8  23.0  24.1  27.6  45.2  ★★★ SZCZYT TYGODNIA
  16 | 23.2  23.4  23.9  24.9  25.0  26.8  41.2  ★ Nie peak
  17 | 21.6  24.2  24.9  24.8  28.4  27.0  38.5  ★ Pią/Sob/Nie peak
  18 | 22.0  21.8  22.8  24.5  27.8  26.0  36.2  ★ Pią/Sob/Nie peak
  19 | 21.1  20.1  21.3  22.8  29.8  27.1  35.1  ★ Pią szczyt
  20 | 16.1  15.8  17.0  19.7  25.3  24.7  27.4
  21 |  9.2   9.1  10.8  10.5  17.5  17.3  15.0
  22 |  2.8   2.6   2.6   3.3   6.2   6.6   4.2
  23 |  0.0   0.0   0.0   0.0   2.3   1.9   0.8
```

**Peaki operacyjne (wg mean orders/h):**
| Okno | Dni | Godz | Avg/h | Intensywność |
|---|---|---|---|---|
| Nie obiad | Nie | 13-17 | 37-45 | 🔥🔥🔥 |
| Nie wieczór | Nie | 18-20 | 27-38 | 🔥🔥 |
| Pią wieczór | Pią | 17-21 | 25-30 | 🔥🔥 |
| Sob wieczór | Sob | 14-21 | 24-28 | 🔥 |
| Weekday obiad | Pn-Czw | 12-13 | 18-22 | 🧊 |
| Weekday wieczór | Pn-Czw | 17-19 | 21-25 | 🧊 |

**Obserwacja dla P0.5:** peak operacyjny ≠ peak korkowy. Niedzielny 15:00 = 45 orderów/h ale puste ulice (weekend, niski ruch). Piątek 19:00 = 30 orderów/h ale korki Białegostoku (peak 15-17). Dwa niezależne wymiary.

---

## 3. Sezonowość (mean orders/day per miesiąc)

```
Paź: 294  ★ TOP (jesień, ochłodzenie)
Lis: 289  (święta + zimno)
Gru: 279  (święta)
Sty: 269  (zima + "dom")
Maj: 269  (wiosna)
Wrz: 267  (back to school, jesień)
Sie: 260
Lut: 255
Cze: 251
Lip: 224  (wakacje — wyjazdy)
Mar: 219
Kwi: 207  ★ DÓŁ (wiosna, ludzie wychodzą)
```

**Wzór:** zimno + święta + deszcz = wysoki popyt. Lato + ciepło = ludzie wychodzą = niski popyt.

**Delta:** Październik (294) vs Kwiecień (207) = **+42%**. Sezonowość ma wyraźny wpływ na potrzeby floty.

---

## 4. Dni specjalne — święta + kulturowe

```
Walentynki 2025 (Pią):      431 vs baseline 285 = 1.51× 🔥
Walentynki 2026 (Sob):      486 vs baseline 256 = 1.90× 🔥
Nowy Rok 2025:              385 vs baseline 224 = 1.72× 🔥
Nowy Rok 2026 (Czw):        396 vs baseline 263 = 1.51× 🔥
Święto Niepodległości 11.11 2025: 442 vs baseline 219 = 2.02× 🔥🔥
Dzień Kobiet 8.03 2025:     268 vs baseline 209 = 1.28× (lekki)
Sylwester 31.12 2024:       278 vs baseline 235 = 1.18× ~
Sylwester 31.12 2025:       280 vs baseline 246 = 1.14× ~
Wszystkich Świętych 1.11:   235 vs baseline 277 = 0.85× 🧊 (niski)
Majówka 1.05:               231 vs baseline 231 = 1.00× (przedłużony weekend = wyjazdy)
```

**Święta które PODBIJAJĄ popyt:** Walentynki, Nowy Rok, Święto Niepodległości (top).
**Święta które OBNIŻAJĄ:** Wszystkich Świętych, Majówka (wyjazdy).

---

## 5. Hipotezy do weryfikacji w Fazie 6

### H1 — "Burst days" = offload od partnerów (kumars/mama thai)
**Obserwacja:** max niedziela 616 = 1.74× mediany. Rozrzut dzień→dzień większy niż wynika z kategorii (dow, month, holiday). Znak że pojawiają się eventy zewnętrzne — np. inni gracze offloadują.
**Weryfikacja:** tracking per-restaurant share + korelacja z total volume. Wymaga rozszerzenia istniejącego trackingu.

### H2 — Pogoda ma duży wpływ
**Intuicja + sezonowość:** Październik (jesień, deszcz) 294 vs Kwiecień (wiosna, suche) 207.
**Weryfikacja:** Open-Meteo historical API (https://archive-api.open-meteo.com/v1/archive — lat=53.13, lon=23.17, 2024-01-01 do dziś). Features: `precipitation_sum`, `rain_sum`, `snowfall_sum`, `temperature_2m_max`, `temperature_2m_min`. Korelacja ze średnim dziennym dowozem. Oczekiwane: rain >0mm = +10-20%, snow = +15-30%, temp <0°C = +5-15%.

### H3 — days_since_payday
**Intuicja:** ludzie w Polsce dostają wypłatę 10-go. Popyt rośnie ~10-15 i spada 1-5.
**Weryfikacja:** feature `days_since_last_payday` + Prophet model.

### H4 — dzień przed/po święcie
**Intuicja:** dzień przed świętem = dużo zakupów + mniej delivery. Dzień po = reset + normalny poziom.
**Weryfikacja:** feature `is_day_before_holiday`, `is_day_after_holiday`.

---

## 6. Rekomendacje dla Fazy 6 (scheduler + forecaster)

### Architektura modelu demand forecasting

**Prophet (proste, interpretowalne):**
- Seasonality: yearly + weekly + daily
- Holidays: Walentynki, Nowy Rok, 11.11, Boże Narodzenie (dodaj jako regressor z wagą)
- Regressors: `temp_max`, `precipitation`, `is_rainy`, `days_since_payday`

**XGBoost/LightGBM (większa dokładność, mniej interpretowalne):**
- Features: `dow`, `hour`, `month`, `day_of_month`, `is_holiday`, `holiday_type`, `temp_max`, `temp_min`, `precipitation`, `snow`, `days_since_payday`, `is_day_before_holiday`, `days_since_last_burst`
- Target: orders_per_hour lub orders_per_day

**Ensemble:** Prophet dla trendu + XGBoost dla residuals. Tak robią najlepsi (Uber, DoorDash).

### Integracja z OR-Tools scheduler (Faza 6)

1. Prophet/XGBoost przewiduje `orders_per_hour` na 7 dni naprzód
2. Konwersja na `kurierzy_potrzebni_per_hour = orders_per_hour / 3.0` (MST średnia)
3. OR-Tools solver z ograniczeniami: MST per kurier, dyspozycyjność z Telegram, min 8h shift, max 12h, godziny peak lockowane dla tier A+B
4. Output: grafik na piątek 18:00, wysyłka propozycji w pt 20:00, publikacja sob 09:00

### Dane do pobrania raz (cache local parquet)

```python
# Open-Meteo, bezpłatne, bez API key
import requests, pandas as pd
url = ("https://archive-api.open-meteo.com/v1/archive"
       "?latitude=53.13&longitude=23.17"
       "&start_date=2024-01-01&end_date=2026-04-12"
       "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,"
       "rain_sum,snowfall_sum,weather_code"
       "&timezone=Europe/Warsaw")
r = requests.get(url).json()
df = pd.DataFrame(r['daily'])
df.to_parquet('/root/.openclaw/workspace/dispatch_state/weather_bialystok.parquet')
```

**Koszt:** 1 call, ~800 dni × 6 features, ~20KB.

---

## 7. Dane dostępne do trainingu modelu

| Źródło | Zakres | Liczba obserwacji |
|---|---|---|
| Google Sheet Controlling | 2024-03-29 .. 2026-04-04 | 477 dni × 15h = **7155 punktów hourly** |
| Open-Meteo historical | 1940 .. dzisiaj | bezpłatne, bez limitu |
| Polskie święta kalendarz | stały | ~13 dni/rok (państwowe + kulturowe) |
| orders_state.json (live) | rolling 2-3 dni | ~170 orderów z pełnymi danymi |

**Dla Prophet to bogaty zestaw treningowy.** Każdy dzień ma DoW + month + holiday + weather → model powinien wyciągnąć sensowne wzorce.

---

## 8. Kolejność implementacji (Faza 6)

1. **Skrypt ETL** `demand_etl.py` — parsuje Google Sheet (CSV export weekly) + łączy z weather + świętami → parquet
2. **Prophet baseline** — pierwszy model, trenuj na 2024-2025, testuj na Q1 2026
3. **XGBoost ensemble** — residuals od Prophet, dodaj features per-hour
4. **Live pipeline** — nightly retrain (niedziela 22:00), predykcja na 7 dni naprzód
5. **OR-Tools scheduler** — konsumuje predykcję, generuje grafik
6. **Telegram workflow** — środa 20:00 /dyspo → piątek 12:00 deadline → pt 18:00 solver → pt 20:00 propozycja → sb 09:00 publikacja

**Szacunek:** 4-6 dni roboty (Faza 6 full).

---

_Wygenerowane automatycznie z danych NadajeSz. Plik do edycji ręcznej wraz z postępem Fazy 6._
