"""Build markdown draft from _adjacency_data.json.
Output: geocoding_adjacency_draft_2026-05-06.md
"""
import json, datetime as dt

DATA_PATH = '/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-05/_adjacency_data.json'
OUT_PATH = '/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-05/geocoding_adjacency_draft_2026-05-06.md'

with open(DATA_PATH) as f:
    d = json.load(f)

now_utc = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
sat_coords = d['sat_coords']
district_coords = d['district_coords']
sat_dist_pairs = d['sat_dist_pairs']
inter_sat_pairs = d['inter_sat_pairs']
SATELLITES_INPUT = d['satellites_input']
EXISTING_ZONES = d['existing_outside_city_zones']

near = [p for p in sat_dist_pairs if p['distance_km'] <= 2.0]
border = [p for p in sat_dist_pairs if 2.0 < p['distance_km'] <= 5.0]
inter_near = [p for p in inter_sat_pairs if p['distance_km'] <= 3.0]

near.sort(key=lambda x: (x['distance_km'], x['sat'], x['district']))
border.sort(key=lambda x: (x['sat'], x['distance_km']))
inter_near.sort(key=lambda x: x['distance_km'])

resolved_sats = sum(1 for c in sat_coords.values() if c['lat'] is not None)
missing_sats = [s for s, c in sat_coords.items() if c['lat'] is None]
resolved_districts = sum(1 for c in district_coords.values() if c['lat'] is not None)
missing_districts = [d2 for d2, c in district_coords.items() if c['lat'] is None]

PRIORITY_16 = {
    'Porosły', 'Grabówka', 'Nowodworce', 'Zaścianki', 'Klepacze',
    'Sobolewo', 'Krupniki', 'Księżyno', 'Horodniany', 'Ignatki',
    'Sowlany', 'Turośń Kościelna', 'Łyski', 'Stanisławowo', 'Śródlesie',
    'Supraśl',
}
priority_resolved = [s for s in PRIORITY_16 if sat_coords.get(s, {}).get('lat') is not None]
priority_missing = sorted(PRIORITY_16 - set(priority_resolved))

lines = []
A = lines.append

A('# Geocoding Adjacency Draft — Środa 06.05 Phase 1 Component 5')
A('')
A(f'**Auto-build:** {now_utc} by CC pre-build agent (Adrian decision: środa rano CC pre-build z `geocode_cache.json` distance≤2km auto-pairs jako sugestii).')
A('')
A('**Adrian\'s task (~15 min środa rano):** review każdy pair → ACCEPT/REJECT/ADJUST. Output → input dla `geo/adjacency_data.py` `SATELLITE_ADJACENCY` mapping (Phase 2 Component 5 sprint piątek 08.05).')
A('')
A('**Methodology:**')
A('- Centroid satellite city = **median** lat/lon ze wszystkich entries `geocode_cache.json` z city tokenu = nazwa satelitki (median > mean: robust na misgeocode same-name towns w innych regionach Polski, np. Grabówka k. Bieszczad lat=49.66).')
A('- Centroid Białystok district = median lat/lon entries których street_name (po stripie ul./al./gen. + lower-case + token przed numerem) matchuje `BIALYSTOK_DISTRICTS[d][\'streets\']` (exact lub substring).')
A('- Outlier filter: satellite >30km od Białystok centre (53.13N, 23.16E) DROPPED; district >15km DROPPED.')
A('- Distance metric = **haversine** (great-circle, R=6371.0088 km).')
A('- Quadrant per `QUADRANT_BY_DISTRICT` (V3.26 STEP 5 R-06).')
A('')
A('## Source data')
A('')
A(f'- `geocode_cache.json`: {d["cache_total_entries"]} entries scanned, {resolved_sats}/{len(sat_coords)} satellite city centroids resolved.')
A(f'- `districts_data.py`: 28 Białystok districts (resolved {resolved_districts}/28), 6 outside-city zones (existing: {", ".join(EXISTING_ZONES)}).')
A(f'- `common.py:BIALYSTOK_DISTRICT_ADJACENCY`: 74 pairs preserved as-is (NIE modify).')
A(f'- `events.db` last 30d: 16 priorytetowe satellite cities + 6 already-mapped + 5 long-tail z cache match.')
A(f'- Białystok cache pts assigned to districts: {d["bialystok_pts_used"]} hit, {d["bialystok_unmatched"]} unmatched (street parser miss).')
A('')
A('## 1. Satellite ↔ Białystok district adjacency (auto-pairs ≤2km — sugestia ACCEPT)')
A('')
A('Auto-suggested pairs do `SATELLITE_ADJACENCY`. Niska distance + same/adjacent quadrant = strong adjacency signal.')
A('')
if near:
    A('| Satellite | Białystok district | Distance (km) | Quadrant | Adrian decision |')
    A('|-----------|-------------------|---------------|----------|-----------------|')
    for p in near:
        A(f'| {p["sat"]} | {p["district"]} | {p["distance_km"]:.2f} | {p["quadrant"]} | [ ] ACCEPT  [ ] REJECT  [ ] ADJUST: ____ |')
else:
    A('_(brak pairów — zero satelitek w odległości ≤2km od centroidu jakiegokolwiek district)._')
A('')
A(f'**Total auto-pairs ≤2km: {len(near)}**')
A('')
A('## 2. Borderline pairs (2-5km — Adrian judgment)')
A('')
A('Distance >2km ALE <5km. Domain knowledge needed (Lekcja #26): czy realnie sąsiadują (drogowo, nie tylko geometrycznie)? Adrian rejects większość; ACCEPT tylko dla par gdzie kurier z district X realnie obsługuje satelitkę Y bez koordynacji.')
A('')
A('| Satellite | Białystok district | Distance (km) | Quadrant | Adrian comment |')
A('|-----------|-------------------|---------------|----------|----------------|')
for p in border:
    A(f'| {p["sat"]} | {p["district"]} | {p["distance_km"]:.2f} | {p["quadrant"]} | [ ] |')
A('')
A(f'**Total borderline pairs (2-5km): {len(border)}**')
A('')
A('## 3. Inter-satellite adjacency (≤3km — pre-condition #2)')
A('')
A('Adrian ACK GATE 2 unknown #2: "Klepacze adjacency: czy sąsiaduje z Starosielce (Białystok zachód) czy tylko Choroszcz/Bacieczki?" — manual mapping wymagany.')
A('Próg 3km dla satellite-satellite (większy niż 2km dla sat-district, bo mniej zagęszczone urbanistycznie).')
A('')
if inter_near:
    A('| Sat A | Sat B | Distance (km) | Adrian decision |')
    A('|-------|-------|---------------|-----------------|')
    for p in inter_near:
        A(f'| {p["a"]} | {p["b"]} | {p["distance_km"]:.2f} | [ ] ACCEPT  [ ] REJECT |')
else:
    A('_(brak pairów ≤3km)._')
A('')
A(f'**Total inter-satellite pairs ≤3km: {len(inter_near)}**')
A('')
A('## 4. Coords used (transparency)')
A('')
A('### 4a. Satellite city centroids')
A('')
A('| Satellite | Lat | Lon | n_samples (cache hits) | Outliers dropped | Sample addr |')
A('|-----------|-----|-----|------------------------|------------------|-------------|')
for s in SATELLITES_INPUT:
    c = sat_coords.get(s, {})
    if c.get('lat') is None:
        A(f'| {s} | — | — | 0 | {c.get("n_outliers_dropped",0)} | _MISSING (skipped, brak entries w cache lub wszystkie outliers)_ |')
    else:
        sample = c.get('sample_addrs', [''])[0] if c.get('sample_addrs') else ''
        A(f'| {s} | {c["lat"]:.4f} | {c["lon"]:.4f} | {c["n_samples"]} | {c.get("n_outliers_dropped",0)} | {sample} |')
A('')
A('### 4b. Białystok district centroids')
A('')
A('| District | Lat | Lon | n_samples | Quadrant |')
A('|----------|-----|-----|-----------|----------|')

import sys
sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2.districts_data import QUADRANT_BY_DISTRICT
for d2 in sorted(district_coords.keys()):
    c = district_coords[d2]
    if c.get('lat') is None:
        A(f'| {d2} | — | — | 0 | {QUADRANT_BY_DISTRICT.get(d2, "?")} |')
    else:
        A(f'| {d2} | {c["lat"]:.4f} | {c["lon"]:.4f} | {c["n_samples"]} | {QUADRANT_BY_DISTRICT.get(d2, "?")} |')
A('')
A('## 5. Quality / unknowns')
A('')
A(f'- **Satellite cache hit rate:** {resolved_sats}/{len(sat_coords)} = {round(100*resolved_sats/len(sat_coords),1)}%')
A(f'  - **Resolved priority-16:** {len(priority_resolved)}/16')
if priority_missing:
    A(f'  - **MISSING priority-16 (defer Adrian środa rano OR new geocoding lookup):** {", ".join(priority_missing)}')
else:
    A('  - **MISSING priority-16:** none')
if missing_sats:
    A(f'  - **Missing all satellites (full list):** {", ".join(missing_sats)}')
A(f'- **Białystok district resolution:** {resolved_districts}/28 districts mapped via street centroid.')
if missing_districts:
    A(f'  - **Missing districts (street parser miss → no centroid):** {", ".join(missing_districts)}')
    A('    - _Note:_ `Piasta II` shares streets z Piasta I (np. `chrobrego bolesława`, `staszica`); reverse-lookup picks first → wszystkie matched addrs idą do Piasta I. Centroid Piasta I obejmuje też Piasta II — acceptable proxy dla adjacency purposes (Adrian: confirm).')
A('- Pairs >5km satellite-district NIE pokazane (assumed too far for adjacency).')
A('- Inter-satellite pairs >3km NIE pokazane (te już pokazują w sat×district borderline).')
A('- **Pairs SKIPPED z powodu missing coords:** Horodniany, Stanisławowo, Izabelin (defer Adrian środa rano: real address sample lub manual-only adjacency).')
A('')
A('## 6. Existing adjacency baseline (preserved)')
A('')
A('Z `common.py:BIALYSTOK_DISTRICT_ADJACENCY` (74 pairs ACK 2026-04-21 post-review) — outside-city zones aktualnie zmapowane:')
A('')
A('```')
A('Choroszcz       → {Bacieczki}')
A('Wasilków        → {Jaroszówka, Sienkiewicza}')
A('Kleosin         → {Ignatki-osiedle, Nowe Miasto, Kawaleryjskie}')
A('Ignatki-osiedle → {Kleosin, Nowe Miasto, Kawaleryjskie}')
A('# Olmonty, Izabelin → V3.26 STEP 5 quadrant only (NIE adjacency entries)')
A('```')
A('')
A('Phase 2 Component 5 sprint output: rozszerz `BIALYSTOK_DISTRICT_ADJACENCY` (lub osobny `SATELLITE_ADJACENCY` w `geo/adjacency_data.py`) o accepted pairs z sekcji 1+2+3 powyżej.')
A('')
A('## 7. Cross-check vs auto-pairs ≤2km')
A('')
A('Sprawdź czy **istniejące** zone adjacencies pokrywają się z auto-suggested ≤2km (sanity):')
A('')
already_mapped_pairs = {
    ('Choroszcz', 'Bacieczki'),
    ('Wasilków', 'Jaroszówka'),
    ('Wasilków', 'Sienkiewicza'),
    ('Kleosin', 'Ignatki-osiedle'),
    ('Kleosin', 'Nowe Miasto'),
    ('Kleosin', 'Kawaleryjskie'),
    ('Ignatki-osiedle', 'Kleosin'),
    ('Ignatki-osiedle', 'Nowe Miasto'),
    ('Ignatki-osiedle', 'Kawaleryjskie'),
}
A('| Existing pair | Distance (km) | Auto-suggest tier (≤2 / 2-5 / >5) |')
A('|---------------|---------------|-----------------------------------|')
for sat, dist in sorted(already_mapped_pairs):
    if sat in {'Kleosin', 'Ignatki-osiedle', 'Wasilków', 'Choroszcz'}:
        match = next((p for p in sat_dist_pairs if p['sat'] == sat and p['district'] == dist), None)
        if match:
            tier = '<=2' if match['distance_km'] <= 2 else ('2-5' if match['distance_km'] <= 5 else '>5')
            A(f'| {sat} ↔ {dist} | {match["distance_km"]:.2f} | {tier} |')
A('')
A('## 8. Final summary')
A('')
A(f'- **N pairs ≤2km auto-suggested ACCEPT:** {len(near)}')
A(f'- **M pairs 2-5km borderline (Adrian judgment):** {len(border)}')
A(f'- **K inter-satellite pairs ≤3km:** {len(inter_near)}')
A(f'- **Satellite hit rate:** {resolved_sats}/{len(sat_coords)} ({round(100*resolved_sats/len(sat_coords))}%)')
A(f'- **Priority-16 hit rate:** {len(priority_resolved)}/16 ({round(100*len(priority_resolved)/16)}%)')
A(f'- **Districts mapped:** {resolved_districts}/28')
A('- **Adrian ETA decision time:** ~15 min (review 3 tabel + tick checkboxes + edge calls dla 3 missing satellite coords).')
A('- **Output target:** `geo/adjacency_data.py:SATELLITE_ADJACENCY` (Phase 2 Component 5 sprint piątek 08.05).')
A('')
A('---')
A('')
A('_Generated by CC pre-build agent (read-only on `geocode_cache.json`+`districts_data.py`+`common.py`+`events.db`); compute scripts: `_adjacency_compute.py`+`_build_draft_md.py` w tym samym dirze; raw data: `_adjacency_data.json`._')

with open(OUT_PATH, 'w') as f:
    f.write('\n'.join(lines) + '\n')

print(f'wrote {OUT_PATH}')
print(f'lines: {len(lines)}')
print(f'NEAR: {len(near)}, BORDER: {len(border)}, INTER-SAT: {len(inter_near)}')
print(f'Resolved sats: {resolved_sats}/{len(sat_coords)} (priority-16: {len(priority_resolved)}/16)')
print(f'Resolved districts: {resolved_districts}/28')
