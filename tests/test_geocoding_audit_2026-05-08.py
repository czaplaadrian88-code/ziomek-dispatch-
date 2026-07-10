"""Tech debt #18 — geocoding_log.jsonl audit trail tests.

Custom-runner (sys.exit) per dispatch_v2 convention.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import geocoding, geocoding_audit


class TestGeocodingAudit(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "geocoding_log.jsonl")
        self._cache_paths = (
            geocoding.CACHE_PATH,
            geocoding.RESTAURANT_CACHE_PATH,
            geocoding.NEG_CACHE_PATH,
        )
        geocoding.CACHE_PATH = Path(self.tmpdir) / "geocode_cache.json"
        geocoding.RESTAURANT_CACHE_PATH = Path(self.tmpdir) / "restaurant_coords.json"
        geocoding.NEG_CACHE_PATH = Path(self.tmpdir) / "geocode_neg_cache.json"
        os.environ["ENABLE_GEOCODING_AUDIT_LOG"] = "1"

    def tearDown(self):
        (
            geocoding.CACHE_PATH,
            geocoding.RESTAURANT_CACHE_PATH,
            geocoding.NEG_CACHE_PATH,
        ) = self._cache_paths
        for f in Path(self.tmpdir).glob("*"):
            f.unlink()
        os.rmdir(self.tmpdir)
        os.environ.pop("ENABLE_GEOCODING_AUDIT_LOG", None)

    def _read_lines(self):
        with open(self.log_path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_record_schema(self):
        geocoding_audit.log_geocode(
            "address", "Mickiewicza 50", "Białystok", 53.13, 23.16,
            "google", 124.5, log_path=self.log_path
        )
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        for field in ("ts_utc", "entity_type", "address", "city", "lat", "lon", "source", "latency_ms"):
            self.assertIn(field, rec)
        self.assertEqual(rec["entity_type"], "address")
        self.assertEqual(rec["source"], "google")
        self.assertAlmostEqual(rec["latency_ms"], 124.5)
        self.assertNotIn("error", rec)

    def test_record_with_error(self):
        geocoding_audit.log_geocode(
            "address", "?", "Białystok", None, None, "none", 5.0,
            error="google_and_osrm_failed", log_path=self.log_path
        )
        rec = self._read_lines()[0]
        self.assertEqual(rec["source"], "none")
        self.assertIsNone(rec["lat"])
        self.assertEqual(rec["error"], "google_and_osrm_failed")

    def test_flag_off_no_log(self):
        os.environ["ENABLE_GEOCODING_AUDIT_LOG"] = "0"
        geocoding_audit.log_geocode(
            "address", "x", "Białystok", 1.0, 2.0, "cache", 0.1,
            log_path=self.log_path
        )
        self.assertFalse(os.path.exists(self.log_path))

    def test_concurrent_append_atomic(self):
        N_THREADS = 5
        N_PER_THREAD = 20

        def worker(tid):
            for i in range(N_PER_THREAD):
                geocoding_audit.log_geocode(
                    "address", f"thread{tid}_call{i}", "Białystok",
                    53.0 + tid * 0.01, 23.0 + i * 0.01, "google", 10.0,
                    log_path=self.log_path
                )

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = self._read_lines()
        self.assertEqual(len(lines), N_THREADS * N_PER_THREAD)
        for rec in lines:
            self.assertIn("ts_utc", rec)
            self.assertEqual(rec["source"], "google")

    def test_log_failure_non_fatal(self):
        with mock.patch("os.open", side_effect=OSError("disk full")):
            geocoding_audit.log_geocode(
                "address", "x", "y", 1.0, 2.0, "cache", 0.1,
                log_path=self.log_path
            )

    def test_geocode_cache_hit_emits_audit(self):
        with mock.patch.object(geocoding_audit, "LOG_PATH", self.log_path):
            with mock.patch.object(geocoding, "_load_cache",
                                    return_value={"mickiewicza 50, białystok": {"lat": 53.13, "lon": 23.16}}):
                with mock.patch.object(geocoding, "_normalize",
                                        return_value="mickiewicza 50, białystok"):
                    result = geocoding.geocode("Mickiewicza 50", city="Białystok")
        self.assertEqual(result, (53.13, 23.16))
        rec = self._read_lines()[0]
        self.assertEqual(rec["source"], "cache")
        self.assertEqual(rec["lat"], 53.13)

    def test_geocode_both_fail_emits_audit_with_error(self):
        with mock.patch.object(geocoding_audit, "LOG_PATH", self.log_path):
            with mock.patch.object(geocoding, "_load_cache", return_value={}):
                with mock.patch.object(geocoding, "_google_geocode", return_value=None):
                    with mock.patch.object(geocoding, "_osrm_fallback", return_value=None):
                        result = geocoding.geocode("Nieznana 999", city="Białystok")
        self.assertIsNone(result)
        rec = self._read_lines()[0]
        self.assertEqual(rec["source"], "none")
        self.assertEqual(rec["error"], "google_and_osrm_failed")
        self.assertIsNone(rec["lat"])

    def test_geocode_osrm_fallback_emits_audit(self):
        with mock.patch.object(geocoding_audit, "LOG_PATH", self.log_path):
            with mock.patch.object(geocoding, "_load_cache", return_value={}):
                with mock.patch.object(geocoding, "_save_cache"):
                    with mock.patch.object(geocoding, "_google_geocode", return_value=None):
                        with mock.patch.object(geocoding, "_osrm_fallback", return_value=(53.20, 23.20)):
                            result = geocoding.geocode("Backup street", city="Białystok")
        self.assertEqual(result, (53.20, 23.20))
        rec = self._read_lines()[0]
        self.assertEqual(rec["source"], "osrm")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(TestGeocodingAudit)
    )
    sys.exit(0 if result.wasSuccessful() else 1)
