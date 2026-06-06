"""FAIL-05 (audyt autonomii 2026-06-03): sanity-bbox świeżego GPS przed zaufaniem
mu jako pos_source="gps".

Scenariusz bugu: apka/sensor wysyła skażony fix ((0,0), spike poza region, NaN).
Bramka GPS (courier_resolver.build_fleet_snapshot) ufała mu bezwarunkowo (najwyższy
priorytet pozycji) → zatruty fleet_avg/scoring + OSRM snapuje (0,0) na krawędź
ekstraktu i zwraca code:Ok z ~117 min legiem (Lekcja #140) → propozycje pod prąd.

Fix: świeży GPS poza HOJNYM bboxem Białegostoku (±55km, coords_in_bialystok_bbox)
→ NIE ufaj, fall-through do bag/recent/no_gps (NIGDY (0,0)). Kill-switch
ENABLE_GPS_BBOX_GUARD=false. Parse-guard (try na lat/lon) zawsze ON.

Uruchom:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_fail05_gps_bbox_guard.py -v
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import courier_resolver as cr  # noqa: E402
from dispatch_v2.common import flag as _real_flag  # noqa: E402

VALID = (53.13, 23.16)   # centrum Białegostoku — w bboxie
EDGE_OK = (53.55, 22.85)  # Supraśl/krawędź — nadal w hojnym bboxie (±55km)
POISON_ZERO = (0.0, 0.0)
POISON_FAR = (60.0, 23.16)  # spike daleko poza Podlasiem


def _gps(lat, lon, age_min=1.0, now=None):
    now = now or datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=age_min)).isoformat()
    return {"888": {"timestamp": ts, "lat": lat, "lon": lon}}


def _flag_bbox_off(name, default=False):
    if name == "ENABLE_GPS_BBOX_GUARD":
        return False
    return _real_flag(name, default)


def _run(gps, state=None, flag_bbox_off=False):
    ctx = [
        mock.patch.object(cr, "_load_kurier_piny", return_value={}),
        mock.patch.object(cr, "_load_courier_names", return_value={"888": "GPS Test"}),
        mock.patch.object(cr, "_load_gps_positions", return_value=gps or {}),
        mock.patch.object(cr, "_load_courier_tiers", return_value={}),
        mock.patch("dispatch_v2.state_machine.get_all", return_value=state or {}),
    ]
    if flag_bbox_off:
        ctx.append(mock.patch.object(cr, "flag", side_effect=_flag_bbox_off))
    for c in ctx:
        c.start()
    try:
        return cr.build_fleet_snapshot()
    finally:
        for c in reversed(ctx):
            c.stop()


def test_valid_fresh_gps_in_bbox_trusted():
    cs = _run(_gps(*VALID))["888"]
    assert cs.pos_source == "gps", f"świeży GPS w bboxie powinien być zaufany, jest {cs.pos_source}"
    assert tuple(cs.pos) == VALID


def test_edge_of_metro_still_trusted():
    # Krawędź metropolii (Supraśl) NIE może być fałszywie odrzucona (regresja-guard).
    cs = _run(_gps(*EDGE_OK))["888"]
    assert cs.pos_source == "gps", (
        f"GPS na krawędzi metropolii błędnie odrzucony (pos_source={cs.pos_source}) "
        f"— bbox za ciasny?")
    assert tuple(cs.pos) == EDGE_OK


def test_poison_zero_rejected_falls_to_no_gps():
    cs = _run(_gps(*POISON_ZERO))["888"]
    assert cs.pos_source != "gps", f"(0,0) NIE może być zaufane jako GPS (pos_source={cs.pos_source})"
    assert cs.pos_source == "no_gps", f"oczekiwano fall-through no_gps, jest {cs.pos_source}"
    assert tuple(cs.pos) == tuple(cr.BIALYSTOK_CENTER)
    assert tuple(cs.pos) != POISON_ZERO


def test_poison_far_spike_rejected():
    cs = _run(_gps(*POISON_FAR))["888"]
    assert cs.pos_source != "gps", f"spike poza regionem NIE zaufany (pos_source={cs.pos_source})"
    assert tuple(cs.pos) != POISON_FAR


def test_kill_switch_off_trusts_out_of_bbox():
    # Guard OFF → zachowanie sprzed fixu (ufa GPS nawet poza bbox).
    cs = _run(_gps(*POISON_FAR), flag_bbox_off=True)["888"]
    assert cs.pos_source == "gps", (
        f"przy ENABLE_GPS_BBOX_GUARD=false GPS poza bbox powinien być zaufany "
        f"(kill-switch), jest {cs.pos_source}")
    assert tuple(cs.pos) == POISON_FAR


def test_unparseable_latlon_no_crash_falls_through():
    # Złe lat/lon (string) → parse-guard łapie → fall-through, ZERO crash.
    bad = {"888": {"timestamp": datetime.now(timezone.utc).isoformat(), "lat": "abc", "lon": None}}
    cs = _run(bad)["888"]
    assert cs.pos_source != "gps"
    assert cs.pos_source == "no_gps"


def test_stale_valid_gps_not_used():
    # Świeżość > 5 min → GPS nieużywany niezależnie od bbox (istniejące zachowanie).
    cs = _run(_gps(*VALID, age_min=10))["888"]
    assert cs.pos_source != "gps", f"stary GPS (10min) nie powinien być pos_source=gps, jest {cs.pos_source}"
