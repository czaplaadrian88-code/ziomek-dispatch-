from __future__ import annotations

import copy
import json
from pathlib import Path

from tools import host_boundary_audit as audit


_CHANGE_ID = "A360-SEC1-GOLDEN"
_OBSERVATION_ID = "OBS-GOLDEN"
_BROWSER_IMAGE_REFERENCE = "registry.example/openclaw-browser@sha256:" + "e" * 64
_DEPLOY_PROVISIONER_COMMIT = "d" * 40
_DEPLOY_PROVISIONER_HASH = "e" * 64
_HASHES = {
    "input_v4": "1" * 64,
    "input_v6": "2" * 64,
    "docker_user_v4": "3" * 64,
    "docker_user_v6": "4" * 64,
}


def _snapshot(
    *listeners: audit.Listener,
    input_v4: str = "TARGET_DENY_RULE_SEEN",
    input_v6: str = "TARGET_DENY_RULE_SEEN",
    docker_v4: str = "TARGET_DENY_RULE_SEEN",
    docker_v6: str = "TARGET_DENY_RULE_SEEN",
    guard_fingerprints: dict[str, str] | None = None,
    browser_image_reference: str = _BROWSER_IMAGE_REFERENCE,
    unit: dict[str, str] | None = None,
) -> audit.RuntimeSnapshot:
    return audit.RuntimeSnapshot(
        observed_at_utc="2030-01-02T03:04:05Z",
        listeners=tuple(listeners),
        unit=unit
        or {
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
                image_reference=browser_image_reference,
            ),
        ),
        ufw="INACTIVE",
        input_v4=input_v4,
        input_v6=input_v6,
        docker_user_v4=docker_v4,
        docker_user_v6=docker_v6,
        guard_fingerprints=guard_fingerprints or {},
    )


def _safe_listeners() -> tuple[audit.Listener, audit.Listener]:
    return (
        audit.Listener(8767, "LOOPBACK_V4", "python", 41),
        audit.Listener(9222, "LOOPBACK_V4", "docker-proxy", 52),
    )


def _firewall_plan() -> dict[str, object]:
    return {
        "schema": "a360.sec1.host-firewall-plan.v1",
        "idempotency": "RECONCILE_SINGLE_JUMP_AND_CHAIN",
        "families": {
            family: {
                "input": {
                    "anchor_position": 1,
                    "chain_name": "A360_SEC1_INPUT",
                    "rules": [
                        "RETURN_IF_LOOPBACK",
                        "DROP_NEW_TCP_DPORTS_8767_9222",
                        "RETURN",
                    ],
                },
                "docker_user": {
                    "anchor_position": 1,
                    "chain_name": "A360_SEC1_DOCKER_USER",
                    "rules": [
                        "RETURN_IF_LOOPBACK",
                        "DROP_NEW_TCP_DPORT_9222",
                        "RETURN",
                    ],
                },
            }
            for family in ("ipv4", "ipv6")
        },
        "persistent_owner": {
            "kind": "VERSIONED_PROVISIONER",
            "source_commit": "a" * 40,
            "artifact_sha256": "a" * 64,
        },
    }


def _valid_bundle() -> dict[str, object]:
    return {
        "schema": audit.EVIDENCE_SCHEMA,
        "change_id": _CHANGE_ID,
        "observation_id": _OBSERVATION_ID,
        "time_policy": {
            "schema": audit.TIME_POLICY_SCHEMA,
            "max_age_seconds": audit.EVIDENCE_MAX_AGE_SECONDS,
            "max_future_skew_seconds": audit.EVIDENCE_MAX_FUTURE_SKEW_SECONDS,
            "max_mutual_skew_seconds": audit.EVIDENCE_MAX_MUTUAL_SKEW_SECONDS,
        },
        "source_contract": {
            "schema": audit.SOURCE_CONTRACT_SCHEMA,
            "change_id": _CHANGE_ID,
            "observation_id": _OBSERVATION_ID,
            "targets": {
                "8767": {
                    "owner": audit.EXPECTED_UNIT,
                    "bind_classes": ["LOOPBACK_V4"],
                },
                "9222": {
                    "owner": audit.EXPECTED_CONTAINER,
                    "bind_classes": ["LOOPBACK_V4"],
                },
            },
            "courier_api": {
                "repository_id": "courier_api",
                "source_commit": "b" * 40,
                "config_sha256": "b" * 64,
                "main_sha256": "c" * 64,
                "unit_owner": audit.EXPECTED_UNIT,
                "bind_policy": "LOOPBACK_ONLY",
                "credential_loader": "SYSTEMD_LOAD_CREDENTIAL",
                "process_fallback_enabled": False,
                "deployment_receipt_policy": {
                    "schema": audit.COURIER_DEPLOYMENT_POLICY_SCHEMA,
                    "producer_kind": "VERSIONED_DEPLOY_PROVISIONER",
                    "provisioner_source_commit": _DEPLOY_PROVISIONER_COMMIT,
                    "provisioner_artifact_sha256": _DEPLOY_PROVISIONER_HASH,
                },
            },
            "browser_container": {
                "container_name": audit.EXPECTED_CONTAINER,
                "manifest_repository": "approved-openclaw-manifests",
                "manifest_path": "containers/openclaw-browser.json",
                "source_commit": "c" * 40,
                "manifest_sha256": "d" * 64,
                "image_reference": _BROWSER_IMAGE_REFERENCE,
                "publish_bind_classes": ["LOOPBACK_V4"],
            },
            "host_firewall": _firewall_plan(),
            "credential_plan": {
                "logical_name": "COURIER_ADMIN_PASS",
                "loader": "SYSTEMD_LOAD_CREDENTIAL",
                "required_uid": 0,
                "required_gid": 0,
                "required_mode": 0o600,
                "nofollow": True,
                "single_link": True,
                "process_fallback_enabled": False,
                "rotation": "NEW_REVISION_ONLY",
            },
            "rollback_policy": {
                "preserve_provider_deny": True,
                "preserve_host_deny": True,
                "preserve_loopback_bind": True,
                "require_second_admin_session": True,
                "credential_strategy": "NEXT_NEW_REVISION_ONLY",
                "container_strategy": "PINNED_PREVIOUS_DIGEST_LOOPBACK_ONLY",
                "network_strategy": "PRESERVE_DENY_AND_LOOPBACK",
            },
        },
        "provider_proof": {
            "schema": audit.PROVIDER_PROOF_SCHEMA,
            "change_id": _CHANGE_ID,
            "observation_id": _OBSERVATION_ID,
            "provider": "APPROVED_PROVIDER",
            "attachment_state": "ATTACHED_TO_CURRENT_HOST",
            "ruleset_sha256": "f" * 64,
            "denied_tcp_ports_ipv4": [8767, 9222],
            "denied_tcp_ports_ipv6": [8767, 9222],
            "captured_at_utc": "2030-01-02T03:04:05Z",
            "valid_until_utc": "2030-02-01T00:00:00Z",
        },
        "external_probes": {
            "schema": audit.PROBE_PROOF_SCHEMA,
            "change_id": _CHANGE_ID,
            "observation_id": _OBSERVATION_ID,
            "independent_vantage": True,
            "observed_at_utc": "2030-01-02T03:04:05Z",
            "results": {
                "direct_8767_ipv4": "DENIED",
                "direct_8767_ipv6": "DENIED",
                "direct_9222_ipv4": "DENIED",
                "direct_9222_ipv6": "DENIED",
                "canonical_https": "ALLOWED",
                "local_api_health": "ALLOWED",
                "cdp_via_tunnel": "ALLOWED",
            },
        },
        "host_rules_receipt": {
            "schema": audit.HOST_RECEIPT_SCHEMA,
            "change_id": _CHANGE_ID,
            "observation_id": _OBSERVATION_ID,
            "observed_at_utc": "2030-01-02T03:04:05Z",
            "live_ruleset_sha256": dict(_HASHES),
            "ordered_checks": {key: "PASS" for key in _HASHES},
            "persistent_owner": {
                "kind": "VERSIONED_PROVISIONER",
                "source_commit": "a" * 40,
                "artifact_sha256": "a" * 64,
            },
        },
        "courier_api_deployment_receipt": {
            "schema": audit.COURIER_DEPLOYMENT_RECEIPT_SCHEMA,
            "change_id": _CHANGE_ID,
            "observation_id": _OBSERVATION_ID,
            "observed_at_utc": "2030-01-02T03:04:05Z",
            "producer": {
                "kind": "VERSIONED_DEPLOY_PROVISIONER",
                "source_commit": _DEPLOY_PROVISIONER_COMMIT,
                "artifact_sha256": _DEPLOY_PROVISIONER_HASH,
            },
            "postimage": {
                "source_commit": "b" * 40,
                "config_sha256": "b" * 64,
                "main_sha256": "c" * 64,
            },
            "runtime": {
                "unit_id": audit.EXPECTED_UNIT,
                "main_pid": 41,
                "load_state": "loaded",
                "active_state": "active",
                "sub_state": "running",
            },
        },
        "credential_receipt": {
            "schema": audit.CREDENTIAL_RECEIPT_SCHEMA,
            "change_id": _CHANGE_ID,
            "observation_id": _OBSERVATION_ID,
            "observed_at_utc": "2030-01-02T03:04:05Z",
            "metadata": {
                "exists": True,
                "regular": True,
                "symlink": False,
                "uid": 0,
                "gid": 0,
                "mode": 0o600,
                "nlink": 1,
                "size": 32,
                "mtime_ns": 1,
            },
            "new_revision_probe": "PASS",
            "previous_revision_probe": "REJECTED",
            "old_revision_restored": False,
        },
        "rollback_preconditions": {
            "schema": "a360.sec1.rollback-preconditions.v1",
            "change_id": _CHANGE_ID,
            "observation_id": _OBSERVATION_ID,
            "observed_at_utc": "2030-01-02T03:04:05Z",
            "second_admin_session_active": True,
            "host_denies_verified": True,
            "provider_denies_verified": True,
            "loopback_binds_verified": True,
            "next_new_credential_revision_prepared": True,
            "previous_credential_restore_forbidden": True,
            "previous_image_digest_pinned": True,
        },
    }


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
    assert audit.classify_bind("0.0.0.0") == "PUBLIC_WILDCARD_V4"
    assert audit.classify_bind("127.0.0.1") == "LOOPBACK_V4"
    assert audit.classify_bind("192.0.2.20") == "NON_LOOPBACK_V4"


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
    assert audit.classify_bind("::") == "PUBLIC_WILDCARD_V6"
    assert audit.classify_bind("::1") == "LOOPBACK_V6"
    assert audit.classify_bind("2001:db8::20") == "NON_LOOPBACK_V6"


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

    with_proof = audit.analyze(
        _snapshot(
            audit.Listener(8767, "LOOPBACK_V4", "unexpected", 99),
            browser,
            guard_fingerprints=dict(_HASHES),
        ),
        _valid_bundle(),
    )
    assert with_proof["verdict"] == "HOLD"
    assert "UNEXPECTED_OR_UNKNOWN_OWNER_8767" in with_proof["findings"]


def test_provider_stays_unknown_with_perfect_local_controls() -> None:
    result = audit.analyze(_snapshot(*_safe_listeners()))

    assert result["provider_firewall"] == {
        "status": "UNKNOWN",
        "reason": "NO_EXTERNAL_PROOF",
    }
    assert "PROVIDER_FIREWALL_UNKNOWN" in result["findings"]
    assert result["verdict"] == "HOLD"

    proven = audit.analyze(
        _snapshot(*_safe_listeners(), guard_fingerprints=dict(_HASHES)),
        _valid_bundle(),
    )
    assert proven["provider_firewall"] == {
        "status": "PROVEN",
        "reason": "VALID_EXTERNAL_PROOF",
    }
    assert proven["remediation_evidence"]["status"] == "VALID"
    assert proven["findings"] == []
    assert proven["verdict"] == "PASS"


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

    receipt_mismatch = audit.analyze(
        _snapshot(*_safe_listeners(), guard_fingerprints={key: "9" * 64 for key in _HASHES}),
        _valid_bundle(),
    )
    assert "HOST_RULE_RECEIPT_RUNTIME_MISMATCH" in receipt_mismatch["findings"]
    assert receipt_mismatch["verdict"] == "HOLD"


def test_raw_malicious_fields_are_not_emitted(tmp_path: Path) -> None:
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

    malicious_bundle = _valid_bundle()
    malicious_bundle["password"] = private_marker
    private_path_marker = "private/manifests/browser-private.json"
    private_hash_marker = "8" * 64
    private_image_reference = "private.example/browser@sha256:" + "6" * 64
    malicious_bundle["source_contract"]["browser_container"][
        "manifest_repository"
    ] = personal_marker
    malicious_bundle["source_contract"]["browser_container"][
        "manifest_path"
    ] = private_path_marker
    malicious_bundle["source_contract"]["browser_container"][
        "image_reference"
    ] = private_image_reference
    malicious_bundle["source_contract"]["courier_api"][
        "config_sha256"
    ] = private_hash_marker
    evidence_rendered = audit.render(
        audit.analyze(
            _snapshot(*_safe_listeners(), guard_fingerprints=dict(_HASHES)),
            malicious_bundle,
        )
    )
    assert private_marker not in evidence_rendered
    assert personal_marker not in evidence_rendered
    assert private_path_marker not in evidence_rendered
    assert private_hash_marker not in evidence_rendered
    assert private_image_reference not in evidence_rendered
    assert "EVIDENCE_PROHIBITED_FIELD_PRESENT" in evidence_rendered
    assert "EVIDENCE_FIELDS_INVALID" in evidence_rendered

    private_pid_marker = 90909091
    pid_bound_bundle = _valid_bundle()
    pid_bound_bundle["courier_api_deployment_receipt"]["runtime"][
        "main_pid"
    ] = private_pid_marker
    pid_bound_snapshot = _snapshot(
        audit.Listener(8767, "LOOPBACK_V4", "python", private_pid_marker),
        audit.Listener(9222, "LOOPBACK_V4", "docker-proxy", 52),
        guard_fingerprints=dict(_HASHES),
        unit={
            "Id": audit.EXPECTED_UNIT,
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": str(private_pid_marker),
            "NRestarts": "0",
        },
    )
    pid_bound_result = audit.analyze(pid_bound_snapshot, pid_bound_bundle)
    pid_bound_rendered = audit.render(pid_bound_result)
    assert pid_bound_result["verdict"] == "PASS"
    assert str(private_pid_marker) not in pid_bound_rendered

    evidence_path = tmp_path / audit.EVIDENCE_FILENAME
    evidence_path.write_text(json.dumps(_valid_bundle()), encoding="utf-8")
    evidence_path.chmod(0o600)
    loaded, load_codes = audit.load_evidence_bundle(evidence_path)
    assert load_codes == ()
    assert loaded is not None

    unsafe_dir = tmp_path / "unsafe"
    unsafe_dir.mkdir()
    unsafe_path = unsafe_dir / audit.EVIDENCE_FILENAME
    unsafe_path.write_text("{}", encoding="utf-8")
    unsafe_path.chmod(0o644)
    assert "EVIDENCE_FILE_MODE_INVALID" in audit.load_evidence_bundle(unsafe_path)[1]

    symlink_dir = tmp_path / "symlink"
    symlink_dir.mkdir()
    target = symlink_dir / "target.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    symlink_path = symlink_dir / audit.EVIDENCE_FILENAME
    symlink_path.symlink_to(target)
    assert "EVIDENCE_FILE_NOT_REGULAR_NOFOLLOW" in audit.load_evidence_bundle(symlink_path)[1]

    hardlink_dir = tmp_path / "hardlink"
    hardlink_dir.mkdir()
    hardlink_target = hardlink_dir / "target.json"
    hardlink_target.write_text("{}", encoding="utf-8")
    hardlink_target.chmod(0o600)
    hardlink_path = hardlink_dir / audit.EVIDENCE_FILENAME
    hardlink_path.hardlink_to(hardlink_target)
    assert "EVIDENCE_FILE_LINK_COUNT_INVALID" in audit.load_evidence_bundle(hardlink_path)[1]

    credential = tmp_path / "credential-revision"
    credential.write_text(private_marker, encoding="utf-8")
    credential.chmod(0o600)
    assert audit.validate_credential_metadata(audit.metadata_only(credential)) == ()
    credential.chmod(0o644)
    assert "CREDENTIAL_REVISION_MODE_INVALID" in audit.validate_credential_metadata(
        audit.metadata_only(credential)
    )
    credential_link = tmp_path / "credential-link"
    credential_link.symlink_to(credential)
    assert "CREDENTIAL_REVISION_NOT_REGULAR_NOFOLLOW" in audit.validate_credential_metadata(
        audit.metadata_only(credential_link)
    )


def test_invalid_cli_does_not_echo_supplied_arguments(capsys) -> None:
    private_marker = "PRIVATE_ARGUMENT_MARKER_4M9"

    assert audit.main(("--invalid", private_marker)) == 3

    output = capsys.readouterr().out
    assert private_marker not in output
    assert json.loads(output)["error"] == "INVALID_ARGUMENTS"

    assert audit.main(("--validate-evidence", private_marker)) == 2
    output = capsys.readouterr().out
    assert private_marker not in output
    assert "EVIDENCE_FILENAME_INVALID" in json.loads(output)["reason_codes"]


def test_command_allowlist_has_no_broad_process_or_container_dump() -> None:
    flattened = " ".join(token for command in audit.COMMAND_ALLOWLIST for token in command).lower()

    assert "inspect" not in flattened
    assert "/proc/" not in flattened
    assert "cmdline" not in flattened
    assert "environ" not in flattened
    assert "--property=environment" not in flattened
    assert " inspect " not in " " + flattened + " "
    assert "docker images" not in flattened
    assert "ps aux" not in flattened
    assert "ps -ef" not in flattened


def test_parsers_normalize_without_preserving_image_or_unknown_unit_fields() -> None:
    private_image_marker = "private.example/browser@sha256:" + "7" * 64
    rows = audit.parse_docker_ps(
        audit.EXPECTED_CONTAINER
        + "\t"
        + private_image_marker
        + "\t0.0.0.0:9222->9222/tcp"
    )
    unit = audit.parse_unit_properties(
        "Id=courier-api.service\nMainPID=41\nUnknown=PRIVATE_UNIT_MARKER"
    )

    assert rows == (
        audit.DockerRow(
            audit.EXPECTED_CONTAINER,
            "0.0.0.0:9222->9222/tcp",
            private_image_marker,
        ),
    )
    assert private_image_marker not in repr(rows)
    assert unit == {"Id": "courier-api.service", "MainPID": "41"}

    wrong_image_runtime = audit.analyze(
        _snapshot(
            *_safe_listeners(),
            guard_fingerprints=dict(_HASHES),
            browser_image_reference="registry.example/replaced@sha256:" + "9" * 64,
        ),
        _valid_bundle(),
    )
    assert wrong_image_runtime["verdict"] == "HOLD"
    assert "BROWSER_RUNTIME_IMAGE_MISMATCH" in wrong_image_runtime["findings"]
    rendered_wrong_image = audit.render(wrong_image_runtime)
    assert "registry.example/replaced" not in rendered_wrong_image
    assert _BROWSER_IMAGE_REFERENCE not in rendered_wrong_image

    mutable_live_image = audit.analyze(
        _snapshot(
            *_safe_listeners(),
            guard_fingerprints=dict(_HASHES),
            browser_image_reference="registry.example/browser:mutable",
        ),
        _valid_bundle(),
    )
    assert "BROWSER_RUNTIME_IMAGE_NOT_IMMUTABLE" in mutable_live_image["findings"]
    assert mutable_live_image["verdict"] == "HOLD"

    mutable = _valid_bundle()
    mutable["source_contract"]["browser_container"]["image_reference"] = (
        "registry.example/openclaw-browser:latest"
    )
    validation = audit.validate_remediation_bundle(
        mutable,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "BROWSER_IMAGE_DIGEST_UNPINNED" in validation.reason_codes
    assert validation.source_contract_valid is False

    unknown_manifest = copy.deepcopy(_valid_bundle())
    unknown_manifest["source_contract"]["browser_container"]["manifest_repository"] = (
        "UNKNOWN"
    )
    validation = audit.validate_remediation_bundle(
        unknown_manifest,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "BROWSER_MANIFEST_REPOSITORY_UNPROVEN" in validation.reason_codes

    artifacts = Path(audit.__file__).parents[1] / "ops" / "security"
    container_schema = json.loads(
        (artifacts / "A360_SEC1_CONTAINER_MANIFEST.schema.json").read_text(
            encoding="utf-8"
        )
    )
    container_template = json.loads(
        (artifacts / "A360_SEC1_CONTAINER_MANIFEST.template.json").read_text(
            encoding="utf-8"
        )
    )
    provider_schema = json.loads(
        (artifacts / "A360_SEC1_PROVIDER_PROOF.schema.json").read_text(encoding="utf-8")
    )
    deployment_schema = json.loads(
        (artifacts / "A360_SEC1_COURIER_API_DEPLOYMENT_RECEIPT.schema.json").read_text(
            encoding="utf-8"
        )
    )
    deployment_template = json.loads(
        (artifacts / "A360_SEC1_COURIER_API_DEPLOYMENT_RECEIPT.template.json").read_text(
            encoding="utf-8"
        )
    )
    assert container_schema["properties"]["image_reference"]["pattern"].endswith(
        "[0-9a-f]{64}$"
    )
    assert container_template["image_reference"] is None
    assert container_template["published_ports"] == [
        {
            "host_address": "127.0.0.1",
            "host_port": 9222,
            "container_port": 9222,
            "protocol": "tcp",
        }
    ]
    assert provider_schema["properties"]["denied_tcp_ports_ipv4"]["const"] == [
        8767,
        9222,
    ]
    assert provider_schema["properties"]["denied_tcp_ports_ipv6"]["const"] == [
        8767,
        9222,
    ]
    assert deployment_schema["properties"]["runtime"]["properties"]["unit_id"][
        "const"
    ] == audit.EXPECTED_UNIT
    assert deployment_template["observed_at_utc"] is None
    assert deployment_template["runtime"]["main_pid"] is None


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

    validation = audit.validate_remediation_bundle(
        _valid_bundle(),
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert not [code for code in validation.reason_codes if code.startswith("FIREWALL_PLAN_")]

    artifacts = Path(audit.__file__).parents[1] / "ops" / "security"
    source_plan = json.loads(
        (artifacts / "A360_SEC1_HOST_FIREWALL_PLAN.json").read_text(encoding="utf-8")
    )
    plan_codes = audit._validate_firewall_plan(source_plan)
    assert not [code for code in plan_codes if "ORDER" in code]
    assert plan_codes == {
        "FIREWALL_PLAN_OWNER_COMMIT_INVALID",
        "FIREWALL_PLAN_OWNER_HASH_INVALID",
    }

    source_template = json.loads(
        (artifacts / "A360_SEC1_EVIDENCE.template.json").read_text(encoding="utf-8")
    )
    assert source_template["schema"] == audit.EVIDENCE_SCHEMA
    assert source_template["source_contract"]["schema"] == audit.SOURCE_CONTRACT_SCHEMA
    assert source_template["courier_api_deployment_receipt"]["runtime"][
        "main_pid"
    ] is None
    validation = audit.validate_remediation_bundle(
        source_template,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert validation.valid is False
    assert "BROWSER_IMAGE_DIGEST_UNPINNED" in validation.reason_codes
    assert "PROVIDER_PROOF_IDENTITY_UNKNOWN" in validation.reason_codes


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

    bad_order = _valid_bundle()
    rules_list = bad_order["source_contract"]["host_firewall"]["families"]["ipv6"][
        "input"
    ]["rules"]
    rules_list[0], rules_list[1] = rules_list[1], rules_list[0]
    validation = audit.validate_remediation_bundle(
        bad_order,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "FIREWALL_PLAN_IPV6_INPUT_ORDER_INVALID" in validation.reason_codes

    malformed_rules = copy.deepcopy(_valid_bundle())
    malformed_rules["source_contract"]["host_firewall"]["families"]["ipv4"][
        "docker_user"
    ]["rules"] = None
    validation = audit.validate_remediation_bundle(
        malformed_rules,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "FIREWALL_PLAN_IPV4_DOCKER_USER_ORDER_INVALID" in validation.reason_codes

    no_route = copy.deepcopy(_valid_bundle())
    no_route["external_probes"]["results"]["direct_9222_ipv6"] = "NO_ROUTE"
    validation = audit.validate_remediation_bundle(
        no_route,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "EXTERNAL_PROBE_DIRECT_9222_IPV6_INVALID" in validation.reason_codes

    unsafe_rollback = copy.deepcopy(_valid_bundle())
    unsafe_rollback["rollback_preconditions"]["host_denies_verified"] = False
    unsafe_rollback["source_contract"]["rollback_policy"]["preserve_loopback_bind"] = False
    validation = audit.validate_remediation_bundle(
        unsafe_rollback,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "ROLLBACK_PRECONDITION_HOST_DENIES_VERIFIED_MISSING" in validation.reason_codes
    assert "ROLLBACK_POLICY_PRESERVE_LOOPBACK_BIND_INVALID" in validation.reason_codes

    missing_deployment_receipt = copy.deepcopy(_valid_bundle())
    missing_deployment_receipt.pop("courier_api_deployment_receipt")
    validation = audit.validate_remediation_bundle(
        missing_deployment_receipt,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "COURIER_API_DEPLOYMENT_RECEIPT_NOT_SUPPLIED" in validation.reason_codes
    assert validation.valid is False
    missing_result = audit.analyze(
        _snapshot(*_safe_listeners(), guard_fingerprints=dict(_HASHES)),
        missing_deployment_receipt,
    )
    assert missing_result["verdict"] == "HOLD"

    for postimage_field, replacement in (
        ("source_commit", "f" * 40),
        ("config_sha256", "7" * 64),
        ("main_sha256", "8" * 64),
    ):
        mismatched_postimage = copy.deepcopy(_valid_bundle())
        mismatched_postimage["courier_api_deployment_receipt"]["postimage"][
            postimage_field
        ] = replacement
        validation = audit.validate_remediation_bundle(
            mismatched_postimage,
            observed_at_utc="2030-01-02T03:04:05Z",
        )
        assert (
            "COURIER_API_DEPLOYMENT_RECEIPT_"
            + postimage_field.upper()
            + "_MISMATCH"
        ) in validation.reason_codes
        assert audit.analyze(
            _snapshot(*_safe_listeners(), guard_fingerprints=dict(_HASHES)),
            mismatched_postimage,
        )["verdict"] == "HOLD"

    wrong_runtime_pid = copy.deepcopy(_valid_bundle())
    wrong_runtime_pid["courier_api_deployment_receipt"]["runtime"]["main_pid"] = 42
    runtime_result = audit.analyze(
        _snapshot(*_safe_listeners(), guard_fingerprints=dict(_HASHES)),
        wrong_runtime_pid,
    )
    assert "COURIER_API_RUNTIME_PID_MISMATCH" in runtime_result["findings"]
    assert runtime_result["verdict"] == "HOLD"

    mismatched_live_unit = {
        "Id": "courier-api-replaced.service",
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "MainPID": "41",
        "NRestarts": "0",
    }
    runtime_result = audit.analyze(
        _snapshot(
            *_safe_listeners(),
            guard_fingerprints=dict(_HASHES),
            unit=mismatched_live_unit,
        ),
        _valid_bundle(),
    )
    assert "COURIER_API_RUNTIME_UNIT_MISMATCH" in runtime_result["findings"]
    assert runtime_result["verdict"] == "HOLD"

    timed_sections = (
        ("external_probes", "EXTERNAL_PROBE"),
        ("host_rules_receipt", "HOST_RULE_RECEIPT"),
        (
            "courier_api_deployment_receipt",
            "COURIER_API_DEPLOYMENT_RECEIPT",
        ),
        ("credential_receipt", "CREDENTIAL_RECEIPT"),
        ("rollback_preconditions", "ROLLBACK_PRECONDITIONS"),
    )
    for section, prefix in timed_sections:
        stale = copy.deepcopy(_valid_bundle())
        stale[section]["observed_at_utc"] = "2030-01-02T02:49:04Z"
        validation = audit.validate_remediation_bundle(
            stale,
            observed_at_utc="2030-01-02T03:04:05Z",
        )
        assert f"{prefix}_STALE" in validation.reason_codes

        future = copy.deepcopy(_valid_bundle())
        future[section]["observed_at_utc"] = "2030-01-02T03:04:36Z"
        validation = audit.validate_remediation_bundle(
            future,
            observed_at_utc="2030-01-02T03:04:05Z",
        )
        assert f"{prefix}_FUTURE_SKEW_EXCEEDED" in validation.reason_codes

    stale_deployment = copy.deepcopy(_valid_bundle())
    stale_deployment["courier_api_deployment_receipt"][
        "observed_at_utc"
    ] = "2030-01-02T02:49:04Z"
    stale_result = audit.analyze(
        _snapshot(*_safe_listeners(), guard_fingerprints=dict(_HASHES)),
        stale_deployment,
    )
    assert stale_result["verdict"] == "HOLD"
    assert "COURIER_API_DEPLOYMENT_RECEIPT_STALE" in stale_result["findings"]

    stale_provider = copy.deepcopy(_valid_bundle())
    stale_provider["provider_proof"]["captured_at_utc"] = "2030-01-02T02:49:04Z"
    stale_provider["provider_proof"]["valid_until_utc"] = "2030-01-02T04:00:00Z"
    validation = audit.validate_remediation_bundle(
        stale_provider,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "PROVIDER_PROOF_STALE" in validation.reason_codes

    future_provider = copy.deepcopy(_valid_bundle())
    future_provider["provider_proof"]["captured_at_utc"] = "2030-01-02T03:04:36Z"
    validation = audit.validate_remediation_bundle(
        future_provider,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "PROVIDER_PROOF_FUTURE_SKEW_EXCEEDED" in validation.reason_codes

    mutually_skewed = copy.deepcopy(_valid_bundle())
    mutually_skewed["credential_receipt"]["observed_at_utc"] = "2030-01-02T02:59:04Z"
    validation = audit.validate_remediation_bundle(
        mutually_skewed,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "EVIDENCE_MUTUAL_TIME_SKEW_EXCEEDED" in validation.reason_codes
    assert "CREDENTIAL_RECEIPT_STALE" not in validation.reason_codes

    mismatched_observation = copy.deepcopy(_valid_bundle())
    mismatched_observation["credential_receipt"]["observation_id"] = "OBS-OTHER"
    validation = audit.validate_remediation_bundle(
        mismatched_observation,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "CREDENTIAL_RECEIPT_OBSERVATION_ID_MISMATCH" in validation.reason_codes

    mismatched_source_observation = copy.deepcopy(_valid_bundle())
    mismatched_source_observation["source_contract"]["observation_id"] = "OBS-OTHER"
    validation = audit.validate_remediation_bundle(
        mismatched_source_observation,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "SOURCE_CONTRACT_OBSERVATION_ID_MISMATCH" in validation.reason_codes

    mismatched_deployment_observation = copy.deepcopy(_valid_bundle())
    mismatched_deployment_observation["courier_api_deployment_receipt"][
        "observation_id"
    ] = "OBS-OTHER"
    validation = audit.validate_remediation_bundle(
        mismatched_deployment_observation,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert (
        "COURIER_API_DEPLOYMENT_RECEIPT_OBSERVATION_ID_MISMATCH"
        in validation.reason_codes
    )

    mismatched_deployment_producer = copy.deepcopy(_valid_bundle())
    mismatched_deployment_producer["courier_api_deployment_receipt"]["producer"][
        "source_commit"
    ] = "e" * 40
    validation = audit.validate_remediation_bundle(
        mismatched_deployment_producer,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert (
        "COURIER_API_DEPLOYMENT_RECEIPT_PRODUCER_COMMIT_MISMATCH"
        in validation.reason_codes
    )

    weakened_policy = copy.deepcopy(_valid_bundle())
    weakened_policy["time_policy"]["max_age_seconds"] = 86400
    validation = audit.validate_remediation_bundle(
        weakened_policy,
        observed_at_utc="2030-01-02T03:04:05Z",
    )
    assert "EVIDENCE_TIME_POLICY_MAX_AGE_INVALID" in validation.reason_codes

    boundary_age = copy.deepcopy(_valid_bundle())
    boundary_age["provider_proof"]["captured_at_utc"] = "2030-01-02T02:49:05Z"
    for section, _prefix in timed_sections:
        boundary_age[section]["observed_at_utc"] = "2030-01-02T02:49:05Z"
    assert audit.validate_remediation_bundle(
        boundary_age,
        observed_at_utc="2030-01-02T03:04:05Z",
    ).valid

    boundary_future = copy.deepcopy(_valid_bundle())
    boundary_future["provider_proof"]["captured_at_utc"] = "2030-01-02T03:04:35Z"
    for section, _prefix in timed_sections:
        boundary_future[section]["observed_at_utc"] = "2030-01-02T03:04:35Z"
    assert audit.validate_remediation_bundle(
        boundary_future,
        observed_at_utc="2030-01-02T03:04:05Z",
    ).valid

    boundary_mutual = copy.deepcopy(_valid_bundle())
    boundary_mutual["credential_receipt"]["observed_at_utc"] = "2030-01-02T02:59:05Z"
    assert audit.validate_remediation_bundle(
        boundary_mutual,
        observed_at_utc="2030-01-02T03:04:05Z",
    ).valid

    hostile_shapes = (
        None,
        {},
        {
            "schema": audit.EVIDENCE_SCHEMA,
            "change_id": _CHANGE_ID,
            "source_contract": [],
            "provider_proof": "raw",
            "external_probes": 7,
            "host_rules_receipt": None,
            "credential_receipt": {"metadata": []},
            "rollback_preconditions": [],
        },
    )
    for hostile in hostile_shapes:
        validation = audit.validate_remediation_bundle(
            hostile,
            observed_at_utc="2030-01-02T03:04:05Z",
        )
        assert validation.valid is False
        assert validation.reason_codes
