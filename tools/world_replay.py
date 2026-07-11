#!/usr/bin/env python3
"""world_replay — K06 programu refaktoru (ADR-R04): replay decyzji bit-w-bit
z nagrań `world_record` (K04).

Co robi: dla nagranej decyzji odtwarza ŚWIAT (zamrożone flagi przez mechanizm
K05, zegar `now` z nagrania [wymaga K06a], flota zrehydrowana do CourierState,
wyniki OSRM serwowane z nagrania — zero sieci) i woła PRAWDZIWE
`dispatch_pipeline.assess_order`, po czym porównuje wynik z kanonicznym
zapisem w `shadow_decisions.jsonl` (join po order_id+ts).

SANDBOX (replay NIE dotyka produkcji):
  - efekty uboczne: bufor K08 uzbrajany SIŁĄ (bez flagi) i PO decyzji
    ODRZUCANY zamiast flushowany → 8 divertowanych writerów połknięte;
  - world_record.enabled → False (nagranie nie nagrywa się ponownie);
  - observability candidate_logger → no-op;
  - telegram_utils.send_admin_alert → no-op (poison-alert N-D w K08);
  - env DISPATCH_UNDER_PYTEST=1 → file-logi silnika wyciszone (guard 03.07);
  - OSRM route/table → wyłącznie odtwarzanie z nagrania (miss = liczony,
    zwracany sentinel-fallback ORYGINALNĄ funkcją NIE jest wołany).

Użycie:
  venvs/dispatch/bin/python -m dispatch_v2.tools.world_replay --order-id 485904
  ... --record-file <jsonl> [--shadow-file <jsonl>] [--json]

Wynik: raport pól (verdict/reason/best_cid/score/pool_*) replay vs zapis.
Exit 0 = pełna zgodność; 1 = różnice; 2 = błąd/brak danych/missy OSRM.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DISPATCH_UNDER_PYTEST", "1")  # wyciszenie file-logów silnika

SCRIPTS = "/root/.openclaw/workspace/scripts"
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

RECORD_DIR = "/root/.openclaw/workspace/dispatch_state/world_record"
SHADOW_LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"

REPLAY_CLASSES = (
    "INPUT_MISS",
    "OSRM_MISS",
    "CRITICAL_DIFF",
    "SOFT_DIFF",
    "PARITY",
)
CRITICAL_FIELDS = frozenset({"verdict", "best_cid", "best_score"})


def _parse_dt(v):
    if not v or not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_field(val, ftype_name: str):
    if val is None:
        return None
    if "datetime" in ftype_name:
        return _parse_dt(val) if isinstance(val, str) else val
    if "Tuple" in ftype_name or "tuple" in ftype_name:  # łapie też Optional[Tuple[...]]
        return tuple(val) if isinstance(val, list) else val
    return val


def rehydrate_fleet(fleet_json: dict) -> dict:
    """dict{cid: dict pól} → dict{cid: CourierState} (pola wspólne; iso→dt,
    list→tuple wg anotacji dataclassy; brakujące pola = defaulty dataclassy)."""
    from dispatch_v2.courier_resolver import CourierState
    fields = {f.name: str(f.type) for f in dataclasses.fields(CourierState)}
    out = {}
    for cid, rec in (fleet_json or {}).items():
        kwargs = {}
        for name, ftype in fields.items():
            if isinstance(rec, dict) and name in rec:
                kwargs[name] = _coerce_field(rec[name], ftype)
        out[str(cid)] = CourierState(**kwargs)
    return out


class OsrmReplayer:
    """Serwuje wyniki route/table z nagrania (per-klucz FIFO; wyczerpana kolejka
    → ostatni wynik; brak klucza → miss + sentinel z fallbacku haversine NIE
    jest wołany — zwracamy None-safe strukturę i liczymy miss)."""

    def __init__(self, calls):
        self.q = {}
        self.misses = []
        for c in calls or []:
            key = (c.get("kind"), json.dumps(c.get("key"), sort_keys=True))
            self.q.setdefault(key, []).append(c.get("result"))
        self.last = {k: v[-1] for k, v in self.q.items()}

    def _take(self, kind, key_obj, empty):
        key = (kind, json.dumps(key_obj, sort_keys=True))
        seq = self.q.get(key)
        if seq:
            return seq.pop(0) if len(seq) > 1 else seq[0]
        if key in self.last:
            return self.last[key]
        self.misses.append(key)
        return empty

    def route(self, from_ll, to_ll, use_cache=True):
        return self._take("route", [list(from_ll or ()), list(to_ll or ())],
                          {"duration_min": 9999.0, "distance_km": 999.0,
                           "osrm_fallback": True, "replay_miss": True})

    def table(self, origins, destinations):
        empty = [[9999.0 for _ in (destinations or [])] for _ in (origins or [])]
        return self._take("table", [[list(o or ()) for o in (origins or [])],
                                    [list(d or ()) for d in (destinations or [])]], empty)


def _iter_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except json.JSONDecodeError:
                continue


def find_record(order_id: str, record_file: str | None):
    files = ([record_file] if record_file
             else sorted(str(p) for p in Path(RECORD_DIR).glob("world_record-*.jsonl")))
    for p in reversed(files):
        for rec in _iter_jsonl(p):
            if str(rec.get("order_id")) == str(order_id):
                return rec
    return None


def find_shadow(order_id: str, ts_iso: str, shadow_file: str | None):
    best = None
    ts = _parse_dt(ts_iso)
    for rec in _iter_jsonl(shadow_file or SHADOW_LOG):
        if str(rec.get("order_id")) != str(order_id):
            continue
        rts = _parse_dt(rec.get("ts"))
        if ts and rts and abs((rts - ts).total_seconds()) > 300:
            continue
        best = rec
    return best


def _extract(result_like) -> dict:
    """Pola porównania z żywego PipelineResult LUB z zapisu shadow (dict)."""
    if isinstance(result_like, dict):
        best = result_like.get("best") or {}
        return {
            "verdict": result_like.get("verdict"),
            "reason": result_like.get("reason"),
            "best_cid": best.get("courier_id"),
            "best_score": (round(float(best["score"]), 3)
                           if best.get("score") is not None else None),
            "pool_feasible": result_like.get("pool_feasible_count"),
            "pool_total": result_like.get("pool_total_count"),
        }
    best = getattr(result_like, "best", None)
    score = getattr(best, "score", None) if best is not None else None
    return {
        "verdict": getattr(result_like, "verdict", None),
        "reason": getattr(result_like, "reason", None),
        "best_cid": (str(getattr(best, "courier_id", None))
                     if best is not None else None),
        "best_score": round(float(score), 3) if score is not None else None,
        "pool_feasible": getattr(result_like, "pool_feasible_count", None),
        "pool_total": getattr(result_like, "pool_total_count", None),
    }


def classify_replay(recorded: dict | None, replayed: dict | None,
                    osrm_misses: int = 0,
                    input_miss_reason: str | None = None) -> dict:
    """Klasyfikuje jeden frozen record do dokladnie jednej klasy replay-truth.

    Precedencja jest czescia kontraktu: brak wejscia uniewaznia porownanie,
    brak nagranego OSRM uniewaznia diff, a dopiero kompletny oracle moze byc
    CRITICAL/SOFT/PARITY. Funkcja jest czysta, aby frozen golden i mutation
    probe nie zależaly od pipeline ani sieci.
    """
    if input_miss_reason:
        return {"class": "INPUT_MISS", "reason": input_miss_reason, "diffs": {}}
    if recorded is None or replayed is None:
        return {"class": "INPUT_MISS", "reason": "missing_comparison_input",
                "diffs": {}}
    if osrm_misses:
        return {"class": "OSRM_MISS", "reason": "recorded_osrm_call_missing",
                "diffs": {}}

    keys = tuple(dict.fromkeys((*replayed.keys(), *recorded.keys())))
    diffs = {
        key: {"replay": replayed.get(key), "zapis": recorded.get(key)}
        for key in keys if replayed.get(key) != recorded.get(key)
    }
    if any(key in CRITICAL_FIELDS for key in diffs):
        cls = "CRITICAL_DIFF"
    elif diffs:
        cls = "SOFT_DIFF"
    else:
        cls = "PARITY"
    return {"class": cls, "reason": None, "diffs": diffs}


def _serve_live_inputs(rec, dp, C, tmpdir, _patch):
    """v1: serwuj ŻYWE wejścia decyzji z nagrania zamiast czytać dysk „teraz".

    Zwraca (k07_dict_or_None, loadgov_tuple_or_None) do dalszego patcha.
    Pliki (reliability/plans/eta/bias): zrzut treści do tmp + przekierowanie
    KANONICZNYCH stałych ścieżek + reset mtime-cache (świeży proces mógł już
    zcache'ować realny plik przy poprzednim rekordzie w pętli bramki). loadgov:
    krotka wprost (patch _loadgov_compute niżej). k07: dict prefetchu (patch
    get_fresh niżej). Brak `live_inputs` (rekord v0) → (None, None), stary
    best-effort. Fail-soft per pole."""
    li = rec.get("live_inputs")
    if not isinstance(li, dict):
        return None, None

    def _redirect(mod, attr, content, cache_obj=None, cache_reset=None):
        if content is None:
            return
        try:
            p = os.path.join(tmpdir, f"{attr}.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(content, fh, ensure_ascii=False)
            _patch(mod, attr, p if not isinstance(getattr(mod, attr), Path) else Path(p))
            if cache_obj is not None and cache_reset is not None:
                cache_obj.update(cache_reset)
        except Exception:
            pass

    # reliability — C.A2_RELIABILITY_FEED_PATH (2 czytelników: A2 soft + RAMPA),
    # cache dp._A2_FEED_CACHE (mtime-keyed → reset mtime=None).
    _redirect(C, "A2_RELIABILITY_FEED_PATH", li.get("reliability"),
              getattr(dp, "_A2_FEED_CACHE", None), {"mtime": None})
    # plans — plan_manager.PLANS_FILE, cache _perf_plans_cache (key-keyed → reset).
    try:
        from dispatch_v2 import plan_manager as _pm
        _redirect(_pm, "PLANS_FILE", li.get("plans"),
                  getattr(_pm, "_perf_plans_cache", None), {"key": None, "data": None})
        if li.get("plans") is not None:
            lock_path = os.path.join(tmpdir, "PLANS_FILE.lock")
            with open(lock_path, "wb"):
                pass
            _patch(_pm, "LOCK_FILE",
                   lock_path if not isinstance(getattr(_pm, "LOCK_FILE"), Path)
                   else Path(lock_path))
    except Exception:
        pass
    # calib eta/bias — calib_maps.*_PATH, cache _eta_cache/_bias_cache (mtime=None).
    try:
        from dispatch_v2 import calib_maps as _cm
        _redirect(_cm, "ETA_QUANTILE_MAP_PATH", li.get("eta_quantile"),
                  getattr(_cm, "_eta_cache", None), {"mtime": None})
        _redirect(_cm, "PREP_BIAS_MAP_PATH", li.get("prep_bias"),
                  getattr(_cm, "_bias_cache", None), {"mtime": None})
    except Exception:
        pass

    k07 = li.get("k07") if isinstance(li.get("k07"), dict) else None
    lg = li.get("loadgov")
    loadgov = tuple(lg) if isinstance(lg, (list, tuple)) and len(lg) == 4 else None
    return k07, loadgov


def replay_one(rec: dict) -> tuple[dict, int]:
    """Zwraca (extract z replayu, osrm_misses). Pełny sandbox — patrz moduł."""
    from dispatch_v2 import common as C
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import osrm_client, effects_buffer, world_record
    from dispatch_v2 import telegram_utils
    from dispatch_v2.observability import candidate_logger

    osrm = OsrmReplayer(rec.get("osrm_calls"))
    fleet = rehydrate_fleet(rec.get("fleet"))
    now = _parse_dt(rec.get("now")) or _parse_dt(rec.get("ts"))

    saved = {}

    def _patch(obj, name, val):
        if (id(obj), name) not in saved:
            saved[(id(obj), name)] = (obj, name, getattr(obj, name))
        setattr(obj, name, val)

    _tmp = tempfile.TemporaryDirectory(prefix="wr_replay_")
    try:
        _patch(C, "_FLAGS_SNAPSHOT_OVERRIDE", dict(rec.get("flags") or {}))  # K05
        _patch(osrm_client, "route", osrm.route)
        _patch(osrm_client, "table", osrm.table)
        # v1: serwuj żywe wejścia (reliability/plans/calib z nagrania; loadgov/k07 niżej).
        _k07_rec, _loadgov_rec = _serve_live_inputs(rec, dp, C, _tmp.name, _patch)
        # K07: gdy nagrany (wr1) → serwuj wynik prefetchu z nagrania; inaczej (wr0)
        # utwardzenie K17 = stub {} (zero żywego fetchu HTTP w sandboxie).
        if _k07_rec is not None:
            _patch(dp, "get_fresh_czas_kuriera_for_bag", lambda *a, _r=_k07_rec, **k: dict(_r))
        else:
            _patch(dp, "get_fresh_czas_kuriera_for_bag", lambda *a, **k: {})
        # loadgov: gdy nagrany (wr1) → krotka wprost (in-proc EWMA nieodtwarzalna).
        if _loadgov_rec is not None:
            _patch(dp, "_loadgov_compute", lambda *a, _t=_loadgov_rec, **k: tuple(_t))
        _patch(world_record, "enabled", lambda: False)
        _patch(telegram_utils, "send_admin_alert", lambda *a, **k: None)
        _patch(candidate_logger, "get_logger",
               lambda: type("N", (), {"_flag_check": lambda s: False,
                                      "log_evaluation": lambda s, **k: None})())
        _patch(effects_buffer, "_ACTIVE", True)   # K08: połknij efekty…
        _patch(effects_buffer, "begin", lambda: False)  # …i nie pozwól wrapperowi flushować
        result = dp.assess_order(dict(rec.get("order_event") or {}), fleet, None, now)
        return _extract(result), len(osrm.misses)
    finally:
        with effects_buffer._LOCK:
            effects_buffer._Q.clear()
        for obj, name, val in saved.values():
            setattr(obj, name, val)
        effects_buffer._ACTIVE = False
        try:
            _tmp.cleanup()
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order-id", required=True)
    ap.add_argument("--record-file")
    ap.add_argument("--shadow-file")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    rec = find_record(a.order_id, a.record_file)
    if rec is None:
        print(f"BRAK nagrania world_record dla order_id={a.order_id}")
        return 2
    if not rec.get("now"):
        print("⚠ nagranie ma now=null (sprzed K06a) — replay czasu NIEwierny")

    replayed, misses = replay_one(rec)
    shadow = find_shadow(a.order_id, rec.get("ts"), a.shadow_file)
    recorded = _extract(shadow) if shadow else None

    diffs = {}
    if recorded:
        diffs = {k: {"replay": replayed[k], "zapis": recorded[k]}
                 for k in replayed if replayed[k] != recorded[k]}

    out = {"order_id": a.order_id, "osrm_misses": misses,
           "replay": replayed, "zapis": recorded, "roznice": diffs,
           "verdict_rec": rec.get("verdict")}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    else:
        print(f"order {a.order_id}: osrm_misses={misses}")
        print(f"  replay: {replayed}")
        print(f"  zapis : {recorded}")
        if diffs:
            print(f"  RÓŻNICE: {diffs}")
    if recorded is None or misses:
        return 2
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
