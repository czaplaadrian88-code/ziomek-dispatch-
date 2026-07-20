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
OD07_SCHEMA = "rule_verdict.v3"
PHASE = "A_SHADOW"
OD07_EVALUATION_STAGE = "POST_SELECTION_OD07_PHYSICAL_INTERVAL"
OD07_FLAG = "ENABLE_A360_D1_OD07_FIREWALL_EXEMPT_TRUTH"

R6_NORMAL_LIMIT_MIN = 35.0
R6_ALARM_LIMIT_MIN = 40.0
R6_INTERVAL = "physical_possession_to_customer_handoff"
R6_MODE_NORMAL = "NORMAL"
R6_MODE_ALARM = "ALARM"
R6_MODE_UNBOUND = "UNBOUND"
R6_EVENT_GATE_BOUND = "BOUND"

R6_THERMAL = "R6_THERMAL"
R27_COMMITTED_PICKUP = "R27_COMMITTED_PICKUP"
SLA_DELIVERY = "SLA_DELIVERY"

PASS = "PASS"
VIOLATION = "VIOLATION"
EXEMPT = "EXEMPT"
EXEMPT_POLICY = "EXEMPT_POLICY"
EXEMPT_PREEXISTING = "EXEMPT_PREEXISTING"
VIOLATION_INTRODUCED = "VIOLATION_INTRODUCED"
ALARM = "ALARM"
PROHIBITED = "PROHIBITED"
UNBOUND = "UNBOUND"
HOLD = "HOLD"
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
    od07_firewall_exempt_truth_enabled: bool = False


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
class R6IntervalEvidence:
    """Domenowy interwal OD-07, niezalezny od technicznego zrodla eventow.

    Obiekt wolno zbudowac dopiero adapterowi zatwierdzonego, wersjonowanego
    kontraktu physical-event. Biezace wpiecia firewalla takiego adaptera nie
    maja i przekazuja brak dowodu. Pola panelowe/planowe nie sa tu czytane.

    ``predecision_customer_handoff_at`` jest jawnym kontrfaktykiem potrzebnym
    wyłącznie do przypisania wpływu decyzji; nie jest substytutem eventu końca.
    """

    physical_possession_at: datetime
    customer_handoff_at: datetime
    event_contract_version: str = ""
    physical_possession_source: str = ""
    customer_handoff_source: str = ""
    cohort: str = ""
    event_gate_status: str = UNBOUND
    mode: str = R6_MODE_UNBOUND
    mode_contract_version: str = ""
    predecision_customer_handoff_at: Optional[datetime] = None
    predecision_mode: str = R6_MODE_UNBOUND
    predecision_mode_contract_version: str = ""
    counterfactual_contract_version: str = ""


@dataclass(frozen=True)
class R6ImpactViolation:
    order_id: str
    rule_id: str
    value: float
    limit: float
    mode: Tuple[str, ...]
    exception_reason: Optional[str]
    unit: str
    source: str
    status: str
    physical_status: str
    provenance_stage: str
    impact_reason: str

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
            "status": self.status,
            "physical_status": self.physical_status,
            "provenance_stage": self.provenance_stage,
            "impact_reason": self.impact_reason,
        }


@dataclass(frozen=True)
class R6EvidenceLineage:
    order_id: str
    event_contract_version: str
    physical_possession_source: str
    customer_handoff_source: str
    cohort: str
    event_gate_status: str
    mode_contract_version: str
    counterfactual_contract_version: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "event_contract_version": self.event_contract_version,
            "physical_possession_source": self.physical_possession_source,
            "customer_handoff_source": self.customer_handoff_source,
            "cohort": self.cohort,
            "event_gate_status": self.event_gate_status,
            "mode_contract_version": self.mode_contract_version,
            "counterfactual_contract_version": (
                self.counterfactual_contract_version),
        }


@dataclass(frozen=True)
class R6Od07Summary:
    rule_id: str
    policy_variant: str
    status: str
    limit: float
    evaluated_count: int
    violation_count: int
    exempt_count: int
    unknown_count: int
    physical_status: str
    interval: str
    normal_limit_min: float
    alarm_limit_min: float
    alarm_count: int
    prohibited_count: int
    introduced_order_count: int
    preexisting_order_count: int
    causality_unbound_order_count: int
    evidence_lineage: Tuple[R6EvidenceLineage, ...] = ()
    food_ready_age_status: str = "SEPARATE_UNBOUND"
    food_ready_age_threshold_min: Optional[float] = None
    count_unit: str = "orders"

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
            "physical_status": self.physical_status,
            "interval": self.interval,
            "normal_limit_min": self.normal_limit_min,
            "alarm_limit_min": self.alarm_limit_min,
            "alarm_count": self.alarm_count,
            "prohibited_count": self.prohibited_count,
            "introduced_order_count": self.introduced_order_count,
            "preexisting_order_count": self.preexisting_order_count,
            "causality_unbound_order_count": self.causality_unbound_order_count,
            "evidence_lineage": [row.to_dict() for row in self.evidence_lineage],
            "food_ready_age_status": self.food_ready_age_status,
            "food_ready_age_threshold_min": self.food_ready_age_threshold_min,
            "count_unit": self.count_unit,
        }


@dataclass(frozen=True)
class RuleVerdictOd07:
    """Wersjonowany werdykt D1; status wpływu nie ukrywa stanu fizycznego."""

    schema: str
    phase: str
    evaluation_stage: str
    status: str
    physical_status: str
    coverage: str
    enforcement: str
    decision_order_id: str
    decision_verdict: str
    selected_courier_id: Optional[str]
    selection_mode: str
    always_propose_enabled: bool
    policy_pending: Tuple[str, ...]
    rules: Tuple[Any, ...]
    violations: Tuple[Any, ...]
    exceptions: Tuple[Any, ...]
    missing_reasons: Tuple[str, ...]
    introduced_order_count: int
    preexisting_order_count: int
    causality_unbound_order_count: int
    count_unit: str
    r6_event_binding: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "phase": self.phase,
            "evaluation_stage": self.evaluation_stage,
            "status": self.status,
            "physical_status": self.physical_status,
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
            "introduced_order_count": self.introduced_order_count,
            "preexisting_order_count": self.preexisting_order_count,
            "causality_unbound_order_count": self.causality_unbound_order_count,
            "count_unit": self.count_unit,
            "r6_event_binding": self.r6_event_binding,
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


def error_verdict(result: Any, policy: FirewallPolicy, error: BaseException) -> Any:
    """Jawny UNKNOWN zamiast brakujacego pola, gdy sam przyrzad zawiedzie."""
    if policy.od07_firewall_exempt_truth_enabled:
        return _od07_error_verdict(result, policy, error)
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


def _evaluate_final_v1(result: Any, order_event: Mapping[str, Any],
                       fleet_snapshot: Mapping[str, Any], decision_now: datetime,
                       policy: FirewallPolicy, *, evaluate_r6: bool = True) -> RuleVerdict:
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

    # R6 v1 jest wykonywany tylko na ścieżce flagi OFF. OD-07 ON pomija ten
    # blok w całości: nie wolno nawet pomocniczo policzyć ready/picked proxy.
    if evaluate_r6:
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
                exceptions.append(RuleException(
                    oid, R6_THERMAL, "PACZKA_THERMAL_EXEMPT", mode))
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
                    oid, R6_THERMAL, _round_min(value),
                    float(policy.r6_limit_min), mode, exception_reason,
                    source=(
                        "sla_anchor.ready_anchor+elapsed_min:"
                        f"{anchor_source};pod_present"),
                ))
        rules.append(RuleSummary(
            R6_THERMAL, "physical_thermal",
            _summary_status(r6_eval, r6_viol, r6_exempt, r6_unknown),
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


def _od07_datetime(value: Any, field: str) -> datetime:
    """Strict domain boundary: no string/status/click timestamp coercion."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field}_MUST_BE_AWARE_DATETIME")
    return value.astimezone(timezone.utc)


_OD07_FORBIDDEN_SOURCE_FRAGMENTS = (
    "food_ready",
    "restaurant_exit",
    "last_inside",
    "click",
    "delivery_arrival",
    "picked_up_at",
    "delivered_at",
)


def _od07_event_evidence_bound(evidence: R6IntervalEvidence) -> bool:
    required = (
        evidence.event_contract_version,
        evidence.physical_possession_source,
        evidence.customer_handoff_source,
        evidence.cohort,
    )
    if evidence.event_gate_status != R6_EVENT_GATE_BOUND:
        return False
    if not all(isinstance(value, str) and value.strip() for value in required):
        return False
    sources = (
        evidence.physical_possession_source.lower(),
        evidence.customer_handoff_source.lower(),
    )
    return not any(
        fragment in source
        for source in sources
        for fragment in _OD07_FORBIDDEN_SOURCE_FRAGMENTS
    )


def _od07_mode_with_gate(age_min: float, mode: str, version: str) -> str:
    if R6_NORMAL_LIMIT_MIN < age_min <= R6_ALARM_LIMIT_MIN:
        if not isinstance(version, str) or not version.strip():
            return R6_MODE_UNBOUND
    return mode


def _od07_interval_age_min(
        possession_at: Any, handoff_at: Any, *, handoff_field: str) -> float:
    possession = _od07_datetime(possession_at, "PHYSICAL_POSSESSION")
    handoff = _od07_datetime(handoff_at, handoff_field)
    age = (handoff - possession).total_seconds() / 60.0
    if not math.isfinite(age) or age < 0:
        raise ValueError("R6_PHYSICAL_INTERVAL_INVALID")
    return age


def _od07_physical_status(age_min: float, mode: str) -> str:
    """OD-07 boundaries; 40 is Alarm-only and never a courier-class dial."""
    if mode not in {R6_MODE_NORMAL, R6_MODE_ALARM, R6_MODE_UNBOUND}:
        raise ValueError("R6_MODE_INVALID")
    if age_min > R6_ALARM_LIMIT_MIN:
        return PROHIBITED
    if age_min <= R6_NORMAL_LIMIT_MIN:
        return PASS
    if mode == R6_MODE_ALARM:
        return ALARM
    if mode == R6_MODE_NORMAL:
        return VIOLATION
    return HOLD


def _od07_impact(
        evidence: R6IntervalEvidence, age_min: float, physical_status: str,
        *, is_new: bool,
) -> Tuple[str, str, str, Optional[str]]:
    """Oddziel fizyczny stan R6 od wpływu bieżącej decyzji.

    EXEMPT wymaga jawnego kontrfaktyku na tym samym physical-possession.
    Brak kontrfaktyku lub niezwiazany Alarm to HOLD, nigdy domysł z planu.
    """
    if physical_status == PASS:
        return PASS, "BOUND_PHYSICAL_INTERVAL", "R6_WITHIN_NORMAL_LIMIT", None
    if physical_status == ALARM:
        return ALARM, "BOUND_AUTOMATIC_ALARM", "R6_AUTOMATIC_ALARM_WINDOW", None
    if physical_status == HOLD:
        return (
            HOLD,
            "ALARM_PREDICATE_UNBOUND",
            "R6_ALARM_PREDICATE_UNBOUND",
            "R6_ALARM_PREDICATE_UNBOUND",
        )
    if is_new:
        return (
            VIOLATION_INTRODUCED,
            "CURRENT_DECISION_NEW_ORDER",
            "R6_NEW_ORDER_BREACH_INTRODUCED",
            None,
        )

    baseline_handoff = evidence.predecision_customer_handoff_at
    if baseline_handoff is None:
        return (
            HOLD,
            "CAUSALITY_UNBOUND",
            "R6_PREDECISION_COUNTERFACTUAL_UNBOUND",
            "R6_PREDECISION_COUNTERFACTUAL_UNBOUND",
        )
    if (not isinstance(evidence.counterfactual_contract_version, str)
            or not evidence.counterfactual_contract_version.strip()):
        return (
            HOLD,
            "CAUSALITY_UNBOUND",
            "R6_PREDECISION_COUNTERFACTUAL_PROVENANCE_UNBOUND",
            "R6_PREDECISION_COUNTERFACTUAL_PROVENANCE_UNBOUND",
        )
    baseline_age = _od07_interval_age_min(
        evidence.physical_possession_at,
        baseline_handoff,
        handoff_field="PREDECISION_CUSTOMER_HANDOFF",
    )
    baseline_mode = _od07_mode_with_gate(
        baseline_age,
        evidence.predecision_mode,
        evidence.predecision_mode_contract_version,
    )
    baseline_status = _od07_physical_status(baseline_age, baseline_mode)
    if baseline_status == HOLD:
        return (
            HOLD,
            "CAUSALITY_UNBOUND",
            "R6_PREDECISION_ALARM_PREDICATE_UNBOUND",
            "R6_PREDECISION_ALARM_PREDICATE_UNBOUND",
        )
    if baseline_status in {VIOLATION, PROHIBITED} and age_min <= baseline_age:
        return (
            EXEMPT_PREEXISTING,
            "PRE_DECISION_BOUND_COUNTERFACTUAL",
            "R6_BREACH_PREEXISTING_NOT_WORSENED",
            None,
        )
    return (
        VIOLATION_INTRODUCED,
        "CURRENT_DECISION_BOUND_COUNTERFACTUAL",
        "R6_BREACH_INTRODUCED_OR_WORSENED",
        None,
    )


def _od07_empty_summary(status: str, physical_status: str) -> R6Od07Summary:
    return R6Od07Summary(
        rule_id=R6_THERMAL,
        policy_variant="in_vehicle_age_od07",
        status=status,
        limit=R6_NORMAL_LIMIT_MIN,
        evaluated_count=0,
        violation_count=0,
        exempt_count=0,
        unknown_count=1 if status in {HOLD, UNKNOWN} else 0,
        physical_status=physical_status,
        interval=R6_INTERVAL,
        normal_limit_min=R6_NORMAL_LIMIT_MIN,
        alarm_limit_min=R6_ALARM_LIMIT_MIN,
        alarm_count=0,
        prohibited_count=0,
        introduced_order_count=0,
        preexisting_order_count=0,
        causality_unbound_order_count=1 if status == HOLD else 0,
    )


def _od07_policy_pending() -> Tuple[str, ...]:
    return (
        "R6_PHYSICAL_POSSESSION_EVENT_SOURCE",
        "R6_CUSTOMER_HANDOFF_EVENT_SOURCE",
        "R6_AUTOMATIC_ALARM_PREDICATE",
        "R6_PREDECISION_COUNTERFACTUAL",
    )


def _od07_error_verdict(
        result: Any, policy: FirewallPolicy, error: BaseException) -> RuleVerdictOd07:
    best = getattr(result, "best", None)
    legacy_rules = _empty_rule_summaries(UNKNOWN, policy)[1:]
    return RuleVerdictOd07(
        schema=OD07_SCHEMA,
        phase=PHASE,
        evaluation_stage=OD07_EVALUATION_STAGE,
        status=HOLD,
        physical_status=UNBOUND,
        coverage=NONE,
        enforcement="NONE",
        decision_order_id=str(getattr(result, "order_id", "") or ""),
        decision_verdict=str(getattr(result, "verdict", "") or ""),
        selected_courier_id=(
            str(getattr(best, "courier_id", "") or "") if best is not None else None),
        selection_mode=_selection_mode(result),
        always_propose_enabled=policy.always_propose_enabled,
        policy_pending=_od07_policy_pending(),
        rules=(_od07_empty_summary(HOLD, UNBOUND),) + legacy_rules,
        violations=(),
        exceptions=(),
        missing_reasons=(f"EVALUATOR_ERROR:{type(error).__name__}",),
        introduced_order_count=0,
        preexisting_order_count=0,
        causality_unbound_order_count=1,
        count_unit="orders",
        r6_event_binding=UNBOUND,
    )


def _evaluate_final_od07(
        result: Any,
        order_event: Mapping[str, Any],
        fleet_snapshot: Mapping[str, Any],
        decision_now: datetime,
        policy: FirewallPolicy,
        r6_intervals: Optional[Mapping[str, R6IntervalEvidence]],
) -> RuleVerdictOd07:
    """OD-07 shadow evaluator. It never derives physical events from payloads."""
    legacy = _evaluate_final_v1(
        result, order_event, fleet_snapshot, decision_now, policy,
        evaluate_r6=False)
    other_rules = tuple(r for r in legacy.rules if r.rule_id != R6_THERMAL)
    other_violations = tuple(
        v for v in legacy.violations if v.rule_id != R6_THERMAL)
    other_exceptions = tuple(
        e for e in legacy.exceptions if e.rule_id != R6_THERMAL)
    other_missing = [
        reason for reason in legacy.missing_reasons
        if not str(reason).startswith("R6_")
    ]

    best = getattr(result, "best", None)
    plan = getattr(best, "plan", None) if best is not None else None
    if best is None:
        r6_summary = _od07_empty_summary(NOT_APPLICABLE, NOT_APPLICABLE)
        return RuleVerdictOd07(
            OD07_SCHEMA, PHASE, OD07_EVALUATION_STAGE,
            legacy.status, NOT_APPLICABLE, legacy.coverage, "NONE",
            legacy.decision_order_id, legacy.decision_verdict,
            legacy.selected_courier_id, legacy.selection_mode,
            legacy.always_propose_enabled, _od07_policy_pending(),
            (r6_summary,) + other_rules, other_violations, other_exceptions,
            tuple(other_missing), 0, 0, 0, "orders", NOT_APPLICABLE,
        )
    if plan is None:
        r6_summary = _od07_empty_summary(HOLD, UNBOUND)
        return RuleVerdictOd07(
            OD07_SCHEMA, PHASE, OD07_EVALUATION_STAGE,
            HOLD, UNBOUND, NONE, "NONE",
            legacy.decision_order_id, legacy.decision_verdict,
            legacy.selected_courier_id, legacy.selection_mode,
            legacy.always_propose_enabled, _od07_policy_pending(),
            (r6_summary,) + other_rules, other_violations, other_exceptions,
            tuple(dict.fromkeys(other_missing + ["R6_SELECTED_PLAN_MISSING"])),
            0, 0, 1, "orders", UNBOUND,
        )

    pod_raw = getattr(plan, "per_order_delivery_times", None)
    predicted_raw = getattr(plan, "predicted_delivered_at", None) or {}
    pickup_raw = getattr(plan, "pickup_at", None) or {}
    pod = ({str(k): v for k, v in pod_raw.items()}
           if isinstance(pod_raw, Mapping) else {})
    predicted = ({str(k): v for k, v in predicted_raw.items()}
                 if isinstance(predicted_raw, Mapping) else {})
    pickup = ({str(k): v for k, v in pickup_raw.items()}
              if isinstance(pickup_raw, Mapping) else {})
    expected_oids = _expected_order_ids(
        result, fleet_snapshot, plan, pod, predicted, pickup)
    contexts = _order_contexts(
        result, order_event, fleet_snapshot, expected_oids)
    interval_map = ({str(k): v for k, v in r6_intervals.items()}
                    if isinstance(r6_intervals, Mapping) else {})
    base_mode = _base_mode(result, policy)

    evaluated = physical_breaches = policy_exempt = event_unbound = 0
    alarm_count = prohibited_count = introduced = preexisting = 0
    causality_unbound = normal_violation_count = physical_hold_count = 0
    r6_violations: List[R6ImpactViolation] = []
    r6_exceptions: List[RuleException] = []
    r6_lineage: List[R6EvidenceLineage] = []
    r6_missing: List[str] = []

    for oid in expected_oids:
        ctx = contexts[oid]
        is_package = _package(ctx, policy)
        mode_tags = _order_mode(
            base_mode, ctx, "interval_possession_handoff", package=is_package)
        if policy.package_thermal_exempt and not ctx.known:
            event_unbound += 1
            r6_missing.append(f"R6_ORDER_METADATA_UNBOUND:{oid}")
            continue
        if policy.package_thermal_exempt and is_package:
            policy_exempt += 1
            r6_exceptions.append(RuleException(
                oid, R6_THERMAL, "PACZKA_THERMAL_EXEMPT", mode_tags))
            continue

        evidence = interval_map.get(oid)
        if not isinstance(evidence, R6IntervalEvidence):
            event_unbound += 1
            r6_missing.extend((
                f"R6_PHYSICAL_POSSESSION_EVENT_UNBOUND:{oid}",
                f"R6_CUSTOMER_HANDOFF_EVENT_UNBOUND:{oid}",
            ))
            continue
        if not _od07_event_evidence_bound(evidence):
            event_unbound += 1
            r6_missing.append(f"R6_PHYSICAL_EVENT_PROVENANCE_UNBOUND:{oid}")
            continue
        try:
            age = _od07_interval_age_min(
                evidence.physical_possession_at,
                evidence.customer_handoff_at,
                handoff_field="CUSTOMER_HANDOFF",
            )
            effective_mode = _od07_mode_with_gate(
                age, evidence.mode, evidence.mode_contract_version)
            physical = _od07_physical_status(age, effective_mode)
            impact, stage, impact_reason, missing_reason = _od07_impact(
                evidence, age, physical, is_new=ctx.is_new)
        except (TypeError, ValueError) as exc:
            event_unbound += 1
            r6_missing.append(
                f"R6_PHYSICAL_INTERVAL_UNBOUND:{oid}:{type(exc).__name__}")
            continue

        evaluated += 1
        r6_lineage.append(R6EvidenceLineage(
            order_id=oid,
            event_contract_version=evidence.event_contract_version,
            physical_possession_source=evidence.physical_possession_source,
            customer_handoff_source=evidence.customer_handoff_source,
            cohort=evidence.cohort,
            event_gate_status=evidence.event_gate_status,
            mode_contract_version=evidence.mode_contract_version,
            counterfactual_contract_version=(
                evidence.counterfactual_contract_version),
        ))
        if missing_reason:
            causality_unbound += 1
            r6_missing.append(f"{missing_reason}:{oid}")
        if impact == VIOLATION_INTRODUCED:
            introduced += 1
        elif impact == EXEMPT_PREEXISTING:
            preexisting += 1
        if physical == ALARM:
            alarm_count += 1
        elif physical == PROHIBITED:
            prohibited_count += 1
        elif physical == VIOLATION:
            normal_violation_count += 1
        elif physical == HOLD:
            physical_hold_count += 1

        if age > R6_NORMAL_LIMIT_MIN:
            physical_breaches += 1
            limit = (
                R6_ALARM_LIMIT_MIN
                if physical in {ALARM, PROHIBITED} else R6_NORMAL_LIMIT_MIN)
            mode_tags = mode_tags + (f"r6_mode_{effective_mode.lower()}",)
            exception_reason = (
                "R6_BREACH_PREEXISTING_NOT_WORSENED"
                if impact == EXEMPT_PREEXISTING else
                "AUTOMATIC_ALARM_WINDOW" if physical == ALARM else None
            )
            r6_violations.append(R6ImpactViolation(
                order_id=oid,
                rule_id=R6_THERMAL,
                value=_round_min(age),
                limit=limit,
                mode=mode_tags,
                exception_reason=exception_reason,
                unit="min",
                source=(
                    "R6IntervalEvidence.physical_possession_at"
                    "->customer_handoff_at"),
                status=impact,
                physical_status=physical,
                provenance_stage=stage,
                impact_reason=impact_reason,
            ))

    unknown_count = event_unbound + causality_unbound
    if introduced:
        r6_status = VIOLATION_INTRODUCED
    elif causality_unbound or event_unbound:
        r6_status = HOLD
    elif preexisting:
        r6_status = EXEMPT_PREEXISTING
    elif alarm_count:
        r6_status = ALARM
    elif evaluated:
        r6_status = PASS
    elif policy_exempt:
        r6_status = EXEMPT_POLICY
    else:
        r6_status = NOT_APPLICABLE

    if prohibited_count:
        r6_physical_status = PROHIBITED
    elif normal_violation_count:
        r6_physical_status = VIOLATION
    elif alarm_count:
        r6_physical_status = ALARM
    elif physical_hold_count:
        r6_physical_status = HOLD
    elif event_unbound:
        r6_physical_status = UNBOUND
    elif evaluated:
        r6_physical_status = PASS
    elif policy_exempt:
        r6_physical_status = EXEMPT
    else:
        r6_physical_status = NOT_APPLICABLE

    r6_summary = R6Od07Summary(
        rule_id=R6_THERMAL,
        policy_variant="in_vehicle_age_od07",
        status=r6_status,
        limit=R6_NORMAL_LIMIT_MIN,
        evaluated_count=evaluated,
        violation_count=physical_breaches,
        exempt_count=policy_exempt,
        unknown_count=unknown_count,
        physical_status=r6_physical_status,
        interval=R6_INTERVAL,
        normal_limit_min=R6_NORMAL_LIMIT_MIN,
        alarm_limit_min=R6_ALARM_LIMIT_MIN,
        alarm_count=alarm_count,
        prohibited_count=prohibited_count,
        introduced_order_count=introduced,
        preexisting_order_count=preexisting,
        causality_unbound_order_count=causality_unbound,
        evidence_lineage=tuple(r6_lineage),
    )
    missing = tuple(dict.fromkeys(other_missing + r6_missing))
    other_unknown = sum(r.unknown_count for r in other_rules)
    other_known = sum(r.evaluated_count + r.exempt_count for r in other_rules)
    known_total = evaluated + policy_exempt + other_known
    if unknown_count or other_unknown or missing:
        coverage = PARTIAL if known_total else NONE
    else:
        coverage = COMPLETE

    if introduced:
        status = VIOLATION_INTRODUCED
    elif other_violations:
        status = VIOLATION
    elif causality_unbound or event_unbound:
        status = HOLD
    elif preexisting:
        status = EXEMPT_PREEXISTING
    elif other_unknown:
        status = UNKNOWN
    elif alarm_count:
        status = ALARM
    elif known_total:
        status = PASS
    else:
        status = NOT_APPLICABLE

    return RuleVerdictOd07(
        schema=OD07_SCHEMA,
        phase=PHASE,
        evaluation_stage=OD07_EVALUATION_STAGE,
        status=status,
        physical_status=r6_physical_status,
        coverage=coverage,
        enforcement="NONE",
        decision_order_id=legacy.decision_order_id,
        decision_verdict=legacy.decision_verdict,
        selected_courier_id=legacy.selected_courier_id,
        selection_mode=legacy.selection_mode,
        always_propose_enabled=legacy.always_propose_enabled,
        policy_pending=_od07_policy_pending(),
        rules=(r6_summary,) + other_rules,
        violations=tuple(r6_violations) + other_violations,
        exceptions=tuple(r6_exceptions) + other_exceptions,
        missing_reasons=missing,
        introduced_order_count=introduced,
        preexisting_order_count=preexisting,
        causality_unbound_order_count=causality_unbound,
        count_unit="orders",
        r6_event_binding=(
            UNBOUND if event_unbound else
            R6_EVENT_GATE_BOUND if evaluated else NOT_APPLICABLE),
    )


def evaluate_final(
        result: Any,
        order_event: Mapping[str, Any],
        fleet_snapshot: Mapping[str, Any],
        decision_now: datetime,
        policy: FirewallPolicy,
        *,
        r6_intervals: Optional[Mapping[str, R6IntervalEvidence]] = None,
) -> Any:
    """Public seam: OFF is exactly v1; ON selects the OD-07 truth contract."""
    if not policy.od07_firewall_exempt_truth_enabled:
        return _evaluate_final_v1(
            result, order_event, fleet_snapshot, decision_now, policy)
    return _evaluate_final_od07(
        result, order_event, fleet_snapshot, decision_now, policy, r6_intervals)


__all__ = [
    "FirewallPolicy", "R6IntervalEvidence", "R6ImpactViolation",
    "R6EvidenceLineage", "R6Od07Summary", "RuleException", "RuleSummary", "RuleVerdict",
    "RuleVerdictOd07", "RuleViolation", "evaluate_final", "error_verdict",
    "SCHEMA", "OD07_SCHEMA", "OD07_FLAG", "R6_THERMAL",
    "R27_COMMITTED_PICKUP", "SLA_DELIVERY", "R6_INTERVAL",
    "R6_NORMAL_LIMIT_MIN", "R6_ALARM_LIMIT_MIN", "R6_MODE_NORMAL",
    "R6_MODE_ALARM", "R6_MODE_UNBOUND", "R6_EVENT_GATE_BOUND",
    "EXEMPT_POLICY",
    "EXEMPT_PREEXISTING", "VIOLATION_INTRODUCED", "ALARM",
    "PROHIBITED", "UNBOUND", "HOLD",
]
