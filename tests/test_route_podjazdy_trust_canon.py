"""trust_canon (2026-06-28, faseta #3 rozjazd konsola↔apka).

route_podjazdy.order_podjazdy domyślnie wymusza carried (picked_up) na sam przód
i batchuje odbiory w podjazdy — apka rozjeżdżała się z konsolą (która renderuje
kanon Ziomka verbatim z relaxem 22.06 „odbierz po drodze zanim dowieziesz niesione").
trust_canon=True → render kanonu (courier_plans) WPROST, lustro
fleet_state._order_from_plan_seq → apka == konsola == kanon.

Dowód:
  - ON≠OFF na case'ie carried+kolokowany odbiór (Bartek 123: 877 carried, 911/944 odbiór),
  - ON == kanon verbatim (przeplot), OFF = carried-first (stary),
  - real case bez carried (cid 75: re-sekwencja odbiorów) ON == kanon,
  - plan niepełny / brak planu → fallback (ON == OFF) — zero regresji.
"""
from dispatch_v2 import route_podjazdy as rp


def _o(oid, status, restaurant, ck=None):
    return {"order_id": oid, "status": status, "restaurant": restaurant,
            "czas_kuriera_warsaw": ck}


# ── Case Bartek 123: niesione 877 (Chicago→Zaułek) + odbiory 911 (Sioux) + 944 (Mama Thai).
# Kanon Ziomka (= to co pokazuje konsola): odbierz 911, odbierz 944, DOPIERO potem dowieź
# niesione 877, potem 944, 911. Apka bez fixu force'owała d877 na sam przód.
_BARTEK_BAG = [
    _o("877", "picked_up", "Chicago Pizza", "2026-06-28T14:00:00+02:00"),
    _o("911", "assigned", "Restauracja Sioux", "2026-06-28T14:02:00+02:00"),
    _o("944", "assigned", "Mama Thai Bistro", "2026-06-28T14:02:00+02:00"),
]
_BARTEK_PLAN = {"stops": [
    {"type": "pickup", "order_id": "877"},   # carried — _canon pominie ten węzeł
    {"type": "pickup", "order_id": "911"},
    {"type": "pickup", "order_id": "944"},
    {"type": "dropoff", "order_id": "877"},
    {"type": "dropoff", "order_id": "944"},
    {"type": "dropoff", "order_id": "911"},
]}
_BARTEK_CANON = [
    ("pickup", ["911"]),
    ("pickup", ["944"]),
    ("dropoff", ["877"]),
    ("dropoff", ["944"]),
    ("dropoff", ["911"]),
]


def test_trust_canon_on_renders_canon_verbatim():
    got = rp.order_podjazdy(_BARTEK_BAG, _BARTEK_PLAN, trust_canon=True)
    assert got == _BARTEK_CANON, got


def test_trust_canon_off_forces_carried_first():
    off = rp.order_podjazdy(_BARTEK_BAG, _BARTEK_PLAN, trust_canon=False)
    # stary błąd: niesione 877 dowożone NA PRZÓD, przed odbiorami
    assert off[0] == ("dropoff", ["877"]), off


def test_trust_canon_on_differs_from_off():
    on = rp.order_podjazdy(_BARTEK_BAG, _BARTEK_PLAN, trust_canon=True)
    off = rp.order_podjazdy(_BARTEK_BAG, _BARTEK_PLAN, trust_canon=False)
    assert on != off
    assert on[0][0] == "pickup"        # kanon: najpierw odbierz po drodze
    assert off[0][0] == "dropoff"      # stary: najpierw dowieź niesione


# ── Real case cid 75 (monitor q3_route_mismatches): BEZ carried, czysta re-sekwencja.
# Kanon: odbierz 665, dowieź 665, odbierz 673, dowieź 673, odbierz 687+690 (ta sama
# restauracja = jeden stop), dowieź 687, dowieź 690.
_C75_BAG = [
    _o("665", "assigned", "Bar A", "2026-06-28T17:20:00+02:00"),
    _o("673", "assigned", "Bar B", "2026-06-28T17:25:00+02:00"),
    _o("687", "assigned", "Sioux", "2026-06-28T17:30:00+02:00"),
    _o("690", "assigned", "Sioux", "2026-06-28T17:31:00+02:00"),
]
_C75_PLAN = {"stops": [
    {"type": "pickup", "order_id": "665"},
    {"type": "dropoff", "order_id": "665"},
    {"type": "pickup", "order_id": "673"},
    {"type": "dropoff", "order_id": "673"},
    {"type": "pickup", "order_id": "687"},
    {"type": "pickup", "order_id": "690"},
    {"type": "dropoff", "order_id": "687"},
    {"type": "dropoff", "order_id": "690"},
]}
_C75_CANON = [
    ("pickup", ["665"]),
    ("dropoff", ["665"]),
    ("pickup", ["673"]),
    ("dropoff", ["673"]),
    ("pickup", ["687", "690"]),   # ta sama restauracja scalona
    ("dropoff", ["687"]),
    ("dropoff", ["690"]),
]


def test_trust_canon_resequence_without_carried():
    got = rp.order_podjazdy(_C75_BAG, _C75_PLAN, trust_canon=True)
    assert got == _C75_CANON, got


def test_trust_canon_merges_same_restaurant_pickups():
    got = rp.order_podjazdy(_C75_BAG, _C75_PLAN, trust_canon=True)
    pickups = [oids for (t, oids) in got if t == "pickup"]
    assert ["687", "690"] in pickups   # jeden stop, jedna liczba


# ── Fallback: plan nie pokrywa całego worka → None → lokalne podjazdy carried-first.
def test_trust_canon_partial_plan_falls_back_to_off():
    bag = _C75_BAG + [_o("999", "assigned", "Bar C", "2026-06-28T17:40:00+02:00")]
    on = rp.order_podjazdy(bag, _C75_PLAN, trust_canon=True)   # plan bez 999 → coverage fail
    off = rp.order_podjazdy(bag, _C75_PLAN, trust_canon=False)
    assert on == off


def test_trust_canon_no_plan_falls_back_to_off():
    on = rp.order_podjazdy(_BARTEK_BAG, None, trust_canon=True)
    off = rp.order_podjazdy(_BARTEK_BAG, None, trust_canon=False)
    assert on == off


def test_trust_canon_default_off_is_legacy():
    # domyślny arg = OFF (bajt-identyczny ze starym zachowaniem)
    default = rp.order_podjazdy(_BARTEK_BAG, _BARTEK_PLAN)
    off = rp.order_podjazdy(_BARTEK_BAG, _BARTEK_PLAN, trust_canon=False)
    assert default == off
