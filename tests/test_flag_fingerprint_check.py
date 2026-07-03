"""L0.1 (2026-07-02) — testy tools/flag_fingerprint_check.py.

Rekoncyliacja EFEKTYWNEGO stanu flag per serwis (flags.json vs FLAG_FINGERPRINT
z logów vs Environment= drop-inów). Wszystkie testy IO HERMETYCZNE: monkeypatch
ścieżek na tmp_path + assert ANTY-PROD (żaden test nie czyta realnych logów/
flags.json/systemd). Klasy: INTERMITTENT-COLD, JSON-DRIFT, ENV-DEAD, COVERAGE-GAP.
"""
import os

import pytest

from dispatch_v2.tools import flag_fingerprint_check as ffc


def _write_fp(path, proc, flags: dict):
    """Dopisz linię FLAG_FINGERPRINT (format produkcyjny) do pliku logu."""
    body = " ".join(f"{k}={v}" for k, v in flags.items())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"2026-07-02 10:00:00 [INFO] x: FLAG_FINGERPRINT proc={proc} {body}\n")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Izoluje wszystkie 4 źródła na tmp_path + assert anty-prod. Zwraca helpery."""
    logs = tmp_path / "logs"; logs.mkdir()
    systemd = tmp_path / "systemd"; systemd.mkdir()
    fjson_path = tmp_path / "flags.json"

    # ── ASSERT ANTY-PROD: nic nie może wskazywać na realne ścieżki ──
    for p in (str(logs), str(systemd), str(fjson_path)):
        assert str(tmp_path) in p and "/root/.openclaw/workspace/scripts" not in p

    monkeypatch.setattr(ffc, "LOGS_DIR", str(logs))
    monkeypatch.setattr(ffc, "SYSTEMD_DIR", str(systemd))
    monkeypatch.setattr(ffc, "FLAGS_JSON", str(fjson_path))
    # Dwa proc-e testowe; log/unit pod tmp. journalctl-fallback nieosiągalny bo
    # pliki logów istnieją (parse bierze plik pierwszy).
    monkeypatch.setattr(ffc, "SERVICES", {
        "shadow": ("shadow.log", "dispatch-shadow.service", "dispatch-shadow"),
        "czasowka": ("czasowka.log", "dispatch-czasowka.service", "dispatch-czasowka"),
    })

    class Box:
        pass
    b = Box()
    b.logs, b.systemd, b.fjson_path, b.tmp = logs, systemd, fjson_path, tmp_path
    b.log = lambda proc: str(logs / ffc.SERVICES[proc][0])
    return b


def _set_decision(monkeypatch, bool_flags, numeric=()):
    monkeypatch.setattr(ffc, "_decision_flags", lambda: (set(bool_flags), set(numeric)))


def _write_flags(path, d):
    import json
    path.write_text(json.dumps(d), encoding="utf-8")


# ── helpery czyste ──────────────────────────────────────────────────────────

def test_fjson_bool_mapping():
    assert ffc._fjson_bool(True) == "1"
    assert ffc._fjson_bool(False) == "0"
    assert ffc._fjson_bool(1) == "1"
    assert ffc._fjson_bool("T1") is None      # numeryczna/tekstowa → poza fingerprintem
    assert ffc._fjson_bool(60.0) is None


def test_drift_count_only_mismatch_bool_in_json():
    fjson = {"A": True, "B": False, "C": True}
    bool_flags = {"A", "B", "C"}
    fp = {"A": "1", "B": "1", "C": "0", "D": "0"}   # B i C ≠ json, D poza json
    assert ffc._drift_count(fp, fjson, bool_flags) == 2


# ── reconcile: klasy rozjazdów ───────────────────────────────────────────────

def test_json_drift_single_flag_benign(sandbox, monkeypatch):
    """Pojedyncza flaga fingerprint≠flags.json (< próg cold) = JSON-DRIFT, NIE cold."""
    _set_decision(monkeypatch, {"ENABLE_X"})
    _write_flags(sandbox.fjson_path, {"ENABLE_X": True})
    _write_fp(sandbox.log("shadow"), "shadow", {"ENABLE_X": "1"})
    _write_fp(sandbox.log("czasowka"), "czasowka", {"ENABLE_X": "0"})  # stale
    res = ffc.reconcile()
    klasy = {f["klass"] for f in res["findings"]}
    assert "JSON-DRIFT" in klasy
    assert "INTERMITTENT-COLD" not in klasy
    drift = [f for f in res["findings"] if f["klass"] == "JSON-DRIFT"][0]
    assert drift["flag"] == "ENABLE_X"
    assert drift["detail"]["fingerprint"] == {"czasowka": "0"}


def test_intermittent_cold_collapses_wholesale_drift(sandbox, monkeypatch):
    """Wholesale rozjazd (≥COLD_DRIFT_MIN flag naraz = common.py defaulty) →
    JEDEN INTERMITTENT-COLD, per-flag JSON-DRIFT tego proc POMINIĘTY."""
    n = ffc.COLD_DRIFT_MIN + 5
    flags = [f"ENABLE_C{i}" for i in range(n)]
    _set_decision(monkeypatch, set(flags))
    _write_flags(sandbox.fjson_path, {f: True for f in flags})   # kanon = wszystkie ON
    # shadow OK (wszystkie 1); czasowka: 2 emity real + 2 emity COLD (wszystkie 0)
    _write_fp(sandbox.log("shadow"), "shadow", {f: "1" for f in flags})
    _write_fp(sandbox.log("czasowka"), "czasowka", {f: "1" for f in flags})
    _write_fp(sandbox.log("czasowka"), "czasowka", {f: "0" for f in flags})  # COLD
    _write_fp(sandbox.log("czasowka"), "czasowka", {f: "1" for f in flags})
    _write_fp(sandbox.log("czasowka"), "czasowka", {f: "0" for f in flags})  # COLD = last
    res = ffc.reconcile()
    cold = [f for f in res["findings"] if f["klass"] == "INTERMITTENT-COLD"]
    assert len(cold) == 1
    assert cold[0]["flag"] == "proc:czasowka"
    assert cold[0]["detail"]["cold_recent"] == 2
    assert cold[0]["detail"]["ostatni_snapshot_cold"] is True
    # per-flag drift czasówki (last=cold) NIE zaśmieca wyniku
    drift_flags = {f["flag"] for f in res["findings"] if f["klass"] == "JSON-DRIFT"}
    assert not any(x.startswith("ENABLE_C") for x in drift_flags)


def test_env_dead_decision_flag_in_unit(sandbox, monkeypatch):
    """Flaga DECYZYJNA ustawiona w Environment= unitu + obecna w flags.json =
    ENV-DEAD (flags.json przykrywa env; env martwy)."""
    _set_decision(monkeypatch, {"ENABLE_X"})
    _write_flags(sandbox.fjson_path, {"ENABLE_X": True})
    _write_fp(sandbox.log("shadow"), "shadow", {"ENABLE_X": "1"})
    _write_fp(sandbox.log("czasowka"), "czasowka", {"ENABLE_X": "1"})
    (sandbox.systemd / "dispatch-shadow.service").write_text(
        "[Service]\nEnvironment=ENABLE_X=0\n")
    res = ffc.reconcile()
    envdead = [f for f in res["findings"] if f["klass"] == "ENV-DEAD"]
    assert len(envdead) == 1 and envdead[0]["flag"] == "ENABLE_X"


def test_coverage_gap_missing_in_one_proc(sandbox, monkeypatch):
    """Flaga w jednym żywym fingerprincie, brak w drugim (proc starszy) =
    COVERAGE-GAP (stale-process)."""
    _set_decision(monkeypatch, {"ENABLE_NEW"})
    _write_flags(sandbox.fjson_path, {"ENABLE_NEW": False})
    _write_fp(sandbox.log("shadow"), "shadow", {"ENABLE_NEW": "0"})
    _write_fp(sandbox.log("czasowka"), "czasowka", {"OTHER": "0"})  # brak ENABLE_NEW
    res = ffc.reconcile()
    gaps = [f for f in res["findings"] if f["klass"] == "COVERAGE-GAP"]
    assert any(g["flag"] == "ENABLE_NEW" and "czasowka" in g["detail"]["brak_w"]
               for g in gaps)


def test_clean_system_no_findings(sandbox, monkeypatch):
    """Wszystkie serwisy spójne z flags.json → zero rozjazdów."""
    _set_decision(monkeypatch, {"ENABLE_X", "ENABLE_Y"})
    _write_flags(sandbox.fjson_path, {"ENABLE_X": True, "ENABLE_Y": False})
    for proc in ("shadow", "czasowka"):
        _write_fp(sandbox.log(proc), proc, {"ENABLE_X": "1", "ENABLE_Y": "0"})
    res = ffc.reconcile()
    assert res["findings"] == []
    assert "brak" in ffc.render(res)


def test_render_and_jsonl_do_not_crash(sandbox, monkeypatch):
    _set_decision(monkeypatch, {"ENABLE_X"})
    _write_flags(sandbox.fjson_path, {"ENABLE_X": True})
    _write_fp(sandbox.log("shadow"), "shadow", {"ENABLE_X": "1"})
    _write_fp(sandbox.log("czasowka"), "czasowka", {"ENABLE_X": "0"})
    res = ffc.reconcile()
    assert "FLAG FINGERPRINT CHECK" in ffc.render(res)
    lines = list(ffc._jsonl_lines(res))
    assert lines and lines[0].startswith("{")


def test_reconcile_live_read_only_smoke():
    """Smoke na żywym systemie (read-only): musi policzyć bez wyjątku + zwrócić
    strukturę. Nie asertuje treści (stan procesów zmienny)."""
    res = ffc.reconcile()
    assert set(res) >= {"procs_live", "procs_dead", "fingerprint_sizes", "findings"}
    assert "FLAG FINGERPRINT CHECK" in ffc.render(res)
