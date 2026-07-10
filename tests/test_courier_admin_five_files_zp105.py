"""Z-P1-05 follow-up (ACK Adrian 2026-07-10): onboarding pisze courier_names.json.

Do 2026-07-10 `courier_admin.add_new_courier` pisal 4 pliki i POMIJAL
dispatch_state/courier_names.json -> 19 CID bez wpisu (raport identity).
Ten test przypina kontrakt 5-plikowej transakcji + rollback on partial fail.

Hermetyczny: wszystkie 5 sciezek zmonkeypatchowane na tmp (stale modulu czytane
late-bound w ciele funkcji — C17-safe), zero I/O do zywego dispatch_state.
"""
from __future__ import annotations

import json

import pytest

from dispatch_v2 import courier_admin as CA


def _seed(tmp_path):
    """Minimalny, anonimowy roster startowy (5 plikow)."""
    files = {
        "KURIER_IDS": ("kurier_ids.json", {"Test Ku": 900, "Test Kurierski": 900}),
        "KURIER_PINY": ("kurier_piny.json", {"1234": "Test Ku"}),
        "COURIER_TIERS": ("courier_tiers.json", {"900": {"name": "Test Ku"}}),
        "COURIER_NAMES": ("courier_names.json", {"900": "Test Ku"}),
        "KURIER_FULL_NAMES": ("kurier_full_names.json", {"Test Ku": "Test Kurierski"}),
    }
    paths = {}
    for const, (fname, data) in files.items():
        p = tmp_path / fname
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        paths[const] = str(p)
    return paths


def _patch_paths(monkeypatch, paths):
    for const, p in paths.items():
        monkeypatch.setattr(CA, const, p)
    monkeypatch.setattr(
        CA, "ALL_FILES",
        [paths["KURIER_IDS"], paths["KURIER_PINY"], paths["COURIER_TIERS"],
         paths["COURIER_NAMES"], paths["KURIER_FULL_NAMES"]],
    )


def _read(paths, const):
    return json.loads(open(paths[const], encoding="utf-8").read())


def test_add_new_courier_writes_all_five_files(tmp_path, monkeypatch):
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    res = CA.add_new_courier(901, "Nowy Testowy")
    assert res["cid"] == 901 and res["alias"] == "Nowy Te"
    assert _read(paths, "KURIER_IDS")["Nowy Te"] == 901
    assert _read(paths, "KURIER_IDS")["Nowy Testowy"] == 901
    assert _read(paths, "KURIER_PINY")[res["pin"]] == "Nowy Te"
    assert _read(paths, "COURIER_TIERS")["901"]["name"] == "Nowy Te"
    # NOWY kontrakt: courier_names dostaje krotka nazwe panelowa (koniec luki 19 CID)
    assert _read(paths, "COURIER_NAMES")["901"] == "Nowy Te"
    assert _read(paths, "KURIER_FULL_NAMES")["Nowy Te"] == "Nowy Testowy"
    # stare wpisy nietkniete
    assert _read(paths, "COURIER_NAMES")["900"] == "Test Ku"


def test_partial_fail_rolls_back_all_five(tmp_path, monkeypatch):
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    before = {c: _read(paths, c) for c in paths}

    real_write = CA._atomic_write_json

    def failing_write(path, data):
        if path == paths["KURIER_FULL_NAMES"]:  # 5. zapis pada
            raise OSError("symulowany fail 5. pliku")
        real_write(path, data)

    monkeypatch.setattr(CA, "_atomic_write_json", failing_write)
    with pytest.raises(RuntimeError, match="rolled back"):
        CA.add_new_courier(902, "Pechowy Przypadek")
    # WSZYSTKIE 5 plikow przywrocone do stanu sprzed (w tym juz-zapisane 1-4)
    for c in paths:
        assert _read(paths, c) == before[c], f"{c} nie wrocil po rollbacku"


def test_conflict_checks_still_fire(tmp_path, monkeypatch):
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    with pytest.raises(ValueError, match="juz przypisany"):
        CA.add_new_courier(999, "Test Kutwa")  # alias 'Test Ku' -> cid 900 != 999
