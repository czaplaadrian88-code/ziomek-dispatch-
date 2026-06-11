"""czasowka_state_cleanup — oneshot sprzątania czasowka_proposals_state.json.

SP-B2-LOGROT (2026-06-11), krok 2+3 sprintu:
  - czasowka_proactive.state.cleanup_stale() istniał od 2026-05-05, ale NIE był
    nigdzie wpięty → state rósł bez ograniczeń (315 orderów od 05.05, w tym
    odbiory sprzed miesiąca).
  - Testowe oidy 500000/500001 ("Test Czasowka Restaurant", wyciek pytest
    2026-05-06 sprzed izolacji conftest) — usuwane twardym filtrem
    oid >= TEST_OID_MIN niezależnie od stale-kryteriów.

Celowo OSOBNY oneshot (cron), nie hak w czasowka_scheduler: sesja B nie dotyka
modułów silnika; cron daily wystarcza (kryteria stale = 1-4h, dzienny tick
utrzymuje state w setkach bajtów). Locking współdzielony z żywymi pisarzami
(locked_write_proposals_state = fcntl LOCK_EX na .lock) → race-safe.

Użycie:
  python3 -m dispatch_v2.tools.czasowka_state_cleanup [--dry-run]

Cron (CRON_TZ=Europe/Warsaw): 45 4 * * *  (po courier_reliability 04:30).
Testy: dispatch_v2/tests/test_b2_logrot_consumers.py.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from dispatch_v2.common import setup_logger
from dispatch_v2.czasowka_proactive import state as cp_state

# Oidy >= 500000 to syntetyki testowe (prawdziwe oidy panelu są 6-cyfrowe
# w zakresie ~4xxxxx w 2026; bufor rzędu wielkości).
TEST_OID_MIN = 500000

_log = setup_logger(
    "czasowka_state_cleanup",
    "/root/.openclaw/workspace/scripts/logs/czasowka_proactive.log",
)


def _purge_test_oids(state: dict) -> int:
    """Usuń wpisy o oid >= TEST_OID_MIN (syntetyki testowe). Zwraca liczbę."""
    orders = state.get("orders")
    if not isinstance(orders, dict):
        return 0
    to_remove = []
    for oid in list(orders):
        try:
            if int(oid) >= TEST_OID_MIN:
                to_remove.append(oid)
        except (TypeError, ValueError):
            continue
    for oid in to_remove:
        del orders[oid]
    return len(to_remove)


def run(dry_run: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    if dry_run:
        state = cp_state.read_proposals_state()
        before = len(state.get("orders") or {})
        stale = cp_state.cleanup_stale(state, now)
        test_oids = _purge_test_oids(state)
        return {"before": before, "stale": stale, "test_oids": test_oids,
                "after": len(state.get("orders") or {}), "dry_run": True}

    with cp_state.locked_write_proposals_state() as state:
        before = len(state.get("orders") or {})
        stale = cp_state.cleanup_stale(state, now)
        test_oids = _purge_test_oids(state)
        after = len(state.get("orders") or {})
    return {"before": before, "stale": stale, "test_oids": test_oids,
            "after": after, "dry_run": False}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dry-run", action="store_true", help="bez zapisu")
    args = p.parse_args(argv)
    stats = run(dry_run=args.dry_run)
    msg = (
        f"cleanup proposals_state: before={stats['before']} "
        f"stale_removed={stats['stale']} test_oids_removed={stats['test_oids']} "
        f"after={stats['after']}{' (dry-run)' if stats['dry_run'] else ''}"
    )
    _log.info(msg)
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
