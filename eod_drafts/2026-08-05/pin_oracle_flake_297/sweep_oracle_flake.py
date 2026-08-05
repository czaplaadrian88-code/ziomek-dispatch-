#!/usr/bin/env python3
"""Sesja 297 — DOWÓD ANTY-FLAKE dla security-oracle `_no_plaintext_oracle`
(bramka `tests.pin-kdf-oracle-substring-flake`).

Pętla N (default 1000) niezależnych biegów: dla losowych PIN-ów (4-6 cyfr) i losowych
soli buduje ŚWIEŻY magazyn KDF (prawdziwe `pin_auth.make_record`, PBKDF2 200k) i liczy:

  * ile razy czerwieni NOWY oracle strukturalny (`tests/test_a6_security_pin_kdf.py`)
    → oczekiwane 0 (zero fałszywych czerwieni),
  * ile razy zaczerwieniłby STARY skan substringiem `pin not in json.dumps(store)`
    → oczekiwane ~0,5-1% (to jest źródło flake'a nocnego strażnika).

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


def _random_pins(rnd, count):
    """Unikatowe PIN-y 4-6 cyfrowe (kształt jak w `kurier_piny.json`)."""
    pins = set()
    while len(pins) < count:
        n = rnd.choice((4, 4, 4, 5, 6))  # produkcja: dominują 4-cyfrowe
        pins.add("".join(rnd.choice("0123456789") for _ in range(n)))
    return sorted(pins)


def sweep(runs, couriers, seed):
    rnd = random.Random(seed)
    new_red = []
    old_red = 0
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=str(_HERE.parent), prefix=".sweep-") as tmp:
        base = Path(tmp)
        for i in range(runs):
            pins = _random_pins(rnd, couriers)
            names = [f"Kurier {j}" for j in range(couriers)]
            d = base / f"run{i}"
            d.mkdir()
            piny = d / "kurier_piny.json"
            kdf = d / "kurier_piny_kdf.json"
            piny.write_text(json.dumps(dict(zip(pins, names))), encoding="utf-8")
            for pin in pins:
                pin_auth.resolve_pin(pin, piny_path=str(piny), kdf_path=str(kdf),
                                     use_kdf=True)
            # (1) NOWY oracle strukturalny.
            try:
                _no_plaintext_oracle(str(kdf), pins)
            except AssertionError as e:
                new_red.append((i, str(e)))
            # (2) STARY skan substringiem (tylko POMIAR, nie asercja).
            blob = json.dumps(pin_auth._load_json(str(kdf)))
            if any(p in blob for p in pins):
                old_red += 1
            for f in (piny, kdf, Path(str(kdf) + pin_auth.LOCK_SUFFIX)):
                if f.exists():
                    f.unlink()
            d.rmdir()
    return new_red, old_red, time.time() - t0


def mutations():
    """DOWÓD, ŻE ORACLE POZOSTAŁ ORACLEM: dla każdej formy wycieku z katalogu
    `LEAK_FORMS` (to samo źródło, które parametryzuje bramkę testową) oracle MUSI
    być RED — i osobno: czy STARY skan substringiem w ogóle by ją złapał."""
    pin, name = "1234", "Marcin By"
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
                verdict, msg = "RED", str(e)
            old_catches = pin in json.dumps(store)
            rows.append((form, verdict, old_catches, msg))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1000)
    ap.add_argument("--couriers", type=int, default=2, help="rekordów w magazynie / bieg")
    ap.add_argument("--seed", type=int, default=297)
    a = ap.parse_args()

    print("── MUTACJE (każda forma realnego wycieku MUSI dać RED) ──")
    mut = mutations()
    for form, verdict, old_catches, msg in mut:
        print(f"  {form:26s} nowy={verdict:9s} stary_skan={'RED' if old_catches else 'GREEN(przeoczenie)'}"
              f"  | {msg}")
    mut_ok = all(v == "RED" for _, v, _, _ in mut)
    print(f"MUTACJE: {sum(1 for _, v, _, _ in mut if v == 'RED')}/{len(mut)} RED "
          f"→ {'OK' if mut_ok else 'ORACLE OSŁABIONY'}\n")

    print("── SWEEP ANTY-FLAKE ──")
    new_red, old_red, dt = sweep(a.runs, a.couriers, a.seed)
    print(f"runs={a.runs} couriers/run={a.couriers} seed={a.seed} czas={dt:.1f}s")
    print(f"STARY oracle (skan substringiem)  : {old_red} czerwieni "
          f"({old_red / a.runs * 100:.2f}% biegów)")
    print(f"NOWY oracle (strukturalny)        : {len(new_red)} czerwieni "
          f"({len(new_red) / a.runs * 100:.2f}% biegów)")
    for i, msg in new_red[:10]:
        print(f"  RED@{i}: {msg}")
    ok = not new_red and mut_ok
    print("WERDYKT:", "OK — 0 fałszywych czerwieni + oracle nadal łapie każdy wyciek"
          if ok else "NIE-OK (flake lub osłabiony oracle)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
