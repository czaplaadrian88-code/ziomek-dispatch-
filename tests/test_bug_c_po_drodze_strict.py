"""Bug C regression guard (sprint 2026-04-25):
"po drodze" strict mode — time proximity + intervening stops checks.

Pre-fix: dispatch_pipeline.py:850 bundle_level3 fires na geometric only
(dev<2.0 km). Adrian's case #468404 Maison 1.02 km od Sweet Fit fires "po drodze"
ALE realnie 33 min apart + 2 intervening stops.

Strict mode flag-gated. Configurable thresholds w common.py:
- PO_DRODZE_DIST_KM (default 2.0)
- PO_DRODZE_TIME_DIFF_MIN (default 10)
- PO_DRODZE_MAX_INTERVENING (default 0)
- ENABLE_V326_PO_DRODZE_STRICT (default False = legacy)

AST-based test (full runtime simulation wymaga full pipeline z fleet, plan, etc.).
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from dispatch_v2 import common  # noqa: E402

DISPATCH_PIPELINE = pathlib.Path(__file__).resolve().parents[1] / "dispatch_pipeline.py"


def test_thresholds_defined():
    """Configurable thresholds w common.py."""
    assert hasattr(common, "PO_DRODZE_DIST_KM")
    assert common.PO_DRODZE_DIST_KM == 2.0
    assert hasattr(common, "PO_DRODZE_TIME_DIFF_MIN")
    assert common.PO_DRODZE_TIME_DIFF_MIN == 10
    assert hasattr(common, "PO_DRODZE_MAX_INTERVENING")
    assert common.PO_DRODZE_MAX_INTERVENING == 0


def test_flag_default_value_post_flip():
    """Flag flipped True 2026-04-25 (commit 291b5a3, post Adrian's #468461 feedback).
    Pre-flip był False; post-flip True. Test reflects current state."""
    assert hasattr(common, "ENABLE_V326_PO_DRODZE_STRICT")
    assert common.ENABLE_V326_PO_DRODZE_STRICT is True


def test_strict_block_present_in_pipeline():
    """Bug C strict block w dispatch_pipeline.py."""
    src = DISPATCH_PIPELINE.read_text()
    markers = [
        "Bug C strict mode (2026-04-25)",
        "ENABLE_V326_PO_DRODZE_STRICT",
        "_time_proximate",
        "_intervening_count",
        "_strict_fail",
    ]
    missing = [m for m in markers if m not in src]
    assert not missing, f"Bug C markers missing: {missing}"


def test_dist_threshold_uses_constant():
    """Hardcoded `< 2.0` zastąpiony przez configurable PO_DRODZE_DIST_KM."""
    src = DISPATCH_PIPELINE.read_text()
    assert "PO_DRODZE_DIST_KM" in src
    # Sanity: nie ma już bareface "< 2.0" w bundle_level3 context
    idx = src.find("dev = _min_dist_to_route_km")
    fragment = src[idx:idx + 600]
    # Should use _po_drodze_dist_km variable
    assert "_po_drodze_dist_km" in fragment


def test_strict_clears_bundle_level3_on_fail():
    """Failed strict check → bundle_level3 = False, bundle_level3_dev = None."""
    src = DISPATCH_PIPELINE.read_text()
    idx = src.find("Bug C strict mode (2026-04-25)")
    assert idx >= 0
    fragment = src[idx:idx + 4000]
    assert "bundle_level3 = False" in fragment
    assert "bundle_level3_dev = None" in fragment


if __name__ == "__main__":
    test_thresholds_defined()
    print("test_thresholds_defined: PASS")
    test_flag_default_value_post_flip()
    print("test_flag_default_value_post_flip: PASS")
    test_strict_block_present_in_pipeline()
    print("test_strict_block_present_in_pipeline: PASS")
    test_dist_threshold_uses_constant()
    print("test_dist_threshold_uses_constant: PASS")
    test_strict_clears_bundle_level3_on_fail()
    print("test_strict_clears_bundle_level3_on_fail: PASS")
    print("ALL 5/5 PASS")
