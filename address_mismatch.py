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
