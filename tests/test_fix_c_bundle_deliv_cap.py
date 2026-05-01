"""V3.28 FIX_C — Bundle deliv_spread hard cap unit tests.

Bug #469834: cross-restaurant bundle (Raj + Grill Kebab pickup 10m apart) z drops
Wasilkowska NE + Magazynowa S (8.49km road) → Andrei K wygrał score 6.80 przez
bonus_l2 (+20) + bug2_continuation (+30). Gate zeruje obie nagrody gdy bag>=1
i deliv_spread > cap. Default OFF, env ENABLE_BUNDLE_DELIV_SPREAD_CAP=1.

Cases (per design ACK 2026-05-01):
- A: deliv_spread=8.49 + bonus_l2=20 + cont=30 + flag ON + cap=8.0 → zero
- B: deliv_spread=4.31 + bonusy + flag ON + cap=8.0 → no-op (under cap)
- C: deliv_spread=8.49 + bonusy + flag OFF → no-op (gate disabled)
- D: bag_size=0 + bonusy + flag ON → no-op (gate requires bag>=1)
"""
import os
import sys
import unittest
from unittest.mock import patch

# Path setup — same pattern as other tests in suite.
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def _gate_logic(
    enable_flag: bool,
    bag_size: int,
    deliv_spread_km,
    cap_km: float,
    bonus_l2: float,
    bonus_l1: float,
    bonus_r4: float,
    bonus_bug2_continuation: float,
):
    """Replicate FIX_C gate at dispatch_pipeline.py (after V326_WAVE_VETO).

    Returns (bonus_l2, bonus_bug2_continuation, bundle_bonus, fix_c_applied).
    Match the production block exactly.
    """
    fix_c_applied = False
    if (enable_flag
            and bag_size >= 1
            and deliv_spread_km is not None
            and deliv_spread_km > cap_km):
        if bonus_l2 != 0.0 or bonus_bug2_continuation != 0.0:
            fix_c_applied = True
        bonus_l2 = 0.0
        bonus_bug2_continuation = 0.0
    bundle_bonus = bonus_l1 + bonus_l2 + bonus_r4
    return bonus_l2, bonus_bug2_continuation, bundle_bonus, fix_c_applied


class TestFixCBundleDelivCap(unittest.TestCase):
    """Replay #469834 + 3 reference scenarios."""

    def test_A_469834_replay_gate_fires(self):
        """#469834 Andrei K: 8.49km > 8.0km cap, flag ON, bag=1 → bonuses zeroed."""
        l2, cont, bb, applied = _gate_logic(
            enable_flag=True, bag_size=1, deliv_spread_km=8.49, cap_km=8.0,
            bonus_l1=0.0, bonus_l2=20.0, bonus_r4=0.0, bonus_bug2_continuation=30.0,
        )
        self.assertEqual(l2, 0.0)
        self.assertEqual(cont, 0.0)
        self.assertEqual(bb, 0.0)  # bonus_l1=0 + bonus_l2=0 + bonus_r4=0
        self.assertTrue(applied)

    def test_B_under_cap_noop(self):
        """deliv_spread=4.31km (Piotr Zaw scenario) < 8.0km → bonusy zostają."""
        l2, cont, bb, applied = _gate_logic(
            enable_flag=True, bag_size=2, deliv_spread_km=4.31, cap_km=8.0,
            bonus_l1=0.0, bonus_l2=6.9, bonus_r4=0.0, bonus_bug2_continuation=0.0,
        )
        self.assertEqual(l2, 6.9)
        self.assertEqual(cont, 0.0)
        self.assertAlmostEqual(bb, 6.9)
        self.assertFalse(applied)

    def test_C_flag_off_noop(self):
        """deliv_spread=8.49km ALE flag OFF → bonusy zostają."""
        l2, cont, bb, applied = _gate_logic(
            enable_flag=False, bag_size=1, deliv_spread_km=8.49, cap_km=8.0,
            bonus_l1=0.0, bonus_l2=20.0, bonus_r4=0.0, bonus_bug2_continuation=30.0,
        )
        self.assertEqual(l2, 20.0)
        self.assertEqual(cont, 30.0)
        self.assertEqual(bb, 20.0)
        self.assertFalse(applied)

    def test_D_bag_empty_noop(self):
        """bag_size=0 (kurier pusty) → gate nie odpala (pickup-only proposal)."""
        l2, cont, bb, applied = _gate_logic(
            enable_flag=True, bag_size=0, deliv_spread_km=10.0, cap_km=8.0,
            bonus_l1=0.0, bonus_l2=20.0, bonus_r4=0.0, bonus_bug2_continuation=30.0,
        )
        self.assertEqual(l2, 20.0)
        self.assertEqual(cont, 30.0)
        self.assertEqual(bb, 20.0)
        self.assertFalse(applied)

    def test_E_deliv_spread_none_noop(self):
        """deliv_spread_km=None (feasibility nie policzył) → gate skip."""
        l2, cont, bb, applied = _gate_logic(
            enable_flag=True, bag_size=1, deliv_spread_km=None, cap_km=8.0,
            bonus_l1=0.0, bonus_l2=20.0, bonus_r4=0.0, bonus_bug2_continuation=30.0,
        )
        self.assertEqual(l2, 20.0)
        self.assertEqual(cont, 30.0)
        self.assertFalse(applied)

    def test_F_exactly_at_cap_noop(self):
        """deliv_spread=8.0 == cap → strict > check, NIE zero (boundary)."""
        l2, cont, bb, applied = _gate_logic(
            enable_flag=True, bag_size=1, deliv_spread_km=8.0, cap_km=8.0,
            bonus_l1=0.0, bonus_l2=20.0, bonus_r4=0.0, bonus_bug2_continuation=30.0,
        )
        self.assertEqual(l2, 20.0)
        self.assertEqual(cont, 30.0)
        self.assertFalse(applied)

    def test_G_bonus_l1_preserved(self):
        """SR bundle bonus_l1 NIE jest dotknięty przez Fix C — osobny mechanizm."""
        l2, cont, bb, applied = _gate_logic(
            enable_flag=True, bag_size=1, deliv_spread_km=10.78, cap_km=8.0,
            bonus_l1=25.0, bonus_l2=20.0, bonus_r4=0.0, bonus_bug2_continuation=30.0,
        )
        self.assertEqual(l2, 0.0)
        self.assertEqual(cont, 0.0)
        self.assertEqual(bb, 25.0)  # bonus_l1=25 retained, bonus_l2 zeroed
        self.assertTrue(applied)

    def test_H_zero_bonuses_no_log_spam(self):
        """Bonuses już 0 + spread > cap → applied=False (zero log spam)."""
        l2, cont, bb, applied = _gate_logic(
            enable_flag=True, bag_size=1, deliv_spread_km=10.0, cap_km=8.0,
            bonus_l1=0.0, bonus_l2=0.0, bonus_r4=0.0, bonus_bug2_continuation=0.0,
        )
        self.assertEqual(l2, 0.0)
        self.assertEqual(cont, 0.0)
        # bonusy == 0 → no log entry, applied flag remains False (avoid spam).
        self.assertFalse(applied)


class TestFlagDefaults(unittest.TestCase):
    """Sanity: flagi w common.py mają poprawne defaulty."""

    def test_flag_default_off(self):
        from dispatch_v2 import common as C
        # Reload bez env override
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_BUNDLE_DELIV_SPREAD_CAP", None)
            self.assertFalse(C.ENABLE_BUNDLE_DELIV_SPREAD_CAP
                             or os.environ.get("ENABLE_BUNDLE_DELIV_SPREAD_CAP", "0") == "1")

    def test_cap_default_8km(self):
        from dispatch_v2 import common as C
        self.assertEqual(C.BUNDLE_MAX_DELIV_SPREAD_KM, 8.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
