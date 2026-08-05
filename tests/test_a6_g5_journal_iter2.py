"""A-6/G5 iter2 — sondy blind-recenzenta jako TESTY REGRESYJNE bramki.

Bramka: engine.a6-journal-extended-validators (iter2).

Werdykt blind na kandydacie iter1 ``50c1ebb74`` = ``CONFIRMED_DEFECT`` (4 findingi).
Werdykt sam wskazał brakujące negatywne oracle: „mutacje M1-M4 nie pokrywają
ścieżki nieudanego rollbacku w procesie; brakującym negatywnym oraclem jest
dokładnie R1/R12". Ten plik adoptuje scenariusze recenzenta (R1, R3, R4, R9,
R10, R12) jako stałą część bramki — z asercjami na stan PO naprawie, więc na
iter1 są CZERWONE, a na iter2 zielone.

Mapa finding → test:

* **F1 (high)** ``journal.run_transaction`` — rollback po łapalnym błędzie był
  best-effort (``shutil.copy2`` w pętli z ``except: pass``), a znacznik kasowany
  BEZWARUNKOWO ⇒ cicha rozdarta generacja przy samo-skorelowanej klasie awarii
  (ENOSPC psuje i zapis roota, i jego odtworzenie).
  → :func:`test_R1_failed_rollback_keeps_marker_and_never_continues_silently`,
    :func:`test_R12_enospc_rollback_never_truncates_roots`.
* **F2 (medium)** materiał do leczenia (backupy) nie był trwały: brak ``fsync``
  treści backupu i ``fsync`` tylko PIERWSZEGO katalogu backupów (a
  ``kurier_full_names`` leży w INNYM katalogu).
  → :func:`test_R3_every_backup_is_durable_before_marker`.
* **F3 (medium)** ``validate_kurier_ids_root`` przepuszczał niekanoniczne CID
  (``-5``, ``"017"``, ``" 900 "``, ``"9_0_0"``) do rejestru autoryzacyjnego.
  → :func:`test_R4_poisoned_backup_with_correct_sha_is_refused`,
    :func:`test_R4_unit_kurier_ids_root_requires_canonical_numeric_cid`.
* **F4 (low)** ``recover_pending`` pisał do KAŻDEJ ścieżki ze znacznika, bez
  sprawdzenia, czy para (root, path) należy do transakcji.
  → :func:`test_R10_recovery_is_confined_to_transaction_roots`.
* **obserwacja (etykieta statusu)** rooty niejednoznaczne (``sha_before ==
  sha_after``, realne po backfillu Z-P1-05) rozstrzygały się jako ``healed``,
  choć wszystkie zapisy się dokończyły.
  → :func:`test_R9_ambiguous_roots_resolve_as_committed_not_healed`.

Hermetyczny jak plik bazowy bramki: wszystkie ścieżki na ``tmp_path``, zero I/O
do żywego ``dispatch_state``. Fixtury re-użyte z ``test_a6_g5_onboarding_journal``
(jedno źródło kształtu generacji — bez drugiej kopii seedu).
"""
from __future__ import annotations

import errno
import glob
import hashlib
import json
import os
import shutil

import pytest

from dispatch_v2 import courier_admin as CA
from dispatch_v2.identity import journal as J
from dispatch_v2.identity import schema as SCH

from tests.test_a6_g5_onboarding_journal import (  # re-użycie fixtur bramki
    _HardCrash,
    _crash_after_k_root_writes,
    _identity_present,
    _journal_files,
    _patch_paths,
    _physical,
    _read,
    _seed,
    _snapshot,
)


# --------------------------------------------------------------------------- #
# Symulacja ENOSPC — klasa awarii SAMO-SKORELOWANA
# --------------------------------------------------------------------------- #
def _install_disk_full(monkeypatch, paths, *, fail_write_to):
    """Dysk zapełnia się przy zapisie roota ``fail_write_to``.

    Od tej chwili KAŻDY zapis do któregokolwiek z 5 rootów pada ``ENOSPC`` — i ten
    przez ``shutil.copy2``, i ten przez ``atomic_write_bytes`` (finalny
    ``os.replace``). Dokładnie ta korelacja demaskuje rollback best-effort:
    brak miejsca, który wywalił zapis roota, wywala też jego odtworzenie.

    Zapis znacznika transakcji NIE jest blokowany (leży poza zbiorem rootów) —
    inaczej test mierzyłby brak znacznika zamiast jego losu po nieudanym rollbacku.
    """
    roots = {os.path.realpath(p) for p in paths.values()}
    state = {"full": False, "copy2_into_roots": []}
    real_copy2 = shutil.copy2
    real_replace = os.replace
    real_write = CA._atomic_write_json

    def write(path, data):
        if os.path.realpath(path) == os.path.realpath(fail_write_to):
            state["full"] = True
            raise OSError(errno.ENOSPC, "No space left on device")
        real_write(path, data)

    def copy2(src, dst, *a, **kw):
        if os.path.realpath(dst) in roots:
            state["copy2_into_roots"].append(str(dst))
            if state["full"]:
                raise OSError(errno.ENOSPC, "No space left on device")
        return real_copy2(src, dst, *a, **kw)

    def replace(src, dst, *a, **kw):
        if state["full"] and os.path.realpath(dst) in roots:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(CA, "_atomic_write_json", write)
    monkeypatch.setattr(J.shutil, "copy2", copy2)
    monkeypatch.setattr(os, "replace", replace)
    return state


# =========================================================================== #
# F1 / R1 — nieudany rollback MUSI zostawić znacznik i NIGDY nie milczeć
# =========================================================================== #
def test_R1_failed_rollback_keeps_marker_and_never_continues_silently(tmp_path, monkeypatch):
    """ENOSPC na 5. zapisie + ENOSPC w kierunku odtwarzania.

    iter1 (RED): ``copy2`` w pętli z ``except: pass`` połykał porażkę per root,
    a ``_remove_durable`` kasowało znacznik BEZWARUNKOWO. Efekt zmierzony przez
    recenzenta: rozdarta generacja (4 z 5 rootów z nową tożsamością), wszystkie
    rooty poprawnym JSON-em, znacznik skasowany — i KOLEJNE ``add_new_courier``
    kończące się SUKCESEM, czyli dokładnie cicha kontynuacja na rozdartej
    generacji, której cała bramka ma zabraniać.

    iter2: rollback jest weryfikowany per root (sha == stan PRZED). Skoro nie
    wyszedł — znacznik ZOSTAJE (jedyny trwały dowód rozdarcia), błąd jest jawny
    (``JournalError``), następne wejście jest fail-closed, a gdy warunek awarii
    ustąpi — leczy do końca.
    """
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    before = _snapshot(paths)

    _install_disk_full(monkeypatch, paths, fail_write_to=paths["KURIER_FULL_NAMES"])

    with pytest.raises(J.JournalError) as ei:
        CA.add_new_courier(950, "Jan Kowalski")
    msg = str(ei.value)
    assert "Jan" not in msg and "Kowalski" not in msg, (
        "komunikat nieudanego rollbacku nie może nieść tożsamości"
    )

    # (a) rozdarcie JEST na dysku (precondition scenariusza)…
    torn = _identity_present(paths, alias="Jan Ko", full_name="Jan Kowalski", cid=950)
    assert 0 < sum(torn.values()) < 5, f"precondition: miało zostać rozdarcie, mam {torn}"
    # …i wszystkie rooty są poprawnym JSON-em, więc NIC nie krzyknie przy odczycie
    for const in paths:
        _read(paths, const)

    # (b) FINDING F1: znacznik MUSI zostać — to jedyny trwały ślad rozdarcia
    assert _journal_files(tmp_path), (
        "znacznik skasowany mimo nieudanego rollbacku — rozdarta generacja "
        "jest po restarcie niewykrywalna (F1)"
    )

    # (c) następne wejście NIE MOŻE cicho dopisać kolejnej tożsamości
    with pytest.raises(J.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert not any(_identity_present(paths, alias="Piotr No",
                                     full_name="Piotr Nowak", cid=951).values()), (
        "onboarding dopisał tożsamość mimo nieuleczonego rozdarcia"
    )

    # (d) gdy awaria ustąpi (dysk zwolniony) — leczenie domyka sprawę
    monkeypatch.undo()
    _patch_paths(monkeypatch, paths)
    res = CA.add_new_courier(951, "Piotr Nowak")
    healed = _identity_present(paths, alias="Jan Ko", full_name="Jan Kowalski", cid=950)
    assert sum(healed.values()) == 0, f"rozdarta tożsamość została w rejestrach: {healed}"
    assert all(_identity_present(paths, alias="Piotr No",
                                 full_name="Piotr Nowak", cid=951).values())
    assert res["cid"] == 951
    assert _journal_files(tmp_path) == [], "znacznik został po udanym commicie"
    for const in paths:
        assert before[const].items() <= _snapshot(paths)[const].items(), (
            f"{const}: leczenie zgubiło rekordy sprzed transakcji"
        )


# =========================================================================== #
# F1 / R12 — rollback NIE MOŻE niszczyć rootów (copy2 obcina cel)
# =========================================================================== #
def test_R12_enospc_rollback_never_truncates_roots(tmp_path, monkeypatch):
    """Wierny ENOSPC: ``shutil.copy2`` NAJPIERW obcina plik docelowy, potem pisze
    i pada.

    iter1 (RED): rollback przez ``copy2`` obciął WSZYSTKIE 5 rootów do 0 bajtów
    (utrata całego rostera), ``except: pass`` to połknął, a znacznik i tak
    zniknął — przy KOMPLECIE poprawnych backupów leżących obok, nieużytych.

    iter2: odtwarzanie idzie przez ``atomic_write_bytes`` (temp → fsync →
    rename), więc porażka zapisu nigdy nie zostawia roota urwanego; rollback
    kończy się pełnym powrotem do stanu PRZED, znacznik jest sprzątnięty, a błąd
    ma parytet ze starym kontraktem (``RuntimeError`` „rolled back").
    """
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    before_bytes = {c: open(paths[c], "rb").read() for c in paths}

    roots = {os.path.realpath(p) for p in paths.values()}
    real_copy2 = shutil.copy2
    into_roots = []

    def truncating_enospc_copy2(src, dst, *a, **kw):
        if os.path.realpath(dst) in roots:
            into_roots.append(str(dst))
            open(dst, "wb").close()                      # truncate (jak copyfile)
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(J.shutil, "copy2", truncating_enospc_copy2)
    real_write = CA._atomic_write_json

    def failing_write(path, data):
        if path == paths["KURIER_FULL_NAMES"]:
            raise OSError(errno.ENOSPC, "No space left on device")
        real_write(path, data)

    monkeypatch.setattr(CA, "_atomic_write_json", failing_write)

    with pytest.raises(RuntimeError, match="rolled back"):
        CA.add_new_courier(950, "Jan Kowalski")

    assert into_roots == [], (
        f"rollback nadal kopiuje przez copy2 do rootów (obcina cel przy ENOSPC): {into_roots}"
    )
    for const in paths:
        assert os.path.getsize(paths[const]) > 0, f"{const}: root urwany przez rollback"
        assert open(paths[const], "rb").read() == before_bytes[const], (
            f"{const}: rollback nie odtworzył bajt-w-bajt stanu PRZED"
        )
    assert _journal_files(tmp_path) == [], "znacznik został po UDANYM rollbacku"

    # rejestr autoryzacyjny jest czytelny, a kolejne wejście przechodzi normalnie
    monkeypatch.undo()
    _patch_paths(monkeypatch, paths)
    assert CA.add_new_courier(951, "Piotr Nowak")["cid"] == 951


# =========================================================================== #
# F2 / R3 — materiał do leczenia musi być TRWAŁY, zanim powstanie znacznik
# =========================================================================== #
def _seed_two_dirs(tmp_path):
    """Układ produkcyjny: 4 rooty w ``dispatch_state``, a ``kurier_full_names``
    w INNYM katalogu (``dispatch_v2/daily_accounting``)."""
    state_dir = tmp_path / "dispatch_state"
    acc_dir = tmp_path / "daily_accounting"
    state_dir.mkdir()
    acc_dir.mkdir()
    paths = _seed(state_dir)
    src = paths["KURIER_FULL_NAMES"]
    dst = acc_dir / "kurier_full_names.json"
    shutil.move(src, str(dst))
    paths["KURIER_FULL_NAMES"] = str(dst)
    return paths


def _backups(paths):
    out = []
    for p in paths.values():
        out.extend(glob.glob(p + ".bak-pre-add-*"))
    return sorted(out)


def test_R3_every_backup_is_durable_before_marker(tmp_path, monkeypatch):
    """Deklarowany model awarii modułu to jawnie „kill -9 / OOM / zanik
    zasilania", a cały kontrakt leczenia stoi na backupach.

    iter1 (RED): ``shutil.copy2`` nie utrwala treści backupu, a ``_fsync_dir``
    wołane było WYŁĄCZNIE dla katalogu PIERWSZEGO backupu (zmierzone przez
    recenzenta: 1 katalog na 5 backupów) — więc backup ``kurier_full_names``,
    leżący w innym katalogu, nie miał utrwalonego nawet wpisu katalogowego.
    Po zaniku zasilania znacznik (fsync) przeżywa, a materiał do leczenia może
    być pusty ⇒ stan LECZALNY degraduje się do trwałego bloku operatorskiego.

    iter2: fsync TREŚCI każdego backupu + fsync KAŻDEGO różnego katalogu
    backupów, wszystko PRZED zapisem znacznika.

    Pomiar jest niezależny od implementacji: podglądam ``os.fsync`` i mapuję
    deskryptor na ścieżkę przez ``/proc/self/fd``.
    """
    paths = _seed_two_dirs(tmp_path)
    _patch_paths(monkeypatch, paths)
    monkeypatch.setattr(CA, "_generate_unique_pin", lambda existing: "4321")
    marker_path = J.journal_path_for(paths["KURIER_IDS"])

    events = []            # („fsync", ścieżka) / („marker", ścieżka) — w kolejności
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync_spy(fd):
        try:
            events.append(("fsync", os.readlink(f"/proc/self/fd/{fd}")))
        except OSError:  # pragma: no cover - deskryptor bez /proc (nie na Linuksie)
            pass
        return real_fsync(fd)

    def replace_spy(src, dst, *a, **kw):
        out = real_replace(src, dst, *a, **kw)
        if os.path.realpath(dst) == os.path.realpath(marker_path):
            events.append(("marker", str(dst)))
        return out

    monkeypatch.setattr(os, "fsync", fsync_spy)
    monkeypatch.setattr(os, "replace", replace_spy)

    CA.add_new_courier(901, "Nowy Testowy")

    monkeypatch.setattr(os, "fsync", real_fsync)
    monkeypatch.setattr(os, "replace", real_replace)

    baks = _backups(paths)
    assert len(baks) == 5, f"parytet backupów zerwany: {baks}"
    marker_idx = next((i for i, (kind, _) in enumerate(events) if kind == "marker"), None)
    assert marker_idx is not None, "znacznik transakcji nigdy nie powstał"
    fsynced_before_marker = {p for i, (kind, p) in enumerate(events)
                             if kind == "fsync" and i < marker_idx}

    missing = [os.path.basename(b) for b in baks
               if os.path.realpath(b) not in {os.path.realpath(p) for p in fsynced_before_marker}]
    assert not missing, (
        f"treść backupów nie została utrwalona przed zapisem znacznika: {missing} (F2)"
    )

    bak_dirs = {os.path.realpath(os.path.dirname(b)) for b in baks}
    assert len(bak_dirs) == 2, "test ma mierzyć DWA różne katalogi backupów"
    fsynced_dirs = {os.path.realpath(p) for p in fsynced_before_marker}
    missing_dirs = [d for d in bak_dirs if d not in fsynced_dirs]
    assert not missing_dirs, (
        f"katalogi backupów bez fsync przed znacznikiem: {missing_dirs} (F2)"
    )


def test_R3_truncated_backup_after_power_loss_is_fail_closed(tmp_path, monkeypatch):
    """Druga połowa R3: gdyby backup mimo wszystko był urwany (nośnik/operator),
    leczenie MUSI odmówić, a nie przywrócić śmiecia."""
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _crash_after_k_root_writes(monkeypatch, 2, paths)
    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")

    baks = _backups(paths)
    assert len(baks) == 5
    with open(baks[0], "wb") as fh:
        fh.write(b"")

    phys_before = _physical(paths)
    with pytest.raises(J.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before
    assert _journal_files(tmp_path), "fail-closed nie może kasować znacznika"


# =========================================================================== #
# F3 / R4 — zatruty backup z POPRAWNYM sha (kontrprzykłady recenzenta)
# =========================================================================== #
POISON = [
    pytest.param("KURIER_IDS", {"Test Ku": " 900 ", "Zly Ktos": "017"}, id="kurier_ids-spacje-i-lead-zero"),
    pytest.param("KURIER_IDS", {"Test Ku": -5}, id="kurier_ids-ujemny"),
    pytest.param("KURIER_IDS", {"Test Ku": "9_0_0"}, id="kurier_ids-podkreslniki"),
    pytest.param("COURIER_NAMES", {"017": "Lead Zero"}, id="courier_names-lead-zero"),
    pytest.param("COURIER_TIERS", {" 900 ": {"name": "x"}}, id="courier_tiers-spacje"),
]


@pytest.mark.parametrize("root_const,poison", POISON)
def test_R4_poisoned_backup_with_correct_sha_is_refused(tmp_path, monkeypatch,
                                                        root_const, poison):
    """Backup zatruty, ale sha w znaczniku POPRAWNE ⇒ jedyną obroną jest walidator
    schematu tego roota (scenariusz z testu autora, kontrprzykłady recenzenta).

    iter1 (RED dla ``kurier_ids``): ``validate_kurier_ids_root`` delegował do
    ``validate_courier_ids_store`` (luźny ``canonical_courier_id``), więc rejestr
    AUTORYZACYJNY dostał najsłabszą regułę z piątki i przyjmował ``" 900 "``,
    ``-5``, ``"9_0_0"`` — formy, o których ``identity/schema`` samo pisze, że
    reader oparty o ``int()`` czyta je jako INNĄ tożsamość. ``courier_names`` i
    ``courier_tiers`` odrzucały tę samą klasę defektu poprawnie.
    """
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _crash_after_k_root_writes(monkeypatch, 2, paths)
    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")

    marker = _journal_files(tmp_path)[0]
    doc = json.load(open(marker, encoding="utf-8"))
    root_path = paths[root_const]
    raw = (json.dumps(poison, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    entry = next(e for e in doc["entries"]
                 if os.path.realpath(e["path"]) == os.path.realpath(root_path))
    for target in (root_path, entry["backup"]):
        with open(target, "wb") as fh:
            fh.write(raw)
    entry["sha256_before"] = hashlib.sha256(raw).hexdigest()
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)

    phys_before = _physical(paths)
    with pytest.raises((J.JournalError, ValueError)):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before, (
        f"{root_const}: recovery ruszyło rooty mimo zatrutego materiału"
    )


def test_R4_unit_kurier_ids_root_requires_canonical_numeric_cid():
    """Walidator roota ``kurier_ids`` (pisarz rejestru autoryzacyjnego) wymaga
    CID kanonicznego LICZBOWO — per rekord.

    Kontrakt ``validate_courier_ids_store`` (autoryzacja ``courier_info``) jest
    CELOWO nietknięty: ostrzejsza kontrola siedzi w warstwie walidatora roota
    transakcji, PONAD istniejącą delegacją (delegacja nadal obowiązuje — patrz
    przypadki „malformed record" niżej).
    """
    for bad in ({"Test Ku": " 900 "}, {"Test Ku": "017"}, {"Test Ku": -5},
                {"Test Ku": "9_0_0"}, {"Test Ku": "+17"}, {"Test Ku": 0},
                {"Test Ku": 1.5}, {"Test Ku": True}):
        with pytest.raises(ValueError):
            SCH.validate_kurier_ids_root(bad)
    # delegacja do istniejącego ownera nadal działa (rekord bez nazwy / nie-mapa)
    with pytest.raises(ValueError):
        SCH.validate_kurier_ids_root({"": 900})
    with pytest.raises(ValueError):
        SCH.validate_kurier_ids_root([("Test Ku", 900)])
    # formy kanoniczne przechodzą (int i str — obie żyją w żywym rejestrze)
    assert SCH.validate_kurier_ids_root({"Test Ku": 900, "Test Kurierski": "900"})


# =========================================================================== #
# F4 / R10 — leczenie ograniczone do rootów transakcji
# =========================================================================== #
def test_R10_recovery_is_confined_to_transaction_roots(tmp_path, monkeypatch):
    """iter1 (RED): ``recover_pending`` pisało ``atomic_write_bytes`` do KAŻDEJ
    ścieżki wymienionej w znaczniku i nigdy nie sprawdzało, czy para
    (root, path) należy do ``courier_admin.transaction_roots()``. Znacznik jest
    root-owned 0600 (żadna granica uprawnień nie jest przekraczana), ale to
    znosi „jednego kanonicznego ownera kontraktu" dokładnie w kroku leczenia:
    uszkodzony/nieaktualny znacznik nadpisywał plik spoza generacji.
    """
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _crash_after_k_root_writes(monkeypatch, 2, paths)
    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")

    marker = _journal_files(tmp_path)[0]
    doc = json.load(open(marker, encoding="utf-8"))

    obcy = tmp_path / "obcy_plik.json"
    obcy.write_text('{"cokolwiek": "stan-biezacy"}', encoding="utf-8")
    obcy_bak = tmp_path / "obcy_plik.wstrzykniety"
    payload = (json.dumps({"Wstrzykniety Al": "Wstrzyknieta Tozsamosc"},
                          indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    obcy_bak.write_bytes(payload)
    doc["entries"].append({
        "root": "kurier_full_names",
        "path": str(obcy),
        "backup": str(obcy_bak),
        "sha256_before": hashlib.sha256(payload).hexdigest(),
        "sha256_after": hashlib.sha256(open(obcy, "rb").read()).hexdigest(),
    })
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)

    obcy_before = obcy.read_text(encoding="utf-8")
    with pytest.raises(J.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert obcy.read_text(encoding="utf-8") == obcy_before, (
        "recovery nadpisało plik spoza transakcji (F4)"
    )
    assert _journal_files(tmp_path), "fail-closed nie może kasować znacznika"


def test_R10_marker_root_id_must_match_the_declared_path(tmp_path, monkeypatch):
    """Confinement jest na PARZE (root, path): podmiana samego root-id przy
    ścieżce z transakcji też jest odrzucana (inaczej root byłby walidowany
    cudzym schematem)."""
    paths = _seed(tmp_path)
    _patch_paths(monkeypatch, paths)
    _crash_after_k_root_writes(monkeypatch, 2, paths)
    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")

    marker = _journal_files(tmp_path)[0]
    doc = json.load(open(marker, encoding="utf-8"))
    for e in doc["entries"]:
        if e["root"] == "courier_names":
            e["root"] = "kurier_full_names"      # ścieżka z transakcji, obcy root-id
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)

    phys_before = _physical(paths)
    with pytest.raises(J.JournalError):
        CA.add_new_courier(951, "Piotr Nowak")
    assert _physical(paths) == phys_before


# =========================================================================== #
# Obserwacja recenzenta / R9 — etykieta statusu przy rootach niejednoznacznych
# =========================================================================== #
def test_R9_ambiguous_roots_resolve_as_committed_not_healed(tmp_path, monkeypatch):
    """Rooty niejednoznaczne (``sha256_before == sha256_after``) są osiągalne w
    realnym dryfie po backfillu Z-P1-05 (kurier jest w kurier_ids/courier_names/
    full_names, a wypadł z courier_tiers) — recenzent zmierzył 3 z 5.

    iter1: taki root klasyfikował się TWARDO jako ``before``, więc crash PO
    wszystkich 5 zapisach, a przed commitem, rozstrzygał się jako ``healed``
    (rollback) mimo że transakcja fizycznie się dokończyła — stan spójny, ale
    etykieta myląca i generacja niepotrzebnie cofana.

    iter2: root niejednoznaczny jest zgodny z OBIEMA stronami i nie tworzy
    sztucznej „mieszanki"; werdykt biorą rooty rozstrzygające. Wszystkie zapisy
    zrobione ⇒ ``committed``.
    """
    paths = _seed(
        tmp_path,
        kids={"Test Ku": 900, "Test Kurierski": 900, "Jan Ko": 950, "Jan Kowalski": 950},
        names={"900": "Test Ku", "950": "Jan Ko"},
        full={"Test Ku": "Test Kurierski", "Jan Ko": "Jan Kowalski"},
        tiers={"900": {"name": "Test Ku"}},          # 950 wypadł z tiers (dryf)
        piny={"1234": "Test Ku"},
    )
    # Rooty w formacie, w jakim zapisuje je onboarding (canonical_bytes) — bez
    # tego różnica jest w samym FORMATOWANIU i niejednoznaczność nie powstaje.
    for const in paths:
        J.atomic_write_json(paths[const], _read(paths, const))
    _patch_paths(monkeypatch, paths)
    monkeypatch.setattr(CA, "_generate_unique_pin", lambda existing: "4321")

    # crash PO wszystkich 5 zapisach, PRZED commitem (usunięciem znacznika)
    real_remove = J._remove_durable
    fired = {"x": False}

    def crash_on_commit(path):
        if not fired["x"]:
            fired["x"] = True
            raise _HardCrash("kill -9 przed commitem")
        return real_remove(path)

    monkeypatch.setattr(J, "_remove_durable", crash_on_commit)
    with pytest.raises(_HardCrash):
        CA.add_new_courier(950, "Jan Kowalski")
    monkeypatch.setattr(J, "_remove_durable", real_remove)

    marker = _journal_files(tmp_path)[0]
    doc = json.load(open(marker, encoding="utf-8"))
    ambiguous = [e["root"] for e in doc["entries"] if e["sha256_before"] == e["sha256_after"]]
    assert ambiguous, "precondition: scenariusz miał dać rooty niejednoznaczne"

    res = J.recover_pending(marker)
    assert res["status"] == "committed", (
        f"rooty niejednoznaczne {ambiguous} przekłamały werdykt na {res['status']!r} — "
        f"wszystkie 5 zapisów się dokończyło, to jest commit, nie leczenie"
    )
    assert _journal_files(tmp_path) == []
    # generacja PO transakcji zostaje kompletna (a nie cofnięta połowicznie)
    assert "4321" in _read(paths, "KURIER_PINY")
    assert "950" in _read(paths, "COURIER_TIERS")
