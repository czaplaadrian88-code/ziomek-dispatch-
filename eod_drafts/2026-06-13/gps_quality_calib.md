# GPS-02 — raport kalibracyjny progów filtra jakości GPS

## 1. Historia gps_history (rozkład PRZED decyzją)

- fixy z accuracy: **111819** (brak pola accuracy: 0)
- accuracy min/max/avg: 0.8 / 2435.8 / 19.0 m

| próg accuracy (m) | fixów > próg | % |
|---|---|---|
| 50 | 7958 | 7.12% |
| 75 | 5182 | 4.63% |
| 100 | 2782 | 2.49% |
| 120 | 2092 | 1.87% |
| 150 ⟵ LIVE | 1341 | 1.20% |
| 200 | 753 | 0.67% |
| 300 | 400 | 0.36% |
| 500 | 204 | 0.18% |

- par inter-fix (dt≤10min): **111274**, max prędkość 1876 km/h
- rozkład prędkości: >200=0.036% / 120-200=0.241% / 80-120=1.761% / ≤80=97.35%

| wariant teleportu (jump_km, speed_kmh) | trafień | % par |
|---|---|---|
| jump>2.0 & speed>120.0 ⟵ LIVE | 27 | 0.024% |
| jump>2.0 & speed>150.0 | 22 | 0.020% |
| jump>1.5 & speed>120.0 | 85 | 0.076% |
| jump>3.0 & speed>120.0 | 7 | 0.006% |
| jump>1.0 & speed>150.0 | 69 | 0.062% |

## 2. Shadow log gps_quality_shadow.jsonl (żywe werdykty)

- ⚠ brak /root/.openclaw/workspace/dispatch_state/gps_quality_shadow.jsonl (shadow jeszcze nie zebrał danych)

## 3. Rekomendacja

- Próg accuracy: dobrać tak, by reject z TEGO tytułu ≤ ~1-2% fixów (LIVE 150m ≈ 1.2% historycznie). Za niski próg karze legalne fixy w mieście.
- Teleport: trzymać OBA warunki (jump + speed); LIVE (2km,120km/h) ≈ 0.05-0.10% par — bezpieczne. Poluzować TYLKO gdy shadow pokaże false-positive na realnym ruchu.
- FLIP ENABLE_GPS_ACCURACY_TELEPORT_FILTER dopiero po: (a) ≥kilku dniach shadow z realnym udziałem GPS w pos_source (zależne od decyzji Adriana o adopcji apki), (b) ACK progów, (c) sprawdzeniu że reject NIE zbiega się z realnymi przypisaniami (cross z PANEL_AGREE).
