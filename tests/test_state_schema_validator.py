#!/usr/bin/env python3
"""test_state_schema_validator.py — testy READ-ONLY walidatora schematu stanu.

Dwa kierunki kontraktu:
  (a) ŻYWE pliki dispatch_state -> PASS (exit 0, brak driftu)
  (b) SYNTETYCZNA kopia w /tmp z usuniętym wymaganym kluczem -> DRIFT (exit 1)

NIGDY nie mutujemy prawdziwych plików stanu — wyłącznie kopie w tmp_path.
Telegram (--alert) NIE jest tu wołany.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dispatch_v2.tools import validate_state_schema as vss

STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")


def _baseline() -> dict:
    return vss._load_baseline()


# ----------------------------------------------------------------------------
# (a) Żywe pliki -> PASS
# ----------------------------------------------------------------------------

@pytest.mark.skipif(
    not (STATE_DIR / "orders_state.json").exists(),
    reason="żywe pliki stanu niedostępne w tym środowisku",
)
def test_live_state_passes():
    """Bieżące produkcyjne pliki nie mają driftu -> exit 0."""
    rc = vss.main(["--state-dir", str(STATE_DIR)])
    assert rc == 0


@pytest.mark.skipif(
    not (STATE_DIR / "orders_state.json").exists(),
    reason="żywe pliki stanu niedostępne w tym środowisku",
)
def test_live_state_no_drift_summary():
    summary = vss.run(_baseline(), state_dir=STATE_DIR)
    assert summary["any_drift"] is False
    # Każdy plik z baseline ok albo warn (warn = brak pliku), żaden drift/error.
    for r in summary["files"]:
        assert r["status"] in ("ok", "warn"), (r["file"], r["status"], r["messages"])


# ----------------------------------------------------------------------------
# (b) Syntetyczny drift -> FAIL
# ----------------------------------------------------------------------------

def _copy_live_into(tmp_dir: Path) -> Path:
    """Skopiuj wszystkie 4 żywe pliki do tmp_dir (jeśli istnieją)."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for fn in _baseline()["files"]:
        src = STATE_DIR / fn
        if src.exists():
            shutil.copy2(src, tmp_dir / fn)
    return tmp_dir


@pytest.mark.skipif(
    not (STATE_DIR / "courier_ground_truth.json").exists(),
    reason="żywe pliki stanu niedostępne — brak czego skopiować",
)
def test_synthetic_drift_dict_of_entries_fails(tmp_path):
    """Usuń wymagany klucz z JEDNEGO wpisu dict-of-entries -> drift, exit 1."""
    work = _copy_live_into(tmp_path / "state")
    target = work / "courier_ground_truth.json"

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    victim_id = next(iter(data))
    removed = "last_status_code"
    assert removed in data[victim_id]
    del data[victim_id][removed]
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f)

    rc = vss.main(["--state-dir", str(work)])
    assert rc == 1

    summary = vss.run(_baseline(), state_dir=work)
    assert summary["any_drift"] is True
    gt = next(r for r in summary["files"] if r["file"] == "courier_ground_truth.json")
    assert gt["drift"] is True
    assert gt["missing_key_counts"].get(removed) == 1
    assert victim_id in gt["missing_key_examples"].get(removed, [])


def test_synthetic_drift_flat_object_fails(tmp_path):
    """Usuń wymagany klucz TOP-LEVEL w płaskim wrapperze -> drift, exit 1.

    Hermetyczny (2026-06-21): buduje SYNTETYCZNY plik zgodny ze schematem zamiast
    kopiować ŻYWY panel_packs_cache.json. Żywy plik jest przepisywany przez
    dispatch-panel-watcher co ~minutę (atomic temp→rename) → wyścig odczyt/zapis w
    trakcie ~95 s pełnej suity dawał INTERMITTENT fail (pass-solo / okazjonalnie
    fail-w-suicie). Walidator i jego logika są poprawne — to był wyłącznie coupling
    testu do współbieżnie mutowanego stanu produkcyjnego. Pozostałe pliki baseline
    nieobecne w `work` → WARN (nie drift), więc jedyny drift = usunięty 'packs'."""
    work = tmp_path / "state"
    work.mkdir(parents=True)
    target = work / "panel_packs_cache.json"
    # Zgodny ze schematem baseline (required_keys: ts/packs/tick/orders_in_panel).
    conformant = {"ts": "2026-06-21T00:00:00Z", "packs": {}, "tick": 1, "orders_in_panel": 0}
    target.write_text(json.dumps(conformant), encoding="utf-8")

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "packs" in data
    del data["packs"]
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f)

    rc = vss.main(["--state-dir", str(work)])
    assert rc == 1

    summary = vss.run(_baseline(), state_dir=work)
    ppc = next(r for r in summary["files"] if r["file"] == "panel_packs_cache.json")
    assert ppc["drift"] is True
    assert "packs" in ppc["missing_top_keys"]


def test_non_dict_top_level_is_drift(tmp_path):
    """Top-level lista zamiast dict -> drift."""
    work = tmp_path / "state"
    work.mkdir(parents=True)
    # Zbuduj minimalny baseline-zgodny plik o złym kształcie.
    (work / "orders_state.json").write_text(json.dumps(["nie", "dict"]))
    spec = {
        "filename": "orders_state.json",
        "shape": "dict_of_entries",
        "required_keys": ["status", "commitment_level", "restaurant", "first_seen"],
    }
    rep = vss.validate_file(spec, state_dir=work)
    assert rep["drift"] is True
    assert rep["status"] == "drift"


def test_non_dict_entry_is_drift(tmp_path):
    """Wpis który nie jest dict -> drift z przykładowym id."""
    work = tmp_path / "state"
    work.mkdir(parents=True)
    payload = {
        "111": {"status": "x", "commitment_level": 1, "restaurant": "r", "first_seen": "t"},
        "222": "to_nie_jest_dict",
    }
    (work / "orders_state.json").write_text(json.dumps(payload))
    spec = {
        "filename": "orders_state.json",
        "shape": "dict_of_entries",
        "required_keys": ["status", "commitment_level", "restaurant", "first_seen"],
    }
    rep = vss.validate_file(spec, state_dir=work)
    assert rep["drift"] is True
    assert "222" in rep["non_dict_entry_ids"]


# ----------------------------------------------------------------------------
# Fail-soft: brakujący plik = WARN, nie crash
# ----------------------------------------------------------------------------

def test_missing_file_is_warn_not_drift(tmp_path):
    work = tmp_path / "empty_state"
    work.mkdir(parents=True)
    spec = {
        "filename": "orders_state.json",
        "shape": "dict_of_entries",
        "required_keys": ["status"],
    }
    rep = vss.validate_file(spec, state_dir=work)
    assert rep["status"] == "warn"
    assert rep["drift"] is False


def test_all_missing_files_exit_zero(tmp_path):
    """Pusty katalog stanu: same WARN-y -> exit 0 (brak driftu)."""
    work = tmp_path / "empty_state"
    work.mkdir(parents=True)
    rc = vss.main(["--state-dir", str(work)])
    assert rc == 0


def test_json_output_is_valid(tmp_path, capsys):
    work = tmp_path / "empty_state"
    work.mkdir(parents=True)
    vss.main(["--state-dir", str(work), "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "any_drift" in parsed
    assert "files" in parsed
