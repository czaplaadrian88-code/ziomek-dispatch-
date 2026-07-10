from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_SHA = "70af4faea8b84d30c66dc933eadf7291f94a1b79"
SAFE_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
REQUIRED_EXTRA = {
    "README.md",
    "findings_master.json",
    "findings_master.csv",
    "tool_trust_matrix.json",
    "tool_trust_matrix.csv",
}
FORBIDDEN = {
    "absolute-home": re.compile(r"/(?:root|home)/"),
    "secret-path": re.compile(r"(?i)(?:/\.ssh/|/\.secrets/|\.restic_password|admin-cred\.conf|\.(?:pem|key|secret)\b)"),
    "email": re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    "entity-id": re.compile(r"(?i)\b(?:cid|courier_id|order_id)\s*[=:]?\s*\d+"),
    "gps": re.compile(r"(?<!\d)(?:4[89]|5[0-5])\.\d{4,}\s*[,;/ ]\s*(?:1[4-9]|2[0-4])\.\d{4,}"),
    "credential": re.compile(r"(?i)\b(?:token|password|passwd|secret|pin|cookie)\b[^\n:=]{0,24}[:=]\s*\S+"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "uuid": re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"),
}


def fail(message: str) -> None:
    raise SystemExit(f"AUDIT360_VALIDATE FAIL: {message}")


def load_csv(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_required() -> set[str]:
    numbered = sorted(ROOT.glob("[0-2][0-9]_*.md"))
    prefixes = [path.name[:2] for path in numbered]
    expected_prefixes = [f"{value:02d}" for value in range(30)]
    if prefixes != expected_prefixes:
        fail(f"numbered reports mismatch: {prefixes}")
    required = {path.name for path in numbered} | REQUIRED_EXTRA
    if len(required) != 35 or any(not (ROOT / name).is_file() for name in required):
        fail("required artifact set is incomplete")
    return required


def validate_findings() -> int:
    payload = json.loads((ROOT / "findings_master.json").read_text(encoding="utf-8"))
    rows = payload["findings"]
    if payload.get("base_sha") != BASE_SHA or payload.get("schema") != "ziomek.audit.findings.v2":
        fail("findings provenance/schema mismatch")
    ids = [row["id"] for row in rows]
    if len(rows) != 110 or len(set(ids)) != 110 or any("--" in value for value in ids):
        fail("findings count/ID uniqueness mismatch")
    summary = payload["summary"]
    expected = {
        "total": len(rows),
        "recovered": sum(row["source"] == "recovered_claude_appendix" for row in rows),
        "audit_added": sum(row["source"] == "codex_recovery" for row in rows),
        "reverified_current_snapshot": sum(bool(row["reverified_current_snapshot"]) for row in rows),
        "status": dict(Counter(row["final_status"] for row in rows)),
        "original_severity": dict(Counter(row["original_severity"] for row in rows)),
        "final_severity": dict(Counter(row["final_severity"] for row in rows)),
    }
    if summary != expected or summary["recovered"] != 106 or summary["audit_added"] != 4:
        fail("findings summary mismatch")
    csv_rows = load_csv("findings_master.csv")
    if [row["id"] for row in csv_rows] != ids:
        fail("findings CSV ordering/IDs mismatch")
    keys = (
        "domain", "original_severity", "final_severity", "final_status",
        "confidence", "evidence_level", "reverification_scope",
        "reverification_note", "known_before", "title", "source",
    )
    by_id = {row["id"]: row for row in rows}
    for csv_row in csv_rows:
        source = by_id[csv_row["id"]]
        if any(csv_row[key] != ("" if source.get(key) is None else str(source.get(key, ""))) for key in keys):
            fail(f"findings CSV mismatch for {csv_row['id']}")
        if csv_row["reverified_current_snapshot"] != str(source["reverified_current_snapshot"]):
            fail(f"findings CSV reverify mismatch for {csv_row['id']}")
        if csv_row["audit_base_sha"] != BASE_SHA:
            fail("findings CSV base SHA mismatch")
    if any("review_verdict_raw" in row or "_review_verdict_raw" in row for row in rows):
        fail("raw source verdict leaked into findings JSON")
    markdown = (ROOT / "19_FINDINGS_MASTER.md").read_text(encoding="utf-8")
    md_rows = []
    for line in markdown.splitlines():
        if not line.startswith("| "):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) != 8 or parts[0] == "ID" or not SAFE_ID.fullmatch(parts[0]):
            continue
        md_rows.append(parts)
    expected_md_rows = []
    for row in rows:
        expected_md_rows.append([
            str(row["id"]),
            str(row["domain"]),
            str(row["original_severity"]),
            str(row["final_severity"]),
            str(row["final_status"]),
            str(row["evidence_level"]),
            str(row["reverification_scope"] if row["reverified_current_snapshot"] else "nie"),
            str(row["title"]).replace("|", "\\|"),
        ])
    if md_rows != expected_md_rows:
        fail("findings Markdown field parity mismatch")
    status_text = f"Statusy: `{dict(Counter(row['final_status'] for row in rows))}`."
    severity_text = f"Finalne severity: `{dict(Counter(row['final_severity'] for row in rows))}`."
    if status_text not in markdown or severity_text not in markdown or BASE_SHA not in markdown:
        fail("findings Markdown summary/provenance mismatch")
    return len(rows)


def validate_tools() -> int:
    payload = json.loads((ROOT / "tool_trust_matrix.json").read_text(encoding="utf-8"))
    rows = payload["instruments"]
    if payload.get("base_sha") != BASE_SHA or payload.get("schema") != "ziomek.audit.tool-trust.v1":
        fail("tool trust provenance/schema mismatch")
    ids = [row["id"] for row in rows]
    if len(rows) != 15 or len(set(ids)) != 15:
        fail("tool trust count/ID uniqueness mismatch")
    expected_summary = {"total": len(rows), **dict(Counter(row["trust"] for row in rows))}
    if payload["summary"] != expected_summary:
        fail("tool trust summary mismatch")
    csv_rows = load_csv("tool_trust_matrix.csv")
    if [row["id"] for row in csv_rows] != ids:
        fail("tool trust CSV ordering/IDs mismatch")
    by_id = {row["id"]: row for row in rows}
    for csv_row in csv_rows:
        source = by_id[csv_row["id"]]
        for key in ("instrument", "trust", "producer_to_reader", "denominator", "freshness", "fail_open_or_skip", "negative_control", "limitation"):
            if csv_row[key] != str(source[key]):
                fail(f"tool trust CSV mismatch for {csv_row['id']}")
        if csv_row["current_snapshot_reverified"] != str(source["current_snapshot_reverified"]):
            fail(f"tool trust CSV reverify mismatch for {csv_row['id']}")
        if csv_row["audit_base_sha"] != BASE_SHA:
            fail("tool trust CSV base SHA mismatch")
    return len(rows)


def validate_links_and_safety(required: set[str]) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in re.findall(r"`([^`]+\.(?:md|json|csv))`", readme):
        is_package_link = bool(re.fullmatch(
            r"(?:[0-2][0-9]_[A-Z0-9_]+\.md|(?:findings_master|tool_trust_matrix)\.(?:json|csv))",
            name,
        ))
        if is_package_link and not (ROOT / name).is_file():
            fail(f"README points to missing file: {name}")
    for helper in (
        "_build_recovered_findings.py",
        "_build_tool_trust_matrix.py",
        "_validate_package.py",
    ):
        source = (ROOT / helper).read_text(encoding="utf-8")
        compile(source, helper, "exec")
    for name in sorted(required):
        value = (ROOT / name).read_text(encoding="utf-8")
        for category, pattern in FORBIDDEN.items():
            if pattern.search(value):
                fail(f"unsafe content in {name}: {category}")


def main() -> None:
    required = validate_required()
    findings = validate_findings()
    tools = validate_tools()
    validate_links_and_safety(required)
    print(f"AUDIT360_VALIDATE OK required={len(required)} findings={findings} tools={tools}")


if __name__ == "__main__":
    main()
