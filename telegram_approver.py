"""telegram_approver - bot Telegram dla shadow proposals (Faza 1, D15).

Flow:
    shadow_decisions.jsonl  ─┐
                             ├→ [PROPOSE] → Telegram sendMessage(inline_kbd)
                             │
    long-poll getUpdates ←───┘
         └→ callback_query → gastro_assign (subprocess)
    watchdog → 5 min timeout → auto-KOORD

4 asyncio tasks:
    shadow_tailer    — ogon shadow_decisions.jsonl
    proposal_sender  — wysyłka inline-button propozycji
    updates_poller   — getUpdates long-polling
    watchdog         — 5-min timeout auto-KOORD

State:
    pending_proposals.json — atomic {order_id: {message_id, sent_at, expires_at, decision_record}}
    learning_log.jsonl     — append-only trail (TAK/NIE/INNY/KOORD/TIMEOUT)
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dispatch_v2.common import WARSAW, load_config, now_iso, parse_panel_timestamp, setup_logger


POLL_SHADOW_SEC = 3
PROPOSAL_TIMEOUT_SEC = 300  # 5 min → auto-KOORD
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"
PENDING_PATH = "/root/.openclaw/workspace/dispatch_state/pending_proposals.json"
LEARNING_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
GASTRO_ASSIGN_PATH = "/root/.openclaw/workspace/scripts/gastro_assign.py"

_log = setup_logger(
    "telegram_approver",
    "/root/.openclaw/workspace/scripts/logs/telegram_approver.log",
)
_shutdown = False


# ---- telegram env ----

def _load_env(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        _log.error(f"telegram env not found: {path}")
    return env


# ---- telegram HTTP (urllib — no external deps) ----

def tg_request(token: str, method: str, payload: Optional[dict] = None, timeout: int = 35) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---- formatting ----

COURIER_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
_courier_names_cache: Optional[Dict[str, str]] = None


def _load_courier_names() -> Dict[str, str]:
    """Lazy load + cache courier_names.json (F1.2).

    Shadow pipeline już populuje cs.name w dispatch_pipeline (z courier_resolver),
    ale tutaj mamy fallback gdy decision.best.name=None/brak.
    """
    global _courier_names_cache
    if _courier_names_cache is not None:
        return _courier_names_cache
    try:
        with open(COURIER_NAMES_PATH) as f:
            _courier_names_cache = json.load(f)
    except Exception as e:
        _log.warning(f"courier_names load fail: {e}")
        _courier_names_cache = {}
    return _courier_names_cache


def name_lookup(courier_id: Optional[str], existing_name: Optional[str]) -> str:
    if existing_name:
        return existing_name
    if courier_id:
        names = _load_courier_names()
        cached = names.get(str(courier_id))
        if cached:
            return cached
        return f"K{courier_id}"
    return "?"


def format_proposal(decision: dict) -> str:
    """Compact [PROPOZYCJA] enriched z F1.3 (km + ETA + delivery_address)."""
    oid = decision.get("order_id", "?")
    rest = decision.get("restaurant") or "?"
    delivery = decision.get("delivery_address") or "—"
    best = decision.get("best") or {}
    alts = decision.get("alternatives") or []
    best_effort = best.get("best_effort", False)

    courier = name_lookup(best.get("courier_id"), best.get("name"))
    score = best.get("score", 0)
    km = best.get("km_to_pickup")
    eta = best.get("eta_pickup_hhmm")

    # Main line: "Marek (0.87) — 2.1 km, ETA 19:38"
    main_bits = [f"{courier} ({score:.2f})"]
    if km is not None:
        main_bits.append(f"{km:.1f} km")
    if eta:
        main_bits.append(f"ETA {eta}")
    main_line = " — ".join([main_bits[0], ", ".join(main_bits[1:])]) if len(main_bits) > 1 else main_bits[0]

    alt_strs = []
    for a in alts[:3]:
        an = name_lookup(a.get("courier_id"), a.get("name"))
        a_score = a.get("score", 0)
        a_km = a.get("km_to_pickup")
        if a_km is not None:
            alt_strs.append(f"{an} ({a_score:.2f}, {a_km:.1f}km)")
        else:
            alt_strs.append(f"{an} ({a_score:.2f})")
    alt_line = " | ".join(alt_strs) if alt_strs else "—"

    banner = "⚠️ " if best_effort else ""
    header_tag = "[PROPOZYCJA best_effort]" if best_effort else "[PROPOZYCJA]"

    lines = [
        f"{header_tag} #{oid}",
        f"{rest}  →  {delivery}",
        "",
        f"🎯 {banner}{main_line}",
        f"🥈 {alt_line}",
        "",
        f"✓ {decision.get('reason','')}",
        "",
        "TAK / NIE / INNY / KOORD",
    ]
    return "\n".join(lines)


def build_keyboard(order_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ TAK", "callback_data": f"TAK:{order_id}"},
                {"text": "❌ NIE", "callback_data": f"NIE:{order_id}"},
            ],
            [
                {"text": "🔄 INNY", "callback_data": f"INNY:{order_id}"},
                {"text": "👤 KOORD", "callback_data": f"KOORD:{order_id}"},
            ],
        ],
    }


# ---- pending state ----

def load_pending(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"load_pending fail: {e}")
        return {}


def save_pending(path: str, pending: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(pending, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_learning(path: str, record: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---- gastro_assign subprocess ----

def round_up_to_5min(eta_minutes: Optional[float]) -> int:
    """ETA kuriera (min from now) → time_param dla gastro_assign.
    Zaokrąglenie w górę do 5, min 5, max 60. None → 5."""
    import math
    if eta_minutes is None:
        return 5
    try:
        m = float(eta_minutes)
    except (TypeError, ValueError):
        return 5
    t = int(math.ceil(m / 5.0) * 5)
    if t < 5:
        t = 5
    if t > 60:
        t = 60
    return t


def _prep_minutes_remaining(decision: dict) -> Optional[float]:
    """Z decision record → minuty od teraz do pickup_ready_at (gotowość jedzenia)."""
    iso = decision.get("pickup_ready_at")
    if not iso:
        return None
    try:
        ready = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None
    delta = (ready - datetime.now(timezone.utc)).total_seconds() / 60.0
    return max(0.0, delta)


def compute_assign_time(decision: dict) -> int:
    """time_param = max(round_up(eta_kuriera), round_up(prep_jedzenia)).
    Kurier nie spóźni się, restauracja zdąży."""
    best = decision.get("best") or {}
    eta_t = round_up_to_5min(best.get("travel_min"))
    prep_t = round_up_to_5min(_prep_minutes_remaining(decision))
    return max(eta_t, prep_t)


def run_gastro_assign(
    order_id: str,
    kurier_name: Optional[str],
    time_minutes: int = 0,
    koordynator: bool = False,
) -> Tuple[bool, str]:
    cmd = ["python3", GASTRO_ASSIGN_PATH, "--id", str(order_id)]
    if koordynator:
        cmd.append("--koordynator")
    elif kurier_name:
        cmd += ["--kurier", kurier_name, "--time", str(time_minutes)]
    else:
        return False, "no_target"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, (r.stdout.strip() or "ok")[-400:]
        return False, f"exit={r.returncode} {r.stderr.strip()[-400:]}"
    except subprocess.TimeoutExpired:
        return False, "subprocess_timeout"
    except Exception as e:
        return False, str(e)


# ---- async tasks ----

async def shadow_tailer(state: dict) -> None:
    path = state["shadow_log_path"]
    try:
        offset = Path(path).stat().st_size
    except FileNotFoundError:
        offset = 0
    _log.info(f"tailer start offset={offset} path={path}")
    while not _shutdown:
        try:
            if Path(path).exists():
                size = Path(path).stat().st_size
                if size < offset:
                    offset = 0  # rotated
                if size > offset:
                    with open(path) as f:
                        f.seek(offset)
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if rec.get("verdict") == "PROPOSE":
                                await state["incoming"].put(rec)
                        offset = f.tell()
        except Exception as e:
            _log.warning(f"tailer err: {e}")
        await asyncio.sleep(POLL_SHADOW_SEC)


async def proposal_sender(state: dict) -> None:
    while not _shutdown:
        try:
            rec = await asyncio.wait_for(state["incoming"].get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        oid = str(rec.get("order_id") or "")
        if not oid or oid in state["pending"]:
            continue
        text = format_proposal(rec)
        kbd = build_keyboard(oid)
        r = await asyncio.to_thread(
            tg_request, state["token"], "sendMessage",
            {
                "chat_id": state["admin_id"],
                "text": text,
                "reply_markup": kbd,
            },
        )
        if not r.get("ok"):
            _log.warning(f"sendMessage fail oid={oid}: {r.get('error') or r.get('description')}")
            continue
        message_id = r["result"]["message_id"]
        state["pending"][oid] = {
            "order_id": oid,
            "message_id": message_id,
            "sent_at": now_iso(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=PROPOSAL_TIMEOUT_SEC)).isoformat(),
            "decision_record": rec,
        }
        save_pending(state["pending_path"], state["pending"])
        _log.info(f"SENT oid={oid} msg={message_id}")


async def updates_poller(state: dict) -> None:
    offset = 0
    while not _shutdown:
        r = await asyncio.to_thread(
            tg_request, state["token"], "getUpdates",
            {"offset": offset, "timeout": 30}, 35,
        )
        if not r.get("ok"):
            _log.warning(f"getUpdates fail: {r.get('error') or r.get('description')}")
            await asyncio.sleep(5)
            continue
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1
            cb = upd.get("callback_query")
            msg = upd.get("message")
            try:
                if cb:
                    data = cb.get("data", "")
                    if ":" in data:
                        action, oid = data.split(":", 1)
                        await handle_callback(state, action, oid, cb)
                elif msg:
                    await handle_message(state, msg)
            except Exception as e:
                _log.error(f"update err: {e}")


# ---- message handlers (F1.4a /status) ----

SLA_LOG_PATH = "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"


def _today_warsaw_start_utc() -> datetime:
    """Start dnia (00:00 Warsaw) w UTC."""
    now_warsaw = datetime.now(WARSAW)
    start_warsaw = now_warsaw.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_warsaw.astimezone(timezone.utc)


def _yesterday_warsaw_range_utc():
    """Wczoraj 00:00 → dzis 00:00 Warsaw, w UTC (F1.6)."""
    today_start = _today_warsaw_start_utc()
    return today_start - timedelta(days=1), today_start


def _count_delivered_today(start_utc: datetime) -> int:
    """State orders delivered od początku dnia Warsaw."""
    from dispatch_v2 import state_machine
    count = 0
    for oid, o in state_machine.get_all().items():
        if o.get("status") != "delivered":
            continue
        d = o.get("delivered_at") or o.get("czas_doreczenia")
        dt = parse_panel_timestamp(d) if d else None
        if dt is not None and dt >= start_utc:
            count += 1
    return count


def _count_learning_today(path: str, start_utc: datetime) -> Counter:
    """Zlicz action w learning_log.jsonl od start_utc (Warsaw 00:00)."""
    counts: Counter = Counter()
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts_str = r.get("ts", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= start_utc:
                        counts[r.get("action", "?")] += 1
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return counts


def _count_learning_in_range(path: str, start_utc: datetime, end_utc: datetime) -> Counter:
    """Zlicz action w learning_log.jsonl w zakresie [start_utc, end_utc) (F1.6)."""
    counts: Counter = Counter()
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts_str = r.get("ts", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if start_utc <= ts < end_utc:
                        counts[r.get("action", "?")] += 1
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return counts


def _sla_records_in_range(path: str, start_utc: datetime, end_utc: datetime) -> list:
    """Zwraca listę sla_log records z logged_at w zakresie (F1.6)."""
    records = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts_str = r.get("logged_at", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if start_utc <= ts < end_utc:
                        records.append(r)
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return records


def _systemd_status() -> Dict[str, bool]:
    services = [
        "dispatch-panel-watcher",
        "dispatch-sla-tracker",
        "dispatch-shadow",
        "dispatch-telegram",
    ]
    result: Dict[str, bool] = {}
    for svc in services:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            result[svc] = (r.stdout.strip() == "active")
        except Exception:
            result[svc] = False
    return result


def format_status() -> str:
    """Build /status message body (F1.4a)."""
    from dispatch_v2 import state_machine

    now_warsaw = datetime.now(WARSAW)
    today_start_utc = _today_warsaw_start_utc()

    try:
        stats = state_machine.stats()
    except Exception:
        all_o = state_machine.get_all()
        c = Counter(o.get("status", "?") for o in all_o.values())
        stats = {"total": len(all_o), "by_status": dict(c), "active_per_courier": {}}

    by_status = stats.get("by_status", {}) or {}
    active_per_courier = stats.get("active_per_courier", {}) or {}

    svcs = _systemd_status()
    svc_names = [
        ("dispatch-panel-watcher", "watcher"),
        ("dispatch-sla-tracker",   "tracker"),
        ("dispatch-shadow",        "shadow"),
        ("dispatch-telegram",      "telegram"),
    ]
    svc_lines = [f"{'✅' if svcs.get(full) else '❌'} {short}" for full, short in svc_names]

    delivered_today = _count_delivered_today(today_start_utc)
    lc = _count_learning_today(LEARNING_LOG_PATH, today_start_utc)
    tak = lc.get("TAK", 0)
    nie = lc.get("NIE", 0)
    inny = lc.get("INNY", 0)
    koord = lc.get("KOORD", 0)
    timeout = lc.get("TIMEOUT", 0)
    total_proposals = tak + nie + inny + koord + timeout
    agreement_rate = (100 * tak / total_proposals) if total_proposals > 0 else 0.0

    active_couriers = len([v for v in active_per_courier.values() if v])

    lines = [
        f"🟢 Ziomek status ({now_warsaw.strftime('%H:%M')})",
        "",
        "Serwisy:",
    ]
    lines.extend(svc_lines)
    lines.append("")
    lines.append("Ordery (state):")
    for key in ("assigned", "picked_up", "planned", "delivered"):
        val = by_status.get(key, 0)
        lines.append(f"• {key}: {val}")
    lines.append("")
    lines.append(f"Fleet aktywny: {active_couriers}")
    lines.append("")
    lines.append("Dziś:")
    lines.append(f"• Delivered: {delivered_today}")
    lines.append(f"• Propozycje: {total_proposals}")
    lines.append(f"• Agreement: {tak}/{total_proposals} = {agreement_rate:.1f}%")
    if timeout > 0:
        lines.append(f"• Timeouts: {timeout}")

    # === WCZORAJ (F1.6 — zastępuje auto-briefing) ===
    yest_start, yest_end = _yesterday_warsaw_range_utc()
    yest_sla = _sla_records_in_range(SLA_LOG_PATH, yest_start, yest_end)
    yest_delivered = len(yest_sla)
    yest_lc = _count_learning_in_range(LEARNING_LOG_PATH, yest_start, yest_end)
    y_tak = yest_lc.get("TAK", 0)
    y_total = sum(v for k, v in yest_lc.items() if k in ("TAK", "NIE", "INNY", "KOORD", "TIMEOUT"))
    y_rate = (100 * y_tak / y_total) if y_total > 0 else 0.0

    lines.append("")
    lines.append("Wczoraj:")
    lines.append(f"• Delivered: {yest_delivered}")
    lines.append(f"• Propozycje: {y_total}")
    lines.append(f"• Agreement: {y_tak}/{y_total} = {y_rate:.1f}%")

    # Top 3 kurierów wczoraj (lazy import — unika circular z courier_ranking)
    if yest_delivered > 0:
        try:
            from dispatch_v2 import courier_ranking
            ranking = courier_ranking.compute_ranking(yest_sla)
            names = courier_ranking._load_courier_names()
            if ranking:
                lines.append("")
                lines.append("Top 3 wczoraj:")
                for i, r in enumerate(ranking[:3], start=1):
                    cname = names.get(r["courier_id"]) or f"K{r['courier_id']}"
                    lines.append(
                        f"{i}. {cname} — {r['deliveries']} dostaw | "
                        f"SLA {r['sla_pct']:.0f}% {courier_ranking._stars(r['sla_pct'])}"
                    )
        except Exception as e:
            _log.warning(f"ranking fail: {e}")
            lines.append("")
            lines.append("Top 3 wczoraj: (ranking error)")

    return "\n".join(lines)


async def handle_message(state: dict, msg: dict) -> None:
    """Handle text messages: /status + free-text manual courier overrides.

    Autoryzacja po chat.id (grupa) — każdy member grupy może pisać.
    """
    chat_id = str(((msg.get("chat") or {}).get("id") or ""))
    from_id = str((msg.get("from") or {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if chat_id != str(state["admin_id"]):
        _log.warning(f"message from unauthorized chat_id={chat_id} user={from_id}")
        return

    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd == "/status":
            try:
                body = await asyncio.to_thread(format_status)
            except Exception as e:
                _log.exception("format_status failed")
                body = f"❌ status error: {type(e).__name__}: {e}"
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {"chat_id": state["admin_id"], "text": body},
            )
            _log.info(f"/status responded to admin={from_id}")
        return

    # Free-text → manual courier overrides
    from dispatch_v2 import manual_overrides
    action, response = await asyncio.to_thread(manual_overrides.parse_command, text)
    if not response:
        return
    await asyncio.to_thread(
        tg_request, state["token"], "sendMessage",
        {"chat_id": state["admin_id"], "text": response},
    )
    _log.info(f"override action={action} text={text!r}")


async def handle_callback(state: dict, action: str, oid: str, cb: dict) -> None:
    entry = state["pending"].get(oid)
    token = state["token"]
    if entry is None:
        await asyncio.to_thread(
            tg_request, token, "answerCallbackQuery",
            {"callback_query_id": cb["id"], "text": f"Unknown order #{oid}"},
        )
        return

    rec = entry["decision_record"]
    best = rec.get("best") or {}
    courier_name = name_lookup(best.get("courier_id"), best.get("name"))

    ok = False
    if action == "TAK":
        time_min = compute_assign_time(rec)
        ok, msg = await asyncio.to_thread(run_gastro_assign, oid, courier_name, time_min, False)
        feedback = f"✅ {courier_name} ({time_min}m)" if ok else f"❌ assign: {msg[:80]}"
    elif action == "NIE":
        ok, feedback = True, "⏭ pozostaje w puli"
    elif action == "INNY":
        # MVP: just ack — full flow (follow-up message with kurier_id) is TODO
        ok, feedback = True, "🔄 INNY (MVP: wpisz ręcznie w panel lub wyślij ponownie)"
    elif action == "KOORD":
        ok, msg = await asyncio.to_thread(run_gastro_assign, oid, None, 0, True)
        feedback = "👤 KOORD" if ok else f"❌ koord: {msg[:80]}"
    else:
        feedback = f"unknown {action}"

    await asyncio.to_thread(
        tg_request, token, "answerCallbackQuery",
        {"callback_query_id": cb["id"], "text": feedback},
    )
    # Strip buttons from original message so it can't be clicked twice
    await asyncio.to_thread(
        tg_request, token, "editMessageReplyMarkup",
        {
            "chat_id": state["admin_id"],
            "message_id": entry["message_id"],
            "reply_markup": {"inline_keyboard": []},
        },
    )

    append_learning(state["learning_log_path"], {
        "ts": now_iso(),
        "order_id": oid,
        "action": action,
        "ok": ok,
        "feedback": feedback,
        "decision": rec,
    })
    state["pending"].pop(oid, None)
    save_pending(state["pending_path"], state["pending"])
    _log.info(f"CB {action} oid={oid} → {feedback}")


async def watchdog(state: dict) -> None:
    while not _shutdown:
        now = datetime.now(timezone.utc)
        expired = []
        for oid, entry in list(state["pending"].items()):
            try:
                exp = datetime.fromisoformat(entry["expires_at"])
            except Exception:
                continue
            if now >= exp:
                expired.append(oid)
        for oid in expired:
            entry = state["pending"][oid]
            _log.warning(f"TIMEOUT oid={oid} → brak odpowiedzi, zlecenie pozostaje w puli")
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {
                    "chat_id": state["admin_id"],
                    "text": f"⏰ Timeout #{oid} (5 min) → brak decyzji, zlecenie w puli",
                },
            )
            append_learning(state["learning_log_path"], {
                "ts": now_iso(),
                "order_id": oid,
                "action": "TIMEOUT_SKIP",
                "ok": True,
                "feedback": "brak decyzji w czasie — zlecenie pozostaje w puli",
                "decision": entry["decision_record"],
            })
            state["pending"].pop(oid, None)
            save_pending(state["pending_path"], state["pending"])
        await asyncio.sleep(10)


# ---- main ----

async def main_async() -> None:
    cfg = load_config()
    env = _load_env(TELEGRAM_ENV_PATH)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    admin_id = str(cfg["telegram"]["admin_id"])

    if not token or token == "PLACEHOLDER":
        _log.error(
            "TELEGRAM_BOT_TOKEN missing / PLACEHOLDER — "
            "approver WILL NOT SEND until real token is set in .secrets/telegram.env"
        )

    state = {
        "token": token,
        "admin_id": admin_id,
        "shadow_log_path": cfg["paths"]["shadow_log"],
        "pending_path": PENDING_PATH,
        "learning_log_path": LEARNING_LOG_PATH,
        "incoming": asyncio.Queue(),
        "pending": load_pending(PENDING_PATH),
    }

    _log.info(
        f"telegram_approver START admin={admin_id} "
        f"pending={len(state['pending'])} token={'SET' if token and token != 'PLACEHOLDER' else 'MISSING'}"
    )

    await asyncio.gather(
        shadow_tailer(state),
        proposal_sender(state),
        updates_poller(state),
        watchdog(state),
    )


def _sigterm(signum, frame):
    global _shutdown
    _log.info(f"signal {signum} → shutdown")
    _shutdown = True


def run() -> int:
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
    _log.info("telegram_approver STOP")
    return 0


if __name__ == "__main__":
    sys.exit(run())
