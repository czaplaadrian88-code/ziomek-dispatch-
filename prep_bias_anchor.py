"""[C2] Korekta kotwicy R6 o zmierzony bias kuchni (prep-bias).

Czyta tabelę `dispatch_state/prep_bias_table.json` (output tool/prep_bias_build.py,
zbudowany TYLKO z czystego sygnału "kurier-dotarł-i-czekał") i zwraca przesunięcie
kotwicy termicznej R6 per restauracja.

Doktryna kierunku (Adrian 2026-06-20, zadanie C2):
  - bias DODATNI = kuchnia systematycznie WOLNIEJSZA niż deklaruje (deklaruje
    gotowość za wcześnie). Wtedy jedzenie realnie "wisi" / plan odbioru jest
    zbyt optymistyczny → R6 ma być MNIEJ optymistyczna (bije wcześniej).
  - W NIEPEWNOŚCI przechylamy ku OCHRONIE ŚWIEŻOŚCI: korekta NIGDY nie czyni
    R6 bardziej liberalną niż baseline. Konkretnie: dla biasu dodatniego
    przesuwamy kotwicę WCZEŚNIEJ (anchor -= bias) → bag_time_min rośnie →
    R6 bije wcześniej. Dla biasu UJEMNEGO (kuchnia szybsza niż deklaruje —
    w realnych danych praktycznie nie występuje) NIE rozluźniamy R6:
    przesunięcie jest klampowane do 0 (anchor bez zmian).

Tabela ładowana RAZ (cache po mtime, hot-reload jak flags). Fail-soft: brak
pliku / zły JSON / brak wpisu → przesunięcie 0.0 (zachowanie baseline).

Ten moduł NIE czyta flagi i NIE decyduje czy korektę stosować — to robi
caller (feasibility_v2) za flagą ENABLE_PREP_BIAS_TABLE. Tu tylko czysta
funkcja "ile minut przesunąć kotwicę dla tej restauracji".
"""

import json
import logging
import os
import threading

log = logging.getLogger(__name__)

PREP_BIAS_TABLE_PATH = "/root/.openclaw/workspace/dispatch_state/prep_bias_table.json"

# Klucz biasu używany do korekty kotwicy. p80 jest ostrożniejszy (większy dodatni
# bias) niż mediana → silniejsza ochrona świeżości; spójne z doktryną "w
# niepewności ku ochronie". Median dostępny w tabeli, ale do GATE bierzemy p80.
_BIAS_KEY = "bias_p80_min"
_BIAS_KEY_FALLBACK = "bias_median_min"

# Sanity cap: nie przesuwamy kotwicy o absurdalną wartość nawet gdyby tabela
# miała wartość odstającą. 20 min = praktyczny sufit (R6 hard = 35 min).
MAX_ANCHOR_SHIFT_MIN = 20.0

_lock = threading.Lock()
_cache = None          # dict: payload z pliku (tabela + _global)
_cache_mtime = None    # float mtime pliku przy ostatnim wczytaniu
_load_failed_logged = False


def _load_table(path=None):
    """Wczytaj tabelę z cache po mtime. Fail-soft → None (brak korekty).

    path=None → bieżąca wartość modułowego PREP_BIAS_TABLE_PATH (resolwowana w
    czasie wywołania, nie bindowana przy definicji — by monkeypatch działał)."""
    global _cache, _cache_mtime, _load_failed_logged
    if path is None:
        path = PREP_BIAS_TABLE_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        # brak pliku — log raz, potem cicho (zachowanie baseline)
        if not _load_failed_logged:
            log.info("prep_bias: brak %s — korekta R6 nieaktywna (baseline)", path)
            _load_failed_logged = True
        return None
    with _lock:
        if _cache is not None and _cache_mtime == mtime:
            return _cache
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("payload nie jest dict")
            _cache = data
            _cache_mtime = mtime
            _load_failed_logged = False
            return _cache
        except (ValueError, OSError) as e:
            if not _load_failed_logged:
                log.warning("prep_bias: nie udało się wczytać %s: %s — baseline", path, e)
                _load_failed_logged = True
            return None


def _raw_bias_for(restaurant, table):
    """Surowy bias (ze znakiem) dla restauracji: wpis → fallback _global → None."""
    if table is None or not restaurant:
        return None
    entry = table.get(restaurant)
    if isinstance(entry, dict):
        v = entry.get(_BIAS_KEY)
        if v is None:
            v = entry.get(_BIAS_KEY_FALLBACK)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    # nieznana restauracja → globalny bias (każda kuchnia w danych zaniża → >0)
    g = table.get("_global")
    if isinstance(g, dict):
        v = g.get(_BIAS_KEY)
        if v is None:
            v = g.get(_BIAS_KEY_FALLBACK)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def anchor_shift_min(restaurant, path=None):
    """Ile minut PRZESUNĄĆ kotwicę R6 dla danej restauracji (ZE ZNAKIEM).

    Konwencja zwracanej wartości: liczba do DODANIA do kotwicy (anchor + shift).
    - bias dodatni (kuchnia wolniejsza) → zwraca wartość UJEMNĄ (anchor wcześniej
      → bag_time większy → R6 bije wcześniej, ochrona świeżości).
    - bias ujemny (kuchnia szybsza) → klamp do 0.0 (NIE rozluźniamy R6).
    - brak danych / brak pliku → 0.0 (baseline).

    Zwraca (shift_min: float, source: str) — source do metryk/logu.
    """
    table = _load_table(path)
    if table is None:
        return 0.0, "no_table"
    raw = _raw_bias_for(restaurant, table)
    if raw is None:
        return 0.0, "no_entry"
    src = "entry" if (restaurant in table and isinstance(table.get(restaurant), dict)) else "global"
    if raw <= 0.0:
        # kuchnia nie wolniejsza niż deklaruje → nie rozluźniamy R6
        return 0.0, src + "_clamped_nonpos"
    shift = -min(raw, MAX_ANCHOR_SHIFT_MIN)  # anchor WCZEŚNIEJ o bias (cap)
    return shift, src


def bias_info_for(restaurant, path=None):
    """Pełny wpis tabeli dla restauracji (do logu/telemetrii) albo None."""
    table = _load_table(path)
    if table is None:
        return None
    entry = table.get(restaurant)
    return entry if isinstance(entry, dict) else None


def _reset_cache_for_tests():
    """Wyzeruj cache (używane w testach gdy podmieniają plik tabeli)."""
    global _cache, _cache_mtime, _load_failed_logged
    with _lock:
        _cache = None
        _cache_mtime = None
        _load_failed_logged = False
