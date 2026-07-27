"""R4 step 2: module paths have one owner and direct entrypoints are isolated."""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = next(
    Path(entry)
    for entry in sys.path
    if entry
    and (Path(entry) / "dispatch_v2").exists()
    and (Path(entry) / "dispatch_v2").samefile(REPO_ROOT)
)
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
COUPLED_FILES = (
    "_physical_import.py",
    "decision_eta_log.py",
    "shadow_dispatcher.py",
    "tools/decision_eta_coverage.py",
)
DIRECT_ENTRYPOINT_FILES = (
    "cod_weekly/restaurant_mapper.py",
    "cod_weekly/run_weekly.py",
    "core/jsonl_rotation.py",
    "daily_accounting/main.py",
    "ml_data_prep/arbitrage_forward.py",
    "ml_data_prep/forward_validation.py",
    "ml_data_prep/online_shadow_parity.py",
    "ml_data_prep/parity_ml_inference.py",
    "ml_data_prep/train_two_models.py",
    "monitoring/detector_419.py",
    "observability/cron_health.py",
    "observability/data_alerts.py",
    "observability/delivered_integrity_monitor.py",
    "observability/ground_truth_gc.py",
    "observability/koord_cascade_monitor.py",
    "observability/liveness_probe.py",
    "observability/log_rotation.py",
    "reconciliation/reconcile_worker.py",
    "shift_notifications/worker.py",
)
RUNTIME_ENTRYPOINT_FILES = (
    "core/jsonl_rotation.py",
    "daily_accounting/main.py",
    "observability/cron_health.py",
    "observability/data_alerts.py",
    "observability/delivered_integrity_monitor.py",
    "observability/ground_truth_gc.py",
    "observability/koord_cascade_monitor.py",
    "observability/liveness_probe.py",
    "observability/log_rotation.py",
    "reconciliation/reconcile_worker.py",
)
FORBIDDEN_HOST_PATH_PREFIXES = (
    "/root/.openclaw/workspace/scripts",
    "/root/.openclaw/workspace/dispatch_state",
)
STATE_ENV_NAMES = (
    "DISPATCH_STATE_DIR",
    "ZIOMEK_STATE_DIR",
    "ZIOMEK_STATE_ROOT",
)
PACKAGE_IMPORT_ROOTS = frozenset(
    {path.stem for path in REPO_ROOT.glob("*.py")}
    | {
        path.name
        for path in REPO_ROOT.iterdir()
        if path.is_dir() and any(path.glob("*.py"))
    }
)


def _migrated_python_files():
    for module_dir in MODULE_DIRS:
        for path in sorted((REPO_ROOT / module_dir).rglob("*.py")):
            if "tests" not in path.relative_to(REPO_ROOT).parts:
                yield path
    for relative in COUPLED_FILES:
        yield REPO_ROOT / relative


def _literal_path_value(node: ast.AST) -> str | None:
    """Evaluate only literal strings and their direct Path/string composition."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Call)
        and len(node.args) == 1
        and not node.keywords
        and (
            isinstance(node.func, ast.Name)
            and node.func.id == "Path"
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "Path"
        )
    ):
        return _literal_path_value(node.args[0])
    if (
        isinstance(node, ast.Call)
        and not node.keywords
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "path"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "os"
    ):
        values = tuple(_literal_path_value(argument) for argument in node.args)
        if values and all(value is not None for value in values):
            return os.path.join(*(value for value in values if value is not None))
    if isinstance(node, ast.BinOp):
        left = _literal_path_value(node.left)
        right = _literal_path_value(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Div):
            return str(Path(left) / right)
        if isinstance(node.op, ast.Add):
            return left + right
    return None


def _direct_host_path_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Find direct and mechanically composed host-bound path literals."""
    offenders = {
        (node.lineno, value)
        for node in ast.walk(tree)
        if (value := _literal_path_value(node)) is not None
        and value.startswith(FORBIDDEN_HOST_PATH_PREFIXES)
    }
    return sorted(offenders)


def test_production_subpackages_have_no_host_path_literals() -> None:
    offenders = []
    for path in _migrated_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)}:{line}: {value}"
            for line, value in _direct_host_path_literals(tree)
        )
    assert not offenders, (
        "R4 step 2: restored a direct host-bound literal instead of common.py:\n"
        + "\n".join(offenders)
    )


def test_host_literal_ratchet_has_a_negative_oracle() -> None:
    tree = ast.parse(
        "STATE_DIR = Path('/root/.openclaw/workspace/dispatch_state')\n"
    )
    assert _direct_host_path_literals(tree) == [
        (1, "/root/.openclaw/workspace/dispatch_state")
    ]


def test_host_literal_ratchet_rejects_composed_path() -> None:
    tree = ast.parse(
        "STATE_DIR = Path('/root/.openclaw/workspace') / 'dispatch_state'\n"
    )
    assert _direct_host_path_literals(tree) == [
        (1, "/root/.openclaw/workspace/dispatch_state")
    ]


def test_host_literal_ratchet_rejects_os_path_join() -> None:
    tree = ast.parse(
        "STATE_DIR = os.path.join("
        "'/root/.openclaw/workspace', 'dispatch_state')\n"
    )
    assert _direct_host_path_literals(tree) == [
        (1, "/root/.openclaw/workspace/dispatch_state")
    ]


def _is_dispatch_import(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.ImportFrom):
        return bool(statement.module and statement.module.startswith("dispatch_v2"))
    if isinstance(statement, ast.Import):
        return any(alias.name.startswith("dispatch_v2") for alias in statement.names)
    return False


def _is_common_import(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.ImportFrom)
        and statement.level == 0
        and statement.module == "dispatch_v2.common"
    )


_PATH_SANITIZER_SOURCE = (
    "import sys\n"
    "\n"
    "_ORIGINAL_SYS_PATH = tuple(sys.path)\n"
    "_RUNTIME_PYTHON_VERSION = (\n"
    "    f'python{sys.version_info.major}.{sys.version_info.minor}'\n"
    ")\n"
    "_RUNTIME_PYTHON_ZIP = (\n"
    "    f'python{sys.version_info.major}{sys.version_info.minor}.zip'\n"
    ")\n"
    "_TRUSTED_STDLIB_PATHS = frozenset(\n"
    "    path\n"
    "    for prefix in dict.fromkeys(\n"
    "        str(prefix).rstrip('/')\n"
    "        for prefix in (sys.base_prefix, sys.prefix, sys.exec_prefix)\n"
    "        if prefix\n"
    "    )\n"
    "    for path in (\n"
    "        f'{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}',\n"
    "        f'{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/lib-dynload',\n"
    "        f'{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_ZIP}',\n"
    "    )\n"
    ")\n"
    "_TRUSTED_SITE_PATHS = frozenset(\n"
    "    path\n"
    "    for prefix in dict.fromkeys(\n"
    "        str(prefix).rstrip('/')\n"
    "        for prefix in (sys.base_prefix, sys.prefix, sys.exec_prefix)\n"
    "        if prefix\n"
    "    )\n"
    "    for path in (\n"
    "        f'{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/site-packages',\n"
    "        f'{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/dist-packages',\n"
    "        f'{prefix}/local/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/dist-packages',\n"
    "        f'{prefix}/{sys.platlibdir}/python{sys.version_info.major}/dist-packages',\n"
    "    )\n"
    ")\n"
    "sys.path[:] = [\n"
    "    entry\n"
    "    for entry in sys.path\n"
    "    if entry and entry in _TRUSTED_STDLIB_PATHS\n"
    "]\n"
)
_PATH_SANITIZER_AST = [
    ast.dump(statement, include_attributes=False)
    for statement in ast.parse(_PATH_SANITIZER_SOURCE).body
]


_BOOTSTRAP_SOURCE = (
    "if __package__ in (None, ''):\n"
    "    _package_dir = Path(__file__).resolve().parent.parent\n"
    "    _package_init = _package_dir / '__init__.py'\n"
    "    if not _package_init.is_file():\n"
    "        raise RuntimeError('cannot locate physical dispatch_v2 package')\n"
    "    if any(\n"
    "        name == 'dispatch_v2' or name.startswith('dispatch_v2.')\n"
    "        for name in sys.modules\n"
    "    ):\n"
    "        raise RuntimeError('conflicting preloaded dispatch_v2 package')\n"
    "    _trusted_local_paths = (\n"
    "        str(_package_dir),\n"
    "        str(Path(__file__).resolve().parent),\n"
    "    )\n"
    "    sys.path[:] = list(\n"
    "        dict.fromkeys(\n"
    "            (\n"
    "                *_trusted_local_paths,\n"
    "                *(\n"
    "                    entry\n"
    "                    for entry in _ORIGINAL_SYS_PATH\n"
    "                    if entry in _TRUSTED_STDLIB_PATHS\n"
    "                    or entry in _TRUSTED_SITE_PATHS\n"
    "                ),\n"
    "            )\n"
    "        )\n"
    "    )\n"
    "    _package_spec = importlib.util.spec_from_file_location(\n"
    "        'dispatch_v2',\n"
    "        _package_init,\n"
    "        submodule_search_locations=[str(_package_dir)],\n"
    "    )\n"
    "    if _package_spec is None or _package_spec.loader is None:\n"
    "        raise RuntimeError('cannot attest physical dispatch_v2 package')\n"
    "    _package_module = importlib.util.module_from_spec(_package_spec)\n"
    "    sys.modules['dispatch_v2'] = _package_module\n"
    "    try:\n"
    "        _package_spec.loader.exec_module(_package_module)\n"
    "    except BaseException:\n"
    "        sys.modules.pop('dispatch_v2', None)\n"
    "        raise\n"
    "else:\n"
    "    sys.path[:] = _ORIGINAL_SYS_PATH\n"
)
_BOOTSTRAP_AST = ast.dump(
    ast.parse(_BOOTSTRAP_SOURCE).body[0],
    include_attributes=False,
)


def _is_stdlib_import(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Import):
        roots = [alias.name.split(".", 1)[0] for alias in statement.names]
    elif isinstance(statement, ast.ImportFrom) and statement.level == 0:
        roots = [(statement.module or "").split(".", 1)[0]]
    else:
        return False
    return all(root == "__future__" or root in sys.stdlib_module_names for root in roots)


def _entrypoint_has_canonical_bootstrap(source: str) -> bool:
    """Require a fixed prefix, not partial interpretation of arbitrary Python."""
    tree = ast.parse(source)
    dispatch_index = next(
        (index for index, stmt in enumerate(tree.body) if _is_dispatch_import(stmt)),
        None,
    )
    if dispatch_index is None or dispatch_index == 0:
        return False
    guard_index = dispatch_index - 1
    if ast.dump(tree.body[guard_index], include_attributes=False) != _BOOTSTRAP_AST:
        return False
    if not _is_common_import(tree.body[dispatch_index]):
        return False
    prefix = tree.body[:guard_index]
    if prefix and isinstance(prefix[0], ast.Expr):
        value = prefix[0].value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return False
        prefix = prefix[1:]
    while (
        prefix
        and isinstance(prefix[0], ast.ImportFrom)
        and prefix[0].module == "__future__"
    ):
        prefix = prefix[1:]
    sanitizer_size = len(_PATH_SANITIZER_AST)
    if [
        ast.dump(statement, include_attributes=False)
        for statement in prefix[:sanitizer_size]
    ] != _PATH_SANITIZER_AST:
        return False
    prefix = prefix[sanitizer_size:]
    if not prefix or not all(_is_stdlib_import(stmt) for stmt in prefix):
        return False
    bound_sys = []
    bound_path = []
    bound_importlib = []
    for stmt in prefix:
        for alias in stmt.names:
            bound_name = (
                alias.asname
                or (
                    alias.name.split(".", 1)[0]
                    if isinstance(stmt, ast.Import)
                    else alias.name
                )
            )
            if bound_name == "sys":
                bound_sys.append((stmt, alias))
            if bound_name == "Path":
                bound_path.append((stmt, alias))
            if bound_name == "importlib":
                bound_importlib.append((stmt, alias))
    return (
        not bound_sys
        and len(bound_path) == 1
        and isinstance(bound_path[0][0], ast.ImportFrom)
        and bound_path[0][0].module == "pathlib"
        and bound_path[0][1].name == "Path"
        and bound_path[0][1].asname is None
        and len(bound_importlib) == 1
        and isinstance(bound_importlib[0][0], ast.Import)
        and bound_importlib[0][1].name == "importlib.util"
        and bound_importlib[0][1].asname is None
    )


def test_every_migrated_direct_entrypoint_has_fixed_bootstrap_prefix() -> None:
    offenders = [
        relative
        for relative in DIRECT_ENTRYPOINT_FILES
        if not _entrypoint_has_canonical_bootstrap(
            (REPO_ROOT / relative).read_text(encoding="utf-8")
        )
    ]
    assert not offenders, "invalid direct-entrypoint prefix: " + ", ".join(offenders)


def _post_bootstrap_sys_path_mutations(source: str) -> list[int]:
    """Find direct sys.path mutation after the canonical common import."""
    tree = ast.parse(source)
    common_import = next(
        (
            statement
            for statement in tree.body
            if _is_common_import(statement)
        ),
        None,
    )
    if common_import is None:
        return []
    boundary = common_import.end_lineno or common_import.lineno

    def is_sys_path(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
            and node.attr == "path"
        )

    def targets_sys_path(node: ast.AST) -> bool:
        return is_sys_path(node) or (
            isinstance(node, ast.Subscript)
            and is_sys_path(node.value)
        )

    offenders = set()
    mutating_methods = {
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "sort",
    }
    for node in ast.walk(tree):
        if getattr(node, "lineno", 0) <= boundary:
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in mutating_methods
            and is_sys_path(node.func.value)
        ):
            offenders.add(node.lineno)
        if isinstance(node, (ast.Assign, ast.Delete)):
            targets = node.targets
            if any(targets_sys_path(target) for target in targets):
                offenders.add(node.lineno)
        if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if targets_sys_path(node.target):
                offenders.add(node.lineno)
    return sorted(offenders)


def test_direct_entrypoints_never_reopen_sys_path_after_package_bootstrap() -> None:
    offenders = []
    for relative in DIRECT_ENTRYPOINT_FILES:
        lines = _post_bootstrap_sys_path_mutations(
            (REPO_ROOT / relative).read_text(encoding="utf-8")
        )
        offenders.extend(f"{relative}:{line}" for line in lines)
    assert not offenders, (
        "post-bootstrap sys.path trust expansion: " + ", ".join(offenders)
    )


def test_post_bootstrap_sys_path_ratchet_has_negative_oracle() -> None:
    source = (
        "from dispatch_v2.common import SCRIPTS_DIR\n"
        "sys.path.insert(0, str(SCRIPTS_DIR))\n"
    )
    assert _post_bootstrap_sys_path_mutations(source) == [2]


def _unqualified_package_imports(source: str) -> list[int]:
    """Find imports that let site-packages impersonate a package member."""
    offenders = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").split(".", 1)[0]
            in PACKAGE_IMPORT_ROOTS
        ):
            offenders.add(node.lineno)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in PACKAGE_IMPORT_ROOTS:
                    offenders.add(node.lineno)
    return sorted(offenders)


def test_direct_entrypoints_use_only_qualified_package_imports() -> None:
    offenders = []
    for relative in DIRECT_ENTRYPOINT_FILES:
        lines = _unqualified_package_imports(
            (REPO_ROOT / relative).read_text(encoding="utf-8")
        )
        offenders.extend(f"{relative}:{line}" for line in lines)
    assert not offenders, (
        "unqualified dispatch_v2 package import: " + ", ".join(offenders)
    )


def test_qualified_package_import_ratchet_has_negative_oracle() -> None:
    assert _unqualified_package_imports(
        "from ml_data_prep import train_two_models\n"
    ) == [1]


def test_direct_entrypoint_bootstrap_never_indexes_path_parents() -> None:
    offenders = []
    for relative in DIRECT_ENTRYPOINT_FILES:
        tree = ast.parse(
            (REPO_ROOT / relative).read_text(encoding="utf-8"),
            filename=relative,
        )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "parents"
            ):
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "shallow direct-entrypoint path can raise IndexError: "
        + ", ".join(offenders)
    )


@pytest.mark.parametrize(
    "premature",
    (
        "raise RuntimeError('stop')\n",
        "assert False\n",
        "while True:\n    pass\n",
        "class Premature:\n    import dispatch_v2\n",
        "__import__('dispatch_v2')\n",
        "import os as sys\n",
        "import os as importlib\n",
        "from os import Path\n",
    ),
)
def test_entrypoint_prefix_rejects_executable_prebootstrap_code(premature) -> None:
    source = (
        f"{_PATH_SANITIZER_SOURCE}"
        "from pathlib import Path\n"
        f"{premature}"
        f"{_BOOTSTRAP_SOURCE}"
        "from dispatch_v2.common import STATE_DIR\n"
    )
    assert not _entrypoint_has_canonical_bootstrap(source)


def _state_env_accesses(tree: ast.AST) -> list[tuple[int, str]]:
    """Ban an owned alias, direct or split, within one consumer statement."""
    offenders = set()
    for statement in ast.walk(tree):
        if not isinstance(statement, ast.stmt):
            continue
        string_nodes = sorted(
            (
                child
                for child in ast.walk(statement)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, str)
            ),
            key=lambda child: (
                getattr(child, "lineno", 0),
                getattr(child, "col_offset", 0),
            ),
        )
        fragments = "".join(child.value for child in string_nodes)
        offenders.update(
            (statement.lineno, name)
            for name in STATE_ENV_NAMES
            if name in fragments
        )
    return sorted(offenders)


def test_migrated_consumers_do_not_resolve_state_env_again() -> None:
    offenders = []
    for path in _migrated_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)}:{line}: {name}"
            for line, name in _state_env_accesses(tree)
        )
    assert not offenders, "competing state env resolver:\n" + "\n".join(offenders)


def test_state_env_ratchet_rejects_composed_alias() -> None:
    tree = ast.parse(
        "ROOT = Path(os.getenv('DISPATCH_' + 'STATE_DIR') or C.STATE_DIR)\n"
    )
    assert _state_env_accesses(tree) == [(1, "DISPATCH_STATE_DIR")]


def _clean_path_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        *STATE_ENV_NAMES,
        "ZIOMEK_SCRIPTS_ROOT",
        "ZIOMEK_LOGS_DIR",
        "SHADOW_DECISIONS_LOG",
    ):
        env.pop(name, None)
    env["PYTHONPATH"] = str(PACKAGE_PARENT)
    return env


def test_subprocess_path_env_preserves_hermetic_registry() -> None:
    env = _clean_path_env()
    assert env["DISPATCH_JSONL_ROTATION_REGISTRY"] == os.environ[
        "DISPATCH_JSONL_ROTATION_REGISTRY"
    ]
    assert env["DISPATCH_JSONL_ROTATION_REGISTRY"] != (
        "/root/.openclaw/workspace/dispatch_state/jsonl_rotation_paths.json"
    )


def test_root_conftest_forces_registry_under_state_dir(tmp_path) -> None:
    state_dir = tmp_path / "state"
    escaped_registry = tmp_path / "escaped" / "jsonl_rotation_paths.json"
    env = _clean_path_env()
    env.update(
        {
            "DISPATCH_STATE_DIR": str(state_dir),
            "DISPATCH_JSONL_ROTATION_REGISTRY": str(escaped_registry),
            "HERMETIC_SUBPROCESS_GUARD": "0",
            "ZIOMEK_SCRIPTS_ROOT": str(PACKAGE_PARENT),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import conftest, os; "
                "print(os.environ['DISPATCH_JSONL_ROTATION_REGISTRY'])"
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == str(state_dir / "jsonl_rotation_paths.json")


def test_common_state_dir_precedence_is_declarative_and_effective(tmp_path) -> None:
    values = {
        "DISPATCH_STATE_DIR": str(tmp_path / "dispatch"),
        "ZIOMEK_STATE_DIR": str(tmp_path / "ziomek"),
        "ZIOMEK_STATE_ROOT": str(tmp_path / "legacy"),
    }
    env = _clean_path_env()
    env.update(values)
    code = (
        "from dispatch_v2 import common as C\n"
        f"assert C.STATE_DIR_ENV_PRECEDENCE == {STATE_ENV_NAMES!r}\n"
        f"assert str(C.STATE_DIR) == {values['DISPATCH_STATE_DIR']!r}\n"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def test_registry_default_is_stable_across_dispatch_state_roots(
    tmp_path,
) -> None:
    observed = []
    for name in ("first-state", "second-state"):
        env = _clean_path_env()
        env["DISPATCH_STATE_DIR"] = str(tmp_path / name)
        env.pop("DISPATCH_JSONL_ROTATION_REGISTRY", None)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from dispatch_v2 import common as C;"
                    "print(C.JSONL_ROTATION_REGISTRY_PATH)"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        observed.append(completed.stdout.strip())
    assert observed == [
        "/var/lib/dispatch-v2/jsonl_rotation_paths.json",
        "/var/lib/dispatch-v2/jsonl_rotation_paths.json",
    ]


@pytest.mark.parametrize(
    "name",
    (*STATE_ENV_NAMES, "ZIOMEK_SCRIPTS_ROOT", "ZIOMEK_LOGS_DIR"),
)
@pytest.mark.parametrize("unsafe", ("relative/path", "/var/lib/state#blue"))
def test_common_rejects_roots_a_writer_cannot_safely_rotate(name, unsafe) -> None:
    env = _clean_path_env()
    env[name] = unsafe
    completed = subprocess.run(
        [sys.executable, "-c", "import dispatch_v2.common"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode != 0
    assert (
        "must be absolute" in completed.stderr
        or "unsafe characters" in completed.stderr
        or "patterns are forbidden" in completed.stderr
    )


@pytest.mark.parametrize("label", ("SCRIPTS_DIR", "STATE_DIR", "LOGS_DIR"))
def test_common_canonicalizes_symlink_root_snapshot(
    tmp_path,
    label,
) -> None:
    from dispatch_v2 import common as C

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    alias = tmp_path / "active-root"
    alias.symlink_to(first, target_is_directory=True)
    pinned = C._validate_runtime_path(alias, label=label)
    assert pinned == first.resolve()

    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)
    assert pinned == first.resolve()
    assert pinned != alias.resolve()
    assert "privileged rename" in C.RUNTIME_NAMESPACE_TRUST_BOUNDARY


def test_jsonl_path_rejects_unstable_final_parent_component(tmp_path) -> None:
    from dispatch_v2 import common as C

    unstable = tmp_path / "missing" / ".."
    with pytest.raises(ValueError, match="final component"):
        C.validate_jsonl_path(unstable)


def test_default_production_roots_pin_registry_outside_data_root() -> None:
    env = _clean_path_env()
    env.pop("DISPATCH_JSONL_ROTATION_REGISTRY", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from dispatch_v2 import common as C;"
                "print(C.SCRIPTS_DIR);print(C.STATE_DIR);print(C.LOGS_DIR);"
                "print(C.JSONL_ROTATION_REGISTRY_PATH)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.stdout.splitlines() == [
        "/root/.openclaw/workspace/scripts",
        "/root/.openclaw/workspace/dispatch_state",
        "/root/.openclaw/workspace/scripts/logs",
        "/var/lib/dispatch-v2/jsonl_rotation_paths.json",
    ]


def test_logrotate_control_defaults_match_the_service_carrier() -> None:
    from dispatch_v2 import common as C

    service = (
        REPO_ROOT / "deploy/dispatch-v2-jsonl-logrotate.service"
    ).read_text(encoding="utf-8")
    expected_tail = (
        f" {C.JSONL_LOGROTATE_CONFIG_PATH}"
        f" --state {C.JSONL_LOGROTATE_STATE_PATH}"
    )
    assert expected_tail in service
    assert str(C.JSONL_LOGROTATE_CONFIG_PATH) == (
        "/etc/logrotate-dispatch-v2-jsonl.conf"
    )
    assert str(C.JSONL_LOGROTATE_STATE_PATH) == (
        "/var/lib/logrotate/dispatch-v2-jsonl.status"
    )


def test_static_writer_and_rotator_share_registry_across_state_snapshots(
    tmp_path,
) -> None:
    shared_registry = tmp_path / "control" / "jsonl_rotation_paths.json"
    writer_state = tmp_path / "writer-state"
    rotator_state = tmp_path / "rotator-state"
    writer_env = _clean_path_env()
    writer_env["DISPATCH_STATE_DIR"] = str(writer_state)
    writer_env["DISPATCH_JSONL_ROTATION_REGISTRY"] = str(shared_registry)
    writer_code = (
        "from dispatch_v2 import decision_eta_log as D\n"
        "from dispatch_v2.core.jsonl_appender import append_jsonl_batch\n"
        "append_jsonl_batch(D.LOG_PATH, [{'event': 'cross-process-probe'}])\n"
    )
    subprocess.run(
        [sys.executable, "-c", writer_code],
        check=True,
        capture_output=True,
        text=True,
        env=writer_env,
    )

    rotator_env = _clean_path_env()
    rotator_env["DISPATCH_STATE_DIR"] = str(rotator_state)
    rotator_env["DISPATCH_JSONL_ROTATION_REGISTRY"] = str(shared_registry)
    shadow_path = tmp_path / "shadow.jsonl"
    rotator_code = (
        "import json\n"
        "from dispatch_v2.core.jsonl_rotation import resolve_jsonl_paths\n"
        f"cfg = {{'paths': {{'shadow_log': {str(shadow_path)!r}}}}}\n"
        "print(json.dumps(resolve_jsonl_paths(cfg)))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", rotator_code],
        check=True,
        capture_output=True,
        text=True,
        env=rotator_env,
    )
    paths = json.loads(completed.stdout)
    assert str(writer_state / "decision_eta_log.jsonl") in paths
    assert str(rotator_state / "decision_eta_log.jsonl") in paths


def test_shadow_writer_registry_preserves_old_and_current_config_paths(tmp_path) -> None:
    from dispatch_v2.core import jsonl_rotation as rotation

    registry = tmp_path / "jsonl_rotation_paths.json"
    old_path = tmp_path / "old" / "shadow.jsonl"
    current_path = tmp_path / "current" / "shadow.jsonl"
    rotation.register_shadow_decisions_writer_path(
        {"paths": {"shadow_log": str(old_path)}},
        registry_path=registry,
    )
    paths = rotation.resolve_jsonl_paths(
        {"paths": {"shadow_log": str(current_path)}},
        registry_path=registry,
    )
    assert str(old_path) in paths
    assert str(current_path) in paths
    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 2,
        "writers": [{"path": str(old_path), "role": "shadow"}],
    }
    assert registry.stat().st_mode & 0o777 == 0o600


def test_shadow_writer_registry_is_monotonic_and_rejects_corruption(tmp_path) -> None:
    from dispatch_v2.core import jsonl_rotation as rotation

    registry = tmp_path / "jsonl_rotation_paths.json"
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    rotation.register_jsonl_writer_path(first, registry_path=registry)
    rotation.register_jsonl_writer_path(second, registry_path=registry)
    assert rotation.registered_jsonl_paths(registry) == tuple(
        sorted((str(first), str(second)))
    )
    registry.write_text("{broken", encoding="utf-8")
    with pytest.raises(rotation.JsonlRotationConfigError):
        rotation.registered_jsonl_paths(registry)


def test_shadow_writer_registry_serializes_concurrent_publishers(tmp_path) -> None:
    registry = tmp_path / "jsonl_rotation_paths.json"
    writer_paths = [tmp_path / f"writer-{index}.jsonl" for index in range(8)]
    code = (
        "from dispatch_v2.core.jsonl_rotation import register_jsonl_writer_path\n"
        "import sys\n"
        "register_jsonl_writer_path(sys.argv[1], registry_path=sys.argv[2])\n"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path), str(registry)],
            env=_clean_path_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for path in writer_paths
    ]
    results = [process.communicate(timeout=10) for process in processes]
    assert [process.returncode for process in processes] == [0] * len(processes)
    assert results == [("", "")] * len(processes)
    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert payload["writers"] == [
        {"path": str(path), "role": "static"}
        for path in sorted(writer_paths)
    ]


def test_shadow_dispatcher_uses_registering_owner_not_raw_config() -> None:
    source = (REPO_ROOT / "shadow_dispatcher.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    run = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    calls = [
        node
        for node in ast.walk(run)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register_shadow_decisions_writer_path"
    ]
    raw_shadow_keys = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "shadow_log"
    ]
    assert len(calls) == 1
    assert len(calls[0].args) == 1
    assert isinstance(calls[0].args[0], ast.Name)
    assert calls[0].args[0].id == "cfg"
    assert not raw_shadow_keys
    append_observation = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "append_czasowka_reclaim_observation"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register_shadow_jsonl_writer_path"
        for node in ast.walk(append_observation)
    )


def test_shadow_decisions_input_alias_does_not_enter_rotation(tmp_path) -> None:
    shadow_input = tmp_path / "read-only-shadow.jsonl"
    writer = tmp_path / "writer-shadow.jsonl"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "config.json").write_text(
        json.dumps({"paths": {"shadow_log": str(writer)}}),
        encoding="utf-8",
    )
    env = _clean_path_env()
    env["SHADOW_DECISIONS_LOG"] = str(shadow_input)
    env["ZIOMEK_SCRIPTS_ROOT"] = str(scripts)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    code = (
        "from dispatch_v2 import common as C\n"
        "from dispatch_v2.core.jsonl_rotation import resolve_jsonl_paths\n"
        f"assert str(C.resolve_shadow_decisions_input_path()) == {str(shadow_input)!r}\n"
        f"assert str(C.resolve_shadow_decisions_writer_path()) == {str(writer)!r}\n"
        f"assert {str(writer)!r} in resolve_jsonl_paths()\n"
        f"assert {str(shadow_input)!r} not in resolve_jsonl_paths()\n"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def test_shadow_decisions_read_only_alias_uses_jsonl_path_contract(tmp_path) -> None:
    env = _clean_path_env()
    env["SHADOW_DECISIONS_LOG"] = "relative-shadow.jsonl"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from dispatch_v2.common import "
                "resolve_shadow_decisions_input_path;"
                "resolve_shadow_decisions_input_path()"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode != 0
    assert "must be absolute" in completed.stderr


def test_direct_entrypoint_package_parent_beats_competing_pythonpath(tmp_path) -> None:
    competitor = tmp_path / "competitor"
    fake_package = competitor / "dispatch_v2"
    fake_package.mkdir(parents=True)
    (fake_package / "__init__.py").write_text("", encoding="utf-8")
    (fake_package / "common.py").write_text(
        "raise RuntimeError('WRONG_PACKAGE')\n",
        encoding="utf-8",
    )
    isolated_scripts = tmp_path / "isolated-scripts"
    isolated_scripts.mkdir()
    (isolated_scripts / "config.json").write_text(
        json.dumps({"paths": {"shadow_log": str(tmp_path / "shadow.jsonl")}}),
        encoding="utf-8",
    )
    env = _clean_path_env()
    env["PYTHONPATH"] = os.pathsep.join((str(competitor), str(PACKAGE_PARENT)))
    env["ZIOMEK_SCRIPTS_ROOT"] = str(isolated_scripts)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    foreign = tmp_path / "foreign-tree" / "jsonl_rotation.py"
    foreign.parent.mkdir()
    foreign.symlink_to(physical)
    for entrypoint in (physical, foreign):
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import runpy;"
                    f"runpy.run_path({str(entrypoint)!r},"
                    "run_name='r4_competing_package_probe')"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )


def test_direct_entrypoint_sanitizes_pythonpath_before_stdlib_import(
    tmp_path,
) -> None:
    competitor = tmp_path / "competitor"
    competitor.mkdir()
    (competitor / "argparse.py").write_text(
        "raise RuntimeError('POISONED_STDLIB_IMPORT')\n",
        encoding="utf-8",
    )
    env = _clean_path_env()
    env["PYTHONPATH"] = os.pathsep.join((str(competitor), str(PACKAGE_PARENT)))
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    completed = subprocess.run(
        [sys.executable, "-S", str(physical), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0
    assert "POISONED_STDLIB_IMPORT" not in completed.stderr


def test_direct_entrypoint_rejects_prefix_descendant_pythonpath_poison(
    tmp_path,
) -> None:
    fake_prefix = tmp_path / "runtime-prefix"
    descendant = fake_prefix / "attacker-controlled"
    descendant.mkdir(parents=True)
    (descendant / "argparse.py").write_text(
        "raise RuntimeError('PREFIX_DESCENDANT_POISON')\n",
        encoding="utf-8",
    )
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    env = _clean_path_env()
    env["PYTHONPATH"] = os.pathsep.join((str(descendant), str(PACKAGE_PARENT)))
    code = (
        "import runpy, sys;"
        f"sys.prefix = {str(fake_prefix)!r};"
        f"sys.argv = [{str(physical)!r}, '--help'];"
        f"runpy.run_path({str(physical)!r}, run_name='__main__')"
    )
    completed = subprocess.run(
        [sys.executable, "-S", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0
    assert "PREFIX_DESCENDANT_POISON" not in completed.stderr


def test_direct_entrypoint_keeps_pythonpath_untrusted_during_package_import(
    tmp_path,
) -> None:
    competitor = tmp_path / "competitor"
    competitor.mkdir()
    (competitor / "logging.py").write_text(
        "raise RuntimeError('POST_RESTORE_STDLIB_POISON')\n",
        encoding="utf-8",
    )
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    env = _clean_path_env()
    env["PYTHONPATH"] = os.pathsep.join(
        (str(competitor), str(PACKAGE_PARENT))
    )
    completed = subprocess.run(
        [sys.executable, "-S", str(physical), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0
    assert "POST_RESTORE_STDLIB_POISON" not in completed.stderr


def test_direct_entrypoint_never_retrusts_scripts_root_for_local_import(
    tmp_path,
) -> None:
    competitor = tmp_path / "competitor"
    poisoned_ml = competitor / "ml_data_prep"
    poisoned_ml.mkdir(parents=True)
    (poisoned_ml / "train_two_models.py").write_text(
        "raise RuntimeError('POST_BOOTSTRAP_SCRIPTS_ROOT_POISON')\n",
        encoding="utf-8",
    )
    physical = PACKAGE_PARENT / "dispatch_v2/ml_data_prep/forward_validation.py"
    env = _clean_path_env()
    env["ZIOMEK_SCRIPTS_ROOT"] = str(competitor)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    completed = subprocess.run(
        [sys.executable, str(physical), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    output = completed.stdout + completed.stderr
    assert "POST_BOOTSTRAP_SCRIPTS_ROOT_POISON" not in output


def test_physical_sibling_loader_binds_exact_attested_neighbor(
    tmp_path,
    monkeypatch,
) -> None:
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    sibling = scripts / "trusted_sibling.py"
    sibling_package = scripts / "trusted_package"
    package.mkdir(parents=True)
    sibling_package.mkdir()
    package_init = package / "__init__.py"
    package_init.write_text("", encoding="utf-8")
    sibling.write_text("BOUNDARY_VALUE = 'trusted-physical-file'\n", encoding="utf-8")
    (sibling_package / "__init__.py").write_text("", encoding="utf-8")
    (sibling_package / "child.py").write_text(
        "BOUNDARY_VALUE = 'trusted-physical-package'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package_init))

    assert physical_import.attest_physical_scripts_dir(scripts) == scripts
    module_name = "_r4_p35_trusted_physical_sibling"
    package_name = "_r4_p35_trusted_physical_package"
    try:
        loaded = physical_import.load_physical_scripts_sibling(
            module_name,
            "trusted_sibling.py",
        )
        assert loaded is not None
        assert loaded.BOUNDARY_VALUE == "trusted-physical-file"
        assert Path(loaded.__file__).samefile(sibling)
        loaded_package = physical_import.load_physical_scripts_sibling(
            package_name,
            "trusted_package",
            package=True,
        )
        loaded_child = importlib.import_module(f"{package_name}.child")
        assert loaded_package is not None
        # Candidate51: `__path__` pakietu jest PUSTE (potomek rozwiązywany
        # wyłącznie przez naszego findera z utrzymanego deskryptora, nigdy z
        # tekstowej ścieżki). Wiązanie z atestowanym sąsiadem potwierdza udany
        # import potomka poniżej, nie zawartość `__path__`.
        assert list(loaded_package.__path__) == []
        assert loaded_child.BOUNDARY_VALUE == "trusted-physical-package"
    finally:
        for loaded_name in tuple(sys.modules):
            if (
                loaded_name == module_name
                or loaded_name == package_name
                or loaded_name.startswith(package_name + ".")
            ):
                sys.modules.pop(loaded_name, None)


def test_scripts_root_cannot_self_attest_via_package_symlink(
    tmp_path,
) -> None:
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    competitor = tmp_path / "competitor"
    competitor.mkdir()
    (competitor / "dispatch_v2").symlink_to(
        Path(dispatch_v2.__file__).resolve().parent,
        target_is_directory=True,
    )
    with pytest.raises(RuntimeError, match="does not own"):
        physical_import.attest_physical_scripts_dir(competitor)


def test_direct_entrypoint_rejects_preloaded_competing_package(tmp_path) -> None:
    competitor = tmp_path / "competitor"
    fake_package = competitor / "dispatch_v2"
    fake_package.mkdir(parents=True)
    (fake_package / "__init__.py").write_text("", encoding="utf-8")
    (fake_package / "common.py").write_text(
        "raise RuntimeError('WRONG_PACKAGE_EXECUTED')\n",
        encoding="utf-8",
    )
    env = _clean_path_env()
    env["PYTHONPATH"] = os.pathsep.join((str(competitor), str(PACKAGE_PARENT)))
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import dispatch_v2, runpy;"
                f"runpy.run_path({str(physical)!r},"
                "run_name='r4_preloaded_package_probe')"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode != 0
    assert "conflicting preloaded dispatch_v2 package" in completed.stderr
    assert "WRONG_PACKAGE_EXECUTED" not in completed.stderr


def test_direct_entrypoint_rejects_mixed_preloaded_package_path(tmp_path) -> None:
    fake_package = tmp_path / "competitor" / "dispatch_v2"
    fake_package.mkdir(parents=True)
    (fake_package / "common.py").write_text(
        "raise RuntimeError('WRONG_MIXED_PACKAGE_EXECUTED')\n",
        encoding="utf-8",
    )
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    code = (
        "import runpy, sys, types;"
        "package = types.ModuleType('dispatch_v2');"
        f"package.__path__ = [{str(fake_package)!r}, {str(REPO_ROOT)!r}];"
        "package.__package__ = 'dispatch_v2';"
        "sys.modules['dispatch_v2'] = package;"
        f"runpy.run_path({str(physical)!r},"
        "run_name='r4_mixed_package_probe')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_path_env(),
    )
    assert completed.returncode != 0
    assert "conflicting preloaded dispatch_v2 package" in completed.stderr
    assert "WRONG_MIXED_PACKAGE_EXECUTED" not in completed.stderr


def test_direct_entrypoint_rejects_preloaded_foreign_submodule(tmp_path) -> None:
    fake_common = tmp_path / "competitor" / "dispatch_v2" / "common.py"
    fake_common.parent.mkdir(parents=True)
    fake_common.write_text("", encoding="utf-8")
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    code = (
        "import runpy, sys, types;"
        "package = types.ModuleType('dispatch_v2');"
        f"package.__path__ = [{str(REPO_ROOT)!r}];"
        "package.__package__ = 'dispatch_v2';"
        "sys.modules['dispatch_v2'] = package;"
        "common = types.ModuleType('dispatch_v2.common');"
        f"common.__file__ = {str(fake_common)!r};"
        "sys.modules['dispatch_v2.common'] = common;"
        f"runpy.run_path({str(physical)!r},"
        "run_name='r4_foreign_submodule_probe')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_path_env(),
    )
    assert completed.returncode != 0
    assert "conflicting preloaded dispatch_v2 package" in completed.stderr


def test_direct_entrypoint_rejects_forged_expected_preloaded_metadata(
    tmp_path,
) -> None:
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    code = (
        "import runpy, sys, types;"
        "from pathlib import Path;"
        "package = types.ModuleType('dispatch_v2');"
        f"package.__path__ = [{str(REPO_ROOT)!r}];"
        "package.__package__ = 'dispatch_v2';"
        "sys.modules['dispatch_v2'] = package;"
        "common = types.ModuleType('dispatch_v2.common');"
        f"common.__file__ = {str(REPO_ROOT / 'common.py')!r};"
        f"common.JSONL_ROTATION_REGISTRY_PATH = Path({str(tmp_path / 'registry.json')!r});"
        f"common.JSONL_LOGROTATE_CONFIG_PATH = Path({str(tmp_path / 'policy.conf')!r});"
        f"common.JSONL_LOGROTATE_STATE_PATH = Path({str(tmp_path / 'logrotate.status')!r});"
        f"common.LOGS_DIR = Path({str(tmp_path / 'logs')!r});"
        f"common.STATE_DIR = Path({str(tmp_path / 'state')!r});"
        f"common.resolve_shadow_decisions_writer_path = lambda config=None: Path({str(tmp_path / 'shadow.jsonl')!r});"
        "common.validate_jsonl_path = lambda value: Path(value);"
        "common.validate_runtime_control_path = lambda value, **kwargs: Path(value);"
        "sys.modules['dispatch_v2.common'] = common;"
        f"runpy.run_path({str(physical)!r},"
        "run_name='r4_forged_metadata_probe')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_path_env(),
    )
    assert completed.returncode != 0
    assert "conflicting preloaded dispatch_v2 package" in completed.stderr


def test_direct_entrypoint_binds_submodule_name_to_expected_file(tmp_path) -> None:
    physical = PACKAGE_PARENT / "dispatch_v2/core/jsonl_rotation.py"
    wrong_file = REPO_ROOT / "decision_eta_log.py"
    code = (
        "import runpy, sys, types;"
        "from pathlib import Path;"
        "package = types.ModuleType('dispatch_v2');"
        f"package.__path__ = [{str(REPO_ROOT)!r}];"
        "package.__package__ = 'dispatch_v2';"
        "sys.modules['dispatch_v2'] = package;"
        "common = types.ModuleType('dispatch_v2.common');"
        f"common.__file__ = {str(wrong_file)!r};"
        f"common.JSONL_ROTATION_REGISTRY_PATH = Path({str(tmp_path / 'registry.json')!r});"
        f"common.LOGS_DIR = Path({str(tmp_path / 'logs')!r});"
        f"common.STATE_DIR = Path({str(tmp_path / 'state')!r});"
        f"common.resolve_shadow_decisions_writer_path = lambda config=None: Path({str(tmp_path / 'shadow.jsonl')!r});"
        "common.validate_jsonl_path = lambda value: Path(value);"
        "sys.modules['dispatch_v2.common'] = common;"
        f"runpy.run_path({str(physical)!r},"
        "run_name='r4_misnamed_submodule_probe')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_path_env(),
    )
    assert completed.returncode != 0
    assert "conflicting preloaded dispatch_v2 package" in completed.stderr


def test_runtime_safe_entrypoints_import_without_pythonpath(tmp_path) -> None:
    isolated_scripts = tmp_path / "isolated-scripts"
    isolated_scripts.mkdir()
    (isolated_scripts / "config.json").write_text(
        json.dumps({"paths": {"shadow_log": str(tmp_path / "shadow.jsonl")}}),
        encoding="utf-8",
    )
    env = _clean_path_env()
    env.pop("PYTHONPATH", None)
    env["ZIOMEK_SCRIPTS_ROOT"] = str(isolated_scripts)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    for relative in RUNTIME_ENTRYPOINT_FILES:
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import runpy;"
                    f"runpy.run_path({str(PACKAGE_PARENT / 'dispatch_v2' / relative)!r},"
                    "run_name='r4_entrypoint_probe')"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )


SRC_IMPORTING_PACKAGE_MODULES = (
    "dispatch_v2.ml_data_prep.train_two_models",
    "dispatch_v2.ml_data_prep.forward_validation",
    "dispatch_v2.ml_data_prep.parity_ml_inference",
)


@pytest.mark.parametrize("module_name", SRC_IMPORTING_PACKAGE_MODULES)
def test_package_route_binds_physical_src_not_pythonpath(
    tmp_path,
    module_name,
) -> None:
    """Candidate37 F2: pakietowa trasa nie ufa PYTHONPATH przy imporcie src.

    Oracle defektu: falszywy pakiet src przed package parent wykonywal sie
    przy package imporcie, bo exact loader dzialal tylko w direct-file branch.
    Kontrakt: marker trucizny NIGDY nie wykonany; import albo wiaze fizyczne
    src (rc 0), albo pada fail-closed na granicy physical loadera.
    """
    poison = tmp_path / "poison"
    fake_src = poison / "src"
    fake_src.mkdir(parents=True)
    marker = tmp_path / "poison_executed.marker"
    poison_body = (
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('POISONED_SRC_EXECUTED')\n"
    )
    (fake_src / "__init__.py").write_text(poison_body, encoding="utf-8")
    for submodule in ("feature_engineering.py", "lgbm_training.py"):
        (fake_src / submodule).write_text(poison_body, encoding="utf-8")
    env = _clean_path_env()
    env["PYTHONPATH"] = os.pathsep.join((str(poison), str(PACKAGE_PARENT)))
    env["ZIOMEK_SCRIPTS_ROOT"] = str(PACKAGE_PARENT)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    completed = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    output = completed.stdout + completed.stderr
    assert not marker.exists(), f"poisoned src executed: {output}"
    assert "POISONED_SRC_EXECUTED" not in output
    assert str(poison) not in output, output
    if completed.returncode != 0:
        # C40 (Sol F1): brak trzeciego, cichego stanu — jesli import nie
        # przeszedl, to WYLACZNIE fail-closed na granicy physical loadera.
        # Pozytywny parytet importu dowodzi osobny oracle
        # test_ml_consumers_positive_import_binds_physical_src.
        assert "trusted physical sibling unavailable" in output, output


def test_physical_sibling_loader_rejects_intermediate_symlink_component(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate37 F3: loader atestuje CALY lancuch komponentow, nie koncowke.

    Oracle defektu: scripts/legacy -> ../outside pozwalal zaladowac payload
    spoza atestowanego physical scripts parent (module.__file__ outside).
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    outside = tmp_path / "outside" / "legacy"
    outside.mkdir(parents=True)
    (outside / "payload.py").write_text(
        "ESCAPED = 'OUTSIDE_PHYSICAL_PARENT'\n",
        encoding="utf-8",
    )
    escaped_package = outside / "pkg"
    escaped_package.mkdir()
    (escaped_package / "__init__.py").write_text(
        "ESCAPED = 'OUTSIDE_PHYSICAL_PARENT'\n",
        encoding="utf-8",
    )
    (scripts / "legacy").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    module_name = "_c37_symlink_chain_probe"
    package_name = "_c37_symlink_chain_pkg"
    try:
        with pytest.raises(RuntimeError, match="physical"):
            physical_import.load_physical_scripts_sibling(
                module_name,
                "legacy/payload.py",
            )
        with pytest.raises(RuntimeError, match="physical"):
            physical_import.load_physical_scripts_sibling(
                package_name,
                "legacy/pkg",
                package=True,
            )
        assert module_name not in sys.modules
        assert package_name not in sys.modules
    finally:
        sys.modules.pop(module_name, None)
        sys.modules.pop(package_name, None)


def _forged_sibling_scenario(tmp_path, monkeypatch):
    """Attested tmp scripts dir + unrecorded ModuleType with expected __file__."""
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    sibling = scripts / "legacy_sibling.py"
    sibling.write_text("DISK_VALUE = 'from-attested-descriptor'\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})

    module_name = "_c38_unrecorded_expected_file_probe"
    forged = ModuleType(module_name)
    forged.__file__ = str(sibling)
    monkeypatch.setitem(sys.modules, module_name, forged)
    return physical_import, module_name, forged


def test_physical_sibling_loader_rejects_unrecorded_module_with_expected_file(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate38 F1: reuse tylko dla obiektu zapisanego przez kanoniczny loader.

    Oracle defektu (Candidate37): nierejestrowany ModuleType, ktorego mutowalny
    __file__ wskazywal oczekiwane zrodlo, byl adoptowany i zapisywany do
    prywatnego rejestru, omijajac cala sciezke O_NOFOLLOW->fd->exec; jego
    zawartosc nigdy nie pochodzila z zaatestowanego deskryptora.
    """
    physical_import, module_name, forged = _forged_sibling_scenario(
        tmp_path,
        monkeypatch,
    )
    assert module_name not in physical_import._LOADED_PHYSICAL_SIBLINGS
    with pytest.raises(
        RuntimeError,
        match="conflicting preloaded physical sibling",
    ):
        physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_sibling.py",
        )
    assert module_name not in physical_import._LOADED_PHYSICAL_SIBLINGS
    assert sys.modules[module_name] is forged
    assert not hasattr(forged, "DISK_VALUE")


def test_physical_sibling_loader_still_reuses_its_own_recorded_object(
    tmp_path,
    monkeypatch,
) -> None:
    """Identity-only reuse nie zawezil legalnej sciezki: drugi load == ten sam obiekt."""
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "legacy_sibling.py").write_text(
        "DISK_VALUE = 'from-attested-descriptor'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    module_name = "_c38_recorded_reuse_probe"
    try:
        first = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_sibling.py",
        )
        second = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_sibling.py",
        )
        assert first is not None
        assert second is first
        assert first.DISK_VALUE == "from-attested-descriptor"
    finally:
        sys.modules.pop(module_name, None)


def test_physical_sibling_loader_rejects_swapped_object_after_recorded_load(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate39 F2: tozsamosc obiektu wiaze ZYWA, publiczna sciezke reuse.

    Oracle luki Candidate38 (finding Opusa): sprawdzenie tozsamosci istnialo
    w dwoch kopiach, a testy dotykaly kopii martwej. Ten test idzie wylacznie
    publicznym API: pierwszy legalny load zapisuje rekord w rejestrze, potem
    obiekt w sys.modules zostaje podmieniony na obcy ModuleType z poprawnym
    __file__ — drugi load MUSI deterministycznie odrzucic reuse i nie moze
    prac obcego obiektu do rejestru. Usuniecie/odwrocenie czlonu tozsamosci
    w zywej decyzji czerwieni ten test (kontrola czulosci: M5 nizej +
    kontrolowana proba na pliku).
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    sibling = scripts / "legacy_sibling.py"
    sibling.write_text("DISK_VALUE = 'from-attested-descriptor'\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})

    module_name = "_c39_swapped_identity_probe"
    try:
        first = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_sibling.py",
        )
        assert first is not None
        recorded = physical_import._LOADED_PHYSICAL_SIBLINGS[module_name]
        assert recorded[1] is first

        forged = ModuleType(module_name)
        forged.__file__ = str(sibling)
        monkeypatch.setitem(sys.modules, module_name, forged)
        with pytest.raises(
            RuntimeError,
            match="conflicting preloaded physical sibling",
        ):
            physical_import.load_physical_scripts_sibling(
                module_name,
                "legacy_sibling.py",
            )
        after = physical_import._LOADED_PHYSICAL_SIBLINGS[module_name]
        assert after[1] is first
        assert not hasattr(forged, "DISK_VALUE")
    finally:
        sys.modules.pop(module_name, None)


def test_c39_mutation_probe_identity_free_reuse_re_reds_oracle(
    tmp_path,
    monkeypatch,
) -> None:
    """Mutation probe M5: zywa decyzja bez czlonu tozsamosci lamie oracle F2.

    Mutant odtwarza dokladnie mutanta z findingu Opusa (reuse po samej zgodnej
    sciezce zrodla, bez `is`): po legalnym loadzie podmieniony obiekt zostaje
    zwrocony bez wykonania bajtow z dysku — asercje F2 (pytest.raises +
    rejestr trzyma oryginal) staja sie pod mutantem czerwone.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    sibling = scripts / "legacy_sibling.py"
    sibling.write_text("DISK_VALUE = 'from-attested-descriptor'\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})

    module_name = "_c39_identity_free_mutant_probe"
    try:
        first = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_sibling.py",
        )
        assert first is not None

        def mutant_adopt(name: str, source: Path):
            if not any(
                loaded == name or loaded.startswith(name + ".")
                for loaded in sys.modules
            ):
                return None
            existing = sys.modules.get(name)
            if existing is not None:
                recorded = physical_import._LOADED_PHYSICAL_SIBLINGS.get(name)
                if recorded is not None and recorded[0] == str(source):
                    return existing
            raise RuntimeError(
                f"conflicting preloaded physical sibling package: {name}"
            )

        monkeypatch.setattr(
            physical_import,
            "_adopt_or_reject_preloaded",
            mutant_adopt,
        )
        forged = ModuleType(module_name)
        forged.__file__ = str(sibling)
        monkeypatch.setitem(sys.modules, module_name, forged)
        adopted = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_sibling.py",
        )
        assert adopted is forged
        assert not hasattr(adopted, "DISK_VALUE")
    finally:
        sys.modules.pop(module_name, None)


def test_c38_mutation_probe_file_based_adoption_re_reds_oracle(
    tmp_path,
    monkeypatch,
) -> None:
    """Mutation probe: przywrocenie akceptacji po samym __file__ lamie oracle.

    Mutant odtwarza usunieta galaz Candidate37 (samefile na mutowalnym
    __file__ + pranie do rejestru). Pod mutantem loader zwraca sfalszowany
    obiekt bez wykonania bajtow z dysku — czyli asercje oracle F1
    (pytest.raises + pusty rejestr) staja sie ponownie czerwone.
    """
    physical_import, module_name, forged = _forged_sibling_scenario(
        tmp_path,
        monkeypatch,
    )

    def mutant_adopt(name: str, source: Path):
        if not any(
            loaded == name or loaded.startswith(name + ".")
            for loaded in sys.modules
        ):
            return None
        existing = sys.modules.get(name)
        if existing is not None:
            recorded = physical_import._LOADED_PHYSICAL_SIBLINGS.get(name)
            if (
                recorded is not None
                and recorded[1] is existing
                and recorded[0] == str(source)
            ):
                return existing
            existing_file = getattr(existing, "__file__", None)
            if existing_file and Path(existing_file).samefile(source):
                physical_import._LOADED_PHYSICAL_SIBLINGS[name] = (
                    str(source),
                    existing,
                )
                return existing
        raise RuntimeError(
            f"conflicting preloaded physical sibling package: {name}"
        )

    monkeypatch.setattr(
        physical_import,
        "_adopt_or_reject_preloaded",
        mutant_adopt,
    )
    adopted = physical_import.load_physical_scripts_sibling(
        module_name,
        "legacy_sibling.py",
    )
    assert adopted is forged
    assert not hasattr(adopted, "DISK_VALUE")
    assert physical_import._LOADED_PHYSICAL_SIBLINGS[module_name][1] is forged


def _direct_src_import_lines(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == "src" or module.startswith("src."):
                lines.append(node.lineno)
        if isinstance(node, ast.Import) and any(
            alias.name == "src" or alias.name.startswith("src.")
            for alias in node.names
        ):
            lines.append(node.lineno)
    return sorted(lines)


def _is_src_loader_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Name)
                and node.func.id == "load_physical_scripts_sibling"
            )
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "load_physical_scripts_sibling"
            )
        )
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "src"
    )


def _unconditional_src_loader_calls(tree: ast.Module) -> int:
    return sum(
        1
        for statement in tree.body
        if isinstance(statement, ast.Expr)
        and _is_src_loader_call(statement.value)
    )


def _any_src_loader_calls(tree: ast.AST) -> int:
    return sum(1 for node in ast.walk(tree) if _is_src_loader_call(node))


def test_direct_src_importers_bind_src_through_one_unconditional_loader() -> None:
    """Candidate37 ratchet: jeden kanoniczny, bezwarunkowy owner bindingu src.

    Modul z bezposrednim importem src MUSI wolac exact physical loader
    dokladnie raz, na module-level, poza jakimkolwiek guardem; modul bez
    importu src nie moze wolac loadera src (zakaz duplikatu polityki).
    """
    offenders = []
    for path in _migrated_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = str(path.relative_to(REPO_ROOT))
        direct = _direct_src_import_lines(tree)
        unconditional = _unconditional_src_loader_calls(tree)
        anywhere = _any_src_loader_calls(tree)
        if direct:
            if unconditional != 1 or anywhere != 1:
                offenders.append(f"{relative} (guarded/duplicated src loader)")
        elif anywhere:
            offenders.append(f"{relative} (src loader without src import)")
    assert not offenders, "src loader ratchet: " + ", ".join(offenders)


def test_src_loader_ratchet_rejects_guarded_loader() -> None:
    tree = ast.parse(
        "if __package__ in (None, ''):\n"
        "    load_physical_scripts_sibling('src', 'x/src', package=True)\n"
        "from src.feature_engineering import bag_size_category\n"
    )
    assert _direct_src_import_lines(tree)
    assert _unconditional_src_loader_calls(tree) == 0
    assert _any_src_loader_calls(tree) == 1


def test_src_loader_ratchet_rejects_duplicate_loader_owner() -> None:
    tree = ast.parse(
        "load_physical_scripts_sibling('src', 'x/src', package=True)\n"
    )
    assert not _direct_src_import_lines(tree)
    assert _any_src_loader_calls(tree) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Candidate40 — cztery obowiazki po dwoch zgodnych CONFIRMED_DEFECT na C39
# (Sol F1/F2 + Opus F1/F2). Kazdy oracle wiaze publiczna, zywa trase.
# ─────────────────────────────────────────────────────────────────────────────

_SYNTHETIC_SRC_LGBM = (
    "WINNER_COLS = ()\n"
    "LOSER_COLS = ()\n"
    "DECISION_LEVEL_COLS = ()\n"
    "CATEGORICAL_COLS = ['level', 'season']\n"
    "def build_pointwise_dataset(*args, **kwargs):\n"
    "    raise NotImplementedError\n"
    "def transform_categorical(*args, **kwargs):\n"
    "    raise NotImplementedError\n"
)
_SYNTHETIC_SRC_FEATURES = (
    "PEAK_LUNCH = {11, 12, 13}\n"
    "PEAK_DINNER = {17, 18, 19}\n"
    "def bag_size_category(value):\n"
    "    return 'unknown'\n"
    "def idle_category(value):\n"
    "    return 'unknown'\n"
    "def idle_capped(value):\n"
    "    return None\n"
    "def district_adjacent(left, right):\n"
    "    return False\n"
    "def time_features(timestamp):\n"
    "    return {'season': 'winter'}\n"
)


def _physical_scripts_layout(tmp_path, *, with_src=True, with_schedule=False):
    """Hermetyczna replika fizycznego parenta: pakiet + wybrane siblingi."""
    scripts = tmp_path / "scripts"
    (scripts / "dispatch_v2").mkdir(parents=True)
    (scripts / "dispatch_v2" / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "config.json").write_text(
        json.dumps({"paths": {"shadow_log": str(tmp_path / "shadow.jsonl")}}),
        encoding="utf-8",
    )
    if with_src:
        src = scripts / "ml_data_prep" / "src"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("", encoding="utf-8")
        (src / "lgbm_training.py").write_text(
            _SYNTHETIC_SRC_LGBM, encoding="utf-8"
        )
        (src / "feature_engineering.py").write_text(
            _SYNTHETIC_SRC_FEATURES, encoding="utf-8"
        )
    if with_schedule:
        (scripts / "schedule_utils.py").write_text(
            "def load_schedule():\n"
            "    return {'sentinel': 'physical-schedule'}\n",
            encoding="utf-8",
        )
    return scripts


def _physical_sibling_subprocess_env(tmp_path, scripts, *, poison=None):
    env = _clean_path_env()
    env["ZIOMEK_SCRIPTS_ROOT"] = str(scripts)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    env["ZIOMEK_LOGS_DIR"] = str(tmp_path / "logs")
    if poison is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            (str(poison), env["PYTHONPATH"])
        )
    return env


ML_CONSUMER_DESCENDANTS = (
    ("dispatch_v2.ml_data_prep.train_two_models", "src.lgbm_training"),
    ("dispatch_v2.ml_data_prep.forward_validation", "src.lgbm_training"),
    ("dispatch_v2.ml_data_prep.parity_ml_inference", "src.feature_engineering"),
)


@pytest.mark.parametrize(
    ("module_name", "descendant"), ML_CONSUMER_DESCENDANTS
)
def test_ml_consumers_positive_import_binds_physical_src(
    tmp_path,
    module_name,
    descendant,
) -> None:
    """Candidate40 O1 (Sol F1): POZYTYWNY oracle importu konsumentow src.

    Fail-closed nie jest parytetem funkcjonalnym: przy fizycznie OBECNYM
    siblingu ml_data_prep/src import konsumenta MUSI sie udac
    (returncode == 0), zwiazac dokladnie fizyczne src (oraz jego POTOMKA)
    z zaatestowanego parenta, a trucizna z PYTHONPATH nie moze sie wykonac.
    Kontrola czulosci: test nizej wymusza rc != 0 z markerem fail-closed,
    gdy fizycznego src zabraknie — ten oracle nie moze byc zielony przy
    zepsutym imporcie.
    """
    scripts = _physical_scripts_layout(tmp_path, with_src=True)
    poison = tmp_path / "poison"
    fake_src = poison / "src"
    fake_src.mkdir(parents=True)
    marker = tmp_path / "poison_executed.marker"
    poison_body = (
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('POISONED_SRC_EXECUTED')\n"
    )
    (fake_src / "__init__.py").write_text(poison_body, encoding="utf-8")
    for submodule in ("feature_engineering.py", "lgbm_training.py"):
        (fake_src / submodule).write_text(poison_body, encoding="utf-8")
    src_init = scripts / "ml_data_prep" / "src" / "__init__.py"
    descendant_file = (
        scripts / "ml_data_prep" / "src" / (descendant.split(".")[-1] + ".py")
    )
    child = (
        "import importlib, pathlib, sys\n"
        "import dispatch_v2\n"
        f"dispatch_v2.__file__ = {str(scripts / 'dispatch_v2' / '__init__.py')!r}\n"
        f"module = importlib.import_module({module_name!r})\n"
        "src = sys.modules['src']\n"
        "assert pathlib.Path(src.__file__).resolve() == "
        f"pathlib.Path({str(src_init)!r}).resolve(), src.__file__\n"
        f"descendant = sys.modules[{descendant!r}]\n"
        "assert pathlib.Path(descendant.__file__).resolve() == "
        f"pathlib.Path({str(descendant_file)!r}).resolve(), descendant.__file__\n"
        "print('C40_POSITIVE_IMPORT_OK')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", child],
        check=False,
        capture_output=True,
        text=True,
        env=_physical_sibling_subprocess_env(tmp_path, scripts, poison=poison),
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "C40_POSITIVE_IMPORT_OK" in completed.stdout, output
    assert not marker.exists(), f"poisoned src executed: {output}"
    assert "POISONED_SRC_EXECUTED" not in output


def test_ml_consumer_positive_oracle_sensitivity_missing_src_fails_closed(
    tmp_path,
) -> None:
    """Kontrola czulosci O1: bez fizycznego src import MUSI upasc fail-closed.

    To domyka luke z findingu Sola: oracle z check=False bez asercji
    returncode przechodzil takze przy calkowicie zepsutym imporcie.
    """
    scripts = _physical_scripts_layout(tmp_path, with_src=False)
    child = (
        "import importlib\n"
        "import dispatch_v2\n"
        f"dispatch_v2.__file__ = {str(scripts / 'dispatch_v2' / '__init__.py')!r}\n"
        "importlib.import_module('dispatch_v2.ml_data_prep.train_two_models')\n"
        "print('SHOULD_NOT_IMPORT')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", child],
        check=False,
        capture_output=True,
        text=True,
        env=_physical_sibling_subprocess_env(tmp_path, scripts),
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "SHOULD_NOT_IMPORT" not in completed.stdout
    assert "trusted physical sibling unavailable" in output, output


def test_twomodel_functional_parity_with_physical_src(tmp_path) -> None:
    """Candidate40 O1b (Sol F1): rownowaznik funkcjonalny 5 utraconych przejsc.

    test_ml_twomodel (base 27p) traci w harnessie 5 przejsc warstwy A,
    bo fizycznego src nie ma obok atestowanego parenta. Ten oracle odtwarza
    ICH TRESC hermetycznie przy fizycznie obecnym src: one-hot tieru (kolumny
    stale, UNK-routing, determinizm) i label-encode (UNK spojne, determinizm)
    na PRAWDZIWYM dispatch_v2.ml_data_prep.train_two_models.
    """
    scripts = _physical_scripts_layout(tmp_path, with_src=True)
    child = (
        "import importlib, sys\n"
        "import pandas as pd\n"
        "import dispatch_v2\n"
        f"dispatch_v2.__file__ = {str(scripts / 'dispatch_v2' / '__init__.py')!r}\n"
        "from dispatch_v2.ml_data_prep import twomodel_common as tmc\n"
        "train_mod = importlib.import_module("
        "'dispatch_v2.ml_data_prep.train_two_models')\n"
        "cats = ['A', 'B', 'UNK']\n"
        "df1 = pd.DataFrame({tmc.TIER_ORD_COL: ['B', 'B'], 'x': [1, 2]})\n"
        "df2 = pd.DataFrame({tmc.TIER_ORD_COL: ['A'], 'x': [3]})\n"
        "o1 = train_mod.apply_tier_onehot(df1, cats)\n"
        "o2 = train_mod.apply_tier_onehot(df2, cats)\n"
        "onehot_cols = [f'{tmc.TIER_ORD_COL}__{c}' for c in cats]\n"
        "for col in onehot_cols:\n"
        "    assert col in o1.columns and col in o2.columns\n"
        "assert tmc.TIER_ORD_COL not in o1.columns\n"
        "assert tmc.TIER_ORD_COL not in o2.columns\n"
        "assert o1['level__B'].tolist() == [1, 1]\n"
        "assert o1['level__A'].tolist() == [0, 0]\n"
        "assert o2['level__A'].tolist() == [1]\n"
        "assert o2['level__B'].tolist() == [0]\n"
        "df3 = pd.DataFrame({tmc.TIER_ORD_COL: ['Z', 'A', None],"
        " 'x': [1, 2, 3]})\n"
        "o3 = train_mod.apply_tier_onehot(df3, cats)\n"
        "assert o3['level__UNK'].tolist() == [1, 0, 1]\n"
        "assert o3['level__A'].tolist() == [0, 1, 0]\n"
        "df4 = pd.DataFrame({tmc.TIER_ORD_COL: ['A', 'B', 'Z'],"
        " 'x': [1, 2, 3]})\n"
        "d1 = train_mod.apply_tier_onehot(df4.copy(), cats)\n"
        "d2 = train_mod.apply_tier_onehot(df4.copy(), cats)\n"
        "pd.testing.assert_frame_equal(d1, d2)\n"
        "train_pw = pd.DataFrame({'season': ['winter', 'spring', 'summer']})\n"
        "enc = train_mod.fit_label_encoders(train_pw)\n"
        "assert 'season' in enc\n"
        "serve = pd.DataFrame({'season': ['winter', 'autumn', 'spring']})\n"
        "out = train_mod.apply_label_encoders(serve, enc)\n"
        "unk_code = enc['season'].transform(['UNK'])[0]\n"
        "winter_code = enc['season'].transform(['winter'])[0]\n"
        "spring_code = enc['season'].transform(['spring'])[0]\n"
        "assert out['season'].tolist() == "
        "[winter_code, unk_code, spring_code]\n"
        "enc2 = train_mod.fit_label_encoders("
        "pd.DataFrame({'season': ['winter', 'spring']}))\n"
        "s = pd.DataFrame({'season': ['winter', 'spring', 'X']})\n"
        "r1 = train_mod.apply_label_encoders(s.copy(), enc2)\n"
        "r2 = train_mod.apply_label_encoders(s.copy(), enc2)\n"
        "assert r1['season'].tolist() == r2['season'].tolist()\n"
        "print('C40_TWOMODEL_PARITY_OK')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", child],
        check=False,
        capture_output=True,
        text=True,
        env=_physical_sibling_subprocess_env(tmp_path, scripts),
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "C40_TWOMODEL_PARITY_OK" in completed.stdout, output


def test_package_descendant_binds_attested_descriptor_not_swapped_path(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate40 O2 (Sol F2): potomkowie pakietu ida ta sama granica fd.

    Oracle defektu C39: package=True wykonywal z deskryptora tylko
    __init__.py, a pozniejsze importy dzieci szly zwyklym PathFinderem po
    mutowalnej sciezce tekstowej — podmiana target na symlink poza
    atestowanego parenta wykonywala obce bajty. Kontrakt C40: potomek
    rozwiazuje sie wylacznie fd-walkiem O_NOFOLLOW od atestowanego parenta;
    podmieniony komponent = twarda granica, obce bajty nigdy nie wykonane;
    brak potomka = fail-closed (zakaz fallbacku do PathFinder).
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "child.py").write_text(
        "SAFE = 'from-attested-descriptor'\n", encoding="utf-8"
    )
    sub = legacy / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "leaf.py").write_text("LEAF = 'attested-leaf'\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "outside_executed.marker"
    (outside / "child.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('ESCAPED')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    module_name = "_c40_descendant_probe"
    try:
        loaded = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_pkg",
            package=True,
        )
        assert loaded is not None
        safe_child = importlib.import_module(module_name + ".child")
        assert safe_child.SAFE == "from-attested-descriptor"
        assert Path(safe_child.__file__).resolve() == (
            legacy / "child.py"
        ).resolve()
        leaf = importlib.import_module(module_name + ".sub.leaf")
        assert leaf.LEAF == "attested-leaf"
        assert Path(leaf.__file__).resolve() == (sub / "leaf.py").resolve()

        # Fail-closed: nieistniejacy potomek NIE spada do PathFindera.
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name + ".sub.absent_leaf")

        # Podmiana katalogu po load: utrzymany deskryptor wskazuje ORYGINAŁ,
        # więc obce bajty (outside/child.py) NIGDY się nie wykonują — potomek
        # jest albo serwowany z oryginału, albo (gdy oryginał usunięty)
        # fail-closed. Tu usuwamy oryginał → import potomka pada, marker pusty.
        import shutil
        sys.modules.pop(module_name + ".child", None)
        os.rename(legacy, scripts / "legacy_pkg_moved")
        shutil.rmtree(scripts / "legacy_pkg_moved")
        (scripts / "legacy_pkg").symlink_to(outside, target_is_directory=True)
        importlib.invalidate_caches()
        with pytest.raises((RuntimeError, ModuleNotFoundError)):
            importlib.import_module(module_name + ".child")
        assert not marker.exists(), "obce bajty potomka wykonane po podmianie"
        assert module_name + ".child" not in sys.modules
    finally:
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)


def test_package_descendant_route_is_identity_bound_to_recorded_package(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate40 O2b: trasa potomkow wiaze TOZSAMOSC zapisanego pakietu.

    Oracle luki stale-route: tabela tras kluczowana sama nazwa pozwalalaby
    finderowi dalej przechwytywac dzieci po podmianie sys.modules[root] na
    obcy ModuleType — fizyczne bajty potomka zostalyby wykonane i doczepione
    do OBCEGO parenta. Kontrakt C40: potomek importuje sie wylacznie pod
    dokladnie tym obiektem pakietu, ktory loader zapisal; obcy parent =
    deterministyczny konflikt, fizyczne bajty nie wykonane.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "physical_child_executed.marker"
    (legacy / "child.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('PHYSICAL_CHILD')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    module_name = "_c40_identity_route_probe"
    try:
        loaded = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_pkg",
            package=True,
        )
        assert loaded is not None
        forged = ModuleType(module_name)
        forged.__file__ = str(legacy / "__init__.py")
        forged.__path__ = [str(legacy)]
        monkeypatch.setitem(sys.modules, module_name, forged)
        with pytest.raises(
            RuntimeError,
            match="conflicting preloaded physical sibling",
        ):
            importlib.import_module(module_name + ".child")
        assert not marker.exists(), (
            "fizyczne bajty potomka wykonane pod OBCYM parentem"
        )
        assert not hasattr(forged, "child")
        assert module_name + ".child" not in sys.modules
    finally:
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)


def test_c40_mutation_probe_route_free_descendants_re_red_oracle(
    tmp_path,
    monkeypatch,
) -> None:
    """Mutation probe M6 (zaktualizowane Candidate51): route-free = FAIL-CLOSED.

    Mutant = brak rejestracji trasy potomkow. W C39/C40 powrot do rozwiazywania
    przez PathFinder po `submodule_search_locations` pozwalal podmienionemu
    symlinkowi wykonac obce bajty. Candidate51 czyni `__path__` pakietu PUSTYM,
    wiec nawet BEZ trasy potomek NIE MOZE spasc do PathFindera — obrona w glab:
    brak trasy => potomek nie rozwiazuje sie (`ModuleNotFoundError`) i obce bajty
    NIGDY sie nie wykonuja (marker nieobecny). Trasa pozostaje load-bearing dla
    LEGALNego rozwiazania potomka; puste `__path__` gwarantuje fail-closed przy
    jej braku, zamiast ucieczki.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "child.py").write_text("SAFE = True\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "outside_executed.marker"
    (outside / "child.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('ESCAPED')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    module_name = "_c40_route_free_mutant_probe"
    try:
        loaded = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_pkg",
            package=True,
        )
        assert loaded is not None
        # Mutant: trasa potomkow znika (stan C39).
        physical_import._PHYSICAL_PACKAGE_ROUTES.pop(module_name, None)
        os.rename(legacy, scripts / "legacy_pkg_moved")
        (scripts / "legacy_pkg").symlink_to(outside, target_is_directory=True)
        importlib.invalidate_caches()
        # Candidate51: puste `__path__` => bez trasy potomek pada FAIL-CLOSED,
        # obce bajty NIGDY nie wykonane (wcześniej: ucieczka przez PathFinder).
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name + ".child")
        assert not marker.exists(), (
            "puste __path__ MUSI blokować ucieczkę potomka nawet bez trasy"
        )
    finally:
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)


def test_optional_missing_sibling_with_foreign_preload_is_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate40 O3 (Opus F1): opcjonalnosc pliku nie omija ownera nazwy.

    Oracle defektu C39: przy required=False i fizycznie nieobecnym siblingu
    loader zwracal None PRZED wywolaniem jedynego walidatora reuse — obcy
    modul pod ta nazwa przechodzil bez konfliktu. Kontrakt C40: decyzja
    nazwy/modulu zapada ZAWSZE u jedynego ownera; stub None wylacznie dla
    rzeczywiscie pustego, bezkonfliktowego przypadku.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})

    module_name = "_c40_optional_conflict_probe"
    forged = ModuleType(module_name)
    forged.__file__ = str(scripts / "absent_sibling.py")
    monkeypatch.setitem(sys.modules, module_name, forged)
    with pytest.raises(
        RuntimeError,
        match="conflicting preloaded physical sibling",
    ):
        physical_import.load_physical_scripts_sibling(
            module_name,
            "absent_sibling.py",
            required=False,
        )
    assert module_name not in physical_import._LOADED_PHYSICAL_SIBLINGS
    assert sys.modules[module_name] is forged


def test_optional_missing_sibling_clean_case_returns_stub(
    tmp_path,
    monkeypatch,
) -> None:
    """Kontrapunkt O3: pusty, bezkonfliktowy przypadek nadal daje stub None."""
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})

    module_name = "_c40_optional_clean_probe"
    assert module_name not in sys.modules
    assert (
        physical_import.load_physical_scripts_sibling(
            module_name,
            "absent_sibling.py",
            required=False,
        )
        is None
    )
    assert module_name not in sys.modules
    assert module_name not in physical_import._LOADED_PHYSICAL_SIBLINGS


def test_optional_recorded_sibling_reused_after_source_removal(
    tmp_path,
    monkeypatch,
) -> None:
    """O3: reuse zapisanego przez ownera modulu przezywa znikniecie zrodla.

    Rekord rejestru (tozsamosc + zrodlo) jest jedynym dowodem reuse; decyzja
    zapada u ownera takze wtedy, gdy plik zniknal po legalnym loadzie.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    sibling = scripts / "legacy_sibling.py"
    sibling.write_text("DISK_VALUE = 'from-attested-descriptor'\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    module_name = "_c40_recorded_after_removal_probe"
    try:
        first = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_sibling.py",
            required=False,
        )
        assert first is not None
        sibling.unlink()
        second = physical_import.load_physical_scripts_sibling(
            module_name,
            "legacy_sibling.py",
            required=False,
        )
        assert second is first
    finally:
        sys.modules.pop(module_name, None)


def test_worker_binds_physical_schedule_utils_when_present(tmp_path) -> None:
    """Candidate40 O3d (Opus F1): pozytywny oracle konsumenta worker.py.

    Przy fizycznie obecnym schedule_utils.py obok atestowanego parenta
    worker MUSI zwiazac dokladnie fizyczny modul (rc == 0, load_schedule
    z bajtow siblinga), a nie stub.
    """
    scripts = _physical_scripts_layout(
        tmp_path, with_src=False, with_schedule=True
    )
    child = (
        "import sys\n"
        "import dispatch_v2\n"
        f"dispatch_v2.__file__ = {str(scripts / 'dispatch_v2' / '__init__.py')!r}\n"
        "import dispatch_v2.shift_notifications.worker as worker\n"
        "schedule_module = sys.modules['schedule_utils']\n"
        "import pathlib\n"
        "assert pathlib.Path(schedule_module.__file__).resolve() == "
        f"pathlib.Path({str(scripts / 'schedule_utils.py')!r}).resolve()\n"
        "assert worker.load_schedule is schedule_module.load_schedule\n"
        "assert worker.load_schedule() == {'sentinel': 'physical-schedule'}\n"
        "print('C40_WORKER_PHYSICAL_OK')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", child],
        check=False,
        capture_output=True,
        text=True,
        env=_physical_sibling_subprocess_env(tmp_path, scripts),
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "C40_WORKER_PHYSICAL_OK" in completed.stdout, output


def test_worker_optional_absent_with_foreign_preload_fails_closed(
    tmp_path,
) -> None:
    """Candidate40 O3d (Opus F1): worker + obcy preload nazwy = twardy konflikt.

    Oracle defektu C39: obcy schedule_utils w sys.modules + fizycznie
    nieobecny sibling → worker importowal sie cicho ze stubem, omijajac
    kontrakt nazwy. C40: import workera MUSI paisc deterministycznym
    konfliktem ownera, obce bajty nie zostaja adoptowane.
    """
    scripts = _physical_scripts_layout(
        tmp_path, with_src=False, with_schedule=False
    )
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "schedule_utils.py").write_text(
        "FOREIGN = True\n"
        "def load_schedule():\n"
        "    return {'sentinel': 'foreign'}\n",
        encoding="utf-8",
    )
    child = (
        "import sys\n"
        f"sys.path.insert(0, {str(foreign)!r})\n"
        "import schedule_utils\n"
        "assert schedule_utils.FOREIGN\n"
        "import dispatch_v2\n"
        f"dispatch_v2.__file__ = {str(scripts / 'dispatch_v2' / '__init__.py')!r}\n"
        "try:\n"
        "    import dispatch_v2.shift_notifications.worker\n"
        "except RuntimeError as exc:\n"
        "    assert 'conflicting preloaded physical sibling' in str(exc)\n"
        "    print('C40_WORKER_CONFLICT_OK')\n"
        "else:\n"
        "    print('C40_WORKER_IMPORTED_WITH_STUB')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", child],
        check=False,
        capture_output=True,
        text=True,
        env=_physical_sibling_subprocess_env(tmp_path, scripts),
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "C40_WORKER_CONFLICT_OK" in completed.stdout, output
    assert "C40_WORKER_IMPORTED_WITH_STUB" not in output


_OWNER_REGISTRY = "_LOADED_PHYSICAL_SIBLINGS"
_OWNER_ROUTES = "_PHYSICAL_PACKAGE_ROUTES"
_OWNER_DESCENDANTS = "_PHYSICAL_DESCENDANT_MODULES"


def _lookup_scoped_alias(
    aliases: dict[tuple[str, str], str], scope: str, name: str
) -> str | None:
    """Rozwiąż (scope,name)->kind wędrując po ŁAŃCUCHU scope'ów otaczających.

    Scope jest kwalifikowaną ścieżką (`Klasa.metoda.wewn`); nazwa dowiązana w
    scope OTACZAJĄCYM jest widoczna w zagnieżdżonym (domknięcie funkcji), więc
    lookup próbuje najbliższy scope, a potem KAŻDY prefiks aż do `<module>`.
    Najbliższe dowiązanie wygrywa (shadowing). Uogólnia dawny
    `get((scope,name)) or get(("<module>",name))` (Candidate59/Sol C58 #4):
    tamten mijał aliasy z pośrednich scope'ów funkcji, przez co alias write-bound
    z otaczającego `def` nie był widziany w zagnieżdżonym `def`.
    """
    current = scope
    while True:
        hit = aliases.get((current, name))
        if hit is not None:
            return hit
        if current == "<module>":
            return None
        current = current.rsplit(".", 1)[0] if "." in current else "<module>"


def _ownership_kind_of(
    node: ast.AST,
    scope: str,
    aliases: dict[tuple[str, str], str],
) -> str | None:
    """Sklasyfikuj wyrażenie jako sys | sys.modules | registry | routes | None.

    Rozpoznaje dowiązania przez KAŻDĄ naturalną ścieżkę składniową:
      - literalne `sys`, `sys.modules`, `_LOADED_PHYSICAL_SIBLINGS`, `_PHYSICAL_PACKAGE_ROUTES`,
      - alias importowy: `import sys as _s`, `from sys import modules as m`,
      - alias przez przypisanie/krotkę/walrus: `_mc = sys.modules`, `(x := sys.modules)`,
      - `getattr(<sys>, 'modules')`, `getattr(<x>, '<rejestr|trasy>')`,
      - łańcuchy: `_s = sys; _s.modules`, `_a = _mc`.
    `aliases` mapuje (scope, name) -> kind i jest zbudowane do fixpointu
    ZANIM detektor naruszeń go użyje (Candidate43).
    """
    if isinstance(node, ast.Name):
        if node.id == _OWNER_REGISTRY:
            return "registry"
        if node.id == _OWNER_ROUTES:
            return "routes"
        if node.id == _OWNER_DESCENDANTS:
            return "descendants"
        if node.id == "sys":
            return "sys"
        return _lookup_scoped_alias(aliases, scope, node.id)
    if isinstance(node, ast.Attribute):
        if node.attr == "modules" and (
            _ownership_kind_of(node.value, scope, aliases) == "sys"
        ):
            return "sys.modules"
        # Candidate58 (świeży blind Sol/max na C57): bound-method chronionego
        # dundera ZAPISU (`X.__setitem__` / `X.__delitem__`, gdzie X rozwiązuje
        # się do registry/routes/descendants/sys.modules) jest WARTOŚCIĄ
        # wywoływalną. Nadaj mu odrębny kind "write-bound:<base>", żeby kanoniczny
        # fixpoint aliasów (`_collect_ownership_aliases`) rozniósł go na NAZWY
        # (`rw = X.__setitem__; rw(k,v)`), tuple/walrus/annotated dokładnie tak
        # samo jak inne aliasy — wywołanie takiej nazwy = ZAPIS do <base>. C57
        # dodał WYŁĄCZNIE bezpośredni `func.attr in (...)` check bez integracji
        # z maszynerią aliasów, przez co forma NAZWA-alias omijała ratchet.
        if node.attr in ("__setitem__", "__delitem__"):
            base = _ownership_kind_of(node.value, scope, aliases)
            if base in ("registry", "routes", "descendants", "sys.modules"):
                return "write-bound:" + base
        return None
    if isinstance(node, ast.Subscript):  # Candidate52/Sol C51: `<sys>.__dict__['modules']`
        base = node.value
        key = node.slice
        if (
            isinstance(base, ast.Attribute)
            and base.attr == "__dict__"
            and _ownership_kind_of(base.value, scope, aliases) == "sys"
            and isinstance(key, ast.Constant)
            and key.value == "modules"
        ):
            return "sys.modules"
        return None
    if isinstance(node, ast.NamedExpr):  # (x := <value>) w wyrażeniu
        return _ownership_kind_of(node.value, scope, aliases)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        attr = node.args[1].value
        base = _ownership_kind_of(node.args[0], scope, aliases)
        if attr == "modules" and base == "sys":
            return "sys.modules"
        if attr == _OWNER_REGISTRY:
            return "registry"
        if attr == _OWNER_ROUTES:
            return "routes"
        if attr == _OWNER_DESCENDANTS:
            return "descendants"
    return None


def _collect_ownership_aliases(tree: ast.Module):
    """Zbierz aliasy (scope,name)->kind do FIXPOINTU.

    Candidate41: alias funkcyjny. Candidate42: alias module-level + getattr.
    Candidate43 (Sol+Opus, zgodnie): alias IMPORTOWY
    (`from sys import modules as m`, `import sys as _s`), rozpakowanie krotki
    (`a, b = sys.modules, None`) oraz walrus też muszą być rozpoznane;
    detekcja `sys.modules` przeszła z „literalny Name sys" na „dowolne
    dowiązanie do modułu sys lub jego atrybutu modules" (`_ownership_kind_of`).
    Fixpoint domyka łańcuchy aliasów niezależnie od kolejności w pliku.

    Candidate59 (Sol C58 #4): fixpoint rozniósł aliasy write-bound/chronione
    także przez GRANICE PARAMETRÓW funkcji lokalnych, bo C58 wiązał wyłącznie
    przypisania/krotki/walrus w JEDNYM scope. Domknięte formy NAZWA-alias:
      - dziedziczenie scope (`_lookup_scoped_alias`): zagnieżdżony `def` widzi
        alias z otaczającego `def`;
      - parametr DOMYŚLNY (`def f(rw=X.__setitem__)`): default liczony w scope
        otaczającym, parametr dowiązany w scope ciała funkcji;
      - ARGUMENT przekazany do wywołania funkcji lokalnej
        (`def f(rw): rw(...); f(X.__setitem__)`): pozycyjne/nazwane argumenty
        mapowane na parametry, każdy dowiązany w scope ciała wywoływanej funkcji.
    getattr/exec/refleksja pozostają jawną granicą (backstop behawioralny).
    """
    aliases: dict[tuple[str, str], str] = {}
    # (def_scope, name) -> (body_scope, ast.arguments) dla funkcji LOKALNYCH,
    # by związać argumenty ich wywołań z parametrami (Candidate59). Persystuje
    # przez iteracje fixpointu, więc call-before-def domyka się w kolejnym obiegu.
    funcs: dict[tuple[str, str], tuple[str, ast.arguments]] = {}
    stack: list[str] = []
    changed = False
    # Priorytet: analiza jest flow-INSENSITIVE (Candidate44/Sol), więc nazwa
    # RAZ dowiązana do chronionego obiektu pozostaje chroniona mimo późniejszego
    # rebindu do `sys` — inaczej `_c = sys.modules; ...; _c = sys` chowałby
    # wcześniejszy odczyt. Chronione (3) nie degraduje do `sys` (1).
    _priority = {
        "registry": 3, "routes": 3, "sys.modules": 3, "descendants": 3,
        # Candidate58: aliasy bound-method chronionego dundera zapisu są
        # chronione (nie degradują do "sys"), spójnie z bazowymi kindami — nazwa
        # RAZ dowiązana do `X.__setitem__` pozostaje write-bound mimo rebindu.
        "write-bound:registry": 3, "write-bound:routes": 3,
        "write-bound:descendants": 3, "write-bound:sys.modules": 3,
        "sys": 1,
    }

    def bind(scope: str, name: str, kind: str | None) -> None:
        nonlocal changed
        if kind is None:
            return
        current = aliases.get((scope, name))
        if current is None or _priority.get(kind, 0) > _priority.get(current, 0):
            if current != kind:
                aliases[(scope, name)] = kind
                changed = True

    class AliasVisitor(ast.NodeVisitor):
        def _scope(self) -> str:
            return stack[-1] if stack else "<module>"

        def _push(self, name: str) -> None:
            # Scope KWALIFIKOWANY (Candidate52) — spójny z
            # `_scope_and_parent_maps`: funkcja/klasa zagnieżdżona dostaje
            # "rodzic.nazwa"; moduł → goła nazwa. Musi zgadzać się z lookupem
            # aliasów, inaczej alias w metodzie klasy nie zostanie rozwiązany.
            parent = stack[-1] if stack else "<module>"
            stack.append(name if parent == "<module>" else parent + "." + name)

        def _positional_params(self, args: ast.arguments) -> list:
            return list(getattr(args, "posonlyargs", [])) + list(args.args)

        def _bind_param_defaults(
            self, enclosing: str, body_scope: str, args: ast.arguments
        ) -> None:
            # default parametru pozycyjnego liczony w scope OTACZAJĄCYM, param
            # dowiązany w scope CIAŁA funkcji (Candidate59 #4b). defaults są
            # wyrównane do OSTATNICH len(defaults) parametrów pozycyjnych.
            posargs = self._positional_params(args)
            defaults = list(args.defaults)
            for arg, default in zip(posargs[len(posargs) - len(defaults):], defaults):
                bind(
                    body_scope,
                    arg.arg,
                    _ownership_kind_of(default, enclosing, aliases),
                )
            for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                if default is not None:
                    bind(
                        body_scope,
                        arg.arg,
                        _ownership_kind_of(default, enclosing, aliases),
                    )

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            enclosing = self._scope()
            self._push(node.name)
            body_scope = stack[-1]
            # rejestr funkcji lokalnej (widocznej w scope OTACZAJĄCYM) do wiązania
            # argumentów jej wywołań (Candidate59 #4c).
            funcs[(enclosing, node.name)] = (body_scope, node.args)
            self._bind_param_defaults(enclosing, body_scope, node.args)
            for stmt in node.body:
                self.visit(stmt)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._push(node.name)
            for stmt in node.body:
                self.visit(stmt)
            stack.pop()

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if alias.name == "sys":
                    bind(self._scope(), alias.asname or "sys", "sys")
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module == "sys":
                for alias in node.names:
                    if alias.name == "modules":
                        bind(
                            self._scope(),
                            alias.asname or "modules",
                            "sys.modules",
                        )
            self.generic_visit(node)

        def _bind_targets(self, target: ast.AST, value: ast.AST) -> None:
            scope = self._scope()
            if (
                isinstance(target, (ast.Tuple, ast.List))
                and isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
            ):
                for t, v in zip(target.elts, value.elts):
                    self._bind_targets(t, v)
                return
            if isinstance(target, ast.Name):
                bind(scope, target.id, _ownership_kind_of(value, scope, aliases))

        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                self._bind_targets(target, node.value)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.value is not None and isinstance(node.target, ast.Name):
                bind(
                    self._scope(),
                    node.target.id,
                    _ownership_kind_of(node.value, self._scope(), aliases),
                )
            self.generic_visit(node)

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
            if isinstance(node.target, ast.Name):
                bind(
                    self._scope(),
                    node.target.id,
                    _ownership_kind_of(node.value, self._scope(), aliases),
                )
            self.generic_visit(node)

        def _lookup_func(self, scope: str, name: str):
            # Funkcja lokalna jest widoczna w scope, w którym ją zdefiniowano ORAZ
            # w scope'ach zagnieżdżonych — szukaj po łańcuchu prefiksów.
            current = scope
            while True:
                target = funcs.get((current, name))
                if target is not None:
                    return target
                if current == "<module>":
                    return None
                current = (
                    current.rsplit(".", 1)[0] if "." in current else "<module>"
                )

        def visit_Call(self, node: ast.Call) -> None:
            # ARGUMENT przekazany do wywołania funkcji lokalnej wiąże jej parametry
            # (Candidate59 #4c). Wartość argumentu liczona w scope WYWOŁANIA, param
            # dowiązany w scope CIAŁA wywoływanej funkcji.
            call_scope = self._scope()
            if isinstance(node.func, ast.Name):
                target = self._lookup_func(call_scope, node.func.id)
                if target is not None:
                    body_scope, fargs = target
                    posparams = self._positional_params(fargs)
                    for param, arg in zip(posparams, node.args):
                        if isinstance(arg, ast.Starred):
                            break
                        bind(
                            body_scope,
                            param.arg,
                            _ownership_kind_of(arg, call_scope, aliases),
                        )
                    named = {
                        a.arg for a in posparams + list(fargs.kwonlyargs)
                    }
                    for kw in node.keywords:
                        if kw.arg is not None and kw.arg in named:
                            bind(
                                body_scope,
                                kw.arg,
                                _ownership_kind_of(kw.value, call_scope, aliases),
                            )
            self.generic_visit(node)

    # FIXPOINT DO KONWERGENCJI (Candidate60/Sol C59 #2): iteruj aż `changed`
    # przestanie się ustawiać — BEZ capa. Poprzedni cap `iterations < 12`
    # obcinał propagację po 12 przebiegach; ODWRÓCONY łańcuch aliasów write-bound
    # dundera długości ≥12 (każdy przebieg rozwiązuje 1 poziom wstecz) NIE zbiegał
    # → głębokie aliasy zostawały None → `violations=[]` (BYPASS). Fixpoint jest
    # MONOTONICZNY i skończony: `bind` ustawia `changed` WYŁĄCZNIE gdy dodaje nowy
    # klucz albo PODNOSI priorytet istniejącego (0→3, patrz `_priority`), zbiór
    # kluczy (scope, name) jest ograniczony przez AST, a priorytety mają górną
    # granicę — więc suma priorytetów po kluczach ściśle rośnie w każdej iteracji
    # ustawiającej `changed` i jest ograniczona z góry ⇒ pętla ZAWSZE się zatrzymuje
    # (brak nieskończonej pętli). Jeden helper zasila OBU konsumentów (ownership +
    # retirement), więc fix domyka bypass w obu naraz.
    changed = True
    while changed:
        changed = False
        AliasVisitor().visit(tree)
    return aliases


def _scope_and_parent_maps(tree: ast.Module):
    """Zwróć (parents, scope_of) — węzeł→rodzic i węzeł→scope funkcji."""
    parents: dict[int, ast.AST] = {}
    scope_of: dict[int, str] = {id(tree): "<module>"}

    def walk(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
            scope_of[id(child)] = scope
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                # Scope KWALIFIKOWANY nazwą zagnieżdżenia (Candidate49/Sol C48)
                # ORAZ nazwą KLASY (Candidate52/Sol C51): autoryzowany scope to
                # dokładnie kwalifikowana ścieżka, np.
                # `_PhysicalSiblingDescendantFinder.find_spec`. Funkcja
                # MODUŁOWA zachowuje gołą nazwę; metoda o nazwie autoryzowanego
                # czytelnika w INNEJ (obcej) klasie dostaje inny prefiks klasy
                # → jej odczyty sys.modules/rejestru są flagowane (nie może
                # podszyć się pod findera samą nazwą `find_spec`).
                child_scope = (
                    child.name
                    if scope == "<module>"
                    else scope + "." + child.name
                )
            else:
                child_scope = scope
            walk(child, child_scope)

    walk(tree, "<module>")
    return parents, scope_of


def _is_protected_value_read(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """Czy węzeł (ewaluujący chroniony obiekt) jest ODCZYTEM WARTOŚCI.

    SOUND: dowolne użycie obiektu jako wartości (subscript-load, metoda-lookup
    `.get/.items/.keys/.values/.setdefault/.copy`, iteracja, argument wywołania
    np. `operator.getitem(sys.modules, x)`, operand) = odczyt. Zapis
    (`x[k]=...`, `del x[k]`) oraz czysta mutacja (`.pop/.clear/.update/.popitem`)
    NIE są odczytem.
    """
    parent = parents.get(id(node))
    if parent is None:
        return True
    if isinstance(parent, ast.Subscript) and parent.value is node:
        return isinstance(parent.ctx, ast.Load)
    if isinstance(parent, ast.Attribute) and parent.value is node:
        if parent.attr in ("clear", "update", "__delitem__", "__setitem__"):
            return False
        if parent.attr in ("pop", "popitem"):
            # Candidate45 (Sol C44): pop/popitem ZWRACAJĄ zdjęty moduł. Jako
            # bare-statement (wynik odrzucony) = czysta mutacja (idiom cleanup);
            # gdy wynik jest UŻYTY (przypisany/zwrócony/w warunku) = odczyt
            # wartości → fast-path `x = sys.modules.pop(name); return x`.
            call = parents.get(id(parent))
            if isinstance(call, ast.Call):
                return not isinstance(parents.get(id(call)), ast.Expr)
            return True
        return True
    if isinstance(parent, ast.Assign) and node in parent.targets:
        return False
    if isinstance(parent, (ast.AugAssign, ast.AnnAssign)) and (
        getattr(parent, "target", None) is node
    ):
        return False
    return True


def _for_body_stmts(node):
    return list(getattr(node, "body", []))


def _contains_return_or_yield(nodes) -> bool:
    """Czy w poddrzewach jest return/yield POZA zagnieżdżonymi funkcjami.

    Candidate45 (Opus C44): poprzednia wersja używała `ast.walk` (płaskie BFS)
    z `break` na pierwszej zagnieżdżonej funkcji — a `walk` kolejkuje nested
    `def` PRZED zewnętrznym `return` z tego samego statementu, więc return był
    mijany. Tu jawna rekurencja DFS: pomija WYŁĄCZNIE ciała zagnieżdżonych
    funkcji/lambd (`continue`), ale przegląda całą resztę.
    """
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            continue  # własny scope — nie liczy się do tej pętli
        stack.extend(ast.iter_child_nodes(node))
    return False


# Wrappery, które na builtinach zwracają KLUCZE słownika. Zaufane TYLKO gdy
# NIE są przesłonięte w pliku (Candidate46/Sol: `def tuple(m): return
# m.values()` zamieniłby snapshot-kluczy w ekspozycję obiektów modułów).
_TRUSTED_ITER_WRAPPERS = frozenset(
    {"tuple", "list", "sorted", "set", "frozenset", "reversed", "iter"}
)


def _shadowed_wrapper_names(tree: ast.Module) -> frozenset:
    """Nazwy z `_TRUSTED_ITER_WRAPPERS` PRZESŁONIĘTE w pliku (def/assign/import/arg)."""
    shadowed: set[str] = set()

    def note(name):
        if name in _TRUSTED_ITER_WRAPPERS:
            shadowed.add(name)

    def note_args(args):
        for a in (
            list(getattr(args, "posonlyargs", []))
            + list(args.args)
            + list(args.kwonlyargs)
            + ([args.vararg] if args.vararg else [])
            + ([args.kwarg] if args.kwarg else [])
        ):
            note(a.arg)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            note(node.name)
            note_args(node.args)
        elif isinstance(node, ast.Lambda):  # Candidate48/Sol: parametry lambdy
            note_args(node.args)
        elif isinstance(node, ast.ClassDef):
            note(node.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    # Wildcard import (`from x import *`) może wprowadzić DOWOLNĄ
                    # nazwę, w tym wrapper — konserwatywnie traktuj WSZYSTKIE
                    # zaufane wrappery jako przesłonięte (Candidate49/Sol C48).
                    shadowed.update(_TRUSTED_ITER_WRAPPERS)
                else:
                    note(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                note(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler):  # Candidate52/Sol C51
            if node.name is not None:  # `except ... as tuple:` wiąże `tuple`
                note(node.name)
        elif isinstance(node, ast.MatchAs):  # Candidate49/Sol C48: `case tuple:`
            if node.name is not None:
                note(node.name)
        elif isinstance(node, ast.MatchStar):  # `case [*tuple]`
            if node.name is not None:
                note(node.name)
        elif isinstance(node, ast.MatchMapping):  # `case {**tuple}`
            if node.rest is not None:
                note(node.rest)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = (
                node.targets if isinstance(node, ast.Assign)
                else [node.target]
            )
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        note(sub.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    note(sub.id)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    note(sub.id)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for sub in ast.walk(item.optional_vars):
                        if isinstance(sub, ast.Name):
                            note(sub.id)
    return frozenset(shadowed)


def _iter_exposes_module_values(
    iter_node: ast.AST,
    aliases: dict[tuple[str, str], str],
    parents: dict[int, ast.AST],
    scope_of: dict[int, str],
    shadowed_wrappers: frozenset,
) -> bool:
    """Czy iter EKSPONUJE obiekty modułów (nie tylko klucze/nazwy).

    Candidate46 (Opus C45): whitelist cleanup MUSI iterować wyłącznie po
    KLUCZACH (nazwach) — `for name in sys.modules` / `tuple(sys.modules)` /
    `sys.modules.keys()`. Iteracja `.items()`/`.values()` (albo subscript/`.get`
    w iter) wystawia OBIEKTY MODUŁÓW, które body może przechwycić do zmiennej
    i zwrócić PO pętli — więc taki iter NIE jest cleanupem.
    Candidate46 (Sol): wrapper `tuple`/`list`/... jest zaufany jako
    snapshot-kluczy TYLKO gdy NIE jest przesłonięty w pliku (`shadowed_wrappers`).
    """
    for sub in ast.walk(iter_node):
        if _ownership_kind_of(
            sub, scope_of.get(id(sub), "<module>"), aliases
        ) != "sys.modules":
            continue
        parent = parents.get(id(sub))
        # Klucze: bare iteration (parent to For.iter → Name/Attribute wprost),
        # arg do NIEPRZESŁONIĘTEGO tuple/list/sorted/set/frozenset/reversed/iter,
        # albo `.keys()`.
        if isinstance(parent, ast.Attribute) and parent.value is sub:
            if parent.attr == "keys":
                continue
            return True  # items/values/get/... eksponują wartości
        if isinstance(parent, ast.Subscript) and parent.value is sub:
            return True  # sys.modules[x] w iter — wartość
        if isinstance(parent, ast.Call) and sub in parent.args:
            fn = parent.func
            fname = fn.id if isinstance(fn, ast.Name) else None
            if (
                fname in _TRUSTED_ITER_WRAPPERS
                and fname not in shadowed_wrappers
            ):
                continue  # zaufany builtin-wrapper zwracający klucze
            return True  # inne/przesłonięte wywołanie z sys.modules — nieznane
        if isinstance(parent, (ast.For, ast.AsyncFor)) and (
            parent.iter is sub
        ):
            continue  # bare `for x in sys.modules` — klucze
        if isinstance(parent, ast.comprehension) and parent.iter is sub:
            continue  # comprehension po kluczach
        # Cokolwiek innego — zachowawczo uznaj za ekspozycję wartości.
        return True
    return False


def _is_cleanup_sys_modules_loop(
    fornode: ast.AST,
    aliases: dict[tuple[str, str], str],
    parents: dict[int, ast.AST],
    scope_of: dict[int, str],
    shadowed_wrappers: frozenset,
) -> bool:
    """Pętla iterująca sys.modules WYŁĄCZNIE po kluczach, bez wycieku modułu.

    To jedyny dozwolony odczyt sys.modules poza ownerem/find_spec. NIE dowodzi
    strukturalnie „body tylko popuje" — egzekwuje SŁABSZY, ale WYSTARCZAJĄCY
    dla bezpieczeństwa zestaw warunków (wszystkie muszą zachodzić):
      1. iter czyta sys.modules WYŁĄCZNIE jako KLUCZE (bare / `keys()` /
         `tuple(...)` itp.), nie `.items()`/`.values()`/subscript — więc
         zmienne pętli to NAZWY (str), nigdy obiekty modułów (Opus C45);
      2. body nie zawiera `return`/`yield` (także po zagnieżdżonej funkcji —
         Opus C44), więc pętla nie może NIC wyemitować;
      3. body nie zawiera żadnego odczytu-WARTOŚCI sys.modules, więc nie może
         pobrać obiektu modułu po nazwie.
    Skutek: pętla nie widzi ani nie zwraca żadnego obiektu modułu (co najwyżej
    nazwy) — dlatego body operujące na NAZWACH poza `pop` (np. zebranie nazw)
    jest dozwolone i BEZPIECZNE; jakiekolwiek późniejsze użycie nazwy do
    zdobycia modułu przechodzi przez odczyt sys.modules (flagowany) lub
    `import_module`/refleksję (łapany behawioralnym backstopem). Realny loader
    używa tu wyłącznie `pop` — to węższe niż egzekwowany kontrakt.
    """
    iter_node = getattr(fornode, "iter", None)
    if iter_node is None:
        return False
    iter_has_sysmodules = any(
        _ownership_kind_of(
            sub, scope_of.get(id(sub), "<module>"), aliases
        ) == "sys.modules"
        for sub in ast.walk(iter_node)
    )
    if not iter_has_sysmodules:
        return False
    # Iter MUSI być iteracją po kluczach — inaczej eksponuje obiekty modułów.
    if _iter_exposes_module_values(
        iter_node, aliases, parents, scope_of, shadowed_wrappers
    ):
        return False
    body = _for_body_stmts(fornode)
    if _contains_return_or_yield(body):
        return False
    for stmt in body:
        for sub in ast.walk(stmt):
            if _ownership_kind_of(
                sub, scope_of.get(id(sub), "<module>"), aliases
            ) == "sys.modules" and _is_protected_value_read(sub, parents):
                return False
    return True


def _collect_protected_value_reads(tree: ast.Module, aliases):
    """{'registry'|'routes'|'sys.modules': [(scope, lineno)]} — odczyty wartości.

    Candidate44: przejście z enumeracji form (`.get`, subscript) na SOUND
    detekcję dowolnego value-readu (Opus C43: `.items()/.values()/.setdefault`,
    `operator.getitem(sys.modules, name)`, iteracja). Odczyty sys.modules
    wewnątrz pętli CLEANUP loadera są wyłączone (whitelist).
    """
    parents, scope_of = _scope_and_parent_maps(tree)
    shadowed_wrappers = _shadowed_wrapper_names(tree)
    permitted_sysmodules: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)) and (
            _is_cleanup_sys_modules_loop(
                node, aliases, parents, scope_of, shadowed_wrappers
            )
        ):
            for sub in ast.walk(node.iter):
                if _ownership_kind_of(
                    sub, scope_of.get(id(sub), "<module>"), aliases
                ) == "sys.modules":
                    permitted_sysmodules.add(id(sub))

    reads: dict[str, list[tuple[str, int]]] = {
        "registry": [],
        "routes": [],
        "sys.modules": [],
        "descendants": [],  # Candidate54/Sol C53: rejestr potomków też chroniony
    }
    for node in ast.walk(tree):
        scope = scope_of.get(id(node), "<module>")
        kind = _ownership_kind_of(node, scope, aliases)
        if kind not in ("registry", "routes", "sys.modules", "descendants"):
            continue
        if not _is_protected_value_read(node, parents):
            continue
        if kind == "sys.modules" and id(node) in permitted_sysmodules:
            continue
        reads[kind].append((scope, getattr(node, "lineno", 0)))
    return reads


def _name_binding_contract(
    tree: ast.Module, name: str
) -> tuple[list[int], list[int], int]:
    """Kanoniczny skan kontraktu „NAZWA = JEDEN module-level `def`".

    Zwraca `(value_refs, rebindings, module_defs)`:
      - value_refs: nazwa użyta jako WARTOŚĆ (Load poza pozycją `func` wywołania
        bezpośredniego) — potencjalny callable-alias omijający licznik wywołań;
      - rebindings: KAŻDE dowiązanie przesłaniające kanoniczny helper (Name
        Store/Del, zagnieżdżony lub kolejny `def`, parametr `arg`);
      - module_defs: liczba module-level `def` tej nazwy (kontrakt wymaga == 1).
    JEDEN resolver dla `_retire_package_route` (Candidate57) i owner-reuse
    `_adopt_or_reject_preloaded` (Candidate59/Sol C58 #5) — bez duplikacji.
    """
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    value_refs: list[int] = []
    rebindings: list[int] = []
    module_defs = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            if isinstance(node.ctx, ast.Load):
                par = parents.get(id(node))
                is_direct_call = isinstance(par, ast.Call) and par.func is node
                if not is_direct_call:
                    value_refs.append(node.lineno)
            elif isinstance(node.ctx, (ast.Store, ast.Del)):
                rebindings.append(node.lineno)
        elif isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name == name:
            if parents.get(id(node)) is tree:
                module_defs += 1
            else:
                rebindings.append(node.lineno)  # zagnieżdżony def = shadow
        elif isinstance(node, ast.arg) and node.arg == name:
            rebindings.append(getattr(node, "lineno", 0))
        elif isinstance(node, ast.alias):
            # Candidate62 (Sol C61 #L2): dowiązanie nazwy przez IMPORT
            # (`from m import x as NAME`, `import m as NAME`, także forma bez
            # `as` gdy nazwa importu == chroniona) przesłania kanoniczny helper
            # tak samo jak Name-Store/def/param. Bez tego
            # `from builtins import print as _adopt_or_reject_preloaded` /
            # `import builtins as _retire_package_route` podmieniały chronioną
            # nazwę NIEWIDZIALNIE dla licznika wywołań (nazwa dalej „woła się"
            # raz, ale wskazuje na obcy callable). Rozszerza istniejący
            # name-binding-contract — bez drugiej maszynerii.
            par = parents.get(id(node))
            if node.asname is not None:
                bound = node.asname
            elif isinstance(par, ast.Import):
                bound = node.name.split(".")[0]  # `import a.b.c` wiąże `a`
            else:  # ImportFrom: `from m import NAME`
                bound = node.name
            if bound == name:
                rebindings.append(getattr(node, "lineno", 0))
    return value_refs, rebindings, module_defs


def _iter_rebound_names(target: ast.AST):
    """Yielduj wszystkie `ast.Name`-targety dowiązane przez `target` w
    przypisaniu — rozpakowuje krotki/listy (w tym JEDNOELEMENTOWE `(X,)` / `[X]`)
    oraz `*X`.

    Candidate62 (Sol C61 #L1): whole-object rebind chronionej mapy przez
    ROZPAKOWANIE (`(_LOADED_PHYSICAL_SIBLINGS,) = ({...},)`) porzuca kanoniczny
    obiekt tak samo jak goły `X = {}`, ale target jest `ast.Tuple`/`ast.List`,
    więc detekcja „tylko `ast.Name`" go mijała. Jeden helper zasila OBU
    konsumentów (ownership `_whole_object_rebind` + retirement), by forma
    liczyła się jako ZAPIS niezależnie od kształtu targetu.
    """
    if isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _iter_rebound_names(elt)
    elif isinstance(target, ast.Starred):
        yield from _iter_rebound_names(target.value)
    elif isinstance(target, ast.Name):
        yield target


def _unbound_dunder_write(
    call: ast.AST, scope: str, aliases: dict[tuple[str, str], str]
):
    """Zwróć `base_kind` gdy `call` to UNBOUND dunder ZAPISU chronionej mapy.

    Formy: `dict.__setitem__(X, k, v)` / `dict.__delitem__(X, k)` (albo dowolny
    `T.__setitem__`/`T.__delitem__`), gdzie X = PIERWSZY argument pozycyjny
    rozwiązuje się do registry/routes/descendants/sys.modules
    (Candidate62/Sol C61 #L3). W formie UNBOUND baza mapy jest ARGUMENTEM, nie
    odbiorcą atrybutu, więc kind `write-bound:<base>` (forma BOUND
    `X.__setitem__(...)`) jej nie łapie.

    Forma BOUND jest tu POMIJANA (baza atrybutu sama rozwiązuje się do
    chronionego obiektu → już liczona przez write-bound), żeby nie dublować.
    Zwraca jeden z: "registry"|"routes"|"descendants"|"sys.modules" albo None.
    """
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
        return None
    if call.func.attr not in ("__setitem__", "__delitem__"):
        return None
    # forma BOUND (`X.__setitem__`) — baza atrybutu jest chronioną mapą → już
    # klasyfikowana jako write-bound; pomiń, by nie liczyć podwójnie.
    if _ownership_kind_of(call.func, scope, aliases) is not None:
        return None
    if not call.args:
        return None
    base_kind = _ownership_kind_of(call.args[0], scope, aliases)
    if base_kind in ("registry", "routes", "descendants", "sys.modules"):
        return base_kind
    return None


def _physical_import_ownership_violations(tree: ast.Module) -> list[str]:
    """Ratchet strukturalny C40/C41 (Opus F2 + Sol F2): jeden owner reuse.

    Wymusza na AST _physical_import.py:
      1. rejestr _LOADED_PHYSICAL_SIBLINGS: czytany WYLACZNIE w
         _adopt_or_reject_preloaded; dokladnie JEDEN zapis, w
         load_physical_scripts_sibling; zadnych innych referencji poza
         definicja module-level;
      2. dokladnie JEDNO wywolanie _adopt_or_reject_preloaded w calym
         module, w load_physical_scripts_sibling;
      3. w publicznym loaderze zakaz sys.modules.get(...) i odczytu
         sys.modules[...] (ksztalty fast-path C38) — RÓWNIEŻ przez alias
         (Candidate41: `x = sys.modules; x.get(...)`);
      4. tabela tras potomkow _PHYSICAL_PACKAGE_ROUTES istnieje, ma
         dokladnie JEDEN zapis (w loaderze), czytana wylacznie w
         find_spec; klasa _PhysicalSiblingDescendantFinder istnieje.

    ZAKRES (Candidate52/Sol C51): ten lint jest DEFENSE-IN-DEPTH dla
    NATURALNYCH refaktorow — NIE jest SOUND (kompletny) wobec adwersaryjnej
    skladni. Odczyt sys.modules przez `getattr(sys, 'mod'+'ules')` (string
    liczony w runtime), `exec`/`eval`, `vars(sys)`, `__import__('sys').modules`
    itp. jest statycznie NIEROZSTRZYGALNY i moze go ominac. SOUND GUARANTEE
    promocji = behawioralny backstop (`test_c45_behavioral_backstop_*`), ktory
    lapie KAZDY mechanizm reuse niezaleznie od skladni; dowod, ze backstop
    lapie fast-path niewidzialny dla tego lintu:
    `test_c52_behavioral_backstop_catches_lint_evading_fastpath`. Lint domyka
    naturalne ksztalty (alias/getattr-const/walrus/match/except-as/wildcard,
    scope kwalifikowany funkcja+klasa, sys.__dict__['modules']) jako wczesny,
    szybki sygnal — nie jako bariera bezpieczenstwa.
    """
    violations: list[str] = []
    registry = "_LOADED_PHYSICAL_SIBLINGS"
    routes = "_PHYSICAL_PACKAGE_ROUTES"
    owner = "_adopt_or_reject_preloaded"
    public_loader = "load_physical_scripts_sibling"
    purge = "_purge_owned_module_subtree"

    aliases = _collect_ownership_aliases(tree)

    stack: list[str] = []
    registry_writes: list[tuple[str, int]] = []
    registry_pops: list[tuple[str, int]] = []
    registry_reads: list[tuple[str, int]] = []
    routes_assignments: list[tuple[str, int]] = []
    routes_mutations: list[tuple[str, int]] = []
    routes_reads: list[tuple[str, int]] = []
    owner_calls: list[tuple[str, int]] = []
    sys_modules_reads: list[tuple[str, int]] = []
    module_defines_routes = False
    finder_class_present = False

    def _name_kind(scope: str, node: ast.AST) -> str | None:
        """registry|routes|sys.modules dla dowolnego dowiązania (Candidate43).

        Deleguje do `_ownership_kind_of`, które rozpoznaje aliasy importowe,
        krotkowe, walrus, getattr i łańcuchy przez `sys`/`sys.modules`. Zwraca
        też "sys" dla samego aliasu modułu — detektor ignoruje to (dopiero
        `<sys>.modules` jest dostępem do rejestru modułów).
        """
        return _ownership_kind_of(node, scope, aliases)

    class Visitor(ast.NodeVisitor):
        def _scope(self) -> str:
            return stack[-1] if stack else "<module>"

        def _push(self, name: str) -> None:
            # Scope KWALIFIKOWANY (Candidate58) — spójny z
            # `_scope_and_parent_maps`/`_collect_ownership_aliases`: metoda w
            # klasie dostaje "Klasa.metoda", funkcja modułowa gołą nazwę. Bez
            # tego lookup aliasów (kluczowanych scope KWALIFIKOWANYM) mijał alias
            # write-bound w metodzie klasy (np. `rw=…__setitem__` w exec_module),
            # bo Visitor kwalifikował scope tylko nazwą funkcji. Autoryzowane
            # scope'y writerów (loader/purge) są MODUŁOWE, więc kwalifikacja nie
            # zmienia ich rozpoznania — domyka wyłącznie aliasy w metodach.
            parent = stack[-1] if stack else "<module>"
            stack.append(name if parent == "<module>" else parent + "." + name)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nonlocal finder_class_present
            if node.name == "_PhysicalSiblingDescendantFinder":
                finder_class_present = True
            self._push(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._push(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def _whole_object_rebind(self, target: ast.AST, lineno: int) -> None:
            # REBIND CAŁEGO chronionego obiektu mapy przez NAZWĘ w scope
            # NIE-modułowym (Candidate59/Sol C58 #6): `X = {}` (też z `global`)
            # w loaderze/metodzie podmienia kanoniczną mapę. Module-level `def`
            # mapy to jedyna dozwolona forma — obsłużona osobno (module scope).
            # Candidate62 (Sol C61 #L1): target ROZPAKOWUJĄCY
            # (`(X,) = (...,)`, `[X] = [...]`, `*X`) też rebinduje mapę przez
            # NAZWĘ — `_iter_rebound_names` spłaszcza go do Name-liści (wspólne z
            # retirement), więc single-element unpack nie omija licznika writerów.
            if not stack:
                return
            for name_node in _iter_rebound_names(target):
                if name_node.id == registry:
                    registry_writes.append((self._scope(), lineno))
                elif name_node.id == routes:
                    routes_assignments.append((self._scope(), lineno))

        def visit_Assign(self, node: ast.Assign) -> None:
            nonlocal module_defines_routes
            if not stack:
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == routes:
                        module_defines_routes = True
            else:
                for target in node.targets:
                    self._whole_object_rebind(target, node.lineno)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            nonlocal module_defines_routes
            if (
                not stack
                and isinstance(node.target, ast.Name)
                and node.target.id == routes
            ):
                module_defines_routes = True
            elif stack and node.value is not None:
                self._whole_object_rebind(node.target, node.lineno)
            self.generic_visit(node)

        def visit_Subscript(self, node: ast.Subscript) -> None:
            # Tylko ZAPISY (odczyty zbiera sound scanner niżej).
            scope = self._scope()
            kind = _name_kind(scope, node.value)
            if kind == "registry" and isinstance(node.ctx, (ast.Store, ast.Del)):
                registry_writes.append((scope, node.lineno))
            elif kind == "routes":
                if isinstance(node.ctx, ast.Store):
                    routes_assignments.append((scope, node.lineno))
                elif isinstance(node.ctx, ast.Del):
                    routes_mutations.append((scope, node.lineno))
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            scope = self._scope()
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == owner:
                owner_calls.append((scope, node.lineno))
            # ZAPIS przez bound-method chronionego dundera — Candidate58: JEDNA
            # kanoniczna ścieżka pokrywa formę BEZPOŚREDNIĄ (`X.__setitem__(k,v)`)
            # i NAZWĘ-ALIAS (`rw = X.__setitem__; rw(k,v)`, tuple/walrus/
            # annotated), bo `_ownership_kind_of` rozwiązuje OBA do
            # "write-bound:<base>" przez fixpoint aliasów. Zastępuje bezpośredni
            # `func.attr` check z C57, który omijała forma aliasowana.
            write_bound = _name_kind(scope, func)
            if write_bound == "write-bound:registry":
                registry_writes.append((scope, node.lineno))
            elif write_bound == "write-bound:routes":
                routes_assignments.append((scope, node.lineno))
            # ZAPIS przez UNBOUND dunder chronionej mapy (Candidate62/Sol C61
            # #L3): `dict.__setitem__(X, k, v)` / `dict.__delitem__(X, k)`, gdzie
            # X (pierwszy arg) = registry/routes. Baza mapy jest ARGUMENTEM (nie
            # odbiorcą atrybutu), więc write-bound (forma bound) jej nie łapie.
            unbound_write = _unbound_dunder_write(node, scope, aliases)
            if unbound_write == "registry":
                registry_writes.append((scope, node.lineno))
            elif unbound_write == "routes":
                routes_assignments.append((scope, node.lineno))
            # Tylko MUTACJE rejestru/tras (odczyty w sound scannerze).
            if isinstance(func, ast.Attribute):
                base_kind = _name_kind(scope, func.value)
                if base_kind in (registry, routes, "registry", "routes"):
                    if func.attr in ("pop", "setdefault", "update", "clear",
                                     "popitem"):
                        if base_kind == "registry":
                            # Mutacja rejestru (pop) = kanoniczny cleanup
                            # rollbacku w purge; ODDZIELNIE od Store-writera.
                            registry_pops.append((scope, node.lineno))
                        else:
                            routes_mutations.append((scope, node.lineno))
            self.generic_visit(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            # AugAssign (`X += ...`, `X[k] += ...`) = ZAPIS (Candidate57/Sol C56).
            scope = self._scope()
            target = node.target
            base = target.value if isinstance(target, ast.Subscript) else target
            kind = _name_kind(scope, base)
            if kind == "registry":
                registry_writes.append((scope, node.lineno))
            elif kind == "routes":
                routes_assignments.append((scope, node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)

    _reads = _collect_protected_value_reads(tree, aliases)
    registry_reads = _reads["registry"]
    routes_reads = _reads["routes"]
    sys_modules_reads = _reads["sys.modules"]
    descendants_reads = _reads["descendants"]

    # `_current_generation_is` (Candidate54) czyta owner-registry i trasę
    # WYŁĄCZNIE do porównania tożsamości generacji (rollback) — autoryzowany
    # obok ownera; nie zwraca obiektu (nie fast-path reuse).
    # `_PhysicalSiblingDescendantFinder.find_spec` (Candidate63/Sol C62) czyta
    # owner-registry WYŁĄCZNIE jako MEMBERSHIP (`fullname in ...`), by fail-close
    # re-resolucji (reload/re-import) nazwy ROOT-LEVEL, którą POSIADAMY — NIE
    # zwraca zapisanego obiektu, więc nie jest fast-path reuse (spójnie z tym, że
    # find_spec jest już autoryzowanym czytelnikiem trasy/sys.modules/descendant).
    bad_registry_reads = [
        entry for entry in registry_reads
        if entry[0] not in (
            owner, "_current_generation_is", "_purge_owned_module_subtree",
            "_PhysicalSiblingDescendantFinder.find_spec",
        )
    ]
    if bad_registry_reads:
        violations.append(
            f"registry read outside owner: {bad_registry_reads}"
        )
    if len(registry_writes) != 1 or registry_writes[0][0] != public_loader:
        violations.append(
            f"registry writers must be exactly one in loader: {registry_writes}"
        )
    # Mutacja rejestru reuse (pop) = kanoniczny cleanup: kontrakt OBECNOŚCI —
    # DOKŁADNIE jeden `_LOADED_PHYSICAL_SIBLINGS.pop`, w purge (jeden writer
    # polityki rollbacku wszystkich rejestrów). Candidate62 (Sol C61 #L4): to
    # kontrakt OBECNOŚCI analogiczny do C46 route-close/`retire_closes` — sam
    # `pop` MUSI ISTNIEĆ, bo bez niego owner-record wycieka i failed/stale
    # generacja jest reused (`owner_record_leaked`/`failed_generation_reused`).
    # Poprzedni guard `if registry_pops and (...)` przepuszczał USUNIĘCIE popa
    # (pusta lista → brak violation); usunięcie/przeniesienie poza purge = teraz
    # violation. Behawioralny backstop nie zwalnia z tego strukturalnego wymogu.
    if len(registry_pops) != 1 or registry_pops[0][0] != purge:
        violations.append(
            f"registry mutator (pop) must be exactly one, in {purge}: "
            f"{registry_pops}"
        )
    if len(owner_calls) != 1 or owner_calls[0][0] != public_loader:
        violations.append(
            f"owner must be called exactly once from loader: {owner_calls}"
        )
    # Owner reuse `_adopt_or_reject_preloaded` = JEDEN module-level `def`, nigdy
    # rebindowany/przesłaniany ani używany jako wartość (Candidate59/Sol C58 #5).
    # Bez tego zagnieżdżony `def _adopt_or_reject_preloaded(...): return None`
    # w exec_module przechwytywał kanonicznego ownera reuse, a licznik wywołań
    # (nazwa niezmieniona, dalej 1 call w loaderze) tego nie wykrywał. Kontrakt
    # analogiczny do `_retire_package_route`, ten sam kanoniczny skan.
    owner_value_refs, owner_rebindings, owner_defs_module = (
        _name_binding_contract(tree, owner)
    )
    if owner_value_refs:
        violations.append(
            f"{owner} referenced as a value (potential callable alias) at "
            f"lines {owner_value_refs}"
        )
    if owner_rebindings or owner_defs_module != 1:
        violations.append(
            f"{owner} must be a single module-level def and never rebound/"
            f"shadowed: rebindings={owner_rebindings}, "
            f"module_defs={owner_defs_module}"
        )
    finder_find_spec = "_PhysicalSiblingDescendantFinder.find_spec"
    # sys.modules jako WARTOŚĆ: owner, find_spec, `_current_generation_is`
    # (porównanie tożsamości generacji). Purge NIE czyta wartości sys.modules —
    # przechwytuje obiekty do derefów z owned-rejestrów, a sys.modules popuje
    # bare (Candidate55).
    bad_sys_modules_reads = [
        entry for entry in sys_modules_reads
        if entry[0] not in (owner, finder_find_spec, "_current_generation_is")
    ]
    if bad_sys_modules_reads:
        violations.append(
            "sys.modules read outside owner/finder (fast-path shape): "
            f"{bad_sys_modules_reads}"
        )
    # Descendant registry jako WARTOŚĆ czytana tylko w find_spec (tożsamość
    # łańcucha przodków) oraz w kanonicznym purge (snapshot kluczy do cleanup);
    # read gdzie indziej = potencjalny fast-path (Candidate54/Sol C53).
    bad_descendants_reads = [
        entry for entry in descendants_reads
        if entry[0] not in (finder_find_spec, "_purge_owned_module_subtree")
    ]
    if bad_descendants_reads:
        violations.append(
            "descendant registry read outside finder/purge (fast-path shape): "
            f"{bad_descendants_reads}"
        )
    if not module_defines_routes:
        violations.append("descendant route table missing")
    if not finder_class_present:
        violations.append("descendant finder class missing")
    if (
        len(routes_assignments) != 1
        or routes_assignments[0][0] != public_loader
    ):
        violations.append(
            "route writers must be exactly one in loader: "
            f"{routes_assignments}"
        )
    # Route READ dozwolony w find_spec (metoda findera), kanonicznym retirement
    # (pop-wynik) oraz `_current_generation_is` (porównanie tożsamości generacji,
    # Candidate54). find_spec kwalifikowany klasą (Candidate52).
    route_readers = (
        "_PhysicalSiblingDescendantFinder.find_spec",
        "_retire_package_route",
        "_current_generation_is",
        "_descendant_generation_current",  # Candidate55/Sol C54 #3
    )
    bad_routes_reads = [
        entry for entry in routes_reads if entry[0] not in route_readers
    ]
    if bad_routes_reads:
        violations.append(
            f"route read outside finder/retire: {bad_routes_reads}"
        )
    # Kontrakt retirement (Candidate46/nadzór): dokładnie jedna mutacja trasy
    # (pop) w `_retire_package_route`, dokładnie jedno wywołanie helpera z
    # rollbacku loadera, dokładnie jeden close handle w helperze; zakaz
    # bezpośrednich popów tras i alternatywnych helperów retirement.
    violations.extend(_retirement_contract_violations(tree))
    return violations


def _retirement_contract_violations(tree: ast.Module) -> list[str]:
    routes = "_PHYSICAL_PACKAGE_ROUTES"
    helper = "_retire_package_route"
    purge = "_purge_owned_module_subtree"
    public_loader = "load_physical_scripts_sibling"
    # scope kwalifikowany klasą (Candidate52/Sol C51):
    exec_module = "_PhysicalSiblingDescendantLoader.exec_module"
    _parents, scope_of = _scope_and_parent_maps(tree)
    # JEDEN kanoniczny resolver aliasów (Candidate46/nadzór) — ten sam fixpoint
    # z tuple/walrus/getattr/import-chain, teraz z kind `descendants`.
    aliases = _collect_ownership_aliases(tree)
    mutators = ("pop", "popitem", "clear", "update", "setdefault")

    def _resolves_descendants(node, scope) -> bool:
        return _ownership_kind_of(node, scope, aliases) == "descendants"

    def _resolves_sysmodules(node, scope) -> bool:
        return _ownership_kind_of(node, scope, aliases) == "sys.modules"

    def _resolves_routes(node, scope) -> bool:
        return _ownership_kind_of(node, scope, aliases) == "routes"

    # `_retire_package_route` może pojawić się WYŁĄCZNIE jako (a) własny `def`
    # oraz (b) BEZPOŚREDNIE wywołanie `_retire_package_route(...)`. KAŻDA inna
    # referencja jako WARTOŚĆ (przypisanie do Name/atrybutu/subskryptu, arg
    # wywołania, element kontenera, walrus, destrukturyzacja) jest potencjalnym
    # aliasem wywoływalnej wartości omijającym licznik retire — flagowana
    # (Candidate56/Sol C55 #3). To domyka WSZYSTKIE formy aliasu naraz, bo każdy
    # alias MUSI najpierw odczytać nazwę funkcji jako wartość.
    # REBINDING/SHADOWING nazwy `_retire_package_route` (Candidate57/Sol C56 #3):
    # nazwa może być zdefiniowana WYŁĄCZNIE przez JEDEN module-level `def`; każde
    # inne dowiązanie (Name Store, zagnieżdżony/inny `def`, arg, import-as, walrus,
    # for-target, with-as) przesłania kanoniczny helper i pozwala rogue-„retire".
    # JEDEN kanoniczny skan (`_name_binding_contract`, Candidate59) — bez duplikacji.
    retire_value_refs, retire_rebindings, retire_defs_module = (
        _name_binding_contract(tree, helper)
    )

    route_mutations: list[tuple[str, int]] = []
    retire_calls: list[tuple[str, int]] = []
    retire_closes: list[int] = []
    desc_writes: list[tuple[str, int]] = []
    desc_pops: list[tuple[str, int]] = []
    sysmod_pops: list[tuple[str, int]] = []
    purge_calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        scope = scope_of.get(id(node), "<module>")
        # ZAPIS przez bound-method chronionego dundera — Candidate58: JEDNA
        # kanoniczna ścieżka pokrywa formę BEZPOŚREDNIĄ (`X.__setitem__(k,v)`) i
        # NAZWĘ-ALIAS (`dw = X.__setitem__; dw(k,v)`, tuple/walrus/annotated), bo
        # `_ownership_kind_of` rozwiązuje OBA do "write-bound:<base>" przez
        # fixpoint aliasów. Zastępuje bezpośredni `_write_dunder` check z C57.
        if isinstance(node, ast.Call):
            _wb = _ownership_kind_of(node.func, scope, aliases)
            if _wb == "write-bound:routes":
                route_mutations.append((scope, node.lineno))
            elif _wb == "write-bound:descendants":
                desc_writes.append((scope, node.lineno))
            elif _wb == "write-bound:sys.modules":
                sysmod_pops.append((scope, node.lineno))
        # ZAPIS przez UNBOUND dunder chronionej mapy (Candidate62/Sol C61 #L3):
        # `dict.__setitem__(X, k, v)` / `dict.__delitem__(X, k)`, gdzie X =
        # descendants/routes/sys.modules przekazany PIERWSZYM argumentem. Forma
        # BOUND idzie przez write-bound wyżej; tu domykamy formę unbound (baza =
        # argument). Jeden helper wspólny z ownershipem — bez drugiej maszynerii.
        if isinstance(node, ast.Call):
            _ubd = _unbound_dunder_write(node, scope, aliases)
            if _ubd == "descendants":
                desc_writes.append((scope, node.lineno))
            elif _ubd == "routes":
                route_mutations.append((scope, node.lineno))
            elif _ubd == "sys.modules":
                sysmod_pops.append((scope, node.lineno))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = node.func.value
            attr = node.func.attr
            if attr in mutators and _resolves_routes(base, scope):
                route_mutations.append((scope, node.lineno))
            if attr in mutators and _resolves_descendants(base, scope):
                desc_pops.append((scope, node.lineno))
            if attr in ("pop", "popitem") and _resolves_sysmodules(base, scope):
                sysmod_pops.append((scope, node.lineno))
            # DOKŁADNIE `handle.close()` (Name `handle`, zero argumentów).
            if (
                attr == "close"
                and scope == helper
                and isinstance(base, ast.Name)
                and base.id == "handle"
                and not node.args
                and not node.keywords
            ):
                retire_closes.append(node.lineno)
        if isinstance(node, ast.Subscript):
            if _resolves_routes(node.value, scope) and isinstance(
                node.ctx, ast.Del
            ):
                route_mutations.append((scope, node.lineno))
        if isinstance(node, ast.Subscript):
            if _resolves_descendants(node.value, scope) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                desc_writes.append((scope, node.lineno))
            if _resolves_sysmodules(node.value, scope) and isinstance(
                node.ctx, ast.Del
            ):
                sysmod_pops.append((scope, node.lineno))
        # AugAssign (`X |= {}`, `X[k] += v`) = ZAPIS/mutacja IN-PLACE (Candidate59/
        # Sol C58 #6): C58 pokrywał AugAssign tylko dla registry/routes w Visitorze
        # ownershipu — descendants (i tu routes/sys.modules) były pominięte. Base
        # alias-aware, bo `|=` na aliasie mutuje realny obiekt.
        if isinstance(node, ast.AugAssign):
            _tgt = node.target
            _base = _tgt.value if isinstance(_tgt, ast.Subscript) else _tgt
            if _resolves_descendants(_base, scope):
                desc_writes.append((scope, node.lineno))
            elif _resolves_routes(_base, scope):
                route_mutations.append((scope, node.lineno))
            elif _resolves_sysmodules(_base, scope):
                sysmod_pops.append((scope, node.lineno))
        # REBIND CAŁEGO obiektu mapy przez NAZWĘ w scope NIE-modułowym
        # (Candidate59/Sol C58 #6): `_PHYSICAL_DESCENDANT_MODULES = {}` /
        # `_PHYSICAL_PACKAGE_ROUTES = {}` (też z `global`) podmienia kanoniczną
        # mapę. Match po LITERALNEJ nazwie (rebind aliasu jest nieszkodliwy);
        # module-level `def` mapy (scope „<module>") pozostaje dozwolony.
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and scope != "<module>":
            _targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            _has_value = node.value is not None
            if _has_value:
                for _t in _targets:
                    # Candidate62 (Sol C61 #L1): rozpakowanie (`(X,) = (...,)`,
                    # `[X] = [...]`, `*X`) rebinduje mapę przez NAZWĘ tak samo
                    # jak goły `X = {}` — `_iter_rebound_names` spłaszcza target
                    # do Name-liści (wspólne z ownershipem).
                    for _n in _iter_rebound_names(_t):
                        if _n.id == _OWNER_DESCENDANTS:
                            desc_writes.append((scope, node.lineno))
                        elif _n.id == routes:
                            route_mutations.append((scope, node.lineno))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # Tylko BEZPOŚREDNIE wywołania (aliasy wyłapane osobno jako
            # value-refs, Candidate56): `_retire_package_route(...)` / `purge(...)`.
            if node.func.id == helper:
                retire_calls.append((scope, node.lineno))
            if node.func.id == purge:
                purge_calls.append((scope, node.lineno))

    out: list[str] = []
    # Alias wywoływalnej wartości retire (dowolna forma) = naruszenie.
    if retire_value_refs:
        out.append(
            f"{helper} referenced as a value (potential callable alias) at "
            f"lines {retire_value_refs}"
        )
    # Rebinding/shadowing nazwy retire = naruszenie (dokładnie 1 module-level def).
    if retire_rebindings or retire_defs_module != 1:
        out.append(
            f"{helper} must be a single module-level def and never rebound/"
            f"shadowed: rebindings={retire_rebindings}, "
            f"module_defs={retire_defs_module}"
        )
    # Descendant registry: DOKŁADNIE jeden Store, w exec_module.
    if len(desc_writes) != 1 or desc_writes[0][0] != exec_module:
        out.append(
            f"descendant registry write must be exactly one, in "
            f"{exec_module}: {desc_writes}"
        )
    # Descendant registry: DOKŁADNIE jeden mutator (pop), w purge.
    if len(desc_pops) != 1 or desc_pops[0][0] != purge:
        out.append(
            f"descendant registry mutator must be exactly one, in "
            f"{purge}: {desc_pops}"
        )
    # sys.modules pop: DOKŁADNIE jeden, w purge (jeden writer purge policy obu
    # rejestrów) — 2× pop w purge też jest naruszeniem.
    if len(sysmod_pops) != 1 or sysmod_pops[0][0] != purge:
        out.append(
            f"sys.modules pop must be exactly one, in {purge}: {sysmod_pops}"
        )
    # purge-call multiset DOKŁADNIE: retire=1, exec_module=1, public_loader=1.
    purge_scopes = sorted(e[0] for e in purge_calls)
    if purge_scopes != sorted((helper, exec_module, public_loader)):
        out.append(
            f"{purge} call-sites must be exactly "
            f"{{{helper}, {exec_module}, {public_loader}}}: {purge_calls}"
        )
    # Route mutation: DOKŁADNIE jedna, w helperze.
    if len(route_mutations) != 1 or route_mutations[0][0] != helper:
        out.append(
            "route mutation must be exactly one, in "
            f"{helper}: {route_mutations}"
        )
    # Loader woła `_retire_package_route` DOKŁADNIE dwa razy (Candidate53):
    # re-registration (retire starej trasy przed publikacją) + rollback
    # (generation-safe). Oba w public_loader; brak wywołań rogue gdzie indziej.
    if len(retire_calls) != 2 or any(
        scope != public_loader for scope, _ in retire_calls
    ):
        out.append(
            f"{helper} must be called exactly twice from loader "
            f"(re-registration + rollback): {retire_calls}"
        )
    if len(retire_closes) != 1:
        out.append(
            f"{helper} must close exactly one handle: {retire_closes}"
        )
    return out


def test_physical_import_single_owner_structural_ratchet() -> None:
    """Candidate40 O4 (Opus F2): strukturalny ratchet jednego ownera reuse."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []


def test_c40_ratchet_rejects_reintroduced_fast_path() -> None:
    """Probe M7a: fast-path sys.modules.get w loaderze = wykryty."""
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "preloaded = _adopt_or_reject_preloaded(module_name, source)"
    assert anchor in real
    mutant = real.replace(
        anchor,
        "existing = sys.modules.get(module_name)\n"
        "                    recorded = _LOADED_PHYSICAL_SIBLINGS.get(module_name)\n"
        "                    if existing is not None and recorded is not None:\n"
        "                        return existing\n"
        "                    " + anchor,
        1,
    )
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any("sys.modules" in item for item in violations), violations
    assert any(
        "registry read outside owner" in item for item in violations
    ), violations


def test_c40_ratchet_rejects_second_registry_writer() -> None:
    """Probe M7b: drugi writer rejestru poza loaderem = wykryty."""
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    mutant = real + (
        "\n\ndef _second_writer(name, source, module):\n"
        "    _LOADED_PHYSICAL_SIBLINGS[name] = (str(source), module)\n"
    )
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "registry writers must be exactly one" in item for item in violations
    ), violations


def test_c40_ratchet_rejects_route_free_module() -> None:
    """Probe M7c: modul bez tabeli tras/findera potomkow = wykryty (stan C39)."""
    mutant = (
        "_LOADED_PHYSICAL_SIBLINGS = {}\n"
        "def _adopt_or_reject_preloaded(module_name, source):\n"
        "    return _LOADED_PHYSICAL_SIBLINGS.get(module_name)\n"
        "def load_physical_scripts_sibling(module_name, relative_path):\n"
        "    preloaded = _adopt_or_reject_preloaded(module_name, relative_path)\n"
        "    _LOADED_PHYSICAL_SIBLINGS[module_name] = (relative_path, preloaded)\n"
        "    return preloaded\n"
    )
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "descendant route table missing" in item for item in violations
    ), violations
    assert any(
        "descendant finder class missing" in item for item in violations
    ), violations


# ─────────────────────────────────────────────────────────────────────────────
# Candidate41 — trzy findingi świeżego Sol/max na frozen C40 (CONFIRMED_DEFECT):
# F1 tożsamość tylko roota, F2 ratchet omijalny aliasami, F3 direct-file ML bez
# pozytywnego oracle (crash akceptowany).
# ─────────────────────────────────────────────────────────────────────────────


def test_package_intermediate_ancestor_is_identity_bound(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate41 O2c (Sol F1): tożsamość CAŁEGO łańcucha przodków potomka.

    Oracle luki C40: finder wiązał trasę wyłącznie z tożsamością pakietu
    GŁÓWNEGO; obcy `ModuleType` wstawiony pod nazwę pakietu POŚREDNIego
    (z własnym `__path__`) przejmował fizycznego potomka — leaf.py był
    wykonywany i doczepiany do obcego parenta zamiast `RuntimeError`.
    Kontrakt C41: każdy pośredni pakiet między rootem a liściem MUSI być
    dokładnie obiektem, który wykonał nasz loader; inaczej deterministyczny
    konflikt, fizyczne bajty liścia nie wykonane.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    root_pkg = scripts / "rootpkg"
    (root_pkg / "sub").mkdir(parents=True)
    (root_pkg / "__init__.py").write_text("", encoding="utf-8")
    (root_pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "leaf_executed.marker"
    (root_pkg / "sub" / "leaf.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('PHYSICAL_LEAF')\n"
        "LEAF = 'physical'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    module_name = "_c41_intermediate_probe"
    try:
        root = physical_import.load_physical_scripts_sibling(
            module_name, "rootpkg", package=True
        )
        assert root is not None
        sub = importlib.import_module(module_name + ".sub")
        assert Path(sub.__file__).resolve() == (
            root_pkg / "sub" / "__init__.py"
        ).resolve()

        foreign = ModuleType(module_name + ".sub")
        foreign.__path__ = [str(root_pkg / "sub")]
        monkeypatch.setitem(sys.modules, module_name + ".sub", foreign)
        sys.modules.pop(module_name + ".sub.leaf", None)
        with pytest.raises(
            RuntimeError,
            match="conflicting preloaded physical sibling",
        ):
            importlib.import_module(module_name + ".sub.leaf")
        assert not marker.exists(), "fizyczny leaf wykonany pod obcym parentem"
        assert not hasattr(foreign, "leaf")
        assert module_name + ".sub.leaf" not in sys.modules
    finally:
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)


def test_c41_mutation_probe_root_only_identity_re_reds_oracle(
    tmp_path,
    monkeypatch,
) -> None:
    """Mutation probe M8: identity tylko roota (stan C40) przywraca ucieczkę.

    Mutant zeruje weryfikację przodków pośrednich (pozostaje tylko root) —
    podmiana pośredniego pakietu obcym obiektem znów wykonuje fizyczny leaf
    pod obcym parentem, więc asercje O2c stają się czerwone.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    root_pkg = scripts / "rootpkg"
    (root_pkg / "sub").mkdir(parents=True)
    (root_pkg / "__init__.py").write_text("", encoding="utf-8")
    (root_pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "leaf_executed.marker"
    (root_pkg / "sub" / "leaf.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('PHYSICAL_LEAF')\n"
        "LEAF = 'physical'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )

    class _RootOnlyDescendants(dict):
        """Stan C40: identity pośrednich pakietów NIE weryfikowana.

        `get(prefix)` zwraca bieżący obiekt z sys.modules, więc kontrola
        `recorded is sys.modules[prefix]` zawsze przechodzi (jak w C40, gdzie
        pętli przodków nie było) — podmieniony pośredni pakiet jest akceptowany.
        """

        def get(self, key, default=None):
            return sys.modules.get(key, default)

    module_name = "_c41_root_only_mutant_probe"
    try:
        root = physical_import.load_physical_scripts_sibling(
            module_name, "rootpkg", package=True
        )
        assert root is not None
        importlib.import_module(module_name + ".sub")
        monkeypatch.setattr(
            physical_import,
            "_PHYSICAL_DESCENDANT_MODULES",
            _RootOnlyDescendants(),
        )
        foreign = ModuleType(module_name + ".sub")
        foreign.__path__ = [str(root_pkg / "sub")]
        monkeypatch.setitem(sys.modules, module_name + ".sub", foreign)
        sys.modules.pop(module_name + ".sub.leaf", None)
        leaf = importlib.import_module(module_name + ".sub.leaf")
        assert marker.exists(), (
            "probe: bez identity przodków podmiana MUSI uciekać (stan C40); "
            f"__file__={leaf.__file__!r}"
        )
    finally:
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)


def test_c41_ratchet_rejects_aliased_fast_path() -> None:
    """Candidate41 O4b (Sol F2): ratchet łapie fast-path przez aliasy.

    Oracle luki C40: Visitor śledził tylko bezpośrednie Name; alias
    `module_cache = sys.modules; registry_cache = _LOADED_PHYSICAL_SIBLINGS`
    przemycał fast-path required=False przez ratchet ZIELONY (a behawioralnie
    zwracał obcy obiekt). C41: ratchet rozpoznaje aliasy i zgłasza naruszenie.
    """
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "preloaded = _adopt_or_reject_preloaded(module_name, source)"
    assert anchor in real
    mutant = real.replace(
        anchor,
        "module_cache = sys.modules\n"
        "                    registry_cache = _LOADED_PHYSICAL_SIBLINGS\n"
        "                    existing = module_cache.get(module_name)\n"
        "                    recorded = registry_cache.get(module_name)\n"
        "                    if required is False and existing is not None and recorded is not None:\n"
        "                        return existing\n"
        "                    " + anchor,
        1,
    )
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any("sys.modules" in item for item in violations), violations
    assert any(
        "registry read outside owner" in item for item in violations
    ), violations


def test_c41_ratchet_still_clean_on_real_file() -> None:
    """Kontrapunkt O4b: alias-aware ratchet nie fałszuje żywego pliku."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []


# ─────────────────────────────────────────────────────────────────────────────
# Candidate42 — dwa niezależne obejścia ratcheta znalezione przez świeże blind
# na frozen C41: Sol (alias MODULE-LEVEL) + Opus (getattr(sys,'modules')).
# Ratchet to strukturalny lint wykrywający kształty fast-path (bezpośredni,
# aliasowany funkcyjnie/modułowo, przez getattr); NIE jest granicą na dowolną
# refleksję (globals()/eval) — tę pokrywa runtime single-owner + behawioralne
# oracle (F1/F2/F3 wyżej).
# ─────────────────────────────────────────────────────────────────────────────


def test_c42_ratchet_rejects_module_level_aliased_fast_path() -> None:
    """Candidate42 (Sol): alias MODULE-LEVEL użyty w loaderze = wykryty.

    Oracle luki C41: aliasy kluczowane per scope; `_MODCACHE = sys.modules`
    na poziomie modułu, użyty w `load_physical_scripts_sibling`, nie był
    rozwiązywany (lookup po scope funkcji mijał wpis "<module>") → ratchet
    ZIELONY. C42: fallback do scope globalnego łapie ten fast-path.
    """
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "preloaded = _adopt_or_reject_preloaded(module_name, source)"
    assert anchor in real
    mutant = real.replace(
        "_PHYSICAL_DESCENDANT_MODULES: dict[str, ModuleType] = {}",
        "_PHYSICAL_DESCENDANT_MODULES: dict[str, ModuleType] = {}\n"
        "_MODCACHE = sys.modules\n"
        "_MODREG = _LOADED_PHYSICAL_SIBLINGS",
        1,
    ).replace(
        anchor,
        "existing = _MODCACHE.get(module_name)\n"
        "                    recorded = _MODREG.get(module_name)\n"
        "                    if required is False and existing is not None and recorded is not None:\n"
        "                        return existing\n"
        "                    " + anchor,
        1,
    )
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any("sys.modules" in item for item in violations), violations
    assert any(
        "registry read outside owner" in item for item in violations
    ), violations


def test_c42_ratchet_rejects_getattr_sys_modules_fast_path() -> None:
    """Candidate42 (Opus): `getattr(sys, 'modules')` fast-path = wykryty.

    Oracle luki C41: `getattr(sys, 'modules')` to `ast.Call`, więc
    `_is_sys_modules_node` był False i `_name_kind` zwracał None → ratchet
    ZIELONY. C42: `_name_kind`/`classify` rozpoznają wywołanie getattr.
    Pokrywa oba warianty: przez alias i bezpośredni `.get`.
    """
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "preloaded = _adopt_or_reject_preloaded(module_name, source)"
    assert anchor in real

    via_alias = real.replace(
        anchor,
        "_mc = getattr(sys, 'modules')\n"
        "                    _hit = _mc.get(module_name)\n"
        "                    if required is False and _hit is not None:\n"
        "                        return _hit\n"
        "                    " + anchor,
        1,
    )
    assert via_alias != real
    v1 = _physical_import_ownership_violations(ast.parse(via_alias))
    assert any("sys.modules" in item for item in v1), v1

    direct = real.replace(
        anchor,
        "_hit = getattr(sys, 'modules').get(module_name)\n"
        "                    if required is False and _hit is not None:\n"
        "                        return _hit\n"
        "                    " + anchor,
        1,
    )
    assert direct != real
    v2 = _physical_import_ownership_violations(ast.parse(direct))
    assert any("sys.modules" in item for item in v2), v2


def test_c42_ratchet_rejects_getattr_registry_fast_path() -> None:
    """Candidate42: `getattr(<x>, '_LOADED_PHYSICAL_SIBLINGS')` = wykryty."""
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "preloaded = _adopt_or_reject_preloaded(module_name, source)"
    assert anchor in real
    import_line = "import dispatch_v2\n"
    assert import_line in real
    mutant = real.replace(
        anchor,
        "_reg = getattr(sys.modules[__name__], '_LOADED_PHYSICAL_SIBLINGS')\n"
        "                    recorded = _reg.get(module_name)\n"
        "                    if required is False and recorded is not None:\n"
        "                        return recorded[1]\n"
        "                    " + anchor,
        1,
    )
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "registry read outside owner" in item for item in violations
    ), violations


def test_c42_ratchet_still_clean_on_real_file() -> None:
    """Kontrapunkt C42: rozszerzony ratchet nie fałszuje żywego pliku."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []


# ─────────────────────────────────────────────────────────────────────────────
# Candidate43 — oba świeże blind na frozen C42 ZGODNIE CONFIRMED_DEFECT: ratchet
# zbierał aliasy tylko z Assign/AnnAssign i porównywał literalny Name 'sys', więc
# alias IMPORTOWY / krotkowy / walrus / łańcuch `_s=sys` omijał go. C43
# przeprojektowuje detekcję na „dowolne dowiązanie do sys/jego modules".
# ─────────────────────────────────────────────────────────────────────────────

# (prelude_wstawiony_po_"import dispatch_v2", wstawka_przed_ownerem)
_C43_RATCHET_BYPASS_SHAPES = {
    "from_import_alias": (
        "from sys import modules as _module_cache\n",
        "    existing = _module_cache.get(module_name)\n"
        "    if existing is not None and not required:\n"
        "        return existing\n",
    ),
    "import_sys_as": (
        "import sys as _s\n",
        "    _hit = _s.modules.get(module_name)\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    "tuple_unpack": (
        "",
        "    _MODCACHE, _UNUSED = sys.modules, None\n"
        "    _hit = _MODCACHE.get(module_name)\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    "walrus": (
        "",
        "    if (_mc := sys.modules).get(module_name) is not None"
        " and not required:\n"
        "        return _mc.get(module_name)\n",
    ),
    "chain_local_sys": (
        "",
        "    _s = sys\n"
        "    _hit = _s.modules.get(module_name)\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
}


@pytest.mark.parametrize("shape", sorted(_C43_RATCHET_BYPASS_SHAPES))
def test_c43_ratchet_rejects_import_and_binding_fast_paths(shape) -> None:
    """Candidate43 (Sol+Opus): każda naturalna forma dowiązania = wykryta.

    Oracle luki C42: aliasy zbierane tylko z Assign/AnnAssign, `sys.modules`
    rozpoznawane tylko jako literalny `Name('sys').modules`. Alias importowy
    (`from sys import modules as m`, `import sys as _s`), rozpakowanie krotki
    i walrus przechodziły ratchet ZIELONY (`violations == []`) mimo
    behawioralnego bypassu ownera. C43: `_ownership_kind_of` + fixpoint
    aliasów wykrywają wszystkie te formy.
    """
    prelude, insertion = _C43_RATCHET_BYPASS_SHAPES[shape]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "    parent_dir = _attested_package_parent()\n"
    assert anchor in real
    mutant = real
    if prelude:
        assert "import dispatch_v2\n" in mutant
        mutant = mutant.replace(
            "import dispatch_v2\n", "import dispatch_v2\n" + prelude, 1
        )
    mutant = mutant.replace(anchor, insertion + anchor, 1)
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any("sys.modules" in item for item in violations), (
        shape,
        violations,
    )


def test_c43_ratchet_rejects_chained_module_level_alias() -> None:
    """Candidate43: łańcuch aliasów module-level (`_a = sys.modules; _b = _a`)."""
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "    parent_dir = _attested_package_parent()\n"
    mutant = real.replace(
        "_PHYSICAL_DESCENDANT_MODULES: dict[str, ModuleType] = {}",
        "_PHYSICAL_DESCENDANT_MODULES: dict[str, ModuleType] = {}\n"
        "_A = sys.modules\n"
        "_B = _A",
        1,
    ).replace(
        anchor,
        "    _hit = _B.get(module_name)\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n" + anchor,
        1,
    )
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any("sys.modules" in item for item in violations), violations


def test_c43_ratchet_still_clean_on_real_file() -> None:
    """Kontrapunkt C43: przeprojektowany ratchet nie fałszuje żywego pliku."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []


# ─────────────────────────────────────────────────────────────────────────────
# Candidate44 — Opus na frozen C43 CONFIRMED_DEFECT: enumeracja form odczytu
# sys.modules była niedomknięta (`.items()/.values()/.setdefault`,
# `operator.getitem(sys.modules,name)`, iteracja). C44 przechodzi na SOUND
# detekcję (dowolny value-read poza owner/find_spec = naruszenie; whitelist
# tylko pętli cleanup) + behawioralny backstop wiążący KAŻDY kształt runtime.
# ─────────────────────────────────────────────────────────────────────────────

# (prelude_po_"import dispatch_v2", wstawka_po_"relative = Path(relative_path)")
_C44_SYS_MODULES_READ_SHAPES = {
    "items": (
        "",
        "    for _n, _m in sys.modules.items():\n"
        "        if _n == module_name and not required:\n"
        "            return _m\n",
    ),
    "values": (
        "",
        "    for _m in sys.modules.values():\n"
        "        if getattr(_m, '__name__', '') == module_name"
        " and not required:\n"
        "            return _m\n",
    ),
    "keys": (
        "",
        "    if module_name in sys.modules.keys() and not required:\n"
        "        return sys.modules[module_name]\n",
    ),
    "setdefault": (
        "",
        "    _hit = sys.modules.setdefault(module_name, None)\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    "operator_getitem": (
        "import operator\n",
        "    if not required and module_name in sys.modules:\n"
        "        return operator.getitem(sys.modules, module_name)\n",
    ),
    "bare_iteration": (
        "",
        "    for _k in sys.modules:\n"
        "        if _k == module_name and not required:\n"
        "            return sys.modules[_k]\n",
    ),
}


@pytest.mark.parametrize("shape", sorted(_C44_SYS_MODULES_READ_SHAPES))
def test_c44_ratchet_flags_any_sys_modules_value_read(shape) -> None:
    """Candidate44 (Opus C43): DOWOLNY value-read sys.modules = wykryty.

    SOUND zamiast enumeracji: `.items()/.values()/.keys()/.setdefault`,
    `operator.getitem(sys.modules, name)` i naga iteracja z subskryptem —
    wszystkie ewaluują sys.modules jako wartość, więc wszystkie flagowane.
    Formy te przechodziły ZIELONO w C43 (enumeracja `.get`+subscript).
    """
    prelude, insertion = _C44_SYS_MODULES_READ_SHAPES[shape]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    top = "    relative = Path(relative_path)\n"
    assert top in real
    mutant = real
    if prelude:
        mutant = mutant.replace(
            "import dispatch_v2\n", "import dispatch_v2\n" + prelude, 1
        )
    mutant = mutant.replace(top, insertion + top, 1)
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "sys.modules read outside owner" in i for i in violations
    ), (shape, violations)


def test_c44_ratchet_flags_default_arg_sys_modules_binding() -> None:
    """Candidate44 (Sol C43 #1): `def _peek(name, modules=sys.modules)` = wykryty.

    Domyślny argument wiąże `sys.modules` w helperze; C43 (helper-extraction po
    scope) tego nie łapał. Sound scanner: wartość domyślna to value-read
    sys.modules w scope helpera (poza owner/find_spec) → naruszenie.
    """
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "    parent_dir = _attested_package_parent()\n"
    assert "def load_physical_scripts_sibling(" in real
    mutant = real.replace(
        "def load_physical_scripts_sibling(",
        "def _peek(name, modules=sys.modules):\n"
        "    return modules.get(name)\n\n"
        "def load_physical_scripts_sibling(",
        1,
    ).replace(
        anchor,
        "    _h = _peek(module_name)\n"
        "    if _h is not None and not required:\n"
        "        return _h\n" + anchor,
        1,
    )
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "sys.modules read outside owner" in i for i in violations
    ), violations


def test_c44_ratchet_flags_flow_insensitive_rebind() -> None:
    """Candidate44 (Sol C43 #2): rebind nie chowa wcześniejszego dostępu.

    `_cache = sys.modules; read; _cache = sys` — analiza jest flow-insensitive,
    więc późniejszy rebind do `sys` nie może ukryć wcześniejszego value-readu
    (łapanego w miejscu wiązania), a priorytet aliasów utrzymuje klasyfikację
    `sys.modules` mimo rebindu (wariant modułowy z importem bez węzła-odczytu).
    """
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    top = "    relative = Path(relative_path)\n"
    assert top in real
    # (a) lokalny rebind: read w miejscu `_cache = sys.modules`
    m_local = real.replace(
        top,
        "    _cache = sys.modules\n"
        "    existing = _cache.get(module_name)\n"
        "    _cache = sys\n"
        "    if existing is not None and not required:\n"
        "        return existing\n" + top,
        1,
    )
    assert m_local != real
    v_local = _physical_import_ownership_violations(ast.parse(m_local))
    assert any("sys.modules read outside owner" in i for i in v_local), v_local
    # (b) modułowy import-alias + rebind do `sys` (bez węzła-odczytu) —
    # priorytet aliasów utrzymuje `sys.modules`.
    m_mod = real.replace(
        "import dispatch_v2\n",
        "import dispatch_v2\nfrom sys import modules as _mc\n_mc = sys\n",
        1,
    ).replace(
        top,
        "    _h = _mc.get(module_name)\n"
        "    if _h is not None and not required:\n"
        "        return _h\n" + top,
        1,
    )
    assert m_mod != real
    v_mod = _physical_import_ownership_violations(ast.parse(m_mod))
    assert any("sys.modules read outside owner" in i for i in v_mod), v_mod


def test_c44_ratchet_permits_cleanup_loop_but_not_return_variant() -> None:
    """Candidate44: pętla cleanup (pop-only) dozwolona; wariant z return — nie.

    Whitelist cleanup jest wąska: iteracja sys.modules z body wyłącznie
    popującym przechodzi (żywy loader), ale ta sama iteracja z `return`
    w środku (fast-path „iteruj i zwróć") jest naruszeniem.
    """
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    # Żywy plik zawiera pętlę cleanup i jest CLEAN:
    assert _physical_import_ownership_violations(ast.parse(real)) == []
    # Wariant z return w pętli iterującej sys.modules poza cleanupem = RED:
    top = "    relative = Path(relative_path)\n"
    mutant = real.replace(
        top,
        "    for _k in tuple(sys.modules):\n"
        "        if _k == module_name and not required:\n"
        "            return sys.modules[_k]\n" + top,
        1,
    )
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "sys.modules read outside owner" in i for i in violations
    ), violations


def test_c44_ratchet_still_clean_on_real_file() -> None:
    """Kontrapunkt C44: sound scanner nie fałszuje żywego pliku (cleanup OK)."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []


_C45_RETURN_HOLE_SHAPES = {
    "nested_def_then_return": (
        "    for _n, _m in tuple(sys.modules.items()):\n"
        "        if _n == module_name and not required:\n"
        "            def _z():\n"
        "                return None\n"
        "            return _m\n"
    ),
    "lambda_then_return": (
        "    for _n, _m in sys.modules.items():\n"
        "        if _n == module_name and package:\n"
        "            _f = lambda: None\n"
        "            return _m\n"
    ),
    "return_in_orelse_after_def": (
        "    for _n, _m in sys.modules.items():\n"
        "        if required:\n"
        "            def _q():\n"
        "                return 1\n"
        "        else:\n"
        "            return _m\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_C45_RETURN_HOLE_SHAPES))
def test_c45_cleanup_whitelist_not_fooled_by_nested_scope_return(shape) -> None:
    """Candidate45 (Opus C44): return po zagnieżdżonej funkcji ≠ pętla cleanup.

    Oracle luki C44: `_contains_return_or_yield` (BFS `ast.walk` + `break` na
    pierwszej zagnieżdżonej funkcji) mijał `return` wyemitowany w kolejce po
    nested `def`/`lambda` w tym samym statemencie — więc pętla „iteruj po
    sys.modules i zwróć preloadowany moduł" była błędnie whitelistowana jako
    cleanup (`violations == []`). C45: rekurencja DFS pomija tylko ciała
    zagnieżdżonych funkcji, więc każdy `return` w ciele pętli dyskwalifikuje
    whitelist → fast-path flagowany.
    """
    insertion = _C45_RETURN_HOLE_SHAPES[shape]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    top = "    relative = Path(relative_path)\n"
    assert top in real
    mutant = real.replace(top, insertion + top, 1)
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "sys.modules read outside owner" in i for i in violations
    ), (shape, violations)


def test_c45_ratchet_still_clean_on_real_file() -> None:
    """Kontrapunkt C45: naprawiony detektor return/yield nie fałszuje żywego pliku."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []


_C45_POP_RETURN_SHAPES = {
    "pop_reinsert_conditional_return": (
        "    _foreign = sys.modules.pop(module_name, None)\n"
        "    if _foreign is not None:\n"
        "        if not required:\n"
        "            return _foreign\n"
        "        sys.modules[module_name] = _foreign\n"
    ),
    "pop_direct_return": (
        "    if not required and module_name in sys.modules:\n"
        "        return sys.modules.pop(module_name)\n"
    ),
    "popitem_value_used": (
        "    _k, _v = sys.modules.popitem()\n"
        "    sys.modules[_k] = _v\n"
        "    if _k == module_name and not required:\n"
        "        return _v\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_C45_POP_RETURN_SHAPES))
def test_c45_ratchet_flags_pop_return_fast_path(shape) -> None:
    """Candidate45 (Sol C44): `sys.modules.pop(name)` z UŻYTĄ wartością = wykryty.

    pop/popitem zwracają zdjęty moduł; C44 traktował je jak czystą mutację
    (jak w cleanupie), więc `x = sys.modules.pop(name); ...; return x`
    (także wariant „zdejmij i wstaw z powrotem, zwróć dla src") omijał ratchet.
    C45: pop/popitem to odczyt, gdy wynik jest użyty; bare-statement pop
    (cleanup) pozostaje dozwolone — żywy plik CLEAN.
    """
    insertion = _C45_POP_RETURN_SHAPES[shape]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    top = "    relative = Path(relative_path)\n"
    assert top in real
    mutant = real.replace(top, insertion + top, 1)
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "sys.modules read outside owner" in i for i in violations
    ), (shape, violations)


def test_c45_cleanup_bare_pop_statement_still_permitted() -> None:
    """Kontrapunkt: `sys.modules.pop(name, None)` jako STATEMENT (cleanup) OK."""
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    # Żywy plik ma dokładnie ten idiom w bloku except i jest CLEAN:
    assert _physical_import_ownership_violations(ast.parse(real)) == []


# Realne nazwy konsumentów + generyczne, moduł i pakiet. Nazwy MAJĄ znaczenie:
# Candidate45 (Sol C44) — fast-path warunkowy na `module_name == "src"` omijał
# backstop z losową nazwą; backstop MUSI ćwiczyć nazwy, na których fast-path
# faktycznie by wystrzelił (jedyni realni konsumenci to `src` i
# `schedule_utils`).
_C45_BACKSTOP_TARGETS = (
    # (module_name, relative_path, package)
    ("src", "ml_data_prep/src", True),
    ("schedule_utils", "schedule_utils.py", False),
    ("_c45_backstop_pkg", "legacy_pkg", True),
    ("_c45_backstop_mod", "legacy_sibling.py", False),
)
# Candidate46 (nadzór): liczby w SCOPE MUSZĄ być liczone maszynowo, nie prozą.
# Backstop = required(2) × 4 targety = 8 przypadków. `package` NIE jest
# niezależną osią skrzyżowaną z nazwami — jest ustalony per-target; 4 targety
# pokrywają package∈{True,False} × {realny konsument (src/schedule_utils),
# generyczny}. Ta stała jest pinowana meta-testem niżej i cytowana w SCOPE.
_C45_BACKSTOP_REQUIRED_VALUES = (True, False)
_C45_BACKSTOP_CASE_COUNT = (
    len(_C45_BACKSTOP_TARGETS) * len(_C45_BACKSTOP_REQUIRED_VALUES)
)


def test_c46_backstop_case_count_matches_declared() -> None:
    """Candidate46 (nadzór C45): faktyczny collect backstopu = deklarowana liczba.

    C45 SCOPE zawyżył pokrycie jako „required × package × 4 nazwy = 16", a
    faktyczny collect to 8 (required × 4 targety; package ustalony per-target).
    Ten meta-test pinuje liczbę maszynowo — deklaracja w SCOPE cytuje
    `_C45_BACKSTOP_CASE_COUNT`, nie liczbę „na oko". Pokrycie osi:
    package∈{True,False} i required∈{True,False} są obecne w zbiorze targetów.
    """
    assert _C45_BACKSTOP_CASE_COUNT == 8
    packages = {t[2] for t in _C45_BACKSTOP_TARGETS}
    assert packages == {True, False}, packages
    real_names = {t[0] for t in _C45_BACKSTOP_TARGETS}
    assert {"src", "schedule_utils"} <= real_names, real_names
    # dokładnie po jednym realnym konsumencie dla pakietu i modułu:
    assert ("src", "ml_data_prep/src", True) in _C45_BACKSTOP_TARGETS
    assert ("schedule_utils", "schedule_utils.py", False) in (
        _C45_BACKSTOP_TARGETS
    )


# ─────────────────────────────────────────────────────────────────────────────
# Candidate46 — oba świeże blind na frozen C45 CONFIRMED_DEFECT + 2 nadzory:
# Opus: whitelist cleanup akceptował `.items()/.values()` (capture-then-return);
# Sol: podmiana ZWYKŁEGO katalogu potomka (O_NOFOLLOW blokuje tylko symlink);
# nadzór: TOCTOU find_spec→exec_module i read-bytes→capture-identity przy root
# load. Fixy: iter cleanup tylko po kluczach; otwarcie potomka zakotwiczone w
# przypiętej tożsamości inode (ten sam fd), identity roota z fd czytającego
# __init__.py.
# ─────────────────────────────────────────────────────────────────────────────

_C46_CLEANUP_VALUE_ITER_SHAPES = {
    "items_capture_return_after": (
        "    _hit = None\n"
        "    for _n, _m in sys.modules.items():\n"
        "        if _n == module_name:\n"
        "            _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n"
    ),
    "values_capture": (
        "    _hit = None\n"
        "    for _m in sys.modules.values():\n"
        "        _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n"
    ),
    "keys_iter_then_return_subscript": (
        "    for _k in sys.modules.keys():\n"
        "        if _k == module_name and not required:\n"
        "            return sys.modules[_k]\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_C46_CLEANUP_VALUE_ITER_SHAPES))
def test_c46_cleanup_whitelist_requires_key_iteration(shape) -> None:
    """Candidate46 (Opus C45): whitelist cleanup tylko dla iteracji po kluczach.

    Luka C45: `_is_cleanup_sys_modules_loop` akceptował KAŻDĄ pętlę, której
    iter czyta sys.modules i której body nie ma return/yield ani value-read —
    w tym `for _n,_m in sys.modules.items()`, gdzie loop-var `_m` to OBIEKT
    modułu przechwytywany do `_hit` i zwracany PO pętli (`violations == []`).
    C46: iter cleanup musi iterować po KLUCZACH (bare/`keys()`/`tuple(...)`);
    `.items()`/`.values()`/subscript w iter → nie cleanup → flagowane.
    """
    insertion = _C46_CLEANUP_VALUE_ITER_SHAPES[shape]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    top = "    relative = Path(relative_path)\n"
    assert top in real
    mutant = real.replace(top, insertion + top, 1)
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "sys.modules read outside owner" in i for i in violations
    ), (shape, violations)


_C47_SHADOWED_WRAPPER_SHAPES = {
    "def_tuple_returns_values": (
        "def tuple(mapping):\n    return mapping.values()\n",
        "    _hit = None\n"
        "    for _m in tuple(sys.modules):\n"
        "        if getattr(_m, '__name__', '') == module_name:\n"
        "            _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    "def_list_returns_values": (
        "def list(m):\n    return m.values()\n",
        "    _hit = None\n"
        "    for _m in list(sys.modules):\n"
        "        _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    "import_as_sorted": (
        "from builtins import list as sorted\n",
        "    _hit = None\n"
        "    for _m in sorted(sys.modules):\n"
        "        _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    # Candidate48 (Sol C47): przesłonięcie przez PARAMETR LAMBDY.
    "lambda_param_tuple": (
        "",
        "    _hit = None\n"
        "    for _m in (lambda tuple: tuple(sys.modules))"
        "(lambda mapping: mapping.values()):\n"
        "        _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    # Candidate49 (Sol C48): przesłonięcie przez wzorzec MATCH (`case tuple:`
    # to capture-pattern wiążący nazwę `tuple`).
    "match_as_case_tuple": (
        "",
        "    _hit = None\n"
        "    match (lambda mapping: mapping.values()):\n"
        "        case tuple:\n"
        "            for _m in tuple(sys.modules):\n"
        "                _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    # Candidate49 (Sol C48): wildcard import może wprowadzić dowolny wrapper —
    # konserwatywnie wszystkie zaufane wrappery uznane za przesłonięte.
    "wildcard_import_shadow": (
        "from os.path import *\n",
        "    _hit = None\n"
        "    for _m in tuple(sys.modules):\n"
        "        _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
    # Candidate52 (Sol C51): `except ... as tuple` wiąże `tuple`.
    "except_as_tuple": (
        "",
        "    _hit = None\n"
        "    try:\n"
        "        raise ValueError()\n"
        "    except ValueError as tuple:\n"
        "        for _m in tuple(sys.modules):\n"
        "            _hit = _m\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n",
    ),
}


@pytest.mark.parametrize("shape", sorted(_C47_SHADOWED_WRAPPER_SHAPES))
def test_c47_cleanup_rejects_shadowed_iter_wrapper(shape) -> None:
    """Candidate47 (Sol C46): przesłonięty `tuple`/`list`/... nie jest zaufany.

    Luka C46: `_iter_exposes_module_values` ufał NAZWIE wrappera (`tuple`) jako
    snapshotowi kluczy. `def tuple(m): return m.values()` (lub import-as)
    zamieniał iterację po kluczach w iterację po OBIEKTACH modułów, które body
    przechwytuje i zwraca — a ratchet dawał `[]`. C47: wrapper zaufany TYLKO
    gdy NIE jest przesłonięty w pliku; przesłonięty → nie cleanup → flagowany.
    """
    prelude, insertion = _C47_SHADOWED_WRAPPER_SHAPES[shape]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    top = "    relative = Path(relative_path)\n"
    assert top in real
    mutant = real.replace(
        "import dispatch_v2\n", "import dispatch_v2\n" + prelude, 1
    ).replace(top, insertion + top, 1)
    assert mutant != real
    violations = _physical_import_ownership_violations(ast.parse(mutant))
    assert any(
        "sys.modules read outside owner" in i for i in violations
    ), (shape, violations)


def test_c46_cleanup_key_iteration_still_permitted() -> None:
    """Kontrapunkt (AST): żywy plik ma dozwoloną pętlę cleanup i jest CLEAN.

    Sprawdzane strukturalnie, nie literałem stringa (kanoniczny purge używa
    `for entry in tuple(sys.modules)`): istnieje ≥1 `For`, którego iter czyta
    sys.modules i który `_is_cleanup_sys_modules_loop` uznaje za dozwolony
    (key-iteration, pop-only, bez return) — a cały plik przechodzi ratchet.
    """
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    aliases = _collect_ownership_aliases(tree)
    parents, scope_of = _scope_and_parent_maps(tree)
    shadowed = _shadowed_wrapper_names(tree)
    cleanup_loops = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.AsyncFor))
        and _is_cleanup_sys_modules_loop(
            node, aliases, parents, scope_of, shadowed
        )
    ]
    assert cleanup_loops, "żywy plik nie ma dozwolonej pętli cleanup sys.modules"
    assert _physical_import_ownership_violations(tree) == []


def _c46_load_pkg_with_child(tmp_path, monkeypatch, *, deep=False):
    """Załaduj pakiet potomny; zwróć (physical_import, root, scripts, legacy)."""
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "child.py").write_text("SAFE = 'attested'\n", encoding="utf-8")
    if deep:
        sub = legacy / "sub"
        sub.mkdir()
        (sub / "__init__.py").write_text("", encoding="utf-8")
        (sub / "leaf.py").write_text("LEAF = 'attested'\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    root = "_c46_root"
    physical_import.load_physical_scripts_sibling(
        root, "legacy_pkg", package=True
    )
    return physical_import, root, scripts, legacy


def _c46_replace_root_with_attacker(scripts, legacy, marker):
    """Rename+rmtree oryginału i podstaw katalog atakującego pod tą nazwą.

    rmtree oryginału wymusza rozróżnienie: utrzymany deskryptor wskazuje
    usunięty katalog → import pada (fail-closed), obce bajty NIGDY nie
    wykonane; regresja do rozwiązywania po nazwie wykonałaby child atakującego
    (marker). Zwraca ścieżkę nowego (obcego) katalogu.
    """
    import shutil
    os.rename(legacy, scripts / "moved")
    shutil.rmtree(scripts / "moved")
    newdir = scripts / "legacy_pkg"
    newdir.mkdir()
    (newdir / "__init__.py").write_text("", encoding="utf-8")
    (newdir / "child.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('X')\n"
        "ESCAPED = 1\n",
        encoding="utf-8",
    )
    return newdir


def test_c46_descendant_plain_dir_swap_is_blocked(tmp_path, monkeypatch) -> None:
    """Candidate46 (Sol C45): podmiana ZWYKŁEGO katalogu potomka nie wycieka.

    O_NOFOLLOW odrzuca tylko symlink; podstawienie zwykłego katalogu pod tą
    samą nazwą po załadowaniu pakietu wykonywało obce bajty potomka. Numer
    inode bywa RECYKLOWANY, więc porównanie (st_dev, st_ino) nie wystarcza —
    C46 UTRZYMUJE otwarty deskryptor atestowanego katalogu i otwiera potomki
    względem niego. Podmiana ścieżki jest nieistotna: obce bajty NIGDY się nie
    wykonują (marker pusty); tu oryginał jest usunięty → import fail-closed.
    """
    import importlib
    physical_import, root, scripts, legacy = _c46_load_pkg_with_child(
        tmp_path, monkeypatch
    )
    try:
        assert importlib.import_module(root + ".child").SAFE == "attested"
        marker = tmp_path / "ESCAPED.marker"
        _c46_replace_root_with_attacker(scripts, legacy, marker)
        sys.modules.pop(root + ".child", None)
        importlib.invalidate_caches()
        with pytest.raises((RuntimeError, ModuleNotFoundError)):
            importlib.import_module(root + ".child")
        assert not marker.exists(), "obce bajty potomka wykonane po podmianie"
    finally:
        for name in tuple(sys.modules):
            if name == root or name.startswith(root + "."):
                sys.modules.pop(name, None)


def test_c46_descendant_swap_between_find_spec_and_exec_blocked(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate46 (nadzór): find_spec→exec_module nie wycieka.

    exec_module otwiera bajty względem UTRZYMANEGO deskryptora, nie po nazwie,
    więc podmiana katalogu POMIĘDZY find_spec a exec_module nie przekierowuje
    otwarcia: obce bajty się nie wykonują (marker pusty); oryginał usunięty →
    exec pada fail-closed.
    """
    import importlib
    import importlib.util
    physical_import, root, scripts, legacy = _c46_load_pkg_with_child(
        tmp_path, monkeypatch
    )
    try:
        assert importlib.import_module(root + ".child").SAFE == "attested"
        sys.modules.pop(root + ".child", None)
        spec = physical_import._DESCENDANT_FINDER.find_spec(root + ".child")
        assert spec is not None
        marker = tmp_path / "TOCTOU.marker"
        _c46_replace_root_with_attacker(scripts, legacy, marker)
        module = importlib.util.module_from_spec(spec)
        with pytest.raises((RuntimeError, ModuleNotFoundError, OSError)):
            spec.loader.exec_module(module)
        assert not marker.exists(), "TOCTOU: obce bajty wykonane w exec_module"
    finally:
        for name in tuple(sys.modules):
            if name == root or name.startswith(root + "."):
                sys.modules.pop(name, None)


def test_c46_root_descriptor_retained_across_read_gap_swap(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate46 (nadzór): mutation-sensitive — deskryptor z fd czytającego init.

    Wrapper `_read_all_from_fd` PO odczycie __init__.py podmienia root
    (rename+rmtree oryginału, nowy katalog atakującego pod tą nazwą). Ponieważ
    utrzymany deskryptor pochodzi z fd, który otworzył __init__.py (przed
    odczytem), wskazuje ORYGINAŁ (teraz usunięty) — import potomka pada, obce
    bajty się nie wykonują. Regresja do świeżego walka/po-nazwie po odczycie
    związałaby trasę z katalogiem atakującego → child wykonany + marker.
    """
    import importlib
    import shutil
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "child.py").write_text("SAFE = 'attested'\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )

    marker = tmp_path / "GAP_ESCAPED.marker"
    orig_read = physical_import._read_all_from_fd
    state = {"swapped": False}

    def read_then_swap(fd):
        data = orig_read(fd)
        if not state["swapped"]:
            state["swapped"] = True
            os.rename(legacy, scripts / "moved")
            shutil.rmtree(scripts / "moved")
            newdir = scripts / "legacy_pkg"
            newdir.mkdir()
            (newdir / "__init__.py").write_text("", encoding="utf-8")
            (newdir / "child.py").write_text(
                "import pathlib\n"
                f"pathlib.Path({str(marker)!r}).write_text('X')\n"
                "ESCAPED = 1\n",
                encoding="utf-8",
            )
        return data

    monkeypatch.setattr(physical_import, "_read_all_from_fd", read_then_swap)
    root = "_c46_gap_root"
    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        route = physical_import._PHYSICAL_PACKAGE_ROUTES[root]
        assert len(route) == 4, route
        assert isinstance(route[3], physical_import._RetainedDirFd)
        sys.modules.pop(root + ".child", None)
        importlib.invalidate_caches()
        with pytest.raises((RuntimeError, ModuleNotFoundError)):
            importlib.import_module(root + ".child")
        assert not marker.exists(), "obce bajty potomka wykonane po swapie"
    finally:
        for name in tuple(sys.modules):
            if name == root or name.startswith(root + "."):
                sys.modules.pop(name, None)


_C46_RETIREMENT_CONTRACT_MUTANTS = {
    "second_route_pop_helper": (
        "def _adopt_or_reject_preloaded(",
        "def _rogue_retire(n):\n"
        "    _PHYSICAL_PACKAGE_ROUTES.pop(n, None)\n\n"
        "def _adopt_or_reject_preloaded(",
        "route mutation must be exactly one",
    ),
    "second_retire_call": (
        "        return module\n"
        "    finally:",
        "        _retire_package_route('x')\n"
        "        return module\n"
        "    finally:",
        "must be called exactly twice from loader",
    ),
    # Kotwica zaktualizowana (Candidate59): `_retire_package_route` owinięty w
    # try/finally (tombstone C58 #3) + `close` jest teraz WEWNĄTRZ `if route is
    # not None:` → wcięcie 12/16 spacji zamiast 8/12. Semantyka mutacji BEZ ZMIAN
    # (brak close / rogue close na innej nazwie), oba nadal FLAGGED.
    "no_close_in_retire": (
        "            if isinstance(handle, _RetainedDirFd):\n"
        "                handle.close()",
        "            if isinstance(handle, _RetainedDirFd):\n"
        "                pass",
        "must close exactly one handle",
    ),
    "rogue_close_not_handle": (
        "            if isinstance(handle, _RetainedDirFd):\n"
        "                handle.close()",
        "            if isinstance(handle, _RetainedDirFd):\n"
        "                rogue = handle\n"
        "                rogue.close()",
        "must close exactly one handle",
    ),
    "direct_pop_in_loader": (
        "                            _retire_package_route(module_name)",
        "                            _PHYSICAL_PACKAGE_ROUTES.pop(module_name, None)",
        "route mutation must be exactly one",
    ),
    # Descendant registry / purge policy (Candidate46/nadzór):
    "desc_pop_outside_purge_alias": (
        "                _dd = _purge_owned_module_subtree(self._fullname)",
        "                _d = _PHYSICAL_DESCENDANT_MODULES\n"
        "                _d.pop(self._fullname, None)",
        "descendant registry mutator must be exactly one",
    ),
    "desc_pop_walrus": (
        "                _dd = _purge_owned_module_subtree(self._fullname)",
        "                (_d := _PHYSICAL_DESCENDANT_MODULES).pop(self._fullname, None)",
        "descendant registry mutator must be exactly one",
    ),
    "desc_write_outside_exec": (
        "def _adopt_or_reject_preloaded(",
        "def _rogue_write(n, m):\n"
        "    _PHYSICAL_DESCENDANT_MODULES[n] = m\n\n"
        "def _adopt_or_reject_preloaded(",
        "descendant registry write must be exactly one",
    ),
    "second_sysmodules_pop_in_purge": (
        "    for entry in tuple(_PHYSICAL_DESCENDANT_MODULES):",
        "    sys.modules.pop('rogue', None)\n"
        "    for entry in tuple(_PHYSICAL_DESCENDANT_MODULES):",
        "sys.modules pop must be exactly one",
    ),
    "remove_purge_from_retire": (
        "    doomed = _purge_owned_module_subtree(module_name)\n"
        "    route = _PHYSICAL_PACKAGE_ROUTES.pop(module_name, None)",
        "    route = _PHYSICAL_PACKAGE_ROUTES.pop(module_name, None)",
        "call-sites must be exactly",
    ),
    "double_purge_in_loader": (
        "                        else:\n"
        "                            _deferred_derefs.extend(\n"
        "                                _purge_owned_module_subtree(module_name)\n"
        "                            )",
        "                        else:\n"
        "                            _purge_owned_module_subtree(module_name)\n"
        "                            _purge_owned_module_subtree(module_name)",
        "call-sites must be exactly",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C46_RETIREMENT_CONTRACT_MUTANTS))
def test_c46_retirement_contract_ratchet(mutant) -> None:
    """Candidate46 (nadzór): kontrakt retirement trasy pinowany strukturalnie.

    Dokładnie jedna mutacja trasy (pop) w `_retire_package_route`, dokładnie
    jedno wywołanie helpera z rollbacku loadera, dokładnie jeden close handle
    w helperze; zakaz bezpośrednich popów tras i alternatywnych helperów.
    Każdy mutant (drugi pop / drugie wywołanie / brak close / bezpośredni pop
    w loaderze) MUSI być flagowany; żywy plik CLEAN.
    """
    anchor, replacement, expected = _C46_RETIREMENT_CONTRACT_MUTANTS[mutant]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    assert anchor in real, mutant
    mutated = real.replace(anchor, replacement, 1)
    assert mutated != real
    violations = _physical_import_ownership_violations(ast.parse(mutated))
    assert any(expected in v for v in violations), (mutant, violations)


def test_c46_retirement_contract_clean_on_real_file() -> None:
    """Kontrapunkt: żywy plik spełnia kontrakt retirement (brak naruszeń)."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []


def test_c46_retained_dir_fd_no_leak_across_lifecycle(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate46 (nadzór lifecycle): brak wycieku fd — owned handle + retire.

    Liczba deskryptorów (/proc/self/fd) nie rośnie przy: powtarzanym
    success+route reset, ścieżce non-package, required=False bez źródła oraz
    reuse (preload). Każde niezachowane otwarcie zamyka fd; owned handle
    zamyka retained fd przy reset trasy / retirement.
    """
    import importlib
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    fd_dir = "/proc/self/fd"
    if not os.path.isdir(fd_dir):
        pytest.skip("brak /proc/self/fd")

    def fdcount():
        return len(os.listdir(fd_dir))

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "child.py").write_text("SAFE = 1\n", encoding="utf-8")
    (scripts / "legacy_mod.py").write_text("MOD = 1\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    try:
        base = fdcount()
        # 20× success pakietu + kanoniczny reset trasy (retire zamyka handle):
        for i in range(20):
            name = f"_lc_pkg_{i}"
            physical_import.load_physical_scripts_sibling(
                name, "legacy_pkg", package=True
            )
            physical_import._retire_package_route(name)
            physical_import._LOADED_PHYSICAL_SIBLINGS.pop(name, None)
        assert fdcount() == base, ("success+retire", fdcount(), base)
        # 20× non-package (nie utrzymuje fd):
        for i in range(20):
            physical_import.load_physical_scripts_sibling(
                f"_lc_mod_{i}", "legacy_mod.py", required=False
            )
        assert fdcount() == base, ("non-package", fdcount(), base)
        # 20× required=False bez źródła:
        for i in range(20):
            physical_import.load_physical_scripts_sibling(
                f"_lc_absent_{i}", "absent.py", required=False
            )
        assert fdcount() == base, ("absent", fdcount(), base)
        # 20× preload/reuse tego samego pakietu (early-return, bez nowego fd):
        physical_import.load_physical_scripts_sibling(
            "_lc_reuse", "legacy_pkg", package=True
        )
        after_first = fdcount()  # +1 żywy route
        for _ in range(20):
            physical_import.load_physical_scripts_sibling(
                "_lc_reuse", "legacy_pkg", package=True
            )
        assert fdcount() == after_first, ("preload", fdcount(), after_first)
        physical_import._retire_package_route("_lc_reuse")
        assert fdcount() == base, ("after retire reuse", fdcount(), base)

        # (a) FAILURE `_ensure_descendant_finder_installed` PO transferze fd do
        # handle: transakcja MUSI wycofać sys.modules + trasę + zamknąć fd.
        orig_ensure = physical_import._ensure_descendant_finder_installed

        def _boom_ensure():
            raise RuntimeError("ensure boom")

        monkeypatch.setattr(
            physical_import,
            "_ensure_descendant_finder_installed",
            _boom_ensure,
        )
        for i in range(20):
            with pytest.raises(RuntimeError, match="ensure boom"):
                physical_import.load_physical_scripts_sibling(
                    f"_lc_ens_{i}", "legacy_pkg", package=True
                )
            assert f"_lc_ens_{i}" not in physical_import._PHYSICAL_PACKAGE_ROUTES
            assert f"_lc_ens_{i}" not in sys.modules
        assert fdcount() == base, ("ensure-fail", fdcount(), base)
        monkeypatch.setattr(
            physical_import,
            "_ensure_descendant_finder_installed",
            orig_ensure,
        )

        # (a') Candidate48 (Sol C47 #1): wyjątek KONSTRUKTORA `_RetainedDirFd`
        # PO otwarciu directory fd. Transfer własności deskryptora musi być
        # atomowy — surowy fd nie może zostać osierocony (fd_delta == 0), a
        # trasa/sys.modules nie mogą powstać.
        orig_retained = physical_import._RetainedDirFd

        class _BoomRetainedDirFd:
            def __init__(self, fd):
                raise RuntimeError("retained ctor boom")

        monkeypatch.setattr(
            physical_import, "_RetainedDirFd", _BoomRetainedDirFd
        )
        for i in range(20):
            with pytest.raises(RuntimeError, match="retained ctor boom"):
                physical_import.load_physical_scripts_sibling(
                    f"_lc_ctor_{i}", "legacy_pkg", package=True
                )
            assert f"_lc_ctor_{i}" not in physical_import._PHYSICAL_PACKAGE_ROUTES
            assert f"_lc_ctor_{i}" not in sys.modules
        assert fdcount() == base, ("ctor-raise", fdcount(), base)
        monkeypatch.setattr(
            physical_import, "_RetainedDirFd", orig_retained
        )

        # (b) FAILURE exec __init__.py pakietu, który NAJPIERW importuje dziecko
        # a POTEM pada: pełny rollback — brak wpisów root/child w sys.modules
        # ORAZ w descendant registry, brak wycieku fd.
        bad = scripts / "bad_pkg"
        bad.mkdir()
        (bad / "__init__.py").write_text(
            "from . import child\nraise RuntimeError('init boom')\n",
            encoding="utf-8",
        )
        (bad / "child.py").write_text("SAFE = 1\n", encoding="utf-8")
        for i in range(20):
            root = f"_lc_boom_{i}"
            with pytest.raises(RuntimeError, match="init boom"):
                physical_import.load_physical_scripts_sibling(
                    root, "bad_pkg", package=True
                )
            assert root not in physical_import._PHYSICAL_PACKAGE_ROUTES
            assert not [n for n in sys.modules if n == root or n.startswith(root + ".")]
            assert not [
                n for n in physical_import._PHYSICAL_DESCENDANT_MODULES
                if n == root or n.startswith(root + ".")
            ], "descendant registry leak po exec-fail"
        assert fdcount() == base, ("exec-fail", fdcount(), base)

        # (c) FAILURE exec POTOMKA (root OK, potem `root.badsub` importuje wnuka
        # i pada): poddrzewo potomka w pełni wycofane (sys.modules + registry),
        # root pozostaje używalny.
        okroot = scripts / "ok_pkg"
        okroot.mkdir()
        (okroot / "__init__.py").write_text("", encoding="utf-8")
        (okroot / "child.py").write_text("SAFE = 1\n", encoding="utf-8")
        badsub = okroot / "badsub"
        badsub.mkdir()
        (badsub / "__init__.py").write_text(
            "from . import leaf\nraise RuntimeError('badsub boom')\n",
            encoding="utf-8",
        )
        (badsub / "leaf.py").write_text("LEAF = 1\n", encoding="utf-8")
        physical_import.load_physical_scripts_sibling(
            "_lc_okroot", "ok_pkg", package=True
        )
        importlib.import_module("_lc_okroot.child")
        with pytest.raises(RuntimeError, match="badsub boom"):
            importlib.import_module("_lc_okroot.badsub")
        assert not [
            n for n in sys.modules if n.startswith("_lc_okroot.badsub")
        ], "sys.modules leak poddrzewa potomka"
        assert not [
            n for n in physical_import._PHYSICAL_DESCENDANT_MODULES
            if n.startswith("_lc_okroot.badsub")
        ], "descendant registry leak poddrzewa potomka"
        assert importlib.import_module("_lc_okroot.child").SAFE == 1
        physical_import._retire_package_route("_lc_okroot")
    finally:
        for name in tuple(sys.modules):
            if "_lc_" in name:
                sys.modules.pop(name, None)


@pytest.mark.parametrize("module_name,relative,package", _C45_BACKSTOP_TARGETS)
@pytest.mark.parametrize("required", _C45_BACKSTOP_REQUIRED_VALUES)
def test_c45_behavioral_backstop_foreign_preload_source_present_conflicts(
    tmp_path,
    monkeypatch,
    module_name,
    relative,
    package,
    required,
) -> None:
    """Candidate44/45: behawioralny backstop — GWARANCJA łapiąca KAŻDY mechanizm.

    Przy fizycznie OBECNYM źródle i OBCYM module preloadowanym pod tą nazwą,
    jedyny owner MUSI zgłosić konflikt, a bajty obcego obiektu nigdy się nie
    wykonują. To runtime-oracle, którego żaden fast-path nie przejdzie —
    niezależnie od MECHANIZMU (odczyt sys.modules dowolną składnią, pop-return,
    `importlib.import_module`, refleksja). Macierz: required∈{True,False} ×
    package∈{True,False} × nazwy realnych konsumentów (`src`,`schedule_utils`)
    i generyczne — więc także fast-path warunkowy na konkretnej nazwie jest
    złapany (Sol C44). Ten backstop jest STATED GUARANTEE promocji; strukturalny
    lint (AST) to defense-in-depth dla naturalnych refaktorów.

    Uruchamiane w SUBPROCESIE (Candidate46) WYŁĄCZNIE jako higiena/izolacja:
    ćwiczenie realnych nazw `src`/`schedule_utils` z obcym preloadem i tak nie
    dotyka globalnego `sys.modules` procesu testów. (To NIE jest fix żadnego
    znanego faila — 1-minutowy flake `is_on_shift` przy end=23:59 to osobna,
    czasowa sprawa; wersja in-process w C45 przechodziła. Parytet asercji:
    ten sam kontrakt — konflikt ownera + brak wykonania obcych bajtów +
    brak wpisu w rejestrze — dla required×package×nazw.)
    """
    scripts = tmp_path / f"scripts_{module_name}_{required}"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    target = scripts / relative
    if package:
        target.mkdir(parents=True)
        (target / "__init__.py").write_text(
            "DISK_VALUE = 'from-attested-descriptor'\n", encoding="utf-8"
        )
        foreign_file = str(target / "__init__.py")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "DISK_VALUE = 'from-attested-descriptor'\n", encoding="utf-8"
        )
        foreign_file = str(target)
    child = (
        "import sys\n"
        "from types import ModuleType\n"
        "import dispatch_v2\n"
        f"dispatch_v2.__file__ = {str(pkg / '__init__.py')!r}\n"
        "from dispatch_v2 import _physical_import as pi\n"
        "pi._LOADED_PHYSICAL_SIBLINGS.clear()\n"
        "pi._PHYSICAL_PACKAGE_ROUTES.clear()\n"
        "pi._PHYSICAL_DESCENDANT_MODULES.clear()\n"
        f"foreign = ModuleType({module_name!r})\n"
        f"foreign.__file__ = {foreign_file!r}\n"
        f"sys.modules[{module_name!r}] = foreign\n"
        "raised = False\n"
        "try:\n"
        f"    pi.load_physical_scripts_sibling({module_name!r}, {relative!r},"
        f" package={package!r}, required={required!r})\n"
        "except RuntimeError as exc:\n"
        "    assert 'conflicting preloaded physical sibling' in str(exc), exc\n"
        "    raised = True\n"
        "assert raised, 'owner nie zglosil konfliktu (fast-path?)'\n"
        f"assert sys.modules[{module_name!r}] is foreign\n"
        "assert not hasattr(foreign, 'DISK_VALUE'), 'obce bajty wykonane'\n"
        f"assert {module_name!r} not in pi._LOADED_PHYSICAL_SIBLINGS\n"
        "print('C46_BACKSTOP_OK')\n"
    )
    env = _clean_path_env()
    env["ZIOMEK_SCRIPTS_ROOT"] = str(scripts)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    env["ZIOMEK_LOGS_DIR"] = str(tmp_path / "logs")
    completed = subprocess.run(
        [sys.executable, "-c", child],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "C46_BACKSTOP_OK" in completed.stdout, output


def test_c45_descendant_sys_modules_first_is_documented_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate45 (Sol C44 #2): granica threat-modelu — sys.modules-first.

    Sol pokazał, że potomek pakietu WSTRZYKNIĘTY wprost do `sys.modules[root +
    '.child']` jest zwracany przez CPython z cache ZANIM zadziała jakikolwiek
    meta-path finder (sys.modules sprawdzany przed `sys.meta_path`). To NIE jest
    luka loadera fizycznego siblinga — to semantyka importu CPythona, wspólna
    dla KAŻDEGO importu (wstrzyknięcie `sys.modules['dispatch_v2.common']`
    równie dobrze podmieni rdzeń). Loader broni przed poison FS/`sys.path`
    (atestacja + fd-walk + tożsamość łańcucha przodków, gdy find_spec JEST
    wołany), nie przed in-process injection do `sys.modules`. Ten test
    DOKUMENTUJE granicę: (a) wstrzyknięty potomek jest zwracany z cache bez
    findera (jak każdy inny cached import), (b) ROOT wciąż jest chroniony —
    obcy root => konflikt. Zakres w SCOPE jest tego świadomy.
    """
    import importlib
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "child.py").write_text("SAFE = True\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    root = "_c45_boundary_root"
    try:
        loaded = physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        assert loaded is not None
        # (a) sys.modules-first: wstrzyknięty potomek zwrócony z cache — to
        #     zachowanie CPythona, nie loadera (nie asertujemy „bezpieczeństwa",
        #     tylko dokumentujemy, że to zwykły cached import).
        foreign_child = ModuleType(root + ".child")
        foreign_child.INJECTED = True
        monkeypatch.setitem(sys.modules, root + ".child", foreign_child)
        got = importlib.import_module(root + ".child")
        assert got is foreign_child  # cache-first, jak każdy import w CPythonie
        # (b) ROOT nadal chroniony przez ownera:
        monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
        foreign_root = ModuleType(root)
        foreign_root.__file__ = str(pkg / "__init__.py")
        monkeypatch.setitem(sys.modules, root, foreign_root)
        with pytest.raises(
            RuntimeError, match="conflicting preloaded physical sibling"
        ):
            physical_import.load_physical_scripts_sibling(
                root, "legacy_pkg", package=True
            )
    finally:
        for name in tuple(sys.modules):
            if name == root or name.startswith(root + "."):
                sys.modules.pop(name, None)


def test_c45_descendant_contract_declaration_is_accurate() -> None:
    """Candidate45 (Sol C44 #2): deklaracja kontraktu w kodzie jest ścisła.

    Domyka finding u ŹRÓDŁA: ŻADEN docstring w `_physical_import.py` (moduł
    ANI klasa `_PhysicalSiblingDescendantFinder`) nie może twierdzić, że finder
    „owns every descendant import" (fałsz wobec sys.modules-first). Ratchet
    przegląda CAŁY source (wszystkie docstringi), a nie tylko module_doc, i
    wymaga jawnego udokumentowania granicy. Pinuje deklarację, by over-claim
    nie wrócił po cichu w żadnym miejscu.
    """
    source = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="_physical_import.py")

    # 1) ŻADEN docstring (moduł, klasa, funkcja, metoda) nie over-claimuje.
    over_claim_phrases = ("own every descendant", "owns every descendant")
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            doc = (ast.get_docstring(node) or "").lower()
            for phrase in over_claim_phrases:
                assert phrase not in doc, (
                    "over-claim własności każdego importu potomka w docstringu: "
                    f"{getattr(node, 'name', '<module>')}"
                )

    # 2) Granica sys.modules-first jest jawnie udokumentowana (moduł lub klasa).
    module_doc = (ast.get_docstring(tree) or "")
    finder_doc = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and (
            node.name == "_PhysicalSiblingDescendantFinder"
        ):
            finder_doc = ast.get_docstring(node) or ""
    combined = module_doc + "\n" + finder_doc
    assert "sys.modules" in combined and "meta_path" in combined, (
        "brak udokumentowanej granicy sys.modules-before-finders"
    )
    assert "resolution" in combined, (
        "kontrakt potomka nie zawężony do RESOLUCJI"
    )
    # Klasa findera zawęża własność do resolucji (nie „every import").
    assert "resolution" in finder_doc.lower(), (
        "docstring findera nie zawęża własności do resolucji"
    )


def test_c43_ratchet_rejects_helper_extraction_fast_path() -> None:
    """Candidate43 (Opus, helper-extraction): read sys.modules w OSOBnej funkcji.

    Oracle luki C42/C43: ratchet ograniczał odczyt sys.modules do scope
    `load_physical_scripts_sibling`; przeniesienie `sys.modules.get`/subskryptu
    do funkcji pomocniczej wywołanej z loadera przed ownerem omijało go
    (`violations == []`). C43: odczyt lookup-po-kluczu dozwolony WYŁĄCZNIE
    w ownerze i `find_spec` — helper = naruszenie, w obu formach (`.get`
    i `sys.modules[...]`).
    """
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "    parent_dir = _attested_package_parent()\n"
    assert anchor in real
    assert "def load_physical_scripts_sibling(" in real

    helper_get = (
        "\ndef _peek_preloaded(module_name, required):\n"
        "    existing = sys.modules.get(module_name)\n"
        "    if existing is not None and not required:\n"
        "        return existing\n"
        "    return None\n\n"
    )
    mutant_get = real.replace(
        "def load_physical_scripts_sibling(",
        helper_get + "def load_physical_scripts_sibling(",
        1,
    ).replace(
        anchor,
        "    _hit = _peek_preloaded(module_name, required)\n"
        "    if _hit is not None:\n"
        "        return _hit\n" + anchor,
        1,
    )
    assert mutant_get != real
    v_get = _physical_import_ownership_violations(ast.parse(mutant_get))
    assert any("sys.modules read outside owner" in i for i in v_get), v_get

    helper_sub = (
        "\ndef _peek2(module_name):\n"
        "    if module_name in sys.modules:\n"
        "        return sys.modules[module_name]\n"
        "    return None\n\n"
    )
    mutant_sub = real.replace(
        "def load_physical_scripts_sibling(",
        helper_sub + "def load_physical_scripts_sibling(",
        1,
    ).replace(
        anchor,
        "    _hit = _peek2(module_name)\n"
        "    if _hit is not None and not required:\n"
        "        return _hit\n" + anchor,
        1,
    )
    assert mutant_sub != real
    v_sub = _physical_import_ownership_violations(ast.parse(mutant_sub))
    assert any("sys.modules read outside owner" in i for i in v_sub), v_sub


def test_c43_owner_rejects_forged_module_with_full_spec_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    """Candidate43 (Opus finding 2): reuse tylko po TOŻSAMOŚCI, nie metadanych.

    Opus zauważył, że dotychczasowe oracle preparowały obcy ModuleType tylko
    z `__file__`. Ten oracle wzmacnia pokrycie behawioralne: obcy obiekt ma
    KOMPLET wiarygodnych metadanych (`__file__`, `__spec__` z poprawnym
    origin, `__loader__`, `__cached__`) — a mimo to jedyny owner reuse
    odrzuca go, bo rekord rejestru wiąże TOŻSAMOŚĆ obiektu, nie metadane.
    """
    import importlib.util
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    sibling = scripts / "legacy_sibling.py"
    sibling.write_text(
        "DISK_VALUE = 'from-attested-descriptor'\n", encoding="utf-8"
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})

    module_name = "_c43_full_spec_forged_probe"
    forged = ModuleType(module_name)
    forged.__file__ = str(sibling)
    spec = importlib.util.spec_from_file_location(module_name, str(sibling))
    forged.__spec__ = spec
    forged.__loader__ = getattr(spec, "loader", None)
    forged.__cached__ = None
    monkeypatch.setitem(sys.modules, module_name, forged)
    with pytest.raises(
        RuntimeError, match="conflicting preloaded physical sibling"
    ):
        physical_import.load_physical_scripts_sibling(
            module_name, "legacy_sibling.py"
        )
    assert module_name not in physical_import._LOADED_PHYSICAL_SIBLINGS
    assert sys.modules[module_name] is forged
    assert not hasattr(forged, "DISK_VALUE")


def test_c43_mutation_probe_spec_metadata_adoption_re_reds_oracle(
    tmp_path,
    monkeypatch,
) -> None:
    """Mutation probe M10: owner akceptujący po `__spec__.origin` łamie oracle.

    Dowodzi, że oracle `..._full_spec_metadata` NIE jest próżniowy: gdy jedyny
    owner zaadoptuje obiekt po metadanej `__spec__.origin` (a nie po
    tożsamości rekordu), sfałszowany obiekt z kompletem metadanych zostaje
    zwrócony bez wykonania bajtów z deskryptora — czyli asercje oracle
    (pytest.raises + brak DISK_VALUE) stają się pod mutantem czerwone.
    """
    import importlib.util
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    sibling = scripts / "legacy_sibling.py"
    sibling.write_text(
        "DISK_VALUE = 'from-attested-descriptor'\n", encoding="utf-8"
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})

    def mutant_adopt(name, source):
        existing = sys.modules.get(name)
        if existing is not None:
            spec = getattr(existing, "__spec__", None)
            origin = getattr(spec, "origin", None)
            if origin is not None and Path(origin).samefile(source):
                return existing  # adopcja po metadanej (nie po tożsamości)
        return None

    monkeypatch.setattr(
        physical_import, "_adopt_or_reject_preloaded", mutant_adopt
    )

    module_name = "_c43_spec_mutant_probe"
    forged = ModuleType(module_name)
    forged.__file__ = str(sibling)
    spec = importlib.util.spec_from_file_location(module_name, str(sibling))
    forged.__spec__ = spec
    monkeypatch.setitem(sys.modules, module_name, forged)
    try:
        adopted = physical_import.load_physical_scripts_sibling(
            module_name, "legacy_sibling.py"
        )
        # Pod mutantem sfałszowany obiekt jest adoptowany bez wykonania bajtów.
        assert adopted is forged
        assert not hasattr(adopted, "DISK_VALUE")
    finally:
        sys.modules.pop(module_name, None)


ML_DIRECT_FILE_ENTRYPOINTS = (
    "ml_data_prep/forward_validation.py",
    "ml_data_prep/train_two_models.py",
    "ml_data_prep/parity_ml_inference.py",
)


@pytest.mark.parametrize("relative", ML_DIRECT_FILE_ENTRYPOINTS)
def test_direct_file_ml_entrypoint_reaches_owner_no_bespoke_crash(
    tmp_path,
    relative,
) -> None:
    """Candidate41 O5 (Sol F3): direct-file ML nie akceptuje dowolnego crashu.

    Oracle luki C40: jedyny test tej kompozycji (`..._never_retrusts...`)
    używał `check=False` i sprawdzał wyłącznie brak markera trucizny —
    bezwarunkowy `RuntimeError` w gałęzi direct-only przechodził zielono.
    Kontrakt C41: uruchomienie pliku bezpośrednio (`__package__==''`)
    z `ZIOMEK_SCRIPTS_ROOT` = fizyczny parent MUSI przejść przez bootstrap,
    attest i kanonicznego ownera fizycznego src; ponieważ w tym layoutcie
    fizyczne `ml_data_prep/src` nie istnieje obok parenta, jedynym legalnym
    terminalem jest DETERMINISTYCZNY `trusted physical sibling unavailable:
    src` z ownera loadera — NIE bespoke crash bootstrapu ani atestu.
    """
    physical = PACKAGE_PARENT / "dispatch_v2" / relative
    # ZIOMEK_SCRIPTS_ROOT MUSI być REALNYM (po realpath) parentem pakietu,
    # bo attest porównuje samefile z Path(dispatch_v2.__file__).resolve().
    # PACKAGE_PARENT bywa symlinkowym pkgroot → attest by odrzucił zanim
    # dojdzie do ownera loadera; chcemy dojść do terminala loadera.
    real_parent = REPO_ROOT.resolve().parent
    env = _clean_path_env()
    env["ZIOMEK_SCRIPTS_ROOT"] = str(real_parent)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    env["ZIOMEK_LOGS_DIR"] = str(tmp_path / "logs")
    completed = subprocess.run(
        [sys.executable, str(physical), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "trusted physical sibling unavailable: src" in output, output
    # Terminal MUSI pochodzić od kanonicznego ownera loadera, nie z atestu
    # ani bespoke wyjątku wstawionego w gałęzi direct-only.
    assert "does not own physical dispatch_v2 package" not in output, output
    assert "POST_BOOTSTRAP_SCRIPTS_ROOT_POISON" not in output


def _mirror_ml_entrypoint_layout(tmp_path, entrypoint_source: str):
    """Produkcyjny mirror: pakiet OWNS parenta + fizyczny sibling src + stuby.

    Odtwarza układ produkcji (dispatch_v2 pakiet obok ml_data_prep/src), tak
    by REALNY plik ML uruchomiony bezpośrednio (`__package__==''`)
    samo-bootstrapował TEN pakiet, przeszedł attest i związał FIZYCZNE src.
    Ciężkie zależności (lightgbm) i sąsiednie moduły pakietu są stubowane —
    testowana jest gałąź direct-file, nie trening.
    """
    scripts = tmp_path / "scripts"
    package = scripts / "dispatch_v2"
    ml = package / "ml_data_prep"
    ml.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_physical_import.py").write_text(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (package / "common.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "SCRIPTS_DIR = Path(os.environ['ZIOMEK_SCRIPTS_ROOT'])\n"
        "STATE_DIR = Path(os.environ.get('DISPATCH_STATE_DIR', '.'))\n"
        "LOGS_DIR = Path(os.environ.get('ZIOMEK_LOGS_DIR', '.'))\n",
        encoding="utf-8",
    )
    (ml / "__init__.py").write_text("", encoding="utf-8")
    (ml / "twomodel_common.py").write_text(
        "DATASET_DIR = '.'\n"
        "def solo_mask(*a, **k):\n    raise NotImplementedError\n"
        "def load_split(*a, **k):\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (ml / "train_two_models.py").write_text(
        "def apply_tier_onehot(*a, **k):\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (ml / "forward_validation.py").write_text(entrypoint_source, encoding="utf-8")
    # Fizyczny sibling src OBOK pakietu (jak w produkcji scripts/ml_data_prep/src).
    src = scripts / "ml_data_prep" / "src"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "lgbm_training.py").write_text(
        "def build_pointwise_dataset(*a, **k):\n    raise NotImplementedError\n"
        "def transform_categorical(*a, **k):\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    # Stub ciężkiej zależności (lightgbm nieobecny w tym interpreterze).
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "lightgbm.py").write_text("__version__ = '0-stub'\n", encoding="utf-8")
    env = _clean_path_env()
    env["PYTHONPATH"] = os.pathsep.join((str(scripts), str(stubs)))
    env["ZIOMEK_SCRIPTS_ROOT"] = str(scripts)
    env["DISPATCH_STATE_DIR"] = str(tmp_path / "state")
    env["ZIOMEK_LOGS_DIR"] = str(tmp_path / "logs")
    return scripts, src, env


def test_direct_file_ml_real_module_positive_binds_physical_src(
    tmp_path,
) -> None:
    """Candidate41 O5b (Sol F3): REALNY plik ML w __package__=='' → pozytyw.

    Uruchamia WERBATIM źródło ml_data_prep/forward_validation.py jako skrypt
    (`__package__==''`) w produkcyjnym mirrorze, gdzie fizyczne
    ml_data_prep/src jest obecne obok pakietu. Realna gałąź direct-only
    (bootstrap → attest → fizyczny loader src → `from src... import`)
    przechodzi do POZYTYWNEGO terminala: `--help` kończy się rc == 0 po
    pełnym imporcie, a fizyczne src jest związane (dowód markerem w src).
    Zamyka Sol F3: sam brak markera trucizny już nie wystarcza.
    """
    source = (
        REPO_ROOT / "ml_data_prep" / "forward_validation.py"
    ).read_text(encoding="utf-8")
    scripts, src, env = _mirror_ml_entrypoint_layout(tmp_path, source)
    # Znacznik wykonania fizycznego src (dowód związania, nie tylko rc).
    marker = tmp_path / "physical_src_bound.marker"
    (src / "__init__.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('PHYSICAL_SRC_BOUND')\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(scripts / "dispatch_v2" / "ml_data_prep" /
                             "forward_validation.py"), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert marker.exists(), f"fizyczne src nie zwiazane: {output}"
    assert "trusted physical sibling unavailable" not in output, output
    assert "does not own physical dispatch_v2 package" not in output, output


def test_c41_direct_only_crash_reds_positive_oracle(tmp_path) -> None:
    """Mutation probe M9 (Sol F3): bespoke crash w realnej gałęzi direct-only.

    Wstawia bezwarunkowy `RuntimeError` do gałęzi `if __package__ in (None,
    '')` REALNEGO źródła forward_validation, uruchamia w tym samym mirrorze
    i dowodzi, że pozytywny terminal ZNIKA (rc != 0, brak związania src) —
    czyli oracle O5b czerwieni się pod tym mutantem (w C40 crash był
    akceptowany przez check=False).
    """
    source = (
        REPO_ROOT / "ml_data_prep" / "forward_validation.py"
    ).read_text(encoding="utf-8")
    anchor = "if __package__ in (None, \"\"):\n    _package_dir = Path(__file__).resolve().parent.parent"
    assert anchor in source
    mutant = source.replace(
        anchor,
        "if __package__ in (None, \"\"):\n"
        "    raise RuntimeError('C41_DIRECT_ONLY_CRASH')\n"
        "    _package_dir = Path(__file__).resolve().parent.parent",
        1,
    )
    assert mutant != source
    scripts, src, env = _mirror_ml_entrypoint_layout(tmp_path, mutant)
    marker = tmp_path / "physical_src_bound.marker"
    (src / "__init__.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('PHYSICAL_SRC_BOUND')\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(scripts / "dispatch_v2" / "ml_data_prep" /
                             "forward_validation.py"), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "C41_DIRECT_ONLY_CRASH" in output, output
    assert not marker.exists(), "src zwiazane mimo crashu w galezi direct-only"


# ─────────────────────────────────────────────────────────────────────────────
# Candidate49 — dziewięć findingów świeżego Sol/max na frozen C48
# (CONFIRMED_DEFECT): F1 publish-before-finder, F2 brak CAS load-locka,
# F3 owner-registry poza transakcją, F4 retire close przerywa purge, F5 dup vs
# retire recykling fd, F6 except-close double-close (ctor infallible), F7 lint
# omija MatchAs/wildcard, F8 autoryzowany scope po gołej nazwie. Fixy U ŹRÓDŁA
# + RED-first oracle każdego.
# ─────────────────────────────────────────────────────────────────────────────


def _c49_setup_pkg(tmp_path, monkeypatch):
    """Layout dispatch_v2 + legacy_pkg/child, świeże rejestry; NIC nie ładuje.

    Zwraca `physical_import, scripts, legacy`. Testy ładują WŁASNĄ, unikalną
    nazwę roota (bez kolizji z zaszytym `_c46_root`) i sprzątają sys.modules
    w `finally`.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "child.py").write_text("SAFE = 'attested'\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    return physical_import, scripts, legacy


def _c49_purge_sys_modules(root: str) -> None:
    for name in tuple(sys.modules):
        if name == root or name.startswith(root + "."):
            sys.modules.pop(name, None)


def _c49_load_lock_body(tree: ast.Module):
    """Zwróć węzeł `with _PHYSICAL_LOAD_LOCK` PUBLIKACJI w loaderze.

    Loader ma KILKA sekcji `with _PHYSICAL_LOAD_LOCK` (Candidate62: osobne krótkie
    sekcje mark/unmark tombstone'u load-window na wejściu i w `finally`, plus
    post-exec supersede-check i rollback). Sekcją PUBLIKACJI (adopt + zapis trasy /
    `sys.modules[root]` / owner-registry) jest ta, której ciało zawiera wywołanie
    `_adopt_or_reject_preloaded` — jednoznaczny marker sekcji krytycznej. Zwracamy
    JĄ, a nie pierwszą napotkaną (którą jest teraz krótka sekcja mark).
    """
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "load_physical_scripts_sibling"
        ):
            for sub in ast.walk(node):
                if isinstance(sub, ast.With):
                    is_load_lock = any(
                        isinstance(item.context_expr, ast.Name)
                        and item.context_expr.id == "_PHYSICAL_LOAD_LOCK"
                        for item in sub.items
                    )
                    if not is_load_lock:
                        continue
                    has_adopt = any(
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "_adopt_or_reject_preloaded"
                        for inner in ast.walk(sub)
                    )
                    if has_adopt:
                        return sub
    return None


def test_c49_publish_order_route_finder_before_sys_modules() -> None:
    """F1+root-claim (Sol C48/C50): niezmiennik bezpiecznej publikacji.

    Candidate51 zmienia model ochrony: zamiast kolejności trasa-przed-sys.modules
    (F1), potomek jest chroniony PUSTYM `__path__` pakietu
    (`submodule_search_locations=[]`), więc `PathFinder` NIGDY nie rozwiąże
    potomka z tekstowej ścieżki — fall-through jest strukturalnie niemożliwy
    niezależnie od kolejności. Dzięki temu `sys.modules[root]` może (i MUSI) być
    ustawione PIERWSZE (root-claim): CPython konsultuje `sys.modules` przed
    `sys.meta_path`/`sys.path`, więc równoległy `import <root>` w oknie
    publikacji dostaje NASZ obiekt, nie obcy z zatrutego `sys.path` czy
    wstrzykniętego findera. Oracle pinuje OBA niezmienniki:
      (1) root spec ma `submodule_search_locations=[]` (puste __path__);
      (2) `sys.modules[module_name] = module` ma linenr MNIEJSZY niż zapis trasy.
    RED-first: przywrócenie `[str(target)]` (child fall-through) albo
    przeniesienie sys.modules po trasie (okno root-claim) czerwieni.
    """
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    with_node = _c49_load_lock_body(tree)
    assert with_node is not None, "brak sekcji `with _PHYSICAL_LOAD_LOCK` w loaderze"
    load_fn = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "load_physical_scripts_sibling"
        ):
            load_fn = node
    assert load_fn is not None
    route_stores: list[int] = []
    sysmod_stores: list[int] = []
    for node in ast.walk(with_node):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "_PHYSICAL_PACKAGE_ROUTES"
            and isinstance(node.ctx, ast.Store)
        ):
            route_stores.append(node.lineno)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "modules"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "sys"
            and isinstance(node.ctx, ast.Store)
        ):
            sysmod_stores.append(node.lineno)
    # (1) puste __path__: enforcer `spec.submodule_search_locations = []` (Assign
    #     pustej listy do atrybutu). `compile()`/build są POZA sekcją krytyczną
    #     (Candidate53), więc szukamy w całej funkcji loadera, nie w `with`.
    empty_search_locations = False
    for node in ast.walk(load_fn):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Attribute)
            and t.attr == "submodule_search_locations"
            for t in node.targets
        ):
            if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
                empty_search_locations = True
    assert empty_search_locations, (
        "root spec MUSI mieć submodule_search_locations = [] (puste __path__)"
    )
    # (2) root-claim: sys.modules PRZED trasą.
    assert len(route_stores) == 1 and len(sysmod_stores) == 1, (
        route_stores, sysmod_stores,
    )
    assert max(sysmod_stores) < route_stores[0], (
        "sys.modules[root] musi być ustawione PRZED trasą (root-claim)",
        sysmod_stores, route_stores,
    )


def test_c49_load_critical_section_is_lock_guarded() -> None:
    """F2 (Sol C48): adopt + publikacja są w JEDNEJ sekcji pod load-lockiem.

    Atomowy CAS pod nazwę: `_adopt_or_reject_preloaded(...)`, zapis trasy,
    `sys.modules[module_name]=module` i zapis owner-registry MUSZĄ leżeć wewnątrz
    tego samego `with _PHYSICAL_LOAD_LOCK`. RED-first: wyjęcie publikacji spod
    locka usuwa je z ciała `with` i czerwieni.
    """
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    with_node = _c49_load_lock_body(tree)
    assert with_node is not None
    body_ids = {id(n) for n in ast.walk(with_node)}
    adopt = route = sysmod = owner = False
    for node in ast.walk(with_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_adopt_or_reject_preloaded"
        ):
            adopt = True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "_PHYSICAL_PACKAGE_ROUTES"
            and isinstance(node.ctx, ast.Store)
        ):
            route = True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "modules"
            and isinstance(node.ctx, ast.Store)
        ):
            sysmod = True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "_LOADED_PHYSICAL_SIBLINGS"
            and isinstance(node.ctx, ast.Store)
        ):
            owner = True
    assert adopt and route and sysmod and owner, (adopt, route, sysmod, owner)
    # `exec` NIE może być pod lockiem (kod użytkownika → ryzyko deadlocku).
    exec_under_lock = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "exec"
        and id(n) in body_ids
        for n in ast.walk(with_node)
    )
    assert not exec_under_lock, "exec nie może biec pod load-lockiem"


def test_c49_dup_retained_is_lock_guarded_and_fail_closed() -> None:
    """F5 (Sol C48): odczyt handle.fd + dup atomowe pod lockiem, None→fail-closed.

    Strukturalnie: `_dup_retained_under_lock` ma `with _PHYSICAL_LOAD_LOCK`,
    sprawdza `fd is None` i podnosi `ModuleNotFoundError`. RED-first: usunięcie
    locka albo braku sprawdzenia None łamie test.
    """
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    fn = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_dup_retained_under_lock"
        ):
            fn = node
    assert fn is not None, "brak _dup_retained_under_lock"
    has_lock = any(
        isinstance(n, ast.With)
        and any(
            isinstance(it.context_expr, ast.Name)
            and it.context_expr.id == "_PHYSICAL_LOAD_LOCK"
            for it in n.items
        )
        for n in ast.walk(fn)
    )
    raises_mnfe = any(
        isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
        and n.exc.func.id == "ModuleNotFoundError"
        for n in ast.walk(fn)
    )
    checks_none = any(
        isinstance(n, ast.Compare)
        and any(isinstance(c, ast.Is) for c in n.ops)
        for n in ast.walk(fn)
    )
    assert has_lock and raises_mnfe and checks_none, (
        has_lock, raises_mnfe, checks_none,
    )


def test_c49_descendant_fail_closed_after_retire(tmp_path, monkeypatch) -> None:
    """F5 (Sol C48): po retire pakietu import potomka jest fail-closed.

    Retire zamyka utrzymany deskryptor (`handle.fd=None`); kolejne rozwiązanie
    potomka bierze dup pod lockiem, widzi None i podnosi `ModuleNotFoundError`
    zamiast dup-ować zrecyklingowany/nieaktualny fd. RED-first: surowy
    `_dup_cloexec(handle.fd)` na `None` rzuciłby `TypeError`, nie fail-closed.
    """
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    root = "_c49_failclosed"
    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        # potomek działa przed retire:
        assert importlib.import_module(root + ".child").SAFE == "attested"
        route = physical_import._PHYSICAL_PACKAGE_ROUTES[root]
        handle = route[3]
        # Retire zamyka handle (fd -> None); testujemy bezpośrednio
        # _open_descendant_source_fd na zretirowanym handle.
        handle.close()
        assert handle.fd is None
        with pytest.raises(ModuleNotFoundError):
            physical_import._open_descendant_source_fd(
                handle, ("child",), package=False
            )
    finally:
        _c49_purge_sys_modules(root)


def test_c49_retire_close_failure_still_purges(tmp_path, monkeypatch) -> None:
    """F4 (Sol C48): wyjątek `handle.close()` NIE przerywa purge poddrzewa.

    Retirement: pop trasy → close fd → PURGE w `finally`. Jeśli close rzuci
    (np. os.close EBADF), sys.modules i rejestr descendant MUSZĄ i tak zostać
    wyczyszczone. RED-first: purge poza `finally` zostawiłby wpisy po close-fail.
    """
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    root = "_c49_closefail"
    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        importlib.import_module(root + ".child")
        assert root in sys.modules and root + ".child" in sys.modules
        assert root + ".child" in physical_import._PHYSICAL_DESCENDANT_MODULES

        orig_close = physical_import._RetainedDirFd.close

        def _close_then_boom(self):
            orig_close(self)  # realnie zamyka fd (bez wycieku)
            raise RuntimeError("close boom")

        monkeypatch.setattr(
            physical_import._RetainedDirFd, "close", _close_then_boom
        )
        with pytest.raises(RuntimeError, match="close boom"):
            physical_import._retire_package_route(root)
        # Mimo wyjątku close — purge wykonany na WSZYSTKICH rejestrach
        # (F3: owner-registry też objęty kanonicznym rollbackiem):
        assert root not in physical_import._PHYSICAL_PACKAGE_ROUTES
        assert root not in sys.modules
        assert root + ".child" not in sys.modules
        assert root not in physical_import._LOADED_PHYSICAL_SIBLINGS
        assert not [
            n for n in physical_import._PHYSICAL_DESCENDANT_MODULES
            if n == root or n.startswith(root + ".")
        ]
    finally:
        _c49_purge_sys_modules(root)


def test_c49_retained_dir_fd_init_is_infallible() -> None:
    """F6 (Sol C48): `_RetainedDirFd.__init__` = pojedynczy `self.fd = fd`.

    Transfer własności deskryptora do handle jest bezpieczny (brak przecieku
    ORAZ brak double-close) TYLKO dlatego, że konstruktor jest infallible:
    jedyna instrukcja to zapis slotu `fd`, więc `handle = _RetainedDirFd(fd)`
    nie może rzucić po przejęciu fd. Ratchet pinuje ten niezmiennik, więc nikt
    nie dołoży fallible pracy (która wskrzesiłaby przeciek/except-close).
    RED-first: dodanie dowolnej instrukcji do `__init__` czerwieni.
    """
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    init = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_RetainedDirFd":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "__init__":
                    init = sub
    assert init is not None, "brak _RetainedDirFd.__init__"
    body = [n for n in init.body if not isinstance(n, ast.Expr)
            or not isinstance(getattr(n, "value", None), ast.Constant)]
    assert len(body) == 1, ast.dump(init)
    stmt = body[0]
    assert isinstance(stmt, ast.Assign)
    assert len(stmt.targets) == 1
    tgt = stmt.targets[0]
    assert isinstance(tgt, ast.Attribute) and tgt.attr == "fd"
    assert isinstance(tgt.value, ast.Name) and tgt.value.id == "self"
    assert isinstance(stmt.value, ast.Name) and stmt.value.id == "fd"


_C49_NESTED_SCOPE_SHADOW_MUTANTS = {
    # Zagnieżdżona funkcja o nazwie autoryzowanego czytelnika (find_spec)
    # nie może uzyskać prawa czytania sys.modules jako wartości.
    "nested_find_spec_name": (
        "preloaded = _adopt_or_reject_preloaded(module_name, source)",
        "def find_spec(_n):\n"
        "                        return sys.modules.get(_n)\n"
        "                    _y = find_spec(module_name)\n"
        "                    if _y is not None and not required:\n"
        "                        return _y\n"
        "                    preloaded = _adopt_or_reject_preloaded(module_name, source)",
    ),
    # Zagnieżdżony owner przechwytujący prawdziwego (Sol C48) — czyta
    # sys.modules warunkowo, delegując resztę.
    "nested_owner_capture": (
        "                    preloaded = _adopt_or_reject_preloaded(module_name, source)",
        "                    def _adopt_or_reject_preloaded(_n, _s, _real=_adopt_or_reject_preloaded):\n"
        "                        if _s is None:\n"
        "                            return sys.modules.get(_n)\n"
        "                        return _real(_n, _s)\n"
        "                    preloaded = _adopt_or_reject_preloaded(module_name, source)",
    ),
    # Candidate52 (Sol C51): DRUGA klasa z metodą `find_spec` nie może uzyskać
    # prawa czytania sys.modules samą nazwą metody (scope kwalifikowany klasą).
    "second_class_find_spec": (
        "class _PhysicalSiblingDescendantFinder:",
        "class _EvilFinder:\n"
        "    def find_spec(self, module_name):\n"
        "        return sys.modules.get(module_name)\n\n\n"
        "class _PhysicalSiblingDescendantFinder:",
    ),
    # Candidate52 (Sol C51): `sys.__dict__['modules']` to odczyt sys.modules.
    "sys_dict_modules": (
        "                    preloaded = _adopt_or_reject_preloaded(module_name, source)",
        "                    _hit = sys.__dict__['modules'].get(module_name)\n"
        "                    if _hit is not None and not required:\n"
        "                        return _hit\n"
        "                    preloaded = _adopt_or_reject_preloaded(module_name, source)",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C49_NESTED_SCOPE_SHADOW_MUTANTS))
def test_c49_ratchet_flags_nested_authorized_scope_shadow(mutant) -> None:
    """F8 (Sol C48): autoryzowany scope nie może być podszyty gołą nazwą.

    Luka C48: `scope_of` kluczował scope gołą nazwą funkcji, więc ZAGNIEŻDŻONA
    funkcja nazwana jak owner/`find_spec` dziedziczyła prawo czytania
    sys.modules jako wartości — fast-path z pominięciem kanonicznego ownera.
    C49: scope kwalifikowany nazwą zagnieżdżenia; tylko def MODUŁOWA jest
    autoryzowana. Oba warianty MUSZĄ być flagowane; żywy plik CLEAN.
    """
    anchor, replacement = _C49_NESTED_SCOPE_SHADOW_MUTANTS[mutant]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    assert anchor in real, mutant
    mutated = real.replace(anchor, replacement, 1)
    assert mutated != real
    violations = _physical_import_ownership_violations(ast.parse(mutated))
    assert any("sys.modules" in v for v in violations), (mutant, violations)


def test_c49_concurrent_same_name_load_is_coherent(tmp_path, monkeypatch) -> None:
    """F2 (Sol C48): równoległe loady tej samej nazwy → spójna tożsamość.

    Dwa wątki ładują ten sam pakiet równocześnie (bariera maksymalizuje
    kolizję). Load-lock czyni adopt+publikację atomowym CAS, więc pierwszy
    publikuje, a drugi widzi preloaded i zwraca ten sam obiekt: sys.modules,
    route[2] i owner-registry wskazują JEDEN moduł, bez konkurencyjnych
    tożsamości ani `conflicting`. Behawioralny backstop współbieżności.
    """
    import threading
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    name = "_c49_conc"
    barrier = threading.Barrier(6)
    results: list = []
    errors: list = []

    def worker():
        try:
            barrier.wait()
            mod = physical_import.load_physical_scripts_sibling(
                name, "legacy_pkg", package=True
            )
            results.append(mod)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert not errors, errors
        published = sys.modules.get(name)
        assert published is not None
        assert all(m is published for m in results), "różne obiekty modułu"
        route = physical_import._PHYSICAL_PACKAGE_ROUTES[name]
        assert route[2] is published, "route[2] ≠ opublikowany moduł"
        recorded = physical_import._LOADED_PHYSICAL_SIBLINGS[name]
        assert recorded[1] is published, "owner-registry ≠ opublikowany moduł"
    finally:
        physical_import._retire_package_route(name)
        physical_import._LOADED_PHYSICAL_SIBLINGS.pop(name, None)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate50 — finding świeżego Sol/max na frozen C49 (CONFIRMED_DEFECT,
# zreprodukowany mechanicznie): retire-side twin okna publikacji. Retirement
# popował trasę PRZED purge sys.modules; w oknie „root wciąż opublikowany, trasa
# zniknęła" import potomka mijał findera (trasa None) i spadał do PathFindera /
# obcego findera → wykonanie obcych bajtów potomka. Fix: purge PRZED popem trasy.
# ─────────────────────────────────────────────────────────────────────────────


def test_c50_retire_purges_sys_modules_before_route_pop() -> None:
    """F-C49 (Sol): w `_retire_package_route` purge (usunięcie roota z
    sys.modules) MUSI poprzedzać pop trasy. Oracle strukturalny: lineno
    wywołania `_purge_owned_module_subtree` < lineno `pop` trasy. RED-first:
    odwrócenie kolejności (pop trasy przed purge) czerwieni.
    """
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    fn = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_retire_package_route"
        ):
            fn = node
    assert fn is not None
    purge_line = route_pop_line = None
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_purge_owned_module_subtree"
        ):
            purge_line = node.lineno
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "pop"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_PHYSICAL_PACKAGE_ROUTES"
        ):
            route_pop_line = node.lineno
    assert purge_line is not None and route_pop_line is not None, (
        purge_line, route_pop_line,
    )
    assert purge_line < route_pop_line, (
        "purge sys.modules musi poprzedzać pop trasy w retire",
        purge_line, route_pop_line,
    )


def test_c50_retire_window_no_descendant_fall_through(
    tmp_path, monkeypatch
) -> None:
    """F-C49 (Sol, zreprodukowany): brak fall-through potomka w oknie retire.

    Podczas retire (pod lockiem) `close` deskryptora jest wstrzymany. W tym
    oknie równoległy import `<root>.<child>` NIE może uciec do obcego findera /
    PathFindera i wykonać obcych bajtów. Dzięki purge-before-pop w chwili
    wstrzymanego close root jest JUŻ usunięty z sys.modules, więc import pada
    fail-closed. RED-first: kolejność pop-przed-purge zostawia root
    opublikowany przy zniknionej trasie → obcy loader wykonuje potomka.
    """
    import threading
    import importlib.abc
    import importlib.util
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    (legacy / "evil.py").write_text("PHYS = 1\n", encoding="utf-8")
    name = "_c50_gap"
    executed: list = []

    class _ForeignLoader(importlib.abc.Loader):
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            executed.append(module.__name__)

    class _ForeignFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == name + ".evil":
                return importlib.util.spec_from_loader(
                    fullname, _ForeignLoader()
                )
            return None

    foreign = _ForeignFinder()
    try:
        physical_import.load_physical_scripts_sibling(
            name, "legacy_pkg", package=True
        )
        idx = sys.meta_path.index(physical_import._DESCENDANT_FINDER)
        sys.meta_path.insert(idx + 1, foreign)

        entered = threading.Event()
        release = threading.Event()
        orig_close = physical_import._RetainedDirFd.close

        def _paused_close(self):
            entered.set()
            release.wait(3)
            return orig_close(self)

        monkeypatch.setattr(
            physical_import._RetainedDirFd, "close", _paused_close
        )

        def _retire():
            with physical_import._PHYSICAL_LOAD_LOCK:
                physical_import._retire_package_route(name)

        t = threading.Thread(target=_retire)
        t.start()
        assert entered.wait(3), "retire nie dotarł do close"
        # W oknie wstrzymanego close: root już usunięty z sys.modules (purge
        # poszedł PRZED popem trasy), więc import potomka pada fail-closed.
        root_published = name in sys.modules
        try:
            importlib.import_module(name + ".evil")
            imported = True
        except BaseException:  # noqa: BLE001
            imported = False
        release.set()
        t.join()
        assert not executed, ("obce bajty potomka wykonane w oknie retire",
                              executed)
        assert root_published is False, "root wciąż w sys.modules w oknie retire"
        assert imported is False, "import potomka przeszedł mimo retire"
    finally:
        release.set()
        try:
            sys.meta_path.remove(foreign)
        except ValueError:
            pass
        _c49_purge_sys_modules(name)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate51 — finding świeżego Sol/max na frozen C50 (CONFIRMED_DEFECT,
# zreprodukowany mechanicznie): okno „root-claim" podczas publikacji. Root był
# chwilowo nieobecny w sys.modules (trasa ustawiona, sys.modules nie), więc
# równoległy `import <root>` mógł go rozwiązać przez zatruty sys.path / obcy
# finder → wykonanie obcych bajtów ROOT. Fix U ŹRÓDŁA: (1) puste `__path__`
# pakietu (submodule_search_locations=[]) → potomek NIGDY nie spada do
# PathFindera; (2) `sys.modules[root]` ustawiane PRZED trasą (CPython konsultuje
# sys.modules przed meta_path/sys.path → `import <root>` dostaje NASZ obiekt).
# ─────────────────────────────────────────────────────────────────────────────


def test_c51_loaded_package_has_empty_path(tmp_path, monkeypatch) -> None:
    """F-C50 (Sol) #1: `__path__` załadowanego pakietu jest PUSTE.

    Puste `__path__` sprawia, że `PathFinder` nie ma gdzie szukać potomka →
    każdy `<root>.<child>` idzie wyłącznie przez naszego findera (rozwiązanie
    z utrzymanego deskryptora), a nie-rozpoznany potomek pada fail-closed.
    Sprawdzane dla roota ORAZ potomnego SUBPAKIETU. RED-first: przywrócenie
    `[str(target)]` daje nie-puste `__path__`.
    """
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    # potomny subpakiet:
    sub = legacy / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "leaf.py").write_text("LEAF = 1\n", encoding="utf-8")
    root = "_c51_emptypath"
    try:
        mod = physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        assert list(mod.__path__) == [], ("root __path__ nie jest puste",
                                          list(mod.__path__))
        submod = importlib.import_module(root + ".sub")
        assert list(submod.__path__) == [], (
            "subpakiet __path__ nie jest puste", list(submod.__path__)
        )
        # potomek nadal ładuje się przez naszego findera (nie z __path__):
        assert importlib.import_module(root + ".sub.leaf").LEAF == 1
    finally:
        _c49_purge_sys_modules(root)


def test_c51_root_claim_no_foreign_root_in_publish_window(
    tmp_path, monkeypatch
) -> None:
    """F-C50 (Sol) #2: w oknie publikacji `import <root>` dostaje NASZ obiekt.

    Zatruty `sys.path` zawiera kolidujący pakiet `<root>`. Podczas publikacji
    (wstrzymany `_ensure_descendant_finder_installed`, pod lockiem) równoległy
    `import <root>` MUSI zwrócić nasz obiekt z `sys.modules` (ustawiony PRZED
    trasą), a obce `__init__.py` z zatrutego sys.path NIE może się wykonać.
    RED-first: ustawienie `sys.modules[root]` PO trasie zostawia root nieobecny
    w oknie → `import <root>` wykonuje obce bajty z sys.path (marker powstaje).
    """
    import threading
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    root = "_c51_claim"
    poison = tmp_path / "poison"
    (poison / root).mkdir(parents=True)
    marker = tmp_path / "foreign_root_ran.marker"
    (poison / root / "__init__.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('FOREIGN')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(poison))
    importlib.invalidate_caches()

    entered = threading.Event()
    release = threading.Event()
    orig = physical_import._ensure_descendant_finder_installed

    def _paused_ensure():
        entered.set()
        release.wait(3)
        orig()

    monkeypatch.setattr(
        physical_import, "_ensure_descendant_finder_installed", _paused_ensure
    )
    observed = {}

    def _load():
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )

    t = threading.Thread(target=_load)
    t.start()
    try:
        assert entered.wait(3), "publikacja nie dotarła do ensure"
        # okno: sys.modules[root] JUŻ ustawione (root-claim), trasa ustawiana,
        # ensure wstrzymany. `import <root>` musi dać nasz obiekt.
        m = importlib.import_module(root)
        observed["is_ours"] = m is sys.modules.get(root)
        observed["foreign_ran"] = marker.exists()
    finally:
        release.set()
        t.join(3)
    assert observed.get("foreign_ran") is False, (
        "obcy root z zatrutego sys.path wykonany w oknie publikacji"
    )
    assert observed.get("is_ours") is True, "import <root> nie zwrócił naszego obiektu"
    _c49_purge_sys_modules(root)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate52 — findingi świeżego Sol/max na frozen C51 (CONFIRMED_DEFECT):
# luki strukturalnego lintu (except-as, druga klasa find_spec, sys.__dict__,
# getattr z liczonym stringiem). ROZSTRZYGNIĘCIE: strukturalny lint z ZAŁOŻENIA
# NIE jest kompletny wobec adwersaryjnej składni (getattr(sys,'mod'+'ules'),
# `exec`, `eval`, refleksja — nierozstrzygalne statycznie). SOUND GUARANTEE
# promocji = behawioralny backstop (runtime owner), który łapie KAŻDY mechanizm
# reuse niezależnie od składni. C52: (1) domyka TANIE, naturalne kształty
# (except-as, class-qualified scope, sys.__dict__) jako defense-in-depth;
# (2) DOWODZI mechanicznie, że backstop łapie fast-path niewidzialny dla lintu.
# ─────────────────────────────────────────────────────────────────────────────


def test_c52_behavioral_backstop_catches_lint_evading_fastpath() -> None:
    """Sol C51: lint NIE jest kompletny; SOUND guarantee = behawioralny backstop.

    Dowód dwuczęściowy dla fast-pathu, którego statyczny lint z założenia nie
    złapie (`getattr(sys, 'mod'+'ules')` — string liczony w runtime):
      (1) strukturalny lint MIJA go (`_physical_import_ownership_violations ==
          []`) — potwierdzenie, że lint to defense-in-depth, nie bariera;
      (2) ten sam mutant BEHAWIORALNIE zwraca OBCY preload zamiast zgłosić
          konflikt — czyli behawioralny backstop (który asertuje RAISE +
          niewykonanie obcych bajtów) staje się CZERWONY. To dowodzi, że
          backstop wiąże KAŻDY mechanizm reuse niezależnie od składni odczytu
          sys.modules, więc luki lintu nie są luką GWARANCJI.
    """
    import types
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = (
        "                    preloaded = _adopt_or_reject_preloaded("
        "module_name, source)"
    )
    assert anchor in real
    fastpath = (
        "                    _ev = getattr(sys, 'mod' + 'ules').get(module_name)\n"
        "                    if _ev is not None:\n"
        "                        return _ev\n"
    )
    mutant = real.replace(anchor, fastpath + anchor, 1)
    assert mutant != real

    # (1) lint MIJA mutant (adwersaryjny getattr — statycznie nierozstrzygalny):
    assert _physical_import_ownership_violations(ast.parse(mutant)) == [], (
        "lint nieoczekiwanie złapał getattr-computed — zmień na inny "
        "adwersaryjny kształt do dowodu"
    )

    # (2) behawioralnie: mutant ZWRACA obcy preload (backstop by to złapał).
    ns: dict = {"__name__": "dispatch_v2._physical_import_c52_probe"}
    stub_pkg = types.ModuleType("dispatch_v2")
    stub_pkg.__file__ = "/nonexistent/dispatch_v2/__init__.py"
    saved = sys.modules.get("dispatch_v2")
    name = "_c52_backstop_probe"
    foreign = types.ModuleType(name)
    foreign.FOREIGN = True
    try:
        sys.modules["dispatch_v2"] = stub_pkg
        exec(compile(mutant, "_physical_import.py", "exec"), ns)
        # stuby: fizyczne źródło OBECNE (żeby fast-path był jedyną różnicą):
        ns["_attested_package_parent"] = lambda: __import__("pathlib").Path("/x")
        ns["_open_physical_source_fd"] = lambda *a, **k: (
            os.open(os.devnull, os.O_RDONLY),
            os.open(".", os.O_RDONLY | os.O_DIRECTORY),
        )
        ns["_read_all_from_fd"] = lambda fd: b"DISK = True\n"
        ns["_LOADED_PHYSICAL_SIBLINGS"].clear()
        ns["_PHYSICAL_PACKAGE_ROUTES"].clear()
        ns["_PHYSICAL_DESCENDANT_MODULES"].clear()
        sys.modules[name] = foreign
        returned = ns["load_physical_scripts_sibling"](
            name, "legacy_mod.py", required=False
        )
        # Fast-path zwrócił OBCY obiekt (owner NIE zgłosił konfliktu) — to jest
        # dokładnie sygnał, na który czerwieni się behawioralny backstop.
        assert returned is foreign, (
            "mutant nie zwrócił obcego preloadu — dowód backstopu nieważny"
        )
        assert not hasattr(foreign, "DISK"), "obce bajty nie powinny się wykonać"

        # (3) PARYTET Z REALNYM BACKSTOPEM (Candidate53/Sol C52 #7): ten sam
        # kontrakt, którego pilnuje `test_c45_behavioral_backstop_foreign_
        # preload_source_present_conflicts` (owner MUSI RAISE „conflicting"),
        # zastosowany do REALNEGO (niemutowanego) loadera — RAISE. Ten sam
        # kontrakt na mutancie zwraca obcy obiekt (wyżej). Czyli: gdy właściwy
        # backstop pilnuje RAISE, mutant getattr-fast-path czerwieni go —
        # backstop jest sygnałem, którego lint (MISSED wyżej) nie daje.
        assert "_C45_BACKSTOP_TARGETS" in globals(), "brak realnego backstopu"
        ns_real: dict = {"__name__": "dispatch_v2._physical_import_c52_real"}
        exec(compile(real, "_physical_import.py", "exec"), ns_real)
        ns_real["_attested_package_parent"] = (
            lambda: __import__("pathlib").Path("/x")
        )
        ns_real["_open_physical_source_fd"] = lambda *a, **k: (
            os.open(os.devnull, os.O_RDONLY),
            os.open(".", os.O_RDONLY | os.O_DIRECTORY),
        )
        ns_real["_read_all_from_fd"] = lambda fd: b"DISK = True\n"
        ns_real["_LOADED_PHYSICAL_SIBLINGS"].clear()
        ns_real["_PHYSICAL_PACKAGE_ROUTES"].clear()
        ns_real["_PHYSICAL_DESCENDANT_MODULES"].clear()
        sys.modules[name] = foreign  # ponowny preload (mutant nic nie zmienił)
        raised = False
        try:
            ns_real["load_physical_scripts_sibling"](
                name, "legacy_mod.py", required=False
            )
        except RuntimeError as exc:
            assert "conflicting preloaded physical sibling" in str(exc), exc
            raised = True
        assert raised, (
            "REALNY loader nie zgłosił konfliktu — kontrakt backstopu złamany"
        )
    finally:
        sys.modules.pop(name, None)
        if saved is not None:
            sys.modules["dispatch_v2"] = saved
        else:
            sys.modules.pop("dispatch_v2", None)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate53 — findingi świeżego Sol/max na frozen C52 (CONFIRMED_DEFECT):
# defekty transakcji/lifecycle/współbieżności runtime. Fixy U ŹRÓDŁA + RED-first.
# ─────────────────────────────────────────────────────────────────────────────


def test_c53_compile_not_under_load_lock_no_reentrant_deadlock(
    tmp_path, monkeypatch
) -> None:
    """F#4 (Sol C52): `compile()` NIE biegnie pod load-lockiem → brak deadlocku.

    `compile()` emituje zdarzenie audytu; hook to KOD UŻYTKOWNIKA i może
    re-entrować loader. Gdy `compile` biegł pod nie-reentrantnym lockiem,
    re-entrujący load zawieszał się na tym samym locku (deadlock, rc=124).
    C53 buduje (read/spec/compile) POZA sekcją krytyczną. Test: audit-hook na
    `compile` ładuje drugi sibling w trakcie pierwszego load-a — oba kończą się
    bez zawieszenia. RED-first: `compile()` z powrotem pod lockiem → deadlock.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "a.py").write_text("A = 1\n", encoding="utf-8")
    (scripts / "b.py").write_text("B = 1\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    state = {"reentered": False, "ok": False}

    def _hook(event, args):
        if event == "compile" and not state["reentered"]:
            state["reentered"] = True
            physical_import.load_physical_scripts_sibling(
                "_c53_b", "b.py", required=False
            )

    sys.addaudithook(_hook)  # audit hook nie da się odpiąć — nazwy unikalne
    try:
        physical_import.load_physical_scripts_sibling(
            "_c53_a", "a.py", required=False
        )
        state["ok"] = True
        assert state["reentered"], "hook nie re-entrował loadera (test nieważny)"
        assert "_c53_a" in sys.modules and "_c53_b" in sys.modules
    finally:
        _c49_purge_sys_modules("_c53_a")
        _c49_purge_sys_modules("_c53_b")


def test_c53_walk_components_fd_transfer_exception_safe() -> None:
    """F#5 (Sol C52): transfer fd w `_walk_components_from_fd` exception-safe.

    Gdy `os.close(old_fd)` rzuci (np. EINTR), NIE wolno zamknąć tego numeru
    ponownie (double-close zrecyklingowanego fd) ani wyciec nowego `next_fd`.
    RED-first: stara kolejność (`os.close(fd); fd = next_fd` + outer
    `os.close(fd)`) daje `[100, 100]` i wyciek 200.
    """
    import errno as _errno
    import unittest.mock as m
    from dispatch_v2 import _physical_import as physical_import

    closes: list = []

    def _fake_close(fd):
        closes.append(fd)
        if len(closes) == 1:
            raise OSError(_errno.EINTR, "EINTR")

    with m.patch.object(physical_import.os, "open", side_effect=[200]), \
            m.patch.object(physical_import.os, "close", side_effect=_fake_close):
        result = physical_import._walk_components_from_fd(100, ("sub",))
    assert result == 200, result
    assert closes == [100], ("double-close/leak", closes)


def test_c53_finally_closes_both_fds_when_first_close_raises(
    tmp_path, monkeypatch
) -> None:
    """F#6 (Sol C52): `finally` zamyka OBA fd niezależnie mimo wyjątku close.

    Dla udanego load-a non-package wymuszamy `EINTR` przy zamykaniu
    `source_fd`; drugi deskryptor (`source_dir_fd`) MUSI i tak zostać zamknięty,
    a load NIE może wypropagować błędu close (moduł jest poprawnie załadowany).
    RED-first: sekwencyjne `os.close` bez try zostawia drugi fd i propaguje błąd.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "leaf.py").write_text("VAL = 7\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    import errno as _errno
    closed: list = []
    real_close = os.close

    def _flaky_close(fd):
        closed.append(fd)
        # Pierwszy close w `finally` (source_fd) rzuca EINTR; reszta normalnie.
        if len(closed) == 1:
            raise OSError(_errno.EINTR, "EINTR")
        return real_close(fd)

    name = "_c53_finally"
    try:
        monkeypatch.setattr(physical_import.os, "close", _flaky_close)
        mod = physical_import.load_physical_scripts_sibling(
            name, "leaf.py", required=False
        )
        # load NIE wypropagował błędu close; moduł załadowany i opublikowany:
        assert mod is not None and mod.VAL == 7
        assert name in sys.modules
        # OBA deskryptory close'owane (≥2 wywołania: source_fd + source_dir_fd):
        assert len(closed) >= 2, ("drugi fd nie zamknięty", closed)
    finally:
        monkeypatch.undo()
        _c49_purge_sys_modules(name)


def test_c53_reregistration_retires_stale_route_no_leak(
    tmp_path, monkeypatch
) -> None:
    """F#3 (Sol C52): re-registration retiruje starą trasę (brak leak fd/desc).

    Ścieżka re-registration: po loadzie pakietu+potomka usuwamy root TYLKO
    z `sys.modules` i ładujemy ponownie. Stary wpis descendant i stary
    `_RetainedDirFd` MUSZĄ zniknąć (brak wycieku fd). RED-first: bez
    re-registration retire stary wpis potomka i stary fd zostają.
    """
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    (legacy / "child.py").write_text("C = 1\n", encoding="utf-8")
    root = "_c53_rereg"
    fd_dir = "/proc/self/fd"
    if not os.path.isdir(fd_dir):
        pytest.skip("brak /proc/self/fd")

    def fdcount():
        return len(os.listdir(fd_dir))

    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        importlib.import_module(root + ".child")
        assert root + ".child" in physical_import._PHYSICAL_DESCENDANT_MODULES
        base_fd = fdcount()
        # RE-REGISTRATION: usuń root TYLKO z sys.modules (nie retire), przeładuj:
        for n in tuple(sys.modules):
            if n == root or n.startswith(root + "."):
                sys.modules.pop(n, None)
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        # Stary wpis potomka usunięty (nie ma duplikatu ze starej generacji),
        # a liczba fd nie wzrosła (stary retained fd zamknięty przy re-reg):
        assert fdcount() <= base_fd, ("stary fd przeciekł", fdcount(), base_fd)
        importlib.import_module(root + ".child")  # nowa generacja działa
    finally:
        physical_import._retire_package_route(root)
        _c49_purge_sys_modules(root)


def test_c53_rollback_generation_safe(tmp_path, monkeypatch) -> None:
    """F#2 (Sol C52): rollback wiąże generację — nie usuwa cudzej.

    Sekwencja Sola: gen1 wstrzymana w `exec`; gen1 zostaje KANONICZNIE
    wycofana (external retire) pod lockiem; gen2 tej samej nazwy ładuje się
    i kończy; gdy gen1 następnie rzuci, jej rollback po samej nazwie NIE może
    usunąć gen2 (nowszej, poprawnie zarejestrowanej). RED-first: rollback bez
    sprawdzenia tożsamości generacji usuwa gen2 ze wszystkich rejestrów.
    """
    import threading
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    root = "_c53_gen"
    boom = scripts / "boom_pkg"
    boom.mkdir()
    entered = threading.Event()
    release = threading.Event()
    (boom / "__init__.py").write_text(
        "import _c53_gen_sync as s\n"
        "s.entered.set()\n"
        "s.release.wait(5)\n"
        "raise RuntimeError('gen1 boom')\n",
        encoding="utf-8",
    )
    ok = scripts / "ok_pkg"
    ok.mkdir()
    (ok / "__init__.py").write_text("GEN = 2\n", encoding="utf-8")
    sync = type(sys)("_c53_gen_sync")
    sync.entered = entered
    sync.release = release
    sys.modules["_c53_gen_sync"] = sync
    errors: list = []

    def _gen1():
        try:
            physical_import.load_physical_scripts_sibling(
                root, "boom_pkg", package=True
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_gen1)
    t.start()
    try:
        assert entered.wait(5), "gen1 nie weszła w exec"
        gen1_mod = sys.modules.get(root)
        assert gen1_mod is not None
        # KANONICZNE wycofanie gen1 pod lockiem (gen1 wciąż w exec, poza lockiem):
        with physical_import._PHYSICAL_LOAD_LOCK:
            physical_import._retire_package_route(root)
        # gen2: ta sama nazwa, po zwolnieniu nazwy publikuje świeżą generację:
        gen2 = physical_import.load_physical_scripts_sibling(
            root, "ok_pkg", package=True
        )
        assert gen2.GEN == 2 and gen2 is not gen1_mod
        # teraz pozwól gen1 rzucić — jej rollback MUSI pominąć gen2:
        release.set()
        t.join(5)
        assert errors and isinstance(errors[0], RuntimeError)
        assert sys.modules.get(root) is gen2, "rollback gen1 usunął gen2"
        assert physical_import._PHYSICAL_PACKAGE_ROUTES[root][2] is gen2
        assert physical_import._LOADED_PHYSICAL_SIBLINGS[root][1] is gen2
    finally:
        release.set()
        t.join(5)
        physical_import._retire_package_route(root)
        sys.modules.pop("_c53_gen_sync", None)
        _c49_purge_sys_modules(root)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate54 — findingi świeżego Sol/max na frozen C53 (CONFIRMED_DEFECT):
# exception-safety fd w helperach otwierających, generation-identity po
# owned-registrach, oraz luki lintu (descendant value-read, aliasowany
# route-pop/retire). Fixy U ŹRÓDŁA + RED-first.
# ─────────────────────────────────────────────────────────────────────────────


def test_c54_open_descendant_source_fd_close_exception_safe() -> None:
    """F#4 (Sol C53): `_open_descendant_source_fd` nie osieroca `file_fd`.

    Gdy zamknięcie `source_dir_fd` w `finally` rzuci (EINTR), już otwarty
    `file_fd` NIE może wyciec (błąd close połknięty). RED-first: `os.close`
    w `finally` propagujące błąd zostawia `file_fd` bez właściciela.
    """
    import errno as _errno
    import unittest.mock as m
    from dispatch_v2 import _physical_import as physical_import

    closes: list = []

    def _fc(fd):
        closes.append(fd)
        if fd == 10:
            raise OSError(_errno.EINTR, "EINTR")

    reg = os.stat_result((0o100644, 0, 0, 1, 0, 0, 0, 0, 0, 0))

    class _H:
        fd = 99

    with m.patch.object(physical_import, "_dup_retained_under_lock",
                        return_value=None), \
            m.patch.object(physical_import, "_walk_components_from_fd",
                           return_value=10), \
            m.patch.object(physical_import.os, "open", return_value=20), \
            m.patch.object(physical_import.os, "fstat", return_value=reg), \
            m.patch.object(physical_import.os, "close", side_effect=_fc):
        result = physical_import._open_descendant_source_fd(
            _H(), ("leaf",), package=False
        )
    # Sukces: file_fd zwrócony, katalog-fd zamkniety (mimo EINTR — połkniety):
    assert result == 20, result
    assert 10 in closes, ("katalog-fd nie zamkniety", closes)


def test_c54_open_physical_source_fd_close_exception_safe() -> None:
    """F#4 (Sol C53): `_open_physical_source_fd` zamyka OBA fd przy błędzie.

    Przy nie-regularnym pliku (fstat) ścieżka sprzątająca zamyka `file_fd` i
    `dir_fd` NIEZALEŻNIE — wyjątek z pierwszego close nie pomija drugiego.
    RED-first: sekwencyjne `os.close` zostawia `dir_fd` gdy pierwszy rzuci.
    """
    import errno as _errno
    import unittest.mock as m
    from dispatch_v2 import _physical_import as physical_import

    closes: list = []

    def _fc(fd):
        closes.append(fd)
        if fd == 20:  # file_fd close rzuca; dir_fd (10) MUSI i tak zostać zamkniety
            raise OSError(_errno.EINTR, "EINTR")

    not_reg = os.stat_result((0o040755, 0, 0, 1, 0, 0, 0, 0, 0, 0))  # katalog

    with m.patch.object(physical_import, "_walk_directory_descriptor",
                        return_value=10), \
            m.patch.object(physical_import.os, "open", return_value=20), \
            m.patch.object(physical_import.os, "fstat", return_value=not_reg), \
            m.patch.object(physical_import.os, "close", side_effect=_fc):
        with pytest.raises(RuntimeError, match="not a regular file"):
            physical_import._open_physical_source_fd(
                Path("/x"), Path("leaf.py"), package=False
            )
    assert 10 in closes and 20 in closes, ("nie zamknieto obu fd", closes)


_C54_LINT_BYPASS_MUTANTS = {
    # #5: naturalny fast-path przez rejestr potomków = flagowany.
    "descendant_registry_get": (
        "                    preloaded = _adopt_or_reject_preloaded(module_name, source)",
        "                    _hit = _PHYSICAL_DESCENDANT_MODULES.get(module_name)\n"
        "                    if _hit is not None and not required:\n"
        "                        return _hit\n"
        "                    preloaded = _adopt_or_reject_preloaded(module_name, source)",
        "ownership",
        "descendant registry read outside finder/purge",
    ),
    # #6a: aliasowany route-pop w retire = flagowany (alias-aware).
    "aliased_route_pop": (
        "    route = _PHYSICAL_PACKAGE_ROUTES.pop(module_name, None)",
        "    _r = _PHYSICAL_PACKAGE_ROUTES\n"
        "    _r.pop('rogue', None)\n"
        "    route = _PHYSICAL_PACKAGE_ROUTES.pop(module_name, None)",
        "retirement",
        "route mutation must be exactly one",
    ),
    # #6b: aliasowany, dodatkowy retire w loaderze = flagowany.
    "aliased_extra_retire": (
        "            exec(code, module.__dict__)",
        "            _retire_alias = _retire_package_route\n"
        "            _retire_alias(module_name)\n"
        "            exec(code, module.__dict__)",
        "retirement",
        "referenced as a value",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C54_LINT_BYPASS_MUTANTS))
def test_c54_ratchet_flags_lint_bypasses(mutant) -> None:
    """F#5/#6 (Sol C53): domknięte NATURALNE luki lintu (descendant value-read,
    aliasowany route-pop/retire). Każdy mutant FLAGGED; żywy plik CLEAN."""
    anchor, replacement, checker, expected = _C54_LINT_BYPASS_MUTANTS[mutant]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    assert anchor in real, mutant
    mutated = real.replace(anchor, replacement, 1)
    assert mutated != real
    tree = ast.parse(mutated)
    if checker == "ownership":
        violations = _physical_import_ownership_violations(tree)
    else:
        violations = _retirement_contract_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate55 — findingi świeżego Sol/max na frozen C54 (CONFIRMED_DEFECT):
# finalizer pod lockiem, transient fd, descendant-generation, alias-completeness,
# oraz KOREKTA dyscypliny RED-first (mój oracle C54 generation nie był
# mutation-sensitive). Fixy U ŹRÓDŁA + RED-first.
# ─────────────────────────────────────────────────────────────────────────────


def test_c55_module_del_not_under_load_lock(tmp_path, monkeypatch) -> None:
    """F#1 (Sol C54): `__del__` usuwanego modułu odpala POZA `_PHYSICAL_LOAD_LOCK`.

    Retire/purge zwracają usuwane obiekty; caller deref'uje je dopiero po
    wyjściu z sekcji krytycznej. Root z globalnym obiektem, którego `__del__`
    re-entruje loader i sprawdza `lock.locked()` — MUSI zobaczyć lock ZWOLNIONY
    (inaczej deadlock/re-entrancy). RED-first: bez deferred-deref finalizer widzi
    lock zajęty.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text(
        "import _c55_delsync as s\n"
        "class _G:\n"
        "    def __del__(self):\n"
        "        s.locked.append(s.lock.locked())\n"
        "_g = _G()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    sync = type(sys)("_c55_delsync")
    sync.locked = []
    sync.lock = physical_import._PHYSICAL_LOAD_LOCK
    sys.modules["_c55_delsync"] = sync
    root = "_c55_delroot"
    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        # Zewnętrzny retire pod lockiem; route[2] to ostatnia ref do modułu.
        _doomed = None
        with physical_import._PHYSICAL_LOAD_LOCK:
            _doomed = physical_import._retire_package_route(root)
        # deref POZA lockiem:
        del _doomed
        import gc
        gc.collect()
        assert sync.locked, "__del__ nie odpalił (test nieważny)"
        assert not any(sync.locked), (
            "__del__ odpalił POD lockiem (re-entrancy/deadlock)", sync.locked
        )
    finally:
        sys.modules.pop("_c55_delsync", None)
        _c49_purge_sys_modules(root)


def test_c55_pending_handle_closed_deterministically(
    tmp_path, monkeypatch
) -> None:
    """F#2 (Sol C54): handle zamknięty NATYCHMIAST gdy zapis trasy padnie.

    Jeśli budowa krotki/zapis trasy padnie PO utworzeniu handle, rollback
    zamyka fd deterministycznie (nie czeka na finalizer GC). RED-first: bez
    `_pending_handle` fd zostaje otwarty do GC (fd_delta > 0 zaraz po wyjątku).
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )
    fd_dir = "/proc/self/fd"
    if not os.path.isdir(fd_dir):
        pytest.skip("brak /proc/self/fd")

    def fdcount():
        return len(os.listdir(fd_dir))

    # Mapa tras, której __setitem__ rzuca PO utworzeniu handle:
    class _BoomRoutes(dict):
        def __setitem__(self, k, v):
            raise MemoryError("route write boom")

    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", _BoomRoutes(),
        raising=False,
    )
    root = "_c55_pending"
    base = fdcount()
    held_exc = None
    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
    except MemoryError as exc:
        held_exc = exc  # TRZYMAMY wyjątek → traceback → ramkę → handle local
    assert held_exc is not None
    # Handle jest utrzymywany żywy przez traceback, więc finalizer GC NIE
    # zamknie fd; deterministyczne zamknięcie w rollbacku (`_pending_handle`)
    # jest JEDYNYM, które je zamyka. RED-first: bez fixu fd zostaje otwarty.
    assert fdcount() == base, (
        "handle fd nie zamknięty deterministycznie (przeciek do GC)",
        fdcount(), base,
    )
    del held_exc
    _c49_purge_sys_modules(root)


def test_c55_descendant_generation_stale_write_blocked(
    tmp_path, monkeypatch
) -> None:
    """F#3 (Sol C54): stary exec_module nie zapisuje potomka do nowej generacji.

    Wątek importujący `root.child` wstrzymany w `_read_all_from_fd`; drugi
    kanonicznie retiruje root i ładuje gen2 (nowy handle). Po zwolnieniu stary
    `exec_module` (handle gen1) MUSI paść fail-closed (`ModuleNotFoundError`)
    zamiast zapisać potomka gen1 do rejestru gen2. RED-first: bez
    generation-check stary zapis zanieczyszcza rejestr gen2.
    """
    import threading
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    (legacy / "child.py").write_text("C = 1\n", encoding="utf-8")
    root = "_c55_descgen"
    entered = threading.Event()
    release = threading.Event()
    orig_read = physical_import._read_all_from_fd

    def _paused_read(fd):
        entered.set()
        release.wait(5)
        return orig_read(fd)

    result: dict = {}
    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        gen1_handle = physical_import._PHYSICAL_PACKAGE_ROUTES[root][3]

        def _import_child():
            monkeypatch.setattr(
                physical_import, "_read_all_from_fd", _paused_read
            )
            try:
                importlib.import_module(root + ".child")
                result["child"] = "loaded"
            except BaseException as exc:  # noqa: BLE001
                result["child"] = type(exc).__name__

        t = threading.Thread(target=_import_child)
        t.start()
        assert entered.wait(5), "import potomka nie wszedł w read"
        # kanoniczny retire gen1 + gen2:
        with physical_import._PHYSICAL_LOAD_LOCK:
            _dd = physical_import._retire_package_route(root)
        del _dd
        monkeypatch.setattr(physical_import, "_read_all_from_fd", orig_read)
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        gen2_handle = physical_import._PHYSICAL_PACKAGE_ROUTES[root][3]
        assert gen2_handle is not gen1_handle
        release.set()
        t.join(5)
        # stary exec_module (handle gen1) NIE zapisał potomka do gen2:
        assert result.get("child") == "ModuleNotFoundError", result
        assert root + ".child" not in physical_import._PHYSICAL_DESCENDANT_MODULES
    finally:
        release.set()
        physical_import._retire_package_route(root)
        _c49_purge_sys_modules(root)


def test_c55_rollback_generation_cleans_when_removed_from_sys_modules(
    tmp_path, monkeypatch
) -> None:
    """F#5 (Sol C54): mutation-sensitive oracle dla generation OR.

    Scenariusz który C53 (sys.modules-only) GUBIŁ: po publikacji root usunięto
    z SAMEGO `sys.modules` (trasa/fd/owner nadal nasze), potem exec pada.
    Rollback MUSI posprzątać trasę/fd/owner (nasza generacja wciąż włada nimi).
    RED-first: `_current_generation_is` sprawdzające tylko `sys.modules` zwraca
    False (root nieobecny) → skip → trasa/fd/owner PRZECIEKAJĄ.
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    root = "_c55_genor"
    fd_dir = "/proc/self/fd"
    if not os.path.isdir(fd_dir):
        pytest.skip("brak /proc/self/fd")

    def fdcount():
        return len(os.listdir(fd_dir))

    # __init__ usuwa root z sys.modules a POTEM rzuca — symuluje „zniknięcie
    # z sys.modules przy zablokowanym, następnie failing exec":
    (legacy / "__init__.py").write_text(
        "import sys\n"
        f"sys.modules.pop({root!r}, None)\n"
        "raise RuntimeError('gone then boom')\n",
        encoding="utf-8",
    )
    base = fdcount()
    with pytest.raises(RuntimeError, match="gone then boom"):
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
    # Rollback posprzątał WSZYSTKO mimo nieobecności w sys.modules:
    assert root not in physical_import._PHYSICAL_PACKAGE_ROUTES, "trasa przeciekła"
    assert root not in physical_import._LOADED_PHYSICAL_SIBLINGS, "owner przeciekł"
    assert fdcount() == base, ("retained fd przeciekł", fdcount(), base)
    _c49_purge_sys_modules(root)


_C55_ALIAS_FORMS = {
    "destructuring": (
        "            exec(code, module.__dict__)",
        "            (_ra,) = (_retire_package_route,)\n"
        "            _ra(module_name)\n"
        "            exec(code, module.__dict__)",
    ),
    "walrus_call": (
        "            exec(code, module.__dict__)",
        "            (_rb := _retire_package_route)(module_name)\n"
        "            exec(code, module.__dict__)",
    ),
    "annotated": (
        "            exec(code, module.__dict__)",
        "            _rc: object = _retire_package_route\n"
        "            _rc(module_name)\n"
        "            exec(code, module.__dict__)",
    ),
}


@pytest.mark.parametrize("form", sorted(_C55_ALIAS_FORMS))
def test_c55_retire_alias_resolver_covers_natural_forms(form) -> None:
    """F#4 (Sol C54)/#3 (Sol C55): aliasowany retire (destrukturyzacja/walrus/
    adnotowane) = FLAGGED. Candidate56 wykrywa KAŻDY alias jako value-reference
    `_retire_package_route` poza bezpośrednim wywołaniem."""
    anchor, replacement = _C55_ALIAS_FORMS[form]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    assert anchor in real
    mutated = real.replace(anchor, replacement, 1)
    assert mutated != real
    violations = _retirement_contract_violations(ast.parse(mutated))
    assert any(
        "referenced as a value" in v for v in violations
    ), (form, violations)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate56 — findingi świeżego Sol/max na frozen C55 (CONFIRMED_DEFECT):
# cykliczny GC pod lockiem, błąd close pending_handle przerywa rollback, alias
# wywoływalnej wartości, manifesty bez wyników. Fixy U ŹRÓDŁA + RED-first.
# ─────────────────────────────────────────────────────────────────────────────


def test_c56_gc_disabled_inside_load_lock() -> None:
    """F#1 (Sol C55): automatyczny GC jest WYŁĄCZONY na czas trzymania locka.

    Alokacje pod lockiem (`tuple(sys.modules)` itd.) NIE mogą wyzwolić
    cyklicznego GC (a ten uruchamia dowolne `__del__` = kod użytkownika →
    re-entrancja/deadlock). Lock pauzuje GC i przywraca stan na wyjściu.
    RED-first: zwykły `threading.Lock` zostawia GC włączony pod lockiem.
    """
    import gc
    from dispatch_v2 import _physical_import as physical_import

    was = gc.isenabled()
    try:
        if not was:
            gc.enable()
        assert gc.isenabled()
        with physical_import._PHYSICAL_LOAD_LOCK:
            assert not gc.isenabled(), "GC NIE wyłączony pod lockiem"
        assert gc.isenabled(), "GC nie przywrócony po locku"
    finally:
        if was:
            gc.enable()
        else:
            gc.disable()


def test_c56_pending_handle_close_error_does_not_skip_rollback(
    tmp_path, monkeypatch
) -> None:
    """F#2 (Sol C55): błąd `_pending_handle.close()` NIE pomija rollbacku.

    Gdy zapis trasy padnie PO utworzeniu handle, a `close` handle rzuci (EINTR),
    rollback MUSI mimo to usunąć częściowo opublikowany moduł z `sys.modules`
    i zgłosić PIERWOTNY wyjątek (nie błąd close). RED-first: zamknięcie handle
    przed rollbackiem (bez połknięcia) przerywa handler → root zostaje.
    """
    import dispatch_v2
    from dispatch_v2 import _physical_import as physical_import

    scripts = tmp_path / "scripts"
    pkg = scripts / "dispatch_v2"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    legacy = scripts / "legacy_pkg"
    legacy.mkdir()
    (legacy / "__init__.py").write_text("R = 1\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_v2, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(physical_import, "_LOADED_PHYSICAL_SIBLINGS", {})
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_DESCENDANT_MODULES", {}, raising=False
    )

    class _BoomRoutes(dict):
        def __setitem__(self, k, v):
            raise MemoryError("route write boom")

    monkeypatch.setattr(
        physical_import, "_PHYSICAL_PACKAGE_ROUTES", _BoomRoutes(),
        raising=False,
    )

    # ŻYWA ścieżka zamyka handle przez `_close_fd_quietly` → `os.close`
    # (Candidate57/Sol C56 #5: instrumentujemy REALNY call-site, nie metodę
    # `.close`, której żywy rollback już nie woła). `os.close` rzuca EINTR;
    # `_close_fd_quietly` MUSI to połknąć, a rollback i tak się wykonać.
    import errno as _errno
    real_close = os.close

    def _bad_close(fd):
        raise OSError(_errno.EINTR, "EINTR")

    monkeypatch.setattr(physical_import.os, "close", _bad_close)
    root = "_c56_prh"
    # PIERWOTNY wyjątek (MemoryError z zapisu trasy), NIE błąd close:
    with pytest.raises(MemoryError, match="route write boom"):
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
    # rollback wykonany mimo błędu close — brak częściowo opublikowanego modułu:
    assert root not in sys.modules, "rollback pominięty (root został)"
    _c49_purge_sys_modules(root)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate57 — findingi świeżego Sol/max na frozen C56 (CONFIRMED_DEFECT):
# exec_module rollback nie generation-safe, rebinding nazwy retire, luki lintu
# (__setitem__/__delitem__/AugAssign), oraz KOREKTA złej instrumentacji oracle
# close (#5). Fixy U ŹRÓDŁA + RED-first.
# ─────────────────────────────────────────────────────────────────────────────


def test_c57_exec_module_rollback_is_generation_safe(tmp_path, monkeypatch):
    """F#2 (Sol C56): rollback exec_module potomka NIE usuwa cudzej generacji.

    DETERMINISTYCZNY, JEDNOWĄTKOWY oracle (poprzednik wątkowy serializował się
    na per-module import locku CPythona → nie był RED-first). Scenariusz:

    1. gen1 (root+child) załadowany; `find_spec(root.child)` zwraca STALE loader
       związany z retained handle gen1. Bytes childa (`__init__.py`) wywołują
       hook, a potem RZUCAJĄ — więc gen1 exec_module wejdzie w rollback.
    2. exec_module gen1 uruchamiany RĘCZNIE. Write-check (przed exec) widzi gen1
       jako AKTUALNĄ trasę roota → zapisuje `_PHYSICAL_DESCENDANT_MODULES[root.child]`
       = obiekt gen1 i wchodzi w `exec(code, ...)`.
    3. `exec` uruchamia bytes childa → `hook.flip()` DETERMINISTYCZNIE (ten sam
       wątek) retiruje gen1, ładuje gen2 (świeży handle) i rejestruje gen2_child w
       `_PHYSICAL_DESCENDANT_MODULES[root.child]`. Potem child RZUCA → rollback.
    4. W chwili rollbacku trasa roota trzyma już handle gen2, więc nasz retained
       handle gen1 jest NIEAKTUALNY (`_descendant_generation_current` == False):
       rollback MUSI zostać POMINIĘTY, a wpis gen2 NIENARUSZONY.

    Instrumentuje REALNĄ ścieżkę rollbacku exec_module (bez monkeypatcha
    generacji): write-check przechodzi na PRAWDZIWEJ aktualnej trasie gen1, a
    zmiana generacji na gen2 zachodzi w PRAWDZIWYM `_retire_package_route` +
    `load_physical_scripts_sibling` wywołanych z wnętrza exec.

    RED-first: bezwarunkowy purge po nazwie (odwrócony fix) usuwa wpis gen2.
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    # child __init__ (gen1): woła hook (retire gen1 + load gen2 + rejestracja
    # gen2_child) i dopiero POTEM pada — żeby wejść w rollback exec_module gdy
    # trasa roota trzyma już handle gen2:
    (legacy / "child").mkdir()
    (legacy / "child" / "__init__.py").write_text(
        "import _c57_hook as h\nh.flip()\nraise RuntimeError('child boom')\n",
        encoding="utf-8",
    )
    root = "_c57_es"
    state: dict = {}

    def _flip() -> None:
        # DETERMINISTYCZNA zmiana generacji WEWNĄTRZ exec gen1 (ten sam wątek):
        # retire gen1 (lock brany i zwalniany PRZED loadem gen2 — lock jest
        # nie-reentrantny), potem load gen2 i rejestracja gen2_child.
        with physical_import._PHYSICAL_LOAD_LOCK:
            _dd = physical_import._retire_package_route(root)
        del _dd[:]
        (legacy / "child" / "__init__.py").write_text(
            "OK = 1\n", encoding="utf-8"
        )
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        state["gen2_child"] = importlib.import_module(root + ".child")

    hook = type(sys)("_c57_hook")
    hook.flip = _flip
    sys.modules["_c57_hook"] = hook
    try:
        # gen1: root + STALE loader potomka (retained handle gen1), BEZ exec:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        stale_spec = physical_import._DESCENDANT_FINDER.find_spec(root + ".child")
        assert stale_spec is not None
        stale_loader = stale_spec.loader
        gen1_module = importlib.util.module_from_spec(stale_spec)
        # exec_module RĘCZNIE: write-check (gen1 aktualne) → exec → flip()
        # przełącza na gen2 → child rzuca → rollback POMINIĘTY (handle gen1 stale).
        with pytest.raises(RuntimeError, match="child boom"):
            stale_loader.exec_module(gen1_module)
        gen2_child = state["gen2_child"]
        # rollback gen1 pominięty → wpis gen2 w OWNED rejestrze NIENARUSZONY:
        assert (
            physical_import._PHYSICAL_DESCENDANT_MODULES.get(root + ".child")
            is gen2_child
        ), "rollback gen1 usunął potomka gen2 (exec_module nie generation-safe)"
    finally:
        physical_import._retire_package_route(root)
        sys.modules.pop("_c57_hook", None)
        _c49_purge_sys_modules(root)


_C57_RETIRE_REBIND_MUTANTS = {
    "rebind_to_benign": (
        "            exec(code, module.__dict__)",
        "            _retire_package_route = print\n"
        "            exec(code, module.__dict__)",
    ),
    "rebind_to_route_pop": (
        "            exec(code, module.__dict__)",
        "            _retire_package_route = _PHYSICAL_PACKAGE_ROUTES.pop\n"
        "            _retire_package_route('rogue', None)\n"
        "            exec(code, module.__dict__)",
    ),
    "nested_def_shadow": (
        "            exec(code, module.__dict__)",
        "            def _retire_package_route(n):\n"
        "                pass\n"
        "            _retire_package_route(module_name)\n"
        "            exec(code, module.__dict__)",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C57_RETIRE_REBIND_MUTANTS))
def test_c57_retire_name_cannot_be_rebound_or_shadowed(mutant):
    """F#3 (Sol C56): nazwa `_retire_package_route` = JEDEN module-level def,
    nigdy rebindowana/przesłaniana. Każdy rebind/shadow = FLAGGED."""
    anchor, replacement = _C57_RETIRE_REBIND_MUTANTS[mutant]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    assert anchor in real
    mutated = real.replace(anchor, replacement, 1)
    assert mutated != real
    violations = _retirement_contract_violations(ast.parse(mutated))
    assert any(v for v in violations), (mutant, violations)


_C57_DUNDER_WRITER_MUTANTS = {
    "registry_setitem": (
        "            exec(code, module.__dict__)",
        "            _LOADED_PHYSICAL_SIBLINGS.__setitem__('rogue', ('x', None))\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_augassign": (
        # PRAWDZIWY węzeł ast.AugAssign (`X |= {}`) — RED-first WYŁĄCZNIE przez
        # `visit_AugAssign` (Candidate59/Sol C58 #6c). Poprzednik używał
        # `X['x'] = X.get('x')` (zwykły Subscript-Store), więc czerwienił też
        # bez `visit_AugAssign` = FAŁSZYWY oracle. `|= {}` nie jest ani
        # Subscript-Store, ani Name-Assign, więc jedynym łapaczem jest AugAssign.
        "            exec(code, module.__dict__)",
        "            _LOADED_PHYSICAL_SIBLINGS |= {'rogue': ('x', module)}\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "registry writers must be exactly one",
    ),
    "descendant_setitem": (
        "            exec(code, module.__dict__)",
        "            _PHYSICAL_DESCENDANT_MODULES.__setitem__('rogue', module)\n"
        "            exec(code, module.__dict__)",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "routes_setitem": (
        "            exec(code, module.__dict__)",
        "            _PHYSICAL_PACKAGE_ROUTES.__setitem__('rogue', ('a','b',module,None))\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "route writers must be exactly one",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C57_DUNDER_WRITER_MUTANTS))
def test_c57_dunder_and_augassign_writers_flagged(mutant):
    """F#4 (Sol C56): konkurencyjny writer przez `__setitem__`/`__delitem__`/
    AugAssign na rejestrze/trasie/descendant = FLAGGED (nie tylko Subscript)."""
    anchor, replacement, checker, expected = _C57_DUNDER_WRITER_MUTANTS[mutant]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    assert anchor in real, mutant
    mutated = real.replace(anchor, replacement, 1)
    assert mutated != real
    tree = ast.parse(mutated)
    if checker == "ownership":
        violations = _physical_import_ownership_violations(tree)
    else:
        violations = _retirement_contract_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate58 — świeży blind Sol/max na frozen C57 (CONFIRMED_DEFECT): structural
# ratchet C57 łapał BEZPOŚREDNI `X.__setitem__`/`__delitem__` (check na
# `func.attr`), ale NIE łapał ALIASU bound-method zapisu — `rw = X.__setitem__;
# rw(k,v)` (i tuple/walrus/annotated/łańcuch) omijał licznik writerów. ROOT-FIX
# u źródła: `_ownership_kind_of` klasyfikuje bound-method chronionego dundera jako
# odrębny kind "write-bound:<base>", więc kanoniczny fixpoint aliasów (C43)
# rozwiązuje NAZWĘ-alias tak samo jak każdy inny alias; oba konsumenty
# (`_physical_import_ownership_violations` registry+routes, `_retirement_
# contract_violations` descendants+sys.modules) liczą wywołanie takiej nazwy jako
# ZAPIS — JEDNA ścieżka pokrywa formę bezpośrednią i aliasowaną. Dodatkowo Visitor
# ownershipu dostał scope KWALIFIKOWANY (Klasa.metoda), spójny z resolverem
# aliasów — inaczej alias write-bound w metodzie klasy (exec_module) był mijany.
# GRANICA (jak C43/#3): domykamy WYŁĄCZNIE formę NAZWA-alias; `getattr(X,
# '__setitem__')`/`exec`/refleksja są statycznie nierozstrzygalne — SOUND
# gwarancją pozostaje behawioralny backstop (runtime owner raises, C52).
# ─────────────────────────────────────────────────────────────────────────────


_C58_WRITE_BOUND_ALIAS_MUTANTS = {
    # (anchor, replacement, checker, expected_substring)
    "registry_setitem_name_alias": (
        "            exec(code, module.__dict__)",
        "            _rw = _LOADED_PHYSICAL_SIBLINGS.__setitem__\n"
        "            _rw('rogue', ('x', module))\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_delitem_name_alias": (
        "            exec(code, module.__dict__)",
        "            _rd = _LOADED_PHYSICAL_SIBLINGS.__delitem__\n"
        "            _rd('rogue')\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_tuple_alias": (
        "            exec(code, module.__dict__)",
        "            _rw, _z = _LOADED_PHYSICAL_SIBLINGS.__setitem__, None\n"
        "            _rw('rogue', ('x', module))\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_walrus_alias": (
        "            exec(code, module.__dict__)",
        "            (_rw := _LOADED_PHYSICAL_SIBLINGS.__setitem__)"
        "('rogue', ('x', module))\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_chained_alias": (
        "            exec(code, module.__dict__)",
        "            _a = _LOADED_PHYSICAL_SIBLINGS.__setitem__\n"
        "            _b = _a\n"
        "            _b('rogue', ('x', module))\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "registry writers must be exactly one",
    ),
    "routes_setitem_name_alias": (
        "            exec(code, module.__dict__)",
        "            _rw = _PHYSICAL_PACKAGE_ROUTES.__setitem__\n"
        "            _rw('rogue', ('a', 'b', module, None))\n"
        "            exec(code, module.__dict__)",
        "ownership",
        "route writers must be exactly one",
    ),
    "descend_setitem_name_alias": (
        "            exec(code, module.__dict__)",
        "            _dw = _PHYSICAL_DESCENDANT_MODULES.__setitem__\n"
        "            _dw('rogue', module)\n"
        "            exec(code, module.__dict__)",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descend_delitem_name_alias": (
        "            exec(code, module.__dict__)",
        "            _dd = _PHYSICAL_DESCENDANT_MODULES.__delitem__\n"
        "            _dd('rogue')\n"
        "            exec(code, module.__dict__)",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descend_walrus_alias": (
        "            exec(code, module.__dict__)",
        "            (_dw := _PHYSICAL_DESCENDANT_MODULES.__setitem__)"
        "('rogue', module)\n"
        "            exec(code, module.__dict__)",
        "retirement",
        "descendant registry write must be exactly one",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C58_WRITE_BOUND_ALIAS_MUTANTS))
def test_c58_write_bound_dunder_alias_writers_flagged(mutant):
    """Candidate58 (blind Sol/max na C57): ZAPIS przez ALIAS bound-method
    chronionego dundera (`name = X.__setitem__/__delitem__; name(...)`, w tym
    tuple/walrus/łańcuch) = FLAGGED tak samo jak forma bezpośrednia.

    Oracle luki C57: detekcja dundera była BEZPOŚREDNIM checkiem `func.attr` bez
    integracji z fixpointem aliasów (C43), więc przypisanie bound-method do nazwy
    i wywołanie tej nazwy omijało licznik writerów rejestru/tras/descendants.
    """
    anchor, replacement, checker, expected = _C58_WRITE_BOUND_ALIAS_MUTANTS[
        mutant
    ]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    assert anchor in real, mutant
    mutated = real.replace(anchor, replacement, 1)
    assert mutated != real
    tree = ast.parse(mutated)
    if checker == "ownership":
        violations = _physical_import_ownership_violations(tree)
    else:
        violations = _retirement_contract_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


def test_c58_write_bound_alias_ratchets_clean_on_real_file() -> None:
    """Kontrapunkt: rozszerzenie write-bound + scope kwalifikowany Visitora NIE
    fałszuje żywego loadera na żadnym z dwóch ratchetów."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []
    assert _retirement_contract_violations(tree) == []


# ─────────────────────────────────────────────────────────────────────────────
# Candidate59 — świeży blind Sol/max na frozen C58 (4 CONFIRMED_DEFECT). ROOT-FIX
# u źródła, spójny z kanonicznym resolverem aliasów (bez drugiej maszynerii):
#   #4 fixpoint aliasów write-bound NIE przekraczał GRANIC scope/parametrów:
#      (a) zagnieżdżony `def` nie dziedziczył aliasu z otaczającego `def`
#          (lookup pytał tylko o scope bieżący + `<module>`) — `_lookup_scoped_alias`
#          wędruje teraz po całym łańcuchu scope'ów;
#      (b) parametr DOMYŚLNY `def f(rw=X.__setitem__)` nie był wiązany —
#          `_bind_param_defaults` liczy default w scope otaczającym, wiąże param
#          w scope ciała;
#      (c) ARGUMENT przekazany do wywołania funkcji lokalnej `f(X.__setitem__)`
#          nie propagował na parametr — `visit_Call` mapuje argumenty na parametry.
#   #5 owner-reuse `_adopt_or_reject_preloaded` był rebindowalny: zagnieżdżony
#      `def _adopt_or_reject_preloaded(...): return None` przechwytywał kanoniczny
#      owner (licznik wywołań nie widział podmiany). Kontrakt „JEDEN module-level
#      def, nigdy rebind/shadow/callable-alias" — ten sam `_name_binding_contract`
#      co `_retire_package_route`.
#   #6 rebind CAŁEGO obiektu mapy (`X = {}` / `X: T = {}` w scope nie-modułowym,
#      też z `global`) był mijany (Visitor liczył tylko Subscript/dunder), a
#      `visit_AugAssign` pokrywał descendants tylko w ownershipie — retirement nie
#      liczył AugAssign descendants/routes/sys.modules wcale. Domknięte: whole-
#      object rebind (registry/routes/descendants) + AugAssign we WSZYSTKICH mapach.
# GRANICA (jak C43/C58): domykamy formy NAZWA-alias; getattr/exec/refleksja są
# statycznie nierozstrzygalne — SOUND gwarancją pozostaje behawioralny backstop.
# ─────────────────────────────────────────────────────────────────────────────


_EXEC_ANCHOR = "            exec(code, module.__dict__)"


def _mutate_before_exec(replacement_body: str) -> str:
    """Wstaw `replacement_body` (linie z wcięciem 12 spacji) TUŻ PRZED
    `exec(code, module.__dict__)` w exec_module żywego loadera."""
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    assert _EXEC_ANCHOR in real
    mutated = real.replace(_EXEC_ANCHOR, replacement_body + _EXEC_ANCHOR, 1)
    assert mutated != real
    return mutated


# #4: alias write-bound chronionego dundera propagowany przez GRANICĘ
# scope/parametru. Formy: dziedziczenie scope, parametr domyślny, argument.
_C59_ALIAS_BOUNDARY_MUTANTS = {
    # ---- registry ----
    "registry_scope_inherited": (
        "            _rw = _LOADED_PHYSICAL_SIBLINGS.__setitem__\n"
        "            def _rogue():\n"
        "                _rw('rogue', ('x', module))\n"
        "            _rogue()\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_default_param": (
        "            def _rogue(_rw=_LOADED_PHYSICAL_SIBLINGS.__setitem__):\n"
        "                _rw('rogue', ('x', module))\n"
        "            _rogue()\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_arg_passed": (
        "            def _rogue(_rw):\n"
        "                _rw('rogue', ('x', module))\n"
        "            _rogue(_LOADED_PHYSICAL_SIBLINGS.__setitem__)\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_arg_kw": (
        "            def _rogue(_rw):\n"
        "                _rw('rogue', ('x', module))\n"
        "            _rogue(_rw=_LOADED_PHYSICAL_SIBLINGS.__setitem__)\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_delitem_scope_inherited": (
        "            _rd = _LOADED_PHYSICAL_SIBLINGS.__delitem__\n"
        "            def _rogue():\n"
        "                _rd('rogue')\n"
        "            _rogue()\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    # ---- routes ----
    "routes_scope_inherited": (
        "            _rw = _PHYSICAL_PACKAGE_ROUTES.__setitem__\n"
        "            def _rogue():\n"
        "                _rw('rogue', ('a', 'b', module, None))\n"
        "            _rogue()\n",
        "ownership",
        "route writers must be exactly one",
    ),
    "routes_default_param": (
        "            def _rogue(_rw=_PHYSICAL_PACKAGE_ROUTES.__setitem__):\n"
        "                _rw('rogue', ('a', 'b', module, None))\n"
        "            _rogue()\n",
        "ownership",
        "route writers must be exactly one",
    ),
    "routes_arg_passed": (
        "            def _rogue(_rw):\n"
        "                _rw('rogue', ('a', 'b', module, None))\n"
        "            _rogue(_PHYSICAL_PACKAGE_ROUTES.__setitem__)\n",
        "ownership",
        "route writers must be exactly one",
    ),
    # ---- descendants (checker retirement) ----
    "descend_scope_inherited": (
        "            _dw = _PHYSICAL_DESCENDANT_MODULES.__setitem__\n"
        "            def _rogue():\n"
        "                _dw('rogue', module)\n"
        "            _rogue()\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descend_default_param": (
        "            def _rogue(_dw=_PHYSICAL_DESCENDANT_MODULES.__setitem__):\n"
        "                _dw('rogue', module)\n"
        "            _rogue()\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descend_arg_passed": (
        "            def _rogue(_dw):\n"
        "                _dw('rogue', module)\n"
        "            _rogue(_PHYSICAL_DESCENDANT_MODULES.__setitem__)\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descend_chained_scope": (
        "            _a = _PHYSICAL_DESCENDANT_MODULES.__setitem__\n"
        "            _b = _a\n"
        "            def _rogue():\n"
        "                _b('rogue', module)\n"
        "            _rogue()\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C59_ALIAS_BOUNDARY_MUTANTS))
def test_c59_write_bound_alias_across_param_boundaries_flagged(mutant):
    """Candidate59 #4: alias bound-method chronionego dundera zapisu, przeniesiony
    przez GRANICĘ scope (zagnieżdżony `def`), parametr DOMYŚLNY lub ARGUMENT
    przekazany do wywołania funkcji lokalnej = FLAGGED tak samo jak forma
    bezpośrednia. C58 wiązał aliasy tylko w JEDNYM scope, więc te formy omijały
    licznik writerów rejestru/tras/descendants."""
    replacement, checker, expected = _C59_ALIAS_BOUNDARY_MUTANTS[mutant]
    tree = ast.parse(_mutate_before_exec(replacement))
    if checker == "ownership":
        violations = _physical_import_ownership_violations(tree)
    else:
        violations = _retirement_contract_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


# #5: owner-reuse `_adopt_or_reject_preloaded` = JEDEN module-level def, nigdy
# rebindowany/przesłaniany ani używany jako callable-alias.
_C59_OWNER_REBIND_MUTANTS = {
    "nested_def_shadow": (
        "            def _adopt_or_reject_preloaded(module_name, source):\n"
        "                return None\n",
        "must be a single module-level def",
    ),
    "name_store_rebind": (
        "            _adopt_or_reject_preloaded = None\n",
        "must be a single module-level def",
    ),
    "name_del_rebind": (
        "            del _adopt_or_reject_preloaded\n",
        "must be a single module-level def",
    ),
    "arg_shadow": (
        "            def _rogue(_adopt_or_reject_preloaded=None):\n"
        "                return _adopt_or_reject_preloaded\n"
        "            _rogue()\n",
        "must be a single module-level def",
    ),
    "value_ref_alias": (
        "            _sneak = _adopt_or_reject_preloaded\n"
        "            _sneak(module_name, source)\n",
        "referenced as a value",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C59_OWNER_REBIND_MUTANTS))
def test_c59_owner_reuse_name_cannot_be_rebound_or_shadowed(mutant):
    """Candidate59 #5: nazwa `_adopt_or_reject_preloaded` (kanoniczny owner reuse)
    = dokładnie JEDEN module-level `def`, nigdy rebindowana/przesłaniana (lokalny
    `def`, przypisanie Name, `del`, parametr) ani używana jako wartość (callable
    alias). Zagnieżdżony `def` tej nazwy przechwytywał ownera, a licznik wywołań
    (nazwa niezmieniona) tego nie łapał. Kontrakt jak `_retire_package_route`."""
    replacement, expected = _C59_OWNER_REBIND_MUTANTS[mutant]
    tree = ast.parse(_mutate_before_exec(replacement))
    violations = _physical_import_ownership_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


# #6a: rebind CAŁEGO obiektu chronionej mapy przez NAZWĘ (nie subscript/dunder)
# w scope nie-modułowym — Assign i AnnAssign, też pod `global`.
_C59_WHOLE_OBJECT_REBIND_MUTANTS = {
    "registry_assign": (
        "            _LOADED_PHYSICAL_SIBLINGS = {}\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_assign_global": (
        "            global _LOADED_PHYSICAL_SIBLINGS\n"
        "            _LOADED_PHYSICAL_SIBLINGS = {}\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_annassign": (
        "            _LOADED_PHYSICAL_SIBLINGS: dict = {}\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "routes_assign": (
        "            _PHYSICAL_PACKAGE_ROUTES = {}\n",
        "ownership",
        "route writers must be exactly one",
    ),
    "routes_annassign": (
        "            _PHYSICAL_PACKAGE_ROUTES: dict = {}\n",
        "ownership",
        "route writers must be exactly one",
    ),
    "descendants_assign": (
        "            _PHYSICAL_DESCENDANT_MODULES = {}\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descendants_assign_global": (
        "            global _PHYSICAL_DESCENDANT_MODULES\n"
        "            _PHYSICAL_DESCENDANT_MODULES = {}\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descendants_annassign": (
        "            _PHYSICAL_DESCENDANT_MODULES: dict = {}\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C59_WHOLE_OBJECT_REBIND_MUTANTS))
def test_c59_whole_object_map_rebind_flagged(mutant):
    """Candidate59 #6a: podmiana CAŁEGO chronionego obiektu mapy przez NAZWĘ
    (`X = {}` / `X: T = {}`, także pod `global`) w scope nie-modułowym = FLAGGED.
    C58 liczył tylko Subscript/dunder-writery, więc whole-object rebind (który
    porzuca kanoniczny obiekt i publikuje własny) omijał ratchet."""
    replacement, checker, expected = _C59_WHOLE_OBJECT_REBIND_MUTANTS[mutant]
    tree = ast.parse(_mutate_before_exec(replacement))
    if checker == "ownership":
        violations = _physical_import_ownership_violations(tree)
    else:
        violations = _retirement_contract_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


# #6b: PRAWDZIWY węzeł ast.AugAssign (whole-object `|=` oraz subscript `[k] +=`)
# pokryty we WSZYSTKICH mapach — retirement wcześniej NIE liczył AugAssign wcale.
_C59_AUGASSIGN_MUTANTS = {
    "registry_iaug": (
        "            _LOADED_PHYSICAL_SIBLINGS |= {'rogue': ('x', module)}\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "routes_iaug": (
        "            _PHYSICAL_PACKAGE_ROUTES |= {'rogue': ('a', 'b', module, None)}\n",
        "ownership",
        "route writers must be exactly one",
    ),
    "descendants_iaug": (
        "            _PHYSICAL_DESCENDANT_MODULES |= {'rogue': module}\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descendants_subscript_aug": (
        "            _PHYSICAL_DESCENDANT_MODULES['rogue'] = module\n"
        "            _PHYSICAL_DESCENDANT_MODULES['rogue'] += ()\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C59_AUGASSIGN_MUTANTS))
def test_c59_augassign_writers_flagged_all_maps(mutant):
    """Candidate59 #6b: PRAWDZIWY węzeł `ast.AugAssign` (`X |= {}`, `X[k] += v`)
    = ZAPIS we WSZYSTKICH chronionych mapach. Retirement wcześniej w ogóle nie
    obsługiwał AugAssign (tylko ownership registry/routes) → descendants/routes/
    sys.modules przez `|=` omijały ratchet. RED-first izoluje `visit_AugAssign`
    (ownership) i gałąź AugAssign w retirement: whole-object `|=` nie jest ani
    Subscript-Store, ani Name-Assign, więc jedynym łapaczem jest AugAssign."""
    replacement, checker, expected = _C59_AUGASSIGN_MUTANTS[mutant]
    tree = ast.parse(_mutate_before_exec(replacement))
    if checker == "ownership":
        violations = _physical_import_ownership_violations(tree)
    else:
        violations = _retirement_contract_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


def test_c59_boundary_and_rebind_ratchets_clean_on_real_file() -> None:
    """Kontrapunkt Candidate59: scope-inheritance, wiązanie parametrów, owner-
    rebind guard, whole-object rebind i AugAssign we wszystkich mapach NIE
    fałszują żywego loadera na żadnym z dwóch ratchetów."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []
    assert _retirement_contract_violations(tree) == []


# ─────────────────────────────────────────────────────────────────────────────
# Candidate57 — Sol C56 #1 (CONFIRMED_DEFECT), ROOT-FIX = FAIL-FAST RE-ENTRANCJI
# `gc.disable()` pod lockiem to flaga PROCESOWA: współbieżny (nawet benign)
# `gc.enable()` z innego wątku znosi pauzę i cykliczny GC znów biegnie w wątku-
# posiadaczu, odpalając obce finalizery pod lockiem (zreprodukowane). CPython nie
# ma per-wątkowego wyłączania GC, więc pauzy NIE da się utwardzić na poziomie
# locka. Realny tryb awarii to DEADLOCK: finalizer re-entruje loader i czeka na
# nie-reentrantny lock, który sam trzyma. GWARANCJA (niezależna od stanu GC):
# lock jest FAIL-FAST — ponowne `acquire()` przez wątek-posiadacza podnosi
# `RuntimeError` zamiast deadlockować, więc żaden finalizer nie zablokuje loadera.
# ─────────────────────────────────────────────────────────────────────────────


def test_c57_lock_reentry_by_holding_thread_fails_fast() -> None:
    """F#1 (Sol C56): ponowne `acquire()` przez wątek-posiadacza = RuntimeError.

    Jedyne źródło re-entrancji tego locka to finalizer/audit-hook/sygnał odpalony
    w środku naszej sekcji krytycznej (loader nigdy nie zagnieżdża locka).
    Nie-reentrantny `threading.Lock` ZADEADLOCKOWAŁBY tam (ten sam wątek czeka na
    lock, który trzyma); gwarancja fail-fast zamienia to na czysty wyjątek.
    RED-first (ograniczony czasowo, bez zawieszania): stary kod bez śledzenia
    ownera na drugim `acquire(timeout=...)` z tego samego wątku po prostu BLOKUJE
    i zwraca False (żadnego RuntimeError) — asercja pada.
    """
    from dispatch_v2 import _physical_import as physical_import

    lock = physical_import._GcPausingLock()  # świeża instancja, nie globalny lock
    assert lock.acquire() is True
    try:
        with pytest.raises(RuntimeError):
            # ten sam wątek już trzyma lock → fail-fast; timeout ogranicza
            # czas RED-a na starym kodzie (zwraca False po timeoucie, nie wisi)
            lock.acquire(timeout=0.2)
    finally:
        lock.release()
    # po zwolnieniu lock znów jest w pełni sprawny (owner wyczyszczony)
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_c57_finalizer_reentering_lock_cannot_deadlock_loader() -> None:
    """F#1 (Sol C56), scenariusz realny: cykliczny finalizer odpalony POD lockiem
    w wątku-posiadaczu, który re-entruje loader, dostaje RuntimeError zamiast
    zawisnąć na deadlocku.

    Odtwarza wektor: obiekt w cyklu z `__del__`, którego finalizer próbuje wziąć
    ten sam lock; wymuszamy jego kolekcję `gc.collect()` W TRAKCIE trzymania
    locka (cykliczny GC biegnie synchronicznie w wątku-posiadaczu). Fail-fast:
    finalizer łapie RuntimeError. RED-first: stary `threading.Lock` na
    `acquire(timeout=...)` z tego samego wątku zwraca False (żadnego RuntimeError)
    → `observed` != ["RUNTIMEERROR"] → asercja pada (a bez timeoutu STARY kod
    zadeadlockowałby).
    """
    import gc
    from dispatch_v2 import _physical_import as physical_import

    lock = physical_import._GcPausingLock()
    observed: list[str] = []

    class _Reenter:
        def __init__(self) -> None:
            self.cycle = self  # cykl → tylko cykliczny GC go zbierze

        def __del__(self) -> None:
            try:
                got = lock.acquire(timeout=0.2)  # re-entrancja z wątku-posiadacza
                if got:
                    observed.append("ACQUIRED")
                    lock.release()
                else:
                    observed.append("FALSE")
            except RuntimeError:
                observed.append("RUNTIMEERROR")

    was = gc.isenabled()
    try:
        if not was:
            gc.enable()
        obj = _Reenter()
        obj.cycle = obj
        with lock:
            del obj
            gc.collect()  # odpala finalizer SYNCHRONICZNIE w tym wątku, pod lockiem
    finally:
        if was:
            gc.enable()
        else:
            gc.disable()

    assert observed == ["RUNTIMEERROR"], observed
    # lock nie został osierocony przez wyjątek w finalizerze
    assert lock.acquire(blocking=False) is True
    lock.release()


# ─────────────────────────────────────────────────────────────────────────────
# MIEJSCE ZAREZERWOWANE — oracle workera concurrency (Candidate59) dołączany tutaj.
# Poniżej NIE dopisywać innych testów.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Candidate59 — findingi świeżego Sol/max na frozen C58 (CONFIRMED_DEFECT):
# #1 dwa sub-instrukcyjne okna fail-fast locka, #2 publish-before-exec oddaje
# częściowy moduł równoległemu loadowi, #3 retire roota nie fail-closed vs
# zatruty search-path. Fixy U ŹRÓDŁA + RED-first.
# ─────────────────────────────────────────────────────────────────────────────


def test_c59_lock_reentry_window_is_closed_atomically() -> None:
    """F#1 (Sol C58): detekcja re-entrancji BEZ sub-instrukcyjnego okna.

    Poprzedni fail-fast ustawiał `_owner` OSOBNĄ instrukcją PO `acquire()`, więc
    finalizer/hook re-entrujący loader między nabyciem realnego locka a zapisem
    `_owner` (albo między czyszczeniem `_owner` a `release()`) NIE widział
    własności i mijał fail-fast → deadlock na nie-reentrantnym `threading.Lock`.
    Fix: `RLock._is_owned()` (własność C-atomowa, bez okna). RED-first (bez
    zawieszania): re-entrujemy `acquire()` DOKŁADNIE w oknie (przez `settrace`,
    gdy realny lock jest już zajęty, a bookkeeping jeszcze nie) — stary kod
    zwraca `False` po timeoucie (żadnego RuntimeError), fix podnosi RuntimeError.
    """
    import sys
    from dispatch_v2 import _physical_import as physical_import

    lock = physical_import._GcPausingLock()
    outcome: list = []
    fired = {"v": False}

    def _engaged(lk) -> bool:
        # Realny lock zajęty? RLock → `_is_owned` (C-atomowo); Lock → próbny
        # nieblokujący acquire (zajęty przez posiadacza = False, zwalniamy gdy
        # wolny). Neutralne wobec OBU implementacji (fix i rewers).
        inner = lk._lock
        if hasattr(inner, "_is_owned"):
            return inner._is_owned()
        if inner.acquire(blocking=False):
            inner.release()
            return False
        return True

    def _probe() -> None:
        try:
            got = lock.acquire(timeout=0.2)
            if got:
                outcome.append("REENTERED")
                lock.release()
            else:
                outcome.append("FALSE")
        except RuntimeError:
            outcome.append("RUNTIMEERROR")

    acquire_code = type(lock).acquire.__code__

    def _tracer(frame, event, arg):
        if (
            not fired["v"]
            and event == "line"
            and frame.f_code is acquire_code
            and _engaged(lock)
        ):
            fired["v"] = True
            sys.settrace(None)  # nie śledź własnego `acquire` z sondy
            _probe()
        return _tracer

    old = sys.gettrace()
    sys.settrace(_tracer)
    try:
        lock.acquire()  # sonda odpala się W ŚRODKU tego acquire (w oknie)
    finally:
        sys.settrace(old)
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            pass

    assert fired["v"], "sonda nie trafiła w okno (test nieważny)"
    assert outcome == ["RUNTIMEERROR"], outcome


def test_c59_no_partial_module_reuse_during_concurrent_init(
    tmp_path, monkeypatch
) -> None:
    """F#2 (Sol C58): równoległy load NIE dostaje częściowego modułu jako sukcesu.

    gen w exec (opublikowana w `sys.modules`, `__init__` jeszcze trwa) — drugi
    wątek ładujący TĘ SAMĄ nazwę MUSI fail-closować (RuntimeError „initializing
    concurrently"), a nie zaadoptować częściowego, jeszcze-nie-wykonanego modułu.
    RED-first: bez markera inicjalizacji drugi load przez ownera zwraca częściowy
    moduł (`second_ok True`), zanim pierwszy exec w ogóle padnie.
    """
    import threading
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    (legacy / "__init__.py").write_text(
        "import _c59_init_sync as s\n"
        "s.started.set()\n"
        "s.release.wait(10)\n"
        "raise RuntimeError('gen init boom')\n",
        encoding="utf-8",
    )
    sync = type(sys)("_c59_init_sync")
    sync.started = threading.Event()
    sync.release = threading.Event()
    sys.modules["_c59_init_sync"] = sync
    root = "_c59_init_root"
    box1: dict = {}
    box2: dict = {}

    def _first() -> None:
        try:
            box1["m"] = physical_import.load_physical_scripts_sibling(
                root, "legacy_pkg", package=True
            )
        except BaseException as exc:  # noqa: BLE001
            box1["err"] = repr(exc)

    t1 = threading.Thread(target=_first)
    t1.start()
    try:
        assert sync.started.wait(5), "gen nie weszła w exec"

        def _second() -> None:
            try:
                box2["m"] = physical_import.load_physical_scripts_sibling(
                    root, "legacy_pkg", package=True
                )
                box2["ok"] = True
            except RuntimeError as exc:
                box2["ok"] = False
                box2["err"] = str(exc)
            except BaseException as exc:  # noqa: BLE001
                box2["ok"] = "other"
                box2["err"] = repr(exc)

        t2 = threading.Thread(target=_second)
        t2.start()
        t2.join(5)
        # Fix: drugi load NIE zwrócił modułu — fail-closed RuntimeError:
        assert box2.get("ok") is False, box2
        assert "initializing concurrently" in box2.get("err", ""), box2
    finally:
        sync.release.set()
        t1.join(5)
        sys.modules.pop("_c59_init_sync", None)
        _c49_purge_sys_modules(root)


def test_c59_retire_root_window_fails_closed_vs_poisoned_path(
    tmp_path, monkeypatch
) -> None:
    """F#3 (Sol C58): w oknie retire `import <root>` NIE wykonuje obcego roota.

    Fizyczny sibling załadowany pod nazwą kolidującą z pakietem na `sys.path`.
    W oknie retire (po purge roota z `sys.modules`, gdy `handle.close` jest w
    toku) równoległy `import <root>` MUSI fail-closować w naszym finderze zamiast
    zejść do PathFindera i wykonać OBCE bajty roota. RED-first: bez tombstone'u
    finder zwraca None → PathFinder ładuje obcy root (`FOREIGN True`).
    """
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    root = "_c59_collide"
    # obcy pakiet o TEJ SAMEJ nazwie na sys.path:
    foreign = tmp_path / "foreign"
    (foreign / root).mkdir(parents=True)
    (foreign / root / "__init__.py").write_text(
        "FOREIGN = True\n", encoding="utf-8"
    )
    _c49_purge_sys_modules(root)
    physical_import.load_physical_scripts_sibling(
        root, "legacy_pkg", package=True
    )
    monkeypatch.syspath_prepend(str(foreign))
    outcome: dict = {}
    fired = {"v": False}
    real_close = physical_import._RetainedDirFd.close

    def _probing_close(self):
        if not fired["v"]:
            fired["v"] = True
            # W tym punkcie root jest już zdjęty z sys.modules (purge), trasa
            # popnięta, handle jeszcze niezamknięty — czyste okno retire.
            try:
                mod = importlib.import_module(root)
                outcome["r"] = ("LOADED", getattr(mod, "FOREIGN", None))
            except ModuleNotFoundError as exc:
                outcome["r"] = ("FAILED_CLOSED", str(exc))
            except BaseException as exc:  # noqa: BLE001
                outcome["r"] = ("OTHER", repr(exc))
            finally:
                sys.modules.pop(root, None)
        return real_close(self)

    monkeypatch.setattr(
        physical_import._RetainedDirFd, "close", _probing_close
    )
    try:
        with physical_import._PHYSICAL_LOAD_LOCK:
            _dd = physical_import._retire_package_route(root)
        del _dd[:]
    finally:
        _c49_purge_sys_modules(root)

    assert fired["v"], "sonda nie odpaliła w oknie retire (test nieważny)"
    assert outcome.get("r", (None,))[0] == "FAILED_CLOSED", outcome


def test_c60_load_window_fails_closed_vs_poisoned_path(
    tmp_path, monkeypatch
) -> None:
    """C60 (świeży blind Sol/max na C59): CAŁE okno load fail-closuje.

    C59 stawiał tombstone (`_mark_root_retiring`) DOPIERO po owner-check —
    okno od WEJŚCIA w sekcję krytyczną `with _PHYSICAL_LOAD_LOCK` do
    `_mark_root_retiring` (init-check → adopt → source-check) pozostawało
    otwarte. Fizyczny sibling ładowany pod nazwą KOLIDUJĄCĄ z pakietem na
    zatrutym `sys.path`; w tym oknie (root jeszcze NIE w `sys.modules`)
    równoległy `import <root>` schodził w finderze do `None` → `PathFinder`
    i wykonywał OBCE bajty roota. Fix przenosi tombstone na SAM POCZĄTEK
    sekcji krytycznej (obejmujące try/finally). Sonda w
    `_adopt_or_reject_preloaded` (pod lockiem, PRZED publikacją
    `sys.modules[root]`) importuje kolidujący root: MUSI fail-closować w
    NASZYM finderze (`ModuleNotFoundError`), a NIE wykonać obcych bajtów.
    RED-first: na stanie C59 (tombstone po owner-check) sonda ładuje obcy
    root (`LOADED`, `FOREIGN True`); po fixie — `FAILED_CLOSED`.

    Sonda jest synchroniczna (ten sam wątek, który trzyma load-lock): finder
    fail-closuje po NAZWIE roota niezależnie od wątku, więc reprodukcja jest
    deterministyczna i nie może zawisnąć (brak cross-thread `wait`).
    """
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    # Finder-singleton MUSI być w `sys.meta_path` ZANIM sonda odpali: na świeżym
    # rootcie loader instaluje go dopiero w publikacji (PO oknie), więc bez tego
    # test byłby zależny od kolejności i mógłby vacuous-passować (finder nieobecny
    # → import <root> zawsze idzie do PathFindera). Idempotentne + inert (przy
    # pustej mapie tras `find_spec` roota zwraca None poza oknem tombstone).
    physical_import._ensure_descendant_finder_installed()
    root = "_c60_collide"
    # obcy pakiet o TEJ SAMEJ nazwie na sys.path:
    foreign = tmp_path / "foreign"
    (foreign / root).mkdir(parents=True)
    (foreign / root / "__init__.py").write_text(
        "FOREIGN = True\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(foreign))
    _c49_purge_sys_modules(root)
    importlib.invalidate_caches()

    outcome: dict = {}
    fired = {"v": False}
    real_adopt = physical_import._adopt_or_reject_preloaded

    def _probing_adopt(module_name, source):
        # Pod `_PHYSICAL_LOAD_LOCK`, PRZED publikacją `sys.modules[root]`
        # (owner-check jeszcze nie zwrócił): sonduj okno load importem
        # kolidującego roota. Po fixie tombstone JUŻ stoi → fail-closed.
        if module_name == root and not fired["v"]:
            fired["v"] = True
            try:
                mod = importlib.import_module(root)
                outcome["r"] = ("LOADED", getattr(mod, "FOREIGN", None))
            except ModuleNotFoundError as exc:
                outcome["r"] = ("FAILED_CLOSED", str(exc))
            except BaseException as exc:  # noqa: BLE001
                outcome["r"] = ("OTHER", repr(exc))
            finally:
                sys.modules.pop(root, None)
        return real_adopt(module_name, source)

    monkeypatch.setattr(
        physical_import, "_adopt_or_reject_preloaded", _probing_adopt
    )
    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
    finally:
        _c49_purge_sys_modules(root)

    assert fired["v"], "sonda nie odpaliła w oknie load (test nieważny)"
    assert outcome.get("r", (None,))[0] == "FAILED_CLOSED", outcome


_C60_ALIAS_CHAIN_BASES = {
    "registry": "_LOADED_PHYSICAL_SIBLINGS",
    "routes": "_PHYSICAL_PACKAGE_ROUTES",
    "descendants": "_PHYSICAL_DESCENDANT_MODULES",
}


@pytest.mark.parametrize("depth", [12, 20, 40])
@pytest.mark.parametrize("base_key", sorted(_C60_ALIAS_CHAIN_BASES))
def test_c60_alias_chain_any_depth_flagged(base_key, depth) -> None:
    """C60 #2 (świeży blind Sol/max na C59): fixpoint aliasów BEZ capa.

    ODWRÓCONY łańcuch aliasów write-bound dundera
    (`_aN=_a{N-1} … _a1=_a0; _a0=X.__setitem__; _aN("rogue", …)`) długości N był
    MIJANY przez cap `iterations < 12` w `_collect_ownership_aliases`: każdy
    przebieg fixpointu rozwiązuje 1 poziom wstecz, więc łańcuch ≥12 nie zbiegał w
    12 przebiegach → głębokie aliasy zostawały None → `violations=[]` (BYPASS,
    drugi writer rejestru/trasy/descendant przemycony). Fixpoint do KONWERGENCJI
    (`while changed:`) rozwiązuje łańcuch DOWOLNEJ głębokości → drugi writer przez
    alias jest FLAGGED. Pokrywa registry/routes/descendants (JEDEN helper zasila
    OBU konsumentów). RED-first: przywrócenie capa `iterations < 12` czerwieni
    N≥12 (`violations=[]`).
    """
    base = _C60_ALIAS_CHAIN_BASES[base_key]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    anchor = "            exec(code, module.__dict__)"
    assert anchor in real
    chain = [f"            _a{i} = _a{i - 1}" for i in range(depth, 0, -1)]
    chain.append(f"            _a0 = {base}.__setitem__")
    chain.append(f'            _a{depth}("rogue", ("x", module))')
    mutated = real.replace(anchor, "\n".join(chain) + "\n" + anchor, 1)
    assert mutated != real
    violations = _physical_import_ownership_violations(ast.parse(mutated))
    assert violations, (
        base_key,
        depth,
        "alias chain not flagged — fixpoint cap regression?",
    )


def test_c61_fresh_root_load_window_fails_closed(tmp_path) -> None:
    """C61 #A (świeży blind Sol/max na C60): okno load ŚWIEŻEGO roota fail-closuje.

    C60 przeniósł tombstone (`_mark_root_retiring`) na SAM POCZĄTEK sekcji
    krytycznej — ale tombstone działa TYLKO przez naszego findera
    (`_PhysicalSiblingDescendantFinder`), a ten na C60 instaluje się dopiero w
    PUBLIKACJI (`_ensure_descendant_finder_installed`, PO oknie). Dla roota
    ładowanego PIERWSZY raz w procesie finder NIE jest jeszcze na `sys.meta_path`
    w oknie pre-publish, więc `import <root>` kolidującej nazwy schodzi wprost do
    `PathFindera` i WYKONUJE obce `__init__.py` (arbitralne wykonanie kodu), mimo
    że tombstone jest ustawiony. Fix U ŹRÓDŁA: finder-singleton jest instalowany
    przy IMPORCIE modułu loadera (zawsze obecny na `meta_path`), więc tombstone
    ustawiony na początku sekcji krytycznej jest skuteczny także dla świeżego
    roota; `import <root>` w oknie trafia NASZ finder → fail-closed.

    Test biegnie w ŚWIEŻYM interpreterze (subprocess), by deterministycznie
    odtworzyć „pierwszy load w procesie" niezależnie od kolejności testów
    (finder-singleton jest globalny i lepki po pierwszej publikacji). W dziecku:
    finder-obecność zaraz po imporcie loadera (`finder_at_import`), kolizja nazwy
    na `sys.path`, sonda `import <root>` w oknie load (pod lockiem, w
    `_adopt_or_reject_preloaded`, PRZED publikacją `sys.modules[root]`).
    RED-first: na stanie C60 `finder_at_import=False` i sonda ładuje OBCE bajty
    (`LOADED`, `FOREIGN True`); po fixie `finder_at_import=True` i `FAILED_CLOSED`.
    """
    import textwrap

    child = tmp_path / "c61a_child.py"
    child.write_text(
        textwrap.dedent(
            '''
            import sys, os, pathlib, importlib
            tmp = pathlib.Path(sys.argv[1])
            scripts = tmp / "scripts"
            pkg = scripts / "dispatch_v2"
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            legacy = scripts / "legacy_pkg"
            legacy.mkdir()
            (legacy / "__init__.py").write_text("", encoding="utf-8")
            import dispatch_v2
            dispatch_v2.__file__ = str(pkg / "__init__.py")
            from dispatch_v2 import _physical_import as pi
            root = "_c61a_collide"
            # Obecność findera ZARAZ po imporcie loadera, PRZED jakimkolwiek load:
            finder_at_import = pi._DESCENDANT_FINDER in sys.meta_path
            foreign = tmp / "foreign"
            (foreign / root).mkdir(parents=True)
            (foreign / root / "__init__.py").write_text(
                "FOREIGN = True\\n", encoding="utf-8"
            )
            sys.path.insert(0, str(foreign))
            importlib.invalidate_caches()
            real_adopt = pi._adopt_or_reject_preloaded
            outcome = {}
            fired = {"v": False}
            def _probing_adopt(module_name, source):
                # Pod `_PHYSICAL_LOAD_LOCK`, PRZED publikacją `sys.modules[root]`
                # (tombstone JUŻ ustawiony na początku sekcji krytycznej): sonduj
                # okno importem kolidującego roota.
                if module_name == root and not fired["v"]:
                    fired["v"] = True
                    try:
                        m = importlib.import_module(root)
                        outcome["r"] = ("LOADED", getattr(m, "FOREIGN", None))
                    except ModuleNotFoundError as exc:
                        outcome["r"] = ("FAILED_CLOSED", str(exc))
                    except BaseException as exc:
                        outcome["r"] = ("OTHER", repr(exc))
                    finally:
                        sys.modules.pop(root, None)
                return real_adopt(module_name, source)
            pi._adopt_or_reject_preloaded = _probing_adopt
            pi.load_physical_scripts_sibling(root, "legacy_pkg", package=True)
            print(repr({
                "finder_at_import": finder_at_import,
                "fired": fired["v"],
                "outcome": outcome.get("r"),
            }))
            '''
        ),
        encoding="utf-8",
    )
    env = _clean_path_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(child), str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result = ast.literal_eval(completed.stdout.strip().splitlines()[-1])
    assert result["fired"] is True, (
        "sonda nie odpaliła w oknie load (test nieważny)",
        result,
        completed.stderr,
    )
    assert result["finder_at_import"] is True, (
        "finder NIE zainstalowany przy imporcie loadera — okno fresh-roota "
        "niechronione",
        result,
    )
    assert (
        result["outcome"] is not None
        and result["outcome"][0] == "FAILED_CLOSED"
    ), ("obce bajty roota wykonane w oknie load fresh-roota", result)


def test_c61_load_superseded_by_concurrent_gen_fails_closed(
    tmp_path, monkeypatch
) -> None:
    """C61 #B (świeży blind Sol/max na C60): load NIE zwraca STALE modułu po supersede.

    `return module` na końcu `load_physical_scripts_sibling` było BEZWARUNKOWE
    po `exec`. Gdy gen1 jest w exec, a równoległy retire + gen2 przejmuje nazwę
    w rejestrach (`sys.modules`/trasa/owner wskazują gen2), gen1 kończył i zwracał
    callerowi STARY (zretirowany) moduł, choć aktualną generacją jest gen2. Fix U
    ŹRÓDŁA: PRZED `return module` (pod lockiem) sprawdź `_current_generation_is`;
    jeśli NASZA generacja nie włada już nazwą → fail-closed `RuntimeError`
    „superseded", zamiast zwrócić stale moduł. Normalny przypadek (nasza generacja
    aktualna) i samo-import → return bez zmian.

    RED-first: na stanie C60 gen1 zwraca stale moduł (`ok=True`, `m` to gen1);
    po fixie gen1 podnosi RuntimeError „superseded". gen2 pozostaje aktualną
    generacją (rollback gen1 jest generation-safe — nie tyka cudzej generacji).
    """
    import threading

    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    # __init__ synchronizuje: PIERWSZY exec (gen1) blokuje na `release`; kolejne
    # (gen2) przechodzą natychmiast (flaga `first_done` w module-sync).
    (legacy / "__init__.py").write_text(
        "import _c61_gen_sync as s\n"
        "if not s.first_done:\n"
        "    s.first_done = True\n"
        "    s.started.set()\n"
        "    s.release.wait(10)\n",
        encoding="utf-8",
    )
    sync = type(sys)("_c61_gen_sync")
    sync.first_done = False
    sync.started = threading.Event()
    sync.release = threading.Event()
    sys.modules["_c61_gen_sync"] = sync
    root = "_c61b_root"
    box1: dict = {}
    gen2_module = None
    gen1_module = None

    def _gen1() -> None:
        try:
            box1["m"] = physical_import.load_physical_scripts_sibling(
                root, "legacy_pkg", package=True
            )
            box1["ok"] = True
        except RuntimeError as exc:
            box1["ok"] = False
            box1["err"] = str(exc)
        except BaseException as exc:  # noqa: BLE001
            box1["ok"] = "other"
            box1["err"] = repr(exc)

    t1 = threading.Thread(target=_gen1)
    t1.start()
    try:
        assert sync.started.wait(5), "gen1 nie weszła w exec"
        gen1_module = sys.modules.get(root)
        assert gen1_module is not None
        # MAIN: retiruj gen1 (purge trasy/owner/sys.modules), potem załaduj gen2
        # tej samej nazwy — gen2 przejmuje nazwę w rejestrach, gdy gen1 wciąż
        # tkwi w exec (zablokowany na `release`).
        with physical_import._PHYSICAL_LOAD_LOCK:
            _dd = physical_import._retire_package_route(root)
        del _dd[:]
        gen2_module = physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        assert sys.modules.get(root) is gen2_module
        assert gen2_module is not gen1_module
    finally:
        sync.release.set()
        t1.join(5)
        sys.modules.pop("_c61_gen_sync", None)
        _c49_purge_sys_modules(root)

    # Fix: gen1 kończąc exec widzi, że NIE włada już nazwą → fail-closed
    # RuntimeError „superseded", zamiast zwrócić stale (zretirowany) moduł.
    assert box1.get("ok") is False, box1
    assert "superseded" in box1.get("err", ""), box1
    # gen1 NIE zwrócił modułu callerowi:
    assert "m" not in box1, box1
    # gen2 pozostaje aktualną generacją (nietknięta rollbackiem gen1):
    assert gen2_module is not None and gen2_module is not gen1_module


def test_c62_prelock_window_fails_closed_vs_poisoned_path(
    tmp_path, monkeypatch
) -> None:
    """C62 (świeży blind Sol/max na frozen C61): okno PRE-LOCK odczytu z dysku fail-closuje.

    C61 osadził tombstone (`_mark_root_retiring`) na SAM POCZĄTEK sekcji
    krytycznej `with _PHYSICAL_LOAD_LOCK`, ale ODCZYT ŹRÓDŁA Z DYSKU
    (`_open_physical_source_fd`) biegnie PRZED tą sekcją (poza lockiem — szerokie
    okno I/O). W tym oknie pre-lock tombstone dla nazwy roota jeszcze NIE stoi,
    więc równoległy `import <root>` nazwy KOLIDUJĄCEJ z zatrutym `sys.path` schodzi
    w finderze do `None` → `PathFinder` i WYKONUJE obce `__init__.py` (arbitralne
    wykonanie kodu — ta sama klasa co #A/C60, lecz w oknie PRZED lockiem/odczytem;
    POTWIERDZONE blind Sol/max: foreign_loaded_during_prelock_read=True,
    foreign_source_executed=True, tombstone_present=False). Fix U ŹRÓDŁA: przenieś
    `_mark_root_retiring` na SAM POCZĄTEK `load_physical_scripts_sibling` — PRZED
    `_open_physical_source_fd` (przed odczytem z dysku), z `_unmark_root_retiring`
    w `finally` obejmującym CAŁĄ funkcję (zdejmuje tombstone na KAŻDEJ ścieżce
    wyjścia). Wtedy tombstone stoi już w oknie pre-lock i `import <root>` trafia
    NASZ finder → fail-closed.

    Sonda odpala SYNCHRONICZNIE wewnątrz monkeypatchowanego
    `_open_physical_source_fd` — dokładnie w oknie pre-lock, PRZED wejściem w
    `with _PHYSICAL_LOAD_LOCK` — i importuje kolidujący root. Finder fail-closuje po
    NAZWIE roota niezależnie od wątku, więc reprodukcja jest deterministyczna i nie
    może zawisnąć (brak cross-thread `wait`). RED-first: na stanie C61 (mark
    wewnątrz locka, PO odczycie) sonda ładuje OBCE bajty (`LOADED`, `FOREIGN True`);
    po fixie — `FAILED_CLOSED`.
    """
    import importlib
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    # Finder-singleton MUSI być w `sys.meta_path` ZANIM sonda odpali (C61):
    # idempotentne + inert przy pustej mapie tras (`find_spec` roota bez wpisu i
    # bez tombstone zwraca None → normalne importy nietknięte).
    physical_import._ensure_descendant_finder_installed()
    root = "_c62_collide"
    # obcy pakiet o TEJ SAMEJ nazwie na sys.path:
    foreign = tmp_path / "foreign"
    (foreign / root).mkdir(parents=True)
    (foreign / root / "__init__.py").write_text(
        "FOREIGN = True\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(foreign))
    _c49_purge_sys_modules(root)
    importlib.invalidate_caches()

    outcome: dict = {}
    fired = {"v": False}
    real_open_fd = physical_import._open_physical_source_fd

    def _probing_open_fd(parent_dir, relative, *, package):
        # OKNO PRE-LOCK: jesteśmy w `load_physical_scripts_sibling` PRZED
        # `with _PHYSICAL_LOAD_LOCK`, dokładnie w odczycie źródła z dysku. Sonduj
        # importem kolidującego roota. Po fixie tombstone JUŻ stoi (mark
        # przeniesiony PRZED ten odczyt) → fail-closed; przed fixem brak tombstone
        # → PathFinder wykonuje OBCE bajty. Sonda odpala RAZ (fresh root nie jest
        # jeszcze w `sys.modules`, więc `import` trafia finder, nie cache).
        if not fired["v"]:
            fired["v"] = True
            try:
                mod = importlib.import_module(root)
                outcome["r"] = ("LOADED", getattr(mod, "FOREIGN", None))
            except ModuleNotFoundError as exc:
                outcome["r"] = ("FAILED_CLOSED", str(exc))
            except BaseException as exc:  # noqa: BLE001
                outcome["r"] = ("OTHER", repr(exc))
            finally:
                sys.modules.pop(root, None)
        return real_open_fd(parent_dir, relative, package=package)

    monkeypatch.setattr(
        physical_import, "_open_physical_source_fd", _probing_open_fd
    )
    try:
        physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
    finally:
        _c49_purge_sys_modules(root)

    assert fired["v"], "sonda nie odpaliła w oknie pre-lock (test nieważny)"
    assert outcome.get("r", (None,))[0] == "FAILED_CLOSED", outcome


# ─────────────────────────────────────────────────────────────────────────────
# Candidate62 — świeży blind Sol/max na frozen C61 (3 CONFIRMED_DEFECT). Trzy
# formy ZAPISU do chronionej mapy/rebindu chronionej nazwy omijały ratchet
# WYŁĄCZNIE wewnątrz funkcji autoryzowanej do ODCZYTU rejestru
# (`_adopt_or_reject_preloaded` i pokrewne scope'y), bo detekcja ZAPISU miała
# dziurę na kształtach, które w innych scope'ach i tak są łapane licznikiem
# writerów. ROOT-FIX u źródła, spójny z istniejącą maszynerią (bez drugiego
# detektora), tak by KAŻDY statycznie rozstrzygalny ZAPIS liczył się jako write —
# wtedy istniejący inwariant „dokładnie jeden writer w loaderze/exec_module"
# flaguje nadmiarowy zapis niezależnie od scope:
#   #L1 whole-object rebind przez ROZPAKOWANIE (`(X,) = (...,)`, `[X] = [...]`,
#       `*X`): target był `ast.Tuple`/`ast.List`, więc detekcja „tylko `ast.Name`"
#       go mijała. `_iter_rebound_names` spłaszcza target do Name-liści (JEDEN
#       helper dla ownership `_whole_object_rebind` i retirement).
#   #L2 rebind chronionej NAZWY przez IMPORT (`from m import x as NAME`,
#       `import m as NAME`): `_name_binding_contract` liczył Name-Store/def/param,
#       ale nie `ast.alias` — rozszerzony o import-binding (owner + retire naraz).
#   #L3 UNBOUND dunder ZAPISU (`dict.__setitem__(X, k, v)` /
#       `dict.__delitem__(X, k)`): baza mapy jest ARGUMENTEM, nie odbiorcą
#       atrybutu, więc kind `write-bound:<base>` (forma BOUND) jej nie łapał.
#       `_unbound_dunder_write` klasyfikuje X po pierwszym argumencie (JEDEN
#       helper dla ownership registry/routes i retirement descendants/routes/
#       sys.modules).
# GRANICA (jak C43/C58): domykamy WYŁĄCZNIE formy statycznie rozstrzygalne;
# `getattr(X, '__setitem__')`/`exec`/`__dict__`/refleksja są nierozstrzygalne —
# SOUND gwarancją pozostaje behawioralny backstop (runtime owner raises).
# ─────────────────────────────────────────────────────────────────────────────


# #L1 (unpack whole-object rebind) + #L3 (unbound dunder write). Wstrzykiwane w
# exec_module (scope NIE-modułowy) TUŻ PRZED `exec(...)` — nadmiarowy writer, więc
# istniejący licznik „dokładnie jeden writer" czerwienieje.
_C62_UNPACK_AND_UNBOUND_MUTANTS = {
    # ---- #L1 rozpakowanie ----
    "registry_single_tuple_unpack": (
        "            global _LOADED_PHYSICAL_SIBLINGS\n"
        "            (_LOADED_PHYSICAL_SIBLINGS,) = ({'rogue': ('x', module)},)\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_single_list_unpack": (
        "            [_LOADED_PHYSICAL_SIBLINGS] = [{'rogue': ('x', module)}]\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "routes_single_tuple_unpack": (
        "            global _PHYSICAL_PACKAGE_ROUTES\n"
        "            (_PHYSICAL_PACKAGE_ROUTES,) = ({'rogue': ('a', 'b', module, None)},)\n",
        "ownership",
        "route writers must be exactly one",
    ),
    "descendants_single_tuple_unpack": (
        "            global _PHYSICAL_DESCENDANT_MODULES\n"
        "            (_PHYSICAL_DESCENDANT_MODULES,) = ({'rogue': module},)\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    # ---- #L3 unbound dunder ----
    "registry_unbound_setitem": (
        "            dict.__setitem__(_LOADED_PHYSICAL_SIBLINGS, 'rogue', ('x', module))\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "registry_unbound_delitem": (
        "            dict.__delitem__(_LOADED_PHYSICAL_SIBLINGS, 'rogue')\n",
        "ownership",
        "registry writers must be exactly one",
    ),
    "routes_unbound_setitem": (
        "            dict.__setitem__(_PHYSICAL_PACKAGE_ROUTES, 'rogue', ('a', 'b', module, None))\n",
        "ownership",
        "route writers must be exactly one",
    ),
    "descendants_unbound_setitem": (
        "            dict.__setitem__(_PHYSICAL_DESCENDANT_MODULES, 'rogue', module)\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
    "descendants_unbound_delitem": (
        "            dict.__delitem__(_PHYSICAL_DESCENDANT_MODULES, 'rogue')\n",
        "retirement",
        "descendant registry write must be exactly one",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C62_UNPACK_AND_UNBOUND_MUTANTS))
def test_c62_unpack_and_unbound_dunder_writers_flagged(mutant):
    """Candidate62 #L1/#L3: ZAPIS do chronionej mapy przez ROZPAKOWANIE
    whole-object (`(X,) = (...,)`, `[X] = [...]`) oraz UNBOUND dunder
    (`dict.__setitem__/__delitem__(X, ...)`) = FLAGGED tak samo jak forma
    kanoniczna. RED-first izoluje `_iter_rebound_names` (unpack) i
    `_unbound_dunder_write` (unbound): żadna z tych form nie jest ani
    Subscript-Store, ani bound write-bound, ani Name-Assign."""
    replacement, checker, expected = _C62_UNPACK_AND_UNBOUND_MUTANTS[mutant]
    tree = ast.parse(_mutate_before_exec(replacement))
    if checker == "ownership":
        violations = _physical_import_ownership_violations(tree)
    else:
        violations = _retirement_contract_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


# #L2 rebind chronionej NAZWY przez import-alias. Wstrzykiwane na POZIOMIE
# MODUŁU (prepend), bo import-binding przesłania kanoniczny helper globalnie.
_C62_IMPORT_ALIAS_MUTANTS = {
    "owner_from_import_as": (
        "from builtins import print as _adopt_or_reject_preloaded\n",
        "ownership",
        "must be a single module-level def and never rebound",
    ),
    "owner_plain_import_as": (
        "import builtins as _adopt_or_reject_preloaded\n",
        "ownership",
        "must be a single module-level def and never rebound",
    ),
    "retire_from_import_as": (
        "from builtins import print as _retire_package_route\n",
        "retirement",
        "must be a single module-level def and never rebound",
    ),
    "retire_plain_import_as": (
        "import builtins as _retire_package_route\n",
        "retirement",
        "must be a single module-level def and never rebound",
    ),
}


@pytest.mark.parametrize("mutant", sorted(_C62_IMPORT_ALIAS_MUTANTS))
def test_c62_import_alias_rebind_of_protected_names_flagged(mutant):
    """Candidate62 #L2: dowiązanie chronionej nazwy (`_adopt_or_reject_preloaded`
    / `_retire_package_route`) przez IMPORT-alias (`from m import x as NAME`,
    `import m as NAME`) przesłania kanoniczny module-level `def` — FLAGGED.
    RED-first izoluje gałąź `ast.alias` w `_name_binding_contract`: bez niej
    licznik wywołań widzi „jedno wołanie NAZWY", nie wiedząc, że NAZWA wskazuje
    na obcy callable."""
    import_line, checker, expected = _C62_IMPORT_ALIAS_MUTANTS[mutant]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    mutated = import_line + real
    tree = ast.parse(mutated)
    if checker == "ownership":
        violations = _physical_import_ownership_violations(tree)
    else:
        violations = _retirement_contract_violations(tree)
    assert any(expected in v for v in violations), (mutant, violations)


# #L4 kontrakt OBECNOŚCI kanonicznego cleanup-popa ownera. Kanoniczny
# `_LOADED_PHYSICAL_SIBLINGS.pop(name, None)` w `_purge_owned_module_subtree`
# MUSI istnieć: jego USUNIĘCIE (owner-record wycieka → stale reuse) i
# PRZENIESIENIE poza purge = violation. Analogiczne do C46 (route-close/pop
# presence w retire).
_C62_POP_PRESENCE_MUTANTS = {
    "pop_removed": "removed",
    "pop_relocated_out_of_purge": "relocated",
}


@pytest.mark.parametrize("mutant", sorted(_C62_POP_PRESENCE_MUTANTS))
def test_c62_owner_cleanup_pop_presence_enforced(mutant):
    """Candidate62 #L4: ratchet WYMAGA istnienia kanonicznego cleanup-popa
    ownera dokładnie w purge. RED-first izoluje zdjęcie guardu
    `if registry_pops and (...)`: bez fixu USUNIĘCIE popa daje pustą listę i
    przechodzi (MISSED, behawioralnie: owner_record_leaked/failed_generation_
    reused); po fixie kontrakt OBECNOŚCI flaguje brak/relokację."""
    kind = _C62_POP_PRESENCE_MUTANTS[mutant]
    real = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    pop = "    _LOADED_PHYSICAL_SIBLINGS.pop(name, None)\n"
    assert pop in real
    without = real.replace(pop, "", 1)
    assert without != real
    if kind == "removed":
        mutated = without
    else:  # relocated: pop znika z purge, pojawia się w exec_module (obcy scope)
        mutated = without.replace(
            _EXEC_ANCHOR,
            "            _LOADED_PHYSICAL_SIBLINGS.pop('rogue', None)\n"
            + _EXEC_ANCHOR,
            1,
        )
        assert mutated != without
    violations = _physical_import_ownership_violations(ast.parse(mutated))
    assert any(
        "registry mutator (pop) must be exactly one" in v for v in violations
    ), (mutant, violations)


def test_c62_unpack_unbound_and_import_alias_ratchets_clean_on_real_file() -> None:
    """Kontrapunkt Candidate62: rozpakowanie whole-object, unbound-dunder i
    import-alias-binding NIE fałszują żywego loadera na żadnym z dwóch ratchetów.
    Legalne ODCZYTY w `_adopt_or_reject_preloaded` (jest ich sporo) pozostają
    czyste — flagujemy WYŁĄCZNIE ZAPISY."""
    tree = ast.parse(
        (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8"),
        filename="_physical_import.py",
    )
    assert _physical_import_ownership_violations(tree) == []
    assert _retirement_contract_violations(tree) == []


# ─────────────────────────────────────────────────────────────────────────────
# Candidate63 — świeży blind Sol/max na frozen C62 (CONFIRMED_DEFECT,
# zreprodukowany niezależnie): `importlib.reload()` (albo re-import po
# `del sys.modules[root]`) fizycznego siblinga załadowanego pod nazwą
# KOLIDUJĄCĄ z modułem na `sys.path`/stdlib (np. `geometry.py` pod nazwą
# `calendar`) WYKONYWAŁ OBCY kod do zaufanego obiektu. PRZYCZYNA: `reload` NIE
# używa `m.__spec__.loader` — re-rozwiązuje spec przez `sys.meta_path`
# (`_bootstrap._find_spec(name, target=m)`); nasz finder dla nazwy ROOT-LEVEL
# (bez kropki) zwracał `None` (poza tombstone retirement), więc reload schodził
# do `PathFinder` → obcy plik z `sys.path`. FIX U ŹRÓDŁA: `find_spec` findera
# fail-closuje dla nazwy ROOT-LEVEL, którą loader POSIADA (jest w
# `_PHYSICAL_PACKAGE_ROUTES` LUB `_LOADED_PHYSICAL_SIBLINGS`) — analogicznie do
# istniejącego checku tombstone — zamiast `return None`. Normalny
# `import <root>` załadowanego siblinga NIE konsultuje findera (CPython
# `sys.modules`-first), więc fail-close dotyka WYŁĄCZNIE reload / re-import.
# Nazwy NIEposiadane (json itd.) dalej `return None` → PathFinder je rozwiązuje.
# ─────────────────────────────────────────────────────────────────────────────


def test_c63_reload_colliding_name_fails_closed(tmp_path, monkeypatch) -> None:
    """F-C62 (Sol) #1: reload siblinga o nazwie kolidującej NIE wykonuje obcego.

    Sibling MODUŁ (`geometry.py`) załadowany pod nazwą kolidującą z OBCYM
    plikiem tej samej nazwy na `sys.path`. `importlib.reload(trusted)`
    re-rozwiązuje spec przez `sys.meta_path` → przed fixem nasz finder zwracał
    `None`, reload schodził do `PathFinder` i WYKONYWAŁ obcy plik DO zaufanego
    obiektu (`trusted.__file__` → obcy, `SHAPE` → 'FOREIGN'). Po fixie finder
    fail-closuje po nazwie posiadanego roota → `ModuleNotFoundError`, obcy kod
    NIE wykonany, zaufany obiekt nietknięty. RED-first: na stanie C62 (finder
    `return None` dla roota poza tombstone) reload wykonuje obcy plik.
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    physical_import._ensure_descendant_finder_installed()
    # Trusted physical sibling MODULE (non-package) beside the attested pkg.
    (scripts / "geometry.py").write_text(
        "SHAPE = 'trusted-geometry'\nMARKER_TRUSTED = True\n", encoding="utf-8"
    )
    root = "_c63_collide"
    # Obcy moduł tej samej nazwy na (zatrutym) sys.path — PathFinder go rozwiąże.
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / (root + ".py")).write_text(
        "FOREIGN_EXECUTED = True\nSHAPE = 'FOREIGN'\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(poison))
    _c49_purge_sys_modules(root)
    importlib.invalidate_caches()
    try:
        trusted = physical_import.load_physical_scripts_sibling(
            root, "geometry.py", package=False
        )
        assert sys.modules[root] is trusted
        assert trusted.SHAPE == "trusted-geometry"
        assert not hasattr(trusted, "FOREIGN_EXECUTED"), "sanity: clean sibling"

        fired = False
        result = None
        try:
            importlib.reload(trusted)
            fired = True
            result = "RELOAD_SUCCEEDED"
        except ModuleNotFoundError as exc:
            fired = True
            result = ("FAILED_CLOSED", str(exc))
        except BaseException as exc:  # noqa: BLE001
            fired = True
            result = ("OTHER", repr(exc))

        cur = sys.modules.get(root)
        after_file = getattr(cur, "__file__", None)
        foreign_executed = (
            bool(getattr(cur, "FOREIGN_EXECUTED", False))
            or getattr(cur, "SHAPE", None) == "FOREIGN"
            or (after_file is not None and "geometry.py" not in str(after_file))
        )
        assert fired, "reload probe did not fire (test invalid)"
        assert not foreign_executed, (
            "FOREIGN module executed into trusted object via reload", result,
            after_file,
        )
    finally:
        _c49_purge_sys_modules(root)


def test_c63_reimport_after_del_colliding_name_fails_closed(
    tmp_path, monkeypatch
) -> None:
    """F-C62 (Sol) #1 twin: re-import po `del sys.modules[root]` fail-closuje.

    Druga droga, którą finder JEST konsultowany dla nazwy roota (poza reload):
    usunięcie roota z `sys.modules` i ponowny `import <root>`. Bez cache'u
    `sys.modules` import trafia `sys.meta_path` → nasz finder MUSI fail-closować
    po nazwie posiadanego roota, inaczej `PathFinder` wykona obcy plik.
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    physical_import._ensure_descendant_finder_installed()
    (scripts / "geometry.py").write_text(
        "SHAPE = 'trusted-geometry'\n", encoding="utf-8"
    )
    root = "_c63_collide_reimport"
    poison = tmp_path / "poison2"
    poison.mkdir()
    (poison / (root + ".py")).write_text(
        "FOREIGN_EXECUTED = True\nSHAPE = 'FOREIGN'\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(poison))
    _c49_purge_sys_modules(root)
    importlib.invalidate_caches()
    try:
        physical_import.load_physical_scripts_sibling(
            root, "geometry.py", package=False
        )
        # Usuń root z cache → następny import trafia finder (nie cache).
        sys.modules.pop(root, None)
        importlib.invalidate_caches()
        result = None
        try:
            importlib.import_module(root)
            result = ("LOADED", getattr(sys.modules.get(root), "SHAPE", None))
        except ModuleNotFoundError as exc:
            result = ("FAILED_CLOSED", str(exc))
        assert result[0] == "FAILED_CLOSED", (
            "re-import after del resolved a foreign module", result,
        )
    finally:
        _c49_purge_sys_modules(root)


def test_c63_finder_does_not_claim_unowned_root_names(
    tmp_path, monkeypatch
) -> None:
    """F-C62 (Sol) #1 kontrola: finder NIE zawłaszcza cudzych nazw root-level.

    Fail-close dotyczy WYŁĄCZNIE nazw, które POSIADAMY (route/owner-registry).
    Legalne zewnętrzne moduły (json, świeży top-level) muszą dalej dostawać
    `None` z findera i normalnie rozwiązywać się przez `PathFinder`.
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    finder = physical_import._DESCENDANT_FINDER
    # Nazwy stdlib / niezaładowane przez nas → finder zwraca None.
    assert finder.find_spec("json") is None
    assert finder.find_spec("calendar") is None
    assert finder.find_spec("_c63_never_owned_name") is None
    # `import json` dalej działa mimo obecności findera na meta_path.
    assert importlib.import_module("json") is sys.modules["json"]
    # Świeży top-level module z tmp sys.path importuje się normalnie.
    extra = tmp_path / "extra_syspath"
    extra.mkdir()
    (extra / "_c63_fresh_top.py").write_text("OK = 11\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(extra))
    importlib.invalidate_caches()
    try:
        assert finder.find_spec("_c63_fresh_top") is None
        assert importlib.import_module("_c63_fresh_top").OK == 11
    finally:
        _c49_purge_sys_modules("_c63_fresh_top")


# Probe uruchamiany w ŚWIEŻYM interpreterze na ZMUTOWANYM (odwróconym) loaderze:
# dowód, że fix jest LOAD-BEARING — po usunięciu owned-check z findera reload
# ZNOWU wykonuje obcy plik (RED). Hermetyczny layout w tmp (dispatch_v2.__file__
# przemapowany), więc nie dotyka żywego drzewa.
_C63_MUTATION_PROBE = """\
import sys, os, importlib, importlib.util, pathlib
PKGROOT = os.environ["PKGROOT"]; TMPD = pathlib.Path(os.environ["TMPD"])
LOADER = os.environ["LOADER"]
sys.path.insert(0, PKGROOT)
import dispatch_v2
spec = importlib.util.spec_from_file_location("dispatch_v2._physical_import", LOADER)
pi = importlib.util.module_from_spec(spec)
sys.modules["dispatch_v2._physical_import"] = pi
spec.loader.exec_module(pi)
scripts = TMPD / "scripts"; pkg = scripts / "dispatch_v2"; pkg.mkdir(parents=True)
(pkg / "__init__.py").write_text("")
(scripts / "geometry.py").write_text("SHAPE='trusted-geometry'\\n")
dispatch_v2.__file__ = str(pkg / "__init__.py")
pi._LOADED_PHYSICAL_SIBLINGS = {}; pi._PHYSICAL_PACKAGE_ROUTES = {}
pi._PHYSICAL_DESCENDANT_MODULES = {}; pi._ROOTS_UNDER_RETIREMENT = {}
root = "_c63_collide_mut"
poison = TMPD / "poison"; poison.mkdir()
(poison / (root + ".py")).write_text("FOREIGN_EXECUTED=True\\nSHAPE='FOREIGN'\\n")
sys.path.insert(0, str(poison)); importlib.invalidate_caches()
trusted = pi.load_physical_scripts_sibling(root, "geometry.py", package=False)
res = "?"
try:
    importlib.reload(trusted); res = "RELOAD_SUCCEEDED"
except ModuleNotFoundError:
    res = "FAILED_CLOSED"
cur = sys.modules.get(root)
foreign = bool(getattr(cur, "FOREIGN_EXECUTED", False)) or getattr(cur, "SHAPE", None) == "FOREIGN"
print("PROBE_RESULT", res, "FOREIGN_EXECUTED", foreign)
"""


def test_c63_mutation_reverting_owned_check_reintroduces_foreign_exec(
    tmp_path,
) -> None:
    """Mutation: usunięcie owned-check z findera PRZYWRACA foreign-exec (RED).

    Bierze ŻYWY loader, odwraca fix (owned-root fail-close → tombstone-only
    `return None`) i uruchamia probe w świeżym interpreterze. Dowód, że oracle
    jest niepusty i fix load-bearing: zmutowany loader ZNOWU wykonuje obcy plik
    przy reload. Na ŻYWYM (niemutowanym) loaderze probe daje FAILED_CLOSED.
    """
    src = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    fixed_block = (
        "            if (\n"
        "                fullname in _ROOTS_UNDER_RETIREMENT\n"
        "                or fullname in _PHYSICAL_PACKAGE_ROUTES\n"
        "                or fullname in _LOADED_PHYSICAL_SIBLINGS\n"
        "            ):\n"
    )
    assert fixed_block in src, "fix anchor not found — loader shape drifted"
    reverted = src.replace(
        fixed_block,
        "            if fullname in _ROOTS_UNDER_RETIREMENT:\n",
        1,
    )
    assert reverted != src
    mutated_loader = tmp_path / "_physical_import_mut.py"
    mutated_loader.write_text(reverted, encoding="utf-8")
    env = dict(os.environ)
    env["PKGROOT"] = str(PACKAGE_PARENT)
    env["TMPD"] = str(tmp_path / "run")
    (tmp_path / "run").mkdir()
    env["LOADER"] = str(mutated_loader)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", _C63_MUTATION_PROBE],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "PROBE_RESULT RELOAD_SUCCEEDED FOREIGN_EXECUTED True" in completed.stdout, (
        "reverting owned-check did NOT reintroduce foreign-exec — oracle vacuous",
        completed.stdout,
        completed.stderr,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Candidate64 (Sol C63): WARIANT DOTTED owned-fail-close. C63 domknął tylko
# nazwę ROOT-LEVEL (bez kropki). Świeży blind Sol/max znalazł, że sibling
# załadowany pod nazwą KROPKOWANĄ kolidującą ze stdlib (np. `email.utils`,
# package=False), którego RODZIC NIE jest naszym pakietem, wciąż wykonuje obcy
# kod przy `importlib.reload`: finder partycjonuje `email.utils` na root=`email`,
# `_PHYSICAL_PACKAGE_ROUTES.get("email")` = None → `return None` → `PathFinder`
# rozwiązuje `email.utils` z `parent.__path__` (stdlib) i WYKONUJE obcy plik DO
# zaufanego obiektu. FIX U ŹRÓDŁA: gałąź `route is None` w `find_spec` fail-closuje
# TĄ SAMĄ regułą owned-name (nazwa w `_ROOTS_UNDER_RETIREMENT` /
# `_PHYSICAL_PACKAGE_ROUTES` / `_LOADED_PHYSICAL_SIBLINGS`) zamiast `return None`.
# Legalny descendant owned-PAKIETU (`route is not None`) NIE trafia tu — dalej
# rozwiązywany ścieżką descendant. Nazwy CUDZE dalej `return None` → PathFinder.
# ─────────────────────────────────────────────────────────────────────────────


def _c64_setup_dotted(tmp_path, monkeypatch, tag):
    """Trusted sibling `geometry.py` + poison PARENT pkg z obcym dzieckiem.

    Zwraca `(physical_import, dotted, foreign_probe)`. `dotted` = `<parent>.utils`,
    gdzie `<parent>` to REALNY pakiet na (zatrutym) `sys.path` (musi być w
    `sys.modules`, by `importlib.reload`/re-import doszły do `_find_spec` po
    `parent.__path__`). `<parent>/utils.py` to OBCY plik (FOREIGN_EXECUTED).
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    physical_import._ensure_descendant_finder_installed()
    (scripts / "geometry.py").write_text(
        "SHAPE = 'trusted-geometry'\nMARKER_TRUSTED = True\n", encoding="utf-8"
    )
    parent = "_c64_parent_" + tag
    poison = tmp_path / ("poison_" + tag)
    ppkg = poison / parent
    ppkg.mkdir(parents=True)
    (ppkg / "__init__.py").write_text("", encoding="utf-8")
    (ppkg / "utils.py").write_text(
        "FOREIGN_EXECUTED = True\nSHAPE = 'FOREIGN'\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(poison))
    _c49_purge_sys_modules(parent)
    importlib.invalidate_caches()
    # Rodzic MUSI być w sys.modules, by reload/re-import doszły do _find_spec.
    importlib.import_module(parent)
    return physical_import, parent + ".utils"


def test_c64_reload_dotted_owned_name_fails_closed(
    tmp_path, monkeypatch
) -> None:
    """F-C63 (Sol) #1: reload DOTTED siblinga kolidującego NIE wykonuje obcego.

    Sibling MODUŁ (`geometry.py`) załadowany pod nazwą KROPKOWANĄ
    `<parent>.utils`, gdzie `<parent>` NIE jest naszym pakietem (jest realnym
    pakietem na zatrutym `sys.path` z obcym `utils.py`). `importlib.reload`
    re-rozwiązuje spec przez `sys.meta_path` → na C63 finder dla route=None
    zwracał `None`, reload schodził do `PathFinder` i WYKONYWAŁ obcy plik DO
    zaufanego obiektu. Po fixie C64 finder fail-closuje po owned-name →
    `ModuleNotFoundError`, obcy kod NIE wykonany. RED-first potwierdzony
    standalone probe na C63 (`PROBE_RESULT RELOAD_SUCCEEDED FOREIGN True`).
    """
    physical_import, dotted = _c64_setup_dotted(tmp_path, monkeypatch, "reload")
    parent = dotted.partition(".")[0]
    try:
        trusted = physical_import.load_physical_scripts_sibling(
            dotted, "geometry.py", package=False
        )
        assert sys.modules[dotted] is trusted
        assert trusted.SHAPE == "trusted-geometry"
        assert not hasattr(trusted, "FOREIGN_EXECUTED"), "sanity: clean sibling"

        fired = False
        result = None
        try:
            importlib.reload(trusted)
            fired = True
            result = "RELOAD_SUCCEEDED"
        except ModuleNotFoundError as exc:
            fired = True
            result = ("FAILED_CLOSED", str(exc))
        except BaseException as exc:  # noqa: BLE001
            fired = True
            result = ("OTHER", repr(exc))

        cur = sys.modules.get(dotted)
        after_file = getattr(cur, "__file__", None)
        foreign_executed = (
            bool(getattr(cur, "FOREIGN_EXECUTED", False))
            or getattr(cur, "SHAPE", None) == "FOREIGN"
            or (after_file is not None and "geometry.py" not in str(after_file))
        )
        assert fired, "reload probe did not fire (test invalid)"
        assert not foreign_executed, (
            "FOREIGN module executed into trusted object via dotted reload",
            result,
            after_file,
        )
    finally:
        _c49_purge_sys_modules(dotted)
        _c49_purge_sys_modules(parent)


def test_c64_reimport_after_del_dotted_owned_name_fails_closed(
    tmp_path, monkeypatch
) -> None:
    """F-C63 (Sol) #1 twin: re-import DOTTED po `del sys.modules[name]` fail-closuje.

    Druga droga konsultacji findera: `del sys.modules[<dotted>]` + ponowny
    `import <dotted>`. Bez cache import trafia `sys.meta_path` → finder MUSI
    fail-closować po owned-name, inaczej `PathFinder` wykona obcy plik dziecka
    z `parent.__path__`.
    """
    physical_import, dotted = _c64_setup_dotted(tmp_path, monkeypatch, "reimp")
    parent = dotted.partition(".")[0]
    try:
        physical_import.load_physical_scripts_sibling(
            dotted, "geometry.py", package=False
        )
        sys.modules.pop(dotted, None)
        importlib.invalidate_caches()
        result = None
        try:
            importlib.import_module(dotted)
            result = ("LOADED", getattr(sys.modules.get(dotted), "SHAPE", None))
        except ModuleNotFoundError as exc:
            result = ("FAILED_CLOSED", str(exc))
        assert result[0] == "FAILED_CLOSED", (
            "re-import after del resolved a foreign dotted module", result,
        )
    finally:
        _c49_purge_sys_modules(dotted)
        _c49_purge_sys_modules(parent)


def test_c64_root_level_owned_check_still_fails_closed(
    tmp_path, monkeypatch
) -> None:
    """Kontrola NIE-REGRESJI C63: root-level owned-fail-close DALEJ działa.

    Uogólnienie na dotted NIE może osłabić wariantu root-level z C63. Sibling
    `geometry.py` pod nazwą root-level kolidującą z obcym plikiem na `sys.path`;
    `reload` MUSI dalej fail-closować.
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    physical_import._ensure_descendant_finder_installed()
    (scripts / "geometry.py").write_text(
        "SHAPE = 'trusted-geometry'\n", encoding="utf-8"
    )
    root = "_c64_root_collide"
    poison = tmp_path / "poison_root"
    poison.mkdir()
    (poison / (root + ".py")).write_text(
        "FOREIGN_EXECUTED = True\nSHAPE = 'FOREIGN'\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(poison))
    _c49_purge_sys_modules(root)
    importlib.invalidate_caches()
    try:
        trusted = physical_import.load_physical_scripts_sibling(
            root, "geometry.py", package=False
        )
        assert trusted.SHAPE == "trusted-geometry"
        fired = False
        try:
            importlib.reload(trusted)
            fired = True
        except ModuleNotFoundError:
            fired = True
        cur = sys.modules.get(root)
        foreign = (
            bool(getattr(cur, "FOREIGN_EXECUTED", False))
            or getattr(cur, "SHAPE", None) == "FOREIGN"
        )
        assert fired
        assert not foreign, "C63 root-level fail-close regressed by C64 change"
    finally:
        _c49_purge_sys_modules(root)


def test_c64_legal_descendant_reload_not_regressed(
    tmp_path, monkeypatch
) -> None:
    """Kontrola NIE-REGRESJI: reload LEGALNego descendanta owned-PAKIETU działa.

    Owned-standalone fail-close NIE może zepsuć rozwiązywania descendanta
    (`route is not None` → ścieżka descendant z retained fd). Ładujemy pakiet
    `legacy_pkg` + `.child`, potem `importlib.reload(child)` — MUSI zwrócić
    zaufaną wartość (nie fail-close, nie obcy plik).
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    monkeypatch.setattr(
        physical_import, "_PHYSICAL_INITIALIZING", {}, raising=False
    )
    physical_import._ensure_descendant_finder_installed()
    root = "_c64_ownedpkg"
    try:
        pkg = physical_import.load_physical_scripts_sibling(
            root, "legacy_pkg", package=True
        )
        child = importlib.import_module(root + ".child")
        assert child.SAFE == "attested"
        assert list(pkg.__path__) == []
        # reload LEGALNego descendanta → nasza ścieżka descendant, nie fail-close.
        reloaded = importlib.reload(child)
        assert reloaded.SAFE == "attested"
        assert sys.modules[root + ".child"] is reloaded
    finally:
        _c49_purge_sys_modules(root)


def test_c64_finder_does_not_claim_unowned_dotted_names(
    tmp_path, monkeypatch
) -> None:
    """Kontrola: finder NIE zawłaszcza CUDZYCH nazw dotted (np. stdlib).

    Fail-close dotyczy WYŁĄCZNIE nazw, które POSIADAMY. `email.utils` (gdy NIE
    nasze) i świeży `<parent>.child` z tmp `sys.path` muszą dalej dostawać
    `None` z findera i rozwiązywać się przez `PathFinder`.
    """
    physical_import, scripts, legacy = _c49_setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        physical_import, "_ROOTS_UNDER_RETIREMENT", {}, raising=False
    )
    finder = physical_import._DESCENDANT_FINDER
    # Nazwy dotted stdlib / nieposiadane → finder zwraca None.
    assert finder.find_spec("email.utils") is None
    assert finder.find_spec("_c64_unowned_parent.child") is None
    # `import email.utils` (cudze) dalej działa mimo obecności findera.
    mod = importlib.import_module("email.utils")
    assert mod is sys.modules["email.utils"]
    # Świeży pakiet <parent>.child z tmp sys.path importuje się normalnie.
    extra = tmp_path / "extra_syspath"
    pkg = extra / "_c64_fresh_parent"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "child.py").write_text("OK = 22\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(extra))
    importlib.invalidate_caches()
    try:
        assert finder.find_spec("_c64_fresh_parent.child") is None
        assert importlib.import_module("_c64_fresh_parent.child").OK == 22
    finally:
        _c49_purge_sys_modules("_c64_fresh_parent")


# Probe w ŚWIEŻYM interpreterze na ZMUTOWANYM loaderze (odwrócony DOTTED fix):
# dowód load-bearing — po usunięciu owned-check z gałęzi `route is None` reload
# DOTTED znowu wykonuje obcy plik (RED). Hermetyczny layout w tmp.
_C64_MUTATION_PROBE = """\
import sys, os, importlib, importlib.util, pathlib
PKGROOT = os.environ["PKGROOT"]; TMPD = pathlib.Path(os.environ["TMPD"])
LOADER = os.environ["LOADER"]
sys.path.insert(0, PKGROOT)
import dispatch_v2
spec = importlib.util.spec_from_file_location("dispatch_v2._physical_import", LOADER)
pi = importlib.util.module_from_spec(spec)
sys.modules["dispatch_v2._physical_import"] = pi
spec.loader.exec_module(pi)
scripts = TMPD / "scripts"; pkg = scripts / "dispatch_v2"; pkg.mkdir(parents=True)
(pkg / "__init__.py").write_text("")
(scripts / "geometry.py").write_text("SHAPE='trusted-geometry'\\n")
dispatch_v2.__file__ = str(pkg / "__init__.py")
pi._LOADED_PHYSICAL_SIBLINGS = {}; pi._PHYSICAL_PACKAGE_ROUTES = {}
pi._PHYSICAL_DESCENDANT_MODULES = {}; pi._ROOTS_UNDER_RETIREMENT = {}
parent = "_c64_parent_mut"
poison = TMPD / "poison"; ppkg = poison / parent; ppkg.mkdir(parents=True)
(ppkg / "__init__.py").write_text("")
(ppkg / "utils.py").write_text("FOREIGN_EXECUTED=True\\nSHAPE='FOREIGN'\\n")
sys.path.insert(0, str(poison)); importlib.invalidate_caches()
importlib.import_module(parent)
dotted = parent + ".utils"
trusted = pi.load_physical_scripts_sibling(dotted, "geometry.py", package=False)
res = "?"
try:
    importlib.reload(trusted); res = "RELOAD_SUCCEEDED"
except ModuleNotFoundError:
    res = "FAILED_CLOSED"
cur = sys.modules.get(dotted)
foreign = bool(getattr(cur, "FOREIGN_EXECUTED", False)) or getattr(cur, "SHAPE", None) == "FOREIGN"
print("PROBE_RESULT", res, "FOREIGN_EXECUTED", foreign)
"""


def test_c64_mutation_reverting_dotted_check_reintroduces_foreign_exec(
    tmp_path,
) -> None:
    """Mutation: usunięcie DOTTED owned-check z findera PRZYWRACA foreign-exec (RED).

    Bierze ŻYWY loader, odwraca WYŁĄCZNIE gałąź `route is None` (owned dotted
    fail-close → `return None`) i uruchamia probe w świeżym interpreterze. Anchor
    to unikalny komunikat „colliding dotted name" (rozróżnia od root-level C63,
    który zostaje NIETKNIĘTY). Dowód, że oracle jest niepusty i fix load-bearing.
    """
    src = (REPO_ROOT / "_physical_import.py").read_text(encoding="utf-8")
    dotted_block = (
        "            if (\n"
        "                fullname in _ROOTS_UNDER_RETIREMENT\n"
        "                or fullname in _PHYSICAL_PACKAGE_ROUTES\n"
        "                or fullname in _LOADED_PHYSICAL_SIBLINGS\n"
        "            ):\n"
        "                raise ModuleNotFoundError(\n"
        "                    \"physical sibling name is owned/retiring; refusing "
        "text-path \"\n"
        "                    f\"resolution of a colliding dotted name: "
        "{fullname}\"\n"
        "                )\n"
        "            return None\n"
    )
    assert dotted_block in src, "dotted fix anchor not found — loader shape drifted"
    reverted = src.replace(dotted_block, "            return None\n", 1)
    assert reverted != src
    mutated_loader = tmp_path / "_physical_import_mut.py"
    mutated_loader.write_text(reverted, encoding="utf-8")
    env = dict(os.environ)
    env["PKGROOT"] = str(PACKAGE_PARENT)
    env["TMPD"] = str(tmp_path / "run")
    (tmp_path / "run").mkdir()
    env["LOADER"] = str(mutated_loader)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", _C64_MUTATION_PROBE],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "PROBE_RESULT RELOAD_SUCCEEDED FOREIGN_EXECUTED True" in completed.stdout, (
        "reverting dotted owned-check did NOT reintroduce foreign-exec — oracle vacuous",
        completed.stdout,
        completed.stderr,
    )
