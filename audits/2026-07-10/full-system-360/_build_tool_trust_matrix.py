from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "tool_trust_matrix.json"
OUTPUT = ROOT / "tool_trust_matrix.csv"
FIELDS = [
    "id",
    "instrument",
    "trust",
    "current_snapshot_reverified",
    "producer_to_reader",
    "denominator",
    "freshness",
    "fail_open_or_skip",
    "negative_control",
    "limitation",
    "audit_base_sha",
]


def atomic_write(path: Path, value: str) -> None:
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


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    base_sha = payload["base_sha"]
    rows = [{**row, "audit_base_sha": base_sha} for row in payload["instruments"]]
    if len(rows) != payload["summary"]["total"]:
        raise SystemExit("tool trust total does not match rows")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=FIELDS,
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(OUTPUT, buffer.getvalue())


if __name__ == "__main__":
    main()
