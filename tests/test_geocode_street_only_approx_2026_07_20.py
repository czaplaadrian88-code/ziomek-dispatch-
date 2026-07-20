"""Owner 2026-07-20: ulica bez numeru może dostać pin przybliżony.

Kontrakt jest wąski: GEOMETRIC_CENTER + zgodny dystrykt (albo przyległy
przy cross-source <800 m), twardy sufit 1500 m i marker end-to-end.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import geocode_verify as V
from dispatch_v2 import geocoding as G


GOOGLE = (53.150000, 23.200000)


def _north_by(meters: float):
    return (GOOGLE[0] + math.degrees(meters / 6_371_000.0), GOOGLE[1])


def _verify(
    address: str,
    *,
    meters: float = 503.0,
    expected: str = "Jaroszówka",
    actual: str = "Jaroszówka",
    adjacent: bool = False,
    enabled: bool = True,
):
    return V.verify(
        address,
        "Białystok",
        *GOOGLE,
        location_type="GEOMETRIC_CENTER",
        expected_district_fn=lambda _a, _c: expected,
        actual_district_fn=lambda _la, _lo: actual,
        districts_adjacent_fn=lambda _a, _b: adjacent,
        cross_source_coords=_north_by(meters),
        street_only_approx_enabled=enabled,
        street_only_approx_final=True,
    )


def test_wasilkowska_503m_on_accepts_and_off_rejects():
    off = _verify("  Wasilkowska  ", enabled=False)
    on = _verify("  Wasilkowska  ", enabled=True)

    assert off["confidence"] == "reject"
    assert "geocode_street_only_approx" not in off
    assert on["confidence"] == "ok"
    assert on["geocode_street_only_approx"] is True
    assert on["checks"]["geocode_street_only_approx"] is True
    assert on["checks"]["cross_source_disagree_m"] == pytest.approx(503.0, abs=0.2)


def test_jp2_47_pre_fix_reject_stays_reject_when_flag_on():
    verdict = _verify("Aleja Jana Pawła II 47", enabled=True)
    assert verdict["confidence"] == "reject"
    assert "geocode_street_only_approx" not in verdict


def test_street_only_4km_cross_source_stays_reject_when_flag_on():
    verdict = _verify("Wasilkowska", meters=4_000.0, enabled=True)
    assert verdict["confidence"] == "reject"
    assert verdict["checks"]["cross_source_disagree_m"] > 3_999.0
    assert "geocode_street_only_approx" not in verdict


@pytest.mark.parametrize(
    "actual,adjacent,meters,accepted",
    [
        ("Bagnówka", True, 799.0, True),
        ("Bagnówka", True, 800.1, False),
        ("Centrum", False, 503.0, False),
        ("Jaroszówka", False, 1_500.0, True),
        ("Jaroszówka", False, 1_500.1, False),
    ],
)
def test_district_and_distance_boundaries(actual, adjacent, meters, accepted):
    verdict = _verify(
        "Wasilkowska", actual=actual, adjacent=adjacent, meters=meters, enabled=True)
    assert (verdict.get("geocode_street_only_approx") is True) is accepted
    assert (verdict["confidence"] == "ok") is accepted


def test_house_number_detection_uses_normalized_street_part():
    assert V.is_street_only_without_house_number("  Wasilkowska , Białystok ") is True
    assert V.is_street_only_without_house_number("Wasilkowska 12, Białystok") is False
    # Konserwatywnie: cyfra w nazwie ulicy również wyłącza wyjątek.
    assert V.is_street_only_without_house_number("42 Pułku Piechoty") is False


def _patch_real_verifier_world(monkeypatch):
    from dispatch_v2 import district_reverse_lookup as DR

    monkeypatch.setattr(C, "ENABLE_GEOCODE_VERIFICATION", True)
    monkeypatch.setattr(C, "ENABLE_GEOCODE_CROSS_SOURCE", True)
    monkeypatch.setattr(C, "drop_zone_from_address", lambda _a, _c: "Jaroszówka")
    monkeypatch.setattr(
        DR,
        "get_district_lookup",
        lambda: SimpleNamespace(lookup=lambda _la, _lo: "Jaroszówka"),
    )
    monkeypatch.setattr(G._gv, "nominatim_geocode", lambda *_a, **_k: _north_by(503.0))


def test_run_verification_waits_for_cross_source_before_accept(monkeypatch):
    _patch_real_verifier_world(monkeypatch)
    verdict = G._run_verification(
        "Wasilkowska",
        "Białystok",
        *GOOGLE,
        {"location_type": "GEOMETRIC_CENTER"},
        street_only_approx_enabled=True,
    )
    assert verdict["confidence"] == "ok"
    assert verdict["geocode_street_only_approx"] is True
    assert verdict["checks"]["cross_source_disagree_m"] == pytest.approx(503.0, abs=0.2)


def test_geocode_bypasses_old_negative_cache_and_cache_is_rollback_safe(
    monkeypatch, tmp_path
):
    _patch_real_verifier_world(monkeypatch)
    monkeypatch.setattr(G, "CACHE_PATH", tmp_path / "positive.json")
    monkeypatch.setattr(G, "NEG_CACHE_PATH", tmp_path / "negative.json")
    monkeypatch.setattr(G, "_audit_log", lambda *_a, **_k: None)
    monkeypatch.setattr(G, "_pin_memory_fallback", lambda *_a, **_k: None)

    calls = {"google": 0}

    def _google(_query, timeout=5.0):
        calls["google"] += 1
        return (*GOOGLE, {"location_type": "GEOMETRIC_CENTER"})

    monkeypatch.setattr(G, "_google_geocode", _google)
    monkeypatch.setattr(G, "_in_service_bbox", lambda _la, _lo: True)
    monkeypatch.setattr(
        C,
        "flag",
        lambda name, default=False: {
            "ENABLE_GEOCODE_NEGATIVE_CACHE": True,
            "ENABLE_GEOCODE_VERIFICATION_ENFORCE": True,
            "ENABLE_GEOCODE_NOMINATIM_FALLBACK": False,
        }.get(name, default),
    )
    enabled = {"value": False}
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: enabled["value"]
        if name == "ENABLE_GEOCODE_STREET_ONLY_APPROX"
        else False,
    )

    key = G._normalize("Wasilkowska", "Białystok")
    G.NEG_CACHE_PATH.write_text(
        json.dumps({key: {"reason": "verify_reject", "cached_at": time.time()}}),
        encoding="utf-8",
    )

    # OFF: historyczny fail-closed i zero sieci.
    assert G.geocode("Wasilkowska", city="Białystok") is None
    assert calls["google"] == 0

    # ON: stary reject nie blokuje re-weryfikacji; publiczny kontrakt nadal 2-tuple.
    enabled["value"] = True
    coords = G.geocode("Wasilkowska", city="Białystok")
    assert coords == GOOGLE
    assert isinstance(coords, tuple) and len(coords) == 2
    assert G.is_street_only_approx(coords) is True
    assert calls["google"] == 1
    cached = json.loads(G.CACHE_PATH.read_text(encoding="utf-8"))[key]
    assert cached["geocode_street_only_approx"] is True

    # OFF ponownie: approximate-positive cache jest pomijany, a stary neg-cache
    # natychmiast przywraca fail-closed bez restartu i bez kolejnego requestu.
    enabled["value"] = False
    assert G.geocode("Wasilkowska", city="Białystok") is None
    assert calls["google"] == 1


def test_flag_default_registry_and_reference_contract():
    assert C.ENABLE_GEOCODE_STREET_ONLY_APPROX is False
    assert "ENABLE_GEOCODE_STREET_ONLY_APPROX" in C.ETAP4_DECISION_FLAGS

    registry = json.loads(
        (Path(__file__).parents[1] / "tools" / "flag_lifecycle_registry.json")
        .read_text(encoding="utf-8")
    )
    entry = registry["flags"]["ENABLE_GEOCODE_STREET_ONLY_APPROX"]
    assert entry["default"] is False
    assert entry["lifecycle"] == "planned"
    assert "common.py:ETAP4_DECISION_FLAGS" in entry["carriers"]

    reference = (
        Path(__file__).parents[1] / "ZIOMEK_LOGIC_REFERENCE.md"
    ).read_text(encoding="utf-8")
    assert "ENABLE_GEOCODE_STREET_ONLY_APPROX" in reference


def test_new_order_state_persists_marker_without_changing_off_shape(
    monkeypatch, tmp_path
):
    from dispatch_v2 import state_machine as S

    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(S, "decision_flag", lambda _name: False)
    monkeypatch.setattr(S, "_observe_order_event", lambda *_a, **_k: None)

    marked = S.update_from_event(
        {
            "event_type": "NEW_ORDER",
            "order_id": "street-only",
            "payload": {
                "restaurant": "R",
                "delivery_address": "Wasilkowska",
                "delivery_coords": list(GOOGLE),
                "geocode_street_only_approx": True,
            },
        }
    )
    exact = S.update_from_event(
        {
            "event_type": "NEW_ORDER",
            "order_id": "exact",
            "payload": {
                "restaurant": "R",
                "delivery_address": "Wasilkowska 12",
                "delivery_coords": list(GOOGLE),
            },
        }
    )
    assert marked["geocode_street_only_approx"] is True
    assert "geocode_street_only_approx" not in exact
