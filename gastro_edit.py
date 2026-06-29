#!/usr/bin/env python3
"""
gastro_edit.py — edytuje pola zlecenia w panelu gastro (NadajeSz) przez `update-zamowienie`.

⚠ OUTWARD-FACING: realnie zmienia zlecenie w panelu gastro (źródło prawdy, Ziomek to czyta).
   Domyślnie DRY-RUN (pokazuje payload, NIC nie wysyła). Realny zapis TYLKO z flagą --commit.

BEZPIECZNY MERGE: formularz update-zamowienie wysyła WSZYSTKIE pola naraz → najpierw czytamy
aktualne zlecenie (edit-zamowienie) i odtwarzamy KOMPLET pól formularza wiernie wg JS panelu,
a nadpisujemy TYLKO te podane w --opcjach. Inaczej pominięte pola zostałyby wyzerowane.

Mapowanie pole-formularza ← pole-zlecenia (z gastro new_custom.js, modal #edit_zamowienie):
  modal_restauracja        ← id_address
  modal_street             ← street
  modal_nr_domu            ← nr_domu
  modal_nr_mieszkania      ← nr_mieszkania
  modal_city               ← id_location_to            (MIASTO = id lokalizacji; Białystok=1)
  modal_phone              ← phone
  modal_price              ← price                      (kwota do pobrania / COD)
  modal_delivery_price     ← delivery_price
  modal_platnosc           ← price?(platnosc?'karta':'gotowka'):'brak'
  modal_uwagi              ← uwagi
  modal_status_zamowienia  ← id_status_zamowienia
  modal_kurier_select      ← id_kurier
(Czas odbioru NIE jest w tym formularzu — to osobny modal/endpoint. Czas zmieniaj gastro_assign --time.)

Użycie:
  python3 gastro_edit.py --id 483504 --city Białystok            # dry-run (pokaż payload)
  python3 gastro_edit.py --id 483504 --city Białystok --commit   # realny zapis
  python3 gastro_edit.py --id 483504 --uwagi "test" --commit
"""
import os, sys, json, re, argparse, urllib.request, urllib.parse, http.cookiejar
from pathlib import Path

# Plik mieszka w dispatch_v2/ (repo ziomek-dispatch-), ale bywa wołany przez symlink
# scripts/gastro_edit.py (panel). Zapewnij, że katalog `scripts/` (zawiera pakiet dispatch_v2)
# jest na sys.path — niezależnie od ścieżki/symlinku inwokacji (realpath rozwija symlink).
# Lazy `from dispatch_v2 import …` w regeocode_and_update tego wymaga.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

BASE = 'https://www.gastro.nadajesz.pl'


def _first_existing(*paths):
    for p in paths:
        if p and Path(p).exists():
            return Path(p)
    return Path(paths[-1])


PANEL_ENV = _first_existing(
    os.environ.get('PANEL_ENV_FILE'),
    '/root/.openclaw/workspace/.secrets/panel.env',
    '/home/node/.openclaw/workspace/.secrets/panel.env',
)


def login():
    """Loguje do gastro, zwraca (csrf_token, html_strony_zlecen). HTML potrzebny do mapy miast."""
    env = dict(l.strip().split('=', 1) for l in PANEL_ENV.read_text().splitlines() if '=' in l)
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
    urllib.request.install_opener(opener)
    r1 = urllib.request.urlopen(f'{BASE}/admin2017/login')
    t = re.search(r'name="_token" value="([^"]+)"', r1.read().decode()).group(1)
    urllib.request.urlopen(urllib.request.Request(
        f'{BASE}/admin2017/login',
        urllib.parse.urlencode({'email': env['PANEL_LOGIN'], 'password': env['PANEL_PASSWORD'], '_token': t}).encode(),
        headers={'Referer': f'{BASE}/admin2017/login', 'Origin': BASE,
                 'Content-Type': 'application/x-www-form-urlencoded'}))
    html = urllib.request.urlopen(f'{BASE}/admin2017/new/orders/zlecenia').read().decode('utf-8', 'ignore')
    csrf = re.search(r"var TOKEN = '([^']+)'", html).group(1)
    return csrf, html


def read_order(zid, csrf):
    """Czyta aktualne zlecenie przez edit-zamowienie. Zwraca dict 'zlecenie' (lub {})."""
    req = urllib.request.Request(
        f'{BASE}/admin2017/new/orders/edit-zamowienie',
        urllib.parse.urlencode({'_token': csrf, 'id_zlecenie': zid}).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest'})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    return data.get('zlecenie', {}) or {}


def _city_option_pairs(html):
    """Lista (id, nazwa) opcji selecta modal_city z formularza update-zamowienie."""
    i = html.find('update-zamowienie'); j = html.find('</form>', i)
    form = html[i:j] if i >= 0 else ''
    m = re.search(r'name="modal_city".*?</select>', form, re.DOTALL)
    pairs = []
    if m:
        for val, lab in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>\s*([^<]*)', m.group(0)):
            lab = lab.strip()
            if val and lab:
                pairs.append((val, lab))
    return pairs


def city_options(html):
    """Mapa nazwa_miasta(lower) -> id (value selecta modal_city)."""
    return {lab.lower(): val for val, lab in _city_option_pairs(html)}


def city_names_by_id(html):
    """Mapa id -> nazwa miasta (do re-geocode po edycji — geocoder Ziomka chce nazwę)."""
    return {val: lab for val, lab in _city_option_pairs(html)}


def _build_display_address(street, nr_domu, nr_miesz):
    """Tekst adresu dostawy z części edycji: 'Ulica nr_domu/nr_miesz' (apartament zachowany).
    Nazwa ulicy ZAWSZE na początku — district-parser (`drop_zone_from_address`) bierze część
    przed pierwszym numerem, więc strefa/dystrykt liczy się poprawnie. Miasto = osobne pole
    `delivery_city`, nie doklejamy (format zgodny z klasą zleceń typu 'Mroźna 10/23'). Czysta."""
    street = (street or "").strip()
    nr_domu = (nr_domu or "").strip()
    nr_miesz = (nr_miesz or "").strip()
    out = street
    if nr_domu:
        out = f"{out} {nr_domu}".strip()
        if nr_miesz and "/" not in nr_domu:
            out = f"{out}/{nr_miesz}"
    return out.strip()


def regeocode_and_update(order_id, full_address, city_name, display_address=None):
    """Po realnej zmianie adresu/miasta w gastro: przelicz koordy nowego adresu i wpisz je do
    orders_state Ziomka (delivery_coords) przez state_machine.upsert_order — JEDYNY bezpieczny
    writer (fcntl.flock + merge-preserving). Demon NIE re-geokoduje istniejących zleceń, więc
    wpisane koordy się utrzymają. FAIL-SOFT: błąd importu/geokodu/zapisu → log + None (zapis do
    gastro już się udał; koordy można poprawić ręcznie/następnym tickiem). Tylko w dispatch venv.

    SYNC TEKST (flaga ENABLE_REGEOCODE_SYNC_TEXT, 2026-06-29): domyślnie OFF = zachowanie sprzed
    fixu (TYLKO coords). ON = zapisz też `delivery_address` (=display_address) + `delivery_city`
    spójnie z coords — koniec asymetrii „pin poprawiony, tekst stary" (case 484269 Można≠Mroźna,
    która kłamała w konsoli/apce I myliła district w scoringu). Tekst też karmi
    `drop_zone_from_address` → poprawny dystrykt."""
    try:
        from dispatch_v2 import geocoding, state_machine, common as C
    except Exception as e:  # noqa: BLE001
        print(f"REGEOCODE_SKIP: brak modułów dispatch_v2 ({e}) — koordy bez zmian")
        return None
    try:
        coords = geocoding.geocode(full_address, city=city_name)
    except Exception as e:  # noqa: BLE001
        print(f"REGEOCODE_SKIP: błąd geokodu {full_address!r}/{city_name!r}: {e}")
        return None
    if not coords:
        print(f"REGEOCODE_SKIP: brak koordów dla {full_address!r}, {city_name!r}")
        return None
    upsert = {"delivery_coords": list(coords)}
    sync_text = False
    try:
        if C.flag("ENABLE_REGEOCODE_SYNC_TEXT", False) and (display_address or "").strip():
            upsert["delivery_address"] = display_address.strip()
            if (city_name or "").strip():
                upsert["delivery_city"] = city_name.strip()
            sync_text = True
    except Exception:  # noqa: BLE001 — odczyt flagi fail → tylko coords (bezpieczny fallback)
        sync_text = False
    try:
        state_machine.upsert_order(str(order_id), upsert, event="EDIT_REGEOCODE")
    except Exception as e:  # noqa: BLE001
        print(f"REGEOCODE_SKIP: błąd zapisu delivery_coords (upsert_order): {e}")
        return None
    _txt = (f" + tekst „{upsert['delivery_address']}”/{upsert.get('delivery_city')}"
            if sync_text else "")
    print(f"REGEOCODE_OK: {full_address!r}, {city_name!r} -> {coords}{_txt} "
          f"(delivery_coords{'+address+city' if sync_text else ''} zaktualizowane)")
    return coords


def _s(v):
    return "" if v is None else str(v)


# nazwa-argumentu -> pole-formularza (proste 1:1 nadpisania)
_OVERRIDE = {
    "street": "modal_street", "nr_domu": "modal_nr_domu", "nr_mieszkania": "modal_nr_mieszkania",
    "phone": "modal_phone", "price": "modal_price", "delivery_price": "modal_delivery_price",
    "uwagi": "modal_uwagi", "status": "modal_status_zamowienia", "kurier": "modal_kurier_select",
    "platnosc": "modal_platnosc", "restauracja": "modal_restauracja",
}


def build_payload(z, csrf, changes, city_map):
    """Pełny payload update-zamowienie: WSZYSTKIE pola z aktualnego zlecenia, nadpisane tylko
    polami z `changes` (None = nie zmieniaj). Wierne odtworzenie zachowania modala gastro."""
    price = z.get("price")
    if price:
        platnosc = "karta" if z.get("platnosc") else "gotowka"
    else:
        platnosc = "brak"
    payload = {
        "_token": csrf,
        "modal_id_zlecenie": _s(z.get("id")),
        "modal_restauracja": _s(z.get("id_address")),
        "modal_street": _s(z.get("street")),
        "modal_nr_domu": _s(z.get("nr_domu")),
        "modal_nr_mieszkania": _s(z.get("nr_mieszkania")),
        "modal_city": _s(z.get("id_location_to")),
        "modal_phone": _s(z.get("phone")),
        "modal_price": _s(z.get("price")),
        "modal_delivery_price": _s(z.get("delivery_price")),
        "modal_platnosc": platnosc,
        "modal_uwagi": _s(z.get("uwagi")),
        "modal_status_zamowienia": _s(z.get("id_status_zamowienia")),
        "modal_kurier_select": _s(z.get("id_kurier")),
    }
    for arg, field in _OVERRIDE.items():
        if changes.get(arg) is not None:
            payload[field] = str(changes[arg])
    # miasto: akceptuj id (cyfry) lub nazwę (rozwiąż z opcji gastro; brak → twardy błąd, nie zgadujemy)
    if changes.get("city") is not None:
        cv = str(changes["city"]).strip()
        if cv.isdigit():
            payload["modal_city"] = cv
        else:
            key = cv.lower()
            if key not in city_map:
                print(f"EDIT_FAIL: nieznane miasto '{cv}' — brak w opcjach gastro (modal_city)")
                sys.exit(2)
            payload["modal_city"] = city_map[key]
    return payload


def post_update(payload):
    req = urllib.request.Request(
        f'{BASE}/admin2017/new/orders/update-zamowienie',
        urllib.parse.urlencode(payload).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest',
                 'Referer': f'{BASE}/admin2017/new/orders/zlecenia'})
    resp = urllib.request.urlopen(req, timeout=15)
    return resp.getcode(), resp.read().decode('utf-8', 'ignore')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--id', required=True, help='ID zlecenia')
    ap.add_argument('--street'); ap.add_argument('--nr-domu', dest='nr_domu')
    ap.add_argument('--nr-mieszkania', dest='nr_mieszkania')
    ap.add_argument('--city', help='Miasto: nazwa (np. Białystok) lub id lokalizacji')
    ap.add_argument('--phone'); ap.add_argument('--price'); ap.add_argument('--delivery-price', dest='delivery_price')
    ap.add_argument('--platnosc', choices=['brak', 'karta', 'gotowka'])
    ap.add_argument('--uwagi'); ap.add_argument('--status'); ap.add_argument('--kurier')
    ap.add_argument('--restauracja')
    ap.add_argument('--commit', action='store_true', help='REALNY zapis do gastro (bez = dry-run)')
    ap.add_argument('--no-regeocode', action='store_true',
                    help='nie przeliczaj koordów po zmianie adresu/miasta (domyślnie przelicza)')
    args = ap.parse_args()

    changes = {k: getattr(args, k) for k in (
        'street', 'nr_domu', 'nr_mieszkania', 'city', 'phone', 'price', 'delivery_price',
        'platnosc', 'uwagi', 'status', 'kurier', 'restauracja')}
    changed_keys = [k for k, v in changes.items() if v is not None]
    if not changed_keys:
        print("EDIT_FAIL: brak pól do zmiany (podaj co najmniej jedno --pole)")
        sys.exit(2)

    try:
        csrf, html = login()
    except Exception as e:
        print(f"EDIT_FAIL: błąd logowania: {e}")
        sys.exit(1)
    z = read_order(args.id, csrf)
    if not z or not z.get('id'):
        print(f"EDIT_FAIL: nie wczytano zlecenia {args.id}")
        sys.exit(1)

    city_map = city_options(html)
    payload = build_payload(z, csrf, changes, city_map)

    # diff dla bezpieczeństwa: co realnie się zmienia vs aktualne
    base = build_payload(z, csrf, {}, city_map)
    diff = {f: (base.get(f), payload.get(f)) for f in payload if f != '_token' and base.get(f) != payload.get(f)}
    print(f"[edit] zlecenie {args.id}: zmieniam pola={changed_keys}")
    for f, (old, new) in diff.items():
        print(f"   {f}: {old!r} -> {new!r}")
    if not diff:
        print("[edit] UWAGA: payload identyczny z aktualnym (no-op).")

    safe_payload = {k: v for k, v in payload.items() if k != '_token'}
    if not args.commit:
        print("EDIT_DRYRUN: " + json.dumps(safe_payload, ensure_ascii=False))
        sys.exit(0)

    try:
        code, body = post_update(payload)
    except Exception as e:
        print(f"EDIT_FAIL: błąd POST update-zamowienie: {e}")
        sys.exit(1)
    low = body.lower()
    if not (code == 200 and 'error' not in low and 'exception' not in low):
        print(f"EDIT_FAIL: nieoczekiwana odpowiedź http={code} resp={body[:300]}")
        sys.exit(1)
    print(f"EDIT_OK: zlecenie {args.id} zaktualizowane ({', '.join(changed_keys)}); http={code} resp={body[:160]}")

    # Po realnej zmianie adresu/miasta — przelicz koordy w orders_state Ziomka (żeby naprawić
    # ŻYWE zlecenie, nie tylko źródło). Tylko gdy adres/miasto faktycznie się zmienił.
    addr_touched = any(k in changed_keys for k in ('street', 'delivery_address', 'nr_domu', 'nr_mieszkania', 'city'))
    if addr_touched and not args.no_regeocode:
        new_street = payload['modal_street']
        city_id = payload['modal_city']
        city_name = city_names_by_id(html).get(city_id, '')
        nr_domu = payload.get('modal_nr_domu', '')
        nr_miesz = payload.get('modal_nr_mieszkania', '')
        full_address = ' '.join(p for p in (new_street, nr_domu) if p).strip() or new_street
        display_address = _build_display_address(new_street, nr_domu, nr_miesz)
        regeocode_and_update(args.id, full_address, city_name, display_address=display_address)


if __name__ == '__main__':
    main()
