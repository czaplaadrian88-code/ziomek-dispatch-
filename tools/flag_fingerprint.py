"""Fingerprint flag decyzyjnych na żądanie (ETAP 4, 2026-06-10, Z-04).

Drukuje wartości WSZYSTKICH flag decyzyjnych rozwiązane ścieżką runtime
(flags.json → env-default → False) — czyli to, czym liczy KAŻDY proces silnika
po unifikacji. Użycie:

    /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.flag_fingerprint

Porównanie z procesami live: grep FLAG_FINGERPRINT w logach
shadow/czasowka/plan-recheck — wszystkie linie muszą być identyczne
(i identyczne z outputem tego narzędzia, modulo zmiany flags.json po starcie).
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402


def main() -> int:
    print(C.flag_fingerprint())
    return 0


if __name__ == "__main__":
    sys.exit(main())
