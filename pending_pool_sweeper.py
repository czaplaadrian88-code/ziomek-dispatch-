"""pending_pool_sweeper — Faza 0: reconciliation + obserwacja puli pending.

Wywoływany przez systemd timer `dispatch-pending-pool` co 1 min. Faza 0 = czysta
obserwacja, ZERO wpływu na dispatch:
  • reconciliation — zlecenie przypisane/odebrane/dostarczone/anulowane/wrócone
    do puli w panelu (status z `state_machine`) → `remove_order` z powodem
  • status `planned` — zlecenie WCIĄŻ legalnie czeka w panelu (np. czasówka,
    której realny odbiór jest później niż nominalny `pickup_ready`). Pula to
    wiernie odzwierciedla — NIE usuwamy, tylko obserwujemy freeze-crossing.
  • obserwacja — log `freeze_cross` gdy zlecenie przekracza `freeze_at`
    (Faza 0: NIC nie emituje — freezing dopiero Faza 2)
  • stuck-guard — SIEROTA: zlecenie nieobecne w `state_machine` (realny rozjazd
    pula↔panel) a wciąż w puli >STUCK_AFTER_MIN po `pickup_ready` → log `stuck`
    + `remove_order` (sygnał Gate 0: reconciliation coś przepuścił). Zlecenia
    `planned` NIGDY nie są stuck — pula je poprawnie odzwierciedla.

Flaga `ENABLE_PENDING_POOL` — False → no-op early return.
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from datetime import datetime, timedelta, timezone  # noqa: E402

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import pending_pool, state_machine  # noqa: E402

_log = C.setup_logger(
    "pending_pool_sweeper",
    "/root/.openclaw/workspace/scripts/logs/pending_pool_sweeper.log",
)

STUCK_AFTER_MIN = 45           # po tylu min od pickup_ready bez removal → stuck
FREEZE_LOG_WINDOW_MIN = 1.5    # log freeze_cross raz — w oknie jednego sweepu po przekroczeniu

# status state_machine → powód usunięcia z puli (zlecenie opuściło stan pending).
# returned_to_pool = tear-down / anulowanie przez koordynatora (panel ignoruje
# status 9, więc skasowane zlecenia wracają jako returned_to_pool, nie cancelled).
RESOLVED_STATUSES = {
    "assigned": "assigned_in_panel",
    "picked_up": "picked_up",
    "delivered": "delivered",
    "cancelled": "cancelled",
    "returned_to_pool": "returned_to_pool",
}


def _parse(iso):
    """ISO str → tz-aware UTC datetime; None gdy puste/niepoprawne."""
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def sweep(now: datetime = None) -> dict:
    """Jeden przebieg reconciliation + obserwacji. Zwraca licznik akcji (telemetria)."""
    if now is None:
        now = datetime.now(timezone.utc)
    counts = {"active": 0, "removed": {}, "freeze_cross": 0, "stuck": 0}
    active = pending_pool.get_active()
    counts["active"] = len(active)
    for entry in active:
        oid = entry.get("order_id")
        if not oid:
            continue

        # 1. reconciliation — zlecenie opuściło stan pending (panel je przejął/zamknął)
        st = state_machine.get_order(str(oid))
        reason = RESOLVED_STATUSES.get((st or {}).get("status"))
        if reason:
            pending_pool.remove_order(oid, reason)
            counts["removed"][reason] = counts["removed"].get(reason, 0) + 1
            continue

        # 2. stuck-guard — TYLKO sieroty: zlecenie nieobecne w state_machine
        #    (realny rozjazd pula↔panel) a dawno po pickup wciąż w puli. Zlecenie
        #    `planned` jest WCIĄŻ legalnie pending (czasówka odbierana później niż
        #    nominalny pickup_ready) → pula je poprawnie trzyma, NIE jest stuck.
        if st is None:
            pr = _parse(entry.get("pickup_ready_at"))
            if pr is not None and now > pr + timedelta(minutes=STUCK_AFTER_MIN):
                pending_pool.log_event("stuck", oid, {
                    "pickup_ready_at": entry.get("pickup_ready_at"),
                    "age_min": round((now - pr).total_seconds() / 60.0, 1),
                })
                pending_pool.remove_order(oid, "stuck")
                counts["stuck"] += 1
                continue

        # 3. obserwacja — log freeze-crossing raz (Faza 0: NIC nie emituje)
        fz = _parse(entry.get("freeze_at"))
        if fz is not None and fz <= now < fz + timedelta(minutes=FREEZE_LOG_WINDOW_MIN):
            pending_pool.log_event("freeze_cross", oid, {"freeze_at": entry.get("freeze_at")})
            counts["freeze_cross"] += 1

    return counts


def main() -> int:
    if not C.ENABLE_PENDING_POOL:
        return 0
    try:
        counts = sweep()
        _log.info(f"sweep done: {counts}")
    except Exception as e:
        _log.error(f"sweep failed: {type(e).__name__}: {e}", exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
