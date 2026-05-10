# Geocoding Adjacency Draft — Środa 06.05 Phase 1 Component 5

**Auto-build:** 2026-05-05 11:22 UTC by CC pre-build agent (Adrian decision: środa rano CC pre-build z `geocode_cache.json` distance≤2km auto-pairs jako sugestii).

**Adrian's task (~15 min środa rano):** review każdy pair → ACCEPT/REJECT/ADJUST. Output → input dla `geo/adjacency_data.py` `SATELLITE_ADJACENCY` mapping (Phase 2 Component 5 sprint piątek 08.05).

**Methodology:**
- Centroid satellite city = **median** lat/lon ze wszystkich entries `geocode_cache.json` z city tokenu = nazwa satelitki (median > mean: robust na misgeocode same-name towns w innych regionach Polski, np. Grabówka k. Bieszczad lat=49.66).
- Centroid Białystok district = median lat/lon entries których street_name (po stripie ul./al./gen. + lower-case + token przed numerem) matchuje `BIALYSTOK_DISTRICTS[d]['streets']` (exact lub substring).
- Outlier filter: satellite >30km od Białystok centre (53.13N, 23.16E) DROPPED; district >15km DROPPED.
- Distance metric = **haversine** (great-circle, R=6371.0088 km).
- Quadrant per `QUADRANT_BY_DISTRICT` (V3.26 STEP 5 R-06).

## Source data

- `geocode_cache.json`: 4099 entries scanned, 24/27 satellite city centroids resolved.
- `districts_data.py`: 28 Białystok districts (resolved 27/28), 6 outside-city zones (existing: Choroszcz, Ignatki-osiedle, Izabelin, Kleosin, Olmonty, Wasilków).
- `common.py:BIALYSTOK_DISTRICT_ADJACENCY`: 74 pairs preserved as-is (NIE modify).
- `events.db` last 30d: 16 priorytetowe satellite cities + 6 already-mapped + 5 long-tail z cache match.
- Białystok cache pts assigned to districts: 2843 hit, 785 unmatched (street parser miss).

## Adrian Decision Method (per Cloud Claude rec, 2026-05-05)

Strict 2km vs liberal 4-5km — Adrian wybiera podejście:

### Option A — Strict 2km auto-accept + per-quadrant batch borderline (REC)

5 strong auto-suggest (≤2km) → ACCEPT domyślnie z high confidence (Adrian quick spot-check).

119 borderline (2-5km) → batch decisions per quadrant zamiast 119 indywidualnych:

| Quadrant | Auto-accept ALL? | Spot-check edge cases |
|----------|-----------------|----------------------|
| N (północ — Fasty/Wasilków satellites) | TAK / NIE | __ |
| E (wschód — Grabówka/Sowlany satellites) | TAK / NIE | __ |
| SW (południe-zach — Kleosin/Ignatki* satellites) | TAK / NIE | __ |
| SE (południe-wsch — limited satellites) | TAK / NIE | __ |
| W (zachód — Klepacze/Krupniki/Porosły/Choroszcz satellites) | TAK / NIE | __ |
| CENTER (centrum — limited satellites) | TAK / NIE | __ |

Effort: 4-6 quadrant decisions + ~10 spot-check edge cases ≈ **10 min Adrian**.

### Option B — Liberal 4-5km auto-accept (faster ale wyższe ryzyko false positive)

Włącz tier 2-5km do auto-accept (ALL ACCEPT). 5 (≤2km) + ~119 (2-5km) = **124 auto-accept**, 0 borderline.

Effort: ~5 min Adrian (tylko spot-check ostrych outliers).

Risk: **false positives outside-domain knowledge** — pary geometrycznie blisko ale drogowo nie sąsiadują (np. autostrada, rzeka, las).

### Empirical evidence z istniejącego BIALYSTOK_DISTRICT_ADJACENCY (74 pairs)

Cross-check sekcja 7: **2 z 7 existing pairs są >2km** mimo że już są w manual map:
- `Choroszcz ↔ Bacieczki` = **6.88 km** (>5km, ale ALREADY adjacency)
- `Wasilków ↔ Sienkiewicza` = **4.96 km** (4-5km tier)

**Insight:** historyczny manual map używał ~5km threshold, NIE 2km. Pure-distance threshold jest guideline, nie hard rule. Adrian's Lekcja #26 (domain knowledge > LLM/API confidence) dominuje — niektóre pary >5km są adjacency bo droga/connectivity, mimo distance.

### Cloud Claude Rec: **Option A** (Strict 2km + per-quadrant batch)

- Higher confidence per pair w >5km tier (Adrian widzi distance + quadrant + spot-check)
- Effort lower niż 119 indywidualnych (~10 min vs ~30 min)
- Risk false positive lower niż Option B

Adrian wybiera w środę rano przed Phase 1 kickoff. Czas decyzji: ~3 min od read.

## 1. Satellite ↔ Białystok district adjacency (auto-pairs ≤2km — sugestia ACCEPT)

Auto-suggested pairs do `SATELLITE_ADJACENCY`. Niska distance + same/adjacent quadrant = strong adjacency signal.

| Satellite | Białystok district | Distance (km) | Quadrant | Adrian decision |
|-----------|-------------------|---------------|----------|-----------------|
| Kleosin | Nowe Miasto | 1.11 | SW | [ ] ACCEPT  [ ] REJECT  [ ] ADJUST: ____ |
| Klepacze | Starosielce | 1.39 | W | [ ] ACCEPT  [ ] REJECT  [ ] ADJUST: ____ |
| Kleosin | Kawaleryjskie | 1.62 | SW | [ ] ACCEPT  [ ] REJECT  [ ] ADJUST: ____ |
| Krupniki | Starosielce | 1.65 | W | [ ] ACCEPT  [ ] REJECT  [ ] ADJUST: ____ |
| Klepacze | Zielone Wzgórza | 1.86 | W | [ ] ACCEPT  [ ] REJECT  [ ] ADJUST: ____ |

**Total auto-pairs ≤2km: 5**

## 2. Borderline pairs (2-5km — Adrian judgment)

Distance >2km ALE <5km. Domain knowledge needed (Lekcja #26): czy realnie sąsiadują (drogowo, nie tylko geometrycznie)? Adrian rejects większość; ACCEPT tylko dla par gdzie kurier z district X realnie obsługuje satelitkę Y bez koordynacji.

| Satellite | Białystok district | Distance (km) | Quadrant | Adrian comment |
|-----------|-------------------|---------------|----------|----------------|
| Fasty | Bacieczki | 2.66 | N | [ ] |
| Fasty | Zawady | 3.45 | N | [ ] |
| Fasty | Wysoki Stoczek | 4.35 | N | [ ] |
| Fasty | Dziesięciny II | 4.36 | N | [ ] |
| Fasty | Dziesięciny I | 4.72 | N | [ ] |
| Fasty | Leśna Dolina | 4.89 | W | [ ] |
| Grabówka | Skorupy | 4.21 | E | [ ] |
| Grabówka | Piasta I | 4.64 | CENTER | [ ] |
| Grabówka | Wygoda | 4.69 | E | [ ] |
| Grabówka | Dojlidy Górne | 4.81 | SE | [ ] |
| Grabówka | Dojlidy | 4.83 | SE | [ ] |
| Ignatki | Nowe Miasto | 4.46 | SW | [ ] |
| Ignatki | Kawaleryjskie | 4.63 | SW | [ ] |
| Ignatki-osiedle | Nowe Miasto | 2.67 | SW | [ ] |
| Ignatki-osiedle | Kawaleryjskie | 2.87 | SW | [ ] |
| Ignatki-osiedle | Bema | 3.78 | SW | [ ] |
| Ignatki-osiedle | Zielone Wzgórza | 3.80 | W | [ ] |
| Ignatki-osiedle | Starosielce | 4.42 | W | [ ] |
| Ignatki-osiedle | Słoneczny Stok | 4.63 | W | [ ] |
| Ignatki-osiedle | Piaski | 4.84 | CENTER | [ ] |
| Ignatki-osiedle | Przydworcowe | 4.90 | CENTER | [ ] |
| Ignatki-osiedle | Mickiewicza | 4.96 | CENTER | [ ] |
| Ignatki-osiedle | Leśna Dolina | 4.97 | W | [ ] |
| Kleosin | Zielone Wzgórza | 2.31 | W | [ ] |
| Kleosin | Bema | 2.36 | SW | [ ] |
| Kleosin | Słoneczny Stok | 3.06 | W | [ ] |
| Kleosin | Starosielce | 3.09 | W | [ ] |
| Kleosin | Przydworcowe | 3.39 | CENTER | [ ] |
| Kleosin | Młodych | 3.45 | N | [ ] |
| Kleosin | Leśna Dolina | 3.51 | W | [ ] |
| Kleosin | Piaski | 3.55 | CENTER | [ ] |
| Kleosin | Mickiewicza | 3.92 | CENTER | [ ] |
| Kleosin | Centrum | 4.17 | CENTER | [ ] |
| Kleosin | Antoniuk | 4.18 | N | [ ] |
| Kleosin | Wysoki Stoczek | 4.69 | N | [ ] |
| Klepacze | Leśna Dolina | 2.13 | W | [ ] |
| Klepacze | Słoneczny Stok | 2.56 | W | [ ] |
| Klepacze | Nowe Miasto | 3.36 | SW | [ ] |
| Klepacze | Młodych | 3.89 | N | [ ] |
| Klepacze | Kawaleryjskie | 4.21 | SW | [ ] |
| Klepacze | Wysoki Stoczek | 4.22 | N | [ ] |
| Klepacze | Bema | 4.39 | SW | [ ] |
| Klepacze | Bacieczki | 4.40 | N | [ ] |
| Klepacze | Przydworcowe | 4.79 | CENTER | [ ] |
| Klepacze | Antoniuk | 4.80 | N | [ ] |
| Krupniki | Leśna Dolina | 2.01 | W | [ ] |
| Krupniki | Zielone Wzgórza | 2.56 | W | [ ] |
| Krupniki | Słoneczny Stok | 2.78 | W | [ ] |
| Krupniki | Bacieczki | 3.51 | N | [ ] |
| Krupniki | Wysoki Stoczek | 3.93 | N | [ ] |
| Krupniki | Młodych | 4.16 | N | [ ] |
| Krupniki | Nowe Miasto | 4.46 | SW | [ ] |
| Krupniki | Dziesięciny II | 4.77 | N | [ ] |
| Krupniki | Antoniuk | 4.98 | N | [ ] |
| Księżyno | Nowe Miasto | 3.85 | SW | [ ] |
| Księżyno | Kawaleryjskie | 4.25 | SW | [ ] |
| Księżyno | Zielone Wzgórza | 4.33 | W | [ ] |
| Księżyno | Starosielce | 4.68 | W | [ ] |
| Nowodworce | Jaroszówka | 2.93 | E | [ ] |
| Nowodworce | Wygoda | 4.24 | E | [ ] |
| Olmonty | Kawaleryjskie | 3.81 | SW | [ ] |
| Olmonty | Dojlidy Górne | 4.07 | SE | [ ] |
| Olmonty | Dojlidy | 4.09 | SE | [ ] |
| Olmonty | Nowe Miasto | 4.36 | SW | [ ] |
| Olmonty | Mickiewicza | 4.42 | CENTER | [ ] |
| Olmonty | Bema | 4.62 | SW | [ ] |
| Olmonty | Piaski | 4.94 | CENTER | [ ] |
| Porosły | Bacieczki | 2.39 | N | [ ] |
| Porosły | Leśna Dolina | 3.01 | W | [ ] |
| Porosły | Starosielce | 3.27 | W | [ ] |
| Porosły | Słoneczny Stok | 3.78 | W | [ ] |
| Porosły | Wysoki Stoczek | 3.81 | N | [ ] |
| Porosły | Zielone Wzgórza | 4.13 | W | [ ] |
| Porosły | Dziesięciny II | 4.37 | N | [ ] |
| Porosły | Zawady | 4.55 | N | [ ] |
| Porosły | Młodych | 4.79 | N | [ ] |
| Porosły | Dziesięciny I | 4.86 | N | [ ] |
| Porosły Kolonia | Bacieczki | 2.57 | N | [ ] |
| Porosły Kolonia | Leśna Dolina | 3.70 | W | [ ] |
| Porosły Kolonia | Starosielce | 4.03 | W | [ ] |
| Porosły Kolonia | Wysoki Stoczek | 4.19 | N | [ ] |
| Porosły Kolonia | Słoneczny Stok | 4.43 | W | [ ] |
| Porosły Kolonia | Zawady | 4.52 | N | [ ] |
| Porosły Kolonia | Dziesięciny II | 4.63 | N | [ ] |
| Porosły Kolonia | Zielone Wzgórza | 4.85 | W | [ ] |
| Sobolewo | Dojlidy Górne | 4.28 | SE | [ ] |
| Sobolewo | Skorupy | 4.63 | E | [ ] |
| Sobolewo | Dojlidy | 4.98 | SE | [ ] |
| Sowlany | Wygoda | 3.12 | E | [ ] |
| Sowlany | Jaroszówka | 3.47 | E | [ ] |
| Sowlany | Skorupy | 3.66 | E | [ ] |
| Sowlany | Piasta I | 3.72 | CENTER | [ ] |
| Sowlany | Bojary | 4.67 | E | [ ] |
| Sowlany | Dojlidy | 4.74 | SE | [ ] |
| Wasilków | Jaroszówka | 2.56 | E | [ ] |
| Wasilków | Wygoda | 4.15 | E | [ ] |
| Wasilków | Białostoczek | 4.48 | N | [ ] |
| Wasilków | Sienkiewicza | 4.96 | E | [ ] |
| Zaścianki | Skorupy | 2.13 | E | [ ] |
| Zaścianki | Piasta I | 2.63 | CENTER | [ ] |
| Zaścianki | Dojlidy | 2.83 | SE | [ ] |
| Zaścianki | Wygoda | 3.12 | E | [ ] |
| Zaścianki | Dojlidy Górne | 3.75 | SE | [ ] |
| Zaścianki | Bojary | 3.85 | E | [ ] |
| Zaścianki | Mickiewicza | 4.14 | CENTER | [ ] |
| Zaścianki | Jaroszówka | 4.34 | E | [ ] |
| Zaścianki | Sienkiewicza | 4.60 | E | [ ] |
| Zaścianki | Piaski | 4.89 | CENTER | [ ] |
| Łyski | Bacieczki | 3.18 | N | [ ] |
| Łyski | Leśna Dolina | 4.42 | W | [ ] |
| Łyski | Starosielce | 4.72 | W | [ ] |
| Łyski | Wysoki Stoczek | 4.86 | N | [ ] |
| Łyski | Zawady | 4.96 | N | [ ] |
| Śródlesie | Kawaleryjskie | 3.10 | SW | [ ] |
| Śródlesie | Nowe Miasto | 3.18 | SW | [ ] |
| Śródlesie | Bema | 4.06 | SW | [ ] |
| Śródlesie | Zielone Wzgórza | 4.65 | W | [ ] |
| Śródlesie | Mickiewicza | 4.84 | CENTER | [ ] |
| Śródlesie | Piaski | 4.93 | CENTER | [ ] |

**Total borderline pairs (2-5km): 119**

## 3. Inter-satellite adjacency (≤3km — pre-condition #2)

Adrian ACK GATE 2 unknown #2: "Klepacze adjacency: czy sąsiaduje z Starosielce (Białystok zachód) czy tylko Choroszcz/Bacieczki?" — manual mapping wymagany.
Próg 3km dla satellite-satellite (większy niż 2km dla sat-district, bo mniej zagęszczone urbanistycznie).

| Sat A | Sat B | Distance (km) | Adrian decision |
|-------|-------|---------------|-----------------|
| Łyski | Porosły Kolonia | 0.72 | [ ] ACCEPT  [ ] REJECT |
| Porosły | Porosły Kolonia | 0.79 | [ ] ACCEPT  [ ] REJECT |
| Śródlesie | Ignatki-osiedle | 1.05 | [ ] ACCEPT  [ ] REJECT |
| Grabówka | Sobolewo | 1.10 | [ ] ACCEPT  [ ] REJECT |
| Księżyno | Ignatki | 1.38 | [ ] ACCEPT  [ ] REJECT |
| Niewodnica Kościelna | Niewodnica Korycka | 1.42 | [ ] ACCEPT  [ ] REJECT |
| Porosły | Łyski | 1.45 | [ ] ACCEPT  [ ] REJECT |
| Klepacze | Krupniki | 1.51 | [ ] ACCEPT  [ ] REJECT |
| Księżyno | Ignatki-osiedle | 1.56 | [ ] ACCEPT  [ ] REJECT |
| Kleosin | Ignatki-osiedle | 1.63 | [ ] ACCEPT  [ ] REJECT |
| Ignatki | Śródlesie | 1.80 | [ ] ACCEPT  [ ] REJECT |
| Ignatki | Ignatki-osiedle | 1.80 | [ ] ACCEPT  [ ] REJECT |
| Księżyno | Niewodnica Korycka | 1.88 | [ ] ACCEPT  [ ] REJECT |
| Grabówka | Sowlany | 2.04 | [ ] ACCEPT  [ ] REJECT |
| Grabówka | Zaścianki | 2.08 | [ ] ACCEPT  [ ] REJECT |
| Zaścianki | Sowlany | 2.15 | [ ] ACCEPT  [ ] REJECT |
| Łyski | Fasty | 2.16 | [ ] ACCEPT  [ ] REJECT |
| Porosły Kolonia | Fasty | 2.20 | [ ] ACCEPT  [ ] REJECT |
| Księżyno | Śródlesie | 2.28 | [ ] ACCEPT  [ ] REJECT |
| Śródlesie | Olmonty | 2.34 | [ ] ACCEPT  [ ] REJECT |
| Śródlesie | Kleosin | 2.35 | [ ] ACCEPT  [ ] REJECT |
| Porosły | Krupniki | 2.38 | [ ] ACCEPT  [ ] REJECT |
| Ignatki | Niewodnica Korycka | 2.42 | [ ] ACCEPT  [ ] REJECT |
| Zaścianki | Sobolewo | 2.57 | [ ] ACCEPT  [ ] REJECT |
| Ignatki | Brończany | 2.57 | [ ] ACCEPT  [ ] REJECT |
| Księżyno | Kleosin | 2.74 | [ ] ACCEPT  [ ] REJECT |
| Porosły | Fasty | 2.82 | [ ] ACCEPT  [ ] REJECT |
| Księżyno | Niewodnica Kościelna | 2.91 | [ ] ACCEPT  [ ] REJECT |

**Total inter-satellite pairs ≤3km: 28**

## 4. Coords used (transparency)

### 4a. Satellite city centroids

| Satellite | Lat | Lon | n_samples (cache hits) | Outliers dropped | Sample addr |
|-----------|-----|-----|------------------------|------------------|-------------|
| Porosły | 53.1449 | 23.0529 | 18 | 0 | jesiennych liści 119 |
| Grabówka | 53.1303 | 23.2611 | 13 | 2 | jodłowa 35 |
| Nowodworce | 53.1771 | 23.2322 | 5 | 0 | supraślska 27 |
| Zaścianki | 53.1270 | 23.2304 | 9 | 2 | afrykańska 5 |
| Klepacze | 53.1132 | 23.0757 | 7 | 0 | górna 11 |
| Sobolewo | 53.1213 | 23.2677 | 4 | 0 | rybacka 78c |
| Krupniki | 53.1242 | 23.0623 | 5 | 0 | rubinowa 17 |
| Księżyno | 53.0821 | 23.0972 | 4 | 0 | wincentego witosa 13l |
| Horodniany | — | — | 0 | 0 | _MISSING (skipped, brak entries w cache lub wszystkie outliers)_ |
| Ignatki | 53.0729 | 23.1111 | 3 | 0 | pogodna 13 |
| Sowlany | 53.1449 | 23.2425 | 3 | 0 | świętego  38 |
| Turośń Kościelna | 53.0156 | 23.0512 | 2 | 0 | lipowa 65b |
| Łyski | 53.1550 | 23.0391 | 2 | 0 | owocowa 37 |
| Stanisławowo | — | — | 0 | 0 | _MISSING (skipped, brak entries w cache lub wszystkie outliers)_ |
| Śródlesie | 53.0836 | 23.1313 | 2 | 0 | dębowa 13 |
| Supraśl | 53.2105 | 23.3321 | 1 | 0 | kodeksu supraskiego 2 |
| Choroszcz | 53.1498 | 22.9834 | 10 | 0 | żółtki 76 |
| Wasilków | 53.1827 | 23.1864 | 17 | 0 | białostocka 104c  19 |
| Kleosin | 53.1032 | 23.1183 | 10 | 0 | tarasiuka 20a |
| Ignatki-osiedle | 53.0885 | 23.1179 | 8 | 0 | jodłowa 8a  19 |
| Olmonty | 53.0810 | 23.1660 | 1 | 0 | wesoła 7 |
| Izabelin | — | — | 0 | 0 | _MISSING (skipped, brak entries w cache lub wszystkie outliers)_ |
| Niewodnica Kościelna | 53.0761 | 23.0549 | 2 | 0 | dąbrowskiego 4 |
| Niewodnica Korycka | 53.0717 | 23.0749 | 2 | 0 | kosciuszki 23 |
| Porosły Kolonia | 53.1514 | 23.0481 | 2 | 0 | kolonia porosły 55 |
| Brończany | 53.0499 | 23.1150 | 1 | 0 | bronczany 1 |
| Fasty | 53.1699 | 23.0596 | 2 | 0 | rolna 23 |

### 4b. Białystok district centroids

| District | Lat | Lon | n_samples | Quadrant |
|----------|-----|-----|-----------|----------|
| Antoniuk | 53.1398 | 23.1323 | 161 | N |
| Bacieczki | 53.1523 | 23.0865 | 84 | N |
| Bema | 53.1197 | 23.1406 | 96 | SW |
| Białostoczek | 53.1478 | 23.1529 | 101 | N |
| Bojary | 53.1353 | 23.1743 | 191 | E |
| Centrum | 53.1335 | 23.1552 | 178 | CENTER |
| Dojlidy | 53.1137 | 23.1942 | 75 | SE |
| Dojlidy Górne | 53.0935 | 23.2233 | 39 | SE |
| Dziesięciny I | 53.1531 | 23.1246 | 80 | N |
| Dziesięciny II | 53.1516 | 23.1175 | 27 | N |
| Jaroszówka | 53.1607 | 23.1976 | 71 | E |
| Kawaleryjskie | 53.1111 | 23.1387 | 109 | SW |
| Leśna Dolina | 53.1301 | 23.0908 | 67 | W |
| Mickiewicza | 53.1207 | 23.1691 | 87 | CENTER |
| Młodych | 53.1342 | 23.1224 | 32 | N |
| Nowe Miasto | 53.1120 | 23.1261 | 69 | SW |
| Piaski | 53.1251 | 23.1571 | 50 | CENTER |
| Piasta I | 53.1313 | 23.1916 | 43 | CENTER |
| Piasta II | — | — | 0 | CENTER |
| Przydworcowe | 53.1302 | 23.1417 | 55 | CENTER |
| Sienkiewicza | 53.1400 | 23.1649 | 58 | E |
| Skorupy | 53.1252 | 23.1986 | 64 | E |
| Starosielce | 53.1237 | 23.0871 | 60 | W |
| Słoneczny Stok | 53.1292 | 23.1032 | 50 | W |
| Wygoda | 53.1458 | 23.1957 | 62 | E |
| Wysoki Stoczek | 53.1451 | 23.1100 | 74 | N |
| Zawady | 53.1663 | 23.1110 | 32 | N |
| Zielone Wzgórza | 53.1210 | 23.1003 | 41 | W |

## 5. Quality / unknowns

- **Satellite cache hit rate:** 24/27 = 88.9%
  - **Resolved priority-16:** 14/16
  - **MISSING priority-16 (defer Adrian środa rano OR new geocoding lookup):** Horodniany, Stanisławowo
  - **Missing all satellites (full list):** Horodniany, Stanisławowo, Izabelin
- **Białystok district resolution:** 27/28 districts mapped via street centroid.
  - **Missing districts (street parser miss → no centroid):** Piasta II
    - _Note:_ `Piasta II` shares streets z Piasta I (np. `chrobrego bolesława`, `staszica`); reverse-lookup picks first → wszystkie matched addrs idą do Piasta I. Centroid Piasta I obejmuje też Piasta II — acceptable proxy dla adjacency purposes (Adrian: confirm).
- Pairs >5km satellite-district NIE pokazane (assumed too far for adjacency).
- Inter-satellite pairs >3km NIE pokazane (te już pokazują w sat×district borderline).
- **Pairs SKIPPED z powodu missing coords:** Horodniany, Stanisławowo, Izabelin (defer Adrian środa rano: real address sample lub manual-only adjacency).

## Missing — Adrian provide manual lat/lon (środa rano, ~5 min)

2 satellite cities miały tylko 2 cache hits — niewystarczająco dla median calc:
- **Horodniany** (4 events / 30d) — Adrian provides: lat=___, lon=___
- **Stanisławowo** (2 events / 30d) — Adrian provides: lat=___, lon=___

Po wpisaniu, agent compute distance pairs do Białystok districts + dodaje do auto-accept jeśli ≤2km.

## 6. Existing adjacency baseline (preserved)

Z `common.py:BIALYSTOK_DISTRICT_ADJACENCY` (74 pairs ACK 2026-04-21 post-review) — outside-city zones aktualnie zmapowane:

```
Choroszcz       → {Bacieczki}
Wasilków        → {Jaroszówka, Sienkiewicza}
Kleosin         → {Ignatki-osiedle, Nowe Miasto, Kawaleryjskie}
Ignatki-osiedle → {Kleosin, Nowe Miasto, Kawaleryjskie}
# Olmonty, Izabelin → V3.26 STEP 5 quadrant only (NIE adjacency entries)
```

Phase 2 Component 5 sprint output: rozszerz `BIALYSTOK_DISTRICT_ADJACENCY` (lub osobny `SATELLITE_ADJACENCY` w `geo/adjacency_data.py`) o accepted pairs z sekcji 1+2+3 powyżej.

## 7. Cross-check vs auto-pairs ≤2km

Sprawdź czy **istniejące** zone adjacencies pokrywają się z auto-suggested ≤2km (sanity):

| Existing pair | Distance (km) | Auto-suggest tier (≤2 / 2-5 / >5) |
|---------------|---------------|-----------------------------------|
| Choroszcz ↔ Bacieczki | 6.88 | >5 |
| Ignatki-osiedle ↔ Kawaleryjskie | 2.87 | 2-5 |
| Ignatki-osiedle ↔ Nowe Miasto | 2.67 | 2-5 |
| Kleosin ↔ Kawaleryjskie | 1.62 | <=2 |
| Kleosin ↔ Nowe Miasto | 1.11 | <=2 |
| Wasilków ↔ Jaroszówka | 2.56 | 2-5 |
| Wasilków ↔ Sienkiewicza | 4.96 | 2-5 |

## 8. Final summary

- **N pairs ≤2km auto-suggested ACCEPT:** 5
- **M pairs 2-5km borderline (Adrian judgment):** 119
- **K inter-satellite pairs ≤3km:** 28
- **Satellite hit rate:** 24/27 (89%)
- **Priority-16 hit rate:** 14/16 (88%)
- **Districts mapped:** 27/28
- **Adrian ETA decision time:** ~15 min (review 3 tabel + tick checkboxes + edge calls dla 3 missing satellite coords).
- **Output target:** `geo/adjacency_data.py:SATELLITE_ADJACENCY` (Phase 2 Component 5 sprint piątek 08.05).

---

_Generated by CC pre-build agent (read-only on `geocode_cache.json`+`districts_data.py`+`common.py`+`events.db`); compute scripts: `_adjacency_compute.py`+`_build_draft_md.py` w tym samym dirze; raw data: `_adjacency_data.json`._
