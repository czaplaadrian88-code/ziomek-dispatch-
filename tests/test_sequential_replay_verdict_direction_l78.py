"""L7.8 — kierunek werdyktu w sequential_replay._determine_verdict (R7-I-G / INV-COH-7).

Cel: przyrząd bramkujący (GO/NO-GO fleet-level) musi mierzyć w KIERUNKU zgodnym
z celem metryki. `couriers_used` (rozłożenie floty) jest HIGHER-is-better —
więcej wykorzystanych kurierów = mniej pile-on = lepiej. Do L7.8 `_determine_verdict`
hardkodował lower-is-better dla KAŻDEGO targetu → dla `--target couriers_used`
zwracał NO-GO gdy realnie się poprawiał (latentna INWERSJA, C18 §Część C).

Izolacja: `sequential_replay.py` robi `os.execv` przy imporcie (determinizm
PYTHONHASHSEED) ORAZ globalnie podmienia `concurrent.futures.ThreadPoolExecutor`
i mutuje `common` na poziomie modułu → NIGDY nie importujemy go w procesie testów.
Uruchamiamy pure-funkcje w IZOLOWANYM subprocessie z PYTHONHASHSEED=0 (execv no-op),
ładując plik Z WORKTREE (importlib po ścieżce), by walidować MOJE zmiany.

Mutation-proof: przywrócenie inwersji (`base[target] - cand[target] > 0` dla
couriers_used) → test_couriers_used_more_is_go pada (GO→NO-GO).
"""
import json
import os
import subprocess
import sys

import pytest

_WT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEQ_PATH = os.path.join(_WT_ROOT, "tools", "sequential_replay.py")

# Driver odpalany w subprocessie: ładuje edytowany plik i liczy werdykty dla
# zestawu scenariuszy. Wynik (JSON) wraca na stdout między znacznikami.
_DRIVER = r'''
import importlib.util, json, os, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("seqreplay_l78", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def base_metrics(**over):
    m = {"sla_breaches": 3, "best_effort": 5, "zero_feasible": 1, "alerts": 2,
         "couriers_used": 5, "max_pile": 4, "pile_ratio": 2.0, "gini": 0.30,
         "peak_bag_max": 3}
    m.update(over)
    return m

def verdict(base, cand, target, gini_tol=0.02, pile_tol=0.10):
    delta = mod._metrics_delta(base, cand)
    return mod._determine_verdict(base, cand, delta, target, gini_tol, pile_tol)

out = {}
b = base_metrics()

# 1) couriers_used ROŚNIE (5→8), zero regresji guardrail → HIGHER-better = GO
c = base_metrics(couriers_used=8, max_pile=3, pile_ratio=1.6)
out["couriers_more"] = verdict(b, c, "couriers_used")

# 2) couriers_used SPADA (5→3) → pogorszenie = NO-GO(target_not_improved)
c = base_metrics(couriers_used=3)
out["couriers_fewer"] = verdict(b, c, "couriers_used")

# 3) lower-better target (sla_breaches) SPADA (3→1) → poprawa = GO (bez regresji)
c = base_metrics(sla_breaches=1)
out["sla_lower"] = verdict(b, c, "sla_breaches")

# 4) lower-better target ROŚNIE → regresja blokuje = NO-GO
c = base_metrics(sla_breaches=6)
out["sla_higher"] = verdict(b, c, "sla_breaches")

# 5) rejestr kierunku istnieje i couriers_used jest w higher-better
out["registry"] = sorted(mod._HIGHER_IS_BETTER)

print("=RESULT=" + json.dumps(out) + "=END=")
'''


@pytest.fixture(scope="module")
def verdicts():
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"  # execv przy imporcie staje się no-op
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, _SEQ_PATH],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        f"driver subprocess failed rc={proc.returncode}\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    raw = proc.stdout
    assert "=RESULT=" in raw and "=END=" in raw, f"brak wyniku w stdout:\n{raw}"
    payload = raw.split("=RESULT=", 1)[1].split("=END=", 1)[0]
    return json.loads(payload)


def test_couriers_used_more_is_go(verdicts):
    # RDZEŃ mutacyjny: więcej kurierów = poprawa rozłożenia = GO.
    # Stary (odwrócony) kierunek zwracał NO-GO → ten assert łapie regresję.
    verdict, blocked = verdicts["couriers_more"]
    assert verdict == "GO", (
        f"couriers_used 5->8 powinno być GO (higher-is-better), dostałem "
        f"{verdict} blocked={blocked} — INWERSJA kierunku wróciła"
    )
    assert blocked == []


def test_couriers_used_fewer_is_no_go(verdicts):
    verdict, blocked = verdicts["couriers_fewer"]
    assert verdict == "NO-GO"
    assert blocked == ["target_not_improved"]


def test_lower_better_target_improvement_still_go(verdicts):
    # Regresja ochronna: domyślne lower-better targety działają jak przedtem.
    verdict, blocked = verdicts["sla_lower"]
    assert verdict == "GO"
    assert blocked == []


def test_lower_better_target_regression_blocks(verdicts):
    verdict, blocked = verdicts["sla_higher"]
    assert verdict == "NO-GO"
    assert "sla_breaches" in blocked


def test_direction_registry_has_couriers_used(verdicts):
    assert verdicts["registry"] == ["couriers_used"]
