"""Fixture-first and mutation tests for the read-only R-04 coverage replay."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from dispatch_v2.tools import r04_telemetry_replay as replay


ROOT = Path(__file__).resolve().parents[1]


class R04TelemetryReplayTest(unittest.TestCase):
    def test_fixture_first_candidate_identity_must_produce_value(self):
        suggestion = {
            "courier_id": "fixture-101",
            "evaluation_ran": True,
            "decision_effect": "telemetry_only",
            "outcome": "promotion_suggested",
        }
        rows = [
            {
                "best": {
                    "courier_id": "fixture-101",
                    "r04": None,
                    "metrics": {"courier_id": "WRONG"},
                },
                "alternatives": [
                    {"courier_id": "fixture-101", "r04": suggestion}
                ],
            },
            {"best": {"courier_id": "fixture-202", "r04": None}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "shadow.jsonl"
            snapshot.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            result = replay.replay_snapshot(snapshot, last_n=2)

        self.assertEqual(result["current_r04"], {"null": 2})
        self.assertEqual(result["projection"]["would_be_filled"], 1)
        self.assertEqual(
            result["projection"]["newly_fillable_vs_current_record"], 1
        )

    def test_both_live_serializer_twins_use_candidate_identity(self):
        self.assertEqual(
            replay.verify_serializer_identity(ROOT / "shadow_dispatcher.py"),
            {
                "_serialize_candidate": "c.courier_id",
                "_serialize_result": "best.courier_id",
            },
        )

    def test_mutation_neutralizing_both_fixes_is_rejected(self):
        source = (ROOT / "shadow_dispatcher.py").read_text(encoding="utf-8")
        mutated = source.replace(
            'str(c.courier_id or "")', 'str(m.get("courier_id") or "")', 1
        ).replace(
            'str(best.courier_id or "")',
            'str(best_m.get("courier_id") or "")',
            1,
        )
        self.assertNotEqual(mutated, source)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shadow_dispatcher_mutated.py"
            path.write_text(mutated, encoding="utf-8")
            with self.assertRaises(replay.SerializerIdentityError):
                replay.verify_serializer_identity(path)


if __name__ == "__main__":
    unittest.main()
