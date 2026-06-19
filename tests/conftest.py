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
        # ETAP 4 (2026-06-10, Z-04): subprocess nie dostaje fixture
        # _isolate_flags_json → bez tego czytałby ŻYWY flags.json z flagami
        # decyzyjnymi (wartości shadow) zamiast env-defaultów jak dotąd.
        stripped = _stripped_flags_copy()
        if stripped:
            env["DISPATCH_FLAGS_PATH"] = stripped
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


_STRIPPED_FLAGS_PATH = None


def _stripped_flags_copy():
    """Kopia żywego flags.json BEZ flag decyzyjnych ETAP 4 (raz per sesję pytest).

    Script-runnery (subprocess) dostają ją przez env DISPATCH_FLAGS_PATH →
    common.FLAGS_PATH; flagi decyzyjne wracają do env-defaultów (zachowanie
    testów identyczne jak przed unifikacją ETAP 4)."""
    global _STRIPPED_FLAGS_PATH
    if _STRIPPED_FLAGS_PATH is not None:
        return _STRIPPED_FLAGS_PATH
    import json
    import tempfile
    try:
        from dispatch_v2 import common as _c
        with open(os.path.join(_SCRIPTS_ROOT, "flags.json")) as f:
            d = json.load(f)
        for k in getattr(_c, "ETAP4_DECISION_FLAGS", ()):
            d.pop(k, None)
        # E7-doklejka 3: numeryczne override'y stałych (BUG A/B) też precz —
        # testy sterują przez patch stałej modułu, nie żywy flags.json.
        for k in getattr(_c, "FLAGS_JSON_NUMERIC_OVERRIDES", ()):
            d.pop(k, None)
        # Front C (2026-06-12): killswitche infra (table-cache/prefetch) precz —
        # testy nie dziedziczą żywych przełączników (determinizm).
        for k in getattr(_c, "TEST_ISOLATED_INFRA_FLAGS", ()):
            d.pop(k, None)
        fd, p = tempfile.mkstemp(prefix="flags_etap4_stripped_", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        _STRIPPED_FLAGS_PATH = p
    except Exception:
        _STRIPPED_FLAGS_PATH = ""
    return _STRIPPED_FLAGS_PATH


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
#
# 2026-06-19 (P1#4): bez scripts/ na sys.path TEN import cicho failował
# (ModuleNotFoundError → _osrm_mod=None) → CAŁY restore był MARTWY i pollution
# c3 (FakeHaversine→2.0) wyciekała na mass_fail_fallback/coord_poison/return_to_restaurant.
# Insert poniżej ożywia restore (i daje pakiet wszystkim testom bez per-plik sys.path).
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)
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
def _isolate_flags_json(monkeypatch, tmp_path):
    """L2 (lekcja #180, PARSE-01): globalna izolacja flags.json od testów.

    Incydent 06.06: test_v320_packs_ghost wołał pośrednio parse_continuity_guard,
    który zapisał PARSER_DEGRADED=true do PRODUKCYJNEGO flags.json → AUTO=0 przez
    5 dni. Warstwy obrony:
      L1: parse_continuity_guard._set_parser_degraded odmawia zapisu pod
          PYTEST_CURRENT_TEST (chroni też script-runnery subprocess, które
          NIE dostają tego fixture — ScriptRunItem ustawia im env).
      L2: ten fixture — kopiuje żywy flags.json do tmp_path i patchuje
          FLAGS_PATH w common + parse_continuity_guard + core.flags_io.
          Odczyty widzą te same wartości flag (kopia żywego pliku — zero zmiany
          zachowania testów), zapisy lądują w tmp.
      L3: per-file patche (np. test_parse_continuity_guard._patch_flags) zostają.

    UWAGA cache: common.load_flags() cache'uje po mtime — resetujemy przed i po
    teście, inaczej tmp-cache przeciekłby do kolejnych odczytów produkcyjnych.
    """
    import shutil
    from pathlib import Path as _P
    try:
        from dispatch_v2 import common
    except ImportError:
        yield
        return
    _live = _P("/root/.openclaw/workspace/scripts/flags.json")
    _tmp_flags = tmp_path / "flags.json"
    try:
        shutil.copyfile(_live, _tmp_flags)
    except OSError:
        _tmp_flags.write_text("{}", encoding="utf-8")
    # ETAP 4 (2026-06-10, Z-04): wytnij flagi DECYZYJNE z kopii — w produkcji
    # flags.json jest dla nich kanonem (wartości shadow), ale testy muszą dalej
    # sterować zachowaniem przez patch stałej modułu (common.ENABLE_X /
    # courier_resolver.ENABLE_F4_*). decision_flag() przy braku klucza spada
    # na stałą modułu → idiom testów sprzed unifikacji działa bez zmian.
    try:
        import json as _json
        _d = _json.loads(_tmp_flags.read_text(encoding="utf-8"))
        for _k in getattr(common, "ETAP4_DECISION_FLAGS", ()):
            _d.pop(_k, None)
        for _k in getattr(common, "FLAGS_JSON_NUMERIC_OVERRIDES", ()):
            _d.pop(_k, None)
        for _k in getattr(common, "TEST_ISOLATED_INFRA_FLAGS", ()):
            _d.pop(_k, None)
        _tmp_flags.write_text(_json.dumps(_d), encoding="utf-8")
    except Exception:
        pass
    monkeypatch.setattr(common, "FLAGS_PATH", _tmp_flags)
    common._flags_cache = None
    common._flags_mtime = 0
    try:
        from dispatch_v2 import parse_continuity_guard as _pcg
        monkeypatch.setattr(_pcg, "FLAGS_PATH", str(_tmp_flags))
    except ImportError:
        pass
    try:
        from dispatch_v2.core import flags_io as _fio
        monkeypatch.setattr(_fio, "FLAGS_PATH", _tmp_flags)
    except ImportError:
        pass
    yield
    common._flags_cache = None
    common._flags_mtime = 0


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
