"""Daily Accounting — panel scraper.

Per kurier 2 calls do gastro.nadajesz.pl:
  1) main: all companies → ilosc_zlecen, suma_pobran_total, suma_platnosci_karta
  2) Bar Eljot (company=27) → eljot_pobrania (suma_pobran tam), eljot_cena
     (suma_doreczonych_przesylek)

H = suma_pobran_total - eljot_pobrania + eljot_cena

Reuse session z dispatch_v2.panel_client.login() (CookieJar, CSRF).
Retry 2× z backoff 30s per PANEL_RETRY_* w config.
"""
import logging
import re
import time
import urllib.parse
from datetime import date
from typing import Dict, List, Optional, Tuple

from dispatch_v2.daily_accounting.config import (
    BAR_ELJOT_COMPANY_ID,
    PANEL_RETRY_ATTEMPTS,
    PANEL_RETRY_BACKOFF_SEC,
)

log = logging.getLogger("daily_accounting.scraper")

PANEL_BASE = "https://www.gastro.nadajesz.pl"
PANEL_ORDERS_URL = f"{PANEL_BASE}/admin2017/orders/zlecenia"

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

ILOSC_RE = re.compile(r"Ilość zleceń\s*:\s*(\d+)")
POBRANIA_RE = re.compile(r"Suma pobrań\s*:\s*([\d\s,\.]+)\s*zł")
KARTA_RE = re.compile(r"Suma płatności kart[aąy]\s*:\s*([\d\s,\.]+)\s*zł")
PRZESYLKI_RE = re.compile(r"Suma doręczonych przesyłek[^:]*:\s*([\d\s,\.]+)\s*zł")

TIMEOUT_S = 30


def _parse_zl(raw: str) -> float:
    """PL/US number parsing: last separator is decimal; others are thousands."""
    s = (raw or "").strip().replace(" ", "").replace(" ", "")
    if not s:
        return 0.0
    last_dot = s.rfind(".")
    last_comma = s.rfind(",")
    if last_dot == -1 and last_comma == -1:
        return float(s)
    decimal_pos = max(last_dot, last_comma)
    decimal_sep = s[decimal_pos]
    other_sep = "," if decimal_sep == "." else "."
    s = s.replace(other_sep, "")
    if decimal_sep == ",":
        s = s.replace(",", ".")
    return float(s)


def _strip_html(html: str) -> str:
    return WHITESPACE_RE.sub(" ", HTML_TAG_RE.sub(" ", html))


def _build_url(
    date_from: date,
    date_to: date,
    cid: int,
    company: Optional[int] = None,
) -> str:
    params = {
        "data_od": date_from.isoformat(),
        "data_do": date_to.isoformat(),
        "kurier": str(cid),
        "platnosc": "",
    }
    if company is not None:
        params["company"] = str(company)
    return f"{PANEL_ORDERS_URL}?{urllib.parse.urlencode(params)}"


def build_courier_iteration_list(
    kurier_ids: Dict[str, int],
    excluded: set,
) -> List[Tuple[int, str]]:
    """Dedupe po cid, canonical alias = pierwszy klucz w kurier_ids.json (insertion order)."""
    seen_cids: Dict[int, str] = {}
    for alias, cid in kurier_ids.items():
        if cid in excluded:
            continue
        if cid not in seen_cids:
            seen_cids[cid] = alias
    return list(seen_cids.items())


def _fetch_panel_page(opener, url: str) -> str:
    """Single fetch with session check. Raises on redirect-to-login."""
    res = opener.open(url, timeout=TIMEOUT_S)
    if "admin2017/login" in res.url:
        raise RuntimeError("redirect to login (session expired)")
    return res.read().decode("utf-8", errors="replace")


def _scrape_with_retry(opener, url: str, kind: str, cid: int) -> str:
    """Fetch URL z retry PANEL_RETRY_ATTEMPTS razy + backoff. Raise po ostatecznym failu."""
    last_err: Optional[Exception] = None
    for attempt in range(PANEL_RETRY_ATTEMPTS):
        try:
            return _fetch_panel_page(opener, url)
        except Exception as e:
            last_err = e
            log.warning(
                f"{kind} cid={cid} attempt {attempt + 1}/{PANEL_RETRY_ATTEMPTS} "
                f"FAIL: {e}"
            )
            if attempt < PANEL_RETRY_ATTEMPTS - 1:
                time.sleep(PANEL_RETRY_BACKOFF_SEC)
    raise RuntimeError(f"{kind} cid={cid}: {PANEL_RETRY_ATTEMPTS} attempts failed, last={last_err}")


def parse_main_page(html: str) -> Dict[str, float]:
    """Parse main call HTML → ilosc_zlecen, suma_pobran_total, suma_platnosci_karta."""
    clean = _strip_html(html)
    ilosc_m = ILOSC_RE.search(clean)
    pobr_m = POBRANIA_RE.search(clean)
    karta_m = KARTA_RE.search(clean)
    if not ilosc_m:
        raise RuntimeError("parse main: Ilość zleceń not found")
    if not pobr_m:
        raise RuntimeError("parse main: Suma pobrań not found")
    # Karta może być 0.00 gdy brak płatności kartą — regex musi złapać etykietę
    karta_val = _parse_zl(karta_m.group(1)) if karta_m else 0.0
    return {
        "ilosc_zlecen": int(ilosc_m.group(1)),
        "suma_pobran_total": _parse_zl(pobr_m.group(1)),
        "suma_platnosci_karta": karta_val,
    }


def parse_eljot_page(html: str) -> Dict[str, float]:
    """Parse Bar Eljot (company=27) HTML → eljot_pobrania, eljot_cena.

    Defaults 0.0 gdy pole brak (brak zleceń Eljot = panel może renderować "Suma pobrań: 0,00 zł"
    albo pominąć sekcję — oba OK, 0.0 fallback).
    """
    clean = _strip_html(html)
    pobr_m = POBRANIA_RE.search(clean)
    przes_m = PRZESYLKI_RE.search(clean)
    return {
        "eljot_pobrania": _parse_zl(pobr_m.group(1)) if pobr_m else 0.0,
        "eljot_cena": _parse_zl(przes_m.group(1)) if przes_m else 0.0,
    }


def compute_h(
    suma_pobran_total: float,
    eljot_pobrania: float,
    eljot_cena: float,
) -> float:
    """H = suma_pobran_total - eljot_pobrania + eljot_cena.

    Eljot exception: wyjmujemy rzeczywiste pobrania Eljot, wkładamy w zamian
    cenę doręczonych przesyłek (dynamiczna, nie hardcoded 20 zł).
    """
    return round(suma_pobran_total - eljot_pobrania + eljot_cena, 2)


def scrape_courier(
    opener,
    cid: int,
    alias: str,
    date_from: date,
    date_to: date,
) -> Dict[str, float]:
    """Full scrape: 2 calls + H compute. Raises jeśli main call fail po retries.

    Eljot call fail → log warning + default 0.0 (Eljot exception nie zadziała).

    Returns: dict z pełnymi polami rekordu kuriera.
    """
    url_main = _build_url(date_from, date_to, cid, company=None)
    html_main = _scrape_with_retry(opener, url_main, "main", cid)
    main = parse_main_page(html_main)

    url_elj = _build_url(date_from, date_to, cid, company=BAR_ELJOT_COMPANY_ID)
    try:
        html_elj = _scrape_with_retry(opener, url_elj, "eljot", cid)
        elj = parse_eljot_page(html_elj)
    except Exception as e:
        log.warning(f"eljot cid={cid} alias={alias!r} final fail: {e} — default 0.0")
        elj = {"eljot_pobrania": 0.0, "eljot_cena": 0.0}

    h = compute_h(main["suma_pobran_total"], elj["eljot_pobrania"], elj["eljot_cena"])

    return {
        "cid": cid,
        "alias": alias,
        "ilosc_zlecen": main["ilosc_zlecen"],
        "suma_pobran_total": main["suma_pobran_total"],
        "suma_platnosci_karta": main["suma_platnosci_karta"],
        "eljot_pobrania": elj["eljot_pobrania"],
        "eljot_cena": elj["eljot_cena"],
        "H_computed": h,
    }
