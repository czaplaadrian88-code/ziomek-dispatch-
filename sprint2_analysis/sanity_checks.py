#!/usr/bin/env python3
"""SCRIPT 6 — Pre-flight sanity checks before evening run."""
import os
import shutil
import sqlite3
from datetime import timedelta
from _common import (LEARNING_LOG, EVENTS_DB, SPRINT1_DEPLOY_UTC,
                     load_entries, now_utc, fmt_warsaw)


def check(label, ok, detail=""):
    icon = "✅" if ok else "❌"
    print(f"  {icon} {label}{(' — ' + detail) if detail else ''}")
    return ok


def main():
    print("=== SANITY CHECKS ===")
    end = now_utc()

    all_ok = True
    warnings = 0

    # 1. learning_log exists, recent mtime
    if LEARNING_LOG.exists():
        mtime = LEARNING_LOG.stat().st_mtime
        age_min = (end.timestamp() - mtime) / 60.0
        ok = age_min < 30
        all_ok &= check(f"learning_log present (mtime {age_min:.1f} min ago)", ok,
                         "stale > 30 min" if not ok else "")
        if not ok:
            warnings += 1
    else:
        all_ok &= check("learning_log present", False, "missing")

    # 2. events.db readable (Opcja C: rozdzielone tabele queue + audit_log)
    try:
        with sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True, timeout=2) as con:
            events_n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            try:
                audit_n = con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            except sqlite3.OperationalError:
                audit_n = "n/a (table missing)"
            check("events.db readable", True, f"events={events_n} audit_log={audit_n}")
    except Exception as e:
        all_ok &= check("events.db readable", False, str(e)[:80])

    # 3. Sprint 1 entries with pool_total_count
    entries = list(load_entries(since_utc=SPRINT1_DEPLOY_UTC, until_utc=end))
    sprint1_fields = sum(1 for e in entries
                          if "pool_total_count" in (e.get("decision") or {}))
    ok = sprint1_fields >= 5
    all_ok &= check(f"Sprint 1 logging (pool_total_count) present in {sprint1_fields} entries since 11:05",
                     ok, "<5 entries" if not ok else "")

    # 4. PANEL_OVERRIDE threshold ≥30
    overrides = sum(1 for e in entries if e.get("action") == "PANEL_OVERRIDE")
    if overrides >= 30:
        check(f"PANEL_OVERRIDE count = {overrides}", True, "GREEN")
    elif overrides >= 15:
        check(f"PANEL_OVERRIDE count = {overrides}", True, "YELLOW (target ≥30)")
        warnings += 1
    else:
        check(f"PANEL_OVERRIDE count = {overrides}", False, "RED (<15)")
        all_ok = False

    # 5. Time range
    check(f"Time window: {fmt_warsaw(SPRINT1_DEPLOY_UTC)} → {fmt_warsaw(end)}", True)

    # 6. Disk space
    free_mb = shutil.disk_usage(LEARNING_LOG.parent).free / (1024 * 1024)
    ok = free_mb > 200
    all_ok &= check(f"Disk free: {free_mb:.0f} MB", ok, "<200 MB" if not ok else "")

    # 7. Lock check (rough: try to open append)
    try:
        f = open(LEARNING_LOG, "a")
        f.close()
        check("learning_log not locked", True)
    except Exception as e:
        all_ok &= check("learning_log not locked", False, str(e)[:80])

    print()
    if all_ok and warnings == 0:
        status = "GREEN — ready to run scripts 1-5"
    elif all_ok:
        status = f"YELLOW — {warnings} warning(s); run with caveats"
    else:
        status = "RED — remediate above before running scripts"
    print(f"STATUS: {status}")

    if not all_ok:
        print("\nRemediation steps:")
        print("  - if learning_log missing: check panel-watcher service active")
        print("  - if PANEL_OVERRIDE <15: postpone Sprint 2 to next peak window")
        print("  - if events.db unreadable: confirm dispatch-shadow not in vacuum")


if __name__ == "__main__":
    main()
