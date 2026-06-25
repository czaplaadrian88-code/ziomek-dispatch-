"""Floor ETA linii „Kandydaci" do realnego planowanego odbioru (Adrian 2026-06-25).

Case #483301 Piwo Kaczka Sushi → Rzemieślnicza 15a/12: best = Patryk (pre_shift,
zmiana 18:00). `eta_pickup_hhmm` = start zmiany 18:00, ale silnik planuje pickup
plan.pickup_at = 18:07 (po gotowości jedzenia 18:06). Linia kandydata pokazywała
18:00 (odbiór przed możliwym). Header JUŻ używał plan.pickup_at (Etap2 #472788) —
ten fix domyka bliźniaczy parytet header↔kandydat. Gated ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dispatch_v2 import telegram_approver as ta
from dispatch_v2.telegram_approver import _candidate_line_v2 as line
from dispatch_v2.telegram_approver import _cand_plan_pickup_hhmm


def _cand(**kw):
    base = dict(courier_id=75, name="Patryk", pos_source="gps",
                eta_pickup_hhmm="18:00", r6_bag_size=0)
    base.update(kw)
    return base


# ---------- unit: _candidate_line_v2 plan floor ----------

def test_plan_floors_preshift_below_plan():
    # pre_shift eta=start zmiany 18:00, plan realny 18:07 → ETA 18:07
    assert "ETA 18:07" in line(1, _cand(pos_source="pre_shift"), True, plan_hhmm="18:07")


def test_plan_does_not_lower_eta():
    # dojazd 18:30 > plan 18:07 → zostaje 18:30 (floor tylko podnosi, max)
    assert "ETA 18:30" in line(1, _cand(eta_pickup_hhmm="18:30"), True, plan_hhmm="18:07")


def test_plan_composes_with_committed_max():
    # committed 18:20 > plan 18:07; eta 18:00 → najpierw committed 18:20, plan no-op → 18:20
    assert "ETA 18:20" in line(1, _cand(), True, committed_hhmm="18:20", plan_hhmm="18:07")
    # plan 18:25 > committed 18:20 → eta podniesione do 18:25
    assert "ETA 18:25" in line(1, _cand(), True, committed_hhmm="18:20", plan_hhmm="18:25")


def test_plan_none_no_floor():
    assert "ETA 18:00" in line(1, _cand(pos_source="pre_shift"), True, plan_hhmm=None)


def test_dash_eta_untouched_by_plan():
    assert "ETA —" in line(1, _cand(eta_pickup_hhmm=None, eta_drive_hhmm=None),
                           True, plan_hhmm="18:07")


# ---------- unit: helper _cand_plan_pickup_hhmm ----------

def test_helper_parses_plan_pickup_to_warsaw_hhmm():
    c = {"plan": {"pickup_at": {"483301": "2026-06-25T16:07:47+00:00"}}}
    assert _cand_plan_pickup_hhmm(c, "483301") == "18:07"


def test_helper_none_when_missing_or_malformed():
    assert _cand_plan_pickup_hhmm({"plan": {"pickup_at": {}}}, "483301") is None
    assert _cand_plan_pickup_hhmm({}, "483301") is None
    assert _cand_plan_pickup_hhmm({"plan": {"pickup_at": {"483301": "garbage"}}}, "483301") is None
    assert _cand_plan_pickup_hhmm({"plan": {"pickup_at": {"483301": "x"}}}, "") is None


# ---------- integration: _format_proposal_v2 + flag toggle ----------

class _PlanFlag:
    """Monkey-patch ta.flag(): PROPOSAL_FORMAT_V2 zawsze True (test woła v2 wprost
    i tak), ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN = sterowana wartość."""
    def __init__(self, plan_floor_on: bool):
        self.on = plan_floor_on
        self._orig = None

    def __enter__(self):
        self._orig = ta.flag

        def fake(name, default=False):
            if name == "ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN":
                return self.on
            if name == "PROPOSAL_FORMAT_V2":
                return True
            return self._orig(name, default)

        ta.flag = fake
        return self

    def __exit__(self, *exc):
        ta.flag = self._orig


def _decision_483301(with_plan=True):
    # 18:06:47 / 18:07:47 Warsaw = 16:06:47 / 16:07:47 UTC
    best = {
        "courier_id": "75", "name": "Patryk", "score": 64.1,
        "pos_source": "pre_shift", "r6_bag_size": 0, "free_at_min": 0.0,
        "travel_min": 23.0, "eta_pickup_hhmm": "18:00",
        "effective_start_at": "2026-06-25T16:00:00+00:00",
    }
    if with_plan:
        best["plan"] = {"pickup_at": {"483301": "2026-06-25T16:07:47+00:00"},
                        "predicted_delivered_at": {"483301": "2026-06-25T16:22:39+00:00"}}
    return {
        "order_id": "483301",
        "restaurant": "Piwo Kaczka Sushi",
        "delivery_address": "Rzemieślnicza 15a/12",
        "best": best,
        "alternatives": [],
        "auto_route": "ACK",
        "pool_total_count": 6, "pool_feasible_count": 6,
        "pickup_ready_at": "2026-06-25T16:06:47+00:00",
    }


def test_v2_candidate_floored_to_plan_when_on():
    with _PlanFlag(True):
        out = ta._format_proposal_v2(_decision_483301(with_plan=True))
    assert "ETA 18:07" in out, out
    assert "ETA 18:00" not in out, out


def test_v2_candidate_raw_when_off():
    with _PlanFlag(False):
        out = ta._format_proposal_v2(_decision_483301(with_plan=True))
    assert "ETA 18:00" in out, out


def test_v2_no_plan_falls_back_to_pickup_ready():
    # brak plan.pickup_at → floor do gotowości jedzenia (18:06), nigdy 18:00
    with _PlanFlag(True):
        out = ta._format_proposal_v2(_decision_483301(with_plan=False))
    assert "ETA 18:06" in out, out
    assert "ETA 18:00" not in out, out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("OK", fn.__name__)
    print(f"ALL {len(fns)} PASS")
