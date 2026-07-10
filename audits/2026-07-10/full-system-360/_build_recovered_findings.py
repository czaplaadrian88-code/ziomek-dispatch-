from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path


SOURCE_NAME = "audyt-360-ziomek-2026-07-10-ZALACZNIK.md"
EXPECTED_SOURCE_SHA256 = "9836c252d5a15f02c21d179584ebab9dab069043478430e06da6929115903604"
OUT = Path(__file__).resolve().parent
HEADER = re.compile(r"^### \[([^]]+)] (P[0-3]) — (.+)$")
BASE_SHA = "70af4faea8b84d30c66dc933eadf7291f94a1b79"
SAFE_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
FORBIDDEN_EXPORT_PATTERNS = {
    "absolute-home": re.compile(r"/(?:root|home)/"),
    "secret-path": re.compile(r"(?i)(?:/\.ssh/|/\.secrets/|\.restic_password|admin-cred\.conf|\.(?:pem|key|secret)\b)"),
    "email": re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    "entity-id": re.compile(r"(?i)\b(?:cid|courier_id|order_id)\s*[=:]?\s*\d+"),
    "gps": re.compile(r"(?<!\d)(?:4[89]|5[0-5])\.\d{4,}\s*[,;/ ]\s*(?:1[4-9]|2[0-4])\.\d{4,}"),
    "phone": re.compile(r"(?<!\d)(?:\+48[ -]?)?\d{3}[ -]\d{3}[ -]\d{3}(?!\d)"),
    "uuid": re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
}

REVERIFIED = {
    "SPRI-01": ("REFUTED", "NONE", "Ścieżka assign wywołuje inline redecide; bieżąca flaga jest ON, więc teza o luce do pięciu minut nie zachodzi."),
    "FEAS-01": ("CONFIRMED", "P1", "Aktywna niespójność HARD/kanon; wartość raw jest predykcją, nie fizycznym outcome."),
    "FEAS-02": ("PARTIAL", "P2", "Unknown/sum proxy są wadliwe, ale live path jest jawnie feasibility=NO/ALERT; KOORD przeczy always-propose."),
    "TRAS-01": ("CONFIRMED", "P2", "Plan solvera nie jest utrwalany, lecz natychmiastowy redecide usuwa tezę o luce do 5 minut."),
    "TRAS-02": ("CONFIRMED", "P2", "Błąd maskowany przez TRAS-01; naprawiać razem, aby nie odblokować złej kolejności stops."),
    "CORE-01": ("PARTIAL", "P2", "Snapshot jest niepełny, ale 22/23 soft diff współwystępuje z OSRM miss; bieżąca przyczyna nieudowodniona."),
    "FLAG-01": ("CONFIRMED", "P2", "Latentny no-op przyszłego flipa; stan zamierzony i faktyczny jest dziś OFF."),
    "OPS-01": ("CONFIRMED", "P2", "Ryzyko po reboot; oba listenery działają, stary PID pozostaje w cgroup przy KillMode=process."),
    "OPS-02": ("CONFIRMED", "P2", "Brak bezpośredniego OnFailure/liveness; Restart= łagodzi skutek, brak aktywnej awarii."),
    "OPS-05": ("PARTIAL", "P2", "Host bind i brak backstopu potwierdzone; aktualnego Cloud Firewall nie da się dowieść z hosta."),
    "BEZP-02": ("CONFIRMED", "P2", "Brak ownership order→CID; P1 dopiero w łańcuchu z publicznym wejściem."),
    "BEZP-04": ("CONFIRMED", "P3", "Katalog pre-login nadal publiczny, lecz per-IP limiter jest LIVE; stara matematyka brute-force była stale."),
    "BEZP-01": ("REFUTED", "NONE", "Legacy listener nie działa; w snapshotcie audytu aktywne były wyłącznie dwa nowsze porty usługi."),
    "DANE-01": ("CONFIRMED", "P2", "Panelowy writer woła save_plan bez expected_version, a zatwierdzony handoff potwierdza aktywny carrier zapisu planu."),
}

REVERIFICATION_SCOPE = {
    "SPRI-01": "head+effective-flag",
    "CORE-01": "head+existing-replay-verdict",
    "FEAS-01": "head+effective-flag",
    "FEAS-02": "head+redacted-record-shape",
    "TRAS-01": "head",
    "TRAS-02": "head",
    "FLAG-01": "head+effective-flag",
    "OPS-01": "runtime-metadata",
    "OPS-02": "runtime-metadata",
    "OPS-05": "host-runtime-only",
    "BEZP-01": "runtime-metadata",
    "BEZP-02": "head+runtime-boundary",
    "BEZP-04": "head+effective-limiter",
    "DANE-01": "cross-repo-head+approved-handoff",
}


def final_status(verdict: str) -> str:
    value = verdict.upper()
    if "NIEZWERYFIKOWANY" in value or not value:
        return "UNVERIFIED"
    if "PLAUSIBLE" in value:
        return "PLAUSIBLE"
    if "CONFIRMED" in value and "REFUTED" in value:
        return "DISPUTED"
    if "REFUTED" in value:
        return "REFUTED"
    if "CONFIRMED" in value:
        return "CONFIRMED"
    return "UNKNOWN"


def final_severity(original: str, verdict: str, status: str) -> str:
    if status == "REFUTED":
        return "NONE"
    targets = re.findall(r"(?:CONFIRMED|PLAUSIBLE)→(P[0-3])", verdict.upper())
    if targets:
        return min(targets, key=lambda item: int(item[1]))
    return original


def sanitized_text(value: str) -> str:
    value = value.replace("/root/", "<local>/")
    value = re.sub(r"\b\d{5,}\b", "<id>", value)
    value = re.sub(
        r"(?i)\b(?:cid|courier_id|order_id)\s*[=:]?\s*\d+",
        "<entity-id>",
        value,
    )
    value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "<email>", value)
    value = re.sub(
        r"(?<!\d)(?:\+48[ -]?)?\d{3}[ -]\d{3}[ -]\d{3}(?!\d)",
        "<phone>",
        value,
    )
    value = re.sub(
        r"(?<!\d)(?:4[89]|5[0-5])\.\d{4,}\s*[,;/ ]\s*(?:1[4-9]|2[0-4])\.\d{4,}",
        "<gps>",
        value,
    )
    value = re.sub(
        r"\(wykluczony [^)]+ <entity-id> koliduje z fixturą\)",
        "(rzeczywisty wpis kuriera koliduje z fixture)",
        value,
        flags=re.IGNORECASE,
    )
    value = value.replace("<local>/.restic_password", "<redacted-secret-carrier>")
    value = value.replace("admin-cred.conf", "<redacted-credential-carrier>")
    value = re.sub(
        r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        "<uuid>",
        value,
    )
    return value


def verified_source_text() -> str:
    source = (Path.home() / SOURCE_NAME).resolve()
    if source.parent != Path.home().resolve() or source.name != SOURCE_NAME:
        raise SystemExit("audit source must be the pinned recovery artifact in the home directory")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SOURCE_SHA256:
        raise SystemExit(f"audit source checksum mismatch: {digest}")
    return raw.decode("utf-8")


def assert_safe_export(rows: list[dict[str, object]]) -> None:
    for index, row in enumerate(rows):
        if not SAFE_ID.fullmatch(str(row.get("id", ""))):
            raise SystemExit(f"unsafe finding id at row {index}")
        for key, value in row.items():
            if value is None or isinstance(value, (bool, int, float)):
                continue
            if not isinstance(value, str):
                raise SystemExit(f"unsupported export type at row {index}, field {key}")
            for category, pattern in FORBIDDEN_EXPORT_PATTERNS.items():
                if pattern.search(value):
                    raise SystemExit(
                        f"unsafe export blocked: row {index}, field {key}, category {category}"
                    )


def atomic_write_text(path: Path, value: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        tmp.unlink(missing_ok=True)


def parse() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    domain = "UNKNOWN"
    current: dict[str, object] | None = None
    for raw in verified_source_text().splitlines():
        if raw.startswith("## "):
            domain = sanitized_text(raw[3:].split("— zdrowie", 1)[0].strip())
            continue
        match = HEADER.match(raw)
        if match:
            if current:
                rows.append(current)
            finding_id, severity, title = match.groups()
            finding_id = finding_id.replace("--", "-")
            current = {
                "id": finding_id,
                "domain": domain,
                "original_severity": severity,
                "title": sanitized_text(title),
                "_review_verdict_raw": "",
                "known_before": None,
                "confidence": "unknown",
            }
            continue
        if current is None:
            continue
        if raw.startswith("- **Werdykt skeptyków:**"):
            current["_review_verdict_raw"] = sanitized_text(
                raw.split("**Werdykt skeptyków:**", 1)[1].strip()
            )
        if raw.startswith("- **Znane wcześniej:**"):
            known = raw.split("**Znane wcześniej:**", 1)[1]
            current["known_before"] = sanitized_text(known.split("·", 1)[0].strip())
            confidence = re.search(r"pewność:\s*([a-zA-Z_-]+)", known)
            if confidence:
                current["confidence"] = confidence.group(1).lower()
    if current:
        rows.append(current)

    for row in rows:
        verdict = str(row.pop("_review_verdict_raw"))
        status = final_status(verdict)
        row["final_status"] = status
        row["final_severity"] = final_severity(str(row["original_severity"]), verdict, status)
        row["evidence_level"] = (
            "unreviewed" if status == "UNVERIFIED" else "dual_review" if "|" in verdict else "single_review"
        )
        row["reverified_current_snapshot"] = False
        row["reverification_scope"] = "not-reverified"
        row["source"] = "recovered_claude_appendix"
        if row["id"] in REVERIFIED:
            status, severity, note = REVERIFIED[str(row["id"])]
            row["final_status"] = status
            row["final_severity"] = severity
            row["reverified_current_snapshot"] = True
            row["reverification_scope"] = REVERIFICATION_SCOPE[str(row["id"])]
            row["reverification_note"] = note

    rows.extend([
        {
            "id": "AUDIT-01", "domain": "Audit process", "original_severity": "P2",
            "final_severity": "P2", "final_status": "PARTIAL", "confidence": "high",
            "evidence_level": "direct", "reverified_current_snapshot": True,
            "reverification_scope": "audit-process",
            "known_before": "NIE", "source": "codex_recovery",
            "title": "Na starcie odzysku brakowało pakietu 35 artefaktów, branch/worktree, commit/push i trwałego handoffu",
            "reverification_note": "Stan historyczny potwierdzony; branch i pakiet odtworzono. Zamrożony raport pozostawia commit, push i trwały handoff jako zewnętrzną bramkę wydania.",
        },
        {
            "id": "AUDIT-02", "domain": "Audit process", "original_severity": "P2",
            "final_severity": "P2", "final_status": "CONFIRMED", "confidence": "high",
            "evidence_level": "direct", "reverified_current_snapshot": True,
            "reverification_scope": "audit-process",
            "known_before": "NIE", "source": "codex_recovery",
            "title": "Synteza Claude’a ma niespójną arytmetykę statusów i severity względem 106 wpisów załącznika",
            "reverification_note": "Indeks maszynowy zachowuje status każdego wpisu zamiast powtarzać zagregowane liczby bez mianownika.",
        },
        {
            "id": "TEST-11", "domain": "Testy i dowody", "original_severity": "P2",
            "final_severity": "P2", "final_status": "CONFIRMED", "confidence": "high",
            "evidence_level": "reproduced", "reverified_current_snapshot": True,
            "reverification_scope": "targeted-test+live-read-dependency",
            "known_before": "NIE", "source": "codex_recovery",
            "title": "Test rejestru flag czyta live flags.json i nadal oczekuje historycznego known-open po flipie USE_V2_PARSER",
            "reverification_note": "Targeted repro: actual verdict open, expected known-open. Helper ma twardą ścieżkę do live flags.json, więc baseline zależy od runtime mimo pytest sandboxu.",
        },
        {
            "id": "TEST-12", "domain": "Testy i dowody", "original_severity": "P2",
            "final_severity": "P2", "final_status": "CONFIRMED", "confidence": "high",
            "evidence_level": "reproduced", "reverified_current_snapshot": True,
            "reverification_scope": "full-HERMETIC_STRICT-suite",
            "known_before": "NIE", "source": "codex_recovery",
            "title": "Pięć script-tests czyta live stan kurierów i nie jest objętych aktualną kwarantanną STRICT",
            "reverification_note": "STRICT na bazowym HEAD: 4792 passed, 6 failed, 76 skipped, 8 xfailed, 2 xpassed. Pięć faili to blokowane live reads; szósty to TEST-11.",
        },
    ])
    return rows


def write_json(rows: list[dict[str, object]]) -> None:
    payload = {
        "schema": "ziomek.audit.findings.v2",
        "audit_date": "2026-07-10",
        "base_sha": BASE_SHA,
        "caveat": "Recovered findings; P3 entries were not independently reviewed by Claude. Selected high-risk entries are revalidated in 26_FINAL_INDEPENDENT_REVIEW.md.",
        "summary": {
            "total": len(rows),
            "recovered": sum(row["source"] == "recovered_claude_appendix" for row in rows),
            "audit_added": sum(row["source"] == "codex_recovery" for row in rows),
            "reverified_current_snapshot": sum(bool(row["reverified_current_snapshot"]) for row in rows),
            "status": dict(Counter(str(row["final_status"]) for row in rows)),
            "original_severity": dict(Counter(str(row["original_severity"]) for row in rows)),
            "final_severity": dict(Counter(str(row["final_severity"]) for row in rows)),
        },
        "findings": rows,
    }
    atomic_write_text(
        OUT / "findings_master.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def write_csv(rows: list[dict[str, object]]) -> None:
    fields = [
        "id", "domain", "original_severity", "final_severity", "final_status",
        "confidence", "evidence_level", "reverified_current_snapshot",
        "reverification_scope", "reverification_note", "known_before", "title",
        "source", "audit_base_sha",
    ]
    csv_rows = []
    for row in rows:
        csv_rows.append({**row, "audit_base_sha": BASE_SHA})
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=fields,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(csv_rows)
    atomic_write_text(OUT / "findings_master.csv", buffer.getvalue())


def write_markdown(rows: list[dict[str, object]]) -> None:
    status_counts = Counter(str(row["final_status"]) for row in rows)
    severity_counts = Counter(str(row["final_severity"]) for row in rows)
    lines = [
        "# Findings master",
        "",
        "> Odzyskano 106 wpisów z załącznika Claude’a i dodano 4 findings procesu odzysku. Indeks jest zredagowany: bez PII, wartości sekretów i pełnych ścieżek wrażliwych.",
        "",
        f"Statusy: `{dict(status_counts)}`. Finalne severity: `{dict(severity_counts)}`.",
        "",
        "Wśród finalnych P3 jest 52 `UNVERIFIED`, 5 `CONFIRMED` i 1 `PLAUSIBLE`; tylko `UNVERIFIED` pozostaje hipotezą. Rozbieżności i ponowna kontrola P1 są w `26_FINAL_INDEPENDENT_REVIEW.md`.",
        "",
        f"Baza kodu: `{BASE_SHA}`. `Weryf.` wskazuje zakres ponownej kontroli; `nie` nie oznacza obalenia.",
        "",
        "| ID | Domena | Oryg. | Final | Status | Dowód | Weryf. | Tytuł |",
        "|---|---|:---:|:---:|---|---|---|---|",
    ]
    for row in rows:
        title = str(row["title"]).replace("|", "\\|")
        lines.append(
            f"| {row['id']} | {row['domain']} | {row['original_severity']} | {row['final_severity']} | "
            f"{row['final_status']} | {row['evidence_level']} | "
            f"{row['reverification_scope'] if row['reverified_current_snapshot'] else 'nie'} | {title} |"
        )
    lines.extend([
        "",
        "## Reguły interpretacji",
        "",
        "- `CONFIRMED` oznacza, że co najmniej jeden niezależny reviewer potwierdził mechanizm; nie jest automatycznym zezwoleniem na zmianę live.",
        "- `PARTIAL` oznacza potwierdzony fragment mechanizmu przy nieudowodnionej przyczynie lub skali wpływu.",
        "- `PLAUSIBLE` oznacza spójny mechanizm bez wystarczającej reprodukcji.",
        "- `DISPUTED` wymaga rozstrzygnięcia przed backlogiem wykonawczym.",
        "- `REFUTED` pozostaje w indeksie, aby nie wracał jako fałszywy alarm.",
        "- `UNVERIFIED` to hipoteza P3, nie fakt.",
    ])
    atomic_write_text(OUT / "19_FINDINGS_MASTER.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    findings = parse()
    if len(findings) != 110:
        raise SystemExit(f"expected 110 findings, got {len(findings)}")
    assert_safe_export(findings)
    write_json(findings)
    write_csv(findings)
    write_markdown(findings)
