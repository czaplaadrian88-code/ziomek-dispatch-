"""GPS feed freshness detector — fleet-level (GPS-01, audyt 2026-06-03).

Wzorowany 1:1 na `monitoring/consumer_stuck_alert.py` (pure functions + frozen
config + mutable in-memory state, zero I/O w hot path). Wykrywa sytuacje gdy
*cała* flota traci świeży GPS (PWA server :8766/:8767 down w peaku) → cicha
degradacja scoringu kierunku/po-drodze/ETA na pozycje zastępcze, BEZ żadnego
sygnału do człowieka.

KLUCZOWA decyzja (audyt): DENOMINATOR = aktywna flota (active_ids z
dispatchable_fleet/grafik), NIE len(gps_dict). Plik gps_positions_pwa.json puchnie
starymi wpisami kurierów nie-pracujących od tygodni (median ~4.5 dnia stale).
Liczenie fresh/total na surowym pliku dałoby bezużyteczny próg.

KONTEKST 2026-06: brak GPS to CELOWY stan testowy (apka GPS na kilku kontach,
debug). GPS będzie normą dopiero przy autonomicznym starcie. Dlatego detektor
buduje się GOTOWY ale DOMYŚLNIE WYŁĄCZONY (GpsFeedAlertConfig.enabled=False →
flaga GPS_FEED_ALERT_ENABLED=false). Gdy enabled=False — caller w ogóle nie woła
tego modułu (hook short-circuit), więc ZERO logu/halasu teraz.

Gdy włączony: alarm gdy fresh_ratio < min_fresh_ratio przez >= sustain_cycles
kolejnych cykli (anti-flap), z cooldown re-alert (operator reminder). Recovery gdy
fresh_ratio wróci >= min_fresh_ratio (hysteresis na tym samym progu — fleet feed
binarny: albo PWA pisze albo nie).

Persistence: in-memory state (restart-clean; sustain_cycles=2 zapobiega
false-positive natychmiast po restarcie). Append-only audit trail w
`dispatch_state/gps_feed_health_evaluations.jsonl` przez `append_gps_feed_log` —
survivability cross-restart + future kalibracja progu.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Literal, Optional, Tuple

GpsAlertKind = Literal["ENTER", "SUSTAINED", "RECOVERY"]

DEFAULT_EVALUATIONS_LOG_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/gps_feed_health_evaluations.jsonl"
)


@dataclass(frozen=True)
class GpsFeedHealth:
    """Per-tick computed fleet GPS feed liveness. Pure-derived z
    (active_ids, gps_dict, now, fresh_cutoff_min).

    Pola:
    - total_active: liczność aktywnej floty (DENOMINATOR — NIE len(gps_dict))
    - fresh: ilu aktywnych kurierów ma wpis GPS świeższy niż fresh_cutoff_min
    - fresh_ratio: fresh/total_active (0.0 gdy total_active==0 — patrz uwaga niżej)
    - median_age_min: mediana wieku (min) świeżych+stale wpisów aktywnych kurierów
      z parsowalnym timestampem; None gdy żaden aktywny nie ma wpisu GPS

    UWAGA total_active==0: brak aktywnej floty (noc / wszyscy poza grafikiem) →
    fresh_ratio=1.0 (neutralne, NIE alarmuj — nie ma kogo widzieć). Decyzję
    'czy to anomalia' podejmuje evaluate (skip gdy total_active < min_active_fleet).
    """

    total_active: int
    fresh: int
    fresh_ratio: float
    median_age_min: Optional[float] = None


def _parse_gps_ts(raw) -> Optional[datetime]:
    """Parsuje GPS timestamp tak samo jak build_fleet_snapshot (courier_resolver:571).
    Zwraca aware UTC datetime lub None (caller traktuje None = brak świeżej pozycji).
    """
    if not raw:
        return None
    try:
        if isinstance(raw, datetime):
            dt = raw
        else:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def compute_gps_feed_health(
    active_ids: Iterable[str],
    gps_dict: Dict,
    now_utc: datetime,
    fresh_cutoff_min: float,
) -> GpsFeedHealth:
    """Pure compute. ZERO I/O — caller dostarcza już-załadowane active_ids + gps_dict.

    - active_ids: courier_id (str) aktywnej floty (dispatchable_fleet / grafik).
      DENOMINATOR. Deduplikowane wewnątrz (set).
    - gps_dict: {courier_id_str: {lat,lon,timestamp,...}} — wynik
      courier_resolver._load_gps_positions(). Per-wpis 'timestamp' (NIE mtime pliku).
    - now_utc: aware UTC datetime (caller: datetime.now(timezone.utc)).
    - fresh_cutoff_min: próg świeżości (mirror GPS_FRESHNESS_MIN=5).

    'fresh' = aktywny kurier którego wpis GPS ma age < fresh_cutoff_min.
    Wpis brakujący / nieparsowalny / stale → NIE liczy się jako fresh.
    """
    ids = {str(c) for c in active_ids if c is not None and str(c) != ""}
    total_active = len(ids)
    if total_active == 0:
        return GpsFeedHealth(total_active=0, fresh=0, fresh_ratio=1.0, median_age_min=None)

    fresh = 0
    ages = []
    for cid in ids:
        entry = gps_dict.get(cid)
        if not isinstance(entry, dict):
            continue
        dt = _parse_gps_ts(entry.get("timestamp"))
        if dt is None:
            continue
        age_min = (now_utc - dt).total_seconds() / 60.0
        if age_min < 0:
            age_min = 0.0
        ages.append(age_min)
        if age_min < fresh_cutoff_min:
            fresh += 1

    fresh_ratio = fresh / total_active
    median_age = None
    if ages:
        ages.sort()
        n = len(ages)
        mid = n // 2
        median_age = ages[mid] if n % 2 == 1 else (ages[mid - 1] + ages[mid]) / 2.0
    return GpsFeedHealth(
        total_active=total_active,
        fresh=fresh,
        fresh_ratio=fresh_ratio,
        median_age_min=median_age,
    )


@dataclass(frozen=True)
class GpsFeedAlertConfig:
    """Config detektora. INERT-by-default: enabled=False → caller nie woła wcale.

    - enabled: master switch (flaga GPS_FEED_ALERT_ENABLED). False = detektor
      martwy (GPS celowo off teraz). Flip True dopiero przy autonomicznym starcie.
    - shadow_only: True → evaluate + audit log, NIE wysyła Telegrama (kalibracja).
    - min_fresh_ratio: alarm gdy fresh_ratio < ten próg (default 0.30).
    - fresh_cutoff_min: próg świeżości pojedynczego wpisu (mirror GPS_FRESHNESS_MIN).
    - sustain_cycles: N kolejnych cykli poniżej progu przed ENTER (anti-flap).
    - realert_interval_sec: SUSTAINED reminder cadence (default 30 min).
    - heartbeat_interval_sec: rytm ticku (informational, dla treści wiadomości).
    - min_active_fleet: nie alarmuj gdy total_active < tyle (noc / mała flota —
      1 kurier offline nie znaczy 'feed zamarł'). Default 3.
    """

    enabled: bool = False
    shadow_only: bool = True
    min_fresh_ratio: float = 0.30
    fresh_cutoff_min: float = 5.0
    sustain_cycles: int = 2
    realert_interval_sec: int = 1800
    heartbeat_interval_sec: int = 60
    min_active_fleet: int = 3

    @classmethod
    def from_flags(cls, flag_fn, **defaults) -> "GpsFeedAlertConfig":
        """Build config z hot-reload flags.json przez przekazany `flag_fn`
        (np. dispatch_v2.common.flag). flag_fn(name, default) zwraca wartość
        (bool/float/int) z flags.json lub default. Pozwala instant kill-switch
        bez restartu (Z1/Z2 doktryna repo).

        Klucze flags.json:
          GPS_FEED_ALERT_ENABLED (bool, default False)
          GPS_FEED_ALERT_SHADOW_ONLY (bool, default True)
          GPS_FEED_MIN_FRESH_RATIO (float, default 0.30)
          GPS_FEED_FRESH_CUTOFF_MIN (float, default 5.0)
          GPS_FEED_SUSTAIN_CYCLES (int, default 2)
          GPS_FEED_REALERT_INTERVAL_SEC (int, default 1800)
          GPS_FEED_MIN_ACTIVE_FLEET (int, default 3)
        """
        d = dict(
            enabled=False, shadow_only=True, min_fresh_ratio=0.30,
            fresh_cutoff_min=5.0, sustain_cycles=2, realert_interval_sec=1800,
            heartbeat_interval_sec=60, min_active_fleet=3,
        )
        d.update(defaults)
        return cls(
            enabled=bool(flag_fn("GPS_FEED_ALERT_ENABLED", d["enabled"])),
            shadow_only=bool(flag_fn("GPS_FEED_ALERT_SHADOW_ONLY", d["shadow_only"])),
            min_fresh_ratio=float(flag_fn("GPS_FEED_MIN_FRESH_RATIO", d["min_fresh_ratio"])),
            fresh_cutoff_min=float(flag_fn("GPS_FEED_FRESH_CUTOFF_MIN", d["fresh_cutoff_min"])),
            sustain_cycles=int(flag_fn("GPS_FEED_SUSTAIN_CYCLES", d["sustain_cycles"])),
            realert_interval_sec=int(flag_fn("GPS_FEED_REALERT_INTERVAL_SEC", d["realert_interval_sec"])),
            heartbeat_interval_sec=d["heartbeat_interval_sec"],
            min_active_fleet=int(flag_fn("GPS_FEED_MIN_ACTIVE_FLEET", d["min_active_fleet"])),
        )

    @classmethod
    def from_env(cls, **defaults) -> "GpsFeedAlertConfig":
        """Build config z env (fallback gdy brak flag_fn). Prefix GPS_FEED_*."""
        def _b(name: str, default: bool) -> bool:
            raw = os.environ.get(name)
            if raw is None:
                return default
            return raw.lower() in ("1", "true", "yes", "on")

        def _f(name: str, default: float) -> float:
            return float(os.environ.get(name, str(default)))

        def _i(name: str, default: int) -> int:
            return int(os.environ.get(name, str(default)))

        d = dict(
            enabled=False, shadow_only=True, min_fresh_ratio=0.30,
            fresh_cutoff_min=5.0, sustain_cycles=2, realert_interval_sec=1800,
            heartbeat_interval_sec=60, min_active_fleet=3,
        )
        d.update(defaults)
        return cls(
            enabled=_b("GPS_FEED_ALERT_ENABLED", d["enabled"]),
            shadow_only=_b("GPS_FEED_ALERT_SHADOW_ONLY", d["shadow_only"]),
            min_fresh_ratio=_f("GPS_FEED_MIN_FRESH_RATIO", d["min_fresh_ratio"]),
            fresh_cutoff_min=_f("GPS_FEED_FRESH_CUTOFF_MIN", d["fresh_cutoff_min"]),
            sustain_cycles=_i("GPS_FEED_SUSTAIN_CYCLES", d["sustain_cycles"]),
            realert_interval_sec=_i("GPS_FEED_REALERT_INTERVAL_SEC", d["realert_interval_sec"]),
            heartbeat_interval_sec=d["heartbeat_interval_sec"],
            min_active_fleet=_i("GPS_FEED_MIN_ACTIVE_FLEET", d["min_active_fleet"]),
        )


@dataclass
class GpsFeedAlertState:
    """In-memory state. Restart-clean. sustain_cycles=2 zapobiega false-positive
    natychmiast po restarcie (pierwsze 2 cykle muszą oba pokazać degraded=True)."""

    alert_sent: bool = False
    streak: int = 0
    last_alert_ts: float = 0.0
    first_alert_ts: float = 0.0


def _is_degraded(health: GpsFeedHealth, config: GpsFeedAlertConfig) -> bool:
    """Degraded = jest sensowna flota (>= min_active_fleet) AND fresh_ratio poniżej
    progu. Mała/zerowa flota → NIE degraded (brak kogo widzieć)."""
    if health.total_active < config.min_active_fleet:
        return False
    return health.fresh_ratio < config.min_fresh_ratio


def evaluate_gps_feed_alert(
    state: GpsFeedAlertState,
    health: GpsFeedHealth,
    now: float,
    config: GpsFeedAlertConfig,
) -> Tuple[bool, Optional[GpsAlertKind], GpsFeedAlertState]:
    """Pure state machine. Returns (emit, kind, new_state). Mirror
    evaluate_stuck_alert (consumer_stuck_alert.py).

    Transitions (precedence RECOVERY > ENTER > SUSTAINED):
    - RECOVERY: latched AND NOT degraded → emit + reset latch
    - ENTER: NOT latched AND streak >= sustain_cycles → emit + set latch + ts
    - SUSTAINED: latched AND degraded AND elapsed >= realert_interval → emit + ts
    - NO-OP: reszta; streak inc gdy degraded else reset (anti-flap)

    Recovery na tym samym progu (min_fresh_ratio): feed flotowy jest binarny
    (PWA pisze albo nie) — brak potrzeby osobnego low-water hysteresis.
    """
    degraded = _is_degraded(health, config)
    new_streak = state.streak + 1 if degraded else 0

    if state.alert_sent and not degraded:
        return (True, "RECOVERY", GpsFeedAlertState())

    if (not state.alert_sent) and new_streak >= config.sustain_cycles:
        return (
            True,
            "ENTER",
            GpsFeedAlertState(alert_sent=True, streak=new_streak, last_alert_ts=now, first_alert_ts=now),
        )

    if (
        state.alert_sent
        and degraded
        and (now - state.last_alert_ts) >= config.realert_interval_sec
    ):
        return (
            True,
            "SUSTAINED",
            GpsFeedAlertState(
                alert_sent=True,
                streak=new_streak,
                last_alert_ts=now,
                first_alert_ts=state.first_alert_ts,
            ),
        )

    return (False, None, replace(state, streak=new_streak))


def render_gps_feed_message(
    kind: GpsAlertKind,
    health: GpsFeedHealth,
    state: GpsFeedAlertState,
    config: GpsFeedAlertConfig,
    now: float,
) -> str:
    """Polski template (co się stało + co robię/co masz zrobić). Mobile-readable."""
    ratio_pct = health.fresh_ratio * 100.0
    min_pct = config.min_fresh_ratio * 100.0
    realert_min = config.realert_interval_sec // 60
    age_str = (
        f"{health.median_age_min:.0f} min" if health.median_age_min is not None else "brak wpisów"
    )
    if kind == "ENTER":
        return (
            f"\U0001F6F0 GPS FEED DEGRADED (ENTER)\n"
            f"świeży GPS: {health.fresh}/{health.total_active} aktywnych "
            f"({ratio_pct:.0f}% < próg {min_pct:.0f}%)\n"
            f"mediana wieku pozycji: {age_str} (cutoff {config.fresh_cutoff_min:.0f} min)\n"
            f"sustain={state.streak}/{config.sustain_cycles} → ENTER\n"
            f"Prawdopodobnie PWA GPS server padł — Ziomek liczy ETA/po-drodze na "
            f"pozycjach zastępczych. Sprawdź gps_server (:8766/:8767).\n"
            f"Kolejny reminder za {realert_min} min jeśli feed nie wróci."
        )
    if kind == "SUSTAINED":
        elapsed_min = (now - state.first_alert_ts) / 60.0 if state.first_alert_ts else 0.0
        return (
            f"⚠️ GPS FEED WCIĄŻ DEGRADED (SUSTAINED, {elapsed_min:.0f} min)\n"
            f"świeży GPS: {health.fresh}/{health.total_active} ({ratio_pct:.0f}%)\n"
            f"Feed nie wrócił — propozycje na pozycjach zastępczych.\n"
            f"Kolejny reminder za {realert_min} min."
        )
    if kind == "RECOVERY":
        total_min = (now - state.first_alert_ts) / 60.0 if state.first_alert_ts else 0.0
        return (
            f"✅ GPS FEED RECOVERED\n"
            f"świeży GPS: {health.fresh}/{health.total_active} ({ratio_pct:.0f}%) ≥ "
            f"próg {min_pct:.0f}%\n"
            f"Degradacja łącznie {total_min:.0f} min. Latch reset, re-armed."
        )
    return f"GPS feed alert unknown kind={kind}"


def append_gps_feed_log(
    health: GpsFeedHealth,
    state_before: GpsFeedAlertState,
    state_after: GpsFeedAlertState,
    emit: bool,
    kind: Optional[GpsAlertKind],
    config: GpsFeedAlertConfig,
    now: float,
    log_path: Optional[Path] = None,
) -> None:
    """Append-only audit trail. Atomic single-line JSON append (O_APPEND, <PIPE_BUF
    = atomic POSIX). Defense-in-depth try/except — log fail NIE blokuje hot path."""
    if log_path is None:
        log_path = DEFAULT_EVALUATIONS_LOG_PATH
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": now,
            "health": asdict(health),
            "state_before": asdict(state_before),
            "state_after": asdict(state_after),
            "emit": emit,
            "kind": kind,
            "enabled": config.enabled,
            "shadow_only": config.shadow_only,
            "min_fresh_ratio": config.min_fresh_ratio,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Audit log fail NIE eskaluje — heartbeat tick must continue.
        pass
