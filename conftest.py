"""Root conftest — Z-P2-07 Hermetyczne testy i fixture (Sprint 4).

JEDYNY punkt aktywacji hermetyzacji suity. Rollback CALOSCI = usun TEN plik.

Dlaczego root `dispatch_v2/conftest.py`, a NIE append do `tests/conftest.py`:
  1. Blast radius — guard/DI odseparowany od 17KB tests/conftest (styk z rownoleglymi sesjami).
  2. Kolejnosc — conftest-RODZIC ladowany PRZED tests/conftest → env + sandbox wstaja najwczesniej.
  3. Rozdzial — tests/conftest jest wlascicielem flag/telegram/osrm/script-runnerow; TEN plik:
     state-dir DI + write/read-guard + kwarantanna.

Warstwy (szczegoly: `tests/hermetic_support.py`, `docs/HERMETIC_TESTS.md`):
  (a) DISPATCH_UNDER_PYTEST=1 — idempotentnie (tests/conftest ustawia to samo pozniej → no-op).
  (b) DISPATCH_STATE_DIR      — swiezy sandbox tmp (seed ANONIM.) TYLKO gdy nieustawiony z zewnatrz.
  (c) WRITE-GUARD (autouse session) — blok zapisu do 3 zywych korzeni (dispatch_state / scripts/logs
      / flags.json) we WSZYSTKICH trybach; w STRICT dodatkowo READ-blok zywego dispatch_state.
  (d) pytest_collection_modifyitems — kwarantanna: marker `nonhermetic` (+skip w STRICT).

Tryby:
  DEFAULT             — tylko write-guard; kwarantanna biega (marker bez -m nie wyklucza) →
                        wynik IDENTYCZNY z baseline.
  STRICT (HERMETIC_STRICT=1) — write+read-guard; kwarantanna SKIP → dowod "suita bez dispatch_state".
"""
import atexit
import os
import shutil
import sys

import pytest

# (a) Marker sesji pytest — NAJWCZESNIEJ, idempotentnie (nie nadpisuj zewnetrznego).
os.environ.setdefault("DISPATCH_UNDER_PYTEST", "1")

# Import wsparcia po tej samej sciezce co reszta suity (JEDNA tozsamosc modulu — guard
# patchowany tu jest tym samym kodem, ktory testy asertuja). ZIOMEK_SCRIPTS_ROOT = pkgroot.
_SCRIPTS_ROOT = os.environ.get("ZIOMEK_SCRIPTS_ROOT", "/root/.openclaw/workspace/scripts")
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)
from dispatch_v2.tests import hermetic_support as _hs  # noqa: E402

# (b) Sandbox DISPATCH_STATE_DIR — TYLKO jesli nie ustawiony z zewnatrz.
if not os.environ.get("DISPATCH_STATE_DIR"):
    _sandbox = _hs.make_sandbox_state_dir()
    os.environ["DISPATCH_STATE_DIR"] = _sandbox
    atexit.register(shutil.rmtree, _sandbox, True)  # ignore_errors=True (pozycyjnie)

# (b2) SITECUSTOMIZE dla SUBPROCESOW (ACK Adrian 10.07 — domkniecie luki #1 ZP207):
# in-process guard NIE dziedziczy sie na dzieci (script-runnery, subprocess.run w testach).
# Katalog z wygenerowanym sitecustomize.py na POCZATKU PYTHONPATH sesji → kazdy
# python-child importuje go na starcie i instaluje TEN SAM guard (hermetic_support).
# FAIL-OPEN, ale GLOSNO: przy bledzie dziecko startuje bez guarda (jak dotad —
# swiadoma decyzja ACK Adrian 10.07), lecz pisze marker na stderr zamiast milczec.
# Cichy fail-open byl root enablerem 4-nocnej slepoty (15.07: guard padl po cichu,
# sonda wyciekla, nikt nie wiedzial). Aktywny WYLACZNIE pod DISPATCH_UNDER_PYTEST=1;
# opt-out per-run: HERMETIC_SUBPROCESS_GUARD=0. Produkcja NIETKNIETA (env+katalog
# tylko w obrebie sesji pytest). Marker greppowalny: HERMETIC_SUBPROC_GUARD_INSTALL_FAILED.
_SITE_TMPL = (
    "# auto-generated (dispatch_v2/conftest.py, Z-P2-07): FS-guard w subprocesach pytest.\n"
    "import os as _os\n"
    "import sys as _sys\n"
    "if (_os.environ.get('DISPATCH_UNDER_PYTEST') == '1'\n"
    "        and _os.environ.get('HERMETIC_SUBPROCESS_GUARD', '1') == '1'):\n"
    "    try:\n"
    "        _sr = _os.environ.get('ZIOMEK_SCRIPTS_ROOT',\n"
    "                              '/root/.openclaw/workspace/scripts')\n"
    "        if _sr not in _sys.path:\n"
    "            _sys.path.insert(0, _sr)\n"
    "        from dispatch_v2.tests.hermetic_support import install_guard_subprocess\n"
    "        install_guard_subprocess()\n"
    "    except Exception as _exc:\n"
    "        _sys.stderr.write('HERMETIC_SUBPROC_GUARD_INSTALL_FAILED '\n"
    "                          '(fail-open, dziecko startuje bez guarda): '\n"
    "                          + repr(_exc) + '\\n')\n"
)
import tempfile  # noqa: E402

_site_dir = tempfile.mkdtemp(prefix="hermetic_site_")
with open(os.path.join(_site_dir, "sitecustomize.py"), "w", encoding="utf-8") as _f:
    _f.write(_SITE_TMPL)
atexit.register(shutil.rmtree, _site_dir, True)
_pp = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
_parts = [_site_dir] + _pp
if _SCRIPTS_ROOT not in _parts:
    # dzieci musza widziec pakiet dispatch_v2 (ScriptRunItem robi setdefault na
    # PYTHONPATH, ktory przy juz-ustawionym env nie zadziala — dokladamy korzen sami).
    _parts.append(_SCRIPTS_ROOT)
os.environ["PYTHONPATH"] = os.pathsep.join(_parts)


@pytest.fixture(scope="session", autouse=True)
def _hermetic_write_guard():
    """(c) Instaluj write/read-guard na prymitywach FS na cala sesje; cofnij na koncu.

    Kolekcja jest read-only (per A4), wiec guard w fazie testow wystarcza. Session-scope
    przez wlasny `pytest.MonkeyPatch()` (undo na teardown) = standardowy wzorzec dla
    monkeypatchowania sesyjnego."""
    mp = pytest.MonkeyPatch()
    _hs.install_guard(mp)
    try:
        yield
    finally:
        mp.undo()


def pytest_collection_modifyitems(config, items):
    """(d) Kwarantanna po nodeid (stem pliku). DEFAULT: tylko marker `nonhermetic`
    (juz zarejestrowany w tests/conftest — REUZYWAM, nie dubluje). Bez -m nie wyklucza →
    baseline bez zmian. STRICT: dodatkowo skip z powodem (dowod hermetycznosci)."""
    entries = _hs.load_quarantine()
    if not entries:
        return
    mode = "strict" if _hs.strict_enabled() else "default"
    for item in items:
        try:
            stem = item.path.stem
        except Exception:
            stem = ""
        nodeid = str(getattr(item, "nodeid", ""))
        for e in entries:
            m = e["match"]
            if m == stem or m in nodeid:
                # Marker `nonhermetic` w KAZDYM trybie (dokumentuje status testu).
                item.add_marker(pytest.mark.nonhermetic)
                # SKIP tylko gdy biezacy tryb jest na liscie `modes` wpisu:
                #   ["strict"]            = live read-only (biega w DEFAULT, skip w STRICT),
                #   ["default","strict"]  = ZNALEZIONY prod-writer (skip w obu — inaczej fail).
                if mode in (e.get("modes") or ["strict"]):
                    item.add_marker(
                        pytest.mark.skip(
                            reason="HERMETIC-QUARANTINE: " + e.get("reason", "nonhermetic")
                        )
                    )
                break
