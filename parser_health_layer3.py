"""V3.28 PARSER-RESILIENCE Layer 3 — Cross-validation extension parser_health.py.

Extends Layer 2 ParserHealthMonitor z deeper semantic validation parsed lists.
Layer 2 quantity-based threshold (Δ% / count) + Layer 3 set-based asymmetry detection
= defense-in-depth (NIE duplikacja, dwa różne wymiary).

Architecture (Z3):
- Same ParserHealthMonitor singleton — extension method `cross_validate_parsed_dict(parsed)`
- Per-severity cooldown (Q2 review): CRITICAL_COOLDOWN_SEC=300, WARNING_COOLDOWN_SEC=1800
- historical_known_ids: rolling 7-day window persistent w dispatch_state/known_ids_window.json
- 4 set-based cross-checks (4 alert types nowe)
- Defense-in-depth: każdy set op try/except, corruption recovery (rebuild over 7 days)

Cross-checks:
1. SET_ASSIGNED_ORPHAN: assigned_ids - (order_ids ∪ historical_known_ids) — assigned ID
   nie pochodzi z znanego order space → parser miss order_ids side (02.05 incident pattern).
2. SET_PACKS_LEAK: ID w courier_packs - assigned_ids → kurier ma packi dla nie-assigned.
3. SET_REST_ORPHAN: rest_names.keys() - (order_ids ∪ assigned_ids) → restauracja
   przypisana do zlecenia spoza known space.
4. SET_CLOSED_ORPHAN: closed_ids - historical_known_ids → closed ID musiał kiedyś być
   known, jeśli nie jest = corruption lub state cache zniszczone.

Severity uplift logic:
- |orphan_set| > UPLIFT_THRESHOLD (default 5) → severity bumped warning→critical
- Critical alerts use shorter cooldown (5 min) — incident detection szybkie

Test deliverables:
- Extension tests 10-14 (cross-validation scenarios)
- Backward compat: tests 1-9 z Layer 2 muszą nadal pass (14/14)

Status: NIE WDROŻONE. Wymaga ACK Adriana po Gate 2.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)


KNOWN_IDS_WINDOW_PATH = Path("/root/.openclaw/workspace/dispatch_state/known_ids_window.json")
ORDERS_STATE_PATH = Path("/root/.openclaw/workspace/dispatch_state/orders_state.json")

# Window expiration (rolling 7 days)
KNOWN_IDS_WINDOW_DAYS = int(os.environ.get("PARSER_HEALTH_KNOWN_IDS_DAYS", "7"))

# Per-severity cooldown (Q2 review extension)
CRITICAL_COOLDOWN_SEC = float(os.environ.get("PARSER_HEALTH_CRITICAL_COOLDOWN_SEC", "300"))    # 5 min
WARNING_COOLDOWN_SEC = float(os.environ.get("PARSER_HEALTH_WARNING_COOLDOWN_SEC", "1800"))     # 30 min

# Severity uplift threshold (orphan set size > N → bump warning→critical)
UPLIFT_THRESHOLD = int(os.environ.get("PARSER_HEALTH_UPLIFT_THRESHOLD", "5"))


class KnownIdsWindow:
    """Rolling 7-day window of known order IDs. Persistent + restart-resilient.

    State schema:
        {
          "version": "1",
          "saved_at": "2026-05-02T13:30:00+00:00",
          "ids": {
            "470001": "2026-05-01T20:05:00+00:00",
            "470002": "2026-05-01T20:10:00+00:00",
            ...
          }
        }

    Operations:
      - add(oid_set, ts): record IDs as known at given ts
      - get_known() -> Set[str]: all IDs within window (auto-expire stale)
      - bootstrap_from_orders_state(): seed window z istniejącego state file
        (one-time call dla pierwszego deploy gdy pliku jeszcze nie ma)

    Defense-in-depth: any error → log + return safe default (empty set).
    """

    def __init__(self, state_path: Path = KNOWN_IDS_WINDOW_PATH, window_days: int = KNOWN_IDS_WINDOW_DAYS):
        self.state_path = state_path
        self.window_days = window_days
        self._ids: Dict[str, str] = {}  # id -> last_seen_iso
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            if not self.state_path.exists():
                log.info(f"KnownIdsWindow: state path {self.state_path} brak — bootstrap pending")
                return
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ids_dict = data.get("ids", {}) or {}
            if isinstance(ids_dict, dict):
                self._ids = {str(k): str(v) for k, v in ids_dict.items() if v}
            log.info(f"KnownIdsWindow: loaded {len(self._ids)} IDs z {self.state_path}")
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            log.warning(f"KnownIdsWindow._load fail (corruption?): {e}. Resetting empty.")
            self._ids = {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1",
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "ids": dict(self._ids),
            }
            tmp = self.state_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except (OSError, TypeError) as e:
            log.warning(f"KnownIdsWindow._save fail (non-blocking): {e}")

    def add(self, oid_set: Set[str], ts: Optional[str] = None) -> int:
        """Add IDs to window. Returns count newly added."""
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()
        added = 0
        with self._lock:
            for oid in oid_set:
                if not oid:
                    continue
                oid_s = str(oid)
                if oid_s not in self._ids:
                    added += 1
                self._ids[oid_s] = ts
            if added > 0:
                self._expire_inplace_unlocked()
                self._save()
        return added

    def _expire_inplace_unlocked(self) -> None:
        """Remove IDs older than window_days. Must be called within self._lock."""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.window_days)
            cutoff_iso = cutoff.isoformat()
            removed = []
            for oid, ts in list(self._ids.items()):
                # ISO comparison: 2026-05-01T...+00:00 lexicographic OK gdy ten sam offset
                # Defensive parse
                try:
                    ts_dt = datetime.fromisoformat(ts)
                    if ts_dt.tzinfo is None:
                        ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                    if ts_dt < cutoff:
                        removed.append(oid)
                except (ValueError, TypeError):
                    # Bad timestamp — drop entry
                    removed.append(oid)
            for oid in removed:
                del self._ids[oid]
            if removed:
                log.debug(f"KnownIdsWindow expire: removed {len(removed)} stale IDs")
        except Exception as e:
            log.warning(f"KnownIdsWindow._expire fail (non-blocking): {e}")

    def get_known(self) -> Set[str]:
        """Returns all IDs within window (auto-expires stale)."""
        with self._lock:
            self._expire_inplace_unlocked()
            return set(self._ids.keys())

    def bootstrap_from_orders_state(self, orders_state_path: Path = ORDERS_STATE_PATH) -> int:
        """One-time seed z orders_state.json. Returns count seeded.

        Idempotent — safe to call multiple times. New IDs get current ts.
        """
        try:
            if not orders_state_path.exists():
                log.warning(f"KnownIdsWindow bootstrap: orders_state {orders_state_path} brak")
                return 0
            with open(orders_state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                log.warning(f"KnownIdsWindow bootstrap: orders_state nie jest dict")
                return 0
            # Extract first_seen / updated_at jako timestamp
            now_iso = datetime.now(timezone.utc).isoformat()
            seeded = 0
            with self._lock:
                for oid, entry in state.items():
                    if not str(oid).isdigit() or len(str(oid)) < 5:
                        continue
                    if oid in self._ids:
                        continue
                    ts = now_iso
                    if isinstance(entry, dict):
                        ts = entry.get("first_seen") or entry.get("updated_at") or ts
                        ts = str(ts)
                    self._ids[str(oid)] = ts
                    seeded += 1
                if seeded > 0:
                    self._expire_inplace_unlocked()
                    self._save()
            log.info(f"KnownIdsWindow bootstrap: seeded {seeded} IDs z orders_state")
            return seeded
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            log.warning(f"KnownIdsWindow bootstrap fail: {e}")
            return 0


# ────────────────────────────────────────────────────────────────────────────
# Layer 3 extension methods dla ParserHealthMonitor (mixin pattern)
# Concrete attachment via monkey-patch at install time, lub alternatywa:
# inheritance ParserHealthMonitorV3 extends Layer 2 class.
# Wybór: monkey-patch (zero refactor pliku Layer 2, simpler diff).
# ────────────────────────────────────────────────────────────────────────────


def cross_validate_parsed_dict(self, parsed: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cross-check parsed lists semantic integrity.

    Args:
        parsed: dict z parse_panel_html (lub None gdy fetch failed).

    Returns:
        List of triggered alerts (each dict z type/severity/message/context).

    4 cross-checks (set-based):
      1. SET_ASSIGNED_ORPHAN: assigned_ids spoza (order_ids ∪ historical) → parser miss
      2. SET_PACKS_LEAK: courier_packs values spoza assigned_ids → kurier z phantom pack
      3. SET_REST_ORPHAN: rest_names keys spoza (order_ids ∪ assigned_ids) → orphaned mapping
      4. SET_CLOSED_ORPHAN: closed_ids spoza historical → corruption signal

    Severity uplift: |orphan_set| > UPLIFT_THRESHOLD → bump warning→critical.
    """
    if not self.enabled:
        return []
    if not parsed or not isinstance(parsed, dict):
        return []

    alerts: List[Dict[str, Any]] = []
    try:
        order_ids = set(parsed.get("order_ids") or [])
        assigned_ids = set(parsed.get("assigned_ids") or set())
        rest_names_keys = set((parsed.get("rest_names") or {}).keys())
        closed_ids = set(parsed.get("closed_ids") or set())
        courier_packs = parsed.get("courier_packs") or {}

        # Get historical known IDs (auto-expires stale)
        try:
            window = self._known_ids_window
            historical = window.get_known() if window is not None else set()
        except Exception as e:
            log.warning(f"cross_validate: known_ids_window access fail: {e}")
            historical = set()

        # Update window z current order_ids (add new, expire old)
        try:
            if window is not None and order_ids:
                window.add(order_ids)
        except Exception as e:
            log.warning(f"cross_validate: known_ids_window.add fail: {e}")

        known_space = order_ids | historical

        # CHECK 1: SET_ASSIGNED_ORPHAN
        assigned_orphan = assigned_ids - known_space
        if assigned_orphan:
            severity = "critical" if len(assigned_orphan) > UPLIFT_THRESHOLD else "warning"
            alerts.append({
                "type": "PARSER_SET_ASSIGNED_ORPHAN",
                "severity": severity,
                "message": (
                    f"Parser asymmetry: assigned_ids contains {sorted(assigned_orphan)[:5]} "
                    f"(total {len(assigned_orphan)}) NOT in order_ids∪historical. "
                    f"Possible: order_ids parser miss (incident 02.05 pattern)."
                ),
                "context": {
                    "orphan_count": len(assigned_orphan),
                    "orphan_sample": sorted(assigned_orphan)[:10],
                    "n_order_ids": len(order_ids),
                    "n_historical": len(historical),
                    "n_assigned": len(assigned_ids),
                },
            })

        # CHECK 2: SET_PACKS_LEAK
        try:
            packs_all_ids: Set[str] = set()
            for kname, zlist in courier_packs.items():
                if isinstance(zlist, (list, tuple, set)):
                    packs_all_ids.update(str(z) for z in zlist if z)
            packs_leak = packs_all_ids - assigned_ids
            if packs_leak:
                severity = "critical" if len(packs_leak) > UPLIFT_THRESHOLD else "warning"
                alerts.append({
                    "type": "PARSER_SET_PACKS_LEAK",
                    "severity": severity,
                    "message": (
                        f"Parser asymmetry: courier_packs contain {sorted(packs_leak)[:5]} "
                        f"(total {len(packs_leak)}) NOT in assigned_ids. "
                        f"Possible: assigned_ids parser miss."
                    ),
                    "context": {
                        "leak_count": len(packs_leak),
                        "leak_sample": sorted(packs_leak)[:10],
                        "n_courier_packs_ids": len(packs_all_ids),
                        "n_assigned": len(assigned_ids),
                    },
                })
        except Exception as e:
            log.warning(f"cross_validate CHECK 2 fail: {e}")

        # CHECK 3: SET_REST_ORPHAN
        rest_orphan = rest_names_keys - (order_ids | assigned_ids)
        if rest_orphan:
            severity = "critical" if len(rest_orphan) > UPLIFT_THRESHOLD else "warning"
            alerts.append({
                "type": "PARSER_SET_REST_ORPHAN",
                "severity": severity,
                "message": (
                    f"Parser asymmetry: rest_names mapped to {sorted(rest_orphan)[:5]} "
                    f"(total {len(rest_orphan)}) NOT in order_ids∪assigned_ids. "
                    f"Possible: order/assigned ids parser miss."
                ),
                "context": {
                    "orphan_count": len(rest_orphan),
                    "orphan_sample": sorted(rest_orphan)[:10],
                    "n_rest_keys": len(rest_names_keys),
                },
            })

        # CHECK 4: SET_CLOSED_ORPHAN
        # Closed = terminal status (7/8/9). Powinny były być known historically.
        # Jeśli closed_id NIE w historical → cache corruption lub fresh start.
        # Skip check gdy historical empty (bootstrap pending lub fresh deploy).
        if historical:
            closed_orphan = closed_ids - historical - order_ids
            if closed_orphan:
                severity = "critical" if len(closed_orphan) > UPLIFT_THRESHOLD else "warning"
                alerts.append({
                    "type": "PARSER_SET_CLOSED_ORPHAN",
                    "severity": severity,
                    "message": (
                        f"Parser corruption: closed_ids contains {sorted(closed_orphan)[:5]} "
                        f"(total {len(closed_orphan)}) NOT in historical_known_ids. "
                        f"Possible: state cache loss lub regression."
                    ),
                    "context": {
                        "orphan_count": len(closed_orphan),
                        "orphan_sample": sorted(closed_orphan)[:10],
                        "n_closed": len(closed_ids),
                    },
                })

    except Exception as e:
        self._error_count += 1
        log.warning(f"cross_validate_parsed_dict fail (non-blocking): {e}")
        return []

    return alerts


def _maybe_send_alert_v3(self, alert: Dict[str, Any]) -> None:
    """Override Layer 2 _maybe_send_alert z per-severity cooldown.

    Layer 2: fixed DEBOUNCE_SECONDS (30 min) all severities.
    Layer 3: WARNING=1800s, CRITICAL=300s — krytyczne alerty re-trigger szybciej.
    """
    try:
        alert_type = alert.get("type", "UNKNOWN")
        severity = alert.get("severity", "warning")
        cooldown = CRITICAL_COOLDOWN_SEC if severity == "critical" else WARNING_COOLDOWN_SEC
        now = time.time()
        last = self._last_alert_at.get(alert_type, 0.0)
        cooldown_remaining = cooldown - (now - last)
        if cooldown_remaining > 0:
            log.debug(
                f"ParserHealthMonitor alert {alert_type} ({severity}) suppressed "
                f"(cooldown {cooldown_remaining:.0f}s)"
            )
            return
        self._last_alert_at[alert_type] = now

        msg = alert.get("message", "(no message)")
        if severity == "critical":
            log.error(f"[ANOMALY {alert_type}] {msg}")
        else:
            log.warning(f"[ANOMALY {alert_type}] {msg}")

        # Telegram
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            emoji = "🚨" if severity == "critical" else "⚠️"
            tg_text = f"{emoji} V3.28 PARSER {alert_type}\n{msg}"
            ok = send_admin_alert(tg_text)
            if not ok:
                log.warning(f"ParserHealthMonitor: send_admin_alert returned False dla {alert_type}")
        except Exception as e:
            log.warning(f"ParserHealthMonitor: telegram send failed dla {alert_type}: {e}")
    except Exception as e:
        log.warning(f"ParserHealthMonitor._maybe_send_alert_v3 fail (non-blocking): {e}")


def install_layer3(monitor) -> None:
    """Attach Layer 3 methods do existing Layer 2 ParserHealthMonitor instance.

    Idempotent — safe call multiple times.
    Adds:
      - monitor.cross_validate_parsed_dict(parsed)
      - monitor._known_ids_window (KnownIdsWindow instance)
      - Override monitor._maybe_send_alert (per-severity cooldown)

    Production usage:
        from dispatch_v2.parser_health import get_monitor
        from dispatch_v2.parser_health_layer3 import install_layer3
        monitor = get_monitor()
        install_layer3(monitor)
        # monitor.cross_validate_parsed_dict() now available
    """
    import types
    if not hasattr(monitor, "_layer3_installed"):
        monitor._known_ids_window = KnownIdsWindow()
        monitor.cross_validate_parsed_dict = types.MethodType(cross_validate_parsed_dict, monitor)
        monitor._maybe_send_alert = types.MethodType(_maybe_send_alert_v3, monitor)
        monitor._layer3_installed = True
        log.info(
            f"Layer 3 cross-validation installed: "
            f"window_days={KNOWN_IDS_WINDOW_DAYS}, "
            f"critical_cooldown={CRITICAL_COOLDOWN_SEC}s, "
            f"warning_cooldown={WARNING_COOLDOWN_SEC}s, "
            f"uplift_threshold={UPLIFT_THRESHOLD}, "
            f"known_ids_loaded={len(monitor._known_ids_window.get_known())}"
        )


# Convenience: extended record_tick wrapper łączący Layer 2 + Layer 3 alerts
def record_tick_full(monitor, cycle_stats: Dict[str, Any], parsed: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combined Layer 2 + Layer 3 anomaly check w jednym call.

    NIE replaces monitor.record_tick — to jest helper dla panel_watcher integration.
    Layer 2 alerts + Layer 3 alerts → łącznie deduplicated by type, sent via _maybe_send_alert.

    Defense-in-depth: each layer wrapped, NIGDY raise.
    """
    alerts: List[Dict[str, Any]] = []
    # Layer 2 (existing)
    try:
        l2 = monitor.record_tick(cycle_stats, parsed)
        if l2:
            alerts.extend(l2)
    except Exception as e:
        log.warning(f"record_tick_full Layer 2 fail (non-blocking): {e}")
    # Layer 3 (cross-validation)
    try:
        if hasattr(monitor, "cross_validate_parsed_dict"):
            l3 = monitor.cross_validate_parsed_dict(parsed)
            if l3:
                with monitor._lock:
                    for alert in l3:
                        monitor._maybe_send_alert(alert)
                alerts.extend(l3)
    except Exception as e:
        log.warning(f"record_tick_full Layer 3 fail (non-blocking): {e}")
    return alerts
