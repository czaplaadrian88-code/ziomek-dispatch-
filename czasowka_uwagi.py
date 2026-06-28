"""czasowka_uwagi.py — ekstrakcja deklarowanego deadline'u DOSTAWY z free-text `uwagi`.

PROBLEM (sesja 18, zlec. 484034 Sikorskiego): koordynator/restauracja wpisuje twardy
czas dostawy w polu `uwagi`, np. "Czasówka na 17:10". Silnik klasyfikuje czasówkę
WYŁĄCZNIE po `prep_minutes >= 60` (panel_client.normalize_order) i NIE czyta tego
deadline'u → zlecenie `order_type='elastic'` z twardym 17:10 jest niewidoczne dla
SLA/feasibility/planu (R6 liczy generyczne pickup+35 → expected_delivery_by 17:34).

TEN MODUŁ = JEDNO źródło ekstrakcji frazy. ADDITYWNY (wzorzec #8): produkuje NOWE pole
`delivery_deadline_uwagi`; NIE nadpisuje `order_type` ani `czas_kuriera*` (committed R27
nietykalny). Deadline z `uwagi` to NOWA klasa ograniczenia — DOSTAWY (feasibility_v2:1016:
"czasówka rządzi tylko czasem pickupu nie delivery").

STAGE 2 BROADENING (sesja 20, 2026-06-28 — empirical-first, Lekcja #82, na korpusie oracle):
parser-cień `bundle_calib_shadow._DEADLINE_RE` (wąski: słowo-czasówka → potem liczba) gubił
~6/48 realnych przypadków (recall): odwrotna kolejność ("na 15:00 czasówka", "na 20.20 …
czasówka"), słowa pomiędzy ("czasówka u klienta na 12.15"), stem bez 'k' ("czasowe na 18:50"),
literówki ("CZASOWKA BA 20:45"). Nowa detekcja = BRAMKA na słowo-kluczu `czas[oó]w` + najbliższy
token czasu (HH:MM / HH.MM / "na HH" / liczba tuż po słowie) w OKNIE wokół słowa, w DOWOLNĄ
stronę. Precyzja: bare-liczbę bierzemy TYLKO tuż-po-słowie lub po "na" (nie "4 piętro"/"50cm").
Sanity (deadline ≥ pickup) NIE tu — robi konsument (normalize_order ma pickup_at). Recall/precyzja
mierzone `tools/czasowka_uwagi_oracle.py` (deadline_before_pickup = suspekt FP).

Konsumpcja DECYZYJNA (3 bliźniaki SLA + serializer) = osobny etap za ACK po dowodzie materialności.
Dziś: ingest + persist + pomiar offline (observability), zero wpływu na decyzje.
"""
import re
from datetime import datetime, timezone

from dispatch_v2.common import WARSAW

# Bramka: dowolny wariant słowa (czasówka/czasowka/czasowe/czasowy/czasowkaaaa/czasowo).
_KEYWORD_RE = re.compile(r"czas[oó]w", re.IGNORECASE)
# Token czasu z separatorem (HH:MM / HH.MM / HH,MM / HH;MM) — najwyższa pewność.
# Separator `,`/`;` REALNY w korpusie (Polacy piszą "12,30", "20;30" — oracle 2026-06-28:
# 4 realne deadline'y gubione bez tego → mis-parse na "na HH" → drop deadline<pickup).
_HHMM_RE = re.compile(r"(\d{1,2})[:.,;](\d{2})")
# "na HH" (bare godzina po przyimku) — średnia pewność; lookahead odcina HH<sep>MM/dłuższe.
_NA_HH_RE = re.compile(r"\bna\s+(\d{1,2})(?![\d:.,;])", re.IGNORECASE)
# Liczba tuż po słowie-kluczu (kompat z wąskim parserem-cieniem: "czasowka 14", "czasówka na 17:10").
_KW_ADJ_RE = re.compile(r"czas[oó]wk[a-zą]*\s*(?:na\s*)?(\d{1,2})(?:[:.,;](\d{2}))?", re.IGNORECASE)

# Okno wokół słowa-klucza (znaki) — łapie odwrotną kolejność i słowa pomiędzy, nie łapie
# odległych liczb (precyzja). Empiryczne: realne frazy mają czas ≤~30 zn. od słowa.
_WINDOW_BEFORE = 30
_WINDOW_AFTER = 40


def _valid(hh, mm):
    return 0 <= hh <= 23 and 0 <= mm <= 59


def parse_delivery_deadline(uwagi, day_warsaw):
    """Z pola `uwagi` → aware UTC deadline DOSTAWY tego dnia (Warsaw), albo None.

    day_warsaw: obiekt z .year/.month/.day (date lub datetime) — data Warsaw kotwicy
                (data odbioru `pickup_at`, fallback dziś Warsaw).
    Zwraca: datetime aware UTC, albo None. Parser CZYSTY (bez sanity względem pickupu).
    """
    if not uwagi or day_warsaw is None:
        return None
    text = str(uwagi)
    km = _KEYWORD_RE.search(text)
    if not km:
        return None
    lo = max(0, km.start() - _WINDOW_BEFORE)
    hi = min(len(text), km.end() + _WINDOW_AFTER)
    window = text[lo:hi]

    cands = []  # (pozycja_w_oknie, hh, mm) — bierzemy najwcześniejszy
    for m in _HHMM_RE.finditer(window):
        hh, mm = int(m.group(1)), int(m.group(2))
        if _valid(hh, mm):
            cands.append((m.start(1), hh, mm))
    for m in _NA_HH_RE.finditer(window):
        hh = int(m.group(1))
        if _valid(hh, 0):
            cands.append((m.start(1), hh, 0))
    ma = _KW_ADJ_RE.search(window)
    if ma:
        hh = int(ma.group(1))
        mm = int(ma.group(2)) if ma.group(2) is not None else 0
        if _valid(hh, mm):
            cands.append((ma.start(1), hh, mm))

    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    _, hh, mm = cands[0]
    dt = datetime(day_warsaw.year, day_warsaw.month, day_warsaw.day, hh, mm, tzinfo=WARSAW)
    return dt.astimezone(timezone.utc)
