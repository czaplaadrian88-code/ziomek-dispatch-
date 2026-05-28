"""Sprint R1+CB+KOORD shadow (2026-05-28) — testy 3 helperów + apply gate.

Diagnoza: 28.05 wieczór Adrian zgłosił 2 tragedy-propozycje:
  #476749 Kebab Król → Mieszka I 8B (cos=-0.425, "Z"-route)
  #476777 Rukola Sienkiewicza → Kraszewskiego 45b (cos=-0.991)
Replay 7d (n=1170) — R1 progresywny + V319H guard łapie 19 historycznych
improvements (w tym oba dzisiejsze) + 2 maybe-regresje (KOORD redirect mit.).

Tu testy POMINIĘTE: pełna integracja przez assess_order (osobny smoke E2E).
Skupienie: czyste helpery + flag gating + difficult_case_log fail-soft.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C
from dispatch_v2.dispatch_pipeline import (
    _compute_r1_progressive_delta,
    _compute_v319h_guard_delta,
    _append_difficult_case_log,
)


class TestR1ProgressiveDelta(unittest.TestCase):
    """4 testy: cos<-0.7→-100, cos<-0.5→-60, cos<-0.3→-45, cos>=-0.3→0."""

    def test_critical_antipodal_cosine_minus_71(self):
        # cos=-0.71 < -0.7 (CRITICAL) → new=-100, vs existing -40 → delta -60
        d = _compute_r1_progressive_delta(-0.71, -40.0)
        self.assertEqual(d, -60.0)

    def test_heavy_apart_cosine_minus_06(self):
        # cos=-0.6 in [-0.7, -0.5) → new=-60, vs existing -40 → delta -20
        d = _compute_r1_progressive_delta(-0.6, -40.0)
        self.assertEqual(d, -20.0)

    def test_medium_apart_cosine_minus_04(self):
        # cos=-0.4 in [-0.5, -0.3) → new=-45, vs existing -35 → delta -10
        d = _compute_r1_progressive_delta(-0.4, -35.0)
        self.assertEqual(d, -10.0)

    def test_keep_existing_for_cosine_above_minus_03(self):
        # cos=-0.1 >= -0.3 → 0 (no change)
        d = _compute_r1_progressive_delta(-0.1, -35.0)
        self.assertEqual(d, 0.0)

    def test_none_cosine_safe_zero(self):
        # cos=None (single-drop bag) → 0 (no signal)
        self.assertEqual(_compute_r1_progressive_delta(None, -35.0), 0.0)

    def test_never_lighten_existing(self):
        # cos=-0.8, existing=-150 (hypothetical heavier than R1 progresywny -100)
        # → 0 (delta nie powinno NIGDY zmniejszyć kary)
        d = _compute_r1_progressive_delta(-0.8, -150.0)
        self.assertEqual(d, 0.0)


class TestV319HGuardDelta(unittest.TestCase):
    """4 testy: cos<-0.3+cb>0→-cb, cos>=-0.3→0, cos=None→0, cb<=0→0."""

    def test_guard_zeros_continuation_when_drops_apart(self):
        # cos=-0.4 < -0.3 + cb=+30 → delta -30 (zeruje continuation_bonus)
        d = _compute_v319h_guard_delta(-0.4, 30.0)
        self.assertEqual(d, -30.0)

    def test_no_guard_when_drops_aligned(self):
        # cos=-0.2 >= -0.3 → 0 (zachowaj continuation_bonus)
        d = _compute_v319h_guard_delta(-0.2, 30.0)
        self.assertEqual(d, 0.0)

    def test_skip_when_no_cosine_signal(self):
        # cos=None → 0 (single-drop bag, brak pairwise)
        d = _compute_v319h_guard_delta(None, 30.0)
        self.assertEqual(d, 0.0)

    def test_skip_when_continuation_zero_or_negative(self):
        # cb=0 lub cb<0 (nie ma czego zerować) → 0
        self.assertEqual(_compute_v319h_guard_delta(-0.4, 0.0), 0.0)
        self.assertEqual(_compute_v319h_guard_delta(-0.4, -5.0), 0.0)


class TestDifficultCaseLog(unittest.TestCase):
    """4 testy: append OK, fail-soft, atomic format, dir auto-create."""

    def test_append_writes_jsonl(self):
        """Smoke: 2 wpisy → 2 linie JSONL z poprawnym deserialize."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "logs", "difficult.jsonl")
            with mock.patch.object(C, "DIFFICULT_CASE_LOG_PATH", path):
                _append_difficult_case_log({"order_id": "476749", "score": -50.0})
                _append_difficult_case_log({"order_id": "476777", "score": -55.0})
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["order_id"], "476749")
            self.assertEqual(json.loads(lines[1])["order_id"], "476777")

    def test_fail_soft_on_unwritable_path(self):
        """Exception w pisaniu nie raise — pipeline kontynuuje."""
        with mock.patch.object(C, "DIFFICULT_CASE_LOG_PATH", "/dev/full/cannot/write"):
            try:
                _append_difficult_case_log({"order_id": "test"})
            except Exception as e:
                self.fail(f"_append_difficult_case_log should be fail-soft, raised: {e!r}")

    def test_atomic_appends_no_corruption(self):
        """Wpis non-trivial (zagnieżdżony dict + lista) deserialize OK."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "difficult.jsonl")
            with mock.patch.object(C, "DIFFICULT_CASE_LOG_PATH", path):
                entry = {
                    "order_id": "476749",
                    "payload": {"max_score": -55.5, "floor": -30.0},
                    "candidates": [
                        {"id": "457", "score": -45.2},
                        {"id": "376", "score": -55.5},
                    ],
                }
                _append_difficult_case_log(entry)
            with open(path) as f:
                roundtrip = json.loads(f.readline())
            self.assertEqual(roundtrip["payload"]["max_score"], -55.5)
            self.assertEqual(len(roundtrip["candidates"]), 2)
            self.assertEqual(roundtrip["candidates"][0]["id"], "457")

    def test_dir_autocreate(self):
        """Parent dir nie istnieje → makedirs creates it."""
        with tempfile.TemporaryDirectory() as td:
            # 3 poziomy zagnieżdżenia, żaden nie istnieje
            path = os.path.join(td, "a", "b", "c", "difficult.jsonl")
            with mock.patch.object(C, "DIFFICULT_CASE_LOG_PATH", path):
                _append_difficult_case_log({"order_id": "x"})
            self.assertTrue(os.path.exists(path))


class TestFlagDefaultsOff(unittest.TestCase):
    """Sanity: wszystkie 3 flagi default OFF — shadow-first per sprint plan."""

    def test_flags_default_off(self):
        # Nie mockujemy ENV — sprawdzamy realne defaults załadowanego modułu.
        # (W run-time env może override; tu sprawdzamy że domyślnie '0' = False.)
        self.assertFalse(
            getattr(C, "ENABLE_R1_PROGRESSIVE_CLIP", True),
            "ENABLE_R1_PROGRESSIVE_CLIP must default OFF (shadow-first)",
        )
        self.assertFalse(
            getattr(C, "ENABLE_V319H_CONTINUATION_GUARD", True),
            "ENABLE_V319H_CONTINUATION_GUARD must default OFF (shadow-first)",
        )
        self.assertFalse(
            getattr(C, "ENABLE_DIFFICULT_CASE_KOORD_REDIRECT", True),
            "ENABLE_DIFFICULT_CASE_KOORD_REDIRECT must default OFF (shadow-first)",
        )


if __name__ == "__main__":
    unittest.main()
