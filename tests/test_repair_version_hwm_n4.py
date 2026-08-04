"""N-4: operatorskie narzędzie diagnozy i naprawy sidecara `.version_hwm`.

Macierz stanów jest ta sama, którą blindy A-2 (iter5 sekcja 4, iter6 sekcja 1)
zmierzyły na trzech drzewach — plus dwa stany, które blind zliczał razem
(marker unieważniony vs sidecar w starym formacie bez markera).

Każdy test przypina `pm.PLANS_FILE`/`LOCK_FILE` i wszystkie pochodne sidecary do
``tmp_path`` i sprawdza tę granicę fail-closed PRZED pierwszym zapisem — żywy
`dispatch_state` nie jest dotykany ani razu.
"""
from __future__ import annotations

import ast
import errno
import json
import sys
import types
from pathlib import Path

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import plan_manager as PM
from dispatch_v2 import state_persistence as SP
from dispatch_v2.tools import repair_version_hwm as RH


_LIVE_STATE = Path("/root/.openclaw/workspace/dispatch_state").resolve()


def _clear_plan_cache() -> None:
    with PM._perf_plans_lock:
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None


def _assert_plan_sandbox(root: Path) -> None:
    allowed = root.resolve()
    for path in (
        Path(PM.PLANS_FILE).resolve(),
        Path(PM.LOCK_FILE).resolve(),
        SP.previous_path(PM.PLANS_FILE).resolve(),
        PM.version_hwm_path().resolve(),
    ):
        assert path.is_relative_to(allowed), (
            f"fail-closed: plan path escaped test sandbox: {path}"
        )
        assert not path.is_relative_to(_LIVE_STATE), (
            f"fail-closed: plan path targets live state: {path}"
        )


@pytest.fixture
def plan_store(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    _assert_plan_sandbox(tmp_path)

    decision_log = types.ModuleType("dispatch_v2.decision_eta_log")
    decision_log.record_plan_commit = lambda *_args, **_kwargs: True
    monkeypatch.setitem(sys.modules, "dispatch_v2.decision_eta_log", decision_log)
    monkeypatch.setattr(PM, "_perf_lazy_on", lambda: False)
    _clear_plan_cache()
    yield tmp_path
    _clear_plan_cache()


def _set_plan_guard(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(C, "ENABLE_PLAN_CORRUPT_RAISE", enabled, raising=False)
    _clear_plan_cache()


def _plan_body(tag: str) -> dict:
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": tag},
        "start_ts": "2026-08-04T09:00:00+00:00",
        "stops": [{
            "order_id": tag,
            "type": "dropoff",
            "coords": {"lat": 53.14, "lng": 23.16},
        }],
        "optimization_method": "incremental",
    }


def _ack(monkeypatch) -> None:
    monkeypatch.setenv(RH.ACK_ENV, RH.ACK_VALUE)


def _build_on_era_store(monkeypatch) -> int:
    """Zbuduj realny magazyn z ery ON i zwróć jego HWM."""
    _set_plan_guard(monkeypatch, True)
    PM.save_plan("c1", _plan_body("on-c1"))
    PM.save_plan("c2", _plan_body("on-c2"))
    return int(json.loads(
        PM.version_hwm_path().read_text(encoding="utf-8")
    )["last_issued"])


def _damage(state: str, hwm: int) -> None:
    """Odtwórz jeden ze stanów sidecara z macierzy blinda."""
    path = PM.version_hwm_path()
    schema = PM._VERSION_HWM_SCHEMA
    if state == "missing":
        path.unlink()
    elif state == "proven":
        pass
    elif state == "unproven":
        path.write_text(json.dumps(
            {"schema": schema, "last_issued": hwm, "covers_all_issued": False}
        ), encoding="utf-8")
    elif state == "legacy_no_marker":
        path.write_text(json.dumps(
            {"schema": schema, "last_issued": hwm}
        ), encoding="utf-8")
    elif state == "malformed_json":
        path.write_text("{ not json", encoding="utf-8")
    elif state == "wrong_schema":
        path.write_text(json.dumps(
            {"schema": "bogus.v9", "last_issued": hwm}
        ), encoding="utf-8")
    elif state == "bad_value_type":
        path.write_text(json.dumps(
            {"schema": schema, "last_issued": "not-an-int"}
        ), encoding="utf-8")
    elif state == "below_epoch_floor":
        path.write_text(json.dumps(
            {"schema": schema, "last_issued": 12}
        ), encoding="utf-8")
    elif state == "bad_marker_type":
        path.write_text(json.dumps(
            {"schema": schema, "last_issued": hwm, "covers_all_issued": "yes"}
        ), encoding="utf-8")
    else:
        raise AssertionError(f"nieznany stan sidecara: {state}")


def _fail_sidecar_read(monkeypatch) -> None:
    real_read = SP.read_json_object

    def guarded(path, **kwargs):
        if Path(path) == PM.version_hwm_path():
            raise OSError(errno.EACCES, "synthetic sidecar read failure")
        return real_read(path, **kwargs)

    monkeypatch.setattr(SP, "read_json_object", guarded)


def _fail_sidecar_write(monkeypatch) -> None:
    real_write = SP.atomic_write_json

    def guarded(path, data, **kwargs):
        if Path(path) == PM.version_hwm_path():
            raise OSError(errno.EACCES, "synthetic sidecar write failure")
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(SP, "atomic_write_json", guarded)


def _tree_fingerprint(root: Path) -> dict:
    return {
        entry.name: (
            entry.stat().st_ino, entry.stat().st_mtime_ns, entry.stat().st_size
        )
        for entry in sorted(root.iterdir())
    }


# ─── diagnoza: cała macierz stanów, zero śladu na dysku ────────────────────

_DIAGNOSE_MATRIX = {
    "missing": (RH.SIDECAR_MISSING, RH.VERDICT_BLOCKED),
    "proven": (RH.SIDECAR_PROVEN, RH.VERDICT_OK),
    "unproven": (RH.SIDECAR_UNPROVEN, RH.VERDICT_SELF_HEALING),
    "legacy_no_marker": (RH.SIDECAR_LEGACY_NO_MARKER, RH.VERDICT_SELF_HEALING),
    "malformed_json": (RH.SIDECAR_CONTENT_REJECTED, RH.VERDICT_BLOCKED),
    "wrong_schema": (RH.SIDECAR_CONTENT_REJECTED, RH.VERDICT_BLOCKED),
    "bad_value_type": (RH.SIDECAR_CONTENT_REJECTED, RH.VERDICT_BLOCKED),
    "below_epoch_floor": (RH.SIDECAR_CONTENT_REJECTED, RH.VERDICT_BLOCKED),
    "bad_marker_type": (RH.SIDECAR_CONTENT_REJECTED, RH.VERDICT_BLOCKED),
}


@pytest.mark.parametrize("state", sorted(_DIAGNOSE_MATRIX))
def test_diagnose_classifies_every_sidecar_state(plan_store, monkeypatch, state):
    """Operator dostaje jednoznaczną klasyfikację dla każdego stanu z macierzy."""
    hwm = _build_on_era_store(monkeypatch)
    _damage(state, hwm)
    expected_sidecar, expected_verdict = _DIAGNOSE_MATRIX[state]

    before = _tree_fingerprint(plan_store)
    report = RH.diagnose()

    assert report["sidecar"]["status"] == expected_sidecar
    assert report["verdict"] == expected_verdict
    assert report["main"]["status"] == RH.FILE_READABLE
    assert report["main"]["max_plan_version"] == hwm
    assert report["explanation"]
    # 100 % read-only: ani jeden bajt, ani jeden nowy plik, ani jeden inode.
    assert _tree_fingerprint(plan_store) == before


def test_diagnose_reports_io_unavailable_sidecar(plan_store, monkeypatch):
    """EACCES przy ODCZYCIE sidecara ma własną, odrębną klasyfikację."""
    _build_on_era_store(monkeypatch)
    _fail_sidecar_read(monkeypatch)

    before = _tree_fingerprint(plan_store)
    report = RH.diagnose()

    assert report["sidecar"]["status"] == RH.SIDECAR_IO_UNAVAILABLE
    assert report["verdict"] == RH.VERDICT_BLOCKED
    assert "uprawnie" in report["explanation"]
    assert report["repair_possible"] is False
    assert _tree_fingerprint(plan_store) == before


def test_diagnose_on_clean_system_without_sidecar_is_ok(plan_store, monkeypatch):
    """System, na którym flaga nigdy nie była włączona, nie jest awarią."""
    _set_plan_guard(monkeypatch, False)
    PM.save_plan("c1", _plan_body("off-only"))
    assert not PM.version_hwm_path().exists()

    report = RH.diagnose()
    assert report["sidecar"]["status"] == RH.SIDECAR_MISSING
    assert report["verdict"] == RH.VERDICT_OK
    assert report["repair_possible"] is False


def test_diagnose_flags_blocked_when_main_is_lost(plan_store, monkeypatch):
    """Dokładnie ten stan, w którym silnik loguje PLAN_VERSION_RECOVERY_BLOCKED."""
    hwm = _build_on_era_store(monkeypatch)
    _damage("unproven", hwm)
    PM.PLANS_FILE.write_text("{ lost main", encoding="utf-8")

    report = RH.diagnose()
    assert report["sidecar"]["status"] == RH.SIDECAR_UNPROVEN
    assert report["main"]["status"] == RH.FILE_CONTENT_REJECTED
    assert report["verdict"] == RH.VERDICT_BLOCKED
    # Naprawa NIE jest możliwa: brak czytelnego maina = brak dowodu.
    assert report["repair_possible"] is False


# ─── naprawa ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", [
    "malformed_json", "wrong_schema", "bad_value_type",
    "below_epoch_floor", "bad_marker_type", "missing",
])
def test_repair_rebuilds_the_proof_from_a_readable_main(
    plan_store, monkeypatch, state
):
    """Naprawa odtwarza dowód ciągłości ze stanu faktycznego, nie z domysłu."""
    hwm = _build_on_era_store(monkeypatch)
    _damage(state, hwm)
    _ack(monkeypatch)

    result = RH.repair()

    assert result["after"]["sidecar"]["status"] == RH.SIDECAR_PROVEN
    assert result["after"]["verdict"] == RH.VERDICT_OK
    # Nigdy poniżej tego, co widać na dysku; zawsze co najmniej o zapas wyżej.
    assert result["new_last_issued"] >= hwm + RH.DEFAULT_MARGIN
    assert result["new_last_issued"] >= PM._VERSION_EPOCH_FLOOR
    # Silnik znów czyta plany po naprawie.
    _set_plan_guard(monkeypatch, True)
    assert set(PM.load_plans()) == {"c1", "c2"}


def test_repair_backs_up_the_damaged_sidecar_before_overwriting(
    plan_store, monkeypatch
):
    """Operator nigdy nie traci bajtów, które zastał."""
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    damaged_bytes = PM.version_hwm_path().read_bytes()
    _ack(monkeypatch)

    result = RH.repair()

    backup = Path(result["backup"])
    assert backup.exists()
    assert backup.read_bytes() == damaged_bytes


def test_repair_never_lowers_a_high_burned_hwm(plan_store, monkeypatch):
    """Spalone HWM z ery ON jest zachowane, choć main jest dużo niżej."""
    _build_on_era_store(monkeypatch)
    burned = PM._VERSION_EPOCH_FLOOR + 999_999
    PM.version_hwm_path().write_text(json.dumps({
        "schema": PM._VERSION_HWM_SCHEMA,
        "last_issued": burned,
        "covers_all_issued": False,
    }), encoding="utf-8")
    _ack(monkeypatch)

    result = RH.repair()

    assert result["new_last_issued"] == burned + RH.DEFAULT_MARGIN
    assert result["previous_last_issued"] == burned


def test_repair_refuses_without_the_explicit_ack(plan_store, monkeypatch):
    """Nic nie dzieje się przypadkiem — brak tokenu = odmowa i zero zapisów."""
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    monkeypatch.delenv(RH.ACK_ENV, raising=False)

    before = _tree_fingerprint(plan_store)
    with pytest.raises(RH.RepairRefused, match=RH.ACK_ENV):
        RH.repair()
    assert _tree_fingerprint(plan_store) == before


def test_repair_refuses_a_wrong_ack_value(plan_store, monkeypatch):
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    monkeypatch.setenv(RH.ACK_ENV, "tak")

    with pytest.raises(RH.RepairRefused, match=RH.ACK_ENV):
        RH.repair()


@pytest.mark.parametrize("loss", ["corrupt", "missing"])
def test_repair_refuses_when_main_is_unreadable(plan_store, monkeypatch, loss):
    """Bez czytelnego maina nie ma dowodu — narzędzie NIE zgaduje."""
    hwm = _build_on_era_store(monkeypatch)
    _damage("unproven", hwm)
    if loss == "corrupt":
        PM.PLANS_FILE.write_text("{ lost main", encoding="utf-8")
    else:
        PM.PLANS_FILE.unlink()
    _ack(monkeypatch)

    sidecar_before = PM.version_hwm_path().read_bytes()
    with pytest.raises(RH.RepairRefused, match="main jest nieczytelny"):
        RH.repair()
    assert PM.version_hwm_path().read_bytes() == sidecar_before


def test_repair_refuses_when_the_sidecar_cannot_be_read(plan_store, monkeypatch):
    """Nieodczytany dowód mógł być prawdziwy — nie nadpisujemy go w ciemno."""
    _build_on_era_store(monkeypatch)
    _ack(monkeypatch)
    _fail_sidecar_read(monkeypatch)

    with pytest.raises(RH.RepairRefused, match="uprawnie"):
        RH.repair()


def test_repair_surfaces_a_sidecar_write_failure(plan_store, monkeypatch):
    """EACCES przy ZAPISIE nie może zostać połknięty jako udana naprawa."""
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    damaged_bytes = PM.version_hwm_path().read_bytes()
    _ack(monkeypatch)
    _fail_sidecar_write(monkeypatch)

    with pytest.raises(OSError, match="sidecar write failure"):
        RH.repair()
    assert PM.version_hwm_path().read_bytes() == damaged_bytes


def test_repair_unblocks_recovery_after_the_operator_restores_main(
    plan_store, monkeypatch
):
    """Pełna ścieżka runbooka: blokada → odmowa → odtworzenie maina → naprawa."""
    hwm = _build_on_era_store(monkeypatch)
    healthy_main = PM.PLANS_FILE.read_bytes()
    _damage("unproven", hwm)
    PM.PLANS_FILE.write_text("{ lost main", encoding="utf-8")
    _ack(monkeypatch)

    # 1. Silnik jest zablokowany dokładnie tak, jak opisuje runbook.
    _set_plan_guard(monkeypatch, True)
    with pytest.raises(PM.PlanVersionStateError, match="continuity"):
        PM.load_plans()

    # 2. Narzędzie odmawia, dopóki nie ma czytelnego maina.
    with pytest.raises(RH.RepairRefused, match="main jest nieczytelny"):
        RH.repair()

    # 3. Operator odtwarza main z kopii, potem naprawa przechodzi.
    PM.PLANS_FILE.write_bytes(healthy_main)
    _clear_plan_cache()
    result = RH.repair()

    assert result["after"]["verdict"] == RH.VERDICT_OK
    _clear_plan_cache()
    assert set(PM.load_plans()) == {"c1", "c2"}


def test_repair_margin_is_configurable(plan_store, monkeypatch):
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    _ack(monkeypatch)

    result = RH.repair(margin=7)
    assert result["new_last_issued"] == hwm + 7


# ─── kontrakt narzędzia (ratchety) ─────────────────────────────────────────

def test_tool_has_no_second_sidecar_writer(plan_store):
    """Ratchet: sidecar zapisuje WYŁĄCZNIE kanoniczny writer silnika."""
    source = Path(RH.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"atomic_write_json", "write_text", "write_bytes"}
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name:
                called.add(name)
    assert not (called & forbidden), (
        f"narzędzie nie może mieć własnego writera sidecara: {called & forbidden}"
    )
    assert "_write_version_hwm" in called
    # Brak własnej kopii schematu = brak drugiego źródła prawdy o formacie.
    assert PM._VERSION_HWM_SCHEMA not in source


def test_canonical_writer_refuses_to_overwrite_unparsable_bytes(
    plan_store, monkeypatch
):
    """Fakt silnikowy, przez który uszkodzony sidecar NIE leczy się sam.

    `atomic_write_json` czyta i waliduje stan zastany, a `_write_version_hwm`
    nie podaje polityki `replace` — więc nieparsowalne bajty zatrzymują nawet
    zapis silnika. To jedyny powód, dla którego naprawa musi najpierw usunąć
    plik. Gdyby ten test kiedyś zzieleniał inaczej, krok usuwania w narzędziu
    stałby się zbędny i należy go wtedy wyciąć, a nie zostawiać.
    """
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    with pytest.raises(json.JSONDecodeError):
        PM._write_version_hwm(hwm + 1, covers_all_issued=True)


def test_repair_removes_only_bytes_it_has_already_copied(plan_store, monkeypatch):
    """Usunięcie pliku jest dopuszczalne wyłącznie po wykonaniu kopii."""
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    damaged_bytes = PM.version_hwm_path().read_bytes()
    _ack(monkeypatch)

    seen = {}
    real_unlink = Path.unlink

    def spy(self, *args, **kwargs):
        if self == PM.version_hwm_path():
            backups = sorted(plan_store.glob("*.bak-repair-*"))
            seen["backup_before_unlink"] = [b.read_bytes() for b in backups]
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", spy)
    RH.repair()
    assert seen["backup_before_unlink"] == [damaged_bytes]


def test_diagnose_takes_no_plan_lock(plan_store, monkeypatch):
    """Diagnoza musi działać nawet gdy lock trzyma ktoś inny — i nic nie tworzyć."""
    calls = []
    real_locked = PM._locked

    def spy(exclusive):
        calls.append(exclusive)
        return real_locked(exclusive)

    monkeypatch.setattr(PM, "_locked", spy)
    _build_on_era_store(monkeypatch)
    calls.clear()

    RH.diagnose()
    assert calls == []


def test_repair_runs_under_the_exclusive_plan_lock(plan_store, monkeypatch):
    """Naprawa nie może ścigać się z żywym writerem planów."""
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    _ack(monkeypatch)

    calls = []
    real_locked = PM._locked

    def spy(exclusive):
        calls.append(exclusive)
        return real_locked(exclusive)

    monkeypatch.setattr(PM, "_locked", spy)
    RH.repair()
    assert calls and calls[0] is True


# ─── CLI ───────────────────────────────────────────────────────────────────

def test_cli_diagnose_exit_codes(plan_store, monkeypatch, capsys):
    hwm = _build_on_era_store(monkeypatch)
    assert RH.main([]) == 0                      # PROVEN + pokrycie = zdrowo
    _damage("malformed_json", hwm)
    assert RH.main([]) == 1                      # wymaga działania operatora
    out = capsys.readouterr().out
    assert "BLOKADA" in out
    assert "docs/runbooks/plan-version-hwm.md" in out or "--repair" in out


def test_cli_repair_without_ack_exits_three(plan_store, monkeypatch, capsys):
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    monkeypatch.delenv(RH.ACK_ENV, raising=False)

    assert RH.main(["--repair"]) == 3
    err = capsys.readouterr().err
    assert "NAPRAWA ODMÓWIONA" in err
    assert "plan-version-hwm.md" in err


def test_cli_rejects_negative_margin(plan_store):
    with pytest.raises(SystemExit) as raised:
        RH.main(["--margin", "-1"])
    assert raised.value.code == 2


def test_cli_repair_happy_path(plan_store, monkeypatch, capsys):
    hwm = _build_on_era_store(monkeypatch)
    _damage("malformed_json", hwm)
    _ack(monkeypatch)

    assert RH.main(["--repair"]) == 0
    out = capsys.readouterr().out
    assert "NAPRAWA WYKONANA" in out
    assert RH.SIDECAR_PROVEN in out
    assert RH.main([]) == 0                      # po naprawie diagnoza = zdrowo


def test_cli_json_output_is_machine_readable(plan_store, monkeypatch, capsys):
    _build_on_era_store(monkeypatch)
    RH.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == RH.VERDICT_OK
    assert payload["paths"]["sidecar"].endswith(".version_hwm")
