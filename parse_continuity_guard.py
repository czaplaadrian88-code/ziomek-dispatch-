r"""PARSE-01 (audyt 2026-06-03) — Straż ciągłości parse.

Wykrywa nagły spadek liczby AKTYWNYCH zleceń (order_ids - closed_ids) do 0
(lub spadek >= PARSE_DROP_PCT vs mediana ostatnich cykli) przy fetch HTTP 200 +
zero wyjątku. To wzorzec 'częściowy/pusty parse leci dalej jako prawda' — np.
layout-change panelu zwraca order_ids=[] z HTTP 200 (regex `\d{5,7}` łapie KLASĘ
takich zmian, ale brak wyjątku => _fail_count nie rośnie => PANEL_UNREACHABLE nie
odpala, a CHECK1 ZERO_OUTPUT w parser_health odpala dopiero po N cyklach i tylko
po 9:00 Warsaw). Incydent 02.05 (16h+ blackout po rollover) to dokładnie to.

Projekt (zgodnie ze spec PARSE-01):
  • SHADOW-FIRST: flaga PARSE_CONTINUITY_GUARD_ENABLED default OFF.
    Gdy OFF => guard tylko LOGUJE ('ZABLOKOWALBYM ...'), NIC nie zamraża,
    NIE ustawia PARSER_DEGRADED. Zero wpływu na decyzję.
  • Gdy ON i wykrycie potwierdzone PARSE_GUARD_CONFIRM_CYCLES cykli z rzędu:
    freeze_new=True (panel_watcher pomija emisję NOWYCH NEW_ORDER, NIE rusza
    detekcji terminalnej disappeared/delivered) + atomowo ustawia
    PARSER_DEGRADED=true w flags.json (consumer: auto_proximity_classifier
    :414 ctx.parser_degraded => ROUTE_ALERT). Po powrocie parse => czyści flagę.
  • Baseline = parser_health._cycles deque (active_orders z poprzednich cykli),
    NIE świeży in-memory licznik — restart bootstrapuje deque z dysku, więc nie
    ma cold-start blind spotu typu 'baseline=0 po restarcie => guard ślepy'.
  • COLD-START: dopóki nie ma >= PARSE_GUARD_CONFIRM_CYCLES historycznych cykli
    z active>0, guard tylko buduje baseline (return no-trip), nie zamraża.

Defense-in-depth: evaluate() NIGDY nie rzuca — każdy except => log + safe default
(no-trip). Hot-path (panel_watcher tick) nie może się wywrócić przez guard.

Kill-switch: PARSE_CONTINUITY_GUARD_ENABLED=false (hot-reload, 5s) cofa do
shadow log-only. Reset PARSER_DEGRADED: guard sam wyczyści przy recovery; ręcznie
flags.json PARSER_DEGRADED=false.

Memory cross-ref:
  • Lekcja #32: silent except = invisible bug — wszystkie except logują.
  • Lekcja #76: anomaly input semantics — 'źródło nagle puste' = awaria scrape,
    nie 'brak zamówień' (Wolt/DoorDash pattern).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

log = logging.getLogger("parse_continuity_guard")

FLAGS_PATH = "/root/.openclaw/workspace/scripts/flags.json"


def _flag(name: str, default):
    """Hot-reload flag read via common.load_flags (defense: fallback default)."""
    try:
        from dispatch_v2.common import load_flags
        return load_flags().get(name, default)
    except Exception as e:  # pragma: no cover - defensive
        log.warning(f"parse_continuity_guard._flag({name}) fail (non-blocking): {e}")
        return default


# Confirm-cycles state (module-level — guard wołany sekwencyjnie z 1 wątku tick()).
# UWAGA (lekcja #180): lifecycle PARSER_DEGRADED NIE jest już trzymany w pamięci
# procesu (_degraded_set_by_guard usunięte) — set/clear rozpoznawany po
# PARSER_DEGRADED_SET_BY w flags.json, niezależnie od procesu/restartu.
_consecutive_suspicious = 0
_state_lock = threading.Lock()

# Identyfikator writera w flags.json (lifecycle persystentny, lekcja #180).
GUARD_SET_BY = "parse01"
SET_BY_KEY = "PARSER_DEGRADED_SET_BY"
SET_TS_KEY = "PARSER_DEGRADED_SET_TS"


def reset_for_test() -> None:
    """Reset module state — UŻYWAJ TYLKO W TESTACH."""
    global _consecutive_suspicious
    with _state_lock:
        _consecutive_suspicious = 0


def _prev_active_window(cycles: Optional[Iterable[Dict[str, Any]]], window: int = 5) -> List[int]:
    """Wyciąga active_orders z poprzednich cykli (parser_health._cycles).

    Defense: fallback do orders_in_panel gdy active_orders brak (legacy entry).
    Bierze tylko wpisy > 0 (interesuje nas 'było aktywnie, nagle 0/spadek').
    """
    out: List[int] = []
    if not cycles:
        return out
    try:
        seq = list(cycles)
    except Exception:
        return out
    for c in seq[-window:]:
        try:
            v = c.get("active_orders", c.get("orders_in_panel", 0))
            v = int(v or 0)
        except Exception:
            continue
        if v > 0:
            out.append(v)
    return out


def _median(values: Sequence[int]) -> int:
    s = sorted(values)
    return s[len(s) // 2]


def _pytest_write_blocked() -> bool:
    """L1 (wzorzec lekcji #75/telegram_utils, rozszerzony lekcją #180 na flags.json):
    writer flags.json ODMAWIA zapisu z procesu testowego. PYTEST_CURRENT_TEST jest
    auto-ustawiane przez pytest (w prod nigdy). Opt-out dla testów które jawnie
    testują zapis (po spatchowaniu FLAGS_PATH na tmp): ALLOW_FLAGS_WRITE_IN_TEST=1.
    """
    if os.environ.get("ALLOW_FLAGS_WRITE_IN_TEST") == "1":
        return False
    return "PYTEST_CURRENT_TEST" in os.environ


def _read_flags_raw() -> Dict[str, Any]:
    """Surowy odczyt flags.json (bez cache common — potrzebujemy SET_BY/TS świeże)."""
    with open(FLAGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _set_parser_degraded(value: bool) -> bool:
    """Atomowy zapis PARSER_DEGRADED w flags.json (temp+fsync+rename).

    Lifecycle (lekcja #180): przy set=True zapisuje też PARSER_DEGRADED_SET_BY
    ="parse01" + PARSER_DEGRADED_SET_TS; przy clear usuwa oba klucze. Dzięki temu
    recovery działa cross-procesowo/po restarcie (stan w pliku, nie w pamięci).

    Hot-reload przez common.load_flags() podchwyci następny odczyt. NIGDY raise.
    Zwraca True gdy zapis się udał (lub był no-op bo wartość już ustawiona).
    """
    try:
        if _pytest_write_blocked():
            log.warning(
                "PARSE-01 L1: odmowa zapisu flags.json z procesu testowego "
                "(PYTEST_CURRENT_TEST w env; opt-out ALLOW_FLAGS_WRITE_IN_TEST=1)"
            )
            return False
        data = _read_flags_raw()
        if bool(data.get("PARSER_DEGRADED", False)) == bool(value):
            return True  # no-op, nie dotykaj pliku (unik mtime churn)
        data["PARSER_DEGRADED"] = bool(value)
        if value:
            data[SET_BY_KEY] = GUARD_SET_BY
            data[SET_TS_KEY] = datetime.now(timezone.utc).isoformat()
        else:
            data.pop(SET_BY_KEY, None)
            data.pop(SET_TS_KEY, None)
        d = os.path.dirname(FLAGS_PATH)
        fd, tmp = tempfile.mkstemp(prefix="flags.", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, FLAGS_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        log.warning(f"PARSE-01 guard set PARSER_DEGRADED={bool(value)} w flags.json")
        return True
    except Exception as e:
        log.warning(f"parse_continuity_guard._set_parser_degraded fail (non-blocking): {e}")
        return False


def _degraded_in_flags() -> bool:
    """Czy PARSER_DEGRADED=true w flags.json (świeży odczyt z pliku). NIGDY raise."""
    try:
        return bool(_read_flags_raw().get("PARSER_DEGRADED", False))
    except Exception as e:
        log.warning(f"parse_continuity_guard._degraded_in_flags fail (non-blocking): {e}")
        return False


def _degraded_set_by_us() -> bool:
    """Czy PARSER_DEGRADED=true ORAZ SET_BY=="parse01" (lifecycle persystentny).

    Process-independent: działa też gdy set zrobił inny proces / poprzedni
    proces przed restartem. NIGDY raise.
    """
    try:
        data = _read_flags_raw()
        return bool(data.get("PARSER_DEGRADED", False)) and data.get(SET_BY_KEY) == GUARD_SET_BY
    except Exception as e:
        log.warning(f"parse_continuity_guard._degraded_set_by_us fail (non-blocking): {e}")
        return False


def _alert(msg: str) -> None:
    """Telegram admin alert (best-effort). NIGDY raise."""
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg)
    except Exception as e:
        log.warning(f"parse_continuity_guard._alert telegram fail (non-blocking): {e}")


def evaluate(
    order_ids,
    closed_ids=None,
    n_state_active: int = 0,
    cycles: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Pre-emit straż. Wołane w _diff_and_emit PRZED pętlą NOWE.

    Args:
        order_ids: parsed['order_ids'] (lista/zbiór ID z HTML).
        closed_ids: parsed['closed_ids'] (status terminalny 7/8/9).
        n_state_active: liczba nie-terminalnych zleceń w orders_state (korroboracja
            'było coś do roboty' — wektor: panel pusty ALE state ma assigned>0).
        cycles: deque parser_health._cycles (baseline). Gdy None => pobierz singleton.

    Returns dict:
        {
          'suspicious': bool,      # bieżący cykl wygląda na zerwany parse
          'confirmed': bool,       # potwierdzone CONFIRM_CYCLES z rzędu
          'freeze_new': bool,      # czy panel_watcher ma pominąć emisję NEW (tylko gdy ON)
          'shadow': bool,          # True = flaga OFF, log-only ('ZABLOKOWALBYM')
          'n_active': int,
          'prev_active_median': int,
          'reason': str,
          'cold_start': bool,
        }

    Defense-in-depth: NIGDY raise. Każdy except => log + no-trip (freeze_new=False).
    """
    global _consecutive_suspicious
    result = {
        "suspicious": False,
        "confirmed": False,
        "freeze_new": False,
        "shadow": True,
        "n_active": None,
        "prev_active_median": 0,
        "reason": "",
        "cold_start": False,
    }
    try:
        enabled = bool(_flag("PARSE_CONTINUITY_GUARD_ENABLED", False))
        result["shadow"] = not enabled
        min_prev = int(_flag("PARSE_BLACKOUT_MIN_PREV", 5))
        drop_pct = float(_flag("PARSE_DROP_PCT", 70))
        confirm_cycles = max(1, int(_flag("PARSE_GUARD_CONFIRM_CYCLES", 2)))

        try:
            oset = set(order_ids or [])
        except Exception:
            oset = set()
        try:
            cset = set(closed_ids or [])
        except Exception:
            cset = set()
        active = oset - cset
        n_active = len(active)
        result["n_active"] = n_active

        if cycles is None:
            try:
                from dispatch_v2.parser_health import get_monitor
                cycles = get_monitor()._cycles
            except Exception as e:
                log.warning(f"parse_continuity_guard: get_monitor fail (non-blocking): {e}")
                cycles = None

        prev_active = _prev_active_window(cycles, window=5)
        prev_median = _median(prev_active) if prev_active else 0
        result["prev_active_median"] = prev_median

        # COLD-START: za mało historii z active>0 => tylko buduj baseline.
        if len(prev_active) < confirm_cycles:
            result["cold_start"] = True
            with _state_lock:
                _consecutive_suspicious = 0
            return result

        # Detekcja podejrzanego cyklu:
        #  (A) BLACKOUT: poprzednio aktywnie (median >= min_prev) a teraz 0.
        #      Korroboracja przez state: panel 0 ALE n_state_active>0 wzmacnia.
        #  (B) DROP: spadek aktywnych >= drop_pct vs mediana (n_active>0 też łapie
        #      — uzupełnia CHECK2 DELTA w parser_health, który ma bramkę n_active>0,
        #      ale spadek DO 0 mu wypada; tu DO-0 to gałąź A).
        suspicious = False
        reason = ""
        if n_active == 0 and prev_median >= min_prev:
            suspicious = True
            reason = (
                f"BLACKOUT active {prev_median}->0 (min_prev={min_prev}, "
                f"state_active={n_state_active})"
            )
        elif n_active > 0 and prev_median > 0:
            pct_drop = (prev_median - n_active) / prev_median * 100.0
            if pct_drop >= drop_pct:
                suspicious = True
                reason = (
                    f"DROP active {prev_median}->{n_active} (-{pct_drop:.0f}% >= {drop_pct:.0f}%)"
                )
        result["suspicious"] = suspicious
        result["reason"] = reason

        with _state_lock:
            if suspicious:
                _consecutive_suspicious += 1
            else:
                _consecutive_suspicious = 0
            confirmed = _consecutive_suspicious >= confirm_cycles
            result["confirmed"] = confirmed

            if suspicious:
                if not enabled:
                    # SHADOW: log-only, nic nie zamrażamy.
                    log.warning(
                        f"PARSE-01 SHADOW ZABLOKOWALBYM emisję NEW: {reason} "
                        f"consecutive={_consecutive_suspicious}/{confirm_cycles} "
                        f"(flaga PARSE_CONTINUITY_GUARD_ENABLED OFF — tylko log)"
                    )
                elif confirmed:
                    result["freeze_new"] = True
                    log.error(
                        f"PARSE-01 ACTIVE freeze NEW emisji: {reason} "
                        f"consecutive={_consecutive_suspicious}/{confirm_cycles}"
                    )
                    # Lifecycle w pliku (lekcja #180): set tylko gdy flaga jeszcze
                    # nie ustawiona — re-alert/re-write nie powtarza się per cykl,
                    # niezależnie od procesu/restartu.
                    if not _degraded_in_flags():
                        if _set_parser_degraded(True):
                            _alert(
                                "🚨 PARSE-01: panel nagle pusty/zwężony — wstrzymuję "
                                "emisję NOWYCH zleceń (możliwy zerwany parse / zmiana "
                                f"layoutu panelu).\n{reason}\n\nCo robię: zamrażam NEW, "
                                "detekcja zakończeń (delivered) działa dalej, AUTO→ALERT "
                                "(PARSER_DEGRADED=true). Jeśli panel realnie pusty — to "
                                "normalne, wróci sam. Jeśli się utrzyma → sprawdź panel / "
                                "restart:\nsudo systemctl restart dispatch-panel-watcher"
                            )
                else:
                    log.warning(
                        f"PARSE-01 ACTIVE wykrycie (jeszcze nie potwierdzone): {reason} "
                        f"consecutive={_consecutive_suspicious}/{confirm_cycles}"
                    )
            else:
                # RECOVERY: parse wrócił — wyczyść degraded jeśli SET_BY=="parse01"
                # (cross-procesowo: także set z innego procesu / sprzed restartu;
                # set ręczny/obcy NIE jest czyszczony — lekcja #180).
                if _degraded_set_by_us():
                    if _set_parser_degraded(False):
                        log.warning("PARSE-01 recovery: PARSER_DEGRADED wyczyszczone (parse wrócił)")
                        _alert(
                            "✅ PARSE-01: parse wrócił do normy — wznawiam emisję NOWYCH "
                            "zleceń, PARSER_DEGRADED=false."
                        )
        return result
    except Exception as e:
        log.warning(f"parse_continuity_guard.evaluate fail (non-blocking, no-trip): {e}")
        return result
