"""Egzekwuje selftesty skilli `.claude/skills/*` w ramach nocnej regresji.

Powód: audyt 2026-07-17 pokazał, że skill, którego oracle uruchomił się RAZ, jest
kruchy (dokładnie wada zdeprecjonowanej bramy `ziomek-change-gate`). Te dwa cienkie
testy zamieniają „zademonstrowane raz" na „egzekwowane co noc" — nocny strażnik
odpala pełną suitę, więc regresja drivera/blindowania/bramki-ACK zapali ALERT.

Read-only: selftesty piszą wyłącznie do mktemp; ścieżki [ACK] drivera zatrzymują
się na bramce (exit 2) PRZED jakimkolwiek zapisem, więc nic nie dotyka żywego stanu.
"""
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / ".claude" / "skills"


def _run_selftest(skill: str) -> subprocess.CompletedProcess:
    script = SKILLS / skill / "selftest.sh"
    assert script.is_file(), f"brak selftestu skilla: {script}"
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=120,
    )


@pytest.mark.parametrize("skill", ["ziomek-blind-review", "run-dispatch-v2", "ziomek-cto"])
def test_skill_selftest_passes(skill: str) -> None:
    r = _run_selftest(skill)
    assert r.returncode == 0, (
        f"selftest {skill} FAILED (rc={r.returncode})\n"
        f"--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
    )
    assert "SELFTEST OK" in r.stdout, r.stdout
