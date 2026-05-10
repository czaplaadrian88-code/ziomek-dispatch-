"""Tech debt #21 — F7AGREE row vs PROPOSAL_FORMAT_V2 collision guard.

Custom-runner. Verify że gdy oba flags ON: V2 grid renderowany, F7AGREE
pomijany, log warning emit'owany once-per-process.
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

    def test_v2_and_f7agree_both_on_warns_once(self):
        with mock.patch.object(TA, "flag", side_effect=self._flag_side_effect(both_on=True)):
            with mock.patch.object(TA._log, "warning") as mock_warn:
                kbd1 = TA.build_keyboard(
                    order_id="471111", candidates=[], pickup_ready_at=None,
                    decision={"auto_route": "AUTO"}
                )
                kbd2 = TA.build_keyboard(
                    order_id="471112", candidates=[], pickup_ready_at=None,
                    decision={"auto_route": "AUTO"}
                )
                kbd3 = TA.build_keyboard(
                    order_id="471113", candidates=[], pickup_ready_at=None,
                    decision={"auto_route": "AUTO"}
                )
        self.assertIn("inline_keyboard", kbd1)
        self.assertIn("inline_keyboard", kbd2)
        f7_calls = [c for c in mock_warn.call_args_list
                    if "F7AGREE_BUTTONS_ENABLED" in str(c)]
        self.assertEqual(len(f7_calls), 1, "warning once-per-process (3 calls → 1 warn)")
        msg = str(f7_calls[0])
        self.assertIn("PROPOSAL_FORMAT_V2", msg)
        self.assertIn("tech-debt #21", msg)

    def test_v2_grid_no_f7agree_row_in_keyboard(self):
        with mock.patch.object(TA, "flag", side_effect=self._flag_side_effect(both_on=True)):
            with mock.patch.object(TA, "_build_keyboard_v2_grid", return_value=[
                [{"text": "✅ Akceptuj", "callback_data": "ASSIGN:1:2:5"}]
            ]):
                kbd = TA.build_keyboard(
                    order_id="471222", candidates=[], pickup_ready_at=None,
                    decision={"auto_route": "AUTO"}
                )
        all_callbacks = []
        for row in kbd["inline_keyboard"]:
            for btn in row:
                all_callbacks.append(btn.get("callback_data", ""))
        f7_callbacks = [cb for cb in all_callbacks if cb.startswith("F7AGREE")]
        self.assertEqual(len(f7_callbacks), 0, "F7AGREE buttons NIE renderowane w V2 grid")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(TestF7AgreeV2Guard)
    )
    sys.exit(0 if result.wasSuccessful() else 1)
