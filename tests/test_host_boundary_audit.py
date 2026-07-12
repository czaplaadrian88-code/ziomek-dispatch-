from __future__ import annotations

import json

from tools import host_boundary_audit as audit


def _snapshot(
    *listeners: audit.Listener,
    input_v4: str = "TARGET_DENY_RULE_SEEN",
    input_v6: str = "TARGET_DENY_RULE_SEEN",
    docker_v4: str = "TARGET_DENY_RULE_SEEN",
    docker_v6: str = "TARGET_DENY_RULE_SEEN",
) -> audit.RuntimeSnapshot:
    return audit.RuntimeSnapshot(
        observed_at_utc="2030-01-02T03:04:05Z",
        listeners=tuple(listeners),
        unit={
            "Id": audit.EXPECTED_UNIT,
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "41",
            "NRestarts": "0",
        },
        docker_rows=(
            audit.DockerRow(
                name=audit.EXPECTED_CONTAINER,
                published_ports="127.0.0.1:9222->9222/tcp",
            ),
        ),
        ufw="INACTIVE",
        input_v4=input_v4,
        input_v6=input_v6,
        docker_user_v4=docker_v4,
        docker_user_v6=docker_v6,
    )


def _safe_listeners() -> tuple[audit.Listener, audit.Listener]:
    return (
        audit.Listener(8767, "LOOPBACK_V4", "python", 41),
        audit.Listener(9222, "LOOPBACK_V4", "docker-proxy", 52),
    )


def test_public_ipv4_listener_is_a_negative_control() -> None:
    courier, browser = _safe_listeners()
    result = audit.analyze(
        _snapshot(
            audit.Listener(courier.port, "PUBLIC_WILDCARD_V4", courier.process_name, courier.pid),
            browser,
        )
    )

    assert "PUBLIC_V4_BIND_8767" in result["findings"]
    assert result["verdict"] == "HOLD"


def test_public_ipv6_listener_is_a_negative_control() -> None:
    courier, browser = _safe_listeners()
    result = audit.analyze(
        _snapshot(
            courier,
            audit.Listener(browser.port, "PUBLIC_WILDCARD_V6", browser.process_name, browser.pid),
        )
    )

    assert "PUBLIC_V6_BIND_9222" in result["findings"]
    assert result["verdict"] == "HOLD"


def test_unexpected_owner_on_scoped_listener_is_blocking() -> None:
    _courier, browser = _safe_listeners()
    courier_result = audit.analyze(
        _snapshot(audit.Listener(8767, "LOOPBACK_V4", "unexpected", 99), browser)
    )

    assert "UNEXPECTED_OR_UNKNOWN_OWNER_8767" in courier_result["findings"]
    assert courier_result["listeners"][0]["owner"] == "UNEXPECTED_OR_UNKNOWN"

    browser_result = audit.analyze(
        _snapshot(
            audit.Listener(8767, "LOOPBACK_V4", "python", 41),
            browser,
            audit.Listener(9222, "LOOPBACK_V4", "docker-proxy", 53),
        )
    )
    assert "UNEXPECTED_OR_UNKNOWN_OWNER_9222" in browser_result["findings"]
    assert browser_result["listeners"][1]["owner"] == "UNEXPECTED_OR_UNKNOWN"


def test_provider_stays_unknown_with_perfect_local_controls() -> None:
    result = audit.analyze(_snapshot(*_safe_listeners()))

    assert result["provider_firewall"] == {
        "status": "UNKNOWN",
        "reason": "NO_EXTERNAL_PROOF",
    }
    assert "PROVIDER_FIREWALL_UNKNOWN" in result["findings"]
    assert result["verdict"] == "HOLD"


def test_missing_or_partial_host_guards_are_blocking() -> None:
    result = audit.analyze(
        _snapshot(
            *_safe_listeners(),
            input_v6="PARTIAL_TARGET_DENY_RULE_SEEN",
            docker_v4="NO_TARGET_DENY_RULE",
        )
    )

    assert "HOST_GUARD_INPUT_V6_PARTIAL_RULES_ONLY" in result["findings"]
    assert "HOST_GUARD_DOCKER_USER_V4_NO_TARGET_DENY_RULE" in result["findings"]


def test_raw_malicious_fields_are_not_emitted() -> None:
    private_marker = "PRIVATE_INPUT_MARKER_8Q2"
    personal_marker = "person_at_example_test"
    address_marker = "198.51.100.77"
    ss_raw = (
        "LISTEN 0 16 "
        + address_marker
        + ':8767 0.0.0.0:* users:(("'
        + personal_marker
        + '",pid=77,fd=3))'
    )
    docker_raw = (
        audit.EXPECTED_CONTAINER
        + "\t"
        + private_marker
        + "\t127.0.0.1:9222->9222/tcp"
    )
    unit_raw = (
        "Id=courier-api.service\nLoadState=loaded\nActiveState=active\n"
        "MainPID=77\nIgnored="
        + private_marker
    )
    snapshot = audit.RuntimeSnapshot(
        observed_at_utc="2030-01-02T03:04:05Z",
        listeners=audit.parse_ss(ss_raw)
        + (audit.Listener(9222, "LOOPBACK_V4", "docker-proxy", 88),),
        unit=audit.parse_unit_properties(unit_raw),
        docker_rows=audit.parse_docker_ps(docker_raw),
        ufw=audit.parse_ufw("Status: inactive\n" + private_marker),
        input_v4=audit.classify_port_guard("-P INPUT ACCEPT\n# " + private_marker, "INPUT", (8767, 9222)),
        input_v6="NO_TARGET_DENY_RULE",
        docker_user_v4="NO_TARGET_DENY_RULE",
        docker_user_v6="NO_TARGET_DENY_RULE",
    )

    rendered = audit.render(audit.analyze(snapshot))

    assert private_marker not in rendered
    assert personal_marker not in rendered
    assert address_marker not in rendered
    assert "UNEXPECTED_OR_UNKNOWN" in rendered


def test_invalid_cli_does_not_echo_supplied_arguments(capsys) -> None:
    private_marker = "PRIVATE_ARGUMENT_MARKER_4M9"

    assert audit.main(("--invalid", private_marker)) == 3

    output = capsys.readouterr().out
    assert private_marker not in output
    assert json.loads(output)["error"] == "INVALID_ARGUMENTS"


def test_command_allowlist_has_no_broad_process_or_container_dump() -> None:
    flattened = " ".join(token for command in audit.COMMAND_ALLOWLIST for token in command).lower()

    assert "inspect" not in flattened
    assert "/proc/" not in flattened
    assert "cmdline" not in flattened
    assert "environ" not in flattened
    assert "--property=environment" not in flattened


def test_parsers_normalize_without_preserving_image_or_unknown_unit_fields() -> None:
    rows = audit.parse_docker_ps(
        audit.EXPECTED_CONTAINER + "\tPRIVATE_IMAGE_MARKER\t0.0.0.0:9222->9222/tcp"
    )
    unit = audit.parse_unit_properties(
        "Id=courier-api.service\nMainPID=41\nUnknown=PRIVATE_UNIT_MARKER"
    )

    assert rows == (
        audit.DockerRow(audit.EXPECTED_CONTAINER, "0.0.0.0:9222->9222/tcp"),
    )
    assert unit == {"Id": "courier-api.service", "MainPID": "41"}


def test_firewall_guard_understands_default_and_explicit_denies() -> None:
    assert (
        audit.classify_port_guard("-P INPUT DROP", "INPUT", (8767, 9222))
        == "DEFAULT_DENY_POLICY_SEEN"
    )
    rules = "\n".join(
        (
            "-P INPUT ACCEPT",
            "-A INPUT -p tcp --dport 8767 -j DROP",
            "-A INPUT -p tcp --dport 9222 -j REJECT",
        )
    )
    assert (
        audit.classify_port_guard(rules, "INPUT", (8767, 9222))
        == "TARGET_DENY_RULE_SEEN"
    )


def test_conditional_or_late_deny_rule_never_proves_effective_guard() -> None:
    rules = "\n".join(
        (
            "-P INPUT ACCEPT",
            "-A INPUT -p tcp --dport 8767 -j ACCEPT",
            "-A INPUT -s 192.0.2.0/24 -p tcp --dport 8767 -j DROP",
            "-A INPUT -s 192.0.2.0/24 -p tcp --dport 9222 -j DROP",
        )
    )
    evidence = audit.classify_port_guard(rules, "INPUT", (8767, 9222))

    assert evidence == "TARGET_DENY_RULE_SEEN"
    result = audit.analyze(_snapshot(*_safe_listeners(), input_v4=evidence))
    assert "HOST_GUARD_INPUT_V4_EFFECTIVENESS_NOT_PROVEN" in result["findings"]
    assert result["verdict"] == "HOLD"
