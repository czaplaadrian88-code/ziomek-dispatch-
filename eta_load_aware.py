"""L5.1 (Faza 3 audytu, Sprint 1 Z3, 2026-07-05) — ETA load-aware: korekta
systematycznego OPTYMIZMU nogi ODBIORU (korzeń K3).

Oś prawdy (eta_truth_map, okno 28.06-04.07, n=925, znak − = optymizm):
noga odbioru med −4.0 min (p10 −17.8), rosnąca ze scarcity (ciasno −5.1),
solo (−6.0) i tierem (std −5.4, new −4.2, gold −3.2); noga JAZDY ~0 błędu.
Silnik OBIECUJE odbiór wcześniej, niż kurier realnie odbiera → nierealne
committed czas_kuriera + zaniżony wait/extension.

Model v1 (celowo prosty, bez 4. mapy — bramka „zero kopii" L5.1):
bufor[min] = clamp(−mediana_błędu(segment), 0, CAP) z hierarchią segmentów
  (tier_bag × solo/bundle) → (tier_bag) → (globalny)
Tabela = dispatch_state/eta_load_aware_calib.json, generowana WYŁĄCZNIE
narzędziem tools/eta_load_aware_calibrate.py z joinów eta_truth_map
(jedno źródło prawdy pomiaru — zero drugiej kopii logiki joinu).

Konsumpcja: dispatch_pipeline._v327_eval_courier_inner (po finalizacji
eta_pickup_utc). SHADOW: metryki `eta_la_buffer_min` +
`eta_pickup_load_aware_utc` zawsze (auto-serializacja L1.1). DECYZJA:
tylko gdy `decision_flag("ENABLE_ETA_LOAD_AWARE")` (default OFF) — wtedy
bufor przesuwa eta_pickup_utc/travel_min (oś OBIETNICY: wait-penalty,
extension, target_pickup/committed-propozycja). NIE dotyka feasibility_v2
(HARD R6 GATE-STRICTER + Q2 „nie zdąży→nie dostaje" = OSOBNY pas za ACK —
inwersja HARD, roadmapa L5 ⛔). Znana granica: kandydaci no_gps/pre_shift
mają eta nadpisywane post-loop polityką (max(15,prep)/clamp do zmiany) —
bufor ich nie dotyczy (celowa polityka równego traktowania, nie K3).

Fail-soft: brak tabeli / zły JSON / brak segmentu → bufor 0.0 (silnik
zachowuje się jak przed L5). Cache po mtime (wzorzec load_flags).
"""
import json
import os
from typing import Optional

CALIB_PATH = os.environ.get(
    "ETA_LOAD_AWARE_CALIB_PATH",
    "/root/.openclaw/workspace/dispatch_state/eta_load_aware_calib.json",
)

# Twardy sufit bufora [min] — kalibracja nigdy nie dodaje więcej (ochrona
# przed zatrutą tabelą; p10 −17.8 to ogon, nie cel korekty mediany).
BUFFER_CAP_MIN = 12.0

_cache = {"mtime": None, "data": None}


def _load_calib() -> dict:
    try:
        mtime = os.path.getmtime(CALIB_PATH)
    except OSError:
        return {}
    if _cache["mtime"] == mtime and _cache["data"] is not None:
        return _cache["data"]
    try:
        with open(CALIB_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "segments" not in data:
            return {}
    except Exception:
        return {}
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def _clamp(v: float) -> float:
    return max(0.0, min(BUFFER_CAP_MIN, float(v)))


def pickup_buffer_min(tier_bag: Optional[str], bag_size: int) -> float:
    """Bufor [min] dokładany do obiecanego czasu ODBIORU. 0.0 = brak korekty.

    tier_bag: gold|std+|std|slow|new|None (cs.tier_bag).
    bag_size: rozmiar worka PRZED dodaniem nowego zlecenia (0 = solo).
    """
    calib = _load_calib()
    segs = calib.get("segments") or {}
    solo = "solo" if int(bag_size or 0) == 0 else "bundle"
    tier = (tier_bag or "").strip() or "unknown"
    for key in (f"{tier}|{solo}", tier, "_global"):
        entry = segs.get(key)
        if not entry:
            continue
        med = entry.get("med_err_min")
        n = entry.get("n") or 0
        if med is None or n < int(calib.get("min_n") or 30):
            continue
        if med >= 0:            # segment pesymistyczny → nie dokładaj
            return 0.0
        return _clamp(-float(med))
    return 0.0
