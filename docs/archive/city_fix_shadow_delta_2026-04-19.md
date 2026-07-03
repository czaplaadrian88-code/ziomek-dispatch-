# City fix shadow delta — 2026-04-19

## Phase 1 — orders_state.json impact
- Total orders in state: 1897
- Orders z non-Białystok miastem w `delivery_address`: 73
- Potentially buggy (non-Bial addr, coords w bbox Białystok): 49

### Top 10 potentially buggy entries (pre-fix)
- order 465234: `'Oleńki 7 Zawady'` city=zawady → coords `[53.164952, 23.122841]` (in Białystok bbox)
- order 465231: `'Horodniany 14C Kleosin'` city=kleosin → coords `[53.088319, 23.110648]` (in Białystok bbox)
- order 465267: `'Kryńska 64 Wasilków'` city=wasilków → coords `[53.196336, 23.209164]` (in Białystok bbox)
- order 465337: `'Leszczynowa 25 Grabówka'` city=grabówka → coords `[53.130362, 23.269412]` (in Białystok bbox)
- order 465354: `'Wasilkowska 47a Białystok'` city=wasilkow → coords `[53.146207, 23.180364]` (in Białystok bbox)
- order 465371: `'Borówkowa 24 Grabówka'` city=grabówka → coords `[53.127982, 23.267427]` (in Białystok bbox)
- order 465381: `'Praska 11 Grabówka'` city=grabówka → coords `[53.128797, 23.254094]` (in Białystok bbox)
- order 465414: `'śródleśna 15 Ignatki-osiedle'` city=ignatki → coords `[53.08473, 23.116224]` (in Białystok bbox)
- order 465450: `'Zambrowska 48 Kleosin'` city=kleosin → coords `[53.093579, 23.118969]` (in Białystok bbox)
- order 465458: `'Jodłowa 3a Ignatki-osiedle'` city=ignatki → coords `[53.087674, 23.116762]` (in Białystok bbox)


## Phase 2 — learning_log 24h bundle analysis
- Total decisions (24h): 267
- bundle_level2 candidates: 320
- Tight bl2_dist < 2km (cross-city false positive kandydaci): 320
- bundle_level3 candidates: 322
- Tight bl3_dev < 2km: 322

## Phase 3 — geocode_cache.json health
- Total entries: 2541
- Entries z kluczem zawierającym inne miasto: 76
- Entries z coords poza bbox Białystok+15km (corrupt): 8

### Out-of-bbox examples
- `bohaterów  8, białystok` → (50.4923, 23.9401)
- `bohaterów  8 białystok` → (50.4923, 23.9401)
- `3x, białystok` → (50.4911, 23.9400)
- `wierzbowa 20a  31,  nr 2, białystok` → (51.8248, 17.4786)
- `5x, białystok` → (50.4916, 23.9402)
- `5x białystok` → (50.4916, 23.9402)
- `piłsudskiego 26, białystok` → (51.9194, 19.1451)
- `piłsudskiego 30, białystok` → (51.9194, 19.1451)


## Summary
- Potentially affected orders w state:   49
- Tight bl2 bundles w 24h (kandydaci):   320
- Cache entries corrupt:                 8

## Rekomendacja
Fix działa dla wszystkich **nowych** NEW_ORDER events od restartu
panel-watcher/shadow-dispatcher. Stare state entries pozostają jak były
(immutable coords po pierwszym geocode — zgodnie z design).

Cache invalidation (minimalna): usunąć 8 out-of-bbox entries,
reszta self-heals przy nowych requestach.

Rollback: `CITY_AWARE_GEOCODING=False` w common.py + restart panel-watcher.
