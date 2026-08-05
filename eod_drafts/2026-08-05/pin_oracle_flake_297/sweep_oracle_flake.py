#!/usr/bin/env python3
"""Sesja 297 — DOWÓD ANTY-FLAKE dla security-oracle `_no_plaintext_oracle`
(bramka `tests.pin-kdf-oracle-substring-flake`).

Pętla N (default 1000) niezależnych biegów: dla losowych SYNTETYCZNYCH PIN-ów o
długości `MIN_ORACLE_PIN_LEN` (kontrakt anty-flake oracle) i losowych soli buduje
ŚWIEŻY magazyn KDF (prawdziwe `pin_auth.make_record`, PBKDF2 200k) i liczy:

  * ile razy czerwieni PEŁNY oracle (`tests/pin_kdf_store_oracle.py`, asercje A-G
    ze SKANEM DOSŁOWNYM włącznie) → oczekiwane 0,
  * ile razy zaczerwieniłby SAM skan dosłowny `pin not in json.dumps(store)` na
    tych samych syntetycznych PIN-ach → oczekiwane 0 (to dowód, że przywrócenie
    skanu w iter2 NIE przywraca flake'a),
  * DIAGNOSTYKA ŹRÓDŁA FLAKE'A: ile razy skan dosłowny trafiłby PIN 4-cyfrowy
    (produkcyjna długość, `identity/schema.PIN_LENGTH`) w tym samym losowym
    hexie → oczekiwane ~0,5-1% (to była fałszywa czerwień nocnego strażnika).

Wniosek kontrolowany przez ten skrypt: źródłem flake'a była DŁUGOŚĆ PIN-u, a nie
idea skanu dosłownego — dlatego oracle iter2 skan ma i egzekwuje długość PIN-u.

Uruchomienie (venv dispatch, dowolny katalog roboczy):
    /root/.openclaw/venvs/dispatch/bin/python \
        <worktree>/eod_drafts/2026-08-05/pin_oracle_flake_297/sweep_oracle_flake.py --runs 1000

Izolacja: wyłącznie katalog tymczasowy TWORZONY OBOK TEGO PLIKU (nigdy /tmp, nigdy
żywy `dispatch_state`); `resolve_pin` wołany z jawnym `use_kdf=True`, więc skrypt nie
czyta `flags.json`. Importujemy WYŁĄCZNIE `identity/pin_auth` + moduł oracle (same
stdlib) — NIE `gps_server`, który przy imporcie otwiera żywy log (poza pytest nie ma
HERMETIC-GUARD). PIN-y są syntetyczne i nigdy nie są drukowane.
"""
import argparse
import atexit
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
_PKG_DIR = _HERE.parents[3]  # katalog pakietu (w worktree nazywa się jak gałąź)


def _bootstrap_import_path():
    """Udostępnia pakiet jako `dispatch_v2` niezależnie od nazwy katalogu worktree
    (bez zależności od symlinków pkgroot)."""
    if _PKG_DIR.name == "dispatch_v2":
        sys.path.insert(0, str(_PKG_DIR.parent))
        return
    shim = tempfile.mkdtemp(dir=str(_HERE.parent), prefix=".pkgshim-")
    atexit.register(shutil.rmtree, shim, True)
    os.symlink(str(_PKG_DIR), os.path.join(shim, "dispatch_v2"))
    sys.path.insert(0, shim)


_bootstrap_import_path()

from dispatch_v2.identity import pin_auth  # noqa: E402
# Oracle + katalog form wycieku 1:1 z bramki testowej (jedno źródło — bramka
# `tests/test_a6_security_pin_kdf.py` importuje DOKŁADNIE ten moduł).
from dispatch_v2.tests import pin_kdf_store_oracle as _oracle  # noqa: E402
from dispatch_v2.tests.pin_kdf_store_oracle import (  # noqa: E402
    assert_no_plaintext_pin_in_store as _no_plaintext_oracle,
)

PROD_PIN_LEN = 4  # identity/schema.PIN_LENGTH — długość, która dawała flake


def _random_pins(rnd, count, length):
    """Unikatowe SYNTETYCZNE PIN-y bramki (długość = kontrakt anty-flake)."""
    pins = set()
    while len(pins) < count:
        pins.add("".join(rnd.choice("0123456789") for _ in range(length)))
    return sorted(pins)


def sweep(runs, couriers, seed, pin_len):
    rnd = random.Random(seed)
    new_red = []
    scan_red = 0       # sam skan dosłowny na PIN-ach syntetycznych (>= MIN_ORACLE_PIN_LEN)
    prod_scan_red = 0  # DIAGNOSTYKA: skan dosłowny na PIN-ie 4-cyfrowym
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=str(_HERE.parent), prefix=".sweep-") as tmp:
        base = Path(tmp)
        for i in range(runs):
            pins = _random_pins(rnd, couriers, pin_len)
            names = [f"Kurier {j}" for j in range(couriers)]
            d = base / f"run{i}"
            d.mkdir()
            piny = d / "kurier_piny.json"
            kdf = d / "kurier_piny_kdf.json"
            piny.write_text(json.dumps(dict(zip(pins, names))), encoding="utf-8")
            for pin in pins:
                pin_auth.resolve_pin(pin, piny_path=str(piny), kdf_path=str(kdf),
                                     use_kdf=True)
            # (1) PEŁNY oracle A-G (ze skanem dosłownym).
            try:
                _no_plaintext_oracle(str(kdf), pins)
            except AssertionError as e:
                new_red.append((i, str(e).splitlines()[0]))
            blob = json.dumps(pin_auth._load_json(str(kdf)))
            # (2) sam skan dosłowny na tych samych syntetycznych PIN-ach.
            if any(p in blob for p in pins):
                scan_red += 1
            # (3) DIAGNOSTYKA: ten sam skan, ale PIN produkcyjnej długości.
            prod_pins = _random_pins(rnd, couriers, PROD_PIN_LEN)
            if any(p in blob for p in prod_pins):
                prod_scan_red += 1
            for f in (piny, kdf, Path(str(kdf) + pin_auth.LOCK_SUFFIX)):
                if f.exists():
                    f.unlink()
            d.rmdir()
    return new_red, scan_red, prod_scan_red, time.time() - t0


def mutations(pin):
    """DOWÓD, ŻE ORACLE POZOSTAŁ ORACLEM: dla każdej formy wycieku z katalogu
    `LEAK_FORMS` (to samo źródło, które parametryzuje bramkę testową) oracle MUSI
    być RED — i osobno: czy STARY skan substringiem w ogóle by ją złapał."""
    name = "Marcin By"
    rows = []
    with tempfile.TemporaryDirectory(dir=str(_HERE.parent), prefix=".mut-") as tmp:
        kdf = str(Path(tmp) / "kurier_piny_kdf.json")
        for form, mutate in sorted(_oracle.LEAK_FORMS.items()):
            store = {name: pin_auth.make_record(pin)}
            mutate(store, name, pin)
            pin_auth._atomic_write_json(kdf, store)
            try:
                _no_plaintext_oracle(kdf, [pin])
                verdict, msg = "GREEN (!!)", ""
            except AssertionError as e:
                verdict, msg = "RED", str(e).splitlines()[0]
            old_catches = pin in json.dumps(store)
            rows.append((form, verdict, old_catches, msg))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1000)
    ap.add_argument("--couriers", type=int, default=2, help="rekordów w magazynie / bieg")
    ap.add_argument("--seed", type=int, default=297)
    a = ap.parse_args()
    pin_len = _oracle.MIN_ORACLE_PIN_LEN

    print("── MUTACJE (każda forma realnego wycieku MUSI dać RED) ──")
    rnd = random.Random(a.seed)
    mut = mutations("".join(rnd.choice("0123456789") for _ in range(pin_len)))
    for form, verdict, old_catches, msg in mut:
        print(f"  {form:26s} nowy={verdict:9s} stary_skan={'RED' if old_catches else 'GREEN(przeoczenie)'}"
              f"  | {msg}")
    mut_ok = all(v == "RED" for _, v, _, _ in mut)
    print(f"MUTACJE: {sum(1 for _, v, _, _ in mut if v == 'RED')}/{len(mut)} RED "
          f"→ {'OK' if mut_ok else 'ORACLE OSŁABIONY'}\n")

    print("── SWEEP ANTY-FLAKE ──")
    new_red, scan_red, prod_scan_red, dt = sweep(a.runs, a.couriers, a.seed, pin_len)
    print(f"runs={a.runs} couriers/run={a.couriers} seed={a.seed} pin_len={pin_len} czas={dt:.1f}s")
    print(f"PEŁNY oracle A-G (PIN {pin_len} cyfr)        : {len(new_red)} czerwieni "
          f"({len(new_red) / a.runs * 100:.2f}% biegów)")
    print(f"sam skan dosłowny (PIN {pin_len} cyfr)       : {scan_red} czerwieni "
          f"({scan_red / a.runs * 100:.2f}% biegów)")
    print(f"DIAGNOZA: skan dosłowny (PIN {PROD_PIN_LEN} cyfry)  : {prod_scan_red} czerwieni "
          f"({prod_scan_red / a.runs * 100:.2f}% biegów) ← źródło flake'a")
    for i, msg in new_red[:10]:
        print(f"  RED@{i}: {msg}")
    ok = not new_red and mut_ok
    print("WERDYKT:", "OK — 0 fałszywych czerwieni + oracle nadal łapie każdy wyciek"
          if ok else "NIE-OK (flake lub osłabiony oracle)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
