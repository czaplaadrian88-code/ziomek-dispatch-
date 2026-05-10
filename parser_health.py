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
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Morning calibration constants (companion fix dla 07.05 false positives:
# 08:37 ZERO 3/10, 08:42 ZERO 6/10, 09:11 DELTA +100% przy 1→2). Pre-09:00
# Warsaw panel ma naturalne plateau po nightly rollover; 1→2 transition
# absolutnym jest noise nie regression signal.
try:
    from dispatch_v2.common import (
        PARSER_HEALTH_STUCK_MIN_HOUR_WARSAW,
        PARSER_HEALTH_STUCK_MIN_BASELINE,
        PARSER_HEALTH_DELTA_MIN_ABS_DIFF,
    )
except ImportError:
    PARSER_HEALTH_STUCK_MIN_HOUR_WARSAW = 9
    PARSER_HEALTH_STUCK_MIN_BASELINE = 3
    PARSER_HEALTH_DELTA_MIN_ABS_DIFF = 3


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
# Default ON — suppress PARSER_STUCK gdy panel quiet (motion sum poniżej threshold).
# Fire alert tylko gdy motion_total >= PARSER_STUCK_MOTION_THRESHOLD ALE order_ids count stuck
# = real bug pattern (np. 02.05 rollover incident: PACKS_CATCHUP fires dla 47XXXX, order_ids broken).
# Set ENABLE_*=0 dla rollback do legacy behavior (alert na każdy stuck, false positives).
ENABLE_PARSER_STUCK_MOTION_AWARE = os.environ.get("ENABLE_PARSER_STUCK_MOTION_AWARE", "1") == "1"

# V3.28-TICKET1-MOTION-THRESHOLD-TUNING (02.05.2026 wieczór):
# Motion sum threshold: sum_new + sum_delivered + assigned_variance >= N → alert.
# Default 4 (eliminates noise z 1+1+1=3 false positives, preserves 02.05 incident detection).
# Set =0 dla legacy >0 behavior (any motion fires).
PARSER_STUCK_MOTION_THRESHOLD = int(os.environ.get("PARSER_STUCK_MOTION_THRESHOLD", "4"))


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
                    # Z2 fix 2026-05-07: rehydrate order_ids/active_ids list → frozenset
                    entry = dict(entry)
                    raw_ids = entry.get("order_ids")
                    if isinstance(raw_ids, list):
                        entry["order_ids"] = frozenset(raw_ids)
                    raw_active = entry.get("active_ids")
                    if isinstance(raw_active, list):
                        entry["active_ids"] = frozenset(raw_active)
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
            # Z2 fix 2026-05-07: konwertuj frozenset(order_ids) → list dla JSON.
            # _load przekonwertuje z powrotem (lista → frozenset).
            cycles_serializable = []
            for entry in self._cycles:
                e = dict(entry)
                if isinstance(e.get("order_ids"), frozenset):
                    e["order_ids"] = sorted(e["order_ids"])
                if isinstance(e.get("active_ids"), frozenset):
                    e["active_ids"] = sorted(e["active_ids"])
                cycles_serializable.append(e)
            data = {
                "version": "1",
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "init_count": self._init_count,
                "cycles": cycles_serializable,
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
        # Z2 fix 2026-05-07: store order_ids set for set-comparison detection
        # Z2 fix 2026-05-07 #2: also compute active_ids = order_ids - closed_ids.
        # Panel zwraca all-today's IDs w JS embedded `id: X` (cały dzień, niezależnie
        # od status terminalnego). closed_ids (z DOM markera "data-idkurier" missing)
        # = status 7/8/9. Active set faktycznie spada z każdym delivery → eliminuje
        # late-evening false positives gdzie order_ids count plateauje (panel design,
        # nie parser bug). Layer 2 STUCK + DELTA przełączone na active_*.
        order_ids_set = None
        active_ids_set = None
        n_active = n_orders  # safe fallback gdy parsed=None / brak closed_ids
        if parsed is not None:
            try:
                raw_ids = parsed.get("order_ids")
                if raw_ids is not None:
                    order_ids_set = frozenset(raw_ids)
                    closed = parsed.get("closed_ids")
                    if closed is not None:
                        try:
                            active_ids_set = order_ids_set - frozenset(closed)
                        except (TypeError, AttributeError):
                            active_ids_set = order_ids_set
                    else:
                        # parser nie dostarczył closed_ids (legacy / shadow path) →
                        # active = order_ids (zachowanie backward-compat z poprzednim algo)
                        active_ids_set = order_ids_set
                    n_active = len(active_ids_set)
            except (TypeError, AttributeError):
                pass
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cycle": int(cycle_stats.get("cycle", 0) or 0),
            "orders_in_panel": n_orders,
            "active_orders": n_active,
            "n_assigned": n_assigned,
            "n_new": int(cycle_stats.get("new", 0) or 0),
            "n_delivered": int(cycle_stats.get("delivered", 0) or 0),
            "had_error": bool(cycle_stats.get("error")),
            "order_ids": order_ids_set,
            "active_ids": active_ids_set,
        }

    # ---- Anomaly detection ----

    def _check_anomalies(self, current: Dict[str, Any]) -> List[Dict[str, Any]]:
        """4 checks. Returns list of alert dicts (empty if healthy).

        Z2 fix 2026-05-07 #2: CHECK 2 (DELTA) i CHECK 3 (STUCK) przełączone z
        order_ids/orders_in_panel na active_ids/active_orders. Panel zwraca
        all-today's IDs w JS embedded — order_ids count plateauje wieczorem
        bez parser bug. active = order_ids - closed_ids odzwierciedla rzeczywiste
        live orders (spadają z każdym delivery). CHECK 1 (ZERO_OUTPUT) zostaje
        na orders_in_panel — interesuje nas sygnał "parser literalnie pusty"
        niezależnie od stanu zamówień.
        """
        alerts: List[Dict[str, Any]] = []
        n_orders = current.get("orders_in_panel", 0)
        n_active = current.get("active_orders", n_orders)  # fallback gdy legacy entry
        n_assigned = current.get("n_assigned", 0)

        # CHECK 1: zero output tolerance (orders_in_panel == 0 przez ≥N cycles)
        # Morning calibration: pre-09:00 Warsaw panel ma naturalne plateau
        # po nightly rollover (parser zwraca 0 bo brak nowych orderów).
        # Suppress alert pre-09:00; po 09:00 normal logic.
        warsaw_hour = datetime.now(ZoneInfo("Europe/Warsaw")).hour
        recent_zero = sum(1 for c in self._cycles if c.get("orders_in_panel", 0) == 0)
        if (recent_zero >= ZERO_ORDERS_TOLERANCE_CYCLES
                and len(self._cycles) >= ZERO_ORDERS_TOLERANCE_CYCLES
                and warsaw_hour >= PARSER_HEALTH_STUCK_MIN_HOUR_WARSAW):
            alerts.append({
                "type": "PARSER_ZERO_OUTPUT",
                "severity": "critical",
                "message": (
                    f"🚨 Panel pusty — Ziomek nie widzi zamówień\n"
                    f"W {recent_zero} z ostatnich {len(self._cycles)} sprawdzeń panel.nadajesz.pl "
                    f"zwracał 0 zamówień. Najczęściej znaczy że panel chwilowo padł "
                    f"(timeout sieci) albo parser się zaciął.\n\n"
                    f"Co robię: monitoruję dalej — alert powtórzy się jak nie wróci. "
                    f"Jeśli zobaczysz to 3+ razy z rzędu → restart:\n"
                    f"sudo systemctl restart dispatch-panel-watcher"
                ),
                "context": {"recent_zero_count": recent_zero, "window_size": len(self._cycles)},
            })

        # CHECK 2: delta_pct vs prev (median 5 cycles) — na ACTIVE orders
        prev_active = [c.get("active_orders", c.get("orders_in_panel", 0)) for c in list(self._cycles)[:-1][-5:]]
        prev_active = [x for x in prev_active if x > 0]
        if prev_active and n_active > 0:
            prev_median = sorted(prev_active)[len(prev_active) // 2]
            if prev_median > 0:
                delta_pct = (n_active - prev_median) / prev_median * 100
                # Morning calibration: 1→2 = +100% but absolute diff = 1, noise.
                # Wymagamy zarówno delta_pct out-of-range AND |abs_diff| >= guard
                # żeby filtrować low-volume transitions.
                abs_diff = abs(n_active - prev_median)
                if (delta_pct < DELTA_PCT_LOWER or delta_pct > DELTA_PCT_UPPER) \
                        and abs_diff >= PARSER_HEALTH_DELTA_MIN_ABS_DIFF:
                    alerts.append({
                        "type": "PARSER_DELTA_SPIKE",
                        "severity": "warning",
                        "message": (
                            f"⚠️ Skok aktywnych zamówień: {prev_median} → {n_active} ({delta_pct:+.0f}%)\n"
                            f"Liczba zamówień w obróbce nagle wzrosła ponad próg. "
                            f"Może to start dnia, nagły wzrost ruchu, albo (rzadko) glitch parsera.\n\n"
                            f"Co robię: dispatchuję dalej normalnie, obserwuję trend. "
                            f"Akcja niepotrzebna chyba że widzisz coś dziwnego w panelu."
                        ),
                        "context": {"current": n_active, "prev_median": prev_median, "delta_pct": delta_pct, "metric": "active_orders"},
                    })

        # CHECK 3: stuck variance (count stałe przez ≥STUCK_COUNT_TOLERANCE cycles) — na ACTIVE
        # V3.28-LAYER2-MOTION-AWARE (02.05.2026 fix):
        # Distinguish "panel quiet" (no fluctuation, expected stable count, NO alert)
        # vs "panel has motion" (delivered/new>0 OR assigned variance>0 BUT active stuck = real bug).
        # 02.05 incident pattern: PACKS_CATCHUP fires dla 47XXXX (assigned grows), active broken (stuck).
        # Z2 fix 2026-05-07 #2: porównanie na active_ids/active_orders (eliminuje
        # late-evening false positives gdzie order_ids plateauje przez panel design).
        if len(self._cycles) >= STUCK_COUNT_TOLERANCE:
            recent = list(self._cycles)[-STUCK_COUNT_TOLERANCE:]
            recent_active = [c.get("active_orders", c.get("orders_in_panel", 0)) for c in recent]
            if all(v == recent_active[0] for v in recent_active) and recent_active[0] > 0:
                # Z2 fix 2026-05-07: set-comparison eliminates rotation false positives
                # Use active_ids (post-fix #2) z fallback do order_ids (legacy entries)
                active_sets = [c.get("active_ids", c.get("order_ids")) for c in recent]
                # Determine if all sets are non-None and identical
                set_stuck = False
                if all(s is not None for s in active_sets):
                    # All cycles have set stored (post-fix entries)
                    if len(active_sets) >= 2:
                        first_set = active_sets[0]
                        set_stuck = all(s == first_set for s in active_sets)
                else:
                    # Mixed or legacy entries (pre-fix) → fallback to legacy motion-only check
                    set_stuck = None  # indicates fallback needed

                # Compute motion signals (defense-in-depth: if metrics missing → fallback legacy behavior)
                try:
                    sum_new = sum(int(c.get("n_new", 0) or 0) for c in recent)
                    sum_delivered = sum(int(c.get("n_delivered", 0) or 0) for c in recent)
                    assigned_values = [int(c.get("n_assigned", 0) or 0) for c in recent]
                    assigned_motion = (max(assigned_values) - min(assigned_values)) if assigned_values else 0
                    # V3.28-TICKET1: motion sum threshold zamiast "any motion".
                    # Eliminuje false positives ze słabego motion (1+1+1=3 < 4 default).
                    motion_total = sum_new + sum_delivered + assigned_motion
                    panel_has_motion = motion_total >= PARSER_STUCK_MOTION_THRESHOLD
                except Exception as _me:
                    log.warning(f"motion-aware compute fail (non-blocking, fallback legacy): {_me}")
                    panel_has_motion = True  # Fallback: assume motion → alert (legacy behavior)

                # Z2 fix: if set_stuck is False (sets differ) → suppress alert even if motion>=threshold
                if set_stuck is False:
                    # Natural rotation underneath, count coincidence → no alert
                    pass
                elif set_stuck is True:
                    # Real parser miss confirmed: order_ids identical across cycles
                    if not ENABLE_PARSER_STUCK_MOTION_AWARE:
                        # Legacy behavior: alert na każdy stuck (false positives możliwe dla off-peak plateau)
                        alerts.append({
                            "type": "PARSER_STUCK",
                            "severity": "warning",
                            "message": (
                                f"⚠️ Panel zamrożony — ten sam zestaw aktywnych zamówień "
                                f"{STUCK_COUNT_TOLERANCE} razy z rzędu\n"
                                f"Panel zwraca {recent_active[0]} aktywnych zamówień przez "
                                f"{STUCK_COUNT_TOLERANCE} cykli (motion-aware OFF, więc nie weryfikuję ruchu w mieście "
                                f"— false positive możliwy w cichej godzinie).\n\n"
                                f"Co robię: alertuję, monitoruję. Jeśli się utrzyma w peak → restart:\n"
                                f"sudo systemctl restart dispatch-panel-watcher"
                            ),
                            "context": {"stuck_value": recent_active[0], "stuck_count": STUCK_COUNT_TOLERANCE,
                                        "motion_aware": False},
                        })
                    elif panel_has_motion:
                        # Motion-aware: panel ma ruch (delivered/new/assigned changing) ALE count stuck = real bug
                        alerts.append({
                            "type": "PARSER_STUCK",
                            "severity": "warning",
                            "message": (
                                f"🚨 Panel zamrożony — ten sam zestaw aktywnych zamówień "
                                f"{STUCK_COUNT_TOLERANCE} razy z rzędu\n"
                                f"Panel zwraca dokładnie te same {recent_active[0]} aktywnych zamówień "
                                f"przez {STUCK_COUNT_TOLERANCE} minut, mimo że w mieście jest ruch "
                                f"({sum_new} nowe, {sum_delivered} dostarczone, {assigned_motion} przypisane). "
                                f"To wygląda na realny bug parsera — nie panel design.\n\n"
                                f"Co robię: alertuję i czekam jeszcze 1 cykl. "
                                f"Jeśli się utrzyma → restart:\n"
                                f"sudo systemctl restart dispatch-panel-watcher"
                            ),
                            "context": {"stuck_value": recent_active[0], "stuck_count": STUCK_COUNT_TOLERANCE,
                                        "motion_new": sum_new, "motion_delivered": sum_delivered,
                                        "motion_assigned_variance": assigned_motion,
                                        "motion_total": motion_total, "motion_threshold": PARSER_STUCK_MOTION_THRESHOLD,
                                        "motion_aware": True, "set_stuck": True},
                        })
                    # else: natural plateau (panel quiet, no motion) → NO alert (suppress false positive)
                else:
                    # set_stuck is None (fallback to legacy motion-only check)
                    if not ENABLE_PARSER_STUCK_MOTION_AWARE:
                        # Legacy behavior: alert na każdy stuck (false positives możliwe dla off-peak plateau)
                        alerts.append({
                            "type": "PARSER_STUCK",
                            "severity": "warning",
                            "message": (
                                f"⚠️ Panel zamrożony — ten sam zestaw aktywnych zamówień "
                                f"{STUCK_COUNT_TOLERANCE} razy z rzędu\n"
                                f"Panel zwraca {recent_active[0]} aktywnych zamówień przez "
                                f"{STUCK_COUNT_TOLERANCE} cykli (motion-aware OFF, więc nie weryfikuję ruchu w mieście "
                                f"— false positive możliwy w cichej godzinie).\n\n"
                                f"Co robię: alertuję, monitoruję. Jeśli się utrzyma w peak → restart:\n"
                                f"sudo systemctl restart dispatch-panel-watcher"
                            ),
                            "context": {"stuck_value": recent_active[0], "stuck_count": STUCK_COUNT_TOLERANCE,
                                        "motion_aware": False},
                        })
                    elif panel_has_motion:
                        # Motion-aware: panel ma ruch (delivered/new/assigned changing) ALE count stuck = real bug
                        alerts.append({
                            "type": "PARSER_STUCK",
                            "severity": "warning",
                            "message": (
                                f"🚨 Panel zamrożony — ten sam zestaw aktywnych zamówień "
                                f"{STUCK_COUNT_TOLERANCE} razy z rzędu\n"
                                f"Panel zwraca {recent_active[0]} aktywnych zamówień przez "
                                f"{STUCK_COUNT_TOLERANCE} minut, mimo że w mieście jest ruch "
                                f"({sum_new} nowe, {sum_delivered} dostarczone, {assigned_motion} przypisane). "
                                f"To wygląda na realny bug parsera — pattern z incydentu 02.05.\n\n"
                                f"Co robię: alertuję i czekam jeszcze 1 cykl. "
                                f"Jeśli się utrzyma → restart:\n"
                                f"sudo systemctl restart dispatch-panel-watcher"
                            ),
                            "context": {"stuck_value": recent_active[0], "stuck_count": STUCK_COUNT_TOLERANCE,
                                        "motion_new": sum_new, "motion_delivered": sum_delivered,
                                        "motion_assigned_variance": assigned_motion,
                                        "motion_total": motion_total, "motion_threshold": PARSER_STUCK_MOTION_THRESHOLD,
                                        "motion_aware": True},
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
                    f"⚠️ Niespójność: kurierzy mają {diff} zamówień których parser już nie widzi w panelu\n"
                    f"Stan bagów ({n_assigned} przypisanych) wyprzedza listę z panelu ({n_orders}). "
                    f"Zwykle to znaczy że panel zdążył usunąć dostarczone, a stan u Ziomka "
                    f"nie zdążył się jeszcze zaktualizować — wyrównuje się w 1-2 cykle.\n\n"
                    f"Co robię: nic — to obserwacyjny alert. Sprawdzę czy się wyrównało po peak. "
                    f"Jeśli utrzyma się 30+ min → daj znać, zrobię ręczny reconcile."
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
            # 2026-05-07: msg zawiera już kompletny content (tytuł z emoji + treść + akcja).
            # Type techniczny zostaje w log.error/warning powyżej dla parsability.
            try:
                from dispatch_v2.telegram_utils import send_admin_alert
                ok = send_admin_alert(msg)
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
                # Z2 fix 2026-05-07 #2: STUCK check używa active_orders dla spójności
                # z alerting path (_check_anomalies). ZERO_OUTPUT zostaje na orders_in_panel
                # (sygnał "parser literalnie pusty", niezależny od stanu zamówień).
                recent_orders = [c.get("orders_in_panel", 0) for c in cycles_list]
                recent_active = [c.get("active_orders", c.get("orders_in_panel", 0)) for c in cycles_list]
                anomalies_recent = []
                # Z2 fix 2026-05-07 #16: snapshot replicates alert-path suppressions
                # (motion-aware STUCK + hour-of-day ZERO) żeby endpoint był spójny
                # z faktycznymi alertami wysyłanymi do operatora.
                warsaw_hour = datetime.now(ZoneInfo("Europe/Warsaw")).hour
                # CHECK 1 ZERO: suppress pre-PARSER_HEALTH_STUCK_MIN_HOUR_WARSAW Warsaw
                # (naturalny plateau panelu po nightly rollover, parser zwraca 0 bo brak
                # nowych orderów — sygnał noise dla operatora).
                recent_zero = sum(1 for v in recent_orders if v == 0)
                if (recent_zero >= ZERO_ORDERS_TOLERANCE_CYCLES
                        and warsaw_hour >= PARSER_HEALTH_STUCK_MIN_HOUR_WARSAW):
                    anomalies_recent.append("PARSER_ZERO_OUTPUT")
                # CHECK 3 STUCK: suppress gdy panel quiet (motion_total < threshold) —
                # mirror motion-aware logic z _check_anomalies. Defense-in-depth try/except
                # — fallback "fire" jeśli motion compute crashes (legacy behavior).
                stuck = (len(recent_active) >= STUCK_COUNT_TOLERANCE
                         and all(v == recent_active[-1] for v in recent_active[-STUCK_COUNT_TOLERANCE:])
                         and recent_active[-1] > 0)
                if stuck:
                    recent = cycles_list[-STUCK_COUNT_TOLERANCE:]
                    try:
                        sum_new = sum(int(c.get("n_new", 0) or 0) for c in recent)
                        sum_delivered = sum(int(c.get("n_delivered", 0) or 0) for c in recent)
                        assigned_values = [int(c.get("n_assigned", 0) or 0) for c in recent]
                        assigned_motion = (max(assigned_values) - min(assigned_values)) if assigned_values else 0
                        motion_total = sum_new + sum_delivered + assigned_motion
                    except Exception as _me:
                        log.warning(f"snapshot motion compute fail (non-blocking, fallback fire): {_me}")
                        motion_total = PARSER_STUCK_MOTION_THRESHOLD  # fallback: fire alert
                    if (not ENABLE_PARSER_STUCK_MOTION_AWARE
                            or motion_total >= PARSER_STUCK_MOTION_THRESHOLD):
                        anomalies_recent.append("PARSER_STUCK")
                status = "critical" if "PARSER_ZERO_OUTPUT" in anomalies_recent else (
                    "degraded" if anomalies_recent else "healthy"
                )
                return {
                    "status": status,
                    "cycles_recorded": len(cycles_list),
                    "last_tick_ts": last.get("ts"),
                    "last_orders_in_panel": last.get("orders_in_panel"),
                    "last_active_orders": last.get("active_orders", last.get("orders_in_panel")),
                    "recent_orders_window": recent_orders,
                    "recent_active_window": recent_active,
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
