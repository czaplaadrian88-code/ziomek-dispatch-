"""State Machine zlecen - jedyne zrodlo prawdy o stanie kazdego zlecenia.

Kluczowe wlasciwosci:
- Atomic writes: temp -> fsync -> rename
- File lock: fcntl.flock zapobiega race condition miedzy procesami
- History per zlecenie: pelny audit trail
- Integracja z event bus: update_from_event() konsumuje eventy
- Statusy: planned -> assigned -> picked_up -> delivered (+ returned_to_pool)
- Commitment levels: planned / assigned / arrived_at_pickup / picked_up / en_route / near_delivery
"""
import fcntl
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dispatch_v2.common import (
    ENABLE_R_DECLARED_TRIPWIRE,
    R_DECLARED_TRIPWIRE_TOLERANCE_MIN,
    coords_in_bialystok_bbox,
    decision_flag,
    flag,
    load_config,
    now_iso,
    now_utc,
    setup_logger,
)
from dispatch_v2.core.jsonl_appender import append_jsonl
from dispatch_v2.order_fsm import FsmOutcome, FsmVerdict, validate_order_event

_WARSAW_TZ = ZoneInfo("Europe/Warsaw")


class CorruptedTimestampError(ValueError):
    """V3.19f: HH:MM string nie zgadza się z ISO datetime po parse.

    Wykrywane przez _verify_czas_kuriera_consistency:
      assert warsaw_dt.strftime("%H:%M") == raw_hhmm
    Jeśli False → log ERROR + skip persist + raise ten wyjątek.

    Sygnał korupcji parsera (panel_client._czas_kuriera_to_datetime edge
    case, malformed input, albo downstream corruption). Lepiej fail-fast
    niż tichy persist bzdury do orders_state.
    """
    pass


class StateReadError(RuntimeError):
    """Faza 1 (incydent 2026-05-18 14:47 — orders_state.json clobber):
    _read_state nie zwrócił definitywnego stanu (FileNotFoundError mimo że
    plik powinien istnieć, albo JSONDecodeError).

    RMW writer (upsert_order / set_status / touch_check_cursor / delete_order)
    MUSI przerwać zapis przy tym wyjątku — zapis pustego/niekompletnego stanu
    nadpisałby cały orders_state.json (total state loss). Fail-loud, nie
    fail-catastrophic (Lekcja #32 silent except + #81 fail-loud sentinel).

    Lepiej zgubić aplikację jednego eventu (event zostaje w events.db,
    append-only → odtwarzalny) niż skasować stan całej floty.
    """
    pass


def _verify_czas_kuriera_consistency(
    warsaw_iso: Optional[str],
    raw_hhmm: Optional[str],
    oid: str,
) -> bool:
    """V3.19f sanity: ISO strftime('%H:%M') MUSI == raw HH:MM.

    Zwraca True gdy consistency OK albo oba pola None (no-op).
    Zwraca False + log ERROR gdy mismatch — caller powinien skip persist
    i raise CorruptedTimestampError.

    Edge cases:
    - oba None → True (nic do weryfikacji)
    - tylko jedno None → False (partial data, zły sygnał)
    - ISO parse fail → False (corrupted)
    - wraparound OK: strftime('%H:%M') daje tę samą godzinę niezależnie
      od zmienionej daty (+1/-1 day), więc sanity check is stabilny pod
      6h wraparound guard z V3.19f parse layer.
    """
    if warsaw_iso is None and raw_hhmm is None:
        return True
    if warsaw_iso is None or raw_hhmm is None:
        _log.error(
            f"CZAS_KURIERA partial data for oid={oid}: "
            f"warsaw_iso={warsaw_iso!r} hhmm={raw_hhmm!r}"
        )
        return False
    try:
        dt = datetime.fromisoformat(warsaw_iso)
    except (ValueError, TypeError) as e:
        _log.error(
            f"CZAS_KURIERA ISO parse fail for oid={oid}: "
            f"warsaw_iso={warsaw_iso!r} err={e}"
        )
        return False
    expected = dt.strftime("%H:%M")
    if expected != raw_hhmm:
        _log.error(
            f"CZAS_KURIERA MISMATCH for oid={oid}: "
            f"ISO→HH:MM={expected!r} != raw_hhmm={raw_hhmm!r} "
            f"(warsaw_iso={warsaw_iso})"
        )
        return False
    return True


# ── Czasówka committed-pickup authority (Adrian 2026-06-24, root #483023) ──
# Umówiony czas CZASÓWKI = pickup_at_warsaw (twarda deklaracja restauracji).
# Gastro przestempluje pole `czas_kuriera` przy KAŻDEJ zmianie statusu
# (panel_kurier.py: "stempluje czas_odbioru/czas_doreczenia ze zmiany statusu")
# → pasywny re-odczyt panelu (panel_re_check / pre_proposal_recheck) wpuszczał
# ten śmieć jako zmianę committed (#483023: 16:22→15:04, 5 s po assignie).
# Dla czasówek NIE ingestujemy pasywnego czas_kuriera. Umówiony czas zmienia
# się TYLKO przez deklarację odbioru (pickup_at → PICKUP_TIME_UPDATED, dowolny
# kierunek = koordynator/restauracja) albo deliberatny, otagowany kanał
# (np. ziomek_late_extension). Źródła pasywne (re-odczyt gastro) → blok.
_CK_PASSIVE_SOURCES = frozenset({"panel_re_check", "pre_proposal_recheck"})

_CZASOWKA_CK_MANUAL_EDIT_FLAG = (
    "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH"
)
_PANEL_STATUS_IDS_BY_STATE = {
    "planned": frozenset({2}),
    "assigned": frozenset({3, 4, 6}),
    "picked_up": frozenset({5}),
}


def _is_czasowka_order(o: Optional[dict]) -> bool:
    """Czasówka = order_type=='czasowka' LUB prep_minutes >= 60 (≥60 = twarda
    deklaracja restauracji, trzymana w buckecie Koordynatora). Lustrzane do
    panel_watcher._diff_and_emit scope-check."""
    if not o:
        return False
    return (o.get("order_type") == "czasowka"
            or (o.get("prep_minutes") or 0) >= 60)


def build_czasowka_manual_ck_pickup_event(
    existing: Optional[dict],
    ck_payload: Optional[dict],
) -> Optional[dict]:
    """Zamien potwierdzona reczna korekte CK na kanoniczny pickup event.

    Gastro nie daje autora ani timestampu edycji. Daje natomiast boolean
    ``zmiana_czasu_odbioru``. Dopuszczenie jest celowo fail-closed i wymaga
    jednoczesnie:

    * nowej flagi decyzyjnej ON oraz aktywnego passive guarda,
    * czasowki i pasywnego zrodla panel/pre-proposal,
    * krawedzi markera False -> True (nie stalego True),
    * niezmienionego ``pickup_at_warsaw`` w tym samym odczycie,
    * panelowego statusu zgodnego z biezaca klasa stanu.

    Ostatnie dwa warunki odcinaja znane re-stampy przy zmianie statusu. Gdy
    gastro zmienia rowniez pickup, zwykly ``_diff_pickup_time`` pozostaje
    jedynym writerem. Zwracany PICKUP_TIME_UPDATED utrzymuje jeden kanoniczny
    zapis pickup -> czas_kuriera dla czasowek i tym samym pole czytane przez
    aplikacje kuriera.
    """
    existing = existing or {}
    ck_payload = ck_payload or {}

    if not decision_flag(_CZASOWKA_CK_MANUAL_EDIT_FLAG):
        return None
    if not flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True):
        return None
    if not _is_czasowka_order(existing):
        return None
    source = ck_payload.get("source")
    if source not in _CK_PASSIVE_SOURCES:
        return None

    # None/legacy nie jest dowodem False: fail-closed zamiast uznania braku
    # baseline za reczna edycje. NEW_ORDER od 07.05 persistuje jawny bool.
    if existing.get("zmiana_czasu_odbioru") is not False:
        return None
    if ck_payload.get("new_zmiana_czasu_odbioru") is not True:
        return None

    old_pickup = existing.get("pickup_at_warsaw")
    observed_pickup = ck_payload.get("observed_pickup_at_warsaw")
    new_ck_iso = ck_payload.get("new_ck_iso")
    new_ck_hhmm = ck_payload.get("new_ck_hhmm")
    if not old_pickup or not observed_pickup or not new_ck_iso or not new_ck_hhmm:
        return None
    try:
        old_pickup_dt = datetime.fromisoformat(old_pickup)
        observed_pickup_dt = datetime.fromisoformat(observed_pickup)
        new_ck_dt = datetime.fromisoformat(new_ck_iso)
    except (TypeError, ValueError):
        return None
    if old_pickup_dt != observed_pickup_dt:
        return None
    if new_ck_dt.strftime("%H:%M") != new_ck_hhmm:
        return None
    if new_ck_dt == old_pickup_dt:
        return None

    allowed_status_ids = _PANEL_STATUS_IDS_BY_STATE.get(existing.get("status"))
    try:
        observed_status_id = int(ck_payload.get("observed_status_id"))
    except (TypeError, ValueError):
        return None
    if not allowed_status_ids or observed_status_id not in allowed_status_ids:
        return None

    delta_min = round(
        (new_ck_dt - old_pickup_dt).total_seconds() / 60.0, 2
    )
    oid = str(ck_payload.get("oid") or existing.get("order_id") or "")
    if not oid:
        return None
    return {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": oid,
        "courier_id": ck_payload.get("courier_id") or existing.get("courier_id"),
        "payload": {
            "oid": oid,
            "courier_id": ck_payload.get("courier_id") or existing.get("courier_id"),
            "old_pickup_at_warsaw": old_pickup,
            "new_pickup_at_warsaw": new_ck_iso,
            "old_prep_minutes": existing.get("prep_minutes"),
            "new_prep_minutes": ck_payload.get("observed_prep_minutes"),
            "new_decision_deadline": ck_payload.get("observed_decision_deadline"),
            "new_zmiana_czasu_odbioru": True,
            "delta_min": delta_min,
            "source": f"{source}_manual_ck_edit",
            "manual_ck_edit_passthrough": True,
        },
        "event_id_suffix": "_CK_MANUAL_EDIT",
    }


def _ck_backward_delta(
    old_ck_iso: Optional[str],
    new_ck_iso: Optional[str],
) -> Optional[float]:
    """Elastyk forward-only (Adrian 2026-06-24, opcja B). Committed czas_kuriera
    elastyka NIE cofamy pasywnym re-odczytem gastro („przyjazd wcześniej niż
    umówiono" = wobble ETA = śmieć; 5/75 zmian w 5 dni). Forward zostaje
    (koordynatorski +15 / realne spóźnienie). Czasówki mają osobny, mocniejszy
    guard (pickup_at authority) — to dotyczy TYLKO nie-czasówek.

    Zwraca signed delta_min (<0) gdy `new` wcześniejszy niż `old` (= cofnięcie
    do zablokowania). None gdy: brak wartości (np. first_acceptance), parse fail,
    albo ruch do przodu/równy (= dozwolony). None == „przepuść"."""
    if not old_ck_iso or not new_ck_iso:
        return None
    try:
        old_dt = datetime.fromisoformat(old_ck_iso)
        new_dt = datetime.fromisoformat(new_ck_iso)
    except (ValueError, TypeError):
        return None
    delta = (new_dt - old_dt).total_seconds() / 60.0
    return delta if delta < 0 else None


# Zamkniete statusy zlecenia
ORDER_STATUSES = {
    "planned",          # widoczne, jeszcze nieprzypisane
    "assigned",         # przypisane kurierowi (propozycja zatwierdzona)
    "picked_up",        # kurier odebral z restauracji
    "delivered",        # dostarczone
    "returned_to_pool", # wrocilo do puli (partial split / tear-down)
    "cancelled",        # anulowane (klient/restauracja)
}

# Commitment levels (6 poziomow, opinia #6)
COMMITMENT_LEVELS = {
    "planned": 1.0,
    "assigned": 1.2,
    "arrived_at_pickup": 1.5,
    "picked_up": 2.0,
    "en_route_delivery": 2.5,
    "near_delivery": 3.0,
}

if os.environ.get("DISPATCH_UNDER_PYTEST"):
    # Hermetyczne testy obserwera nie dotykaja nawet katalogu logow runtime.
    _log = logging.getLogger("state_machine")
else:
    _log = setup_logger(
        "state_machine", "/root/.openclaw/workspace/scripts/logs/dispatch.log"
    )


# Z-P1-01 Phase A: the formal FSM is an observer only.  Enforcement is
# deliberately hard-OFF (not a runtime flag) until the shadow matrix and
# historical replay have been reviewed.  Nothing in the legacy path branches
# on this value; changing it alone cannot enable enforcement.
ORDER_FSM_OBSERVER_ENABLED = True
ORDER_FSM_ENFORCEMENT_ENABLED = False
_FSM_CURRENT_UNSET = object()
_LIFECYCLE_APPLY_LOCAL = threading.local()
_LIFECYCLE_APPLY_THREAD_LOCK = threading.RLock()
_LIFECYCLE_DOWNSTREAM_LOCAL = threading.local()
_LIFECYCLE_DOWNSTREAM_THREAD_LOCK = threading.RLock()


@contextmanager
def lifecycle_apply_lock():
    """Cross-process, reentrant lock dla outbox version-check -> state apply.

    Osobny sidecar zapobiega deadlockowi z ``_locked_write``. Reentrancja jest
    potrzebna, bo kanoniczny mutator bierze ten lock ponownie. Zakres obejmuje
    outbox precheck i zapis orders_state, ale CELOWO nie obejmuje wolnego
    plan/recanon downstream (osobna kolejka/lock ponizej).
    """
    depth = int(getattr(_LIFECYCLE_APPLY_LOCAL, "depth", 0) or 0)
    if depth:
        _LIFECYCLE_APPLY_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _LIFECYCLE_APPLY_LOCAL.depth -= 1
        return

    # flock serializuje procesy, ale jego semantyka nie gwarantuje wzajemnego
    # wykluczenia dwoch watkow tego samego procesu. RLock domyka ten przypadek;
    # thread-local depth zachowuje reentrancje bez ponownego flock().
    with _LIFECYCLE_APPLY_THREAD_LOCK:
        lock_path = f"{_state_path()}.lifecycle_apply.lock"
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        # os.open daje prawdziwy deskryptor także w testach, które mockują
        # builtins.open dla plików panelu/stanu.
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locked = True
            _LIFECYCLE_APPLY_LOCAL.depth = 1
            _LIFECYCLE_APPLY_LOCAL.fd = lock_fd
            yield
        finally:
            _LIFECYCLE_APPLY_LOCAL.depth = 0
            _LIFECYCLE_APPLY_LOCAL.fd = None
            try:
                if locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


@contextmanager
def lifecycle_downstream_lock():
    """Osobny FIFO-consumer lock; yield True tylko dla outer consumera."""
    depth = int(getattr(_LIFECYCLE_DOWNSTREAM_LOCAL, "depth", 0) or 0)
    if depth:
        _LIFECYCLE_DOWNSTREAM_LOCAL.depth = depth + 1
        try:
            yield False
        finally:
            _LIFECYCLE_DOWNSTREAM_LOCAL.depth -= 1
        return

    with _LIFECYCLE_DOWNSTREAM_THREAD_LOCK:
        lock_path = f"{_state_path()}.lifecycle_downstream.lock"
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locked = True
            _LIFECYCLE_DOWNSTREAM_LOCAL.depth = 1
            yield True
        finally:
            _LIFECYCLE_DOWNSTREAM_LOCAL.depth = 0
            try:
                if locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


def _lifecycle_state_mutation(fn):
    """Kazdy writer orders_state uczestniczy w wersjonowanym protokole C3.

    Sam ``_locked_write`` chroni atomowy RMW pliku, ale nie obejmuje odczytu
    wersji wykonanego w durable outboxie. Ten wspolny, reentrant wrapper sprawia,
    ze bezposredni writer nie moze wejsc pomiedzy check wersji i lifecycle apply.
    """
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with lifecycle_apply_lock():
            return fn(*args, **kwargs)

    return wrapped


def _observe_order_event(event, current=_FSM_CURRENT_UNSET) -> Optional[FsmVerdict]:
    """Run the pure formal FSM validator without affecting legacy behavior.

    Fail-open is intentional in Phase A: validator/read/logging failures are
    diagnostics and must never block, mutate, or replace the existing event
    handler.  Illegal/invalid events get one structured WARNING; explicit
    reconcile/correction exceptions get INFO; ordinary legal events stay DEBUG.
    """
    if not ORDER_FSM_OBSERVER_ENABLED:
        return None
    try:
        if current is _FSM_CURRENT_UNSET:
            oid = event.get("order_id") if isinstance(event, dict) else None
            current = get_order(str(oid)) if oid else None
        verdict = validate_order_event(event, current=current)
        issue_codes = ",".join(verdict.issue_codes) or "none"
        message = (
            "ORDER_FSM_OBSERVER mode=log_only enforcement=hard_off "
            f"would_reject={int(verdict.would_reject)} "
            f"oid={verdict.order_id or '-'} event={verdict.event_type} "
            f"from={verdict.from_status} to={verdict.to_status or '-'} "
            f"outcome={verdict.outcome.value} source={verdict.source or '-'} "
            f"event_id={verdict.event_id or '-'} issues={issue_codes}"
        )
        if verdict.would_reject:
            _log.warning(message)
        elif verdict.outcome in {
            FsmOutcome.RECONCILE_EXCEPTION,
            FsmOutcome.CORRECTION_EXCEPTION,
        }:
            _log.info(message)
        else:
            _log.debug(message)
        return verdict
    except Exception as exc:
        # The observer is never authoritative in Phase A.  In particular, a
        # malformed event must retain exactly the exception/partial-write
        # behavior of the legacy handler below.
        try:
            _log.warning(
                "ORDER_FSM_OBSERVER_FAIL mode=log_only enforcement=hard_off "
                f"error={type(exc).__name__}:{exc}"
            )
        except Exception:
            pass
        return None


def _state_path() -> str:
    """Ścieżka orders_state.json.

    Faza 2b (2026-05-18, diagnoza D2): honoruje override env DISPATCH_STATE_DIR.
    Testy `test_v3275_*` ustawiały tę zmienną wierząc, że izoluje stan — ale
    _state_path jej NIGDY nie czytał → test robił `os.remove` na PRODUKCYJNYM
    `orders_state.json` (incydent 2026-05-18: kasacja stanu floty + residuum
    fixture'ów typu order 469087). Override = realna izolacja per-test."""
    override_dir = os.environ.get("DISPATCH_STATE_DIR")
    if override_dir:
        return os.path.join(override_dir, "orders_state.json")
    path = load_config()["paths"]["orders_state"]
    # Faza 2b guard (klasa Lekcji #75 — leak izolacji testu): pod pytest ŻADEN
    # test nie może operować na produkcyjnym orders_state.json. Brak
    # DISPATCH_STATE_DIR + brak monkeypatcha _state_path = test nieizolowany
    # → raise zamiast pozwolić skasować/zatruć stan całej floty. Świadomy
    # wyjątek (np. read-only smoke na realnym pliku): ALLOW_PROD_STATE_IN_TEST=1.
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ALLOW_PROD_STATE_IN_TEST"):
        raise RuntimeError(
            f"_state_path: pod pytest zwrócono ścieżkę PRODUKCYJNĄ ({path}) — "
            f"test nieizolowany, ryzyko skasowania/zatrucia stanu floty. Napraw: "
            f"env DISPATCH_STATE_DIR=<tmpdir> albo monkeypatch "
            f"state_machine._state_path. Świadomy override: ALLOW_PROD_STATE_IN_TEST=1."
        )
    return path


@contextmanager
def _locked_write():
    """Kontekst: otwiera lock file, trzyma exclusive lock, zwraca sciezke state file.
    Dopiero po yield mozna zapisywac atomic."""
    state_path = Path(_state_path())
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(state_path) + ".lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        yield state_path
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def _backup_prev(path: Path) -> None:
    """Faza 1 backup-on-write: snapshot obecnej wersji state file → .prev
    (1-deep recovery point) PRZED nadpisaniem. Best-effort — porażka backupu
    NIE blokuje głównego zapisu (loguje warning). Atomiczny: copy do temp +
    os.replace na .prev."""
    if not path.exists():
        return
    ptmp = None
    try:
        ptmp_fd, ptmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=".prevtmp_", suffix=".json"
        )
        os.close(ptmp_fd)
        shutil.copy2(path, ptmp)
        os.replace(ptmp, Path(str(path) + ".prev"))
    except Exception as e:
        _log.warning(f"_backup_prev: snapshot .prev nieudany dla {path}: "
                     f"{type(e).__name__}: {e} (zapis główny kontynuuje)")
        if ptmp and os.path.exists(ptmp):
            try:
                os.unlink(ptmp)
            except OSError:
                pass


def ensure_state_directory_durable(path: Optional[Path] = None) -> None:
    """Utrwal wpis katalogowy aktualnego pliku orders_state."""
    path = Path(_state_path()) if path is None else Path(path)
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    dir_fd = os.open(str(path.parent), dir_flags)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _atomic_write(path: Path, data: dict):
    """Zapis temp -> fsync -> replace -> fsync katalogu (trwale na POSIX).
    Faza 1: przed nadpisaniem robi snapshot obecnej wersji → .prev."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp_", suffix=".json"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _backup_prev(path)          # Faza 1: 1-deep recovery snapshot
        os.replace(tmp_path, path)
        # Sam fsync pliku tymczasowego nie utrwala wpisu katalogowego rename.
        # Outbox SQLite moze zostac oznaczony applied zaraz po tym zapisie, wiec
        # awaria hosta nie moze przywrocic starego orders_state przy trwalszym
        # receipcie. Blad fsync katalogu jest bledem zapisu, nie best-effort.
        ensure_state_directory_durable(path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _guarded_write(path: Path, new_state: dict, old_count: int, op: str):
    """Faza 1 count-regression guard: zapis state z weryfikacją liczności.

    upsert_order / set_status / touch_check_cursor NIGDY nie zmniejszają
    liczby zleceń (tylko dodają/aktualizują). delete_order zmniejsza o
    dokładnie 1. Każde inne zmniejszenie = oznaka clobberu (np. czytany stan
    był niekompletny) → raise StateReadError, NIE zapisuj.

    Defense-in-depth: łapie KAŻDY przyszły bug kurczący stan, nie tylko znany
    wektor _read_state→{}. Kill-switch: ENABLE_STATE_WRITE_GUARD=false
    (flags.json) — wyłącza guard, przywraca surowy _atomic_write."""
    if not flag("ENABLE_STATE_WRITE_GUARD", True):
        _atomic_write(path, new_state)
        return
    new_count = len(new_state)
    if op == "delete":
        ok = new_count >= old_count - 1
    else:  # upsert / set_status / touch — add/update only, count nie maleje
        ok = new_count >= old_count
    if not ok:
        detail = (f"_guarded_write: regresja liczności state {old_count}->{new_count} "
                  f"przy op={op!r} — zapis ZABLOKOWANY (możliwy clobber orders_state)")
        _alert_state_read_failure(detail)
        raise StateReadError(detail)
    _atomic_write(path, new_state)


# Faza 1: throttled alert gdy state RMW odmawia zapisu (clobber prevention).
_STATE_READ_ALERT_COOLDOWN_S = 300.0
_last_state_read_alert_ts = 0.0


def _alert_state_read_failure(detail: str) -> None:
    """Faza 1: loud, throttled (5 min) admin alert gdy RMW writer przerywa
    zapis (orders_state nieczytelny ALBO regresja liczności).

    Lazy import telegram_utils — state_machine to moduł niskopoziomowy, nie
    ciągnie zależności na sztywno. send_admin_alert sam refuse'uje pod pytest
    (Lekcja #75). Best-effort: nigdy nie raise (alert nie może zablokować
    głównej ścieżki ani zamaskować pierwotnego StateReadError)."""
    global _last_state_read_alert_ts
    now = time.monotonic()
    if now - _last_state_read_alert_ts < _STATE_READ_ALERT_COOLDOWN_S:
        return
    _last_state_read_alert_ts = now
    _log.error(f"STATE WRITE GUARD: {detail}")
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(
            f"🛑 STATE WRITE GUARD — RMW writer przerwany\n\n{detail}\n\n"
            f"Stan NIE został nadpisany (ochrona przed clobberem orders_state). "
            f"Eventy zostają w events.db (append-only → odtwarzalne). "
            f"Sprawdź dispatch_state/orders_state.json i logi state_machine."
        )
    except Exception as e:
        _log.warning(f"_alert_state_read_failure: alert nieudany: "
                     f"{type(e).__name__}: {e}")


def _read_state() -> dict:
    """Czyta state z shared lock + retry (P0.5b Fix #2).

    Problem: watcher 20s + sla_tracker 10s odczytują concurrent. Podczas atomic
    rename pojawia sie okno gdzie plik chwilowo nie istnieje LUB jest partial.
    Fix: 3 retry z exponential backoff (50/100/200 ms) + fcntl.LOCK_SH.

    Zwraca {} jesli plik nie istnieje po 3 retries (nie traci state silently —
    loguje warning). JSONDecodeError → zwraca {} + error log.
    """
    path = Path(_state_path())
    for attempt in range(3):
        try:
            with open(path) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            if attempt == 2:
                _log.warning(f"_read_state: {path} not found after 3 retries")
                return {}
            time.sleep(0.05 * (2 ** attempt))  # 50ms, 100ms, 200ms
        except json.JSONDecodeError as e:
            _log.error(f"JSONDecodeError w {path}: {e}. Zwracam pusty state.")
            return {}
    return {}


def state_storage_token() -> str:
    """Token snapshotu *treści* pliku, niezależny od zegara.

    Używany wyłącznie w rzadkiej ścieżce, gdy strict JSON read zawiódł przed
    emisją durable eventu. Hash pozwala później dowieść, że żaden writer nie
    zmienił surowego state; mtime/updated_at nie są oracle przy korekcie zegara.
    """
    path = Path(_state_path())
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError:
        return "missing:sha256"
    return f"sha256:{digest.hexdigest()}"


def _is_bootstrap() -> bool:
    """Faza 1: True TYLKO gdy orders_state.json nigdy nie istniał (świeża
    instalacja). Obecność backupu .prev oznacza, że plik istniał wcześniej —
    więc jego brak teraz to anomalia (skasowany / zniknął), NIE bootstrap.

    Dzięki temu _read_state_strict odróżnia legalny pierwszy zapis od
    sytuacji „plik zniknął" (incydent 2026-05-18) i nie pozwala RMW writerowi
    odtworzyć stanu z jednym zleceniem zamiast całej floty."""
    return not Path(str(_state_path()) + ".prev").exists()


def _read_state_strict() -> dict:
    """Faza 1: zwraca state ALBO raise StateReadError. Wyłącznie dla RMW
    writerów (upsert/set_status/touch/delete).

    W przeciwieństwie do _read_state() NIGDY nie zwraca {} przez fallback —
    cichy {} z RMW nadpisałby cały orders_state.json. Pusty wynik dozwolony
    tylko przy świadomym bootstrapie (plik nigdy nie istniał)."""
    path = Path(_state_path())
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            with open(path) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.05 * (2 ** attempt))  # 50ms, 100ms
        except json.JSONDecodeError as e:
            last_err = e
            break  # malformed — retry nie pomoże, plik istnieje ale zepsuty
    if isinstance(last_err, FileNotFoundError) and _is_bootstrap():
        _log.warning(f"_read_state_strict: {path} nie istnieje — bootstrap (świeża instalacja)")
        return {}
    detail = (f"_read_state_strict: {path} nieczytelny po retry "
              f"({type(last_err).__name__}: {last_err}) — RMW przerwany, "
              f"NIE nadpisuję orders_state (ochrona przed clobberem)")
    _alert_state_read_failure(detail)
    raise StateReadError(detail)


def get_all() -> dict:
    """Zwraca caly state. Uzywaj ostroznie - kopiuj jesli modyfikujesz."""
    return _read_state()


def get_all_strict() -> dict:
    """Fail-closed pełny snapshot dla granic state→zewnętrzny writer."""
    return _read_state_strict()


def get_order(order_id: str) -> Optional[dict]:
    """Zwraca pojedyncze zlecenie lub None."""
    return _read_state().get(order_id)


def get_order_strict(order_id: str) -> Optional[dict]:
    """Fail-closed odczyt jednego zlecenia dla granic read→write.

    Zwykli read-only konsumenci zachowuja historyczny kontrakt ``get_order``
    (fallback do pustego stanu). Durable event bridge nie moze jednak pomylic
    chwilowo brakujacego/uszkodzonego pliku z prawdziwym brakiem rekordu, bo
    taki falszywy ``None`` staje sie wersja oczekiwana outboxa. Dlatego tylko
    granica C3 uzywa strict readera wspolnego z kanonicznymi RMW writerami.
    """
    return _read_state_strict().get(order_id)


def get_by_status(status: str) -> list:
    """Zwraca liste zlecen w danym statusie."""
    state = _read_state()
    return [o for o in state.values() if o.get("status") == status]


def get_by_courier(courier_id: str, statuses: Optional[list] = None) -> list:
    """Zwraca zlecenia przypisane kurierowi. Opcjonalny filtr statusow."""
    state = _read_state()
    result = [o for o in state.values() if o.get("courier_id") == courier_id]
    if statuses:
        result = [o for o in result if o.get("status") in statuses]
    return result


def event_effect_status(
    event: dict,
    current=_FSM_CURRENT_UNSET,
) -> str:
    """Stan postcondition: ``applied`` / ``pending`` / ``superseded``.

    Sam bool nie wystarcza: terminal nowszy od oczekującego eventu nie jest ani
    "brakiem apply", ani dowodem, że wolno odtworzyć stary event. ``superseded``
    zatrzymuje stale/out-of-order retry. Funkcja jest read-only; weryfikację
    wersji ``updated_at`` robi durable outbox.
    """
    oid = event.get("order_id")
    if not oid:
        return "pending"
    if current is _FSM_CURRENT_UNSET:
        current = get_order(str(oid))
    if not current:
        return "pending"

    etype = event.get("event_type")
    payload = event.get("payload") or {}
    status = current.get("status")
    if etype == "NEW_ORDER":
        # Późniejsze lifecycle states także dowodzą, że NEW_ORDER był zastosowany.
        return "applied"
    if etype == "COURIER_ASSIGNED":
        # Nie odtwarzaj starego assignment po zamknięciu lub pickupie innego
        # kuriera. Legalny nowy reassign dostaje osobną generację outbox.
        if status in ("delivered", "returned_to_pool", "cancelled"):
            return "superseded"
        matches = (
            status in ("assigned", "picked_up")
            and str(current.get("courier_id") or "")
            == str(event.get("courier_id") or "")
        )
        ck_iso = payload.get("czas_kuriera_warsaw")
        ck_hhmm = payload.get("czas_kuriera_hhmm")
        ck_valid = _verify_czas_kuriera_consistency(ck_iso, ck_hhmm, str(oid))
        # Handler przy uszkodzonym CK nadal trwale stosuje SAM assignment, ale
        # odrzuca oba pola czasu i podnosi CorruptedTimestampError. Oracle musi
        # wtedy oceniac postcondition assignmentu bez wadliwych pol; exact marker
        # rozstrzyga crash po tym czesciowym, swiadomym commicie.
        if matches and ck_valid and ck_iso is not None:
            matches = current.get("czas_kuriera_warsaw") == ck_iso
        if matches and ck_valid and ck_hhmm is not None:
            matches = current.get("czas_kuriera_hhmm") == ck_hhmm
        if matches:
            return "applied"
        if status == "picked_up":
            return "superseded"
        return "pending"
    if etype == "COURIER_PICKED_UP":
        event_courier = str(event.get("courier_id") or "")
        current_courier = str(current.get("courier_id") or "")
        if event_courier and event_courier != current_courier:
            return "superseded"
        if status == "picked_up":
            if (
                payload.get("source") == "parcel_status_inbox"
                and str(
                    current.get("last_lifecycle_event_id_courier_picked_up") or ""
                )
                != str(event.get("event_id") or "")
            ):
                # The inbox key contains its source generation timestamp, but
                # business state intentionally does not adopt that timestamp.
                # Status+same courier therefore cannot prove this *new* row;
                # only its exact durable marker can acknowledge a crash retry.
                return "superseded"
            return "applied"
        if status in ("delivered", "returned_to_pool", "cancelled"):
            return "superseded"
        return "pending"
    if etype == "COURIER_DELIVERED":
        if payload.get("source") == "parcel_status_inbox":
            event_courier = str(event.get("courier_id") or "")
            current_courier = str(current.get("courier_id") or "")
            if event_courier and event_courier != current_courier:
                return "superseded"
        if status == "delivered":
            if (
                payload.get("source") == "parcel_status_inbox"
                and str(
                    current.get("last_lifecycle_event_id_courier_delivered") or ""
                )
                != str(event.get("event_id") or "")
            ):
                return "superseded"
            return "applied"
        if status in ("returned_to_pool", "cancelled"):
            return "superseded"
        return "pending"
    if etype == "ORDER_RESURRECTED":
        desired = str(payload.get("new_status") or "picked_up")
        if desired not in ("assigned", "picked_up"):
            desired = "picked_up"
        event_courier = str(event.get("courier_id") or "")
        current_courier = str(current.get("courier_id") or "")
        if event_courier and event_courier != current_courier:
            return "superseded"
        if status == desired:
            return "applied"
        if status == "delivered":
            return "pending"
        return "superseded"
    if etype == "ORDER_RETURNED_TO_POOL":
        if status == "returned_to_pool":
            return "applied"
        if status in ("delivered", "cancelled"):
            return "superseded"
        return "pending"
    if etype == "CZAS_KURIERA_UPDATED":
        if status in ("delivered", "returned_to_pool", "cancelled"):
            return "superseded"
        new_ck_iso = payload.get("new_ck_iso")
        new_ck_hhmm = payload.get("new_ck_hhmm")
        if not _verify_czas_kuriera_consistency(
            new_ck_iso, new_ck_hhmm, str(oid)
        ):
            # Trwale wadliwy payload nie moze pozostac poison-rowem pending i
            # blokowac causal/downstream lanes. Event jest zachowany w audycie, ale
            # jego state/downstream zostaja terminalnie pominiete.
            return "superseded"
        source = payload.get("source")
        if (
            flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)
            and _is_czasowka_order(current)
            and source in _CK_PASSIVE_SOURCES
        ):
            return "superseded"
        if (
            flag("ENABLE_ELASTYK_CK_NO_BACKWARD", True)
            and not _is_czasowka_order(current)
            and source in _CK_PASSIVE_SOURCES
            and _ck_backward_delta(
                current.get("czas_kuriera_warsaw"), new_ck_iso
            ) is not None
        ):
            return "superseded"
        return "applied" if (
            current.get("czas_kuriera_warsaw") == new_ck_iso
            and current.get("czas_kuriera_hhmm") == new_ck_hhmm
        ) else "pending"
    if etype == "PICKUP_TIME_UPDATED":
        if status in ("delivered", "returned_to_pool", "cancelled"):
            return "superseded"
        new_pickup = payload.get("new_pickup_at_warsaw")
        try:
            if not new_pickup:
                raise ValueError("missing new_pickup_at_warsaw")
            datetime.fromisoformat(str(new_pickup))
        except (ValueError, TypeError):
            return "superseded"
        return (
            "applied"
            if current.get("pickup_at_warsaw")
            == new_pickup
            else "pending"
        )
    return "pending"


def event_effect_is_applied(
    event: dict,
    current=_FSM_CURRENT_UNSET,
) -> bool:
    """Kompatybilny bool-oracle; stale terminal NIE jest "applied"."""
    return event_effect_status(event, current=current) == "applied"


def _sanitize_ingest_coords(order_id: str, data: dict) -> dict:
    """L2.1 sentinel-ingest (2026-07-01, K5a): JEDEN chokepoint walidacji coords
    na wejściu do orders_state — pokrywa NEW_ORDER (oba branche), COURIER_PICKED_UP,
    COURIER_DELIVERED, parcel_lane_merge i każdego przyszłego writera przez upsert.

    Wartość niepoprawna ((0,0)/NaN/poza-bbox — `coords_in_bialystok_bbox`) →
    klucz USUWANY z data (merge {**existing, **data} zachowuje ewentualne DOBRE
    istniejące coords — wzorzec sink-guard 2026-06-13) + log.warning. Flaga OFF
    = pass-through legacy. Zwraca data (kopię przy modyfikacji)."""
    if not decision_flag("ENABLE_COORD_SENTINEL_INGEST_GUARD"):
        return data
    bad = [
        k for k in ("pickup_coords", "delivery_coords")
        if k in data and data[k] is not None
        and not coords_in_bialystok_bbox(data[k])
    ]
    if not bad:
        return data
    data = dict(data)
    for k in bad:
        _log.warning(
            f"COORD_INGEST_GUARD upsert {order_id}: {k}={data[k]!r} "
            f"odrzucone (sentinel/poza-bbox) — klucz pominięty"
        )
        del data[k]
    return data


# ── R-DECLARED tripwire (L7.1, audyt 2026-06-30 root R7-I-E) ──────────────────
# Reguła R-DECLARED-TIME (HARD): `czas_kuriera >= czas_odbioru_timestamp` — dziś
# NIE ma runtime-inwariantu (tylko komentarze; egzekucja pośrednia przez SOFT
# R27 → zmiana R27 cicho ją łamie). JEDEN obserwacyjny tripwire w chokepoincie
# zapisu (upsert_order = jedyny funnel commitowanego czas_kuriera do orders_state
# — pokrywa NEW_ORDER / COURIER_ASSIGNED / CZAS_KURIERA_UPDATED / PICKUP_TIME_
# UPDATED / resurrect / każdego przyszłego writera). Fail-loud LOG + append JSONL,
# NIGDY reject/zmiana `merged` (always-propose). OFF = zero kodu ścieżki.
#
# Edge/throttle per-oid: ta sama para (czas_kuriera, czas_odbioru) logowana RAZ
# (nie spamuje co tick/re-upsert). Zmiana którejkolwiek wartości = nowy stan
# naruszenia = ponowny wpis. Pamięć throttle = module-level (proces długożyjący:
# shadow/panel-watcher/plan-recheck); cap bezpieczeństwa vs nieograniczony wzrost.
_R_DECLARED_LOGGED: dict = {}       # oid -> (ck_iso, pickup_iso) ostatnio zalogowane naruszenie
_R_DECLARED_LOGGED_CAP = 10000      # audyt: żadnych nieograniczonych cache — reset przy przepełnieniu


def _to_warsaw_axis(s: Optional[str]) -> Optional[datetime]:
    """Parsuje ISO timestamp na WSPÓLNĄ oś porównania (aware, Europe/Warsaw).

    `czas_kuriera_warsaw` = aware ISO z offsetem (+02:00). `pickup_at_warsaw`
    (= czas_odbioru_timestamp) też aware Warsaw w praktyce, ale bywa naive w
    historycznych/alternatywnych ścieżkach → naive traktujemy jako Warsaw-local
    przez ZoneInfo (NIGDY fixed-offset — DST by złamał; ratchet TZ). Zwraca
    aware datetime (porównanie instant-owe, poprawne pod DST) lub None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_WARSAW_TZ)
    return dt


def _r_declared_tripwire(order_id: str, merged: dict, event: Optional[str]) -> None:
    """L7.1: obserwacyjny strażnik R-DECLARED-TIME na zmergowanym rekordzie.

    NIGDY nie modyfikuje `merged` ani nie wpływa na decyzję — tylko log + JSONL.
    Flaga OFF = natychmiastowy return (bajt-parytet ścieżki decyzji). Defensywny:
    żaden wyjątek stąd nie może przerwać zapisu stanu (opakowane w callerze)."""
    if not flag("ENABLE_R_DECLARED_TRIPWIRE", ENABLE_R_DECLARED_TRIPWIRE):
        return
    ck = _to_warsaw_axis(merged.get("czas_kuriera_warsaw"))
    pickup = _to_warsaw_axis(merged.get("pickup_at_warsaw"))
    if ck is None or pickup is None:
        return  # brak którejkolwiek deklaracji — nic do sprawdzenia
    delta_min = (ck - pickup).total_seconds() / 60.0
    # Reguła: czas_kuriera >= czas_odbioru. Naruszenie = ck wcześniejszy niż
    # pickup poza tolerancją (default 0.0 = ścisła nierówność).
    if delta_min >= -R_DECLARED_TRIPWIRE_TOLERANCE_MIN:
        return
    ck_iso = merged.get("czas_kuriera_warsaw")
    pickup_iso = merged.get("pickup_at_warsaw")
    sig = (ck_iso, pickup_iso)
    if _R_DECLARED_LOGGED.get(order_id) == sig:
        return  # to samo naruszenie już zalogowane — throttle (edge-triggered)
    if len(_R_DECLARED_LOGGED) >= _R_DECLARED_LOGGED_CAP:
        _R_DECLARED_LOGGED.clear()  # reset bezpieczeństwa (najwyżej pojedynczy re-log)
    _R_DECLARED_LOGGED[order_id] = sig
    record = {
        "ts": now_iso(),
        "oid": order_id,
        "event": event,                       # źródło zapisu (NEW_ORDER / COURIER_ASSIGNED / ...)
        "status": merged.get("status"),
        "order_type": merged.get("order_type"),
        "czas_kuriera_hhmm": merged.get("czas_kuriera_hhmm"),
        "czas_kuriera_warsaw": ck_iso,
        "czas_odbioru_timestamp": pickup_iso,  # = pickup_at_warsaw
        "delta_min": round(delta_min, 2),
        "courier_id": merged.get("courier_id"),
    }
    _log.warning(
        f"R_DECLARED_VIOLATION oid={order_id} event={event} "
        f"czas_kuriera={merged.get('czas_kuriera_hhmm')} ({ck_iso}) < "
        f"czas_odbioru={pickup_iso} Δ={delta_min:+.1f}min "
        f"(R-DECLARED-TIME HARD — obserwacyjny, decyzja NIEzmieniana)"
    )
    try:
        log_path = os.path.join(os.path.dirname(_state_path()), "r_declared_tripwire.jsonl")
        append_jsonl(log_path, record)
    except Exception as _e:
        _log.debug(f"R_DECLARED tripwire jsonl append skip oid={order_id}: {_e}")


@_lifecycle_state_mutation
def upsert_order(order_id: str, data: dict, event: Optional[str] = None) -> dict:
    """Dodaje lub aktualizuje zlecenie. Zapisuje history entry.
    Zwraca zaktualizowany rekord."""
    data = _sanitize_ingest_coords(order_id, data)
    with _locked_write() as path:
        state = _read_state_strict()        # Faza 1: raise StateReadError zamiast cichego {}
        old_count = len(state)
        existing = state.get(order_id, {})
        merged = {**existing, **data, "order_id": order_id}

        # History
        history = existing.get("history", [])
        if event:
            history.append({"at": now_iso(), "event": event, "status": merged.get("status")})
        merged["history"] = history
        merged["updated_at"] = now_iso()

        state[order_id] = merged
        _guarded_write(path, state, old_count, op="upsert")
        _log.info(f"upsert {order_id} status={merged.get('status')} event={event}")
        # L7.1 R-DECLARED tripwire — obserwacyjny, PO commicie zapisu; nigdy nie
        # zmienia `merged` ani decyzji. Defensywnie: żaden błąd stąd nie wpływa
        # na zwrot (already-persisted record).
        try:
            _r_declared_tripwire(order_id, merged, event)
        except Exception as _tw_e:
            _log.debug(f"R_DECLARED tripwire skip oid={order_id}: {_tw_e}")
        return merged


@_lifecycle_state_mutation
def set_status(order_id: str, status: str, extra: Optional[dict] = None, event: Optional[str] = None) -> Optional[dict]:
    """Zmiana statusu + dodatkowe pola."""
    if status not in ORDER_STATUSES:
        raise ValueError(f"Nieznany status: {status}. Dozwolone: {ORDER_STATUSES}")
    data = {"status": status}
    if extra:
        data.update(extra)
    return upsert_order(order_id, data, event=event)


@_lifecycle_state_mutation
def update_from_event(event: dict) -> Optional[dict]:
    """Konsumuje event z event busa i aktualizuje state machine.
    Zwraca zaktualizowany rekord lub None."""
    # Z-P1-01 Phase A: formal FSM shadow.  It is intentionally fail-open and
    # log-only; legacy behavior below (including current fallbacks/exceptions)
    # remains the sole writer until a separately approved enforcement phase.
    _observe_order_event(event)
    etype = event["event_type"]
    oid = event.get("order_id")
    payload = event.get("payload", {})
    if not oid:
        return None

    durable_event_id = event.get("event_id")

    def _marked(fields: dict) -> dict:
        """Powiaz exact outbox event z tym samym atomowym zapisem stanu."""
        if not durable_event_id:
            return fields
        marked = dict(fields)
        marked["last_lifecycle_event_id"] = str(durable_event_id)
        marker_type = "".join(
            ch.lower() if ch.isalnum() else "_" for ch in str(etype)
        ).strip("_")
        if marker_type:
            # Marker per typ nie ginie po ortogonalnym evencie (np. ASSIGNED,
            # potem CZAS_KURIERA_UPDATED przed receiptem outboxa).
            marked[f"last_lifecycle_event_id_{marker_type}"] = str(durable_event_id)
        return marked

    if etype == "NEW_ORDER":
        # V3.19f: sanity check czas_kuriera consistency przed persist.
        ck_iso = payload.get("czas_kuriera_warsaw")
        ck_hhmm = payload.get("czas_kuriera_hhmm")
        if not _verify_czas_kuriera_consistency(ck_iso, ck_hhmm, oid):
            # Skip persist czas_kuriera fields; log ERROR w helper; raise signal.
            # Inne pola persistowane bez zmian (order dalej trafia do state).
            ck_iso = None
            ck_hhmm = None
            _result = upsert_order(oid, _marked({
                "status": "planned",
                "commitment_level": "planned",
                "restaurant": payload.get("restaurant"),
                "pickup_address": payload.get("pickup_address"),
                "delivery_address": payload.get("delivery_address"),
                "pickup_time_minutes": payload.get("pickup_time_minutes"),
                "first_seen": payload.get("first_seen") or now_iso(),
                "address_id": payload.get("address_id"),
                "pickup_coords": payload.get("pickup_coords"),
                "delivery_coords": payload.get("delivery_coords"),
                "pickup_at_warsaw": payload.get("pickup_at_warsaw"),
                "prep_minutes": payload.get("prep_minutes"),
                "order_type": payload.get("order_type"),
                "bag_time_alerted": False,
                # Tech debt #19a/b/c (2026-05-07) — audit + SLA fields:
                "decision_deadline": payload.get("decision_deadline"),
                "zmiana_czasu_odbioru": payload.get("zmiana_czasu_odbioru"),
                "created_at_utc": payload.get("created_at_utc"),
            }), event="NEW_ORDER")
            raise CorruptedTimestampError(
                f"NEW_ORDER {oid}: czas_kuriera sanity fail, "
                f"persisted bez czas_kuriera fields"
            )
        return upsert_order(oid, _marked({
            "status": "planned",
            "commitment_level": "planned",
            "restaurant": payload.get("restaurant"),
            "pickup_address": payload.get("pickup_address"),
            "delivery_address": payload.get("delivery_address"),
            "pickup_time_minutes": payload.get("pickup_time_minutes"),
            "first_seen": payload.get("first_seen") or now_iso(),
            "address_id": payload.get("address_id"),
            "pickup_coords": payload.get("pickup_coords"),
            "delivery_coords": payload.get("delivery_coords"),
            "pickup_at_warsaw": payload.get("pickup_at_warsaw"),
            "prep_minutes": payload.get("prep_minutes"),
            "order_type": payload.get("order_type"),
            "uwagi": payload.get("uwagi"),
            "uwagi_pickup_parsed": payload.get("uwagi_pickup_parsed"),
            # CZASÓWKA-W-UWAGACH SHADOW (2026-06-28, sesja 20): deklarowany deadline
            # DOSTAWY sparsowany z `uwagi` (panel_client, za flagą). ADDITYWNE — żaden
            # konsument decyzyjny go jeszcze nie czyta. None gdy flaga OFF / brak frazy.
            "delivery_deadline_uwagi": payload.get("delivery_deadline_uwagi"),
            # V3.19f: czas_kuriera 2-field persist (ISO Warsaw + raw HH:MM).
            "czas_kuriera_warsaw": ck_iso,
            "czas_kuriera_hhmm": ck_hhmm,
            "bag_time_alerted": False,  # F2.1b step 5: R6 pre-warning gate init
            # Tech debt #19a/b/c (2026-05-07) — audit + SLA fields:
            # decision_deadline (czas_na_decyzje), zmiana_czasu_odbioru (panel
            # zmienił pickup time flag), created_at_utc (single age anchor).
            "decision_deadline": payload.get("decision_deadline"),
            "zmiana_czasu_odbioru": payload.get("zmiana_czasu_odbioru"),
            "created_at_utc": payload.get("created_at_utc"),
        }), event="NEW_ORDER")

    if etype == "COURIER_ASSIGNED":
        # V3.28 P4 — auto-activation koordynatora (Adrian doktryna 2026-05-10).
        # Bartek O. (cid=123) ma flag `coordinator: true` w courier_tiers.json.
        # Pierwsze COURIER_ASSIGNED dnia → activate (może już dziś jeździć).
        # Późniejsze ASSIGNED zachowują state (idempotent).
        try:
            _ev_cid = str(event.get("courier_id") or "")
            if _ev_cid:
                from dispatch_v2.courier_resolver import _load_courier_tiers
                from dispatch_v2 import coordinator_activations as _coord_act
                _tiers = _load_courier_tiers()
                _tinfo = _tiers.get(_ev_cid) if isinstance(_tiers, dict) else None
                if isinstance(_tinfo, dict) and _tinfo.get("coordinator") is True:
                    _changed = _coord_act.activate(_ev_cid, source=f"first_assignment_{oid}")
                    if _changed:
                        _log.info(
                            f"P4 COORDINATOR_ACTIVATED cid={_ev_cid} ({_tinfo.get('name','?')}) "
                            f"trigger=first_assignment oid={oid}"
                        )
        except Exception as _e:
            _log.warning(f"P4 coordinator auto-activate fail oid={oid}: {_e}")

        # V3.19f: update czas_kuriera przy re-assignment (panel "+15min" button
        # może zmienić commitment). Sanity check przed update.
        # V3.27.5 Path B (2026-04-27): preserve terminal status (picked_up,
        # delivered) na subsequent COURIER_ASSIGNED. Pre-fix: panel_diff
        # COURIER_ASSIGNED post-PICKED_UP nadpisywał status="picked_up" → "assigned",
        # tworząc inconsistency (status=assigned + picked_up_at SET) — TASK H
        # diagnoza 2026-04-27 wykryła 13.4% rate (185/1384 picked-up orders 7d).
        # Race condition: PICKED_UP (reconcile) + COURIER_ASSIGNED (panel_diff)
        # fire same panel_watcher cycle, ASSIGNED ~12-18s later → status revert.
        ck_iso = payload.get("czas_kuriera_warsaw")
        ck_hhmm = payload.get("czas_kuriera_hhmm")
        # V3.27.5 Path B: check current status — preserve terminal states
        prev = get_order(oid) or {}
        prev_status = prev.get("status")
        if prev_status in ("picked_up", "delivered"):
            # Order już terminal — preserve status. Update tylko legitimate
            # re-assignment fields (courier_id, czas_kuriera) jeśli zmienione.
            new_status = prev_status
            _log.warning(
                f"COURIER_ASSIGNED {oid} ignored status revert: "
                f"prev_status={prev_status}, source={event.get('source','?')}, "
                f"courier_id_new={event.get('courier_id')} courier_id_old={prev.get('courier_id')} "
                f"(V3.27.5 Path B preserve terminal)"
            )
        else:
            new_status = "assigned"
        merged = {
            "status": new_status,
            "commitment_level": new_status if new_status in ("picked_up", "delivered") else "assigned",
            "courier_id": event.get("courier_id"),
            "assigned_at": now_iso(),
            "proposed_delivery_time": payload.get("proposed_time"),
            "bag_time_alerted": False,  # F2.1b step 5: reset on new assignment / reassignment
        }
        # L4 (2026-07-02, F1) CHOKEPOINT: NOWE POLE effective_pickup_at =
        # max(deklarowany czas odbioru, available_from) OBOK deklaracji. Deklaracja
        # restauracji (czas_kuriera/pickup_at) NIETYKALNA (Q2, frozen R27 ±5) — tu
        # tylko SURFACUJEMY realny najwcześniejszy odbiór respektujący start zmiany
        # kuriera (available_from=max(now,shift_start) z courier_resolver). Bez
        # konsumentów na razie (pas renderów = fala L3). Gated; OFF = pole nie powstaje.
        if decision_flag("ENABLE_AVAILABLE_FROM_SINGLE_SOURCE"):
            try:
                from dispatch_v2 import courier_resolver as _CR_af
                _now_af = now_utc()
                _af_dt, _af_src = _CR_af.resolve_available_from_by_cid(
                    event.get("courier_id"), _now_af)
                _decl_raw = ck_iso or prev.get("czas_kuriera_warsaw")
                _decl_dt = None
                if _decl_raw:
                    try:
                        _decl_dt = datetime.fromisoformat(str(_decl_raw).replace("Z", "+00:00"))
                        if _decl_dt.tzinfo is None:  # parytet PR._parse_dt: naive→UTC (real=aware +02:00)
                            _decl_dt = _decl_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        _decl_dt = None
                if _decl_dt is not None and _af_dt is not None:
                    _eff = max(_decl_dt, _af_dt)
                    _eff_src = "available_from" if _af_dt > _decl_dt else "declared"
                elif _af_dt is not None:
                    _eff, _eff_src = _af_dt, "available_from"
                else:
                    _eff, _eff_src = _decl_dt, "declared"
                if _eff is not None:
                    # NIE nadpisujemy czas_kuriera_warsaw/pickup_at — osobne pole.
                    merged["effective_pickup_at"] = _eff.astimezone(timezone.utc).isoformat()
                    merged["effective_pickup_source"] = _eff_src
                    merged["effective_pickup_af_source"] = _af_src
            except Exception as _eff_e:
                _log.debug(f"L4 effective_pickup_at skip oid={oid}: {_eff_e}")
        if ck_iso is not None or ck_hhmm is not None:
            if _verify_czas_kuriera_consistency(ck_iso, ck_hhmm, oid):
                # Source-block (Adrian 2026-06-24): CZASÓWKA z już ustalonym
                # committed czas_kuriera — NIE nadpisuj odczytem z assignu
                # (pasywny read gastro, może być już przestempl­owany). Umówiony
                # czas czasówki rządzi pickup_at. Przypisanie i tak zapisujemy.
                if (flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)
                        and _is_czasowka_order(prev)
                        and prev.get("czas_kuriera_warsaw")):
                    _log.info(
                        f"CK_PASSIVE_SUPPRESSED oid={oid} czasówka (COURIER_ASSIGNED) "
                        f"keep committed {prev.get('czas_kuriera_hhmm')} "
                        f"(ignore assign read {ck_hhmm})"
                    )
                    return upsert_order(oid, _marked(merged), event="COURIER_ASSIGNED")
                merged["czas_kuriera_warsaw"] = ck_iso
                merged["czas_kuriera_hhmm"] = ck_hhmm
                _result = upsert_order(oid, _marked(merged), event="COURIER_ASSIGNED")
                return _result
            else:
                # Skip persist czas_kuriera; log ERROR done; raise after upsert.
                _result = upsert_order(oid, _marked(merged), event="COURIER_ASSIGNED")
                raise CorruptedTimestampError(
                    f"COURIER_ASSIGNED {oid}: czas_kuriera sanity fail, "
                    f"persisted bez czas_kuriera update"
                )
        return upsert_order(oid, _marked(merged), event="COURIER_ASSIGNED")

    if etype == "CZAS_KURIERA_UPDATED":
        # V3.19g1: panel_watcher detected czas_kuriera change (|Δt| ≥ 3min)
        # for already-assigned order. Update ck fields ONLY, preserve status,
        # courier_id, commitment_level, etc. Sanity check via V3.19f helper.
        new_ck_iso = payload.get("new_ck_iso")
        new_ck_hhmm = payload.get("new_ck_hhmm")
        if not _verify_czas_kuriera_consistency(new_ck_iso, new_ck_hhmm, oid):
            _log.error(
                f"CZAS_KURIERA_UPDATED {oid}: sanity fail "
                f"(iso={new_ck_iso!r} hhmm={new_ck_hhmm!r}), skipping persist"
            )
            return None
        existing = get_order(oid)
        if existing is None:
            _log.warning(f"CZAS_KURIERA_UPDATED for unknown oid={oid}, skipping")
            return None
        # Source-block (Adrian 2026-06-24, root #483023): CZASÓWKA — pasywny
        # re-odczyt gastro (panel_re_check / pre_proposal_recheck) NIE zmienia
        # committed czas_kuriera (to przestempl­owany przy zmianie statusu śmieć).
        # Umówiony czas czasówki rządzi pickup_at (PICKUP_TIME_UPDATED, dowolny
        # kierunek). first_acceptance + kanały deliberatne (np. ziomek_late_
        # extension/coordinator_edit) NIE są w _CK_PASSIVE_SOURCES → przechodzą.
        _src = payload.get("source")
        # Incydent #489052: pasywny producer moze niesc pozytywny sygnal
        # recznej korekty gastro. Nie omijamy kanonu bezposrednim zapisem CK:
        # tlumaczymy go na PICKUP_TIME_UPDATED, czyli writer, ktory atomowo
        # aktualizuje pickup_at i pole czasu czytane przez aplikacje kuriera.
        _manual_pickup_evt = build_czasowka_manual_ck_pickup_event(existing, payload)
        if _manual_pickup_evt is not None:
            _log.info(
                f"CK_MANUAL_EDIT_PASSTHROUGH oid={oid} czasówka ck "
                f"{existing.get('czas_kuriera_hhmm')}→{new_ck_hhmm} src={_src} "
                f"→ PICKUP_TIME_UPDATED"
            )
            return update_from_event(_manual_pickup_evt)
        if (flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)
                and _is_czasowka_order(existing)
                and _src in _CK_PASSIVE_SOURCES):
            _log.info(
                f"CK_PASSIVE_SUPPRESSED oid={oid} czasówka ck "
                f"{existing.get('czas_kuriera_hhmm')}→{new_ck_hhmm} src={_src} "
                f"— committed=pickup_at, gastro re-stamp ignorowany (skip persist)"
            )
            return None
        # Elastyk (non-czasówka) forward-only (Adrian 2026-06-24, opcja B):
        # pasywny re-odczyt gastro NIE może COFNĄĆ committed czas_kuriera
        # („przyjazd wcześniej niż umówiono" = wobble ETA). Forward przechodzi
        # (koordynatorski +15 / realne spóźnienie). Deliberatne sources nie są
        # w _CK_PASSIVE_SOURCES → przechodzą w każdym kierunku.
        if (flag("ENABLE_ELASTYK_CK_NO_BACKWARD", True)
                and not _is_czasowka_order(existing)
                and _src in _CK_PASSIVE_SOURCES):
            _bwd = _ck_backward_delta(existing.get("czas_kuriera_warsaw"), new_ck_iso)
            if _bwd is not None:
                _log.info(
                    f"CK_ELASTYK_BACKWARD_BLOCKED oid={oid} ck "
                    f"{existing.get('czas_kuriera_hhmm')}→{new_ck_hhmm} Δ={_bwd:+.1f}min "
                    f"src={_src} — elastyk forward-only, nie cofamy (skip persist)"
                )
                return None
        prev_count = int(existing.get("v319g_ck_change_count") or 0)
        update_fields = {
            "czas_kuriera_warsaw": new_ck_iso,
            "czas_kuriera_hhmm": new_ck_hhmm,
            "v319g_ck_change_count": prev_count + 1,
        }
        _delta = payload.get("delta_min")
        _delta_str = f"Δ={_delta:+.1f}min" if _delta is not None else "Δ=null(first_ack)"
        _log.info(
            f"V3.19g1 oid={oid} ck {payload.get('old_ck_hhmm')} → {new_ck_hhmm} "
            f"{_delta_str} src={payload.get('source')}"
        )
        return upsert_order(
            oid, _marked(update_fields), event="CZAS_KURIERA_UPDATED"
        )

    if etype == "PICKUP_TIME_UPDATED":
        # Root cause oid 474577 (2026-05-19): pickup_at_warsaw zapisywany RAZ
        # w NEW_ORDER, nigdy nie odświeżany dla czasówek status=planned →
        # koordynator zmienił czas odbioru na życzenie restauracji, Ziomek
        # czytał stary (czasowka_scheduler._minutes_to_pickup → FORCE_ASSIGN
        # spam). panel_watcher._diff_pickup_time wykrył zmianę pickup_at_warsaw
        # (|Δt| ≥ próg). Odśwież pola czasu odbioru, preserve status/courier/
        # czas_kuriera/commitment (orthogonal — czas_kuriera ma własny handler).
        new_pickup = payload.get("new_pickup_at_warsaw")
        if not new_pickup:
            _log.error(
                f"PICKUP_TIME_UPDATED {oid}: brak new_pickup_at_warsaw, skip"
            )
            return None
        # Sanity: musi parsować się jako ISO datetime (Lekcja #81 fail-loud).
        try:
            datetime.fromisoformat(new_pickup)
        except (ValueError, TypeError) as e:
            _log.error(
                f"PICKUP_TIME_UPDATED {oid}: pickup_at_warsaw parse fail "
                f"({new_pickup!r}): {e}, skip"
            )
            return None
        existing = get_order(oid)
        if existing is None:
            _log.warning(f"PICKUP_TIME_UPDATED for unknown oid={oid}, skipping")
            return None
        prev_count = int(existing.get("pickup_time_change_count") or 0)
        update_fields = {
            "pickup_at_warsaw": new_pickup,
            "pickup_time_change_count": prev_count + 1,
        }
        # Mirror committed pickup → czas_kuriera (Adrian 2026-06-24): dla czasówki
        # umówiony czas rządzi pickup_at, ale apka/kurier pokazują czas_kuriera —
        # więc musi nadążać za LEGALNĄ zmianą odbioru (koordynator/restauracja,
        # dowolny kierunek; w przyszłości ziomek_late_extension). To jest kanał,
        # którym committed czasówki ma się zmieniać (zamiast pasywnego czas_kuriera).
        if (flag("ENABLE_PICKUP_TIME_MIRRORS_CK", True)
                and _is_czasowka_order(existing)):
            try:
                _np_dt = datetime.fromisoformat(new_pickup)
                update_fields["czas_kuriera_warsaw"] = new_pickup
                update_fields["czas_kuriera_hhmm"] = _np_dt.strftime("%H:%M")
            except (ValueError, TypeError):
                pass  # new_pickup już zwalidowany wyżej; defensywnie
        # prep_minutes / decision_deadline / zmiana_czasu_odbioru — odśwież
        # gdy panel dostarczył świeże; NIE nadpisuj realnej wartości None'em.
        new_prep = payload.get("new_prep_minutes")
        if new_prep is not None:
            update_fields["prep_minutes"] = new_prep
        new_dd = payload.get("new_decision_deadline")
        if new_dd is not None:
            update_fields["decision_deadline"] = new_dd
        new_zco = payload.get("new_zmiana_czasu_odbioru")
        if new_zco is not None:
            update_fields["zmiana_czasu_odbioru"] = new_zco
        _p_delta = payload.get("delta_min")
        _p_delta_str = (
            f"Δ={_p_delta:+.1f}min" if _p_delta is not None else "Δ=null(late)"
        )
        _log.info(
            f"PICKUP_TIME_UPDATED oid={oid} pickup "
            f"{payload.get('old_pickup_at_warsaw')} → {new_pickup} "
            f"{_p_delta_str} src={payload.get('source')}"
        )
        return upsert_order(
            oid, _marked(update_fields), event="PICKUP_TIME_UPDATED"
        )

    if etype == "COURIER_PICKED_UP":
        # F2.1b step 5: CELOWO NIE resetujemy bag_time_alerted tutaj.
        # Panel_watcher może reemit COURIER_PICKED_UP przez reconcile retry po
        # tym jak sla_tracker już ustawił flag=True. Reset w tym handlerze
        # spowodowałby duplicate alerty (flag→False, następny tick→kolejny alert).
        # Reset jest w ASSIGNED/DELIVERED/REJECTED/RETURNED — bezpieczne punkty.
        picked = payload.get("timestamp") or now_iso()
        # expected_delivery_by = picked + 35 min (SLA)
        try:
            # panel timestamps sa naive Warsaw, dorzuc UTC jako fallback
            if "T" in picked or "Z" in picked:
                picked_dt = datetime.fromisoformat(picked.replace("Z", "+00:00"))
            else:
                # "2026-04-11 18:01:47" = naive Warsaw
                from zoneinfo import ZoneInfo
                picked_dt = datetime.strptime(picked, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Warsaw"))
        except Exception:
            picked_dt = datetime.now(timezone.utc)
        expected = (picked_dt + timedelta(minutes=35)).isoformat()
        pickup_coords = payload.get("pickup_coords")
        update_fields = {
            "status": "picked_up",
            "commitment_level": "picked_up",
            "picked_up_at": picked,
            "expected_delivery_by": expected,
            "assigned_check_ts": now_iso(),
        }
        if pickup_coords:
            update_fields["pickup_coords"] = pickup_coords
        return upsert_order(oid, _marked(update_fields), event="COURIER_PICKED_UP")

    if etype == "COURIER_DELIVERED":
        deliv_addr = payload.get("delivery_address") or payload.get("final_location")
        deliv_city = payload.get("delivery_city")
        deliv_coords = None
        if deliv_addr:
            try:
                from dispatch_v2.geocoding import geocode
                r = geocode(deliv_addr, city=deliv_city)
                if r:
                    deliv_coords = [round(float(r[0]), 6), round(float(r[1]), 6)]
            except Exception as _e:
                pass  # geocode fail nie blokuje zapisu delivered
        # FIX 2026-06-13 (sink guard, B3/B5): dwa defekty u ujścia.
        # (1) `payload.get("timestamp", now_iso())` zwraca None gdy klucz ISTNIEJE
        #     z wartością None — a reconcile/panel_diff/packs_ghost podają
        #     {"timestamp": raw.get("czas_doreczenia")} BEZ fallbacku → delivered_at
        #     = null → build_delivered wyklucza → znika z "Doręczone" + utarg 0.
        #     `or now_iso()` łapie też None-value (default działa tylko dla braku klucza).
        # (2) Gdy geocode zawiódł (brak delivery_city → no_city) deliv_coords=None
        #     NADPISYWAŁO poprawne coords z NEW_ORDER → piny mapy znikały całej flocie.
        #     upsert_order MERGE'uje ({**existing, **data}), więc pominięcie klucza
        #     delivery_coords zachowuje istniejące — nie nadpisujemy dobrych None'em.
        delivered_update = {
            "status": "delivered",
            "commitment_level": "planned",  # reset, kurier wolny
            "delivered_at": payload.get("timestamp") or now_iso(),
            "final_location": payload.get("final_location"),
            "delivery_address": deliv_addr,
            "bag_time_alerted": False,  # F2.1b step 5: housekeeping reset at end-of-life
        }
        if deliv_coords:
            delivered_update["delivery_coords"] = deliv_coords
        return upsert_order(
            oid, _marked(delivered_update), event="COURIER_DELIVERED"
        )

    if etype == "ORDER_RESURRECTED":
        existing = get_order(oid) or {}
        if existing.get("status") != "delivered":
            return None
        new_status = str(payload.get("new_status") or "picked_up")
        if new_status not in ("assigned", "picked_up"):
            new_status = "picked_up"
        correction = {
            "status": new_status,
            "commitment_level": new_status,
            "delivered_at": None,
            "final_location": None,
            "bag_time_alerted": False,
            # New correction epoch revokes a crash-pending DELIVERED marker.
            "last_lifecycle_event_id_courier_delivered": None,
        }
        if event.get("courier_id"):
            correction["courier_id"] = str(event.get("courier_id"))
        _log.warning(
            f"RESURRECT {oid} delivered→{new_status} "
            f"reason={payload.get('reason')} cid={event.get('courier_id')}"
        )
        return upsert_order(
            oid, _marked(correction), event="ORDER_RESURRECTED"
        )

    if etype == "ORDER_RETURNED_TO_POOL":
        return upsert_order(oid, _marked({
            "status": "returned_to_pool",
            "commitment_level": "planned",
            "courier_id": None,
            "return_reason": payload.get("reason"),
            "bag_time_alerted": False,  # F2.1b step 5: reset — next courier starts clean
        }), event="ORDER_RETURNED_TO_POOL")

    if etype == "COURIER_REJECTED_PROPOSAL":
        # Wraca do planned, bez kuriera
        return upsert_order(oid, _marked({
            "status": "planned",
            "commitment_level": "planned",
            "courier_id": None,
            "last_rejected_by": event.get("courier_id"),
            "rejection_reason": payload.get("reason"),
            "bag_time_alerted": False,  # F2.1b step 5: reset on rejection — next courier starts clean
        }), event="COURIER_REJECTED_PROPOSAL")

    # Pozostale eventy nie zmieniaja stanu zlecen
    return None


@_lifecycle_state_mutation
def touch_check_cursor(order_id: str) -> bool:
    """Cicha aktualizacja cursora round-robin dla round-robin watchera.
    Ustawia assigned_check_ts=now_iso dla ordera. Nie loguje historii.
    Uzywane przez panel_watcher picked_up reconcile do rotacji candidate'ow.
    Zwraca True jesli order istnial, False inaczej."""
    with _locked_write():
        state = _read_state_strict()        # Faza 1: raise StateReadError zamiast cichego {}
        old_count = len(state)
        if order_id not in state:
            return False
        state[order_id]["assigned_check_ts"] = now_iso()
        _guarded_write(Path(_state_path()), state, old_count, op="touch")
        return True


@_lifecycle_state_mutation
def delete_order(order_id: str) -> bool:
    """Fizyczne usuniecie (tylko do testow lub purge).

    TASK 2 Część A (2026-05-04) Z3 safety guard: order MUSI mieć status terminal
    (delivered/cancelled/returned_to_pool) PRZED delete. Inaczej events.db nie ma
    closure event → phantom. Caller emituje terminal event najpierw, potem delete.
    """
    TERMINAL_STATUSES = ("delivered", "cancelled", "returned_to_pool")
    with _locked_write() as path:
        state = _read_state_strict()        # Faza 1: raise StateReadError zamiast cichego {}
        old_count = len(state)
        if order_id in state:
            current_status = state[order_id].get("status")
            if current_status not in TERMINAL_STATUSES:
                raise RuntimeError(
                    f"delete_order({order_id}) refused: status={current_status!r} not terminal "
                    f"(must be in {TERMINAL_STATUSES}). Emit terminal event first to avoid events.db phantom."
                )
            del state[order_id]
            _guarded_write(path, state, old_count, op="delete")
            _log.info(f"delete {order_id} (status={current_status})")
            return True
        return False


# ──────────────────────────────────────────────────────────────────────────
# STATE-RMW-02 (audyt 2026-06-03): bulk-prune terminalnych zleceń.
#
# Problem: orders_state.json rośnie monotonicznie (~+0.5 MB/dzień; 3693 zleceń =
# 8.4 MB, z czego 99.3% terminalnych), a KAŻDY RMW writer czyta+zapisuje+fsync
# CAŁY plik pod LOCK_EX → koszt każdego upsertu = O(cały stan) + rosnąca
# rywalizacja o lock z czytelnikami (watcher/sla_tracker/reconcile).
#
# Fix: nocny prune usuwa zlecenia TERMINALNE (delivered/cancelled/
# returned_to_pool) starsze niż retention_hours wg `updated_at` (tz-aware ISO,
# 100% pokrycia — NIE `delivered_at`, który jest naiwnym czasem Warsaw z lukami).
#
# Bezpieczeństwo: bulk-write OMIJA `_guarded_write` (dopuszcza max 1 delete na
# wywołanie → naiwna pętla = ~3500 pełnych zapisów 8 MB pod LOCK_EX = godziny
# I/O). Zamiast tego: jeden `_read_state_strict` + jeden `_atomic_write` pod tym
# samym współdzielonym `_locked_write` (serializacja z upsert/set_status/touch/
# delete) + TWARDY sanity-guard PRZED zapisem (zastępuje _guarded_write):
#   1. żaden kandydat nie może być nie-terminalny,
#   2. żaden aktywny order nie znika (regresja liczby aktywnych = abort),
#   3. spójność liczby + zakaz całkowitego wyzerowania,
#   → inaczej raise StateReadError + throttled admin alert.
# Odzysk pełnego payloadu pruned-zlecenia: snapshot (.prev / /snapshots, ~7 dni)
# lub events.db. Audit_log (90 dni) zachowuje closure eventy (forensyka).
TERMINAL_STATUSES_PRUNE = ("delivered", "cancelled", "returned_to_pool")


def _parse_updated_at_utc(value) -> Optional[datetime]:
    """Parsuje `updated_at` (ISO) → tz-aware UTC datetime, albo None gdy się nie da.
    Naiwny (bez tz) traktowany jako UTC (now_iso() zawsze pisze tz-aware)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@_lifecycle_state_mutation
def prune_terminal_orders(retention_hours: float = 12.0, dry_run: bool = True) -> dict:
    """Usuwa terminalne zlecenia starsze niż retention_hours (anchor: `updated_at`).

    Zwraca raport (zawsze, też w dry-run):
      {old_count, active_count, pruned_count, new_count, retention_hours,
       dry_run, skipped_no_updated_at, sample}

    dry_run=True → tylko liczy i loguje, NIC nie zapisuje (zero ryzyka).
    Bezpieczeństwo: patrz docstring sekcji. Aktywne (planned/assigned/picked_up)
    NIGDY nie są usuwane — pełna ochrona przez status-filter + sanity-guard.
    """
    cutoff = now_utc() - timedelta(hours=retention_hours)
    with _locked_write() as path:
        state = _read_state_strict()        # Faza 1: raise zamiast cichego {}
        old_count = len(state)
        active_count = sum(
            1 for r in state.values()
            if r.get("status") not in TERMINAL_STATUSES_PRUNE
        )
        skipped_no_updated_at = 0
        to_prune = []
        for oid, rec in state.items():
            if rec.get("status") not in TERMINAL_STATUSES_PRUNE:
                continue
            dt = _parse_updated_at_utc(rec.get("updated_at"))
            if dt is None:
                skipped_no_updated_at += 1   # brak wiarygodnego anchora → NIE ruszaj
                continue
            if dt < cutoff:
                to_prune.append(oid)

        report = {
            "old_count": old_count,
            "active_count": active_count,
            "pruned_count": len(to_prune),
            "new_count": old_count - len(to_prune),
            "retention_hours": retention_hours,
            "dry_run": dry_run,
            "skipped_no_updated_at": skipped_no_updated_at,
            "sample": to_prune[:10],
        }

        if not to_prune:
            _log.info(
                f"prune_terminal_orders: nic do usunięcia "
                f"(old={old_count}, active={active_count}, retention={retention_hours}h)"
            )
            return report

        prune_set = set(to_prune)
        new_state = {k: v for k, v in state.items() if k not in prune_set}
        new_count = len(new_state)
        active_after = sum(
            1 for r in new_state.values()
            if r.get("status") not in TERMINAL_STATUSES_PRUNE
        )

        # ── TWARDY sanity-guard (zastępuje _guarded_write dla bulk-delete) ──
        non_terminal_in_prune = [
            oid for oid in to_prune
            if state[oid].get("status") not in TERMINAL_STATUSES_PRUNE
        ]
        if (
            non_terminal_in_prune                       # 1. tknięto nie-terminalny
            or active_after != active_count             # 2. zniknął aktywny order
            or new_count != old_count - len(to_prune)   # 3. niespójność liczby
            or (old_count > 0 and new_count == 0)        # 4. całkowite wyzerowanie
        ):
            detail = (
                f"prune_terminal_orders SANITY ABORT: old={old_count} new={new_count} "
                f"prune={len(to_prune)} active={active_count}→{active_after} "
                f"non_terminal_in_prune={len(non_terminal_in_prune)} — zapis ZABLOKOWANY "
                f"(ochrona przed utratą aktywnych/clobberem)"
            )
            _alert_state_read_failure(detail)
            raise StateReadError(detail)

        if dry_run:
            _log.info(
                f"prune_terminal_orders DRY-RUN: usunąłbym {len(to_prune)} terminalnych "
                f">{retention_hours}h ({old_count}→{new_count}); active={active_count} "
                f"nietknięte; skipped_no_ts={skipped_no_updated_at}"
            )
            return report

        _atomic_write(path, new_state)
        _log.info(
            f"prune_terminal_orders: usunięto {len(to_prune)} terminalnych "
            f"({old_count}→{new_count}); active={active_count} nietknięte; "
            f"skipped_no_ts={skipped_no_updated_at}"
        )
        return report


def compute_oldest_picked_up_age_min(bag, now_utc):
    """Wiek (minuty) najstarszego ordera w statusie 'picked_up' w bagu kuriera.

    Implementacja D4 V3.1: SLA kuriera liczy sie od picked_up_at (nie od assigned_at).
    Ordery w statusie 'assigned' nie karcony time_penalty w scoringu - kurier ich
    jeszcze nie ma fizycznie, restauracja jeszcze prepuje.

    Parsowanie timestampow: akceptowane formaty:
      1. datetime z tzinfo
      2. ISO string "YYYY-MM-DDTHH:MM:SS+HH:MM" lub z "Z"
      3. naive Warsaw "YYYY-MM-DD HH:MM:SS" (format panelu gastro.nadajesz.pl)

    Args:
        bag: lista dict orderow (np. z get_by_courier). Kazdy order ma min. "status".
             Dla statusu "picked_up" wymagany jest "picked_up_at".
        now_utc: datetime z tzinfo UTC. Caller MUSI podac - zero ukrytych defaults
                 dla deterministycznosci (replay historical data, A/B testy).

    Returns:
        float minut lub None gdy bag nie ma zadnego ordera w statusie "picked_up"
        z poprawnym picked_up_at timestampem.

    Raises:
        ValueError: gdy now_utc jest naive (bez tzinfo).

    Example:
        >>> from datetime import datetime, timezone, timedelta
        >>> now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
        >>> bag = [
        ...     {"status": "picked_up", "picked_up_at": "2026-04-12T11:45:00+00:00"},
        ...     {"status": "assigned"},
        ... ]
        >>> compute_oldest_picked_up_age_min(bag, now)
        15.0
    """
    if now_utc is None:
        raise ValueError("now_utc required - caller must pass explicit timestamp")
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware (got naive datetime)")

    if not bag:
        return None

    now_utc_norm = now_utc.astimezone(timezone.utc)
    oldest_age_min = None

    for order in bag:
        if not isinstance(order, dict):
            continue
        if order.get("status") != "picked_up":
            continue
        picked_ts = order.get("picked_up_at")
        if not picked_ts:
            continue

        picked_dt = _parse_picked_up_at(picked_ts)
        if picked_dt is None:
            continue

        age_min = (now_utc_norm - picked_dt).total_seconds() / 60.0
        if oldest_age_min is None or age_min > oldest_age_min:
            oldest_age_min = age_min

    return oldest_age_min


def _parse_picked_up_at(value):
    """Wrapper na common.parse_panel_timestamp dla kompatybilnosci wewnetrznej."""
    from dispatch_v2.common import parse_panel_timestamp
    return parse_panel_timestamp(value)


def stats() -> dict:
    """Statystyki state machine."""
    state = _read_state()
    by_status = {}
    by_courier = {}
    for o in state.values():
        s = o.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        c = o.get("courier_id")
        if c and s in ("assigned", "picked_up"):
            by_courier[c] = by_courier.get(c, 0) + 1
    return {
        "total": len(state),
        "by_status": by_status,
        "active_per_courier": by_courier,
    }
