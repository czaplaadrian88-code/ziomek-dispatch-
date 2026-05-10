"""Compute satellite/district centroids + adjacency distances.
Read-only on cache + districts_data. Writes JSON to stdout.
Used by CC pre-build agent (2026-05-05) for geocoding adjacency draft.
"""
import json, math, sys
sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2.districts_data import (
    BIALYSTOK_DISTRICTS,
    BIALYSTOK_OUTSIDE_CITY_ZONES,
    QUADRANT_BY_DISTRICT,
)

with open('/root/.openclaw/workspace/dispatch_state/geocode_cache.json') as f:
    cache = json.load(f)


def parse_key(k):
    parts = [p.strip() for p in k.split(',') if p.strip()]
    if not parts:
        return None, None
    if parts[-1].lower() in ('polska',):
        parts = parts[:-1]
    if len(parts) >= 2:
        city = parts[-1].lower().strip()
        addr = ', '.join(parts[:-1])
        return addr.lower(), city
    return None, None


per_city = {}
for k, v in cache.items():
    if not isinstance(v, dict):
        continue
    lat = v.get('lat'); lon = v.get('lon')
    if lat is None or lon is None:
        continue
    addr, city = parse_key(k)
    if not city:
        continue
    per_city.setdefault(city, []).append((lat, lon, addr))


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))


SATELLITES = [
    'Porosły', 'Grabówka', 'Nowodworce', 'Zaścianki', 'Klepacze',
    'Sobolewo', 'Krupniki', 'Księżyno', 'Horodniany', 'Ignatki',
    'Sowlany', 'Turośń Kościelna', 'Łyski', 'Stanisławowo', 'Śródlesie',
    'Supraśl',
    'Choroszcz', 'Wasilków', 'Kleosin', 'Ignatki-osiedle', 'Olmonty', 'Izabelin',
    'Niewodnica Kościelna', 'Niewodnica Korycka', 'Porosły Kolonia',
    'Brończany', 'Fasty',
]

# Białystok regional centre approx — used to filter out misgeocoded entries
# (same town name elsewhere in Poland, e.g. "Grabówka" k. Bieszczad lat=49.66).
BIA_CENTER = (53.13, 23.16)
MAX_REGIONAL_KM = 30.0  # anything >30km from Białystok = misgeocode candidate


def median(vals):
    vs = sorted(vals)
    n = len(vs)
    if n == 0:
        return None
    if n % 2 == 1:
        return vs[n // 2]
    return (vs[n // 2 - 1] + vs[n // 2]) / 2


sat_coords = {}
for s in SATELLITES:
    raw = per_city.get(s.lower(), [])
    # Filter outliers: drop entries >MAX_REGIONAL_KM from Białystok
    pts = []
    n_outliers = 0
    for lat, lon, addr in raw:
        d_bia = haversine_km(BIA_CENTER[0], BIA_CENTER[1], lat, lon)
        if d_bia <= MAX_REGIONAL_KM:
            pts.append((lat, lon, addr))
        else:
            n_outliers += 1
    if not pts:
        sat_coords[s] = {
            'lat': None, 'lon': None,
            'n_samples': 0, 'n_outliers_dropped': n_outliers,
            'sample_addrs': [],
        }
        continue
    # Use median for robustness
    med_lat = median([p[0] for p in pts])
    med_lon = median([p[1] for p in pts])
    sat_coords[s] = {
        'lat': round(med_lat, 6),
        'lon': round(med_lon, 6),
        'n_samples': len(pts),
        'n_outliers_dropped': n_outliers,
        'sample_addrs': [p[2] for p in pts[:3]],
    }


bialystok_pts = per_city.get('białystok', []) + per_city.get('bialystok', [])

street_to_district = {}
for d, info in BIALYSTOK_DISTRICTS.items():
    streets = info.get('streets', frozenset())
    for s in streets:
        street_to_district.setdefault(s.lower(), []).append(d)


def extract_street(addr_lc):
    tokens = addr_lc.split()
    out = []
    SKIP = {'ul.', 'ul', 'al.', 'al', 'pl.', 'pl', 'gen.', 'gen', 'św.',
            'os.', 'aleja', 'ulica', 'plac'}
    for t in tokens:
        if t in SKIP or not t:
            continue
        if t[0].isdigit() or '/' in t:
            break
        out.append(t)
    return ' '.join(out).strip()


district_pts = {d: [] for d in BIALYSTOK_DISTRICTS.keys()}
unmatched = 0
for lat, lon, addr in bialystok_pts:
    street = extract_street(addr)
    if not street:
        unmatched += 1
        continue
    matched = None
    if street in street_to_district:
        matched = street_to_district[street]
    else:
        for s_key, dists in street_to_district.items():
            if s_key in street or street in s_key:
                matched = dists
                break
    if matched:
        district_pts[matched[0]].append((lat, lon))
    else:
        unmatched += 1

district_coords = {}
for d, pts in district_pts.items():
    # filter outliers (>15km from Białystok centre — misgeocodes within "białystok" tag)
    filtered = [(la, lo) for la, lo in pts
                if haversine_km(BIA_CENTER[0], BIA_CENTER[1], la, lo) <= 15.0]
    if not filtered:
        district_coords[d] = {'lat': None, 'lon': None, 'n_samples': 0}
        continue
    med_lat = median([p[0] for p in filtered])
    med_lon = median([p[1] for p in filtered])
    district_coords[d] = {
        'lat': round(med_lat, 6),
        'lon': round(med_lon, 6),
        'n_samples': len(filtered),
        'n_outliers_dropped': len(pts) - len(filtered),
    }


sat_dist_pairs = []
for sat, sc in sat_coords.items():
    if sc['lat'] is None:
        continue
    for d, dc in district_coords.items():
        if dc['lat'] is None:
            continue
        dist = haversine_km(sc['lat'], sc['lon'], dc['lat'], dc['lon'])
        sat_dist_pairs.append({
            'sat': sat,
            'district': d,
            'distance_km': round(dist, 2),
            'quadrant': QUADRANT_BY_DISTRICT.get(d, '?'),
            'sat_n': sc['n_samples'],
            'd_n': dc['n_samples'],
        })

inter_sat_pairs = []
sats_with_coords = [s for s, sc in sat_coords.items() if sc['lat'] is not None]
for i, s1 in enumerate(sats_with_coords):
    for s2 in sats_with_coords[i+1:]:
        sc1 = sat_coords[s1]; sc2 = sat_coords[s2]
        dist = haversine_km(sc1['lat'], sc1['lon'], sc2['lat'], sc2['lon'])
        inter_sat_pairs.append({'a': s1, 'b': s2, 'distance_km': round(dist, 2)})

sat_dist_pairs.sort(key=lambda x: (x['sat'], x['distance_km']))
inter_sat_pairs.sort(key=lambda x: x['distance_km'])

out = {
    'sat_coords': sat_coords,
    'district_coords': district_coords,
    'sat_dist_pairs': sat_dist_pairs,
    'inter_sat_pairs': inter_sat_pairs,
    'satellites_input': SATELLITES,
    'cache_total_entries': len(cache),
    'bialystok_pts_used': len(bialystok_pts),
    'bialystok_unmatched': unmatched,
    'existing_outside_city_zones': sorted(BIALYSTOK_OUTSIDE_CITY_ZONES),
}

print(json.dumps(out, ensure_ascii=False, indent=1))
