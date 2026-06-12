"""AUTON-01 — egzekutor auto-assign (szkielet ZA FLAGĄ, default OFF).

Projekt: eod_drafts/2026-06-13/AUTON01_DESIGN.md (sekcja 5).

Wołany WYŁĄCZNIE z shadow_dispatcher (po zapisie decyzji do shadow_decisions) —
NIE z dispatch_pipeline, żeby procesy czasówki / plan-recheck (re-decyzje)
nigdy nie wykonywały przypisań.

Kontrakt bezpieczeństwa:
  1. `ENABLE_AUTO_ASSIGN` (kanon ETAP4 flags.json, default false) = killswitch
     hot-reload. Przy OFF pierwsza linia robi return None — ZERO pracy, zero I/O.
  2. Bramka jakościowa = result.would_auto_assign (auto_assign_gate, czysta).
  3. Bezpieczniki stanowe nakładane TUTAJ w chwili wykonania:
     - rate-cap: max AUTO_ASSIGN_MAX_PER_HOUR wykonań / 60 min (state file),
     - cooldown: PANEL_OVERRIDE na tym kurierze < AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN
       temu (tail-scan learning_log, wzorzec _check_panel_agree).
  4. Mechanizm wykonania = subprocess scripts/gastro_assign.py — identyczna
     ścieżka jak ASSIGN_DIRECT z telegram_approver (jedyna przetestowana bojowo),
     bez importu telegram_approver i bez dotykania demona dispatch-telegram.
  5. Notyfikacja post-hoc = telegram_utils.send_admin_alert (informacja, nie
     pytanie); propozycja do koordynatora i tak idzie normalną ścieżką.
  6. Fail-safe: każdy wyjątek połknięty z WARN — egzekutor NIGDY nie może
     zakłócić pętli shadow.
  7. Obrona przed testami (klasa lekcji #75/#180): default runner subprocess,
     zapis state i learning_log odmawiają pod PYTEST_CURRENT_TEST — testy
     wstrzykują assign_runner/notifier i patchują ścieżki.

⚠ Realny assign NIGDY nie przeszedł E2E (matchowanie nazwy kuriera w panelu
gastro). Pierwsze wykonanie = osobny krok z Adrianem w dzień, na zleceniu
kontrolowanym, PO flipie progów z E7.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from dispatch_v2 import common as C

log = logging.getLogger("auto_assign_executor")

GASTRO_ASSIGN_PATH = "/root/.openclaw/workspace/scripts/gastro_assign.py"
STATE_PATH = "/root/.openclaw/workspace/dispatch_state/auto_assign_state.json"
LEARNING_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
LEARNING_LOG_TAIL_BYTES = 262144  # wzorzec _PANEL_AGREE_TAIL_BYTES


def _numeric(name: str) -> float:
    """Stała: flags.json (hot) → stała modułu common (FLAGS_JSON_NUMERIC_OVERRIDES)."""
    try:
        fl = C.load_flags()
    except Exception:
        fl = {}
    try:
        return float(fl.get(name, getattr(C, name)))
    except (TypeError, ValueError):
        return float(getattr(C, name))


def _pytest_active() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


# ---------------- stan rate-capu ----------------

def _load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"executed": []}


def _save_state(path: str, state: Dict[str, Any]) -> None:
    """Atomic write (temp+rename). Odmawia pod pytest (ochrona prod state)."""
    if _pytest_active() and not os.environ.get("ALLOW_AUTO_ASSIGN_STATE_IN_TEST"):
        return
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _rate_cap_exceeded(state: Dict[str, Any], now_ts: float, max_per_hour: float) -> bool:
    executed = [t for t in (state.get("executed") or [])
                if isinstance(t, (int, float)) and now_ts - t < 3600.0]
    state["executed"] = executed
    return len(executed) >= int(max_per_hour)


# ---------------- cooldown po PANEL_OVERRIDE ----------------

def _recent_override_for_courier(
    courier_id: str,
    now: datetime,
    cooldown_min: float,
    log_path: Optional[str] = None,
) -> bool:
    """True gdy w ostatnich cooldown_min był PANEL_OVERRIDE dot. tego kuriera
    (proposed LUB actual) — koordynator właśnie wyraził zdanie, nie wciskamy
    auto-decyzji. Tail-scan ostatnich LEARNING_LOG_TAIL_BYTES (fail-open=False
    przy braku pliku; fail-closed=True przy błędzie parsowania nie jest
    potrzebny — pojedyncze złe linie pomijamy). log_path=None → moduł-attr
    w czasie wywołania (testy monkeypatchują LEARNING_LOG_PATH)."""
    if log_path is None:
        log_path = LEARNING_LOG_PATH
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return False
    try:
        with open(log_path, "rb") as f:
            f.seek(max(0, size - LEARNING_LOG_TAIL_BYTES))
            raw = f.read().decode("utf-8", errors="replace")
    except Exception:
        return False
    cid = str(courier_id)
    cutoff = now.timestamp() - cooldown_min * 60.0
    for line in raw.splitlines():
        if '"PANEL_OVERRIDE"' not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("action") != "PANEL_OVERRIDE":
            continue
        if cid not in (str(rec.get("proposed_courier_id")), str(rec.get("actual_courier_id"))):
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts.timestamp() >= cutoff:
            return True
    return False


# ---------------- wykonanie ----------------

def _default_assign_runner(order_id: str, kurier_name: str, time_minutes: int) -> Tuple[bool, str]:
    """Subprocess gastro_assign.py — lustrzane do telegram_approver.run_gastro_assign
    (ścieżka ASSIGN_DIRECT). Odmawia pod pytest."""
    if _pytest_active() and not os.environ.get("ALLOW_AUTO_ASSIGN_SUBPROCESS_IN_TEST"):
        return False, "blocked_pytest_context"
    cmd = ["python3", GASTRO_ASSIGN_PATH, "--id", str(order_id),
           "--kurier", str(kurier_name), "--time", str(int(time_minutes))]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, (r.stdout.strip() or "ok")[-400:]
        return False, f"exit={r.returncode} {r.stderr.strip()[-400:]}"
    except subprocess.TimeoutExpired:
        return False, "timeout_30s"
    except Exception as e:
        return False, f"exc:{type(e).__name__}"


def _default_notifier(text: str) -> None:
    try:
        from dispatch_v2 import telegram_utils
        telegram_utils.send_admin_alert(text)
    except Exception as e:
        log.warning(f"auto_assign notifier fail: {e}")


def _append_learning_log(rec: Dict[str, Any]) -> None:
    if _pytest_active() and not os.environ.get("ALLOW_AUTO_ASSIGN_STATE_IN_TEST"):
        return
    try:
        from dispatch_v2.core.jsonl_appender import append_jsonl
        append_jsonl(LEARNING_LOG_PATH, rec)
    except Exception as e:
        log.warning(f"AUTO_ASSIGN learning_log append fail: {e}")


def _time_minutes_from_record(record: Dict[str, Any], now: datetime) -> int:
    """time dla gastro_assign = minuty od teraz do target_pickup_at (≥0)."""
    best = record.get("best") or {}
    tgt = best.get("target_pickup_at")
    if not tgt:
        return 0
    try:
        dt = datetime.fromisoformat(str(tgt).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int(round((dt - now).total_seconds() / 60.0)))
    except Exception:
        return 0


def maybe_execute(
    record: Dict[str, Any],
    result: Any,
    payload: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    assign_runner: Optional[Callable[[str, str, int], Tuple[bool, str]]] = None,
    notifier: Optional[Callable[[str], None]] = None,
    state_path: str = STATE_PATH,
) -> Optional[Dict[str, Any]]:
    """Punkt wejścia z shadow_dispatcher. Przy ENABLE_AUTO_ASSIGN=false → None.

    Zwraca dict z przebiegu (executed/blocked + szczegóły) albo None gdy flaga
    OFF / bramka nie przeszła. NIGDY nie rzuca.
    """
    try:
        # 1. Killswitch hot (kanon ETAP4 flags.json, default false).
        if not C.decision_flag("ENABLE_AUTO_ASSIGN"):
            return None
        # 2. Bramka jakościowa (czysta, policzona w dispatch_pipeline).
        if not getattr(result, "would_auto_assign", False):
            return None
        # Verdict z REKORDU (po suppressach firmowych itd., hook jest po
        # finalnej mutacji) — PROPOSE albo nic.
        if record.get("verdict") != "PROPOSE":
            return {"blocked": "record_verdict_not_propose"}

        best = record.get("best") or {}
        oid = str(record.get("order_id") or "")
        cid = str(best.get("courier_id") or "")
        name = best.get("name")
        if not oid or not cid or not name:
            return {"blocked": "missing_oid_cid_or_name"}

        now = now or datetime.now(timezone.utc)
        now_ts = now.timestamp()

        # 3. Rate-cap wykonań.
        state = _load_state(state_path)
        if _rate_cap_exceeded(state, now_ts, _numeric("AUTO_ASSIGN_MAX_PER_HOUR")):
            log.warning(f"AUTO_ASSIGN blocked rate_cap oid={oid}")
            return {"blocked": "rate_cap"}

        # 4. Cooldown po PANEL_OVERRIDE na tym kurierze.
        cooldown = _numeric("AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN")
        if _recent_override_for_courier(cid, now, cooldown):
            log.warning(f"AUTO_ASSIGN blocked override_cooldown oid={oid} cid={cid}")
            return {"blocked": "override_cooldown", "courier_id": cid}

        # 5. Wykonanie (ścieżka ASSIGN_DIRECT — subprocess gastro_assign).
        time_minutes = _time_minutes_from_record(record, now)
        runner = assign_runner or _default_assign_runner
        ok, msg = runner(oid, str(name), time_minutes)

        outcome = {
            "executed": bool(ok),
            "order_id": oid,
            "courier_id": cid,
            "courier_name": name,
            "time_minutes": time_minutes,
            "runner_msg": msg,
        }
        if ok:
            state.setdefault("executed", []).append(now_ts)
            _save_state(state_path, state)
            _append_learning_log({
                "ts": now.isoformat(),
                "order_id": oid,
                "action": "AUTO_ASSIGN_EXECUTED",
                "courier_id": cid,
                "courier_name": name,
                "time_minutes": time_minutes,
                "score": best.get("score"),
            })
            log.info(f"AUTO_ASSIGN_EXECUTED oid={oid} cid={cid} time={time_minutes}min")
        else:
            log.warning(f"AUTO_ASSIGN runner fail oid={oid} cid={cid}: {msg}")

        # 6. Notyfikacja post-hoc (informacja, nie pytanie).
        notify = notifier or _default_notifier
        status = "✅ wykonane" if ok else f"❌ nieudane ({msg[:120]})"
        notify(
            f"🤖 AUTO-ASSIGN {status}\n"
            f"Zlecenie #{oid} → {name} (cid={cid})\n"
            f"time={time_minutes} min | score={best.get('score')}"
        )
        return outcome
    except Exception as e:
        log.warning(f"auto_assign maybe_execute fail-safe: {type(e).__name__}: {e}")
        return None
