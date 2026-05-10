"""Regression dla F14 (audit STATE_OWNERSHIP 2026-05-07): reconcile worker
HARD_CAP dynamic scaling + backlog_high alert.

Pre-fix: HARD_CAP=5 all-or-nothing. >5 eligible phantoms → all alert_only,
zero auto-resyncs. Incident-day 50+ phantoms → backlog grows faster than
drain (5/run × 48 runs/dzień = 240/dzień max ALE praktycznie 0 gdy
hard_cap_hit fires every run = total stop).

Post-fix:
  - dynamic_scaling=True (default): scale UP cap od hard_cap_per_run (5)
    do min(hard_cap_max=20, backlog_size). Powyżej hard_cap_max →
    all-or-nothing safety stop (legacy extreme-bug guard).
  - backlog_high_alert: counts.backlog_high_alert=True gdy backlog >=
    backlog_alert_threshold (default 50) → worker emit Telegram alert.
  - counts.hard_cap_actual i hard_cap_dynamic_applied: telemetry.

Lekcja #100 (evening #9 incident 28 phantoms post panel-watcher restart
in-peak) — F14 eliminuje recurring drain stuck pattern.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.reconciliation import auto_resync  # noqa: E402


def _phantom(oid, age_h=5.0, cls="PHANTOM"):
    """Build minimal phantom dict (eligible for auto-resync if PHANTOM + age > threshold)."""
    return {
        "order_id": oid,
        "courier_id": "100",
        "classification": cls,
        "phantom_subtype": "STATE_TERMINAL",
        "last_event_age_h": age_h,
        "last_event_ts": "2026-05-09T05:00:00+00:00",
        "state_status": "delivered",
        "inferred_terminal_event": "COURIER_DELIVERED",
        "inferred_reason": "test",
    }


def _run(discrepancies, dry_run=True, **kwargs):
    """Run auto_resync z dry_run default — zwraca counts + actions."""
    return auto_resync.auto_resync_phantoms(
        discrepancies=discrepancies,
        emit_fn=lambda **_: "fake_event_id",
        state_update_fn=lambda _: None,
        dry_run=dry_run,
        **kwargs,
    )


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  PASS  {label}")
            results["pass"] += 1
        else:
            print(f"  FAIL  {label}  {detail}")
            results["fail"] += 1

    importlib.reload(auto_resync)

    # === TEST 1: Backlog ≤ default cap → no scaling ===
    print("\n=== test 1: 3 eligible, default cap=5 → effective_cap=5, no dynamic ===")
    res = _run([_phantom(f"P{i}") for i in range(3)], hard_cap_per_run=5)
    c = res["counts"]
    expect("backlog_size=3", c["backlog_size"] == 3)
    expect("hard_cap_actual=5 (no scale needed)", c["hard_cap_actual"] == 5)
    expect("hard_cap_dynamic_applied=False", c["hard_cap_dynamic_applied"] is False)
    expect("hard_cap_hit=False", c["hard_cap_hit"] is False)
    expect("backlog_high_alert=False", c["backlog_high_alert"] is False)

    # === TEST 2: Backlog > default but ≤ max → dynamic scale up ===
    print("\n=== test 2: 15 eligible, default=5/max=20 → scale to 15, drain ===")
    res = _run(
        [_phantom(f"P{i}") for i in range(15)],
        hard_cap_per_run=5, hard_cap_max=20,
    )
    c = res["counts"]
    expect("backlog_size=15", c["backlog_size"] == 15)
    expect("hard_cap_actual=15 (scaled to backlog)", c["hard_cap_actual"] == 15)
    expect("hard_cap_dynamic_applied=True", c["hard_cap_dynamic_applied"] is True)
    expect("hard_cap_hit=False (drain in scope)", c["hard_cap_hit"] is False)
    # Pre-fix: 0 actions w drain bo all alert_only_hard_cap. Post-fix: 15 drain
    # actions (dry_run=True więc would_resync_dry_run).
    drain_actions = [a for a in res["actions"] if a["action"] == "would_resync_dry_run"]
    expect(f"15 drain actions (got {len(drain_actions)})", len(drain_actions) == 15)

    # === TEST 3: Backlog > max → all-or-nothing safety stop ===
    print("\n=== test 3: 30 eligible, max=20 → exceeds, hard_cap_hit ===")
    res = _run(
        [_phantom(f"P{i}") for i in range(30)],
        hard_cap_per_run=5, hard_cap_max=20,
    )
    c = res["counts"]
    expect("backlog_size=30", c["backlog_size"] == 30)
    expect("hard_cap_actual=20 (capped at max)", c["hard_cap_actual"] == 20)
    expect("hard_cap_hit=True (>max safety stop)", c["hard_cap_hit"] is True)
    expect("alerts_only_hard_cap=30", c["alerts_only_hard_cap"] == 30)
    drain_actions = [a for a in res["actions"] if a["action"] == "would_resync_dry_run"]
    expect("zero drain actions (all-or-nothing)", len(drain_actions) == 0)

    # === TEST 4: backlog_high_alert threshold ===
    print("\n=== test 4: 50 eligible, threshold=50 → backlog_high_alert ===")
    res = _run(
        [_phantom(f"P{i}") for i in range(50)],
        hard_cap_per_run=5, hard_cap_max=20, backlog_alert_threshold=50,
    )
    c = res["counts"]
    expect("backlog_high_alert=True (50 >= 50)", c["backlog_high_alert"] is True)
    expect("backlog_size=50", c["backlog_size"] == 50)

    # === TEST 5: backlog < threshold → NO alert ===
    print("\n=== test 5: 25 eligible, threshold=50 → no backlog_high_alert ===")
    res = _run(
        [_phantom(f"P{i}") for i in range(25)],
        hard_cap_per_run=5, hard_cap_max=20, backlog_alert_threshold=50,
    )
    c = res["counts"]
    expect("backlog_high_alert=False (25 < 50)", c["backlog_high_alert"] is False)

    # === TEST 6: dynamic_scaling=False → legacy behavior ===
    print("\n=== test 6: dynamic_scaling=False, 15 eligible, cap=5 → all-or-nothing ===")
    res = _run(
        [_phantom(f"P{i}") for i in range(15)],
        hard_cap_per_run=5, hard_cap_max=20, dynamic_scaling=False,
    )
    c = res["counts"]
    expect("hard_cap_actual=5 (no scaling)", c["hard_cap_actual"] == 5)
    expect("hard_cap_dynamic_applied=False", c["hard_cap_dynamic_applied"] is False)
    expect("hard_cap_hit=True (15 > 5 legacy)", c["hard_cap_hit"] is True)
    expect("alerts_only_hard_cap=15", c["alerts_only_hard_cap"] == 15)

    # === TEST 7: GHOST + PHANTOM mix — only PHANTOM count w backlog ===
    print("\n=== test 7: 5 GHOST + 10 PHANTOM → backlog=10 ===")
    discr = [_phantom(f"G{i}", cls="GHOST") for i in range(5)] + \
            [_phantom(f"P{i}") for i in range(10)]
    res = _run(discr, hard_cap_per_run=5, hard_cap_max=20)
    c = res["counts"]
    expect("backlog_size=10 (PHANTOM eligible)", c["backlog_size"] == 10)
    expect("ghosts_total=5", c["ghosts_total"] == 5)
    expect("hard_cap_dynamic_applied=True (10>5)", c["hard_cap_dynamic_applied"] is True)
    expect("hard_cap_actual=10", c["hard_cap_actual"] == 10)

    # === TEST 8: Young phantoms (age < threshold) NIE w backlog eligible ===
    print("\n=== test 8: 10 PHANTOM age=2h (<4h thresh) → backlog=0 ===")
    discr = [_phantom(f"Y{i}", age_h=2.0) for i in range(10)]
    res = _run(discr, age_threshold_hours=4.0, hard_cap_per_run=5)
    c = res["counts"]
    expect("backlog_size=0 (all young)", c["backlog_size"] == 0)
    expect("alerts_only_young=10", c["alerts_only_young"] == 10)
    expect("hard_cap_hit=False", c["hard_cap_hit"] is False)

    # === TEST 9: Edge — backlog == hard_cap_per_run (no scale needed) ===
    print("\n=== test 9: backlog == default cap → no dynamic ===")
    res = _run([_phantom(f"P{i}") for i in range(5)], hard_cap_per_run=5, hard_cap_max=20)
    c = res["counts"]
    expect("backlog_size=5", c["backlog_size"] == 5)
    expect("hard_cap_actual=5", c["hard_cap_actual"] == 5)
    expect("hard_cap_dynamic_applied=False (backlog==cap)", c["hard_cap_dynamic_applied"] is False)

    # === TEST 10: Worker integration (dry-run smoke) ===
    print("\n=== test 10: reconcile_worker integration smoke ===")
    from dispatch_v2.reconciliation import reconcile_worker
    expect("FLAG_DEFAULTS has DYNAMIC_SCALING",
           "RECONCILIATION_DYNAMIC_SCALING" in reconcile_worker.FLAG_DEFAULTS)
    expect("FLAG_DEFAULTS has HARD_CAP_MAX",
           "RECONCILIATION_HARD_CAP_MAX" in reconcile_worker.FLAG_DEFAULTS)
    expect("FLAG_DEFAULTS has BACKLOG_ALERT_THRESHOLD",
           "RECONCILIATION_BACKLOG_ALERT_THRESHOLD" in reconcile_worker.FLAG_DEFAULTS)
    expect("default DYNAMIC_SCALING=True",
           reconcile_worker.FLAG_DEFAULTS["RECONCILIATION_DYNAMIC_SCALING"] is True)
    expect("default HARD_CAP_MAX=20",
           reconcile_worker.FLAG_DEFAULTS["RECONCILIATION_HARD_CAP_MAX"] == 20)
    expect("default BACKLOG_ALERT_THRESHOLD=50",
           reconcile_worker.FLAG_DEFAULTS["RECONCILIATION_BACKLOG_ALERT_THRESHOLD"] == 50)

    # === TEST 11: _format_alert handles backlog_high case ===
    print("\n=== test 11: _format_alert backlog_high path ===")
    counts_high = {
        "backlog_high_alert": True,
        "hard_cap_hit": False,
        "backlog_size": 60,
        "backlog_alert_threshold": 50,
        "auto_resyncs": 20,
        "hard_cap_actual": 20,
        "hard_cap_dynamic_applied": True,
        "phantoms_total": 60,
        "ghosts_total": 0,
        "alerts_only_young": 0,
    }
    msg = reconcile_worker._format_alert(counts_high, [])
    expect("BACKLOG_HIGH w komunikacie", "BACKLOG_HIGH" in msg)
    expect("60 w komunikacie", "60" in msg)
    expect("threshold≥50 w komunikacie", "≥50" in msg)

    # === TEST 12: _format_alert dynamic scale info gdy ok-path ===
    print("\n=== test 12: _format_alert dynamic scale info (ok path) ===")
    counts_scaled = {
        "backlog_high_alert": False,
        "hard_cap_hit": False,
        "backlog_size": 12,
        "auto_resyncs": 12,
        "hard_cap_actual": 12,
        "hard_cap_dynamic_applied": True,
        "phantoms_total": 12,
        "ghosts_total": 0,
        "alerts_only_young": 0,
    }
    msg = reconcile_worker._format_alert(counts_scaled, [])
    expect("F14 dynamic scale info w komunikacie", "F14" in msg)
    expect("backlog=12 w komunikacie", "backlog=12" in msg)

    # === TEST 13: hard_cap_hit alert format mentions cap_max ===
    print("\n=== test 13: _format_alert hard_cap_hit cap_max info ===")
    counts_hit = {
        "hard_cap_hit": True,
        "backlog_size": 30,
        "hard_cap_actual": 20,
        "phantoms_total": 30,
    }
    msg = reconcile_worker._format_alert(counts_hit, [])
    expect("HARD_CAP_HIT w komunikacie", "HARD_CAP_HIT" in msg)
    expect("cap_max=20 w komunikacie", "cap_max=20" in msg)

    print(f"\n=== RESULT: {results['pass']} PASS / {results['fail']} FAIL ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
