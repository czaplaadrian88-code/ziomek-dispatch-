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
import os
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
    monkeypatch.setattr(O, "_DOC_CACHE", {"path": None, "sig": None, "doc": None,
                                          "err": None, "parses": 0})
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


def test_carried_position_honored_with_hard_breach_logged(env, monkeypatch):
    """Niesione (picked_up, bez węzła odbioru) idzie na pozycji operatora —
    override jest nadrzędny wobec carried-first (legalny relax, świadoma decyzja
    koordynatora) — ALE gdy odsunięcie łamie R6 (carried-age > 35/40), naruszenie
    jest GŁOŚNO zalogowane w hard_breaches zdarzenia applied (polityka v2:
    wykonujemy + raportujemy, bez veta)."""
    orders = {
        "A": dict(ORDERS["A"]),
        "K": {"courier_id": CID, "status": "picked_up",
              "picked_up_at": "2026-07-19 13:05:00",  # naive=WARSAW → 11:05Z, 55 min przed NOW
              "czas_kuriera_warsaw": "2026-07-19T13:00:00+02:00",
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
    applied = [e for e in _events(env)
               if e["event"] == "operator_route_override_applied"]
    assert applied
    hb = applied[-1]["hard_breaches"]
    r6_k = [b for b in hb if b["type"] == "r6" and b["order_id"] == "K"]
    assert r6_k and r6_k[0]["value"] > 35
    assert r6_k[0]["alarm40"] is True  # >40 = poziom Alarmu OD-07, zalogowany


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
    a_breach = [b for b in breaches if b["oid"] == "A" and b["late_min"] > 5]
    assert a_breach
    # v2: spóźnienie > progu BUG C (10 min) = ranga ALERT (event + WARNING),
    # nadal bez zmiany zobowiązania i bez veta
    assert a_breach[0]["alert"] is True
    assert applied[-1]["r27_alert"] is True


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


# ── v2 (NO-GO Sola 19.07): veto techniczne retime, HARD-raport, walidacje ────

def test_gen_retime_fail_aborts_keeps_plan(env, monkeypatch):
    """Pin przestawił stopy, czasów nie umiemy policzyć → _gen NIE zapisuje
    przestawionej sekwencji ze starymi czasami (poprzedni stan planów nietknięty)
    + zdarzenie rejected/retime_failed (veto techniczne)."""
    _flag_on(monkeypatch)
    monkeypatch.setattr(P, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    monkeypatch.setattr(P, "_apply_canon_order_invariants",
                        lambda s, o, p=None, n=None: s)
    monkeypatch.setattr(P, "_retime_stops", lambda *a, **k: None)
    _write_override(env, ["B", "A"])
    assert P.redecide_courier(CID, now=NOW) is False
    assert PM.load_plan(CID) is None  # nic nie zapisano — plan nietknięty
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "retime_failed"


def test_retime_path_retime_fail_event_plan_untouched(env, monkeypatch):
    """Ścieżka recanon/retime: OSRM pada po pinie → zapisu nie ma (plan i
    plan_version nietknięte) + rejected/retime_failed w cieniu."""
    _flag_on(monkeypatch)
    _save_base()
    v0 = PM.load_plan(CID)["plan_version"]
    _write_override(env, ["B", "A"])
    monkeypatch.setattr(osrm_client, "table", lambda a, b: None)
    assert P.recanon_courier(CID, now=NOW) is False
    assert PM.load_plan(CID)["plan_version"] == v0
    assert _order()[0] == ("pickup", "A")  # stara sekwencja bez zmian
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "retime_failed"


def test_no_return_breach_logged_in_applied(env, monkeypatch):
    """Operator rozdziela zlecenia tej samej restauracji (A..C z Alfa, B z Beta
    pomiędzy) → wykonujemy, ale powrót jest w hard_breaches (no_return)."""
    orders = {
        "A": dict(ORDERS["A"]),
        "B": dict(ORDERS["B"]),
        "C": {"courier_id": CID, "status": "assigned",
              "czas_kuriera_warsaw": "2026-07-19T14:15:00+02:00",
              "pickup_coords": [53.130, 23.100], "delivery_coords": [53.145, 23.115],
              "restaurant": "Alfa"},
    }
    (env / "orders_state.json").write_text(json.dumps(orders))
    stops = [dict(s) for s in BASE_STOPS] + [
        {"order_id": "C", "type": "pickup", "coords": {"lat": 53.130, "lng": 23.100},
         "predicted_at": "2026-07-19T12:40:00+00:00", "dwell_min": 1.0,
         "status_at_plan_time": "assigned"},
        {"order_id": "C", "type": "dropoff", "coords": {"lat": 53.145, "lng": 23.115},
         "predicted_at": "2026-07-19T12:50:00+00:00", "dwell_min": 3.5,
         "status_at_plan_time": "assigned"}]
    PM.save_plan(CID, {"start_pos": {"lat": 53.128, "lng": 23.130, "source": "x"},
                       "start_ts": NOW.isoformat(), "stops": stops,
                       "optimization_method": "incremental",
                       "bag_signature": "A:0|B:0|C:0"})
    _flag_on(monkeypatch)
    _write_override(env, ["A", "B", "C"])
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order() == [("pickup", "A"), ("dropoff", "A"),
                        ("pickup", "B"), ("dropoff", "B"),
                        ("pickup", "C"), ("dropoff", "C")]
    applied = [e for e in _events(env)
               if e["event"] == "operator_route_override_applied"]
    assert applied
    hb = applied[-1]["hard_breaches"]
    assert any(b["type"] == "no_return" and b["order_id"] == "C" for b in hb)


def test_ttl_zero_defaults_to_120(env, monkeypatch):
    """ttl_min<=0 = śmieć konfiguracyjny → default 120 (świeży wpis DZIAŁA,
    nie wygasa natychmiast)."""
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"], ttl_min=0)
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "B")
    assert not [e for e in _events(env)
                if e["event"] == "operator_route_override_expired"]


def test_future_set_at_rejected(env, monkeypatch):
    """set_at z przyszłości (> now + 2 min skew) = wpis niewiarygodny → odrzut."""
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"], set_at="2026-07-19T12:10:00+00:00")  # NOW+10'
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "A")
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "invalid_set_at"


def test_doc_cache_parses_once_per_mtime(env, monkeypatch):
    """Koszt: parse pliku TYLKO przy zmianie (mtime_ns, size); kolejne odczyty
    z cache. Zmiana pliku (wymuszony bump mtime) → jeden nowy parse."""
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"])
    p0 = O._DOC_CACHE["parses"]
    assert P.recanon_courier(CID, now=NOW) is True
    assert P.recanon_courier(CID, now=NOW) is True
    assert O._DOC_CACHE["parses"] == p0 + 1  # drugi bieg bez parse
    _write_override(env, ["A", "B"])
    f = env / "operator_route_overrides.json"
    st = os.stat(f)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert P.recanon_courier(CID, now=NOW) is True
    assert O._DOC_CACHE["parses"] == p0 + 2  # nowa zawartość = jeden parse


def test_recanon_after_raw_save_reapplies_pin(env, monkeypatch):
    """Okno surowego zapisu (panel_watcher._save_plan_on_assign pisze sekwencję
    solvera bez pinu) samo się goi: recanon w tym samym handlerze re-nakłada
    pin przez chokepoint _retime_one_bag_plan."""
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"])
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "B")
    _save_base()  # surowy zapis nadpisuje kanon sekwencją bez pinu
    assert _order()[0] == ("pickup", "A")
    assert P.recanon_courier(CID, now=NOW, reason="assign") is True
    assert _order()[0] == ("pickup", "B")  # chokepoint re-nakłada pin


# ── v3 (re-review Sola 20.07): L3-override, stale-ETA, grafik, walidacje ─────

def test_l3_reject_overridden_by_pin(env, monkeypatch):
    """L3 compare-and-keep (LIVE) daje REJECT dla spiętej sekwencji — przy
    AKTYWNYM pinie zapis NASTĘPUJE (pin wygrywa z bramką biznesową), a werdykt
    idzie do eventu applied jako l3_would_reject + detal."""
    from dispatch_v2 import route_simulator_v2 as R
    orders = {
        "A": dict(ORDERS["A"]),
        "K": {"courier_id": CID, "status": "picked_up",
              "picked_up_at": "2026-07-19 13:40:00",  # naive=WARSAW → 11:40Z, 20 min przed NOW
              "czas_kuriera_warsaw": "2026-07-19T13:30:00+02:00",
              "pickup_coords": [53.125, 23.120], "delivery_coords": [53.126, 23.125],
              "restaurant": "Karma"},
    }
    (env / "orders_state.json").write_text(json.dumps(orders))
    # Istniejący plan: K dowożone OD RAZU (age ~22 min < 35 — L3 baseline czysty)
    PM.save_plan(CID, {"start_pos": {"lat": 53.128, "lng": 23.130, "source": "x"},
                       "start_ts": NOW.isoformat(),
                       "stops": [
                           {"order_id": "K", "type": "dropoff",
                            "coords": {"lat": 53.126, "lng": 23.125},
                            "predicted_at": "2026-07-19T12:02:00+00:00",
                            "dwell_min": 3.5, "status_at_plan_time": "picked_up"},
                           {"order_id": "A", "type": "pickup",
                            "coords": {"lat": 53.130, "lng": 23.100},
                            "predicted_at": "2026-07-19T12:08:00+00:00",
                            "dwell_min": 1.0, "status_at_plan_time": "assigned"},
                           {"order_id": "A", "type": "dropoff",
                            "coords": {"lat": 53.140, "lng": 23.110},
                            "predicted_at": "2026-07-19T12:15:00+00:00",
                            "dwell_min": 3.5, "status_at_plan_time": "assigned"}],
                       "optimization_method": "incremental",
                       "bag_signature": "A:0|K:1"})
    v0 = PM.load_plan(CID)["plan_version"]
    _flag_on(monkeypatch)
    monkeypatch.setattr(C, "ENABLE_PLAN_RECHECK_GATES", True, raising=False)
    _write_override(env, ["A", "K"])  # K odsunięte za A → fresh łamie R6, existing NIE
    ok = P._gen_one_bag_plan(CID, ["A", "K"], orders, {}, NOW, R,
                             expected_version=v0)
    assert ok is True  # zapis NASTĄPIŁ mimo L3 REJECT
    assert _order() == [("pickup", "A"), ("dropoff", "A"), ("dropoff", "K")]
    applied = [e for e in _events(env)
               if e["event"] == "operator_route_override_applied"]
    assert applied and applied[-1]["l3_would_reject"] is True
    assert applied[-1]["l3_detail"]["fresh_r6"] > 35


def test_gen_f6_stale_with_pin_unchanged_aborts(env, monkeypatch):
    """Ścieżka Sola: F6 przestawił stopy, retime F6 padł (stale czasy), pin
    zgodny z tą kolejnością (changed=False) → v3 wymusza retime finalnej
    sekwencji; fail ⇒ veto techniczne, plan nietknięty."""
    _flag_on(monkeypatch)
    monkeypatch.setattr(P, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)

    def _f6_reorder(s, o, p=None, n=None):
        # deterministycznie ustaw kolejność = przyszły pin (B przed A)
        key = {"B": 0, "A": 1}
        return sorted(s, key=lambda x: (key[str(x["order_id"])],
                                        0 if x["type"] == "pickup" else 1))

    monkeypatch.setattr(P, "_apply_canon_order_invariants", _f6_reorder)
    monkeypatch.setattr(P, "_retime_stops", lambda *a, **k: None)
    _write_override(env, ["B", "A"])
    assert P.redecide_courier(CID, now=NOW) is False
    assert PM.load_plan(CID) is None  # żadnego zapisu ze stalymi czasami
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "retime_failed"


def test_missing_osrm_cell_vetoes_pin(env, monkeypatch):
    """Brakująca komórka macierzy OSRM przy aktywnym pinie = veto techniczne
    (żadnego cichego lega 0 min), plan nietknięty."""
    _flag_on(monkeypatch)
    _save_base()
    v0 = PM.load_plan(CID)["plan_version"]
    _write_override(env, ["B", "A"])
    monkeypatch.setattr(osrm_client, "table",
                        lambda a, b: [[{"duration_s": None} for _ in b] for _ in a])
    assert P.recanon_courier(CID, now=NOW) is False
    assert PM.load_plan(CID)["plan_version"] == v0
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "retime_failed"


def test_grafik_breach_logged_in_applied(env, monkeypatch):
    """Pin wypycha stopy za EFEKTYWNY koniec zmiany kuriera → breach `grafik`
    w hard_breaches (okno 1:1 z feasibility — delegacja do
    courier_resolver.resolve_effective_shift_end_by_cid), bez veta."""
    from dispatch_v2 import courier_resolver as CR
    _flag_on(monkeypatch)
    monkeypatch.setattr(C, "ENABLE_V324A_SCHEDULE_INTEGRATION", True, raising=False)
    _save_base()
    _write_override(env, ["B", "A"])
    monkeypatch.setattr(CR, "resolve_effective_shift_end_by_cid",
                        lambda cid, **k: NOW)  # zmiana kończy się „teraz"
    assert P.recanon_courier(CID, now=NOW) is True
    applied = [e for e in _events(env)
               if e["event"] == "operator_route_override_applied"]
    assert applied
    hb = applied[-1]["hard_breaches"]
    grafik = [b for b in hb if b["type"] == "grafik"]
    assert grafik and any(b["order_id"] == "A" and b["stop_type"] == "dropoff"
                          and b["value"] > 5 for b in grafik)


def test_grafik_pickup_no_tolerance(env, monkeypatch):
    """Parytet V3.25: PICKUP po shift_end = breach BEZ 5-min tolerancji
    (v3 by go przemilczał — excess < 5)."""
    from dispatch_v2 import courier_resolver as CR
    from datetime import timedelta
    _flag_on(monkeypatch)
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", True, raising=False)
    monkeypatch.setattr(C, "ENABLE_V324A_SCHEDULE_INTEGRATION", False, raising=False)
    _save_base()
    _write_override(env, ["B", "A"])
    # odbiór B po pinie ~12:13Z; koniec zmiany 12:09:30Z ⇒ excess B ~3.8 min —
    # strefa, którą 5-min tolerancja dropoffów by przemilczała
    monkeypatch.setattr(CR, "resolve_effective_shift_end_by_cid",
                        lambda cid, **k: NOW + timedelta(minutes=9.5))
    assert P.recanon_courier(CID, now=NOW) is True
    hb = [e for e in _events(env)
          if e["event"] == "operator_route_override_applied"][-1]["hard_breaches"]
    pu = [b for b in hb if b["type"] == "grafik" and b["stop_type"] == "pickup"]
    assert pu and any(0 < b["value"] <= 5 for b in pu)  # breach mimo excess<5


def test_grafik_salvage_suppresses_dropoff_breach(env, monkeypatch):
    """Parytet EOD-salvage: aktywny salvage (predykat feasibility) ⇒ dropoff po
    końcu zmiany NIE jest raportowany jako grafik-breach (zero false-positive)."""
    from dispatch_v2 import courier_resolver as CR
    from dispatch_v2 import feasibility_v2 as F
    from datetime import timedelta
    _flag_on(monkeypatch)
    monkeypatch.setattr(C, "ENABLE_V324A_SCHEDULE_INTEGRATION", True, raising=False)
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", False, raising=False)
    _save_base()
    _write_override(env, ["B", "A"])
    monkeypatch.setattr(CR, "resolve_effective_shift_end_by_cid",
                        lambda cid, **k: NOW - timedelta(minutes=60))
    monkeypatch.setattr(F, "_end_of_day_salvage", lambda now: (True, None))
    assert P.recanon_courier(CID, now=NOW) is True
    hb = [e for e in _events(env)
          if e["event"] == "operator_route_override_applied"][-1]["hard_breaches"]
    assert not [b for b in hb if b["type"] == "grafik"]


def test_name_chain_parity_sol_counterexample(monkeypatch):
    """Kontrprzykład Sola r4: grafik 14:00, courier_tiers ma STALE alias,
    working-override 23:00. Resolver MUSI rozwiązywać nazwę TYM SAMYM łańcuchem
    co cs.name floty (_load_courier_names) ⇒ grafik zmatchowany ⇒ GRAFIK-CAP
    przycina wo do 14:00 (nie 23:00/None)."""
    import schedule_utils
    from dispatch_v2 import courier_resolver as CR
    from dispatch_v2 import manual_overrides as MO
    schedule = {"Jan Kowalski": {"start": "10:00", "end": "14:00"}}
    monkeypatch.setattr(CR, "_load_courier_tiers",
                        lambda: {"7777": {"name": "ZLY-STALE-ALIAS"}})
    monkeypatch.setattr(CR, "_load_courier_names",
                        lambda: {"7777": "Jan Kowalski"})
    monkeypatch.setattr(schedule_utils, "load_schedule", lambda: schedule)
    monkeypatch.setattr(schedule_utils, "match_courier",
                        lambda name, sched: name if name in sched else None)
    monkeypatch.setattr(schedule_utils, "is_on_shift",
                        lambda name, sched: (False, "po zmianie"))
    monkeypatch.setattr(MO, "get_working",
                        lambda: {"7777": {"end": "23:00",
                                          "added_at": "2026-07-19T09:00:00+00:00"}})
    monkeypatch.setattr(C, "ENABLE_WORKING_OVERRIDE", True, raising=False)
    monkeypatch.setattr(C, "ENABLE_WORKING_OVERRIDE_GRAFIK_CAP", True, raising=False)
    end = CR.resolve_effective_shift_end_by_cid("7777")
    assert end is not None and end.hour == 14  # cap do grafiku, nie 23:00


def test_grafik_pickup_salvage_suppressed(env, monkeypatch):
    """v5 parytet feasibility:743 — pickup po shift_end w oknie EOD-salvage
    jest legalny ⇒ zero breachu grafik (ten sam predykat co dropoff)."""
    from dispatch_v2 import courier_resolver as CR
    from dispatch_v2 import feasibility_v2 as F
    from datetime import timedelta
    _flag_on(monkeypatch)
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", True, raising=False)
    monkeypatch.setattr(C, "ENABLE_V324A_SCHEDULE_INTEGRATION", False, raising=False)
    _save_base()
    _write_override(env, ["B", "A"])
    monkeypatch.setattr(CR, "resolve_effective_shift_end_by_cid",
                        lambda cid, **k: NOW + timedelta(minutes=4))
    monkeypatch.setattr(F, "_end_of_day_salvage", lambda now: (True, None))
    assert P.recanon_courier(CID, now=NOW) is True
    hb = [e for e in _events(env)
          if e["event"] == "operator_route_override_applied"][-1]["hard_breaches"]
    assert not [b for b in hb if b["type"] == "grafik"]


def test_veto_scope_status_writes_persist(env, monkeypatch):
    """Zakres veta technicznego (doprecyzowanie v5): „plan nietknięty" =
    PIN NIE ZOSTAŁ ZASTOSOWANY (kolejność sprzed pinu), ale legalne zapisy
    statusowe (mark_picked_up sprzed recanonu — pre-existing) ZOSTAJĄ."""
    _flag_on(monkeypatch)
    _save_base()
    # legalny zapis statusowy jak w handlerze pickup: prune węzła odbioru A
    PM.mark_picked_up(CID, "A", picked_up_at="2026-07-19 13:55:00")
    orders = json.loads((env / "orders_state.json").read_text())
    orders["A"]["status"] = "picked_up"
    orders["A"]["picked_up_at"] = "2026-07-19 13:55:00"
    (env / "orders_state.json").write_text(json.dumps(orders))
    v_after_status = PM.load_plan(CID)["plan_version"]
    order_after_status = _order()
    assert ("pickup", "A") not in order_after_status  # prune wszedł
    _write_override(env, ["B", "A"])
    monkeypatch.setattr(osrm_client, "table", lambda a, b: None)  # strict fail
    assert P.recanon_courier(CID, now=NOW) is False
    assert PM.load_plan(CID)["plan_version"] == v_after_status  # zero zapisu pinu
    assert _order() == order_after_status  # kolejność sprzed pinu zachowana
    st = [s for s in PM.load_plan(CID)["stops"] if s["order_id"] == "A"]
    assert st and all(s.get("status_at_plan_time") == "picked_up" for s in st)
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "retime_failed"


def test_effective_shift_end_working_override_extends(monkeypatch):
    """Jedno źródło okna: working-override 'pracuje' (kurier NIE na realnej
    zmianie) wydłuża efektywny koniec ponad grafik; na realnej zmianie wygrywa
    realny grafik — dokładnie jak cs.shift_end w dispatchable_fleet."""
    from dispatch_v2 import courier_resolver as CR
    wo = {"end": "23:00"}
    grafik = {"end": "14:00"}
    ext = CR.effective_shift_end(wo, grafik, False, False)
    assert ext is not None and ext.hour == 23  # override wydłuża (FALLBACK)
    real = CR.effective_shift_end(wo, grafik, True, False)
    assert real is not None and real.hour == 14  # realna zmiana wygrywa
    assert CR.effective_shift_end(None, grafik, False, True).hour == 14


def test_ttl_bool_and_garbage_default_120(env, monkeypatch):
    """bool jako ttl (json true) NIE jest liczbą → default 120: wpis sprzed
    60 min DZIAŁA (float(True)=1.0 by go wygasił). Wartości NaN/Inf/ułamek/
    poza zakresem → też 120 (asercje jednostkowe na _ttl_min)."""
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"],
                    set_at="2026-07-19T11:00:00+00:00", ttl_min=True)  # 60 min temu
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "B")  # zadziałał (120), nie wygasł (1.0)
    assert not [e for e in _events(env)
                if e["event"] == "operator_route_override_expired"]
    assert O._ttl_min({"ttl_min": float("inf")}) == 120.0
    assert O._ttl_min({"ttl_min": float("nan")}) == 120.0
    assert O._ttl_min({"ttl_min": 120.5}) == 120.0
    assert O._ttl_min({"ttl_min": 99999}) == 120.0
    assert O._ttl_min({"ttl_min": 60}) == 60.0


def test_ttl_infinity_in_file_expires_old_entry(env, monkeypatch):
    """Infinity w pliku (json.load je akceptuje) nie może unieśmiertelnić
    wpisu: default 120 ⇒ wpis sprzed 200 min WYGASA."""
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"],
                    set_at="2026-07-19T08:40:00+00:00",  # 200 min temu
                    ttl_min=float("inf"))
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "A")  # nie spinowany
    exp = [e for e in _events(env)
           if e["event"] == "operator_route_override_expired"]
    assert exp and exp[-1]["ttl_min"] == 120


def test_set_at_without_offset_rejected(env, monkeypatch):
    """Kontrakt: set_at = ISO z jawnym offsetem; naiwny czas → invalid_set_at
    (nie zgadujemy strefy)."""
    _flag_on(monkeypatch)
    _save_base()
    _write_override(env, ["B", "A"], set_at="2026-07-19T11:50:00")  # bez offsetu
    assert P.recanon_courier(CID, now=NOW) is True
    assert _order()[0] == ("pickup", "A")
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "invalid_set_at"


def test_f6_poisoned_times_pin_unchanged_strict_veto(env, monkeypatch):
    """Kombinacja Sola r3: F6 reorder + legacy retime „udany" z zatrutymi
    0-min legami (None-cell) + pin changed=False. v4: strict retime FINALNEJ
    sekwencji biegnie ZAWSZE ⇒ None-cell ⇒ veto techniczne, plan nietknięty."""
    _flag_on(monkeypatch)
    monkeypatch.setattr(P, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)

    def _f6_reorder(s, o, p=None, n=None):
        key = {"B": 0, "A": 1}
        return sorted(s, key=lambda x: (key[str(x["order_id"])],
                                        0 if x["type"] == "pickup" else 1))

    calls = []

    def _spy_retime(stops, pos, anchor, ostate, now, strict_cells=False):
        calls.append(strict_cells)
        if not strict_cells:
            # legacy (F6): None-cell ⇒ cichy leg 0 min — lista WRACA (zatruta)
            out = [dict(s) for s in stops]
            for s in out:
                s["predicted_at"] = NOW.isoformat()
            return out
        return None  # strict: None-cell ⇒ veto

    monkeypatch.setattr(P, "_apply_canon_order_invariants", _f6_reorder)
    monkeypatch.setattr(P, "_retime_stops", _spy_retime)
    _write_override(env, ["B", "A"])
    assert P.redecide_courier(CID, now=NOW) is False
    assert PM.load_plan(CID) is None  # zatrute czasy NIE weszły do kanonu
    assert True in calls  # strict retime pobiegł mimo changed=False
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "retime_failed"


def test_retime_exception_in_recanon_emits_rejected(env, monkeypatch):
    """Wyjątek w strict-retime (ścieżka recanon) = ten sam los co None:
    rejected/retime_failed + plan nietknięty (deklaracja = kod)."""
    _flag_on(monkeypatch)
    _save_base()
    v0 = PM.load_plan(CID)["plan_version"]
    _write_override(env, ["B", "A"])

    def _boom(*a, **k):
        raise RuntimeError("osrm boom")

    monkeypatch.setattr(P, "_retime_stops", _boom)
    assert P.recanon_courier(CID, now=NOW) is False
    assert PM.load_plan(CID)["plan_version"] == v0
    rej = [e for e in _events(env)
           if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "retime_failed"


def test_ttl_strict_int_only(env):
    """Kontrakt v4: ttl_min WYŁĄCZNIE int 1..1440 — string \"60\" i float 60.0
    odrzucone do default 120."""
    assert O._ttl_min({"ttl_min": "60"}) == 120.0
    assert O._ttl_min({"ttl_min": 60.0}) == 120.0
    assert O._ttl_min({"ttl_min": 60}) == 60.0
    assert O._ttl_min({"ttl_min": 1441}) == 120.0
    assert O._ttl_min({"ttl_min": 1}) == 1.0
    assert O._ttl_min({}) == 120.0


def test_flag_off_structure_fail_not_would_apply(env, monkeypatch):
    """Cień would_apply dopiero PO udanym dry-run konstrukcji: strukturalnie
    niemożliwy override (duplikat węzła) przy OFF = structure_fail, nigdy
    fałszywe would_apply."""
    stops = [dict(s) for s in BASE_STOPS] + [
        {"order_id": "A", "type": "pickup",  # DUPLIKAT węzła odbioru A
         "coords": {"lat": 53.130, "lng": 23.100},
         "predicted_at": "2026-07-19T12:06:00+00:00", "dwell_min": 1.0,
         "status_at_plan_time": "assigned"}]
    PM.save_plan(CID, {"start_pos": {"lat": 53.128, "lng": 23.130, "source": "x"},
                       "start_ts": NOW.isoformat(), "stops": stops,
                       "optimization_method": "incremental",
                       "bag_signature": "A:0|B:0"})
    _write_override(env, ["B", "A"])  # flaga OFF (default)
    assert P.recanon_courier(CID, now=NOW) is True
    evs = _events(env)
    rej = [e for e in evs if e["event"] == "operator_route_override_rejected"]
    assert rej and rej[-1]["reason"] == "structure_fail"
    assert not any(e.get("would_apply") for e in evs)
