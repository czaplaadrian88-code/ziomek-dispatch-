"""NO_GPS równe traktowanie (Adrian 2026-06-22): "bez GPS na równi z GPS, żadnych kar".

Flaga ENABLE_NO_GPS_EQUAL_TREATMENT:
  ON  → no_gps+empty NIE jest demote'owany (_demote_blind_empty go pomija) →
        konkuruje czystym score jak GPS (zostaje na topie gdy ma najwyższy score).
  OFF → legacy V3.16 demote zachowany (regresja #467189).
pre_shift/none zawsze demote (tylko no_gps wyłączony).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import dispatch_pipeline as dp  # noqa: E402


class FakeCand:
    def __init__(self, cid, score, pos_source, bag_size):
        self.courier_id = cid
        self.score = score
        self.metrics = {"pos_source": pos_source, "r6_bag_size": bag_size}


def _feasible():
    # no_gps idle ma NAJWYŻSZY score; informed niosą bagaż ze słabym score
    return [
        FakeCand("500", 110.0, "no_gps", 0),
        FakeCand("457", -6.0, "last_assigned_pickup", 1),
        FakeCand("179", -96.0, "gps", 3),
    ]


def test_flag_off_demotes_nogps_legacy(monkeypatch):
    monkeypatch.setattr(dp, "_no_gps_equal_on", lambda: False)
    monkeypatch.setattr(dp.C, "ENABLE_NO_GPS_EMPTY_DEMOTE", True, raising=False)
    res = dp._demote_blind_empty(_feasible(), order_id="T")
    assert res[0].courier_id == "457"    # informed promoted (legacy)
    assert res[-1].courier_id == "500"   # no_gps demoted last


def test_flag_on_nogps_treated_equal(monkeypatch):
    monkeypatch.setattr(dp, "_no_gps_equal_on", lambda: True)
    monkeypatch.setattr(dp.C, "ENABLE_NO_GPS_EMPTY_DEMOTE", True, raising=False)
    res = dp._demote_blind_empty(_feasible(), order_id="T")
    assert res[0].courier_id == "500"    # no_gps zostaje #1 (równy GPS), NIE demoted


def test_flag_on_preshift_still_demoted(monkeypatch):
    monkeypatch.setattr(dp, "_no_gps_equal_on", lambda: True)
    monkeypatch.setattr(dp.C, "ENABLE_NO_GPS_EMPTY_DEMOTE", True, raising=False)
    feasible = [
        FakeCand("600", 50.0, "pre_shift", 0),
        FakeCand("457", -6.0, "last_assigned_pickup", 1),
    ]
    res = dp._demote_blind_empty(feasible, order_id="T")
    assert res[0].courier_id == "457"    # pre_shift dalej demote
    assert res[-1].courier_id == "600"


def test_is_demotable_predicate(monkeypatch):
    ng = FakeCand("500", 110.0, "no_gps", 0)
    pre = FakeCand("600", 50.0, "pre_shift", 0)
    monkeypatch.setattr(dp, "_no_gps_equal_on", lambda: True)
    assert dp._is_demotable_blind_empty(ng) is False   # no_gps wyłączony gdy ON
    assert dp._is_demotable_blind_empty(pre) is True    # pre_shift dalej demotable
    monkeypatch.setattr(dp, "_no_gps_equal_on", lambda: False)
    assert dp._is_demotable_blind_empty(ng) is True     # legacy: demotable


def test_all_blind_guard_unchanged(monkeypatch):
    # sami no_gps (brak informed) → zostaw bez zmian niezależnie od flagi
    monkeypatch.setattr(dp, "_no_gps_equal_on", lambda: True)
    feasible = [FakeCand("500", 110.0, "no_gps", 0), FakeCand("501", 90.0, "no_gps", 0)]
    res = dp._demote_blind_empty(feasible, order_id="T")
    assert [c.courier_id for c in res] == ["500", "501"]
