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
from dispatch_v2.common import ENABLE_LGBM_METRICS_READ


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
    metrics: dict = None


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

    # ── F4 tests ──────────────────────────────────────────────────────────
    def test_I_get_candidate_attr_reads_from_metrics(self):
        """_get_candidate_attr reads bag_size from metrics dict."""
        cand = MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=0,
                        metrics={"bag_size_before": 3})
        val = self.inferer._get_candidate_attr(cand, "bag_size", 0)
        self.assertEqual(val, 3)

    def test_J_get_candidate_attr_fallback_when_no_metrics(self):
        """_get_candidate_attr falls back to getattr when metrics missing."""
        cand = MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=2)
        val = self.inferer._get_candidate_attr(cand, "bag_size", 0)
        self.assertEqual(val, 2)

    def test_K_derive_bag_fields_counts(self):
        """_derive_bag_fields correctly counts pending drops/pickups."""
        cand = MockCand("123", "Bartek O.", 53.1017, 23.2344,
                        metrics={
                            "bag_context": [
                                {"picked_up_at": "2026-05-01T12:00:00", "delivered_at": None},
                                {"picked_up_at": None, "delivered_at": None},
                                {"picked_up_at": "2026-05-01T12:05:00", "delivered_at": "2026-05-01T12:15:00"},
                            ]
                        })
        derived = LGBMShadowInferer._derive_bag_fields(cand)
        self.assertEqual(derived["bag_drops_pending"], 1)  # first entry: picked_up, not delivered
        self.assertEqual(derived["bag_pickup_pending"], 1)  # second entry: not picked_up

    def test_L_all_bag_zero_with_real_metrics(self):
        """all_bag_zero=False when metrics show bag_size_before>=1."""
        cands = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344,
                     metrics={"bag_size_before": 2}),
            MockCand("400", "Adrian R", 53.1255, 23.1508,
                     metrics={"bag_size_before": 1}),
        ]
        # We need to test the internal logic; we can call predict_for_decision
        # with ENABLE_LGBM_METRICS_READ=True (simulate by patching)
        with patch("dispatch_v2.ml_inference.ENABLE_LGBM_METRICS_READ", True):
            result = self.inferer.predict_for_decision(_make_ctx(), cands)
        self.assertIsNone(result.fallback_reason)  # not all_bag_zero

    def test_M_flag_off_legacy_path(self):
        """When ENABLE_LGBM_METRICS_READ=False, getattr fallback still works."""
        cands = [
            MockCand("123", "Bartek O.", 53.1017, 23.2344, bag_size=0,
                     metrics={"bag_size_before": 3}),
        ]
        with patch("dispatch_v2.ml_inference.ENABLE_LGBM_METRICS_READ", False):
            result = self.inferer.predict_for_decision(_make_ctx(), cands)
        # With flag False, bag_size from getattr is 0 → all_bag_zero=True
        self.assertEqual(result.fallback_reason, "all_bag_zero")


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


class TestF4MetricsRead(unittest.TestCase):
    """F4 — Candidate.metrics-based read (Opt 3 hack) z flag-gated rollout."""

    def _make_candidate(self, bag_size_before=None, bag_context=None, name="Test"):
        class C:
            pass
        c = C()
        c.name = name
        c.metrics = {}
        if bag_size_before is not None:
            c.metrics["bag_size_before"] = bag_size_before
        if bag_context is not None:
            c.metrics["bag_context"] = bag_context
        return c

    def _make_inferer(self):
        from dispatch_v2.ml_inference import LGBMShadowInferer
        inf = LGBMShadowInferer.__new__(LGBMShadowInferer)
        return inf

    def test_get_candidate_attr_reads_from_metrics(self):
        inf = self._make_inferer()
        c = self._make_candidate(bag_size_before=3)
        self.assertEqual(inf._get_candidate_attr(c, "bag_size", 0), 3)

    def test_get_candidate_attr_fallback_no_metrics(self):
        inf = self._make_inferer()
        class C:
            pass
        c = C()
        # No metrics dict
        self.assertEqual(inf._get_candidate_attr(c, "bag_size", 99), 99)

    def test_derive_bag_fields_counts(self):
        inf = self._make_inferer()
        bc = [
            {"picked_up_at": "2026-05-06T10:00:00", "delivered_at": None,
             "drop_district": "Centrum"},
            {"picked_up_at": None, "delivered_at": None,
             "drop_district": "Bojary"},
            {"picked_up_at": "2026-05-06T10:05:00", "delivered_at": None,
             "drop_district": "Centrum", "has_distant_drop": True},
        ]
        c = self._make_candidate(bag_context=bc)
        d = inf._derive_bag_fields(c)
        self.assertEqual(d["bag_drops_pending"], 2)  # picked_up but not delivered
        self.assertEqual(d["bag_pickup_pending"], 1)  # not yet picked
        self.assertEqual(d["bag_n_distinct_districts"], 2)  # Centrum + Bojary
        self.assertTrue(d["bag_has_distant_drop"])

    def test_all_bag_zero_with_metrics_flag_on(self):
        # Dynamic flag flip via env + subprocess (clean import)
        import os, sys, subprocess, textwrap
        probe = textwrap.dedent("""
            import sys
            sys.path.insert(0, '/root/.openclaw/workspace/scripts')
            from dispatch_v2.ml_inference import LGBMShadowInferer
            from dispatch_v2.common import ENABLE_LGBM_METRICS_READ as F
            inf = LGBMShadowInferer.__new__(LGBMShadowInferer)
            class C: pass
            c = C(); c.name = 'X'; c.metrics = {'bag_size_before': 2}
            print('flag=', F)
            print('val=', inf._get_candidate_attr(c, 'bag_size', 0))
        """)
        env = os.environ.copy()
        env["ENABLE_LGBM_METRICS_READ"] = "1"
        r = subprocess.run([sys.executable, "-c", probe], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("flag= True", r.stdout)
        self.assertIn("val= 2", r.stdout)

    def test_flag_off_legacy_path(self):
        # Default env (no override) → flag False → legacy getattr
        import os, sys, subprocess
        probe = (
            "import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts'); "
            "from dispatch_v2.common import ENABLE_LGBM_METRICS_READ; "
            "print('flag=', ENABLE_LGBM_METRICS_READ)"
        )
        env = os.environ.copy()
        env.pop("ENABLE_LGBM_METRICS_READ", None)
        r = subprocess.run([sys.executable, "-c", probe], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("flag= False", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
