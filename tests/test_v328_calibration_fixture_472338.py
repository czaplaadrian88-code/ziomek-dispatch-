"""V3.28 tech debt #31: empirical fixture artifact dla calibration sprintów.

Order 472338 Ogniomistrz 10.05.2026 19:13:47 UTC:
- proposed_courier_id=520 (Michał Rom K), score=-20.83
- actual_courier_id=500 (Adrian panel override)
- Geometry: cos=-0.326, deliv_spread=12.63 km, strategy=ortools_rejected_v3274
- Plan sequence: [Grill Kebab×2 deliv → Chicago Pizza pickup → Ogniomistrz pickup
  → Mieszka I delivery → Poligonowa delivery] — zigzag pattern

Motywuje sprintów P3-D5 (R1 corridor recalibration -15→-35 + spread mult) +
P3-D6 path A (trajectory smoothness tie-break) + P3-D6 path B (geometry-blind
KOORD escalation).

Pattern (Adrian doktryna Z3 'buduj na lata'): każdy 'wallpaper test case'
(Adrian rozpoznaje jako bad, Ziomek nie reject'uje) zachować jako fixture
dla regression. Future sprints mogą replay'ować ten case żeby verify
calibration changes (np. score reduction post-fix).
"""
import json
from pathlib import Path


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "calibration_cases" /
    "2026-05-10_472338_ogniomistrz_zigzag.json"
)


def test_fixture_file_exists():
    assert FIXTURE_PATH.exists(), f"Missing fixture: {FIXTURE_PATH}"


def test_fixture_loads_as_json():
    with FIXTURE_PATH.open() as f:
        rec = json.load(f)
    assert isinstance(rec, dict)


def test_fixture_order_id_is_472338():
    with FIXTURE_PATH.open() as f:
        rec = json.load(f)
    assert rec["order_id"] == "472338"


def test_fixture_panel_override_action():
    with FIXTURE_PATH.open() as f:
        rec = json.load(f)
    assert rec["action"] == "PANEL_OVERRIDE"


def test_fixture_proposed_score_negative():
    """Empirical reference: pre-P3-D5 score=-20.83 (po R1 corridor pre-fix)."""
    with FIXTURE_PATH.open() as f:
        rec = json.load(f)
    proposed = rec.get("proposed_score")
    assert proposed is not None
    assert -25.0 < proposed < -15.0, f"Expected score ~-20.83, got {proposed}"


def test_fixture_actual_cid_differs_from_proposed():
    """Adrian override → actual cid różny od proposed."""
    with FIXTURE_PATH.open() as f:
        rec = json.load(f)
    assert rec["proposed_courier_id"] != rec["actual_courier_id"]


def test_fixture_has_decision_payload():
    """Decision payload zawiera plan + metrics — ground truth dla replay."""
    with FIXTURE_PATH.open() as f:
        rec = json.load(f)
    assert "decision" in rec
    decision = rec["decision"]
    assert isinstance(decision, dict)


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
