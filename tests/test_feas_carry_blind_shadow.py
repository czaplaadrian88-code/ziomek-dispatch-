"""Warstwa B shadow — _feas_carry_blind_shadow (read-only, #483000).

Weryfikuje: (1) dziura — bypassowany survivor + lepszy odrzucony NO blocking →
would_redirect True + regret; (2) kontrola — odrzucony GORSZY → would_redirect
False; (3) chosen czysty (objm=0) → brak emisji; (4) zero mutacji decyzji.
"""
import json
import types

from dispatch_v2 import dispatch_pipeline as dp


def _cand(cid, verdict, reason, objm, committed=0.0, new_late=0.0):
    return types.SimpleNamespace(
        courier_id=cid,
        feasibility_verdict=verdict,
        feasibility_reason=reason,
        metrics={
            "objm_r6_breach_max_min": objm,
            "late_pickup_committed_max": committed,
            "new_pickup_late_min": new_late,
        },
    )


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_hole_483000_like(tmp_path, monkeypatch):
    """Bypassowany survivor (objm 6.0) + odrzucony NO sla mniejszy (objm 3.3) →
    would_redirect True, regret 2.7, marginal (over by 2.3)."""
    log = tmp_path / "fb.jsonl"
    monkeypatch.setattr(dp, "FEAS_CARRY_BLIND_SHADOW_LOG_PATH", str(log))
    chosen = _cand("457", "MAYBE", "ok_sla_fits", 6.0)        # 41 wiek → objm 6
    better = _cand("393", "NO", "sla_violation (482968 +37.3min, over by 2.3)", 3.3)
    worse = _cand("484", "NO", "sla_violation (482991 +39.8min, over by 4.8)", 4.8)
    top = [chosen]
    feasible = [chosen]
    candidates = [chosen, better, worse]
    dp._feas_carry_blind_shadow(top, feasible, candidates, "483000", now=None)

    rows = _read(log)
    assert len(rows) == 1
    r = rows[0]
    assert r["would_redirect"] is True
    assert r["redirect_cid"] == "393"
    assert r["redirect_objm"] == 3.3
    assert r["redirect_kind"] == "sla"
    assert r["regret_min"] == 2.7
    assert r["marginal"] is True
    assert r["pool_total"] == 3 and r["pool_feasible"] == 1
    assert r["chosen_cid"] == "457"
    # zero mutacji
    assert chosen.feasibility_verdict == "MAYBE"
    assert top == [chosen] and feasible == [chosen]


def test_control_no_better_rejected(tmp_path, monkeypatch):
    """Odrzuceni mają WYŻSZY objm niż chosen → would_redirect False."""
    log = tmp_path / "fb.jsonl"
    monkeypatch.setattr(dp, "FEAS_CARRY_BLIND_SHADOW_LOG_PATH", str(log))
    chosen = _cand("457", "MAYBE", "ok_sla_fits", 6.0)
    worse = _cand("370", "NO", "sla_violation (483000 +37.6min, over by 2.6)", 12.0)
    dp._feas_carry_blind_shadow([chosen], [chosen], [chosen, worse], "X1", now=None)
    rows = _read(log)
    assert len(rows) == 1
    assert rows[0]["would_redirect"] is False
    assert rows[0]["redirect_cid"] == "370"  # best blocking, ale gorszy
    assert rows[0]["regret_min"] is None


def test_clean_chosen_no_emit(tmp_path, monkeypatch):
    """Chosen bez wybaczonego breachu (objm 0) → brak emisji (poza zakresem warstwy B)."""
    log = tmp_path / "fb.jsonl"
    monkeypatch.setattr(dp, "FEAS_CARRY_BLIND_SHADOW_LOG_PATH", str(log))
    chosen = _cand("100", "MAYBE", "ok_sla_fits", 0.0)
    better = _cand("200", "NO", "sla_violation (1 +36min, over by 1.0)", 1.0)
    dp._feas_carry_blind_shadow([chosen], [chosen], [chosen, better], "X2", now=None)
    assert not log.exists()


def test_non_blocking_reject_ignored(tmp_path, monkeypatch):
    """Odrzucony za shift_end/dist (NIE blocking SLA/R6) NIE liczy się jako redirect."""
    log = tmp_path / "fb.jsonl"
    monkeypatch.setattr(dp, "FEAS_CARRY_BLIND_SHADOW_LOG_PATH", str(log))
    chosen = _cand("457", "MAYBE", "ok_sla_fits", 6.0)
    shift = _cand("999", "NO", "v324a_dropoff_after_shift (dropoff 22:10 ...)", 1.0)
    dp._feas_carry_blind_shadow([chosen], [chosen], [chosen, shift], "X3", now=None)
    rows = _read(log)
    assert len(rows) == 1
    assert rows[0]["would_redirect"] is False
    assert rows[0]["redirect_cid"] is None
    assert rows[0]["n_blocking"] == 0 and rows[0]["n_rejected"] == 1
