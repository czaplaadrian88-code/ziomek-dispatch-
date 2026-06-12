"""AUTON-01 — serializacja would_auto_assign/auto_block_reasons (LOCATION B).

Pola top-level w shadow_decisions obok auto_route — wzorzec
commit_divergence_redirect/best_effort_r6_redirect.
"""
from types import SimpleNamespace

from dispatch_v2 import shadow_dispatcher


def _min_result(**extra):
    r = SimpleNamespace(
        order_id="480400",
        verdict="PROPOSE",
        reason="ok",
        best=None,
        candidates=[],
        pickup_ready_at=None,
        restaurant="Testowa",
        delivery_address="Testowa 1",
        auto_route="ACK",
        auto_route_reason="",
        auto_route_context={},
    )
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def test_serializes_would_auto_assign_true():
    rec = shadow_dispatcher._serialize_result(
        _min_result(would_auto_assign=True, auto_block_reasons=[]),
        "ev1", 10.0)
    assert rec["would_auto_assign"] is True
    assert rec["auto_block_reasons"] == []


def test_serializes_block_reasons_list():
    blocks = ["czasowka", "scarcity_pool:2"]
    rec = shadow_dispatcher._serialize_result(
        _min_result(would_auto_assign=False, auto_block_reasons=blocks),
        "ev2", 10.0)
    assert rec["would_auto_assign"] is False
    assert rec["auto_block_reasons"] == blocks


def test_missing_fields_serialize_as_none():
    # result bez pól (stary rekord / KOORD bez classify) → None, nie KeyError
    rec = shadow_dispatcher._serialize_result(_min_result(), "ev3", 10.0)
    assert rec["would_auto_assign"] is None
    assert rec["auto_block_reasons"] is None


def test_record_json_roundtrip():
    import json
    rec = shadow_dispatcher._serialize_result(
        _min_result(would_auto_assign=False,
                    auto_block_reasons=["pos_not_informed:no_gps"]),
        "ev4", 10.0)
    parsed = json.loads(json.dumps(rec, ensure_ascii=False, default=str))
    assert parsed["auto_block_reasons"] == ["pos_not_informed:no_gps"]
