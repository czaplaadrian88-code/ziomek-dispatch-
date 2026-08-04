"""Atomic roster updates dla nowych kurierow.

Aktualizuje 5 plikow w jednej transakcji z rollback on partial fail:
  - dispatch_state/kurier_ids.json
  - dispatch_state/kurier_piny.json
  - dispatch_state/courier_tiers.json
  - dispatch_state/courier_names.json  (cid -> krotka nazwa panelowa; do 2026-07-10
    POMIJANY przez onboarding -> 19 CID bez wpisu, luka Z-P1-05 zamknieta backfillem)
  - dispatch_v2/daily_accounting/kurier_full_names.json

Hard rules:
  - Atomic per-file (temp + fsync + rename)
  - fcntl.LOCK_EX per file w trakcie modify
  - Backup z timestamp suffix przed write
  - Jezeli partial-write fail: restore z backupow + raise
  - PIN: 4-digit, bezkolizyjny (max 100 retries, raise jak nie znajdzie)
  - Alias derivation: <FirstName> <First2OfSurname> bez kropki, np. "Marcin Bystrowski" -> "Marcin By"
"""
import json, os, fcntl, secrets, datetime, tempfile, shutil
from typing import Dict

from dispatch_v2.identity.schema import canonical_courier_id, canonical_numeric_cid

KURIER_IDS = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"
KURIER_PINY = "/root/.openclaw/workspace/dispatch_state/kurier_piny.json"
COURIER_TIERS = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"
COURIER_NAMES = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
KURIER_FULL_NAMES = "/root/.openclaw/workspace/scripts/dispatch_v2/daily_accounting/kurier_full_names.json"

ALL_FILES = [KURIER_IDS, KURIER_PINY, COURIER_TIERS, COURIER_NAMES, KURIER_FULL_NAMES]


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
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-roster-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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

    # Backup wszystkich
    backups = []
    try:
        for p in ALL_FILES:
            bk = p + bak_suffix
            shutil.copy2(p, bk)
            backups.append((p, bk))

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

        # Atomic writes
        _atomic_write_json(KURIER_IDS, kids)
        _atomic_write_json(KURIER_PINY, piny)
        _atomic_write_json(COURIER_TIERS, tiers)
        _atomic_write_json(COURIER_NAMES, names)
        _atomic_write_json(KURIER_FULL_NAMES, full)
    except Exception as e:
        # Rollback z backupow
        for orig, bk in backups:
            try:
                shutil.copy2(bk, orig)
            except Exception:
                pass
        raise RuntimeError(f"add_new_courier failed, rolled back: {type(e).__name__}: {e}") from e

    return {"cid": cid, "alias": alias, "full_name": full_name, "pin": pin}
