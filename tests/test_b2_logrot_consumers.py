"""SP-B2-LOGROT — testy konsumentów po sweepie logrotate-aware (2026-06-11).

Scenariusz wspólny: okno agregatu obejmuje rotację (rekordy w .1 + żywym);
przed sweepem konsument widział tylko żywy ogon.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dispatch_v2.tools import bug_e_resolution_tracker as bet
from dispatch_v2.tools import commit_divergence_resolution_tracker as cdt
from dispatch_v2.tools import czasowka_state_cleanup as csc
from dispatch_v2.czasowka_proactive import state as cp_state
from dispatch_v2 import daily_briefing as db


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---- bug_e tracker ----

def _bug_e_rec(oid, ts):
    return {
        "ts": _iso(ts),
        "order_id": oid,
        "verdict": "KOORD",
        "reason": "best_effort_r6_breach_v2",
        "best": {"courier_id": "1", "courier_name": "X", "score": 1.0},
    }


def test_bug_e_tracker_reads_rotated(tmp_path, monkeypatch):
    base = tmp_path / "shadow_decisions.jsonl"
    t = _now()
    _write_jsonl(str(base) + ".1", [_bug_e_rec("111", t - timedelta(hours=10))])
    _write_jsonl(base, [_bug_e_rec("222", t - timedelta(hours=1))])
    monkeypatch.setattr(bet, "SHADOW_LOG", Path(base))

    by_oid = bet.collect_ziomek_koord_records(hours_back=24)
    assert set(by_oid) == {"111", "222"}, "rekord z .1 musi wejść do okna 24h"


def test_bug_e_tracker_window_filter_still_applies(tmp_path, monkeypatch):
    base = tmp_path / "shadow_decisions.jsonl"
    t = _now()
    _write_jsonl(str(base) + ".1", [_bug_e_rec("111", t - timedelta(hours=30))])
    _write_jsonl(base, [_bug_e_rec("222", t - timedelta(hours=1))])
    monkeypatch.setattr(bet, "SHADOW_LOG", Path(base))

    by_oid = bet.collect_ziomek_koord_records(hours_back=24)
    assert set(by_oid) == {"222"}, "per-rekord cutoff ts nadal odsiewa"


# ---- commit divergence tracker ----

def _cd_rec(oid, ts):
    return {
        "ts": _iso(ts),
        "order_id": oid,
        "verdict": "KOORD",
        "reason": "commit_divergence_gate",
        "best": {"courier_id": "1", "courier_name": "X", "score": 1.0},
    }


def test_commit_divergence_tracker_reads_rotated(tmp_path, monkeypatch):
    base = tmp_path / "shadow_decisions.jsonl"
    t = _now()
    _write_jsonl(str(base) + ".1", [_cd_rec("333", t - timedelta(hours=12))])
    _write_jsonl(base, [_cd_rec("444", t - timedelta(minutes=30))])
    monkeypatch.setattr(cdt, "SHADOW_LOG", Path(base))

    by_oid = cdt.collect_ziomek_koord_records(hours_back=24)
    assert set(by_oid) == {"333", "444"}


# ---- daily_briefing ----

def test_briefing_learning_counter_spans_rotation(tmp_path):
    base = tmp_path / "learning_log.jsonl"
    t = _now()
    _write_jsonl(str(base) + ".1", [
        {"ts": _iso(t - timedelta(hours=20)), "action": "TAK"},
        {"ts": _iso(t - timedelta(hours=18)), "action": "NIE"},
    ])
    _write_jsonl(base, [
        {"ts": _iso(t - timedelta(hours=2)), "action": "TAK"},
    ])
    counts = db._count_learning_in_range(str(base), t - timedelta(hours=24), t)
    assert counts["TAK"] == 2 and counts["NIE"] == 1


def test_briefing_learning_counter_range_excludes_old(tmp_path):
    base = tmp_path / "learning_log.jsonl"
    t = _now()
    _write_jsonl(base, [
        {"ts": _iso(t - timedelta(hours=30)), "action": "TAK"},
        {"ts": _iso(t - timedelta(hours=2)), "action": "KOORD"},
    ])
    counts = db._count_learning_in_range(str(base), t - timedelta(hours=24), t)
    assert counts.get("TAK", 0) == 0 and counts["KOORD"] == 1


# ---- shadow_outcome_enricher ----

def test_enricher_scans_rotated_sibling(tmp_path, monkeypatch):
    """Rekord przeniesiony do .1 przez copytruncate o 00:00 nadal wchodzi do
    re-scanu okna (przed sweepem: ginął — outcome z końca dnia nie łapany)."""
    import sqlite3
    from dispatch_v2.tools import shadow_outcome_enricher as enr

    shadow = str(tmp_path / "shadow.jsonl")
    dbpath = str(tmp_path / "events.db")
    con = sqlite3.connect(dbpath)
    con.execute(
        "CREATE TABLE audit_log (event_id TEXT, event_type TEXT, order_id TEXT,"
        " courier_id TEXT, payload TEXT, created_at TEXT)"
    )
    t = _now()
    con.execute(
        "INSERT INTO audit_log VALUES ('e1','COURIER_PICKED_UP','ROT','9','{}',?)",
        (_iso(t - timedelta(hours=1)),),
    )
    con.commit()
    con.close()

    monkeypatch.setattr(enr, "SHADOW_LOG", shadow)
    monkeypatch.setattr(enr, "ENRICHED_LOG", str(tmp_path / "enriched.jsonl"))
    monkeypatch.setattr(enr, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(enr, "EVENTS_DB", dbpath)

    # decyzja w .1 (zrotowana), żywy plik pusty (po copytruncate)
    _write_jsonl(shadow + ".1", [{
        "ts": _iso(t - timedelta(hours=3)), "order_id": "ROT",
        "verdict": "PROPOSE",
        "best": {"courier_id": "9", "travel_min": 10, "drive_min": 8},
    }])
    Path(shadow).write_text("")

    stats = enr.run(hours=24)
    assert stats["shadow_scanned"] == 1, "rekord z .1 musi wejść do skanu"
    assert stats["enriched"] == 1


# ---- czasowka_state_cleanup ----

def _redirect_state(monkeypatch, tmp_path):
    state_file = tmp_path / "czasowka_proposals_state.json"
    monkeypatch.setattr(cp_state, "STATE_PATH", state_file)
    monkeypatch.setattr(cp_state, "LOCK_PATH", Path(str(state_file) + ".lock"))
    return state_file


def _seed_state(state_file, now):
    orders = {
        # świeża czasówka (odbiór za 2h) → zostaje
        "470001": {"czas_odbioru_ts": _iso(now + timedelta(hours=2)),
                   "final_assignment_ts": None},
        # odbiór 3 dni temu → stale
        "470002": {"czas_odbioru_ts": _iso(now - timedelta(days=3)),
                   "final_assignment_ts": None},
        # testowy oid ze świeżym odbiorem (nie-stale) → mimo to purge
        "500001": {"czas_odbioru_ts": _iso(now + timedelta(hours=2)),
                   "final_assignment_ts": None},
        # testowy oid stary → purge (przez stale LUB filtr)
        "500000": {"czas_odbioru_ts": _iso(now - timedelta(days=30)),
                   "final_assignment_ts": None},
    }
    state_file.write_text(json.dumps({"orders": orders, "updated_at": None}))


def test_cleanup_removes_stale_and_test_oids(tmp_path, monkeypatch):
    state_file = _redirect_state(monkeypatch, tmp_path)
    now = _now()
    _seed_state(state_file, now)

    stats = csc.run(dry_run=False)
    assert stats["before"] == 4
    assert stats["after"] == 1
    on_disk = json.loads(state_file.read_text())
    assert set(on_disk["orders"]) == {"470001"}
    assert stats["test_oids"] >= 1  # 500001 na pewno przez filtr testowy


def test_cleanup_dry_run_does_not_write(tmp_path, monkeypatch):
    state_file = _redirect_state(monkeypatch, tmp_path)
    now = _now()
    _seed_state(state_file, now)
    before_txt = state_file.read_text()

    stats = csc.run(dry_run=True)
    assert stats["dry_run"] is True and stats["after"] == 1
    assert state_file.read_text() == before_txt, "dry-run nie zapisuje"


def test_purge_test_oids_handles_garbage_keys():
    state = {"orders": {"abc": {}, "500005": {}, "470000": {}}}
    removed = csc._purge_test_oids(state)
    assert removed == 1
    assert set(state["orders"]) == {"abc", "470000"}
