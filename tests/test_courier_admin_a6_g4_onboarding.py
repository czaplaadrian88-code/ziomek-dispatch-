"""A-6/G4 (K6+K8) — onboarding waliduje CID u źródła + dwukierunkowe kolizje.

Bramka: engine.a6-onboarding-input-and-conflicts

DEFEKT na masterze (RED tu, GREEN na kandydacie):
  K6 — add_new_courier nie ma ŻADNEJ walidacji cid; boolean/float/ujemny/
       niecyfrowy/whitespace CID przechodzi checki kolizji i TRAFIA do 5 plików.
  K8 — kolizja kurier_full_names sprawdzana jednokierunkowo; full_name związany
       z INNYM aliasem oraz CID cudzej tożsamości nie są wykrywane.

Hermetyczny: wszystkie 5 ścieżek zmonkeypatchowane na tmp (stałe modułu czytane
late-bound w ciele funkcji), zero I/O do żywego dispatch_state.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os

import pytest

from dispatch_v2 import courier_admin as CA


# --- fixtury -----------------------------------------------------------------
def _seed(tmp_path, *, kids=None, piny=None, tiers=None, names=None, full=None):
    files = {
        "KURIER_IDS": ("kurier_ids.json", kids if kids is not None else {"Test Ku": 900, "Test Kurierski": 900}),
        "KURIER_PINY": ("kurier_piny.json", piny if piny is not None else {"1234": "Test Ku"}),
        "COURIER_TIERS": ("courier_tiers.json", tiers if tiers is not None else {"900": {"name": "Test Ku"}}),
        "COURIER_NAMES": ("courier_names.json", names if names is not None else {"900": "Test Ku"}),
        "KURIER_FULL_NAMES": ("kurier_full_names.json", full if full is not None else {"Test Ku": "Test Kurierski"}),
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


def _snapshot(paths):
    return {c: _read(paths, c) for c in paths}


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _physical(paths):
    """(sha256, mtime_ns) per file — a rejected write must leave BYTES + mtime untouched."""
    return {c: (_sha256(paths[c]), os.stat(paths[c]).st_mtime_ns) for c in paths}


def _bak_count(tmp_path):
    """add_new_courier writes .bak-pre-add-* backups only once past the CID gate."""
    return len(glob.glob(str(tmp_path / "*.bak-pre-add-*")))


# --- K6: walidacja CID u źródła ---------------------------------------------
# każdy z tych CID jest NIEKANONICZNY i NIGDY nie może trafić do żadnego z 5 plików.
BAD_CIDS = [
    pytest.param(True, id="bool-True"),
    pytest.param(False, id="bool-False"),
    pytest.param(3.5, id="float"),
    pytest.param(17.0, id="float-integer"),
    pytest.param(-5, id="negative-int"),
    pytest.param("abc", id="non-digit-str"),
    pytest.param("1_7", id="underscore"),
    pytest.param("+17", id="plus-sign"),
    pytest.param(" 17 ", id="whitespace"),
    pytest.param("017", id="leading-zero"),
    pytest.param("", id="empty-str"),
    pytest.param(None, id="none"),
    # A-6/G4 iter2 (FINDING-1, security-blind 2026-08-04): int 0 leaked przez bramkę
    # bo canonical_numeric_cid(0)=='0'; kanoniczny czytelnik courier_availability._canon_cid(0)
    # RZUCA (0 falsy -> ""), więc pisarz utrwaliłby tożsamość, której czytelnik nie odczyta.
    # 0 jest na liście MUST-reject i żaden żywy kurier nie ma CID 0 (min 21).
    pytest.param(0, id="int-zero"),
    pytest.param(0.0, id="float-zero"),
    pytest.param("0", id="str-zero"),
    pytest.param("00", id="str-double-zero"),
]


@pytest.mark.parametrize("bad_cid", BAD_CIDS)
def test_k6_noncanonical_cid_never_written_to_any_of_5_files(tmp_path, monkeypatch, bad_cid):
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    before = _snapshot(paths)
    phys_before = _physical(paths)

    with pytest.raises(ValueError):
        CA.add_new_courier(bad_cid, "Jan Kowalski")

    # asercja na WSZYSTKICH 5 plikach: nic się nie zmieniło = zły CID nigdzie nie trafił.
    # (a) treść JSON, (b) FIZYCZNIE bajty sha256 + mtime_ns, (c) ZERO backupów .bak — write
    # path (a tym samym backup) rusza dopiero PO bramce CID, więc odrzucenie = 0 baków.
    after = _snapshot(paths)
    for c in paths:
        assert after[c] == before[c], f"{c} zmienione — niekanoniczny CID {bad_cid!r} wyciekł do rejestru"
    assert _physical(paths) == phys_before, f"bajty/mtime tknięte — CID {bad_cid!r} wyciekł fizycznie"
    assert _bak_count(tmp_path) == 0, f"powstał backup .bak — CID {bad_cid!r} przeszedł bramkę i zaczął zapis"


def test_k6_legit_int_cid_still_writes_all_five(tmp_path, monkeypatch):
    """PARYTET: legalne dodanie realnego int CID przechodzi bez zmiany zachowania."""
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    res = CA.add_new_courier(901, "Nowy Testowy")
    assert res["cid"] == 901 and res["alias"] == "Nowy Te"
    assert _read(paths, "KURIER_IDS")["Nowy Te"] == 901
    assert _read(paths, "KURIER_IDS")["Nowy Testowy"] == 901
    assert _read(paths, "COURIER_TIERS")["901"]["name"] == "Nowy Te"
    assert _read(paths, "COURIER_NAMES")["901"] == "Nowy Te"
    assert _read(paths, "KURIER_FULL_NAMES")["Nowy Te"] == "Nowy Testowy"


def test_k6_positive_int_cid_8001_still_writes_all_five(tmp_path, monkeypatch):
    """PARYTET (iter2): fix 0-boundary NIE zawęża legalnych DODATNICH int CID.

    8001 to realny zakres roster (żywe min 21); musi dalej przechodzić bramkę i
    zapisać wszystkie 5 rejestrów — dowód że `int(s) > 0` odrzuca tylko 0, nie >0.
    """
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    res = CA.add_new_courier(8001, "Piotr Wysocki")
    assert res["cid"] == 8001 and res["alias"] == "Piotr Wy"
    assert _read(paths, "KURIER_IDS")["Piotr Wy"] == 8001
    assert _read(paths, "KURIER_IDS")["Piotr Wysocki"] == 8001
    assert _read(paths, "COURIER_TIERS")["8001"]["name"] == "Piotr Wy"
    assert _read(paths, "COURIER_NAMES")["8001"] == "Piotr Wy"
    assert _read(paths, "KURIER_FULL_NAMES")["Piotr Wy"] == "Piotr Wysocki"


# --- K8: dwukierunkowe kolizje ----------------------------------------------
def test_k8_reverse_full_name_collision_rejected(tmp_path, monkeypatch):
    """full_name już związany z INNYM aliasem (legacy klucz) -> odmowa.

    Master: brak reverse-checku -> ZAPISUJE duplikat tożsamości (RED).
    """
    # full ma legacy alias "Jan K" (NIE derive("Jan Kowalski")=="Jan Ko") -> tego samego
    # full_name'a nie łapie ani check aliasu, ani forward-check full.
    paths = _seed(
        tmp_path,
        kids={"Jan K": 300},
        piny={"1111": "Jan K"},
        tiers={"300": {"name": "Jan K"}},
        names={"300": "Jan K"},
        full={"Jan K": "Jan Kowalski"},
    )
    _patch_paths(monkeypatch, paths)
    before = _snapshot(paths)
    with pytest.raises(ValueError):
        CA.add_new_courier(950, "Jan Kowalski")  # derive -> "Jan Ko", cid 950 świeży
    after = _snapshot(paths)
    for c in paths:
        assert after[c] == before[c], f"{c} zmienione — reverse full_name collision niewyłapany"


def test_k8_cid_owned_by_another_identity_rejected(tmp_path, monkeypatch):
    """CID już należący do innej tożsamości w kurier_ids (bez wpisu w tiers) -> odmowa.

    Master: sprawdza tylko str(cid) in tiers; cid obecny w kurier_ids ale nie w tiers
    przechodzi i tworzy drugiego właściciela tego samego CID (RED).
    """
    paths = _seed(
        tmp_path,
        kids={"Stary Ku": 500, "Stary Kurier": 500},
        piny={"2222": "Stary Ku"},
        tiers={},           # celowo BEZ wpisu tiers dla 500
        names={},
        full={"Stary Ku": "Stary Kurier"},
    )
    _patch_paths(monkeypatch, paths)
    before = _snapshot(paths)
    with pytest.raises(ValueError):
        CA.add_new_courier(500, "Nowy Wlasciciel")  # cid 500 już należy do "Stary Ku"
    after = _snapshot(paths)
    for c in paths:
        assert after[c] == before[c], f"{c} zmienione — CID cudzej tożsamości niewyłapany"


def test_k8_forward_full_name_collision_still_fires(tmp_path, monkeypatch):
    """Istniejący jednokierunkowy check (alias->inny full_name) nadal działa (parytet)."""
    paths = _seed(
        tmp_path,
        kids={"Adam N": 400},
        piny={"3333": "Adam N"},
        tiers={"400": {"name": "Adam N"}},
        names={"400": "Adam N"},
        full={"Adam No": "Adam Nowakowski"},
    )
    _patch_paths(monkeypatch, paths)
    # derive("Adam Nowak") == "Adam No", ale full["Adam No"] == "Adam Nowakowski" != "Adam Nowak"
    with pytest.raises(ValueError):
        CA.add_new_courier(450, "Adam Nowak")


def test_k8_legit_new_courier_passes_parity(tmp_path, monkeypatch):
    """Świeży kurier (nowy alias, nowy full_name, nowy CID) przechodzi — parytet."""
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    res = CA.add_new_courier(912, "Marek Zielinski")
    assert res["cid"] == 912
    assert _read(paths, "KURIER_FULL_NAMES")["Marek Zi"] == "Marek Zielinski"
