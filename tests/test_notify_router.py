"""Testy centrum powiadomień (notify_router) — klasyfikacja + routing + feed + flaga.

Zero realnych sendów: _send_silent ma pytest-guard; feed kierowany do tmp.
"""
import json

import pytest

from dispatch_v2 import notify_router as nr


@pytest.fixture
def tmp_feed(tmp_path, monkeypatch):
    feed = tmp_path / "notify_feed.jsonl"
    monkeypatch.setattr(nr, "FEED_PATH", feed)
    monkeypatch.setattr(nr, "_STATE_DIR", tmp_path)
    # świeży config-cache na czystym defaultcie
    monkeypatch.setattr(nr, "_config_cache", None)
    monkeypatch.setattr(nr, "_config_mtime", 0.0)
    monkeypatch.setattr(nr, "CONFIG_PATH", tmp_path / "nope.json")  # wymuś default
    return feed


def _feed_rows(feed):
    if not feed.exists():
        return []
    return [json.loads(l) for l in feed.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── klasyfikacja ──────────────────────────────────────────────────────────

def test_classify_infra_onfailure_is_high(tmp_feed):
    assert nr.classify("🔴 dispatch-shadow.service OnFailure Result=exit-code") == "high"


def test_classify_business_is_high(tmp_feed):
    assert nr.classify("Nowy kurier do sparowania: Jan K.") == "high"
    assert nr.classify("Problem z płatnością P24 dla zamówienia") == "high"


def test_classify_briefing_is_low(tmp_feed):
    assert nr.classify("Briefing dzienny — podsumowanie 320 zleceń") == "low"
    assert nr.classify("⚠ warning: V3274 divergence 8.7min shadow") == "low"


def test_classify_unknown_defaults_high(tmp_feed):
    assert nr.classify("zupełnie nietypowy komunikat bez słów kluczowych") == "high"


def test_classify_high_wins_over_low(tmp_feed):
    # zawiera i 'warning' (low) i 'awaria' (high) → high wygrywa
    assert nr.classify("warning: awaria krytyczna serwisu") == "high"


def test_source_priority_override(tmp_feed, monkeypatch):
    monkeypatch.setattr(nr, "_config_cache",
                        {**nr._DEFAULT_CONFIG, "source_priority": {"x": "low"}})
    monkeypatch.setattr(nr, "_config_mtime", 9e18)
    # treść wyglada na high (awaria), ale source wymusza low
    assert nr.classify("awaria", source="x") == "low"


# ── routing: flaga OFF (legacy) ───────────────────────────────────────────

def test_route_flag_off_always_proceeds_main(tmp_feed, monkeypatch):
    monkeypatch.setattr("dispatch_v2.common.flag", lambda name, default=False: False)
    assert nr.route("Briefing dzienny", source="daily_briefing") is True  # mimo LOW
    rows = _feed_rows(tmp_feed)
    assert len(rows) == 1
    assert rows[0]["priority"] == "low"
    assert rows[0]["sent_main"] is True       # nie odcięte (flaga off)
    assert rows[0]["sent_silent"] is False


# ── routing: flaga ON ─────────────────────────────────────────────────────

def test_route_flag_on_low_diverted(tmp_feed, monkeypatch):
    monkeypatch.setattr("dispatch_v2.common.flag", lambda name, default=False: True)
    # _send_silent zablokowany pytest-guardem → zwraca False, ale proceed_main=False
    assert nr.route("Briefing dzienny", source="daily_briefing") is False
    rows = _feed_rows(tmp_feed)
    assert rows[0]["priority"] == "low"
    assert rows[0]["sent_main"] is False       # odcięte od głównego bota


def test_route_flag_on_high_stays_main(tmp_feed, monkeypatch):
    monkeypatch.setattr("dispatch_v2.common.flag", lambda name, default=False: True)
    assert nr.route("🔴 OnFailure awaria", source="alert_onfailure", priority="high") is True
    rows = _feed_rows(tmp_feed)
    assert rows[0]["priority"] == "high"
    assert rows[0]["sent_main"] is True         # HIGH zostaje na głównym bocie


def test_route_explicit_priority_respected(tmp_feed, monkeypatch):
    monkeypatch.setattr("dispatch_v2.common.flag", lambda name, default=False: True)
    # treść 'briefing' = low, ale explicit high wygrywa
    assert nr.route("briefing", priority="high") is True
    assert _feed_rows(tmp_feed)[0]["priority"] == "high"
