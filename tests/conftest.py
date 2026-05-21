"""Pytest config + autouse safety fixtures dla całej dispatch_v2/tests/.

Z2 fix 2026-05-07 (Lekcja #75): defense-in-depth Layer 2 against accidental
real Telegram sends z testów. Warstwy:
  L1: telegram_utils.send_admin_alert sprawdza PYTEST_CURRENT_TEST env (in-prod check)
  L2: ten conftest autouse monkeypatch (ratuje gdy L1 byłby kiedyś refaktorowany)
  L3: per-file mock_telegram fixtures (np. test_parser_health_layer3.py)

Opt-out: test wprost weryfikujący real send = `request.getfixturevalue` z markerem
LUB env ALLOW_TELEGRAM_IN_TEST=1 (omija L1).

────────────────────────────────────────────────────────────────────────────
De-erozja 2026-05-21 (audyt Ziomka): script-style collector.

Część plików `test_*.py` to "custom-runnery" pisane jako standalone skrypty
(historia: pytest nie był zainstalowany) — wykonują logikę testów na poziomie
modułu i kończą `sys.exit()`. Importowane przez pytest podczas kolekcji:
  - z `sys.exit()` poza `if __name__` → SystemExit przy imporcie → INTERNALERROR
    (cała komenda `pytest tests/` przerywana na pierwszym takim pliku),
  - albo runner pod `if __name__` bez funkcji `test_*` → pytest zbiera 0 testów.

Zamiast ryzykownej ręcznej konwersji ~60 plików, hook `pytest_pycollect_makemodule`
wykrywa pliki script-style (AST — auto-adaptacja do przyszłych plików) i zamiast
importować je w procesie pytest, uruchamia każdy jako subprocess `python -m
dispatch_v2.tests.<modul>` i mapuje exit-code na pass/fail. Dzięki temu `pytest
tests/` działa JEDNĄ komendą, a każdy script-runner daje realny sygnał (jego
własny `sys.exit(0 if ok else 1)` = pytest pass/fail). Zero zmian w plikach testów,
zero zmian w logice produkcji. Granularność per-funkcja = przyszły refactor.
"""
import ast
import os
import subprocess
import sys

import pytest

_SCRIPTS_ROOT = "/root/.openclaw/workspace/scripts"
_SUBPROC_TIMEOUT = 240


def _is_main_guard(test_node) -> bool:
    """True dla `if __name__ == "__main__":`."""
    return (
        isinstance(test_node, ast.Compare)
        and isinstance(test_node.left, ast.Name)
        and test_node.left.id == "__name__"
        and len(test_node.comparators) == 1
        and isinstance(test_node.comparators[0], ast.Constant)
        and test_node.comparators[0].value == "__main__"
    )


def _stmt_has_sysexit(node) -> bool:
    """True jeśli poddrzewo statementu zawiera sys.exit(...) / raise SystemExit."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Attribute) and f.attr == "exit":
                return True
            if isinstance(f, ast.Name) and f.id in ("exit", "quit"):
                return True
        if isinstance(sub, ast.Raise):
            exc = sub.exc
            name = None
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                name = exc.func.id
            elif isinstance(exc, ast.Name):
                name = exc.id
            if name == "SystemExit":
                return True
    return False


def _is_script_style(path) -> bool:
    """Native pytest NIE poradzi sobie z plikiem → kieruj do subprocess. Dwa sygnały:
      (A) NIEzguardowany module-level sys.exit/SystemExit → SystemExit przy imporcie
          = INTERNALERROR kolekcji (crash). Liczy się tylko poza def/class i poza
          `if __name__ == "__main__"`.
      (B) BRAK funkcji `test_*`/klas `Test*` + obecny runner (`if __name__` lub
          inline module-level kod) → pytest zebrałby 0 testów (script-runner).
    Pliki z `def test_*` i bez niezguardowanego sys.exit = normalny pytest (nie tu)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return False
    has_test_obj = False
    has_main_guard = False
    has_module_exec = False
    unguarded_sysexit = False
    for i, node in enumerate(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                has_test_obj = True
            continue  # ciało funkcji = nie wykonuje się przy imporcie
        if isinstance(node, ast.ClassDef):
            if node.name.startswith("Test"):
                has_test_obj = True
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node, ast.Expr) and i == 0 and isinstance(node.value, ast.Constant):
            continue  # module docstring
        if isinstance(node, ast.If) and _is_main_guard(node.test):
            has_main_guard = True
            continue  # __main__ guard nie odpala się przy imporcie
        # cokolwiek innego na poziomie modułu wykonuje się przy imporcie
        has_module_exec = True
        if _stmt_has_sysexit(node):
            unguarded_sysexit = True
    if unguarded_sysexit:
        return True
    if (has_main_guard or has_module_exec) and not has_test_obj:
        return True
    return False


class ScriptRunItem(pytest.Item):
    def __init__(self, *, name, parent, modname):
        super().__init__(name=name, parent=parent)
        self.modname = modname

    def runtest(self):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = "0"          # determinizm (Lekcja replay harness)
        env["PYTEST_CURRENT_TEST"] = self.modname  # L1 telegram-block guard ON
        env.setdefault("PYTHONPATH", _SCRIPTS_ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", self.modname],
            cwd=_SCRIPTS_ROOT, env=env, capture_output=True, text=True,
            timeout=_SUBPROC_TIMEOUT,
        )
        if proc.returncode != 0:
            raise ScriptRunError(self.modname, proc.returncode, proc.stdout, proc.stderr)

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ScriptRunError):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"script: {self.modname}"


class ScriptRunError(Exception):
    def __init__(self, modname, rc, out, err):
        tail_out = "\n".join((out or "").splitlines()[-25:])
        tail_err = "\n".join((err or "").splitlines()[-12:])
        super().__init__(
            f"[script-runner] {modname} exit={rc}\n"
            f"--- stdout (tail) ---\n{tail_out}\n--- stderr (tail) ---\n{tail_err}"
        )


class ScriptRunFile(pytest.File):
    def collect(self):
        modname = "dispatch_v2.tests." + self.path.stem
        yield ScriptRunItem.from_parent(self, name="script_run", modname=modname)


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makemodule(module_path, parent):
    """Przejmij kolekcję plików script-style (zamiast importować → subprocess)."""
    if _is_script_style(module_path):
        return ScriptRunFile.from_parent(parent, path=module_path)
    return None


# De-erozja 2026-05-21: izolacja stanu OSRM. Niektóre testy podmieniają
# `osrm_client.haversine/table/route` GLOBALNIE bez restore (np. dawniej
# test_decision_engine_f21 module-level, test_feasibility_c3 `_setup_mock`) →
# wyciek na całą sesję pytest → pass-solo/fail-w-suicie dla test_uwagi_defense_gates,
# test_f4_courier_pos_interp, test_v326_traffic_multiplier. Conftest importuje się
# PRZED modułami testów → łapiemy pristine i przywracamy po każdym teście.
try:
    import dispatch_v2.osrm_client as _osrm_mod
    _PRISTINE_OSRM = {
        n: getattr(_osrm_mod, n)
        for n in ("haversine", "route", "table")
        if hasattr(_osrm_mod, n)
    }
except Exception:  # pragma: no cover
    _osrm_mod = None
    _PRISTINE_OSRM = {}


@pytest.fixture(autouse=True)
def _restore_osrm_state():
    """Przywróć pristine osrm_client.{haversine,route,table} po każdym teście."""
    yield
    if _osrm_mod is not None:
        for _n, _fn in _PRISTINE_OSRM.items():
            setattr(_osrm_mod, _n, _fn)


@pytest.fixture(autouse=True)
def _block_real_telegram_sends(monkeypatch, request):
    """Default-block dla send_admin_alert na czas testu.

    Override w teście: zmocuj atrybut samodzielnie (last monkeypatch wins).
    Np. mock_telegram fixture w test_parser_health_layer3.py podmienia na
    capture-lambda i autouse override jest nadpisany.
    """
    try:
        from dispatch_v2 import telegram_utils
    except ImportError:
        return
    monkeypatch.setattr(
        telegram_utils,
        "send_admin_alert",
        lambda text: True,
        raising=False,
    )
