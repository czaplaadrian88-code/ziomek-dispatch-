"""czasowka_uwagi.py — ekstrakcja deklarowanego deadline'u DOSTAWY z free-text `uwagi`.

PROBLEM (sesja 18, zlec. 484034 Sikorskiego): koordynator/restauracja wpisuje twardy
czas dostawy w polu `uwagi`, np. "Czasówka na 17:10". Silnik klasyfikuje czasówkę
WYŁĄCZNIE po `prep_minutes >= 60` (panel_client.normalize_order) i NIE czyta tego
deadline'u → zlecenie `order_type='elastic'` z twardym 17:10 jest niewidoczne dla
SLA/feasibility/planu (R6 liczy generyczne pickup+35 → expected_delivery_by 17:34).

TEN MODUŁ = JEDNO źródło ekstrakcji frazy (parytet semantyki z parserem-cieniem
`tools/bundle_calib_shadow._parse_deadline`, który dziś żyje tylko w shadow — bundle_calib
jest POD AUDYTEM, więc NIE migrujemy go; test `test_czasowka_uwagi_deadline` pilnuje
PARYTETU regexu między tym modułem a shadow, żeby się nie rozjechały).

ADDITYWNY (wzorzec #8 z ziomek-change-protocol): produkuje NOWE pole
`delivery_deadline_uwagi`; NIE nadpisuje `order_type` ani `czas_kuriera*` (committed R27
nietykalny). Deadline z `uwagi` to NOWA klasa ograniczenia — DOSTAWY (feasibility_v2:1016:
"czasówka rządzi tylko czasem pickupu nie delivery"), nie pokrywa jej istniejąca czasówka
(prep≥60 = pickup).

Konsumpcja DECYZYJNA (wpięcie w 3 bliźniaki SLA + serializer) = osobny, późniejszy etap
za flagą, po dowodzie materialności z oracle + decyzji HARD-vs-SOFT. Dziś: ingest + persist
+ pomiar offline (observability), zero wpływu na decyzje.

⚠ Parser jest CZYSTY (sama ekstrakcja) — sanity (deadline ≥ pickup) i tier-aware (35/40)
należą do warstwy konsumującej, nie do ekstrakcji. Precyzję/recall regexu mierzy
`tools/czasowka_uwagi_oracle.py` na realnych `uwagi` (empirical-first, Lekcja #82) PRZED
ewentualnym poszerzeniem wzorca.
"""
import re
from datetime import datetime, timezone

from dispatch_v2.common import WARSAW

# Łapie m.in.: "Czasówka na 17:10", "czasowka 14", "na 14.00", "CZASOWKA NA 16.30".
# Godzina 0-23, minuty opcjonalne. PARYTET 1:1 z tools/bundle_calib_shadow._DEADLINE_RE
# (intencjonalna kopia do czasu migracji bundle_calib spod audytu — test pilnuje parytetu).
_DELIVERY_DEADLINE_RE = re.compile(
    r"czas[oó]wk[a-zą]*\s*(?:na\s*)?(\d{1,2})(?:[:.](\d{2}))?",
    re.IGNORECASE)


def parse_delivery_deadline(uwagi, day_warsaw):
    """Z pola `uwagi` → aware UTC deadline DOSTAWY tego dnia (Warsaw), albo None.

    day_warsaw: obiekt z atrybutami .year/.month/.day (date lub datetime, dowolna tz)
                — data Warsaw, do której przypinamy godzinę z `uwagi` (kotwica = data
                odbioru `pickup_at`, fallback dziś Warsaw; analogicznie do `czas_kuriera`).
    Zwraca: datetime aware w UTC, albo None gdy brak frazy / niepoprawna godzina.
    Parser CZYSTY — żadnej sanity względem pickupu (to robi konsument).
    """
    if not uwagi or day_warsaw is None:
        return None
    m = _DELIVERY_DEADLINE_RE.search(str(uwagi))
    if not m:
        return None
    try:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) is not None else 0
    except (ValueError, TypeError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    dt = datetime(day_warsaw.year, day_warsaw.month, day_warsaw.day, hh, mm,
                  tzinfo=WARSAW)
    return dt.astimezone(timezone.utc)
