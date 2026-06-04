"""STATE-RMW-02 (audyt 2026-06-03) — nocny prune terminalnych zleceń z orders_state.json.

Usuwa zlecenia TERMINALNE (delivered/cancelled/returned_to_pool) starsze niż
retention wg `updated_at`. Patrz `state_machine.prune_terminal_orders` (cała
logika bezpieczeństwa: współdzielony lock, sanity-guard, atomic write).

Bramki (double-gated, oba w flags.json, hot-reload):
    ENABLE_ORDERS_STATE_PRUNE   (default False) — gdy False: no-op, exit 0
    ORDERS_STATE_PRUNE_DRY_RUN  (default True)   — gdy True: tylko liczy, nie zapisuje
    ORDERS_STATE_PRUNE_RETENTION_HOURS (default 12)

Uruchamiany przez systemd dispatch-orders-state-prune.timer (daily 03:30 UTC,
off-peak, PO snapshocie 03:00). Wzór: event_bus_cleanup.py.

CLI (manual):
    python -m dispatch_v2.prune_orders_state              # honoruje flagi (cron path)
    python -m dispatch_v2.prune_orders_state --dry-run    # wymuś dry-run niezależnie od flag
    python -m dispatch_v2.prune_orders_state --execute    # wymuś realny prune (ACK off-peak)
    python -m dispatch_v2.prune_orders_state --retention-hours 24

Exit codes: 0 = ok (też gdy disabled / nic do usunięcia), 1 = błąd (sanity abort / read error).
"""
import argparse
import json
import sys

from dispatch_v2 import state_machine
from dispatch_v2.common import flag, setup_logger

_log = setup_logger(
    "prune_orders_state",
    "/root/.openclaw/workspace/scripts/logs/prune_orders_state.log",
)


def run(dry_run_override=None, retention_override=None) -> int:
    enabled = flag("ENABLE_ORDERS_STATE_PRUNE", False)
    retention = (
        retention_override
        if retention_override is not None
        else flag("ORDERS_STATE_PRUNE_RETENTION_HOURS", 12)
    )
    if dry_run_override is not None:
        dry_run = dry_run_override
    else:
        # Cron path: domyślnie dry-run dopóki Adrian świadomie nie flipnie flagi.
        dry_run = flag("ORDERS_STATE_PRUNE_DRY_RUN", True)

    if not enabled and dry_run_override is None:
        # Cron path z wyłączoną flagą = czysty no-op (nie czytamy nawet stanu).
        _log.info("prune_orders_state: ENABLE_ORDERS_STATE_PRUNE=False — no-op (exit 0)")
        print(json.dumps({"ok": True, "skipped": "disabled"}, ensure_ascii=False))
        return 0

    try:
        report = state_machine.prune_terminal_orders(
            retention_hours=float(retention), dry_run=bool(dry_run)
        )
    except Exception as e:  # noqa: BLE001 — chcemy exit!=0 + alert (sanity abort / read error)
        _log.error(f"prune_orders_state: ABORT — {type(e).__name__}: {e}")
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="prune_orders_state",
        description="STATE-RMW-02 prune terminalnych zleceń z orders_state.json",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="Wymuś dry-run (tylko licz, nie zapisuj)")
    g.add_argument("--execute", action="store_true", help="Wymuś realny prune (ACK off-peak)")
    p.add_argument("--retention-hours", type=float, default=None, help="Override retention (godziny)")
    args = p.parse_args(argv)

    dry_override = None
    if args.dry_run:
        dry_override = True
    elif args.execute:
        dry_override = False
    return run(dry_run_override=dry_override, retention_override=args.retention_hours)


if __name__ == "__main__":
    sys.exit(main())
