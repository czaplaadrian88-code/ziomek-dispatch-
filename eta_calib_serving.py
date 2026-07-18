"""Serving kalibratora ETA per-kurier — WYŁĄCZNIE warstwa OBIETNIC (D6a, SHADOW).

OWNER_CONFIRMED D1-D7 (Adrian 2026-07-18): memory
`owner-decision-eta-calib-d1-d7-2026-07-18` + karta
`eod_drafts/2026-07-18/ETA_CALIB_OWNER_DECISION_CARD.md`.

Co robi: dla ZWYCIĘZCY decyzji liczy kalibrowane kwantyle obietnic (P80) z
artefaktów championa v2 (bootstrap D7) i dokłada **NOWE** metryki
`eta_calib_promise_*` do `best.metrics` (wzorzec #8 — pola OBOK, żadnej
podmiany pól karmiących decyzje). Auto-serializacja L1.1 zanosi je do
shadow_decisions.jsonl → parytet stary-vs-nowy na tych samych zleceniach
(cień 2 dni) liczy się offline z logu.

Czego NIE robi: nie dotyka feasibility/R6/scoringu/wyświetlanych czasów;
flip warstwy APPLY = osobny krok za końcowym ACK po cieniu.

Parytet cech z treningiem (krytyczne): `osrm_deliv_ff_min` = SUROWY czas
z lokalnego OSRM `/route` (bez mnożnika ruchu silnika!) — lustro
`tools/eta_calibration/features.OSRM.freeflow` (silnikowy `osrm_client.route`
dokłada traffic-mult → NIE nadaje się). Fail-soft wszędzie: każdy brak →
metryka `eta_calib_srv_skip` z powodem, nigdy wyjątek do lejka.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, Optional, Tuple

from dispatch_v2 import common as C

_STATE = "/root/.openclaw/workspace/dispatch_state"
CHAMPION_PATH = {
    "pickup": os.path.join(_STATE, "eta_calib_pickup_map.json"),
    "delivery": os.path.join(_STATE, "eta_calib_delivery_map.json"),
}
_OSRM_BASE = "http://127.0.0.1:5001"
_OSRM_TIMEOUT_S = 2.0
_OSRM_CACHE_MAX = 5000

_model_cache: Dict[str, Tuple[float, Any, str]] = {}   # leg -> (mtime, model, sha12)
_osrm_cache: Dict[str, Tuple[float, float]] = {}


def _load_model(leg: str):
    """(model, sha12) | (None, powód). Cache po mtime artefaktu; lazy import
    modeli (lightgbm/numpy dostępne w venv dispatch)."""
    path = CHAMPION_PATH[leg]
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None, "champion_missing"
    cached = _model_cache.get(leg)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]
    try:
        payload = json.load(open(path, encoding="utf-8"))
        if not payload.get("schema") or "runtime_model" not in payload:
            return None, "champion_legacy_schema"
        from dispatch_v2.tools.eta_calibration.models import model_from_artifact
        model = model_from_artifact(payload["runtime_model"])
        sha12 = str(payload.get("artifact_sha256", ""))[:12]
        _model_cache[leg] = (mtime, model, sha12)
        return model, sha12
    except Exception as e:  # noqa: BLE001 — fail-soft (obserwacyjny serving)
        return None, f"champion_load_err:{type(e).__name__}"


def _ff_raw(a, b) -> Optional[Tuple[float, float]]:
    """(dist_km, dur_min) SUROWE free-flow — lustro features.OSRM.freeflow
    (ten sam endpoint, format i zaokrąglenie klucza cache)."""
    if not a or not b:
        return None
    try:
        key = f"{a[0]:.4f},{a[1]:.4f};{b[0]:.4f},{b[1]:.4f}"
    except Exception:
        return None
    if key in _osrm_cache:
        return _osrm_cache[key]
    url = (f"{_OSRM_BASE}/route/v1/driving/"
           f"{a[1]:.5f},{a[0]:.5f};{b[1]:.5f},{b[0]:.5f}?overview=false")
    try:
        with urllib.request.urlopen(url, timeout=_OSRM_TIMEOUT_S) as r:
            data = json.loads(r.read())
        rt = data["routes"][0]
        val = (rt["distance"] / 1000.0, rt["duration"] / 60.0)
    except Exception:  # noqa: BLE001
        return None
    if len(_osrm_cache) >= _OSRM_CACHE_MAX:
        _osrm_cache.clear()
    _osrm_cache[key] = val
    return val


def _was_czasowka(order_event) -> int:
    try:
        co = (order_event or {}).get("czas_odbioru")
        return 1 if co is not None and float(co) >= 60 else 0
    except Exception:
        return 0


def attach_shadow_promise_metrics(result, order_event) -> None:
    """Dołóż eta_calib_promise_* do best.metrics (SHADOW). Nigdy nie podnosi."""
    if not C.decision_flag("ENABLE_ETA_CALIB_PROMISE_SHADOW"):
        return
    best = getattr(result, "best", None)
    m = getattr(best, "metrics", None)
    if best is None or not isinstance(m, dict):
        return
    try:
        ev = order_event or {}
        rest = ev.get("pickup_coords")
        drop = ev.get("delivery_coords")
        row = {
            "courier_id": str(getattr(best, "courier_id", "") or ""),
            "rest_lat": (rest or (None, None))[0],
            "rest_lon": (rest or (None, None))[1],
            "was_czasowka": _was_czasowka(ev),
        }
        shas = []
        mp, why_p = _load_model("pickup")
        if mp is None:
            m["eta_calib_srv_skip"] = f"pickup:{why_p}"
        else:
            q = mp.predict_quantiles(row)
            if q and q.get(0.8) is not None:
                m["eta_calib_promise_pickup_p80_min"] = round(float(q[0.8]), 2)
            shas.append(why_p)
        md, why_d = _load_model("delivery")
        if md is None:
            m["eta_calib_srv_skip"] = (m.get("eta_calib_srv_skip", "") +
                                       f"|delivery:{why_d}").strip("|")
        else:
            ff = _ff_raw(rest, drop)
            if ff is None:
                m["eta_calib_srv_skip"] = (m.get("eta_calib_srv_skip", "") +
                                           "|delivery:osrm_ff_unavailable").strip("|")
            else:
                drow = dict(row, osrm_deliv_km=ff[0], osrm_deliv_ff_min=ff[1])
                q = md.predict_quantiles(drow)
                if q and q.get(0.8) is not None:
                    m["eta_calib_promise_delivery_p80_min"] = round(float(q[0.8]), 2)
            shas.append(why_d)
        if shas:
            m["eta_calib_champion"] = "/".join(shas)
    except Exception as e:  # noqa: BLE001 — obserwacyjny: nigdy nie psuj emitu
        try:
            m["eta_calib_srv_skip"] = f"err:{type(e).__name__}"
        except Exception:
            pass
