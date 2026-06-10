"""Exclude-by-cid fix (2026-06-10) — Opcja A + zalążek B.

Root cause: PANEL-CANON fix (commit bb9bc27, 06-10) sprawił, że courier_resolver
nadaje flocie cs.name = pełne imię z grafiku ('Mateusz Ostapczuk'), a manual
override trzyma skrót panelowy ('Mateusz O'). Egzekucja wykluczenia robiła czysty
match nazw (cs.name in excluded) → blokada gubiona → kurier wpadał do propozycji.

Fix:
- Opcja A: dispatchable_fleet egzekwuje wykluczenie PO CID (get_excluded_cids
  mapuje dowolną formę nazwy → cid). Naprawia LIVE bez ponownego /stop.
- zalążek B: /stop zapisuje cid jawnie (excluded_cids); include/reset/daily-reset
  go czyszczą.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2")

from dispatch_v2 import manual_overrides as mo  # noqa: E402
from dispatch_v2 import courier_resolver as cr  # noqa: E402


# ---------- fixtures: izolowane pliki nazw + override ----------

@pytest.fixture
def names_setup(tmp_path, monkeypatch):
    """Symuluje realny desync: kurier_ids ma OBIE formy, courier_names ma skrót,
    grafik_full_names ma pełne imię. cid 413 = Mateusz O = Mateusz Ostapczuk."""
    kurier_ids = tmp_path / "kurier_ids.json"
    courier_names = tmp_path / "courier_names.json"
    grafik = tmp_path / "grafik_full_names.json"
    overrides = tmp_path / "manual_overrides.json"
    kurier_ids.write_text(json.dumps({
        "Mateusz O": 413, "Mateusz Ostapczuk": 413,
        "Bartek O": 123, "Bartek Olszewski": 123,
    }), encoding="utf-8")
    courier_names.write_text(json.dumps({
        "413": "Mateusz O", "123": "Bartek O",
    }), encoding="utf-8")
    grafik.write_text(json.dumps({
        "Mateusz Ostapczuk": 413, "Bartek Olszewski": 123,
    }), encoding="utf-8")
    monkeypatch.setattr(mo, "KURIER_IDS_PATH", str(kurier_ids))
    monkeypatch.setattr(mo, "COURIER_NAMES_PATH", str(courier_names))
    monkeypatch.setattr(mo, "GRAFIK_FULL_NAMES_PATH", str(grafik))
    monkeypatch.setattr(mo, "OVERRIDES_PATH", str(overrides))
    return overrides


# ---------- Opcja A: name→cid mapping ----------

def test_all_name_to_cid_both_forms(names_setup):
    m = mo._all_name_to_cid()
    assert m["Mateusz O"] == 413
    assert m["Mateusz Ostapczuk"] == 413


def test_legacy_name_only_excluded_resolves_to_cid(names_setup):
    """LIVE case: plik ma TYLKO skrót (zapisany przed fixem). get_excluded_cids
    i tak zwraca cid → blokada działa bez ponownego /stop."""
    mo.save({"excluded": ["Mateusz O"], "excluded_cids": [], "working": {}})
    assert mo.get_excluded_cids() == {"413"}


def test_full_name_excluded_also_resolves(names_setup):
    mo.save({"excluded": ["Mateusz Ostapczuk"], "excluded_cids": [], "working": {}})
    assert "413" in mo.get_excluded_cids()


# ---------- zalążek B: /stop zapisuje cid ----------

def test_do_exclude_writes_cid(names_setup):
    action, _resp = mo.parse_command("/stop Mateusz")
    assert action == "exclude"
    d = mo.load()
    assert "413" in [str(c) for c in d["excluded_cids"]]
    assert mo.get_excluded_cids() == {"413"}


def test_include_clears_cid_and_all_name_forms(names_setup):
    # wyklucz skrótem
    mo.parse_command("/stop Mateusz")
    assert mo.get_excluded_cids() == {"413"}
    # przywróć pełnym imieniem → musi zdjąć też skrót + cid
    action, _resp = mo.parse_command("/pracuje Mateusz Ostapczuk")
    assert action == "include"
    d = mo.load()
    assert d["excluded_cids"] == []
    assert "Mateusz O" not in d["excluded"]
    assert mo.get_excluded_cids() == set()


def test_reset_clears_excluded_cids(names_setup):
    mo.parse_command("/stop Mateusz")
    assert mo.get_excluded_cids() == {"413"}
    action, _resp = mo.parse_command("reset")
    assert action == "reset"
    assert mo.load()["excluded_cids"] == []
    assert mo.get_excluded_cids() == set()


def test_load_backfills_excluded_cids_key(names_setup):
    # stary plik bez klucza excluded_cids
    names_setup.write_text(json.dumps({"excluded": ["Mateusz O"]}), encoding="utf-8")
    d = mo.load()
    assert d["excluded_cids"] == []  # setdefault, nie wybucha


# ---------- integracja: dispatchable_fleet egzekwuje po cid ----------

def _mk_fleet():
    pos = (53.132, 23.168)
    return {
        "413": cr.CourierState(courier_id="413", name="Mateusz Ostapczuk",
                               pos=pos, pos_source="gps_fresh"),
        "999": cr.CourierState(courier_id="999", name="Kontrol K",
                               pos=pos, pos_source="gps_fresh"),
    }


@pytest.fixture
def schedule_passthrough(monkeypatch):
    """Pusty grafik → dispatchable_fleet pomija filtr grafiku (if schedule and ...),
    izolując sam gate wykluczenia."""
    import schedule_utils as su
    monkeypatch.setattr(su, "load_schedule", lambda: {})
    monkeypatch.setattr(su, "is_schedule_stale", lambda: False)


def test_dispatchable_fleet_excludes_by_cid_despite_name_mismatch(
        schedule_passthrough, monkeypatch):
    """Sedno: cs.name='Mateusz Ostapczuk', excluded=['Mateusz O'] → mimo różnicy
    nazw kurier 413 wykluczony PO CID."""
    monkeypatch.setattr(mo, "get_excluded", lambda: ["Mateusz O"])
    monkeypatch.setattr(mo, "get_excluded_cids", lambda: {"413"})
    monkeypatch.setattr(mo, "get_working", lambda: {})
    result = cr.dispatchable_fleet(_mk_fleet())
    cids = {c.courier_id for c in result}
    assert "413" not in cids, "Mateusz (413) powinien być wykluczony po cid"
    assert "999" in cids, "kontrolny kurier przechodzi"


def test_dispatchable_fleet_flag_off_reverts_to_name_only(
        schedule_passthrough, monkeypatch):
    """ENABLE_EXCLUDE_BY_CID=OFF → match tylko po nazwie; przy desync nazw
    blokada (skrótem) NIE łapie pełnego imienia floty (zachowanie sprzed fixu)."""
    import dispatch_v2.common as C
    _real_flag = C.flag

    def fake_flag(name, default=False):
        if name == "ENABLE_EXCLUDE_BY_CID":
            return False
        return _real_flag(name, default)

    monkeypatch.setattr(C, "flag", fake_flag)
    monkeypatch.setattr(mo, "get_excluded", lambda: ["Mateusz O"])
    monkeypatch.setattr(mo, "get_excluded_cids", lambda: {"413"})
    monkeypatch.setattr(mo, "get_working", lambda: {})
    result = cr.dispatchable_fleet(_mk_fleet())
    cids = {c.courier_id for c in result}
    # flaga OFF: cid match wyłączony, nazwa 'Mateusz Ostapczuk' != 'Mateusz O'
    assert "413" in cids


def test_dispatchable_fleet_name_match_still_works(
        schedule_passthrough, monkeypatch):
    """Regresja: gdy nazwa floty == wpis excluded, klasyczny match nadal działa."""
    monkeypatch.setattr(mo, "get_excluded", lambda: ["Mateusz Ostapczuk"])
    monkeypatch.setattr(mo, "get_excluded_cids", lambda: set())
    monkeypatch.setattr(mo, "get_working", lambda: {})
    result = cr.dispatchable_fleet(_mk_fleet())
    cids = {c.courier_id for c in result}
    assert "413" not in cids
