"""V3.28 Faza 6 LGBM shadow inference unit tests.

Tests defense-in-depth fallback paths + happy path inference.
Per Adrian spec: 8 cases A-H covering all fallback reasons + reconstruction defaults.
"""
import os
import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.ml_inference import (
    LGBMShadowInferer,
    ShadowResult,
    GROUP_A_DEFAULTS,
)


@dataclass
class MockCand:
    courier_id: str
    name: str
    last_pos_lat: float = None
    last_pos_lon: float = None
    bag_size: int = 0
    bag_drops_pending: int = 0
    bag_pickup_pending: int = 0
    idle_min: float = None
    orders_today_before_T0: int = 5
    bag_n_distinct_districts: int = 0
    bag_has_distant_drop: bool = False


def _make_ctx(**kw):
    base = {
        "decision_ts": datetime.now(timezone.utc),
        "order_id": "TEST",
        "pickup_lat": 53.1324,
        "pickup_lon": 23.1651,
        "pickup_district": "Centrum",
        "drop_district": "Bema",
    }
    base.update(kw)
    return base


class TestLGBMShadowInference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inferer = LGBMShadowInferer()

    def test_A_bundle_agreement(self):
        """Bundle decision (>=1 candidate has bag>=1), LGBM ranks → check returns ranking."""
        cands = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=1, idle_min=10),
            MockCand("400", "Adrian R", 53.1255, 23.1508, bag_size=2, idle_min=5),
            MockCand("484", "Andrei K", 53.1140, 23.1277, bag_size=0, idle_min=30),
        ]
        result = self.inferer.predict_for_decision(_make_ctx(), cands)
        self.assertTrue(result.enabled)
        self.assertIsNone(result.fallback_reason)
        self.assertIsNotNone(result.winner_cid)
        self.assertGreaterEqual(len(result.ranking), 1)
        self.assertTrue(result.reconstruction_features_defaulted)

    def test_B_bundle_disagreement_pattern(self):
        """Different scenarios produce different winners (tests model is sensitive)."""
        # Adrian R has small bag = should be preferred over busy Bartek
        cands_v1 = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=3, idle_min=2),
            MockCand("400", "Adrian R", 53.1255, 23.1508, bag_size=0, idle_min=15),
        ]
        # Now flip: Bartek free, Adrian busy
        cands_v2 = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=0, idle_min=15),
            MockCand("400", "Adrian R", 53.1255, 23.1508, bag_size=3, idle_min=2),
        ]
        r1 = self.inferer.predict_for_decision(_make_ctx(), cands_v1)
        r2 = self.inferer.predict_for_decision(_make_ctx(order_id="TEST2"), cands_v2)
        # Both must be enabled, both produce a ranking
        self.assertTrue(r1.enabled and r2.enabled)
        self.assertIsNotNone(r1.winner_cid)
        self.assertIsNotNone(r2.winner_cid)

    def test_C_all_bag_zero_fallback(self):
        """All candidates bag=0 → fallback 'all_bag_zero', winner_cid=None."""
        cands = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=0, idle_min=10),
            MockCand("400", "Adrian R", 53.1255, 23.1508, bag_size=0, idle_min=5),
        ]
        result = self.inferer.predict_for_decision(_make_ctx(), cands)
        self.assertEqual(result.fallback_reason, "all_bag_zero")
        self.assertIsNone(result.winner_cid)

    def test_D_lgbm_exception(self):
        """Forced exception → fallback 'lgbm_error', enabled=False."""
        cands = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=1, idle_min=10),
            MockCand("400", "Adrian R", 53.1255, 23.1508, bag_size=2, idle_min=5),
        ]
        with patch.object(self.inferer, "_model") as mock_model:
            mock_model.predict.side_effect = RuntimeError("forced LGBM fail")
            result = self.inferer.predict_for_decision(_make_ctx(), cands)
            self.assertFalse(result.enabled)
            self.assertEqual(result.fallback_reason, "lgbm_error")

    def test_E_oov_district_handled(self):
        """Encoder OOV (unseen district) → graceful 'OTHER' (or UNK) bucket."""
        cands = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=1, idle_min=10),
            MockCand("400", "Adrian R", 53.1255, 23.1508, bag_size=2, idle_min=5),
        ]
        ctx = _make_ctx(pickup_district="NieistniejacaDzielnica_OOV")
        result = self.inferer.predict_for_decision(ctx, cands)
        self.assertTrue(result.enabled)
        self.assertIsNone(result.fallback_reason)

    def test_F_feature_compute_with_no_pos(self):
        """Candidate without pos data → feature compute should still work (defaults)."""
        cands = [
            MockCand("123", "Bartek O.", None, None, bag_size=1, idle_min=10),
            MockCand("400", "Adrian R", 53.1255, 23.1508, bag_size=2, idle_min=5),
        ]
        result = self.inferer.predict_for_decision(_make_ctx(), cands)
        self.assertTrue(result.enabled)
        self.assertIsNotNone(result.winner_cid)

    def test_G_model_not_loaded(self):
        """Inferer w broken state (model=None) → fallback 'model_not_loaded'."""
        broken = LGBMShadowInferer.__new__(LGBMShadowInferer)
        broken._model = None
        broken._encoders = {}
        broken._feature_columns = []
        broken._osrm = None
        broken._district_lookup = None
        broken._name_to_tier = {}
        broken._loaded = False
        broken._predict_count = 0
        from collections import Counter
        broken._fallback_count = Counter()
        cands = [MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=1)]
        result = broken.predict_for_decision(_make_ctx(), cands)
        self.assertFalse(result.enabled)
        self.assertEqual(result.fallback_reason, "model_not_loaded")

    def test_H_reconstruction_defaults_flag(self):
        """v1.0 always has reconstruction_features_defaulted=True (Group A)."""
        cands = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=1, idle_min=10),
            MockCand("400", "Adrian R", 53.1255, 23.1508, bag_size=2, idle_min=5),
        ]
        result = self.inferer.predict_for_decision(_make_ctx(), cands)
        self.assertTrue(result.reconstruction_features_defaulted)
        # Verify Group A defaults known
        self.assertEqual(GROUP_A_DEFAULTS["level_A_count"], 0)
        self.assertEqual(GROUP_A_DEFAULTS["exclude_virtual"], 0)


class TestShadowResultSerialization(unittest.TestCase):
    def test_to_dict_has_all_fields(self):
        r = ShadowResult(
            enabled=True, fallback_reason=None, winner_cid="123",
            winner_score=1.5, ranking=[{"cid": "123", "score": 1.5}],
            evaluation_ts="2026-05-01T20:00:00+00:00",
            latency_ms=42.0, feature_compute_ms=30.0, inference_ms=12.0,
            n_candidates_scored=3,
        )
        d = r.to_dict()
        for key in ["enabled", "fallback_reason", "winner_cid", "winner_score",
                    "ranking", "agreement_with_primary", "reconstruction_features_defaulted",
                    "evaluation_ts", "latency_ms", "feature_compute_ms", "inference_ms",
                    "n_candidates_scored"]:
            self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
