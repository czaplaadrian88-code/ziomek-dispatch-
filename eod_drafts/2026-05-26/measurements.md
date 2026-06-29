# OSRM 3-Backend Measurements — Geometry Bug Diagnoza

**Data pomiaru:** 2026-05-26 ~16:05-20:30 Wt Warsaw (peak operacyjny lunch + evening peak)

**Backends:**
- **OSRM ff:** `http://localhost:5001` free-flow, brak traffic
- **OSRM ×traffic:** OSRM ff × `V326_OSRM_TRAFFIC_TABLE` multiplier (wt 13-14 ×1.2, 16-17 ×1.3, 17-19 ×1.2)
- **TomTom:** `api.tomtom.com/routing/1/calculateRoute` z `traffic=true` (realny ruch w momencie pomiaru)

---

## Case #3 — Toriko/Grill Kebab/Rany Julek

| Segment | OSRM ff | OSRM×1.3 | TomTom | km |
|---------|---------|----------|--------|-----|
| Toriko → GrillKebab | 3.1 | 4.1 | 6.5 | 1.47 |
| GrillKebab → RanyJulek | 3.7 | 4.8 | 9.2 | 1.78 |
| Toriko → RanyJulek | 0.7 | 0.9 | 3.0 | 0.30 |
| RanyJulek → GrillKebab | 3.4 | 4.5 | 7.5 | 1.65 |

**Sekwencje:**
- System (Tor→GK→RJ): 6.8 / 8.8 / **15.7** min, 3.25 km
- Adrian (Tor→RJ→GK): 4.1 / 5.4 / **10.5** min, 1.94 km
- **Różnica:** Adrian -40% drive

---

## Case A — Pan Schabowy/Transportowa (Mateusz O)

| Segment | OSRM ff | OSRM×1.3 | TomTom | km |
|---------|---------|----------|--------|-----|
| RanyJulek → PanSchabowy | 8.1 | 10.6 | 12.2 | 5.74 |
| PanSchabowy → Transportowa | 9.9 | 12.8 | 12.65 | 6.76 |
| RanyJulek → Transportowa | 6.3 | 8.2 | n/d | 3.73 |
| Transportowa → PanSchabowy | 9.9 | 12.9 | 12.65 | 6.73 |

**Sekwencje:**
- System (RJ→Schabowy→Trans): 18.0 / 23.4 / **24.85** min, 12.50 km
- Adrian (RJ→Trans→Schabowy): 16.2 / 21.0 / ~ min, 10.46 km
- **Różnica:** Adrian -16% km, -10% drive
- **R6 violation:** Sweet Fit pickup 13:40 → drop Transportowa 14:47 = **67 min bag time**

---

## Case C — Karczma/Skłodowskiej detour

| Segment | OSRM ff | OSRM×1.3 | TomTom | km |
|---------|---------|----------|--------|-----|
| Kaczorowskiego → 1000-lecia | 6.7 | 8.7 | **14.7** | 4.07 |
| Kaczorowskiego → Karczma | 1.5 | 1.9 | 1.8 | 0.64 |
| Karczma → Skłodowskiej | 1.7 | 2.2 | 3.4 | 0.61 |
| Skłodowskiej → 1000-lecia | 7.7 | 10.1 | 19.0 | 4.20 |

**Sekwencje:**
- Bez detour (base): 6.7 / 8.7 / **14.7** min, 4.07 km
- Z detour: 10.9 / 14.1 / **24.2** min, 5.44 km
- **Koszt detour:** +63% drive (+1.4 km +4.2 min OSRM, +9.5 min TomTom)

---

## Case #2 — Rukola Sienkiewicza/Andersa/1000-lecia (Michał K.)

| Segment | OSRM ff | OSRM×1.2 | TomTom | km |
|---------|---------|----------|--------|-----|
| Rukola → Andersa | 4.8 | 6.2 | **11.3** | 2.55 |
| Andersa → 1000-lecia | 4.9 | 6.3 | 5.9 | 2.96 |
| Rukola → 1000-lecia | 5.6 | 7.2 | 9.85 | 3.19 |
| 1000-lecia → Andersa | 5.9 | 7.7 | 5.85 | 2.78 |

**Sekwencje:**
- System (Rukola→Andersa→1000): 9.7 / 12.5 / **17.2** min, 5.52 km
- Adrian (Rukola→1000→Andersa): 11.5 / 15.0 / **15.7** min, 5.96 km
- **Wniosek:** OSRM ff/×1.2 mówi system jest -16% szybciej, ale **TomTom realny pokazuje Adrian -8.7%**.
- TomTom obala wcześniejszy wniosek — Adrian wygrywa też geometrycznie w real-time.

---

## Case D — Jakub OL klaster osiedlowy

**System trasa:** Rukola → Wybickiego → SushiRJ → Wąska → Kraszewskiego → Chrobrego

| Segment | OSRM ff | OSRM×1.2 | TomTom | km |
|---------|---------|----------|--------|-----|
| Rukola → Wybickiego | 5.9 | 7.1 | 7.1 | 3.37 |
| Wybickiego → Sushi RJ | 8.6 | 10.3 | 10.8 | 5.08 |
| Sushi RJ → Wąska | 4.5 | 5.4 | 5.7 | 2.57 |
| Wąska → Kraszewskiego | 2.6 | 3.1 | 3.9 | 0.83 |
| Kraszewskiego → Chrobrego | 4.2 | 5.0 | 5.8 | 1.79 |
| **SUMA SYSTEM** | **25.8** | **30.9** | **33.3** | **13.65** |

**Adrian trasa:** Rukola → Wąska → Kraszewskiego → Chrobrego → SushiRJ → Wybickiego

| Segment | OSRM ff | OSRM×1.2 | TomTom | km |
|---------|---------|----------|--------|-----|
| Rukola → Wąska | 1.9 | 2.3 | 2.3 | 0.65 |
| Wąska → Kraszewskiego | 2.6 | 3.1 | 3.9 | 0.83 |
| Kraszewskiego → Chrobrego | 4.2 | 5.0 | 5.8 | 1.79 |
| Chrobrego → Sushi RJ | 6.3 | 7.5 | 8.8 | 3.91 |
| Sushi RJ → Wybickiego | 8.8 | 10.6 | 10.6 | 5.23 |
| **SUMA ADRIAN** | **23.8** | **28.5** | **31.3** | **12.41** |

**Różnica:** Adrian -1.24 km / -2 min OSRM / -2 min TomTom

**R6 violations w trasie systemu:**
- _500 stopni: pickup 16:48 → drop ~17:33 = **45 min R6 BREACH**
- Rukola Sien: pickup 17:00 → drop ~17:41 = **41 min R6 BREACH**
- Sushi RJ (stary): 16:53 → 17:26 = 33 min ✓
- Sushi RJ NOWY: 17:15 → 17:41 = 26 min ✓

---

## Case E — Dariusz M (Bacieczki 90 min)

**System trasa:** Stroma → Mama Thai → Jana Pawła 59a → Rukola Kacz (NOWY) → Jana Pawła 61B → Bacieczki

| Segment | OSRM ff | OSRM×1.2 | TomTom | km |
|---------|---------|----------|--------|-----|
| Stroma → Mama Thai | 8.6 | 10.4 | 10.9 | 4.35 |
| Mama Thai → Jana Pawła 59a | 8.4 | 10.1 | 10.7 | 5.46 |
| Jana Pawła 59a → Rukola Kacz | 8.5 | 10.2 | 9.9 | 4.99 |
| Rukola Kacz → Jana Pawła 61B | 8.8 | 10.5 | 11.2 | 4.74 |
| Jana Pawła 61B → Bacieczki 223 | 5.1 | 6.2 | 5.5 | 3.05 |
| **SUMA SYSTEM** | **39.4** | **47.3** | **48.2** | **22.59** |

**Adrian propozycja "skip Rukoli, dokończ bag":** Stroma → Mama Thai → Jana Pawła 59a → Jana Pawła 61B → Bacieczki

| Segment | OSRM ff | OSRM×1.2 | TomTom | km |
|---------|---------|----------|--------|-----|
| Stroma → Mama Thai | 8.6 | 10.4 | 10.9 | 4.35 |
| Mama Thai → Jana Pawła 59a | 8.4 | 10.1 | 10.7 | 5.46 |
| Jana Pawła 59a → Jana Pawła 61B | 1.6 | 1.9 | 1.5 | 0.25 |
| Jana Pawła 61B → Bacieczki 223 | 5.1 | 6.2 | 5.5 | 3.05 |
| **SUMA ADRIAN** | **23.7** | **28.5** | **28.6** | **13.11** |

**Różnica:** Adrian **-15.7 min OSRM / -19.6 min TomTom / -9.48 km**. OGROMNA oszczędność.

**R6 violations w trasie systemu:**
- Chinatown pickup 16:30 → Stroma 17:17 = **47 min R6 BREACH**
- Sushi RJ pickup 16:32 → Bacieczki 18:02 = **90 min R6 BREACH** (ABSURDALNY)

---

## Case F — Dariusz M (Borsucza+Skidelska dwa kierunki)

**System trasa:** Bacieczki → Jana Pawła 61B → Sushi RJ → Szklanki → Borsucza → Skidelska

| Segment | OSRM ff | OSRM×1.2 | TomTom | km |
|---------|---------|----------|--------|-----|
| Bacieczki → Jana Pawła 61B | 4.6 | 5.6 | 4.7 | 2.88 |
| Jana Pawła 61B → Sushi RJ | 6.2 | 7.5 | 7.1 | 3.64 |
| Sushi RJ → Szklanki | 1.5 | 1.8 | 2.8 | 0.63 |
| Szklanki → Borsucza | 9.1 | 10.9 | 11.9 | 5.41 |
| Borsucza → Skidelska | 12.8 | 15.3 | 14.8 | 7.36 |
| **SUMA SYSTEM** | **34.2** | **41.1** | **41.2** | **19.91** |

**R6 violations:**
- Mama Thai pickup 17:23 → drop Bacieczki 18:18 = **55 min R6 BREACH**
- Rukola Kacz pickup 17:31 → drop Jana Pawła 61B 18:27 = **56 min R6 BREACH**
- Sushi RJ pickup 18:31 → drop Skidelska 19:12 = **41 min R6 BREACH** (Adrian's komentarz)

---

## Case G — Mateusz O (Rany Julek 43 min)

**System trasa:** Rany Julek → Saturna → Rukola Kacz → Street Mama Thai → Transportowa → Wiadukt

| Segment | OSRM ff | OSRM×1.2 | TomTom | km |
|---------|---------|----------|--------|-----|
| Rany Julek → Saturna | 6.5 | 7.8 | 8.4 | 4.70 |
| Saturna → Rukola Kacz | 9.3 | 11.1 | 12.4 | 6.23 |
| Rukola Kacz → Street Mama Thai | 6.6 | 7.9 | 8.3 | 4.22 |
| Street Mama Thai → Transportowa | 10.1 | 12.2 | 13.0 | 6.49 |
| Transportowa → Wiadukt | 4.7 | 5.6 | 5.5 | 2.45 |
| **SUMA SYSTEM** | **37.1** | **44.5** | **47.7** | **24.08** |

**R6 violations:**
- Rany Julek pickup 18:26 → drop Saturna 19:09 = **43 min R6 BREACH**

---

## Sumaryczne wnioski

### BUG D — OSRM peak underestimation (kalibracja `V326_OSRM_TRAFFIC_TABLE`)

Stosunek TomTom/OSRM ff w peakach:

| Segment | TomTom / OSRM ff |
|---------|------------------|
| Toriko → GK (centrum) | 2.10× |
| GK → RJ (centrum) | 2.49× |
| Toriko → RJ (300m centrum) | 4.29× (mały sample) |
| Skłodowskiej → 1000-lecia | 2.47× |
| Rukola → Andersa | 2.35× |
| Saturna → Rukola Kacz | 1.33× |
| Bacieczki → Jana Pawła 61B | 1.02× |
| Jana Pawła 59a → Rukola Kacz | 1.16× |

**Średnia segmentów krótkich (<3 km) w peaku: ~2.3×**
**Średnia segmentów długich (>5 km) w peaku: ~1.15×**

**Wniosek:** TRAFFIC_TABLE multiplier ×1.2-1.3 znacznie zaniża **krótkie segmenty w centrum**. Długie segmenty międzydzielnicowe są blisko OSRM ff.

**Hipoteza:** OSRM nie modeluje świateł, znaków stop, korków na skrzyżowaniach. Dla 300m-3km w centrum peak to dominujący koszt.

**Action:** sprint osobny po GATE B re-werdyct 22.05. Rozważyć **per-distance-bin traffic multiplier** zamiast flat per-hour.

### R6 Violations w 4 nowych case'ach

Łącznie **9 z 14 orderów** w bagach łamie R6 35 min hard cap. Najgorszy: 90 min (Sushi RJ → Bacieczki w Case E). Wszystkie 4 case'y mają flagę `⚠️ Best effort — brak feasible kandydata` — czyli system wie że są w breach, ale i tak pcha PROPOSE.

**To jest BUG E** — najpilniejszy do fix'a (Faza 1 jutro).

---

## TomTom API ważne notatki

- Endpoint: `https://api.tomtom.com/routing/1/calculateRoute/{lat1},{lon1}:{lat2},{lon2}/json?key={KEY}&traffic=true`
- Klucz w `/root/.openclaw/workspace/.env` jako `TOMTOM_API_KEY`
- Rate limit: ~5 req/s w darmowym tier; przy batch >20 par stosuj `time.sleep(0.6)` między
- Sampling pattern: `key=KEY`, `traffic=true`, `routeType=fastest` (default), `computeTravelTimeFor=all` (opcjonalnie)
- Response: `routes[0].summary.travelTimeInSeconds` (in seconds, with traffic)
- Failures (timeout/rate-limit): zwraca None — retry pojedynczo z sleep 0.6s

Sample script: `tools/measure_realworld.py` (cron `*/10 7-22` UTC, GATE B PoC).

---

**Last update:** 2026-05-26 ~20:30 (post diagnostic session)
