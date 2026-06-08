"""FAZA 2 — testy warstwy weryfikacji geokodu (items 2,3,4 + pin item 5)."""
from dispatch_v2 import geocode_verify as gv
from dispatch_v2.geocoding import _is_pinned_entry

# proste stuby dzielnic (czyste, deterministyczne)
def _exp_centrum(addr, city): return "Centrum"
def _act_centrum(lat, lon): return "Centrum"
def _act_jaroszowka(lat, lon): return "Jaroszówka"
def _adj_never(a, b): return False
def _adj_always(a, b): return True

BASE = dict(
    expected_district_fn=_exp_centrum,
    districts_adjacent_fn=_adj_never,
    cross_source_max_disagree_m=400.0,
)


def test_clean_result_ok():
    v = gv.verify("Lipowa 12", "Białystok", 53.133, 23.154,
                  location_type="ROOFTOP", partial_match=False,
                  actual_district_fn=_act_centrum,
                  cross_source=True, cross_source_coords=(53.1331, 23.1541), **BASE)
    assert v["confidence"] == "ok", v


def test_partial_match_only_is_low():
    v = gv.verify("Lipowa 12", "Białystok", 53.133, 23.154,
                  location_type="ROOFTOP", partial_match=True,
                  actual_district_fn=_act_centrum,
                  cross_source=False, cross_source_coords=None, **BASE)
    assert v["confidence"] == "low", v


def test_single_district_mismatch_is_low():
    # 1 mocny sygnał, brak cross-source → low (nie reject — unikamy false-reject)
    v = gv.verify("Lipowa 12", "Białystok", 53.16, 23.21,
                  location_type="ROOFTOP", partial_match=False,
                  actual_district_fn=_act_jaroszowka,
                  cross_source=False, cross_source_coords=None, **BASE)
    assert v["confidence"] == "low", v
    assert any("district" in r for r in v["reasons"])


def test_district_mismatch_plus_cross_source_disagree_is_reject():
    # Scenariusz Magazynowa→Malachitowa: zła dzielnica + drugie źródło daleko
    v = gv.verify("Magazynowa 3", "Białystok", 53.16, 23.21,    # zły (NE)
                  location_type="ROOFTOP", partial_match=False,
                  actual_district_fn=_act_jaroszowka,
                  cross_source=True, cross_source_coords=(53.114, 23.126),  # prawdziwy SW
                  **BASE)
    assert v["confidence"] == "reject", v


def test_partial_match_plus_district_mismatch_is_reject():
    v = gv.verify("Lipowa 12", "Białystok", 53.16, 23.21,
                  location_type="APPROXIMATE", partial_match=True,
                  actual_district_fn=_act_jaroszowka,
                  cross_source=False, cross_source_coords=None, **BASE)
    assert v["confidence"] == "reject", v


def test_adjacent_districts_not_a_hard_signal():
    v = gv.verify("Lipowa 12", "Białystok", 53.14, 23.16,
                  location_type="ROOFTOP", partial_match=False,
                  actual_district_fn=_act_jaroszowka,
                  districts_adjacent_fn=_adj_always,   # sąsiednie → tylko soft
                  expected_district_fn=_exp_centrum,
                  cross_source=False, cross_source_coords=None,
                  cross_source_max_disagree_m=400.0)
    assert v["confidence"] == "low", v


def test_cross_source_agreement_keeps_ok():
    v = gv.verify("Lipowa 12", "Białystok", 53.133, 23.154,
                  location_type="ROOFTOP", partial_match=False,
                  actual_district_fn=_act_centrum,
                  cross_source=True, cross_source_coords=(53.1332, 23.1543), **BASE)
    assert v["confidence"] == "ok", v
    assert v["checks"]["cross_source_disagree_m"] < 400


def test_haversine_m_sanity():
    d = gv.haversine_m((53.133, 23.154), (53.114, 23.126))
    assert 2500 < d < 3500, d   # ~3 km


# item 5 — pin detection
def test_pin_detection():
    assert _is_pinned_entry({"cached_at": "pinned:panel_ground_truth_2026-05-21"})
    assert _is_pinned_entry({"source": "manual_override"})
    assert _is_pinned_entry({"source": "nominatim_osm_2026-04-18_adrian_verified"})
    assert _is_pinned_entry({"source": "manual_fix_bug2_2026-06-05"})
    assert not _is_pinned_entry({"source": "google", "cached_at": 123456.0})
    assert not _is_pinned_entry({"cached_at": 123456.0})
    assert not _is_pinned_entry(None)
