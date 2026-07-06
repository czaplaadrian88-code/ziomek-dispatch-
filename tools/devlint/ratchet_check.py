#!/usr/bin/env python3
"""devlint ratchet (K01 programu refaktoru, 2026-07-06).

Zasada: liczba naruszeń ruff/mypy NIE MOŻE URUSNĄĆ ponad baseline.
Zielono = naruszeń ≤ baseline (sprzątanie mile widziane — potem --update-baseline).
Czerwono = nowe naruszenia (exit 1) z wypisaną deltą per reguła.

Uruchamianie (dowolny python3; narzędzia z venv devlint):
    python3 tools/devlint/ratchet_check.py            # sprawdzenie vs baseline
    python3 tools/devlint/ratchet_check.py --update-baseline   # po świadomym sprzątnięciu

Env: DEVLINT_VENV (default /root/.openclaw/venvs/devlint) — ścieżka venv z ruff+mypy.
Baseline: tools/devlint/baseline.json (w repo; zmiany baseline'u = jawny commit).
Zakres mypy = MYPY_MODULES (moduły rdzenia objęte programem refaktoru).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASELINE_PATH = HERE / "baseline.json"
VENV = Path(os.environ.get("DEVLINT_VENV", "/root/.openclaw/venvs/devlint"))
RUFF = VENV / "bin" / "ruff"
MYPY = VENV / "bin" / "mypy"

# Moduły rdzenia w zakresie mypy (rozszerzaj przy kolejnych krokach stranglera).
MYPY_MODULES = [
    "common.py",
    "dispatch_pipeline.py",
    "feasibility_v2.py",
    "scoring.py",
    "route_simulator_v2.py",
    "tsp_solver.py",
    "objm_lexr6.py",
    "sla_anchor.py",
    "shadow_dispatcher.py",
    "plan_recheck.py",
    "plan_manager.py",
    "courier_resolver.py",
    "osrm_client.py",
    "state_machine.py",
    "chain_eta.py",
    "event_bus.py",
    "pending_proposals_store.py",
    "postpone_sweeper.py",
    "panel_watcher.py",
]


def run_ruff() -> Counter:
    out = subprocess.run(
        [str(RUFF), "check", "--config", str(HERE / "ruff.toml"),
         "--output-format", "json", "--exit-zero", str(REPO)],
        capture_output=True, text=True, timeout=600,
    )
    try:
        items = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        print("ruff: niepoprawny JSON na stdout — traktuję jako awarię narzędzia")
        print(out.stdout[:2000], out.stderr[:2000])
        sys.exit(2)
    return Counter(i.get("code") or "PARSE" for i in items)


def run_mypy() -> int:
    files = [str(REPO / m) for m in MYPY_MODULES if (REPO / m).exists()]
    out = subprocess.run(
        [str(MYPY), "--config-file", str(HERE / "mypy.ini"), *files],
        capture_output=True, text=True, timeout=900,
    )
    return sum(1 for line in out.stdout.splitlines() if ": error:" in line)


def main() -> int:
    if not RUFF.exists() or not MYPY.exists():
        print(f"devlint venv niekompletny: {VENV} (DEVLINT_VENV?) — pomiń albo zainstaluj ruff+mypy")
        return 2

    ruff_counts = run_ruff()
    mypy_errors = run_mypy()
    current = {"ruff_total": sum(ruff_counts.values()),
               "ruff_by_code": dict(sorted(ruff_counts.items())),
               "mypy_errors": mypy_errors}

    if "--update-baseline" in sys.argv or not BASELINE_PATH.exists():
        BASELINE_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
        print(f"baseline zapisany: ruff={current['ruff_total']} mypy={mypy_errors} → {BASELINE_PATH}")
        return 0

    base = json.loads(BASELINE_PATH.read_text())
    ok = True
    if current["ruff_total"] > base["ruff_total"]:
        ok = False
        base_by = base.get("ruff_by_code", {})
        for code, n in current["ruff_by_code"].items():
            delta = n - base_by.get(code, 0)
            if delta > 0:
                print(f"RUFF ↑ {code}: +{delta} (teraz {n})")
    if current["mypy_errors"] > base["mypy_errors"]:
        ok = False
        print(f"MYPY ↑ errors: {base['mypy_errors']} → {current['mypy_errors']}")

    status = "ZIELONO" if ok else "CZERWONO"
    print(f"devlint {status}: ruff {current['ruff_total']}/{base['ruff_total']} · "
          f"mypy {current['mypy_errors']}/{base['mypy_errors']} (current/baseline)")
    if ok and (current["ruff_total"] < base["ruff_total"] or current["mypy_errors"] < base["mypy_errors"]):
        print("↓ mniej naruszeń niż baseline — rozważ --update-baseline (osobny, jawny commit)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
