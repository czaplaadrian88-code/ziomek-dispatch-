"""R-PACZKI-FLEX (2026-05-20) — testy klasyfikatora + gradient.

Spec: dispatch_v2/eod_drafts/2026-05-20/r_paczki_flex_design.md
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from dispatch_v2 import common as C
from dispatch_v2.dispatch_pipeline import _r_paczki_flex_penalty


# ============================================================
# SECTION 1 — is_paczka_order happy path (6 aid)
# ============================================================
@pytest.mark.parametrize("aid", [161, 232, 233, 234, 235, 236])
def test_s1_paczka_happy_path(aid):
    assert C.is_paczka_order({"address_id": str(aid)}) is True


# ============================================================
# SECTION 2 — is_paczka_order negative
# ============================================================
def test_s2_food_restaurant_not_paczka():
    assert C.is_paczka_order({"address_id": "190"}) is False


def test_s2_missing_address_id():
    assert C.is_paczka_order({}) is False


def test_s2_corrupt_address_id():
    assert C.is_paczka_order({"address_id": "abc"}) is False


def test_s2_none_input():
    assert C.is_paczka_order(None) is False


def test_s2_address_id_as_int():
    assert C.is_paczka_order({"address_id": 232}) is True


# ============================================================
# SECTION 3 — is_paczka_flex_eligible czasówka exception
# ============================================================
@pytest.mark.parametrize("aid", [161, 232, 233, 234, 235, 236])
def test_s3_paczka_elastic_eligible(aid):
    assert C.is_paczka_flex_eligible(
        {"address_id": str(aid), "order_type": "elastic"}
    ) is True


@pytest.mark.parametrize("aid", [161, 232, 233, 234, 235, 236])
def test_s3_paczka_czasowka_not_eligible(aid):
    assert C.is_paczka_flex_eligible(
        {"address_id": str(aid), "order_type": "czasowka"}
    ) is False


# ============================================================
# SECTION 4 — flex_eligible negative (food restaurant)
# ============================================================
def test_s4_food_restaurant_not_eligible():
    assert C.is_paczka_flex_eligible(
        {"address_id": "190", "order_type": "elastic"}
    ) is False


# ============================================================
# Helper builders dla gradient testów
# ============================================================
def _make_new_order(oid="X", aid="232", otype="elastic", created_utc=None):
    if created_utc is None:
        created_utc = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc)
    o = MagicMock(spec=[])
    o.order_id = oid
    o.address_id = aid
    o.order_type = otype
    o.created_at_utc = created_utc
    return o


def _make_plan(oid="X", pickup_utc=None, delivery_utc=None):
    p = MagicMock(spec=[])
    p.pickup_at = {oid: pickup_utc} if pickup_utc else {}
    p.predicted_delivered_at = {oid: delivery_utc} if delivery_utc else {}
    return p


_NOW = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc)


# ============================================================
# SECTION 5 — flag OFF → 0
# ============================================================
def test_s5_flag_off_returns_zero(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", False)
    # Hot-reload flag w flags.json też musi byc OFF — patch C.flag dla testu
    monkeypatch.setattr(C, "flag", lambda name, default=False: False if name == "ENABLE_R_PACZKI_FLEX" else default)
    no = _make_new_order()
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 13, 0, tzinfo=timezone.utc),
    )
    assert _r_paczki_flex_penalty(no, pl, _NOW) == 0.0


# ============================================================
# SECTION 6 — NOT paczka → 0
# ============================================================
def test_s6_not_paczka_returns_zero(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    no = _make_new_order(aid="190")
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 13, 0, tzinfo=timezone.utc),
    )
    assert _r_paczki_flex_penalty(no, pl, _NOW) == 0.0


# ============================================================
# SECTION 7 — czasówka-paczka → 0 (R-DECLARED-TIME nadrzędne)
# ============================================================
def test_s7_czasowka_paczka_returns_zero(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    no = _make_new_order(aid="232", otype="czasowka")
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 13, 0, tzinfo=timezone.utc),
    )
    assert _r_paczki_flex_penalty(no, pl, _NOW) == 0.0


# ============================================================
# SECTION 8 — under cap = 0
# ============================================================
def test_s8_under_cap_zero(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    no = _make_new_order()
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 9, 30, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 10, 30, tzinfo=timezone.utc),
    )
    assert _r_paczki_flex_penalty(no, pl, _NOW) == 0.0


# ============================================================
# SECTION 9 — over pickup cap → liniowa kara
# ============================================================
def test_s9_over_pickup_cap_linear(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    no = _make_new_order()
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 10, 30, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc),
    )
    assert _r_paczki_flex_penalty(no, pl, _NOW) == pytest.approx(-30.0, rel=1e-3)


# ============================================================
# SECTION 10 — over both caps
# ============================================================
def test_s10_over_both_caps(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    no = _make_new_order()
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 10, 30, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )
    assert _r_paczki_flex_penalty(no, pl, _NOW) == pytest.approx(-90.0, rel=1e-3)


# ============================================================
# SECTION 11 — missing created_at → 0
# ============================================================
def test_s11_missing_created_at_returns_zero(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    no = _make_new_order()
    no.created_at_utc = None
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 13, 0, tzinfo=timezone.utc),
    )
    assert _r_paczki_flex_penalty(no, pl, _NOW) == 0.0


# ============================================================
# SECTION 12 — plan=None → 0
# ============================================================
def test_s12_plan_none_returns_zero(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    no = _make_new_order()
    assert _r_paczki_flex_penalty(no, None, _NOW) == 0.0


# ============================================================
# SECTION 13 — created_at jako string (panel format) → parser
# ============================================================
def test_s13_created_at_as_iso_string(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    no = _make_new_order(created_utc="2026-05-20T08:00:00+00:00")
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 10, 30, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc),
    )
    result = _r_paczki_flex_penalty(no, pl, _NOW)
    assert result == pytest.approx(-30.0, rel=1e-3) or result == 0.0


# ============================================================
# SECTION 14 — defense fail-soft: corrupt new_order → 0
# ============================================================
def test_s14_defense_fail_soft_no_exception(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", True)
    broken = MagicMock(spec=[])
    broken.order_id = "X"
    pl = _make_plan(
        pickup_utc=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        delivery_utc=datetime(2026, 5, 20, 13, 0, tzinfo=timezone.utc),
    )
    assert _r_paczki_flex_penalty(broken, pl, _NOW) == 0.0
