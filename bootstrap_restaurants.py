#!/usr/bin/env python3
"""
Bootstrap restaurant_coords.json z walidacją 3-poziomową.
Usage:
  python3 bootstrap_restaurants.py          # dry-run, raport, NIE zapisuje
  python3 bootstrap_restaurants.py --write  # zapisuje restaurant_coords.json
"""
import sys, json, math, logging, argparse, time
from pathlib import Path
sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2.geocoding import geocode  # zwraca (lat, lon) tuple, sam dokleja ", Białystok, Polska"

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("bootstrap_rest")

IN  = Path("/tmp/restaurant_addresses_from_panel.json")
OUT = Path("/root/.openclaw/workspace/dispatch_state/restaurant_coords.json")

BIALYSTOK_CENTER = (53.1325, 23.1688)
OUTLIER_KM = 15.0
HARD_DUP_DECIMALS = 3     # ~110 m
SOFT_DUP_METERS   = 50.0
GENERIC_POSTCODE = {"15-000"}

# Znane food courts / wspólne budynki — Soft Duplicate OK, nie pytaj
# Restauracje ktore realnie siedza pod tymi samymi koordynatami
# (pasaze, pierzeje, food courty). Whitelist dla HARD duplicates - same coords OK.
HARD_DUPLICATE_WHITELIST = {
    frozenset({96, 114, 138}),        # Kilinskiego 12 pasaz: Raj + 350 Stopni + Grill Kebab
    frozenset({131, 168, 169, 186}),  # Rynek Kosciuszki pierzeja (bez Zapiecka - drugi dojazd)
    frozenset({145, 154, 166}),       # Kaczorowskiego 14/Kopernika 2 (rog): Chinatown + Mama Thai + Rukola K
    frozenset({199, 214}),            # Galeria Biala Milosza 2: Pizzeria 105 + 500 Stopni
    frozenset({162, 190}),            # Sienkiewicza: Ramen Base + Trzy Po Trzy (tech-debt: rozjechac)
}

# Whitelist SOFT duplicates (<50m)
SOFT_DUPLICATE_WHITELIST = {
    frozenset({199, 214, 226}),     # Galeria Biala: Pizzeria 105 + 500 Stopni + Eat Point
    frozenset({53, 96, 138}),       # Maison du cafe (Kilinskiego 10) obok pasazu Kilinskiego 12
    frozenset({53, 114}),           # Maison du cafe obok 350 Stopni
    frozenset({12, 136}),           # Lipowa 12/14: Nalesniki Jak Smok + Sushi Rany Julek
}

# Manual override - rzadki przypadek gdy adres panelu jest poprawny
# ale budynek ma dwa wejscia/adresy i operacyjnie to jeden punkt pickup.
# Nadpisuje wynik geocode() dla podanego address_id koordynatami referencyjnymi.
MANUAL_COORDS_OVERRIDE = {
    154: {"lat": 53.121879, "lng": 23.146168,  # Mama Thai -> Kaczorowskiego 14 (ten sam budynek co Chinatown 145)
          "note": "Budynek na rogu Kaczorowskiego 14 / Kopernika 2, dwa wejscia, ten sam punkt odbioru"},
}

def haversine_m(a, b):
    R = 6371000
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1; dlon = lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(h))

def haversine_km(a, b): return haversine_m(a, b) / 1000.0

def build_query(info):
    # geocode() sam dokleja ", Białystok, Polska" - dajemy tylko ulicę (+ kod jeśli sensowny)
    street = info["street"]
    pc = info.get("post_code")
    if pc and pc not in GENERIC_POSTCODE:
        return f"{street}, {pc}"
    return street

def geocode_all(addresses):
    results = {}
    failed = []
    total = len(addresses)
    for i, (aid_str, info) in enumerate(sorted(addresses.items(), key=lambda x: int(x[0])), 1):
        aid = int(aid_str)
        q = build_query(info)
        try:
            r = geocode(q)  # zwraca (lat, lon) lub None
            if not r:
                failed.append((aid, info["company"], q, "empty result"))
                continue
            lat, lon = r
            results[aid] = {
                "address_id": aid,
                "company": info["company"],
                "street": info["street"],
                "post_code": info.get("post_code"),
                "city": info.get("city"),
                "lat": round(float(lat), 6),
                "lng": round(float(lon), 6),
                "location_type": "UNKNOWN",  # geocoding.py nie zapisuje location_type (tech-debt)
                "source": "google",
                "query": q,
                "geocoded_at": int(time.time()),
            }
            log.info(f"[{i}/{total}] {aid} {info['company'][:30]:30s} -> {lat:.6f},{lon:.6f}")
        except Exception as e:
            failed.append((aid, info["company"], q, str(e)))
            log.warning(f"[{i}/{total}] {aid} {info['company']} FAIL: {e}")
    # Apply manual overrides (np. budynki z dwoma adresami)
    for aid, override in MANUAL_COORDS_OVERRIDE.items():
        if aid in results:
            old_ll = (results[aid]["lat"], results[aid]["lng"])
            results[aid]["lat"] = round(float(override["lat"]), 6)
            results[aid]["lng"] = round(float(override["lng"]), 6)
            results[aid]["source"] = "manual_override"
            results[aid]["override_note"] = override.get("note", "")
            log.info(f"MANUAL OVERRIDE [{aid}] {results[aid]['company']}: {old_ll} -> ({results[aid]['lat']}, {results[aid]['lng']})")
        else:
            log.warning(f"MANUAL_COORDS_OVERRIDE ma [{aid}] ale restauracji nie ma w wynikach geocodingu")
    return results, failed

def validate(results):
    hard = []; soft = []; outliers = []; low_accuracy = []
    items = list(results.items())
    for aid, r in items:
        d_center = haversine_km((r["lat"], r["lng"]), BIALYSTOK_CENTER)
        if d_center > OUTLIER_KM:
            outliers.append((aid, r["company"], round(d_center, 1)))
        # location_type wyłączone - geocoding.py go nie zapisuje (tech-debt)
        pass
    for i in range(len(items)):
        for j in range(i+1, len(items)):
            aid_a, a = items[i]; aid_b, b = items[j]
            la = (round(a["lat"], HARD_DUP_DECIMALS), round(a["lng"], HARD_DUP_DECIMALS))
            lb = (round(b["lat"], HARD_DUP_DECIMALS), round(b["lng"], HARD_DUP_DECIMALS))
            if la == lb:
                pair_ids = {aid_a, aid_b}
                is_wl = any(pair_ids.issubset(wl) for wl in HARD_DUPLICATE_WHITELIST)
                hard.append((aid_a, a["company"], aid_b, b["company"], is_wl))
                continue
            d = haversine_m((a["lat"], a["lng"]), (b["lat"], b["lng"]))
            if d < SOFT_DUP_METERS:
                soft.append((aid_a, a["company"], aid_b, b["company"], round(d, 1)))
    return hard, soft, outliers, low_accuracy

def whitelisted(soft_pair):
    ids = {soft_pair[0], soft_pair[2]}
    for wl in SOFT_DUPLICATE_WHITELIST:
        if ids.issubset(wl):
            return True
    return False

def report(results, failed, hard, soft, outliers, low_acc):
    print("\n" + "="*70)
    print(f"BOOTSTRAP REPORT — {len(results)} OK, {len(failed)} FAIL")
    print("="*70)
    if failed:
        print(f"\n❌ FAILED GEOCODING ({len(failed)}):")
        for aid, name, q, err in failed:
            print(f"  [{aid}] {name} — {q}  →  {err}")
    hard_wl = [h for h in hard if h[4]]
    hard_flagged = [h for h in hard if not h[4]]
    if hard_wl:
        print(f"\n🟢 HARD DUPLICATES WHITELISTED ({len(hard_wl)}) — bundle/pasaz, auto-OK:")
        for aid_a, na, aid_b, nb, _ in hard_wl:
            print(f"  [{aid_a}] {na}  ==  [{aid_b}] {nb}")
    if hard_flagged:
        print(f"\n🔴 HARD DUPLICATES nieznanych ({len(hard_flagged)}) — wymaga poprawki:")
        for aid_a, na, aid_b, nb, _ in hard_flagged:
            print(f"  [{aid_a}] {na}  ==  [{aid_b}] {nb}")
    else:
        print("\n🔴 HARD DUPLICATES (nieznanych): brak ✓")
    soft_flagged = [s for s in soft if not whitelisted(s)]
    soft_whitelisted = [s for s in soft if whitelisted(s)]
    if soft_whitelisted:
        print(f"\n🟢 SOFT DUPLICATES WHITELISTED ({len(soft_whitelisted)}) — food courts, auto-OK:")
        for aid_a, na, aid_b, nb, d in soft_whitelisted:
            print(f"  [{aid_a}] {na}  ~{d}m~  [{aid_b}] {nb}")
    if soft_flagged:
        print(f"\n🟡 SOFT DUPLICATES ({len(soft_flagged)}) — <50m, potwierdź czy to OK (food court) czy błąd:")
        for aid_a, na, aid_b, nb, d in soft_flagged:
            print(f"  [{aid_a}] {na}  ~{d}m~  [{aid_b}] {nb}")
    else:
        print("\n🟡 SOFT DUPLICATES (nieznanych): brak ✓")
    if outliers:
        print(f"\n🟠 OUTLIERS ({len(outliers)}) — >{OUTLIER_KM}km od centrum, sprawdź czy to nie Supraśl/Wasilków przez pomyłkę:")
        for aid, name, d in outliers:
            print(f"  [{aid}] {name} — {d} km od centrum")
    else:
        print("\n🟠 OUTLIERS: brak ✓")
    if low_acc:
        print(f"\n🔵 LOW ACCURACY ({len(low_acc)}) — Google nie zwrócił ROOFTOP, może być +/- kilkadziesiąt m:")
        for aid, name, lt in low_acc:
            print(f"  [{aid}] {name} — {lt}")
    print("\n" + "="*70)
    blocking = bool(hard_flagged) or bool(soft_flagged)
    return blocking

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="Zapisz restaurant_coords.json")
    ap.add_argument("--force", action="store_true", help="Zapisz nawet przy blocking warnings")
    args = ap.parse_args()

    addresses = json.loads(IN.read_text())
    log.info(f"Input: {len(addresses)} adresów z {IN}")

    results, failed = geocode_all(addresses)
    hard, soft, outliers, low_acc = validate(results)
    blocking = report(results, failed, hard, soft, outliers, low_acc)

    if not args.write:
        print("\n[DRY-RUN] Nic nie zapisane. Sprawdź raport, potem: --write")
        return

    if blocking and not args.force:
        print("\n❌ ZAPIS ZABLOKOWANY: są HARD duplicates lub nieznane SOFT duplicates.")
        print("   Popraw ręcznie /tmp/restaurant_addresses_from_panel.json albo dopisz do whitelist.")
        print("   Nadpisz --force jeśli wiesz co robisz.")
        sys.exit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out = {str(aid): r for aid, r in sorted(results.items())}
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log.info(f"✅ Zapisano {len(out)} restauracji → {OUT}")

if __name__ == "__main__":
    main()
