"""W0.5 — korekta ETA per-komórka floty (advisory Faza 6.2, werdykt E-7-GO).

Testuje generator mapy (slot×solo/worek, shrinkage) + konsument
`calib_maps.eta_cell_residual_correct`: parytet slotów, kierunek korekty,
fail-soft (brak mapy/komórki → None), izolacja OFF (mapa None = brak korekty).
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dispatch_v2 import calib_maps as CM
from dispatch_v2.tools import eta_cell_residual_build as B

_WAW = ZoneInfo("Europe/Warsaw")


def _synthetic_log():
    """Rekordy eta_calibration: solo niedoszacowane (real=pred+6), worki
    przeszacowane (real=pred-3), pełne peak_lunch (12:xx Warsaw = h=12)."""
    rows = []
    for i in range(60):
        rows.append({"predicted_delivery_min": 20.0, "real_delivery_min": 26.0,
                     "hour_warsaw": 12, "is_bundle": False})
        rows.append({"predicted_delivery_min": 30.0, "real_delivery_min": 27.0,
                     "hour_warsaw": 12, "is_bundle": True})
    return rows


def _write(tmp_path, rows):
    p = tmp_path / "src.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(p)


def _install_map(tmp_path, monkeypatch, m):
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(CM, "ETA_CELL_RESIDUAL_MAP_PATH", str(mp))
    CM.reset_caches()


def test_generator_cells_direction(tmp_path):
    m = B.build_map(source=_write(tmp_path, _synthetic_log()), min_n=20, shrink_k=15)
    cells = {(c["slot"], c["bundle"]): c for c in m["cells"]}
    assert cells[("peak_lunch", False)]["resid_min"] == 6.0   # solo niedoszacowanie
    assert cells[("peak_lunch", True)]["resid_min"] == -3.0   # worek przeszacowanie
    assert cells[("peak_lunch", False)]["n"] == 60


def test_consumer_applies_shrunk_residual(tmp_path, monkeypatch):
    m = B.build_map(source=_write(tmp_path, _synthetic_log()), min_n=20, shrink_k=15)
    _install_map(tmp_path, monkeypatch, m)
    now = datetime(2026, 7, 1, 12, 30, tzinfo=_WAW)  # peak_lunch
    w = 60 / (60 + 15)  # 0.8
    assert CM.eta_cell_residual_correct(20.0, now, is_bundle=False) == round(20 + w * 6.0, 1)
    assert CM.eta_cell_residual_correct(20.0, now, is_bundle=True) == round(20 + w * -3.0, 1)


def test_restaurant_layer_additive(tmp_path, monkeypatch):
    """T2.2: warstwa restauracji dokłada się PO korekcie komórki (addytywnie).
    Wolna restauracja (residual +) → korekta wyższa niż sama komórka."""
    rows = _synthetic_log()  # peak_lunch solo resid +6, worek -3
    # dołóż restaurację 'Wolna' z dodatkowym residualem PO komórce
    for i in range(40):
        rows.append({"predicted_delivery_min": 20.0, "real_delivery_min": 32.0,
                     "hour_warsaw": 12, "is_bundle": False, "restaurant": "Wolna"})
    p = tmp_path / "src.jsonl"
    p.write_text("\n".join(__import__("json").dumps(r) for r in rows), encoding="utf-8")
    m = B.build_map(source=str(p), min_n=20, shrink_k=15)
    assert "Wolna" in m["restaurants"]
    _install_map(tmp_path, monkeypatch, m)
    now = datetime(2026, 7, 1, 12, 30, tzinfo=_WAW)
    without = CM.eta_cell_residual_correct(20.0, now, is_bundle=False)
    with_rest = CM.eta_cell_residual_correct(20.0, now, is_bundle=False, restaurant="Wolna")
    assert with_rest > without  # restauracja-wolna dokłada dodatni residual
    # nieznana restauracja = tylko komórka (fail-soft)
    assert CM.eta_cell_residual_correct(20.0, now, is_bundle=False, restaurant="NieMa") == without


def test_restaurant_bridges_excluded(tmp_path):
    rows = []
    for i in range(40):
        rows.append({"predicted_delivery_min": 20.0, "real_delivery_min": 45.0,
                     "hour_warsaw": 12, "is_bundle": False, "restaurant": "Dr Tusz"})
        rows.append({"predicted_delivery_min": 20.0, "real_delivery_min": 26.0,
                     "hour_warsaw": 12, "is_bundle": False, "restaurant": "Zwykla"})
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(__import__("json").dumps(r) for r in rows), encoding="utf-8")
    m = B.build_map(source=str(p), min_n=20, shrink_k=15)
    assert "Dr Tusz" not in m["restaurants"]  # most wykluczony z warstwy restauracji
    assert "Zwykla" in m["restaurants"]


def test_consumer_none_when_no_map(monkeypatch, tmp_path):
    monkeypatch.setattr(CM, "ETA_CELL_RESIDUAL_MAP_PATH", str(tmp_path / "nope.json"))
    CM.reset_caches()
    now = datetime(2026, 7, 1, 12, 30, tzinfo=_WAW)
    assert CM.eta_cell_residual_correct(20.0, now, is_bundle=False) is None  # brak mapy = brak korekty


def test_consumer_none_when_cell_missing(tmp_path, monkeypatch):
    # mapa tylko peak_lunch → zapytanie o off (h=2) → None (nie zgadujemy)
    m = B.build_map(source=_write(tmp_path, _synthetic_log()), min_n=20, shrink_k=15)
    _install_map(tmp_path, monkeypatch, m)
    now = datetime(2026, 7, 1, 2, 30, tzinfo=_WAW)  # off
    assert CM.eta_cell_residual_correct(20.0, now, is_bundle=False) is None


def test_consumer_none_on_bad_pred(tmp_path, monkeypatch):
    m = B.build_map(source=_write(tmp_path, _synthetic_log()), min_n=20, shrink_k=15)
    _install_map(tmp_path, monkeypatch, m)
    now = datetime(2026, 7, 1, 12, 30, tzinfo=_WAW)
    assert CM.eta_cell_residual_correct(None, now) is None
    assert CM.eta_cell_residual_correct("x", now) is None


# ── efekt flagi ENABLE_ETA_CELL_RESIDUAL_CORRECTION w rekordzie decyzji (ON≠OFF) ──

def test_flag_effect_on_serialized_record(monkeypatch):
    """Flaga ENABLE_ETA_CELL_RESIDUAL_CORRECTION zmienia serializowany rekord:
    `eta_cell_correction_flag` odzwierciedla stan (shadow-first; aktywne zastosowanie
    do obietnicy = deploy). Korekta shadow (`eta_cell_corrected_min`) liczona zawsze."""
    from datetime import timezone
    from dispatch_v2 import dispatch_pipeline as DP
    from dispatch_v2 import shadow_dispatcher as SD

    class _Plan:
        def __init__(self, pd):
            self.predicted_delivered_at = pd
            self.sequence = ["1"]
            self.total_duration_min = 12.0
            self.strategy = "solo"
            self.sla_violations = 0
            self.per_order_delivery_times = None
            self.pickup_at = {}
            self.osrm_fallback_used = False

    class _Cand:
        def __init__(self, pd):
            self.courier_id = "447"; self.name = "K"; self.score = 1.0
            self.best_effort = False
            self.feasibility_verdict = "MAYBE"; self.feasibility_reason = ""
            self.metrics = {}
            self.plan = _Plan(pd)

    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    best = _Cand({"9": now.replace(hour=12, minute=25)})
    res = DP.PipelineResult(order_id="9", verdict="PROPOSE", reason="r", best=best,
                            candidates=[best], pickup_ready_at=now, restaurant="R")

    def _flag(on):
        def f(name, default=None):
            if name == "ENABLE_ETA_CELL_RESIDUAL_CORRECTION":
                return on
            return default if default is not None else False
        return f

    monkeypatch.setattr(SD.C, "flag", _flag(False))
    rec_off = SD._serialize_result(res, "e1", 1.0)
    monkeypatch.setattr(SD.C, "flag", _flag(True))
    rec_on = SD._serialize_result(res, "e2", 1.0)
    assert rec_off["eta_cell_correction_flag"] is False
    assert rec_on["eta_cell_correction_flag"] is True
    assert "eta_cell_corrected_min" in rec_off  # obserwacja liczona zawsze
