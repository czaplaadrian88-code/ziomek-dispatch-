"""A8-2 — integralność puli ``best + alternatives`` w shadow_decisions.

Serializer nie może zakładać, że ``PipelineResult.best`` jest pierwszym
elementem ``candidates``. To założenie łamią trzy realne ścieżki selekcji:
OBJM best-effort, solo_fallback i no_solo. Kontrakt ledgera: wybrany kurier
w ``best`` oraz każdy niewybrany kurier dokładnie raz w ``alternatives``.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dispatch_v2 import eta_calibration_logger as ETA
from dispatch_v2 import shadow_dispatcher as SD


OID = "a8-2-order"
TS = datetime(2026, 7, 19, 9, 0, tzinfo=timezone.utc)


def _plan(cid: str):
    predicted = TS + timedelta(minutes=20 + int(cid))
    return SimpleNamespace(
        sequence=[("pickup", OID), ("delivery", OID)],
        total_duration_min=20.0,
        strategy="test",
        sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times={OID: 20.0},
        predicted_delivered_at={OID: predicted},
        pickup_at={OID: TS + timedelta(minutes=5)},
    )


def _candidate(cid: str, *, verdict: str = "MAYBE", solo: bool = False):
    return SimpleNamespace(
        courier_id=cid,
        name=f"Courier-{cid}",
        score=float(cid),
        plan=_plan(cid),
        feasibility_verdict=verdict,
        feasibility_reason="solo_fallback" if solo else "test",
        best_effort=False,
        metrics={"courier_id": cid, "solo_fallback": solo},
    )


def _result(best, candidates):
    return SimpleNamespace(
        order_id=OID,
        restaurant="Test",
        delivery_address="Test",
        verdict="PROPOSE" if best is not None else "KOORD",
        reason="test",
        best=best,
        candidates=candidates,
        pickup_ready_at=TS,
    )


def _serialize(best, candidates):
    return SD._serialize_result(_result(best, candidates), "event-a8-2", 1.0)


def _alternative_cids(record):
    return [str(c["courier_id"]) for c in record["alternatives"]]


def test_winner_first_path_is_byte_identical_to_legacy_slice():
    """Zwykła ścieżka zachowuje stary wynik bajt-w-bajt."""
    best, alt1, alt2 = _candidate("100"), _candidate("200"), _candidate("300")
    record = _serialize(best, [best, alt1, alt2])
    legacy_alternatives = [SD._serialize_candidate(c) for c in [alt1, alt2]]
    legacy_record = {**record, "alternatives": legacy_alternatives}

    assert json.dumps(record, sort_keys=True) == json.dumps(legacy_record, sort_keys=True)


def test_objm_winner_from_middle_does_not_drop_original_leader():
    """OBJM może przestawić best bez przeniesienia go na czoło listy."""
    original, best, tail = _candidate("100"), _candidate("200"), _candidate("300")
    record = _serialize(best, [original, best, tail])

    assert record["best"]["courier_id"] == "200"
    assert _alternative_cids(record) == ["100", "300"]


def test_solo_fallback_excludes_rejected_twin_of_selected_courier():
    """Solo best jest nowym obiektem; odrzucony wariant tego cid nie jest altem."""
    other = _candidate("100", verdict="NO")
    rejected_twin = _candidate("200", verdict="NO")
    third = _candidate("300", verdict="NO")
    solo_best = _candidate("200", solo=True)
    record = _serialize(solo_best, [other, rejected_twin, third])

    assert record["best"]["courier_id"] == "200"
    assert _alternative_cids(record) == ["100", "300"]


def test_no_solo_best_none_keeps_first_candidate():
    """KOORD no_solo ma best=None, więc cała oceniona pula jest alternatywą."""
    first, second = _candidate("100", verdict="NO"), _candidate("200", verdict="NO")
    record = _serialize(None, [first, second])

    assert record["best"] is None
    assert _alternative_cids(record) == ["100", "200"]


def test_duplicate_non_winner_cid_is_serialized_once_in_original_order():
    """Powtórzony wariant niewybranego kuriera nie dubluje puli ledgera."""
    best = _candidate("200")
    first = _candidate("100")
    duplicate = _candidate("100", verdict="NO")
    tail = _candidate("300")
    record = _serialize(best, [first, best, duplicate, tail])

    assert _alternative_cids(record) == ["100", "300"]


def test_canonical_numeric_cid_excludes_selected_twin():
    """Kanon identity traktuje 200 i 200.0 jako tego samego kuriera."""
    best = _candidate(200)
    selected_twin = _candidate(200.0, verdict="NO")
    other = _candidate("300")
    record = _serialize(best, [selected_twin, other])

    assert _alternative_cids(record) == ["300"]


def test_missing_cid_uses_object_identity_without_collapsing_candidates():
    """Błędna granica CID nie może po cichu zgubić dwóch różnych obiektów."""
    best = _candidate("200")
    best.courier_id = None
    other_unknown = _candidate("300")
    other_unknown.courier_id = None
    record = _serialize(best, [best, other_unknown])

    assert len(record["alternatives"]) == 1
    assert record["alternatives"][0]["courier_id"] is None
    assert record["alternatives"][0]["name"] == "Courier-300"


def test_eta_calibration_join_finds_candidate_previously_lost_by_slice():
    """Po fixie realny kurier z candidates[0] daje matched_courier=True."""
    real, best, tail = _candidate("100"), _candidate("200"), _candidate("300")
    record = _serialize(best, [real, best, tail])

    picked = ETA.pick_prediction(
        OID,
        real_cid="100",
        delivered_at=TS + timedelta(hours=1),
        shadow_recs=[(TS, record)],
    )

    assert picked is not None
    assert picked[2]["courier_id"] == "100"
    assert picked[3] is True
