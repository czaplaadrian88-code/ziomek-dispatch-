"""Testy centrum powiadomień (notify_router) — klasyfikacja + routing + feed + flagi.

Zero realnych sendów: _send_silent/_send_repairs mają pytest-guard; feed → tmp.

Od 2026-08-02 dwa wymiary:
- priority (high/low)   — legacy, głośny vs cichy.
- category (technical/critical/info) — który głośny kanał (napraw vs Adrian).
Dwie niezależne flagi: ENABLE_NOTIFY_PRIORITY_ROUTING (LOW→cichy),
ENABLE_NOTIFY_CHANNEL_SPLIT (TECHNICAL→napraw).
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


def _flags(monkeypatch, **vals):
    """Monkeypatch common.flag name-aware. Domyślnie każda flaga OFF; podaj
    override per nazwa. Zastępuje stary wzorzec `lambda name, default: True`
    (który po dodaniu drugiej flagi włączał obie naraz)."""
    def _f(name, default=False):
        return vals.get(name, default)
    monkeypatch.setattr("dispatch_v2.common.flag", _f)


# ── klasyfikacja priority (legacy, bez zmian) ─────────────────────────────

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


# ── klasyfikacja category (NOWY wymiar, 2026-08-02) ───────────────────────

def test_category_low_is_info(tmp_feed):
    assert nr.classify_category("Briefing dzienny — podsumowanie") == "info"
    assert nr.classify_category("⚠ warning: divergence shadow") == "info"


def test_category_technical_by_keyword(tmp_feed):
    assert nr.classify_category("🔴 dispatch-shadow.service OnFailure exit=1") == "technical"
    assert nr.classify_category("OSRM degraded — timeout na routingu") == "technical"
    assert nr.classify_category("backup_sentinel: brak snapshotu 24h") == "technical"


def test_category_critical_by_keyword(tmp_feed):
    assert nr.classify_category("Problem z płatnością P24 zamówienia 900") == "critical"
    assert nr.classify_category("Nowy kurier do sparowania: Jan K.") == "critical"
    assert nr.classify_category("Awaria integracji restauracji Foodage") == "critical"


def test_category_source_override_wins(tmp_feed):
    # source=alert_onfailure → technical, nawet gdyby treść mówiła 'płatność'
    assert nr.classify_category("płatność", source="alert_onfailure") == "technical"
    # backup_sentinel → technical
    assert nr.classify_category("cokolwiek", source="backup_sentinel") == "technical"


def test_category_business_wins_over_technical(tmp_feed):
    # ORACLE kolejności: alert ma i business (restauracja), i technikę (padła)
    # → critical (Adrian MUSI zobaczyć). Odwrócenie kolejności = ten test RED.
    assert nr.classify_category("Restauracja Foodage padła — awaria integracji") == "critical"
    assert nr.classify_category("płatność P24 failed exit=1") == "critical"


def test_category_unknown_high_is_critical_failsafe(tmp_feed):
    # nieznany HIGH (nic nie pasuje) → critical (nigdy na kanał napraw)
    assert nr.classify_category("zupełnie nietypowy komunikat bez słów kluczowych") == "critical"


def test_category_source_override_only_for_high(tmp_feed):
    # gdy priorytet wymuszony LOW, kategoria = info NIEZALEŻNIE od source_category
    assert nr.classify_category("cokolwiek", source="alert_onfailure",
                                priority="low") == "info"


# ── routing: obie flagi OFF (legacy) ──────────────────────────────────────

def test_route_flags_off_always_proceeds_main(tmp_feed, monkeypatch):
    _flags(monkeypatch)  # obie OFF
    monkeypatch.setattr(
        nr, "_send_silent",
        lambda text: pytest.fail("flaga OFF nie może dotknąć cichego transportu"),
    )
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda text: pytest.fail("flaga OFF nie może dotknąć kanału napraw"),
    )
    assert nr.route("Briefing dzienny", source="daily_briefing") is True  # mimo LOW
    rows = _feed_rows(tmp_feed)
    assert len(rows) == 1
    assert rows[0]["priority"] == "low"
    assert rows[0]["category"] == "info"       # kategoria zapisana nawet OFF (shadow)
    assert rows[0]["sent_main"] is True         # nie odcięte (flagi off)
    assert rows[0]["sent_silent"] is False
    assert rows[0]["sent_repairs"] is False


def test_route_split_off_technical_stays_main(tmp_feed, monkeypatch):
    # ON≠OFF: ten sam alert techniczny przy split OFF zostaje na main...
    _flags(monkeypatch)  # obie OFF
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda text: pytest.fail("split OFF nie może dotknąć kanału napraw"),
    )
    assert nr.route("🔴 OnFailure awaria", source="alert_onfailure", priority="high") is True
    rows = _feed_rows(tmp_feed)
    assert rows[0]["category"] == "technical"    # shadow: DOKĄD BY poszło
    assert rows[0]["sent_main"] is True
    assert rows[0]["sent_repairs"] is False


# ── routing: LOW→cichy (ENABLE_NOTIFY_PRIORITY_ROUTING) ───────────────────

def test_route_low_routing_on_low_diverted(tmp_feed, monkeypatch):
    _flags(monkeypatch, ENABLE_NOTIFY_PRIORITY_ROUTING=True)
    monkeypatch.setattr(nr, "_send_silent", lambda text: True)
    assert nr.route("Briefing dzienny", source="daily_briefing") is False
    rows = _feed_rows(tmp_feed)
    assert rows[0]["priority"] == "low"
    assert rows[0]["category"] == "info"
    assert rows[0]["sent_main"] is False       # odcięte od głównego bota
    assert rows[0]["sent_silent"] is True


def test_route_low_routing_on_fails_open_when_silent_fails(tmp_feed, monkeypatch):
    """LOW wolno odciąć od main wyłącznie po potwierdzonym silent delivery."""
    _flags(monkeypatch, ENABLE_NOTIFY_PRIORITY_ROUTING=True)
    monkeypatch.setattr(nr, "_send_silent", lambda text: False)
    assert nr.route("Briefing dzienny", source="daily_briefing") is True
    rows = _feed_rows(tmp_feed)
    assert rows[0]["priority"] == "low"
    assert rows[0]["sent_main"] is True
    assert rows[0]["sent_silent"] is False


def test_route_low_routing_on_fails_open_when_silent_raises(tmp_feed, monkeypatch):
    """Wyjątek transportu też nie może przerwać ścieżki głównej ani feedu."""
    _flags(monkeypatch, ENABLE_NOTIFY_PRIORITY_ROUTING=True)

    def _raise(_text):
        raise TimeoutError("synthetic silent timeout")

    monkeypatch.setattr(nr, "_send_silent", _raise)
    assert nr.route("Briefing dzienny", source="daily_briefing") is True
    rows = _feed_rows(tmp_feed)
    assert rows[0]["sent_main"] is True
    assert rows[0]["sent_silent"] is False


def test_route_low_routing_on_high_stays_main(tmp_feed, monkeypatch):
    # tylko LOW-routing ON (split OFF): HIGH-technical zostaje na main (jak legacy)
    _flags(monkeypatch, ENABLE_NOTIFY_PRIORITY_ROUTING=True)
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda text: pytest.fail("split OFF nie może dotknąć kanału napraw"),
    )
    assert nr.route("🔴 OnFailure awaria", source="alert_onfailure", priority="high") is True
    rows = _feed_rows(tmp_feed)
    assert rows[0]["priority"] == "high"
    assert rows[0]["sent_main"] is True         # HIGH zostaje na głównym bocie


def test_route_explicit_priority_respected(tmp_feed, monkeypatch):
    _flags(monkeypatch, ENABLE_NOTIFY_PRIORITY_ROUTING=True)
    # treść 'briefing' = low, ale explicit high wygrywa
    assert nr.route("briefing", priority="high") is True
    assert _feed_rows(tmp_feed)[0]["priority"] == "high"


# ── routing: TECHNICAL→napraw (ENABLE_NOTIFY_CHANNEL_SPLIT) ────────────────
# NEGATYWNY ORACLE: technical NIE na main gdy repairs OK; critical NIGDY na napraw.

def test_route_split_on_technical_to_repairs(tmp_feed, monkeypatch):
    _flags(monkeypatch, ENABLE_NOTIFY_CHANNEL_SPLIT=True)
    monkeypatch.setattr(nr, "_send_repairs", lambda text: True)
    monkeypatch.setattr(
        nr, "_send_silent",
        lambda text: pytest.fail("technical nie idzie przez cichy bot"),
    )
    assert nr.route("🔴 OnFailure awaria serwisu", source="alert_onfailure",
                    priority="high") is False       # odcięte od głównej grupy
    rows = _feed_rows(tmp_feed)
    assert rows[0]["priority"] == "high"
    assert rows[0]["category"] == "technical"
    assert rows[0]["sent_main"] is False
    assert rows[0]["sent_repairs"] is True


def test_route_split_on_critical_never_to_repairs(tmp_feed, monkeypatch):
    # NEGATYWNY ORACLE: krytyczny biznes NIGDY nie ląduje na kanale napraw.
    _flags(monkeypatch, ENABLE_NOTIFY_CHANNEL_SPLIT=True)
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda text: pytest.fail("krytyczny alert NIGDY na kanał napraw"),
    )
    assert nr.route("Problem z płatnością P24 zamówienia 900138",
                    priority="high") is True         # zostaje na głównej grupie
    rows = _feed_rows(tmp_feed)
    assert rows[0]["category"] == "critical"
    assert rows[0]["sent_main"] is True
    assert rows[0]["sent_repairs"] is False


def test_route_split_on_technical_fails_open_when_repairs_fails(tmp_feed, monkeypatch):
    """Technical wolno odciąć od main wyłącznie po potwierdzonym repairs delivery."""
    _flags(monkeypatch, ENABLE_NOTIFY_CHANNEL_SPLIT=True)
    monkeypatch.setattr(nr, "_send_repairs", lambda text: False)
    assert nr.route("🔴 OnFailure awaria", source="alert_onfailure",
                    priority="high") is True         # fail-open na główną grupę
    rows = _feed_rows(tmp_feed)
    assert rows[0]["category"] == "technical"
    assert rows[0]["sent_main"] is True
    assert rows[0]["sent_repairs"] is False


def test_route_split_on_technical_fails_open_when_repairs_raises(tmp_feed, monkeypatch):
    _flags(monkeypatch, ENABLE_NOTIFY_CHANNEL_SPLIT=True)

    def _raise(_text):
        raise TimeoutError("synthetic repairs timeout")

    monkeypatch.setattr(nr, "_send_repairs", _raise)
    assert nr.route("🔴 OnFailure awaria", source="alert_onfailure",
                    priority="high") is True
    rows = _feed_rows(tmp_feed)
    assert rows[0]["sent_main"] is True
    assert rows[0]["sent_repairs"] is False


def test_route_split_on_low_still_goes_main_when_low_routing_off(tmp_feed, monkeypatch):
    # split ON, ale LOW-routing OFF: LOW nie jest info-divertowane (cichy off),
    # a LOW nie jest technical → zostaje na main. Wymiary niezależne.
    _flags(monkeypatch, ENABLE_NOTIFY_CHANNEL_SPLIT=True)
    monkeypatch.setattr(
        nr, "_send_silent",
        lambda text: pytest.fail("LOW-routing OFF nie może dotknąć cichego bota"),
    )
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda text: pytest.fail("LOW nie jest technical — kanał napraw nietknięty"),
    )
    assert nr.route("Briefing dzienny", source="daily_briefing") is True
    rows = _feed_rows(tmp_feed)
    assert rows[0]["category"] == "info"
    assert rows[0]["sent_main"] is True


def test_route_both_flags_on_are_independent(tmp_feed, monkeypatch):
    # obie flagi ON: info→cichy, technical→napraw, critical→main. Rozdzielone.
    _flags(monkeypatch, ENABLE_NOTIFY_PRIORITY_ROUTING=True,
           ENABLE_NOTIFY_CHANNEL_SPLIT=True)
    monkeypatch.setattr(nr, "_send_silent", lambda text: True)
    monkeypatch.setattr(nr, "_send_repairs", lambda text: True)

    assert nr.route("Briefing dzienny", source="daily_briefing") is False   # info→cichy
    assert nr.route("🔴 OnFailure awaria", source="alert_onfailure",
                    priority="high") is False                               # tech→napraw
    assert nr.route("płatność P24 zamówienia", priority="high") is True     # critical→main

    rows = _feed_rows(tmp_feed)
    assert [r["category"] for r in rows] == ["info", "technical", "critical"]
    assert [r["sent_main"] for r in rows] == [False, False, True]
    assert [r["sent_silent"] for r in rows] == [True, False, False]
    assert [r["sent_repairs"] for r in rows] == [False, True, False]
