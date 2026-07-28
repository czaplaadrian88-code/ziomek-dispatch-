#!/usr/bin/env python3
"""End-to-end route-order parity monitor (backend DTO -> Kotlin projection).

The monitor deliberately does not compare two Python renderers.  For every
qualifying live courier bag it compares:

    one orders/plans snapshot -> route_order canon
    -> real courier-api ``build_view_from_snapshots`` DTO
    -> a faithful legacy-field projection of Kotlin ``RouteLogic.buildSteps``.

WB3 v3 will add stop identity fields later.  Until then this monitor projects
only the legacy fields and reuses ``tests/golden/route_order_corpus.json``;
it is not a second cross-language golden.

Verdicts and exits:
  0 OK                  qualifying bags were checked and all matched
  3 EXPECTED_NO_DATA    no qualifying active courier bags (never reported OK)
  1 BROKEN              parity/configuration failure
  2 BROKEN              infrastructure/coverage/output failure
  4 CONFIG_DRIFT        courier-api process flags differ from the corpus

The command is read-only unless ``--result-path`` is supplied.  The future
systemd unit must supply that path; writes are atomic and mode 0600.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_BACKEND = Path("/root/.openclaw/workspace/nadajesz_clone/panel/backend")
_SCRIPTS = Path("/root/.openclaw/workspace/scripts")
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_SCRIPTS))

STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
ORDERS_PATH = STATE_DIR / "orders_state.json"

# Monitor jest READ-ONLY wobec żywego stanu — a ścieżka budowy DTO
# (courier_api.delivery_town) przy cache-miss ZAPISUJE wspólny cache miast.
# Przekierowanie na seedowaną temp-kopię: odczyty identyczne, zapisy nie
# dotykają produkcji (HERMETIC-GUARD łapał to pod pytestem jako naruszenie).
if "DELIVERY_TOWN_CACHE" not in os.environ:
    _live_town_cache = STATE_DIR / "delivery_town_cache.json"
    _tmp_town_cache = Path(tempfile.gettempdir()) / (
        f"parity_town_cache_{os.getpid()}.json")
    try:
        if _live_town_cache.exists():
            _tmp_town_cache.write_bytes(_live_town_cache.read_bytes())
    except OSError:
        pass
    os.environ["DELIVERY_TOWN_CACHE"] = str(_tmp_town_cache)
PLANS_PATH = STATE_DIR / "courier_plans.json"
CORPUS_PATH = (
    _SCRIPTS / "dispatch_v2" / "tests" / "golden" / "route_order_corpus.json"
)

EXIT_OK = 0
EXIT_PARITY_BROKEN = 1
EXIT_INFRA_BROKEN = 2
EXIT_EXPECTED_NO_DATA = 3
EXIT_CONFIG_DRIFT = 4
PICKUP_MERGE_MIN = 10  # Kotlin RouteLogic.kt contract, pinned by golden tests.
GATE_ID = "obs.route-parity-telemetry"
GATE_DUE = "2026-08-01"
COURIER_API_SERVICE = "courier-api.service"
ROUTE_CONFIG_ENV = {
    "app_route_from_console": "ENABLE_APP_ROUTE_FROM_CONSOLE",
    "route_order_unified": "ENABLE_ROUTE_ORDER_UNIFIED",
    "plan_aware_podjazdy": "ENABLE_PLAN_AWARE_PODJAZDY",
    "build_view_trust_canon_order": "ENABLE_BUILD_VIEW_TRUST_CANON_ORDER",
}


class ProcessConfigError(RuntimeError):
    """The effective courier-api process configuration cannot be attested."""


def _load_gen():
    """Reuse the existing corpus/live-bag adapter and effective flag readers."""
    sys.path.insert(0, str(_SCRIPTS / "dispatch_v2" / "tools"))
    import route_order_golden_corpus_gen as gen  # noqa: PLC0415

    return gen


def _load_backend_module() -> Any:
    """Load the module that serves ``/api/courier/orders``.

    Import failure is infrastructure BROKEN.  There is intentionally no local
    DTO reconstruction fallback: that would certify a second implementation
    instead of the payload consumed by the app.
    """
    # courier_orders has a legacy best-effort alert in its import-error branch.
    # Prove these imports first so a broken monitor cannot enter that branch,
    # read alert credentials, or attempt a network send.
    from dispatch_v2 import live_eta as _live_eta  # noqa: F401,PLC0415
    from dispatch_v2 import route_podjazdy as _route_podjazdy  # noqa: F401,PLC0415

    try:
        from courier_api import courier_orders  # type: ignore  # noqa: PLC0415
    except ImportError:
        sys.path.insert(0, str(_SCRIPTS / "courier_api"))
        import courier_orders  # type: ignore  # noqa: PLC0415

    return courier_orders


def _backend_contract(
    courier_orders: Any,
) -> tuple[Callable[..., Mapping[str, Any]], type[Any]]:
    """Require the snapshot/config-explicit courier-api boundary.

    Falling back to ``build_view`` would reintroduce both defects: that wrapper
    reads live files again and its module-global config reflects this monitor's
    environment rather than the running courier-api process.
    """
    builder = getattr(courier_orders, "build_view_from_snapshots", None)
    if not callable(builder):
        raise RuntimeError(
            "courier_orders.build_view_from_snapshots is unavailable"
        )
    route_config_type = getattr(courier_orders, "RouteConfig", None)
    if not isinstance(route_config_type, type):
        raise RuntimeError("courier_orders.RouteConfig is unavailable")
    if not callable(getattr(route_config_type, "from_environ", None)):
        raise RuntimeError("courier_orders.RouteConfig.from_environ is unavailable")
    if not callable(getattr(route_config_type, "as_dict", None)):
        raise RuntimeError("courier_orders.RouteConfig.as_dict is unavailable")
    return builder, route_config_type


def _load_backend_contract() -> tuple[Callable[..., Mapping[str, Any]], type[Any]]:
    return _backend_contract(_load_backend_module())


def _read_service_environ(
    *,
    service: str = COURIER_API_SERVICE,
    proc_root: Path = Path("/proc"),
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, str]:
    """Read only the four route flags from the running service's environment."""
    command = [
        "systemctl",
        "show",
        service,
        "--property=MainPID",
        "--value",
        "--no-pager",
    ]
    try:
        completed = run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        raise ProcessConfigError(
            f"cannot resolve {service} MainPID: {type(exc).__name__}"
        ) from exc
    if completed.returncode != 0:
        raise ProcessConfigError(
            f"cannot resolve {service} MainPID: systemctl exit "
            f"{completed.returncode}"
        )
    raw_pid = completed.stdout.strip()
    if not raw_pid.isdecimal() or int(raw_pid) <= 0:
        raise ProcessConfigError(f"{service} has no running MainPID")

    environ_path = proc_root / raw_pid / "environ"
    try:
        raw_environ = environ_path.read_bytes()
    except Exception as exc:
        raise ProcessConfigError(
            f"cannot read {service} process environ: {type(exc).__name__}"
        ) from exc

    watched = set(ROUTE_CONFIG_ENV.values())
    environ: dict[str, str] = {}
    for raw_entry in raw_environ.split(b"\0"):
        if b"=" not in raw_entry:
            continue
        raw_key, raw_value = raw_entry.split(b"=", 1)
        try:
            key = raw_key.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if key not in watched:
            continue
        if key in environ:
            raise ProcessConfigError(
                f"{service} process environ contains duplicate {key}"
            )
        try:
            environ[key] = raw_value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProcessConfigError(
                f"{service} process environ has non-UTF8 {key}"
            ) from exc

    return environ


def _load_state_snapshots(
    orders_path: Path = ORDERS_PATH,
    plans_path: Path = PLANS_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read each live state carrier exactly once for the whole monitor cycle."""
    raw_orders = json.loads(orders_path.read_text(encoding="utf-8"))
    if not isinstance(raw_orders, dict):
        raise ValueError("orders_state.json must contain an object")
    nested_orders = raw_orders.get("orders")
    orders = nested_orders if isinstance(nested_orders, dict) else raw_orders
    plans = json.loads(plans_path.read_text(encoding="utf-8"))
    if not isinstance(plans, dict):
        raise ValueError("courier_plans.json must contain an object")
    return orders, plans


def _pickup_min(value: Any) -> int | None:
    """Faithful port of Kotlin ``pickupMin`` (including equality fallback)."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0].strip()) * 60 + int(parts[1].strip())
    except ValueError:
        return None


def _pickup_together(left: Any, right: Any) -> bool:
    left_min = _pickup_min(left)
    right_min = _pickup_min(right)
    if left_min is not None and right_min is not None:
        return abs(left_min - right_min) <= PICKUP_MERGE_MIN
    return left == right


def _restaurant_key(place: Any) -> str:
    """Faithful projection of Kotlin ``restaurantKey(PlaceDto)``."""
    doc = place if isinstance(place, Mapping) else {}
    lat, lon = doc.get("lat"), doc.get("lon")
    if lat is not None and lon is not None:
        return f"{float(lat):.4f},{float(lon):.4f}"
    name = str(doc.get("name") or "").strip().lower()
    address = str(doc.get("address") or "").strip().lower()
    return f"{name}|{address}"


def project_kotlin_build_steps(dto: Mapping[str, Any]) -> list[list[Any]]:
    """Project real legacy DTO through Kotlin ``RouteLogic.buildSteps``.

    The projection intentionally ignores ``done`` and display-only metadata.
    It preserves step order and groups only adjacent pickups exactly as the
    Kotlin code does, then normalizes group ids like the existing route-order
    golden (sorted strings).
    """
    raw_orders = dto.get("orders")
    raw_stops = dto.get("stop_sequence")
    if not isinstance(raw_orders, list) or not isinstance(raw_stops, list):
        raise ValueError("DTO requires list fields orders and stop_sequence")

    by_id: dict[str, Mapping[str, Any]] = {}
    for order in raw_orders:
        if not isinstance(order, Mapping) or order.get("order_id") is None:
            continue
        by_id[str(order["order_id"])] = order

    out: list[dict[str, Any]] = []
    for ref in raw_stops:
        if not isinstance(ref, Mapping) or ref.get("order_id") is None:
            continue
        order_id = str(ref["order_id"])
        order = by_id.get(order_id)
        if order is None:  # Kotlin: ``byId[ref.order_id] ?: continue``.
            continue
        is_pickup = ref.get("kind") == "pickup"
        step = {
            "kind": "pickup" if is_pickup else "dropoff",
            "ids": [order_id],
            "restaurant_key": _restaurant_key(order.get("restaurant")),
            "pickup_time": order.get("pickup_time"),
        }
        last = out[-1] if out else None
        if (
            is_pickup
            and last is not None
            and last["kind"] == "pickup"
            and last["restaurant_key"] == step["restaurant_key"]
            and _pickup_together(last["pickup_time"], step["pickup_time"])
        ):
            last["ids"].append(order_id)
        else:
            out.append(step)

    return [[step["kind"], sorted(step["ids"])] for step in out]


def _safe_id(value: Any) -> str:
    """Stable operator correlation without exposing courier/order identifiers."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _redact_projection(projection: Sequence[Sequence[Any]]) -> list[list[Any]]:
    return [
        [step[0], [_safe_id(order_id) for order_id in step[1]]]
        for step in projection
    ]


def _flatten_projection(
    projection: Sequence[Sequence[Any]],
) -> list[tuple[str, str]]:
    """Normalize stop grouping without changing the route sequence.

    A grouped pickup ``["pickup", ["A", "B"]]`` and two adjacent pickup
    groups ``["pickup", ["A"]], ["pickup", ["B"]]`` describe the same
    route.  Flattening both sides to ``(kind, order_id)`` pairs preserves the
    order within every group and across groups while removing only that
    representational boundary.
    """
    flattened: list[tuple[str, str]] = []
    for index, step in enumerate(projection):
        if (
            not isinstance(step, Sequence)
            or isinstance(step, (str, bytes))
            or len(step) != 2
        ):
            raise ValueError(f"projection step {index} must be [kind, order_ids]")
        kind, raw_order_ids = step
        if not isinstance(kind, str):
            raise ValueError(f"projection step {index} kind must be a string")
        if (
            not isinstance(raw_order_ids, Sequence)
            or isinstance(raw_order_ids, (str, bytes))
        ):
            raise ValueError(
                f"projection step {index} order_ids must be a sequence"
            )
        flattened.extend(
            (kind, str(order_id)) for order_id in raw_order_ids
        )
    return flattened


def _gate_line(verdict: str, coverage: Mapping[str, Any], reason: str) -> str:
    alarm = "—"
    if verdict in {"BROKEN", "CONFIG_DRIFT"}:
        alarm = (
            f"ALARM: route parity {verdict}; "
            f"coverage={coverage.get('checked_bags', 0)}/"
            f"{coverage.get('qualifying_bags', 0)}; {reason}"
        )
    return (
        f"| — | {GATE_ID} | WAIT_DATA | CTO | {GATE_DUE} | {alarm} |"
    )


def evaluate(
    bags: Mapping[str, Sequence[Mapping[str, Any]]],
    plans: Mapping[str, Any],
    *,
    orders_snapshot: Mapping[str, Any],
    route_config: Any,
    canon_builder: Callable[[Sequence[Mapping[str, Any]], Any], Sequence[Any]],
    dto_builder: Callable[
        [str, Mapping[str, Any], Mapping[str, Any], Any], Mapping[str, Any]
    ],
    canon_projector: Callable[[Sequence[Any]], list[list[Any]]],
    live_flags: Mapping[str, Any],
    corpus_flags: Mapping[str, Any],
    observed_at: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Pure verdict engine; dependencies are injected for hermetic oracles."""
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    qualifying = len(bags)
    checked = 0
    mismatches: list[dict[str, Any]] = []
    parity_mismatch_bags = 0
    grouping_only_difference_bags = 0
    errors: list[dict[str, str]] = []

    flag_drift = dict(live_flags) != dict(corpus_flags)
    if not flag_drift:
        for courier_id, bag in sorted(bags.items()):
            try:
                canonical = canon_projector(
                    canon_builder(bag, plans.get(courier_id))
                )
                dto = dto_builder(
                    courier_id, orders_snapshot, plans, route_config
                )
                client = project_kotlin_build_steps(dto)
                canonical_flat = _flatten_projection(canonical)
                client_flat = _flatten_projection(client)
            except Exception as exc:
                # One bad courier must make coverage fail closed.
                errors.append(
                    {
                        "courier_ref": _safe_id(courier_id),
                        "error": f"{type(exc).__name__}: {exc}"[:240],
                    }
                )
                continue
            checked += 1
            if client != canonical:
                grouping_only_difference = client_flat == canonical_flat
                if grouping_only_difference:
                    grouping_only_difference_bags += 1
                else:
                    parity_mismatch_bags += 1
                mismatches.append(
                    {
                        "courier_ref": _safe_id(courier_id),
                        "canonical": _redact_projection(canonical),
                        "client": _redact_projection(client),
                        "grouping_only_difference": grouping_only_difference,
                    }
                )

    coverage = {
        "qualifying_bags": qualifying,
        "checked_bags": checked,
        "coverage_ratio": (checked / qualifying) if qualifying else None,
        "mismatch_bags": parity_mismatch_bags,
        "grouping_only_difference_bags": grouping_only_difference_bags,
        "error_bags": len(errors),
    }
    if flag_drift:
        verdict, verdict_class, reason, exit_code = (
            "CONFIG_DRIFT",
            "INFRA_BROKEN",
            "effective courier-api route config differs from the pinned corpus",
            EXIT_CONFIG_DRIFT,
        )
    elif errors or checked != qualifying:
        verdict, verdict_class, reason, exit_code = (
            "BROKEN",
            "INFRA_BROKEN",
            "qualifying DTO coverage is incomplete",
            EXIT_INFRA_BROKEN,
        )
    elif parity_mismatch_bags:
        verdict, verdict_class, reason, exit_code = (
            "BROKEN",
            "PARITY_BROKEN",
            "canonical and Kotlin-projected routes differ",
            EXIT_PARITY_BROKEN,
        )
    elif qualifying == 0:
        verdict, verdict_class, reason, exit_code = (
            "EXPECTED_NO_DATA",
            "EXPECTED_NO_DATA",
            "no qualifying active courier bags",
            EXIT_EXPECTED_NO_DATA,
        )
    else:
        verdict, verdict_class, reason, exit_code = (
            "OK",
            "OK",
            "full parity",
            EXIT_OK,
        )

    result = {
        "schema_version": 3,
        "monitor": GATE_ID,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "reason": reason,
        "heartbeat": {
            "observed_at_utc": observed_at,
            "run_id": str(uuid.uuid4()),
            "coverage": coverage,
        },
        "mismatches": mismatches,
        "errors": errors,
        "flag_drift": flag_drift,
        "live_flags": dict(live_flags),
        "corpus_flags": dict(corpus_flags),
        "config_source": {
            "service": COURIER_API_SERVICE,
            "reader": "/proc/<MainPID>/environ",
        },
    }
    result["open_gates_line"] = _gate_line(verdict, coverage, reason)
    return result, exit_code


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomic 0600 write: temp -> fsync -> rename -> directory fsync."""
    path = path.resolve()
    if not path.parent.is_dir():
        raise FileNotFoundError(f"result directory does not exist: {path.parent}")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


def _infra_result(exc: Exception) -> tuple[dict[str, Any], int]:
    now = datetime.now(timezone.utc).isoformat()
    coverage = {
        "qualifying_bags": 0,
        "checked_bags": 0,
        "coverage_ratio": None,
        "mismatch_bags": 0,
        "grouping_only_difference_bags": 0,
        "error_bags": 1,
    }
    reason = f"{type(exc).__name__}: {exc}"[:240]
    result = {
        "schema_version": 3,
        "monitor": GATE_ID,
        "verdict": "BROKEN",
        "verdict_class": "INFRA_BROKEN",
        "reason": "monitor infrastructure failure",
        "heartbeat": {
            "observed_at_utc": now,
            "run_id": str(uuid.uuid4()),
            "coverage": coverage,
        },
        "mismatches": [],
        "errors": [{"error": reason}],
        "flag_drift": False,
        "live_flags": {},
        "corpus_flags": {},
        "config_source": {
            "service": COURIER_API_SERVICE,
            "reader": "/proc/<MainPID>/environ",
        },
    }
    result["open_gates_line"] = _gate_line(
        "BROKEN", coverage, "monitor infrastructure failure"
    )
    return result, EXIT_INFRA_BROKEN


def _run_live() -> tuple[dict[str, Any], int]:
    gen = _load_gen()
    dto_builder, route_config_type = _load_backend_contract()
    if dict(getattr(route_config_type, "ENV_BY_FIELD", {})) != ROUTE_CONFIG_ENV:
        raise ProcessConfigError(
            "courier_orders.RouteConfig environment schema differs from monitor"
        )
    process_environ = _read_service_environ()
    route_config = route_config_type.from_environ(process_environ)
    live_flags = route_config.as_dict()
    if set(live_flags) != set(ROUTE_CONFIG_ENV):
        raise ProcessConfigError(
            "courier_orders.RouteConfig.as_dict returned an unexpected schema"
        )

    orders_snapshot, plans_snapshot = _load_state_snapshots()
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    corpus_flags = corpus["meta"]["courier_api_route_config"]
    if set(corpus_flags) != set(ROUTE_CONFIG_ENV):
        raise ValueError("golden corpus route config schema is incomplete")
    bags = gen._bag_dicts_live(list(orders_snapshot.values()))

    from dispatch_v2 import route_podjazdy as route_canon  # noqa: PLC0415

    def canon_builder(bag: Sequence[Mapping[str, Any]], plan: Any):
        return route_canon.order_podjazdy(
            bag,
            plan,
            plan_aware=route_config.plan_aware_podjazdy,
            trust_canon=route_config.build_view_trust_canon_order,
        )

    return evaluate(
        bags,
        plans_snapshot,
        orders_snapshot=orders_snapshot,
        route_config=route_config,
        canon_builder=canon_builder,
        dto_builder=dto_builder,
        canon_projector=gen._proj,
        live_flags=live_flags,
        corpus_flags=corpus_flags,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="full JSON on stdout")
    parser.add_argument(
        "--result-path",
        type=Path,
        help="atomically persist the heartbeat/verdict (no write when omitted)",
    )
    args = parser.parse_args()

    try:
        result, exit_code = _run_live()
    except Exception as exc:  # fail closed within the four-state contract
        result, exit_code = _infra_result(exc)

    if args.result_path is not None:
        try:
            _atomic_write_json(args.result_path, result)
        except Exception as exc:
            result, exit_code = _infra_result(exc)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        coverage = result["heartbeat"]["coverage"]
        print(
            f"[route_order_live_parity {result['heartbeat']['observed_at_utc']}] "
            f"verdict={result['verdict']} "
            f"checked={coverage['checked_bags']}/"
            f"{coverage['qualifying_bags']} "
            f"mismatches={coverage['mismatch_bags']} "
            f"grouping_only={coverage['grouping_only_difference_bags']} "
            f"errors={coverage['error_bags']}"
        )
        print(result["open_gates_line"])
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
