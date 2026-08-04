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

  1. **Znacznik (journal)** — przed pierwszym zapisem powstaje trwały plik
     ``.onboarding-journal.json`` (atomowo + ``fsync`` pliku i katalogu) z
     listą rootów: ścieżka, ścieżka backupu, ``sha256`` stanu PRZED i sha256
     bajtów, które transakcja zamierza zapisać (PO).
  2. **Zapis** pięciu rootów.
  3. **Commit = usunięcie znacznika** (durable unlink + ``fsync`` katalogu).
     Brak znacznika ≡ brak transakcji w locie.

``recover_pending`` przy KAŻDYM wejściu do onboardingu:

  * brak znacznika → nic (czysto),
  * wszystkie rooty w stanie PRZED → transakcja nie ruszyła: sprzątamy znacznik,
  * wszystkie rooty w stanie PO → zapisy się dokończyły, padło przed commitem:
    domykamy commit,
  * MIESZANKA → rozdarta generacja: **rollback do stanu PRZED** z backupów,
  * cokolwiek nieczytelnego / niekompletnego / obcego (sha ≠ PRZED i ≠ PO,
    brak backupu, backup o innym sha, backup łamiący schemat roota) →
    **FAIL-CLOSED** ``JournalError``; nigdy cicha kontynuacja i nigdy
    przywrócenie śmiecia.

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


@contextmanager
def onboarding_lock(journal_path: str):
    """``flock(LOCK_EX)`` na całą sekcję krytyczną onboardingu (recovery + zapis)."""
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
    """
    if not os.path.exists(journal_path):
        return {"status": "clean", "roots": []}

    doc = _read_journal(journal_path)
    entries: List[Dict[str, Any]] = doc["entries"]

    states = {}
    for entry in entries:
        current = _sha256_file(entry["path"])
        if current == entry["sha256_before"]:
            states[entry["path"]] = "before"
        elif current == entry["sha256_after"]:
            states[entry["path"]] = "after"
        else:
            raise JournalError(
                f"root {entry['root']!r} ma bajty spoza transakcji (sha ≠ PRZED i ≠ PO) — "
                f"recovery nie zgaduje, wymagana decyzja operatora"
            )

    distinct = set(states.values())
    if distinct == {"before"}:
        _remove_durable(journal_path)
        return {"status": "aborted", "roots": []}
    if distinct == {"after"}:
        _remove_durable(journal_path)
        return {"status": "committed", "roots": [e["root"] for e in entries]}

    # ROZDARTA GENERACJA — rollback do stanu PRZED. Najpierw sprawdzamy materiał
    # dla WSZYSTKICH rootów transakcji (nie tylko tych do nadpisania): połowiczne
    # leczenie byłoby drugim rozdarciem, a niesprawdzony root to dokładnie luka K5.
    material = {entry["path"]: _restorable_payload(entry) for entry in entries}

    restored: List[str] = []
    for entry in entries:
        if states[entry["path"]] == "before":
            continue
        atomic_write_bytes(entry["path"], material[entry["path"]])
        if _sha256_file(entry["path"]) != entry["sha256_before"]:
            raise JournalError(
                f"root {entry['root']!r}: przywrócenie nie odtworzyło stanu PRZED"
            )
        restored.append(entry["root"])

    _remove_durable(journal_path)
    return {"status": "healed", "roots": restored}


# --- transakcja --------------------------------------------------------------

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

    backups: List[tuple] = []
    try:
        for item, rec in zip(items, doc_entries):
            shutil.copy2(item["path"], rec["backup"])
            backups.append((item["path"], rec["backup"]))
        _fsync_dir(doc_entries[0]["backup"])

        atomic_write_json(journal_path, {
            "version": JOURNAL_VERSION,
            "entries": doc_entries,
        })

        for item in items:
            write(item["path"], item["payload"])
    except Exception:
        # Rollback w procesie (parytet ze starym kontraktem) + sprzątnięcie
        # znacznika: stan wraca do PRZED, więc nie ma czego leczyć po restarcie.
        for orig, bk in backups:
            try:
                shutil.copy2(bk, orig)
            except Exception:
                pass
        _remove_durable(journal_path)
        raise

    _remove_durable(journal_path)
    return {"status": "committed", "roots": [e["root"] for e in doc_entries]}
