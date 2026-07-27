"""RED-first oracle dla log-only `c7_normal_path.v1` (C7 normal path).

Testy celowo opierają się na wstrzykiwanym kanonicznym selektorze: dzięki temu
usunięcie drugiego ramienia, zamiana OFF/ON albo ciche zaakceptowanie mismatchu
zawsze czerwieni się niezależnie od wielkości fixture'a dispatchu.
"""
from datetime import datetime, timezone
import copy
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
from types import SimpleNamespace

import pytest

from dispatch_v2 import c7_normal_path as c7
from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
OID = "C7-ORACLE"


def _candidate(cid, score, penalty, *, bag=1, load=0.2):
    delivered = NOW.replace(hour=13)
    plan = dp.RoutePlanV2(
        sequence=[OID],
        predicted_delivered_at={OID: delivered},
        pickup_at={},
        total_duration_min=35.0,
        strategy="synthetic",
        sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times={OID: 31.0},
    )
    return dp.Candidate(
        courier_id=str(cid),
        name=f"PII-NAME-{cid}",
        score=float(score),
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        plan=plan,
        metrics={
            "post_shift_overrun_min": penalty / 2.0,
            "post_shift_overrun_penalty": float(penalty),
            "post_shift_overrun_score_delta": 0.0,
            "r6_max_bag_time_min": 31.0,
            "objm_r6_breach_max_min": 0.0,
            "late_pickup_committed_max": 0.0,
            "new_pickup_late_min": 1.0,
            "czas_kuriera_warsaw": "2026-07-27T12:20:00+00:00",
            "loadgov_load_ewma": load,
            "r6_bag_size": bag,
        },
    )


def _result(best, candidates, *, verdict="PROPOSE", routing="ACK", margin=0.0):
    return SimpleNamespace(
        best=best,
        candidates=list(candidates),
        full_pool_candidates=list(candidates),
        verdict=verdict,
        auto_route=routing,
        auto_route_context={"auto_route_score_margin": margin},
        pool_total_count=len(candidates),
        pool_feasible_count=sum(
            x.feasibility_verdict == "MAYBE" for x in candidates
        ),
    )


def _selector_counter(call_log):
    def select(ctx, candidates):
        call_log.append(C.decision_flag("ENABLE_POST_SHIFT_OVERRUN_PENALTY"))
        ordered = sorted(candidates, key=lambda x: -x.score)
        best = ordered[0]
        margin = best.score - ordered[1].score if len(ordered) > 1 else 0.0
        trace = getattr(ctx, "selection_trace", None)
        if isinstance(trace, dict):
            trace.update(
                score=str(best.courier_id),
                OBJM=str(best.courier_id),
                E2=str(best.courier_id),
            )
        return _result(best, ordered, margin=margin)

    return select


def _route(result, **_kwargs):
    margin = result.auto_route_context["auto_route_score_margin"]
    return ("AUTO", "test_margin") if margin >= 5.0 else ("ACK", "test_margin")


def _ctx():
    return SimpleNamespace(
        order_id=OID,
        order_event={"restaurant": "PII RESTAURANT", "address": "PII ADDRESS"},
        new_order=SimpleNamespace(order_id=OID),
        fleet_snapshot={},
        now=NOW,
        shadow_only=False,
        selection_trace=None,
    )


def test_mutation_oracle_wymaga_dwoch_kanonicznych_selekcji():
    """Usunięcie drugiej selekcji albo zamiana kolejności OFF/ON musi zaczerwienić."""
    calls = []
    pool = [_candidate("A", 100, 80), _candidate("B", 60, 0)]
    prepared = c7.prepare(pool)
    live = _result(pool[0], pool, margin=40.0)

    payload = c7.measure_prepared(
        _ctx(),
        prepared,
        live,
        select_fn=_selector_counter(calls),
        route_fn=_route,
        code_sha_fn=lambda: "a" * 40,
        fingerprint_fn=lambda: "ENABLE_POST_SHIFT_OVERRUN_PENALTY=0",
    )

    assert calls == [False, True]
    assert payload["off"]["winner_cid"] == "A"
    assert payload["on"]["winner_cid"] == "B"
    assert payload["winner_changed"] is True
    assert payload["last_changed_stage"] == "score"


@pytest.mark.parametrize(
    "off_trace,on_trace,off_winner,on_winner,expected",
    [
        (
            {"score": "A", "OBJM": "A", "E2": "A"},
            {"score": "B", "OBJM": "B", "E2": "B"},
            "A", "B", "score",
        ),
        (
            {"score": "A", "OBJM": "A", "E2": "A"},
            {"score": "A", "OBJM": "B", "E2": "B"},
            "A", "B", "OBJM",
        ),
        (
            {"score": "A", "OBJM": "A", "E2": "A"},
            {"score": "A", "OBJM": "A", "E2": "B"},
            "A", "B", "E2",
        ),
        (
            {"score": "A", "OBJM": "A", "E2": "A"},
            {"score": "A", "OBJM": "A", "E2": "A"},
            "A", "B", "gate",
        ),
    ],
)
def test_last_changed_stage_wskazuje_przyczyne_nie_ostatni_snapshot(
    off_trace, on_trace, off_winner, on_winner, expected,
):
    off = {
        "winner_cid": off_winner, "verdict": "PROPOSE",
        "routing": "ACK", "score_margin": 10.0,
    }
    on = {
        "winner_cid": on_winner, "verdict": "PROPOSE",
        "routing": "ACK", "score_margin": 10.0,
    }
    assert c7._last_changed_stage(
        off_trace, on_trace, off=off, on=on) == expected


def test_realny_kanoniczny_selektor_off_ma_parytet_100_procent():
    from dispatch_v2.core.selection import SelectionContext, select_and_emit

    pool = [_candidate("A", 100, 80), _candidate("B", 60, 0)]
    prepared = c7.prepare(pool)
    ctx = SelectionContext(
        now=NOW,
        order_event={"order_id": OID},
        order_id=OID,
        restaurant=None,
        delivery_address=None,
        pickup_coords=None,
        delivery_coords=None,
        pickup_ready_at=NOW,
        new_order=SimpleNamespace(order_id=OID),
        fleet_snapshot={},
        v328_fail_causes={},
    )
    with C.post_shift_overrun_override(False):
        actual = select_and_emit(ctx, copy.deepcopy(pool))

    returned = c7.attach_fail_safe(ctx, prepared, actual)

    assert returned is actual
    assert returned.c7_normal_path["status"] == "OK"
    assert returned.c7_normal_path["off"]["winner_cid"] == actual.best.courier_id
    assert returned.c7_normal_path["off"]["verdict"] == actual.verdict
    assert returned.c7_normal_path["off"]["routing"] == actual.auto_route


def test_prepare_normalizuje_takze_snapshot_z_rzeczywistym_c7_on():
    candidate = _candidate("A", -120, 40)
    candidate.metrics["post_shift_overrun_score_delta"] = -40.0

    prepared = c7.prepare([candidate])

    assert prepared.off_candidates[0].score == pytest.approx(-80.0)
    assert prepared.off_candidates[0].metrics[
        "post_shift_overrun_score_delta"] == 0.0
    assert prepared.on_candidates[0].score == pytest.approx(-120.0)
    assert prepared.on_candidates[0].metrics[
        "post_shift_overrun_score_delta"] == -40.0


def test_override_c7_jest_thread_local_i_przywraca_stan():
    import threading

    barrier = threading.Barrier(2)

    def arm(value):
        with C.post_shift_overrun_override(value):
            barrier.wait(timeout=2)
            return C.decision_flag("ENABLE_POST_SHIFT_OVERRUN_PENALTY")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(arm, False), executor.submit(arm, True)]
        values = {future.result() for future in futures}
    assert values == {False, True}
    assert C.decision_flag("ENABLE_POST_SHIFT_OVERRUN_PENALTY") is False


def test_code_sha_czyta_worktree_ref_bez_subprocesu(tmp_path):
    repo = tmp_path / "worktree"
    gitdir = tmp_path / "main.git" / "worktrees" / "c7"
    common = tmp_path / "main.git"
    ref = common / "refs" / "heads" / "feat-c7"
    repo.mkdir()
    gitdir.mkdir(parents=True)
    ref.parent.mkdir(parents=True)
    sha = "1a" * 20
    (repo / ".git").write_text(f"gitdir: {gitdir}\n")
    (gitdir / "HEAD").write_text("ref: refs/heads/feat-c7\n")
    (gitdir / "commondir").write_text("../..\n")
    ref.write_text(f"{sha}\n")

    assert c7._read_git_head(repo) == sha


def test_off_parity_mismatch_jest_bledem_oracle_nie_skipem():
    actual = _result(_candidate("LIVE", 10, 0), [], routing="AUTO")
    off = _result(_candidate("OFF", 10, 0), [], routing="ACK")
    with pytest.raises(c7.InstrumentMismatch):
        c7.assert_off_parity(actual, off)


def test_off_parity_mismatch_jest_logowany_jako_instrument_mismatch():
    calls = []
    pool = [_candidate("A", 100, 80), _candidate("B", 60, 0)]
    prepared = c7.prepare(pool)
    wrong_live = _result(pool[1], pool, margin=-40.0)

    payload = c7.measure_prepared(
        _ctx(),
        prepared,
        wrong_live,
        select_fn=_selector_counter(calls),
        route_fn=_route,
        code_sha_fn=lambda: "b" * 40,
        fingerprint_fn=lambda: "ENABLE_POST_SHIFT_OVERRUN_PENALTY=0",
    )

    assert payload["status"] == "INSTRUMENT_MISMATCH"
    assert "winner_cid" in payload["mismatch_fields"]


def test_realny_c7_on_zawsze_uniewaznia_oracle_off(monkeypatch):
    calls = []
    pool = [_candidate("A", 100, 0), _candidate("B", 60, 0)]
    monkeypatch.setattr(
        C,
        "load_flags",
        lambda: {"ENABLE_POST_SHIFT_OVERRUN_PENALTY": True},
    )
    payload = c7.measure_prepared(
        _ctx(),
        c7.prepare(pool),
        _result(pool[0], pool, margin=40.0),
        select_fn=_selector_counter(calls),
        route_fn=_route,
        code_sha_fn=lambda: "e" * 40,
        fingerprint_fn=lambda: "ENABLE_POST_SHIFT_OVERRUN_PENALTY=1",
    )
    assert payload["status"] == "INSTRUMENT_MISMATCH"
    assert "actual_c7_enabled" in payload["mismatch_fields"]


def test_fail_safe_nigdy_nie_zmienia_produkcjnego_result():
    pool = [_candidate("A", 100, 10)]
    live = _result(pool[0], pool)
    prepared = c7.prepare(pool)

    def explode(*_args, **_kwargs):
        raise RuntimeError("PII ADDRESS SHOULD NOT LEAK")

    returned = c7.attach_fail_safe(
        _ctx(), prepared, live, select_fn=explode
    )

    assert returned is live
    assert returned.best is pool[0]
    assert returned.verdict == "PROPOSE"
    assert returned.c7_normal_path == {
        "schema": c7.SCHEMA,
        "status": "INSTRUMENT_ERROR",
        "error_type": "RuntimeError",
    }


def test_payload_nie_zawiera_pii_ani_zabronionych_kluczy():
    calls = []
    pool = [_candidate("A", 100, 80), _candidate("B", 60, 0)]
    payload = c7.measure_prepared(
        _ctx(),
        c7.prepare(pool),
        _result(pool[0], pool, margin=40.0),
        select_fn=_selector_counter(calls),
        route_fn=_route,
        code_sha_fn=lambda: "c" * 40,
        fingerprint_fn=lambda: "ENABLE_POST_SHIFT_OVERRUN_PENALTY=0",
    )
    encoded = json.dumps(payload, sort_keys=True)

    for forbidden_value in ("PII-NAME", "PII RESTAURANT", "PII ADDRESS"):
        assert forbidden_value not in encoded
    forbidden_keys = {"name", "restaurant", "address", "delivery_address",
                      "lat", "lon", "coords", "gps"}

    def walk(value):
        if isinstance(value, dict):
            assert not (set(value) & forbidden_keys)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)


def test_ten_sam_winner_ale_margin_lub_routing_nadal_jest_mierzony():
    calls = []
    pool = [_candidate("A", 100, 20), _candidate("B", 90, 14)]
    payload = c7.measure_prepared(
        _ctx(),
        c7.prepare(pool),
        _result(pool[0], pool, margin=10.0, routing="AUTO"),
        select_fn=_selector_counter(calls),
        route_fn=_route,
        code_sha_fn=lambda: "d" * 40,
        fingerprint_fn=lambda: "ENABLE_POST_SHIFT_OVERRUN_PENALTY=0",
    )

    assert payload["winner_changed"] is False
    assert payload["margin_changed"] is True
    assert payload["routing_changed"] is True
    assert payload["last_changed_stage"] == "gate"


@pytest.mark.parametrize("always_propose", [False, True])
def test_ratchet_c7_nie_steruje_best_effort_low_score_gate(always_propose):
    """C7 może zmienić ranking, ale nie verdict przez rankingową deltę score."""
    c = _candidate("A", -120, 40)
    c.metrics["post_shift_overrun_score_delta"] = -40.0
    with C.post_shift_overrun_override(True):
        gate_score = dp._gate_score_excluding_ranking_deltas(c)
    assert gate_score == pytest.approx(-80.0)

    src = inspect.getsource(
        __import__("dispatch_v2.core.selection", fromlist=["selection"])
    )
    gate_block = src[src.index("# P3-D3 2026-05-11 (root cause 3)"):
                     src.index("# R29 SOLO fallback")]
    assert "_gate_score_excluding_ranking_deltas(best)" in gate_block
    if always_propose:
        assert "and not _always_propose_on()" in gate_block


@pytest.mark.parametrize("always_propose", [False, True])
def test_ratchet_c7_nie_steruje_difficult_case_gate(always_propose):
    """Difficult-case ma czytać score bez delt rankingowych także po always-propose."""
    src = inspect.getsource(
        __import__("dispatch_v2.core.selection", fromlist=["selection"])
    )
    block = src[src.index("# === Difficult-case KOORD redirect"):
                src.index("# === Load-aware selection SHADOW")]
    assert "_gate_score_excluding_ranking_deltas(top[0])" in block
    assert "_gate_score_excluding_ranking_deltas(_c)" in block
    if always_propose:
        # Always-propose nie może przywrócić surowego score jako drugiej polityki.
        assert 'getattr(top[0], "score"' not in block


def test_flaga_kill_switch_jest_hot_i_w_fingerprint():
    assert "ENABLE_C7_NORMAL_PATH_LOG" in C.ETAP4_DECISION_FLAGS
    assert C.ENABLE_C7_NORMAL_PATH_LOG is False
    assert "ENABLE_C7_NORMAL_PATH_LOG=" in C.flag_fingerprint()
    import pathlib
    registry = json.loads(
        pathlib.Path("tools/flag_lifecycle_registry.json").read_text())
    entry = registry["flags"]["ENABLE_C7_NORMAL_PATH_LOG"]
    assert entry["source_of_truth"] == "flags.json"
    assert entry["rollback"].startswith("flags.json false")


def test_serializer_ma_jeden_addytywny_consumer_payloadu():
    from dispatch_v2 import shadow_dispatcher

    src = inspect.getsource(shadow_dispatcher._serialize_result)
    assert '"c7_normal_path"' in src
    assert "getattr(result, \"c7_normal_path\"" in src

    result = dp.PipelineResult(
        order_id=OID,
        verdict="KOORD",
        reason="test",
        best=None,
        candidates=[],
        pickup_ready_at=None,
        restaurant=None,
    )
    legacy = shadow_dispatcher._serialize_result(result, "event", 1.0)
    assert "c7_normal_path" not in legacy
    result.c7_normal_path = {"schema": c7.SCHEMA, "status": "OK"}
    instrumented = shadow_dispatcher._serialize_result(result, "event", 1.0)
    assert instrumented["c7_normal_path"] == result.c7_normal_path
