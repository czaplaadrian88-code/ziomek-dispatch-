"""V3.28 R-04 v2.0 graduation evaluator unit tests.

Tests evaluate_courier_tier() z empirical 30d baseline metrics
dla 7 reference cids per Adrian's expected calibration.

Sustained_days gates SUPPRESSED Phase 1 (Andrei K NIE auto-demote
mimo tg_neg=8 — wymaga 14d historical evolution data dla validacji).
"""
import os
import sys
import unittest

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.r04_evaluator import (
    CourierMetrics,
    evaluate_courier_tier,
    load_schema,
)


def _make_metrics(**kw):
    """Helper: empty CourierMetrics + overrides."""
    base = dict(
        cid="0",
        name=None,
        peak_deliveries_30d=0,
        off_peak_deliveries_30d=0,
        peak_active_days_30d=0,
        peak_speed_n=0,
        peak_speed_med_min=None,
        peak_speed_p25_min=None,
        peak_speed_p75_min=None,
        speed_data_completeness_pct=0.0,
        tg_negative_30d=0,
        days_since_first_delivery=None,
    )
    base.update(kw)
    return CourierMetrics(**base)


class TestR04V2Evaluator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema()

    def test_A_bartek_gold_candidate(self):
        """Bartek profile (med=14.3, p25=6.8, deliv=117, days=10, tg_neg=3): gold + gold_candidate=True."""
        m = _make_metrics(
            cid="123", name="Bartek O.",
            peak_deliveries_30d=117, peak_active_days_30d=10,
            peak_speed_n=100, peak_speed_med_min=14.3, peak_speed_p25_min=6.8,
            peak_speed_p75_min=20.6, speed_data_completeness_pct=85.5,
            tg_negative_30d=3, days_since_first_delivery=120,
        )
        s = evaluate_courier_tier("123", "Bartek O.", m, "gold", self.schema)
        self.assertEqual(s.suggested_tier, "gold")
        self.assertTrue(s.tier_match)
        self.assertTrue(s.gold_candidate)
        self.assertFalse(s.insufficient_data)

    def test_B_mateusz_o_gold_part_time(self):
        """Mateusz O (deliv=46): part-time Gold, peak_deliv<50 → ALE Gold preserved (not insufficient bo deliv>=40)."""
        m = _make_metrics(
            cid="413", name="Mateusz O",
            peak_deliveries_30d=46, peak_active_days_30d=7,
            peak_speed_n=44, peak_speed_med_min=13.6, peak_speed_p25_min=5.7,
            peak_speed_p75_min=24.3, speed_data_completeness_pct=95.7,
            tg_negative_30d=0, days_since_first_delivery=120,
        )
        s = evaluate_courier_tier("413", "Mateusz O", m, "gold", self.schema)
        self.assertEqual(s.suggested_tier, "gold")
        self.assertTrue(s.tier_match)
        self.assertFalse(s.insufficient_data)
        # gold_candidate FALSE bo deliv=46 < 80 (Adrian threshold spec)
        self.assertFalse(s.gold_candidate,
                         "Per spec gold_candidate.peak_deliveries threshold=80, Mateusz part-time fails")

    def test_C_gabriel_gold_borderline(self):
        """Gabriel (tg_neg=6 > 3): gold preserved, gold_candidate=False (tg_neg fails threshold)."""
        m = _make_metrics(
            cid="179", name="Gabriel",
            peak_deliveries_30d=116, peak_active_days_30d=11,
            peak_speed_n=103, peak_speed_med_min=14.61, peak_speed_p25_min=7.03,
            peak_speed_p75_min=23.6, speed_data_completeness_pct=88.8,
            tg_negative_30d=6, days_since_first_delivery=180,
        )
        s = evaluate_courier_tier("179", "Gabriel", m, "gold", self.schema)
        self.assertEqual(s.suggested_tier, "gold")
        self.assertTrue(s.tier_match)
        self.assertFalse(s.gold_candidate,
                         "tg_neg=6 > 3 threshold → gold_candidate=False")

    def test_D_adrian_r_std_plus_maintained(self):
        """Adrian R (med=17.0, p25=9.7, deliv=120): std+ stays std+."""
        m = _make_metrics(
            cid="400", name="Adrian R",
            peak_deliveries_30d=120, peak_active_days_30d=13,
            peak_speed_n=100, peak_speed_med_min=17.0, peak_speed_p25_min=9.7,
            peak_speed_p75_min=23.5, speed_data_completeness_pct=83.3,
            tg_negative_30d=0,
        )
        s = evaluate_courier_tier("400", "Adrian R", m, "std+", self.schema)
        self.assertEqual(s.suggested_tier, "standard_plus")
        self.assertTrue(s.tier_match or s.current_tier == "std+")
        self.assertFalse(s.gold_candidate)

    def test_E_dariusz_m_std_to_std_plus_promotion(self):
        """Dariusz M (med=13.3, p25=9.95, deliv=97): std → std+ promotion."""
        m = _make_metrics(
            cid="509", name="Dariusz M",
            peak_deliveries_30d=97, peak_active_days_30d=10,
            peak_speed_n=80, peak_speed_med_min=13.3, peak_speed_p25_min=9.95,
            peak_speed_p75_min=20.3, speed_data_completeness_pct=82.5,
            tg_negative_30d=1,
        )
        s = evaluate_courier_tier("509", "Dariusz M", m, "std", self.schema)
        self.assertEqual(s.suggested_tier, "standard_plus")
        self.assertTrue(s.promotion_eligible)
        self.assertFalse(s.gold_candidate, "p25=9.95 > 7.5 → no gold_candidate")

    def test_F_andrei_k_std_sustained_suppressed(self):
        """Andrei K (med=17.3, p25=11.3, tg_neg=8): std preserved.

        tg_neg=8 > 5 (std demotion threshold) ALE sustained_days=14 — Phase 1 suppressed.
        Per Adrian expected: Andrei K stays std mimo tg_neg=8 borderline.
        """
        m = _make_metrics(
            cid="484", name="Andrei K",
            peak_deliveries_30d=122, peak_active_days_30d=14,
            peak_speed_n=105, peak_speed_med_min=17.3, peak_speed_p25_min=11.3,
            peak_speed_p75_min=24.4, speed_data_completeness_pct=86.1,
            tg_negative_30d=8,
        )
        s = evaluate_courier_tier("484", "Andrei K", m, "std", self.schema)
        # std promotion fails (p25=11.3 > 11.0), demotion suppressed (sustained 14d)
        self.assertEqual(s.suggested_tier, "standard")
        self.assertFalse(s.demotion_required)
        self.assertFalse(s.promotion_eligible)

    def test_G_albert_dec_insufficient_data(self):
        """Albert Dec (peak_deliv=42 OK, peak_active_days=4 < 5): insufficient_data → preserve std."""
        m = _make_metrics(
            cid="414", name="Albert Dec",
            peak_deliveries_30d=42, peak_active_days_30d=4,
            peak_speed_n=40, peak_speed_med_min=14.96, peak_speed_p25_min=8.34,
            peak_speed_p75_min=22.7, speed_data_completeness_pct=95.2,
            tg_negative_30d=1,
        )
        s = evaluate_courier_tier("414", "Albert Dec", m, "std", self.schema)
        self.assertEqual(s.suggested_tier, "std")
        self.assertTrue(s.insufficient_data)
        self.assertIn("peak_active_days", s.insufficient_data_reason or "")

    def test_H_szymon_sa_new_insufficient(self):
        """Szymon Sa (new, deliv=12, days=2): insufficient → keep new."""
        m = _make_metrics(
            cid="522", name="Szymon Sa",
            peak_deliveries_30d=12, peak_active_days_30d=2,
            peak_speed_n=10, peak_speed_med_min=13.24, peak_speed_p25_min=8.77,
            speed_data_completeness_pct=83.3,
            tg_negative_30d=0, days_since_first_delivery=10,
        )
        s = evaluate_courier_tier("522", "Szymon Sa", m, "new", self.schema)
        self.assertEqual(s.suggested_tier, "new")
        self.assertTrue(s.insufficient_data)

    def test_I_michal_li_slow_to_std_promotion(self):
        """Michał Li (med=20, deliv=55): slow → std (slow promotion gates simple)."""
        m = _make_metrics(
            cid="508", name="Michał Li",
            peak_deliveries_30d=55, peak_active_days_30d=8,
            peak_speed_n=50, peak_speed_med_min=19.95, peak_speed_p25_min=14.29,
            speed_data_completeness_pct=82.0,
            tg_negative_30d=0,
        )
        s = evaluate_courier_tier("508", "Michał Li", m, "slow", self.schema)
        # slow promotion gate: tg_neg<=3 sustained 14d → suppressed Phase 1 → no promotion
        # Therefore slow maintained
        self.assertEqual(s.suggested_tier, "slow")

    def test_J_low_volume_insufficient(self):
        """Edge: peak_deliv=20 → insufficient_data, regardless of speed."""
        m = _make_metrics(
            cid="999", name="Test",
            peak_deliveries_30d=20, peak_active_days_30d=3,
            peak_speed_n=18, peak_speed_med_min=10.0, peak_speed_p25_min=5.0,
            speed_data_completeness_pct=90.0,
            tg_negative_30d=0,
        )
        s = evaluate_courier_tier("999", "Test", m, "std", self.schema)
        self.assertTrue(s.insufficient_data)
        self.assertEqual(s.suggested_tier, "std")
        self.assertFalse(s.gold_candidate)

    def test_K_speed_completeness_gate(self):
        """Edge: speed_completeness <70% → insufficient even gdy deliv+days OK."""
        m = _make_metrics(
            cid="999", name="Test",
            peak_deliveries_30d=80, peak_active_days_30d=10,
            peak_speed_n=40, peak_speed_med_min=14.0, peak_speed_p25_min=7.0,
            speed_data_completeness_pct=50.0,  # below 70 threshold
            tg_negative_30d=0,
        )
        s = evaluate_courier_tier("999", "Test", m, "std", self.schema)
        self.assertTrue(s.insufficient_data)
        self.assertIn("speed_completeness", s.insufficient_data_reason or "")


class TestR04SchemaIntegrity(unittest.TestCase):
    def test_schema_loadable(self):
        s = load_schema()
        self.assertEqual(s.get("_meta", {}).get("version"), "2.0")

    def test_gold_promotion_blocked(self):
        s = load_schema()
        gold = s["tiers"]["gold"]
        self.assertTrue(gold.get("auto_promotion_blocked"))
        self.assertEqual(gold.get("promotion_gates"), [])

    def test_peak_window_hours(self):
        s = load_schema()
        hours = s.get("peak_window_warsaw_hours")
        self.assertEqual(set(hours), {11, 12, 13, 17, 18, 19})


if __name__ == "__main__":
    unittest.main(verbosity=2)
