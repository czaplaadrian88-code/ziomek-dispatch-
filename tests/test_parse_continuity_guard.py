"""PARSE-01 — parse_continuity_guard tests.

Izolacja jak w repo:
  • flags.json: każdy test, który dotyka zapisu PARSER_DEGRADED, używa
    tymczasowego flags.json (monkeypatch FLAGS_PATH + common.FLAGS_PATH) — zero
    dotyku live pliku.
  • Telegram: send_admin_alert zmockowane (autouse) — zero realnych wysyłek.
  • Baseline cycles: podajemy syntetyczny deque przez arg cycles= (NIE singleton),
    więc nie ruszamy parser_health._instance.

Pokrycie:
  happy:    parse OK (active>0, brak spadku) => no-trip
  edge:     cold-start (za mało historii) => no freeze nawet gdy active=0
  core:     blackout prev>=min_prev -> 0, shadow OFF => log-only (freeze_new False)
  core:     blackout, flaga ON, confirmed po N cyklach => freeze_new True + PARSER_DEGRADED=true
  recovery: po freeze parse wraca => PARSER_DEGRADED wyczyszczone
  edge:     drop >= PARSE_DROP_PCT (n_active>0) => suspicious
  edge:     low-volume 3 -> 0 (median < min_prev) => NIE suspicious
  regresja: evaluate NIGDY nie rzuca (śmieciowy input) => no-trip
  regresja: active = order_ids - closed_ids (terminalne wykluczone)
"""
import json
import sys
import tempfile
from collections import deque
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import parse_continuity_guard as pcg  # noqa: E402


@pytest.fixture(autouse=True)
def _block_real_telegram(monkeypatch):
    from dispatch_v2 import telegram_utils
    monkeypatch.setattr(telegram_utils, "send_admin_alert", lambda text: True)


@pytest.fixture(autouse=True)
def _reset_guard_state():
    pcg.reset_for_test()
    yield
    pcg.reset_for_test()


def _flags_file(tmpdir, **overrides):
    """Tworzy tymczasowy flags.json."""
    data = {
        "PARSE_CONTINUITY_GUARD_ENABLED": False,
        "PARSE_BLACKOUT_MIN_PREV": 5,
        "PARSE_DROP_PCT": 70,
        "PARSE_GUARD_CONFIRM_CYCLES": 2,
        "PARSER_DEGRADED": False,
    }
    data.update(overrides)
    p = Path(tmpdir) / "flags.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _patch_flags(monkeypatch, path, allow_write=True):
    """Wpina tmp flags.json do pcg (zapis) i common (odczyt hot-reload).

    allow_write=True → ALLOW_FLAGS_WRITE_IN_TEST=1 (opt-out L1, lekcja #180):
    te testy jawnie testują zapis do SPATCHOWANEGO tmp flags.json, więc odmowa
    L1 (PYTEST_CURRENT_TEST) jest tu wyłączana świadomie.
    """
    monkeypatch.setattr(pcg, "FLAGS_PATH", str(path))
    from dispatch_v2 import common
    monkeypatch.setattr(common, "FLAGS_PATH", path)
    common._flags_cache = None
    common._flags_mtime = 0
    if allow_write:
        monkeypatch.setenv("ALLOW_FLAGS_WRITE_IN_TEST", "1")
    else:
        monkeypatch.delenv("ALLOW_FLAGS_WRITE_IN_TEST", raising=False)


def _cycles(active_values):
    return deque({"active_orders": v, "orders_in_panel": v} for v in active_values)


def _read_degraded(path):
    return json.loads(Path(path).read_text(encoding="utf-8")).get("PARSER_DEGRADED")


def test_happy_no_trip(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([10, 11, 12, 12, 11])
        r = pcg.evaluate([str(i) for i in range(11)], closed_ids=[], cycles=cy)
        assert r["suspicious"] is False
        assert r["freeze_new"] is False
        assert _read_degraded(fp) is False


def test_cold_start_builds_baseline_no_freeze(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=True)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([8])  # tylko 1 historyczny cykl < confirm_cycles(2)
        r = pcg.evaluate([], closed_ids=[], cycles=cy)
        assert r["cold_start"] is True
        assert r["freeze_new"] is False
        assert _read_degraded(fp) is False


def test_blackout_shadow_logonly_when_flag_off(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=False)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([10, 11, 12, 12, 11])
        r1 = pcg.evaluate([], closed_ids=[], cycles=cy)
        r2 = pcg.evaluate([], closed_ids=[], cycles=cy)
        assert r1["suspicious"] is True
        assert r2["suspicious"] is True
        assert r2["shadow"] is True
        assert r2["freeze_new"] is False
        assert _read_degraded(fp) is False


def test_blackout_confirmed_freezes_and_sets_degraded(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=True,
                         PARSE_GUARD_CONFIRM_CYCLES=2)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([20, 21, 22, 22, 21])
        r1 = pcg.evaluate([], closed_ids=[], cycles=cy)
        assert r1["suspicious"] is True
        assert r1["confirmed"] is False  # 1/2 — jeszcze nie
        assert r1["freeze_new"] is False
        r2 = pcg.evaluate([], closed_ids=[], cycles=cy)
        assert r2["confirmed"] is True   # 2/2
        assert r2["freeze_new"] is True
        assert _read_degraded(fp) is True


def test_recovery_clears_degraded(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=True,
                         PARSE_GUARD_CONFIRM_CYCLES=2)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([20, 21, 22, 22, 21])
        pcg.evaluate([], closed_ids=[], cycles=cy)
        pcg.evaluate([], closed_ids=[], cycles=cy)
        assert _read_degraded(fp) is True
        # parse wraca: 18 aktywnych vs median ~21 => spadek ~14% < 70% => not suspicious
        good = [str(i) for i in range(18)]
        r = pcg.evaluate(good, closed_ids=[], cycles=cy)
        assert r["suspicious"] is False
        assert _read_degraded(fp) is False


def test_drop_pct_branch(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_DROP_PCT=70)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([20, 20, 20, 20, 20])
        # 3 aktywne vs median 20 => -85% >= 70%
        r = pcg.evaluate(["1", "2", "3"], closed_ids=[], cycles=cy)
        assert r["suspicious"] is True
        assert "DROP" in r["reason"]


def test_low_volume_zero_not_suspicious(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_BLACKOUT_MIN_PREV=5, PARSE_GUARD_CONFIRM_CYCLES=2)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([3, 2, 3])  # median 3 < min_prev 5
        r = pcg.evaluate([], closed_ids=[], cycles=cy)
        assert r["cold_start"] is False
        assert r["suspicious"] is False
        assert r["freeze_new"] is False


def test_garbage_input_never_raises(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=True)
        _patch_flags(monkeypatch, fp)
        r = pcg.evaluate(None, closed_ids=None, cycles=None)
        assert r["freeze_new"] is False
        r2 = pcg.evaluate(12345, closed_ids=object(), cycles="nonsense")
        assert r2["freeze_new"] is False


def test_active_excludes_closed_ids(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([10, 10, 10])
        order = [str(i) for i in range(10)]
        closed = [str(i) for i in range(10)]  # wszystkie terminalne => active=0
        r = pcg.evaluate(order, closed_ids=closed, cycles=cy)
        assert r["n_active"] == 0
        assert r["suspicious"] is True  # 10->0 active, median 10 >= min_prev 5


# ── ETAP 1 PARSE-01 (lekcja #180): lifecycle persystentny + L1 pytest-guard ──

def _read_key(path, key):
    return json.loads(Path(path).read_text(encoding="utf-8")).get(key)


def test_set_writes_lifecycle_keys_and_clear_removes_them(monkeypatch):
    """Set → PARSER_DEGRADED_SET_BY='parse01' + SET_TS w flags.json; clear usuwa oba."""
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=True,
                         PARSE_GUARD_CONFIRM_CYCLES=2)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([20, 21, 22, 22, 21])
        pcg.evaluate([], closed_ids=[], cycles=cy)
        pcg.evaluate([], closed_ids=[], cycles=cy)
        assert _read_degraded(fp) is True
        assert _read_key(fp, pcg.SET_BY_KEY) == pcg.GUARD_SET_BY
        assert _read_key(fp, pcg.SET_TS_KEY)  # ISO ts obecny
        # recovery
        good = [str(i) for i in range(18)]
        pcg.evaluate(good, closed_ids=[], cycles=cy)
        assert _read_degraded(fp) is False
        assert _read_key(fp, pcg.SET_BY_KEY) is None
        assert _read_key(fp, pcg.SET_TS_KEY) is None


def test_cross_process_set_then_clear(monkeypatch):
    """Set w 'procesie A', clear w świeżym 'procesie B' (reset_for_test symuluje
    restart — lifecycle MUSI być w pliku, nie w pamięci; sedno incydentu #180)."""
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=True,
                         PARSE_GUARD_CONFIRM_CYCLES=2)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([20, 21, 22, 22, 21])
        # proces A: set
        pcg.evaluate([], closed_ids=[], cycles=cy)
        pcg.evaluate([], closed_ids=[], cycles=cy)
        assert _read_degraded(fp) is True
        # proces B: świeży stan modułu (restart / inny proces)
        pcg.reset_for_test()
        good = [str(i) for i in range(18)]
        r = pcg.evaluate(good, closed_ids=[], cycles=cy)
        assert r["suspicious"] is False
        assert _read_degraded(fp) is False, (
            "recovery musi czyścić po SET_BY w pliku, nie po zmiennej in-memory"
        )


def test_manual_or_foreign_set_not_cleared_on_recovery(monkeypatch):
    """PARSER_DEGRADED=true ustawione ręcznie/przez inny mechanizm (brak SET_BY
    lub SET_BY != 'parse01') NIE jest czyszczone przez recovery guardu."""
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=True,
                         PARSER_DEGRADED=True)
        _patch_flags(monkeypatch, fp)
        cy = _cycles([20, 21, 22, 22, 21])
        good = [str(i) for i in range(20)]
        r = pcg.evaluate(good, closed_ids=[], cycles=cy)
        assert r["suspicious"] is False
        assert _read_degraded(fp) is True  # nie ruszone (brak SET_BY)


def test_l1_refuses_flags_write_under_pytest(monkeypatch):
    """L1 (lekcja #180/#75): writer flags.json odmawia zapisu gdy
    PYTEST_CURRENT_TEST w env i brak opt-outu ALLOW_FLAGS_WRITE_IN_TEST=1."""
    with tempfile.TemporaryDirectory() as td:
        fp = _flags_file(td, PARSE_CONTINUITY_GUARD_ENABLED=True,
                         PARSE_GUARD_CONFIRM_CYCLES=2)
        _patch_flags(monkeypatch, fp, allow_write=False)
        assert "PYTEST_CURRENT_TEST" in __import__("os").environ
        # bezpośrednio writer:
        assert pcg._set_parser_degraded(True) is False
        assert _read_degraded(fp) is False
        # i przez pełny flow confirmed (zapis zablokowany, freeze dalej działa):
        cy = _cycles([20, 21, 22, 22, 21])
        pcg.evaluate([], closed_ids=[], cycles=cy)
        r = pcg.evaluate([], closed_ids=[], cycles=cy)
        assert r["freeze_new"] is True
        assert _read_degraded(fp) is False  # plik NIE dotknięty z testu
