"""SLA Tracker - konsumer COURIER_PICKED_UP + COURIER_DELIVERED.
Liczy delivery_time_minutes, loguje do sla_log.jsonl.

F2.1b step 6: R6 BAG_TIME pre-warning — scan picked_up orderów co 10s,
alert Telegram gdy bag_time > 30 min, one-shot per order."""
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from dispatch_v2 import common as C
from dispatch_v2.common import now_iso, setup_logger
from dispatch_v2.core.broadcast_handlers import dispatch_config_reload
from dispatch_v2.core.config_reload_subscriber import BroadcastSubscriber
from dispatch_v2.event_bus import get_pending, mark_processed, mark_failed, get_pending_count
from dispatch_v2.monitoring.consumer_stuck_alert import (
    StuckAlertConfig,
    StuckAlertState,
    append_evaluation_log,
    compute_heartbeat,
    evaluate_stuck_alert,
    render_telegram_message,
)
from dispatch_v2.state_machine import get_order, upsert_order, get_by_status
from dispatch_v2.telegram_utils import send_admin_alert

_log = setup_logger("sla_tracker", "/root/.openclaw/workspace/scripts/logs/sla_tracker.log")
_running = True
_stats = {"pickup": 0, "delivered": 0, "violations": 0, "r6_alerts": 0,
          "restaurant_violations": 0}

# Sprint #37 v2 Phase B (2026-05-13): per-consumer stuck alert dla sla_tracker.
# Post-#36 (poison-msg fix) sla_tracker miał per-event isolation ALE brakowało
# stuck alertu — 3-dniowy infinite loop 08-11.05 byłby NIEWIDOCZNY dla Adriana
# bez empirycznego "naprawiaj" trigger'a. Brak alertu = silent killer (#87 ext).
# Thresholds konserwatywne (SLA events ~30-60s rytm vs shadow 5-15s):
#   age=600s, pending=50, low_water=15, sustain=2, realert=1800s.
# shadow_mode_only=True na 7-day calibration window — eval+log JSONL bez
# Telegrama. Po empirycznej walidacji thresholds → flip False via env:
#   STUCK_ALERT_SLA_TRACKER_SHADOW_MODE_ONLY=false.
_SLA_STUCK_CONFIG = StuckAlertConfig.from_env(
    consumer_id="sla_tracker",
    consumer_display_name="Ziomek SLA tracker",
    event_types=frozenset(["COURIER_PICKED_UP", "COURIER_DELIVERED"]),
    age_threshold_sec=600,
    pending_threshold=50,
    pending_low_water=15,
    sustain_cycles=2,
    realert_interval_sec=1800,
    heartbeat_interval_sec=60,
    shadow_mode_only=True,  # 7-day calibration window — flip env po obs
)
SLA_HEARTBEAT_INTERVAL_SEC = 60.0
LOG_PATH = Path("/root/.openclaw/workspace/scripts/logs/sla_log.jsonl")
# ETAP 6 (Z-19, 2026-06-10): naruszenia kontraktu restauracji ±5 min.
# KB §II.8 deklarował ten plik od początku — kod nigdy nie powstał.
# Próg celowo TUTAJ (nie w common.py) — równoległa sesja ETAPU 4 pracuje
# na common.py; flaga czytana inline przez C.flag() z tego samego powodu.
RESTAURANT_VIOLATIONS_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/restaurant_violations.jsonl"
)
RESTAURANT_VIOLATION_MAX_WAIT_MIN = 5.0
COURIER_NAMES_PATH = Path("/root/.openclaw/workspace/dispatch_state/courier_names.json")
KURIER_IDS_PATH = Path("/root/.openclaw/workspace/dispatch_state/kurier_ids.json")  # V3.25 inverse fallback
_courier_names: Dict[str, str] = {}


def _load_courier_names() -> Dict[str, str]:
    """V3.25 (STEP A.2): MERGE inverse(kurier_ids) + courier_names. courier_names wins."""
    merged: Dict[str, str] = {}
    try:
        ids = json.loads(KURIER_IDS_PATH.read_text())
        for name, cid in ids.items():
            cid_str = str(cid)
            if cid_str not in merged:
                merged[cid_str] = name
    except Exception as e:
        _log.warning(f"_load_courier_names: kurier_ids fallback fail: {e}")
    try:
        names = json.loads(COURIER_NAMES_PATH.read_text())
        for cid_str, name in names.items():
            merged[cid_str] = name
    except Exception as e:
        _log.warning(f"courier_names load fail: {e}")
    return merged


def _handler(signum, frame):
    global _running
    _log.info(f"Signal {signum}")
    _running = False


def _parse(s):
    """Legacy parser — SLA path. Aware strings zwracają aware, naive zwracają naive.
    TECHDEBT (F2.2): naive Warsaw string z fromisoformat zwraca naive datetime.
    SLA (d-p) działa poprawnie bo obie strony naive w tej samej strefie.
    Pełny fix primary path → F2.2 z retestem SLA delivery_time_minutes.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_aware_utc(s):
    """Parser zwracający ZAWSZE aware datetime w UTC (lub None).

    Różnica vs _parse():
      _parse()         — legacy parser używany przez SLA check ścieżkę
                         (process(COURIER_DELIVERED)). Dla naive stringów
                         `fromisoformat` sukces zwraca NAIVE datetime (bez tzinfo).
                         SLA check działa z tym "niechcący poprawnie" bo robi
                         `(d - p)` gdzie OBIE daty są naive — timedelta jest
                         numerycznie correct, ale oba są w tej samej (nieznanej)
                         tzinfo=None strefie.
      _parse_aware_utc() — NOWY parser dla R6 krok #6.1. Naive string traktuje
                         jako Warsaw-local (bo panel Rutcom emituje naive Warsaw)
                         i konwertuje do UTC. Zawsze zwraca aware UTC datetime.

    Dlaczego dwa parsery:
      R6 `_check_bag_time_alerts` porównuje `now_utc` (aware UTC z datetime.now)
      z `picked_up_at` z state. Mieszanka aware vs naive → TypeError. SLA check
      ma **obie** naive więc nie crashuje. Nie możemy zastąpić _parse() przez
      _parse_aware_utc() bez pełnej weryfikacji SLA path — istniejące
      delivery_time_minutes liczone z (d - p) gdzie oba są naive Warsaw dają
      POPRAWNY wynik numerycznie (różnica nie zależy od tzinfo gdy oba są w
      tej samej strefie). Zmiana _parse → aware Warsaw → aware UTC zmienia
      typ, co wymaga retestu SLA logowania + downstream consumerów sla_log.jsonl.

    Pre-existing bug:
      _parse() fallback `strptime.replace(tzinfo=timezone.utc)` ZAKŁADA że naive
      jest UTC — to jest błędne, panel wysyła naive Warsaw. W praktyce ścieżka
      strptime nie jest dotykana (fromisoformat sukces dla naive), więc bug
      jest uśpiony. Udokumentowany w docs/TECH_DEBT.md sekcja "F2.1b step 6 —
      sla_tracker._parse() naive→UTC timestamp bug". Fix → F2.1c lub krok #8
      z pełnym retestem SLA delivery_time_minutes.

    Returns:
        datetime with tzinfo=timezone.utc, albo None dla corrupt/empty input.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo
        dt = dt.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
    return dt.astimezone(timezone.utc)


def process(evt):
    etype = evt["event_type"]
    oid = evt.get("order_id")
    payload = evt.get("payload", {})

    if etype == "COURIER_PICKED_UP":
        ts = payload.get("timestamp", now_iso())
        upsert_order(oid, {"picked_up_at": ts}, event="SLA_PICKUP")
        _stats["pickup"] += 1
        _log.info(f"pickup {oid} at {ts}")
        return True

    if etype == "COURIER_DELIVERED":
        order = get_order(oid) or {}
        delivered_ts = payload.get("timestamp", now_iso())
        picked_ts = order.get("picked_up_at")

        dmin = None
        sla_ok = None
        if picked_ts:
            # V3.28 #36 (2026-05-11 wieczór): TZ-safe parse via _parse_aware_utc.
            # Pre-#36 `_parse` (legacy) zwracał mixed aware/naive zależnie od
            # input format (`fromisoformat` na "2026-05-11T12:34:55+00:00" → aware UTC,
            # ale "2026-05-11 13:22:37" naive Warsaw → naive datetime). Subtrakcja
            # mieszanych typów rzucała `TypeError: can't subtract offset-naive and
            # offset-aware datetimes` → poison message blokujący całą kolejkę
            # COURIER_PICKED_UP/DELIVERED (akumulacja 201 eventów do ~18:48 UTC dziś).
            # `_parse_aware_utc` zawsze zwraca aware UTC (naive Warsaw → UTC convert)
            # → subtrakcja numerycznie correct, zachowuje semantykę SLA. Tech debt
            # docstring _parse_aware_utc:75-94 dokumentował ten fix jako odłożony
            # do F2.2 retestu — przesunięty na #36 incident-driven.
            p, d = _parse_aware_utc(picked_ts), _parse_aware_utc(delivered_ts)
            if p and d:
                dmin = round((d - p).total_seconds() / 60, 1)
                sla_ok = dmin <= 35

        rec = {
            "order_id": oid,
            "courier_id": evt.get("courier_id") or order.get("courier_id"),
            "restaurant": order.get("restaurant"),
            "delivery_address": order.get("delivery_address"),
            "picked_up_at": picked_ts,
            "delivered_at": delivered_ts,
            "delivery_time_minutes": dmin,
            "sla_ok": sla_ok,
            "was_czasowka": order.get("order_type") == "czasowka",
            "logged_at": now_iso(),
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        _stats["delivered"] += 1
        if sla_ok is False:
            _stats["violations"] += 1
            _log.warning(f"SLA VIOLATION {oid}: {dmin}min courier={rec['courier_id']}")
        else:
            _log.info(f"SLA OK {oid}: {dmin}min")
        return True

    return False


def _format_picked_up_hhmm(picked_dt: datetime) -> str:
    """ISO dt → 'HH:MM' Warsaw local for R6 alert message."""
    try:
        from zoneinfo import ZoneInfo
        if picked_dt.tzinfo is None:
            picked_dt = picked_dt.replace(tzinfo=timezone.utc)
        return picked_dt.astimezone(ZoneInfo("Europe/Warsaw")).strftime("%H:%M")
    except Exception:
        return "??:??"


def _check_bag_time_alerts(now_utc: datetime) -> None:
    """F2.1b step 6: R6 BAG_TIME pre-warning scan.

    Iteruje picked_up ordery, liczy bag_time_min = now - picked_up_at.
    Dla orderów z bag_time > C.BAG_TIME_PRE_WARNING_MIN AND bag_time_alerted=False:
      1. Upsert bag_time_alerted=True (PRZED send — one-shot guarantee)
      2. Wysyła Telegram alert do admina
      3. Loguje warning z detail, error przy send fail (alert lost)

    Per-order try/except — jeden bad order nie ubija całego skanu.
    Set-then-send (Opcja X): duplicate-safe, Telegram fail logowany bez retry.

    Adrian decision 2026-05-07: flag `ENABLE_BAG_TIME_ALERTS` (default False)
    suppress Telegram send. Scan dalej iteruje + flag bag_time_alerted=True
    persisted (audit + R6 hard reject downstream w feasibility nadal działa) —
    tylko Telegram noise wyłączony. Hot-reload via flags.json.
    """
    if not C.flag("ENABLE_BAG_TIME_ALERTS", False):
        return  # alerts disabled per Adrian decision; scan no-op
    try:
        picked_up_orders = get_by_status("picked_up")
    except Exception as e:
        _log.error(f"R6 scan: get_by_status fail: {e}")
        return

    for order in picked_up_orders:
        oid = order.get("order_id") or "unknown"
        try:
            if order.get("bag_time_alerted", False):
                continue  # one-shot gate — already alerted

            picked_ts = order.get("picked_up_at")
            if not picked_ts:
                _log.warning(f"R6 skip {oid}: picked_up_at missing")
                continue

            picked_dt = _parse_aware_utc(picked_ts)
            if picked_dt is None:
                _log.warning(f"R6 skip {oid}: picked_up_at unparseable: {picked_ts!r}")
                continue

            bag_time_min = (now_utc - picked_dt).total_seconds() / 60.0
            if bag_time_min <= C.BAG_TIME_PRE_WARNING_MIN:
                continue

            # R-PACZKI-FLEX (2026-05-20): paczki nie mają termiki, brak alertu
            # "kurier wiezie >30min". Gdy ENABLE_BAG_TIME_ALERTS=True flipped
            # w przyszłości, paczki dalej suppress (jedzeniówki normalnie).
            if (C.ENABLE_R_PACZKI_FLEX or C.flag("ENABLE_R_PACZKI_FLEX", False)) and C.is_paczka_order(order):
                continue

            # Gate met. Set flag PRZED send (set-then-send, Opcja X).
            upsert_order(
                oid, {"bag_time_alerted": True}, event="R6_PRE_WARNING_ALERT"
            )

            cid = str(order.get("courier_id") or "?")
            cname = _courier_names.get(cid, cid)
            restaurant = order.get("restaurant") or "?"
            delivery = order.get("delivery_address") or "?"
            picked_hhmm = _format_picked_up_hhmm(picked_dt)

            msg = (
                f"⚠️ Kurier wiezie zamówienie już {bag_time_min:.0f} minut "
                f"(próg ostrzeżenia {C.BAG_TIME_PRE_WARNING_MIN} — niedługo "
                f"przekroczy max 35)\n"
                f"#{oid} {restaurant} → {delivery}\n"
                f"Kurier: {cname} ({cid}), odebrał o {picked_hhmm}\n\n"
                f"Co robię: nic automatycznie — to ostrzeżenie żebyś monitorował. "
                f"Jeśli przekroczy 35 min → R6 odrzuci propozycje dla tego kuriera "
                f"dopóki nie dostarczy."
            )
            ok = send_admin_alert(msg)
            _stats["r6_alerts"] += 1
            if ok:
                _log.warning(
                    f"R6 ALERT sent {oid} courier={cid} bag_time={bag_time_min:.1f}min"
                )
            else:
                _log.error(
                    f"R6 alert send FAILED for order {oid} — "
                    f"flag already set, alert LOST (bag_time={bag_time_min:.1f}min)"
                )
        except Exception as e:
            _log.error(
                f"R6 check failed for order {order.get('order_id','unknown')}: {e}"
            )
            continue  # next order, nie crashuj całego ticku


def _check_restaurant_violations() -> None:
    """ETAP 6 (Z-19): naruszenie RESTAURACJI = kurier był na czas, a jedzenie nie.

    Formuła (kontrakt ±5 min, Adrian 2026-06-10):
      wait_min = real_pickup (czas_odbioru_timestamp → orders_state.picked_up_at)
                 − max(commit (czas_kuriera_warsaw), przyjazd kuriera)
      violation gdy wait_min > RESTAURANT_VIOLATION_MAX_WAIT_MIN.

    Przyjazd kuriera: orders_state NIE persystuje wejścia w id_status=4
    (zwiad 2026-06-10 — wymagałoby edycji panel_watcher, gorąca ścieżka
    z WIP równoległej sesji). Forward-compat: gdy pole `waiting_at` kiedyś
    powstanie, zostanie użyte (arrival_source=status4); do tego czasu
    przyjazd = commit (arrival_source=commit_fallback) — bo max(commit,
    przyjazd) i tak obcina wcześniejszy przyjazd do commitu, fallback
    zawyża jedynie gdy kurier przyjechał PO commicie (naruszenie wtedy
    raportowane łagodniej dla restauracji — bezpieczny kierunek błędu).

    Wzorzec R6 _check_bag_time_alerts: skan per tick + persisted seen-flag
    (`restaurant_violation_logged`, set-then-write — duplicate-safe przez
    restart; przegrany append po udanym upsert = wpis stracony, widoczny
    w logu ERROR). Skan obejmuje picked_up ORAZ delivered (przejście
    picked_up→delivered między tickami nie gubi naruszenia; delivered żyją
    w orders_state do porannego prune). ZERO Telegrama (Adrian zarządza
    przez panel). Paczki pomijane (brak termiki/deadline'u restauracji).
    Flaga ENABLE_RESTAURANT_VIOLATIONS (default ON) hot-reload via flags.json.
    """
    if not C.flag("ENABLE_RESTAURANT_VIOLATIONS", True):
        return
    try:
        orders = get_by_status("picked_up") + get_by_status("delivered")
    except Exception as e:
        _log.error(f"restaurant_violations scan: get_by_status fail: {e}")
        return

    for order in orders:
        oid = order.get("order_id") or "unknown"
        try:
            if order.get("restaurant_violation_logged", False):
                continue  # one-shot gate — JEDEN wpis per oid

            real_ts = order.get("picked_up_at")
            commit_ts = order.get("czas_kuriera_warsaw")
            if not real_ts or not commit_ts:
                continue  # brak realnego odbioru albo commitu → nie da się ocenić

            if C.is_paczka_order(order):
                continue  # paczki bez termiki — kontrakt ±5 nie dotyczy

            real_dt = C.parse_panel_timestamp(real_ts)
            commit_dt = C.parse_panel_timestamp(commit_ts)
            if real_dt is None or commit_dt is None:
                _log.warning(
                    f"restaurant_violations skip {oid}: unparseable "
                    f"real={real_ts!r} commit={commit_ts!r}"
                )
                continue

            waiting_dt = C.parse_panel_timestamp(order.get("waiting_at"))
            if waiting_dt is not None:
                arrival_dt = max(commit_dt, waiting_dt)
                arrival_source = "status4"
            else:
                arrival_dt = commit_dt
                arrival_source = "commit_fallback"

            wait_min = (real_dt - arrival_dt).total_seconds() / 60.0
            if wait_min <= RESTAURANT_VIOLATION_MAX_WAIT_MIN:
                continue

            # set-then-write (wzorzec R6 Opcja X): flaga PRZED append.
            upsert_order(
                oid, {"restaurant_violation_logged": True},
                event="RESTAURANT_VIOLATION",
            )
            from zoneinfo import ZoneInfo
            _waw = ZoneInfo("Europe/Warsaw")
            record = {
                "ts": now_iso(),
                "order_id": oid,
                "restaurant": order.get("restaurant"),
                "committed_hhmm": commit_dt.astimezone(_waw).strftime("%H:%M"),
                "arrival_source": arrival_source,
                "real_pickup_hhmm": real_dt.astimezone(_waw).strftime("%H:%M"),
                "wait_min": round(wait_min, 1),
                "courier_id": str(order.get("courier_id") or "?"),
                "order_type": order.get("order_type"),
            }
            from dispatch_v2.core.jsonl_appender import append_jsonl
            append_jsonl(RESTAURANT_VIOLATIONS_PATH, record)
            _stats["restaurant_violations"] += 1
            _log.warning(
                f"RESTAURANT_VIOLATION {oid} {record['restaurant']} "
                f"wait={wait_min:.1f}min commit={record['committed_hhmm']} "
                f"real={record['real_pickup_hhmm']} src={arrival_source}"
            )
        except Exception as e:
            _log.error(
                f"restaurant_violations check failed for "
                f"{order.get('order_id', 'unknown')}: {type(e).__name__}: {e}"
            )
            continue  # next order, nie crashuj całego ticku


def run():
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    _log.info("SLA tracker START")
    last_summary = time.time()

    # F2.1b step 6: load courier_names cache once on start (zero IO per tick).
    global _courier_names
    _courier_names = _load_courier_names()
    _bag_alerts_state = "ENABLED" if C.flag("ENABLE_BAG_TIME_ALERTS", False) else "SUPPRESSED"
    _log.info(
        f"R6 bag_time alerts {_bag_alerts_state} — courier_names loaded: "
        f"{len(_courier_names)}, threshold={C.BAG_TIME_PRE_WARNING_MIN}min "
        f"(flag ENABLE_BAG_TIME_ALERTS hot-reload via flags.json)"
    )

    # A4.1 (2026-05-09): BroadcastSubscriber dla CONFIG_RELOAD events.
    _broadcast_sub = None
    try:
        _broadcast_sub = BroadcastSubscriber(
            consumer_id="sla_tracker",
            state_path=Path(
                "/root/.openclaw/workspace/dispatch_state/event_subscribers/sla_tracker.json"
            ),
        )
        _log.info("A4.1 BroadcastSubscriber init OK consumer=sla_tracker")
    except Exception as _bs_e:
        _log.warning(
            f"A4.1 BroadcastSubscriber init fail "
            f"({type(_bs_e).__name__}: {_bs_e}) — broadcast disabled"
        )
    last_broadcast_poll = 0.0
    BROADCAST_POLL_INTERVAL_S = 30.0

    # Sprint #37 v2 Phase B: stuck alert state + last_processed_ts tracking.
    # In-memory state, restart-clean (sustain_cycles=2 → false-positive immediate
    # post-restart prawie niemożliwy). _last_processed_ts updateowany po każdym
    # `mark_processed` (process(evt) zwrócił True).
    _sla_last_processed_ts = time.time()
    _sla_stuck_state = StuckAlertState()
    _sla_last_heartbeat = time.time()
    _log.info(
        f"Sprint #37 v2 Phase B: stuck alert config "
        f"consumer_id={_SLA_STUCK_CONFIG.consumer_id} "
        f"event_types={sorted(_SLA_STUCK_CONFIG.event_types)} "
        f"age_threshold={_SLA_STUCK_CONFIG.age_threshold_sec}s "
        f"pending_threshold={_SLA_STUCK_CONFIG.pending_threshold} "
        f"low_water={_SLA_STUCK_CONFIG.pending_low_water} "
        f"shadow_mode_only={_SLA_STUCK_CONFIG.shadow_mode_only}"
    )

    SLA_EVENT_TYPES = ["COURIER_PICKED_UP", "COURIER_DELIVERED"]
    while _running:
        # V3.28 #36 (2026-05-11): per-event isolation — pojedynczy poison message
        # (TZ TypeError, malformed payload, etc.) NIE blokuje konsumpcji reszty
        # kolejki. Pre-#36 jeden exception w `process(evt)` rzucał z forki przez
        # outer try → break iteration → `mark_processed` nie wywoływany dla evt →
        # następny tick ten sam evt head-of-queue → infinite poison loop, kolejka
        # pucha (201 eventów akumulowanych dziś przed fixem). Per-event try/except
        # + `mark_failed` na exception zachowuje audit trail + zwalnia kolejkę.
        try:
            _pending = get_pending(limit=200, event_types=SLA_EVENT_TYPES)
        except Exception as e:
            _log.error(f"loop get_pending: {e}")
            _pending = []
        for evt in _pending:
            _eid = evt.get("event_id")
            try:
                if process(evt):
                    mark_processed(_eid)
                    _sla_last_processed_ts = time.time()  # Sprint #37 v2: liveness signal
            except Exception as e:
                import traceback as _tb
                _log.error(
                    f"poison_msg event_id={_eid} oid={evt.get('order_id')} "
                    f"type={evt.get('event_type')}: {type(e).__name__}: {e}\n"
                    f"{_tb.format_exc()}"
                )
                # mark_failed → wyjęte z get_pending, audit visibility zachowany.
                try:
                    mark_failed(_eid, f"{type(e).__name__}: {e}")
                except Exception as _mf_e:
                    _log.error(f"mark_failed fail event_id={_eid}: {_mf_e}")

        # F2.1b step 6: R6 BAG_TIME scan per tick (outer safety net).
        try:
            _check_bag_time_alerts(datetime.now(timezone.utc))
        except Exception as e:
            _log.error(f"R6 scan wrapper fail: {e}")

        # ETAP 6 (Z-19): naruszenia restauracji ±5 min per tick (outer safety net).
        try:
            _check_restaurant_violations()
        except Exception as e:
            _log.error(f"restaurant_violations scan wrapper fail: {e}")

        # A4.1: poll CONFIG_RELOAD broadcast events co 30s rate-limited.
        if _broadcast_sub is not None and time.time() - last_broadcast_poll >= BROADCAST_POLL_INTERVAL_S:
            try:
                _new_events = _broadcast_sub.poll(["CONFIG_RELOAD"], limit=50)
                if _new_events:
                    dispatch_config_reload(_new_events, "sla_tracker")
            except Exception as _bp_e:
                _log.warning(
                    f"A4.1 broadcast poll fail "
                    f"({type(_bp_e).__name__}: {_bp_e}) — skip, retry next interval"
                )
            last_broadcast_poll = time.time()

        # Sprint #37 v2 Phase B: heartbeat tick + stuck alert evaluate.
        _sla_now = time.time()
        if _sla_now - _sla_last_heartbeat >= SLA_HEARTBEAT_INTERVAL_SEC:
            try:
                _sla_pending = get_pending_count(event_types=list(_SLA_STUCK_CONFIG.event_types))
            except Exception as _gpc_e:
                _log.warning(f"get_pending_count fail (non-blocking): {_gpc_e}")
                _sla_pending = 0
            _sla_snapshot = compute_heartbeat(
                last_processed_ts=_sla_last_processed_ts,
                now=_sla_now,
                pending=_sla_pending,
                config=_SLA_STUCK_CONFIG,
            )
            _log.info(
                f"HEARTBEAT stats={_stats} "
                f"pending_SLA={_sla_pending} "
                f"last_processed_age_sec={_sla_snapshot.age_sec:.0f} "
                f"worker_alive={_sla_snapshot.worker_alive} "
                f"is_stuck={_sla_snapshot.is_stuck} "
                f"is_recovered={_sla_snapshot.is_recovered}"
            )
            if _sla_snapshot.is_stuck:
                _log.critical(
                    f"SLA_TRACKER_STUCK age={_sla_snapshot.age_sec:.0f}s "
                    f"pending_SLA={_sla_pending} "
                    f"threshold_age={_SLA_STUCK_CONFIG.age_threshold_sec}s "
                    f"threshold_pending={_SLA_STUCK_CONFIG.pending_threshold}"
                )
            _state_before = _sla_stuck_state
            _sla_emit, _sla_kind, _sla_stuck_state = evaluate_stuck_alert(
                state=_state_before,
                snapshot=_sla_snapshot,
                now=_sla_now,
                config=_SLA_STUCK_CONFIG,
            )
            append_evaluation_log(
                snapshot=_sla_snapshot,
                state_before=_state_before,
                state_after=_sla_stuck_state,
                emit=_sla_emit,
                kind=_sla_kind,
                config=_SLA_STUCK_CONFIG,
                now=_sla_now,
            )
            # shadow_mode_only=True (calibration window) → log only, NIE Telegram.
            # Defensive try/except dla render+send (Lekcja #87/#110).
            if _sla_emit and not _SLA_STUCK_CONFIG.shadow_mode_only:
                try:
                    _msg = render_telegram_message(
                        kind=_sla_kind,
                        snapshot=_sla_snapshot,
                        state=_sla_stuck_state,
                        config=_SLA_STUCK_CONFIG,
                        now=_sla_now,
                    )
                    send_admin_alert(_msg)
                except Exception as _sa_e:
                    _log.error(
                        f"SLA_TRACKER_STUCK telegram alert fail "
                        f"({type(_sa_e).__name__}: {_sa_e}) — log only"
                    )
            elif _sla_emit and _SLA_STUCK_CONFIG.shadow_mode_only:
                _log.info(
                    f"SLA_TRACKER_STUCK shadow_mode_only=True — emit suppressed "
                    f"(kind={_sla_kind}, would-send: pending={_sla_pending})"
                )
            _sla_last_heartbeat = _sla_now

        if time.time() - last_summary > 300:
            _log.info(f"SUMMARY: {_stats}")
            last_summary = time.time()
        time.sleep(10)

    _log.info("SLA tracker STOP")


if __name__ == "__main__":
    run()
