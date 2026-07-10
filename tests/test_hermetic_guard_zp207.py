"""Testy kontrolne Z-P2-07 (dowod DoD hermetyzacji suity).

Deterministyczne — MUSZA przechodzic w DEFAULT i STRICT (zero zaleznosci od trybu
sesji). Sekcja PURE (1-4) testuje czysty klasyfikator. Sekcja INTEGRACYJNA (5-7)
korzysta z sesyjnego guarda (autouse) i realnych prod-writerow. Zywe pliki tylko
STAT-owane (os.stat niepatchowany) — zaden zapis do produkcji.
"""
import os
from pathlib import Path

import pytest

from dispatch_v2.tests import hermetic_support as hs

LIVE_STATE = "/root/.openclaw/workspace/dispatch_state"
LIVE_PLANS = LIVE_STATE + "/courier_plans.json"
LIVE_ALLOC = LIVE_STATE + "/global_alloc.json"
LIVE_LOG = "/root/.openclaw/workspace/scripts/logs/probe.log"
LIVE_FLAGS = "/root/.openclaw/workspace/scripts/flags.json"


def _mtime(path):
    """mtime_ns zywego pliku albo None gdy brak. os.stat NIE jest patchowany."""
    try:
        return os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return None


# ── (1-4) CZYSTY klasyfikator (mode-niezalezny) ─────────────────────────────
def test_classify_blocks_write_to_live_state():
    r = hs.resolve_target(LIVE_PLANS)
    assert hs.classify(r, is_write=True, strict=False) == hs.BLOCK_WRITE
    assert hs.classify(r, is_write=True, strict=True) == hs.BLOCK_WRITE


def test_classify_blocks_write_to_live_logs_and_flags():
    assert hs.classify(hs.resolve_target(LIVE_LOG), True, False) == hs.BLOCK_WRITE
    assert hs.classify(hs.resolve_target(LIVE_FLAGS), True, False) == hs.BLOCK_WRITE


def test_classify_allows_write_to_tmp(tmp_path):
    r = hs.resolve_target(str(tmp_path / "out.json"))
    assert hs.classify(r, True, False) == hs.ALLOW
    assert hs.classify(r, True, True) == hs.ALLOW


def test_classify_strict_read_block_only_dispatch_state():
    rs = hs.resolve_target(LIVE_PLANS)   # dispatch_state
    rl = hs.resolve_target(LIVE_LOG)     # scripts/logs
    # DEFAULT: odczyt nieblokowany nigdzie
    assert hs.classify(rs, is_write=False, strict=False) == hs.ALLOW
    # STRICT: odczyt dispatch_state BLOK; logs NIE (scope DoD = tylko dispatch_state)
    assert hs.classify(rs, is_write=False, strict=True) == hs.BLOCK_READ
    assert hs.classify(rl, is_write=False, strict=True) == hs.ALLOW


# ── (5) NEGATYW: realny prod-writer wycelowany w ZYWY korzen → guard RAISE ──
def test_negative_prod_writer_raises_and_leaves_live_untouched(monkeypatch):
    """Realny writer (plan_manager.save_plan) skierowany na PROBE-sciezke POD zywym
    dispatch_state → guard RAISE, plik NIE powstaje.

    Celowo NIE mierzymy mtime zywego courier_plans.json: produkcja (dispatch-shadow)
    pisze go rownolegle → flaky. Zamiast tego wlasna sciezka-sonda, ktorej produkcja
    NIGDY nie uzywa: deterministyczny dowod (RAISE + brak pliku), niezalezny od stanu
    produkcji i ewentualnych wyciekow monkeypatcha PLANS_FILE z innych testow."""
    import dispatch_v2.plan_manager as pm

    probe_plans = Path(LIVE_STATE) / "hermetic_probe_zp207_plans.json"
    probe_lock = Path(LIVE_STATE) / "hermetic_probe_zp207_plans.lock"
    assert not probe_plans.exists() and not probe_lock.exists(), "sonda juz istnieje?!"
    # Jawnie celujemy w ZYWY korzen (jak hardcode produkcji), przez stala modulu (C17):
    monkeypatch.setattr(pm, "PLANS_FILE", probe_plans)
    monkeypatch.setattr(pm, "LOCK_FILE", probe_lock)
    body = {
        "start_pos": {"lat": 53.13, "lng": 23.16, "source": "test"},
        "start_ts": "2026-07-10T10:00:00+00:00",
        "stops": [],
        "optimization_method": "greedy",
    }
    # Guard bije na LOCK_FILE.touch → os.open(O_CREAT) pod zywym korzeniem.
    with pytest.raises(RuntimeError, match="HERMETIC-GUARD"):
        pm.save_plan("hermetic_probe_cid", body)
    assert not probe_plans.exists(), "guard nie zapobiegl utworzeniu pliku pod zywym dispatch_state"
    assert not probe_lock.exists(), "guard nie zapobiegl utworzeniu locka pod zywym dispatch_state"


# ── (6) POZYTYW: ten sam writer z izolacja → laduje w tmp, zywy NIETKNIETY ──
def test_positive_prod_writer_lands_in_tmp(tmp_path, monkeypatch):
    import dispatch_v2.plan_manager as pm

    before = _mtime(LIVE_PLANS)
    # Izolacja jak w produkcji: przez STALA modulu (nie default-arg — C17). save_plan
    # czyta PLANS_FILE/LOCK_FILE jako globale modulu przy kazdym wywolaniu.
    monkeypatch.setattr(pm, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(pm, "LOCK_FILE", tmp_path / "courier_plans.lock")
    body = {
        "start_pos": {"lat": 53.13, "lng": 23.16, "source": "test"},
        "start_ts": "2026-07-10T10:00:00+00:00",
        "stops": [],
        "optimization_method": "greedy",
    }
    saved = pm.save_plan("hermetic_probe_cid", body)
    assert (tmp_path / "courier_plans.json").exists(), "plan nie wyladowal w tmp"
    assert saved["plan_version"] >= 1
    assert _mtime(LIVE_PLANS) == before, "zywy courier_plans.json DOTKNIETY przy izolacji"


# ── (7) fail-soft writer: guard blokuje mimo except → zwraca 0, zywy NIETKNIETY ──
def test_failsoft_writer_blocked_returns_zero():
    import dispatch_v2.global_alloc_store as gas
    from datetime import datetime, timezone

    before = _mtime(LIVE_ALLOC)
    # Domyslna (ZYWA) sciezka. gas.write ma `except Exception: return 0` — guard bije
    # wewnatrz (mkstemp/os.replace pod zywym korzeniem), wyjatek polkniety → 0.
    n = gas.write({"999001": {"verdict": "TEST"}}, datetime.now(timezone.utc))
    assert n == 0, "fail-soft writer powinien zwrocic 0 (guard zablokowal zapis)"
    assert _mtime(LIVE_ALLOC) == before, "zywy global_alloc.json DOTKNIETY mimo guarda"


# ── (7b) DELETE-guard: unlink/remove pod zywym korzeniem → RAISE, tmp → OK ──
def test_unlink_guard_blocks_live_allows_tmp(tmp_path):
    """Klasa DELETE: os.unlink / os.remove / Path.unlink pod zywym dispatch_state →
    RAISE (guard bije PRZED skasowaniem, niezaleznie czy plik istnieje); w tmp dziala."""
    probe = Path(LIVE_STATE) / "hermetic_probe_zp207_unlink.json"
    # NEGATYW: 3 warianty kasowania zywej sciezki → HERMETIC-GUARD RuntimeError
    with pytest.raises(RuntimeError, match="HERMETIC-GUARD"):
        os.unlink(probe)
    with pytest.raises(RuntimeError, match="HERMETIC-GUARD"):
        os.remove(probe)
    with pytest.raises(RuntimeError, match="HERMETIC-GUARD"):
        Path(probe).unlink()  # Path.unlink → os.unlink
    assert not probe.exists(), "sonda NIE moze istniec (guard blokuje kasowanie)"
    # POZYTYW: kasowanie w tmp dziala normalnie
    f = tmp_path / "x.json"
    f.write_text("{}", encoding="utf-8")
    os.unlink(f)
    assert not f.exists()


# ── (8) kwarantanna + marker ────────────────────────────────────────────────
def test_quarantine_loader_parses_expected_entries():
    stems = {e["match"] for e in hs.load_quarantine()}
    assert "test_v325_pin_leak_defense" in stems
    assert "test_route_order_live_parity" in stems
    for e in hs.load_quarantine():
        assert e.get("reason"), f"kazdy wpis kwarantanny musi miec powod: {e}"


def test_nonhermetic_marker_registered():
    """Marker `nonhermetic` zarejestrowany w tests/conftest (reuzywany, nie dublowany)."""
    import inspect

    from dispatch_v2.tests import conftest as ct

    src = inspect.getsource(ct.pytest_configure)
    assert "nonhermetic" in src, "marker nonhermetic musi byc zarejestrowany w tests/conftest"
