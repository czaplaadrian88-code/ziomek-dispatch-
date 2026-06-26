"""B (ingestia Ziomka) — detektor ulica↔miasto + shadow-log. Bliźniak panelowego check_street_town.

Monkeypatch rozkładu z geocode_cache → deterministyczne (niezależne od żywych danych).
"""
from __future__ import annotations

import json

from dispatch_v2 import address_mismatch as AM

_FAKE = {
    "armii krajowej": {"bialystok": 42, "olmonty": 1},
    "kraszewskiego": {"bialystok": 57},
    "wiejska": {"choroszcz": 6},
    "lesna": {"bialystok": 18, "olmonty": 3},
}


def _patch(monkeypatch):
    monkeypatch.setattr(AM, "_street_town_counts", lambda: _FAKE)


def test_bialystok_street_in_other_town_warns(monkeypatch):
    _patch(monkeypatch)
    w = AM.check_street_town("Armii Krajowej 15", "Olmonty")
    assert w is not None
    assert w["suggest_town"] == "Białystok"
    assert w["street_bialystok_count"] == 42
    assert w["town"] == "Olmonty"


def test_same_street_in_bialystok_ok(monkeypatch):
    _patch(monkeypatch)
    assert AM.check_street_town("Armii Krajowej 15", "Białystok") is None
    assert AM.check_street_town("Armii Krajowej 15", "bialystok") is None


def test_genuine_other_town_no_warning(monkeypatch):
    _patch(monkeypatch)
    assert AM.check_street_town("Wiejska 3", "Choroszcz") is None


def test_unknown_street_no_warning(monkeypatch):
    _patch(monkeypatch)
    assert AM.check_street_town("Jakaś Nieznana 9", "Olmonty") is None


def test_missing_inputs_no_warning(monkeypatch):
    _patch(monkeypatch)
    assert AM.check_street_town("Armii Krajowej 15", "") is None
    assert AM.check_street_town("", "Olmonty") is None
    assert AM.check_street_town(None, None) is None


def test_here_count_above_threshold_blocks(monkeypatch):
    _patch(monkeypatch)
    # Leśna: białostocka, ale w Olmontach 3× (>MAX_HERE) → realny obszar, brak alarmu
    assert AM.check_street_town("Leśna 5", "Olmonty") is None


def test_maybe_log_writes_jsonl_on_mismatch(monkeypatch, tmp_path):
    _patch(monkeypatch)
    log = tmp_path / "mismatch.jsonl"
    monkeypatch.setattr(AM, "_SHADOW_LOG", log)
    w = AM.maybe_log_mismatch("483504", "Armii Krajowej 15", "Olmonty")
    assert w is not None
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["order_id"] == "483504"
    assert rec["town"] == "Olmonty"
    assert rec["street_bialystok_count"] == 42


def test_maybe_log_no_write_when_ok(monkeypatch, tmp_path):
    _patch(monkeypatch)
    log = tmp_path / "mismatch.jsonl"
    monkeypatch.setattr(AM, "_SHADOW_LOG", log)
    assert AM.maybe_log_mismatch("1", "Armii Krajowej 15", "Białystok") is None
    assert not log.exists()


def test_street_key_normalization():
    assert AM._street_name_key("Armii Krajowej 15") == AM._street_name_key("ul. Armii Krajowej 2/8")
    assert AM._street_name_key("Białystok Armii Krajowej 3") == AM._street_name_key("Armii Krajowej 9")
