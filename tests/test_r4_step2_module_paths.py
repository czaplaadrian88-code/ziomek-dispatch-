"""R4 krok 2: podpakiety produkcyjne nie mogą przywracać ścieżek hosta."""

from __future__ import annotations

import re
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIRS = (
    "cod_weekly",
    "core",
    "czasowka_proactive",
    "daily_accounting",
    "identity",
    "ml_data_prep",
    "monitoring",
    "observability",
    "reconciliation",
    "shift_notifications",
    "sms",
)
HOST_PATH_LITERAL = re.compile(
    r"""["']/root/\.openclaw/workspace/(?:scripts|dispatch_state)"""
)


def test_production_subpackages_have_no_host_path_literals() -> None:
    """Kanon scripts/state ma pochodzić z common.py, nie z kopii host path."""
    offenders: list[str] = []
    for module_dir in MODULE_DIRS:
        for path in sorted((REPO_ROOT / module_dir).rglob("*.py")):
            if "tests" in path.relative_to(REPO_ROOT).parts:
                continue
            for line_no, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if HOST_PATH_LITERAL.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{line_no}: {line.strip()}"
                    )

    assert not offenders, (
        "R4 krok 2: przywrócono host-bound literal zamiast stałej z common.py:\n"
        + "\n".join(offenders)
    )


def test_common_state_dir_honors_hermetic_dispatch_state_dir(tmp_path) -> None:
    """Migracja consumerów nie może ominąć istniejącego sandboxu pytest."""
    env = os.environ.copy()
    env.pop("ZIOMEK_STATE_DIR", None)
    env["DISPATCH_STATE_DIR"] = str(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from dispatch_v2.common import STATE_DIR; print(STATE_DIR)",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.stdout.strip() == str(tmp_path)
