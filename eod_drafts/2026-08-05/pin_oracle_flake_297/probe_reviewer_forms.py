#!/usr/bin/env python3
"""Sesja 297 / iter2 — SONDY SIŁY ORACLE odtworzone z blind review 2026-08-05.

Odtwarza 1:1 formy wycieku z sondy recenzenta
(`/root/artifacts/blind-297/pinflake/reviewer_probes/probe_oracle_strength.py`)
i mierzy dla każdej:
  * NOWY oracle (`tests/pin_kdf_store_oracle.assert_no_plaintext_pin_in_store`),
  * STARY oracle mastera 06e4d5c39 (`pin not in json.dumps(store)` + kdf/iter/salt-len),
  * czy PIN jest DOSŁOWNIE czytelny w pliku i czy rekord pozostał prawdziwym PBKDF2.

`--pin-len 12` (default) = warunki kontraktowe bramki (SYNTETYCZNY PIN >=
`MIN_ORACLE_PIN_LEN`): każda forma musi być RED przez SWOJĄ asercję.
`--pin-len 4` = dokładne warunki recenzenta (PIN produkcyjny): tam nowy oracle
jest RED już na kontrakcie długości (fail-closed), co jest poprawne, ale nie
dowodzi siły pojedynczych asercji — dlatego domyślny bieg to 12.

Izolacja: wyłącznie katalog tymczasowy TWORZONY OBOK TEGO PLIKU (nigdy /tmp, nigdy
żywy `dispatch_state`). Importujemy WYŁĄCZNIE `identity/pin_auth` + moduł oracle.
PIN-y są syntetyczne i nigdy nie są drukowane.
"""
import argparse
import atexit
import json
import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
_PKG_DIR = _HERE.parents[3]  # katalog pakietu (w worktree nazywa się jak gałąź)


def _bootstrap_import_path():
    if _PKG_DIR.name == "dispatch_v2":
        sys.path.insert(0, str(_PKG_DIR.parent))
        return
    shim = tempfile.mkdtemp(dir=str(_HERE.parent), prefix=".pkgshim-")
    atexit.register(shutil.rmtree, shim, True)
    os.symlink(str(_PKG_DIR), os.path.join(shim, "dispatch_v2"))
    sys.path.insert(0, shim)


_bootstrap_import_path()

from dispatch_v2.identity import pin_auth  # noqa: E402
from dispatch_v2.tests.pin_kdf_store_oracle import (  # noqa: E402
    SALT_HEX_LEN,
    assert_no_plaintext_pin_in_store as new_oracle,
)

NAME = "Marcin By"


def old_oracle(kdf_path, pins):
    """DOKŁADNA kopia oracle z mastera 06e4d5c39 (przed sesją 297)."""
    store = pin_auth._load_json(kdf_path)
    blob = json.dumps(store)
    for pin in pins:
        assert pin not in blob, "PLAINTEXT PIN w magazynie KDF (mutacja/regresja)"
    salts = []
    for name, rec in store.items():
        assert rec.get("kdf") == pin_auth.KDF_NAME, f"{name}: obcy/kdf-less rekord"
        assert int(rec.get("iter", 0)) >= pin_auth._MIN_ITER_FLOOR, f"{name}: kosz KDF poniżej floor"
        assert len(str(rec.get("salt", ""))) >= pin_auth.SALT_BYTES * 2, f"{name}: sól za krótka/brak"
        salts.append(rec["salt"])
    assert len(salts) == len(set(salts)), "sole NIE są per-user unikatowe"


def _rehash(rec, pin):
    rec["hash"] = pin_auth._hash_pin(pin, bytes.fromhex(rec["salt"]), int(rec["iter"])).hex()


def leak_pin_prefix_of_salt(store, name, pin):
    rec = store[name]
    rec["salt"] = pin + rec["salt"][len(pin):]
    _rehash(rec, pin)


def leak_pin_middle_of_salt(store, name, pin):
    rec = store[name]
    s = rec["salt"]
    off = (len(s) - len(pin)) // 2
    rec["salt"] = s[:off] + pin + s[off + len(pin):]
    _rehash(rec, pin)


def leak_pin_suffix_of_salt(store, name, pin):
    rec = store[name]
    rec["salt"] = rec["salt"][:-len(pin)] + pin
    _rehash(rec, pin)


def leak_pin_hexencoded_in_salt(store, name, pin):
    rec = store[name]
    rec["salt"] = pin.encode("utf-8").hex().ljust(SALT_HEX_LEN, "0")[:SALT_HEX_LEN]
    _rehash(rec, pin)


def leak_pin_reversed_in_salt(store, name, pin):
    rec = store[name]
    rec["salt"] = pin[::-1] + rec["salt"][len(pin):]
    _rehash(rec, pin)


def leak_pin_in_iter(store, name, pin):
    """Cyfry PIN-u w parametrze kosztu. Dla PIN-u >= 12 cyfr niesiemy 4 ostatnie
    cyfry (PBKDF2_ITERATIONS + int(pin) to ~10¹² iteracji — nie do policzenia)."""
    rec = store[name]
    rec["iter"] = pin_auth.PBKDF2_ITERATIONS + int(pin if len(pin) <= 6 else pin[-4:])
    _rehash(rec, pin)


def leak_pin_split_salt_and_key(store, name, pin):
    half = len(pin) // 2
    rec = store.pop(name)
    rec["salt"] = pin[half:] + rec["salt"][len(pin) - half:]
    _rehash(rec, pin)
    store[f"{name} #{pin[:half]}"] = rec


def leak_extra_record_not_a_map(store, name, pin):
    store["_backup"] = pin


def leak_nested_store(store, name, pin):
    store[name] = {"kdf": pin_auth.KDF_NAME, "salt": store[name]["salt"],
                   "iter": store[name]["iter"], "hash": store[name]["hash"],
                   "legacy": {"pin": pin}}


def leak_pin_uppercase_hex_salt(store, name, pin):
    """KONTROLA KIERUNKU (nie wyciek): sól WIELKIMI literami hex. RED jest tu
    bezpieczny — `bytes.hex()` zawsze zwraca lowercase, więc zmiana produkcji na
    uppercase byłaby świadoma."""
    rec = store[name]
    rec["salt"] = rec["salt"].upper()
    _rehash(rec, pin)


PROBES = [
    ("pin_prefix_of_salt", leak_pin_prefix_of_salt, True),
    ("pin_middle_of_salt", leak_pin_middle_of_salt, True),
    ("pin_suffix_of_salt", leak_pin_suffix_of_salt, True),
    ("pin_hexencoded_in_salt", leak_pin_hexencoded_in_salt, True),
    ("pin_reversed_in_salt", leak_pin_reversed_in_salt, True),
    ("pin_in_iter_value", leak_pin_in_iter, True),
    ("pin_split_salt_and_key", leak_pin_split_salt_and_key, True),
    ("extra_record_not_a_map", leak_extra_record_not_a_map, True),
    ("nested_legacy_pin", leak_nested_store, True),
    ("uppercase_hex_salt (NIE wyciek)", leak_pin_uppercase_hex_salt, False),
]


def run(pin):
    rows = []
    with tempfile.TemporaryDirectory(dir=str(_HERE.parent), prefix=".probe-") as tmp:
        kdf = str(Path(tmp) / "kurier_piny_kdf.json")
        for label, mutate, is_leak in PROBES:
            store = {NAME: pin_auth.make_record(pin)}
            mutate(store, NAME, pin)
            pin_auth._atomic_write_json(kdf, store)
            blob = json.dumps(pin_auth._load_json(kdf))
            recs = [r for r in pin_auth._load_json(kdf).values() if isinstance(r, dict)]
            kdf_true = any(pin_auth.verify_record(pin, r) for r in recs)
            try:
                new_oracle(kdf, [pin])
                new_v, new_msg = "GREEN", ""
            except AssertionError as e:
                new_v, new_msg = "RED", str(e).splitlines()[0][:72]
            except Exception as e:
                new_v, new_msg = f"ERR({type(e).__name__})", str(e)[:72]
            try:
                old_oracle(kdf, [pin])
                old_v = "GREEN"
            except AssertionError:
                old_v = "RED"
            except Exception as e:
                old_v = f"ERR({type(e).__name__})"
            rows.append(dict(probe=label, real_leak=is_leak, new=new_v, old=old_v,
                             pin_verbatim_in_file=(pin in blob), kdf_bijection_ok=kdf_true,
                             new_msg=new_msg))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pin-len", type=int, default=12,
                    help="12 = kontrakt bramki (default); 4 = warunki recenzenta")
    ap.add_argument("--seed", type=int, default=297)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    pin = "".join(rnd.choice("0123456789") for _ in range(a.pin_len))

    rows = run(pin)
    print(f"PIN syntetyczny: {a.pin_len} cyfr (seed={a.seed})")
    print(f"{'sonda':34s} {'wyciek':7s} {'NOWY':6s} {'STARY':6s} {'PIN dosł.':10s} {'F(KDF)':7s} msg")
    for r in rows:
        print(f"{r['probe']:34s} {str(r['real_leak']):7s} {r['new']:6s} {r['old']:6s} "
              f"{str(r['pin_verbatim_in_file']):10s} {str(r['kdf_bijection_ok']):7s} {r['new_msg']}")
    missed = [r for r in rows if r["real_leak"] and r["new"] == "GREEN"]
    regress = [r for r in missed if r["old"] == "RED"]
    print()
    print(f"REALNE WYCIEKI przepuszczone przez NOWY: {len(missed)} "
          + (", ".join(r["probe"] for r in missed) if missed else "— (brak)"))
    print(f"OSŁABIENIE vs STARY (stary RED, nowy GREEN): {len(regress)} "
          + (", ".join(r["probe"] for r in regress) if regress else "— (brak)"))
    return 0 if not missed else 1


if __name__ == "__main__":
    raise SystemExit(main())
