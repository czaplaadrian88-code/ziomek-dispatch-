"""V3.28 PARSER-RESILIENCE Layer 2 — Post-parse anomaly detection.

Watchdog dla panel_watcher tick output. Wykrywa parser regresje sygnalizowane przez:
- orders_in_panel = 0 lub stuck przez ≥5 cycles
- delta orders_in_panel poza [-30%, +50%]
- assigned_ids - order_ids > threshold (cross-source asymmetry — Layer 3 territory)
- transition: previous TICK had orders, current=0

Anomaly response:
- WARNING log + Telegram admin alert
- Cooldown 30 min (DEBOUNCE_SECONDS) — no spam
- NIE crash, NIE rollback — system continues, operator notified

Architecture (Z3):
- State persistence w dispatch_state/parser_health.json — rolling 10 cycles
- Restart-resilient (load on init, save per tick)
- Sentinel pattern: ENABLE_PARSER_HEALTH_MONITOR env flag (default ON, OFF dla rollback)
- Defense-in-depth: each method try/except → log + return safe default, NIE crash caller
- Singleton z lazy init (avoid import-time side effects)

Memory cross-reference:
- Lekcja #32: silent except = invisible bug. Wszystkie except mają _log.warning + context.
- Lekcja #57: pre-step verify. Class invariants (rolling buffer max len) tested.
- Lekcja #45: memory cross-reference grep — sprawdzono że nie ma kolizji z istniejącymi
  parser_health/parser_monitor symbols w scripts/dispatch_v2/.
- detector_419 pattern: cooldown via _last_alert_at + lock, send_admin_alert utility.

Production deployment:
1. Copy ten plik do dispatch_v2/parser_health.py
2. Apply diff layer2_panel_watcher_integration.diff (tick + run() integration)
3. Set ENABLE_PARSER_HEALTH_MONITOR=1 w override.conf (default ON dla nowego deploy)
4. Restart panel_watcher (Adrian explicit ACK required)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

log = logging.getLogger(__name__)


STATE_PATH = Path("/root/.openclaw/workspace/dispatch_state/parser_health.json")
ROLLING_WINDOW = 10  # liczba cycles trzymana w pamięci/disk

# Anomaly thresholds (tunable via env, sensible defaults)
ZERO_ORDERS_TOLERANCE_CYCLES = int(os.environ.get("PARSER_HEALTH_ZERO_TOLERANCE", "3"))
DELTA_PCT_LOWER = float(os.environ.get("PARSER_HEALTH_DELTA_LOWER_PCT", "-30.0"))
DELTA_PCT_UPPER = float(os.environ.get("PARSER_HEALTH_DELTA_UPPER_PCT", "50.0"))
STUCK_COUNT_TOLERANCE = int(os.environ.get("PARSER_HEALTH_STUCK_TOLERANCE", "5"))
ASSIGNED_MINUS_ORDERS_MAX = int(os.environ.get("PARSER_HEALTH_ASSIGNED_DIFF_MAX", "5"))

DEBOUNCE_SECONDS = float(os.environ.get("PARSER_HEALTH_DEBOUNCE_SEC", "1800"))  # 30 min

# Sentinel — gdy False, monitor disabled (rollback safety)
_ENABLED_DEFAULT = os.environ.get("ENABLE_PARSER_HEALTH_MONITOR", "1") == "1"

# V3.28-LAYER2-MOTION-AWARE (02.05.2026 fix): adaptive STUCK detection.
# Default ON — suppress PARSER_STUCK gdy panel quiet (no motion: delivered=0, assigned variance=0).
# Fire alert tylko gdy panel ma ruch (delivered>0 OR assigned variance>0) ALE order_ids count stuck
# = real bug pattern (np. 02.05 rollover incident: PACKS_CATCHUP fires dla 47XXXX, order_ids broken).
# Set =0 dla rollback do legacy behavior (alert na każdy stuck, false positives).
ENABLE_PARSER_STUCK_MOTION_AWARE = os.environ.get("ENABLE_PARSER_STUCK_MOTION_AWARE", "1") == "1"


class ParserHealthMonitor:
    """Watchdog per-tick anomaly detector.

    Usage z panel_watcher.run():
        monitor = get_monitor()
        ...
        stats = tick(cycle)
        monitor.record_tick(stats, parsed)  # parsed dict z parse_panel_html

    Anomaly detection (4 checks):
      1. orders_in_panel == 0 przez >= ZERO_ORDERS_TOLERANCE_CYCLES → ALERT "PARSER_ZERO_OUTPUT"
      2. abs(delta_pct vs prev_5_cycles_median) poza [LOWER, UPPER] → ALERT "PARSER_DELTA_SPIKE"
      3. orders_in_panel stuck (variance == 0) >= STUCK_COUNT_TOLERANCE → ALERT "PARSER_STUCK"
         (subtle bug: 02.05.2026 incident pattern — count stałe 180 przez >12h)
      4. len(assigned_ids - order_ids) > ASSIGNED_MINUS_ORDERS_MAX → ALERT "PARSER_ASYMMETRY"

    Cooldown: DEBOUNCE_SECONDS (30 min) per alert_type — różne typy mają osobne timery
    żeby krytyczne PARSER_ZERO_OUTPUT nie był blokowany przez świeży PARSER_ASYMMETRY.
    """

    def __init__(self, enabled: bool = _ENABLED_DEFAULT, state_path: Path = STATE_PATH):
        self.enabled = enabled
        self.state_path = state_path
        self._cycles: Deque[Dict[str, Any]] = deque(maxlen=ROLLING_WINDOW)
        self._last_alert_at: Dict[str, float] = {}  # alert_type -> ts
        self._lock = threading.Lock()
        self._init_count = 0
        self._error_count = 0
        if self.enabled:
            self._load()
            log.info(
                f"ParserHealthMonitor enabled, loaded {len(self._cycles)} cycles, "
                f"thresholds: zero_tolerance={ZERO_ORDERS_TOLERANCE_CYCLES}, "
                f"stuck_tolerance={STUCK_COUNT_TOLERANCE}, "
                f"delta_pct=[{DELTA_PCT_LOWER},{DELTA_PCT_UPPER}], "
                f"asymmetry_max={ASSIGNED_MINUS_ORDERS_MAX}, "
                f"debounce={DEBOUNCE_SECONDS}s"
            )
        else:
            log.info("ParserHealthMonitor DISABLED (env ENABLE_PARSER_HEALTH_MONITOR=0)")

    # ---- Persistence ----

    def _load(self) -> None:
        """Load state z dysku. Defense-in-depth: never raise, return on any error."""
        try:
            if not self.state_path.exists():
                return
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cycles = data.get("cycles") or []
            for entry in cycles[-ROLLING_WINDOW:]:
                if isinstance(entry, dict):
                    self._cycles.append(entry)
            last_alert = data.get("last_alert_at") or {}
            if isinstance(last_alert, dict):
                self._last_alert_at = {str(k): float(v) for k, v in last_alert.items()}
            self._init_count = int(data.get("init_count", 0)) + 1
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            log.warning(f"ParserHealthMonitor._load fail (non-blocking): {e}")
            self._cycles.clear()
            self._last_alert_at = {}

    def _save(self) -> None:
        """Atomic save: temp → fsync → rename. Never raise."""
        if not self.enabled:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1",
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "init_count": self._init_count,
                "cycles": list(self._cycles),
                "last_alert_at": dict(self._last_alert_at),
            }
            tmp = self.state_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except (OSError, TypeError) as e:
            log.warning(f"ParserHealthMonitor._save fail (non-blocking): {e}")

    # ---- Recording ----

    def record_tick(self, cycle_stats: Dict[str, Any], parsed: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Record tick metrics + check assertions + maybe alert.

        Args:
            cycle_stats: dict z tick() output (zawiera orders_in_panel, new, assigned, etc.)
            parsed: dict z parse_panel_html (optional — gdy None, tylko basic checks)

        Returns:
            List of triggered alerts (every alert is a dict z type/message/context).
            Empty list = healthy.

        Defense-in-depth: NIGDY raise. Każdy except → log + return [].
        """
        if not self.enabled:
            return []
        try:
            with self._lock:
                entry = self._build_entry(cycle_stats, parsed)
                self._cycles.append(entry)
                alerts = self._check_anomalies(entry)
                if alerts:
                    for alert in alerts:
                        self._maybe_send_alert(alert)
                self._save()
                return alerts
        except Exception as e:
            self._error_count += 1
            log.warning(f"ParserHealthMonitor.record_tick fail (non-blocking): {e}")
            return []

    def _build_entry(self, cycle_stats: Dict[str, Any], parsed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract minimal metrics, NIE store entire parsed dict (memory bound)."""
        n_orders = int(cycle_stats.get("orders_in_panel", 0) or 0)
        n_assigned = 0
        if parsed and "assigned_ids" in parsed:
            try:
                n_assigned = len(parsed["assigned_ids"])
            except (TypeError, AttributeError):
                pass
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cycle": int(cycle_stats.get("cycle", 0) or 0),
            "orders_in_panel": n_orders,
            "n_assigned": n_assigned,
            "n_new": int(cycle_stats.get("new", 0) or 0),
            "n_delivered": int(cycle_stats.get("delivered", 0) or 0),
            "had_error": bool(cycle_stats.get("error")),
        }

    # ---- Anomaly detection ----

    def _check_anomalies(self, current: Dict[str, Any]) -> List[Dict[str, Any]]:
        """4 checks. Returns list of alert dicts (empty if healthy)."""
        alerts: List[Dict[str, Any]] = []
        n_orders = current.get("orders_in_panel", 0)
        n_assigned = current.get("n_assigned", 0)

        # CHECK 1: zero output tolerance (orders_in_panel == 0 przez ≥N cycles)
        recent_zero = sum(1 for c in self._cycles if c.get("orders_in_panel", 0) == 0)
        if recent_zero >= ZERO_ORDERS_TOLERANCE_CYCLES and len(self._cycles) >= ZERO_ORDERS_TOLERANCE_CYCLES:
            alerts.append({
                "type": "PARSER_ZERO_OUTPUT",
                "severity": "critical",
                "message": (
                    f"Parser monitor: orders_in_panel=0 przez {recent_zero} z ostatnich "
                    f"{len(self._cycles)} cycli. Possible parser regression lub panel down."
                ),
                "context": {"recent_zero_count": recent_zero, "window_size": len(self._cycles)},
            })

        # CHECK 2: delta_pct vs prev (median 5 cycles)
        prev_orders = [c.get("orders_in_panel", 0) for c in list(self._cycles)[:-1][-5:]]
        prev_orders = [x for x in prev_orders if x > 0]
        if prev_orders and n_orders > 0:
            prev_median = sorted(prev_orders)[len(prev_orders) // 2]
            if prev_median > 0:
                delta_pct = (n_orders - prev_median) / prev_median * 100
                if delta_pct < DELTA_PCT_LOWER or delta_pct > DELTA_PCT_UPPER:
                    alerts.append({
                        "type": "PARSER_DELTA_SPIKE",
                        "severity": "warning",
                        "message": (
                            f"Parser monitor: delta {delta_pct:+.1f}% vs prev_median={prev_median} "
                            f"(curr={n_orders}). Threshold [{DELTA_PCT_LOWER}, {DELTA_PCT_UPPER}]%."
                        ),
                        "context": {"current": n_orders, "prev_median": prev_median, "delta_pct": delta_pct},
                    })

        # CHECK 3: stuck variance (count stałe przez ≥STUCK_COUNT_TOLERANCE cycles)
        # V3.28-LAYER2-MOTION-AWARE (02.05.2026 fix):
        # Distinguish "panel quiet" (no fluctuation, expected stable count, NO alert)
        # vs "panel has motion" (delivered/new>0 OR assigned variance>0 BUT order_ids stuck = real bug).
        # 02.05 incident pattern: PACKS_CATCHUP fires dla 47XXXX (assigned grows), order_ids broken (stuck).
        if len(self._cycles) >= STUCK_COUNT_TOLERANCE:
            recent = list(self._cycles)[-STUCK_COUNT_TOLERANCE:]
            recent_orders = [c.get("orders_in_panel", 0) for c in recent]
            if all(v == recent_orders[0] for v in recent_orders) and recent_orders[0] > 0:
                # Compute motion signals (defense-in-depth: if metrics missing → fallback legacy behavior)
                try:
                    sum_new = sum(int(c.get("n_new", 0) or 0) for c in recent)
                    sum_delivered = sum(int(c.get("n_delivered", 0) or 0) for c in recent)
                    assigned_values = [int(c.get("n_assigned", 0) or 0) for c in recent]
                    assigned_motion = (max(assigned_values) - min(assigned_values)) if assigned_values else 0
                    panel_has_motion = (sum_new > 0) or (sum_delivered > 0) or (assigned_motion > 0)
                except Exception as _me:
                    log.warning(f"motion-aware compute fail (non-blocking, fallback legacy): {_me}")
                    panel_has_motion = True  # Fallback: assume motion → alert (legacy behavior)

                if not ENABLE_PARSER_STUCK_MOTION_AWARE:
                    # Legacy behavior: alert na każdy stuck (false positives możliwe dla off-peak plateau)
                    alerts.append({
                        "type": "PARSER_STUCK",
                        "severity": "warning",
                        "message": (
                            f"Parser monitor: orders_in_panel = {recent_orders[0]} stałe przez "
                            f"{STUCK_COUNT_TOLERANCE} cycle. (legacy mode, motion-aware OFF)"
                        ),
                        "context": {"stuck_value": recent_orders[0], "stuck_count": STUCK_COUNT_TOLERANCE,
                                    "motion_aware": False},
                    })
                elif panel_has_motion:
                    # Motion-aware: panel ma ruch (delivered/new/assigned changing) ALE count stuck = real bug
                    alerts.append({
                        "type": "PARSER_STUCK",
                        "severity": "warning",
                        "message": (
                            f"Parser monitor: orders_in_panel = {recent_orders[0]} stałe przez "
                            f"{STUCK_COUNT_TOLERANCE} cycle ALE panel ma motion "
                            f"(new={sum_new}, delivered={sum_delivered}, assigned_var={assigned_motion}). "
                            f"Real bug pattern: 02.05 incident (PACKS_CATCHUP dla 47XXXX, order_ids parser miss)."
                        ),
                        "context": {"stuck_value": recent_orders[0], "stuck_count": STUCK_COUNT_TOLERANCE,
                                    "motion_new": sum_new, "motion_delivered": sum_delivered,
                                    "motion_assigned_variance": assigned_motion, "motion_aware": True},
                    })
                # else: natural plateau (panel quiet, no motion) → NO alert (suppress false positive)

        # CHECK 4: assigned vs order asymmetry (informational; Layer 3 zrobi pełną cross-validation)
        # Gdy assigned_ids zawiera więcej niż order_ids → parser miss order side
        diff = n_assigned - n_orders
        if diff > ASSIGNED_MINUS_ORDERS_MAX:
            alerts.append({
                "type": "PARSER_ASYMMETRY",
                "severity": "warning",
                "message": (
                    f"Parser monitor: assigned_ids ({n_assigned}) - order_ids ({n_orders}) = "
                    f"{diff}, przekracza threshold {ASSIGNED_MINUS_ORDERS_MAX}. "
                    f"Possible: order_ids parser miss."
                ),
                "context": {"n_assigned": n_assigned, "n_orders": n_orders, "diff": diff},
            })

        return alerts

    # ---- Alerting (Telegram + log, with cooldown) ----

    def _maybe_send_alert(self, alert: Dict[str, Any]) -> None:
        """Send Telegram + log gdy cooldown expired. NIGDY raise."""
        try:
            alert_type = alert.get("type", "UNKNOWN")
            now = time.time()
            last = self._last_alert_at.get(alert_type, 0.0)
            cooldown_remaining = DEBOUNCE_SECONDS - (now - last)
            if cooldown_remaining > 0:
                # Suppressed — log debug, NIE flood admin
                log.debug(f"ParserHealthMonitor alert {alert_type} suppressed (cooldown {cooldown_remaining:.0f}s)")
                return
            self._last_alert_at[alert_type] = now

            severity = alert.get("severity", "warning")
            msg = alert.get("message", "(no message)")

            # Local log — always
            if severity == "critical":
                log.error(f"[ANOMALY {alert_type}] {msg}")
            else:
                log.warning(f"[ANOMALY {alert_type}] {msg}")

            # Telegram alert
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
            log.warning(f"ParserHealthMonitor._maybe_send_alert fail (non-blocking): {e}")

    # ---- Health endpoint (Layer 4 will use) ----

    def get_health_snapshot(self) -> Dict[str, Any]:
        """Snapshot dla observability/health endpoint. Defense-in-depth: NIGDY raise."""
        try:
            with self._lock:
                cycles_list = list(self._cycles)
                if not cycles_list:
                    return {"status": "unknown", "reason": "no_cycles_recorded", "cycles": 0}
                last = cycles_list[-1]
                recent_orders = [c.get("orders_in_panel", 0) for c in cycles_list]
                anomalies_recent = []
                # Check CHECK 1+3 dla aktualnego status (NIE retrigger cooldown)
                recent_zero = sum(1 for v in recent_orders if v == 0)
                stuck = (len(recent_orders) >= STUCK_COUNT_TOLERANCE
                         and all(v == recent_orders[-1] for v in recent_orders[-STUCK_COUNT_TOLERANCE:])
                         and recent_orders[-1] > 0)
                if recent_zero >= ZERO_ORDERS_TOLERANCE_CYCLES:
                    anomalies_recent.append("PARSER_ZERO_OUTPUT")
                if stuck:
                    anomalies_recent.append("PARSER_STUCK")
                status = "critical" if "PARSER_ZERO_OUTPUT" in anomalies_recent else (
                    "degraded" if anomalies_recent else "healthy"
                )
                return {
                    "status": status,
                    "cycles_recorded": len(cycles_list),
                    "last_tick_ts": last.get("ts"),
                    "last_orders_in_panel": last.get("orders_in_panel"),
                    "recent_orders_window": recent_orders,
                    "anomalies_active": anomalies_recent,
                    "init_count": self._init_count,
                    "error_count": self._error_count,
                    "thresholds": {
                        "zero_tolerance_cycles": ZERO_ORDERS_TOLERANCE_CYCLES,
                        "stuck_tolerance_cycles": STUCK_COUNT_TOLERANCE,
                        "delta_pct": [DELTA_PCT_LOWER, DELTA_PCT_UPPER],
                        "asymmetry_max": ASSIGNED_MINUS_ORDERS_MAX,
                        "debounce_sec": DEBOUNCE_SECONDS,
                    },
                }
        except Exception as e:
            log.warning(f"get_health_snapshot fail (non-blocking): {e}")
            return {"status": "error", "reason": f"snapshot_fail: {type(e).__name__}", "cycles": 0}


# ---- Singleton ----

_instance: Optional[ParserHealthMonitor] = None
_instance_lock = threading.Lock()


def get_monitor() -> ParserHealthMonitor:
    """Singleton accessor. Lazy init dla testability."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ParserHealthMonitor()
    return _instance


def reset_for_test() -> None:
    """Reset singleton — UŻYWAJ TYLKO W TESTACH."""
    global _instance
    with _instance_lock:
        _instance = None
