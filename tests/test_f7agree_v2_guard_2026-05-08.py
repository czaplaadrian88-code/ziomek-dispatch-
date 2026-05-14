"""Tech debt #21 RESOLVED 2026-05-14 — F7AGREE row + PROPOSAL_FORMAT_V2 cohabitation.

Custom-runner. Verify że gdy oba flags ON: V2 grid renderowany Z F7AGREE row
appended (mockup v2 strict 4-button preserved dla dispatcher decision, F7AGREE
jest meta-rating classifier verdykt — ortogonalny, log-only).

Pre-resolve (2026-05-08 → 2026-05-14): F7AGREE pomijany, warning emit'owany.
Post-resolve: F7AGREE row appended, zero warning.
"""
import logging
import sys
import unittest
from unittest import mock

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import telegram_approver as TA


class TestF7AgreeV2Guard(unittest.TestCase):

    def setUp(self):
        if hasattr(TA.build_keyboard, "_f7agree_v2_warned"):
            del TA.build_keyboard._f7agree_v2_warned

    def _flag_side_effect(self, both_on=True):
        def side(name, default=False):
            if name == "PROPOSAL_FORMAT_V2":
                return True
            if name == "FAZA7_AGREEMENT_BUTTONS_ENABLED":
                return both_on
            return default
        return side

    def test_v2_on_f7agree_off_no_warning(self):
        with mock.patch.object(TA, "flag", side_effect=self._flag_side_effect(both_on=False)):
            with mock.patch.object(TA._log, "warning") as mock_warn:
                kbd = TA.build_keyboard(
                    order_id="471999", candidates=[], pickup_ready_at=None,
                    decision={"auto_route": "AUTO"}
                )
        self.assertIn("inline_keyboard", kbd)
        f7_calls = [c for c in mock_warn.call_args_list
                    if "F7AGREE_BUTTONS_ENABLED" in str(c)]
        self.assertEqual(len(f7_calls), 0, "no F7AGREE warning gdy F7AGREE OFF")

    def test_v2_and_f7agree_both_on_appends_f7agree_row_no_warning(self):
        """Post-#21-resolve (2026-05-14): F7AGREE row appended, zero warning."""
        with mock.patch.object(TA, "flag", side_effect=self._flag_side_effect(both_on=True)):
            with mock.patch.object(TA._log, "warning") as mock_warn:
                kbd = TA.build_keyboard(
                    order_id="471111", candidates=[],
                    pickup_ready_at=None,
                    decision={"auto_route": "AUTO"},
                )
        self.assertIn("inline_keyboard", kbd)
        f7_calls = [c for c in mock_warn.call_args_list
                    if "F7AGREE_BUTTONS_ENABLED" in str(c)]
        self.assertEqual(len(f7_calls), 0,
                         "no warning post-#21-resolve — F7AGREE row appended cleanly")
        all_callbacks = []
        for row in kbd["inline_keyboard"]:
            for btn in row:
                all_callbacks.append(btn.get("callback_data", ""))
        f7_callbacks = [cb for cb in all_callbacks if cb.startswith("F7AGREE")]
        self.assertEqual(len(f7_callbacks), 3,
                         "F7AGREE row z 3 buttonami (AUTO/ACK/ALERT) appended")

    def test_v2_grid_f7agree_only_when_auto_route_set(self):
        """F7AGREE row pomijany gdy decision.auto_route brak (NIE Faza 7)."""
        with mock.patch.object(TA, "flag", side_effect=self._flag_side_effect(both_on=True)):
            kbd_no_auto = TA.build_keyboard(
                order_id="471222", candidates=[], pickup_ready_at=None,
                decision={},  # brak auto_route
            )
            kbd_none = TA.build_keyboard(
                order_id="471223", candidates=[], pickup_ready_at=None,
                decision=None,
            )
        for kbd, label in [(kbd_no_auto, "decision={}"), (kbd_none, "decision=None")]:
            all_callbacks = []
            for row in kbd["inline_keyboard"]:
                for btn in row:
                    all_callbacks.append(btn.get("callback_data", ""))
            f7_callbacks = [cb for cb in all_callbacks if cb.startswith("F7AGREE")]
            self.assertEqual(len(f7_callbacks), 0,
                             f"F7AGREE pomijany gdy {label}")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(TestF7AgreeV2Guard)
    )
    sys.exit(0 if result.wasSuccessful() else 1)
