---
name: Lekcja #73 — Robust median + outlier filter centroid pattern
description: median lat/lon (NIE mean) + 30km outlier filter automatycznie wykrył same-name towns w innych regionach (Grabówka k. Bieszczad, Zaścianki w Pomorzu); reusable design pattern dla multi-region geo-scaling (Warszawa expansion, Restimo franczyza, future tenants)
type: feedback
---

# Lekcja #73 — Robust median + outlier filter centroid pattern

## Pryncypium

Gdy budujesz centroid z multi-source data (geocode_cache, addresses, panel events), używaj **median (NIE mean) + outlier filter** (≥30km od reference center) jako defensive design. To 2-warstwowa obrona przed cross-region pollution w geocode lookup.

- **Mean zawodzi** gdy pojedynczy outlier z innego regionu Polski dominates (1× lat=49.66 + 4× lat=53.13 daje mean=52.43 — szkodliwy drift).
- **Median jest robust** — outlier nie wpływa, dopóki >50% próbek jest w-region.
- **Outlier filter (≥30km od centrum miasta)** dropuje cross-region samples PRZED median calc — extra warstwa.

## Evidence (2026-05-05 geocoding adjacency draft pre-build)

Median + 30km outlier filter automatycznie wykrył **2 same-name false positives** w `geocode_cache.json` (4099 entries scanned dla 27 satellite cities Białystok):

- **Grabówka k. Bieszczad** (lat=49.66, ~370km od Białystok 53.13N) — town w innym region (woj. podkarpackie). Mean centroid by zniekształcił Grabówka Białystok pos by ~2-3km gdyby ten outlier dominował small sample. Outlier filter dropped automatically.
- **Zaścianki w Pomorzu** (lat=54.18, ~270km od Białystok) — town w woj. pomorskim. Same wynik — auto-dropped.

Bez tego pattern: false adjacency suggestions, np. Grabówka-Białystok kazałby pokazywać 200km+ dystans → silently broken bundling logic.

## Reusability dla multi-region scaling

Wszystkie planowane multi-region setups Ziomek/related **WYMAGAJĄ** tego pattern żeby unik`ąć cross-region pollution:

- **Warsaw expansion** (Q3 2026) — geocode_cache shared dla Białystok+Warsaw potencjalnie zawiera same-named streets/dzielnice (Centrum, Skorupy, etc. + małe town names overlap).
- **Restimo franczyza** — multi-tenant, multi-city architecture; każdy tenant ma reference center, outlier filter per-tenant prevents cross-pollution.
- **Bolt Food integration** — third-party order stream może zawierać addresses spoza domain area; reference-center filter jako pierwsza obrona.

Pattern jest cheap (Python `statistics.median()` + haversine call), reusable bez modyfikacji per region.

## Anti-patterns

- **Mean centroid bez outlier filter** — single far-away outlier z N=2 sample completely poisons centroid.
- **No outlier filter** — cross-region pollution silent, manifests jako "weird distances" downstream (impossible adjacency suggestions, wrong "po drodze" decisions).
- **Per-region threshold tuning** — manual maintenance burden (każdy nowy tenant/region wymaga distance thresholds re-cal). Universal 30km filter = good-enough dla city-scale tenants.
- **Mean + manual outlier rejection** — wymaga human review every cache rebuild, NIE skaluje się.

## Cross-refs

- **Lekcja #26** (domain knowledge > LLM/API confidence) — corroborates: Nominatim sometimes returns same-name town wrong region; defensive median+filter compensates dla LLM/geocoder fallibility.
- **TASK E Geocoding** (Sr+Pt sprint) — Phase 1 Component 5 input MUSI używać tego pattern.
- **V3.12 city-aware geocoding fix** (2026-04-19) — sister incident: panel_client nie parsował miasta klienta → cache key collision między miastami; ten sam class problemu (cross-region pollution), inne layer (key vs value).
- **Z3 (zasada kardynalna — buduj na lata, multi-tenant ready)** — pattern jest explicit Z3 implementation dla geo data.
