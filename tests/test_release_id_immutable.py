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
import os

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


def test_release_id_read_from_env_at_import(reloadable_release_id):
    os.environ["ZIOMEK_MODEL_VERSION"] = "REL-ORACLE-42"
    mod = importlib.reload(ahb)
    assert mod.MODEL_VERSION == "REL-ORACLE-42"


def test_release_id_default_when_env_absent(reloadable_release_id):
    os.environ.pop("ZIOMEK_MODEL_VERSION", None)
    mod = importlib.reload(ahb)
    assert mod.MODEL_VERSION == "V3.28"


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
