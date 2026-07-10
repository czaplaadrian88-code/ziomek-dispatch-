"""Kontrakt wersjonowanego logrotate dla sidecara Z-P1-03.

Test czyta wyłącznie artefakt repozytoryjny. Nie instaluje konfiguracji,
nie uruchamia logrotate i nie dotyka produkcyjnego pliku ani katalogu /etc.
"""

from pathlib import Path


CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "deploy"
    / "stage-timing-logrotate.conf"
)
SIDECAR_PATH = (
    "/root/.openclaw/workspace/scripts/logs/"
    "shadow_decisions.stage_timings.jsonl"
)


def test_stage_timing_sidecar_has_exact_private_rotation_block():
    lines = [
        line.strip()
        for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert lines[0] == f"{SIDECAR_PATH} {{"
    assert lines[-1] == "}"
    assert lines.count("}") == 1
    assert "shadow_decisions.jsonl" not in lines[0]
    assert "*" not in lines[0]

    assert set(lines[1:-1]) == {
        "daily",
        "rotate 30",
        "size 100M",
        "compress",
        "delaycompress",
        "missingok",
        "notifempty",
        "copytruncate",
        "su root root",
        "create 0600 root root",
    }
    assert len(lines[1:-1]) == 10


def test_stage_timing_rotation_never_weakens_private_file_mode():
    text = CONFIG_PATH.read_text(encoding="utf-8")

    assert "create 0600 root root" in text
    assert "create 0640" not in text
    assert "create 0644" not in text
