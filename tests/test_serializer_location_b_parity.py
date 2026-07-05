"""A1-SERIALIZER (Sprint 1, 2026-07-05) — parytet kluczy LOCATION B (funkcjonalny).

Domknięcie de-VOID inwariantu „serializer −38 kluczy" (ZIOMEK_INVARIANTS
kontrakt ⑤). L1.1 (85d92f7) odwrócił allowlistę na deny-listę i dał testy
LOCATION A (`test_serializer_completeness_l11`); parytet A↔B był tam jednak
sprawdzany tylko TEKSTUALNIE (licznik call-site'ów helpera). Ten plik ćwiczy
LOCATION B (`_serialize_result` → out["best"]) FUNKCJONALNIE, na realnym
`PipelineResult` — mutation-probe „wytnij call B" łapie się tu semantycznie,
nie grep-em.

Re-oracle C9 (2026-07-05, okno świeże od restartu shadow 03.07 13:18 UTC,
n=229 decyzji): klucze ginące przed L1.1 płyną — eta_source/c2_*/cs_tier_*/
sla_minutes_used/wave_bonus = 221/229 (8 rekordów = early-path bez best),
sla_violations_* = 67/229 (warunkowe), r6_gold4_gate_recovered = 14/229.
Zera wyjaśnione grepem producentów (paczka-exempt / stale-grafik / V328
mass-fail / violation-only); `eta_src`+`drive_source` NIE mają producentów
w silniku (nazwy z ery audytu B07). Dowód: eod_drafts/2026-07-05/
A1_SERIALIZER_reoracle_dowod.md.
"""
import json
import sys
from dataclasses import dataclass, field

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import shadow_dispatcher as SD  # noqa: E402
from dispatch_v2.dispatch_pipeline import PipelineResult  # noqa: E402
from dispatch_v2.tests.test_serializer_completeness_l11 import (  # noqa: E402
    _AUDIT_VANISHED_KEYS,
)


@dataclass
class _MockCand:
    metrics: dict = field(default_factory=dict)
    plan: object = None
    courier_id: int = 123
    name: str = "Test"
    score: float = 100.0
    feasibility_verdict: str = "FEASIBLE"
    feasibility_reason: str = ""
    best_effort: bool = False


def _result_with_best(metrics: dict) -> PipelineResult:
    cand = _MockCand(metrics=dict(metrics))
    return PipelineResult(
        order_id="470001",
        verdict="PROPOSE",
        reason="test-parity",
        best=cand,
        candidates=[cand],
        pickup_ready_at=None,
        restaurant="TestRest",
    )


def test_audit_vanished_keys_reach_location_b():
    """Każdy klucz z listy audytu B07 dociera do LOCATION B (out['best'])."""
    out = SD._serialize_result(_result_with_best(_AUDIT_VANISHED_KEYS),
                               "evt-parity-b", 1.0)
    assert out["best"] is not None
    missing = [k for k in _AUDIT_VANISHED_KEYS if k not in out["best"]]
    assert not missing, f"klucze giną z LOCATION B: {missing}"


def test_arbitrary_future_key_reaches_location_b():
    """Kontrakt ⑤: NOWA metryka best-kandydata widoczna w ledgerze od
    urodzenia także przez LOCATION B (nie tylko helper w izolacji)."""
    out = SD._serialize_result(
        _result_with_best({"totally_new_metric_2099": 42}), "evt-new-b", 1.0)
    assert out["best"].get("totally_new_metric_2099") == 42


def test_parity_a_b_same_key_set():
    """Parytet bliźniaków A↔B: ten sam dict metrics przepuszczony przez OBA
    serializery daje ten sam podzbiór kluczy metrics (poza deny-listą)."""
    metrics = dict(_AUDIT_VANISHED_KEYS)
    metrics["parity_probe_key"] = 1.23
    out_a = SD._serialize_candidate(_MockCand(metrics=dict(metrics)))
    out_b = SD._serialize_result(_result_with_best(metrics), "evt-par", 1.0)["best"]
    expected = set(metrics) - set(SD._METRICS_EXCLUDE)
    missing_a = sorted(expected - set(out_a))
    missing_b = sorted(expected - set(out_b))
    assert not missing_a and not missing_b, (
        f"parytet A↔B złamany — brak w A: {missing_a}, brak w B: {missing_b}")


def test_full_record_location_b_json_safe():
    """Cały rekord decyzji (z egzotycznymi wartościami w metrics best)
    musi być json-serializowalny — inaczej append_jsonl wywala zapis."""
    from datetime import datetime, timezone
    out = SD._serialize_result(
        _result_with_best({
            "future_dt": datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
            "future_set": {"a", "b"},
        }), "evt-json-b", 1.0)
    encoded = json.dumps(out, ensure_ascii=False)  # nie może rzucić
    assert "2026-07-05T12:00:00+00:00" in encoded
