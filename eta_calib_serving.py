"""Serving kalibratora ETA per-kurier — WYŁĄCZNIE warstwa OBIETNIC (D6a, SHADOW).

OWNER_CONFIRMED D1-D7 (Adrian 2026-07-18): memory
`owner-decision-eta-calib-d1-d7-2026-07-18` + karta
`eod_drafts/2026-07-18/ETA_CALIB_OWNER_DECISION_CARD.md`.

Co robi w D6a: dla ZWYCIĘZCY decyzji liczy kalibrowane kwantyle obietnic z
artefaktów championa v2 (bootstrap D7) i dokłada **NOWE** metryki
`eta_calib_promise_*` do `best.metrics` (wzorzec #8 — pola OBOK, żadnej
podmiany pól karmiących decyzje). Auto-serializacja L1.1 zanosi je do
shadow_decisions.jsonl → parytet stary-vs-nowy na tych samych zleceniach
(cień 2 dni) liczy się offline z logu.

Co robi w K6: ten sam kanoniczny serving zwraca wersjonowaną parę pickup
P50/P80 dla całej puli `decision_eta.v1`; logger tylko serializuje wynik.

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
import math
import os
import urllib.request
from typing import Any, Dict, Iterable, Optional, Tuple

from dispatch_v2 import common as C

_STATE = "/root/.openclaw/workspace/dispatch_state"
CHAMPION_PATH = {
    "pickup": os.path.join(_STATE, "eta_calib_pickup_map.json"),
    "delivery": os.path.join(_STATE, "eta_calib_delivery_map.json"),
}
_OSRM_BASE = "http://127.0.0.1:5001"
_OSRM_TIMEOUT_S = 2.0
_OSRM_CACHE_MAX = 5000
PICKUP_PREDICTION_VERSION = "eta_pickup_quantiles.v1"

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
        event = order_event or {}
        prep = event.get("czas_odbioru")
        if prep is None:
            prep = event.get("prep_minutes")
        return 1 if C.is_czasowka_prep(prep) else 0
    except Exception:
        return 0


def predict_pickup_quantiles_batch(
    candidates: Iterable[Any],
    order_event,
) -> list[tuple[Optional[dict], Optional[str]]]:
    """Return one versioned P50/P80 contract for each decision candidate.

    Pickup models predict slip against ``czas_kuriera``.  ``pred_op`` is the
    point prediction (P50) required by K6; ``p80`` is the operational
    quantile.  Model load/stat happens once for the whole pool.  The function
    is fail-soft and never mutates candidates.
    """
    pool = list(candidates)
    if not pool:
        return []
    try:
        event = order_event or {}
        pickup = event.get("pickup_coords")
        model, model_version = _load_model("pickup")
        if model is None:
            return [(None, str(model_version)) for _ in pool]
        from dispatch_v2.tools.eta_calibration import models
        results: list[tuple[Optional[dict], Optional[str]]] = []
        for candidate in pool:
            try:
                row = {
                    "courier_id": str(
                        getattr(candidate, "courier_id", "") or ""
                    ),
                    "rest_lat": (pickup or (None, None))[0],
                    "rest_lon": (pickup or (None, None))[1],
                    "was_czasowka": _was_czasowka(event),
                }
                quantiles = model.predict_quantiles(row)
                if not isinstance(quantiles, dict):
                    results.append((None, "quantiles_unavailable"))
                    continue
                p50 = float(quantiles[0.5])
                p80 = float(quantiles[0.8])
                if not math.isfinite(p50) or not math.isfinite(p80):
                    results.append((None, "quantiles_non_finite"))
                    continue
                if p80 < p50:
                    results.append((None, "quantiles_not_monotonic"))
                    continue
                results.append(({
                    "pred_op": round(p50, 4),
                    "p80": round(p80, 4),
                    "prediction_version": PICKUP_PREDICTION_VERSION,
                    "prediction_provenance": {
                        "producer": (
                            "eta_calib_serving.predict_pickup_quantiles_batch"
                        ),
                        "model_artifact_sha256_12": str(model_version),
                        "feature_contract_version": (
                            models.FEATURE_CONTRACT_VERSION
                        ),
                        "target": "pickup_slip_vs_czas_kuriera_min",
                        "quantiles": {"pred_op": 0.5, "p80": 0.8},
                    },
                }, None))
            except Exception as exc:  # one bad candidate cannot drop the pool
                results.append((
                    None, f"prediction_err:{type(exc).__name__}"
                ))
        return results
    except Exception as exc:  # noqa: BLE001 - instrumentation is fail-soft
        return [
            (None, f"prediction_err:{type(exc).__name__}") for _ in pool
        ]


def predict_pickup_quantiles(
    candidate,
    order_event,
) -> tuple[Optional[dict], Optional[str]]:
    """Single-candidate adapter over the canonical batch producer."""
    return predict_pickup_quantiles_batch([candidate], order_event)[0]


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
        pickup_prediction, why_p = predict_pickup_quantiles(best, ev)
        if pickup_prediction is None:
            m["eta_calib_srv_skip"] = f"pickup:{why_p}"
        else:
            m["eta_calib_promise_pickup_p50_min"] = round(
                float(pickup_prediction["pred_op"]), 2
            )
            m["eta_calib_promise_pickup_p80_min"] = round(
                float(pickup_prediction["p80"]), 2
            )
            pickup_provenance = pickup_prediction["prediction_provenance"]
            m["eta_calib_promise_pickup_model_version"] = (
                pickup_provenance["model_artifact_sha256_12"]
            )
            m["eta_calib_promise_contract_version"] = (
                pickup_prediction["prediction_version"]
            )
            shas.append(pickup_provenance["model_artifact_sha256_12"])
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
