"""Z-P1-03: addytywna telemetria etapow jednej decyzji.

Modul nie zawiera budzetow, limitow ani backpressure. Rozdziela:

* rozlaczne odcinki wall-time glownego watku (mozna je sumowac),
* zagniezdzona prace kandydatow/OSRM/solvera (watki nakladaja sie, wiec jej
  ``work_sum`` NIE jest skladnikiem ``fanout_wall``).

``ContextVar`` zapewnia izolacje zagniezdzen, ale ThreadPoolExecutor nie
propaguje kontekstu automatycznie. Dlatego ``core.candidates`` jawnie wiaze ten
sam ``DecisionTrace`` w kazdym workerze przez :func:`candidate_scope`.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, Iterator, Optional


SCHEMA = "decision_timing.v1"
SIDECAR_SCHEMA = "decision_stage_timing.v1"

_TRACE: ContextVar[Optional["DecisionTrace"]] = ContextVar(
    "ziomek_stage_timing_trace", default=None)
_CANDIDATE: ContextVar[Optional[str]] = ContextVar(
    "ziomek_stage_timing_candidate", default=None)
_OBSERVATION_OVERRIDE: ContextVar[Optional[bool]] = ContextVar(
    "ziomek_stage_timing_observation_override", default=None)

_PIPELINE_PARTS = (
    "prepare_wall_ms",
    "pre_recheck_wall_ms",
    "fanout_setup_wall_ms",
    "fanout_wall_ms",
    "post_pool_wall_ms",
    "selection_wall_ms",
)
_ASSESS_PARTS = (
    "impl_wall_ms",
    "effects_flush_wall_ms",
    "post_hooks_wall_ms",
)
# Tylko bezposrednie dzieci candidate wall. Cache lock/eviction sa z kolei
# zagniezdzone w OSRM i ich ponowne odjecie podwoiloby koszt.
_CANDIDATE_EXCLUSIVE_CHILDREN = frozenset({"pre_recheck", "osrm", "solver"})
_OSRM_DETAIL_WORK = ("osrm_cache_lock_wait", "osrm_cache_eviction")
_PUBLIC_STAGE_KEYS = (
    "assess_wall_ms",
    *_ASSESS_PARTS,
    *_PIPELINE_PARTS,
)


def _ms(ns: int | float) -> float:
    value = round(float(ns) / 1_000_000.0, 3)
    return 0.0 if abs(value) < 0.0005 else value


@dataclass
class _WorkStat:
    calls: int = 0
    total_ns: int = 0
    max_ns: int = 0
    tags: Dict[str, Dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def add(self, elapsed_ns: int, tags: Dict[str, Any]) -> None:
        elapsed_ns = max(0, int(elapsed_ns))
        self.calls += 1
        self.total_ns += elapsed_ns
        self.max_ns = max(self.max_ns, elapsed_ns)
        for key, value in tags.items():
            if value is None:
                continue
            self.tags[str(key)][str(value)] += 1

    def merge(self, other: "_WorkStat") -> None:
        self.calls += other.calls
        self.total_ns += other.total_ns
        self.max_ns = max(self.max_ns, other.max_ns)
        for key, values in other.tags.items():
            for value, count in values.items():
                self.tags[key][value] += count

    def as_dict(self) -> dict:
        out = {
            "calls": self.calls,
            "wall_sum_ms": _ms(self.total_ns),
            "wall_max_ms": _ms(self.max_ns),
        }
        if self.tags:
            out["tags"] = {
                key: dict(sorted(values.items()))
                for key, values in sorted(self.tags.items())
            }
        return out


@dataclass
class _CandidateTiming:
    wall_ns: int = 0
    work: Dict[str, _WorkStat] = field(default_factory=dict)

    def stat(self, kind: str) -> _WorkStat:
        if kind not in self.work:
            self.work[kind] = _WorkStat()
        return self.work[kind]


class DecisionTrace:
    """Thread-safe collector jednej decyzji; nie jest konsumentem biznesowym."""

    def __init__(self, clock_ns: Optional[Callable[[], int]] = None):
        self.clock_ns = clock_ns or time.perf_counter_ns
        self._lock = RLock()
        self._stages_ns: Dict[str, int] = defaultdict(int)
        self._decision_work: Dict[str, _WorkStat] = {}
        self._candidates: Dict[str, _CandidateTiming] = {}

    def now_ns(self) -> int:
        return int(self.clock_ns())

    def record_ns(self, name: str, elapsed_ns: int) -> None:
        with self._lock:
            self._stages_ns[str(name)] += max(0, int(elapsed_ns))

    def record_ms(self, name: str, elapsed_ms: float) -> None:
        self.record_ns(name, int(round(float(elapsed_ms) * 1_000_000.0)))

    def record_since(self, name: str, started_ns: Optional[int]) -> None:
        if started_ns is None:
            return
        self.record_ns(name, self.now_ns() - int(started_ns))

    def _candidate(self, cid: str) -> _CandidateTiming:
        cid = str(cid)
        if cid not in self._candidates:
            self._candidates[cid] = _CandidateTiming()
        return self._candidates[cid]

    def record_candidate_wall(self, cid: str, elapsed_ns: int) -> None:
        with self._lock:
            self._candidate(cid).wall_ns += max(0, int(elapsed_ns))

    def record_work(self, kind: str, elapsed_ns: int,
                    candidate_id: Optional[str] = None, **tags: Any) -> None:
        with self._lock:
            if candidate_id is None:
                stat = self._decision_work.setdefault(str(kind), _WorkStat())
            else:
                stat = self._candidate(str(candidate_id)).stat(str(kind))
            stat.add(elapsed_ns, tags)

    def _candidate_dict_unlocked(self, cid: str) -> dict:
        state = self._candidates.get(str(cid), _CandidateTiming())
        nested_ns = sum(
            stat.total_ns for kind, stat in state.work.items()
            if kind in _CANDIDATE_EXCLUSIVE_CHILDREN)
        out = {
            "wall_ms": _ms(state.wall_ns),
            "exclusive_ms": _ms(state.wall_ns - nested_ns),
            "pre_recheck_ms": _ms(
                state.work.get("pre_recheck", _WorkStat()).total_ns),
            "osrm": state.work.get("osrm", _WorkStat()).as_dict(),
            "solver": state.work.get("solver", _WorkStat()).as_dict(),
        }
        extra = {
            kind: stat.as_dict()
            for kind, stat in sorted(state.work.items())
            if kind not in {"pre_recheck", "osrm", "solver"}
        }
        if extra:
            out["other_work"] = extra
        return out

    def candidate_snapshot(self, cid: str) -> dict:
        with self._lock:
            return self._candidate_dict_unlocked(str(cid))

    def _aggregate_work_unlocked(self, kind: str) -> _WorkStat:
        total = _WorkStat()
        if kind in self._decision_work:
            total.merge(self._decision_work[kind])
        for state in self._candidates.values():
            if kind in state.work:
                total.merge(state.work[kind])
        return total

    def snapshot(self) -> dict:
        with self._lock:
            stage_ns = dict(self._stages_ns)
            out = {"schema": SCHEMA, "clock": "perf_counter_ns"}
            for key in _PUBLIC_STAGE_KEYS:
                out[key] = _ms(stage_ns.get(key, 0))

            pipeline_sum_ns = sum(stage_ns.get(k, 0) for k in _PIPELINE_PARTS)
            assess_sum_ns = sum(stage_ns.get(k, 0) for k in _ASSESS_PARTS)
            out["pipeline_parts_sum_ms"] = _ms(pipeline_sum_ns)
            out["pipeline_unattributed_ms"] = _ms(
                stage_ns.get("impl_wall_ms", 0) - pipeline_sum_ns)
            out["assess_parts_sum_ms"] = _ms(assess_sum_ns)
            out["assess_unattributed_ms"] = _ms(
                stage_ns.get("assess_wall_ms", 0) - assess_sum_ns)

            walls = [state.wall_ns for state in self._candidates.values()]
            candidate_sum_ns = sum(walls)
            candidate_max_ns = max(walls, default=0)
            out["candidate_count"] = len(walls)
            out["candidate_work_sum_ms"] = _ms(candidate_sum_ns)
            out["candidate_work_max_ms"] = _ms(candidate_max_ns)

            osrm = self._aggregate_work_unlocked("osrm")
            solver = self._aggregate_work_unlocked("solver")
            candidate_pre_recheck = self._aggregate_work_unlocked("pre_recheck")
            out["candidate_pre_recheck_calls"] = candidate_pre_recheck.calls
            out["candidate_pre_recheck_work_sum_ms"] = _ms(
                candidate_pre_recheck.total_ns)
            out["candidate_pre_recheck_work_max_ms"] = _ms(
                candidate_pre_recheck.max_ns)
            out["osrm_calls"] = osrm.calls
            out["osrm_work_sum_ms"] = _ms(osrm.total_ns)
            out["osrm_work_max_ms"] = _ms(osrm.max_ns)
            out["solver_calls"] = solver.calls
            out["solver_work_sum_ms"] = _ms(solver.total_ns)
            out["solver_work_max_ms"] = _ms(solver.max_ns)
            if osrm.tags:
                out["osrm_tags"] = osrm.as_dict().get("tags", {})
            for kind in _OSRM_DETAIL_WORK:
                detail = self._aggregate_work_unlocked(kind)
                out[f"{kind}_calls"] = detail.calls
                out[f"{kind}_work_sum_ms"] = _ms(detail.total_ns)
                out[f"{kind}_work_max_ms"] = _ms(detail.max_ns)
                if detail.tags:
                    out[f"{kind}_tags"] = detail.as_dict().get("tags", {})

            fanout_ns = stage_ns.get("fanout_wall_ms", 0)
            out["fanout_parallelism_factor"] = (
                round(candidate_sum_ns / fanout_ns, 3) if fanout_ns > 0 else None)
            return out

    def attach(self, result: Any) -> dict:
        """Dolacz telemetrie dopiero po decyzji, aby selection jej nie czytala."""
        timing = self.snapshot()
        seen: set[int] = set()
        candidates = []
        best = getattr(result, "best", None)
        if best is not None:
            candidates.append(best)
        candidates.extend(list(getattr(result, "candidates", None) or []))
        for candidate in candidates:
            if candidate is None or id(candidate) in seen:
                continue
            seen.add(id(candidate))
            cid = str(getattr(candidate, "courier_id", "") or "")
            metrics = getattr(candidate, "metrics", None)
            if isinstance(metrics, dict):
                metrics["candidate_timing"] = self.candidate_snapshot(cid)
        setattr(result, "stage_timing", timing)
        return timing


def current_trace() -> Optional[DecisionTrace]:
    # Tick moze zamrozic kill-switch na OFF dla calego batcha. Wtedy nawet
    # przypadkowo odziedziczony trace nie moze reaktywowac glebszych szwow
    # OSRM/solver. Workery dostaja trace jawnie przez candidate_scope.
    if _OBSERVATION_OVERRIDE.get() is False:
        return None
    return _TRACE.get()


def current_candidate_id() -> Optional[str]:
    return _CANDIDATE.get()


def observation_override() -> Optional[bool]:
    """Snapshot wlasciciela scope; ``None`` oznacza samodzielny odczyt flagi."""
    return _OBSERVATION_OVERRIDE.get()


@contextmanager
def observation_scope(enabled: bool) -> Iterator[None]:
    """Zamroz stan obserwacji dla calego ticka i wywolan assess w nim."""
    token = _OBSERVATION_OVERRIDE.set(bool(enabled))
    try:
        yield
    finally:
        _OBSERVATION_OVERRIDE.reset(token)


@contextmanager
def bind(trace: Optional[DecisionTrace], candidate_id: Optional[str] = None) -> Iterator[None]:
    token_trace = _TRACE.set(trace)
    token_candidate = _CANDIDATE.set(
        str(candidate_id) if candidate_id is not None else None)
    try:
        yield
    finally:
        _CANDIDATE.reset(token_candidate)
        _TRACE.reset(token_trace)


@contextmanager
def candidate_scope(trace: Optional[DecisionTrace], candidate_id: str) -> Iterator[None]:
    if trace is None:
        yield
        return
    with bind(trace, str(candidate_id)):
        try:
            started = trace.now_ns()
        except Exception:
            yield
            return
        try:
            yield
        finally:
            try:
                trace.record_candidate_wall(
                    str(candidate_id), trace.now_ns() - started)
            except Exception:
                pass


@contextmanager
def span(name: str, trace: Optional[DecisionTrace] = None) -> Iterator[None]:
    target = trace or current_trace()
    if target is None:
        yield
        return
    try:
        started = target.now_ns()
    except Exception:
        yield
        return
    try:
        yield
    finally:
        try:
            target.record_ns(name, target.now_ns() - started)
        except Exception:
            pass


@contextmanager
def work_span(kind: str, trace: Optional[DecisionTrace] = None,
              **tags: Any) -> Iterator[Dict[str, Any]]:
    """Zmierz zagniezdzona prace; zwrocony slownik pozwala dopisac tag wyniku."""
    target = trace or current_trace()
    final_tags = dict(tags)
    if target is None:
        yield final_tags
        return
    try:
        started = target.now_ns()
    except Exception:
        yield final_tags
        return
    try:
        yield final_tags
    finally:
        try:
            target.record_work(
                kind, target.now_ns() - started,
                candidate_id=current_candidate_id(), **final_tags)
        except Exception:
            pass


def record_work(kind: str, elapsed_ms: float, trace: Optional[DecisionTrace] = None,
                **tags: Any) -> None:
    """Szew dla OSRM/solvera; nigdy nie zmienia wyniku wywolania."""
    target = trace or current_trace()
    if target is None:
        return
    try:
        elapsed_ns = int(round(float(elapsed_ms) * 1_000_000.0))
    except (TypeError, ValueError):
        return
    try:
        target.record_work(
            kind, elapsed_ns, candidate_id=current_candidate_id(), **tags)
    except Exception:
        pass


def event_age_ms(created_at: Any, now: Optional[datetime] = None) -> tuple[Optional[float], bool]:
    """Wiek eventu ze wspolnego wall-clock; wartosc ujemna pozostaje jawna."""
    if not created_at:
        return None, False
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = round((current.astimezone(timezone.utc)
                     - parsed.astimezone(timezone.utc)).total_seconds() * 1000.0, 3)
        return age, age < 0
    except (TypeError, ValueError, OverflowError):
        return None, False


def sidecar_path(shadow_log_path: str | os.PathLike) -> Path:
    path = Path(shadow_log_path)
    return path.with_name(f"{path.stem}.stage_timings.jsonl")


def event_ref(event_id: Any) -> str:
    """Stabilny, niejawny klucz joinu; sidecar nie powiela surowego event/order ID."""
    payload = f"{SIDECAR_SCHEMA}\0{event_id}".encode("utf-8", errors="replace")
    return "evt_" + hashlib.sha256(payload).hexdigest()[:24]


def new_tick_ref() -> str:
    """Losowy klucz ticku bez czasu, PID ani identyfikatora biznesowego."""
    return "tick_" + uuid.uuid4().hex


def append_sidecar_rows(path: str | os.PathLike, rows: list[dict], *,
                        append_fn: Optional[Callable[..., int]] = None) -> int:
    """Batch append sidecara; calkowicie fail-soft wobec decyzji i ACK kolejki."""
    if not rows:
        return 0
    path = Path(path)
    # Test, ktory przez pomylke poda sciezke produkcyjna, nie moze jej dotknac.
    if os.environ.get("DISPATCH_UNDER_PYTEST") and str(path).startswith(
            "/root/.openclaw/workspace/"):
        return 0
    try:
        if append_fn is None:
            from dispatch_v2.core.jsonl_appender import append_jsonl_batch
            append_fn = append_jsonl_batch
        # Sidecar zawiera jedynie pseudonimowe referencje, ale pozostaje logiem
        # operacyjnym. Tworzymy go jako 0600 i odrzucamy symlink przed appendem.
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(os.fspath(path), flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        return int(append_fn(path, rows))
    except Exception:
        return 0
