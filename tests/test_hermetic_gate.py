"""C-HERMETIC-GATE-TESTS (audyt 2026-06-24, spec odporności §6.C): strażnik
hermetyczności bramek. Geneza: podczas A4 złapaliśmy `test_sla_preexisting_bypass`
i `test_parser_health_layer3::test_03` jako FLAKY — przechodziły/padały zależnie od
ŻYWEGO stanu (OSRM traffic-multiplier po porze dnia / zegar Warsaw), nie od kodu.
Zielony taki test nic nie dowodzi.

Ten guard NIE skanuje wszystkich testów (pomiar 2026-06-24: 16 testów na ścieżce
live-OSRM, 15 używa ROBUSTNYCH marginesów i jest stabilne — blanket-mock byłby
przeszacowaniem). Zamiast tego: (1) marker `nonhermetic` jest zarejestrowany
(hermetyczny przebieg = `pytest -m "not nonhermetic"`), (2) DWA naprawione testy
nie mogą po cichu wrócić do zależności od żywego stanu (regression-lock).
"""
import inspect
import re
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent


def _read(name: str) -> str:
    return (_TESTS_DIR / name).read_text(encoding="utf-8")


def test_nonhermetic_marker_registered():
    """conftest.pytest_configure rejestruje marker `nonhermetic` (brak literówki,
    przebieg hermetyczny CI ma czym wykluczać)."""
    src = _read("conftest.py")
    assert "def pytest_configure(" in src, "conftest musi mieć pytest_configure"
    assert re.search(r'addinivalue_line\(\s*["\']markers["\']', src), \
        "pytest_configure musi rejestrować markery przez addinivalue_line"
    assert '"nonhermetic:' in src or "'nonhermetic:" in src, \
        "marker `nonhermetic` musi być zarejestrowany w pytest_configure"


def test_sla_preexisting_mocks_osrm():
    """test_sla_preexisting_bypass MUSI mockować osrm_client.table — inaczej wraca
    do live-OSRM knife-edge (34.8↔38.6) i znów jest flaky."""
    src = _read("test_sla_preexisting_bypass.py")
    assert 'setattr(osrm_client, "table"' in src, \
        "test_sla_preexisting_bypass musi mockować osrm_client.table (deterministyczny OSRM)"
    # breach carry-dominant (picked_up_at oparty o `now`, nie sztywny timestamp na granicy 35)
    assert "picked_up_at=now - timedelta(minutes=40)" in src, \
        "breach 474835 musi być carry-dominant (picked_up_at = now-40), nie OSRM-zależny"


def test_parser_zero_output_freezes_hour_gate():
    """test_03_zero_output_alert MUSI zamrażać bramkę godzinową
    PARSER_HEALTH_STUCK_MIN_HOUR_WARSAW — inaczej pre-09:00 Warsaw alert suppressed
    i test pada zależnie od pory uruchomienia."""
    src = _read("test_parser_health_layer3.py")
    m = re.search(r"def test_03_zero_output_alert\(.*?\n(.*?)\n\s*def ",
                  src, re.DOTALL)
    assert m, "nie znaleziono test_03_zero_output_alert"
    body = m.group(1)
    assert "PARSER_HEALTH_STUCK_MIN_HOUR_WARSAW" in body, \
        "test_03 musi monkeypatchować bramkę godzinową (hermetyzacja zegara)"
