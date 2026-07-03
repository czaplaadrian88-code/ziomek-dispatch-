"""Bug #4 reseq — schema-2 objektyw w loggerze + oracle obj-axis reader (2026-07-02).

Zakres (pas RDZENIA `bug4-logger`, build-only, log-only):
  (A) LOGGER `_bug4_reseq_shadow` schema-2: fresh_total_duration/fresh_sla DARMOWE z
      plan_fresh; frozen_* = null (nota); wersjonowanie `schema:2`; wstecznie zgodne.
  (B) PARYTET DECYZJI: dopisanie pól NIE mutuje żadnego wejścia decyzyjnego i NIE
      zmienia starych pól (replay ≥100, log-only → zero wpływu na decyzje).
  (C) ORACLE read_obj/obj_tripwire: preferuj schema-2; tripwire fresh≤frozen NA OSI
      OBJEKTYWU + mutation ×2 (kill); reverdict statystyki obj-osi + wstecz-kompat.

Loader tool = SELF-LOCATING (C12e): tool z TEGO worktree po ścieżce względnej.
"""
import copy
import importlib.util
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import common as C
from dispatch_v2 import route_simulator_v2 as R

NOW = datetime(2026, 6, 26, 19, 16, tzinfo=timezone.utc)

# Stary (schema-1) zestaw kluczy wpisu — dowód „tylko DOŁOŻONE, nic nie zmienione".
_OLD_KEYS = {
    "ts", "cid", "bag", "n_orders", "deliv_seq_differs", "frozen_deliv_order",
    "fresh_deliv_order", "seq_differs", "frozen_drive_min", "fresh_drive_min",
    "delta_min", "invariant_violation", "frozen_seq", "fresh_seq",
}
_NEW_KEYS = {
    "schema", "fresh_total_duration", "fresh_sla", "frozen_total_duration",
    "frozen_sla", "obj_axis_note",
}


class _FakePlan:
    sla_violations = 1
    total_duration_min = 33.75
    sequence = ["A", "B"]
    pickup_at = {"A": datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc),
                 "B": datetime(2026, 6, 26, 19, 40, tzinfo=timezone.utc)}
    predicted_delivered_at = {"A": datetime(2026, 6, 26, 19, 35, tzinfo=timezone.utc),
                              "B": datetime(2026, 6, 26, 19, 50, tzinfo=timezone.utc)}


def _orders():
    return {
        "A": {"status": "assigned", "pickup_coords": [53.11, 23.14],
              "delivery_coords": [53.12, 23.13], "courier_id": "99",
              "czas_kuriera_warsaw": "2026-06-26T21:16:00+02:00"},
        "B": {"status": "assigned", "pickup_coords": [53.12, 23.15],
              "delivery_coords": [53.13, 23.18], "courier_id": "99",
              "czas_kuriera_warsaw": "2026-06-26T21:29:00+02:00"},
    }


def _existing():
    return {"stops": [
        {"order_id": "A", "type": "pickup", "coords": {"lat": 53.11, "lng": 23.14}},
        {"order_id": "B", "type": "pickup", "coords": {"lat": 53.12, "lng": 23.15}},
        {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.12, "lng": 23.13}},
        {"order_id": "B", "type": "dropoff", "coords": {"lat": 53.13, "lng": 23.18}},
    ]}


def _setup(monkeypatch, tmp_path, fresh=25.0, frozen=30.0, plan=None):
    p = tmp_path / "bug4.jsonl"
    monkeypatch.setattr(PR, "_BUG4_RESEQ_SHADOW_PATH", str(p))
    monkeypatch.setattr(C, "flag",
                        lambda name, default=False: True if name == "ENABLE_BUG4_RESEQ_SHADOW" else default)
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: ((53.1, 23.1), NOW, "gps_pwa"))
    monkeypatch.setattr(R, "simulate_bag_route_v2", lambda *a, **k: (plan or _FakePlan()))
    calls = {"i": 0}

    def fake_sum(start, coords):
        i = calls["i"]
        calls["i"] += 1
        return fresh if i == 0 else frozen
    monkeypatch.setattr(PR, "_osrm_drive_min_sum", fake_sum)
    return p


# ── (A) LOGGER schema-2 completeness ─────────────────────────────────────────
def test_schema2_entry_complete(monkeypatch, tmp_path):
    p = _setup(monkeypatch, tmp_path)
    PR._bug4_reseq_shadow("99", ["A", "B"], _existing(), _orders(), {}, NOW, R, {})
    rec = json.loads(p.read_text().strip())
    assert rec["schema"] == 2
    # fresh objektyw = DARMOWE z plan_fresh (co silnik faktycznie zwrócił)
    assert rec["fresh_total_duration"] == 33.75
    assert rec["fresh_sla"] == 1
    # frozen objektyw = null (zero-cost tick) + nota
    assert rec["frozen_total_duration"] is None
    assert rec["frozen_sla"] is None
    assert "frozen_obj=null" in rec["obj_axis_note"]
    # WSZYSTKIE stare pola (drive-oś) nietknięte
    assert _OLD_KEYS.issubset(rec.keys())
    # DOKŁADNIE dołożone klucze — nic więcej, nic mniej
    assert set(rec.keys()) - _OLD_KEYS == _NEW_KEYS


# ── (B) PARYTET DECYZJI: log-only, zero mutacji wejść, stare pola byte-parytet ──
def test_decision_byte_parity_replay(monkeypatch, tmp_path):
    """Replay ≥100 losowych worków: (1) wejścia (orders_state/existing/gps) NIE
    zmutowane; (2) return None; (3) rekord = STARE pola + DOKŁADNIE _NEW_KEYS —
    dopisanie objektywu NIE dotyka niczego decyzyjnego (funkcja log-only)."""
    rng = random.Random(4)
    n = 120
    for _ in range(n):
        p = _setup(monkeypatch, tmp_path,
                   fresh=round(rng.uniform(5, 40), 2), frozen=round(rng.uniform(5, 40), 2))
        orders = _orders()
        # losowe warianty stanu (picked_up / coords) — nie wpływa na log-only naturę
        if rng.random() < 0.4:
            orders["A"]["status"] = "picked_up"
            orders["A"]["picked_up_at"] = "2026-06-26T19:00:00+00:00"
        existing = _existing()
        gps = {"99": {"timestamp": "2026-06-26T19:15:00+00:00"}}
        before = (copy.deepcopy(orders), copy.deepcopy(existing), copy.deepcopy(gps))
        ret = PR._bug4_reseq_shadow("99", ["A", "B"], existing, orders, gps, NOW, R, {})
        assert ret is None                              # log-only, brak wartości decyzyjnej
        assert (orders, existing, gps) == before        # ZERO mutacji wejść decyzyjnych
        line = p.read_text().strip().splitlines()[-1]
        rec = json.loads(line)
        assert _OLD_KEYS.issubset(rec.keys())           # stare pola zawsze obecne
        assert set(rec.keys()) - _OLD_KEYS == _NEW_KEYS  # zmieniła się TYLKO zawartość wpisu


# ═══ ORACLE (self-locating loader) ═══════════════════════════════════════════
_TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "bug4_reseq_oracle.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("bug4_oracle_s2_uut", _TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


@pytest.fixture()
def O():
    mod = _load_tool()
    try:
        yield mod
    finally:
        sys.modules.pop("bug4_oracle_s2_uut", None)


def _rec(**kw):
    base = {"ts": "2026-07-02T10:00:00+00:00", "cid": "1"}
    base.update(kw)
    return base


# ── (C1) read_obj: preferuj schema-2, fallback dla starych ───────────────────
def test_read_obj_prefers_schema2(O):
    r = _rec(schema=2, fresh_total_duration=30.0, fresh_sla=1,
             frozen_total_duration=34.0, frozen_sla=2)
    assert O.read_obj(r) == (30.0, 1, 34.0, 2, "schema2")


def test_read_obj_old_record_reconstruct(O):
    r = _rec(frozen_drive_min=30.0, fresh_drive_min=25.0, delta_min=5.0)  # schema-1
    assert O.read_obj(r) == (None, None, None, None, "reconstruct")


# ── (C2) obj_tripwire fresh≤frozen NA OSI OBJEKTYWU (True/False/None) ─────────
def test_obj_tripwire_bites_on_fresh_worse_total(O):
    r = _rec(schema=2, fresh_total_duration=36.0, fresh_sla=0,
             frozen_total_duration=33.0, frozen_sla=0)
    assert O.obj_tripwire(r) is True   # fresh dłuższy przy równym sla → residual


def test_obj_tripwire_ok_when_fresh_better(O):
    r = _rec(schema=2, fresh_total_duration=30.0, fresh_sla=0,
             frozen_total_duration=34.0, frozen_sla=0)
    assert O.obj_tripwire(r) is False


def test_obj_tripwire_sla_dominates(O):
    worse = _rec(schema=2, fresh_total_duration=10.0, fresh_sla=1,
                 frozen_total_duration=99.0, frozen_sla=0)     # fresh mniej jazdy, ale +sla
    better = _rec(schema=2, fresh_total_duration=99.0, fresh_sla=0,
                  frozen_total_duration=10.0, frozen_sla=1)
    assert O.obj_tripwire(worse) is True    # lex: sla dominuje → fresh gorszy
    assert O.obj_tripwire(better) is False


def test_obj_tripwire_none_when_frozen_null(O):
    r = _rec(schema=2, fresh_total_duration=30.0, fresh_sla=0,
             frozen_total_duration=None, frozen_sla=None)      # dzisiejszy żywy wpis
    assert O.obj_tripwire(r) is None


# ── (C3) MUTATION ×2 (C13: zmutuj cel → test PADA) ───────────────────────────
def test_mutation_total_term_is_load_bearing(O, monkeypatch):
    """Mut1: podbij _EPS ~∞ → marginalnie-gorszy fresh (total) PRZESTAJE być
    naruszeniem → człon porównania total jest NOŚNY (nie martwy)."""
    r = _rec(schema=2, fresh_total_duration=33.10, fresh_sla=0,
             frozen_total_duration=33.0, frozen_sla=0)
    assert O.obj_tripwire(r) is True                 # realnie: fresh > frozen+eps
    monkeypatch.setattr(O, "_EPS", 1e9)
    assert O.obj_tripwire(r) is False, "człon total niewidoczny — tripwire martwy!"


def test_mutation_sla_term_is_load_bearing(O):
    """Mut2: reimplementacja BEZ członu sla zwróciłaby False (bo fresh_total<frozen),
    a wierny tripwire (z sla) → True. Potwierdza, że sla jest lex-dominujące (nośne)."""
    r = _rec(schema=2, fresh_total_duration=20.0, fresh_sla=2,
             frozen_total_duration=40.0, frozen_sla=1)
    ft, fs, zt, zs, _ = O.read_obj(r)

    def _mutant_no_sla(ft, fs, zt, zs):              # ignoruje sla — tylko total
        return ft > zt + O._EPS
    assert _mutant_no_sla(ft, fs, zt, zs) is False   # mutant: fresh krótszy → „OK"
    assert O.obj_tripwire(r) is True                 # wierny: +sla → NARUSZENIE


# ── (C4) reverdict: statystyki osi objektywu + wstecz-kompat starych kluczy ───
def test_reverdict_objective_stats_and_backcompat(O, tmp_path):
    jsonl = tmp_path / "shadow.jsonl"
    rows = [
        # schema-2 żywy: frozen null → obj-tripwire NIEOCENIALE
        _rec(ts="2026-07-02T10:00:00+00:00", schema=2, deliv_seq_differs=True,
             fresh_total_duration=30.0, fresh_sla=0,
             frozen_total_duration=None, frozen_sla=None,
             invariant_violation=False, frozen_seq=["A:dropoff", "B:dropoff"],
             fresh_seq=["B:dropoff", "A:dropoff"], delta_min=2.0),
        # schema-2 z frozen (przyszłość / offline) — fresh GORSZY = residual NARUSZENIE
        _rec(ts="2026-07-02T10:01:00+00:00", schema=2, deliv_seq_differs=True,
             fresh_total_duration=40.0, fresh_sla=0,
             frozen_total_duration=33.0, frozen_sla=0,
             invariant_violation=False, frozen_seq=["X:dropoff", "Y:dropoff"],
             fresh_seq=["Y:dropoff", "X:dropoff"], delta_min=1.0),
        # STARY schema-1 (brak schema/obj) — nadal liczony na starej osi (wstecz-kompat)
        _rec(ts="2026-07-02T10:02:00+00:00", deliv_seq_differs=False,
             invariant_violation=False, frozen_seq=["Z:dropoff"],
             fresh_seq=["Z:dropoff"], delta_min=0.0),
    ]
    with open(jsonl, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    out = O.reverdict_from_log(str(jsonl), since="2026-07-02")
    # NOWE metryki osi objektywu
    assert out["schema2_n"] == 2
    assert out["fresh_obj_n"] == 2
    assert out["obj_tripwire_evaluable"] == 1     # tylko rekord z NIE-null frozen
    assert out["obj_tripwire_violations"] == 1     # fresh 40 > frozen 33 = residual
    # WSTECZ-KOMPAT: stare klucze/semantyka nietknięte
    assert out["n"] == 3
    assert out["deliv_seq_differs"] == 2
    assert "old_drive_suspect" in out and "wrong_axis_fp" in out
