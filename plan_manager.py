"""plan_manager — V3.19b saved plans persistence.

Persists per-courier TSP route plans (sequence + ETAs + start_pos) across dispatch
decisions. Replaces "fresh re-TSP per propose" with incremental load/save/insert,
giving coherence between Telegram display and scoring, plus basis for V3.19c
periodic re-check.

Storage: /root/.openclaw/workspace/dispatch_state/courier_plans.json.
Concurrency: fcntl LOCK_EX (write) / LOCK_SH (read) on a companion lockfile.
Atomicity: temp file → fsync → os.replace (POSIX atomic rename).
Schema: see /tmp/v319_schema.json (JSON Schema Draft 2020-12). Top-level is a
flat dict keyed by courier_id string; each value is a CourierPlan.

Pure library — no imports from dispatch_pipeline / panel_watcher (one-way).
"""
from __future__ import annotations

import copy
import fcntl
import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("plan_manager")

PLANS_FILE = Path("/root/.openclaw/workspace/dispatch_state/courier_plans.json")
LOCK_FILE = Path("/root/.openclaw/workspace/dispatch_state/courier_plans.lock")
SCHEMA_VERSION = 1

# Procesowy, monotoniczny licznik odrzuconych zapisow CAS. Dwa procesy
# (panel-watcher i plan-recheck) publikuja delte we wlasnych podsumowaniach;
# licznik w jednym miejscu obejmuje wszystkie production call-site'y save_plan.
_cas_stats_lock = threading.Lock()
_cas_conflicts_total = 0

INVALIDATION_REASONS = frozenset({
    "ORDER_DELIVERED_ALL",
    "ORDER_CANCELLED",
    "GPS_DRIFT",
    "SHIFT_END",
    "MANUAL",
    "SCHEMA_UPGRADE",
    "BAG_CHANGED",  # BUG-1 (2026-06-05): reassign/PANEL_OVERRIDE dorzucił order poza planem
})


def _ensure_parent() -> None:
    PLANS_FILE.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _locked(exclusive: bool):
    """File lock guard. Uses a dedicated lockfile to survive PLANS_FILE rename."""
    _ensure_parent()
    LOCK_FILE.touch(exist_ok=True)
    mode = "r+b"
    with open(LOCK_FILE, mode) as lockfh:
        fcntl.flock(lockfh.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lockfh.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, data: Any) -> None:
    """Write JSON atomically: temp file in same dir → fsync → os.replace."""
    _ensure_parent()
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception as _e:
        # A1: log path PRZED re-raise żeby caller widział co fail'owało.
        # Re-raise pattern OK (cleanup tmpfile + propagate do callera).
        _log.error(f"atomic write fail path={path} ({type(_e).__name__}: {_e})")
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _read_raw() -> Dict[str, Any]:
    """Load entire plans dict. Must be called under shared or exclusive lock."""
    if not PLANS_FILE.exists():
        return {}
    try:
        with open(PLANS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            _log.warning("courier_plans.json is not an object; treating as empty")
            return {}
        return data
    except json.JSONDecodeError as e:
        _log.error(f"courier_plans.json corrupt: {e}")
        return {}


def _write_raw(data: Dict[str, Any]) -> None:
    """Write entire plans dict. Must be called under exclusive lock."""
    _atomic_write(PLANS_FILE, data)
    # perf-lazy write-through (03.07): unieważnij read-cache W TYM procesie
    # natychmiast po zapisie. Klucz mtime NIE wystarcza sam: dwa zapisy w tym
    # samym ticku zegara jądra dają IDENTYCZNY st_mtime_ns (a size bywa równy)
    # → czytelnik dostawał stan sprzed zapisu (flake test_v319c_sub_a: 4/30
    # FAIL przy PERF_LAZY=ON / 0/30 OFF). Cross-proces domyka st_ino w kluczu
    # (_read_raw_shared) — os.replace = nowy inode przy każdym zapisie.
    with _perf_plans_lock:
        _perf_plans_cache["key"] = None
        _perf_plans_cache["data"] = None


# ─── FALA perf-lazy (2026-07-02, finding E audytu 2.0): read-cache planów ───
# Problem: load_plan czytany PER KANDYDAT (ThreadPoolExecutor) → N× pełny
# `_read_raw` (open+json.load) POD fcntl-lockiem → wątki serializują się na
# lockfile (kontencja rośnie w peak). Cache po (mtime_ns, size, ino) NAD lockiem:
# ciepły hit pomija fcntl-lock+open+json.load. Używany WYŁĄCZNIE przez ścieżki
# READ (load_plan/load_plans), które zwracają deepcopy → caller nigdy nie mutuje
# współdzielonego obiektu cache. WRITERS (save/invalidate/advance/insert) dalej
# wołają surowy `_read_raw()` pod exclusive lock i mutują świeży parse.
# ⚠ KOREKTA 03.07: pierwotne założenie „os.replace bumpuje mtime → cache sam
# się unieważnia" było FAŁSZYWE — zapisy w tym samym ticku zegara jądra dają
# identyczny mtime_ns (flake test_v319c_sub_a 4/30 przy ON). Dlatego: (1)
# `_write_raw` czyści cache write-through (in-process zawsze świeże), (2) klucz
# zawiera st_ino (os.replace = nowy inode → cross-proces sam się unieważnia).
# Bajt-parytet: deepcopy = niezależny obiekt identyczny z fresh-parse.
# OFF (default) = ścieżka sprzed fali (fcntl-lock + _read_raw co wywołanie).
_perf_plans_cache = {"key": None, "data": None}
_perf_plans_lock = threading.Lock()


def _perf_lazy_on() -> bool:
    """EFEKTYWNY stan flagi perf-lazy (flags.json → stała common → False)."""
    try:
        from dispatch_v2 import common as _C
        return bool(_C.flag("ENABLE_PERF_LAZY_MEMBERS",
                            getattr(_C, "ENABLE_PERF_LAZY_MEMBERS", False)))
    except Exception:
        return False


def _read_raw_shared() -> Dict[str, Any]:
    """mtime-keyed cache parsowanego pliku planów (perf-lazy). Zwraca WSPÓLNY
    obiekt — TYLKO dla ścieżek READ, które deepcopy'ują przed zwrotem. Ciepły hit
    pomija fcntl-lock + open + json.load."""
    try:
        st = PLANS_FILE.stat()
        # st_ino w kluczu (03.07): _atomic_write robi os.replace = NOWY inode
        # przy każdym zapisie → klucz zmienia się nawet gdy dwa zapisy trafią
        # w ten sam tick zegara (identyczny mtime_ns) i mają równy size.
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
    except FileNotFoundError:
        return {}
    with _perf_plans_lock:
        if _perf_plans_cache["key"] == key and _perf_plans_cache["data"] is not None:
            return _perf_plans_cache["data"]
    with _locked(exclusive=False):
        data = _read_raw()
    with _perf_plans_lock:
        _perf_plans_cache["key"] = key
        _perf_plans_cache["data"] = data
    return data


# ---- public API ----

def load_plans() -> Dict[str, Any]:
    """Load all plans (read-only copy). Shared lock (perf-lazy: mtime-cache)."""
    if _perf_lazy_on():
        return copy.deepcopy(_read_raw_shared())
    with _locked(exclusive=False):
        return _read_raw()


def load_plan(
    courier_id: str,
    active_bag_oids: Optional[set] = None,
    invalidate_on_mismatch: bool = True,
) -> Optional[Dict[str, Any]]:
    """Load a single plan. If active_bag_oids provided and any plan stop's
    order_id is outside that set → mismatch with reality (return None).

    invalidate_on_mismatch (default True = legacy): on mismatch ALSO persist
    invalidate_plan(ORDER_DELIVERED_ALL).

    FIX 2026-06-29 (root cause oscylacji carried-first): czytelnicy-PODGLĄDY
    (dispatch_pipeline `_soon_free_probe` / base_sequence read) wołają to per-tick
    z workiem KANDYDATA. Przy wyścigu z `advance_plan` (po dostawie, ZANIM
    chirurgicznie wykreśli dostarczony stop) read widzi „stop planu spoza worka"
    i DRZE CAŁY plan — invalidated z mylnym ORDER_DELIVERED_ALL MIMO żywych
    stopów → konsola mruga co tick na carried-first (case Jakub W / Piotr K).
    Z `invalidate_on_mismatch=False` read jest CZYSTY (zwraca None, NIE
    persystuje) — autorytatywne unieważnienia (advance_plan na dostawie /
    panel_watcher BAG_CHANGED na reassign / plan_recheck terminal/missing/stale)
    pozostają JEDYNYM źródłem invalidacji. Flaga w callerze: ENABLE_LOAD_PLAN_PURE_READ.

    Returns None if no plan or plan invalidated.
    """
    cid = str(courier_id)
    _lazy = _perf_lazy_on()
    if _lazy:
        plans = _read_raw_shared()  # WSPÓLNY — tylko czytamy, zwracamy deepcopy
    else:
        with _locked(exclusive=False):
            plans = _read_raw()
    plan = plans.get(cid)
    if plan is None:
        return None
    if plan.get("invalidated_at") is not None:
        return None
    if active_bag_oids is not None:
        plan_oids = {s["order_id"] for s in plan.get("stops", [])
                     if s.get("type") == "dropoff"}
        if plan_oids and not plan_oids.issubset(active_bag_oids):
            if invalidate_on_mismatch:
                try:
                    invalidate_plan(
                        cid,
                        "ORDER_DELIVERED_ALL",
                        expected_version=plan.get("plan_version", 0),
                    )
                except ConcurrencyError:
                    # Snapshot z load jest juz stary. Nowszy current zostaje;
                    # caller nadal dostaje None dla niedopasowanego snapshotu.
                    pass
            return None
    # deepcopy tylko na ścieżce współdzielonego cache — chroni cache przed
    # ewentualną mutacją u callera (writers używają surowego _read_raw).
    return copy.deepcopy(plan) if _lazy else plan


def cas_conflicts_total() -> int:
    """Procesowy licznik odrzuconych mutacji z expected_version CAS."""
    with _cas_stats_lock:
        return _cas_conflicts_total


def _check_expected_version(
    courier_id: str,
    expected_version: Optional[int],
    current_version: int,
) -> None:
    """Wspolny guard CAS dla save/invalidate; konflikt nie mutuje planu."""
    if expected_version is None or current_version == expected_version:
        return
    global _cas_conflicts_total
    with _cas_stats_lock:
        _cas_conflicts_total += 1
    _log.warning(
        "PLAN_CAS_CONFLICT cid=%s expected_version=%s current_version=%s",
        courier_id, expected_version, current_version,
    )
    raise ConcurrencyError(courier_id, expected_version, current_version)


def save_plan(
    courier_id: str,
    plan_body: Dict[str, Any],
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Persist a plan. plan_body must contain: start_pos, start_ts, stops,
    optimization_method. plan_version and timestamps are managed here.

    expected_version: optimistic CAS. If current version != expected, raises
    ConcurrencyError. None = accept any prior version (create-or-overwrite).

    Returns the saved plan (with final plan_version).
    """
    cid = str(courier_id)
    _validate_plan_body(plan_body)
    with _locked(exclusive=True):
        plans = _read_raw()
        current = plans.get(cid)
        prev_version = (current or {}).get("plan_version", 0)
        _check_expected_version(cid, expected_version, prev_version)
        new_version = prev_version + 1
        now_iso = _now_iso()
        created_at = (current or {}).get("created_at", now_iso)
        saved = {
            "plan_version": new_version,
            "created_at": created_at,
            "last_modified_at": now_iso,
            "start_pos": plan_body["start_pos"],
            "start_ts": plan_body["start_ts"],
            "stops": plan_body["stops"],
            "optimization_method": plan_body["optimization_method"],
            # F2 zunifikowany silnik trasy: sygnatura worka (kiedy zdecydowano
            # sekwencję) + znacznik ostatniego re-czasowania. Opcjonalne — gdy
            # caller nie poda bag_signature, przenosimy z poprzedniej wersji, by
            # zapis nie-F2 (np. _save_plan_on_assign) jej nie kasował.
            "bag_signature": plan_body.get(
                "bag_signature", (current or {}).get("bag_signature")),
            "retimed_at": plan_body.get("retimed_at"),
            "invalidated_at": None,
            "invalidation_reason": None,
        }
        plans[cid] = saved
        _write_raw(plans)
        return saved


def invalidate_plan(
    courier_id: str,
    reason: str,
    expected_version: Optional[int] = None,
) -> None:
    """Mark plan invalidated. Plan stays in file for debug + GC-able.

    expected_version: optimistic CAS for read-check-invalidate cycles. Mismatch
    raises ConcurrencyError and leaves the newer current plan untouched. None
    preserves the atomic event-mutator/manual-call contract.
    """
    cid = str(courier_id)
    if reason not in INVALIDATION_REASONS:
        _log.warning(f"invalidate_plan: unknown reason {reason!r}, allowing")
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        current_version = (plan or {}).get("plan_version", 0)
        _check_expected_version(cid, expected_version, current_version)
        if plan is None:
            return
        plan["invalidated_at"] = _now_iso()
        plan["invalidation_reason"] = reason
        plan["plan_version"] = int(plan.get("plan_version", 0)) + 1
        plan["last_modified_at"] = plan["invalidated_at"]
        _write_raw(plans)


def touch_plan(courier_id: str, reason: str = "SIGNAL") -> bool:
    """FIX-E (2026-06-13, B1): LEKKI sygnał zmiany dla apki — bump plan_version +
    last_modified_at BEZ invalidacji. Plan ZOSTAJE w obecnym stanie (ważny zostaje
    ważny, invalidated zostaje invalidated) → plan_recheck NIE jest zmuszany do
    regeneracji ani apka nie migocze widokiem fallback (co dałby invalidate_plan).

    Po co: czas_kuriera/pickup zmienia się dla zlecenia POKRYTEGO planem; sam plan
    nie musi się zmienić (build_view i tak klampuje wyświetlane eta do committed z
    orders_state), ale apka odświeża /api/courier/orders TYLKO gdy plan_version
    LUB invalidated_at się ruszy. touch_plan rusza plan_version (per-cid) bez kosztu
    regeneracji. Działa też na planie JUŻ invalidated (B1: PANEL_OVERRIDE unieważnił
    plan, potem wchodzi czas_kuriera) — bump plan_version i tak zmienia /plan-version.

    No-op (False) gdy brak planu w pliku (apka i tak na pełnym worku z fresh czas_kuriera).
    Zwraca True gdy bumpnięto. Bump monotoniczny — spójny z save_plan/advance_plan."""
    cid = str(courier_id)
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        if plan is None:
            return False
        plan["plan_version"] = int(plan.get("plan_version", 0)) + 1
        plan["last_modified_at"] = _now_iso()
        new_ver = plan["plan_version"]
        _write_raw(plans)
    _log.info(f"touch_plan cid={cid} reason={reason} → plan_version={new_ver}")
    return True


def advance_plan(
    courier_id: str,
    delivered_order_id: str,
    delivered_at: str,
    delivery_coords: Optional[Tuple[float, float]] = None,
) -> None:
    """Remove the dropoff stop for delivered_order_id; update start_pos to the
    delivery location + start_ts to delivered_at. If no stops remain, invalidate
    with reason ORDER_DELIVERED_ALL.
    """
    cid = str(courier_id)
    doid = str(delivered_order_id)
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        if plan is None or plan.get("invalidated_at") is not None:
            return
        # Remove both pickup and dropoff for delivered order — once delivered,
        # pickup is definitionally in the past and shouldn't linger in the plan.
        if not any(s.get("order_id") == doid for s in plan.get("stops", [])):
            # At-least-once durable callback retry after a later save_plan must
            # not overwrite that newer plan's anchor with an old delivery.
            return
        new_stops = [
            s for s in plan.get("stops", []) if s.get("order_id") != doid
        ]
        if not new_stops:
            plan["invalidated_at"] = _now_iso()
            plan["invalidation_reason"] = "ORDER_DELIVERED_ALL"
            plan["plan_version"] = int(plan.get("plan_version", 0)) + 1
            plan["last_modified_at"] = plan["invalidated_at"]
        else:
            plan["stops"] = new_stops
            if delivery_coords is not None:
                plan["start_pos"] = {
                    "lat": float(delivery_coords[0]),
                    "lng": float(delivery_coords[1]),
                    "source": "last_delivered",
                    "source_ts": delivered_at,
                }
            plan["start_ts"] = delivered_at
            plan["plan_version"] = plan.get("plan_version", 0) + 1
            plan["last_modified_at"] = _now_iso()
        _write_raw(plans)


def remove_stops(courier_id: str, order_id: str) -> None:
    """Remove ALL stops (pickup AND dropoff) for order_id. For ORDER_CANCELLED
    / ORDER_RETURNED_TO_POOL path (+ REASSIGN-RELEASE, + GC terminal-prune).
    If plan empty after removal, invalidate.

    v3 (Sol flip-gate 2026-07-20): gdy order_id NIE występuje w żadnym stopie →
    czysty no-op BEZ zapisu i BEZ bumpu wersji. Wcześniej bump-always: zapis
    + plan_version+1 nawet bez zmiany treści = pusty SSE-refresh apki bez
    powodu. Decyzja zapada WEWNĄTRZ tego samego exclusive locka co zapis
    (race-safe — pre-check poza lockiem był TOCTOU: nowszy plan bez stopa
    mógł wejść między odczyt a wywołanie).
    """
    cid = str(courier_id)
    oid = str(order_id)
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        if plan is None or plan.get("invalidated_at") is not None:
            return
        stops = plan.get("stops", [])
        new_stops = [s for s in stops if s.get("order_id") != oid]
        if len(new_stops) == len(stops):
            return  # oid nieobecny → no-op (zero zapisu, zero bumpu)
        if not new_stops:
            plan["invalidated_at"] = _now_iso()
            plan["invalidation_reason"] = "ORDER_CANCELLED"
            plan["plan_version"] = int(plan.get("plan_version", 0)) + 1
            plan["last_modified_at"] = plan["invalidated_at"]
        else:
            plan["stops"] = new_stops
            plan["plan_version"] = plan.get("plan_version", 0) + 1
            plan["last_modified_at"] = _now_iso()
        _write_raw(plans)


def mark_stale(
    courier_id: str,
    reason: str = "GPS_DRIFT",
    expected_version: Optional[int] = None,
) -> None:
    """Alias of invalidate_plan for GPS-drift scenarios."""
    invalidate_plan(courier_id, reason, expected_version=expected_version)


def mark_picked_up(courier_id: str, order_id: str,
                   picked_up_at: Optional[str] = None) -> None:
    """V3.19c sub A: update stop.status_at_plan_time to 'picked_up' for this
    order. Prune pickup stop (definitionally done). No-op if plan absent/
    invalidated or order_id not in plan.stops.
    """
    cid = str(courier_id)
    oid = str(order_id)
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        if plan is None or plan.get("invalidated_at") is not None:
            return
        changed = False
        new_stops = []
        for s in plan.get("stops", []):
            if s.get("order_id") == oid:
                if s.get("type") == "pickup":
                    # pickup happened → prune
                    changed = True
                    continue
                if s.get("status_at_plan_time") != "picked_up":
                    s = dict(s)
                    s["status_at_plan_time"] = "picked_up"
                    changed = True
            new_stops.append(s)
        if not changed:
            return
        plan["stops"] = new_stops
        plan["plan_version"] = plan.get("plan_version", 0) + 1
        plan["last_modified_at"] = _now_iso()
        _write_raw(plans)


def _parse_iso_aware(iso: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 → aware UTC datetime. None on empty/non-str/unparsable.
    Naive timestamps are assumed already UTC (plan predicted_at is always UTC).
    Aware timestamps (np. czas_kuriera_warsaw z offsetem +02:00) → przeliczone na UTC.
    """
    if not iso or not isinstance(iso, str):
        return None
    s = iso.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def refloor_pickup(courier_id: str, order_id: str, czas_kuriera_iso: str,
                   min_delta_sec: float = 60.0) -> float:
    """Podnieś predicted_at pickupu do podłogi czas_kuriera i przesuń o tę samą
    dodatnią deltę WSZYSTKIE kolejne stopy (kaskada w dół trasy).

    Źródłowy odpowiednik display-clampa w courier_api: gdy plan policzono zanim
    czas_kuriera (ustalony po odpowiedzi do restauracji) wpłynął, predicted_at
    pickupu kotwiczy na czasie deklarowanym przez restaurację i nie jest nigdy
    przeliczany. Refloor dosuwa plan do przodu — MONOTONICZNIE (tylko później,
    nigdy wcześniej) — żeby trasa i ETA były spójne z obietnicą kurierowi, zanim
    Ziomek przeliczy plan od zera. Naprawia też dropoff (clamp display tego nie robił).

    No-op (zwraca 0.0): brak planu / zinwalidowany; czas_kuriera nieparsowalny;
    brak żywego pickupu dla order_id (już odebrane / przycięte); albo wymagane
    podniesienie < min_delta_sec (unikamy churnu na sub-minutowym szumie).

    Zwraca zastosowane przesunięcie w minutach (0.0 gdy no-op).
    """
    cid = str(courier_id)
    oid = str(order_id)
    floor = _parse_iso_aware(czas_kuriera_iso)
    if floor is None:
        return 0.0
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        if plan is None or plan.get("invalidated_at") is not None:
            return 0.0
        stops = plan.get("stops", [])
        pu_idx = None
        for i, s in enumerate(stops):
            if str(s.get("order_id")) == oid and s.get("type") == "pickup":
                pu_idx = i
                break
        if pu_idx is None:
            return 0.0
        pred = _parse_iso_aware(stops[pu_idx].get("predicted_at"))
        if pred is None:
            return 0.0
        delta_sec = (floor - pred).total_seconds()
        if delta_sec < min_delta_sec:
            return 0.0
        shift = timedelta(seconds=delta_sec)
        # Przesuń pickup ORAZ każdy stop po nim (zachowuje przejazdy/dwell gaps).
        for s in stops[pu_idx:]:
            sp = _parse_iso_aware(s.get("predicted_at"))
            if sp is not None:
                s["predicted_at"] = (sp + shift).isoformat()
        plan["stops"] = stops
        plan["plan_version"] = plan.get("plan_version", 0) + 1
        plan["last_modified_at"] = _now_iso()
        _write_raw(plans)
        return delta_sec / 60.0


SHADOW_LOG_PATH = Path("/root/.openclaw/workspace/dispatch_state/v319c_read_shadow_log.jsonl")


def log_read_shadow_diff(
    courier_id: str,
    fresh_sequence: List[str],
    active_bag_oids: set,
    *,
    now: Optional[datetime] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """V3.19c sub B: observational diff log. Loads saved plan for courier,
    compares BAG ordering in fresh_sequence vs saved plan, appends one JSONL
    line. No-op gdy flag off / brak saved / brak active bag.

    Cicho skip na błędy (read-only observation, nie wpływa na flow).
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS_READ_SHADOW
        if not ENABLE_SAVED_PLANS_READ_SHADOW:
            return
    except Exception:
        return
    cid = str(courier_id)
    if not cid or not active_bag_oids:
        return
    try:
        with _locked(exclusive=False):
            plans = _read_raw()
        saved = plans.get(cid)
        has_saved = saved is not None and saved.get("invalidated_at") is None
        if has_saved:
            saved_bag_order = [
                s["order_id"]
                for s in saved.get("stops", [])
                if s.get("type") == "dropoff"
                and str(s.get("order_id")) in active_bag_oids
            ]
        else:
            saved_bag_order = []
        fresh_bag_order = [
            str(oid) for oid in (fresh_sequence or [])
            if str(oid) in active_bag_oids
        ]
        match = saved_bag_order == fresh_bag_order
        entry = {
            "ts": (now or datetime.now(timezone.utc)).isoformat(),
            "cid": cid,
            "has_saved_plan": has_saved,
            "saved_plan_version": (saved or {}).get("plan_version") if has_saved else None,
            "saved_bag_sequence": saved_bag_order,
            "fresh_bag_sequence": fresh_bag_order,
            "match": match,
            "active_bag_oids": sorted(list(active_bag_oids)),
        }
        if extra:
            entry["extra"] = extra
        from dispatch_v2.core.jsonl_appender import append_jsonl
        append_jsonl(SHADOW_LOG_PATH, entry)
    except Exception as e:
        _log.warning(f"log_read_shadow_diff fail cid={cid}: {e}")


def gc_invalidated(older_than_hours: float = 24.0) -> int:
    """V3.19c sub A: garbage-collect invalidated plans older than threshold.
    Returns count removed. Manual / cron hook — no auto-schedule.
    """
    cutoff_ts = datetime.now(timezone.utc).timestamp() - (older_than_hours * 3600)
    removed = 0
    with _locked(exclusive=True):
        plans = _read_raw()
        to_del = []
        for cid, p in plans.items():
            inv = p.get("invalidated_at")
            if not inv:
                continue
            try:
                inv_ts = datetime.fromisoformat(inv.replace("Z", "+00:00")).timestamp()
            except Exception as _e:
                # A1: dawniej silent → bad invalidated_at zostawał na zawsze
                # (GC nie usuwa). Dedup-by-class cap=50.
                seen = getattr(gc_invalidated, "_warned_inv", set())
                key = (type(_e).__name__, str(inv)[:40])
                if key not in seen and len(seen) < 50:
                    _log.warning(f"invalidated_at parse fail cid={cid} ({type(_e).__name__}: {_e}) input={inv!r}")
                    seen.add(key)
                    gc_invalidated._warned_inv = seen
                continue
            if inv_ts < cutoff_ts:
                to_del.append(cid)
        for cid in to_del:
            del plans[cid]
            removed += 1
        if removed:
            _write_raw(plans)
    return removed


def insert_stop_optimal(
    plan: Dict[str, Any],
    new_order_stops: List[Dict[str, Any]],
    now: datetime,
    leg_min_fn,
) -> Dict[str, Any]:
    """Pure function: given an existing plan + new stops for ONE order
    (pickup+dropoff, or dropoff-only if picked_up), try every legal insertion
    position for the new stops as a block, returning the plan with minimum total
    duration. No I/O.

    leg_min_fn(from_coords, to_coords) -> float minutes.

    Enforces pickup-before-dropoff for the new order when both stops present.
    Does NOT reorder existing stops (incremental, not re-TSP).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    existing = list(plan.get("stops", []))
    start_pos = plan["start_pos"]
    start_coords = (float(start_pos["lat"]), float(start_pos["lng"]))

    new_pickup = next((s for s in new_order_stops if s.get("type") == "pickup"), None)
    new_dropoff = next((s for s in new_order_stops if s.get("type") == "dropoff"), None)
    if new_dropoff is None:
        raise ValueError("insert_stop_optimal requires at least a dropoff stop")

    best_plan: Optional[Dict[str, Any]] = None
    best_total: float = float("inf")

    n = len(existing)
    for d_pos in range(n + 1):
        if new_pickup is None:
            candidate = existing[:d_pos] + [new_dropoff] + existing[d_pos:]
            total = _sequence_total_min(start_coords, candidate, leg_min_fn)
            if total < best_total:
                best_total = total
                best_plan = _build_plan_like(plan, candidate, total)
            continue
        for p_pos in range(d_pos + 1):
            # p_pos <= d_pos → after inserting pickup first then dropoff at d_pos+1
            candidate = (
                existing[:p_pos]
                + [new_pickup]
                + existing[p_pos:d_pos]
                + [new_dropoff]
                + existing[d_pos:]
            )
            total = _sequence_total_min(start_coords, candidate, leg_min_fn)
            if total < best_total:
                best_total = total
                best_plan = _build_plan_like(plan, candidate, total)

    if best_plan is None:
        raise RuntimeError("insert_stop_optimal: no valid sequence found")
    return best_plan


def _sequence_total_min(
    start_coords: Tuple[float, float],
    stops: List[Dict[str, Any]],
    leg_min_fn,
) -> float:
    total = 0.0
    current = start_coords
    for s in stops:
        c = s.get("coords", {})
        nxt = (float(c.get("lat", 0.0)), float(c.get("lng", 0.0)))
        total += leg_min_fn(current, nxt)
        total += float(s.get("dwell_min", 0.0))
        current = nxt
    return total


def _build_plan_like(base: Dict[str, Any], stops: List[Dict[str, Any]],
                     total_min: float) -> Dict[str, Any]:
    """Clone base plan (shallow) with updated stops + optimization_method=incremental."""
    return {
        "start_pos": base["start_pos"],
        "start_ts": base["start_ts"],
        "stops": stops,
        "optimization_method": "incremental",
        # plan_version, created_at, last_modified_at are handled by save_plan.
        "_total_duration_min": round(total_min, 2),
    }


def _validate_plan_body(plan_body: Dict[str, Any]) -> None:
    for key in ("start_pos", "start_ts", "stops", "optimization_method"):
        if key not in plan_body:
            raise ValueError(f"plan_body missing required key: {key}")
    sp = plan_body["start_pos"]
    for k in ("lat", "lng", "source"):
        if k not in sp:
            raise ValueError(f"start_pos missing {k}")
    if plan_body["optimization_method"] not in {"bruteforce", "greedy", "incremental"}:
        raise ValueError(
            f"invalid optimization_method: {plan_body['optimization_method']!r}"
        )
    for s in plan_body["stops"]:
        if s.get("type") not in {"pickup", "dropoff"}:
            raise ValueError(f"invalid stop type: {s.get('type')!r}")
        if "order_id" not in s or "coords" not in s:
            raise ValueError(f"stop missing order_id or coords: {s}")


class ConcurrencyError(RuntimeError):
    """Raised when a plan mutation's expected_version CAS fails."""

    def __init__(self, courier_id: str, expected_version: int,
                 current_version: int):
        self.courier_id = str(courier_id)
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            f"CAS fail for courier {self.courier_id}: "
            f"expected_version={expected_version}, current={current_version}"
        )
