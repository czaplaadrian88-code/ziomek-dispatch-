"""Z-P2-06: truthful OSRM health, provenance and bounded cache eviction.

All HTTP is synthetic.  The tests must never contact the live OSRM instance and
must never mutate production state.  Numeric assertions intentionally pin the
legacy duration/distance contract while the new fields remain additive.
"""
from __future__ import annotations

import copy
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout

import pytest

from dispatch_v2 import osrm_client as oc


A = (53.1325, 23.1688)
B = (53.1158, 23.1611)
C = (53.1300, 23.1600)


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def _route_payload(duration=321.5, distance=2345.0):
    return {
        "code": "Ok",
        "routes": [{"duration": duration, "distance": distance}],
        "waypoints": [{"distance": 0.0}, {"distance": 0.0}],
    }


def _table_payload(n_o=2, n_d=2):
    return {
        "code": "Ok",
        "durations": [[float(60 * (i + j + 1)) for j in range(n_d)] for i in range(n_o)],
        "distances": [[float(1000 * (i + j + 1)) for j in range(n_d)] for i in range(n_o)],
    }


def _nearest_payload():
    return {"code": "Ok", "waypoints": [{"location": [23.16, 53.13], "name": "probe"}]}


def _all_endpoints_ok(url, timeout=1.0):
    del timeout
    if "/route/" in url:
        return _Response(_route_payload())
    if "/table/" in url:
        return _Response(_table_payload(2, 1))
    if "/nearest/" in url:
        return _Response(_nearest_payload())
    raise AssertionError(f"unexpected URL {url}")


@pytest.fixture(autouse=True)
def _isolated_osrm(monkeypatch):
    monkeypatch.setattr(oc, "_route_cache", {})
    monkeypatch.setattr(oc, "_table_cell_cache", {})
    monkeypatch.setattr(oc, "_osrm_stats", copy.deepcopy(oc._osrm_stats))
    monkeypatch.setattr(oc, "ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER", False)
    monkeypatch.setattr(oc, "_common_flag", lambda _name, default=False: default)
    monkeypatch.setattr(oc, "_mp13_send_alert_safe", lambda _msg: None)
    with oc._module_lock:
        monkeypatch.setattr(oc, "_osrm_failures", 0)
        monkeypatch.setattr(oc, "_osrm_circuit_open_until", 0.0)
        monkeypatch.setattr(oc, "_osrm_last_success_ts", None)
        monkeypatch.setattr(oc, "_osrm_degraded_since", None)
        monkeypatch.setattr(oc, "_osrm_degraded_alert_sent", False)
        monkeypatch.setattr(oc, "_osrm_recovery_alert_sent", False)
    if hasattr(oc, "_new_osrm_telemetry"):
        monkeypatch.setattr(oc, "_osrm_telemetry", oc._new_osrm_telemetry())


def _numeric(result):
    return {
        k: result.get(k)
        for k in ("duration_s", "duration_min", "distance_m", "distance_km")
    }


def test_route_upstream_then_cache_has_additive_provenance_and_numeric_parity(monkeypatch):
    calls = []

    def fake(url, timeout=3):
        calls.append((url, timeout))
        return _Response(_route_payload())

    monkeypatch.setattr(oc.urllib.request, "urlopen", fake)
    first = oc.route(A, B)
    second = oc.route(A, B)

    assert len(calls) == 1
    assert first["osrm_source"] == "upstream"
    assert first["osrm_degraded"] is False
    assert first["osrm_fallback"] is False
    assert second["osrm_source"] == "cache"
    assert second["osrm_degraded"] is False
    assert second["osrm_fallback"] is False
    assert _numeric(second) == _numeric(first)


def test_route_timeout_is_fallback_and_keeps_legacy_numbers(monkeypatch):
    monkeypatch.setattr(
        oc.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("synthetic timeout")),
    )
    result = oc.route(A, B, use_cache=False)
    assert result["osrm_source"] == "fallback"
    assert result["osrm_degraded"] is True
    assert result["osrm_fallback"] is True
    assert result["duration_s"] > 0
    assert result["distance_m"] > 0


def test_route_open_circuit_is_fallback_without_http(monkeypatch):
    monkeypatch.setattr(oc, "_osrm_is_circuit_open", lambda: True)
    monkeypatch.setattr(
        oc.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("HTTP must be bypassed")),
    )
    result = oc.route(A, B, use_cache=False)
    assert result["osrm_source"] == "fallback"
    assert result["osrm_degraded"] is True


def test_invalid_coordinate_is_explicit_degraded_fallback_without_http(monkeypatch):
    monkeypatch.setattr(
        oc.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("HTTP must be bypassed")),
    )
    result = oc.route((0.0, 0.0), B, use_cache=False)
    assert result["coord_invalid"] is True
    assert result["osrm_source"] == "fallback"
    assert result["osrm_degraded"] is True


def test_table_cold_then_full_hit_marks_each_cell_and_preserves_numbers(monkeypatch):
    monkeypatch.setattr(
        oc,
        "_common_flag",
        lambda name, default=False: name == "ENABLE_OSRM_TABLE_CELL_CACHE",
    )
    calls = []

    def fake(url, timeout=3):
        calls.append((url, timeout))
        return _Response(_table_payload(2, 2))

    monkeypatch.setattr(oc.urllib.request, "urlopen", fake)
    first = oc.table([A, B], [A, B])
    second = oc.table([A, B], [A, B])

    assert len(calls) == 1
    assert {c["osrm_source"] for row in first for c in row} == {"upstream"}
    assert {c["osrm_source"] for row in second for c in row} == {"cache"}
    assert all(not c["osrm_degraded"] for row in first + second for c in row)
    assert [[_numeric(c) for c in row] for row in second] == [
        [_numeric(c) for c in row] for row in first
    ]


def test_table_http_failure_returns_only_degraded_fallback_cells(monkeypatch):
    monkeypatch.setattr(
        oc.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("synthetic timeout")),
    )
    matrix = oc.table([A, B], [A, B])
    assert {c["osrm_source"] for row in matrix for c in row} == {"fallback"}
    assert all(c["osrm_degraded"] and c["osrm_fallback"] for row in matrix for c in row)
    assert oc._table_cell_cache == {}


def test_table_partial_code_ok_is_rejected_in_upstream_telemetry_without_policy_change(
        monkeypatch):
    malformed = {
        "code": "Ok",
        "durations": [[60.0]],
        "distances": [[1000.0]],
    }
    monkeypatch.setattr(
        oc.urllib.request, "urlopen", lambda *_a, **_k: _Response(malformed))

    matrix = oc.table([A, B], [A, B])

    # Preserve the legacy code=Ok return semantics; only observation changes.
    assert len(matrix) == 1 and len(matrix[0]) == 1
    assert matrix[0][0]["osrm_source"] == "upstream"
    upstream = oc.telemetry_snapshot()["upstream"]["table"]
    assert upstream["attempts"] == 1
    assert upstream["successes"] == 0
    assert upstream["rejected"] == 1


def test_direct_probe_bypasses_cache_fallback_and_circuit_without_mutating_them(monkeypatch):
    oc._route_cache[("sentinel", "sentinel")] = (time.time(), {"duration_s": 1})
    oc._table_cell_cache[("sentinel", "sentinel")] = (time.time(), {"duration_s": 1})
    with oc._module_lock:
        oc._osrm_failures = 9
        oc._osrm_circuit_open_until = time.time() + 999
        before = (oc._osrm_failures, oc._osrm_circuit_open_until)
    monkeypatch.setattr(oc.urllib.request, "urlopen", _all_endpoints_ok)

    probe = oc.probe_upstream(timeout_s=0.25)

    assert probe["upstream_ok"] is True
    assert probe["endpoints"]["route"]["ok"] is True
    assert probe["endpoints"]["table"]["ok"] is True
    assert probe["endpoints"]["nearest"]["ok"] is True
    with oc._module_lock:
        assert (oc._osrm_failures, oc._osrm_circuit_open_until) == before
    assert len(oc._route_cache) == 1
    assert len(oc._table_cell_cache) == 1


def test_direct_probe_down_then_recovery_is_truthful_and_does_not_touch_cb(monkeypatch):
    state = {"down": True}

    def fake(url, timeout=1.0):
        if state["down"]:
            raise TimeoutError("synthetic down")
        return _all_endpoints_ok(url, timeout)

    monkeypatch.setattr(oc.urllib.request, "urlopen", fake)
    before = (oc._osrm_failures, oc._osrm_circuit_open_until)
    down = oc.probe_upstream()
    state["down"] = False
    recovered = oc.probe_upstream()

    assert down["upstream_ok"] is False
    assert {v["error_kind"] for v in down["endpoints"].values()} == {"timeout"}
    assert recovered["upstream_ok"] is True
    assert (oc._osrm_failures, oc._osrm_circuit_open_until) == before


@pytest.mark.parametrize(
    ("broken_endpoint", "malformed_payload"),
    [
        ("route", {"code": "Ok", "routes": []}),
        (
            "table",
            {"code": "Ok", "durations": [[60.0]], "distances": [[1000.0]]},
        ),
        ("nearest", {"code": "Ok", "waypoints": []}),
    ],
)
def test_direct_probe_rejects_code_ok_partial_payload_without_state_mutation(
        monkeypatch, broken_endpoint, malformed_payload):
    oc._route_cache[("route", "sentinel")] = (time.time(), {"duration_s": 1})
    oc._table_cell_cache[("table", "sentinel")] = (time.time(), {"duration_s": 1})
    with oc._module_lock:
        oc._osrm_failures = 4
        oc._osrm_circuit_open_until = time.time() + 90
        oc._osrm_last_success_ts = 123.0
        oc._osrm_degraded_since = 120.0
        before_cb = (
            oc._osrm_failures,
            oc._osrm_circuit_open_until,
            oc._osrm_last_success_ts,
            oc._osrm_degraded_since,
        )
    before_route_cache = dict(oc._route_cache)
    before_table_cache = dict(oc._table_cell_cache)

    def fake(url, timeout=1.0):
        del timeout
        endpoint = next(name for name in ("route", "table", "nearest")
                        if f"/{name}/" in url)
        if endpoint == broken_endpoint:
            return _Response(malformed_payload)
        return _all_endpoints_ok(url)

    monkeypatch.setattr(oc.urllib.request, "urlopen", fake)
    probe = oc.probe_upstream()

    assert probe["upstream_ok"] is False
    broken = probe["endpoints"][broken_endpoint]
    assert broken["name"] == broken_endpoint
    assert broken["ok"] is False
    assert broken["latency_ms"] >= 0
    assert broken["error_kind"] == "invalid_response"
    assert all(
        row["ok"] for name, row in probe["endpoints"].items()
        if name != broken_endpoint
    )
    assert dict(oc._route_cache) == before_route_cache
    assert dict(oc._table_cell_cache) == before_table_cache
    assert (
        oc._osrm_failures,
        oc._osrm_circuit_open_until,
        oc._osrm_last_success_ts,
        oc._osrm_degraded_since,
    ) == before_cb


def test_direct_probe_records_slow_latency_without_sleep(monkeypatch):
    monkeypatch.setattr(oc.urllib.request, "urlopen", _all_endpoints_ok)
    ticks = iter((
        0, 250_000_000,
        300_000_000, 560_000_000,
        600_000_000, 870_000_000,
    ))
    monkeypatch.setattr(oc.time, "perf_counter_ns", lambda: next(ticks))
    probe = oc.probe_upstream()
    assert probe["upstream_ok"] is True
    assert probe["endpoints"]["route"]["latency_ms"] == pytest.approx(250.0)
    assert probe["endpoints"]["table"]["latency_ms"] == pytest.approx(260.0)
    assert probe["endpoints"]["nearest"]["latency_ms"] == pytest.approx(270.0)


def test_health_never_calls_fallback_or_cache_green_when_upstream_is_down(monkeypatch):
    oc._route_cache[("cached", "cached")] = (time.time(), {"duration_s": 1})
    monkeypatch.setattr(
        oc.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("synthetic down")),
    )
    health = oc.health_check(timeout_s=0.1)
    assert health["schema"] == "osrm_health.v1"
    assert health["state_scope"] == "process_local"
    assert health["pid"] == oc.os.getpid()
    assert health["process_role"]
    assert health["osrm_ok"] is False
    assert health["upstream_ok"] is False
    assert health["route_ok"] is False
    assert health["table_ok"] is False
    assert health["nearest_ok"] is False
    assert health["degraded"] is True
    assert health["status"] != "healthy"


def test_health_reports_backend_recovered_but_serving_circuit_still_degraded(monkeypatch):
    monkeypatch.setattr(oc.urllib.request, "urlopen", _all_endpoints_ok)
    with oc._module_lock:
        oc._osrm_failures = oc.CIRCUIT_BREAKER_THRESHOLD
        oc._osrm_circuit_open_until = time.time() + 60
        oc._osrm_degraded_since = time.time() - 10
    health = oc.health_check()
    assert health["upstream_ok"] is True
    assert health["serving_degraded"] is True
    assert health["degraded"] is True
    assert health["status"] == "degraded"


def test_exact_circuit_transition_counter_ignores_open_window_extensions(monkeypatch):
    monkeypatch.setattr(oc.urllib.request, "urlopen", _all_endpoints_ok)
    legacy_before = oc._osrm_stats["circuit_opens"]

    oc._osrm_record_failure()
    oc._osrm_record_failure()
    oc._osrm_record_failure()  # closed -> open
    oc._osrm_record_failure()  # extends an already-open window
    assert oc.telemetry_snapshot()["circuit"]["circuit_open_transitions"] == 1

    with oc._module_lock:
        oc._osrm_circuit_open_until = 0.0
    oc._osrm_record_failure()  # expired -> open again

    health = oc.health_check()
    assert health["circuit"]["circuit_open_transitions"] == 2
    # Compatibility: legacy counter still counts every >=threshold failure.
    assert oc._osrm_stats["circuit_opens"] == legacy_before + 3


def test_existing_fallback_smoke_consumes_provenance_and_exact_transition():
    from dispatch_v2.tools import osrm_fallback_smoke as smoke

    result = smoke.smoke_circuit_breaker()

    assert result["circuit_open_transition_exact"] is True
    assert result["route_source_fallback"] is True
    assert result["table_source_fallback_all_cells"] is True
    assert all(result.values())


def test_route_eviction_retained_set_matches_legacy_batch_policy(monkeypatch):
    monkeypatch.setattr(oc, "CACHE_MAX_SIZE", 10)
    monkeypatch.setattr(oc.time, "time", lambda: 100.0)
    # Kolejnosc insercji celowo rozna od timestampow: oracle ma byc legacy
    # sort-by-write-time, nie FIFO ani incremental single-entry.
    oc._route_cache = {
        (f"old-{i}", f"old-{i}"): (float(9 - i), {"duration_s": i})
        for i in range(10)
    }
    expected = copy.deepcopy(oc._route_cache)
    for key, _ in sorted(expected.items(), key=lambda item: item[1][0])[:1]:
        del expected[key]
    expected[(oc._cache_key(A), oc._cache_key(B))] = (
        100.0, {"duration_s": 1})
    oc._cache_set(A, B, {"duration_s": 1})
    assert oc._route_cache == expected
    assert len(oc._route_cache) == 10  # 10 - legacy batch(1) + nowy wpis


def test_table_eviction_legacy_batch_and_telemetry_are_exact(monkeypatch):
    monkeypatch.setattr(oc, "TABLE_CACHE_MAX_SIZE", 20)
    oc._table_cell_cache = {
        (f"old-{i}", f"old-{i}"): (float(i), {"duration_s": i})
        for i in range(20)
    }
    oc._table_cache_set(A, B, {"duration_s": 1})
    snap = oc.telemetry_snapshot()
    assert len(oc._table_cell_cache) == 19  # 20 - legacy batch(2) + nowy
    assert ("old-0", "old-0") not in oc._table_cell_cache
    assert ("old-1", "old-1") not in oc._table_cell_cache
    assert snap["table_cache"]["evictions"] == 2
    assert snap["table_cache"]["eviction_runs"] == 1
    assert snap["table_cache"]["eviction_ns_max"] >= 0


def test_legacy_retained_set_preserves_cache_vs_fallback_during_outage(monkeypatch):
    monkeypatch.setattr(oc, "CACHE_MAX_SIZE", 10)
    pairs = [
        ((53.11 + i * 0.001, 23.11), (53.12 + i * 0.001, 23.12))
        for i in range(10)
    ]
    oc._route_cache = {
        (oc._cache_key(origin), oc._cache_key(destination)):
            (float(i), {"duration_s": 60.0 + i, "distance_m": 1000.0})
        for i, (origin, destination) in enumerate(pairs)
    }
    new_pair = ((53.1255, 23.1355), (53.1355, 23.1455))
    expected = copy.deepcopy(oc._route_cache)
    for key, _ in sorted(expected.items(), key=lambda item: item[1][0])[:1]:
        del expected[key]
    expected[(oc._cache_key(new_pair[0]), oc._cache_key(new_pair[1]))] = (
        999.0, {"duration_s": 99.0, "distance_m": 999.0})
    monkeypatch.setattr(oc.time, "time", lambda: 999.0)
    oc._cache_set(*new_pair, {"duration_s": 99.0, "distance_m": 999.0})
    assert set(oc._route_cache) == set(expected)

    monkeypatch.setattr(oc, "_osrm_circuit_open_until", 2000.0)
    assert oc.route(*pairs[0])["osrm_source"] == "fallback"
    assert oc.route(*pairs[-1])["osrm_source"] == "cache"


def test_cache_contention_wait_is_measured():
    started = threading.Event()
    done = threading.Event()

    def waiter():
        started.set()
        oc._cache_get(A, B)
        done.set()

    oc._module_lock.acquire()
    try:
        thread = threading.Thread(target=waiter)
        thread.start()
        assert started.wait(1)
        time.sleep(0.02)
    finally:
        oc._module_lock.release()
    assert done.wait(1)
    thread.join(timeout=1)
    snap = oc.telemetry_snapshot()
    assert snap["route_cache"]["lock_wait_ns_max"] >= 5_000_000


def test_parallel_cache_readers_writers_remain_bounded(monkeypatch):
    monkeypatch.setattr(oc, "CACHE_MAX_SIZE", 128)

    def work(i):
        a = (53.10 + (i % 100) / 10_000, 23.10 + (i % 100) / 10_000)
        b = (53.20 + (i % 97) / 10_000, 23.20 + (i % 97) / 10_000)
        oc._cache_set(a, b, {"duration_s": float(i)})
        return oc._cache_get(a, b)

    with ThreadPoolExecutor(max_workers=12) as pool:
        out = list(pool.map(work, range(2000)))
    assert any(v is not None for v in out)
    assert len(oc._route_cache) <= 128
    snap = oc.telemetry_snapshot()
    assert snap["route_cache"]["sets"] == 2000
    assert snap["route_cache"]["evictions"] > 0


def test_source_and_upstream_telemetry_counts_real_outcomes(monkeypatch):
    state = {"ok": True}

    def fake(_url, timeout=3):
        del timeout
        if not state["ok"]:
            raise TimeoutError("synthetic")
        return _Response(_route_payload())

    monkeypatch.setattr(oc.urllib.request, "urlopen", fake)
    oc.route(A, B)
    oc.route(A, B)
    state["ok"] = False
    oc.route(A, C, use_cache=False)
    snap = oc.telemetry_snapshot()
    assert snap["sources"]["route"] == {"upstream": 1, "cache": 1, "fallback": 1}
    assert snap["upstream"]["route"]["attempts"] == 2
    assert snap["upstream"]["route"]["successes"] == 1
    assert snap["upstream"]["route"]["failures"] == 1
    assert snap["upstream"]["route"]["timeouts"] == 1


def test_table_eviction_telemetry_does_not_change_legacy_occupancy(monkeypatch):
    size = 100
    monkeypatch.setattr(oc, "TABLE_CACHE_MAX_SIZE", size)
    base = {
        (f"k-{i}", f"k-{i}"): (float(i), {"duration_s": i})
        for i in range(size)
    }

    oc._table_cell_cache = dict(base)
    oc._table_cache_set(A, B, {"duration_s": 1})
    snap = oc.telemetry_snapshot()["table_cache"]
    assert len(oc._table_cell_cache) == size - size // 10 + 1
    assert snap["evictions"] == size // 10
    assert snap["eviction_runs"] == 1
    assert snap["eviction_ns_total"] >= snap["eviction_ns_max"] >= 0


def test_health_report_is_a_real_read_only_consumer(monkeypatch):
    from dispatch_v2.tools import osrm_health_report as report

    health = {
        "schema": "osrm_health.v1",
        "state_scope": "process_local",
        "pid": 123,
        "process_role": "health-reporter",
        "status": "healthy",
        "upstream_ok": True,
        "degraded": False,
        "serving_degraded": False,
        "circuit": {"open": False},
        "probe": {"endpoints": {}},
    }
    monkeypatch.setattr(report.oc, "health_check", lambda timeout_s=1.0: dict(health))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = report.main(["--json", "--timeout", "0.2"])
    assert rc == 0
    assert json.loads(buf.getvalue())["upstream_ok"] is True

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = report.main([])
    assert rc == 0
    assert "direct_upstream_ok=True" in buf.getvalue()
    assert "state_scope=process_local" in buf.getvalue()
    assert "role=health-reporter" in buf.getvalue()
    assert "local_circuit_open=False" in buf.getvalue()

    health.update(status="degraded", upstream_ok=False, degraded=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = report.main(["--json"])
    assert rc == 1
    assert json.loads(buf.getvalue())["status"] == "degraded"


def test_hourly_process_telemetry_is_emitted_and_resets_only_counters(monkeypatch):
    """The real importing process, not the health CLI, must publish cache truth."""
    monkeypatch.setenv("ZIOMEK_PROCESS_ROLE", "synthetic-shadow")
    monkeypatch.setitem(oc._osrm_stats, "hour_start", time.time())
    oc._route_cache[("sentinel", "sentinel")] = (
        time.time(), {"duration_s": 1.0, "osrm_source": "upstream"})
    with oc._module_lock:
        oc._osrm_failures = 2
        oc._osrm_circuit_open_until = time.time() + 60
        oc._osrm_degraded_since = time.time() - 5
        oc._osrm_telemetry["route_cache"]["hits"] = 7
        oc._osrm_telemetry["route_cache"]["evictions"] = 3
        oc._osrm_telemetry["sources"]["route"]["cache"] = 7
        oc._osrm_telemetry["probe"]["runs"] = 2
        oc._osrm_telemetry["probe"]["last_checked_at"] = "2026-07-10T12:00:00+00:00"
        oc._osrm_telemetry["probe"]["last_upstream_ok"] = False
        oc._osrm_telemetry["hour_start"] = time.time() - 3601
    before_cb = (
        oc._osrm_failures,
        oc._osrm_circuit_open_until,
        oc._osrm_degraded_since,
    )
    messages = []

    def capture(message, *args, **_kwargs):
        messages.append(message % args if args else message)

    monkeypatch.setattr(oc._log, "info", capture)
    oc._maybe_log_stats()

    raw = next(m for m in messages if m.startswith("OSRM telemetry hourly: "))
    payload = json.loads(raw.split(": ", 1)[1])
    assert payload["schema"] == "osrm_telemetry.v1"
    assert payload["process_role"] == "synthetic-shadow"
    assert payload["route_cache"]["hits"] == 7
    assert payload["route_cache"]["evictions"] == 3
    assert payload["route_cache"]["size"] == 1
    assert payload["sources"]["route"]["cache"] == 7

    after = oc.telemetry_snapshot()
    assert after["route_cache"]["hits"] == 0
    assert after["route_cache"]["evictions"] == 0
    assert after["sources"]["route"]["cache"] == 0
    assert after["probe"]["runs"] == 0
    # Last-observation gauges are state, not hourly counters.
    assert after["probe"]["last_checked_at"] == "2026-07-10T12:00:00+00:00"
    assert after["probe"]["last_upstream_ok"] is False
    assert ("sentinel", "sentinel") in oc._route_cache
    assert (
        oc._osrm_failures,
        oc._osrm_circuit_open_until,
        oc._osrm_degraded_since,
    ) == before_cb


def test_public_route_and_table_wrappers_record_full_osrm_work(monkeypatch):
    from dispatch_v2.observability import stage_timing as st

    monkeypatch.setattr(
        oc,
        "_route_impl_k04",
        lambda *_a, **_k: {"duration_s": 1.0, "osrm_source": "upstream"},
    )
    monkeypatch.setattr(
        oc,
        "_table_impl_k04",
        lambda *_a, **_k: [[
            {"duration_s": 1.0, "osrm_source": "cache"},
            {"duration_s": 2.0, "osrm_source": "fallback"},
        ]],
    )
    trace = st.DecisionTrace()
    with st.bind(trace, "courier-7"):
        oc.route(A, B)
        oc.table([A], [B, C])

    snap = trace.candidate_snapshot("courier-7")["osrm"]
    assert snap["calls"] == 2
    assert snap["tags"]["operation"] == {"route": 1, "table": 1}
    assert snap["tags"]["source"] == {"mixed": 1, "upstream": 1}


def test_cache_lock_and_eviction_work_reaches_decision_trace(monkeypatch):
    from dispatch_v2.observability import stage_timing as st

    monkeypatch.setattr(oc, "CACHE_MAX_SIZE", 10)
    oc._route_cache = {
        (f"old-{i}", f"old-{i}"): (float(i), {"duration_s": i})
        for i in range(10)
    }
    trace = st.DecisionTrace()
    with st.bind(trace, "courier-8"):
        oc._cache_set(A, C, {"duration_s": 2.0})

    other = trace.candidate_snapshot("courier-8")["other_work"]
    assert other["osrm_cache_lock_wait"]["calls"] == 1
    assert other["osrm_cache_lock_wait"]["tags"]["cache"] == {"route": 1}
    assert other["osrm_cache_eviction"]["calls"] == 1
    assert other["osrm_cache_eviction"]["tags"]["cache"] == {"route": 1}
    assert other["osrm_cache_eviction"]["tags"]["evicted"] == {"1": 1}
