# GEO-05: fałszywe pary adjacency w BIALYSTOK_DISTRICT_ADJACENCY — werdykt

**Data:** 2026-06-13 | **Sesja:** auton/geo-districts | **Plik:** `common.py:1098 BIALYSTOK_DISTRICT_ADJACENCY` (jedyne źródło adjacency; `district_reverse_lookup.py` NIE ma własnej mapy — to kd-tree delegujący do `drop_zone_from_address`).

## Metodologia (data-driven, nie z pamięci)
Centroidy 33 dzielnic policzone EMPIRYCZNIE z `geocode_cache.json` (13 640 adresów): każdy cached adres → dzielnica przez `drop_zone_from_address`, centroid = mediana lat/lon (odporna na mis-geokody). Min 4 adresy/dzielnicę (wszystkie 33 spełniają, większość 150-1000 adresów). Odległości centroidów = haversine.

## Kontrole spójności (przed geografią)
- **Symetria:** 0 par asymetrycznych (A→B zawsze ⇒ B→A). ✅
- **Self-reference:** brak. ✅
- **Nieznane nazwy:** brak (wszystkie 32 nazwy w adjacency są w DISTRICTS∪OUTSIDE). ✅

Mapa była ACK właściciela 2026-04-21 — struktura solidna. Mediana odległości par sąsiednich = **1.73 km** (rozsądna dla sąsiadujących osiedli).

## Znalezione anomalie (par sąsiednich z dużą odległością centroidów)
Outliery intra-city (>2.8 km), posortowane:

| km | para | werdykt |
|---|---|---|
| **4.38** | **Mickiewicza ↔ Dojlidy Górne** | 🔴 **FAŁSZYWE — naprawione** |
| 3.94 | Bema ↔ Starosielce | duża dzielnica (Bema rozległa), graniczą — zostaje |
| 3.55 | Bacieczki ↔ Zielone Wzgórza | duże dzielnice NW, graniczą — zostaje |
| 3.53 | Bema ↔ Leśna Dolina | graniczą wzdłuż obwodnicy — zostaje |
| 3.46 | Centrum ↔ Dojlidy | Centrum rozległe, graniczą — zostaje |
| 3.34 | Antoniuk ↔ Bacieczki | graniczą — zostaje |
| 3.36 | Piasta I ↔ Jaroszówka | graniczą N — zostaje |
| 3.14 | Sienkiewicza ↔ Jaroszówka | graniczą — zostaje |
| 3.13 | Białostoczek ↔ Zawady | graniczą — zostaje |
| 3.02 | Antoniuk ↔ Zielone Wzgórza | graniczą — zostaje |

Pary z dzielnicami outside-city (Sienkiewicza↔Wasilków 6.87, Bacieczki↔Choroszcz 6.46) = artefakt (centroid odrębnej miejscowości daleko od jej styku z miastem) — NIE błąd adjacency.

## NAPRAWIONE: Mickiewicza ↔ Dojlidy Górne (jedyna jednoznaczna)
- Centroidy: Mickiewicza (53.121, 23.173) — Dojlidy Górne (53.093, 23.219) = **4.38 km**, o 1.6 km dalej niż kolejny najdalszy sąsiad Mickiewicza (Kawaleryjskie 2.77 km).
- **Dojlidy leży DOKŁADNIE między nimi:** Mickiewicza→Dojlidy = 1.71 km, Dojlidy→Dojlidy Górne = 2.75 km. Link Mick→Dojlidy Górne PRZESKAKIWAŁ Dojlidy.
- Dojlidy Górne (małe peryferyjne osiedle SE) graniczy realnie tylko z Dojlidy (i wychodzi za miasto). Link do Mickiewicza = fałszywe sąsiedztwo z czasów ACK 04-21.
- **Fix:** usunięto `'Dojlidy Górne'` z `Mickiewicza` i symetrycznie `'Mickiewicza'` z `Dojlidy Górne`. Dojlidy Górne → `{'Dojlidy'}`. Symetria zachowana.
- **Skutek dla scoringu:** `classify_trajectory` (SIMILAR przy adjacency-hit) i `_drop_proximity_factor` (0.5 dla sąsiadów) nie będą już traktować dostawy Mickiewicza-centrum + Dojlidy-Górne-SE jako „blisko" — co było zawyżeniem bonusu bundla na rozjeżdżonej geometrii.

## Czego NIE ruszono (świadomie — wymaga ACK Adriana, LESSON-QA-11)
**Fałszywe NEGATYWY** (centroidy <1.6 km, ale NIE oznaczone jako sąsiednie): Antoniuk↔Przydworcowe (1.24), Młodych↔Przydworcowe (1.30), Bema↔Piaski (1.38), Antoniuk↔Dziesięciny I (1.50), Mickiewicza↔Piasta I (1.52). **NIE dodane** — bliskość centroidów ≠ wspólna granica (Antoniuk i Przydworcowe rozdziela korytarz kolejowy / Wysoki Stoczek; Przydworcowe ma ciasny klaster sąsiadów Bema/Centrum/Piaski ~1.0-1.3 km). Dodawanie sąsiedztw na podstawie samej odległości centroidów = zgadywanie przeciw zrewidowanej mapie właściciela. To decyzja domenowa dla Adriana.

## Test regresji
`tests/test_geo05_district_adjacency.py` — pilnuje: (a) symetrii, (b) braku self-ref, (c) wszystkie nazwy walidne, (d) brak Mickiewicza↔Dojlidy Górne (lock fixa), (e) Dojlidy Górne ma dokładnie {Dojlidy}, (f) sanity: każda para intra-city ≤ próg centroidowy (z marginesem na duże dzielnice).
