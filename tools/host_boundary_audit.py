#!/usr/bin/env python3
"""Read-only, redacted audit of the A360 host boundary.

Only a fixed command allowlist is used. Raw command output is parsed in memory
and never included in the JSON result.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA = "a360.host-boundary-audit.v1"
EVIDENCE_SCHEMA = "a360.sec1.evidence-bundle.v1"
EVIDENCE_VALIDATION_SCHEMA = "a360.sec1.evidence-validation.v1"
SOURCE_CONTRACT_SCHEMA = "a360.sec1.source-contract.v1"
PROVIDER_PROOF_SCHEMA = "a360.sec1.provider-proof.v1"
PROBE_PROOF_SCHEMA = "a360.sec1.external-probes.v1"
HOST_RECEIPT_SCHEMA = "a360.sec1.host-rules-receipt.v1"
CREDENTIAL_RECEIPT_SCHEMA = "a360.sec1.credential-receipt.v1"
TARGET_PORTS = (8767, 9222)
EXPECTED_UNIT = "courier-api.service"
EXPECTED_CONTAINER = "openclaw-browser"
SAFE_CHILD_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
EVIDENCE_FILENAME = "A360_SEC1_EVIDENCE.json"
MAX_EVIDENCE_BYTES = 128 * 1024

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CHANGE_ID = re.compile(r"^A360-SEC1-[A-Z0-9][A-Z0-9._-]{2,63}$")
_IMAGE_DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")

_EXPECTED_FIREWALL_RULES: Mapping[str, tuple[str, ...]] = {
    "input": (
        "RETURN_IF_LOOPBACK",
        "DROP_NEW_TCP_DPORTS_8767_9222",
        "RETURN",
    ),
    "docker_user": (
        "RETURN_IF_LOOPBACK",
        "DROP_NEW_TCP_DPORT_9222",
        "RETURN",
    ),
}

_PROHIBITED_EVIDENCE_KEYS = frozenset(
    {
        "argv",
        "cmdline",
        "command_line",
        "environment",
        "environmentfile",
        "password",
        "private_key",
        "secret",
        "token",
        "credential_value",
    }
)

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
    guard_fingerprints: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceValidation:
    reason_codes: tuple[str, ...]
    source_contract_valid: bool
    provider_proof_valid: bool
    external_probes_valid: bool
    host_receipt_valid: bool
    credential_receipt_valid: bool
    rollback_preconditions_valid: bool

    @property
    def valid(self) -> bool:
        return not self.reason_codes


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


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _fields_are_allowed(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str] | None = None,
) -> bool:
    keys = set(value)
    return required.issubset(keys) and keys.issubset(required | (optional or set()))


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX_64.fullmatch(value))


def _valid_commit(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX_40.fullmatch(value))


def _parse_utc(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _has_prohibited_evidence_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {
                re.sub(r"[^a-z0-9]", "", item) for item in _PROHIBITED_EVIDENCE_KEYS
            }:
                return True
            if _has_prohibited_evidence_key(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_has_prohibited_evidence_key(item) for item in value)
    return False


def metadata_only(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return lstat-only metadata without opening or naming the target."""

    try:
        info = os.lstat(path)
    except OSError:
        return {"exists": False}
    return {
        "exists": True,
        "regular": stat.S_ISREG(info.st_mode),
        "symlink": stat.S_ISLNK(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "nlink": info.st_nlink,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def validate_credential_metadata(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    codes: set[str] = set()
    if not _fields_are_allowed(
        metadata,
        {"exists", "regular", "symlink", "uid", "gid", "mode", "nlink", "size", "mtime_ns"},
    ):
        codes.add("CREDENTIAL_REVISION_METADATA_FIELDS_INVALID")
    if metadata.get("exists") is not True:
        codes.add("CREDENTIAL_REVISION_MISSING")
    if metadata.get("regular") is not True or metadata.get("symlink") is not False:
        codes.add("CREDENTIAL_REVISION_NOT_REGULAR_NOFOLLOW")
    if metadata.get("uid") != 0 or metadata.get("gid") != 0:
        codes.add("CREDENTIAL_REVISION_OWNER_INVALID")
    if metadata.get("mode") != 0o600:
        codes.add("CREDENTIAL_REVISION_MODE_INVALID")
    if metadata.get("nlink") != 1:
        codes.add("CREDENTIAL_REVISION_LINK_COUNT_INVALID")
    size = metadata.get("size")
    if not isinstance(size, int) or size <= 0:
        codes.add("CREDENTIAL_REVISION_EMPTY_OR_SIZE_INVALID")
    if not isinstance(metadata.get("mtime_ns"), int):
        codes.add("CREDENTIAL_REVISION_MTIME_MISSING")
    return tuple(sorted(codes))


def load_evidence_bundle(path: str | os.PathLike[str]) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    """Load one classified, root-only proof file without following links."""

    evidence_path = Path(path)
    codes: set[str] = set()
    if evidence_path.name != EVIDENCE_FILENAME:
        return None, ("EVIDENCE_FILENAME_INVALID",)

    metadata = metadata_only(evidence_path)
    if metadata.get("exists") is not True:
        return None, ("EVIDENCE_FILE_MISSING",)
    if metadata.get("regular") is not True or metadata.get("symlink") is not False:
        codes.add("EVIDENCE_FILE_NOT_REGULAR_NOFOLLOW")
    if metadata.get("uid") != 0 or metadata.get("gid") != 0:
        codes.add("EVIDENCE_FILE_OWNER_INVALID")
    if metadata.get("mode") not in {0o400, 0o600}:
        codes.add("EVIDENCE_FILE_MODE_INVALID")
    if metadata.get("nlink") != 1:
        codes.add("EVIDENCE_FILE_LINK_COUNT_INVALID")
    size = metadata.get("size")
    if not isinstance(size, int) or not 0 < size <= MAX_EVIDENCE_BYTES:
        codes.add("EVIDENCE_FILE_SIZE_INVALID")
    if codes:
        return None, tuple(sorted(codes))

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(evidence_path, flags)
    except OSError:
        return None, ("EVIDENCE_FILE_OPEN_FAILED",)
    try:
        opened = os.fstat(descriptor)
        current = os.lstat(evidence_path)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            return None, ("EVIDENCE_FILE_REPLACED_DURING_OPEN",)
        opened_codes: set[str] = set()
        if not stat.S_ISREG(opened.st_mode):
            opened_codes.add("EVIDENCE_FILE_NOT_REGULAR_NOFOLLOW")
        if opened.st_uid != 0 or opened.st_gid != 0:
            opened_codes.add("EVIDENCE_FILE_OWNER_INVALID")
        if stat.S_IMODE(opened.st_mode) not in {0o400, 0o600}:
            opened_codes.add("EVIDENCE_FILE_MODE_INVALID")
        if opened.st_nlink != 1:
            opened_codes.add("EVIDENCE_FILE_LINK_COUNT_INVALID")
        if not 0 < opened.st_size <= MAX_EVIDENCE_BYTES:
            opened_codes.add("EVIDENCE_FILE_SIZE_INVALID")
        if opened_codes:
            return None, tuple(sorted(opened_codes))
        payload = bytearray()
        while len(payload) <= MAX_EVIDENCE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_EVIDENCE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
    except OSError:
        return None, ("EVIDENCE_FILE_READ_FAILED",)
    finally:
        os.close(descriptor)

    if len(payload) > MAX_EVIDENCE_BYTES:
        return None, ("EVIDENCE_FILE_SIZE_INVALID",)
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, ("EVIDENCE_JSON_INVALID",)
    if not isinstance(decoded, Mapping):
        return None, ("EVIDENCE_JSON_ROOT_INVALID",)
    return decoded, ()


def _validate_firewall_plan(plan: Mapping[str, Any]) -> set[str]:
    codes: set[str] = set()
    if not _fields_are_allowed(
        plan,
        {"schema", "idempotency", "families", "persistent_owner"},
        {"status", "invariants", "apply_available_in_this_artifact"},
    ):
        codes.add("FIREWALL_PLAN_FIELDS_INVALID")
    if plan.get("schema") != "a360.sec1.host-firewall-plan.v1":
        codes.add("FIREWALL_PLAN_SCHEMA_INVALID")
    if plan.get("idempotency") != "RECONCILE_SINGLE_JUMP_AND_CHAIN":
        codes.add("FIREWALL_PLAN_IDEMPOTENCY_INVALID")
    families = _as_mapping(plan.get("families"))
    if set(families) != {"ipv4", "ipv6"}:
        codes.add("FIREWALL_PLAN_FAMILY_SET_INVALID")
    for family in ("ipv4", "ipv6"):
        family_plan = _as_mapping(families.get(family))
        if not _fields_are_allowed(family_plan, {"input", "docker_user"}):
            codes.add(f"FIREWALL_PLAN_{family.upper()}_FIELDS_INVALID")
        for chain_key, expected_rules in _EXPECTED_FIREWALL_RULES.items():
            chain = _as_mapping(family_plan.get(chain_key))
            expected_name = (
                "A360_SEC1_INPUT" if chain_key == "input" else "A360_SEC1_DOCKER_USER"
            )
            rules = chain.get("rules")
            if (
                not _fields_are_allowed(chain, {"anchor_position", "chain_name", "rules"})
                or chain.get("anchor_position") != 1
                or chain.get("chain_name") != expected_name
                or not isinstance(rules, list)
                or tuple(rules) != expected_rules
            ):
                codes.add(f"FIREWALL_PLAN_{family.upper()}_{chain_key.upper()}_ORDER_INVALID")
    owner = _as_mapping(plan.get("persistent_owner"))
    if not _fields_are_allowed(owner, {"kind", "source_commit", "artifact_sha256"}):
        codes.add("FIREWALL_PLAN_PERSISTENT_OWNER_FIELDS_INVALID")
    if owner.get("kind") != "VERSIONED_PROVISIONER":
        codes.add("FIREWALL_PLAN_PERSISTENT_OWNER_INVALID")
    if not _valid_commit(owner.get("source_commit")):
        codes.add("FIREWALL_PLAN_OWNER_COMMIT_INVALID")
    if not _valid_sha256(owner.get("artifact_sha256")):
        codes.add("FIREWALL_PLAN_OWNER_HASH_INVALID")
    return codes


def _validate_source_contract(contract: Mapping[str, Any], change_id: str) -> set[str]:
    codes: set[str] = set()
    if not _fields_are_allowed(
        contract,
        {
            "schema",
            "change_id",
            "targets",
            "courier_api",
            "browser_container",
            "host_firewall",
            "credential_plan",
            "rollback_policy",
        },
    ):
        codes.add("SOURCE_CONTRACT_FIELDS_INVALID")
    if contract.get("schema") != SOURCE_CONTRACT_SCHEMA:
        codes.add("SOURCE_CONTRACT_SCHEMA_INVALID")
    if contract.get("change_id") != change_id:
        codes.add("SOURCE_CONTRACT_CHANGE_ID_MISMATCH")

    targets = _as_mapping(contract.get("targets"))
    if set(targets) != {"8767", "9222"}:
        codes.add("SOURCE_TARGET_SET_INVALID")
    expected_targets = {
        "8767": (EXPECTED_UNIT, ["LOOPBACK_V4"]),
        "9222": (EXPECTED_CONTAINER, ["LOOPBACK_V4"]),
    }
    for port, (owner, binds) in expected_targets.items():
        target = _as_mapping(targets.get(port))
        if not _fields_are_allowed(target, {"owner", "bind_classes"}):
            codes.add(f"SOURCE_TARGET_FIELDS_{port}_INVALID")
        if target.get("owner") != owner:
            codes.add(f"SOURCE_TARGET_OWNER_{port}_INVALID")
        if target.get("bind_classes") != binds:
            codes.add(f"SOURCE_TARGET_BIND_{port}_INVALID")

    courier = _as_mapping(contract.get("courier_api"))
    if not _fields_are_allowed(
        courier,
        {
            "repository_id",
            "source_commit",
            "config_sha256",
            "main_sha256",
            "unit_owner",
            "bind_policy",
            "credential_loader",
            "process_fallback_enabled",
        },
        {"preimage_source_commit"},
    ):
        codes.add("COURIER_API_SOURCE_FIELDS_INVALID")
    if courier.get("repository_id") != "courier_api":
        codes.add("COURIER_API_REPOSITORY_UNPROVEN")
    if not _valid_commit(courier.get("source_commit")):
        codes.add("COURIER_API_SOURCE_COMMIT_INVALID")
    if not _valid_sha256(courier.get("config_sha256")):
        codes.add("COURIER_API_CONFIG_HASH_INVALID")
    if not _valid_sha256(courier.get("main_sha256")):
        codes.add("COURIER_API_MAIN_HASH_INVALID")
    if courier.get("unit_owner") != EXPECTED_UNIT:
        codes.add("COURIER_API_UNIT_OWNER_INVALID")
    if courier.get("bind_policy") != "LOOPBACK_ONLY":
        codes.add("COURIER_API_BIND_POLICY_INVALID")
    if courier.get("credential_loader") != "SYSTEMD_LOAD_CREDENTIAL":
        codes.add("COURIER_API_CREDENTIAL_LOADER_INVALID")
    if courier.get("process_fallback_enabled") is not False:
        codes.add("COURIER_API_PROCESS_FALLBACK_NOT_DISABLED")

    browser = _as_mapping(contract.get("browser_container"))
    if not _fields_are_allowed(
        browser,
        {
            "container_name",
            "manifest_repository",
            "manifest_path",
            "source_commit",
            "manifest_sha256",
            "image_reference",
            "publish_bind_classes",
        },
    ):
        codes.add("BROWSER_MANIFEST_FIELDS_INVALID")
    if browser.get("container_name") != EXPECTED_CONTAINER:
        codes.add("BROWSER_CONTAINER_OWNER_INVALID")
    repository = browser.get("manifest_repository")
    if not isinstance(repository, str) or not repository or repository.upper() == "UNKNOWN":
        codes.add("BROWSER_MANIFEST_REPOSITORY_UNPROVEN")
    manifest_path = browser.get("manifest_path")
    if (
        not isinstance(manifest_path, str)
        or not manifest_path
        or Path(manifest_path).is_absolute()
        or ".." in Path(manifest_path).parts
    ):
        codes.add("BROWSER_MANIFEST_PATH_INVALID")
    if not _valid_commit(browser.get("source_commit")):
        codes.add("BROWSER_MANIFEST_SOURCE_COMMIT_INVALID")
    if not _valid_sha256(browser.get("manifest_sha256")):
        codes.add("BROWSER_MANIFEST_HASH_INVALID")
    image_reference = browser.get("image_reference")
    if not isinstance(image_reference, str) or not _IMAGE_DIGEST.fullmatch(image_reference):
        codes.add("BROWSER_IMAGE_DIGEST_UNPINNED")
    if browser.get("publish_bind_classes") != ["LOOPBACK_V4"]:
        codes.add("BROWSER_PUBLISH_BIND_POLICY_INVALID")

    codes.update(_validate_firewall_plan(_as_mapping(contract.get("host_firewall"))))

    credential = _as_mapping(contract.get("credential_plan"))
    expected_credential = {
        "logical_name": "COURIER_ADMIN_PASS",
        "loader": "SYSTEMD_LOAD_CREDENTIAL",
        "required_uid": 0,
        "required_gid": 0,
        "required_mode": 0o600,
        "nofollow": True,
        "single_link": True,
        "process_fallback_enabled": False,
        "rotation": "NEW_REVISION_ONLY",
    }
    if not _fields_are_allowed(credential, set(expected_credential)):
        codes.add("CREDENTIAL_PLAN_FIELDS_INVALID")
    for key, expected in expected_credential.items():
        if credential.get(key) != expected:
            codes.add(f"CREDENTIAL_PLAN_{key.upper()}_INVALID")

    rollback = _as_mapping(contract.get("rollback_policy"))
    required_rollback = {
        "preserve_provider_deny": True,
        "preserve_host_deny": True,
        "preserve_loopback_bind": True,
        "require_second_admin_session": True,
        "credential_strategy": "NEXT_NEW_REVISION_ONLY",
        "container_strategy": "PINNED_PREVIOUS_DIGEST_LOOPBACK_ONLY",
        "network_strategy": "PRESERVE_DENY_AND_LOOPBACK",
    }
    if not _fields_are_allowed(rollback, set(required_rollback)):
        codes.add("ROLLBACK_POLICY_FIELDS_INVALID")
    for key, expected in required_rollback.items():
        if rollback.get(key) != expected:
            codes.add(f"ROLLBACK_POLICY_{key.upper()}_INVALID")
    return codes


def _validate_provider_proof(proof: Mapping[str, Any], change_id: str, now: dt.datetime) -> set[str]:
    codes: set[str] = set()
    if not _fields_are_allowed(
        proof,
        {
            "schema",
            "change_id",
            "provider",
            "attachment_state",
            "ruleset_sha256",
            "denied_tcp_ports_ipv4",
            "denied_tcp_ports_ipv6",
            "captured_at_utc",
            "valid_until_utc",
        },
    ):
        codes.add("PROVIDER_PROOF_FIELDS_INVALID")
    if proof.get("schema") != PROVIDER_PROOF_SCHEMA:
        codes.add("PROVIDER_PROOF_SCHEMA_INVALID")
    if proof.get("change_id") != change_id:
        codes.add("PROVIDER_PROOF_CHANGE_ID_MISMATCH")
    provider = proof.get("provider")
    if not isinstance(provider, str) or not provider or provider.upper() == "UNKNOWN":
        codes.add("PROVIDER_PROOF_IDENTITY_UNKNOWN")
    if proof.get("attachment_state") != "ATTACHED_TO_CURRENT_HOST":
        codes.add("PROVIDER_PROOF_ATTACHMENT_UNPROVEN")
    if not _valid_sha256(proof.get("ruleset_sha256")):
        codes.add("PROVIDER_PROOF_RULESET_HASH_INVALID")
    if proof.get("denied_tcp_ports_ipv4") != [8767, 9222]:
        codes.add("PROVIDER_PROOF_IPV4_DENY_MISSING")
    if proof.get("denied_tcp_ports_ipv6") != [8767, 9222]:
        codes.add("PROVIDER_PROOF_IPV6_DENY_MISSING")
    captured = _parse_utc(proof.get("captured_at_utc"))
    valid_until = _parse_utc(proof.get("valid_until_utc"))
    if captured is None or valid_until is None or not captured <= now <= valid_until:
        codes.add("PROVIDER_PROOF_TIME_WINDOW_INVALID")
    return codes


def _validate_external_probes(proof: Mapping[str, Any], change_id: str) -> set[str]:
    codes: set[str] = set()
    if not _fields_are_allowed(
        proof,
        {"schema", "change_id", "independent_vantage", "observed_at_utc", "results"},
    ):
        codes.add("EXTERNAL_PROBE_FIELDS_INVALID")
    if proof.get("schema") != PROBE_PROOF_SCHEMA:
        codes.add("EXTERNAL_PROBE_SCHEMA_INVALID")
    if proof.get("change_id") != change_id:
        codes.add("EXTERNAL_PROBE_CHANGE_ID_MISMATCH")
    if proof.get("independent_vantage") is not True:
        codes.add("EXTERNAL_PROBE_VANTAGE_NOT_INDEPENDENT")
    if _parse_utc(proof.get("observed_at_utc")) is None:
        codes.add("EXTERNAL_PROBE_TIMESTAMP_INVALID")
    expected = {
        "direct_8767_ipv4": "DENIED",
        "direct_8767_ipv6": "DENIED",
        "direct_9222_ipv4": "DENIED",
        "direct_9222_ipv6": "DENIED",
        "canonical_https": "ALLOWED",
        "local_api_health": "ALLOWED",
        "cdp_via_tunnel": "ALLOWED",
    }
    results = _as_mapping(proof.get("results"))
    if set(results) != set(expected):
        codes.add("EXTERNAL_PROBE_RESULT_SET_INVALID")
    for probe_id, expected_status in expected.items():
        if results.get(probe_id) != expected_status:
            codes.add(f"EXTERNAL_PROBE_{probe_id.upper()}_INVALID")
    return codes


def _validate_host_receipt(receipt: Mapping[str, Any], change_id: str) -> set[str]:
    codes: set[str] = set()
    if not _fields_are_allowed(
        receipt,
        {
            "schema",
            "change_id",
            "observed_at_utc",
            "live_ruleset_sha256",
            "ordered_checks",
            "persistent_owner",
        },
    ):
        codes.add("HOST_RULE_RECEIPT_FIELDS_INVALID")
    if receipt.get("schema") != HOST_RECEIPT_SCHEMA:
        codes.add("HOST_RULE_RECEIPT_SCHEMA_INVALID")
    if receipt.get("change_id") != change_id:
        codes.add("HOST_RULE_RECEIPT_CHANGE_ID_MISMATCH")
    if _parse_utc(receipt.get("observed_at_utc")) is None:
        codes.add("HOST_RULE_RECEIPT_TIMESTAMP_INVALID")
    hashes = _as_mapping(receipt.get("live_ruleset_sha256"))
    if set(hashes) != {"input_v4", "input_v6", "docker_user_v4", "docker_user_v6"}:
        codes.add("HOST_RULE_RECEIPT_HASH_SET_INVALID")
    elif any(not _valid_sha256(value) for value in hashes.values()):
        codes.add("HOST_RULE_RECEIPT_HASH_INVALID")
    checks = _as_mapping(receipt.get("ordered_checks"))
    expected_checks = {"input_v4", "input_v6", "docker_user_v4", "docker_user_v6"}
    if set(checks) != expected_checks or any(checks.get(key) != "PASS" for key in expected_checks):
        codes.add("HOST_RULE_RECEIPT_ORDER_UNPROVEN")
    owner = _as_mapping(receipt.get("persistent_owner"))
    if not _fields_are_allowed(owner, {"kind", "source_commit", "artifact_sha256"}):
        codes.add("HOST_RULE_RECEIPT_OWNER_FIELDS_INVALID")
    if owner.get("kind") != "VERSIONED_PROVISIONER":
        codes.add("HOST_RULE_RECEIPT_OWNER_INVALID")
    if not _valid_commit(owner.get("source_commit")):
        codes.add("HOST_RULE_RECEIPT_OWNER_COMMIT_INVALID")
    if not _valid_sha256(owner.get("artifact_sha256")):
        codes.add("HOST_RULE_RECEIPT_OWNER_HASH_INVALID")
    return codes


def _validate_credential_receipt(receipt: Mapping[str, Any], change_id: str) -> set[str]:
    codes: set[str] = set()
    if not _fields_are_allowed(
        receipt,
        {
            "schema",
            "change_id",
            "observed_at_utc",
            "metadata",
            "new_revision_probe",
            "previous_revision_probe",
            "old_revision_restored",
        },
    ):
        codes.add("CREDENTIAL_RECEIPT_FIELDS_INVALID")
    if receipt.get("schema") != CREDENTIAL_RECEIPT_SCHEMA:
        codes.add("CREDENTIAL_RECEIPT_SCHEMA_INVALID")
    if receipt.get("change_id") != change_id:
        codes.add("CREDENTIAL_RECEIPT_CHANGE_ID_MISMATCH")
    if _parse_utc(receipt.get("observed_at_utc")) is None:
        codes.add("CREDENTIAL_RECEIPT_TIMESTAMP_INVALID")
    codes.update(validate_credential_metadata(_as_mapping(receipt.get("metadata"))))
    if receipt.get("new_revision_probe") != "PASS":
        codes.add("CREDENTIAL_NEW_REVISION_PROBE_FAILED")
    if receipt.get("previous_revision_probe") != "REJECTED":
        codes.add("CREDENTIAL_PREVIOUS_REVISION_NOT_REJECTED")
    if receipt.get("old_revision_restored") is not False:
        codes.add("CREDENTIAL_OLD_REVISION_RESTORED_OR_UNKNOWN")
    return codes


def _validate_rollback_preconditions(preconditions: Mapping[str, Any], change_id: str) -> set[str]:
    codes: set[str] = set()
    if preconditions.get("schema") != "a360.sec1.rollback-preconditions.v1":
        codes.add("ROLLBACK_PRECONDITIONS_SCHEMA_INVALID")
    if preconditions.get("change_id") != change_id:
        codes.add("ROLLBACK_PRECONDITIONS_CHANGE_ID_MISMATCH")
    required_true = (
        "second_admin_session_active",
        "host_denies_verified",
        "provider_denies_verified",
        "loopback_binds_verified",
        "next_new_credential_revision_prepared",
        "previous_credential_restore_forbidden",
        "previous_image_digest_pinned",
    )
    if not _fields_are_allowed(
        preconditions,
        {"schema", "change_id", *required_true},
    ):
        codes.add("ROLLBACK_PRECONDITIONS_FIELDS_INVALID")
    for key in required_true:
        if preconditions.get(key) is not True:
            codes.add(f"ROLLBACK_PRECONDITION_{key.upper()}_MISSING")
    return codes


def validate_remediation_bundle(
    bundle: Mapping[str, Any] | None,
    *,
    observed_at_utc: str,
) -> EvidenceValidation:
    now = _parse_utc(observed_at_utc) or _utc_now()
    if bundle is None:
        missing = (
            "CREDENTIAL_RECEIPT_NOT_SUPPLIED",
            "EXTERNAL_PROBE_PROOF_NOT_SUPPLIED",
            "HOST_RULE_RECEIPT_NOT_SUPPLIED",
            "PROVIDER_PROOF_NOT_SUPPLIED",
            "ROLLBACK_PRECONDITIONS_NOT_SUPPLIED",
            "SOURCE_CONTRACT_NOT_SUPPLIED",
        )
        return EvidenceValidation(missing, False, False, False, False, False, False)

    base_codes: set[str] = set()
    if not _fields_are_allowed(
        bundle,
        {
            "schema",
            "change_id",
            "source_contract",
            "provider_proof",
            "external_probes",
            "host_rules_receipt",
            "credential_receipt",
            "rollback_preconditions",
        },
    ):
        base_codes.add("EVIDENCE_FIELDS_INVALID")
    if bundle.get("schema") != EVIDENCE_SCHEMA:
        base_codes.add("EVIDENCE_SCHEMA_INVALID")
    change_id_raw = bundle.get("change_id")
    change_id = change_id_raw if isinstance(change_id_raw, str) else ""
    if not _CHANGE_ID.fullmatch(change_id):
        base_codes.add("EVIDENCE_CHANGE_ID_INVALID")
    if _has_prohibited_evidence_key(bundle):
        base_codes.add("EVIDENCE_PROHIBITED_FIELD_PRESENT")

    source_codes = _validate_source_contract(_as_mapping(bundle.get("source_contract")), change_id)
    provider_codes = _validate_provider_proof(
        _as_mapping(bundle.get("provider_proof")), change_id, now
    )
    probe_codes = _validate_external_probes(
        _as_mapping(bundle.get("external_probes")), change_id
    )
    host_codes = _validate_host_receipt(
        _as_mapping(bundle.get("host_rules_receipt")), change_id
    )
    credential_codes = _validate_credential_receipt(
        _as_mapping(bundle.get("credential_receipt")), change_id
    )
    rollback_codes = _validate_rollback_preconditions(
        _as_mapping(bundle.get("rollback_preconditions")), change_id
    )
    all_codes = (
        base_codes
        | source_codes
        | provider_codes
        | probe_codes
        | host_codes
        | credential_codes
        | rollback_codes
    )
    base_valid = not base_codes
    return EvidenceValidation(
        tuple(sorted(all_codes)),
        base_valid and not source_codes,
        base_valid and not provider_codes,
        base_valid and not probe_codes,
        base_valid and not host_codes,
        base_valid and not credential_codes,
        base_valid and not rollback_codes,
    )


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

    guard_commands = {
        "input_v4": COMMAND_ALLOWLIST[4],
        "input_v6": COMMAND_ALLOWLIST[5],
        "docker_user_v4": COMMAND_ALLOWLIST[6],
        "docker_user_v6": COMMAND_ALLOWLIST[7],
    }
    guard_fingerprints = {
        name: hashlib.sha256(outputs[command].encode("utf-8")).hexdigest()
        for name, command in guard_commands.items()
        if outputs[command]
    }

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
        guard_fingerprints=guard_fingerprints,
    )


def analyze(
    snapshot: RuntimeSnapshot,
    evidence_bundle: Mapping[str, Any] | None = None,
    *,
    evidence_load_errors: Sequence[str] = (),
) -> dict[str, Any]:
    validation = validate_remediation_bundle(
        evidence_bundle,
        observed_at_utc=snapshot.observed_at_utc,
    )
    validation_codes = set(validation.reason_codes) | set(evidence_load_errors)
    host_receipt_valid = validation.host_receipt_valid
    if host_receipt_valid:
        receipt_hashes = _as_mapping(
            _as_mapping(evidence_bundle).get("host_rules_receipt")
        ).get("live_ruleset_sha256")
        if dict(_as_mapping(receipt_hashes)) != dict(snapshot.guard_fingerprints):
            validation_codes.add("HOST_RULE_RECEIPT_RUNTIME_MISMATCH")
            host_receipt_valid = False

    findings: set[str] = set(snapshot.source_errors)
    if evidence_bundle is not None or evidence_load_errors:
        findings.update(validation_codes)
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
        elif not host_receipt_valid:
            # A local line parser cannot prove rule order, predicates, jumps, or
            # the effective packet path. A matching root-only receipt plus
            # independent probes is required before seen rules become proof.
            findings.add(f"HOST_GUARD_{name.upper()}_EFFECTIVENESS_NOT_PROVEN")

    if not validation.provider_proof_valid:
        # Local evidence is intentionally insufficient to prove a provider control.
        findings.add("PROVIDER_FIREWALL_UNKNOWN")

    evidence_valid = (
        validation.valid
        and not evidence_load_errors
        and host_receipt_valid
        and "HOST_RULE_RECEIPT_RUNTIME_MISMATCH" not in validation_codes
    )
    ordered_findings = sorted(findings)
    return {
        "schema": SCHEMA,
        "phase": "POST_MAINTENANCE_EVIDENCE" if evidence_bundle is not None else "SOURCE_PREP",
        "mutations_performed": False,
        "observed_at_utc": snapshot.observed_at_utc,
        "verdict": "PASS" if evidence_valid and not ordered_findings else "HOLD",
        "listeners": listener_results,
        "host_firewall": firewall,
        "provider_firewall": (
            {"status": "PROVEN", "reason": "VALID_EXTERNAL_PROOF"}
            if validation.provider_proof_valid
            else {
                "status": "UNKNOWN",
                "reason": (
                    "INVALID_EXTERNAL_PROOF"
                    if evidence_bundle is not None or evidence_load_errors
                    else "NO_EXTERNAL_PROOF"
                ),
            }
        ),
        "remediation_evidence": {
            "status": "VALID" if evidence_valid else "INVALID_OR_INCOMPLETE",
            "reason_codes": sorted(validation_codes),
            "source_contract": "VALID" if validation.source_contract_valid else "INVALID",
            "provider_proof": "VALID" if validation.provider_proof_valid else "INVALID",
            "external_probes": "VALID" if validation.external_probes_valid else "INVALID",
            "host_rules_receipt": "VALID" if host_receipt_valid else "INVALID",
            "credential_receipt": (
                "VALID" if validation.credential_receipt_valid else "INVALID"
            ),
            "rollback_preconditions": (
                "VALID" if validation.rollback_preconditions_valid else "INVALID"
            ),
        },
        "findings": ordered_findings,
        "prohibited_sources_used": False,
    }


def render(result: Mapping[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments == ("--help",):
        print(
            "usage: host_boundary_audit.py --live "
            "[--evidence A360_SEC1_EVIDENCE.json] | "
            "--validate-evidence A360_SEC1_EVIDENCE.json"
        )
        return 0
    if arguments == ("--live",):
        result = analyze(collect_live())
        print(render(result))
        return 0 if result["verdict"] == "PASS" else 2
    if len(arguments) == 3 and arguments[:2] == ("--live", "--evidence"):
        bundle, load_errors = load_evidence_bundle(arguments[2])
        result = analyze(collect_live(), bundle, evidence_load_errors=load_errors)
        print(render(result))
        return 0 if result["verdict"] == "PASS" else 2
    if len(arguments) == 2 and arguments[0] == "--validate-evidence":
        bundle, load_errors = load_evidence_bundle(arguments[1])
        validation = validate_remediation_bundle(
            bundle,
            observed_at_utc=_utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )
        codes = sorted(set(validation.reason_codes) | set(load_errors))
        print(
            render(
                {
                    "schema": EVIDENCE_VALIDATION_SCHEMA,
                    "mutations_performed": False,
                    "status": "VALID" if not codes else "INVALID_OR_INCOMPLETE",
                    "reason_codes": codes,
                }
            )
        )
        return 0 if not codes else 2
    print(render({"schema": SCHEMA, "verdict": "ERROR", "error": "INVALID_ARGUMENTS"}))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
