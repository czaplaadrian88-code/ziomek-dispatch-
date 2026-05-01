"""V3.28 R-04 v2.0 Phase 2 apply unit tests.

Filter logic + cooldown + gold guard + atomic write.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.r04_apply import (
    _build_eligible_changes,
    _cooldown_active,
    _last_applied_per_cid,
    _atomic_write_courier_tiers,
    _apply_changes,
)


def _make_suggestion(**kw):
    base = {
        "cid": "100",
        "name": "Test",
        "current_tier": "std",
        "suggested_tier": "standard_plus",
        "tier_match": False,
        "insufficient_data": False,
        "gold_candidate": False,
        "reasoning": "test",
    }
    base.update(kw)
    return base


SCHEMA = {"_meta": {"promotion_cooldown_days": 7}}


class TestEligibleFilter(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 5, 1, 16, 0, tzinfo=timezone.utc)
        self.last_applied = {}

    def test_promotion_eligible(self):
        sug = {"509": _make_suggestion(cid="509", name="Dariusz M",
                                        current_tier="std", suggested_tier="standard_plus")}
        b = _build_eligible_changes(sug, SCHEMA, {}, self.now)
        self.assertEqual(len(b["eligible"]), 1)
        self.assertEqual(b["eligible"][0]["cid"], "509")

    def test_skip_match(self):
        sug = {"400": _make_suggestion(cid="400", name="Adrian R",
                                        current_tier="std+", suggested_tier="standard_plus",
                                        tier_match=True)}
        b = _build_eligible_changes(sug, SCHEMA, {}, self.now)
        self.assertEqual(len(b["eligible"]), 0)
        self.assertEqual(len(b["skip_match"]), 1)

    def test_skip_insufficient(self):
        sug = {"522": _make_suggestion(cid="522", name="Szymon Sa",
                                        insufficient_data=True)}
        b = _build_eligible_changes(sug, SCHEMA, {}, self.now)
        self.assertEqual(len(b["eligible"]), 0)
        self.assertEqual(len(b["skip_insufficient"]), 1)

    def test_skip_gold_current(self):
        """Gold preserved (manual only) — even gdy demotion suggested."""
        sug = {"123": _make_suggestion(cid="123", name="Bartek O.",
                                        current_tier="gold", suggested_tier="standard_plus",
                                        tier_match=False)}
        b = _build_eligible_changes(sug, SCHEMA, {}, self.now)
        self.assertEqual(len(b["eligible"]), 0)
        self.assertEqual(len(b["skip_gold_current"]), 1)

    def test_skip_gold_target(self):
        """Schema NIGDY auto-promote do gold."""
        sug = {"509": _make_suggestion(cid="509", current_tier="standard_plus",
                                        suggested_tier="gold")}
        b = _build_eligible_changes(sug, SCHEMA, {}, self.now)
        self.assertEqual(len(b["eligible"]), 0)
        self.assertEqual(len(b["skip_gold_target"]), 1)

    def test_skip_cooldown(self):
        """Cid changed 3 days ago — cooldown 7d → skip."""
        recent = (self.now - timedelta(days=3)).isoformat()
        last_applied = {"509": recent}
        sug = {"509": _make_suggestion(cid="509", current_tier="std",
                                        suggested_tier="standard_plus")}
        b = _build_eligible_changes(sug, SCHEMA, last_applied, self.now)
        self.assertEqual(len(b["eligible"]), 0)
        self.assertEqual(len(b["skip_cooldown"]), 1)
        self.assertIn("3d", b["skip_cooldown"][0]["cooldown_reason"])

    def test_cooldown_expired(self):
        """Cid changed 8 days ago — cooldown expired → eligible."""
        old = (self.now - timedelta(days=8)).isoformat()
        last_applied = {"509": old}
        sug = {"509": _make_suggestion(cid="509", current_tier="std",
                                        suggested_tier="standard_plus")}
        b = _build_eligible_changes(sug, SCHEMA, last_applied, self.now)
        self.assertEqual(len(b["eligible"]), 1)

    def test_cid_filter(self):
        sug = {
            "509": _make_suggestion(cid="509", current_tier="std", suggested_tier="standard_plus"),
            "393": _make_suggestion(cid="393", current_tier="std", suggested_tier="standard_plus"),
        }
        b = _build_eligible_changes(sug, SCHEMA, {}, self.now, cid_filter={"509"})
        self.assertEqual(len(b["eligible"]), 1)
        self.assertEqual(b["eligible"][0]["cid"], "509")


class TestCooldownActive(unittest.TestCase):
    def test_no_history(self):
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        self.assertIsNone(_cooldown_active("509", {}, 7, now))

    def test_within_cooldown(self):
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        past = (now - timedelta(days=2)).isoformat()
        reason = _cooldown_active("509", {"509": past}, 7, now)
        self.assertIsNotNone(reason)
        self.assertIn("cooldown", reason)

    def test_after_cooldown(self):
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        past = (now - timedelta(days=10)).isoformat()
        self.assertIsNone(_cooldown_active("509", {"509": past}, 7, now))


class TestApplyAtomic(unittest.TestCase):
    def test_apply_writes_atomically_with_backup(self):
        with tempfile.TemporaryDirectory() as td:
            tiers_path = os.path.join(td, "courier_tiers.json")
            evol_path = os.path.join(td, "tier_evolution.jsonl")
            initial = {
                "_meta": {"schema_version": "v1"},
                "509": {"name": "Dariusz M", "bag": {"tier": "std", "cap_override": None}},
            }
            with open(tiers_path, "w") as f:
                json.dump(initial, f)
            now = datetime(2026, 5, 1, 16, tzinfo=timezone.utc)
            eligible = [{
                "cid": "509", "name": "Dariusz M",
                "current_tier_short": "std", "suggested_tier_short": "std+",
            }]
            result = _apply_changes(eligible, tiers_path, evol_path, now)
            # Check file updated
            after = json.load(open(tiers_path))
            self.assertEqual(after["509"]["bag"]["tier"], "std+")
            self.assertIn("last_r04_apply", after["_meta"])
            # Check backup exists
            self.assertTrue(os.path.exists(result["backup_path"]))
            backup = json.load(open(result["backup_path"]))
            self.assertEqual(backup["509"]["bag"]["tier"], "std")
            # Check evolution log entry
            with open(evol_path) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0]["applied"])
            self.assertEqual(lines[0]["new_tier"], "std+")

    def test_apply_skips_missing_cid(self):
        with tempfile.TemporaryDirectory() as td:
            tiers_path = os.path.join(td, "courier_tiers.json")
            evol_path = os.path.join(td, "tier_evolution.jsonl")
            with open(tiers_path, "w") as f:
                json.dump({"_meta": {}}, f)  # no 509 entry
            now = datetime(2026, 5, 1, tzinfo=timezone.utc)
            eligible = [{
                "cid": "509", "name": "Dariusz M",
                "current_tier_short": "std", "suggested_tier_short": "std+",
            }]
            result = _apply_changes(eligible, tiers_path, evol_path, now)
            # No-op: no entry to mutate
            self.assertEqual(result["applied_count"], 0)


class TestLastAppliedPerCid(unittest.TestCase):
    def test_picks_latest_applied_only(self):
        with tempfile.TemporaryDirectory() as td:
            evol_path = os.path.join(td, "tier_evolution.jsonl")
            entries = [
                {"ts": "2026-04-25T10:00:00+00:00", "cid": "509", "applied": True},
                {"ts": "2026-04-28T10:00:00+00:00", "cid": "509", "applied": True},
                {"ts": "2026-04-30T10:00:00+00:00", "cid": "509", "applied": False},  # ignore
                {"ts": "2026-05-01T10:00:00+00:00", "cid": "393", "applied": True},
            ]
            with open(evol_path, "w") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")
            out = _last_applied_per_cid(evol_path)
            self.assertEqual(out["509"], "2026-04-28T10:00:00+00:00")
            self.assertEqual(out["393"], "2026-05-01T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
