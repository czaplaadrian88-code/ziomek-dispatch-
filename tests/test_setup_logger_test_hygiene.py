"""Test-hygiene log-guard (2026-07-03): FileHandler setup_logger milczy pod pytestem.

Kontekst: ~34 moduły silnika robią module-level `setup_logger(..., PROD path)`.
Przed guardem KAŻDY proces importujący moduł (pytest in-process + script-runner)
pisał do żywych logów PROD — testowe FLAG_FINGERPRINT z conftest-owo odartym
flags.json (defaulty) lądowały w logs/czasowka.log i okłamywały log-based
instrumenty (fałszywy INTERMITTENT-COLD 22-40% w flag_fingerprint_check,
eskalacja P1 z 02.07 — REFUTED korelacją z journalem: 334/334 ticków warm).

Guard: common._file_log_blocked_under_test() + _ProdFileLogTestFilter na
FileHandlerze (per-rekord — pytest ustawia PYTEST_CURRENT_TEST dopiero w fazie
testu, po imporcie). Markery: PYTEST_CURRENT_TEST / DISPATCH_UNDER_PYTEST
(conftest, cała sesja). Opt-out: ALLOW_FILE_LOG_IN_TEST=1.
"""
import os
import subprocess
import sys

from dispatch_v2 import common as C

_PROD_LOGS = "/root/.openclaw/workspace/scripts/logs"


def _fresh_logger(name, path):
    # unikalna nazwa per test — setup_logger cache'uje po logger.handlers
    return C.setup_logger(name, str(path))


def test_file_log_blocked_under_pytest(tmp_path):
    p = tmp_path / "hyg_blocked.log"
    assert not str(p).startswith(_PROD_LOGS)  # anty-PROD (mina sesji 02-03.07)
    log = _fresh_logger("hyg_test_blocked", p)
    log.info("nie-powinno-trafic-do-pliku")
    # delay=True + filtr → plik nawet nie powstaje
    assert not p.exists() or p.read_text() == ""


def test_opt_out_allows_file_log(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_FILE_LOG_IN_TEST", "1")
    p = tmp_path / "hyg_optout.log"
    assert not str(p).startswith(_PROD_LOGS)
    log = _fresh_logger("hyg_test_optout", p)
    log.info("ma-trafic")
    assert p.exists() and "ma-trafic" in p.read_text()


def test_prod_without_markers_writes(tmp_path):
    """Symulacja PROD: świeży interpreter BEZ markerów testowych pisze do pliku
    (zachowanie produkcyjne NIETKNIĘTE — serwisy logują jak dotąd)."""
    p = tmp_path / "prod_sim.log"
    assert not str(p).startswith(_PROD_LOGS)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTEST_CURRENT_TEST", "DISPATCH_UNDER_PYTEST",
                        "ALLOW_FILE_LOG_IN_TEST")}
    env.setdefault("PYTHONPATH", "/root/.openclaw/workspace/scripts")
    code = (
        "from dispatch_v2 import common as C; "
        f"log = C.setup_logger('hyg_prod_sim', {str(p)!r}); "
        "log.info('prod-pisze')"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert p.exists() and "prod-pisze" in p.read_text()


def test_session_marker_set_and_inheritable():
    """conftest ustawia DISPATCH_UNDER_PYTEST=1 na sesję; ScriptRunItem robi
    dict(os.environ) → subprocesy script-runner dziedziczą marker."""
    assert os.environ.get("DISPATCH_UNDER_PYTEST") == "1"


def test_blocked_helper_precedence(monkeypatch):
    """Opt-out wygrywa z markerami (test może jawnie badać file-log)."""
    monkeypatch.setenv("DISPATCH_UNDER_PYTEST", "1")
    monkeypatch.delenv("ALLOW_FILE_LOG_IN_TEST", raising=False)
    assert C._file_log_blocked_under_test() is True
    monkeypatch.setenv("ALLOW_FILE_LOG_IN_TEST", "1")
    assert C._file_log_blocked_under_test() is False
