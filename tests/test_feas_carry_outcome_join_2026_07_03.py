#!/usr/bin/env python3
"""Testy behawioralne harnessu L7.4 feas_carry_outcome_join (read-only join).

Pokrycie: (1) etykieta regret outcome (realized_forgiveness_cost) — kierunek +
progi; (2) mutation-probe KIERUNKU regret (odwrócenie znaku MUSI przeklasyfikować
harmful↔harmless — dowód, że kierunek jest load-bearing); (3) join core build_rows
na syntetycznych źródłach (real re-admit executed vs override; would_redirect
kosztowny vs nieszkodliwy); (4) fail-soft na brak prawdy (truth_source=none, cost=None).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dispatch_v2.tools import feas_carry_outcome_join as J  # noqa: E402
from dispatch_v2.tools import ledger_io  # noqa: E402


# ── 1. etykieta regret outcome (realized_forgiveness_cost_min) ──
def test_realized_cost_breach_positive():
    assert J.realized_forgiveness_cost_min(47.0) == 12.0
    assert J.realized_forgiveness_cost_min(40.1) == 5.1


def test_realized_cost_ontime_zero():
    assert J.realized_forgiveness_cost_min(31.6) == 0.0
    assert J.realized_forgiveness_cost_min(35.0) == 0.0  # próg włącznie = 0


def test_realized_cost_missing_is_none_not_zero():
    # brak fizyki → None (NIE zero-jako-zgoda; cisza ≠ brak breach)
    assert J.realized_forgiveness_cost_min(None) is None
    assert J.realized_forgiveness_cost_min("47") is None
    assert J.realized_forgiveness_cost_min(True) is None  # bool ≠ liczba


# ── 2. MUTATION-PROBE kierunku regret ──
def test_mutation_probe_regret_direction():
    """Kanon: breach (r6>35) → koszt>0 = HARMFUL; on-time → 0 = HARMLESS.
    Zmutowana definicja (odwrócony znak: 35−r6) MUSI przeklasyfikować, inaczej
    kierunek nie jest load-bearing (przyrząd kłamałby o sensie re-admita)."""
    def mutated(r6, sla=J.SLA_MIN):  # celowo odwrócony znak
        v = J._num(r6)
        return None if v is None else round(max(0.0, sla - v), 1)

    breach_r6, ontime_r6 = 47.0, 20.0
    # kanon: breach = harmful (>0), on-time = harmless (0)
    assert J.realized_forgiveness_cost_min(breach_r6) > 0
    assert J.realized_forgiveness_cost_min(ontime_r6) == 0
    # mutant: odwraca — breach staje się „harmless", on-time „harmful"
    assert mutated(breach_r6) == 0
    assert mutated(ontime_r6) > 0
    # klasyfikacje MUSZĄ być różne (mutacja wykryta)
    canon_harmful = J.realized_forgiveness_cost_min(breach_r6) > 0
    mut_harmful = mutated(breach_r6) > 0
    assert canon_harmful != mut_harmful


# ── fixtures dla join core ──
@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Podstawia źródła ledger_io + plik blind_shadow syntetycznymi danymi."""
    outcomes = {
        # real re-admit OVERRIDE: re-admitowany 492 NIE dowiózł (dowiózł 520), on-time
        "R1": {"actual_cid": "520", "proposed_cid": "492", "r6_actual_min": 31.6,
               "r6_breach": False, "verdict": "override", "action": "PANEL_OVERRIDE",
               "delivered_at": "2026-06-28T12:49:45+00:00"},
        # real re-admit EXECUTED: re-admitowany 509 dowiózł, breach
        "R2": {"actual_cid": "509", "proposed_cid": "509", "r6_actual_min": 41.0,
               "r6_breach": True, "verdict": "match", "action": "AUTO",
               "delivered_at": "2026-06-28T13:08:12+00:00"},
        # would_redirect KOSZTOWNY: chosen zachowany, fizyczny breach
        "W1": {"actual_cid": "300", "r6_actual_min": 44.0, "r6_breach": True,
               "verdict": "match"},
        # would_redirect NIESZKODLIWY: chosen zachowany, on-time
        "W2": {"actual_cid": "301", "r6_actual_min": 22.0, "r6_breach": False,
               "verdict": "match"},
    }
    gps = {"R1": {"confidence": "high", "physical_delivered_at": "2026-06-28T12:49:00+00:00",
                  "courier_id": "520"}}
    shadow_recs = [
        {"order_id": "R1", "ts": "2026-06-28T11:56:04+00:00",
         "best": {"courier_id": "492", "feas_carry_readmit": True,
                  "feas_carry_regret_min": 0.6, "feas_carry_newbag_min": 36.0,
                  "feas_carry_redirect_from_cid": "289"}},
        {"order_id": "R2", "ts": "2026-06-28T12:19:08+00:00",
         "best": {"courier_id": "509", "feas_carry_readmit": True,
                  "feas_carry_regret_min": 3.6, "feas_carry_newbag_min": 34.4,
                  "feas_carry_redirect_from_cid": "520"}},
        # rekord bez readmit → MUSI być pominięty
        {"order_id": "Z9", "ts": "2026-06-28T12:00:00+00:00",
         "best": {"courier_id": "111", "feas_carry_readmit": False}},
    ]
    blind = [
        {"order_id": "W1", "ts": "2026-06-28T12:00:00+00:00", "would_redirect": True,
         "chosen_cid": "300", "redirect_cid": "400", "chosen_forgiven_breach": 8.0,
         "redirect_objm": 3.0, "regret_min": 5.0, "redirect_kind": "r6_new"},
        {"order_id": "W2", "ts": "2026-06-28T12:05:00+00:00", "would_redirect": True,
         "chosen_cid": "301", "redirect_cid": "401", "chosen_forgiven_breach": 6.0,
         "redirect_objm": 2.0, "regret_min": 4.0, "redirect_kind": "sla"},
        # would_redirect False → pominięty
        {"order_id": "W3", "ts": "2026-06-28T12:06:00+00:00", "would_redirect": False},
    ]
    blind_path = tmp_path / "blind.jsonl"
    with open(blind_path, "w", encoding="utf-8") as fh:
        for r in blind:
            fh.write(json.dumps(r) + "\n")

    monkeypatch.setattr(J.ledger_io, "load_outcomes", lambda _c: outcomes)
    monkeypatch.setattr(J.ledger_io, "load_gps_truth", lambda _c: gps)
    monkeypatch.setattr(J.ledger_io, "iter_shadow_decisions", lambda _c: iter(shadow_recs))
    monkeypatch.setattr(J, "BLIND_SHADOW", str(blind_path))
    return J.build_rows(None)


# ── 3. join core ──
def test_real_readmit_override_detected(wired):
    real_rows, _, _ = wired
    r1 = next(r for r in real_rows if r["order_id"] == "R1")
    assert r1["redirect_to_cid"] == "492"
    assert r1["actual_delivered_cid"] == "520"
    assert r1["readmit_executed"] is False          # 492 ≠ 520 → override
    assert r1["realized_forgiveness_cost_min"] == 0.0
    assert r1["truth_source"] == ledger_io.TRUTH_PHYSICAL  # R1 ma GPS


def test_real_readmit_executed_breach(wired):
    real_rows, _, _ = wired
    r2 = next(r for r in real_rows if r["order_id"] == "R2")
    assert r2["readmit_executed"] is True           # 509 == 509
    assert r2["phys_r6_breach"] is True
    assert r2["realized_forgiveness_cost_min"] == 6.0  # 41−35


def test_non_readmit_record_skipped(wired):
    real_rows, _, meta = wired
    assert all(r["order_id"] != "Z9" for r in real_rows)
    assert meta["n_real"] == 2


def test_would_redirect_costly_vs_harmless(wired):
    _, wr_rows, _ = wired
    assert len(wr_rows) == 2                          # W3 (False) pominięty
    w1 = next(r for r in wr_rows if r["order_id"] == "W1")
    w2 = next(r for r in wr_rows if r["order_id"] == "W2")
    assert w1["realized_forgiveness_cost_min"] == 9.0  # 44−35 = kosztowny
    assert w2["realized_forgiveness_cost_min"] == 0.0  # 22 on-time = nieszkodliwy


def test_missing_truth_is_none(monkeypatch, tmp_path):
    """oid bez outcome/gps → truth_source none, cost None (nie 0)."""
    monkeypatch.setattr(J.ledger_io, "load_outcomes", lambda _c: {})
    monkeypatch.setattr(J.ledger_io, "load_gps_truth", lambda _c: {})
    monkeypatch.setattr(J.ledger_io, "iter_shadow_decisions", lambda _c: iter([
        {"order_id": "X", "ts": "2026-06-28T12:00:00+00:00",
         "best": {"courier_id": "1", "feas_carry_readmit": True}}]))
    blind_path = tmp_path / "b.jsonl"
    blind_path.write_text("")
    monkeypatch.setattr(J, "BLIND_SHADOW", str(blind_path))
    real_rows, _, meta = J.build_rows(None)
    assert real_rows[0]["truth_source"] == ledger_io.TRUTH_NONE
    assert real_rows[0]["realized_forgiveness_cost_min"] is None
    assert meta["n_phys"] == 0 and meta["n_proxy"] == 0


def test_selftest_oracle_passes():
    assert J._selftest() == 0
