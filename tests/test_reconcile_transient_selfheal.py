"""Transient TOCTOU ghost — self-heal + re-validation (2026-05-31).

Regression dla godzinnego false-positive 'reconciliation_degraded: ghosts=1'.
Root cause: reconcile worker wczytuje orders_state snapshot na początku runu;
order doręczony MID-RUN (event COURIER_DELIVERED w events.db, ale state jeszcze
nie zapisany jako delivered) jest fałszywie flagowany jako GHOST. Stary
query_recent_summary liczył ten ghost z logu przez pełne 24h bez sprawdzenia
czy rozjazd nadal istnieje → downstream_status degraded → Telegram co godzinę.

Dwa mechanizmy (oba za flagą, default ON):
  Warstwa 2 — reconcile_log.query_recent_summary(self_heal): nie liczy ghostów
              które dogoniły do terminal w bieżącym orders_state.json.
  Warstwa 1 — reconcile_worker._revalidate_transient: odrzuca rozjazdy które
              znikają przy świeżym odczycie stanu (eliminacja u źródła).
"""
import json
import time
from pathlib import Path

from dispatch_v2.reconciliation import reconcile_log as rl


def _write_log(tmp_path: Path, records: list) -> Path:
    p = tmp_path / "reconciliation_log.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return p


def _write_state(tmp_path: Path, state: dict) -> Path:
    p = tmp_path / "orders_state.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return p


def _ghost_record(oid: str, ts: str) -> dict:
    return {
        "ts": ts,
        "run_id": "reconcile_test",
        "type": "GHOST",
        "order_id": oid,
        "courier_id": "518",
        "last_event_type": "COURIER_DELIVERED",
        "state_status": "picked_up",
        "action": "alert_only_ghost",
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Warstwa 2 — query_recent_summary self-heal
# ---------------------------------------------------------------------------

def test_selfheal_drops_resolved_ghost(tmp_path):
    """Ghost zalogowany, ale order już delivered w bieżącym state → ghosts=0, ok."""
    log = _write_log(tmp_path, [_ghost_record("477379", _now_iso())])
    state = _write_state(tmp_path, {"477379": {"status": "delivered"}})
    s = rl.query_recent_summary(log_path=log, self_heal=True, orders_state_path=state)
    assert s["discrepancies_24h"]["ghosts"] == 0
    assert s["discrepancies_24h"]["manual_alerts"] == 0  # ghost liczy się w obu
    assert s["status"] == "ok"


def test_selfheal_keeps_live_ghost(tmp_path):
    """Order NADAL aktywny (picked_up) → realny rozjazd → ghosts=1, degraded."""
    log = _write_log(tmp_path, [_ghost_record("477400", _now_iso())])
    state = _write_state(tmp_path, {"477400": {"status": "picked_up"}})
    s = rl.query_recent_summary(log_path=log, self_heal=True, orders_state_path=state)
    assert s["discrepancies_24h"]["ghosts"] == 1
    assert s["status"] == "degraded"


def test_selfheal_order_missing_from_state_is_resolved(tmp_path):
    """Order zniknął ze state (cleaned) → classify zwróciłby None → resolved."""
    log = _write_log(tmp_path, [_ghost_record("477500", _now_iso())])
    state = _write_state(tmp_path, {})  # pusty state
    s = rl.query_recent_summary(log_path=log, self_heal=True, orders_state_path=state)
    assert s["discrepancies_24h"]["ghosts"] == 0
    assert s["status"] == "ok"


def test_selfheal_off_preserves_legacy_behavior(tmp_path):
    """self_heal=False → stare zachowanie: ghost liczony mimo delivered state."""
    log = _write_log(tmp_path, [_ghost_record("477379", _now_iso())])
    state = _write_state(tmp_path, {"477379": {"status": "delivered"}})
    s = rl.query_recent_summary(log_path=log, self_heal=False, orders_state_path=state)
    assert s["discrepancies_24h"]["ghosts"] == 1
    assert s["status"] == "degraded"


def test_selfheal_missing_state_file_is_conservative(tmp_path):
    """Brak pliku state → nie mogę zweryfikować → NIE self-heal (nie maskuj real)."""
    log = _write_log(tmp_path, [_ghost_record("477379", _now_iso())])
    missing = tmp_path / "does_not_exist.json"
    s = rl.query_recent_summary(log_path=log, self_heal=True, orders_state_path=missing)
    assert s["discrepancies_24h"]["ghosts"] == 1  # zachowawczo zachowany
    assert s["status"] == "degraded"


def test_selfheal_mixed_one_resolved_one_live(tmp_path):
    """Dwa ghosty: jeden delivered (drop), jeden picked_up (keep)."""
    log = _write_log(tmp_path, [
        _ghost_record("477379", _now_iso()),
        _ghost_record("477400", _now_iso()),
    ])
    state = _write_state(tmp_path, {
        "477379": {"status": "delivered"},
        "477400": {"status": "picked_up"},
    })
    s = rl.query_recent_summary(log_path=log, self_heal=True, orders_state_path=state)
    assert s["discrepancies_24h"]["ghosts"] == 1
    assert s["status"] == "degraded"


# ---------------------------------------------------------------------------
# helper _resolved_ghost_oids — jednostkowo
# ---------------------------------------------------------------------------

def test_resolved_ghost_oids_helper():
    state = {
        "A": {"status": "delivered"},
        "B": {"status": "picked_up"},
        "C": {"status": "assigned"},
        "D": {"status": "cancelled"},
    }
    resolved = rl._resolved_ghost_oids({"A", "B", "C", "D", "E"}, state)
    # A delivered→resolved, D cancelled→resolved, E missing→resolved;
    # B picked_up + C assigned → wciąż aktywne (keep)
    assert resolved == {"A", "D", "E"}


def test_resolved_ghost_oids_skips_synthetic_none():
    """Syntetyczne __none_* oidy pomijane (order_id=None nie weryfikowalny)."""
    resolved = rl._resolved_ghost_oids({"__none_0", "__none_1"}, {})
    assert resolved == set()


# ---------------------------------------------------------------------------
# Warstwa 1 — reconcile_worker._revalidate_transient
# ---------------------------------------------------------------------------

def test_revalidate_drops_transient_ghost(monkeypatch):
    """Świeży odczyt: state dogonił do delivered + events terminal → drop."""
    from dispatch_v2.reconciliation import reconcile_worker as rw
    from dispatch_v2.reconciliation import phantom_detector as pd

    discrepancies = [{
        "order_id": "477379",
        "courier_id": "518",
        "classification": "GHOST",
        "last_event_type": "COURIER_DELIVERED",
        "state_status": "picked_up",
        "last_event_age_h": 0.0,
    }]
    # Fresh state: order już delivered
    monkeypatch.setattr(rw, "_load_orders_state", lambda: {"477379": {"status": "delivered"}})
    # Fresh last event: terminal
    monkeypatch.setattr(
        pd, "get_last_events_per_order",
        lambda db, since_dt=None: {"477379": ("COURIER_DELIVERED", "518", _now_iso())},
    )
    out = rw._revalidate_transient(discrepancies, lookback_days=30)
    assert out == []  # transient → dropped


def test_revalidate_keeps_live_ghost(monkeypatch):
    """Świeży odczyt: state nadal picked_up + events terminal → realny ghost, keep."""
    from dispatch_v2.reconciliation import reconcile_worker as rw
    from dispatch_v2.reconciliation import phantom_detector as pd

    discrepancies = [{
        "order_id": "477400",
        "courier_id": "518",
        "classification": "GHOST",
        "last_event_type": "COURIER_DELIVERED",
        "state_status": "picked_up",
        "last_event_age_h": 1.0,
    }]
    monkeypatch.setattr(rw, "_load_orders_state", lambda: {"477400": {"status": "picked_up"}})
    monkeypatch.setattr(
        pd, "get_last_events_per_order",
        lambda db, since_dt=None: {"477400": ("COURIER_DELIVERED", "518", _now_iso())},
    )
    out = rw._revalidate_transient(discrepancies, lookback_days=30)
    assert len(out) == 1
    assert out[0]["order_id"] == "477400"
    assert out[0]["classification"] == "GHOST"


def test_revalidate_keeps_when_event_unavailable(monkeypatch):
    """Brak świeżego eventu dla oid → zachowawczo zachowaj wpis."""
    from dispatch_v2.reconciliation import reconcile_worker as rw
    from dispatch_v2.reconciliation import phantom_detector as pd

    discrepancies = [{"order_id": "477999", "classification": "GHOST", "last_event_age_h": 2.0}]
    monkeypatch.setattr(rw, "_load_orders_state", lambda: {})
    monkeypatch.setattr(pd, "get_last_events_per_order", lambda db, since_dt=None: {})
    out = rw._revalidate_transient(discrepancies, lookback_days=30)
    assert len(out) == 1  # nie da się re-zweryfikować → keep


def test_revalidate_empty_passthrough():
    from dispatch_v2.reconciliation import reconcile_worker as rw
    assert rw._revalidate_transient([], lookback_days=30) == []
