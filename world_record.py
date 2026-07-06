"""world_record — K04 programu refaktoru (ADR-R04): nagrywanie WEJŚĆ decyzji.

Cel: golden corpus do replayu bit-w-bit i testów charakteryzujących stranglera
(docs/refaktor/04-plan-migracji.md K04; ADR-R04). Per decyzja nagrywamy:
snapshot flag (pełny dict + sha1), flotę, order_event, `now`, WSZYSTKIE wyniki
OSRM skonsumowane w tej decyzji (rekorder w osrm_client — łapie też wątki puli
kandydatów i cache-hity) oraz wersje (mtime) map kalibracyjnych.

Flaga: `ENABLE_WORLD_RECORD` przez `C.flag()` (hot-reload; BRAK klucza w
flags.json = OFF → kod inertny). Flip = dopisanie klucza (FLIPMASTER, za ACK).

Zapis: RECORD_DIR/world_record-YYYYMMDD.jsonl przez `core.jsonl_appender`
(O_APPEND+flock). Retencja RETENTION_DAYS — GC przy pierwszym zapisie dnia.

Fail-soft TOTALNY: żaden błąd nagrywania nie zmienia ani nie wywala decyzji.
Zakres v0: proces dispatch-shadow (hook w shadow_dispatcher.process_event).
czasowka_scheduler = osobny proces, świadomie N-D w v0 (dołączy przy wspólnym
WorldState, pakiet 2) — odnotowane w docs/refaktor/05-dziennik.md.

Anty-prod guard (C17): pod pytestem z DOMYŚLNYM RECORD_DIR zapis jest
blokowany — testy muszą jawnie spatchować RECORD_DIR na tmp.

v1 (2026-07-06, sesja A po K13): +`live_inputs` = ŻYWE wejścia decyzji, których
v0 NIE nagrywał, przez co replay czytał je z dysku „teraz" i dryfował (klasa
różnic „krytyczne/miękkie" B/K17). Domykane:
  - `k07` — wynik prefetchu czas_kuriera (HTTP panelu; K07);
  - `loadgov` — obliczony krotka (now/ewma/orders/couriers; zależy od
    orders_state.json + in-proc EWMA _LOADGOV_STATE — nieodtwarzalne w świeżym
    procesie);
  - `files` — treść (przycięta do floty) plików czytanych w scoringu:
    courier_reliability, courier_plans, eta_quantile_map, restaurant_prep_bias.
`k07`/`loadgov` łapane cienkim hookiem `note_decision_input` z silnika (main
thread, first-note-wins → odporne na rekurencyjny kontrfaktyk early-bird);
`files` snapshotowane NA WEJŚCIU around_assess (czyste odczyty — ZERO patchowania
żywego procesu). Format ADDITIVE: brak `live_inputs` = rekord v0, replay działa
po staremu (best-effort). Bit-w-bit replay wymaga rekordu wr1.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dispatch_v2 import common as C
from dispatch_v2 import osrm_client
from dispatch_v2.core.jsonl_appender import append_jsonl

_log = C.setup_logger("world_record", "/root/.openclaw/workspace/scripts/logs/world_record.log")

_DEFAULT_RECORD_DIR = "/root/.openclaw/workspace/dispatch_state/world_record"
RECORD_DIR = _DEFAULT_RECORD_DIR
RETENTION_DAYS = 14
SCHEMA = "wr1"

# ── v1: capture wejść obliczonych w silniku (K07, loadgov) ──
# Decyzje w dispatch-shadow są SEKWENCYJNE (shadow_dispatcher._tick: `for ev in
# events`), a pula wątków żyje WEWNĄTRZ jednej decyzji i NIE liczy k07/loadgov
# (te są w main-thread _assess_order_impl przed pulą) → prosty słownik + lock
# wystarcza. `first-note-wins`: rekurencyjny kontrfaktyk early-bird woła
# _assess_order_impl ponownie w tej samej ramce around_assess; pierwszy note
# (decyzji ZEWNĘTRZNEJ) wygrywa (loadgov liczony przed bramką early-bird).
_CAP_LOCK = threading.Lock()
_CAP_ACTIVE = False
_CAP: Dict[str, Any] = {}


def note_decision_input(key: str, value: Any) -> None:
    """Hook silnika: zapamiętaj żywe wejście decyzji (k07/loadgov). Poza oknem
    capture (OFF / brak around_assess) = natychmiastowy no-op. first-note-wins.
    NIGDY nie podnosi wyjątku (nie może dotknąć decyzji)."""
    try:
        if not _CAP_ACTIVE:
            return
        with _CAP_LOCK:
            if key not in _CAP:
                _CAP[key] = value
    except Exception:
        pass


def _cap_begin() -> None:
    global _CAP_ACTIVE
    with _CAP_LOCK:
        _CAP.clear()
        _CAP_ACTIVE = True


def _cap_end() -> Dict[str, Any]:
    global _CAP_ACTIVE
    with _CAP_LOCK:
        _CAP_ACTIVE = False
        return dict(_CAP)

_CALIB_FILES = {
    "eta_quantile_map": "/root/.openclaw/workspace/dispatch_state/eta_quantile_map.json",
    "restaurant_prep_bias": "/root/.openclaw/workspace/dispatch_state/restaurant_prep_bias.json",
    "courier_reliability": "/root/.openclaw/workspace/dispatch_state/courier_reliability.json",
    "courier_tiers": "/root/.openclaw/workspace/dispatch_state/courier_tiers.json",
}


def enabled() -> bool:
    try:
        return bool(C.flag("ENABLE_WORLD_RECORD", False))
    except Exception:
        return False


def _blocked_under_test() -> bool:
    return ("PYTEST_CURRENT_TEST" in os.environ or "DISPATCH_UNDER_PYTEST" in os.environ) \
        and RECORD_DIR == _DEFAULT_RECORD_DIR


def _json_safe(o: Any, _depth: int = 0) -> Any:
    """Rekurencyjna serializacja do JSON-safe: datetime→iso, dataclass→dict,
    tuple/set→list, Path→str; nieznane → repr (nigdy wyjątek)."""
    if _depth > 12:
        return "…depth…"
    try:
        if o is None or isinstance(o, (bool, int, float, str)):
            return o
        if isinstance(o, datetime):
            return o.isoformat()
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return {f.name: _json_safe(getattr(o, f.name), _depth + 1)
                    for f in dataclasses.fields(o)}
        if isinstance(o, dict):
            return {str(k): _json_safe(v, _depth + 1) for k, v in o.items()}
        if isinstance(o, (list, tuple, set, frozenset)):
            return [_json_safe(x, _depth + 1) for x in o]
        if isinstance(o, Path):
            return str(o)
        return repr(o)
    except Exception:
        return "…unserializable…"


def _read_json_safe(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _snapshot_live_files(fleet_snapshot: Any) -> Dict[str, Any]:
    """Snapshot NA WEJŚCIU (przed decyzją) treści plików czytanych w scoringu,
    PRZYCIĘTY do kurierów z floty (decyzja dotyka tylko ich → rekord szczupły).
    Czyste odczyty dysku — zero patchowania żywego procesu. Każdy fail-soft→None.
    Ścieżki brane z KANONICZNYCH stałych modułów (lazy import, unik cyklu)."""
    out: Dict[str, Any] = {}
    try:
        fleet_cids = {str(c) for c in (fleet_snapshot or {})}
    except Exception:
        fleet_cids = set()
    # reliability (C.A2_RELIABILITY_FEED_PATH; 2 czytelników: A2 soft + RAMPA) —
    # zachowaj fleet_median + tylko couriers z floty (struktura jak u czytelnika).
    try:
        rp = getattr(C, "A2_RELIABILITY_FEED_PATH", "") or ""
        raw = _read_json_safe(rp) if rp else None
        if isinstance(raw, dict):
            cr = raw.get("couriers") or {}
            out["reliability"] = {
                "fleet_median_breach_rate": raw.get("fleet_median_breach_rate"),
                "couriers": {str(k): v for k, v in cr.items() if str(k) in fleet_cids},
            }
    except Exception:
        pass
    # plans (plan_manager.PLANS_FILE) — tylko plany kurierów z floty.
    try:
        from dispatch_v2 import plan_manager as _pm
        raw = _read_json_safe(str(_pm.PLANS_FILE))
        if isinstance(raw, dict):
            out["plans"] = {str(k): v for k, v in raw.items() if str(k) in fleet_cids}
    except Exception:
        pass
    # calib eta/bias (małe mapy keyed po slocie/restauracji) — całość.
    try:
        from dispatch_v2 import calib_maps as _cm
        out["eta_quantile"] = _read_json_safe(_cm.ETA_QUANTILE_MAP_PATH)
        out["prep_bias"] = _read_json_safe(_cm.PREP_BIAS_MAP_PATH)
    except Exception:
        pass
    return out


def _calib_versions() -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for name, p in _CALIB_FILES.items():
        try:
            st = os.stat(p)
            out[name] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            out[name] = None
    return out


def _gc(now_utc: datetime) -> None:
    """Skasuj pliki starsze niż RETENTION_DAYS (po dacie w nazwie). Fail-soft."""
    try:
        cutoff = now_utc.date().toordinal() - RETENTION_DAYS
        for p in Path(RECORD_DIR).glob("world_record-*.jsonl"):
            try:
                d = datetime.strptime(p.stem.split("-", 1)[1], "%Y%m%d").date()
                if d.toordinal() < cutoff:
                    p.unlink()
            except Exception:
                continue
    except Exception:
        pass


def _capture(order_event: Optional[dict], fleet_snapshot: Any, now: Optional[datetime],
             flags_snap: Optional[dict], osrm_calls: List[dict], result: Any,
             live_inputs: Optional[dict] = None) -> None:
    if _blocked_under_test():
        return
    ts = datetime.now(timezone.utc)
    flags_blob = json.dumps(flags_snap or {}, sort_keys=True, ensure_ascii=False)
    rec = {
        "schema": SCHEMA,
        "ts": ts.isoformat(),
        "order_id": str((order_event or {}).get("order_id") or ""),
        "now": _json_safe(now),
        "flags_sha1": hashlib.sha1(flags_blob.encode("utf-8")).hexdigest(),
        "flags": flags_snap or {},
        "order_event": _json_safe(order_event),
        "fleet": _json_safe(fleet_snapshot),
        "osrm_calls": _json_safe(osrm_calls),
        "n_osrm": len(osrm_calls or []),
        "calib": _calib_versions(),
        "live_inputs": _json_safe(live_inputs or {}),  # v1: żywe wejścia decyzji (K07/loadgov/pliki)
        "verdict": getattr(result, "verdict", None),
    }
    path = Path(RECORD_DIR) / f"world_record-{ts:%Y%m%d}.jsonl"
    if not path.exists():
        _gc(ts)
    append_jsonl(str(path), rec)


def around_assess(fn: Callable[[], Any], order_event: Optional[dict] = None,
                  fleet_snapshot: Any = None, now: Optional[datetime] = None) -> Any:
    """Opakowanie wywołania assess_order. Flaga OFF → czysta delegacja fn().
    Flaga ON → nagraj wejścia + wyniki OSRM tej decyzji. NIGDY nie podnosi
    własnych wyjątków (wyjątek z fn() propaguje bez zmian)."""
    if not enabled():
        return fn()
    flags_snap = None
    started = False
    live_files: Dict[str, Any] = {}
    capturing = False
    try:
        flags_snap = dict(C.load_flags() or {})
        osrm_client.world_record_start()
        started = True
        _cap_begin()               # v1: uzbrój hook note_decision_input (K07/loadgov)
        capturing = True
        live_files = _snapshot_live_files(fleet_snapshot)  # v1: snapshot plików NA WEJŚCIU
    except Exception:
        started = False
    try:
        result = fn()
    except Exception:
        if started:
            try:
                osrm_client.world_record_stop()
            except Exception:
                pass
        if capturing:
            _cap_end()
        raise
    try:
        calls = osrm_client.world_record_stop() if started else []
        notes = _cap_end() if capturing else {}
        live_inputs = dict(live_files)
        if notes:
            live_inputs.update(notes)  # k07, loadgov
        _capture(order_event, fleet_snapshot, now, flags_snap, calls, result,
                 live_inputs=live_inputs)
    except Exception as e:
        try:
            _log.warning(f"world_record capture fail (decyzja NIEDOTKNIĘTA): {e}")
        except Exception:
            pass
    return result
