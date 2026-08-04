"""Atomic roster updates dla nowych kurierow.

Aktualizuje 5 plikow w jednej TRWALEJ transakcji (A-6/G5: znacznik + recovery,
owner = dispatch_v2/identity/journal.py) z rollback on partial fail:
  - dispatch_state/kurier_ids.json
  - dispatch_state/kurier_piny.json
  - dispatch_state/courier_tiers.json
  - dispatch_state/courier_names.json  (cid -> krotka nazwa panelowa; do 2026-07-10
    POMIJANY przez onboarding -> 19 CID bez wpisu, luka Z-P1-05 zamknieta backfillem)
  - dispatch_v2/daily_accounting/kurier_full_names.json

Hard rules:
  - Atomic per-file (temp + fsync + rename)
  - Cala sekcja krytyczna (recovery -> odczyt -> zapis) pod flock (identity.journal)
  - Backup z timestamp suffix przed write
  - Jezeli partial-write fail: restore z backupow + raise
  - Twardy crash (kill -9) miedzy zapisami: trwaly znacznik transakcji zostaje na
    dysku, a NASTEPNE wejscie go leczy (rollback do generacji sprzed transakcji)
    albo odmawia jawnie — patrz identity/journal.py (A-6/G5, K5)
  - PIN: 4-digit, bezkolizyjny (max 100 retries, raise jak nie znajdzie)
  - Alias derivation: <FirstName> <First2OfSurname> bez kropki, np. "Marcin Bystrowski" -> "Marcin By"
"""
# os/fcntl/tempfile/shutil zniknely z tego modulu wraz z A-6/G5: backupy, flock,
# zapis atomowy i rollback naleza teraz do JEDNEGO ownera transakcji
# (dispatch_v2/identity/journal.py) — tu nie ma juz drugiej kopii tej polityki.
import json, secrets, datetime
from typing import Dict, List, Tuple

from dispatch_v2.identity import journal
from dispatch_v2.identity.schema import canonical_courier_id, canonical_numeric_cid

KURIER_IDS = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"
KURIER_PINY = "/root/.openclaw/workspace/dispatch_state/kurier_piny.json"
COURIER_TIERS = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"
COURIER_NAMES = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
KURIER_FULL_NAMES = "/root/.openclaw/workspace/scripts/dispatch_v2/daily_accounting/kurier_full_names.json"

ALL_FILES = [KURIER_IDS, KURIER_PINY, COURIER_TIERS, COURIER_NAMES, KURIER_FULL_NAMES]


def transaction_roots() -> List[Tuple[str, str]]:
    """(root-id, sciezka) w KOLEJNOSCI ZAPISU — jedno zrodlo prawdy o tym, co
    wchodzi w transakcje generacji.

    root-id jest kluczem do walidatora schematu w identity/schema.ROOT_VALIDATORS
    (A-6/G5): dolozenie szostego roota bez walidatora lapie ratchet. Sciezki
    czytane late-bound (testy monkeypatchuja stale modulu)."""
    return [
        ("kurier_ids", KURIER_IDS),
        ("kurier_piny", KURIER_PINY),
        ("courier_tiers", COURIER_TIERS),
        ("courier_names", COURIER_NAMES),
        ("kurier_full_names", KURIER_FULL_NAMES),
    ]


def _journal_path() -> str:
    """Znacznik transakcji lezy obok rejestru autoryzacyjnego (kurier_ids)."""
    return journal.journal_path_for(KURIER_IDS)


def derive_alias(full_name: str) -> str:
    """Marcin Bystrowski -> Marcin By. Single-name -> first name only."""
    parts = full_name.strip().split()
    if len(parts) == 0:
        raise ValueError("empty full_name")
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[1][:2]}"


def _generate_unique_pin(existing_pins: set) -> str:
    for _ in range(100):
        p = f"{secrets.randbelow(9000) + 1000:04d}"
        if p not in existing_pins:
            return p
    raise RuntimeError("PIN generation exhausted 100 retries")


def _atomic_write_json(path: str, data: dict) -> None:
    """Seam zapisu pojedynczego roota — DELEGUJE do jedynego ownera zapisu
    atomowego (identity.journal.atomic_write_json: temp -> fsync -> rename ->
    fsync katalogu, format bajtow 1:1 z baseline). Zostaje jako funkcja modulu,
    bo to punkt wstrzykiwania bledu pojedynczego pliku w testach transakcji."""
    journal.atomic_write_json(path, data)


def add_new_courier(cid: int, full_name: str) -> Dict:
    """Atomic add. Returns {cid, alias, full_name, pin}. Raises ValueError on conflict.

    A-6/K6: the CID is validated to a canonical decimal integer BEFORE any read,
    backup or write — a non-canonical scalar (bool/float/negative/non-digit/
    whitespace) is rejected here so it can never reach any of the 5 registries.
    A-6/K8: full-name and CID collisions are checked in BOTH directions.
    """
    # A-6/K6 — canonical CID gate at the boundary, before touching any file.
    # Owner of the CID-canonical contract is identity/schema (delegate, do not
    # re-implement); a bool/float/negative/non-digit/whitespace CID would
    # otherwise be persisted as a distinct identity across the 5 registries.
    cid_key = canonical_numeric_cid(cid)
    if cid_key is None:
        raise ValueError(f"cid {cid!r} nie jest kanonicznym liczbowym CID — odrzucono przed zapisem")

    alias = derive_alias(full_name)
    today_iso = datetime.date.today().isoformat()
    bak_suffix = f".bak-pre-add-{cid}-{today_iso}"

    # A-6/G5 (K5) — CALA sekcja krytyczna pod jednym flock: recovery, odczyt
    # generacji, checki kolizji i zapis. Trzech konsumentow wola add_new_courier
    # (timer dispatch-new-courier-watch, CLI identity.onboarding, /dopisz), a dwa
    # rownolegle wejscia nadpisalyby sobie znacznik transakcji.
    with journal.onboarding_lock(_journal_path()):
        # Niedokonczona transakcja z poprzedniego (zabitego) procesu MUSI zostac
        # domknieta ZANIM cokolwiek przeczytamy — inaczej dopisalibysmy nowa
        # tozsamosc do ROZDARTEJ generacji. Nieczytelny/niekompletny/obcy stan =
        # JournalError (fail-closed), nigdy cicha kontynuacja.
        journal.recover_pending(_journal_path())

        # Load all files
        kids = json.load(open(KURIER_IDS))
        piny = json.load(open(KURIER_PINY))
        tiers = json.load(open(COURIER_TIERS))
        names = json.load(open(COURIER_NAMES))
        full = json.load(open(KURIER_FULL_NAMES))

        # Conflict checks — alias / CID ownership / full-name, BOTH directions (K8).
        if alias in kids and kids[alias] != cid:
            raise ValueError(f"alias {alias!r} juz przypisany do cid {kids[alias]}, nie {cid}")
        # CID must have a single owner (G7 invariant): reject if this CID already
        # backs a DIFFERENT identity in the authorization registry (kurier_ids),
        # even when courier_tiers has drifted and lost its row.
        for _name, _existing in kids.items():
            if _name not in (alias, full_name) and canonical_courier_id(_existing) == cid_key:
                raise ValueError(f"cid {cid} juz nalezy do innej tozsamosci ({_name!r} w kurier_ids)")
        if cid_key in tiers:
            raise ValueError(f"cid {cid} juz istnieje w courier_tiers (name={tiers[cid_key].get('name')!r})")
        if cid_key in names and names[cid_key] != alias:
            raise ValueError(f"cid {cid} juz ma nazwe panelowa {names[cid_key]!r} w courier_names, nie {alias!r}")
        # full-name binding, forward (alias -> different full_name)…
        if alias in full and full[alias] != full_name:
            raise ValueError(f"alias {alias!r} juz w full_names z innym mapping: {full[alias]!r} vs {full_name!r}")
        # …and reverse (full_name already bound to a DIFFERENT alias -> duplicate identity).
        for _al, _full in full.items():
            if _full == full_name and _al != alias:
                raise ValueError(f"full_name {full_name!r} juz zwiazany z aliasem {_al!r}, nie {alias!r}")

        # Generate PIN bezkolizyjny
        pin = _generate_unique_pin(set(piny.keys()))

        # Patch in-memory
        kids[alias] = cid
        kids[full_name] = cid  # full alias rownolegle (matchuje grafik)
        piny[pin] = alias
        names[str(cid)] = alias  # krotka nazwa panelowa (konwencja courier_names)
        full[alias] = full_name
        tiers[str(cid)] = {
            "name": alias,
            "bag": {
                "tier": "new",
                "cap_override": {"off_peak": 1, "normal": 2, "peak": 2},
                "reason": f"new courier added {today_iso}",
            },
            "speed": {"tier_proposed": "SAFE", "delivery_time_p90_min": None},
            "tier_label": "new",
            "added_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        # A-6/G5 — JEDNA transakcja: walidacja schematu KAZDEGO roota -> backupy ->
        # trwaly znacznik -> 5 zapisow -> commit (usuniecie znacznika). Backupy,
        # rollback i znacznik nalezą do ownera (identity/journal); tu zostaje
        # WYLACZNIE zlozenie generacji i mapa root -> payload.
        payloads = {
            "kurier_ids": kids,
            "kurier_piny": piny,
            "courier_tiers": tiers,
            "courier_names": names,
            "kurier_full_names": full,
        }
        entries = [
            {"root": root, "path": path, "backup": path + bak_suffix,
             "payload": payloads[root]}
            for root, path in transaction_roots()
        ]
        try:
            journal.run_transaction(_journal_path(), entries, write=_atomic_write_json)
        except journal.JournalError:
            # Fail-closed (schemat roota / stan znacznika) — jawny blad, bez
            # przebierania go za zwykly blad zapisu.
            raise
        except Exception as e:
            # Parytet komunikatu ze starym kontraktem (rollback z backupow zrobil
            # juz owner transakcji).
            raise RuntimeError(f"add_new_courier failed, rolled back: {type(e).__name__}: {e}") from e

    return {"cid": cid, "alias": alias, "full_name": full_name, "pin": pin}
