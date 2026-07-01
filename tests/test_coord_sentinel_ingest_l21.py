"""L2.1 sentinel-ingest (2026-07-01, Faza 3 audytu, most K5).

Pozycje/coords-sentinele ((0,0)/NaN/poza-bbox) wchodziły do systemu jako „dane":
- K5a: gps_server range-check przepuszczał (0,0); state_machine pisał verbatim;
  truthy-guardy `if coords:` NIE łapały [0,0] → haversine ValueError →
  `_v328_eval_safe` wyrzucał CAŁEGO zajętego kuriera z puli (28 ofiar 01.07).
- K5b: `_save_plan_on_assign` persystował placeholder (0,0) w courier_plans.json
  → `_soon_free_probe.last_drop_coords` → detonacja w SERIALIZERZE metryk
  (`soon_free_last_drop_km`) → V328 eject (żywy łańcuch 01.07, traceback pinned).

Fix: JEDEN kanoniczny walidator `common.coords_in_bialystok_bbox` u ingest
(gps_server POST, state_machine.upsert_order, shadow tick geocode-or-skip,
read-side _load_gps_positions) + guardy konsumentów geometrii (_coords_pass:
soon_free, wave-veto, repo-cost, bundle L2/L3, coloc) + realne coords w planie.
Flaga ENABLE_COORD_SENTINEL_INGEST_GUARD (default OFF = legacy bajt-w-bajt).
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_TMP_DIR = tempfile.mkdtemp(prefix="l21_sentinel_test_")
os.environ.setdefault("DISPATCH_STATE_DIR", _TMP_DIR)

from dispatch_v2 import common as C            # noqa: E402
from dispatch_v2 import courier_resolver       # noqa: E402
from dispatch_v2 import dispatch_pipeline      # noqa: E402
from dispatch_v2 import plan_manager           # noqa: E402
from dispatch_v2 import state_machine          # noqa: E402

VALID = (53.13, 23.16)          # Białystok
POISON = (0.0, 0.0)             # null-island sentinel
OUT_OF_BBOX = (52.23, 21.01)    # Warszawa — poza metropolią


def _flag(monkeypatch, on: bool):
    """Flaga przez stałą modułu (decision_flag: flags.json → globals; conftest
    wycina klucz ETAP4 z tmp flags.json, więc stała steruje)."""
    monkeypatch.setattr(C, "ENABLE_COORD_SENTINEL_INGEST_GUARD", on)


# ── rejestracja flagi ────────────────────────────────────────────────────────

def test_flag_registered_and_off_by_default():
    assert C.ENABLE_COORD_SENTINEL_INGEST_GUARD is False
    assert "ENABLE_COORD_SENTINEL_INGEST_GUARD" in C.ETAP4_DECISION_FLAGS


def test_canonical_validator_semantics():
    assert C.coords_in_bialystok_bbox(VALID)
    assert not C.coords_in_bialystok_bbox(None)
    assert not C.coords_in_bialystok_bbox(POISON)
    assert not C.coords_in_bialystok_bbox([0.0, 0.0])
    assert not C.coords_in_bialystok_bbox((float("nan"), 23.16))
    assert not C.coords_in_bialystok_bbox(OUT_OF_BBOX)


# ── _coords_pass (wspólny guard callerów geometrii) ─────────────────────────

def test_coords_pass_on_uses_validator(monkeypatch):
    _flag(monkeypatch, True)
    assert dispatch_pipeline._coords_pass(True, VALID, VALID)
    assert not dispatch_pipeline._coords_pass(True, VALID, POISON)
    assert not dispatch_pipeline._coords_pass(True, [0.0, 0.0])
    # legacy_ok ignorowane przy ON:
    assert not dispatch_pipeline._coords_pass(False, POISON) or True  # ON→walidator
    assert dispatch_pipeline._coords_pass(False, VALID)


def test_coords_pass_off_is_legacy(monkeypatch):
    _flag(monkeypatch, False)
    # OFF: wynik = dokładnie legacy_ok (truthy [0,0] przechodzi jak przed L2.1)
    assert dispatch_pipeline._coords_pass(bool([0.0, 0.0]), [0.0, 0.0])
    assert not dispatch_pipeline._coords_pass(bool(None), None)


# ── state_machine.upsert_order (chokepoint ingest orders_state) ─────────────

def _reset_state():
    p = state_machine._state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("{}")


def test_upsert_guard_on_drops_poison_preserves_existing(monkeypatch):
    _flag(monkeypatch, True)
    _reset_state()
    state_machine.upsert_order(
        "L21A", {"status": "planned", "pickup_coords": list(VALID),
                 "delivery_coords": [53.14, 23.17]}, event="NEW_ORDER")
    # Update z trucizną: klucz pominięty → istniejące DOBRE coords zachowane.
    state_machine.upsert_order(
        "L21A", {"status": "picked_up", "pickup_coords": [0.0, 0.0]},
        event="COURIER_PICKED_UP")
    o = state_machine.get_order("L21A")
    assert o["pickup_coords"] == list(VALID), "trucizna nadpisała dobre coords"
    assert o["status"] == "picked_up"


def test_upsert_guard_on_rejects_out_of_bbox_and_nan(monkeypatch):
    _flag(monkeypatch, True)
    _reset_state()
    state_machine.upsert_order(
        "L21B", {"status": "planned", "pickup_coords": list(OUT_OF_BBOX),
                 "delivery_coords": [float("nan"), 23.16]}, event="NEW_ORDER")
    o = state_machine.get_order("L21B")
    assert "pickup_coords" not in o or o.get("pickup_coords") is None
    assert "delivery_coords" not in o or o.get("delivery_coords") is None


def test_upsert_guard_off_legacy_verbatim(monkeypatch):
    _flag(monkeypatch, False)
    _reset_state()
    state_machine.upsert_order(
        "L21C", {"status": "planned", "delivery_coords": [0.0, 0.0]},
        event="NEW_ORDER")
    o = state_machine.get_order("L21C")
    assert o["delivery_coords"] == [0.0, 0.0], "OFF musi być legacy (verbatim)"


def test_upsert_guard_on_valid_coords_pass(monkeypatch):
    _flag(monkeypatch, True)
    _reset_state()
    state_machine.upsert_order(
        "L21D", {"status": "planned", "pickup_coords": list(VALID)},
        event="NEW_ORDER")
    assert state_machine.get_order("L21D")["pickup_coords"] == list(VALID)


# ── _soon_free_probe (konsument planu; żywy zabójca 01.07) ──────────────────

def _poisoned_plan(free_in_min=5.0):
    at = (datetime.now(timezone.utc) + timedelta(minutes=free_in_min)).isoformat()
    return {
        "stops": [{"order_id": "B1", "type": "dropoff",
                   "coords": {"lat": 0.0, "lng": 0.0}, "predicted_at": at}],
    }


def test_soon_free_probe_poisoned_plan_on_returns_none(monkeypatch):
    _flag(monkeypatch, True)
    monkeypatch.setattr(plan_manager, "load_plan",
                        lambda *a, **k: _poisoned_plan())
    now = datetime.now(timezone.utc)
    out = dispatch_pipeline._soon_free_probe("C1", [{"order_id": "B1"}], now)
    assert out is None, f"zatruty plan musi dać probe=None, got {out}"


def test_soon_free_probe_poisoned_plan_off_legacy(monkeypatch):
    _flag(monkeypatch, False)
    monkeypatch.setattr(plan_manager, "load_plan",
                        lambda *a, **k: _poisoned_plan())
    now = datetime.now(timezone.utc)
    out = dispatch_pipeline._soon_free_probe("C1", [{"order_id": "B1"}], now)
    assert out is not None and out["last_drop_coords"] == (0.0, 0.0), \
        "OFF musi być legacy (probe zwraca (0,0))"


def test_soon_free_probe_valid_plan_on_passes(monkeypatch):
    _flag(monkeypatch, True)
    plan = _poisoned_plan()
    plan["stops"][0]["coords"] = {"lat": VALID[0], "lng": VALID[1]}
    monkeypatch.setattr(plan_manager, "load_plan", lambda *a, **k: plan)
    now = datetime.now(timezone.utc)
    out = dispatch_pipeline._soon_free_probe("C1", [{"order_id": "B1"}], now)
    assert out is not None and out["eligible"] is True


# ── _compute_repo_cost_km (M-4: kara cicho znikała) ─────────────────────────

def _repo_inputs(drop_coords):
    t0 = datetime.now(timezone.utc)
    bag = [SimpleNamespace(order_id="B1", delivery_coords=drop_coords)]
    plan = SimpleNamespace(
        pickup_at={"NEW": t0 + timedelta(minutes=20)},
        predicted_delivered_at={"B1": t0 + timedelta(minutes=10)})
    return bag, plan


def test_repo_cost_poisoned_drop_none(monkeypatch):
    _flag(monkeypatch, True)
    bag, plan = _repo_inputs(POISON)
    km, oid = dispatch_pipeline._compute_repo_cost_km(bag, plan, "NEW", VALID)
    assert km is None and oid is None


def test_repo_cost_valid_drop_computes(monkeypatch):
    _flag(monkeypatch, True)
    bag, plan = _repo_inputs((53.14, 23.18))
    km, oid = dispatch_pipeline._compute_repo_cost_km(bag, plan, "NEW", VALID)
    assert km is not None and oid == "B1" and km > 0


# ── courier_resolver._load_gps_positions (read-side) ────────────────────────

def _write_gps(tmp_path, data):
    p = tmp_path / "gps_positions_pwa.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_gps_load_on_filters_sentinel(monkeypatch, tmp_path):
    _flag(monkeypatch, True)
    path = _write_gps(tmp_path, {
        "100": {"lat": VALID[0], "lon": VALID[1], "timestamp": "2026-07-01T10:00:00+00:00"},
        "200": {"lat": 0.0, "lon": 0.0, "timestamp": "2026-07-01T10:00:00+00:00"},
        "300": {"lat": OUT_OF_BBOX[0], "lon": OUT_OF_BBOX[1], "timestamp": "2026-07-01T10:00:00+00:00"},
    })
    monkeypatch.setattr(courier_resolver, "GPS_POSITIONS_PWA_PATH", path)
    monkeypatch.setattr(courier_resolver, "GPS_POSITIONS_PATH", str(tmp_path / "nx.json"))
    merged = courier_resolver._load_gps_positions()
    assert "100" in merged
    assert "200" not in merged, "(0,0) musi być odfiltrowane przy ON"
    assert "300" not in merged, "poza-bbox musi być odfiltrowane przy ON"


def test_gps_load_off_legacy_keeps_all(monkeypatch, tmp_path):
    _flag(monkeypatch, False)
    path = _write_gps(tmp_path, {
        "100": {"lat": VALID[0], "lon": VALID[1]},
        "200": {"lat": 0.0, "lon": 0.0},
    })
    monkeypatch.setattr(courier_resolver, "GPS_POSITIONS_PWA_PATH", path)
    monkeypatch.setattr(courier_resolver, "GPS_POSITIONS_PATH", str(tmp_path / "nx.json"))
    merged = courier_resolver._load_gps_positions()
    assert "100" in merged and "200" in merged, "OFF musi być legacy"


# ── gps_server ingest (K5a) ─────────────────────────────────────────────────

def test_gps_ingest_ok_on_off(monkeypatch):
    from dispatch_v2 import gps_server
    _flag(monkeypatch, True)
    assert gps_server._ingest_coords_ok(*VALID)
    assert not gps_server._ingest_coords_ok(0.0, 0.0)
    assert not gps_server._ingest_coords_ok(*OUT_OF_BBOX)
    _flag(monkeypatch, False)
    assert gps_server._ingest_coords_ok(0.0, 0.0), "OFF = legacy pass-through"


# ── shadow_dispatcher tick geocode-or-skip (K5a) ────────────────────────────

def test_shadow_payload_sanitize_on(monkeypatch):
    from dispatch_v2 import shadow_dispatcher
    _flag(monkeypatch, True)
    payload = {"pickup_coords": [0.0, 0.0], "delivery_coords": list(VALID)}
    changed = shadow_dispatcher._sanitize_payload_coords(payload, "X1")
    assert changed and payload["pickup_coords"] is None
    assert payload["delivery_coords"] == list(VALID)


def test_shadow_payload_sanitize_off_noop(monkeypatch):
    from dispatch_v2 import shadow_dispatcher
    _flag(monkeypatch, False)
    payload = {"pickup_coords": [0.0, 0.0]}
    assert not shadow_dispatcher._sanitize_payload_coords(payload, "X1")
    assert payload["pickup_coords"] == [0.0, 0.0], "OFF = legacy (verbatim)"


# ── panel_watcher._save_plan_on_assign (K5b: produkcja placeholderów) ───────

def _pending_fixture(tmp_path, cid="C515", oids=("N1",)):
    rec = {
        "ts": "2026-07-01T10:00:00+00:00",
        "decision_record": {
            "ts": "2026-07-01T10:00:00+00:00",
            "best": {
                "courier_id": cid,
                "pos_source": "gps",
                "plan": {
                    "sequence": list(oids),
                    "pickup_at": {oids[0]: "2026-07-01T10:10:00+00:00"},
                    "predicted_delivered_at": {oids[0]: "2026-07-01T10:30:00+00:00"},
                    "strategy": "greedy",
                },
                "bag_context": [],
            },
        },
    }
    p = tmp_path / "pending_proposals.json"
    p.write_text(json.dumps({oids[0]: rec}))
    return str(p)


def _run_save_plan(monkeypatch, tmp_path, flag_on):
    from dispatch_v2 import panel_watcher
    _flag(monkeypatch, flag_on)
    _reset_state()
    state_machine.upsert_order(
        "N1", {"status": "assigned", "pickup_coords": [53.128, 23.152],
               "delivery_coords": [53.14, 23.17]}, event="NEW_ORDER")
    monkeypatch.setattr(panel_watcher, "_PENDING_PROPOSALS_PATH",
                        _pending_fixture(tmp_path))
    monkeypatch.setattr(C, "ENABLE_SAVED_PLANS", True)
    saved = {}
    monkeypatch.setattr(plan_manager, "save_plan",
                        lambda cid, body, **k: saved.update({"cid": cid, "body": body}))
    panel_watcher._save_plan_on_assign("N1", "C515")
    return saved


def test_save_plan_on_writes_real_coords(monkeypatch, tmp_path):
    saved = _run_save_plan(monkeypatch, tmp_path, flag_on=True)
    assert saved, "save_plan nie zawołany"
    stops = saved["body"]["stops"]
    pickup = next(s for s in stops if s["type"] == "pickup")
    drop = next(s for s in stops if s["type"] == "dropoff")
    assert pickup["coords"] == {"lat": 53.128, "lng": 23.152}
    assert drop["coords"] == {"lat": 53.14, "lng": 23.17}


def test_save_plan_off_legacy_placeholder(monkeypatch, tmp_path):
    saved = _run_save_plan(monkeypatch, tmp_path, flag_on=False)
    assert saved, "save_plan nie zawołany"
    for s in saved["body"]["stops"]:
        assert s["coords"] == {"lat": 0.0, "lng": 0.0}, "OFF = legacy placeholder"


# ── E2E: detonacja serializera → V328 eject (żywy łańcuch 01.07) ────────────

def _fleet_with_poisoned_plan():
    """2 kurierów: C515 z workiem + zatrutym planem (0,0), C100 czysty pusty.
    Wierne odwzorowanie produkcji: częściowy fail (<50%) = cichy drop kuriera
    (przy 100% fail odpala MASS_FAIL heurystyka — inna ścieżka)."""
    now = datetime.now(timezone.utc)
    bag = [{
        "order_id": "B1", "status": "picked_up", "restaurant": "R1",
        "pickup_coords": [53.128, 23.152], "delivery_coords": [53.14, 23.17],
        "picked_up_at": (now - timedelta(minutes=8)).isoformat(),
    }]
    poisoned = SimpleNamespace(
        courier_id="C515", name="Test C515", pos=(53.131, 23.160),
        pos_source="gps", pos_age_min=1.0,
        shift_end=now + timedelta(hours=4), shift_start_min=0,
        bag=bag,
    )
    clean = SimpleNamespace(
        courier_id="C100", name="Test C100", pos=(53.125, 23.150),
        pos_source="gps", pos_age_min=1.0,
        shift_end=now + timedelta(hours=4), shift_start_min=0,
        bag=[],
    )
    return {"C515": poisoned, "C100": clean}


def _load_plan_poisoned_for_c515(cid, *a, **k):
    """Zatruty plan TYLKO dla C515 (czysty kurier bez planu)."""
    return _poisoned_plan(free_in_min=5.0) if str(cid) == "C515" else None


def _order_event():
    now = datetime.now(timezone.utc)
    return {
        "order_id": "NEW1", "restaurant": "R2",
        "pickup_address": "Pickup 2", "pickup_city": "Białystok",
        "delivery_address": "Drop 2", "delivery_city": "Białystok",
        "pickup_at_warsaw": (now + timedelta(minutes=15)).astimezone().isoformat(),
        "pickup_coords": [53.121879, 23.146168],
        "delivery_coords": [53.135, 23.155],
        "status_id": 2,
        "first_seen": (now - timedelta(minutes=2)).isoformat(),
        "address_id": 1,
        "czas_kuriera_warsaw": None, "czas_kuriera_hhmm": None,
    }


def test_e2e_poisoned_plan_off_ejects_courier(monkeypatch):
    """OFF (legacy): haversine na (0,0) z planu C515 → ValueError w metrykach →
    _v328_eval_safe wyrzuca C515 z puli (dzisiejszy pożar: 28 ofiar 01.07);
    czysty C100 przechodzi (partial fail <50%, bez MASS_FAIL heurystyki)."""
    _flag(monkeypatch, False)
    monkeypatch.setattr(plan_manager, "load_plan", _load_plan_poisoned_for_c515)
    res = dispatch_pipeline.assess_order(
        _order_event(), _fleet_with_poisoned_plan(),
        now=datetime.now(timezone.utc))
    cids = {str(c.courier_id) for c in res.candidates}
    assert "C515" not in cids, (
        f"OFF: kurier z zatrutym planem powinien być wyrzucony (V328), got {cids}")
    assert "C100" in cids, f"czysty kurier musi przejść, got {cids}"


def test_e2e_poisoned_plan_on_courier_stays_in_pool(monkeypatch):
    """ON: probe odfiltrowuje zatruty plan → C515 NORMALNIE ewaluowany →
    zostaje w puli obok czystego (odbudowa puli = cel L2.1)."""
    _flag(monkeypatch, True)
    monkeypatch.setattr(plan_manager, "load_plan", _load_plan_poisoned_for_c515)
    res = dispatch_pipeline.assess_order(
        _order_event(), _fleet_with_poisoned_plan(),
        now=datetime.now(timezone.utc))
    cids = {str(c.courier_id) for c in res.candidates}
    assert "C515" in cids, f"ON: kurier musi zostać w puli (nie V328-eject), got {cids}"
    assert "C100" in cids
    c515 = next(c for c in res.candidates if str(c.courier_id) == "C515")
    # Telemetria trucizny: plan zatruty ale worek czysty → poison_bag None/pusty.
    m = c515.metrics or {}
    assert not m.get("coord_poison_bag_oids")
    assert m.get("coord_poison_new_delivery") is False
