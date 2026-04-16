"""Smoke test dla PANEL_OVERRIDE detection (Task #4).

Weryfikuje _check_panel_override w panel_watcher.py:
  1. pending ma oid + proposed=A, panel przypisuje A → BRAK logu
  2. pending ma oid + proposed=A, panel przypisuje B → PANEL_OVERRIDE log
  3. pending NIE ma tego oid → BRAK logu (not-an-override)
  4. pending_proposals.json nie istnieje → BRAK logu (graceful)
  5. proposed courier_id pusty/None → BRAK logu
  6. source label (panel_initial/panel_diff/panel_reassign) poprawnie w zapisie

Uruchomienie: python3 -m dispatch_v2.tests.smoke_panel_override
Nie dotyka prod plików — pełna izolacja przez monkey-patch + tempdir.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import panel_watcher


FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ✓ {msg}")
    else:
        FAILURES.append(msg)
        print(f"  ✗ {msg}")


def _with_isolated_paths(pending_contents, test_fn):
    """Zapisz pending_contents do tmp pliku, podmień ścieżki w module,
    wywołaj test_fn, zwróć listę wpisów learning_log."""
    with tempfile.TemporaryDirectory() as td:
        pending_path = Path(td) / "pending_proposals.json"
        learning_path = Path(td) / "learning_log.jsonl"
        if pending_contents is not None:
            pending_path.write_text(json.dumps(pending_contents, ensure_ascii=False))
        # else: file doesn't exist → FileNotFoundError path

        orig_pending = panel_watcher._PENDING_PROPOSALS_PATH
        orig_learning = panel_watcher._LEARNING_LOG_PATH
        panel_watcher._PENDING_PROPOSALS_PATH = str(pending_path)
        panel_watcher._LEARNING_LOG_PATH = str(learning_path)
        try:
            test_fn()
        finally:
            panel_watcher._PENDING_PROPOSALS_PATH = orig_pending
            panel_watcher._LEARNING_LOG_PATH = orig_learning

        if learning_path.exists():
            lines = [json.loads(ln) for ln in learning_path.read_text().splitlines() if ln.strip()]
            return lines
        return []


def test_case_1_same_courier_no_override():
    """proposed=A, panel=A → no override."""
    print("\n[Case 1] proposed == panel_courier → brak logu:")
    pending = {
        "466700": {
            "message_id": 111,
            "sent_at": "2026-04-16T20:00:00+00:00",
            "expires_at": "2026-04-16T20:30:00+00:00",
            "decision_record": {
                "best": {"courier_id": "207", "score": 85.5, "name": "Marek"},
            },
        },
    }
    logs = _with_isolated_paths(pending, lambda: panel_watcher._check_panel_override(
        "466700", "207", "panel_initial"
    ))
    check(len(logs) == 0, f"brak wpisu w learning_log (got {len(logs)} entries)")


def test_case_2_different_courier_override_logged():
    """proposed=A, panel=B → PANEL_OVERRIDE log."""
    print("\n[Case 2] proposed != panel_courier → PANEL_OVERRIDE log:")
    pending = {
        "466701": {
            "decision_record": {
                "best": {"courier_id": "207", "score": 85.5, "name": "Marek"},
                "restaurant": "Chicago Pizza",
                "order_id": "466701",
            },
        },
    }
    logs = _with_isolated_paths(pending, lambda: panel_watcher._check_panel_override(
        "466701", "289", "panel_diff"
    ))
    check(len(logs) == 1, f"dokładnie 1 wpis w learning_log (got {len(logs)})")
    if logs:
        rec = logs[0]
        check(rec["action"] == "PANEL_OVERRIDE", f"action=PANEL_OVERRIDE (got {rec.get('action')})")
        check(rec["order_id"] == "466701", f"order_id=466701 (got {rec.get('order_id')})")
        check(rec["proposed_courier_id"] == "207", f"proposed=207 (got {rec.get('proposed_courier_id')})")
        check(rec["actual_courier_id"] == "289", f"actual=289 (got {rec.get('actual_courier_id')})")
        check(rec["panel_source"] == "panel_diff", f"source=panel_diff (got {rec.get('panel_source')})")
        check(rec["proposed_score"] == 85.5, f"proposed_score=85.5 (got {rec.get('proposed_score')})")
        check(rec.get("decision", {}).get("restaurant") == "Chicago Pizza",
              "decision_record pełny zachowany (restaurant=Chicago Pizza)")
        check("ts" in rec, "wpis ma timestamp 'ts'")


def test_case_3_oid_not_in_pending():
    """pending NIE ma oid → brak logu."""
    print("\n[Case 3] oid not in pending → brak logu:")
    pending = {"466700": {"decision_record": {"best": {"courier_id": "207"}}}}
    logs = _with_isolated_paths(pending, lambda: panel_watcher._check_panel_override(
        "999999", "289", "panel_initial"
    ))
    check(len(logs) == 0, f"brak wpisu (got {len(logs)})")


def test_case_4_pending_file_missing():
    """pending_proposals.json nie istnieje → graceful, brak exception."""
    print("\n[Case 4] pending_proposals.json missing → graceful:")
    try:
        logs = _with_isolated_paths(None, lambda: panel_watcher._check_panel_override(
            "466700", "289", "panel_initial"
        ))
        check(len(logs) == 0, f"brak wpisu (got {len(logs)})")
        check(True, "brak exception przy missing file")
    except Exception as e:
        check(False, f"nieoczekiwany exception: {e}")


def test_case_5_empty_proposed_courier():
    """proposed_courier_id pusty → brak logu (degenerate case)."""
    print("\n[Case 5] proposed_courier_id pusty → brak logu:")
    pending = {
        "466702": {
            "decision_record": {"best": {"courier_id": "", "score": 0}},
        },
    }
    logs = _with_isolated_paths(pending, lambda: panel_watcher._check_panel_override(
        "466702", "289", "panel_reassign"
    ))
    check(len(logs) == 0, f"brak wpisu dla pustego proposed (got {len(logs)})")


def test_case_6_reassign_source():
    """Source label panel_reassign zapisany poprawnie."""
    print("\n[Case 6] source=panel_reassign:")
    pending = {
        "466703": {
            "decision_record": {"best": {"courier_id": "207", "score": 70}},
        },
    }
    logs = _with_isolated_paths(pending, lambda: panel_watcher._check_panel_override(
        "466703", "289", "panel_reassign"
    ))
    check(len(logs) == 1, f"1 wpis (got {len(logs)})")
    if logs:
        check(logs[0]["panel_source"] == "panel_reassign",
              f"source=panel_reassign (got {logs[0].get('panel_source')})")


def main():
    print("=== SMOKE TEST PANEL_OVERRIDE (Task #4) ===")
    test_case_1_same_courier_no_override()
    test_case_2_different_courier_override_logged()
    test_case_3_oid_not_in_pending()
    test_case_4_pending_file_missing()
    test_case_5_empty_proposed_courier()
    test_case_6_reassign_source()

    print("\n=== WYNIK ===")
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} failure(s):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS (wszystkie asercje OK)")


if __name__ == "__main__":
    main()
