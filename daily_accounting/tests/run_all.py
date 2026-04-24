"""Runner dla daily_accounting testów — pytest niedostępny w tym env.

Wywołuje każdą funkcję zaczynającą się od 'test_' w każdym module tests/test_*.py,
zlicza PASS/FAIL, exit code 0 = all pass, 1 = any fail.

Usage:
    cd /root/.openclaw/workspace/scripts
    python3 -m dispatch_v2.daily_accounting.tests.run_all
"""
import importlib
import pkgutil
import sys
import traceback
from pathlib import Path


def _collect_test_modules():
    tests_dir = Path(__file__).parent
    for entry in sorted(tests_dir.glob("test_*.py")):
        mod_name = f"dispatch_v2.daily_accounting.tests.{entry.stem}"
        yield mod_name


def run() -> int:
    modules = list(_collect_test_modules())
    total = 0
    passed = 0
    failed: list = []

    for mod_name in modules:
        mod = importlib.import_module(mod_name)
        test_fns = [
            (name, getattr(mod, name))
            for name in dir(mod)
            if name.startswith("test_") and callable(getattr(mod, name))
        ]
        for name, fn in test_fns:
            total += 1
            try:
                fn()
                passed += 1
                print(f"  PASS  {mod_name}.{name}")
            except AssertionError as e:
                failed.append((mod_name, name, f"AssertionError: {e}"))
                print(f"  FAIL  {mod_name}.{name}: AssertionError: {e}")
            except Exception:
                failed.append((mod_name, name, traceback.format_exc()))
                print(f"  FAIL  {mod_name}.{name}:\n{traceback.format_exc()}")

    print()
    print(f"=== {passed}/{total} passed ===")
    if failed:
        print(f"FAILED: {len(failed)}")
        for m, n, err in failed:
            print(f"  {m}.{n}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
