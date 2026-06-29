"""B (ingestia Ziomka) — shadow-detektor spójności ulica↔miasto.

Ulica „silnie białostocka" (≥ MIN_BIA trafień w Białymstoku) wpisana w INNYM mieście, gdzie
prawie nie występuje (≤ MAX_HERE) = niemal na pewno błąd miasta na zleceniu → zły geokod
(case „Armii Krajowej 15, Olmonty", 7 km w bok). Zlecenia z gastro to dominujące źródło tych
błędów (panel restauracji ma własny detektor w /estimate — TO jest bliźniak po stronie silnika).

SHADOW/log-only: `maybe_log_mismatch` dopisuje do dispatch_state/address_mismatch_shadow.jsonl,
NIE zmienia decyzji dispatchu. Flaga `ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW` (gate po stronie callera).

⚠ BLIŹNIAK: logika identyczna z panelem `app/api/dispatch.py:check_street_town` (ten sam próg
MIN_BIA=5 / MAX_HERE=1, ten sam klucz ulicy). Zmiana progu = zmień OBA miejsca.
"""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from functools import lru_cache
from pathlib import Path

from dispatch_v2.geocoding import CACHE_PATH as _GEOCODE_CACHE_PATH

_SHADOW_LOG = Path("/root/.openclaw/workspace/dispatch_state/address_mismatch_shadow.jsonl")

_ADDR_CHECK_MIN_BIA = 5   # ulica musi mieć ≥5 trafień w Białymstoku, by była „silnie białostocka"
_ADDR_CHECK_MAX_HERE = 1  # i ≤1 w wybranym mieście → mismatch


def _town_key(s: str) -> str:
    """Klucz dedup miasta: bez akcentów, lower, trim; ł→l ręcznie (NFKD nie tknie)."""
    norm = unicodedata.normalize("NFKD", (s or "").lower())
    stripped = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return stripped.replace("ł", "l").strip()


def _street_name_key(raw: str) -> str:
    """Klucz ulicy z surowego adresu: część przed pierwszym numerem, bez prefiksu ul./miasta."""
    m = re.match(r"^(.*?)[\s,]+\d", (raw or "").strip())
    name = (m.group(1) if m else (raw or "")).strip().rstrip(",")
    sl = name.lower()
    for pref in ("ulica ", "ul. ", "ul "):
        if sl.startswith(pref):
            name = name[len(pref):].strip()
            sl = name.lower()
            break
    if sl.startswith(("białystok ", "bialystok ")):
        name = name[len("białystok "):].strip()
    return _town_key(name)


@lru_cache(maxsize=1)
def _street_town_counts_cached(mtime: float) -> dict:
    """{street_key: {town_key: liczność}} z geocode_cache — rozkład ulic per miasto."""
    try:
        data = json.loads(Path(_GEOCODE_CACHE_PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for key, v in data.items():
        if not isinstance(v, dict):
            continue
        city = _town_key(v.get("city") or "Białystok")
        if city.startswith("bialystok"):
            city = "bialystok"
        orig = (v.get("original") or key.split("|")[0]).strip()
        sk = _street_name_key(orig)
        if len(sk) < 3 or any(ch.isdigit() for ch in sk):
            continue
        out.setdefault(sk, {})
        out[sk][city] = out[sk].get(city, 0) + 1
    return out


def _street_town_counts() -> dict:
    try:
        mtime = Path(_GEOCODE_CACHE_PATH).stat().st_mtime
    except OSError:
        mtime = 0.0
    return _street_town_counts_cached(mtime)


def check_street_town(street, town) -> dict | None:
    """Ostrzeżenie gdy ulica jest silnie białostocka, a wybrano inne miasto. None = OK.
    Czysta funkcja — patrzy tylko w rozkład geocode_cache."""
    if not street or not town:
        return None
    tk = _town_key(town)
    if tk.startswith("bialystok"):
        return None  # Białystok = bez zastrzeżeń
    sk = _street_name_key(street)
    if len(sk) < 3:
        return None
    counts = _street_town_counts().get(sk, {})
    bia = counts.get("bialystok", 0)
    here = counts.get(tk, 0)
    if bia >= _ADDR_CHECK_MIN_BIA and here <= _ADDR_CHECK_MAX_HERE:
        return {
            "street": str(street).strip(), "town": str(town).strip(),
            "street_bialystok_count": bia, "street_here_count": here,
            "suggest_town": "Białystok",
        }
    return None


def maybe_log_mismatch(order_id, street, town) -> dict | None:
    """SHADOW: jeśli ulica↔miasto się nie zgadza, dopisz wpis do jsonl. Zwraca werdykt lub None.
    Fail-soft — błąd zapisu nie przerywa (caller i tak ma try/except)."""
    w = check_street_town(street, town)
    if not w:
        return None
    rec = {"ts": time.time(), "order_id": str(order_id) if order_id is not None else None, **w}
    try:
        with open(_SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return w


# ─────────────────────────────────────────────────────────────────────────────
# B2 — shadow-detektor rozjazdu TEKST ↔ PIN (współrzędne).
#
# Inna klasa niż ulica↔miasto: tu MIASTO bywa poprawne (Białystok), ale napisana
# nazwa ulicy wskazuje inne miejsce niż pin, na którym kurier faktycznie jedzie.
# Case 484269: tekst „Można 10/23" geokoduje się 4,26 km od zapisanego
# `delivery_coords` (Mroźna 10) — tekst stał po edycji, coords poprawione
# (`gastro_edit.regeocode_and_update` aktualizuje TYLKO coords, nie tekst).
# Łapie też zwykłe literówki ulicy geokodujące się gdzie indziej.
#
# Wykrywanie ŹRÓDŁOWO-AGNOSTYCZNE: throttlowany sweep utrwalonego orders_state
# (to, co konsola/apka POKAZUJE), niezależnie od tego jak rozjazd powstał
# (tworzenie / nasza edycja / edycja w gastro / stale). NIE hook NEW_ORDER — tam
# coords=geokod(tekst), więc rozjazd jeszcze nie istnieje.
# SHADOW/log-only do TEGO SAMEGO jsonl z polem `check:"text_coords"`; flaga
# `ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW` (gate po stronie callera).
# ─────────────────────────────────────────────────────────────────────────────

_COORDS_MISMATCH_MIN_M = 400.0   # próg rozjazdu tekst↔pin (Adrian 29.06)
_SWEEP_INTERVAL_S = 300.0        # throttle sweepa (≈1 cykl plan_recheck)
_ACTIVE_FOR_SWEEP = {"planned", "assigned", "picked_up"}

# Precyzja (FAZA A, 29.06) — odsiew ZMIERZONYCH fałszywek, gdy geokod tekstu jest NIEPEWNY,
# bo nie znamy właściwego miasta (detektor domyśla 'Białystok' przy braku delivery_city):
#  (1) kod pocztowy NN-NNN na początku = adres luzem spoza miasta (484119 „16-070 Porosły", 7,4 km);
#  (2) BRAK delivery_city + ulica WIELOMIEJSKA (jest w ≥1 innym mieście z liczbą ≥2) → wymuszony
#      'Białystok' ląduje we wsi/innym miejscu (484332 „Spacerowa" Nowodworce 7,7 km; 484334
#      „Ananasowa" Grabówka 6,5 km). Gdy delivery_city PODANE — geokodujemy właściwym miastem,
#      więc NIE pomijamy. Ulica jednomiastowa-białostocka („Można" {bia:1}) przechodzi → realne
#      typo łapane. Oś MIASTA należy do detektora ulica↔miasto, nie tu.
# NIE filtrowane (świadomie, czeka na kalibrację danymi at-198, część to realne rozjazdy):
# niepewność numeru na długiej ulicy (484298 „Wyszyńskiego" 1,25 km z PODANYM Białymstokiem).
# Numerowane ulice („11 Listopada", „3 Maja") NIE są łapane — wzorzec wymaga myślnika NN-NNN.
_POSTAL_PREFIX_RE = re.compile(r"^\s*\d{2}-\d{3}")


def _skip_for_text_pin(street, city) -> bool:
    """True = pomiń detekcję tekst-pin (geokod tekstu byłby niepewny → fałszywka). Patrz komentarz
    wyżej: (1) kod pocztowy na początku; (2) brak miasta + ulica wielomiejska. Czysta."""
    if _POSTAL_PREFIX_RE.match(street or ""):
        return True
    if not (city and str(city).strip()):           # brak delivery_city → ryzyko złego miasta
        sk = _street_name_key(street or "")
        if len(sk) >= 3:
            counts = _street_town_counts().get(sk, {})
            if any(c >= 2 for t, c in counts.items() if not t.startswith("bialystok")):
                return True                          # ulica istotnie obecna poza Białymstokiem
    return False

_sweep_last_ts = 0.0
_coords_logged: set = set()      # (oid, round(lat,5), round(lng,5)) — dedup w obrębie procesu


def _haversine_m(a, b) -> float:
    """Odległość w metrach między (lat,lng)."""
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def check_text_coords(street, city, used_coords, *, geocode_fn) -> dict | None:
    """Rozjazd tekst↔pin: geokoduj `street` (cache-first) i porównaj z `used_coords`
    (współrzędne, których pipeline realnie używa do trasy). > MIN_M = tekst i pin
    wskazują różne miejsca → jedno z nich błędne. None = OK / za mało danych.

    Czysta funkcja — `geocode_fn` wstrzykiwany (testowalność, brak importu cyklicznego).
    Fail-soft: każdy błąd parsowania/geokodu → None (nie alarmuje, nie wywraca)."""
    if not street or not used_coords:
        return None
    if _skip_for_text_pin(street, city):    # FAZA A: kod pocztowy/spoza miasta → fałszywka
        return None
    try:
        uc = (float(used_coords[0]), float(used_coords[1]))
    except (TypeError, ValueError, IndexError):
        return None
    try:
        tc = geocode_fn(street, city=city or "Białystok")
    except Exception:  # noqa: BLE001
        return None
    if not tc:
        return None
    try:
        tcf = (float(tc[0]), float(tc[1]))
        dist = _haversine_m(uc, tcf)
    except (TypeError, ValueError, IndexError):
        return None
    if dist <= _COORDS_MISMATCH_MIN_M:
        return None
    return {
        "check": "text_coords",
        "street": str(street).strip(),
        "city": (str(city).strip() if city else "Białystok"),
        "text_coords": [round(tcf[0], 6), round(tcf[1], 6)],
        "used_coords": [round(uc[0], 6), round(uc[1], 6)],
        "distance_m": round(dist, 1),
    }


def maybe_sweep_text_coords(state, now_ts, *, geocode_fn) -> int:
    """SHADOW: throttlowany sweep aktywnych zleceń `orders_state` — porównuje
    `delivery_address` (tekst) z `delivery_coords` (pin). Rozjazdy > MIN_M dopisuje
    do jsonl (check=text_coords), dedup per (oid, coords) w obrębie procesu.
    Zwraca liczbę NOWYCH wpisów. Fail-soft. Gate flagi po stronie callera."""
    global _sweep_last_ts
    try:
        if (now_ts - _sweep_last_ts) < _SWEEP_INTERVAL_S:
            return 0
    except (TypeError, ValueError):
        return 0
    _sweep_last_ts = now_ts
    n = 0
    for oid, o in (state or {}).items():
        if not isinstance(o, dict) or o.get("status") not in _ACTIVE_FOR_SWEEP:
            continue
        coords = o.get("delivery_coords")
        street = o.get("delivery_address")
        if not coords or not street:
            continue
        try:
            dk = (str(oid), round(float(coords[0]), 5), round(float(coords[1]), 5))
        except (TypeError, ValueError, IndexError):
            continue
        if dk in _coords_logged:
            continue
        w = check_text_coords(street, o.get("delivery_city"), coords, geocode_fn=geocode_fn)
        if not w:
            continue
        _coords_logged.add(dk)
        rec = {"ts": now_ts, "order_id": str(oid), **w}
        try:
            with open(_SHADOW_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
        n += 1
    return n
