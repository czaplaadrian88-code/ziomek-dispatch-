"""A8-2 twin sweep — czasówka `best + alternatives` identity dedup (2026-07-19).

``czasowka_scheduler._eval_czasowka_impl`` had the identical
``result.candidates[1:]`` position-assumption bug as ``shadow_dispatcher``
(A8-2, commit 863d11c) in 6 dict-construction sites, fed by the same
``core.decide._decide()`` producer. Two real consumers read
``result.get("alternatives")`` directly (not the shielded
``all_candidates_for_proactive``): ``_format_koord_alert`` (Telegram KOORD
"Top 3 odrzuconych") and the ``candidate_logger`` observability hook.

Mirrors the three selection-path scenarios from
``tests/test_a8_alternatives_integrity_2026_07_19.py`` through the real
``eval_czasowka()`` entry point (mins=35, FORCE_ASSIGN/KOORD window — the
only window where ``best`` and ``alternatives`` are both populated from a
raw ``candidates`` list rather than ``[]``), plus a dedicated
``_format_koord_alert`` regression for the Telegram-facing symptom.
"""
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

os.environ.setdefault("ENABLE_V324B_CZASOWKA_SCHEDULER", "1")
os.environ.setdefault("ENABLE_V324A_SCHEDULE_INTEGRATION", "1")

import pytest

from dispatch_v2 import czasowka_scheduler as CS
from dispatch_v2.identity.candidate_pool import alternative_candidates

WARSAW = ZoneInfo("Europe/Warsaw")

# pickup 12:00 Warsaw, now 11:25 Warsaw (09:25 UTC) = 35min to pickup, inside
# the FORCE_ASSIGN window (mins <= V324B_CZASOWKA_FORCE_ASSIGN_MIN=40) — the
# branch pair (lines ~349-368) that emits a raw, non-empty candidates pool.
_NOW_35MIN_UTC = datetime(2026, 7, 19, 9, 25, tzinfo=timezone.utc)


def _candidate(cid, *, score=50.0, verdict="MAYBE", name=None):
    return SimpleNamespace(
        courier_id=cid,
        name=name or f"Courier-{cid}",
        score=score,
        feasibility_verdict=verdict,
        feasibility_reason="test",
        plan=None,
        metrics={"km_to_pickup": 1.0, "v319h_bug1_drop_proximity_factor": 0.5},
    )


def _order_state(pickup_warsaw_iso="2026-07-19T12:00:00+02:00"):
    return {
        "pickup_at_warsaw": pickup_warsaw_iso,
        "prep_minutes": 90,
        "courier_id": "26",
        "restaurant": "FakeResto",
        "pickup_address": "Fake Rest 1",
        "pickup_city": "Białystok",
        "delivery_address": "Fake Addr 1",
        "delivery_city": "Białystok",
        "status_id": 2,
        "first_seen": "2026-07-19T09:00:00+00:00",
        "address_id": "999",
        "pickup_coords": [53.13, 23.17],
        "delivery_coords": [53.14, 23.18],
    }


class _FakeResult:
    def __init__(self, candidates, best):
        self.candidates = candidates
        self.best = best


@pytest.fixture(autouse=True)
def _hermetic_fleet(monkeypatch):
    """No real courier I/O — fake ``_decide`` (per test) ignores the fleet anyway."""
    monkeypatch.setattr(CS.courier_resolver, "dispatchable_fleet", lambda *a, **k: [])


def _eval_with(monkeypatch, candidates, best, now_utc=_NOW_35MIN_UTC):
    fake_result = _FakeResult(candidates, best)
    monkeypatch.setattr(CS, "_decide", lambda *a, **k: fake_result)
    return CS.eval_czasowka("a8-2-cz-oid", _order_state(), now_utc)


def test_objm_winner_from_middle_does_not_drop_original_leader(monkeypatch):
    """OBJM może przestawić best bez przeniesienia go na czoło listy."""
    original, best, tail = _candidate("100"), _candidate("200"), _candidate("300")
    res = _eval_with(monkeypatch, [original, best, tail], best)

    assert res["decision"] == "FORCE_ASSIGN"
    assert res["best"] is best
    assert [c.courier_id for c in res["alternatives"]] == ["100", "300"]


def test_solo_fallback_excludes_rejected_twin_of_selected_courier(monkeypatch):
    """Solo best jest nowym obiektem; odrzucony wariant tego samego cid nie jest altem."""
    other = _candidate("100", verdict="NO")
    rejected_twin = _candidate("200", verdict="NO")
    third = _candidate("300", verdict="NO")
    solo_best = _candidate("200", verdict="MAYBE")  # nowy obiekt, ten sam cid co twin
    res = _eval_with(monkeypatch, [other, rejected_twin, third], solo_best)

    assert res["decision"] == "FORCE_ASSIGN"
    assert res["best"] is solo_best
    assert [c.courier_id for c in res["alternatives"]] == ["100", "300"]


def test_no_solo_best_none_keeps_full_pool(monkeypatch):
    """KOORD no_solo ma best=None, więc cała oceniona pula jest alternatywą."""
    first, second = _candidate("100", verdict="NO"), _candidate("200", verdict="NO")
    res = _eval_with(monkeypatch, [first, second], None)

    assert res["decision"] == "KOORD"
    assert res["best"] is None
    assert [c.courier_id for c in res["alternatives"]] == ["100", "200"]


def test_koord_alert_top3_no_loss_no_duplicate():
    """`_format_koord_alert` (Telegram "Top 3 odrzuconych") czyta ``alternatives``
    bezpośrednio — solo_fallback-owy kształt nie może pokazać tego samego
    kuriera 2x (kontrastowe werdykty best vs alt) ani zgubić realnego
    odrzuconego (dawny ``candidates[1:]`` zawsze odcinał indeks 0)."""
    other = _candidate("100", verdict="NO", score=10.0)
    rejected_twin = _candidate("200", verdict="NO", score=7.0)
    third = _candidate("300", verdict="NO", score=1.0)
    solo_best = _candidate("200", verdict="NO", score=7.0)  # NO: to jest "brak kandydatów" alert
    alternatives = alternative_candidates([other, rejected_twin, third], solo_best)

    result = {
        "reason": "brak_feasible_maybe",
        "minutes_to_pickup": 35.0,
        "best": solo_best,
        "alternatives": alternatives,
    }
    text = CS._format_koord_alert("a8-2-koord-oid", _order_state(), result)

    assert text.count("cid=200") == 1, "bliźniak tego samego kuriera nie może zdublować wpisu"
    assert "cid=100" in text, "realny odrzucony kandydat nie może zniknąć"
    assert "cid=300" in text
