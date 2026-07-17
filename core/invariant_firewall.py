"""Finalny, obserwacyjny firewall regul R6/R27/SLA (Z-P0-01, faza A).

Modul jest czystym rdzeniem: nie czyta plikow ani flag, nie loguje i nie mutuje
wejsc. Dostaje gotowy ``FirewallPolicy`` oraz finalny ``PipelineResult`` po
selekcji. Wynik ``RuleVerdict`` jest w fazie A wylacznie telemetria -- nigdy nie
zmienia werdyktu, zwyciezcy, score ani trasy.

R6 i SLA pozostaja osobnymi regulami, bo maja rozne kotwice. R27 emituje dwa
warianty tylko wtedy, gdy polityka przeciazenia jest istotna albo stan loadu jest
nieznany. B-01/B-02 sa jawnie nierozstrzygniete; ten modul nie zgaduje przyszlego
enforcementu.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from dispatch_v2 import sla_anchor as _sla_anchor


WARSAW = ZoneInfo("Europe/Warsaw")

SCHEMA = "rule_verdict.v1"
PHASE = "A_SHADOW"

R6_THERMAL = "R6_THERMAL"
R27_COMMITTED_PICKUP = "R27_COMMITTED_PICKUP"
SLA_DELIVERY = "SLA_DELIVERY"

PASS = "PASS"
VIOLATION = "VIOLATION"
EXEMPT = "EXEMPT"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
NONE = "NONE"


@dataclass(frozen=True)
class FirewallPolicy:
    """Jawny snapshot polityki potrzebnej do obserwacji jednej decyzji."""

    r6_limit_min: float
    sla_limit_min: float
    r27_strict_limit_min: float
    r27_overload_limit_min: float
    overload_threshold: float
    package_address_ids: Tuple[int, ...]
    package_thermal_exempt: bool
    sla_anchor_kind: str  # "now" | "ready"
    always_propose_enabled: bool
    policy_pending: Tuple[str, ...] = ("B-01", "B-02")


@dataclass(frozen=True)
class RuleViolation:
    """Pojedyncze przekroczenie; pierwsze 6 pol to kontrakt Sprintu 1."""

    order_id: str
    rule_id: str
    value: Optional[float]
    limit: Optional[float]
    mode: Tuple[str, ...]
    exception_reason: Optional[str]
    unit: str = "min"
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "rule_id": self.rule_id,
            "value": self.value,
            "limit": self.limit,
            "mode": list(self.mode),
            "exception_reason": self.exception_reason,
            "unit": self.unit,
            "source": self.source,
        }


@dataclass(frozen=True)
class RuleException:
    order_id: str
    rule_id: str
    reason: str
    mode: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "mode": list(self.mode),
        }


@dataclass(frozen=True)
class RuleSummary:
    rule_id: str
    policy_variant: str
    status: str
    limit: Optional[float]
    evaluated_count: int
    violation_count: int
    exempt_count: int
    unknown_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "policy_variant": self.policy_variant,
            "status": self.status,
            "limit": self.limit,
            "evaluated_count": self.evaluated_count,
            "violation_count": self.violation_count,
            "exempt_count": self.exempt_count,
            "unknown_count": self.unknown_count,
        }


@dataclass(frozen=True)
class RuleVerdict:
    schema: str
    phase: str
    status: str
    coverage: str
    enforcement: str
    decision_order_id: str
    decision_verdict: str
    selected_courier_id: Optional[str]
    selection_mode: str
    always_propose_enabled: bool
    policy_pending: Tuple[str, ...]
    rules: Tuple[RuleSummary, ...]
    violations: Tuple[RuleViolation, ...]
    exceptions: Tuple[RuleException, ...]
    missing_reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "phase": self.phase,
            "status": self.status,
            "coverage": self.coverage,
            "enforcement": self.enforcement,
            "decision_order_id": self.decision_order_id,
            "decision_verdict": self.decision_verdict,
            "selected_courier_id": self.selected_courier_id,
            "selection_mode": self.selection_mode,
            "always_propose_enabled": self.always_propose_enabled,
            "policy_pending": list(self.policy_pending),
            "rules": [r.to_dict() for r in self.rules],
            "violations": [v.to_dict() for v in self.violations],
            "exceptions": [e.to_dict() for e in self.exceptions],
            "missing_reasons": list(self.missing_reasons),
        }


@dataclass(frozen=True)
class _OrderContext:
    order_id: str
    known: bool
    is_new: bool = False
    address_id: Any = None
    order_type: Optional[str] = None
    committed_pickup: Any = None
    picked_up_at: Any = None
    pickup_ready_at: Any = None
    status: Optional[str] = None

    @property
    def is_picked(self) -> bool:
        return self.status == "picked_up" or self.picked_up_at is not None

    @property
    def is_czasowka(self) -> bool:
        return str(self.order_type or "").strip().lower() == "czasowka"


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _round_min(value: float) -> float:
    # Trzy miejsca zachowuja rozroznienie 35.001 > 35 bez klamliwego "35.0".
    return round(float(value), 3)


def _panel_datetime(value: Any) -> Optional[datetime]:
    """Timestamp panelowy: naive oznacza Europe/Warsaw, aware -> UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw or raw in {"None", "null", "NULL"}:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WARSAW)
    return dt.astimezone(timezone.utc)


def _plan_datetime(value: Any) -> Optional[datetime]:
    """Timestamp planu: symulator operuje w UTC; naive traktujemy jak UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _selection_mode(result: Any) -> str:
    best = getattr(result, "best", None)
    if best is None:
        return "none"
    if getattr(best, "plan", None) is None:
        return "planless"
    metrics = getattr(best, "metrics", None) or {}
    if bool(getattr(best, "best_effort", False)):
        return "best_effort"
    if metrics.get("solo_fallback") or str(getattr(result, "reason", "")).startswith("solo_fallback"):
        return "solo_fallback"
    return "normal"


def _base_mode(result: Any, policy: FirewallPolicy) -> Tuple[str, ...]:
    tags = ["shadow", f"selection_{_selection_mode(result)}"]
    if policy.always_propose_enabled:
        tags.append("always_propose_enabled")
    return tuple(tags)


def _order_mode(base: Tuple[str, ...], ctx: _OrderContext, *extra: str,
                package: bool = False) -> Tuple[str, ...]:
    tags = list(base)
    tags.extend(extra)
    if package:
        tags.append("order_paczka")
    if ctx.is_czasowka:
        tags.append("order_czasowka")
    return tuple(tags)


def _package(ctx: _OrderContext, policy: FirewallPolicy) -> bool:
    try:
        return int(ctx.address_id) in set(policy.package_address_ids)
    except (TypeError, ValueError):
        return False


def _selected_state(fleet_snapshot: Mapping[str, Any], courier_id: str) -> Any:
    if courier_id in fleet_snapshot:
        return fleet_snapshot[courier_id]
    for key, value in fleet_snapshot.items():
        if str(key) == courier_id:
            return value
    return None


def _context_from_record(record: Any, *, known: bool, is_new: bool = False,
                         fallback_oid: str = "") -> _OrderContext:
    oid = str(_obj_get(record, "order_id") or _obj_get(record, "id") or fallback_oid)
    return _OrderContext(
        order_id=oid,
        known=known,
        is_new=is_new,
        address_id=_obj_get(record, "address_id"),
        order_type=_obj_get(record, "order_type"),
        committed_pickup=_obj_get(record, "czas_kuriera_warsaw"),
        picked_up_at=_obj_get(record, "picked_up_at"),
        pickup_ready_at=(
            _obj_get(record, "pickup_ready_at")
            or _obj_get(record, "pickup_at_warsaw")
        ),
        status=_obj_get(record, "status"),
    )


def _order_contexts(result: Any, order_event: Mapping[str, Any],
                    fleet_snapshot: Mapping[str, Any], order_ids: Iterable[str]) -> Dict[str, _OrderContext]:
    best = getattr(result, "best", None)
    cid = str(getattr(best, "courier_id", "") or "") if best is not None else ""
    state = _selected_state(fleet_snapshot, cid) if cid else None
    out: Dict[str, _OrderContext] = {}
    for raw in (_obj_get(state, "bag", []) or []):
        ctx = _context_from_record(raw, known=True)
        if ctx.order_id:
            out[ctx.order_id] = ctx

    # bag_context niesie dokladnie commit uzyty przez finalnego kandydata; overlay
    # nie gubi address_id/order_type z pelnego snapshotu floty.
    metrics = getattr(best, "metrics", None) or {} if best is not None else {}
    for raw in (metrics.get("bag_context") or []):
        oid = str(_obj_get(raw, "order_id") or "")
        if not oid:
            continue
        old = out.get(oid, _context_from_record(raw, known=False))
        commit = _obj_get(raw, "czas_kuriera_warsaw")
        out[oid] = replace(
            old,
            committed_pickup=(commit if commit is not None else old.committed_pickup),
        )

    new_oid = str(getattr(result, "order_id", "") or order_event.get("order_id") or "")
    new_ctx = _context_from_record(order_event, known=True, is_new=True, fallback_oid=new_oid)
    if getattr(result, "pickup_ready_at", None) is not None:
        new_ctx = replace(new_ctx, pickup_ready_at=getattr(result, "pickup_ready_at"))
    if new_oid:
        out[new_oid] = new_ctx

    for oid in order_ids:
        soid = str(oid)
        out.setdefault(soid, _OrderContext(order_id=soid, known=False, is_new=(soid == new_oid)))
    return out


def _expected_order_ids(result: Any, fleet_snapshot: Mapping[str, Any],
                        plan: Any, *plan_maps: Mapping[str, Any]) -> Tuple[str, ...]:
    """Pełna domena finalnego planu, także gdy pojedyncza mapa jest niepełna."""
    expected = set()

    def _add(value: Any) -> None:
        if value is None:
            return
        text = str(value)
        if text:
            expected.add(text)

    for mapping in plan_maps:
        if isinstance(mapping, Mapping):
            for oid in mapping:
                _add(oid)
    for oid in (getattr(plan, "sequence", None) or []):
        _add(oid)

    best = getattr(result, "best", None)
    cid = str(getattr(best, "courier_id", "") or "") if best is not None else ""
    state = _selected_state(fleet_snapshot, cid) if cid else None
    for record in (_obj_get(state, "bag", []) or []):
        _add(_obj_get(record, "order_id") or _obj_get(record, "id"))
    metrics = (getattr(best, "metrics", None) or {}) if best is not None else {}
    for record in (metrics.get("bag_context") or []):
        _add(_obj_get(record, "order_id") or _obj_get(record, "id"))
    _add(getattr(result, "order_id", ""))
    return tuple(sorted(expected))


def _normalized_anchor_context(ctx: _OrderContext) -> _OrderContext:
    """OrderSim-compatible timestampy dla kanonicznych helperów kotwicy."""
    return replace(
        ctx,
        picked_up_at=_panel_datetime(ctx.picked_up_at),
        pickup_ready_at=_panel_datetime(ctx.pickup_ready_at),
    )


def _summary_status(evaluated: int, violations: int, exempt: int, unknown: int,
                    applicable: bool = True) -> str:
    if not applicable:
        return NOT_APPLICABLE
    if violations:
        return VIOLATION
    if unknown:
        return UNKNOWN
    if evaluated:
        return PASS
    if exempt:
        return EXEMPT
    return NOT_APPLICABLE


def _pre_existing_exception(ctx: _OrderContext, predicted: Optional[datetime],
                            new_pickup: Optional[datetime]) -> Tuple[Optional[str], Optional[str]]:
    if not ctx.is_picked:
        return None, None
    if predicted is None or new_pickup is None:
        return None, f"PRE_EXISTING_CONTEXT_MISSING:{ctx.order_id}"
    if predicted <= new_pickup:
        return "PRE_EXISTING_PICKED_UP_NO_NEW_DETOUR", None
    return None, None


def _empty_rule_summaries(status: str, policy: FirewallPolicy) -> Tuple[RuleSummary, ...]:
    return (
        RuleSummary(R6_THERMAL, "physical_thermal", status, policy.r6_limit_min, 0, 0, 0,
                    1 if status == UNKNOWN else 0),
        RuleSummary(R27_COMMITTED_PICKUP, "strict_5_candidate", status,
                    policy.r27_strict_limit_min, 0, 0, 0, 1 if status == UNKNOWN else 0),
        RuleSummary(SLA_DELIVERY, f"anchor_{policy.sla_anchor_kind}", status,
                    policy.sla_limit_min, 0, 0, 0, 1 if status == UNKNOWN else 0),
    )


def error_verdict(result: Any, policy: FirewallPolicy, error: BaseException) -> RuleVerdict:
    """Jawny UNKNOWN zamiast brakujacego pola, gdy sam przyrzad zawiedzie."""
    best = getattr(result, "best", None)
    return RuleVerdict(
        schema=SCHEMA,
        phase=PHASE,
        status=UNKNOWN,
        coverage=NONE,
        enforcement="NONE",
        decision_order_id=str(getattr(result, "order_id", "") or ""),
        decision_verdict=str(getattr(result, "verdict", "") or ""),
        selected_courier_id=(str(getattr(best, "courier_id", "")) if best is not None else None),
        selection_mode=_selection_mode(result),
        always_propose_enabled=policy.always_propose_enabled,
        policy_pending=policy.policy_pending,
        rules=_empty_rule_summaries(UNKNOWN, policy),
        violations=(),
        exceptions=(),
        missing_reasons=(f"EVALUATOR_ERROR:{type(error).__name__}",),
    )


def evaluate_final(result: Any, order_event: Mapping[str, Any],
                   fleet_snapshot: Mapping[str, Any], decision_now: datetime,
                   policy: FirewallPolicy) -> RuleVerdict:
    """Ocen finalnie wybrany plan. Funkcja jest deterministyczna i bez efektow."""
    if decision_now.tzinfo is None:
        raise ValueError("decision_now must be timezone-aware")
    now_utc = decision_now.astimezone(timezone.utc)
    if policy.sla_anchor_kind not in {"now", "ready"}:
        raise ValueError(f"unsupported sla_anchor_kind={policy.sla_anchor_kind!r}")

    best = getattr(result, "best", None)
    selected_cid = str(getattr(best, "courier_id", "") or "") if best is not None else None
    selection = _selection_mode(result)
    base_mode = _base_mode(result, policy)

    if best is None:
        reason = str(getattr(result, "reason", "") or "unknown").split(" ", 1)[0]
        return RuleVerdict(
            SCHEMA, PHASE, NOT_APPLICABLE, NONE, "NONE",
            str(getattr(result, "order_id", "") or ""),
            str(getattr(result, "verdict", "") or ""), None, selection,
            policy.always_propose_enabled, policy.policy_pending,
            _empty_rule_summaries(NOT_APPLICABLE, policy), (), (),
            (f"NO_SELECTED_PLAN:{reason}",),
        )

    plan = getattr(best, "plan", None)
    if plan is None:
        return RuleVerdict(
            SCHEMA, PHASE, UNKNOWN, NONE, "NONE",
            str(getattr(result, "order_id", "") or ""),
            str(getattr(result, "verdict", "") or ""), selected_cid, selection,
            policy.always_propose_enabled, policy.policy_pending,
            _empty_rule_summaries(UNKNOWN, policy), (), (),
            ("SELECTED_PLAN_MISSING",),
        )

    pod_raw = getattr(plan, "per_order_delivery_times", None)
    predicted_raw = getattr(plan, "predicted_delivered_at", None) or {}
    pickup_raw = getattr(plan, "pickup_at", None) or {}
    pod: Dict[str, Any] = ({str(k): v for k, v in pod_raw.items()}
                           if isinstance(pod_raw, Mapping) else {})
    predicted = {str(k): _plan_datetime(v) for k, v in predicted_raw.items()}
    pickup = {str(k): _plan_datetime(v) for k, v in pickup_raw.items()}
    expected_oids = _expected_order_ids(
        result, fleet_snapshot, plan, pod, predicted, pickup)
    contexts = _order_contexts(result, order_event, fleet_snapshot, expected_oids)
    new_oid = str(getattr(result, "order_id", "") or "")
    new_pickup = pickup.get(new_oid)

    violations: List[RuleViolation] = []
    exceptions: List[RuleException] = []
    rules: List[RuleSummary] = []
    missing: List[str] = []

    # R6 -- fizyczny wiek termiczny finalnego planu.
    r6_eval = r6_viol = r6_exempt = r6_unknown = 0
    for oid in expected_oids:
        ctx = contexts[oid]
        is_package = _package(ctx, policy)
        mode = _order_mode(base_mode, ctx, "anchor_thermal", package=is_package)
        if policy.package_thermal_exempt and not ctx.known:
            r6_unknown += 1
            missing.append(f"R6_ORDER_METADATA_MISSING:{oid}")
            continue
        if policy.package_thermal_exempt and is_package:
            r6_exempt += 1
            exceptions.append(RuleException(oid, R6_THERMAL, "PACZKA_THERMAL_EXEMPT", mode))
            continue
        if oid not in pod:
            r6_unknown += 1
            missing.append(f"R6_PER_ORDER_DATA_MISSING:{oid}")
            continue
        if _finite_number(pod.get(oid)) is None:
            r6_unknown += 1
            missing.append(f"R6_VALUE_MISSING:{oid}")
            continue
        pred = predicted.get(oid)
        if pred is None:
            r6_unknown += 1
            missing.append(f"R6_PREDICTED_DELIVERY_MISSING:{oid}")
            continue
        try:
            anchor_ctx = _normalized_anchor_context(ctx)
            anchor, anchor_source, _is_picked = _sla_anchor.ready_anchor(
                anchor_ctx, ctx.is_new, pickup, now_utc)
            value = _sla_anchor.elapsed_min(pred, anchor)
        except Exception:
            r6_unknown += 1
            missing.append(f"R6_READY_ANCHOR_MISSING:{oid}")
            continue
        r6_eval += 1
        if value > policy.r6_limit_min:
            r6_viol += 1
            exception_reason, missing_reason = _pre_existing_exception(
                ctx, pred, new_pickup)
            if missing_reason:
                missing.append(f"R6_{missing_reason}")
            violations.append(RuleViolation(
                oid, R6_THERMAL, _round_min(value), float(policy.r6_limit_min),
                mode, exception_reason,
                source=f"sla_anchor.ready_anchor+elapsed_min:{anchor_source};pod_present",
            ))
    rules.append(RuleSummary(
        R6_THERMAL, "physical_thermal", _summary_status(r6_eval, r6_viol, r6_exempt, r6_unknown),
        float(policy.r6_limit_min), r6_eval, r6_viol, r6_exempt, r6_unknown,
    ))

    # R27 -- tylko prawdziwy commit. Czasowka/paczka nie sa zwolnione z odbioru.
    committed = [ctx for ctx in contexts.values() if ctx.committed_pickup is not None]
    active_committed = [ctx for ctx in committed if not ctx.is_picked]
    load = _finite_number((getattr(best, "metrics", None) or {}).get("loadgov_load_ewma"))
    variants: List[Tuple[str, float, Tuple[str, ...]]] = []
    if active_committed:
        if load is None:
            variants = [
                ("strict_5_candidate", policy.r27_strict_limit_min, ("r27_strict_candidate", "load_unknown")),
                ("overload_10_candidate", policy.r27_overload_limit_min, ("r27_overload_candidate", "load_unknown")),
            ]
            missing.append("R27_OVERLOAD_STATE_UNKNOWN")
        elif load >= policy.overload_threshold:
            variants = [
                ("strict_5_candidate", policy.r27_strict_limit_min, ("r27_strict_candidate", "load_overload")),
                ("overload_10_candidate", policy.r27_overload_limit_min, ("r27_overload_candidate", "load_overload")),
            ]
        else:
            variants = [
                ("strict_5_candidate", policy.r27_strict_limit_min, ("r27_strict_candidate", "load_normal")),
            ]

    if not active_committed:
        rules.append(RuleSummary(
            R27_COMMITTED_PICKUP, "strict_5_candidate", NOT_APPLICABLE,
            float(policy.r27_strict_limit_min), 0, 0, 0, 0,
        ))
    else:
        for variant, limit, variant_tags in variants:
            evaluated = broken = unknown = 0
            for ctx in active_committed:
                oid = ctx.order_id
                is_package = _package(ctx, policy)
                mode = _order_mode(base_mode, ctx, *variant_tags, package=is_package)
                commit_dt = _panel_datetime(ctx.committed_pickup)
                pickup_dt = pickup.get(oid)
                if commit_dt is None or pickup_dt is None:
                    unknown += 1
                    missing.append(f"R27_TIMESTAMP_MISSING:{oid}:{variant}")
                    continue
                delta = (pickup_dt - commit_dt).total_seconds() / 60.0
                value = abs(delta)
                direction = "pickup_early" if delta < 0 else "pickup_late"
                mode = _order_mode(
                    base_mode, ctx, *variant_tags, direction, package=is_package)
                evaluated += 1
                if value > limit:
                    broken += 1
                    exception_reason = (
                        "B02_OVERLOAD_VARIANT_PENDING"
                        if "load_overload" in variant_tags or "load_unknown" in variant_tags
                        else None
                    )
                    violations.append(RuleViolation(
                        oid, R27_COMMITTED_PICKUP, _round_min(value), float(limit),
                        mode, exception_reason,
                        source="abs(plan.pickup_at-czas_kuriera_warsaw)",
                    ))
            rules.append(RuleSummary(
                R27_COMMITTED_PICKUP, variant,
                _summary_status(evaluated, broken, 0, unknown), float(limit),
                evaluated, broken, 0, unknown,
            ))

    # SLA -- dostawa od jawnej kotwicy NOW albo READY; osobno od R6.
    sla_eval = sla_viol = sla_exempt = sla_unknown = 0
    for oid in expected_oids:
        ctx = contexts[oid]
        is_package = _package(ctx, policy)
        anchor_tag = f"sla_anchor_{policy.sla_anchor_kind}"
        mode = _order_mode(base_mode, ctx, anchor_tag, package=is_package)
        if policy.package_thermal_exempt and not ctx.known:
            sla_unknown += 1
            missing.append(f"SLA_ORDER_METADATA_MISSING:{oid}")
            continue
        if policy.package_thermal_exempt and is_package:
            sla_exempt += 1
            exceptions.append(RuleException(oid, SLA_DELIVERY, "PACZKA_THERMAL_EXEMPT", mode))
            continue
        pred = predicted.get(oid)
        if pred is None:
            sla_unknown += 1
            missing.append(f"SLA_PREDICTED_DELIVERY_MISSING:{oid}")
            continue
        if policy.sla_anchor_kind == "ready" and (
                oid not in pod or _finite_number(pod.get(oid)) is None):
            sla_unknown += 1
            missing.append(f"SLA_PER_ORDER_DATA_MISSING:{oid}")
            continue
        try:
            # INV-TWIN-SLA-ANCHOR: żadnej lokalnej kopii precedencji kotwic.
            # Obie odmiany liczą raw elapsed z predicted, nie z zaokrąglonego POD.
            anchor_ctx = _normalized_anchor_context(ctx)
            if policy.sla_anchor_kind == "ready":
                anchor, anchor_source, _is_picked = _sla_anchor.ready_anchor(
                    anchor_ctx, ctx.is_new, pickup, now_utc)
                source = (
                    f"sla_anchor.ready_anchor+elapsed_min:{anchor_source};pod_present")
            else:
                anchor = _sla_anchor.now_anchor(anchor_ctx, pickup, now_utc)
                source = "sla_anchor.now_anchor+elapsed_min"
            value = _sla_anchor.elapsed_min(pred, anchor)
        except Exception:
            sla_unknown += 1
            missing.append(f"SLA_ANCHOR_VALUE_MISSING:{oid}")
            continue
        sla_eval += 1
        if value > policy.sla_limit_min:
            sla_viol += 1
            exception_reason, missing_reason = _pre_existing_exception(ctx, pred, new_pickup)
            if missing_reason:
                missing.append(f"SLA_{missing_reason}")
            violations.append(RuleViolation(
                oid, SLA_DELIVERY, _round_min(value), float(policy.sla_limit_min),
                mode, exception_reason, source=source,
            ))
    rules.append(RuleSummary(
        SLA_DELIVERY, f"anchor_{policy.sla_anchor_kind}",
        _summary_status(sla_eval, sla_viol, sla_exempt, sla_unknown),
        float(policy.sla_limit_min), sla_eval, sla_viol, sla_exempt, sla_unknown,
    ))

    # Dedup powodow zachowujac kolejnosc -- stabilny JSON/replay.
    missing_unique = tuple(dict.fromkeys(missing))
    unknown_total = sum(r.unknown_count for r in rules)
    known_total = sum(r.evaluated_count + r.exempt_count for r in rules)
    # Brak kontekstu polityki/wyjatku (np. nieznany load dla B-02) takze
    # oznacza PARTIAL, nawet gdy oba warianty arytmetyczne daly sie policzyc.
    if unknown_total or missing_unique:
        coverage = PARTIAL if known_total else NONE
    else:
        coverage = COMPLETE
    if violations:
        status = VIOLATION
    elif unknown_total:
        status = UNKNOWN
    elif known_total:
        status = PASS
    else:
        status = NOT_APPLICABLE

    return RuleVerdict(
        schema=SCHEMA,
        phase=PHASE,
        status=status,
        coverage=coverage,
        enforcement="NONE",
        decision_order_id=str(getattr(result, "order_id", "") or ""),
        decision_verdict=str(getattr(result, "verdict", "") or ""),
        selected_courier_id=selected_cid,
        selection_mode=selection,
        always_propose_enabled=policy.always_propose_enabled,
        policy_pending=policy.policy_pending,
        rules=tuple(rules),
        violations=tuple(violations),
        exceptions=tuple(exceptions),
        missing_reasons=missing_unique,
    )


__all__ = [
    "FirewallPolicy", "RuleException", "RuleSummary", "RuleVerdict", "RuleViolation",
    "evaluate_final", "error_verdict", "SCHEMA", "R6_THERMAL",
    "R27_COMMITTED_PICKUP", "SLA_DELIVERY",
]
