#!/usr/bin/env python3
"""Read-only discovery of process debt; insertion requires explicit --apply.

The collector never writes directly to SQLite. Even with ``--apply`` it calls
``GateStore.add_gate`` so schema validation and the event ledger cannot be
bypassed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from process_debt_gate import (
    DEFAULT_DB,
    GateAlreadyExists,
    GateError,
    GateStore,
    atomic_write,
    iso_utc,
    parse_timestamp,
    sha256_json,
    utc_now,
)


DEFAULT_SERVICES = (
    "dispatch-shadow.service",
    "dispatch-panel-watcher.service",
    "dispatch-plan-recheck.service",
)
_ATQ_RE = re.compile(r"^\s*(\d+)\s+")
_SLUG_RE = re.compile(r"[^a-z0-9._:-]+")
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class CollectionUnavailable(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionUnavailable(f"{path}: {exc}") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise CollectionUnavailable(f"nie można zahashować {path}: {exc}") from exc
    return digest.hexdigest()


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 30.0,
    check: bool = True,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectionUnavailable(f"{command[0]} niedostępne: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:300]
        raise CollectionUnavailable(
            f"{' '.join(command[:3])} zakończyło się {result.returncode}: {detail}"
        )
    return result


def _slug(category: str, source: str) -> str:
    stem = _SLUG_RE.sub("-", source.lower()).strip("-._:")
    stem = stem[:72] or "unknown"
    suffix = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
    return f"{category}.{stem}.{suffix}"


def _proposal(
    *,
    category: str,
    source: str,
    title: str,
    kind: str,
    owner: str,
    due_at: str,
    next_step: str,
    blocker: str,
    code_sha: str,
    evidence: Mapping[str, Any],
    opened_at: str,
    metadata: Mapping[str, Any] | None = None,
    gate_id: str | None = None,
) -> dict[str, Any]:
    return {
        "gate_id": gate_id or _slug(category, source),
        "title": title,
        "kind": kind,
        "state": "BUILT_OFF",
        "owner": owner,
        "due_at": due_at,
        "next_step": next_step,
        "blocker": blocker,
        "code_sha": code_sha.lower(),
        "evidence_hash": sha256_json(evidence),
        "opened_at": opened_at,
        "metadata": {
            "collector": "process_debt_collect/v1",
            "category": category,
            "source": source,
            **dict(metadata or {}),
        },
    }


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
    return None


def _normalize_process_flags(payload: Any) -> dict[str, dict[str, bool]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("processes"), Mapping):
        payload = payload["processes"]
    if not isinstance(payload, Mapping):
        raise CollectionUnavailable("effective flags: oczekiwano obiektu JSON")
    result: dict[str, dict[str, bool]] = {}
    for process, raw_flags in payload.items():
        if isinstance(raw_flags, Mapping) and isinstance(raw_flags.get("flags"), Mapping):
            raw_flags = raw_flags["flags"]
        if not isinstance(raw_flags, Mapping):
            continue
        flags: dict[str, bool] = {}
        for name, value in raw_flags.items():
            parsed = _bool(value)
            if parsed is not None:
                flags[str(name)] = parsed
        if flags:
            result[str(process)] = flags
    if not result:
        raise CollectionUnavailable("effective flags: brak rozpoznanych fingerprintów")
    return result


def _parse_fingerprint(text: str) -> dict[str, bool]:
    marker = text.rfind("FLAG_FINGERPRINT")
    candidate = text[marker + len("FLAG_FINGERPRINT") :] if marker >= 0 else text
    candidate = candidate.lstrip(" :=\t")
    object_start = candidate.find("{")
    if object_start >= 0:
        try:
            decoded = json.JSONDecoder().raw_decode(candidate[object_start:])[0]
            normalized = _normalize_process_flags({"process": decoded})
            return normalized["process"]
        except (json.JSONDecodeError, CollectionUnavailable, KeyError):
            pass
    result: dict[str, bool] = {}
    for name, raw in re.findall(r"\b([A-Z][A-Z0-9_]+)=(0|1|true|false|on|off)\b", candidate, re.I):
        parsed = _bool(raw)
        if parsed is not None:
            result[name] = parsed
    if not result:
        raise CollectionUnavailable("brak parsowalnego FLAG_FINGERPRINT")
    return result


def _journal_effective_flags(services: Iterable[str]) -> tuple[dict[str, dict[str, bool]], list[str]]:
    effective: dict[str, dict[str, bool]] = {}
    errors: list[str] = []
    for service in services:
        try:
            result = _run(
                [
                    "journalctl",
                    "--no-pager",
                    "--quiet",
                    "-u",
                    service,
                    "-g",
                    "FLAG_FINGERPRINT",
                    "-n",
                    "1",
                ],
                timeout=10.0,
            )
            effective[service] = _parse_fingerprint(result.stdout)
        except CollectionUnavailable as exc:
            errors.append(f"{service}: {exc}")
    if not effective:
        raise CollectionUnavailable("; ".join(errors) or "brak fingerprintów usług")
    return effective, errors


def collect_flags(
    *,
    flags_path: Path,
    effective_path: Path | None,
    evidence_path: Path | None,
    services: Sequence[str],
    owner: str,
    default_due_at: str,
    default_opened_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    configured_raw = _read_json(flags_path)
    if not isinstance(configured_raw, Mapping):
        raise CollectionUnavailable("flags.json nie jest obiektem")
    configured = {
        str(name): parsed
        for name, value in configured_raw.items()
        if (parsed := _bool(value)) is not None
    }
    if not configured:
        raise CollectionUnavailable("flags.json nie zawiera flag boolowskich")

    runtime_warnings: list[str] = []
    if effective_path:
        effective = _normalize_process_flags(_read_json(effective_path))
        runtime_source = str(effective_path)
    else:
        effective, runtime_warnings = _journal_effective_flags(services)
        runtime_source = "journalctl:FLAG_FINGERPRINT"

    if evidence_path is None:
        raise CollectionUnavailable(
            "brak --flag-evidence; nie wolno uznać flagi za zbudowane-OFF bez dowodu"
        )
    evidence_raw = _read_json(evidence_path)
    if isinstance(evidence_raw, Mapping) and isinstance(evidence_raw.get("flags"), Mapping):
        evidence_raw = evidence_raw["flags"]
    if not isinstance(evidence_raw, Mapping):
        raise CollectionUnavailable("flag evidence: oczekiwano obiektu")

    proposals: list[dict[str, Any]] = []
    compared = 0
    for flag_name, evidence_entry in sorted(evidence_raw.items()):
        if not isinstance(evidence_entry, Mapping):
            continue
        if configured.get(flag_name) is not False:
            continue
        observations = {
            process: flags[flag_name]
            for process, flags in effective.items()
            if flag_name in flags
        }
        if not observations or any(observations.values()):
            continue
        compared += 1
        code_sha = str(evidence_entry.get("code_sha", "")).lower()
        if not _SHA_RE.fullmatch(code_sha):
            runtime_warnings.append(f"{flag_name}: brak pełnego code_sha w dowodzie")
            continue
        opened_at = str(evidence_entry.get("opened_at", default_opened_at))
        due_at = str(evidence_entry.get("due_at", default_due_at))
        evidence = {
            "configured": False,
            "effective": observations,
            "manifest": evidence_entry,
            "manifest_sha256": _file_sha256(evidence_path),
            "runtime_source": runtime_source,
        }
        proposals.append(
            _proposal(
                category="flag-off",
                source=str(flag_name),
                title=str(evidence_entry.get("title", f"Flaga {flag_name} zbudowana i OFF")),
                kind="BUILT_FLAG_OFF",
                owner=str(evidence_entry.get("owner", owner)),
                due_at=due_at,
                next_step=str(
                    evidence_entry.get(
                        "next_step", "Zweryfikuj dowód i skieruj do decyzji ownera"
                    )
                ),
                blocker=str(evidence_entry.get("blocker", "Brak decyzji o promocji")),
                code_sha=code_sha,
                evidence=evidence,
                opened_at=opened_at,
                metadata={"flag": flag_name, "effective_processes": sorted(observations)},
            )
        )
    return proposals, {
        "status": "OK",
        "configured_flags": len(configured),
        "processes": sorted(effective),
        "built_off_with_evidence": compared,
        "warnings": runtime_warnings,
    }


def _git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["git", *arguments], cwd=repo, check=check)


def _git_master_sha(repo: Path, master: str) -> str:
    sha = _git(repo, "rev-parse", "--verify", f"{master}^{{commit}}").stdout.strip().lower()
    if not _SHA_RE.fullmatch(sha):
        raise CollectionUnavailable(f"{master}: niepełny SHA")
    return sha


def _real_branches(repo: Path, master: str) -> list[dict[str, Any]]:
    output = _git(
        repo,
        "for-each-ref",
        "--format=%(refname:short)%00%(objectname)%00%(committerdate:iso-strict)",
        "refs/heads",
        "refs/remotes",
    ).stdout
    branches: list[dict[str, Any]] = []
    seen_tips: set[str] = set()
    exclusions = {master, f"origin/{master}", "origin/HEAD"}
    for line in output.splitlines():
        parts = line.split("\x00")
        if len(parts) != 3:
            continue
        name, tip, committed_at = parts
        if name in exclusions or name.endswith("/HEAD") or tip in seen_tips:
            continue
        seen_tips.add(tip)
        merged = _git(repo, "merge-base", "--is-ancestor", tip, master, check=False).returncode == 0
        cherry = _git(repo, "cherry", master, tip, check=False)
        cherry_lines = [line for line in cherry.stdout.splitlines() if line[:1] in {"+", "-"}]
        patch_equivalent = bool(cherry_lines) and all(line.startswith("-") for line in cherry_lines)
        unique_patches = sum(line.startswith("+") for line in cherry_lines)
        branches.append(
            {
                "name": name,
                "tip": tip,
                "committed_at": committed_at,
                "merged": merged,
                "patch_equivalent": patch_equivalent,
                "unique_patches": unique_patches,
            }
        )
    return branches


def collect_branches(
    *,
    repo: Path,
    master: str,
    fixture: Path | None,
    owner: str,
    default_due_at: str,
    default_opened_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    if fixture:
        payload = _read_json(fixture)
        if isinstance(payload, Mapping):
            master_sha = str(payload.get("master_sha", "")).lower()
            branches = payload.get("branches", [])
        else:
            master_sha = ""
            branches = payload
        if not isinstance(branches, list):
            raise CollectionUnavailable("branches fixture: oczekiwano listy")
    else:
        master_sha = _git_master_sha(repo, master)
        branches = _real_branches(repo, master)
    if not _SHA_RE.fullmatch(master_sha):
        raise CollectionUnavailable("branches: brak pełnego SHA mastera")

    proposals: list[dict[str, Any]] = []
    unmerged = 0
    equivalent = 0
    for branch in sorted(branches, key=lambda item: str(item.get("name", ""))):
        if not isinstance(branch, Mapping) or branch.get("merged"):
            continue
        name = str(branch.get("name", ""))
        tip = str(branch.get("tip", "")).lower()
        if not name or not _SHA_RE.fullmatch(tip):
            continue
        unmerged += 1
        is_equivalent = bool(branch.get("patch_equivalent"))
        equivalent += int(is_equivalent)
        evidence = {
            "branch": name,
            "tip": tip,
            "master": master,
            "master_sha": master_sha,
            "merged": False,
            "patch_equivalent": is_equivalent,
            "unique_patches": int(branch.get("unique_patches", 0)),
        }
        if is_equivalent:
            title = f"Branch patch-equivalent do {master}: {name}"
            kind = "BRANCH_PATCH_EQUIVALENT"
            next_step = "Po review usuń zbędną referencję brancha"
            blocker = "BRAK"
        else:
            title = f"Niezmergowany branch z unikalnym patchem: {name}"
            kind = "BRANCH_UNMERGED"
            next_step = "Porównaj semantykę, następnie merge, supersede albo reject"
            blocker = "Brak decyzji integracyjnej"
        proposals.append(
            _proposal(
                category="branch",
                source=name,
                title=title,
                kind=kind,
                owner=owner,
                due_at=default_due_at,
                next_step=next_step,
                blocker=blocker,
                code_sha=tip,
                evidence=evidence,
                opened_at=str(branch.get("committed_at", default_opened_at)),
                metadata={"branch": name, "master": master, "patch_equivalent": is_equivalent},
            )
        )
    return proposals, {
        "status": "OK",
        "unmerged": unmerged,
        "patch_equivalent": equivalent,
        "source": str(fixture) if fixture else str(repo),
    }, master_sha


def _real_bundles(bundle_dir: Path, repo: Path, master: str) -> list[dict[str, Any]]:
    if not bundle_dir.is_dir():
        raise CollectionUnavailable(f"katalog bundle nie istnieje: {bundle_dir}")
    bundles: list[dict[str, Any]] = []
    # Bundle jest importowany wyłącznie do chwilowego repo. Repo docelowe jest
    # źródłem mastera/prerequisite, ale nie dostaje żadnych obiektów ani refów.
    with tempfile.TemporaryDirectory(prefix="process-debt-bundle-") as temporary:
        comparison_repo = Path(temporary) / "compare.git"
        _run(["git", "init", "--bare", str(comparison_repo)])
        master_fetch = _run(
            [
                "git",
                "--git-dir",
                str(comparison_repo),
                "fetch",
                "--no-tags",
                str(repo),
                f"{master}:refs/heads/{master}",
            ],
            check=False,
        )
        if master_fetch.returncode != 0:
            detail = (master_fetch.stderr or master_fetch.stdout).strip()[:300]
            raise CollectionUnavailable(f"nie można pobrać {master} do repo porównawczego: {detail}")
        for index, path in enumerate(sorted(bundle_dir.glob("*.bundle"))):
            heads = _run(["git", "bundle", "list-heads", str(path)], check=False)
            if heads.returncode != 0:
                bundles.append(
                    {
                        "path": str(path),
                        "head": "",
                        "error": (heads.stderr or heads.stdout).strip()[:300],
                        "bundle_sha256": _file_sha256(path),
                    }
                )
                continue
            for head_index, line in enumerate(heads.stdout.splitlines()):
                parts = line.split(maxsplit=1)
                if len(parts) != 2:
                    continue
                head, ref = parts
                local_ref = f"refs/process-debt/bundle-{index}-{head_index}"
                imported = _run(
                    [
                        "git",
                        "--git-dir",
                        str(comparison_repo),
                        "fetch",
                        "--no-tags",
                        str(path),
                        f"{ref}:{local_ref}",
                    ],
                    check=False,
                )
                resolvable = imported.returncode == 0
                integrated = False
                patch_equivalent = False
                import_error = ""
                if resolvable:
                    integrated = _run(
                        [
                            "git",
                            "--git-dir",
                            str(comparison_repo),
                            "merge-base",
                            "--is-ancestor",
                            head,
                            master,
                        ],
                        check=False,
                    ).returncode == 0
                    cherry = _run(
                        [
                            "git",
                            "--git-dir",
                            str(comparison_repo),
                            "cherry",
                            master,
                            head,
                        ],
                        check=False,
                    )
                    lines = [item for item in cherry.stdout.splitlines() if item[:1] in {"+", "-"}]
                    patch_equivalent = bool(lines) and all(item.startswith("-") for item in lines)
                else:
                    import_error = (imported.stderr or imported.stdout).strip()[:300]
                bundles.append(
                    {
                        "path": str(path),
                        "head": head,
                        "ref": ref,
                        "target_present": resolvable,
                        "integrated": integrated,
                        "patch_equivalent": patch_equivalent,
                        "error": import_error,
                        "bundle_sha256": _file_sha256(path),
                        "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    }
                )
    return bundles


def collect_bundles(
    *,
    bundle_dir: Path,
    repo: Path,
    master: str,
    fixture: Path | None,
    owner: str,
    default_due_at: str,
    default_opened_at: str,
    master_sha: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if fixture:
        payload = _read_json(fixture)
        bundles = payload.get("bundles", []) if isinstance(payload, Mapping) else payload
        if not isinstance(bundles, list):
            raise CollectionUnavailable("bundles fixture: oczekiwano listy")
    else:
        bundles = _real_bundles(bundle_dir, repo, master)
    proposals: list[dict[str, Any]] = []
    counters = {"scanned": 0, "target_missing": 0, "unmerged": 0, "patch_equivalent": 0}
    for bundle in sorted(bundles, key=lambda item: (str(item.get("path", "")), str(item.get("head", "")))):
        if not isinstance(bundle, Mapping):
            continue
        counters["scanned"] += 1
        path = str(bundle.get("path", ""))
        head = str(bundle.get("head", "")).lower()
        if bundle.get("integrated"):
            continue
        code_sha = head if _SHA_RE.fullmatch(head) else master_sha
        if not _SHA_RE.fullmatch(code_sha):
            continue
        source = f"{path}#{head or 'unknown'}"
        if bundle.get("error") or not bundle.get("target_present", False):
            counters["target_missing"] += 1
            kind = "BUNDLE_TARGET_MISSING"
            title = f"Bundle bez rozwiązywalnego prerequisite w repo: {Path(path).name}"
            next_step = "Wskaż właściwe repo/prerequisite i zweryfikuj bundle"
            blocker = str(bundle.get("error") or "Brak obiektu docelowego w repo")
        elif bundle.get("patch_equivalent"):
            counters["patch_equivalent"] += 1
            kind = "BUNDLE_PATCH_EQUIVALENT"
            title = f"Bundle patch-equivalent do {master}: {Path(path).name}"
            next_step = "Potwierdź integrację i usuń zbędny bundle z /tmp"
            blocker = "BRAK"
        else:
            counters["unmerged"] += 1
            kind = "BUNDLE_UNMERGED"
            title = f"Bundle z unikalną zmianą: {Path(path).name}"
            next_step = "Zweryfikuj i zintegruj, odrzuć albo oznacz superseded"
            blocker = "Brak decyzji integracyjnej"
        evidence = {
            "path": path,
            "head": head,
            "target_present": bool(bundle.get("target_present")),
            "integrated": bool(bundle.get("integrated")),
            "patch_equivalent": bool(bundle.get("patch_equivalent")),
            "bundle_sha256": bundle.get("bundle_sha256"),
            "error": bundle.get("error"),
        }
        proposals.append(
            _proposal(
                category="bundle",
                source=source,
                title=title,
                kind=kind,
                owner=owner,
                due_at=default_due_at,
                next_step=next_step,
                blocker=blocker,
                code_sha=code_sha,
                evidence=evidence,
                opened_at=str(bundle.get("mtime", default_opened_at)),
                metadata={"bundle_path": path, "head": head, "target_repo": str(repo)},
            )
        )
    return proposals, {"status": "OK", **counters, "source": str(fixture or bundle_dir)}


def parse_atq(text: str) -> set[str]:
    result: set[str] = set()
    for line in text.splitlines():
        match = _ATQ_RE.match(line)
        if match:
            result.add(match.group(1))
    return result


def _atq_snapshot(path: Path | None, unavailable: bool) -> tuple[set[str] | None, str]:
    if unavailable:
        return None, "UNAVAILABLE wymuszone opcją"
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"UNAVAILABLE: {path}: {exc}"
        return parse_atq(text), str(path)
    try:
        result = _run(["atq"], timeout=10.0)
    except CollectionUnavailable as exc:
        return None, f"UNAVAILABLE: {exc}"
    return parse_atq(result.stdout), "atq"


def collect_atq(
    *,
    store: GateStore,
    snapshot_path: Path | None,
    force_unavailable: bool,
    owner: str,
    default_due_at: str,
    default_opened_at: str,
    master_sha: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    present, source = _atq_snapshot(snapshot_path, force_unavailable)
    if present is None:
        return [], {"status": "UNAVAILABLE", "detail": source}
    jobs = store.list_at_jobs(active_only=True)
    registered_ids = {str(job["at_job_id"]): job for job in jobs if job.get("at_job_id")}
    proposals: list[dict[str, Any]] = []
    gate_cache: dict[str, dict[str, Any]] = {}
    for queue_id, job in sorted(registered_ids.items(), key=lambda item: int(item[0])):
        if queue_id in present:
            continue
        gate_id = str(job["gate_id"])
        try:
            gate = gate_cache.setdefault(gate_id, store.show_gate(gate_id))
        except GateError:
            continue
        evidence = {
            "at_job_id": queue_id,
            "job_key": job["job_key"],
            "registered_status": job["status"],
            "present_in_atq": False,
            "snapshot_source": source,
        }
        proposals.append(
            _proposal(
                category="at-missing",
                source=queue_id,
                gate_id=gate_id,
                title=f"ALARM: at-job #{queue_id} zniknął bez statusu terminalnego",
                kind="AT_JOB_MISSING",
                owner=str(gate["owner"]),
                due_at=str(gate["due_at"]),
                next_step="Uruchom at_gate.py reconcile i ustal wynik z logu",
                blocker="Brak terminalnego statusu zarejestrowanego joba",
                code_sha=str(gate["code_sha"]),
                evidence=evidence,
                opened_at=str(gate["opened_at"]),
                metadata={"at_job_id": queue_id, "existing_gate": True},
            )
        )
    unknown = sorted(present - set(registered_ids), key=int)
    if _SHA_RE.fullmatch(master_sha):
        for queue_id in unknown:
            evidence = {
                "at_job_id": queue_id,
                "registered": False,
                "present_in_atq": True,
                "snapshot_source": source,
            }
            proposals.append(
                _proposal(
                    category="at-unregistered",
                    source=queue_id,
                    title=f"at-job #{queue_id} poza kanonicznym rejestrem",
                    kind="AT_JOB_UNREGISTERED",
                    owner=owner,
                    due_at=default_due_at,
                    next_step="Ustal właściciela; anuluj albo zarejestruj następcę przez wrapper",
                    blocker="Job utworzony poza at_gate.py",
                    code_sha=master_sha,
                    evidence=evidence,
                    opened_at=default_opened_at,
                    metadata={"at_job_id": queue_id},
                )
            )
    return proposals, {
        "status": "OK",
        "source": source,
        "present": sorted(present, key=int),
        "registered_active": len(registered_ids),
        "missing_terminal": len(set(registered_ids) - present),
        "unregistered": len(unknown),
    }


def _component(
    name: str,
    function: Any,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], tuple[Any, ...]]:
    try:
        raw = function(**kwargs)
        if not isinstance(raw, tuple):
            raise AssertionError(f"{name}: collector nie zwrócił tuple")
        proposals = raw[0]
        status = raw[1]
        extras = tuple(raw[2:])
        return proposals, status, extras
    except CollectionUnavailable as exc:
        return [], {"status": "UNAVAILABLE", "detail": str(exc)}, ()
    except Exception as exc:  # izolacja komponentów; błąd jest jawny w wyniku
        return [], {"status": "ERROR", "detail": f"{type(exc).__name__}: {exc}"}, ()


def _apply(store: GateStore, proposals: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    added: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    for proposal in proposals:
        gate_id = str(proposal["gate_id"])
        try:
            store.add_gate(
                gate_id=gate_id,
                title=str(proposal["title"]),
                kind=str(proposal["kind"]),
                owner=str(proposal["owner"]),
                due_at=str(proposal["due_at"]),
                next_step=str(proposal["next_step"]),
                blocker=str(proposal["blocker"]),
                code_sha=str(proposal["code_sha"]),
                evidence_hash=str(proposal["evidence_hash"]),
                opened_at=str(proposal["opened_at"]),
                metadata=dict(proposal.get("metadata", {})),
                actor="process_debt_collect/--apply",
                reason="jawnie zatwierdzona propozycja kolektora",
            )
            added.append(gate_id)
        except GateAlreadyExists:
            skipped.append(gate_id)
        except GateError as exc:
            errors.append({"gate_id": gate_id, "error": str(exc)})
    return {"mode": "APPLY", "added": added, "skipped_existing": skipped, "errors": errors}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--flags-json", type=Path)
    parser.add_argument("--effective-flags", type=Path)
    parser.add_argument("--flag-evidence", type=Path)
    parser.add_argument("--service", action="append", dest="services")
    parser.add_argument("--branches-fixture", type=Path)
    parser.add_argument("--bundles-fixture", type=Path)
    parser.add_argument("--bundle-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--atq-file", type=Path)
    parser.add_argument("--atq-unavailable", action="store_true")
    parser.add_argument("--master", default="master")
    parser.add_argument("--owner", default="CTO")
    parser.add_argument("--as-of", help="ISO-8601; dla deterministycznego testu")
    parser.add_argument("--due-days", type=int, default=7)
    parser.add_argument("--output", default="-")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="JAWNIE dodaj propozycje przez GateStore; bez tej flagi zero zapisów DB",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.due_days < 1:
        print(json.dumps({"error": "--due-days musi być dodatnie"}), file=sys.stderr)
        return 2
    as_of = parse_timestamp(args.as_of, "as_of") if args.as_of else utc_now()
    as_of = as_of.astimezone(timezone.utc).replace(microsecond=0)
    default_opened_at = iso_utc(as_of)
    default_due_at = iso_utc(as_of + timedelta(days=args.due_days))
    repo = args.repo.resolve()
    flags_path = (args.flags_json or repo / "flags.json").resolve()
    store = GateStore(args.db)
    components: dict[str, Any] = {}
    proposals: list[dict[str, Any]] = []

    found, status, _ = _component(
        "flags",
        collect_flags,
        flags_path=flags_path,
        effective_path=args.effective_flags,
        evidence_path=args.flag_evidence,
        services=tuple(args.services or DEFAULT_SERVICES),
        owner=args.owner,
        default_due_at=default_due_at,
        default_opened_at=default_opened_at,
    )
    proposals.extend(found)
    components["flags"] = status

    found, status, extras = _component(
        "branches",
        collect_branches,
        repo=repo,
        master=args.master,
        fixture=args.branches_fixture,
        owner=args.owner,
        default_due_at=default_due_at,
        default_opened_at=default_opened_at,
    )
    proposals.extend(found)
    components["branches"] = status
    master_sha = str(extras[0]) if extras else ""

    found, status, _ = _component(
        "bundles",
        collect_bundles,
        bundle_dir=args.bundle_dir,
        repo=repo,
        master=args.master,
        fixture=args.bundles_fixture,
        owner=args.owner,
        default_due_at=default_due_at,
        default_opened_at=default_opened_at,
        master_sha=master_sha,
    )
    proposals.extend(found)
    components["bundles"] = status

    found, status, _ = _component(
        "atq",
        collect_atq,
        store=store,
        snapshot_path=args.atq_file,
        force_unavailable=args.atq_unavailable,
        owner=args.owner,
        default_due_at=default_due_at,
        default_opened_at=default_opened_at,
        master_sha=master_sha,
    )
    proposals.extend(found)
    components["atq"] = status

    proposals.sort(key=lambda item: (item["kind"], item["gate_id"]))
    mutation = _apply(store, proposals) if args.apply else {
        "mode": "PROPOSALS_ONLY",
        "note": "baza nie została zmieniona; użyj jawnego --apply",
    }
    payload = {
        "schema_version": 1,
        "generated_at": iso_utc(as_of),
        "repo": str(repo),
        "db": str(args.db),
        "components": components,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "mutation": mutation,
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output == "-":
        sys.stdout.write(output)
    else:
        atomic_write(Path(args.output), output)
        print(json.dumps({"written": args.output, "proposal_count": len(proposals)}, ensure_ascii=False))
    return 1 if any(component.get("status") == "ERROR" for component in components.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
