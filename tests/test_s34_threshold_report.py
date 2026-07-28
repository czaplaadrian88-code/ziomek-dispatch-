from __future__ import annotations

import json
from datetime import datetime, timezone

from dispatch_v2.tools import s34_threshold_report as report


def _decision(
    seq: int,
    *,
    base_w: int,
    chosen_w: int,
    gain: float,
    delta: float,
    identity: bool = False,
) -> dict:
    arrival = 10.0 + delta
    return {
        "schema": "lex_window_ledger.v2",
        "schema_version": 2,
        "record_kind": "decision",
        "emitted_at": f"2026-07-28T18:{seq:02d}:00+00:00",
        "decision_id": f"decision-{seq}",
        "attempt_id": f"attempt-{seq}",
        "run_id": f"run-{seq}",
        "caller": {"role": "canonical", "source": "test", "pid": 100 + seq},
        "courier_id": "synthetic",
        "bag": {"carried": []},
        "baseline": {"window_viol": base_w, "drive_min": 20.0},
        "chosen": {"window_viol": chosen_w, "drive_min": 20.0 - gain},
        "items": [
            {
                "order_id": "synthetic-order",
                "kind": "dropoff",
                "arrival_min": arrival,
                "baseline_arrival_min": 10.0,
                "dwell_min": 1.0,
                "raw_carry_min": None,
            }
        ],
        "decision": {
            "identity": identity,
            "candidate_chosen": not identity,
            "decided": not identity,
        },
        "guards": {},
        "thresholds": {},
        "coverage": {"run_seq": seq},
    }


def test_parser_dedup_and_grid_on_ten_synthetic_records(tmp_path):
    records = [
        _decision(0, base_w=2, chosen_w=1, gain=0.1, delta=99),
        _decision(1, base_w=3, chosen_w=2, gain=-2, delta=99),
        _decision(2, base_w=1, chosen_w=0, gain=0, delta=99),
        _decision(3, base_w=1, chosen_w=1, gain=2.0, delta=3.0),
        _decision(4, base_w=1, chosen_w=1, gain=2.0, delta=4.2),
        _decision(5, base_w=1, chosen_w=1, gain=0.4, delta=2.0),
        _decision(6, base_w=1, chosen_w=1, gain=1.0, delta=8.0),
        _decision(7, base_w=1, chosen_w=1, gain=3.0, delta=10.0),
        _decision(8, base_w=1, chosen_w=1, gain=1.0, delta=1.0, identity=True),
    ]
    duplicate = json.loads(json.dumps(records[4]))
    duplicate.update(
        {
            "emitted_at": "2026-07-28T19:59:00+00:00",
            "decision_id": "different-decision-id",
            "attempt_id": "different-attempt-id",
            "run_id": "different-run-id",
            "coverage": {"run_seq": 999},
        }
    )
    duplicate["caller"]["pid"] = 999
    records.append(duplicate)
    assert len(records) == 10

    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
    )
    parsed = list(
        report._iter_records(
            ledger, datetime(2026, 7, 28, tzinfo=timezone.utc)
        )
    )
    result = report.analyze_ledger_records(
        parsed, datetime(2026, 7, 28, tzinfo=timezone.utc)
    )

    assert result["input"]["parsed_dict_records"] == 10
    assert result["input"]["duplicates"] == 1
    assert result["input"]["unique_decisions"] == 9
    assert result["input"]["identity_decisions_excluded_from_grid"] == 1
    assert result["classes"] == {
        "strict_w": 3,
        "equal_w": 5,
        "w_regression": 0,
        "n/d": 0,
    }
    assert result["oracle_492_class_n"] == 2

    cell = next(
        row
        for row in result["grid"]
        if row["tol_min"] == 3.0 and row["gain_min"] == 2.0
    )
    assert cell["strict_w"] == {"survive": 3, "die": 0, "n/d": 0}
    assert cell["equal_w"] == {"survive": 1, "die": 4, "n/d": 0}
    assert cell["oracle_492"]["killed"] == 2

    boundary = next(
        row
        for row in result["grid"]
        if row["tol_min"] == 10.0 and row["gain_min"] == 2.0
    )
    assert boundary["equal_w"] == {"survive": 3, "die": 2, "n/d": 0}
    # Mutation witness: odwrócenie inclusive <= / >= zmienia wynik graniczny.
    assert report.grid_survives(
        "equal_w", delivery_delta=10.0, drive_gain=2.0, tol=10.0, gain=2.0
    )
    assert not (10.0 < 10.0 and 2.0 > 2.0)

    out = tmp_path / "S34_PAKIET_PROGOW_TEST.md"
    argv = [
        "--since",
        "2026-07-28T00:00",
        "--timestamp",
        "20260728T220000Z",
        "--out",
        str(out),
        "--ledger",
        str(ledger),
        "--episodes",
        str(ledger),
        "--shadow",
        str(ledger),
        "--freeze",
        str(tmp_path),
    ]
    ledger_before = (ledger.read_bytes(), ledger.stat().st_mtime_ns)
    assert report.main(argv) == 0
    first_md = out.read_bytes()
    first_json = out.with_suffix(".json").read_bytes()
    assert report.main(argv) == 0
    assert (ledger.read_bytes(), ledger.stat().st_mtime_ns) == ledger_before
    assert out.read_bytes() == first_md
    assert out.with_suffix(".json").read_bytes() == first_json
    # Kanoniczny conftest (izolacja flag) dokłada własny flags.json do tmp_path
    # — asercja pilnuje TYLKO artefaktów harnessu, nie całego katalogu.
    assert {path.name for path in tmp_path.iterdir()} - {"flags.json"} == {
        "ledger.jsonl",
        "S34_PAKIET_PROGOW_TEST.md",
        "S34_PAKIET_PROGOW_TEST.json",
    }
    payload = json.loads(first_json)
    assert payload["schema"] == "s34.threshold_report.v1"
    assert payload["mode"] == "build_read_only_no_verdict"
