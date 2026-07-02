#!/usr/bin/env python3
"""
gastro_assign.py — przypisuje zlecenie do kuriera w panelu NadajeSz
Użycie:
  python3 gastro_assign.py --id 464827 --kurier "Bartek O." --time "23:02"
  python3 gastro_assign.py --id 464827 --koordynator
"""
import os, sys, json, re, argparse, urllib.request, urllib.parse, http.cookiejar
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
_WARSAW = ZoneInfo("Europe/Warsaw")  # DST-safe CET/CEST — L2 audyt 2.0 bomba #1 (był fixed +2: zimą HH:MM<1h liczony teraz+1h → guard "+1 dzień" → ~1410 min do panelu zamiast ~20)

BASE      = 'https://www.gastro.nadajesz.pl'


def _first_existing(*paths):
    """Pierwsza istniejąca ścieżka (env override → kandydaci). Odporne na różny HOME
    (root vs node) — ostatni kandydat jako domyślny, gdy żaden nie istnieje."""
    for p in paths:
        if p and Path(p).exists():
            return Path(p)
    return Path(paths[-1])


PANEL_ENV = _first_existing(
    os.environ.get('PANEL_ENV_FILE'),
    '/root/.openclaw/workspace/.secrets/panel.env',
    '/home/node/.openclaw/workspace/.secrets/panel.env',
)
KURIER_IDS_FILE = _first_existing(
    os.environ.get('KURIER_IDS_FILE'),
    '/root/.openclaw/workspace/dispatch_state/kurier_ids.json',
    '/home/node/.openclaw/workspace/dispatch_state/kurier_ids.json',
)

def login():
    env = dict(l.strip().split('=',1) for l in PANEL_ENV.read_text().splitlines() if '=' in l)
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [('User-Agent','Mozilla/5.0')]
    urllib.request.install_opener(opener)
    r1 = urllib.request.urlopen(f'{BASE}/admin2017/login')
    t = re.search(r'name="_token" value="([^"]+)"', r1.read().decode()).group(1)
    urllib.request.urlopen(urllib.request.Request(f'{BASE}/admin2017/login',
        urllib.parse.urlencode({'email':env['PANEL_LOGIN'],'password':env['PANEL_PASSWORD'],'_token':t}).encode(),
        headers={'Referer':f'{BASE}/admin2017/login','Origin':BASE,'Content-Type':'application/x-www-form-urlencoded'}))
    res = urllib.request.urlopen(f'{BASE}/admin2017/new/orders/zlecenia')
    html = res.read().decode('utf-8')
    csrf = re.search(r"var TOKEN = '([^']+)'", html).group(1)
    return csrf

def get_kurier_id(kurier_name):
    """Znajdź ID kuriera po nazwie (dokładne lub częściowe dopasowanie)."""
    try:
        ids = json.loads(KURIER_IDS_FILE.read_text(encoding='utf-8'))
    except:
        print(f"BŁĄD: Nie można odczytać {KURIER_IDS_FILE}", file=sys.stderr)
        return None

    # Dokładne dopasowanie
    if kurier_name in ids:
        return ids[kurier_name]

    # Częściowe — pierwsze słowo + pierwsza litera nazwiska
    parts = kurier_name.strip().split()
    first = parts[0].lower()
    initial = parts[1][0].upper() if len(parts) > 1 else None

    candidates = []
    for name, kid in ids.items():
        nparts = name.split()
        if nparts[0].lower() == first:
            if initial and len(nparts) > 1 and nparts[1][0].upper() == initial:
                candidates.append((name, kid))
            elif not initial:
                candidates.append((name, kid))

    if len(candidates) == 1:
        print(f"[assign] Dopasowano: '{kurier_name}' → '{candidates[0][0]}' (ID={candidates[0][1]})")
        return candidates[0][1]
    elif len(candidates) > 1:
        print(f"[assign] Niejednoznaczne: '{kurier_name}' → {[c[0] for c in candidates]}, używam pierwszego")
        return candidates[0][1]

    print(f"BŁĄD: Nie znaleziono kuriera '{kurier_name}'", file=sys.stderr)
    return None

def assign(order_id, kurier_id, time_minutes, csrf):
    """
    Wywołuje /przypisz-zamowienie.
    time_minutes: liczba minut od teraz (int lub string), lub '0' = zostaw oryginalny czas
    """
    time_full = str(time_minutes)

    payload = urllib.parse.urlencode({
        '_token':       csrf,
        'id_kurier':    kurier_id,
        'id_zamowienia': order_id,
        'time':         time_full,
    }).encode()

    req = urllib.request.Request(
        f'{BASE}/admin2017/new/orders/przypisz-zamowienie',
        payload,
        headers={
            'Content-Type':     'application/x-www-form-urlencoded',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer':          f'{BASE}/admin2017/new/orders/zlecenia',
        }
    )
    resp = urllib.request.urlopen(req, timeout=10)
    body = resp.read().decode('utf-8')
    try:
        data = json.loads(body)
        return data
    except:
        return {'raw': body}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--id',          required=True,  help='ID zlecenia')
    parser.add_argument('--kurier',      default=None,   help='Nazwa kuriera')
    parser.add_argument('--time',        default=None,   help='Czas odbioru: HH:MM lub liczba minut od teraz (int)')
    parser.add_argument('--koordynator', action='store_true', help='Przypisz do Koordynatora')
    parser.add_argument('--keep-time',   action='store_true', help='Nie zmieniaj czasu odbioru (zostaw oryginalny z panelu)')
    args = parser.parse_args()

    order_id = args.id

    # Ustal kurier_id
    if args.koordynator:
        kurier_id = 26
        kurier_name = 'Koordynator'
    elif args.kurier:
        kurier_id = get_kurier_id(args.kurier)
        kurier_name = args.kurier
        if not kurier_id:
            print(f"ASSIGN_ERROR: nie znaleziono kuriera '{args.kurier}'")
            sys.exit(1)
    else:
        print("ASSIGN_ERROR: podaj --kurier lub --koordynator")
        sys.exit(1)

    # Czas — oblicz minuty od teraz do suggested_time
    keep_time = args.keep_time or (args.koordynator and not args.time)
    if keep_time:
        time_minutes = None  # ustalimy po zalogowaniu
        time_str = 'oryginalny'
    elif args.time:
        time_str = args.time
        # Auto-detect: ":" → HH:MM (legacy CLI), inaczej → liczba minut (telegram_approver F1.7).
        if ':' in args.time:
            now_waw = datetime.now(_WARSAW).replace(tzinfo=None)
            try:
                h, m = args.time.split(':')
                target = now_waw.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
                if target < now_waw:
                    from datetime import timedelta
                    target += timedelta(days=1)
                time_minutes = max(1, round((target - now_waw).total_seconds() / 60))
            except Exception as e:
                print(f"ASSIGN_ERROR: błąd przeliczania czasu HH:MM '{args.time}': {e}")
                sys.exit(1)
        else:
            try:
                time_minutes = int(args.time)
                if time_minutes < 0:
                    raise ValueError("ujemne minuty")
            except Exception as e:
                print(f"ASSIGN_ERROR: --time musi być HH:MM lub liczbą minut: '{args.time}' ({e})")
                sys.exit(1)
    else:
        print("ASSIGN_ERROR: brak --time (HH:MM lub liczba minut)")
        sys.exit(1)

    print(f"[assign] Loguję do panelu...")
    try:
        csrf = login()
    except Exception as e:
        print(f"ASSIGN_ERROR: błąd logowania: {e}")
        sys.exit(1)

    # Pobierz oryginalny czas po zalogowaniu (csrf już dostępny)
    if keep_time:
        try:
            req = urllib.request.Request(
                f'{BASE}/admin2017/new/orders/edit-zamowienie',
                urllib.parse.urlencode({'_token': csrf, 'id_zlecenie': order_id}).encode(),
                headers={'Content-Type':'application/x-www-form-urlencoded','X-Requested-With':'XMLHttpRequest'}
            )
            z = json.loads(urllib.request.urlopen(req, timeout=5).read().decode()).get('zlecenie', {})
            original_time = z.get('czas_odbioru', 0)
            time_minutes = int(original_time) if original_time else 0
            time_str = f'oryginalny ({time_minutes}min)'
            print(f"[assign] Oryginalny czas z panelu: {time_minutes} min")
        except Exception as e:
            print(f"[assign] Błąd pobierania czasu: {e} — używam 0")
            time_minutes = 0

    print(f"[assign] Przypisuję id={order_id} → {kurier_name} (ID={kurier_id}), czas={time_str} ({time_minutes}min)")
    try:
        result = assign(order_id, kurier_id, time_minutes, csrf)
        print(f"[assign] Odpowiedź panelu: {result}")

        # Sprawdź czy sukces
        if isinstance(result, dict) and (result.get('success') or result.get('status') == 'ok' or 'error' not in str(result).lower()):
            print(f"ASSIGN_OK: {kurier_name} → zlecenie {order_id}, odbiór {time_str}")
        else:
            print(f"ASSIGN_ERROR: nieoczekiwana odpowiedź: {result}")
    except Exception as e:
        print(f"ASSIGN_ERROR: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
