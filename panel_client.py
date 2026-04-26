"""Panel Client - biblioteka dostepu do gastro.nadajesz.pl (dispatch v2).

ZWERYFIKOWANE EMPIRYCZNIE:
- czas_odbioru_timestamp: Warsaw time (NIE UTC), faktyczny pickup, aktualizowany
  przez koordynatora bezposrednio w bazie.
- created_at: UTC (suffix Z).
- czas_odbioru (int): ile minut restauracja potrzebuje na przygotowanie.
- czas_odbioru < 60 -> elastyczne zlecenie (zwykly dispatch).
- czas_odbioru >= 60 -> czasowka (default do koordynatora id_kurier=26).
- Serwer Hetzner jest w UTC -> uzywamy zoneinfo dla Warsaw, NIE sztywnego offsetu.
- Statusy ignorowane przez watcher: 7 (doreczone), 8 (nieodebrano), 9 (anulowane).

API:
    login(force=False) -> (opener, csrf, html_or_None)
    fetch_panel_html() -> str
    parse_panel_html(html) -> dict z order_ids, assigned, packs, loads, rest_names, html_times
    fetch_order_details(zid, csrf=None) -> dict z surowymi polami
    normalize_order(raw, rest_hint) -> czysty dict dispatch-v2
    health_check() -> dict
"""
import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, time as _time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dispatch_v2.common import setup_logger

BASE_URL = "https://www.gastro.nadajesz.pl"
PANEL_ENV = Path("/root/.openclaw/workspace/.secrets/panel.env")
WARSAW_TZ = ZoneInfo("Europe/Warsaw")

STATUS_MAP = {
    2: "nowe",
    3: "dojazd",
    4: "oczekiwanie",
    5: "odebrane",
    6: "opoznienie",
    7: "doreczone",
    8: "nieodebrano",
    9: "anulowane",
}
IGNORED_STATUSES = {7, 8, 9}
KOORDYNATOR_ID = 26
CZASOWKA_THRESHOLD_MIN = 60

_log = setup_logger("panel_client", "/root/.openclaw/workspace/scripts/logs/dispatch.log")

_session_lock = threading.Lock()
_session = {
    "opener": None,
    "csrf": None,
    "last_login_at": 0.0,
    "last_ok": False,
}


def _creds() -> dict:
    if not PANEL_ENV.exists():
        raise FileNotFoundError(f"Brak {PANEL_ENV}")
    return dict(
        l.strip().split("=", 1)
        for l in PANEL_ENV.read_text().splitlines()
        if "=" in l
    )


def _fresh_opener():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    return opener


def login(force: bool = False) -> tuple:
    """Loguje. Zwraca (opener, csrf, html). Cache 20 min."""
    with _session_lock:
        now = time.time()
        age = now - _session["last_login_at"]
        if not force and _session["last_ok"] and _session["opener"] and age < 1200:
            _log.debug("login: cached")
            return _session["opener"], _session["csrf"], None

        _log.info(f"login: fresh (age={age:.0f}s, force={force})")
        env = _creds()
        opener = _fresh_opener()

        try:
            r1 = opener.open(f"{BASE_URL}/admin2017/login", timeout=15)
            body = r1.read().decode("utf-8", errors="replace")
            m = re.search(r'name="_token" value="([^"]+)"', body)
            if not m:
                raise RuntimeError("Brak _token na stronie loginu")
            token = m.group(1)
        except Exception as e:
            _session["last_ok"] = False
            _log.error(f"login GET: {e}")
            raise

        try:
            req = urllib.request.Request(
                f"{BASE_URL}/admin2017/login",
                urllib.parse.urlencode({
                    "email": env["PANEL_LOGIN"],
                    "password": env["PANEL_PASSWORD"],
                    "_token": token,
                }).encode(),
                headers={
                    "Referer": f"{BASE_URL}/admin2017/login",
                    "Origin": BASE_URL,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            opener.open(req, timeout=15)
        except Exception as e:
            _session["last_ok"] = False
            _log.error(f"login POST: {e}")
            raise

        try:
            res = opener.open(f"{BASE_URL}/admin2017/new/orders/zlecenia", timeout=15)
            if "admin2017/login" in res.url:
                _session["last_ok"] = False
                raise RuntimeError("Zle credentials")
            html = res.read().decode("utf-8", errors="replace")
        except Exception as e:
            _session["last_ok"] = False
            _log.error(f"login verify: {e}")
            raise

        m_csrf = re.search(r"var TOKEN = '([^']+)'", html)
        csrf = m_csrf.group(1) if m_csrf else token

        _session["opener"] = opener
        _session["csrf"] = csrf
        _session["last_login_at"] = now
        _session["last_ok"] = True
        _log.info(f"login OK csrf={csrf[:10]}...")
        return opener, csrf, html


def fetch_panel_html() -> str:
    """Fetch panelu z re-login fallback."""
    for attempt in range(2):
        try:
            opener, csrf, html = login(force=(attempt > 0))
            if html is not None:
                return html
            res = opener.open(f"{BASE_URL}/admin2017/new/orders/zlecenia", timeout=15)
            if "admin2017/login" in res.url:
                _log.warning("fetch_panel: redirect na login, retry")
                continue
            return res.read().decode("utf-8", errors="replace")
        except Exception as e:
            _log.warning(f"fetch_panel attempt {attempt+1}: {e}")
            if attempt == 1:
                raise
    raise RuntimeError("fetch_panel wyczerpane proby")


def parse_panel_html(html: str) -> dict:
    """Parse struktury panelu z HTML.
    Zwraca dict z:
        order_ids, assigned_ids, unassigned_ids,
        rest_names, courier_packs, courier_load,
        html_times (dict zid -> [created_warsaw, pickup_warsaw])
    """
    html_clean = re.sub(r"<svg[^>]*>.*?</svg>", "", html, flags=re.DOTALL)

    order_ids = list(dict.fromkeys(re.findall(r"id:\s*(46\d{4})", html_clean)))
    assigned_ids = set(re.findall(r'id="zlec_(\d+)"', html_clean))
    unassigned_ids = [z for z in order_ids if z not in assigned_ids]

    rest_names = {}
    for zid, rname in re.findall(
        r'id="zamowienie_(\d+)".*?box_zam_name[^>]*>\s*([^<]+)',
        html_clean, re.DOTALL,
    ):
        rest_names[zid] = rname.strip()

    # Czasy widoczne w HTML listy (tak jak koordynator widzi)
    # Format: obok kazdego ID sa dwa czasy H:MM - created i pickup (Warsaw time)
    html_times = {}
    blocks = re.findall(
        r'id="zamowienie_(\d+)">(.*?)(?=id="zamowienie_|kurier_col|\Z)',
        html_clean, re.DOTALL,
    )
    for zid, content in blocks:
        times = re.findall(r"\b(\d{1,2}:\d{2})\b", content)
        if times:
            html_times[zid] = times[:2]  # pierwsze dwa to created, pickup

    courier_packs = {}
    courier_load = {}
    kurier_cols = re.findall(
        r'<div[^>]+class="[^"]*widok_kurier[^"]*"[^>]*>(.*?)(?=<div[^>]+class="[^"]*widok_kurier|\Z)',
        html_clean, re.DOTALL,
    )
    for col in kurier_cols:
        m = re.search(r"name_kurier[^>]*>([^<]+)", col)
        if not m:
            continue
        kname = m.group(1).strip()
        courier_packs[kname] = re.findall(r'id="zlec_(\d+)"', col)
        m_load = re.search(r"<div>(\d/\d)</div>", col)
        if m_load:
            courier_load[kname] = m_load.group(1)

    # Status sygnal z HTML: order bez 'data-idkurier' w bloku = terminalny (status 7/8/9)
    # Sprawdzone na 80 probce: status 2/3/5 zawsze maja data-idkurier, status 7 nigdy nie ma.
    closed_ids = set()
    pickup_addresses = {}
    delivery_addresses = {}
    for m in re.finditer(r'id="zamowienie_(\d+)"(.*?)(?=id="zamowienie_|\Z)', html, re.DOTALL):
        zid = m.group(1)
        block = m.group(2)
        if "data-idkurier" not in block:
            closed_ids.add(zid)
        mp = re.search(r'data-address_from="([^"]*)"', block)
        if mp:
            pickup_addresses[zid] = mp.group(1).strip()
        md = re.search(r'data-address_to="([^"]*)"', block)
        if md:
            delivery_addresses[zid] = md.group(1).strip()

    return {
        "order_ids": order_ids,
        "assigned_ids": assigned_ids,
        "unassigned_ids": unassigned_ids,
        "rest_names": rest_names,
        "courier_packs": courier_packs,
        "courier_load": courier_load,
        "html_times": html_times,
        "closed_ids": closed_ids,
        "pickup_addresses": pickup_addresses,
        "delivery_addresses": delivery_addresses,
    }


def _open_with_relogin(req: urllib.request.Request, timeout: float = 10):
    """urllib opener z automatycznym re-login przy HTTP 401/419 (P0.5b Fix #4).

    Max 1 retry. NIE uzywane w login() samym (uniknięcie rekursji) — tylko
    w wrapperach zewnętrznych (fetch_order_details). fetch_panel_html ma
    własny redirect-based re-login przez force=True w for attempt loop.
    """
    for attempt in range(2):
        opener = _session["opener"]
        if opener is None:
            login()
            opener = _session["opener"]
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 419) and attempt == 0:
                _log.warning(f"panel HTTP {e.code} → re-login + retry")
                login(force=True)
                continue
            raise
    raise RuntimeError("_open_with_relogin: unreachable")


def fetch_order_details(zid: str, csrf: Optional[str] = None, timeout: int = 10) -> Optional[dict]:
    """POST edit-zamowienie. Zwraca surowy dict 'zlecenie' lub None.

    V3.27.1 sesja 2 (2026-04-26): dodano `timeout` parametr (default 10 backward compat).
    Pre-proposal recheck używa timeout=2 żeby nie blokować dispatch pipeline (Blocker 3
    Opcja A). Caller może override per-use-case — np. 5s dla quick health checks.
    """
    if csrf is None:
        _, csrf, _ = login()
    opener = _session["opener"]
    if opener is None:
        opener, csrf, _ = login()

    try:
        req = urllib.request.Request(
            f"{BASE_URL}/admin2017/new/orders/edit-zamowienie",
            urllib.parse.urlencode({"_token": csrf, "id_zlecenie": zid}).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/admin2017/new/orders/zlecenia",
            },
        )
        raw = _open_with_relogin(req, timeout=timeout).read().decode("utf-8", errors="replace")
        _parsed = json.loads(raw)
        _zlecenie = _parsed.get("zlecenie")
        if isinstance(_zlecenie, dict):
            # V3.19f FIX Finding #1 (2026-04-20): poprzednia wersja zwracała
            # wyłącznie raw.get("zlecenie"), wywalając top-level klucze. Panel
            # trzyma czas_kuriera (HH:MM declared courier arrival) na poziomie
            # response sibling do "zlecenie". Invisible data loss od V1 pipeline.
            # Fix: merge wszystkich top-level kluczy (oprócz "zlecenie") do
            # returned dict, żeby downstream normalize_order miał dostęp.
            # TECH_DEBT rule (2026-04-20): parse wrapper layer loguje unhandled
            # top-level keys — invisible data loss kosztowniejszy niż verbose log.
            _known_top = {"zlecenie"}
            _handled = {"czas_kuriera"}  # explicitly propagate
            for _k, _v in _parsed.items():
                if _k in _known_top:
                    continue
                if _k in _handled:
                    _zlecenie[_k] = _v
                else:
                    _log.debug(
                        f"fetch_order_details({zid}): unhandled top-level key "
                        f"'{_k}' (type={type(_v).__name__})"
                    )
        return _zlecenie
    except urllib.error.HTTPError as he:
        _log.warning(f"fetch_order_details({zid}): HTTP {he.code}")
        return None
    except Exception as e:
        _log.warning(f"fetch_order_details({zid}): {type(e).__name__}: {e}")
        return None


def _parse_warsaw_naive(s: Optional[str]) -> Optional[datetime]:
    """Parse '2026-04-11 01:09:09' jako aware Warsaw datetime."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW_TZ)
    except Exception:
        return None


def _czas_kuriera_to_datetime(
    hhmm: Optional[str],
    pickup_at_warsaw: Optional[datetime],
    now_warsaw: Optional[datetime] = None,
) -> Optional[datetime]:
    """V3.19f: panel HH:MM → Warsaw-aware datetime.

    Panel zwraca czas_kuriera jako string HH:MM (np. "17:10"). Aby
    zamienić na pełen datetime, bierze date-component z pickup_at_warsaw
    (primary anchor), fallback today Warsaw.

    6h wraparound guard:
      - candidate.delta_hours(anchor) < -6 → wraparound forward (+1 dzień)
        np. HH:MM="00:15" + pickup=23:45 poprzedniego dnia → następny dzień
      - candidate.delta_hours(anchor) > +6 → wraparound backward (-1 dzień)
        np. HH:MM="23:45" + pickup=00:15 następnego dnia → poprzedni dzień

    Returns Warsaw-aware datetime, albo None gdy parse fail / out-of-range.
    """
    if not hhmm or not isinstance(hhmm, str) or ":" not in hhmm:
        return None
    try:
        _parts = hhmm.strip().split(":")
        h = int(_parts[0])
        m = int(_parts[1])
    except (ValueError, TypeError, IndexError):
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None

    anchor = pickup_at_warsaw or now_warsaw or datetime.now(WARSAW_TZ)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=WARSAW_TZ)
    else:
        anchor = anchor.astimezone(WARSAW_TZ)

    candidate = datetime.combine(
        anchor.date(), _time(h, m), tzinfo=WARSAW_TZ
    )
    delta_hours = (candidate - anchor).total_seconds() / 3600.0
    if delta_hours < -6:
        candidate = candidate + timedelta(days=1)
    elif delta_hours > 6:
        candidate = candidate - timedelta(days=1)
    return candidate


def _parse_utc(s: Optional[str]) -> Optional[datetime]:
    """Parse '2026-04-10T20:09:09.000000Z' jako aware UTC."""
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def normalize_order(
    raw: dict,
    rest_name_hint: Optional[str] = None,
) -> Optional[dict]:
    """Mapuje surowe pola na czysty format dispatch v2.
    Zwraca None jesli status ignorowany (7, 8, 9).
    """
    if not raw or not isinstance(raw, dict):
        return None

    status_id = raw.get("id_status_zamowienia")
    if status_id in IGNORED_STATUSES:
        return None

    zid = str(raw.get("id", ""))
    if not zid:
        return None

    pickup_at = _parse_warsaw_naive(raw.get("czas_odbioru_timestamp"))
    created_at_utc = _parse_utc(raw.get("created_at"))
    decision_deadline = _parse_warsaw_naive(raw.get("czas_na_decyzje_timestamp"))

    # V3.19f: czas_kuriera (top-level HH:MM) → pełny Warsaw datetime.
    # Panel trzyma declared courier arrival time jako "17:10" string.
    # Anchor date z pickup_at (primary), fallback today Warsaw.
    # Zachowaj raw HH:MM (czas_kuriera_hhmm) dla debug/audit + sanity
    # check przy persist (state_machine weryfikuje strftime==raw).
    czas_kuriera_raw = raw.get("czas_kuriera")
    czas_kuriera_dt = _czas_kuriera_to_datetime(
        czas_kuriera_raw, pickup_at, datetime.now(WARSAW_TZ)
    )

    try:
        prep_minutes = int(raw.get("czas_odbioru") or 0)
    except (ValueError, TypeError):
        prep_minutes = 0

    order_type = "czasowka" if prep_minutes >= CZASOWKA_THRESHOLD_MIN else "elastic"

    # Adres dostawy (konkatenacja)
    adres_parts = [raw.get("street") or "", raw.get("nr_domu") or ""]
    adres_dostawa = " ".join(p for p in adres_parts if p).strip()
    if raw.get("nr_mieszkania"):
        adres_dostawa += f"/{raw['nr_mieszkania']}"

    addr_obj = raw.get("address") or {}
    adres_rest = addr_obj.get("street", "")
    rest_name = rest_name_hint or addr_obj.get("name") or "?"
    pickup_city = (addr_obj.get("city") or "").strip() or None

    # Miasto klienta z lokalizacja.name (FK id_location_to).
    # Panel trzyma miasto odrębnie od pola `street` — koordynator wpisuje ulicę,
    # miasto wybierane z dropdown `lokalizacja`. Bez tego geocoder defaultowałby
    # do Białystok i cachował Kleosin/Ignatki/Wasilków pod złymi coords (bug 2026-04-19).
    loc_obj = raw.get("lokalizacja") or {}
    delivery_city = (loc_obj.get("name") or "").strip() or None

    id_kurier = raw.get("id_kurier")
    is_koordynator = id_kurier == KOORDYNATOR_ID

    # Wieki zlecenia (do priority decay)
    age_minutes = None
    if created_at_utc:
        age_minutes = (datetime.now(timezone.utc) - created_at_utc).total_seconds() / 60

    # Minuty do pickupu
    minutes_to_pickup = None
    if pickup_at:
        minutes_to_pickup = (pickup_at - datetime.now(WARSAW_TZ)).total_seconds() / 60

    return {
        "order_id": zid,
        "status_id": status_id,
        "status_name": STATUS_MAP.get(status_id, f"unknown({status_id})"),
        "order_type": order_type,
        "restaurant": rest_name,
        "pickup_address": adres_rest,
        "pickup_city": pickup_city,
        "address_id": addr_obj.get("id"),
        "delivery_address": adres_dostawa,
        "delivery_city": delivery_city,
        "id_location_to": raw.get("id_location_to"),
        "pickup_at_warsaw": pickup_at.isoformat() if pickup_at else None,
        "pickup_at_epoch": pickup_at.timestamp() if pickup_at else None,
        "minutes_to_pickup": round(minutes_to_pickup, 1) if minutes_to_pickup is not None else None,
        "prep_minutes": prep_minutes,
        # V3.19f: czas_kuriera — dwa pola (raw HH:MM + ISO Warsaw).
        # Consumer (dispatch_pipeline) pod flagą ENABLE_CZAS_KURIERA_PROPAGATION.
        "czas_kuriera_hhmm": czas_kuriera_raw if isinstance(czas_kuriera_raw, str) else None,
        "czas_kuriera_warsaw": czas_kuriera_dt.isoformat() if czas_kuriera_dt else None,
        "created_at_utc": created_at_utc.isoformat() if created_at_utc else None,
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "decision_deadline": decision_deadline.isoformat() if decision_deadline else None,
        "id_kurier": id_kurier,
        "is_koordynator": is_koordynator,
        "dzien_odbioru": raw.get("dzien_odbioru"),
        "czas_doreczenia": raw.get("czas_doreczenia"),
        "zmiana_czasu_odbioru": bool(raw.get("zmiana_czasu_odbioru")),
        "position_in_pack": raw.get("position"),
        "phone": raw.get("phone"),
        "uwagi": raw.get("uwagi"),
    }


def health_check() -> dict:
    """Szybki test: login + fetch + parse + normalize 1 zlecenia."""
    result = {
        "login_ok": False,
        "fetch_ok": False,
        "parse_ok": False,
        "normalize_ok": False,
        "error": None,
    }
    try:
        opener, csrf, html = login(force=True)
        result["login_ok"] = True
        if html is None:
            html = fetch_panel_html()
        result["fetch_ok"] = True
        parsed = parse_panel_html(html)
        result["parse_ok"] = True
        result["stats"] = {
            "orders": len(parsed["order_ids"]),
            "assigned": len(parsed["assigned_ids"]),
            "unassigned": len(parsed["unassigned_ids"]),
            "restaurants": len(parsed["rest_names"]),
            "with_html_times": len(parsed["html_times"]),
        }
        # Test normalize na pierwszym nieprzypisanym
        if parsed["unassigned_ids"]:
            test_id = parsed["unassigned_ids"][0]
            raw = fetch_order_details(test_id, csrf)
            if raw:
                norm = normalize_order(raw, parsed["rest_names"].get(test_id))
                if norm:
                    result["normalize_ok"] = True
                    result["sample"] = {
                        "order_id": norm["order_id"],
                        "order_type": norm["order_type"],
                        "restaurant": norm["restaurant"],
                        "pickup_at_warsaw": norm["pickup_at_warsaw"],
                        "minutes_to_pickup": norm["minutes_to_pickup"],
                        "age_minutes": norm["age_minutes"],
                    }
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result
