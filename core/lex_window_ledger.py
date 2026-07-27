"""Kanoniczny writer ledgera okna odbioru (P-1 lex-committed-window), schemat v2.

WB1 faza 1 incydentu CZASY 492. Kontrakt zamrożony w `docs/WB1_LEDGER_V2_SCHEMA.md`
— TEN plik jest jedyną implementacją tego kontraktu i jedynym miejscem, które
decyduje, do KTÓREGO pliku trafia wpis.

Problem u źródła (v1): pole `applied` było wartością flagi
`ENABLE_LEX_COMMITTED_WINDOW`, a nie faktem zapisu planu. Trzy timery obserwatorów
(carried-first-guard ~3 min, b-route-shadow ~3,5 min, bundle-calib-shadow ~5 min)
wołają tę samą warstwę kanonu co silnik i pisały `applied: true`, choć ich zapisy
planu idą na temp. Pomiar 2026-07-27 na żywym pliku: 612 wierszy/24 h, z tego 384
(62,7 %) to powtórki tej samej treści, mediana odstępu 0,36 min, `applied == true`
w 612/612. Na takim korpusie nie da się skalibrować progów WB2.

Naprawa u źródła: rola wywołania jest **własnością ścieżki kodu**, nie procesu i nie
env unitu (env obserwatora dowodnie kłamie — `dispatch-b-route-shadow.service.d/
route-flag-parity.conf` deklaruje `ENABLE_LEX_COMMITTED_WINDOW=1`). Dlatego rola
podróżuje jako jawny `LedgerContext`, tworzony wyłącznie przez `writer_context()`,
którą woła TYLKO `plan_recheck` na ścieżkach mogących utrwalić plan. Brak kontekstu
= OBSERVER: kierunek fail-safe (gubimy wiersz kanoniczny, którego brak widać w
`coverage`, zamiast zanieczyścić kanon).

Semantyka zamrożona — `applied` NIE ISTNIEJE w v2. Trzy rozłączne fakty:
  decided  — silnik wybrał kandydata (rekord `decision`),
  written  — plan realnie utrwalony po CAS (rekord `write_receipt`),
  served   — sekwencja wyszła do konsumenta (rekord `served_receipt`).
`decided` nie implikuje `written`, `written` nie implikuje `served`.

Bezpieczeństwo hot-path: żadna funkcja publiczna NIE RZUCA — błąd zapisu jest
logowany i liczony, decyzja silnika przeżywa. Kill-switch czytany przy każdym
wywołaniu (hot-reload z konstrukcji, bez cache'a module-level).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dispatch_v2 import common as _C
from dispatch_v2.core.jsonl_appender import _locked_namespace, append_jsonl

_log = logging.getLogger("dispatch.lex_window_ledger")

SCHEMA = "lex_window_ledger.v2"
SCHEMA_VERSION = 2

#: Kill-switch. Niedecyzyjny (nie zmienia treści decyzji) → poza ETAP4_DECISION_FLAGS,
#: dokładnie jak precedens `ENABLE_STAGE_TIMING_OBSERVATION`. OFF = ścieżka legacy v1
#: bajt-w-bajt jak przed WB1; ON = wyłącznie v2 z rozdziałem ról, v1 zamrożony.
FLAG_LEDGER_V2 = "ENABLE_LEX_WINDOW_LEDGER_V2"

# ── ścieżki (czytane przez `_target_path`, więc testy mogą je monkeypatchować) ──
CANONICAL_PATH = str(_C.STATE_DIR / "lex_window_ledger_v2.jsonl")
OBSERVATION_PATH = str(_C.STATE_DIR / "lex_window_ledger_v2_observations.jsonl")
#: Legacy v1 — pisany WYŁĄCZNIE gdy kill-switch OFF; po flipie zamrożony na dysku.
LEGACY_V1_PATH = str(_C.STATE_DIR / "lex_committed_window_shadow.jsonl")

# ── budżet / backpressure ──
MAX_RECORDS_PER_RUN = int(os.environ.get("LEX_LEDGER_MAX_RECORDS_PER_RUN", "2000"))
MAX_BYTES = int(os.environ.get("LEX_LEDGER_MAX_BYTES", str(64 * 1024 * 1024)))
CANDIDATE_SUMMARY_MAX = 12

_ROTATION_SUFFIX_FMT = "%Y%m%dT%H%M%SZ"
_RUN_STATE_KEEP = 8


class Role(str, Enum):
    """Rola wywołania. Jedyne dwie wartości; brak kontekstu ≡ OBSERVER."""

    WRITER = "writer"
    OBSERVER = "observer"


@dataclass(frozen=True)
class LedgerContext:
    """Jawny kontekst wywołania — nośnik roli. Tworzony TYLKO przez fabryki niżej."""

    role: Role
    source: str
    trigger: str
    run_id: str
    courier_id: Optional[str] = None
    route_generation: Optional[int] = None

    def for_courier(self, courier_id: Any,
                    route_generation: Optional[int] = None) -> "LedgerContext":
        return replace(self, courier_id=str(courier_id),
                       route_generation=route_generation)


def writer_context(source: str, trigger: str) -> LedgerContext:
    """Kontekst ścieżki, która MOŻE utrwalić plan. Woła wyłącznie `plan_recheck`."""
    return LedgerContext(role=Role.WRITER, source=source, trigger=trigger,
                         run_id=uuid.uuid4().hex)


def observer_context(source: str, trigger: str = "observe") -> LedgerContext:
    """Kontekst read-only. Wpisy trafiają do pliku obserwacyjnego, nigdy do kanonu."""
    return LedgerContext(role=Role.OBSERVER, source=source, trigger=trigger,
                         run_id=uuid.uuid4().hex)


def _target_path(ctx: Optional[LedgerContext]) -> str:
    """JEDYNE odwzorowanie rola → plik w całym repo.

    Brak kontekstu albo rola inna niż WRITER ⇒ plik obserwacyjny. Ta funkcja jest
    punktem, który mutation-test musi zaczerwienić po usunięciu rozróżnienia ról.
    """
    if ctx is None or ctx.role is not Role.WRITER:
        return OBSERVATION_PATH
    return CANONICAL_PATH


def ledger_v2_enabled() -> bool:
    """Kill-switch czytany W MOMENCIE WYWOŁANIA → hot-reload bez restartu."""
    try:
        return bool(_C.decision_flag(FLAG_LEDGER_V2))
    except Exception:
        return False


# ── stan procesu: liczniki pokrycia i budżetu (nigdy nie wpływa na decyzję) ──
_RUN_STATE: Dict[str, Dict[str, Any]] = {}
_LAST_EMIT_US: Optional[int] = None
_FP_CACHE: Dict[str, str] = {}
_CODE_FP: Optional[str] = None


def _run_state(run_id: str) -> Dict[str, Any]:
    st = _RUN_STATE.get(run_id)
    if st is None:
        st = {"seq": 0, "dropped": 0, "errors": 0}
        _RUN_STATE[run_id] = st
        if len(_RUN_STATE) > _RUN_STATE_KEEP:
            for stale in list(_RUN_STATE)[:-_RUN_STATE_KEEP]:
                _RUN_STATE.pop(stale, None)
    return st


def reset_state() -> None:
    """Wyczyść liczniki procesu (izolacja testów)."""
    global _LAST_EMIT_US, _CODE_FP
    _RUN_STATE.clear()
    _FP_CACHE.clear()
    _PENDING.clear()
    _LAST_EMIT_US = None
    _CODE_FP = None


def stats(run_id: str) -> Dict[str, Any]:
    """Migawka liczników pokrycia dla danego przebiegu (diagnostyka/testy)."""
    return dict(_run_state(run_id))


def _sha16(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _flag_fingerprint(run_id: str) -> str:
    """Odcisk flag — liczony raz na przebieg (w oknie ticku flagi są stałe)."""
    fp = _FP_CACHE.get(run_id)
    if fp is None:
        try:
            fp = _C.flag_fingerprint()
        except Exception:
            fp = ""
        _FP_CACHE.clear()
        _FP_CACHE[run_id] = fp
    return fp


def _code_fingerprint() -> str:
    """Odcisk ŹRÓDŁA warstwy reorderu — wykrywa zmianę kodu bez zmiany flag."""
    global _CODE_FP
    if _CODE_FP is None:
        try:
            import inspect

            from dispatch_v2 import plan_recheck as _PR

            _CODE_FP = _sha16(inspect.getsource(_PR._lex_committed_window_reorder))
        except Exception:
            _CODE_FP = ""
    return _CODE_FP


def _maybe_rotate(path: str) -> None:
    """Rotacja rozmiarowa bez utraty danych, pod TYM SAMYM lockiem co append.

    `os.rename` zachowuje komplet wierszy pod nową nazwą; `jsonl_appender.
    _open_current` sam wykrywa przeniesiony inode i ponawia otwarcie aktywnej
    ścieżki. Dzielenie namespace-locka z appendem jest tu warunkiem poprawności —
    dlatego reużywamy `_locked_namespace`, zamiast zakładać własny lock.
    """
    p = Path(path)
    try:
        if not p.exists() or p.stat().st_size <= MAX_BYTES:
            return
    except OSError:
        return
    with _locked_namespace(p):
        try:
            if p.stat().st_size <= MAX_BYTES:
                return  # inny proces zdążył obrócić
            stamp = datetime.now(timezone.utc).strftime(_ROTATION_SUFFIX_FMT)
            os.rename(str(p), f"{p}.{stamp}")
        except OSError as exc:
            _log.warning("lex_window_ledger rotacja nieudana %s: %s", path, exc)


def _emit(ctx: Optional[LedgerContext], path: str, record: Dict[str, Any]) -> None:
    """Append append-only. NIGDY nie rzuca — błąd zapisu nie może wywrócić decyzji."""
    global _LAST_EMIT_US
    t0 = time.perf_counter_ns()
    try:
        _maybe_rotate(path)
        append_jsonl(path, record, default=str)
    except Exception as exc:
        run_id = ctx.run_id if ctx is not None else "-"
        _run_state(run_id)["errors"] += 1
        _log.warning("lex_window_ledger zapis nieudany %s: %s: %s",
                     path, type(exc).__name__, exc)
    finally:
        _LAST_EMIT_US = (time.perf_counter_ns() - t0) // 1000


def _budget_ok(ctx: Optional[LedgerContext]) -> bool:
    """Backpressure: po przekroczeniu limitu na przebieg rekordy są odrzucane."""
    run_id = ctx.run_id if ctx is not None else "-"
    st = _run_state(run_id)
    if st["seq"] >= MAX_RECORDS_PER_RUN:
        st["dropped"] += 1
        return False
    return True


def _maybe_heartbeat(ctx: Optional[LedgerContext]) -> None:
    """Raz na przebieg: pełny odcisk flag + kod + tożsamość procesu.

    Pełni dwie role naraz: (a) rozwija `flags.fingerprint_sha` z rekordów decyzji,
    (b) jest dowodem POKRYCIA — przebieg bez heartbeatu w pliku oznacza, że writer
    w ogóle nie doszedł do warstwy okna (inaczej cisza jest nieodróżnialna od braku
    rozjazdów).
    """
    run_id = ctx.run_id if ctx is not None else "-"
    st = _run_state(run_id)
    if st.get("heartbeat"):
        return
    st["heartbeat"] = True
    record = _header(ctx, "heartbeat", "", "")
    record.update({
        "caller": {
            "role": (ctx.role.value if ctx is not None else Role.OBSERVER.value),
            "source": (ctx.source if ctx is not None else "unknown"),
            "trigger": (ctx.trigger if ctx is not None else "observe"),
            "pid": os.getpid(),
        },
        "flags": {
            "fingerprint": _flag_fingerprint(run_id),
            "fingerprint_sha": _sha16(_flag_fingerprint(run_id)),
            "code_fingerprint": _code_fingerprint(),
        },
    })
    _emit(ctx, _target_path(ctx), record)


def _header(ctx: Optional[LedgerContext], kind: str, decision_id: str,
            attempt_id: str) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "record_kind": kind,
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "decision_id": decision_id,
        "attempt_id": attempt_id,
        "run_id": ctx.run_id if ctx is not None else None,
    }


def _legacy_v1_record(now: datetime, carried: Sequence[str],
                      baseline: Dict[str, Any], chosen: Dict[str, Any],
                      apply_flag: bool) -> Dict[str, Any]:
    """Rekord v1 BAJT-W-BAJT jak przed WB1 (kolejność kluczy istotna).

    Odtworzony tu, a nie w `plan_recheck`, żeby oba formaty miały jednego właściciela
    i żeby nigdy nie istniały dwa writery tej samej prawdy.
    """
    return {
        "ts": now.isoformat(),
        "carried": sorted(carried),
        "base_window_viol": baseline["window_viol"],
        "lex_window_viol": chosen["window_viol"],
        "d_drive_min": round(chosen["drive_min"] - baseline["drive_min"], 1),
        "base_max_carry": round(baseline["max_carry_min"], 1),
        "lex_max_carry": round(chosen["max_carry_min"], 1),
        "applied": apply_flag,
        "base_seq": baseline["seq"],
        "lex_seq": chosen["seq"],
    }


def record_decision(
    ctx: Optional[LedgerContext],
    *,
    now: datetime,
    courier_id: Optional[str],
    carried: Sequence[str],
    bag: Dict[str, Any],
    route: Dict[str, Any],
    candidates: Dict[str, Any],
    baseline: Dict[str, Any],
    chosen: Dict[str, Any],
    items: List[Dict[str, Any]],
    thresholds: Dict[str, Any],
    apply_flag: bool,
    shadow_flag: bool,
    decided: bool,
    identity: bool,
    guards: Optional[Dict[str, Any]] = None,
    loadgov: Optional[Dict[str, Any]] = None,
    validator: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Zapisz rekord decyzji warstwy okna odbioru. Zwraca `attempt_id` albo `None`.

    `None` = nic nie zapisano (kill-switch OFF przy identity, budżet wyczerpany,
    błąd zapisu). Wywołujący MUSI ignorować wynik — to ścieżka log-only.
    """
    try:
        if not ledger_v2_enabled():
            # Ścieżka legacy: v1 powstaje TYLKO przy realnym rozjeździe (parytet
            # z zachowaniem sprzed WB1 — brak rekordów identity).
            if not identity:
                _emit(ctx, LEGACY_V1_PATH,
                      _legacy_v1_record(now, carried, baseline, chosen, apply_flag))
            return None

        if not _budget_ok(ctx):
            return None

        _maybe_heartbeat(ctx)
        run_id = ctx.run_id if ctx is not None else "-"
        st = _run_state(run_id)
        t_build = time.perf_counter_ns()

        cid = str(courier_id) if courier_id is not None else (
            ctx.courier_id if ctx is not None else None)
        active_ids = [str(o) for o in (bag.get("active_order_ids") or [])]
        decision_id = _sha16("|".join([
            run_id, str(cid), str(bag.get("signature")), str(route.get("generation")),
        ]))
        attempt_id = uuid.uuid4().hex

        summary = candidates.get("summary") or []
        record = _header(ctx, "decision", decision_id, attempt_id)
        record.update({
            "caller": {
                "role": (ctx.role.value if ctx is not None else Role.OBSERVER.value),
                "source": (ctx.source if ctx is not None else "unknown"),
                "trigger": (ctx.trigger if ctx is not None else "observe"),
                "can_persist_plan": bool(ctx is not None and ctx.role is Role.WRITER),
                "pid": os.getpid(),
            },
            "courier_id": cid,
            "bag": {
                "signature": bag.get("signature"),
                "active_order_ids": active_ids,
                "active_order_signature": _sha16(",".join(sorted(active_ids))),
                "size": bag.get("size"),
                "carried": sorted(str(c) for c in carried),
            },
            "route": {
                "generation": route.get("generation"),
                "sequence_lock": route.get("sequence_lock"),
            },
            "candidates": {
                "pool_size": candidates.get("pool_size"),
                "feasible": candidates.get("feasible"),
                "rejected": candidates.get("rejected") or {},
                "summary": list(summary)[:CANDIDATE_SUMMARY_MAX] or None,
            },
            "baseline": baseline,
            "chosen": chosen,
            "items": items,
            "raw": {
                "W_baseline": baseline.get("window_viol"),
                "W_chosen": chosen.get("window_viol"),
                "drive_baseline": baseline.get("drive_min"),
                "drive_chosen": chosen.get("drive_min"),
                "carry_baseline": baseline.get("max_carry_min"),
                "carry_chosen": chosen.get("max_carry_min"),
            },
            # WB2 wypełnia te pola; brak argumentu = kształt jak w WB1 (same
            # `null`), więc rekord ma ZAWSZE ten sam zestaw kluczy niezależnie
            # od stanu flagi guardów — analiza nigdy nie musi zgadywać, czy pole
            # jest nieobecne, bo guardy były OFF, czy dlatego, że zgubił je writer.
            # Klucze są ZAMROŻONE schematem v2: nadmiarowe są odrzucane tutaj,
            # zamiast po cichu rozszerzać kontrakt (nowe pole ⇒ schemat v3).
            "guards": {g: (guards or {}).get(g)
                       for g in ("G1", "G2", "G3", "G4", "G5")},
            "loadgov": {k: (loadgov or {}).get(k)
                        for k in ("source", "age_s", "fingerprint", "ewma",
                                  "observed_at", "valid_until", "generation")},
            "validator": {"final": (validator or {}).get("final")},
            "write": {"cas_expected": None, "cas_current": None,
                      "outcome": "not_attempted", "receipt": None},
            "served": {"outcome": None, "receipt": None},
            # `decided` = FAKT wyboru sekwencji planu, nie wartość flagi (to była
            # istota defektu v1). Obserwator liczy kandydata, ale niczego nie
            # decyduje — dlatego rola jest częścią definicji faktu i jest ANDowana
            # TUTAJ, w module-właścicielu semantyki, a nie u wywołującego.
            "decision": {
                "decided": bool(decided) and ctx is not None and ctx.role is Role.WRITER,
                "candidate_chosen": bool(decided),
                "identity": bool(identity),
            },
            "flags": {
                "apply": bool(apply_flag),
                "shadow": bool(shadow_flag),
                "ledger_v2": True,
                # Pełny odcisk (~170 flag, ~6 kB) idzie RAZ na przebieg w rekordzie
                # `heartbeat`; tu skrót do joinu. Powtarzanie go w każdym wierszu
                # rozdymało rekord ~30x i zjadało rotację w 2 dni.
                "fingerprint_sha": _sha16(_flag_fingerprint(run_id)),
                "code_fingerprint": _code_fingerprint(),
            },
            "thresholds": thresholds,
            "coverage": {
                "run_seq": st["seq"],
                "dropped": st["dropped"],
                "degraded": bool(st["dropped"] or st["errors"]),
                "build_us": (time.perf_counter_ns() - t_build) // 1000,
                "emit_us_prev": _LAST_EMIT_US,
            },
        })
        st["seq"] += 1
        _emit(ctx, _target_path(ctx), record)
        return attempt_id
    except Exception as exc:  # ostatnia zapora — decyzja silnika przeżywa zawsze
        _log.warning("lex_window_ledger record_decision nieudany: %s: %s",
                     type(exc).__name__, exc)
        return None


def record_write_receipt(
    ctx: Optional[LedgerContext],
    *,
    attempt_id: Optional[str],
    decision_id: Optional[str] = None,
    courier_id: Optional[str] = None,
    outcome: str,
    writer: str,
    cas_expected: Optional[int] = None,
    cas_current: Optional[int] = None,
    plan_version_after: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    """Fakt ZAPISU planu (rozłączny z `decided`). Nigdy nie rzuca.

    `outcome`: `written` | `skipped_cas` | `failed` | `not_attempted`.
    """
    try:
        if not ledger_v2_enabled() or not attempt_id:
            return
        if not _budget_ok(ctx):
            return
        run_id = ctx.run_id if ctx is not None else "-"
        st = _run_state(run_id)
        record = _header(ctx, "write_receipt", decision_id or "", attempt_id)
        record.update({
            "courier_id": str(courier_id) if courier_id is not None else None,
            "write": {
                "outcome": outcome,
                "writer": writer,
                "cas_expected": cas_expected,
                "cas_current": cas_current,
                "plan_version_after": plan_version_after,
                "error": error,
            },
            "coverage": {"run_seq": st["seq"], "dropped": st["dropped"]},
        })
        st["seq"] += 1
        _emit(ctx, _target_path(ctx), record)
    except Exception as exc:
        _log.warning("lex_window_ledger record_write_receipt nieudany: %s: %s",
                     type(exc).__name__, exc)


def record_served_receipt(
    ctx: Optional[LedgerContext],
    *,
    attempt_id: Optional[str],
    decision_id: Optional[str] = None,
    courier_id: Optional[str] = None,
    surface: str,
    outcome: str,
    served_at: Optional[str] = None,
    stop_sequence_hash: Optional[str] = None,
) -> None:
    """Fakt PODANIA sekwencji konsumentowi (API/panel/APK/live_eta).

    Rozłączny z `write_receipt` z rozmysłem: plan w `courier_plans.json` nie jest
    tożsamy z planem, który zobaczył kurier. Emitery po stronie API wpina WB1 faza 2.
    """
    try:
        if not ledger_v2_enabled() or not attempt_id:
            return
        if not _budget_ok(ctx):
            return
        run_id = ctx.run_id if ctx is not None else "-"
        st = _run_state(run_id)
        record = _header(ctx, "served_receipt", decision_id or "", attempt_id)
        record.update({
            "courier_id": str(courier_id) if courier_id is not None else None,
            "served": {
                "surface": surface,
                "outcome": outcome,
                "served_at": served_at or datetime.now(timezone.utc).isoformat(),
                "stop_sequence_hash": stop_sequence_hash,
            },
            "coverage": {"run_seq": st["seq"], "dropped": st["dropped"]},
        })
        st["seq"] += 1
        _emit(ctx, _target_path(ctx), record)
    except Exception as exc:
        _log.warning("lex_window_ledger record_served_receipt nieudany: %s: %s",
                     type(exc).__name__, exc)


# ── uchwyt attempt_id między warstwą decyzji a warstwą zapisu ──
# Reorder liczy się głęboko w `_apply_canon_order_invariants`, a CAS-zapis planu
# następuje o dwie warstwy wyżej. Uchwyt pozwala powiązać fakt `written` z tą samą
# próbą decyzji, bez przeciekania pól ledgera przez sygnatury funkcji silnika.
_PENDING: Dict[str, str] = {}
_PENDING_KEEP = 64


def _pending_key(ctx: Optional[LedgerContext]) -> Optional[str]:
    if ctx is None or ctx.courier_id is None:
        return None
    return f"{ctx.run_id}:{ctx.courier_id}"


def set_pending_attempt(ctx: Optional[LedgerContext], attempt_id: str) -> None:
    key = _pending_key(ctx)
    if key is None:
        return
    _PENDING[key] = attempt_id
    if len(_PENDING) > _PENDING_KEEP:
        for stale in list(_PENDING)[:-_PENDING_KEEP]:
            _PENDING.pop(stale, None)


def pop_pending_attempt(ctx: Optional[LedgerContext]) -> Optional[str]:
    key = _pending_key(ctx)
    return _PENDING.pop(key, None) if key is not None else None


def read_records(path: str) -> List[Dict[str, Any]]:
    """Pomocnik czytający (narzędzia/testy). Wiersze niepoprawne są pomijane."""
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return []
    return out
