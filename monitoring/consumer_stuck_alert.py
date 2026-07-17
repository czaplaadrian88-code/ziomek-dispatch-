"""Consumer stuck alert state machine — reusable abstraction.

Sprint #37 v2 (2026-05-13). Pure functions + dataclasses, zero I/O w hot path.
Refaktor 3 inline implementacji (MP-#13 OSRM L2, V3.28 #33/#35 shadow, sla_tracker
brakujący alert post-#36) na jedną abstrakcję. Każdy consumer dostaje `StuckAlertConfig`
+ trzyma `StuckAlertState` in-memory. Tick wywołuje `compute_heartbeat` → `evaluate_stuck_alert`
→ jeśli emit i nie shadow_mode → `render_telegram_message` + `send_admin_alert`.

Backward-compat: `shadow_dispatcher._v328_should_emit_stuck_alert` zostaje jako
thin wrapper aż 25 #35 testów cleanup. Po Sprint #37+1 stable 7 dni → delete wrapper.

Persistence: in-memory state (restart-clean, sustain_cycles=2 zapobiega false-positive
post-restart). Append-only audit trail w `dispatch_state/consumer_stuck_alert_evaluations.jsonl`
przez `append_evaluation_log` — survivability cross-restart + future calibration.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import FrozenSet, Literal, Optional, Tuple

AlertKind = Literal["ENTER", "SUSTAINED", "RECOVERY"]

DEFAULT_EVALUATIONS_LOG_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/consumer_stuck_alert_evaluations.jsonl"
)


@dataclass(frozen=True)
class StuckAlertConfig:
    """Per-consumer configuration. Env-overrideable via prefix `STUCK_ALERT_{CONSUMER_ID.upper()}_*`.

    Pola:
    - consumer_id: stable identifier, używany w env prefix + alert telemetry
    - consumer_display_name: human-readable PL nazwa w treści Telegram
    - event_types: queue typy które consumer faktycznie czyta z event_bus (per-consumer
      attribution, eliminacja wrong-attribution Lekcja #113)
    - age_threshold_sec: stuck = age > X AND pending > pending_threshold
    - pending_threshold: high-water, drugi warunek is_stuck
    - pending_low_water: hysteresis exit, recovery = pending ≤ low_water (NIE single
      is_stuck=False — Lekcja #112)
    - sustain_cycles: N kolejnych is_stuck=True przed ENTER (anti-flap)
    - realert_interval_sec: SUSTAINED reminder cadence
    - heartbeat_interval_sec: tick rhythm (informational, używany w message templates)
    - shadow_mode_only: True → evaluate + log, NIE wysyła Telegrama (calibration window
      dla nowych consumerów; flip False po empirycznej walidacji)
    """

    consumer_id: str
    consumer_display_name: str
    event_types: FrozenSet[str]
    age_threshold_sec: int = 300
    pending_threshold: int = 100
    pending_low_water: int = 30
    sustain_cycles: int = 2
    realert_interval_sec: int = 1800
    heartbeat_interval_sec: int = 60
    shadow_mode_only: bool = False

    @classmethod
    def from_env(
        cls,
        consumer_id: str,
        consumer_display_name: str,
        event_types: FrozenSet[str],
        **defaults,
    ) -> "StuckAlertConfig":
        """Build config z env overrides per-consumer prefix `STUCK_ALERT_{CONSUMER_ID.upper()}_*`.

        Examples:
          STUCK_ALERT_SHADOW_AGE_SEC=300
          STUCK_ALERT_SLA_TRACKER_PENDING_THRESHOLD=50
          STUCK_ALERT_SLA_TRACKER_SHADOW_MODE_ONLY=true
        """
        prefix = f"STUCK_ALERT_{consumer_id.upper()}_"

        def _env_int(suffix: str, default: int) -> int:
            return int(os.environ.get(f"{prefix}{suffix}", str(default)))

        def _env_bool(suffix: str, default: bool) -> bool:
            raw = os.environ.get(f"{prefix}{suffix}")
            if raw is None:
                return default
            return raw.lower() in ("1", "true", "yes", "on")

        return cls(
            consumer_id=consumer_id,
            consumer_display_name=consumer_display_name,
            event_types=event_types,
            age_threshold_sec=_env_int("AGE_SEC", defaults.get("age_threshold_sec", 300)),
            pending_threshold=_env_int("PENDING_THRESHOLD", defaults.get("pending_threshold", 100)),
            pending_low_water=_env_int("PENDING_LOW_WATER", defaults.get("pending_low_water", 30)),
            sustain_cycles=_env_int("SUSTAIN_CYCLES", defaults.get("sustain_cycles", 2)),
            realert_interval_sec=_env_int(
                "REALERT_INTERVAL_SEC", defaults.get("realert_interval_sec", 1800)
            ),
            heartbeat_interval_sec=_env_int(
                "HEARTBEAT_INTERVAL_SEC", defaults.get("heartbeat_interval_sec", 60)
            ),
            shadow_mode_only=_env_bool(
                "SHADOW_MODE_ONLY", defaults.get("shadow_mode_only", False)
            ),
        )


@dataclass
class StuckAlertState:
    """In-memory state. Restart-clean. Sustain_cycles=2 zapobiega false-positive
    natychmiast po restart (pierwsze 2 cycle muszą oba pokazać is_stuck=True)."""

    alert_sent: bool = False
    streak: int = 0
    last_alert_ts: float = 0.0
    first_alert_ts: float = 0.0


@dataclass(frozen=True)
class HeartbeatSnapshot:
    """Per-tick computed liveness. Pure-derived z (last_processed_ts, now, pending, config)."""

    age_sec: float
    pending: int
    worker_alive: bool
    is_stuck: bool
    is_recovered: bool


def compute_heartbeat(
    last_processed_ts: float,
    now: float,
    pending: int,
    config: StuckAlertConfig,
) -> HeartbeatSnapshot:
    """Pure compute. Multi-signal stuck = age > threshold AND pending > threshold
    (quiet period z low pending = worker idle, NIE stuck). Recovery decoupled od
    is_stuck — Lekcja #112 (single processed event flipuje age=0 ale pending wciąż
    high → to NIE jest recovery)."""
    age_sec = max(0.0, now - last_processed_ts)
    return HeartbeatSnapshot(
        age_sec=age_sec,
        pending=pending,
        worker_alive=age_sec < config.age_threshold_sec,
        is_stuck=(age_sec > config.age_threshold_sec and pending > config.pending_threshold),
        is_recovered=(pending <= config.pending_low_water),
    )


def evaluate_stuck_alert(
    state: StuckAlertState,
    snapshot: HeartbeatSnapshot,
    now: float,
    config: StuckAlertConfig,
) -> Tuple[bool, Optional[AlertKind], StuckAlertState]:
    """Pure state machine. Returns (emit, kind, new_state).

    Cztery transitions (zachowuje #35 semantics):
    - RECOVERY: latched AND is_recovered → emit + reset latch+streak+ts
    - ENTER: NOT latched AND streak >= sustain_cycles → emit + set latch + zapis ts
    - SUSTAINED: latched AND is_stuck AND elapsed >= realert_interval → emit + update ts
    - NO-OP: pozostałe; streak inc gdy is_stuck else reset (Lekcja #112)

    Precedence RECOVERY > ENTER > SUSTAINED: gdy is_recovered (pending dropped),
    nawet jeśli streak sustain'owało, latch reset ważniejszy niż nowy ENTER.
    """
    new_streak = state.streak + 1 if snapshot.is_stuck else 0

    if state.alert_sent and snapshot.is_recovered:
        return (True, "RECOVERY", StuckAlertState())

    if (not state.alert_sent) and new_streak >= config.sustain_cycles:
        return (
            True,
            "ENTER",
            StuckAlertState(alert_sent=True, streak=new_streak, last_alert_ts=now, first_alert_ts=now),
        )

    if (
        state.alert_sent
        and snapshot.is_stuck
        and (now - state.last_alert_ts) >= config.realert_interval_sec
    ):
        return (
            True,
            "SUSTAINED",
            StuckAlertState(
                alert_sent=True,
                streak=new_streak,
                last_alert_ts=now,
                first_alert_ts=state.first_alert_ts,
            ),
        )

    return (
        False,
        None,
        replace(state, streak=new_streak),
    )


def render_telegram_message(
    kind: AlertKind,
    snapshot: HeartbeatSnapshot,
    state: StuckAlertState,
    config: StuckAlertConfig,
    now: float,
) -> str:
    """Polski template per feedback rule (co się stało + co robię/co masz zrobić).
    Parametryzowane consumer_display_name + event_types w pending label."""
    pending_label = "+".join(sorted(config.event_types))
    realert_min = config.realert_interval_sec // 60

    if kind == "ENTER":
        return (
            f"🚨 {config.consumer_display_name} STUCK (ENTER)\n"
            f"age={snapshot.age_sec:.0f}s (próg {config.age_threshold_sec}s)\n"
            f"pending {pending_label}={snapshot.pending} "
            f"(high={config.pending_threshold}, low_water={config.pending_low_water})\n"
            f"sustain_cycles={state.streak}/{config.sustain_cycles} → ENTER\n"
            f"Możliwe peak load lub poison message — koordynator review.\n"
            f"Kolejny reminder za {realert_min} min "
            f"jeśli pending nie spadnie poniżej {config.pending_low_water}."
        )
    if kind == "SUSTAINED":
        elapsed_min = (now - state.first_alert_ts) / 60.0 if state.first_alert_ts else 0.0
        return (
            f"⚠️ {config.consumer_display_name} WCIĄŻ STUCK "
            f"(SUSTAINED, {elapsed_min:.0f} min)\n"
            f"age={snapshot.age_sec:.0f}s pending {pending_label}={snapshot.pending} "
            f"(low_water={config.pending_low_water})\n"
            f"Backlog nie spada — operator review konieczny.\n"
            f"Kolejny reminder za {realert_min} min."
        )
    if kind == "RECOVERY":
        total_min = (now - state.first_alert_ts) / 60.0 if state.first_alert_ts else 0.0
        return (
            f"✅ {config.consumer_display_name} RECOVERED\n"
            f"pending {pending_label}={snapshot.pending} ≤ low_water={config.pending_low_water}\n"
            f"Stuck cycle łącznie {total_min:.0f} min. Latch reset, re-armed."
        )
    return f"{config.consumer_display_name} stuck alert unknown kind={kind}"


def append_evaluation_log(
    snapshot: HeartbeatSnapshot,
    state_before: StuckAlertState,
    state_after: StuckAlertState,
    emit: bool,
    kind: Optional[AlertKind],
    config: StuckAlertConfig,
    now: float,
    log_path: Optional[Path] = None,
) -> None:
    """Append-only audit trail. Atomic line write (append O_APPEND single-line JSON
    = atomic na POSIX dla <PIPE_BUF). Defense-in-depth try/except — log fail NIE
    blokuje hot path (consumer wciąż działa, tylko obs gap)."""
    if log_path is None:
        log_path = DEFAULT_EVALUATIONS_LOG_PATH
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": now,
            "consumer_id": config.consumer_id,
            "event_types": sorted(config.event_types),
            "snapshot": asdict(snapshot),
            "state_before": asdict(state_before),
            "state_after": asdict(state_after),
            "emit": emit,
            "kind": kind,
            "shadow_mode_only": config.shadow_mode_only,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Audit log fail NIE eskaluje — consumer tick must continue.
        pass
