"""K16 — hierarchia źródeł pozycji kuriera (charakteryzacja + flaga).

Część 1 (charakteryzująca, GREEN na kodzie SPRZED przenosin): każda gałąź
hierarchii build_fleet_snapshot (gps → bag → recent → store → no_gps) emituje
DOKŁADNIE dotychczasowe `pos_source`/pozycję. To jest pin zachowania dla
przenosin 1:1 do `_resolve_position`.

Część 2 (po K16): flaga `ENABLE_POS_SOURCE_HIERARCHY` — OFF = snapshot bez
adnotacji (bajt-parytet: zero nowych pól dataclassy, zero dynamicznych atrybutów);
ON = addytywna adnotacja `pos_resolution` (Known|Unknown, F-3 minimalnie),
przy IDENTYCZNYCH pos/pos_source/pos_age_min jak OFF (hierarchia sama w sobie
niezmieniona — klasa „równe traktowanie no-GPS" NIETKNIĘTA).
"""
from datetime import datetime, timedelta, timezone
from unittest import mock

from dispatch_v2 import courier_resolver as CR

POS_OK = (53.1400, 23.1500)        # w bboxie Białegostoku
POS_FAR = (52.2297, 21.0122)       # Warszawa — poza bboxem
POS_OK2 = (53.1210, 23.1700)


def _iso(now, age_min):
    return (now - timedelta(minutes=age_min)).isoformat()


def _flag_side_effect(overrides):
    def _f(name, default=False):
        if name in overrides:
            return overrides[name]
        return default
    return _f


def _run_fleet(state, names, gps=None, store=None, flags=None):
    flags = dict(flags or {})
    with mock.patch.object(CR, "_load_kurier_piny", return_value={}), \
         mock.patch.object(CR, "_load_courier_names", return_value=names), \
         mock.patch.object(CR, "_load_gps_positions", return_value=gps or {}), \
         mock.patch.object(CR, "_load_courier_tiers", return_value={}), \
         mock.patch.object(CR, "_load_last_known_pos", return_value=dict(store or {})), \
         mock.patch.object(CR, "_save_last_known_pos", side_effect=lambda upd: None), \
         mock.patch.object(CR, "_load_panel_packs_cache",
                           return_value=(None, {}, None)), \
         mock.patch.object(CR, "flag", side_effect=_flag_side_effect(flags)), \
         mock.patch("dispatch_v2.state_machine.get_all", return_value=state):
        return CR.build_fleet_snapshot()


# ── Część 1: charakteryzacja gałęzi (pin zachowania sprzed przenosin) ────────

def test_char_gps_fresh_in_bbox():
    now = datetime.now(timezone.utc)
    gps = {"470": {"lat": POS_OK[0], "lon": POS_OK[1], "timestamp": _iso(now, 2)}}
    cs = _run_fleet({}, {"470": "A"}, gps=gps)["470"]
    assert cs.pos_source == "gps"
    assert tuple(round(x, 4) for x in cs.pos) == POS_OK
    assert cs.pos_age_min is not None and cs.pos_age_min < 3


def test_char_gps_out_of_bbox_falls_through_to_no_gps():
    now = datetime.now(timezone.utc)
    gps = {"470": {"lat": POS_FAR[0], "lon": POS_FAR[1], "timestamp": _iso(now, 2)}}
    cs = _run_fleet({}, {"470": "A"}, gps=gps)["470"]
    assert cs.pos_source == "no_gps"
    assert tuple(cs.pos) == tuple(CR.BIALYSTOK_CENTER)


def test_char_picked_up_delivery_coords_f4_off():
    now = datetime.now(timezone.utc)
    state = {"900001": {
        "courier_id": "470", "status": "picked_up",
        "picked_up_at": _iso(now, 10), "updated_at": _iso(now, 10),
        "delivery_coords": list(POS_OK2), "pickup_coords": list(POS_OK),
    }}
    cs = _run_fleet(state, {"470": "A"})["470"]
    assert cs.pos_source == "last_picked_up_delivery"
    assert tuple(round(x, 4) for x in cs.pos) == POS_OK2
    assert len(cs.bag) == 1


def test_char_assigned_pickup_coords():
    now = datetime.now(timezone.utc)
    state = {"900002": {
        "courier_id": "470", "status": "assigned",
        "updated_at": _iso(now, 5),
        "pickup_coords": list(POS_OK), "delivery_coords": list(POS_OK2),
    }}
    cs = _run_fleet(state, {"470": "A"})["470"]
    assert cs.pos_source == "last_assigned_pickup"
    assert tuple(round(x, 4) for x in cs.pos) == POS_OK


def test_char_recent_delivered():
    now = datetime.now(timezone.utc)
    state = {"900003": {
        "courier_id": "470", "status": "delivered",
        "delivered_at": _iso(now, 12), "updated_at": _iso(now, 12),
        "delivery_coords": list(POS_OK2),
    }}
    cs = _run_fleet(state, {"470": "A"})["470"]
    assert cs.pos_source == "last_delivered"
    assert tuple(round(x, 4) for x in cs.pos) == POS_OK2
    assert 11 < cs.pos_age_min < 13


def test_char_store_rescue_before_no_gps():
    now = datetime.now(timezone.utc)
    store = {"470": {"lat": POS_OK[0], "lon": POS_OK[1],
                     "ts": _iso(now, 9), "source": "last_delivered"}}
    cs = _run_fleet({}, {"470": "A"}, store=store,
                    flags={"ENABLE_COURIER_LAST_KNOWN_POS": True})["470"]
    assert cs.pos_source == "last_delivered" and cs.pos_from_store is True


def test_char_no_gps_fallback_center():
    cs = _run_fleet({}, {"470": "A"})["470"]
    assert cs.pos_source == "no_gps"
    assert tuple(cs.pos) == tuple(CR.BIALYSTOK_CENTER)
    assert cs.pos_from_store is False


# ── Część 2: flaga K16 (adnotacja Known|Unknown; aktywne po przenosinach) ────

def _snapshot_key(cs):
    # pos_age_min zaokrąglony: dwa biegi build_fleet_snapshot dzieli ułamek
    # sekundy zegara ściennego (wewnętrzne datetime.now) — to nie jest różnica
    # zachowania, tylko artefakt porównywania dwóch wywołań.
    age = round(cs.pos_age_min, 2) if cs.pos_age_min is not None else None
    return (cs.pos, cs.pos_source, age, cs.pos_from_store)


def _mixed_world(now):
    gps = {"1": {"lat": POS_OK[0], "lon": POS_OK[1], "timestamp": _iso(now, 2)}}
    state = {"900010": {
        "courier_id": "2", "status": "assigned", "updated_at": _iso(now, 5),
        "pickup_coords": list(POS_OK), "delivery_coords": list(POS_OK2),
    }}
    names = {"1": "GPS-owy", "2": "Worki", "3": "Ciemny"}
    return state, names, gps


def test_k16_off_no_annotation_and_legacy_sources():
    """OFF (brak klucza) = bajt-parytet: żadnych nowych atrybutów na CourierState."""
    now = datetime.now(timezone.utc)
    state, names, gps = _mixed_world(now)
    fleet = _run_fleet(state, names, gps=gps)
    for cs in fleet.values():
        assert not hasattr(cs, "pos_resolution"), \
            "OFF nie może dodawać adnotacji (bajt-parytet snapshotu)"


def test_k16_on_annotation_present_positions_identical():
    """ON = adnotacja obecna, a pos/pos_source/age/from_store IDENTYCZNE jak OFF
    (hierarchia niezmieniona — dowód, że K16 tylko porządkuje odczyt źródeł)."""
    now = datetime.now(timezone.utc)
    state, names, gps = _mixed_world(now)
    off = _run_fleet(state, names, gps=gps)
    on = _run_fleet(state, names, gps=gps,
                    flags={"ENABLE_POS_SOURCE_HIERARCHY": True})
    assert set(off) == set(on)
    for kid in off:
        assert _snapshot_key(off[kid]) == _snapshot_key(on[kid]), \
            f"kid={kid}: flaga K16 zmieniła pozycję/źródło — ZABRONIONE"
    # adnotacja: gps = Known, no_gps (fikcja BIALYSTOK_CENTER) = Unknown
    assert on["1"].pos_resolution.known is True
    assert on["1"].pos_resolution.source == "gps"
    assert on["2"].pos_resolution.known is True          # last_assigned_pickup
    assert on["3"].pos_resolution.known is False         # no_gps
    assert on["3"].pos_resolution.source == "no_gps"


def test_k16_known_classification_single_source_of_truth():
    """`is_position_known` = JEDYNE miejsce klasyfikacji (F-3 minimalnie):
    fikcje/syntetyki = Unknown, realne kotwice = Known."""
    for src in ("no_gps", "pre_shift", "none", None,
                "post_shift_start_synthetic", "working_override_synthetic"):
        assert CR.is_position_known(src) is False, src
    for src in ("gps", "last_picked_up_interp", "last_picked_up_pickup",
                "last_picked_up_delivery", "last_assigned_pickup",
                "last_delivered", "last_picked_up_recent"):
        assert CR.is_position_known(src) is True, src
