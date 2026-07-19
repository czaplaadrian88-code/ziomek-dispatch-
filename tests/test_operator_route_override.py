"""Testy OPERATOR ROUTE-ORDER OVERRIDE (`operator_route_override` + hooki w
`plan_recheck._gen_one_bag_plan` / `_retime_one_bag_plan`).

Zadanie ownera 2026-07-19: koordynator ustawia kolejność podjazdów; kanon
(courier_plans.json) = sekwencja operatora (flaga ON + ważny override), czasy
przeliczane istniejącym `_retime_stops` (łańcuch OSRM + clamp committed).
Kontrakt: operator_route_overrides.json obok orders_state; zbiór id == zbiór
aktywnych zleceń kuriera; TTL od set_at; fail-open na brak/uszkodzenie.

Harness jak test_recanon_on_write: OSRM zamockowany (haversine ~30 km/h),
ścieżki plików izolowane do tmp — zero żywego stanu.
"""
import json
import math
import pathlib
from datetime import datetime, timezone

import pytest

from dispatch_v2 import plan_recheck as P
from dispatch_v2 import plan_manager as PM
from dispatch_v2 import operator_route_override as O
from dispatch_v2 import osrm_client
from dispatch_v2 import route_order
from dispatch_v2 import common as C


def _hav_m(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _fake_table(pts_a, pts_b):
    return [[{"duration_s": _hav_m(a, b) / 8.333} for b in pts_b] for a in pts_a]


CID = "7777"
NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)  # 14:00 Warsaw

ORDERS = {
    "A": {"courier_id": CID, "status": "assigned",
          "czas_kuriera_warsaw": "2026-07-19T14:05:00+02:00",
          "pickup_coords": [53.130, 23.100], "delivery_coords": [53.140, 23.110],
          "restaurant": "Alfa"},
    "B": {"courier_id": CID, "status": "assigned",
          "czas_kuriera_warsaw": "2026-07-19T14:10:00+02:00",
          "pickup_coords": [53.120, 23.160], "delivery_coords": [53.110, 23.170],
          "restaurant": "Beta"},
}

BASE_STOPS = [
    {"order_id": "A", "type": "pickup", "coords": {"lat": 53.130, "lng": 23.100},
     "predicted_at": "2026-07-19T12:05:00+00:00", "dwell_min": 1.0,
     "status_at_plan_time": "assigned"},
    {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.140, "lng": 23.110},
     "predicted_at": "2026-07-19T12:15:00+00:00", "dwell_min": 3.5,
     "status_at_plan_time": "assigned"},
    {"order_id": "B", "type": "pickup", "coords": {"lat": 53.120, "lng": 23.160},
     "predicted_at": "2026-07-19T12:25:00+00:00", "dwell_min": 1.0,
     "status_at_plan_time": "assigned"},
    {"order_id": "B", "type": "dropoff", "coords": {"lat": 53.110, "lng": 23.170},
     "predicted_at": "2026-07-19T12:35:00+00:00", "dwell_min": 3.5,
     "status_at_plan_time": "assigned"},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    ordp = tmp_path / "orders_state.json"
    ordp.write_text(json.dumps(ORDERS))
    monkeypatch.setattr(P, "ORDERS_STATE_PATH", str(ordp))
    monkeypatch.setattr(PM, "PLANS_FILE", pathlib.Path(tmp_path / "courier_plans.json"))
    monkeypatch.setattr(PM, "LOCK_FILE", pathlib.Path(tmp_path / "courier_plans.lock"))
    monkeypatch.setattr(osrm_client, "table", _fake_table)
    monkeypatch.setattr(P, "_load_gps_positions", lambda: {})
    monkeypatch.setattr(P, "ENABLE_RECANON_ON_WRITE", True)
    monkeypatch.setattr(P, "ENABLE_PLAN_CANON_ORDER_INVARIANTS", True)
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "ENABLE_GPS_FREE_ANCHOR", True)
    monkeypatch.setattr(O, "OVERRIDES_PATH", str(tmp_path / "operator_route_overrides.json"))
    monkeypatch.setattr(O, "EVENTS_PATH", str(tmp_path / "operator_route_override_events.jsonl"))
    O._EMITTED.clear()
    return tmp_path


def _write_override(tmp_path, order_ids, set_at=None, ttl_min=120, cid=CID):
    doc = {"courier_overrides": {cid: {
        "order_ids": order_ids, "set_by": "koordynator@nadajesz.pl",
        "set_at": (set_at or NOW.isoformat()), "ttl_min": ttl_min}}}
    (tmp_path / "operator_route_overrides.json").write_text(
        json.dumps(doc, ensure_ascii=False))


def _flag_on(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE", True, raising=False)


def _save_base():
    PM.save_plan(CID, {"start_pos": {"lat": 53.128, "lng": 23.130, "source": "x"},
                       "start_ts": NOW.isoformat(),
                       "stops": [dict(s) for s in BASE_STOPS],
                       "optimization_method": "incremental",
                       "bag_signature": "A:0|B:0"})
    # sekwencja wyjściowa = kanoniczna (committed asc), retime jej nie permutuje


def _order():
    return [(s["type"], s["order_id"]) for s in PM.load_plan(CID)["stops"]]


def _events(tmp_path):
    p = tmp_path / "operator_route_override_events.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


# ── pin kolejności: kanon = sekwencja operatora ──────────────────────────────

def test_pin_applies_operator_sequence(env, monkeypatch):
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"])
    assert P.recanon_courier(CID, now=NOW, reason="assign") is True
    assert _order() == [("pickup", "B"), ("dropoff", "B"),
                        ("pickup", "A"), ("dropoff", "A")]
    evs = _events(env)
    applied = [e for e in evs if e["event"] == "operator_route_override_applied"]
    assert applied and applied[-1]["cid"] == CID and applied[-1]["changed"] is True
    assert applied[-1]["stops"] == 4


def test_pin_transparent_for_surfaces_via_route_order(env, monkeypatch):
    """E2E przez warstwy: zapis kanonu z pinem → projekcja podjazdów
    (route_order.order_podjazdy trust_canon — TO konsumują konsola i apka)
    renderuje stopy w kolejności operatora."""
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"])
    assert P.recanon_courier(CID, now=NOW) is True
    plan_doc = PM.load_plan(CID)
    bag = [{"order_id": oid, "status": rec["status"], "restaurant": rec["restaurant"],
            "czas_kuriera_warsaw": rec["czas_kuriera_warsaw"]}
           for oid, rec in ORDERS.items()]
    proj = route_order.order_podjazdy(bag, plan_doc, trust_canon=True)
    assert proj == [("pickup", ["B"]), ("dropoff", ["B"]),
                    ("pickup", ["A"]), ("dropoff", ["A"])]


def test_pin_on_vs_off_differs(env, monkeypatch):
    """ON≠OFF na tym samym wejściu: OFF zostawia kolejność kanoniczną."""
    _save_base()
    _write_override(env, ["B", "A"])
    # flaga OFF (default stałej common = False)
    assert P.recanon_courier(CID, now=NOW) is True
    off_order = _order()
    assert off_order == [("pickup", "A"), ("dropoff", "A"),
                         ("pickup", "B"), ("dropoff", "B")]
    evs = _events(env)
    rej = [e for e in evs if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "flag_off" and rej[-1]["would_apply"] is True
    # ta sama sytuacja z flagą ON → sekwencja operatora
    _flag_on(monkeypatch)
    O._EMITTED.clear()
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order() != off_order
    assert _order()[0] == ("pickup", "B")


def test_pin_idempotent(env, monkeypatch):
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"])
    P.recanon_courier(CID, now=NOW)
    first = _order()
    P.recanon_courier(CID, now=NOW)
    assert _order() == first  # brak oscylacji F6↔pin


def test_same_restaurant_adjacent_grouped_one_podjazd(env, monkeypatch):
    """Kolejne zlecenia tej samej restauracji w sekwencji operatora = jeden
    podjazd (odbiory grupą, potem dostawy) — odbicie projekcji podjazdów."""
    orders = {
        "A": dict(ORDERS["A"]),
        "B": {**ORDERS["B"], "restaurant": "Alfa",
              "pickup_coords": [53.130, 23.100]},
        "Z": {"courier_id": CID, "status": "assigned",
              "czas_kuriera_warsaw": "2026-07-19T14:20:00+02:00",
              "pickup_coords": [53.150, 23.140], "delivery_coords": [53.155, 23.150],
              "restaurant": "Zeta"},
    }
    (env / "orders_state.json").write_text(json.dumps(orders))
    stops = [dict(s) for s in BASE_STOPS] + [
        {"order_id": "Z", "type": "pickup", "coords": {"lat": 53.150, "lng": 23.140},
         "predicted_at": "2026-07-19T12:40:00+00:00", "dwell_min": 1.0,
         "status_at_plan_time": "assigned"},
        {"order_id": "Z", "type": "dropoff", "coords": {"lat": 53.155, "lng": 23.150},
         "predicted_at": "2026-07-19T12:50:00+00:00", "dwell_min": 3.5,
         "status_at_plan_time": "assigned"}]
    PM.save_plan(CID, {"start_pos": {"lat": 53.128, "lng": 23.130, "source": "x"},
                       "start_ts": NOW.isoformat(), "stops": stops,
                       "optimization_method": "incremental",
                       "bag_signature": "A:0|B:0|Z:0"})
    _flag_on(monkeypatch)
    _write_override(env, ["Z", "A", "B"])
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order() == [("pickup", "Z"), ("dropoff", "Z"),
                        ("pickup", "A"), ("pickup", "B"),
                        ("dropoff", "A"), ("dropoff", "B")]


def test_carried_position_honored(env, monkeypatch):
    """Niesione (picked_up, bez węzła odbioru) idzie na pozycji operatora —
    override jest nadrzędny wobec carried-first (świadoma decyzja koordynatora)."""
    orders = {
        "A": dict(ORDERS["A"]),
        "K": {"courier_id": CID, "status": "picked_up",
              "picked_up_at": "2026-07-19 13:40:00",
              "czas_kuriera_warsaw": "2026-07-19T13:35:00+02:00",
              "pickup_coords": [53.125, 23.120], "delivery_coords": [53.126, 23.125],
              "restaurant": "Karma"},
    }
    (env / "orders_state.json").write_text(json.dumps(orders))
    stops = [
        {"order_id": "K", "type": "dropoff", "coords": {"lat": 53.126, "lng": 23.125},
         "predicted_at": "2026-07-19T12:05:00+00:00", "dwell_min": 3.5,
         "status_at_plan_time": "picked_up"},
        {"order_id": "A", "type": "pickup", "coords": {"lat": 53.130, "lng": 23.100},
         "predicted_at": "2026-07-19T12:15:00+00:00", "dwell_min": 1.0,
         "status_at_plan_time": "assigned"},
        {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.140, "lng": 23.110},
         "predicted_at": "2026-07-19T12:25:00+00:00", "dwell_min": 3.5,
         "status_at_plan_time": "assigned"}]
    PM.save_plan(CID, {"start_pos": {"lat": 53.128, "lng": 23.130, "source": "x"},
                       "start_ts": NOW.isoformat(), "stops": stops,
                       "optimization_method": "incremental",
                       "bag_signature": "A:0|K:1"})
    _flag_on(monkeypatch)
    _write_override(env, ["A", "K"])  # odbierz A PO DRODZE, potem dowieź niesione
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order() == [("pickup", "A"), ("dropoff", "A"), ("dropoff", "K")]


# ── walidacja / TTL / fail-open ──────────────────────────────────────────────

def test_set_mismatch_ignored(env, monkeypatch):
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B"])  # brakuje A → zbiory różne
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "A")  # kolejność kanoniczna, pin zignorowany
    evs = _events(env)
    rej = [e for e in evs if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "set_mismatch"
    assert rej[-1]["active_ids"] == ["A", "B"]


def test_duplicate_ids_ignored(env, monkeypatch):
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "B"])
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "A")
    assert any(e["reason"] == "duplicate_ids" for e in _events(env)
               if e["event"] == "operator_route_override_rejected")


def test_ttl_expired(env, monkeypatch):
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"],
                    set_at="2026-07-19T08:00:00+00:00", ttl_min=120)  # 4h temu
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "A")  # wygasły → zachowanie dotychczasowe
    evs = _events(env)
    exp = [e for e in evs if e["event"] == "operator_route_override_expired"]
    assert exp and exp[-1]["cid"] == CID and exp[-1]["age_min"] > 120


def test_corrupt_file_fail_open(env, monkeypatch):
    _flag_on(monkeypatch)
    _save_base()
    (env / "operator_route_overrides.json").write_text("{nie-json![")
    assert P.recanon_courier(CID, now=NOW) is True  # bez wyjątku, plan retimed
    assert _order()[0] == ("pickup", "A")
    assert any(e["reason"] == "file_corrupt" for e in _events(env)
               if e["event"] == "operator_route_override_rejected")


def test_missing_file_zero_events(env, monkeypatch):
    _flag_on(monkeypatch)
    _save_base()
    assert P.recanon_courier(CID, now=NOW) is True
    assert _events(env) == []  # brak pliku = zero szumu


# ── czas_kuriera NIETYKALNY (R27) + naruszenie logowane ─────────────────────

def test_czas_kuriera_untouched_and_breach_logged(env, monkeypatch):
    """Sekwencja operatora opóźnia odbiór A poza tolerancję R27: zobowiązanie
    (czas_kuriera_warsaw) NIE jest ruszane nigdzie, clamp trzyma odbiór ≥
    committed, a spóźnienie ląduje w committed_breaches zdarzenia applied."""
    _flag_on(monkeypatch)
    _save_base()
    before = json.loads((env / "orders_state.json").read_text())
    _write_override(env, ["B", "A"])
    assert P.recanon_courier(CID, now=NOW) is True
    after = json.loads((env / "orders_state.json").read_text())
    assert {k: v["czas_kuriera_warsaw"] for k, v in after.items()} == \
           {k: v["czas_kuriera_warsaw"] for k, v in before.items()}
    stops = PM.load_plan(CID)["stops"]
    for s in stops:
        ck = O._parse_iso((after.get(s["order_id"]) or {}).get("czas_kuriera_warsaw"))
        if s["type"] == "pickup" and ck is not None:
            assert O._parse_iso(s["predicted_at"]) >= ck  # clamp: nie wcześniej
    applied = [e for e in _events(env)
               if e["event"] == "operator_route_override_applied"]
    assert applied
    breaches = applied[-1]["committed_breaches"]
    assert any(b["oid"] == "A" and b["late_min"] > 5 for b in breaches)


# ── drugi writer: pełna decyzja `_gen_one_bag_plan` (redecide) ──────────────

def test_gen_path_pins_sequence(env, monkeypatch):
    """Świeża decyzja sekwencji (redecide → _gen_one_bag_plan: TSP + F6 + pin
    + retime) też honoruje sekwencję operatora — bliźniak retime pokryty RAZEM."""
    _flag_on(monkeypatch)
    monkeypatch.setattr(P, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    _write_override(env, ["B", "A"])
    assert P.redecide_courier(CID, now=NOW) is True  # brak planu → pełny _gen
    assert _order() == [("pickup", "B"), ("dropoff", "B"),
                        ("pickup", "A"), ("dropoff", "A")]
    applied = [e for e in _events(env)
               if e["event"] == "operator_route_override_applied"]
    assert applied and applied[-1]["stops"] == 4


def test_gen_path_flag_off_unpinned(env, monkeypatch):
    monkeypatch.setattr(P, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    _write_override(env, ["B", "A"])
    assert P.redecide_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "A")  # kanon bez pinu (committed asc)
