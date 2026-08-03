"""Oracle A-5 — release_id (MODEL_VERSION) immutable, ustalony z env przy starcie procesu.

Kontrakt broniony (naprawa u źródła, ETAP 4 Przykazania #0):
  1. release_id = etykieta wersji/generacji silnika jest USTALANA RAZ z env
     ``ZIOMEK_MODEL_VERSION`` przy imporcie modułu (starcie procesu);
  2. wartość jest STAŁA przez cały cykl życia procesu (brak dryfu);
  3. próba MUTACJI w runtime jest ODRZUCANA wyjątkiem (nie no-op cichy) — poza
     przypisaniem identycznej wartości, które jest idempotentnym no-op;
  4. release_id emitowany w telemetrii pulsu (heartbeat) niesie wartość ze startu,
     nawet po odrzuconej próbie mutacji.

MUTATION GUARD: usunięcie/odwrócenie zatrzasku w ``assistant_heartbeat.py`` (linia
``sys.modules[__name__].__class__ = _ReleaseIdImmutableModule``) sprawia, że
``test_runtime_mutation_rejected`` oraz ``test_emitted_release_id_survives_mutation_attempt``
czerwienieją (przypisanie do globala przestaje rzucać AttributeError).
"""
import importlib
import json
import logging
import os
import subprocess
import sys
import textwrap
import types

import pytest

from dispatch_v2 import assistant_heartbeat as ahb


@pytest.fixture
def reloadable_release_id():
    """Pozwala testom reloadować moduł z podmienionym env i ZAWSZE przywraca stan
    modułu zgodny z env procesu po teście (niezależnie od kolejności finalizerów)."""
    saved = os.environ.get("ZIOMEK_MODEL_VERSION")
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("ZIOMEK_MODEL_VERSION", None)
        else:
            os.environ["ZIOMEK_MODEL_VERSION"] = saved
        importlib.reload(ahb)


def test_intentional_reload_reinitializes_release_id_from_env(reloadable_release_id):
    """`reload()` jest świadomym re-exec modułu, nie mutacją atrybutu.

    Produkcja nie reloaduje tego modułu; wspieranym sposobem zmiany etykiety
    pozostaje start nowego procesu. Test dokumentuje jawny wyjątek od zatrzasku.
    """
    os.environ["ZIOMEK_MODEL_VERSION"] = "REL-ORACLE-42"
    mod = importlib.reload(ahb)
    assert mod.MODEL_VERSION == "REL-ORACLE-42"


def test_release_id_default_when_env_absent(reloadable_release_id):
    os.environ.pop("ZIOMEK_MODEL_VERSION", None)
    mod = importlib.reload(ahb)
    assert mod.MODEL_VERSION == "V3.28"


@pytest.mark.parametrize(
    ("raw_value", "reason"),
    [
        (None, "unset"),
        ("", "blank"),
        ("   ", "blank"),
        ("REL-OK\nINJECT", "invalid"),
    ],
)
def test_invalid_or_missing_env_uses_logged_fallback(
    reloadable_release_id, caplog, raw_value, reason
):
    if raw_value is None:
        os.environ.pop("ZIOMEK_MODEL_VERSION", None)
    else:
        os.environ["ZIOMEK_MODEL_VERSION"] = raw_value
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ahb.__name__):
        mod = importlib.reload(ahb)
    assert mod.MODEL_VERSION == "V3.28"
    messages = [record.getMessage() for record in caplog.records]
    assert any("release_id fallback" in message and reason in message for message in messages)


def test_runtime_mutation_rejected():
    original = ahb.MODEL_VERSION
    with pytest.raises(AttributeError):
        ahb.MODEL_VERSION = str(original) + "-HACK"
    assert ahb.MODEL_VERSION == original


def test_same_value_reassignment_is_idempotent_noop():
    original = ahb.MODEL_VERSION
    ahb.MODEL_VERSION = original  # ta sama wartość — nie może rzucić
    assert ahb.MODEL_VERSION == original


def test_release_id_stable_across_repeated_reads():
    assert len({ahb.MODEL_VERSION for _ in range(5)}) == 1


def _attack_in_subprocess(tmp_path, attack: str) -> dict:
    heartbeat_path = tmp_path / "attack-heartbeat.json"
    script = textwrap.dedent(
        f"""
        import json
        import sys
        import types

        from dispatch_v2 import assistant_heartbeat as ahb

        original = ahb.MODEL_VERSION
        blocked = False
        try:
            {attack}
        except (AttributeError, TypeError):
            blocked = True

        ahb.HEARTBEAT_PATH = {str(heartbeat_path)!r}
        ahb.write_heartbeat(
            optimizer_running=True,
            queue_depth=0,
            processed=1,
            failed=0,
            worker_alive=True,
            fallback_active=False,
        )
        payload = json.loads(open({str(heartbeat_path)!r}, encoding="utf-8").read())
        print(json.dumps({{
            "blocked": blocked,
            "attribute": getattr(ahb, "MODEL_VERSION", None),
            "emitted": payload["model_version"],
            "original": original,
        }}))
        """
    )
    env = os.environ.copy()
    scripts_root = env["ZIOMEK_SCRIPTS_ROOT"]
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (scripts_root, env.get("PYTHONPATH", "")) if part
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    ("attack", "must_raise"),
    [
        ("del ahb.MODEL_VERSION; ahb.MODEL_VERSION = 'DRIFT-DEL'", True),
        ("ahb.__dict__['MODEL_VERSION'] = 'DRIFT-DICT'", False),
        ("vars(ahb).update(MODEL_VERSION='DRIFT-VARS')", False),
        ("ahb.__class__ = types.ModuleType", True),
    ],
    ids=["del-setattr", "module-dict", "vars-update", "class-replacement"],
)
def test_realistic_runtime_bypass_vectors_are_neutralized(tmp_path, attack, must_raise):
    outcome = _attack_in_subprocess(tmp_path, attack)
    assert outcome == {
        # Prawdziwy module dict jest wymagany przez importlib.reload. Zapis do
        # niego jest dozwolonym martwym cieniem, ale nie zmienia atrybutu ani
        # payloadu; mutacje przez protokół atrybutów są jawnie odrzucane.
        "blocked": must_raise,
        "attribute": outcome["original"],
        "emitted": outcome["original"],
        "original": outcome["original"],
    }


def test_sys_modules_and_none_assignment_are_rejected():
    original = ahb.MODEL_VERSION
    with pytest.raises(AttributeError):
        sys.modules[ahb.__name__].MODEL_VERSION = "DRIFT-SYS-MODULES"
    with pytest.raises(AttributeError):
        ahb.MODEL_VERSION = None
    assert ahb.MODEL_VERSION == original


def test_release_id_has_explicit_delete_guard():
    with pytest.raises(AttributeError, match="chroniony przed usunięciem"):
        delattr(ahb, "MODEL_VERSION")


def test_release_id_has_explicit_class_replacement_guard():
    with pytest.raises(AttributeError, match="runtime-podmiana odrzucona"):
        ahb.__class__ = types.ModuleType


def test_emitted_release_id_survives_mutation_attempt(tmp_path, monkeypatch):
    hb = tmp_path / "hb.json"
    monkeypatch.setattr(ahb, "HEARTBEAT_PATH", str(hb))
    original = ahb.MODEL_VERSION
    with pytest.raises(AttributeError):
        ahb.MODEL_VERSION = "DRIFT-99"
    ahb.write_heartbeat(
        optimizer_running=True,
        queue_depth=0,
        processed=1,
        failed=0,
        worker_alive=True,
        fallback_active=False,
    )
    payload = json.loads(hb.read_text())
    assert payload["model_version"] == original
