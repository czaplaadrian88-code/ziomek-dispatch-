#!/usr/bin/env python3
"""gastro_assign.py — przypisuje zlecenie do kuriera w panelu NadajeSz
Użycie:
  python3 gastro_assign.py --id 464827 --kurier "Bartek O." --time "23:02"
  python3 gastro_assign.py --id 464827 --koordynator

⚠ STAGING (deploy_staging/scripts/gastro_assign.py) — AUDYT 2.0 Blocker-1
  (lane auton-blockers, 2026-07-02). Różni się od ŻYWEGO
  /root/.openclaw/workspace/scripts/gastro_assign.py WYŁĄCZNIE fixem
  „fałszywy sukces na exit-code":
    1. `_classify_assign_response` — PUSTE ciało i JSON-ok = SUKCES (kontrakt
       panelu: auto_koord 1057 parkowań + telegram realne przypisania od
       miesięcy), ale strona logowania / HTML / jawny błąd = PORAŻKA (dziś
       przechodziły, bo brak słowa "error").
    2. Gałąź „nie potwierdzono" robi `sys.exit(1)` na stderr (dziś kończyła
       exit 0 → fałszywy sukces widziany przez executor/auto_koord/telegram).
    3. Opcjonalny `--verify`: read-back `edit-zamowienie` potwierdza id_kurier
       (tor autonomii). auto_koord/telegram GO NIE przekazują → ich zachowanie
       niezmienione (empty-body = sukces zostaje).
  Deploy = cp staged→live (subprocess per call, ZERO restartu). Po deployu żywy
  MUSI być bajt-identyczny ze staged (test parytetu mirrora).
"""
import os, sys, json, re, argparse, urllib.request, urllib.parse, http.cookiejar
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
_WARSAW = ZoneInfo("Europe/Warsaw")  # DST-safe CET/CEST — L2 audyt 2.0 bomba #1 (był fixed +2: zimą HH:MM<1h liczony teraz+1h → guard "+1 dzień" → ~1410 min do panelu zamiast ~20)

BASE      = 'https://www.gastro.nadajesz.pl'

# Sentinel sukcesu w stdout — executor/consumers wymagają go (nie samego exit 0).
ASSIGN_OK_SENTINEL = "ASSIGN_OK:"


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


def _fetch_order(order_id, csrf):
    """Read-back / keep-time: pobiera zlecenie z edit-zamowienie (dict 'zlecenie' | None)."""
    req = urllib.request.Request(
        f'{BASE}/admin2017/new/orders/edit-zamowienie',
        urllib.parse.urlencode({'_token': csrf, 'id_zlecenie': order_id}).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest'}
    )
    body = urllib.request.urlopen(req, timeout=8).read().decode()
    return json.loads(body).get('zlecenie') or None


def _classify_assign_response(result):
    """Klasyfikuje odpowiedź /przypisz-zamowienie → (confirmed_ok: bool, detail: str).

    Kontrakt panelu (zweryfikowany na żywym auto_koord_log: 1057 parkowań +
    telegram realne przypisania od miesięcy): SUKCES = PUSTE ciało albo JSON
    success/status:ok. PORAŻKA = jawny błąd JSON, odbicie na stronę logowania /
    HTML (wygasła sesja), słowo error/exception. Kierunek fail-closed jest
    bezpieczny: fałszywe „nieudane" (człowiek widzi i powtarza) >> fałszywe
    „udane" (cichy drop bez człowieka). Zachowawczo: niepuste, nie-błędne,
    nie-HTML ciało zostaje SUKCESEM (jak dziś, bez regresji nieznanych kształtów
    sukcesu) — definitywną pewność w torze autonomii daje read-back (--verify)."""
    if not isinstance(result, dict):
        return False, f"non_dict:{type(result).__name__}"
    # jawny sygnał błędu w JSON
    if result.get("success") is False:
        return False, "json_success_false"
    _st = str(result.get("status", "")).strip().lower()
    if _st in ("error", "fail", "failed"):
        return False, f"json_status_{_st}"
    raw = result.get("raw")
    if raw is not None:
        raw_s = (raw if isinstance(raw, str) else str(raw)).strip()
        if raw_s == "":
            return True, "empty_body_ok"          # kontrakt panelu: pusto = sukces
        low = raw_s.lower()
        _bounce = ('<html', '<!doctype', 'name="_token"', 'sesja wygas', 'zaloguj',
                   'logowanie', '/admin2017/login', 'unauthorized', 'nie jesteś zalogowany')
        if any(s in low for s in _bounce):
            return False, f"session_bounce:{raw_s[:100]}"
        if 'error' in low or 'exception' in low:
            return False, f"raw_error:{raw_s[:100]}"
        return True, f"raw_nonempty_ok:{raw_s[:60]}"
    # JSON dict bez 'raw' i bez sygnału błędu → sukces (success:true / status:ok / ok-kształt)
    return True, "json_ok"


def verify_assignment(order_id, expected_kid, csrf, fetch_fn=None):
    """Read-back potwierdzenie: id_kurier zlecenia == expected_kid → (ok, detail).

    fetch_fn(order_id, csrf) -> dict|None wstrzykiwalny (testy bez HTTP)."""
    try:
        z = (fetch_fn or _fetch_order)(order_id, csrf)
    except Exception as e:
        return False, f"verify_fetch_exc:{type(e).__name__}"
    if not isinstance(z, dict):
        return False, "verify_no_order"
    actual = z.get("id_kurier")
    try:
        if int(actual) == int(expected_kid):
            return True, f"verify_ok_kid={actual}"
    except (TypeError, ValueError):
        pass
    return False, f"verify_mismatch expected={expected_kid} actual={actual}"


def _resolve_success(result, *, verify=False, verify_fn=None):
    """Klasyfikacja odpowiedzi + opcjonalny read-back → (ok: bool, detail: str)."""
    ok, detail = _classify_assign_response(result)
    if ok and verify:
        vok, vdetail = (verify_fn or (lambda: (True, "verify_skipped")))()
        detail = f"{detail}|{vdetail}"
        if not vok:
            ok = False
    return ok, detail

def main(argv=None, *, login_fn=None, assign_fn=None, get_kid_fn=None, fetch_fn=None):
    """Zwraca kod wyjścia (0 = przypisanie POTWIERDZONE, !=0 = niepotwierdzone).
    Seams (login_fn/assign_fn/get_kid_fn/fetch_fn) wstrzykiwalne dla testów bez HTTP."""
    login_fn = login_fn or login
    assign_fn = assign_fn or assign
    get_kid_fn = get_kid_fn or get_kurier_id
    fetch_fn = fetch_fn or _fetch_order

    parser = argparse.ArgumentParser()
    parser.add_argument('--id',          required=True,  help='ID zlecenia')
    parser.add_argument('--kurier',      default=None,   help='Nazwa kuriera')
    parser.add_argument('--time',        default=None,   help='Czas odbioru: HH:MM lub liczba minut od teraz (int)')
    parser.add_argument('--koordynator', action='store_true', help='Przypisz do Koordynatora')
    parser.add_argument('--keep-time',   action='store_true', help='Nie zmieniaj czasu odbioru (zostaw oryginalny z panelu)')
    parser.add_argument('--verify',      action='store_true', help='Read-back: potwierdź przypisanie odczytem zwrotnym (tor autonomii)')
    args = parser.parse_args(argv)

    def _err(msg):
        # Wszystkie porażki → stderr + exit!=0 (OnFailure/executor MUSI widzieć porażkę).
        print(msg, file=sys.stderr)
        return 1

    order_id = args.id

    # Ustal kurier_id
    if args.koordynator:
        kurier_id = 26
        kurier_name = 'Koordynator'
    elif args.kurier:
        kurier_id = get_kid_fn(args.kurier)
        kurier_name = args.kurier
        if not kurier_id:
            return _err(f"ASSIGN_ERROR: nie znaleziono kuriera '{args.kurier}'")
    else:
        return _err("ASSIGN_ERROR: podaj --kurier lub --koordynator")

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
                    target += timedelta(days=1)
                time_minutes = max(1, round((target - now_waw).total_seconds() / 60))
            except Exception as e:
                return _err(f"ASSIGN_ERROR: błąd przeliczania czasu HH:MM '{args.time}': {e}")
        else:
            try:
                time_minutes = int(args.time)
                if time_minutes < 0:
                    raise ValueError("ujemne minuty")
            except Exception as e:
                return _err(f"ASSIGN_ERROR: --time musi być HH:MM lub liczbą minut: '{args.time}' ({e})")
    else:
        return _err("ASSIGN_ERROR: brak --time (HH:MM lub liczba minut)")

    print(f"[assign] Loguję do panelu...")
    try:
        csrf = login_fn()
    except Exception as e:
        return _err(f"ASSIGN_ERROR: błąd logowania: {e}")

    # Pobierz oryginalny czas po zalogowaniu (csrf już dostępny)
    if keep_time:
        try:
            z = fetch_fn(order_id, csrf) or {}
            original_time = z.get('czas_odbioru', 0)
            time_minutes = int(original_time) if original_time else 0
            time_str = f'oryginalny ({time_minutes}min)'
            print(f"[assign] Oryginalny czas z panelu: {time_minutes} min")
        except Exception as e:
            print(f"[assign] Błąd pobierania czasu: {e} — używam 0")
            time_minutes = 0

    print(f"[assign] Przypisuję id={order_id} → {kurier_name} (ID={kurier_id}), czas={time_str} ({time_minutes}min)")
    try:
        result = assign_fn(order_id, kurier_id, time_minutes, csrf)
    except Exception as e:
        return _err(f"ASSIGN_ERROR: {e}")
    print(f"[assign] Odpowiedź panelu: {result}")

    # Sukces (exit 0) TYLKO gdy przypisanie POTWIERDZONE (klasyfikacja odpowiedzi
    # + opcjonalny read-back). Każda inna ścieżka = exit 1 na stderr.
    ok, detail = _resolve_success(
        result, verify=args.verify,
        verify_fn=(lambda: verify_assignment(order_id, kurier_id, csrf, fetch_fn=fetch_fn)),
    )
    if ok:
        print(f"{ASSIGN_OK_SENTINEL} {kurier_name} → zlecenie {order_id}, odbiór {time_str} [{detail}]")
        return 0
    return _err(f"ASSIGN_ERROR: przypisanie NIE potwierdzone ({detail}) — odpowiedź panelu: {result}")

if __name__ == '__main__':
    sys.exit(main())
