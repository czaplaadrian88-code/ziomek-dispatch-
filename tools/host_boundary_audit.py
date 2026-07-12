#!/usr/bin/env python3
"""Read-only, redacted audit of the A360 host boundary.

Only a fixed command allowlist is used. Raw command output is parsed in memory
and never included in the JSON result.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


SCHEMA = "a360.host-boundary-audit.v1"
TARGET_PORTS = (8767, 9222)
EXPECTED_UNIT = "courier-api.service"
EXPECTED_CONTAINER = "openclaw-browser"
SAFE_CHILD_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}

# Public for a static test: every external command must remain on this list.
COMMAND_ALLOWLIST: tuple[tuple[str, ...], ...] = (
    ("ss", "-H", "-lntp"),
    (
        "systemctl",
        "show",
        EXPECTED_UNIT,
        "--property=Id,LoadState,ActiveState,SubState,MainPID,NRestarts",
    ),
    (
        "docker",
        "ps",
        "--no-trunc",
        "--format={{.Names}}\\t{{.Image}}\\t{{.Ports}}",
    ),
    ("ufw", "status"),
    ("iptables", "-S", "INPUT"),
    ("ip6tables", "-S", "INPUT"),
    ("iptables", "-S", "DOCKER-USER"),
    ("ip6tables", "-S", "DOCKER-USER"),
)

_UNIT_KEYS = frozenset(
    {"Id", "LoadState", "ActiveState", "SubState", "MainPID", "NRestarts"}
)


@dataclass(frozen=True)
class Listener:
    port: int
    bind_class: str
    process_name: str | None
    pid: int | None


@dataclass(frozen=True)
class DockerRow:
    name: str
    published_ports: str


@dataclass(frozen=True)
class RuntimeSnapshot:
    observed_at_utc: str
    listeners: tuple[Listener, ...]
    unit: Mapping[str, str]
    docker_rows: tuple[DockerRow, ...]
    ufw: str
    input_v4: str
    input_v6: str
    docker_user_v4: str
    docker_user_v6: str
    source_errors: tuple[str, ...] = ()


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _run_allowlisted(command: tuple[str, ...]) -> tuple[bool, str]:
    if command not in COMMAND_ALLOWLIST:
        raise ValueError("COMMAND_NOT_ALLOWLISTED")
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
            env=SAFE_CHILD_ENV,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if completed.returncode != 0:
        return False, ""
    return True, completed.stdout


def _split_endpoint(endpoint: str) -> tuple[str, int] | None:
    endpoint = endpoint.strip()
    if endpoint.startswith("["):
        match = re.fullmatch(r"\[([^]]+)]:(\d+)", endpoint)
        if not match:
            return None
        host, port_raw = match.groups()
    else:
        try:
            host, port_raw = endpoint.rsplit(":", 1)
        except ValueError:
            return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    if not 0 < port <= 65535:
        return None
    return host, port


def classify_bind(host: str) -> str:
    normalized = host.strip().strip("[]").split("%", 1)[0]
    if normalized == "*":
        return "WILDCARD_UNKNOWN_FAMILY"
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return "UNCLASSIFIED"
    family = "V4" if address.version == 4 else "V6"
    if address.is_unspecified:
        return f"PUBLIC_WILDCARD_{family}"
    if address.is_loopback:
        return f"LOOPBACK_{family}"
    return f"NON_LOOPBACK_{family}"


def parse_ss(raw: str) -> tuple[Listener, ...]:
    listeners: list[Listener] = []
    for line in raw.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 5 or parts[0] != "LISTEN":
            continue
        endpoint = _split_endpoint(parts[3])
        if endpoint is None:
            continue
        host, port = endpoint
        process_blob = parts[5] if len(parts) == 6 else ""
        process_match = re.search(r'users:\(\(\"([^\"]{1,64})\"', process_blob)
        pid_match = re.search(r"\bpid=(\d+)\b", process_blob)
        listeners.append(
            Listener(
                port=port,
                bind_class=classify_bind(host),
                process_name=process_match.group(1) if process_match else None,
                pid=int(pid_match.group(1)) if pid_match else None,
            )
        )
    return tuple(listeners)


def parse_unit_properties(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in _UNIT_KEYS:
            result[key] = value.strip()
    return result


def parse_docker_ps(raw: str) -> tuple[DockerRow, ...]:
    rows: list[DockerRow] = []
    for line in raw.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        name, _discarded_image, published_ports = parts
        rows.append(DockerRow(name=name, published_ports=published_ports))
    return tuple(rows)


def parse_ufw(raw: str) -> str:
    lines = raw.splitlines()
    first_line = lines[0].strip().lower() if lines else ""
    if first_line == "status: active":
        return "ACTIVE"
    if first_line == "status: inactive":
        return "INACTIVE"
    return "UNKNOWN"


def classify_port_guard(raw: str, chain: str, ports: Sequence[int]) -> str:
    if re.search(rf"(?m)^-P\s+{re.escape(chain)}\s+(DROP|REJECT)\s*$", raw):
        return "DEFAULT_DENY_POLICY_SEEN"

    denied: set[int] = set()
    for line in raw.splitlines():
        if not re.search(r"(?:^|\s)-j\s+(?:DROP|REJECT)(?:\s|$)", line):
            continue
        for raw_group in re.findall(r"--dports?\s+([0-9,:]+)", line):
            for token in re.split(r"[,:]", raw_group):
                if token.isdigit():
                    denied.add(int(token))
    required = set(ports)
    if required and required.issubset(denied):
        return "TARGET_DENY_RULE_SEEN"
    if required.intersection(denied):
        return "PARTIAL_TARGET_DENY_RULE_SEEN"
    return "NO_TARGET_DENY_RULE"


def _docker_binding_classes(row: DockerRow, port: int) -> tuple[str, ...]:
    classes: list[str] = []
    pattern = re.compile(
        r"(?P<host>\[[^]]+]|[^:,\s]+):"
        r"(?P<host_port>\d+)->(?P<container_port>\d+)/tcp"
    )
    for part in row.published_ports.split(","):
        match = pattern.fullmatch(part.strip())
        if not match:
            continue
        if int(match.group("host_port")) != port or int(match.group("container_port")) != port:
            continue
        classes.append(classify_bind(match.group("host")))
    return tuple(classes)


def _docker_owner_matches(
    rows: Sequence[DockerRow], listeners: Sequence[Listener], port: int
) -> bool:
    publishers = [
        (row, _docker_binding_classes(row, port))
        for row in rows
        if _docker_binding_classes(row, port)
    ]
    if len(publishers) != 1 or publishers[0][0].name != EXPECTED_CONTAINER:
        return False
    expected_bindings = Counter(publishers[0][1])
    observed_bindings = Counter(listener.bind_class for listener in listeners)
    return expected_bindings == observed_bindings


def _expected_owner(port: int, listeners: Sequence[Listener], snapshot: RuntimeSnapshot) -> bool:
    if not listeners:
        return False
    if port == 8767:
        try:
            main_pid = int(snapshot.unit.get("MainPID", "0"))
        except ValueError:
            return False
        unit_ok = (
            snapshot.unit.get("Id") == EXPECTED_UNIT
            and snapshot.unit.get("LoadState") == "loaded"
            and snapshot.unit.get("ActiveState") == "active"
            and main_pid > 0
        )
        return unit_ok and all(
            listener.pid == main_pid and listener.process_name in {"python", "python3"}
            for listener in listeners
        )
    if port == 9222:
        return _docker_owner_matches(snapshot.docker_rows, listeners, port) and all(
            listener.process_name == "docker-proxy" for listener in listeners
        )
    return False


def collect_live() -> RuntimeSnapshot:
    outputs: dict[tuple[str, ...], str] = {}
    errors: list[str] = []
    for index, command in enumerate(COMMAND_ALLOWLIST):
        ok, output = _run_allowlisted(command)
        outputs[command] = output
        if not ok:
            errors.append(f"SOURCE_{index}_UNAVAILABLE")

    return RuntimeSnapshot(
        observed_at_utc=_utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        listeners=parse_ss(outputs[COMMAND_ALLOWLIST[0]]),
        unit=parse_unit_properties(outputs[COMMAND_ALLOWLIST[1]]),
        docker_rows=parse_docker_ps(outputs[COMMAND_ALLOWLIST[2]]),
        ufw=parse_ufw(outputs[COMMAND_ALLOWLIST[3]]),
        input_v4=classify_port_guard(outputs[COMMAND_ALLOWLIST[4]], "INPUT", TARGET_PORTS),
        input_v6=classify_port_guard(outputs[COMMAND_ALLOWLIST[5]], "INPUT", TARGET_PORTS),
        docker_user_v4=classify_port_guard(
            outputs[COMMAND_ALLOWLIST[6]], "DOCKER-USER", (9222,)
        ),
        docker_user_v6=classify_port_guard(
            outputs[COMMAND_ALLOWLIST[7]], "DOCKER-USER", (9222,)
        ),
        source_errors=tuple(errors),
    )


def analyze(snapshot: RuntimeSnapshot) -> dict[str, Any]:
    findings: set[str] = set(snapshot.source_errors)
    listener_results: list[dict[str, Any]] = []
    for port in TARGET_PORTS:
        scoped = tuple(item for item in snapshot.listeners if item.port == port)
        bind_classes = sorted({item.bind_class for item in scoped})
        owner_ok = _expected_owner(port, scoped, snapshot)
        if not scoped:
            findings.add(f"EXPECTED_LISTENER_MISSING_{port}")
        if not owner_ok:
            findings.add(f"UNEXPECTED_OR_UNKNOWN_OWNER_{port}")
        for bind_class in bind_classes:
            if bind_class == "PUBLIC_WILDCARD_V4":
                findings.add(f"PUBLIC_V4_BIND_{port}")
            elif bind_class == "PUBLIC_WILDCARD_V6":
                findings.add(f"PUBLIC_V6_BIND_{port}")
            elif bind_class not in {"LOOPBACK_V4", "LOOPBACK_V6"}:
                findings.add(f"NON_LOOPBACK_OR_UNKNOWN_BIND_{port}")
        listener_results.append(
            {
                "port": port,
                "bind_classes": bind_classes,
                "owner": (
                    EXPECTED_UNIT
                    if owner_ok and port == 8767
                    else EXPECTED_CONTAINER
                    if owner_ok and port == 9222
                    else "UNEXPECTED_OR_UNKNOWN"
                ),
            }
        )

    firewall = {
        "ufw": snapshot.ufw,
        "input_v4": snapshot.input_v4,
        "input_v6": snapshot.input_v6,
        "docker_user_v4": snapshot.docker_user_v4,
        "docker_user_v6": snapshot.docker_user_v6,
    }
    for name in ("input_v4", "input_v6", "docker_user_v4", "docker_user_v6"):
        evidence = firewall[name]
        if evidence == "NO_TARGET_DENY_RULE":
            findings.add(f"HOST_GUARD_{name.upper()}_NO_TARGET_DENY_RULE")
        elif evidence == "PARTIAL_TARGET_DENY_RULE_SEEN":
            findings.add(f"HOST_GUARD_{name.upper()}_PARTIAL_RULES_ONLY")
        else:
            # A local line parser cannot prove rule order, predicates, jumps, or
            # the effective packet path. Seen rules are evidence, never a pass.
            findings.add(f"HOST_GUARD_{name.upper()}_EFFECTIVENESS_NOT_PROVEN")

    # Local evidence is intentionally insufficient to prove a provider control.
    findings.add("PROVIDER_FIREWALL_UNKNOWN")
    ordered_findings = sorted(findings)
    return {
        "schema": SCHEMA,
        "phase": "SOURCE_PREP",
        "mutations_performed": False,
        "observed_at_utc": snapshot.observed_at_utc,
        "verdict": "HOLD",
        "listeners": listener_results,
        "host_firewall": firewall,
        "provider_firewall": {"status": "UNKNOWN", "reason": "NO_EXTERNAL_PROOF"},
        "findings": ordered_findings,
        "prohibited_sources_used": False,
    }


def render(result: Mapping[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments == ("--help",):
        print("usage: host_boundary_audit.py --live")
        return 0
    if arguments != ("--live",):
        print(render({"schema": SCHEMA, "verdict": "ERROR", "error": "INVALID_ARGUMENTS"}))
        return 3
    print(render(analyze(collect_live())))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
