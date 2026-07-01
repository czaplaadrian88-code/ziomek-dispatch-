"""L0.2 anty-dryf: env strażnika carried-first ≡ env silnika (plan-recheck).

Strażnik (tools/carried_first_guard.py) reużywa plan_recheck._start_anchor
+ _apply_canon_order_invariants — semantykę flag route/canon definiują drop-iny
dispatch-plan-recheck.service.d (env-frozen na poziomie modułu). Pusty albo
rozjechany env strażnika = VOID przyrząd: 91,7% fikcyjnych `no_position`
(audyt spójności 30.06, R3-D1, backing/faza_E_carried_first_guard_empty_env_VERDICT.md).
Kontrakt INV-TRUTH-5: pełne lustro (≡), nie podzbiór — podzbiór dryfuje.

Test czyta PLIKI drop-inów (nie systemctl) — deterministyczny, działa też gdy
systemd nie odpowiada. Na hoście produkcyjnym brak drop-ina strażnika przy
istniejących drop-inach silnika = FAIL (nie skip): zniknięcie parytetu ma
krzyczeć, nie znikać po cichu. Na maszynie bez unitów Ziomka — skip całości.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ENGINE_DROPIN_DIR = Path("/etc/systemd/system/dispatch-plan-recheck.service.d")
GUARD_DROPIN = Path(
    "/etc/systemd/system/dispatch-carried-first-guard.service.d/engine-env-parity.conf"
)

pytestmark = pytest.mark.skipif(
    not ENGINE_DROPIN_DIR.is_dir(),
    reason="brak drop-inów dispatch-plan-recheck (nie-produkcyjny host)",
)


def _env_flags(paths) -> dict:
    """Environment=NAME=VALUE z plików .conf -> {NAME: VALUE} (tylko ENABLE_*)."""
    flags: dict = {}
    for path in paths:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line.startswith("Environment="):
                continue
            payload = line[len("Environment=") :].strip().strip('"')
            if "=" not in payload:
                continue
            name, value = payload.split("=", 1)
            if name.startswith("ENABLE_"):
                flags[name] = value.strip().strip('"')
    return flags


def _engine_flags() -> dict:
    # glob("*.conf") celowo pomija kopie .conf.bak-* (nie kończą się na .conf)
    return _env_flags(sorted(ENGINE_DROPIN_DIR.glob("*.conf")))


def test_guard_dropin_exists():
    assert GUARD_DROPIN.is_file(), (
        f"Brak {GUARD_DROPIN} — strażnik carried-first znów biega z pustym env "
        "(default-OFF => fikcyjne no_position, VOID R3-D1). Odtwórz drop-in "
        "lustrem drop-inów dispatch-plan-recheck.service.d."
    )


def test_guard_env_mirrors_engine():
    if not GUARD_DROPIN.is_file():
        pytest.fail("brak drop-ina strażnika — patrz test_guard_dropin_exists")
    engine = _engine_flags()
    guard = _env_flags([GUARD_DROPIN])
    assert engine, "0 flag ENABLE_* w drop-inach plan-recheck — zmiana layoutu? Zaktualizuj test."
    missing = {k: v for k, v in engine.items() if guard.get(k) != v}
    extra = {k: v for k, v in guard.items() if k not in engine}
    assert not missing and not extra, (
        "Dryf env strażnik↔silnik (INV-TRUTH-5 wymaga ≡).\n"
        f"U silnika, brak/inne u strażnika: {missing}\n"
        f"U strażnika, już nie u silnika: {extra}\n"
        f"Fix: wyrównaj {GUARD_DROPIN} do drop-inów {ENGINE_DROPIN_DIR} "
        "i `systemctl daemon-reload` (timer oneshot załaduje sam)."
    )
