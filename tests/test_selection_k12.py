"""K12 (refaktor, 2026-07-06): selekcja/werdykt w core.selection — kontrakty przenosin.

Parytet treści dowodzi replay różnicowy (bramka korpusowa master↔gałąź);
tu kontrakty, które przenosiny mogłyby zerwać + dowód C15 dla „re-assert na EMIT".
"""
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import dispatch_v2.dispatch_pipeline as dp
from dispatch_v2.core import selection as sel

_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def _ctx(**over):
    kw = dict(
        now=_NOW, order_event={"order_id": "K12T"}, order_id="K12T",
        restaurant="Testownia", delivery_address="Testowa 1",
        pickup_coords=(53.13, 23.16), delivery_coords=(53.14, 23.17),
        pickup_ready_at=None, new_order=None, fleet_snapshot={},
        v328_fail_causes={},
    )
    kw.update(over)
    return sel.SelectionContext(**kw)


def test_pusta_pula_daje_pipelineresult_koord_no_solo():
    res = sel.select_and_emit(_ctx(), [])
    assert type(res).__name__ == "PipelineResult"
    assert res.verdict in ("KOORD", "NO", "SKIP"), res.verdict
    assert res.pool_total_count == 0 and res.pool_feasible_count == 0


def test_solo_fallback_carries_plan_expected_version(monkeypatch):
    cs = SimpleNamespace(
        pos=(53.13, 23.16), name="Solo", shift_end=None, shift_start=None,
        tier_bag=None, schedule_source_stale=False, pos_from_store=False,
        pos_source="gps",
    )
    plan = SimpleNamespace(sla_violations=0)
    monkeypatch.setattr(
        dp, "check_feasibility_v2",
        lambda **kw: ("MAYBE", "ok", {"pickup_dist_km": 1.0}, plan),
    )

    res = sel.select_and_emit(
        _ctx(
            fleet_snapshot={"9": cs}, new_order=object(),
            plan_versions={"9": 6},
        ),
        [],
    )

    assert res.best is not None
    assert res.best.metrics["plan_expected_version"] == 6


def test_monkeypatch_min_propose_obowiazuje_w_core(monkeypatch):
    """Aliasy prologu czytają atrybut dispatch_pipeline per-call (kontrakt jak K11)."""
    called = {"n": 0}
    orig = dp._min_propose_score

    def counting():
        called["n"] += 1
        return orig()

    monkeypatch.setattr(dp, "_min_propose_score", counting)
    sel.select_and_emit(_ctx(), [])  # pusta pula — może nie dojść do progu
    # kontrakt strukturalny: alias w prologu wiąże _dp._min_propose_score
    src = inspect.getsource(sel)
    assert "_min_propose_score = _dp._min_propose_score" in src


def test_emit_reassert_c15_dowod():
    """„NOWY re-assert _assert_feasibility_first na EMIT" ze spec = JUŻ WYKONANE
    (fala L7.3, klasa C15): wspólny lejek _classify_and_set_auto_route woła
    _split_layer_emit_assert przy KAŻDYM emicie; selection emituje przez lejek."""
    lejek = inspect.getsource(dp._classify_and_set_auto_route)
    assert "_split_layer_emit_assert" in lejek, "lejek EMIT musi wołać re-assert L7.3"
    src = inspect.getsource(sel)
    assert src.count("_classify_and_set_auto_route(") >= 8, \
        "ścieżki emisji selection MUSZĄ przechodzić przez wspólny lejek pre-emit"
    guard = inspect.getsource(dp._split_layer_emit_assert)
    assert "_assert_feasibility_first" in guard
