"""A-6/G5 (K5) — trwała transakcja onboardingu + recovery + walidatory 5 rootów.

Bramka: engine.a6-journal-extended-validators

DEFEKT na masterze (RED tu, GREEN na kandydacie):
  K5 — ``courier_admin.add_new_courier`` robi 5 SEKWENCYJNYCH atomic-write'ów
       (kurier_ids, kurier_piny, courier_tiers, courier_names, kurier_full_names)
       a jedynym zabezpieczeniem jest rollback z ``.bak`` w ``except Exception``
       WEWNĄTRZ procesu. Twardy crash (kill -9 / OOM / zanik zasilania) pomiędzy
       zapisami:
         * zostawia ROZDARTĄ GENERACJĘ (część rootów nowa, część stara),
         * nie zostawia ŻADNEGO trwałego znacznika transakcji,
         * następne wejście do onboardingu CICHO czyta rozdartą generację i
           dokłada do niej kolejną tożsamość (brak recovery),
         * przy odtwarzaniu nie ma walidacji schematu rootów (przywrócenie
           śmiecia byłoby niewykrywalne).

Wariant ownera (04.08, reskope G5): JEDEN kanoniczny owner transakcji
``identity/journal.py`` — trwały znacznik (journal: lista rootów + sha256
przed/po + ścieżki backupów), zapis 5 plików, commit = usunięcie znacznika.
Wejście do onboardingu NAJPIERW leczy niedokończoną transakcję (rollback do
generacji „przed"), a przy nieczytelnym/niekompletnym/obcym stanie jest
FAIL-CLOSED (jawny ``JournalError``, nigdy cicha kontynuacja). Walidatory
WSZYSTKICH 5 rootów żyją w ``identity/schema`` (delegacja, zero kopii inline)
i biegną i przy zapisie, i przy recovery.

Hermetyczny: wszystkie 5 ścieżek + journal zmonkeypatchowane na tmp, zero I/O
do żywego dispatch_state. Crash wstrzykiwany na poziomie ``os.replace`` (a nie
przez seam modułu), żeby oracle był tak samo prawdziwy na masterze i na
kandydacie — niezależnie od tego, KTÓRY moduł wykonuje finalny rename.
"""
from __future__ import annotations

import ast
import glob
import hashlib
import inspect
import json
import os
import textwrap

import pytest

from dispatch_v2 import courier_admin as CA


# --- fixtury (kształt 1:1 z testami G4/Z-P1-05) ------------------------------
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
    return {c: _sha256(paths[c]) for c in paths}


def _bak_files(tmp_path):
    return sorted(glob.glob(str(tmp_path / "*.bak-pre-add-*")))


def _journal_files(tmp_path):
    """Trwały znacznik transakcji — czymkolwiek jest, MUSI leżeć obok rootów."""
    return sorted(
        p for p in glob.glob(str(tmp_path / "*journal*")) + glob.glob(str(tmp_path / ".*journal*"))
        if not p.endswith(".lock")
    )


def _journal_mod():
    """Owner transakcji. Brak modułu = RED (nie skip) — na masterze go NIE MA."""
    from dispatch_v2.identity import journal
    return journal


# --- symulacja TWARDEGO crashu ----------------------------------------------
class _HardCrash(BaseException):
    """kill -9 / OOM: NIE jest ``Exception``, więc omija in-process rollback
    (``except Exception``) tak samo na masterze jak na kandydacie."""


def _crash_after_k_root_writes(monkeypatch, k, paths):
    """Przerwij proces po k-tym FINALNYM rename do któregoś z 5 rootów.

    Wstrzyknięcie na ``os.replace`` (nie na seamie modułu) — liczone są wyłącznie
    rename'y celujące w 5 rootów, więc zapis samego znacznika transakcji nie
    zużywa budżetu i oracle mierzy dokładnie ROZDARCIE GENERACJI.
    """
    real_replace = os.replace
    roots = {os.path.realpath(p) for p in paths.values()}
    state = {"n": 0, "fired": False}

    def spy(src, dst, *a, **kw):
        if os.path.realpath(dst) in roots and not state["fired"]:
            if state["n"] >= k:
                # JEDNORAZOWO: crash kończy proces. Wszystko po nim to już NOWY
                # proces (następne wejście do onboardingu) — bez wstrzyknięcia.
                state["fired"] = True
                raise _HardCrash(f"symulowany kill -9 po {k} zapisach rootów")
            state["n"] += 1
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", spy)
    return state


def _identity_present(paths, *, alias, full_name, cid):
    """W ilu z 5 rootów widać tożsamość (alias/full_name/cid)? 0 lub 5 = generacja
    spójna; cokolwiek pomiędzy = ROZDARTA GENERACJA (defekt K5)."""
    kids = _read(paths, "KURIER_IDS")
    piny = _read(paths, "KURIER_PINY")
    tiers = _read(paths, "COURIER_TIERS")
    names = _read(paths, "COURIER_NAMES")
    full = _read(paths, "KURIER_FULL_NAMES")
    return {
        "KURIER_IDS": alias in kids or full_name in kids,
        "KURIER_PINY": alias in piny.values(),
        "COURIER_TIERS": str(cid) in tiers,
        "COURIER_NAMES": str(cid) in names,
        "KURIER_FULL_NAMES": alias in full,
    }


# --------------------------------------------------------------------------- #
# (1) ORACLE NEGATYWNY: twardy crash po k-tym zapisie (k=1..4)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("k", [1, 2, 3, 4])
def test_k5_hard_crash_leaves_durable_transaction_marker(tmp_path, monkeypatch, k):
    """Po twardym crashu MUSI zostać trwały znacznik transakcji.

    Master: znacznika nie ma w ogóle (rozdarcie jest niewykrywalne po restarcie)
    -> RED. Kandydat: journal leży obok rootów i opisuje niedokończoną transakcję.
    """
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _crash_after_k_root_writes(monkeypatch, k, paths)

    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")

    marker = _journal_files(tmp_path)
    assert marker, (
        f"crash po {k} zapisach nie zostawił ŻADNEGO trwałego znacznika "
        f"transakcji — rozdarta generacja jest po restarcie niewykrywalna (K5)"
    )


@pytest.mark.parametrize("k", [1, 2, 3, 4])
def test_k5_torn_generation_healed_on_next_entry(tmp_path, monkeypatch, k):
    """Twardy crash po k-tym zapisie -> następne wejście do onboardingu WYKRYWA
    rozdartą generację i ją LECZY (rollback do generacji „przed").

    Master (RED): crash zostawia k z 5 rootów z nową tożsamością, a kolejne
    wejście CICHO dopisuje następnego kuriera do rozdartej generacji — połówkowy
    kurier zostaje w rejestrach na zawsze.
    """
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    before = _snapshot(paths)
    phys_before = _physical(paths)
    crash = _crash_after_k_root_writes(monkeypatch, k, paths)

    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")

    # PRECONDITION: na dysku jest rozdarta generacja (dokładnie k zapisanych rootów).
    assert crash["n"] == k
    torn = _identity_present(paths, alias="Jan Ko", full_name="Jan Kowalski", cid=950)
    assert 0 < sum(torn.values()) < 5, (
        f"precondition: crash po {k} zapisach miał rozdzielić generację, mam {torn}"
    )

    # NASTĘPNE WEJŚCIE (nowy proces w produkcji): leczy zanim cokolwiek doda.
    res = CA.add_new_courier(951, "Piotr Nowak")

    # (a) rozdarta tożsamość ZNIKNĘŁA ze WSZYSTKICH 5 rootów
    after_torn = _identity_present(paths, alias="Jan Ko", full_name="Jan Kowalski", cid=950)
    assert sum(after_torn.values()) == 0, (
        f"po recovery połówkowa tożsamość nadal w rejestrach: {after_torn}"
    )
    # (b) nowy kurier zapisany KOMPLETNIE (heal nie zablokował onboardingu)
    added = _identity_present(paths, alias="Piotr No", full_name="Piotr Nowak", cid=951)
    assert all(added.values()), f"kurier po recovery zapisany częściowo: {added}"
    assert res["cid"] == 951 and res["alias"] == "Piotr No"
    # (c) stan bazowy sprzed crashu nietknięty (leczenie = rollback, nie kasowanie)
    for const in paths:
        assert before[const].items() <= _snapshot(paths)[const].items(), (
            f"{const}: recovery zgubiło rekordy sprzed transakcji"
        )
    assert phys_before  # sanity: baseline policzony
    # (d) znacznik po udanym commicie usunięty
    assert _journal_files(tmp_path) == [], "znacznik transakcji został po commicie"


def test_k5_crash_before_any_write_leaves_roots_untouched(tmp_path, monkeypatch):
    """k=0 (crash przed 1. zapisem): roots bajtowo nietknięte, a następne wejście
    sprząta znacznik i przechodzi normalnie (brak fałszywego alarmu)."""
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    phys_before = _physical(paths)
    _crash_after_k_root_writes(monkeypatch, 0, paths)

    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")
    assert _physical(paths) == phys_before, "crash przed 1. zapisem tknął rooty"

    res = CA.add_new_courier(951, "Piotr Nowak")
    assert res["cid"] == 951
    assert _journal_files(tmp_path) == []


# --------------------------------------------------------------------------- #
# (2) WALIDATORY WSZYSTKICH 5 ROOTÓW przy recovery — parametryzowane śmieci
# --------------------------------------------------------------------------- #
# Dla KAŻDEGO roota: zawartość, która NIE MOŻE zostać przywrócona przy leczeniu
# rozdartej generacji (schemat roota złamany u źródła).
BROKEN_ROOTS = [
    pytest.param("KURIER_IDS", {"Test Ku": True}, id="kurier_ids-bool-cid"),
    pytest.param("KURIER_IDS", {"": 900}, id="kurier_ids-empty-name"),
    pytest.param("KURIER_PINY", {"12": "Test Ku"}, id="kurier_piny-short-pin"),
    pytest.param("KURIER_PINY", {"1234": ""}, id="kurier_piny-empty-alias"),
    pytest.param("COURIER_TIERS", {"017": {"name": "Test Ku"}}, id="courier_tiers-leading-zero-cid"),
    pytest.param("COURIER_TIERS", {"900": "nie-dict"}, id="courier_tiers-scalar-value"),
    pytest.param("COURIER_NAMES", {" 900 ": "Test Ku"}, id="courier_names-whitespace-cid"),
    pytest.param("COURIER_NAMES", {"900": ""}, id="courier_names-empty-name"),
    pytest.param("KURIER_FULL_NAMES", {"Test Ku": ""}, id="kurier_full_names-empty-full"),
    pytest.param("KURIER_FULL_NAMES", {"": "Test Kurierski"}, id="kurier_full_names-empty-alias"),
]


@pytest.mark.parametrize("root_const,garbage", BROKEN_ROOTS)
def test_k5_recovery_refuses_to_restore_broken_root(tmp_path, monkeypatch,
                                                    root_const, garbage):
    """Recovery ODMAWIA leczenia, gdy KTÓRYKOLWIEK root generacji jest śmieciem.

    Scenariusz (realny): generacja „przed" była już zatruta — root i jego backup
    zawierają tę samą, złamaną schematem treść (ręczna edycja rejestru / backup
    z innego źródła / starszy pisarz bez walidatorów), więc sam ``sha256`` nic
    nie wykryje: bajty zgadzają się ze znacznikiem. Jedyną obroną jest walidator
    schematu TEGO roota.

    To jest dokładnie lekcja K5: bryła V12 walidowała przy odtwarzaniu wyłącznie
    PIN+KDF, więc każdy inny root wracał do rejestru bez sprawdzenia. Tu każdy z
    5 rootów ma własny, parametryzowany kontrprzykład i recovery musi odmówić
    ZANIM cokolwiek nadpisze.
    """
    journal = _journal_mod()
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    # k=2: pierwsze dwa rooty zapisane -> generacja rozdarta -> wymagany rollback
    _crash_after_k_root_writes(monkeypatch, 2, paths)
    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")

    marker = _journal_files(tmp_path)[0]
    doc = json.load(open(marker, encoding="utf-8"))
    root_path = paths[root_const]

    # zatruta generacja „przed": ta sama złamana treść w rootcie i w backupie…
    raw = (json.dumps(garbage, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    entry = next(e for e in doc["entries"] if os.path.realpath(e["path"]) == os.path.realpath(root_path))
    for target in (root_path, entry["backup"]):
        with open(target, "wb") as fh:
            fh.write(raw)
    # …a znacznik ma dla niej POPRAWNE sha (bajty się zgadzają — sha nic nie wykryje)
    entry["sha256_before"] = hashlib.sha256(raw).hexdigest()
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)

    phys_before = _physical(paths)
    with pytest.raises(journal.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before, (
        f"{root_const}: recovery ruszyło rooty mimo śmiecia w generacji "
        f"(walidator tego roota nie zadziałał)"
    )


def test_k5_backup_sha_mismatch_is_fail_closed(tmp_path, monkeypatch):
    """Backup o innym sha256 niż stan PRZED ze znacznika = materiał do leczenia
    nie jest tym, co znacznik opisuje -> odmowa (nie zgadujemy)."""
    journal = _journal_mod()
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _make_pending_journal(tmp_path, monkeypatch, paths)
    for b in _bak_files(tmp_path):
        with open(b, "w", encoding="utf-8") as fh:
            json.dump({"Podmieniony Ku": 901}, fh, ensure_ascii=False)

    phys_before = _physical(paths)
    with pytest.raises(journal.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before


def test_k5_write_side_validates_every_root_before_touching_disk(tmp_path, monkeypatch):
    """Walidatory biegną też PRZY ZAPISIE: generacja z niekanonicznym rootem
    (courier_names z kluczem '017') nie zostaje utrwalona — odmowa PRZED
    backupami i PRZED jakimkolwiek zapisem."""
    journal = _journal_mod()
    paths = _seed(tmp_path, names={"900": "Test Ku", "017": "Lead Zero"})
    _patch_paths(monkeypatch, paths)
    phys_before = _physical(paths)

    with pytest.raises((journal.JournalError, ValueError)):
        CA.add_new_courier(951, "Piotr Nowak")

    assert _physical(paths) == phys_before, "zapis ruszył mimo złamanego schematu roota"
    assert _bak_files(tmp_path) == [], "powstały backupy mimo odmowy walidacji"
    assert _journal_files(tmp_path) == [], "został znacznik mimo odmowy walidacji"


# --------------------------------------------------------------------------- #
# (3) FAIL-CLOSED: nieczytelny / niekompletny / obcy stan znacznika
# --------------------------------------------------------------------------- #
def _make_pending_journal(tmp_path, monkeypatch, paths, k=2):
    """Zostaw na dysku niedokończoną transakcję (crash po k zapisach)."""
    _crash_after_k_root_writes(monkeypatch, k, paths)
    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")
    marker = _journal_files(tmp_path)
    assert marker, "brak znacznika transakcji"
    return marker[0]


def test_k5_unreadable_journal_is_fail_closed(tmp_path, monkeypatch):
    """Nieczytelny znacznik -> jawny wyjątek, NIGDY cicha kontynuacja."""
    journal = _journal_mod()
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    marker = _make_pending_journal(tmp_path, monkeypatch, paths)
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("{ to nie jest json ]]")

    phys_before = _physical(paths)
    with pytest.raises(journal.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before, "onboarding pisał mimo nieczytelnego znacznika"


def test_k5_incomplete_journal_is_fail_closed(tmp_path, monkeypatch):
    """Znacznik bez wymaganych pól wpisu -> jawny wyjątek (nie zgadujemy)."""
    journal = _journal_mod()
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    marker = _make_pending_journal(tmp_path, monkeypatch, paths)
    data = json.load(open(marker, encoding="utf-8"))
    assert data.get("entries"), "znacznik nie opisuje wpisów transakcji"
    for e in data["entries"]:
        e.pop("sha256_before", None)
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)

    phys_before = _physical(paths)
    with pytest.raises(journal.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before


def test_k5_foreign_bytes_are_fail_closed(tmp_path, monkeypatch):
    """Root zmieniony przez KOGOŚ INNEGO w trakcie rozdarcia (sha ≠ przed ≠ po)
    -> recovery nie zgaduje, tylko odmawia."""
    journal = _journal_mod()
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _make_pending_journal(tmp_path, monkeypatch, paths)

    obcy = {"Ktos Inny": 777}
    with open(paths["KURIER_IDS"], "w", encoding="utf-8") as fh:
        json.dump(obcy, fh, ensure_ascii=False)

    phys_before = _physical(paths)
    with pytest.raises(journal.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before, "recovery nadpisało obce bajty"


def test_k5_missing_backup_is_fail_closed(tmp_path, monkeypatch):
    """Brakujący backup = brak materiału do leczenia -> odmowa, nie zgadywanie."""
    journal = _journal_mod()
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _make_pending_journal(tmp_path, monkeypatch, paths)
    for b in _bak_files(tmp_path):
        os.unlink(b)

    phys_before = _physical(paths)
    with pytest.raises(journal.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before


def test_k5_recovery_is_idempotent(tmp_path, monkeypatch):
    """Powtórne wejście po udanym leczeniu nie robi nic nadzwyczajnego."""
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _make_pending_journal(tmp_path, monkeypatch, paths)
    CA.add_new_courier(951, "Piotr Nowak")
    assert _journal_files(tmp_path) == []
    res = CA.add_new_courier(952, "Anna Zielona")
    assert res["cid"] == 952
    assert _journal_files(tmp_path) == []


# --------------------------------------------------------------------------- #
# (4) PARYTET zdrowej ścieżki — bajty, backupy, brak śladu po znaczniku
# --------------------------------------------------------------------------- #
def _canonical_bytes(obj):
    """Serializacja bajt-w-bajt jak baseline ``courier_admin._atomic_write_json``."""
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def test_k5_parity_healthy_path_bytes(tmp_path, monkeypatch):
    """Zdrowa ścieżka: te same 5 plików, ten sam format bajtów, te same backupy,
    zero pozostałości po znaczniku."""
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    monkeypatch.setattr(CA, "_generate_unique_pin", lambda existing: "4321")

    res = CA.add_new_courier(901, "Nowy Testowy")
    assert res == {"cid": 901, "alias": "Nowy Te", "full_name": "Nowy Testowy", "pin": "4321"}

    expected = {
        "KURIER_IDS": {"Test Ku": 900, "Test Kurierski": 900, "Nowy Te": 901, "Nowy Testowy": 901},
        "KURIER_PINY": {"1234": "Test Ku", "4321": "Nowy Te"},
        "COURIER_NAMES": {"900": "Test Ku", "901": "Nowy Te"},
        "KURIER_FULL_NAMES": {"Test Ku": "Test Kurierski", "Nowy Te": "Nowy Testowy"},
    }
    for const, obj in expected.items():
        assert open(paths[const], "rb").read() == _canonical_bytes(obj), (
            f"{const}: bajty rozjechały się z baseline (kolejność kluczy / format)"
        )
    # courier_tiers ma niedeterministyczne added_at — pinujemy resztę + format
    tiers = _read(paths, "COURIER_TIERS")
    assert set(tiers) == {"900", "901"}
    assert tiers["901"]["name"] == "Nowy Te"
    assert tiers["901"]["tier_label"] == "new"
    assert tiers["901"]["bag"]["cap_override"] == {"off_peak": 1, "normal": 2, "peak": 2}
    assert open(paths["COURIER_TIERS"], "rb").read() == _canonical_bytes(tiers)

    assert len(_bak_files(tmp_path)) == 5, "parytet backupów .bak-pre-add-* zerwany"
    assert _journal_files(tmp_path) == [], "znacznik transakcji nie został skasowany po commicie"


def test_k5_in_process_failure_still_rolls_back_with_marker_cleared(tmp_path, monkeypatch):
    """Parytet ze starym kontraktem: zwykły (łapalny) błąd zapisu -> rollback z
    backupów + RuntimeError 'rolled back', a znacznik jest sprzątnięty."""
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    before = _snapshot(paths)
    real_write = CA._atomic_write_json

    def failing_write(path, data):
        if path == paths["KURIER_FULL_NAMES"]:
            raise OSError("symulowany fail 5. pliku")
        real_write(path, data)

    monkeypatch.setattr(CA, "_atomic_write_json", failing_write)
    with pytest.raises(RuntimeError, match="rolled back"):
        CA.add_new_courier(902, "Pechowy Przypadek")
    assert _snapshot(paths) == before
    assert _journal_files(tmp_path) == [], "znacznik został po rollbacku w procesie"


# --------------------------------------------------------------------------- #
# (5) RATCHET (discovery-based, wzorzec G3 + O-1) — jeden owner, zero kopii
# --------------------------------------------------------------------------- #
def _func_ast(fn):
    return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]


def _call_names(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_ratchet_add_new_courier_delegates_to_journal_owner():
    """RATCHET: ``add_new_courier`` MUSI delegować transakcję i recovery do
    ownera ``identity.journal`` — powrót do gołej sekwencji 5 zapisów = RED."""
    journal = _journal_mod()
    calls = _call_names(_func_ast(CA.add_new_courier))
    assert "run_transaction" in calls, (
        "add_new_courier nie deleguje zapisu do identity.journal.run_transaction "
        "(powrót konkurencyjnego pisarza generacji)"
    )
    assert "recover_pending" in calls, (
        "add_new_courier nie leczy niedokończonej transakcji przy wejściu"
    )
    assert hasattr(journal, "JournalError")


def test_ratchet_every_transaction_root_has_a_shared_validator():
    """RATCHET discovery-based (O-1): zbiór rootów transakcji jest ODKRYWANY z
    kodu, nie wyliczany w teście. Każdy root MUSI mieć walidator, a każdy
    walidator MUSI pochodzić z ``identity.schema`` (zero kopii inline).

    Dołożenie 6. roota bez walidatora = RED (dokładnie luka K5 z bryły V12).
    """
    from dispatch_v2.identity import schema as SCH

    roots = CA.transaction_roots()
    assert len(roots) == len(CA.ALL_FILES), (
        "liczba rootów transakcji rozjechała się z ALL_FILES — root poza transakcją"
    )
    for root_id, path in roots:
        assert root_id in SCH.ROOT_VALIDATORS, (
            f"root {root_id!r} bierze udział w transakcji, ale NIE MA walidatora "
            f"schematu (lekcja K5: walidowano tylko część rootów)"
        )
        validator = SCH.ROOT_VALIDATORS[root_id]
        assert validator.__module__ == "dispatch_v2.identity.schema", (
            f"walidator roota {root_id!r} nie jest ownerem z identity.schema "
            f"({validator.__module__}) — kopia polityki poza jednym ownerem"
        )
        assert path


def test_ratchet_journal_validates_via_shared_schema_owner():
    """RATCHET: owner transakcji NIE MOŻE mieć własnych, inline'owych walidatorów
    rootów — waliduje wyłącznie przez ``identity.schema``."""
    journal = _journal_mod()
    src = inspect.getsource(journal)
    assert "validate_root" in src, "journal nie deleguje walidacji do identity.schema"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("isdigit", "isdecimal"):
            pytest.fail(
                "identity.journal reimplementuje kanonizację CID (isdigit/isdecimal) "
                "zamiast delegować do identity.schema"
            )


def test_ratchet_courier_admin_has_no_second_write_path():
    """RATCHET: w ``add_new_courier`` nie może wrócić bezpośrednia sekwencja
    zapisów rootów (konkurencyjny pisarz tej samej prawdy)."""
    fn = _func_ast(CA.add_new_courier)
    direct_writes = [n for n in ast.walk(fn)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name)
                     and n.func.id == "_atomic_write_json"]
    assert direct_writes == [], (
        "add_new_courier znowu zapisuje rooty sam, poza transakcją journal "
        "(powrót defektu K5)"
    )
