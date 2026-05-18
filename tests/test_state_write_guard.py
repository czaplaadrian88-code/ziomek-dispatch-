"""Faza 1 — state write guard tests (incydent 2026-05-18 14:47 orders_state clobber).

Root cause: _read_state() zwracało ciche {} przy FileNotFoundError/JSONDecodeError,
a upsert_order ufał temu i zapisywał {} + 1 order → total state loss całej floty.

Coverage:
  1. _read_state_strict happy path → zwraca pełny dict
  2. _read_state_strict, plik zniknął + .prev istnieje (non-bootstrap) → StateReadError
  3. _read_state_strict, brak pliku + brak .prev (bootstrap) → {} (legalny pierwszy zapis)
  4. _read_state_strict, malformed JSON → StateReadError
  5. CAUSAL: upsert_order przy zniknietym pliku → raise, plik NIE odtworzony jako 1-order
  6. upsert_order normalnie → dodaje order, zachowuje resztę (zero regresji)
  7. _guarded_write blokuje regresję liczności (op=upsert, count maleje) → StateReadError
  8. _guarded_write dopuszcza delete -1
  9. _guarded_write blokuje delete -2 (za duży spadek)
 10. backup-on-write: .prev powstaje po zapisie
 11. kill-switch ENABLE_STATE_WRITE_GUARD=false → guard wyłączony (regresja przechodzi)
 12. alert odpala się gdy guard blokuje zapis

Pattern: tmp state file, patch _state_path + stub _alert_state_read_failure
(zero realnego Telegrama). Custom runner — pytest nieinstalowany w env dispatch.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import state_machine as sm
from dispatch_v2.state_machine import StateReadError

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label} {detail}")


def expect_raises(label, fn, exc=StateReadError):
    try:
        fn()
        check(label, False, "(brak wyjątku)")
    except exc:
        check(label, True)
    except Exception as e:
        check(label, False, f"(zły wyjątek: {type(e).__name__}: {e})")


class _GuardState:
    """Context manager: temp state file, patched _state_path,
    stub _alert_state_read_failure (rejestruje wywołania, zero Telegrama)."""

    def __init__(self, initial=None):
        self.initial = initial  # dict albo None (plik nie istnieje)

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="state_guard_")
        self.path = os.path.join(self.tmpdir, "orders_state.json")
        if self.initial is not None:
            with open(self.path, "w") as f:
                json.dump(self.initial, f)
        self.alert_calls = []
        self._orig_path = sm._state_path
        self._orig_alert = sm._alert_state_read_failure
        sm._state_path = lambda: self.path
        sm._alert_state_read_failure = lambda detail: self.alert_calls.append(detail)
        return self

    def write_prev(self):
        """Symuluje istnienie backupu .prev (plik istniał wcześniej)."""
        with open(self.path + ".prev", "w") as f:
            json.dump({}, f)

    def read_raw(self):
        with open(self.path) as f:
            return json.load(f)

    def exists(self):
        return os.path.exists(self.path)

    def __exit__(self, *a):
        sm._state_path = self._orig_path
        sm._alert_state_read_failure = self._orig_alert
        for suffix in ("", ".lock", ".prev"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass
        for fn in os.listdir(self.tmpdir):
            try:
                os.unlink(os.path.join(self.tmpdir, fn))
            except OSError:
                pass
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass


print("=== Faza 1 state write guard ===")

# Test 1 — _read_state_strict happy path
with _GuardState({"A": {"status": "assigned"}, "B": {"status": "picked_up"}}) as g:
    st = sm._read_state_strict()
    check("1. _read_state_strict happy → pełny dict (2 ordery)", len(st) == 2)

# Test 2 — plik zniknął, .prev istnieje (non-bootstrap) → StateReadError
with _GuardState({"A": {"status": "assigned"}}) as g:
    os.unlink(g.path)        # plik znika
    g.write_prev()           # ale .prev istnieje → plik istniał wcześniej
    expect_raises("2. _read_state_strict: plik zniknął + .prev → StateReadError",
                  sm._read_state_strict)

# Test 3 — brak pliku + brak .prev (bootstrap) → {}
with _GuardState(None) as g:  # plik nie utworzony
    try:
        st = sm._read_state_strict()
        check("3. _read_state_strict: bootstrap (brak pliku + brak .prev) → {}",
              st == {})
    except Exception as e:
        check("3. _read_state_strict: bootstrap → {}", False,
              f"({type(e).__name__}: {e})")

# Test 4 — malformed JSON → StateReadError
with _GuardState({"A": {}}) as g:
    with open(g.path, "w") as f:
        f.write("{ to nie jest poprawny json")
    expect_raises("4. _read_state_strict: malformed JSON → StateReadError",
                  sm._read_state_strict)

# Test 5 — CAUSAL: upsert przy zniknietym pliku NIE clobberuje
with _GuardState({f"O{i}": {"status": "assigned", "courier_id": str(i)}
                  for i in range(5)}) as g:
    os.unlink(g.path)
    g.write_prev()           # plik istniał wcześniej → brak bootstrap
    expect_raises("5a. upsert przy zniknietym pliku → StateReadError",
                  lambda: sm.upsert_order("NEW", {"status": "assigned"},
                                          event="COURIER_ASSIGNED"))
    check("5b. CAUSAL: plik NIE odtworzony jako 1-order clobber",
          not g.exists())
    check("5c. alert odpalił się przy zablokowanym zapisie",
          len(g.alert_calls) >= 1)

# Test 6 — upsert normalnie: dodaje + zachowuje resztę
with _GuardState({"O1": {"status": "assigned", "courier_id": "1"},
                  "O2": {"status": "picked_up", "courier_id": "2"},
                  "O3": {"status": "assigned", "courier_id": "3"}}) as g:
    sm.upsert_order("O4", {"status": "assigned", "courier_id": "4"},
                    event="COURIER_ASSIGNED")
    st = g.read_raw()
    check("6a. upsert normalnie → 4 ordery (3 stare + 1 nowy)", len(st) == 4)
    check("6b. stare ordery zachowane", all(k in st for k in ("O1", "O2", "O3")))
    check("6c. nowy order zapisany z cid", st.get("O4", {}).get("courier_id") == "4")

# Test 7 — _guarded_write blokuje regresję liczności (upsert, count maleje)
with _GuardState({"X": {}}) as g:
    expect_raises("7. _guarded_write op=upsert, count 10→3 → StateReadError",
                  lambda: sm._guarded_write(Path(g.path), {"only": {}}, old_count=10,
                                            op="upsert"))

# Test 8 — _guarded_write dopuszcza delete -1
with _GuardState({"X": {}}) as g:
    try:
        sm._guarded_write(Path(g.path), {"a": {}, "b": {}}, old_count=3, op="delete")
        check("8. _guarded_write op=delete, count 3→2 → OK", g.read_raw() and
              len(g.read_raw()) == 2)
    except Exception as e:
        check("8. _guarded_write op=delete -1 → OK", False,
              f"({type(e).__name__}: {e})")

# Test 9 — _guarded_write blokuje delete -2 (za duży spadek)
with _GuardState({"X": {}}) as g:
    expect_raises("9. _guarded_write op=delete, count 5→3 (-2) → StateReadError",
                  lambda: sm._guarded_write(Path(g.path), {"a": {}, "b": {}, "c": {}},
                                            old_count=5, op="delete"))

# Test 10 — backup-on-write: .prev powstaje
with _GuardState({"O1": {"status": "assigned"}}) as g:
    sm.upsert_order("O2", {"status": "assigned"}, event="COURIER_ASSIGNED")
    check("10. backup-on-write: orders_state.json.prev powstał",
          os.path.exists(g.path + ".prev"))

# Test 11 — kill-switch: ENABLE_STATE_WRITE_GUARD=false → guard wyłączony
with _GuardState({"X": {}}) as g:
    _orig_flag = sm.flag
    sm.flag = lambda name, default=False: (
        False if name == "ENABLE_STATE_WRITE_GUARD" else _orig_flag(name, default))
    try:
        sm._guarded_write(Path(g.path), {"only": {}}, old_count=10, op="upsert")
        check("11. kill-switch OFF → regresja przechodzi (surowy _atomic_write)",
              len(g.read_raw()) == 1)
    except Exception as e:
        check("11. kill-switch OFF → regresja przechodzi", False,
              f"({type(e).__name__}: {e})")
    finally:
        sm.flag = _orig_flag

# Test 12 — alert odpala się gdy _read_state_strict raises (osobny, czysty)
with _GuardState({"A": {}}) as g:
    os.unlink(g.path)
    g.write_prev()
    try:
        sm._read_state_strict()
    except StateReadError:
        pass
    check("12. alert (_alert_state_read_failure) odpalił się przy read fail",
          len(g.alert_calls) >= 1)

print("\n" + "=" * 60)
print(f"FAZA 1 STATE WRITE GUARD: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"FAZA 1 STATE WRITE GUARD: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
