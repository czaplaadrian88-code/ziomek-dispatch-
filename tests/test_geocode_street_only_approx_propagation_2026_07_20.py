"""Wiring markera approximate do metrics i kanonicznego serializera A+B."""
from __future__ import annotations

import pytest


def test_marker_reaches_result_best_alternative_and_top_level():
    # Lokalny systemowy Python w izolowanym worktree może nie mieć OR-Tools;
    # kanoniczny dispatch venv ma zależność i wykonuje pełny test.
    pytest.importorskip("ortools")

    from dispatch_v2 import dispatch_pipeline as DP
    from dispatch_v2 import shadow_dispatcher as SD

    best = DP.Candidate(
        courier_id="1",
        name="Best",
        score=1.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        plan=None,
    )
    alternative = DP.Candidate(
        courier_id="2",
        name="Alt",
        score=0.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        plan=None,
    )
    result = DP.PipelineResult(
        order_id="street-only",
        verdict="PROPOSE",
        reason="ok",
        best=best,
        candidates=[best, alternative],
        pickup_ready_at=None,
        restaurant="R",
        delivery_address="Wasilkowska",
    )

    DP._attach_geocode_street_only_approx(
        result, {"geocode_street_only_approx": True})
    record = SD._serialize_result(result, "event-1", 1.0)

    assert result.geocode_street_only_approx is True
    assert best.metrics["geocode_street_only_approx"] is True
    assert alternative.metrics["geocode_street_only_approx"] is True
    assert record["geocode_street_only_approx"] is True
    assert record["best"]["geocode_street_only_approx"] is True
    assert record["alternatives"][0]["geocode_street_only_approx"] is True


def test_marker_absent_keeps_legacy_metrics_shape():
    pytest.importorskip("ortools")

    from dispatch_v2 import dispatch_pipeline as DP

    candidate = DP.Candidate(
        courier_id="1",
        name="Exact",
        score=1.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        plan=None,
    )
    result = DP.PipelineResult(
        order_id="exact",
        verdict="PROPOSE",
        reason="ok",
        best=candidate,
        candidates=[candidate],
        pickup_ready_at=None,
        restaurant="R",
    )
    DP._attach_geocode_street_only_approx(result, {})
    assert result.geocode_street_only_approx is False
    assert "geocode_street_only_approx" not in candidate.metrics
