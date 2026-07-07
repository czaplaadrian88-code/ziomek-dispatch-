"""SP-B2 (Bartek 2.0, 2026-06-11) — konsument map kalibracyjnych (shadow-first).

Dwie mapy generowane cronem przez tor narzędziowy (SESJA B, sprinty SP-B2-ETAQ
i SP-B2-PREPBIAS):

  1. dispatch_state/eta_quantile_map.json — kalibracja kwantylowa ETA
     (pred→real z eta_calibration_log; raport BARTEK_2.0 §4.1.4: bias -10..-25
     min przy pred>25). Konsumpcja: `travel_min_cal` obok `travel_min`
     w decyzjach (LOCATION A+B), flaga ENABLE_ETA_QUANTILE_SHADOW.

  2. dispatch_state/restaurant_prep_bias.json — tablica prep-bias
     restauracja×slot (deklaracja vs rzeczywistość, med +9-22 min; §3.1.5).
     Konsumpcja: `effective_ready_shadow = pickup_ready + bias` w decyzjach,
     flaga ENABLE_PREP_BIAS_SHADOW.

KONTRAKT FORMATU (uzgodnienie sesja A↔B):
eta_quantile_map.json:
    {"version": 1, "generated_at": iso, "buckets": [
        {"slot": <slot>, "pred_lo": float, "pred_hi": float,
         "p50": float, "p80": float, "n": int}, ...]}
    slot ∈ {"peak_lunch","high_risk","peak_dinner","off","all"} (time_slot_warsaw
    niżej; "all" = fallback bez podziału na sloty). Lookup: bucket z pasującym
    slotem i pred_lo <= pred < pred_hi; brak → slot "all"; brak → None.

restaurant_prep_bias.json:
    {"version": 1, "generated_at": iso,
     "global": {<slot>: {"bias_med": float, "n": int, "std": float}, ...},
     "restaurants": {<nazwa lower/strip>: {<slot>: {...jw...}, ...}, ...}}
    Lookup: restaurants[norm][slot] → global[slot] → None. Generator
    odpowiada za min n=30 per komórka (konsument tylko waliduje strukturę).

FAIL-SOFT: brak pliku / zły format / dowolny wyjątek → None (zero raise,
zero wpływu na decyzje). Mapy mogą nie istnieć — generatory powstają
równolegle. Hot-reload po mtime (wzorzec _load_restaurant_meta_cached).

⚠ SHADOW ONLY: effective_ready_shadow NIE wolno (bez osobnego flipa za ACK)
używać w feasibility/czasówkach — R-DECLARED-TIME (czas_kuriera ≥ deklaracja
restauracji) pozostaje nadrzędne dla deklaracji, bias to telemetria.
"""
from __future__ import annotations

import html
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
    _WARSAW = ZoneInfo("Europe/Warsaw")
except Exception:  # pragma: no cover — zoneinfo zawsze dostępne na py3.12
    _WARSAW = None

ETA_QUANTILE_MAP_PATH = os.environ.get(
    "ETA_QUANTILE_MAP_PATH",
    "/root/.openclaw/workspace/dispatch_state/eta_quantile_map.json",
)
PREP_BIAS_MAP_PATH = os.environ.get(
    "PREP_BIAS_MAP_PATH",
    "/root/.openclaw/workspace/dispatch_state/restaurant_prep_bias.json",
)
# W0.5 (advisory, werdykt E-7-GO): korekta ETA per-komórka floty (slot×solo/worek).
ETA_CELL_RESIDUAL_MAP_PATH = os.environ.get(
    "ETA_CELL_RESIDUAL_MAP_PATH",
    "/root/.openclaw/workspace/dispatch_state/eta_cell_residual_map.json",
)

SLOT_PEAK_LUNCH = "peak_lunch"      # 11-14 Warsaw (doktryna)
SLOT_HIGH_RISK = "high_risk"        # 14-17 Warsaw — strefa śmierci (mining H6/H10)
SLOT_PEAK_DINNER = "peak_dinner"    # 17-20 Warsaw (doktryna)
SLOT_OFF = "off"
SLOT_ALL = "all"                    # fallback w mapach (bez podziału)

_eta_cache: Dict[str, Any] = {"mtime": None, "data": None}
_bias_cache: Dict[str, Any] = {"mtime": None, "data": None}
_cell_resid_cache: Dict[str, Any] = {"mtime": None, "data": None}


def time_slot_warsaw(now: Optional[datetime] = None) -> str:
    """Slot czasowy Warsaw dla map kalibracyjnych i bucketów klasyfikatora.

    peak_lunch 11-14 / high_risk 14-17 / peak_dinner 17-20 / off reszta.
    Naive datetime traktowany jako UTC (konwencja pipeline'u).
    """
    try:
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        h = now.astimezone(_WARSAW).hour if _WARSAW is not None else now.hour
    except Exception:
        return SLOT_OFF
    if 11 <= h < 14:
        return SLOT_PEAK_LUNCH
    if 14 <= h < 17:
        return SLOT_HIGH_RISK
    if 17 <= h < 20:
        return SLOT_PEAK_DINNER
    return SLOT_OFF


def _load_cached(path: str, cache: Dict[str, Any]) -> Optional[dict]:
    """mtime-cached JSON load. Fail-soft → None. Brak pliku NIE cache'owany
    na sztywno (mapa może pojawić się w trakcie życia procesu)."""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        cache["mtime"] = None
        cache["data"] = None
        return None
    try:
        if cache["mtime"] != mt or cache["data"] is None:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            cache["data"] = data if isinstance(data, dict) else None
            cache["mtime"] = mt
        return cache["data"]
    except Exception:
        cache["mtime"] = mt   # nie młóć zepsutego pliku co tick
        cache["data"] = None
        return None


def _finite(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def eta_quantile_calibrate(
    pred_min: Any,
    now: Optional[datetime] = None,
    quantile: str = "p50",
) -> Optional[float]:
    """Skalibrowane travel_min z mapy kwantylowej. None gdy brak mapy/koszyka.

    Nigdy nie podnosi wyjątku; wynik clampowany ≥ 0.
    """
    pred = _finite(pred_min)
    if pred is None:
        return None
    data = _load_cached(ETA_QUANTILE_MAP_PATH, _eta_cache)
    if not data:
        return None
    try:
        buckets = data.get("buckets")
        if not isinstance(buckets, list):
            return None
        slot = time_slot_warsaw(now)
        for want_slot in (slot, SLOT_ALL):
            for b in buckets:
                if not isinstance(b, dict) or b.get("slot") != want_slot:
                    continue
                lo = _finite(b.get("pred_lo"))
                hi = _finite(b.get("pred_hi"))
                if lo is None or hi is None or not (lo <= pred < hi):
                    continue
                val = _finite(b.get(quantile))
                if val is not None:
                    return round(max(0.0, val), 1)
        return None
    except Exception:
        return None


def prep_bias_for(
    restaurant: Any,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """bias_med [min] dla (restauracja, slot) z tablicy prep-bias.

    Fallback: komórka restauracji → global per slot → None. Nazwa restauracji
    normalizowana strip().lower() (konwencja pipeline new_rest_norm).
    """
    data = _load_cached(PREP_BIAS_MAP_PATH, _bias_cache)
    if not data:
        return None
    try:
        slot = time_slot_warsaw(now)
        norm = str(restaurant or "").strip().lower()
        rests = data.get("restaurants")
        if norm and isinstance(rests, dict):
            cell = rests.get(norm)
            if isinstance(cell, dict):
                for want_slot in (slot, SLOT_ALL):
                    sub = cell.get(want_slot)
                    if isinstance(sub, dict):
                        v = _finite(sub.get("bias_med"))
                        if v is not None:
                            return round(v, 1)
        glob = data.get("global")
        if isinstance(glob, dict):
            for want_slot in (slot, SLOT_ALL):
                sub = glob.get(want_slot)
                if isinstance(sub, dict):
                    v = _finite(sub.get("bias_med"))
                    if v is not None:
                        return round(v, 1)
        return None
    except Exception:
        return None


def eta_cell_residual_correct(
    pred_min: Any,
    now: Optional[datetime] = None,
    is_bundle: bool = False,
    restaurant: Optional[str] = None,
) -> Optional[float]:
    """W0.5 (E-7-GO) + T2.2 (Tura 2): skorygowana predykcja ETA (min) = pred +
    shrunk residual per komórka floty (slot × solo/worek) [+ opcjonalna warstwa
    RESTAURACJI, addytywna po komórce — feature-mining: restauracja-nazwa to
    wtórny realny czynnik residualu, +~1,5pp OOS]. Korekta ADDYTYWNA na OBIETNICĘ
    (uczciwość); konsument NIE rusza bramki R6 (SOFT nie osłabia HARD).

    Warstwa restauracji stosowana TYLKO gdy podano `restaurant`, mapa ma sekcję
    `restaurants` i nazwa jest w mapie (fail-soft: nieznana restauracja = tylko
    komórka). None gdy brak mapy/komórki/pred niefinite — caller trzyma surowe pred."""
    pred = _finite(pred_min)
    if pred is None:
        return None
    data = _load_cached(ETA_CELL_RESIDUAL_MAP_PATH, _cell_resid_cache)
    if not data:
        return None
    try:
        slot = time_slot_warsaw(now)
        want_bundle = bool(is_bundle)
        cell_corr = None
        for c in data.get("cells", []):
            if not isinstance(c, dict):
                continue
            if c.get("slot") == slot and bool(c.get("bundle")) == want_bundle:
                resid = _finite(c.get("resid_min"))
                if resid is None:
                    return None
                w = _finite(c.get("weight"))
                w = 1.0 if w is None else max(0.0, min(1.0, w))
                cell_corr = w * resid
                break
        if cell_corr is None:
            return None  # brak komórki → brak korekty (nie zgadujemy globalem)
        rest_corr = 0.0
        if restaurant:
            rmap = data.get("restaurants") or {}
            # Parytet z generatorem (u źródła): tools/eta_cell_residual_build buduje
            # klucze `restaurants` przez _html.unescape(restaurant) (l.114) — nazwa
            # restauracji z panelu jest HTML-escaped ("Sweet Fit &amp; Eat",
            # "Restauracja Kumar&#039;s"), więc surowy rmap.get(str(restaurant))
            # chybiał (0 trafień na 3 restauracje z encjami). Ten sam unescape co
            # generator → klucz zgodny; czysta nazwa (bez encji) → unescape = no-op
            # (zero regresji, mapa nie zawiera encji). Warstwa RESTAURACJI jest
            # addytywna na OBIETNICĘ — feasibility/R6 NIETKNIĘTE.
            rentry = rmap.get(html.unescape(str(restaurant)))
            if isinstance(rentry, dict):
                rr = _finite(rentry.get("resid_min"))
                if rr is not None:
                    rw = _finite(rentry.get("weight"))
                    rw = 1.0 if rw is None else max(0.0, min(1.0, rw))
                    rest_corr = rw * rr
        return round(pred + cell_corr + rest_corr, 1)
    except Exception:
        return None


def reset_caches() -> None:
    """Testy: wyczyść cache map (izolacja między testami)."""
    _eta_cache["mtime"] = None
    _eta_cache["data"] = None
    _bias_cache["mtime"] = None
    _bias_cache["data"] = None
    _cell_resid_cache["mtime"] = None
    _cell_resid_cache["data"] = None
