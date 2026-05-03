"""V3.28 Fix 5 (incident 03.05.2026, Lekcja #67) — health endpoint downstream tests.

Pre-flight 9:55 raportował GREEN parser mimo że pipeline silent 12h.
Lekcja #67: pre-flight diagnostic MUST cross-check primary output produced
RIGHT NOW, NIE tylko parser metadata.

Helper `_v328_compute_downstream_status` testowany w izolacji.
Helper `_v328_query_events_stats` integration test (z synthetic in-memory db).
"""
import sys
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import parser_health_endpoint as phe  # noqa: E402


def test_compute_downstream_normal_state_ok():
    """Wszystkie sygnały zdrowe → status=ok."""
    result = phe._v328_compute_downstream_status(
        last_proposal_age_sec=120.0,
        events_failed_1h=0,
        new_orders_1h=5,
        worker_age_sec=60.0,
    )
    assert result["downstream_status"] == "ok"
    assert result["downstream_reason"] is None


def test_compute_downstream_pipeline_silent_critical():
    """last_proposal_age=2000s + new_orders=5 → critical pipeline_silent_despite_work.

    Production scenario: 02.05 23:03 → 03.05 ~10:00. Telegram nie wysyła propose
    od 12h, ALE NEW_ORDERY napływają. Cross-check by Lekcja #67.
    """
    result = phe._v328_compute_downstream_status(
        last_proposal_age_sec=2000.0,
        events_failed_1h=0,
        new_orders_1h=5,
        worker_age_sec=60.0,
    )
    assert result["downstream_status"] == "critical"
    assert result["downstream_reason"] == "pipeline_silent_despite_work"


def test_compute_downstream_silent_no_work_NOT_critical():
    """last_proposal_age=2000s ALE new_orders=0 (off-peak) → NIE critical.

    Multi-signal Lekcja #66 — quiet period (no orders) NIE jest "silent despite work".
    """
    result = phe._v328_compute_downstream_status(
        last_proposal_age_sec=2000.0,
        events_failed_1h=0,
        new_orders_1h=0,
        worker_age_sec=60.0,
    )
    assert result["downstream_status"] == "ok"
    assert result["downstream_reason"] is None


def test_compute_downstream_worker_stuck_critical():
    """worker_age=1300s (>2x slow threshold 600) → critical worker_stuck."""
    result = phe._v328_compute_downstream_status(
        last_proposal_age_sec=120.0,
        events_failed_1h=0,
        new_orders_1h=5,
        worker_age_sec=1300.0,
    )
    assert result["downstream_status"] == "critical"
    assert result["downstream_reason"] == "worker_stuck"


def test_compute_downstream_events_failed_high_degraded():
    """events_failed_1h=10 (>5 threshold) → degraded elevated_failure_rate."""
    result = phe._v328_compute_downstream_status(
        last_proposal_age_sec=120.0,
        events_failed_1h=10,
        new_orders_1h=5,
        worker_age_sec=60.0,
    )
    assert result["downstream_status"] == "degraded"
    assert result["downstream_reason"] == "elevated_failure_rate"


def test_compute_downstream_worker_slow_degraded():
    """worker_age=700s (>600 slow, <1200 stuck) → degraded worker_slow."""
    result = phe._v328_compute_downstream_status(
        last_proposal_age_sec=120.0,
        events_failed_1h=0,
        new_orders_1h=5,
        worker_age_sec=700.0,
    )
    assert result["downstream_status"] == "degraded"
    assert result["downstream_reason"] == "worker_slow"


def test_compute_downstream_critical_takes_priority_over_degraded():
    """Compound anomalies — critical bije degraded (priority order)."""
    result = phe._v328_compute_downstream_status(
        last_proposal_age_sec=2000.0,
        events_failed_1h=10,
        new_orders_1h=5,
        worker_age_sec=700.0,
    )
    assert result["downstream_status"] == "critical"
    assert result["downstream_reason"] == "pipeline_silent_despite_work"


def test_compute_downstream_none_inputs_safe():
    """None inputs (DB unavailable lub log unparseable) → ok (defensive default)."""
    result = phe._v328_compute_downstream_status(
        last_proposal_age_sec=None,
        events_failed_1h=0,
        new_orders_1h=0,
        worker_age_sec=None,
    )
    assert result["downstream_status"] == "ok"


def test_query_events_stats_synthetic_db():
    """Integration: query synthetic events.db, verify counts + ages."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            order_id TEXT,
            courier_id TEXT,
            payload TEXT,
            created_at TEXT NOT NULL,
            processed_at TEXT,
            status TEXT DEFAULT 'pending'
        );
    """)
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    propose_30min_ago = (now - timedelta(minutes=30)).isoformat()
    recent_iso = (now - timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("e1", "PROPOSAL_SENT", "100", "1", "{}", recent_iso, propose_30min_ago, "processed"),
    )
    for i in range(3):
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"new_{i}", "NEW_ORDER", f"20{i}", None, "{}", recent_iso, recent_iso, "processed"),
        )
    for i in range(2):
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"fail_{i}", "NEW_ORDER", f"30{i}", None, "{}", recent_iso, None, "failed"),
        )
    conn.commit()
    conn.close()

    result = phe._v328_query_events_stats(events_db_path=db_path)
    assert result["new_orders_last_1h_count"] == 5
    assert result["events_failed_last_1h_count"] == 2
    # Fix 5b: last_proposal_sent_age_sec moved to separate function
    # (_v328_parse_last_propose_age_from_journal). NOT in events_stats anymore.
    assert "last_proposal_sent_age_sec" not in result

    Path(db_path).unlink()


def test_query_events_stats_missing_db_safe():
    """DB nie istnieje → return zero defaults (defensive). Fix 5b: no PROPOSAL_SENT key."""
    result = phe._v328_query_events_stats(events_db_path="/nonexistent/path.db")
    assert "last_proposal_sent_age_sec" not in result  # Fix 5b removal
    assert result["events_failed_last_1h_count"] == 0
    assert result["new_orders_last_1h_count"] == 0


def test_parse_worker_age_from_journalctl_smoke():
    """V3.28 Fix 5b: worker_age via subprocess journalctl. Live smoke (real system)."""
    # Live system test — subprocess journalctl dispatch-shadow
    age = phe._v328_parse_worker_age_from_log()
    # Either None (gdy żaden HEARTBEAT w 5min) OR float >= 0
    assert age is None or (isinstance(age, float) and age >= 0)


def test_parse_last_propose_age_from_journal_smoke():
    """V3.28 Fix 5b: last_propose age via subprocess journalctl dispatch-telegram. Live smoke."""
    age = phe._v328_parse_last_propose_age_from_journal()
    # Either None (gdy żaden SENT w 2h) OR float >= 0
    assert age is None or (isinstance(age, float) and age >= 0)


def test_thresholds_module_level_constants():
    """Module-level constants z reasonable defaults."""
    assert phe.V328_DOWNSTREAM_PIPELINE_SILENT_AGE_SEC == 1800
    assert phe.V328_DOWNSTREAM_FAILED_1H_THRESHOLD == 5
    assert phe.V328_DOWNSTREAM_WORKER_SLOW_AGE_SEC == 600


def test_returns_dict_with_status_and_reason():
    """Type guarantee."""
    result = phe._v328_compute_downstream_status(None, 0, 0, None)
    assert isinstance(result, dict)
    assert "downstream_status" in result
    assert "downstream_reason" in result
    assert result["downstream_status"] in ("ok", "degraded", "critical")
