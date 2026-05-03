"""V3.24-B Czasówka Emit Scheduler (design: /tmp/v324b_design.md).

Standalone module wywoływany przez systemd timer co 1 min.
Skanuje orders_state dla czasówek (czas_odbioru ≥ 60 min + id_kurier=26),
ewaluuje per-order z gradient selectivity 60→50→40, decyduje:
  DONT_EMIT | WAIT | EMIT | FORCE_ASSIGN | KOORD

B10 scope: logic + state tracking + eval log.
B11 scope: emit integration (shadow_decisions write + telegram alert/proposal).

Flag-gated: ENABLE_V324B_CZASOWKA_SCHEDULER=False → no-op early return.
"""
import fcntl
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Standalone-friendly import path (mirror panel_watcher/shadow_dispatcher convention)
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C
from dispatch_v2 import courier_resolver, event_bus, state_machine
from dispatch_v2.dispatch_pipeline import assess_order
from dispatch_v2.telegram_approver import tg_request

WARSAW = ZoneInfo("Europe/Warsaw")

STATE_PATH = Path("/root/.openclaw/workspace/dispatch_state/czasowka_eval_state.json")
STATE_LOCK = Path("/root/.openclaw/workspace/dispatch_state/czasowka_eval_state.lock")
LOG_PATH = Path("/root/.openclaw/workspace/dispatch_state/czasowka_eval_log.jsonl")

# Alert target: grupa ziomka (Adrian + Bartek), NIE DM Adriana.
# config.telegram.admin_id już jest = -5149910559 (potwierdzone w config.json).
# Przenoszę inline dla safety; resolver zawsze woli wartość z config.
ALERT_GROUP_CHAT_ID_FALLBACK = -5149910559

MAX_STATE_AGE_HOURS = 2
KOORDYNATOR_CID = "26"

# V3.28 Fix 8 (incident 03.05.2026): czasówki visibility bug fix + 3 safety mechanisms.
# Pre-fix: czasowka_scheduler ZAWSZE widział 0 czasówek bo line 441 użyła
# `state_machine.get_all().get("orders", {})` ALE state_machine zwraca FLAT dict.
# Module live od 22.04 (V3.24-B deploy) — silent dead code 12+ dni.
#
# Fix 8.A: bug fix (1-line) — usunąć .get("orders", {}) wrapper
# Fix 8.B: dryrun mode dla Telegram (default ON — first deploy = NIE wysyłaj real alerts)
# Fix 8.C: retroactive filter — skip stale orders_state legacy entries (cutoff = N hours)
# Fix 8.D: per-tick rate limit emit/koord (Bartek MUSI mieć window do review pierwszych)
DRYRUN_MODE = os.environ.get("CZASOWKA_TELEGRAM_DRYRUN", "1") == "1"  # SAFE default ON
RETROACTIVE_HOURS = int(os.environ.get("CZASOWKA_RETROACTIVE_HOURS", "2"))
MAX_EMIT_PER_TICK = int(os.environ.get("CZASOWKA_MAX_EMIT_PER_TICK", "3"))


def _resolve_alert_chat_id() -> int:
    try:
        cfg = C.load_config()
        return int((cfg.get("telegram") or {}).get("admin_id") or ALERT_GROUP_CHAT_ID_FALLBACK)
    except Exception:
        return ALERT_GROUP_CHAT_ID_FALLBACK


def _resolve_bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")

_log = C.setup_logger(
    "czasowka_scheduler",
    "/root/.openclaw/workspace/scripts/logs/czasowka.log",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------- State file I/O (atomic) ----------

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"orders": {}, "updated_at": None}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _log.warning(f"state load failed: {e} — starting fresh")
        return {"orders": {}, "updated_at": None}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_utc().isoformat()
    # fcntl lock on companion file (plan_manager pattern)
    with open(STATE_LOCK, "a+") as lockfh:
        fcntl.flock(lockfh.fileno(), fcntl.LOCK_EX)
        tmp = str(STATE_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(STATE_PATH))


def _cleanup_stale(state: dict, now: datetime) -> int:
    """Remove entries z last_eval_ts > MAX_STATE_AGE_HOURS."""
    cutoff = now - timedelta(hours=MAX_STATE_AGE_HOURS)
    orders = state.setdefault("orders", {})
    to_remove = []
    for oid, info in orders.items():
        try:
            last = datetime.fromisoformat(
                (info.get("last_eval_ts") or "").replace("Z", "+00:00")
            )
        except Exception:
            to_remove.append(oid)
            continue
        if last < cutoff:
            to_remove.append(oid)
    for oid in to_remove:
        del orders[oid]
    return len(to_remove)


# ---------- Order classification ----------

def _is_czasowka(order_state: dict) -> bool:
    """Czasówka = prep_minutes >= 60 AND held by Koordynator (id_kurier=26)."""
    prep = order_state.get("prep_minutes")
    if prep is None or prep < 60:
        return False
    cid = str(order_state.get("courier_id") or "")
    # id_kurier=26 (Koordynator) OR not yet assigned
    return cid == KOORDYNATOR_CID or cid == "" or cid == "None"


def _minutes_to_pickup(order_state: dict, now_utc: datetime) -> float:
    """Parse pickup_at_warsaw (Warsaw-aware) → minutes until pickup."""
    ts_raw = (
        order_state.get("pickup_at_warsaw")
        or order_state.get("czas_odbioru_timestamp")
    )
    if not ts_raw:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        # pickup_at_warsaw w panel raw = Warsaw naive (per CLAUDE.md)
        dt = dt.replace(tzinfo=WARSAW)
    return (dt - now_utc).total_seconds() / 60.0


def _classify_match(metrics: dict) -> str:
    """ideal | good | none per V3.24-B thresholds."""
    km = metrics.get("km_to_pickup")
    drop_prox = metrics.get("v319h_bug1_drop_proximity_factor")
    if km is None:
        # pre_shift kurier (brak km) nie kwalifikuje się do match quality
        return "none"
    if (km <= C.V324B_CZASOWKA_IDEAL_KM_MAX
            and drop_prox is not None
            and drop_prox >= C.V324B_CZASOWKA_IDEAL_DROP_PROX_MIN):
        return "ideal"
    if km <= C.V324B_CZASOWKA_GOOD_KM_MAX:
        return "good"
    if drop_prox is not None and drop_prox >= C.V324B_CZASOWKA_GOOD_DROP_PROX_MIN:
        return "good"
    return "none"


def _early_morning_blocked(now_warsaw: datetime) -> bool:
    """now_warsaw < 9:10 → block all emits."""
    cutoff = now_warsaw.replace(
        hour=C.OPERATION_EMIT_NOT_BEFORE_HOUR_WARSAW,
        minute=C.OPERATION_EMIT_NOT_BEFORE_MIN_WARSAW,
        second=0,
        microsecond=0,
    )
    return now_warsaw < cutoff


# ---------- Core eval ----------

def eval_czasowka(order_id: str, order_state: dict, now_utc: datetime) -> dict:
    """Return {decision, reason, minutes_to_pickup, match_quality, best, alternatives}.

    decision ∈ {DONT_EMIT, WAIT, EMIT, FORCE_ASSIGN, KOORD, SKIP}
    SKIP = no pickup_at_warsaw (dane niekompletne, bez klasyfikacji).
    """
    now_warsaw = now_utc.astimezone(WARSAW)
    if _early_morning_blocked(now_warsaw):
        return {
            "decision": "DONT_EMIT",
            "reason": f"before 09:10 Warsaw (now {now_warsaw.strftime('%H:%M')})",
            "minutes_to_pickup": None,
            "match_quality": None,
            "best": None,
            "alternatives": [],
        }

    mins = _minutes_to_pickup(order_state, now_utc)
    if mins is None:
        return {
            "decision": "SKIP",
            "reason": "no_pickup_timestamp",
            "minutes_to_pickup": None,
            "match_quality": None,
            "best": None,
            "alternatives": [],
        }

    if mins > C.V324B_CZASOWKA_EVAL_START_MIN:
        return {
            "decision": "WAIT",
            "reason": f"outside eval window ({mins:.1f}min > {C.V324B_CZASOWKA_EVAL_START_MIN})",
            "minutes_to_pickup": mins,
            "match_quality": None,
            "best": None,
            "alternatives": [],
        }

    # Build order_event dict w format którego oczekuje assess_order (mirror panel_watcher emit).
    order_event = {
        "order_id": order_id,
        "restaurant": order_state.get("restaurant"),
        "pickup_address": order_state.get("pickup_address"),
        "pickup_city": order_state.get("pickup_city"),
        "delivery_address": order_state.get("delivery_address"),
        "delivery_city": order_state.get("delivery_city"),
        "pickup_at_warsaw": order_state.get("pickup_at_warsaw"),
        "prep_minutes": order_state.get("prep_minutes"),
        "order_type": order_state.get("order_type"),
        "status_id": order_state.get("status_id", 2),
        "first_seen": order_state.get("first_seen"),
        "address_id": order_state.get("address_id"),
        "pickup_coords": order_state.get("pickup_coords"),
        "delivery_coords": order_state.get("delivery_coords"),
        "czas_kuriera_warsaw": order_state.get("czas_kuriera_warsaw"),
        "czas_kuriera_hhmm": order_state.get("czas_kuriera_hhmm"),
    }

    fleet_snapshot = courier_resolver.build_fleet_snapshot()
    result = assess_order(order_event, fleet_snapshot, now=now_utc)

    best = result.best
    best_maybe = best is not None and best.feasibility_verdict == "MAYBE"
    match_q = _classify_match(best.metrics) if best else "none"

    # FORCE_ASSIGN window: mins ≤ 40
    if mins <= C.V324B_CZASOWKA_FORCE_ASSIGN_MIN:
        if best_maybe:
            return {
                "decision": "FORCE_ASSIGN",
                "reason": f"{mins:.1f}min ≤ {C.V324B_CZASOWKA_FORCE_ASSIGN_MIN} — force top MAYBE",
                "minutes_to_pickup": mins,
                "match_quality": match_q,
                "best": best,
                "alternatives": result.candidates[1:] if result.candidates else [],
            }
        return {
            "decision": "KOORD",
            "reason": f"≤{C.V324B_CZASOWKA_FORCE_ASSIGN_MIN}min + zero MAYBE candidates",
            "minutes_to_pickup": mins,
            "match_quality": "none",
            "best": best,
            "alternatives": result.candidates[1:] if result.candidates else [],
        }

    # 40 < mins ≤ 60 window
    if not best_maybe:
        return {
            "decision": "WAIT",
            "reason": "no MAYBE candidate",
            "minutes_to_pickup": mins,
            "match_quality": "none",
            "best": None,
            "alternatives": [],
        }

    # 50 < mins ≤ 60 → EMIT only on ideal
    if mins > (C.V324B_CZASOWKA_EVAL_START_MIN - 10):
        if match_q == "ideal":
            return {
                "decision": "EMIT",
                "reason": f"ideal match ({mins:.1f}min in 60-50 window)",
                "minutes_to_pickup": mins,
                "match_quality": match_q,
                "best": best,
                "alternatives": result.candidates[1:],
            }
        return {
            "decision": "WAIT",
            "reason": f"no ideal match ({mins:.1f}min in 60-50, quality={match_q})",
            "minutes_to_pickup": mins,
            "match_quality": match_q,
            "best": best,
            "alternatives": result.candidates[1:],
        }

    # 40 < mins ≤ 50 → EMIT on ideal OR good
    if match_q in ("ideal", "good"):
        return {
            "decision": "EMIT",
            "reason": f"{match_q} match ({mins:.1f}min in 50-40 window)",
            "minutes_to_pickup": mins,
            "match_quality": match_q,
            "best": best,
            "alternatives": result.candidates[1:],
        }
    return {
        "decision": "WAIT",
        "reason": f"no good+ match ({mins:.1f}min in 50-40, quality={match_q})",
        "minutes_to_pickup": mins,
        "match_quality": match_q,
        "best": best,
        "alternatives": result.candidates[1:],
    }


def _append_eval_log(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _emit_to_event_bus(oid: str, order_state: dict, result: dict, eval_count: int) -> None:
    """Emit NEW_ORDER event z unique suffix event_id ({oid}_CZASOWKA_EVAL_{n}).

    Shadow_dispatcher picks up przez event_bus polling (same path co panel_watcher).
    Idempotent: event_bus.emit returns None gdy event_id już processed.
    """
    event_id = f"{oid}_CZASOWKA_EVAL_{eval_count}"
    payload = {
        "v324b_trigger": True,
        "v324b_decision": result["decision"],
        "v324b_minutes_to_pickup": result["minutes_to_pickup"],
        "v324b_match_quality": result["match_quality"],
        "v324b_force": (result["decision"] == "FORCE_ASSIGN"),
        # Mirror panel_watcher NEW_ORDER payload (shadow_dispatcher consumer shape).
        "restaurant": order_state.get("restaurant"),
        "pickup_address": order_state.get("pickup_address"),
        "pickup_city": order_state.get("pickup_city"),
        "delivery_address": order_state.get("delivery_address"),
        "delivery_city": order_state.get("delivery_city"),
        "pickup_at_warsaw": order_state.get("pickup_at_warsaw"),
        "prep_minutes": order_state.get("prep_minutes"),
        "order_type": order_state.get("order_type"),
        "status_id": order_state.get("status_id", 2),
        "first_seen": order_state.get("first_seen"),
        "address_id": order_state.get("address_id"),
        "pickup_coords": order_state.get("pickup_coords"),
        "delivery_coords": order_state.get("delivery_coords"),
        "czas_kuriera_warsaw": order_state.get("czas_kuriera_warsaw"),
        "czas_kuriera_hhmm": order_state.get("czas_kuriera_hhmm"),
    }
    emitted = event_bus.emit(
        "NEW_ORDER",
        order_id=oid,
        payload=payload,
        event_id=event_id,
    )
    if emitted:
        _log.info(f"event_bus emit {event_id} decision={result['decision']}")
    else:
        _log.debug(f"event_bus dup/skip {event_id}")


def _format_koord_alert(oid: str, order_state: dict, result: dict) -> str:
    """Alert text per Adrian spec (multi-line z Top 3 odrzuconych)."""
    mins = result.get("minutes_to_pickup")
    pickup_ts = order_state.get("pickup_at_warsaw") or "?"
    try:
        pickup_hhmm = datetime.fromisoformat(
            str(pickup_ts).replace("Z", "+00:00")
        ).astimezone(WARSAW).strftime("%H:%M")
    except Exception:
        pickup_hhmm = str(pickup_ts)

    lines = [
        "🚨 BRAK KANDYDATÓW (czasówka)",
        f"Order: {oid}",
        f"Restauracja: {order_state.get('restaurant') or '?'}",
        f"Czas odbioru: {pickup_hhmm}",
        f"Minut do pickupu: {int(mins) if mins is not None else '?'}",
        f"Powód: {result.get('reason') or 'brak feasible MAYBE kandydatów'}",
    ]

    alts = result.get("alternatives") or []
    # Filter NO candidates (MAYBE gave zero or insufficient; alternatives here are all feasibility=NO)
    rejected = [c for c in alts if getattr(c, "feasibility_verdict", None) == "NO"]
    # Fallback: jeśli best też NO, dodaj go
    best = result.get("best")
    if best is not None and getattr(best, "feasibility_verdict", None) == "NO":
        rejected = [best] + rejected
    # Sort by score desc (top rejected = "najbliższy feasible, ale blokowany")
    rejected.sort(key=lambda c: -(c.score or -1e9))

    if rejected:
        lines.append("Top 3 odrzuconych:")
        for c in rejected[:3]:
            cid = getattr(c, "courier_id", "?")
            name = getattr(c, "name", None) or "?"
            reason = getattr(c, "feasibility_reason", None) or "?"
            lines.append(f"- cid={cid} {name}: {reason}")
    return "\n".join(lines)


def _send_koord_alert(oid: str, order_state: dict, result: dict) -> None:
    token = _resolve_bot_token()
    if not token:
        _log.warning(f"KOORD alert SKIPPED (TELEGRAM_BOT_TOKEN empty) oid={oid}")
        return
    chat_id = _resolve_alert_chat_id()
    text = _format_koord_alert(oid, order_state, result)
    try:
        r = tg_request(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout=15)
        if r.get("ok"):
            _log.info(f"KOORD alert sent oid={oid} chat={chat_id}")
        else:
            _log.warning(f"KOORD alert tg failed oid={oid}: {r.get('description') or r.get('error')}")
    except Exception as e:
        _log.warning(f"KOORD alert exception oid={oid}: {e}")


def _interval_gate_blocks(info: dict, now_utc: datetime) -> bool:
    """Re-eval gated by V324B_CZASOWKA_EVAL_INTERVAL_MIN since last_eval_ts."""
    if not info:
        return False
    try:
        last = datetime.fromisoformat(
            info.get("last_eval_ts", "").replace("Z", "+00:00")
        )
    except Exception:
        return False
    mins_since = (now_utc - last).total_seconds() / 60.0
    return mins_since < C.V324B_CZASOWKA_EVAL_INTERVAL_MIN


# ---------- Main loop ----------

def main() -> int:
    if not C.ENABLE_V324B_CZASOWKA_SCHEDULER:
        _log.debug("flag OFF — no-op")
        return 0

    now_utc = _now_utc()
    state = _load_state()
    cleaned = _cleanup_stale(state, now_utc)
    if cleaned:
        _log.info(f"cleaned {cleaned} stale state entries")

    # V3.28 Fix 8.A (incident 03.05.2026): bug fix — state_machine.get_all() returns
    # FLAT dict {order_id: dict}, NIE wrapped {"orders": {...}}. Pre-fix .get("orders", {})
    # zwracało {} ZAWSZE → 0 czasówek processed od V3.24-B deploy 22.04.
    all_orders_state = state_machine.get_all()

    # V3.28 Fix 8.C: retroactive filter — skip stale legacy orders_state entries.
    # Cutoff default 2h (CZASOWKA_RETROACTIVE_HOURS env override). Prevents trigger
    # scheduler na orders_state legacy entries (delivered/cancelled, ale state stale).
    # Ground truth timestamp: first_seen (UTC ISO), fallback updated_at, skip jeśli oba None.
    cutoff = now_utc - timedelta(hours=RETROACTIVE_HOURS)
    filtered_orders_state = {}
    parse_errors = 0
    for oid, osrec in all_orders_state.items():
        ts_str = osrec.get("first_seen") or osrec.get("updated_at")
        if not ts_str:
            continue  # skip legacy orders bez timestamp
        try:
            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts > cutoff:
                filtered_orders_state[oid] = osrec
        except Exception as e:
            parse_errors += 1
            if parse_errors <= 3:  # log first 3, suppress rest
                _log.warning(f"V328_CZASOWKA_FILTER_PARSE_ERR oid={oid} ts={ts_str!r} err={e}")
            continue

    _log.info(
        f"V328_CZASOWKA_FILTER total_state={len(all_orders_state)} "
        f"after_cutoff={len(filtered_orders_state)} cutoff_h={RETROACTIVE_HOURS} "
        f"parse_errors={parse_errors}"
    )

    czasowki = {oid: osrec for oid, osrec in filtered_orders_state.items()
                if _is_czasowka(osrec)}
    _log.info(f"tick: {len(czasowki)} czasówek w orders_state (now {now_utc.isoformat()})")

    stats = {"eval": 0, "skip_interval": 0, "emit": 0, "force": 0, "koord": 0, "wait": 0, "dont_emit": 0, "skip": 0}

    # V3.28 Fix 8.D: per-tick rate limit emit/koord. Bartek MUSI mieć window do
    # review pierwszych przejmowanych czasówek (Lekcja Bartek validation gate).
    emit_count = 0
    emit_pending_oids = []

    for oid, osrec in czasowki.items():
        info = state["orders"].get(oid)
        if _interval_gate_blocks(info, now_utc):
            stats["skip_interval"] += 1
            continue

        # Rate limit guard PRZED eval (NIE marnować eval gdy nie wyemujemy)
        # Eval still runs dla state tracking, ale emit/koord skip
        result = eval_czasowka(oid, osrec, now_utc)
        stats["eval"] += 1
        stats[result["decision"].lower().replace("force_assign", "force").replace("dont_emit", "dont_emit")] = \
            stats.get(result["decision"].lower().replace("force_assign", "force").replace("dont_emit", "dont_emit"), 0) + 1

        # Update state (eval_count increments per re-eval)
        prev_count = info.get("eval_count", 0) if info else 0
        state["orders"][oid] = {
            "last_eval_ts": now_utc.isoformat(),
            "last_decision": result["decision"],
            "eval_count": prev_count + 1,
            "last_match_quality": result["match_quality"],
        }

        # Log every eval (observational)
        _append_eval_log({
            "ts": now_utc.isoformat(),
            "order_id": oid,
            "decision": result["decision"],
            "reason": result["reason"],
            "minutes_to_pickup": result["minutes_to_pickup"],
            "match_quality": result["match_quality"],
            "best_courier_id": (result["best"].courier_id if result["best"] else None),
            "best_score": (result["best"].score if result["best"] else None),
            "eval_count": prev_count + 1,
        })

        # B11 emit paths z V3.28 Fix 8.B (dryrun) + Fix 8.D (rate limit)
        is_emit_decision = result["decision"] in ("EMIT", "FORCE_ASSIGN", "KOORD")
        if is_emit_decision and emit_count >= MAX_EMIT_PER_TICK:
            emit_pending_oids.append(oid)
            continue

        if result["decision"] in ("EMIT", "FORCE_ASSIGN"):
            if DRYRUN_MODE:
                best_cid = result["best"].courier_id if result["best"] else None
                _log.info(
                    f"V328_CZASOWKA_DRYRUN would_emit oid={oid} verdict={result['decision']} "
                    f"courier={best_cid} reason={result['reason']!r}"
                )
            else:
                # Shadow_dispatcher konsumuje event z unique event_id suffix.
                _emit_to_event_bus(oid, osrec, result, prev_count + 1)
            emit_count += 1
        elif result["decision"] == "KOORD":
            if DRYRUN_MODE:
                _log.info(
                    f"V328_CZASOWKA_DRYRUN would_koord_alert oid={oid} reason={result['reason']!r}"
                )
            else:
                # Alert do grupy ziomka (Adrian + Bartek).
                _send_koord_alert(oid, osrec, result)
            emit_count += 1

    if emit_pending_oids:
        _log.info(
            f"V328_CZASOWKA_TICK_LIMIT max_per_tick={MAX_EMIT_PER_TICK} "
            f"deferred={len(emit_pending_oids)} oids={emit_pending_oids[:5]}"
        )

    _save_state(state)
    _log.info(f"tick done: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
