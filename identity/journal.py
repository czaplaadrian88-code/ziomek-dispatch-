"""Trwała transakcja generacji rostera — JEDEN owner (A-6/G5, K5).

Onboarding kuriera zmienia PIĘĆ rootów naraz (``kurier_ids``, ``kurier_piny``,
``courier_tiers``, ``courier_names``, ``kurier_full_names``). Do A-6/G5
``courier_admin.add_new_courier`` robił pięć sekwencyjnych atomic-write'ów, a
jedynym zabezpieczeniem był rollback z ``.bak`` w ``except Exception`` WEWNĄTRZ
procesu. Atomowość per plik nie daje atomowości GENERACJI: twardy crash
(kill -9 / OOM / zanik zasilania) pomiędzy zapisami zostawiał część rootów nową,
część starą — bez żadnego trwałego śladu, że transakcja w ogóle trwała. Po
restarcie nikt tego nie wykrywał: następne wejście do onboardingu czytało
rozdartą generację i dokładało do niej kolejną tożsamość.

Ten moduł jest kanonicznym ownerem tej transakcji:

  1. **Backupy** pięciu rootów — z ``fsync`` TREŚCI każdego backupu i ``fsync``
     KAŻDEGO RÓŻNEGO katalogu backupów (``kurier_full_names`` leży w innym
     katalogu niż pozostała czwórka). Cały kontrakt leczenia stoi na backupach,
     więc muszą być trwałe ZANIM powstanie znacznik — inaczej po zaniku zasilania
     znacznik przeżyje, a materiał do leczenia nie, i stan leczalny degraduje się
     do trwałego bloku operatorskiego.
  2. **Znacznik (journal)** — przed pierwszym zapisem powstaje trwały plik
     ``.onboarding-journal.json`` (atomowo + ``fsync`` pliku i katalogu) z
     listą rootów: ścieżka, ścieżka backupu, ``sha256`` stanu PRZED i sha256
     bajtów, które transakcja zamierza zapisać (PO).
  3. **Zapis** pięciu rootów.
  4. **Commit = usunięcie znacznika** (durable unlink + ``fsync`` katalogu).
     Brak znacznika ≡ brak transakcji w locie.

Błąd łapalny (``Exception``) w trakcie zapisu uruchamia rollback W PROCESIE —
i on też jest WERYFIKOWANY: rooty wracają przez ``atomic_write_bytes`` (nigdy
``shutil.copy2``, który najpierw obcina cel), każdy sprawdzony ``sha256`` wobec
stanu PRZED, a znacznik wolno skasować DOPIERO gdy potwierdzone są wszystkie.
Jeżeli choć jeden root nie wrócił — znacznik ZOSTAJE (jedyny trwały dowód
rozdarcia) i leci ``JournalError``. Klasa awarii bywa samo-skorelowana: ENOSPC,
który wywalił zapis roota, wywala też jego odtworzenie.

``recover_pending`` przy KAŻDYM wejściu do onboardingu:

  * brak znacznika → nic (czysto),
  * wszystkie rooty w stanie PRZED → transakcja nie ruszyła: sprzątamy znacznik,
  * wszystkie rooty w stanie PO → zapisy się dokończyły, padło przed commitem:
    domykamy commit,
  * MIESZANKA → rozdarta generacja: **rollback do stanu PRZED** z backupów,
  * cokolwiek nieczytelnego / niekompletnego / obcego (sha ≠ PRZED i ≠ PO,
    brak backupu, backup o innym sha, backup łamiący schemat roota, para
    ``(root, path)`` spoza ``courier_admin.transaction_roots()``) →
    **FAIL-CLOSED** ``JournalError``; nigdy cicha kontynuacja i nigdy
    przywrócenie śmiecia. Znacznik przy fail-closed ZOSTAJE.

**Walidatory WSZYSTKICH rootów, nie podzbioru** (lekcja K5): każdy root — i przy
zapisie, i przy recovery — przechodzi przez walidator schematu z
``identity.schema`` (``ROOT_VALIDATORS`` / ``validate_root``). Ten moduł NIE ma
własnych kopii tych reguł; kanonizację CID i kształt rootów definiuje wyłącznie
``identity.schema`` (ratchet pilnuje braku kopii inline).

Cała sekcja krytyczna (recovery → odczyt → zapis) biegnie pod ``flock`` na
dedykowanym lockfile obok znacznika: onboarding ma dziś TRZECH konsumentów
(timer ``dispatch-new-courier-watch``, CLI ``identity.onboarding``, komenda
``/dopisz``), a dwa równoległe wejścia nadpisałyby sobie znacznik. ``flock``
zwalnia się przy śmierci procesu, więc crash nie zostawia martwej blokady.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

from .schema import ROOT_VALIDATORS, validate_root

__all__ = [
    "JournalError",
    "JOURNAL_NAME",
    "JOURNAL_VERSION",
    "atomic_write_json",
    "canonical_bytes",
    "journal_path_for",
    "onboarding_lock",
    "recover_pending",
    "run_transaction",
]


class JournalError(RuntimeError):
    """Transakcja generacji jest w stanie, którego NIE WOLNO zgadywać.

    Podklasa ``RuntimeError`` (konsumenci onboardingu łapią ``Exception`` i
    raportują operatorowi), ale osobny typ — recovery ma być odróżnialne od
    zwykłego błędu zapisu.
    """


JOURNAL_VERSION = 1
JOURNAL_NAME = ".onboarding-journal.json"
LOCK_SUFFIX = ".lock"
TMP_PREFIX = ".tmp-roster-"

# Wpis znacznika opisuje JEDEN root; brak któregokolwiek pola = znacznik
# niekompletny = fail-closed (nie dopowiadamy sobie brakującego sha).
REQUIRED_ENTRY_KEYS = ("root", "path", "backup", "sha256_before", "sha256_after")


# --- prymitywy trwałości -----------------------------------------------------

def canonical_bytes(data: Any) -> bytes:
    """Bajty roota — format 1:1 z historycznym ``courier_admin._atomic_write_json``
    (``indent=2``, ``ensure_ascii=False``, końcowy ``\\n``), żeby zdrowa ścieżka
    była bajt-w-bajt identyczna z baseline."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _fsync_dir(path: str) -> None:
    """``fsync`` katalogu — bez tego rename/unlink może nie przetrwać zaniku
    zasilania i znacznik „zniknąłby" mimo nieukończonej transakcji."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: str) -> None:
    """``fsync`` TREŚCI pliku. Backup zrobiony ``shutil.copy2`` siedzi do tej pory
    wyłącznie w page cache: po zaniku zasilania (deklarowany model awarii) znacznik
    — utrwalony — przeżyje, a materiał do leczenia może być pusty/urwany, więc stan
    LECZALNY zdegradowałby się do trwałego bloku operatorskiego."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: str, payload: bytes) -> None:
    """temp → ``fsync`` → ``rename`` → ``fsync`` katalogu."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=TMP_PREFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    _fsync_dir(path)


def atomic_write_json(path: str, data: Any) -> None:
    """Jedyny zapis roota/znacznika w tej transakcji (patrz ``canonical_bytes``)."""
    atomic_write_bytes(path, canonical_bytes(data))


def _remove_durable(path: str) -> None:
    """Usuń plik i utrwal usunięcie (commit transakcji)."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    _fsync_dir(path)


def _sha256_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return None


# --- ścieżki / blokada -------------------------------------------------------

def journal_path_for(anchor_path: str) -> str:
    """Znacznik leży w katalogu roota-kotwicy (``kurier_ids.json``) — tam, gdzie
    szuka go następne wejście, niezależnie od tego, który proces padł."""
    return os.path.join(os.path.dirname(anchor_path) or ".", JOURNAL_NAME)


def _transaction_pairs() -> set:
    """Dozwolone pary ``(root-id, realpath)`` — CONFINEMENT leczenia.

    Zbiór rootów generacji ma JEDNEGO ownera (``courier_admin.transaction_roots``),
    więc recovery pyta jego, zamiast wierzyć ścieżkom ze znacznika: uszkodzony albo
    nieaktualny znacznik nie może skierować ``atomic_write_bytes`` na plik spoza
    generacji ani kazać zwalidować roota cudzym schematem. Import jest late-bound,
    bo to ``courier_admin`` importuje ten moduł (kierunek zależności zostaje).
    """
    from dispatch_v2 import courier_admin

    return {(root, os.path.realpath(path)) for root, path in courier_admin.transaction_roots()}


@contextmanager
def onboarding_lock(journal_path: str):
    """``flock(LOCK_EX)`` na całą sekcję krytyczną onboardingu (recovery + zapis).

    Lockfile (0 B, obok znacznika) jest CELOWO trwały i NIE jest kasowany po
    zwolnieniu blokady — także po onboardingu odrzuconym w sekcji krytycznej.
    ``flock`` siedzi na I-WĘŹLE: gdyby posiadacz blokady odlinkował plik, kolejny
    proces otworzyłby świeży i-węzeł i wszedł RÓWNOLEGLE (dwóch „posiadaczy" tej
    samej blokady). Walidacja wejścia (kanoniczny CID, alias) biegnie w
    ``courier_admin.add_new_courier`` PRZED wejściem tutaj, więc śmieciowe
    wywołanie nie tworzy lockfile'a; kolizje rejestru można sprawdzić dopiero pod
    blokadą, bo wymagają odczytu generacji.
    """
    lock_path = journal_path + LOCK_SUFFIX
    d = os.path.dirname(lock_path) or "."
    os.makedirs(d, exist_ok=True)
    with open(lock_path, "w") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            yield lock_path
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


# --- walidacja (delegacja do jedynego ownera schematu) -----------------------

def _validate_payload(root: str, payload: Any) -> None:
    """Schemat roota — WYŁĄCZNIE przez ``identity.schema.validate_root``."""
    if root not in ROOT_VALIDATORS:
        raise JournalError(
            f"root {root!r} bierze udział w transakcji, ale nie ma walidatora "
            f"schematu w identity.schema (K5: walidujemy WSZYSTKIE rooty)"
        )
    try:
        validate_root(root, payload)
    except ValueError as e:
        raise JournalError(f"root {root!r} łamie schemat: {e}") from e


# --- znacznik ----------------------------------------------------------------

def _read_journal(journal_path: str) -> Dict[str, Any]:
    try:
        with open(journal_path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:  # pragma: no cover - sprawdzane przez wołającego
        raise JournalError("znacznik transakcji zniknął w trakcie odczytu")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise JournalError(f"znacznik transakcji jest nieczytelny: {type(e).__name__}") from e
    if not isinstance(doc, dict) or doc.get("version") != JOURNAL_VERSION:
        raise JournalError("znacznik transakcji ma nieznany format/wersję")
    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        raise JournalError("znacznik transakcji nie opisuje żadnego roota")
    for entry in entries:
        if not isinstance(entry, dict):
            raise JournalError("wpis znacznika nie jest obiektem")
        missing = [k for k in REQUIRED_ENTRY_KEYS if not entry.get(k)]
        if missing:
            raise JournalError(
                f"wpis znacznika jest niekompletny (brak: {', '.join(sorted(missing))})"
            )
        if entry["root"] not in ROOT_VALIDATORS:
            raise JournalError(f"znacznik opisuje nieznany root {entry['root']!r}")
    return doc


def _restorable_payload(entry: Dict[str, Any]) -> bytes:
    """Bajty backupu, dopuszczone do przywrócenia dopiero po PEŁNEJ kontroli:
    backup istnieje, ma sha ze znacznika i przechodzi walidator SWOJEGO roota."""
    backup = entry["backup"]
    root = entry["root"]
    if not os.path.exists(backup):
        raise JournalError(f"root {root!r}: brak backupu {os.path.basename(backup)} — nie ma czym leczyć")
    if _sha256_file(backup) != entry["sha256_before"]:
        raise JournalError(f"root {root!r}: backup ma inne sha256 niż stan PRZED ze znacznika")
    with open(backup, "rb") as fh:
        raw = fh.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise JournalError(f"root {root!r}: backup jest nieczytelny ({type(e).__name__})") from e
    _validate_payload(root, payload)
    return raw


def recover_pending(journal_path: str) -> Dict[str, Any]:
    """Domknij niedokończoną transakcję. Zwraca ``{"status": ..., "roots": [...]}``.

    ``status``: ``clean`` (brak znacznika), ``aborted`` (żaden root nie zdążył),
    ``committed`` (wszystkie rooty zapisane, padło przed commitem),
    ``healed`` (rozdarta generacja cofnięta do stanu PRZED).
    Każdy inny stan → ``JournalError`` (fail-closed).

    Znacznik jest wpuszczany do leczenia dopiero po CONFINEMENCIE: każda para
    ``(root, path)`` musi należeć do ``courier_admin.transaction_roots()``
    (:func:`_transaction_pairs`).

    Root NIEJEDNOZNACZNY (``sha256_before == sha256_after``, czyli transakcja
    zapisywała mu bajt-w-bajt to, co już tam było — realne po backfillu Z-P1-05,
    gdy kurier jest w ``kurier_ids``/``courier_names``/``kurier_full_names``, a
    wypadł z ``courier_tiers``) jest zgodny z OBIEMA stronami transakcji, więc nie
    tworzy sztucznej „mieszanki": werdykt biorą rooty rozstrzygające. Bez tego
    crash po WSZYSTKICH pięciu zapisach, ale przed commitem, rozstrzygał się jako
    ``healed`` (rollback) mimo że transakcja fizycznie się dokończyła — stan był
    spójny, ale etykieta myląca, a kompletna generacja niepotrzebnie cofana.
    """
    if not os.path.exists(journal_path):
        return {"status": "clean", "roots": []}

    doc = _read_journal(journal_path)
    entries: List[Dict[str, Any]] = doc["entries"]

    allowed = _transaction_pairs()
    for entry in entries:
        if (entry["root"], os.path.realpath(entry["path"])) not in allowed:
            raise JournalError(
                f"znacznik kieruje root {entry['root']!r} na ścieżkę spoza transakcji "
                f"({os.path.basename(entry['path'])}) — leczenie tylko w granicach generacji"
            )

    states = {}
    for entry in entries:
        current = _sha256_file(entry["path"])
        if entry["sha256_before"] == entry["sha256_after"]:
            states[entry["path"]] = "either" if current == entry["sha256_before"] else None
        elif current == entry["sha256_before"]:
            states[entry["path"]] = "before"
        elif current == entry["sha256_after"]:
            states[entry["path"]] = "after"
        else:
            states[entry["path"]] = None
        if states[entry["path"]] is None:
            raise JournalError(
                f"root {entry['root']!r} ma bajty spoza transakcji (sha ≠ PRZED i ≠ PO) — "
                f"recovery nie zgaduje, wymagana decyzja operatora"
            )

    distinct = set(states.values()) - {"either"}
    if distinct == {"before"}:
        _remove_durable(journal_path)
        return {"status": "aborted", "roots": []}
    if distinct in ({"after"}, set()):
        # set() = wszystkie rooty niejednoznaczne: docelowe bajty i tak są na dysku.
        _remove_durable(journal_path)
        return {"status": "committed", "roots": [e["root"] for e in entries]}

    # ROZDARTA GENERACJA — rollback do stanu PRZED. Najpierw sprawdzamy materiał
    # dla WSZYSTKICH rootów transakcji (nie tylko tych do nadpisania): połowiczne
    # leczenie byłoby drugim rozdarciem, a niesprawdzony root to dokładnie luka K5.
    material = {entry["path"]: _restorable_payload(entry) for entry in entries}

    restored: List[str] = []
    for entry in entries:
        if states[entry["path"]] in ("before", "either"):
            continue
        try:
            atomic_write_bytes(entry["path"], material[entry["path"]])
        except OSError as e:
            # Nośnik nie przyjmuje leczenia (ENOSPC/EROFS/EIO). Znacznik ZOSTAJE —
            # to jedyny trwały dowód rozdarcia — a błąd jest jawny i tego samego
            # typu co reszta fail-closed (konsument nie musi zgadywać po typie).
            raise JournalError(
                f"root {entry['root']!r}: nie udało się przywrócić stanu PRZED "
                f"({type(e).__name__}); znacznik zostaje, wymagana decyzja operatora"
            ) from e
        if _sha256_file(entry["path"]) != entry["sha256_before"]:
            raise JournalError(
                f"root {entry['root']!r}: przywrócenie nie odtworzyło stanu PRZED"
            )
        restored.append(entry["root"])

    _remove_durable(journal_path)
    return {"status": "healed", "roots": restored}


# --- transakcja --------------------------------------------------------------

def _rollback_to_before(doc_entries: List[Dict[str, Any]],
                        backed_up: set) -> List[str]:
    """Cofnij rooty do stanu PRZED i ZWERYFIKUJ każdy z nich.

    Zwraca listę root-id, których NIE udało się potwierdzić w stanie PRZED (pusta
    lista = rollback pełny). Odtwarzanie idzie przez :func:`atomic_write_bytes`
    (temp → fsync → rename), nigdy ``shutil.copy2``: ``copy2`` NAJPIERW obcina cel,
    więc porażka w połowie (ENOSPC — ta sama awaria, która wywaliła zapis roota)
    zostawiłaby root URWANY zamiast starego.

    Backup jest materiałem zaufanym inaczej niż przy ``recover_pending``: powstał
    chwilę temu, w TYM procesie, z bajtów sprzed transakcji — więc kontrolą jest
    ``sha256`` względem stanu PRZED, a nie walidator schematu (schemat generacji
    „przed" to nie jest coś, co ten rollback ma prawo zmieniać).
    """
    unconfirmed: List[str] = []
    for rec in doc_entries:
        path = rec["path"]
        if _sha256_file(path) == rec["sha256_before"]:
            continue                      # root nietknięty (albo już cofnięty)
        if path not in backed_up:
            unconfirmed.append(rec["root"])
            continue
        try:
            with open(rec["backup"], "rb") as fh:
                raw = fh.read()
            if hashlib.sha256(raw).hexdigest() != rec["sha256_before"]:
                unconfirmed.append(rec["root"])
                continue
            atomic_write_bytes(path, raw)
        except OSError:
            unconfirmed.append(rec["root"])
            continue
        if _sha256_file(path) != rec["sha256_before"]:
            unconfirmed.append(rec["root"])
    return unconfirmed



def run_transaction(journal_path: str, entries: Iterable[Dict[str, Any]],
                    *, write=None) -> Dict[str, Any]:
    """Zapisz komplet rootów jako JEDNĄ transakcję pod trwałym znacznikiem.

    ``entries``: ``{"root": <id z identity.schema.ROOT_VALIDATORS>, "path": str,
    "backup": str, "payload": obj}`` w kolejności zapisu.

    ``write``: seam zapisu roota (domyślnie :func:`atomic_write_json`);
    ``courier_admin`` podaje swój ``_atomic_write_json`` — to jedyne miejsce,
    gdzie test może wstrzyknąć błąd pojedynczego pliku.

    Kolejność: walidacja WSZYSTKICH payloadów → backupy → znacznik → zapisy →
    commit. Błąd łapalny (``Exception``) po utworzeniu backupów = rollback z
    backupów, sprzątnięcie znacznika i re-raise oryginału. ``BaseException``
    (kill -9 / OOM) celowo NIE jest łapany — znacznik zostaje na dysku i to on
    jest jedynym dowodem, że generacja jest rozdarta.
    """
    write = write or atomic_write_json
    items = list(entries)
    if not items:
        raise JournalError("transakcja bez rootów")

    doc_entries: List[Dict[str, Any]] = []
    for item in items:
        root = item["root"]
        _validate_payload(root, item["payload"])
        before = _sha256_file(item["path"])
        if before is None:
            raise JournalError(f"root {root!r}: plik nie istnieje — brak stanu PRZED")
        doc_entries.append({
            "root": root,
            "path": item["path"],
            "backup": item["backup"],
            "sha256_before": before,
            "sha256_after": hashlib.sha256(canonical_bytes(item["payload"])).hexdigest(),
        })

    backed_up: set = set()
    try:
        # MATERIAŁ DO LECZENIA MUSI BYĆ TRWAŁY, ZANIM POWSTANIE ZNACZNIK: fsync
        # treści KAŻDEGO backupu i fsync KAŻDEGO RÓŻNEGO katalogu backupów
        # (kurier_full_names leży w innym katalogu niż pozostałe cztery). Inaczej
        # po zaniku zasilania znacznik przeżywa, a backup — nie, i stan leczalny
        # zamienia się w trwały blok operatorski.
        fsynced_dirs = set()
        for item, rec in zip(items, doc_entries):
            shutil.copy2(item["path"], rec["backup"])
            _fsync_file(rec["backup"])
            backed_up.add(item["path"])
            d = os.path.dirname(os.path.abspath(rec["backup"])) or "."
            if d not in fsynced_dirs:
                _fsync_dir(rec["backup"])
                fsynced_dirs.add(d)

        atomic_write_json(journal_path, {
            "version": JOURNAL_VERSION,
            "entries": doc_entries,
        })

        for item in items:
            write(item["path"], item["payload"])
    except Exception as exc:
        # Rollback w procesie (parytet ze starym kontraktem), ale WERYFIKOWANY:
        # znacznik wolno skasować dopiero, gdy KAŻDY root jest potwierdzony w
        # stanie PRZED. Wcześniej rollback był best-effort (`copy2` w pętli z
        # `except: pass`), a znacznik znikał bezwarunkowo — przy samo-skorelowanej
        # klasie awarii (ENOSPC psuje i zapis, i odtworzenie) zostawiało to
        # ROZDARTĄ generację BEZ ŚLADU, a następne wejście cicho dokładało do niej
        # kolejną tożsamość. Skoro rollback nie wyszedł: znacznik ZOSTAJE (jedyny
        # trwały dowód rozdarcia) i lecimy fail-closed.
        unconfirmed = _rollback_to_before(doc_entries, backed_up)
        if unconfirmed:
            raise JournalError(
                f"zapis generacji padł ({type(exc).__name__}), a rollback NIE cofnął "
                f"rootów {sorted(unconfirmed)} do stanu PRZED — generacja jest rozdarta, "
                f"znacznik zostaje do leczenia/decyzji operatora"
            ) from exc
        _remove_durable(journal_path)
        raise

    _remove_durable(journal_path)
    return {"status": "committed", "roots": [e["root"] for e in doc_entries]}
