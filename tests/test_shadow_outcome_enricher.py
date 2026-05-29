"""#21 Opcja C — shadow_outcome_enricher unit + integration tests.

Coverage:
- enrich_record: ready path (pickup present), not ready (no pickup), no best
- query_outcomes: select & deduplicate first ASSIGNED/PICKED_UP/DELIVERED per oid
- iter_shadow_records: offset tracking
- run() pipeline: dry-run idempotency, dedup via processed_oids
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest


# ── enrich_record ────────────────────────────────────────────────────────


def test_enrich_record_full_path():
    """All 3 outcomes present + best courier → full enriched record."""
    from dispatch_v2.tools.shadow_outcome_enricher import enrich_record
    decision = {
        "ts": "2026-05-27T07:00:00+00:00",
        "order_id": "476307",
        "verdict": "PROPOSE",
        "reason": "ok",
        "best": {
            "courier_id": "393",
            "name": "Michal K.",
            "target_pickup_at": "2026-05-27T08:00:00+00:00",
            "travel_min": 40.0,
            "drive_min": 35.0,
            "pos_source": "pre_shift",
            "km_to_pickup": 2.5,
            "traffic_v2_shadow_route": {"n_legs": 3, "avg_v2_mult": 1.7},
        },
    }
    outcomes = {
        "assigned": {"courier_id": "393", "created_at_utc": "2026-05-27T07:05:00+00:00", "payload": {}},
        "picked_up": {"courier_id": "393", "created_at_utc": "2026-05-27T07:52:00+00:00", "payload": {}},
        "delivered": {"courier_id": "393", "created_at_utc": "2026-05-27T08:02:00+00:00", "payload": {}},
    }
    r = enrich_record(decision, outcomes)
    assert r is not None
    assert r["order_id"] == "476307"
    assert r["predicted"]["travel_min"] == 40.0
    assert r["predicted"]["traffic_v2_shadow_route"]["n_legs"] == 3
    # actual: decision_ts 07:00, pickup 07:52 → 52 min
    assert abs(r["actual"]["actual_kurier_to_pickup_min"] - 52.0) < 0.1
    # assign 07:05 → pickup 07:52 = 47 min
    assert abs(r["actual"]["actual_assign_to_pickup_min"] - 47.0) < 0.1
    # delivery: pickup 07:52 → delivered 08:02 = 10 min
    assert abs(r["actual"]["actual_pickup_to_delivery_min"] - 10.0) < 0.1
    # delta: assign_to_pickup 47 − travel_min 40 = +7
    assert abs(r["delta"]["assign_to_pickup_vs_travel_min"] - 7.0) < 0.1
    assert r["actual"]["kurier_overridden"] is False


def test_enrich_record_returns_none_when_not_picked_up():
    """No COURIER_PICKED_UP → record not ready, skip dla next run."""
    from dispatch_v2.tools.shadow_outcome_enricher import enrich_record
    decision = {"ts": "2026-05-27T07:00:00+00:00", "order_id": "X", "best": {"courier_id": "1", "travel_min": 5}}
    outcomes = {"assigned": None, "picked_up": None, "delivered": None}
    assert enrich_record(decision, outcomes) is None


def test_enrich_record_returns_none_when_no_best():
    """KOORD verdict / no best → skip (no proposed kurier do walidacji)."""
    from dispatch_v2.tools.shadow_outcome_enricher import enrich_record
    decision = {"ts": "2026-05-27T07:00:00+00:00", "order_id": "X", "best": None}
    outcomes = {"picked_up": {"courier_id": "1", "created_at_utc": "2026-05-27T07:30:00+00:00", "payload": {}}}
    assert enrich_record(decision, outcomes) is None


def test_enrich_record_detects_human_override():
    """Proposed cid != applied cid → kurier_overridden=True."""
    from dispatch_v2.tools.shadow_outcome_enricher import enrich_record
    decision = {
        "ts": "2026-05-27T07:00:00+00:00",
        "order_id": "X",
        "best": {"courier_id": "1", "travel_min": 10, "drive_min": 8},
    }
    outcomes = {
        "assigned": {"courier_id": "2", "created_at_utc": "2026-05-27T07:05:00+00:00", "payload": {}},
        "picked_up": {"courier_id": "2", "created_at_utc": "2026-05-27T07:15:00+00:00", "payload": {}},
    }
    r = enrich_record(decision, outcomes)
    assert r is not None
    assert r["actual"]["kurier_overridden"] is True
    assert r["actual"]["applied_courier_id"] == "2"


# ── query_outcomes (events.db integration) ───────────────────────────────


def _make_temp_db():
    """Build a small in-memory-style db with audit_log table + sample records."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE audit_log (
            event_id TEXT,
            event_type TEXT,
            order_id TEXT,
            courier_id TEXT,
            payload TEXT,
            created_at TEXT
        )
    """)
    rows = [
        # oid 1: full lifecycle
        ("e1", "COURIER_ASSIGNED", "1", "100", "{}", "2026-05-27T07:05:00+00:00"),
        ("e2", "COURIER_PICKED_UP", "1", "100", '{"pickup_coords": [53.13, 23.16]}', "2026-05-27T07:30:00+00:00"),
        ("e3", "COURIER_DELIVERED", "1", "100", '{"final_location": "X"}', "2026-05-27T07:45:00+00:00"),
        # oid 2: only assigned (in-flight)
        ("e4", "COURIER_ASSIGNED", "2", "200", "{}", "2026-05-27T08:00:00+00:00"),
        # oid 3: assignment changed (race)
        ("e5", "COURIER_ASSIGNED", "3", "300", "{}", "2026-05-27T09:00:00+00:00"),
        ("e6", "COURIER_ASSIGNED", "3", "301", "{}", "2026-05-27T09:01:00+00:00"),  # 2nd assign
        ("e7", "COURIER_PICKED_UP", "3", "301", "{}", "2026-05-27T09:10:00+00:00"),
    ]
    cur.executemany(
        "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?)", rows
    )
    con.commit()
    return con, path


def test_query_outcomes_full_lifecycle():
    """oid 1: assigned + picked_up + delivered all returned."""
    from dispatch_v2.tools.shadow_outcome_enricher import query_outcomes
    con, path = _make_temp_db()
    try:
        out = query_outcomes(con, "1")
        assert out["assigned"]["courier_id"] == "100"
        assert out["picked_up"]["courier_id"] == "100"
        assert out["delivered"]["courier_id"] == "100"
    finally:
        con.close()
        os.unlink(path)


def test_query_outcomes_first_assigned_only():
    """oid 3: 2 ASSIGNED events → first one selected (earliest by created_at)."""
    from dispatch_v2.tools.shadow_outcome_enricher import query_outcomes
    con, path = _make_temp_db()
    try:
        out = query_outcomes(con, "3")
        assert out["assigned"]["courier_id"] == "300"  # earliest, NOT 301
        assert out["picked_up"]["courier_id"] == "301"
    finally:
        con.close()
        os.unlink(path)


def test_query_outcomes_in_flight():
    """oid 2: only ASSIGNED (no PICKED_UP yet) — picked_up=None signals not ready."""
    from dispatch_v2.tools.shadow_outcome_enricher import query_outcomes
    con, path = _make_temp_db()
    try:
        out = query_outcomes(con, "2")
        assert out["assigned"] is not None
        assert out["picked_up"] is None
        assert out["delivered"] is None
    finally:
        con.close()
        os.unlink(path)


# ── State management ─────────────────────────────────────────────────────


def test_state_save_load_roundtrip(tmp_path, monkeypatch):
    """save_state(roundtrip) → load_state preserves data."""
    from dispatch_v2.tools import shadow_outcome_enricher as enricher
    state_file = str(tmp_path / "state.json")
    monkeypatch.setattr(enricher, "STATE_FILE", state_file)

    s = {"last_offset": 12345, "processed_oids": {"a", "b", "c"}}
    enricher.save_state(s)
    loaded = enricher.load_state()
    assert loaded["last_offset"] == 12345
    assert loaded["processed_oids"] == {"a", "b", "c"}


def test_state_load_missing_returns_defaults(tmp_path, monkeypatch):
    """load_state z missing file → default empty state."""
    from dispatch_v2.tools import shadow_outcome_enricher as enricher
    monkeypatch.setattr(enricher, "STATE_FILE", str(tmp_path / "nonexistent.json"))
    s = enricher.load_state()
    assert s["last_offset"] == 0
    assert s["processed_oids"] == set()


# ── run() pipeline regression (#6 fix) ───────────────────────────────────


def _write_shadow(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_run_reenriches_after_outcome_lands(tmp_path, monkeypatch):
    """REGRESJA #6: rekord not_ready w runie 1 MUSI zostać wzbogacony w runie 2,
    gdy COURIER_PICKED_UP dotrze później. Stary kod przewijał offset do EOF po
    pierwszym skanie → rekordu nigdy nie re-czytał → outcome nigdy złapany."""
    from datetime import datetime, timezone, timedelta
    from dispatch_v2.tools import shadow_outcome_enricher as enr

    shadow = str(tmp_path / "shadow.jsonl")
    enriched = str(tmp_path / "enriched.jsonl")
    state = str(tmp_path / "state.json")
    con, dbpath = _make_temp_db()  # tworzy tabelę audit_log (oid NR jeszcze nie istnieje)
    con.close()  # run() otwiera własne połączenie
    monkeypatch.setattr(enr, "SHADOW_LOG", shadow)
    monkeypatch.setattr(enr, "ENRICHED_LOG", enriched)
    monkeypatch.setattr(enr, "STATE_FILE", state)
    monkeypatch.setattr(enr, "EVENTS_DB", dbpath)

    ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    _write_shadow(shadow, [{"ts": ts, "order_id": "NR", "verdict": "PROPOSE",
                            "best": {"courier_id": "9", "travel_min": 10, "drive_min": 8}}])

    # Run 1: brak picked_up → not_ready
    s1 = enr.run(hours=240)
    assert s1["enriched"] == 0
    assert s1["skipped_not_ready"] == 1

    # Outcome dociera PO runie 1
    con = sqlite3.connect(dbpath); cur = con.cursor()
    pts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    cur.execute("INSERT INTO audit_log VALUES (?,?,?,?,?,?)",
                ("eNR1", "COURIER_ASSIGNED", "NR", "9", "{}", ts))
    cur.execute("INSERT INTO audit_log VALUES (?,?,?,?,?,?)",
                ("eNR2", "COURIER_PICKED_UP", "NR", "9", "{}", pts))
    con.commit(); con.close()

    # Run 2: MUSI wzbogacić (rekord re-czytany mimo stanu z runu 1)
    s2 = enr.run(hours=240)
    assert s2["enriched"] == 1, "rekord not_ready musi być re-czytany gdy outcome dojrzeje"
    lines = open(enriched).read().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["order_id"] == "NR"

    # Run 3: dedup — już wzbogacony, brak duplikatu
    s3 = enr.run(hours=240)
    assert s3["enriched"] == 0
    assert s3["skipped_dedup"] == 1

    os.unlink(dbpath)
