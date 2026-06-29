"""Sprint 2 NO-GPS-EQUAL (Adrian 2026-06-29): duch przerzutu nie ripuje zleceń od
kuriera który PRACUJE (w grafiku, bez GPS/pre_shift/już jedzie). Ramię RATUNEK na
samym `a_cand is None` = fałszywy alarm (holder wypadł z hipotetycznej puli re-pickupu,
nie = spóźniony). Flaga ENABLE_REASSIGN_RESCUE_REQUIRE_HOLDER_ABSENT:
  OFF (default) → legacy: a_cand=None ⇒ a_late (rescue).
  ON  → a_cand=None ⇒ a_late TYLKO gdy holder NIEOBECNY w żywej flocie (a_in_fleet=False);
        holder pracujący bez zmierzonego R6>próg → NIE ratujemy (rescue_suppressed_working).
"""
import os
import contextlib
from datetime import datetime, timezone, timedelta
from dispatch_v2.tools import reassignment_forward_shadow as R

BASE = datetime(2026, 6, 29, 14, 0, tzinfo=timezone.utc)
FLAG = "ENABLE_REASSIGN_RESCUE_REQUIRE_HOLDER_ABSENT"


class _Plan:
    def __init__(self, pred, pick):
        self.predicted_delivered_at = pred
        self.pickup_at = pick


class _Cand:
    def __init__(self, cid, deliver_min, pick_min=0):
        self.courier_id = cid
        self.plan = _Plan({"O1": BASE + timedelta(minutes=deliver_min)},
                          {"O1": BASE + timedelta(minutes=pick_min)})


@contextlib.contextmanager
def _env(v):
    old = os.environ.get(FLAG)
    if v is None:
        os.environ.pop(FLAG, None)
    else:
        os.environ[FLAG] = v
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(FLAG, None)
        else:
            os.environ[FLAG] = old


def _gate(a, b, a_in_fleet, a_pos="last_assigned_pickup", b_pos="gps"):
    return R._quality_gate(a, b, "O1", a_pos, b_pos, "123", "370",
                           b_bag=0, a_in_fleet=a_in_fleet)


def test_off_legacy_working_holder_still_rescued():
    # a_cand=None (niewykonalny re-pickup) + holder PRACUJE; b on-time. OFF → legacy rescue.
    with _env(None):
        r = _gate(None, _Cand("370", 10), a_in_fleet=True)
    assert r["a_late"] is True
    assert r["quality_reassign"] is True
    assert r["quality_rescue_suppressed_working"] is False


def test_on_working_holder_suppressed():
    # ON → holder pracujący (a_in_fleet=True), brak zmierzonego R6 → NIE ratujemy.
    with _env("1"):
        r = _gate(None, _Cand("370", 10), a_in_fleet=True)
    assert r["a_late"] is False
    assert r["quality_reassign"] is False
    assert r["quality_rescue_suppressed_working"] is True
    assert "NIE przerzucamy" in r["quality_reason"]


def test_on_genuinely_absent_holder_still_rescued():
    # ON → holder NIEOBECNY w żywej flocie (a_in_fleet=False) → realnie zniknął → rescue zostaje.
    with _env("1"):
        r = _gate(None, _Cand("370", 10), a_in_fleet=False)
    assert r["a_late"] is True
    assert r["quality_reassign"] is True
    assert r["quality_rescue_suppressed_working"] is False


def test_on_measured_late_holder_still_rescued():
    # ON → holder feasible ale R6=40>35 (zmierzone spóźnienie) → rescue zostaje.
    with _env("1"):
        r = _gate(_Cand("123", 40), _Cand("370", 10), a_in_fleet=True)
    assert r["a_late"] is True
    assert r["quality_reassign"] is True
    assert r["quality_rescue_suppressed_working"] is False


def test_on_vs_off_differ_working_holder():
    with _env(None):
        off = _gate(None, _Cand("370", 10), a_in_fleet=True)["quality_reassign"]
    with _env("1"):
        on = _gate(None, _Cand("370", 10), a_in_fleet=True)["quality_reassign"]
    assert off is True and on is False
