"""Scrape 3 liczb z górnej sumki panel Rutcom per (company, week_range).

Zwraca {'przesylki', 'pobrania', 'prowizja'} jako float PLN.
COD = pobrania - przesylki - prowizja.
"""
import logging
import re
import time
import urllib.parse
from datetime import date
from typing import Union, List

from dispatch_v2.cod_weekly.config import PANEL_ORDERS_URL

log = logging.getLogger("cod_weekly.scraper")

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

PRZESYLKI_RE = re.compile(r"Suma doręczonych przesyłek[^:]*:\s*([\d\s,\.]+)\s*zł")
POBRANIA_RE = re.compile(r"Suma pobrań:\s*([\d\s,\.]+)\s*zł")
PROWIZJA_RE = re.compile(r"Prowizja płatności kart[aąy]\s*:\s*([\d\s,\.]+)\s*zł")
# Panel nie renderuje wartości prowizji gdy 0 kartowych → "Prowizja...: Do wypłaty:..."
PROWIZJA_EMPTY_RE = re.compile(
    r"Prowizja płatności kart[aąy]\s*:\s*(?:Do wypłaty|<)"
)

RETRY_MAX = 3
RETRY_BACKOFF_S = (2, 5, 10)
TIMEOUT_S = 30


def _parse_zl(raw: str) -> float:
    """'343.96' / '1 234,56' / '2.408,44' / '1,234.56' → float.

    Wykrywa format po pozycji OSTATNIEGO separatora = dziesiętny,
    inne wystąpienia = separator tysięcy (usuwane). Obsługuje:
      pl: 2.408,44  (kropka=tysiące, przecinek=dec)
      pl: 1 234,56  (spacja=tysiące, przecinek=dec)
      us: 1,234.56  (przecinek=tysiące, kropka=dec)
      plain: 343.96 / 0,87 / 87.00
    """
    s = raw.strip().replace("\u00a0", "").replace(" ", "")
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


def _build_url(company_id: int, week_start: date, week_end: date) -> str:
    qs = urllib.parse.urlencode(
        {
            "data_od": week_start.isoformat(),
            "data_do": week_end.isoformat(),
            "company": company_id,
            "kurier": "",
            "platnosc": "",
        }
    )
    return f"{PANEL_ORDERS_URL}?{qs}"


def scrape_company_week(
    opener, company_id: int, week_start: date, week_end: date
) -> dict:
    """Single company, single week → {'przesylki','pobrania','prowizja'} float PLN.

    Retry 3× z exp backoff. Gdy 0 zleceń — panel renderuje 0.00, OK.
    """
    url = _build_url(company_id, week_start, week_end)
    last_err = None
    for attempt in range(RETRY_MAX):
        try:
            res = opener.open(url, timeout=TIMEOUT_S)
            if "admin2017/login" in res.url:
                raise RuntimeError("redirect na login (sesja wygasła)")
            html = res.read().decode("utf-8", errors="replace")
            clean = _strip_html(html)
            p_m = PRZESYLKI_RE.search(clean)
            pb_m = POBRANIA_RE.search(clean)
            pr_m = PROWIZJA_RE.search(clean)
            if not (p_m and pb_m):
                raise RuntimeError(
                    f"parse fail company={company_id}: "
                    f"przes={bool(p_m)} pobr={bool(pb_m)}"
                )
            if pr_m:
                prow_val = _parse_zl(pr_m.group(1))
            elif PROWIZJA_EMPTY_RE.search(clean):
                prow_val = 0.0  # panel nie renderuje wartości gdy 0 kartowych
            else:
                raise RuntimeError(
                    f"parse fail company={company_id}: prowizja etykieta nieobecna"
                )
            return {
                "przesylki": _parse_zl(p_m.group(1)),
                "pobrania": _parse_zl(pb_m.group(1)),
                "prowizja": prow_val,
            }
        except Exception as e:
            last_err = e
            backoff = RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)]
            log.warning(
                f"company={company_id} attempt {attempt + 1}/{RETRY_MAX} FAIL: "
                f"{e} (backoff {backoff}s)"
            )
            if attempt < RETRY_MAX - 1:
                time.sleep(backoff)
    raise RuntimeError(
        f"company={company_id} {week_start}..{week_end}: "
        f"{RETRY_MAX} prób nieudanych, last={last_err}"
    )


def compute_cod(sums: dict) -> float:
    return round(sums["pobrania"] - sums["przesylki"] - sums["prowizja"], 2)


def scrape_restaurant_cod(
    opener,
    restaurant_name: str,
    company_value: Union[int, List[int]],
    week_start: date,
    week_end: date,
) -> dict:
    """Obsługa single-int lub multi-company list — sumuje 3 liczby przed COD.

    Zwraca pełny rekord z detalami per company (jeśli multi).
    """
    cids = company_value if isinstance(company_value, list) else [company_value]
    details = []
    total_p = 0.0
    total_pb = 0.0
    total_pr = 0.0
    for cid in cids:
        s = scrape_company_week(opener, cid, week_start, week_end)
        details.append(
            {"company_id": cid, **s, "cod_partial": compute_cod(s)}
        )
        total_p += s["przesylki"]
        total_pb += s["pobrania"]
        total_pr += s["prowizja"]
    total_cod = round(total_pb - total_p - total_pr, 2)
    return {
        "restaurant": restaurant_name,
        "company_ids": cids,
        "przesylki": round(total_p, 2),
        "pobrania": round(total_pb, 2),
        "prowizja": round(total_pr, 2),
        "cod": total_cod,
        "details": details if len(details) > 1 else None,
    }
