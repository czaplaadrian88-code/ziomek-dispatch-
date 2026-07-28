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
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from dispatch_v2 import common as C
from dispatch_v2 import authority_card as AC
from dispatch_v2 import proposal_freshness as PF
from dispatch_v2 import state_machine
from dispatch_v2.tools import auto_assign_monitor as AAM

log = logging.getLogger("auto_assign_executor")

GASTRO_ASSIGN_PATH = "/root/.openclaw/workspace/scripts/gastro_assign.py"
STATE_PATH = "/root/.openclaw/workspace/dispatch_state/auto_assign_state.json"
LEARNING_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
LEARNING_LOG_TAIL_BYTES = 262144  # wzorzec _PANEL_AGREE_TAIL_BYTES
MONITOR_HEARTBEAT_PATH = AAM.HEARTBEAT_PATH
SHADOW_DECISIONS_PATH = AAM.SHADOW_PATH

# AUDYT 2.0 Blocker-1: executor ufa sentinelowi z gastro_assign, NIE samemu
# exit-code (exit 0 mimo niewykonanego przypisania = cichy drop bez człowieka).
ASSIGN_OK_SENTINEL = "ASSIGN_OK:"
GASTRO_ASSIGN_TIMEOUT_SEC = 45  # +read-back (--verify) round-trip (było 30)

# AUTON-02 / T2 (2026-07-28, ODR-002 „tylko owner podnosi autonomię"):
# PIN właściciela na PODNIESIENIE autonomii żyje w PANELU (nadajesz_clone,
# commit d42da13, POST /coordinator/auto-assign) i chroni WYŁĄCZNIE ścieżkę
# przycisku w konsoli. `ENABLE_AUTO_ASSIGN` mieszka w `flags.json` — pliku, do
# którego pisze każdy proces/agent/merge z prawem zapisu, więc panelowy PIN da
# się ominąć w całości, nie dotykając panelu. To nie jest hipoteza: 2026-07-21
# 20:57 flaga weszła do gita workspace w cudzym merge'u (memory
# `enable-auto-assign-true-bez-pin-2026-07-26`).
#
# Dlatego silnik NIE ufa samej fladze. Stan ON musi być WYTŁUMACZALNY: ostatni
# udany toggle w dzienniku audytu koordynatora ma podnosić flagę i mieć
# `pin_verified=true`, i musi być ŚWIEŻY. Upoważnienie ODR-002 to ZDARZENIE, nie
# stan wieczysty — inaczej jedno kliknięcie ownera autoryzuje autonomię na
# zawsze (dokładnie to zrobił flip z 20.07: PIN o 13:31Z, a flaga została ON
# przez tydzień).
#
# ⚠ UCZCIWA GRANICA (ta sama klasa co sekcja 2D karty canary): sesje agentów
# chodzą po tym hoście jako root, więc ten plik jest teoretycznie podrabialny.
# Bramka daje fail-closed default, jedną wąską ścieżkę i tamper-EVIDENCE —
# NIE kryptograficzną niepodrabialność. Ta wymaga klucza ownera POZA hostem
# (osobna bramka ODR-002). W oknie TTL bramka nie odróżni też ręcznego
# wyłączenia i ponownego podniesienia poza panelem (killswitch nie zostawia
# wiersza) — świadomie ograniczone czasem, nie udawane jako pełna ochrona.
COORDINATOR_AUDIT_PATH = "/root/.openclaw/workspace/dispatch_state/coordinator_assign_audit.jsonl"
COORDINATOR_AUDIT_TAIL_BYTES = 262144
AUTO_ASSIGN_OWNER_AUTH_TTL_SEC = 86400.0  # karta canary może zacieśnić (flags.json/env)
_OWNER_AUTH_CLOCK_SKEW_SEC = 120.0
_OWNER_AUTH_WARN_THROTTLE_SEC = 300.0
_last_owner_auth_warn: Dict[str, float] = {}

# AUTON-02 / T5: podpisana karta ownera jest bezwarunkowym krokiem 1c wewnątrz
# ścieżki AUTO. Nie jest flagą ani profilem jakościowym.
AUTHORITY_CARD_PATH = AC.CARD_PATH
AUTHORITY_CARD_STATE_PATH = AC.STATE_PATH
AUTHORITY_BUILD_SHA_PATH = AC.BUILD_SHA_PATH
_AUTHORITY_WARN_THROTTLE_SEC = 300.0
_last_authority_warn: Dict[str, float] = {}

# AUDYT 2.0 Blocker-2 — pokrętła operacyjne (env/flags-overridable W MODULE
# executora; common.py poza tym pasem). Domyślne wartości = dokumentacja.
AUTO_ASSIGN_ARM_DELAY_SEC = 45.0          # dry-first: pauza po KAŻDEJ zmianie flags.json
AUTO_ASSIGN_IDEMPOTENCY_TTL_SEC = 900.0   # ten sam oid nie wykona się 2× w tym oknie


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


def _exec_numeric(name: str, default: float) -> float:
    """Stała operacyjna executora (Blocker-2): flags.json (hot) → env → default.
    Osobno od `_numeric` — te klucze NIE żyją w common.py (poza tym pasem)."""
    try:
        fl = C.load_flags()
    except Exception:
        fl = {}
    v = fl.get(name)
    if v is None:
        v = os.environ.get(name)
    try:
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


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


# ---------------- dry-first + idempotencja (AUDYT 2.0 Blocker-2) ----------------

def _flags_recently_changed(now_ts: float, arm_delay_sec: float) -> bool:
    """True gdy flags.json zmieniony w ostatnich arm_delay_sec → 'dry-first':
    pierwszy tick po flipie OFF→ON (i po każdej zmianie configu) NIE wykonuje —
    daje nadzorującemu operatorowi beat, a decyzja z 'starego snu' sprzed flipu
    nie odpala się natychmiast. Fail-open (brak mtime → nie blokuj) tylko gdy
    plik nieosiągalny; ENABLE_AUTO_ASSIGN i tak żyje w tym pliku, więc przy ON
    mtime jest dostępny. Odmawia (return False) pod pytest, chyba że jawnie
    włączone — testy sterują deterministycznie, nie mtime współdzielonego pliku."""
    if _pytest_active() and not os.environ.get("ALLOW_AUTO_ASSIGN_DRYFIRST_IN_TEST"):
        return False
    try:
        mt = os.path.getmtime(str(C.FLAGS_PATH))
    except Exception:
        return False
    return (now_ts - mt) < arm_delay_sec


def _last_successful_toggle(audit_path: str) -> Optional[Dict[str, Any]]:
    """Ostatni UDANY wiersz `auto_assign_toggle` z dziennika audytu koordynatora
    (tail-scan, wzorzec `_recent_override_for_courier`). `ok=false` to PRÓBA
    zapisu flagi, nie upoważnienie — odfiltrowana. Zwraca None gdy pliku nie ma,
    nie da się go odczytać albo nie zawiera ani jednego takiego wiersza."""
    try:
        size = os.path.getsize(audit_path)
    except OSError:
        return None
    try:
        with open(audit_path, "r", encoding="utf-8", errors="replace") as f:
            if size > COORDINATOR_AUDIT_TAIL_BYTES:
                f.seek(size - COORDINATOR_AUDIT_TAIL_BYTES)
                f.readline()  # odetnij ucięty pierwszy wiersz
            lines = f.readlines()
    except OSError:
        return None
    found = None
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except Exception:  # noqa: BLE001 — pojedyncze śmieci nie mogą autoryzować ANI wywracać
            continue
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "auto_assign_toggle" and row.get("ok") is True:
            found = row
    return found


def _owner_authorization(now: datetime, audit_path: Optional[str] = None
                         ) -> Tuple[bool, str]:
    """Czy bieżący stan `ENABLE_AUTO_ASSIGN=true` jest pokryty świeżym,
    PIN-owanym podniesieniem właściciela (ODR-002)? Zwraca (ok, powód).

    Wołane WYŁĄCZNIE gdy flaga jest ON — ścieżka OFF zostaje bez I/O (kontrakt
    AUTON-01 pkt 1). Każda wątpliwość = False (fail-closed): brak pliku, brak
    wiersza, ostatni toggle wyłączający, brak `pin_verified`, nieczytelny albo
    przeterminowany znacznik czasu. `audit_path=None` → atrybut modułu w czasie
    wywołania (testy monkeypatchują COORDINATOR_AUDIT_PATH)."""
    if audit_path is None:
        audit_path = COORDINATOR_AUDIT_PATH
    row = _last_successful_toggle(audit_path)
    if row is None:
        return False, ("audit_unreadable" if not os.path.exists(audit_path)
                       else "no_toggle_row")
    if row.get("value") is not True:
        return False, "last_toggle_disabled"
    if row.get("pin_verified") is not True:
        return False, "not_pin_verified"
    try:
        ts = datetime.fromisoformat(str(row.get("ts")))
    except (TypeError, ValueError):
        return False, "authorization_stale"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (now - ts).total_seconds()
    # Wiersz z PRZYSZŁOŚCI = ujemny wiek = „zawsze młodszy niż TTL" → wieczne
    # upoważnienie z jednej daty. Odrzucamy, tolerując drobny rozjazd zegarów
    # panel↔silnik (osobne procesy, ts stawia panel).
    if age < -_OWNER_AUTH_CLOCK_SKEW_SEC:
        return False, "authorization_future"
    ttl = _exec_numeric("AUTO_ASSIGN_OWNER_AUTH_TTL_SEC", AUTO_ASSIGN_OWNER_AUTH_TTL_SEC)
    if age > ttl:
        return False, "authorization_stale"
    return True, "ok"


def _warn_owner_auth(reason: str, now_ts: float) -> None:
    """WARN o odmowie autoryzacji — dławiony per powód, bo przy ON bez
    upoważnienia trafiałby na KAŻDĄ decyzję i utopiłby log."""
    last = _last_owner_auth_warn.get(reason, 0.0)
    if now_ts - last < _OWNER_AUTH_WARN_THROTTLE_SEC:
        return
    _last_owner_auth_warn[reason] = now_ts
    log.warning(
        f"AUTO_ASSIGN blocked owner_auth_missing reason={reason} — "
        f"ENABLE_AUTO_ASSIGN=true BEZ pokrycia PIN-em właściciela (ODR-002). "
        f"Podnieś autonomię przyciskiem w konsoli koordynatora (z PIN-em) "
        f"albo wyłącz flagę.")


def _warn_authority_card(reason: str, now_ts: float) -> None:
    """Dławiony WARN kroku 1c — jeden komunikat per przyczyna na pięć minut."""
    last = _last_authority_warn.get(reason, 0.0)
    if now_ts - last < _AUTHORITY_WARN_THROTTLE_SEC:
        return
    _last_authority_warn[reason] = now_ts
    log.warning(
        f"AUTO_ASSIGN blocked authority_card reason={reason} class_id={AC.CLASS_ID} "
        f"— podpisana karta execution authority nie potwierdza tego wykonania "
        f"(ODR-002).")


def _authority_card_gate(
    record: Dict[str, Any],
    result: Any,
    payload: Optional[Dict[str, Any]],
    now: datetime,
    card_path: str,
    audit_path: str,
    state_path: str,
    code_git_sha: Optional[str],
    flag_fp: Optional[str],
) -> Tuple[bool, str, Dict[str, Any]]:
    """Krok 1c: latch → podpis → scope → limity. Każdy błąd jest odmową."""
    state = AC.load_state(state_path)
    if state.get("auto_off_latch") is True:
        return False, "latch_on", {
            "state": state,
            "state_path": state_path,
            "enforced": True,
        }

    verdict = AC.verify_card(
        card_path=card_path,
        audit_path=audit_path,
        now=now,
        code_git_sha=code_git_sha,
        flag_fp=flag_fp,
    )
    if not verdict.valid:
        try:
            AC.latch_auto_off(state_path, verdict.reason, now)
        except Exception as latch_error:
            log.warning(
                f"AUTO_ASSIGN authority latch write fail reason={verdict.reason}: "
                f"{type(latch_error).__name__}: {latch_error}")
        return False, verdict.reason, {
            "state": state,
            "state_path": state_path,
            "enforced": True,
        }

    body = verdict.body or {}
    scope_ok, scope_reason = AC.check_scope(
        record, result, payload, body.get("scope") or {})
    if not scope_ok:
        return False, scope_reason, {
            "state": state,
            "state_path": state_path,
            "card_sha256": verdict.card_sha256,
            "enforced": True,
        }
    limits = body.get("limits") or {}
    limits_ok, limits_reason = AC.check_limits(
        state, now.timestamp(), limits)
    if not limits_ok:
        return False, limits_reason, {
            "state": state,
            "state_path": state_path,
            "card_sha256": verdict.card_sha256,
            "limits": limits,
            "enforced": True,
        }
    return True, "ok", {
        "state": state,
        "state_path": state_path,
        "card_sha256": verdict.card_sha256,
        "limits": limits,
        "enforced": True,
    }


def _recent_auto_assign(state: Dict[str, Any], oid: str, now_ts: float, ttl_sec: float) -> bool:
    """True gdy oid już auto-przypisany w ostatnich ttl_sec (idempotencja per-order:
    reconcile-lag panelu 15-90 s + 2. event z innym event_id = podwójny assign)."""
    ts = (state.get("assigned_orders") or {}).get(str(oid))
    return isinstance(ts, (int, float)) and (now_ts - ts) < ttl_sec


def _record_auto_assign(state: Dict[str, Any], oid: str, now_ts: float, ttl_sec: float) -> None:
    """Zapisz oid+ts do idempotency-store, przycinając wygasłe wpisy."""
    ao = {k: v for k, v in (state.get("assigned_orders") or {}).items()
          if isinstance(v, (int, float)) and (now_ts - v) < ttl_sec}
    ao[str(oid)] = now_ts
    state["assigned_orders"] = ao


# ---------------- wykonanie ----------------

def _default_assign_runner(order_id: str, kurier_name: str, time_minutes: int) -> Tuple[bool, str]:
    """Subprocess gastro_assign.py — lustrzane do telegram_approver.run_gastro_assign
    (ścieżka ASSIGN_DIRECT). Odmawia pod pytest.

    AUDYT 2.0 Blocker-1: sukces TYLKO gdy exit 0 ORAZ jawny sentinel ASSIGN_OK:
    w stdout (gastro drukuje go dopiero po POTWIERDZENIU). --verify wymusza po
    stronie gastro read-back przypisania (tor autonomii = człowiek poza pętlą)."""
    if _pytest_active() and not os.environ.get("ALLOW_AUTO_ASSIGN_SUBPROCESS_IN_TEST"):
        return False, "blocked_pytest_context"
    cmd = ["python3", GASTRO_ASSIGN_PATH, "--id", str(order_id),
           "--kurier", str(kurier_name), "--time", str(int(time_minutes)), "--verify"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=GASTRO_ASSIGN_TIMEOUT_SEC)
        out = (r.stdout or "")
        if r.returncode == 0 and ASSIGN_OK_SENTINEL in out:
            return True, out.strip()[-400:]
        err = (r.stderr or "").strip() or out.strip()
        return False, f"exit={r.returncode} no_confirm {err[-360:]}"
    except subprocess.TimeoutExpired:
        return False, f"timeout_{int(GASTRO_ASSIGN_TIMEOUT_SEC)}s"
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
    authority_card_path: str = AUTHORITY_CARD_PATH,
    authority_audit_path: str = COORDINATOR_AUDIT_PATH,
    authority_state_path: str = AUTHORITY_CARD_STATE_PATH,
    monitor_heartbeat_path: str = MONITOR_HEARTBEAT_PATH,
    shadow_decisions_path: str = SHADOW_DECISIONS_PATH,
    commit_recheck_provider: Optional[Callable[..., Dict[str, Any]]] = None,
    code_git_sha: Optional[str] = None,
    flag_fp: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Punkt wejścia z shadow_dispatcher. Przy ENABLE_AUTO_ASSIGN=false → None.

    Zwraca dict z przebiegu (executed/blocked + szczegóły) albo None gdy flaga
    OFF / bramka nie przeszła. NIGDY nie rzuca.
    """
    try:
        # 1. Killswitch hot (kanon ETAP4 flags.json, default false).
        if not C.decision_flag("ENABLE_AUTO_ASSIGN"):
            return None
        # 1b. AUTON-02/T2: UPOWAŻNIENIE WŁAŚCICIELA (ODR-002) — sama flaga nie
        # wystarcza, bo flags.json pisze każdy z prawem zapisu (incydent 21.07).
        # PRZED bramką jakościową: odmowa ma być WIDOCZNA w logu decyzji nawet
        # gdy jakość i tak by odrzuciła — inaczej „lewa" flaga jest niema.
        now = now or datetime.now(timezone.utc)
        _auth_ok, _auth_reason = _owner_authorization(now)
        if not _auth_ok:
            _warn_owner_auth(_auth_reason, now.timestamp())
            return {"blocked": "owner_auth_missing", "reason": _auth_reason}
        # 1c. AUTON-02/T5: karta klasy per wykonanie. Git SHA pochodzi wyłącznie
        # z BUILD_SHA wbitego przy deployu — hot-path nigdy nie uruchamia gita.
        if code_git_sha is None:
            code_git_sha = AC.read_code_git_sha(AUTHORITY_BUILD_SHA_PATH)
        if flag_fp is None:
            try:
                flag_fp = C.flag_fingerprint()
            except Exception:
                flag_fp = None
        _card_ok, _card_reason, _card_ctx = _authority_card_gate(
            record=record,
            result=result,
            payload=payload,
            now=now,
            card_path=authority_card_path,
            audit_path=authority_audit_path,
            state_path=authority_state_path,
            code_git_sha=code_git_sha,
            flag_fp=flag_fp,
        )
        if not _card_ok:
            _warn_authority_card(_card_reason, now.timestamp())
            return {
                "blocked": f"authority_card_{_card_reason}",
                "reason": _card_reason,
            }
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

        now_ts = now.timestamp()

        # 2b. DRY-FIRST (Blocker-2): pierwszy tick po flipie OFF→ON (i po każdej
        # zmianie flags.json) = log-only handshake, ZERO wykonania. Decyzja ze
        # „starego snu" sprzed flipu nie odpala się natychmiast.
        arm_delay = _exec_numeric("AUTO_ASSIGN_ARM_DELAY_SEC", AUTO_ASSIGN_ARM_DELAY_SEC)
        if _flags_recently_changed(now_ts, arm_delay):
            log.info(f"AUTO_ASSIGN dry_first_handshake oid={oid} (flags.json <{arm_delay:.0f}s temu)")
            return {"blocked": "dry_first_handshake", "order_id": oid}

        # 3. Rate-cap wykonań.
        state = _load_state(state_path)

        # 3b. Idempotencja per-order (Blocker-2): reconcile-lag 15-90 s + drugi
        # event (inny event_id, dedup event_bus nie chroni) = podwójne przypisanie.
        idem_ttl = _exec_numeric("AUTO_ASSIGN_IDEMPOTENCY_TTL_SEC", AUTO_ASSIGN_IDEMPOTENCY_TTL_SEC)
        if _recent_auto_assign(state, oid, now_ts, idem_ttl):
            log.warning(f"AUTO_ASSIGN blocked idempotent oid={oid} (już auto-przypisany <{idem_ttl:.0f}s)")
            return {"blocked": "idempotent_recent", "order_id": oid}

        # T5: na prawdziwej ścieżce karty limit godzinowy został już oceniony
        # z podpisanego body. Stary flags.json rate-cap zostaje wyłącznie dla
        # izolowanych testów starszych warstw, które jawnie bypassują krok 1c.
        if (not _card_ctx.get("enforced")
                and _rate_cap_exceeded(
                    state, now_ts, _numeric("AUTO_ASSIGN_MAX_PER_HOUR"))):
            log.warning(f"AUTO_ASSIGN blocked rate_cap oid={oid}")
            return {"blocked": "rate_cap"}

        # 4. Cooldown po PANEL_OVERRIDE na tym kurierze.
        cooldown = _numeric("AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN")
        if _recent_override_for_courier(cid, now, cooldown):
            log.warning(f"AUTO_ASSIGN blocked override_cooldown oid={oid} cid={cid}")
            return {"blocked": "override_cooldown", "courier_id": cid}

        # 4b/T1/T6. Jedna sekcja: limity karty + heartbeat + lifecycle CAS +
        # świeży solve + runner + sprzężone zapisy. Card lock jest reentrant,
        # lifecycle lock blokuje każdego writera materialnego orders_state.
        authority_lock = (
            AC.state_lock(authority_state_path)
            if _card_ctx.get("enforced")
            else nullcontext()
        )
        with authority_lock:
            with state_machine.lifecycle_apply_lock():
                if not C.decision_flag("ENABLE_AUTO_ASSIGN"):
                    log.info(f"AUTO_ASSIGN aborted flag_off_at_execution oid={oid}")
                    return {"blocked": "flag_off_at_execution", "order_id": oid}
                _card_ok, _card_reason, _card_ctx = _authority_card_gate(
                    record=record,
                    result=result,
                    payload=payload,
                    now=now,
                    card_path=authority_card_path,
                    audit_path=authority_audit_path,
                    state_path=authority_state_path,
                    code_git_sha=code_git_sha,
                    flag_fp=flag_fp,
                )
                if not _card_ok:
                    _warn_authority_card(_card_reason, now_ts)
                    return {
                        "blocked": f"authority_card_{_card_reason}",
                        "reason": _card_reason,
                    }
                if _card_ctx.get("enforced"):
                    heartbeat_ok, heartbeat_reason = AAM.heartbeat_fresh(
                        monitor_heartbeat_path, now
                    )
                    if not heartbeat_ok:
                        AC.latch_auto_off(
                            authority_state_path, heartbeat_reason, now
                        )
                        return {
                            "blocked": heartbeat_reason,
                            "reason": heartbeat_reason,
                        }

                # Powtórz także idempotencję po wejściu pod wspólny lock.
                state = _load_state(state_path)
                if _recent_auto_assign(state, oid, now_ts, idem_ttl):
                    return {"blocked": "idempotent_recent", "order_id": oid}

                if _card_ctx.get("enforced"):
                    recheck = commit_recheck_provider or PF.prepare_commit_recheck
                    fresh = recheck(oid, payload, now=now)
                    commit_ok, commit_reason = PF.compare_commit_snapshots(
                        record.get("commit_proposal"), fresh, now
                    )
                    if not commit_ok:
                        # Staleness != tamper: celowo bez latcha.
                        return {
                            "blocked": commit_reason,
                            "reason": commit_reason,
                        }

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
                    if _card_ctx.get("state_path"):
                        try:
                            AC.record_success(
                                str(_card_ctx["state_path"]),
                                _card_ctx["state"],
                                oid,
                                now,
                            )
                            outcome["authority_card_sha256"] = _card_ctx.get(
                                "card_sha256")
                        except Exception as card_state_error:
                            outcome["authority_state_recorded"] = False
                            log.warning(
                                f"AUTO_ASSIGN authority state write fail oid={oid}: "
                                f"{type(card_state_error).__name__}: {card_state_error}")
                            AC.latch_auto_off(
                                str(_card_ctx["state_path"]),
                                "state_write_failed_after_execution",
                                now,
                            )
                    state.setdefault("executed", []).append(now_ts)
                    _record_auto_assign(state, oid, now_ts, idem_ttl)
                    state["executed_total"] = int(
                        state.get("executed_total", 0) or 0
                    ) + 1
                    order_ids = [
                        str(value)
                        for value in (state.get("executed_order_ids") or [])
                    ]
                    if oid not in order_ids:
                        order_ids.append(oid)
                    state["executed_order_ids"] = order_ids
                    _save_state(state_path, state)
                    if _card_ctx.get("enforced"):
                        from dispatch_v2.core.jsonl_appender import append_jsonl
                        append_jsonl(shadow_decisions_path, {
                            "ts": now.isoformat(),
                            "record_type": "auto_executed",
                            "auto_executed": True,
                            "order_id": oid,
                            "courier_id": cid,
                            "card_sha256": _card_ctx.get("card_sha256"),
                        })
                    _append_learning_log({
                        "ts": now.isoformat(),
                        "order_id": oid,
                        "action": "AUTO_ASSIGN_EXECUTED",
                        "courier_id": cid,
                        "courier_name": name,
                        "time_minutes": time_minutes,
                        "score": best.get("score"),
                    })
                    log.info(
                        f"AUTO_ASSIGN_EXECUTED oid={oid} cid={cid} "
                        f"time={time_minutes}min")
                else:
                    log.warning(
                        f"AUTO_ASSIGN runner fail oid={oid} cid={cid}: {msg}")

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
