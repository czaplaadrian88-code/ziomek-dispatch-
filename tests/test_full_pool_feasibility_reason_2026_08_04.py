"""Siódme pole `full_pool_compact` — powód odcięcia kandydata (owner GO 2026-08-04).

Defekt, który to reprodukuje: sześciopolowa projekcja logowała KTÓRY kurier
wypadł z puli, ale nie KTÓRA bramka go wycięła. Audyt per-bramka musiał więc
zgadywać rodzinę bramki z trzech pól diagnostycznych (`pos_source`,
`km_to_pickup`, `r6_bag_size`) — a te trzy pola potrafią wskazać INNĄ rodzinę
niż bramka, która realnie odrzuciła kandydata (patrz
`test_reason_is_verbatim_not_proxy_reconstruction`).

Kontrakt:
- powód jest przepisywany DOSŁOWNIE z kandydata, czyli z pary (verdict, reason),
  którą zapisuje warstwa realnie rozstrzygająca — nie jest rekonstruowany;
- `None` dokładnie wtedy, gdy kandydat jest feasible (`MAYBE`);
- pole należy wyłącznie do `full_pool_compact`; LOCATION A/B pozostają bez zmian;
- `ENABLE_FULL_CHOICE_SET_LOG` OFF = brak całego klucza (rollback hot).
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.dispatch_pipeline import Candidate, PipelineResult
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
PICKUP = (53.13, 23.16)
DROP = (53.14, 23.17)


def _order(ordinal: int) -> OrderSim:
    return OrderSim(
        order_id=str(400000 + ordinal),
        pickup_coords=PICKUP,
        delivery_coords=DROP,
        pickup_ready_at=NOW,
    )


def _engine_verdict(bag_size: int) -> tuple[str, str]:
    """Prawdziwa feasibility — źródło pary (verdict, reason) dla oracle.

    Test celowo NIE zna oczekiwanego tekstu powodu z góry: bierze go z silnika
    i sprawdza, że dokładnie ten tekst ląduje w ledgerze.
    """
    verdict, reason, _metrics, _plan = check_feasibility_v2(
        courier_pos=PICKUP,
        bag=[_order(i) for i in range(bag_size)],
        new_order=_order(99),
        shift_end=NOW + timedelta(hours=4),
        shift_start=NOW - timedelta(hours=1),
        now=NOW,
        pickup_ready_at=NOW,
    )
    return verdict, reason


def _candidate(cid: str, verdict: str, reason: str, **metrics) -> Candidate:
    base = {"pos_source": "gps", "km_to_pickup": 1.2, "r6_bag_size": 1}
    base.update(metrics)
    return Candidate(
        courier_id=cid,
        name=f"Courier-{cid}",
        score=10.0,
        feasibility_verdict=verdict,
        feasibility_reason=reason,
        plan=None,
        metrics=base,
    )


def _result(pool: list[Candidate]) -> PipelineResult:
    return PipelineResult(
        order_id="feas-reason-order",
        verdict="PROPOSE",
        reason="test",
        best=pool[0],
        candidates=pool[:16],
        full_pool_candidates=pool,
        pickup_ready_at=None,
        restaurant="Test",
    )


def _full_pool(pool: list[Candidate], monkeypatch) -> list[dict]:
    monkeypatch.setattr(C, "ENABLE_FULL_CHOICE_SET_LOG", True)
    record = SD._serialize_result(_result(pool), "shadow-event-feas", 1.0)
    return record["full_pool_compact"]


def test_negative_oracle_cut_candidate_carries_the_gate_that_cut_him(monkeypatch):
    """Kandydat odrzucony przez PRAWDZIWĄ bramkę → jej powód jest w ledgerze.

    To jest oracle reprodukujący defekt: na baseline klucza nie ma w ogóle,
    więc odczyt `row["feasibility_reason"]` podnosi KeyError.
    """
    cut_verdict, cut_reason = _engine_verdict(bag_size=9)
    assert cut_verdict == "NO", "oracle wymaga realnie odrzuconego kandydata"

    rows = _full_pool(
        [_candidate("101", cut_verdict, cut_reason)], monkeypatch
    )

    assert rows[0]["feasibility_reason"] == cut_reason
    # Nazwa bramki (człon przed nawiasem) musi być odczytywalna — to jedyna
    # rzecz, której audyt per-bramka potrzebuje ponad tożsamość kuriera.
    assert rows[0]["feasibility_reason"].split(" (")[0] == "bag_full"


def test_feasible_candidate_from_engine_has_no_reason(monkeypatch):
    """Feasible = brak powodu; „nie wycięty" niesie już sam werdykt."""
    ok_verdict, ok_reason = _engine_verdict(bag_size=0)
    assert ok_verdict == "MAYBE", "oracle wymaga realnie feasible kandydata"
    assert ok_reason, "silnik zwraca niepusty powód także dla MAYBE"

    rows = _full_pool([_candidate("102", ok_verdict, ok_reason)], monkeypatch)

    assert rows[0]["feasibility_reason"] is None
    assert rows[0]["feasibility_verdict"] == "MAYBE"


def test_reason_is_verbatim_not_proxy_reconstruction(monkeypatch):
    """Powód pochodzi z decyzji, nie z trzech pól diagnostycznych.

    Kandydat jest skonstruowany tak, żeby PROXY po `pos_source`/`km`/`bag`
    wskazało rodzinę POZYCJA, podczas gdy realnie wyciął go sufit SLA. Gdyby
    fix rekonstruował powód z pól compact, ten test złapałby podmianę.
    """
    rows = _full_pool(
        [
            _candidate(
                "103",
                "NO",
                "sla_violation (491924 +83.4min, over by 48.4)",
                pos_source="pre_shift",
                km_to_pickup=9.9,
                r6_bag_size=4,
            )
        ],
        monkeypatch,
    )

    assert rows[0]["feasibility_reason"].startswith("sla_violation (")
    assert "pre_shift" not in rows[0]["feasibility_reason"]


@pytest.mark.parametrize(
    "verdict,reason,expected",
    [
        ("MAYBE", "ok_sla_fits", None),
        ("NO", "bag_full (9/8)", "bag_full (9/8)"),
        ("UNKNOWN", "candidate_not_materialized", "candidate_not_materialized"),
        ("UNKNOWN", "candidate_evaluation_error", "candidate_evaluation_error"),
        ("NO", "", None),
        ("NO", None, None),
    ],
)
def test_reason_present_exactly_when_candidate_is_not_feasible(
    verdict, reason, expected, monkeypatch
):
    rows = _full_pool([_candidate("104", verdict, reason)], monkeypatch)
    assert rows[0]["feasibility_reason"] == expected


def test_unknown_candidates_explain_their_own_absence(monkeypatch):
    """Kandydat UNKNOWN nie jest feasible — jego powód musi przetrwać.

    `_alarm_counterfactual_pool` materializuje kurierów, których pętla oceny
    w ogóle nie zwróciła; bez powodu byliby w ledgerze nieodróżnialni od
    kandydatów wyciętych bramką.
    """
    rows = _full_pool(
        [
            _candidate("105", "NO", "bag_full (9/8)"),
            _candidate("106", "UNKNOWN", "candidate_evaluation_error"),
            _candidate("107", "MAYBE", "ok_sla_fits"),
        ],
        monkeypatch,
    )

    by_cid = {row["cid"]: row["feasibility_reason"] for row in rows}
    assert by_cid == {
        "105": "bag_full (9/8)",
        "106": "candidate_evaluation_error",
        "107": None,
    }


def test_flag_off_removes_the_whole_key(monkeypatch):
    """Rollback jest hot: OFF = kontrakt sprzed zmiany, bez nowego pola."""
    pool = [_candidate("108", "NO", "bag_full (9/8)")]

    monkeypatch.setattr(C, "ENABLE_FULL_CHOICE_SET_LOG", False)
    off = SD._serialize_result(_result(pool), "shadow-event-off", 1.0)
    assert "full_pool_compact" not in off

    monkeypatch.setattr(C, "ENABLE_FULL_CHOICE_SET_LOG", True)
    on = SD._serialize_result(_result(pool), "shadow-event-on", 1.0)
    assert on["full_pool_compact"][0]["feasibility_reason"] == "bag_full (9/8)"
    assert on != off


def test_ledger_row_stays_small_with_realistic_reasons(monkeypatch):
    """Powód nie może rozsadzić ledgera — pełna flota z długimi powodami."""
    long_reason = (
        "R6_per_order_>35min (491947 51.1min, thermal anchor=ready_at; "
        "n_violations=2)"
    )
    pool = [
        _candidate(str(200 + i), "NO", long_reason) for i in range(20)
    ]
    rows = _full_pool(pool, monkeypatch)

    assert len(json.dumps(rows, ensure_ascii=False)) < 6000


def test_compact_projection_is_the_only_writer_of_the_field():
    """Ratchet: żaden drugi writer nie produkuje wiersza pełnej puli.

    Wiersze `full_pool_compact` mogą powstawać WYŁĄCZNIE przez wspólny helper.
    Literał słownika z tym kluczem gdziekolwiek indziej w serializerze oznacza
    powrót konkurencyjnego źródła prawdy.
    """
    source_path = Path(SD.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_serialize_candidate_compact"
    )
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and not (helper.lineno <= node.lineno <= helper.end_lineno)
        and any(
            isinstance(key, ast.Constant) and key.value == "feasibility_reason"
            for key in node.keys
            if key is not None
        )
    ]
    assert not offenders, f"drugi writer feasibility_reason, linie: {offenders}"


def test_full_pool_builder_delegates_every_row_to_the_helper():
    """Ratchet: builder pełnej puli nie może omijać wspólnej projekcji."""
    source_path = Path(SD.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    builder = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_serialize_full_pool_compact"
    )
    called = {
        node.func.id
        for node in ast.walk(builder)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_serialize_candidate_compact" in called
    assert not [
        node for node in ast.walk(builder) if isinstance(node, ast.Dict)
    ], "builder buduje wiersz sam zamiast delegować do projekcji"


def test_serializer_locations_a_and_b_keep_their_shape(monkeypatch):
    """Parytet A/B: siódme pole nie wycieka poza pełną pulę."""
    candidate = _candidate("109", "NO", "bag_full (9/8)")
    monkeypatch.setattr(C, "ENABLE_FULL_CHOICE_SET_LOG", True)

    location_a = SD._serialize_candidate(candidate)
    location_b = SD._serialize_result(
        _result([candidate]), "shadow-event-ab-feas", 1.0
    )["best"]

    for location in (location_a, location_b):
        assert "feasibility_reason" not in location
        assert location["reason"] == "bag_full (9/8)"


def test_projection_survives_candidate_without_the_attribute(monkeypatch):
    """Fail-soft: obcy obiekt bez atrybutu nie może wywrócić logowania."""
    stub = SimpleNamespace(
        courier_id="110",
        score=1.0,
        feasibility_verdict="NO",
        metrics={"pos_source": "gps", "km_to_pickup": 1.0, "r6_bag_size": 0},
    )
    compact = SD._serialize_candidate_compact(stub)
    assert compact["feasibility_reason"] is None
    assert compact["feasibility_verdict"] == "NO"
