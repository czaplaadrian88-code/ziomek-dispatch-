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


def test_category_technical_by_source(tmp_feed):
    # SOURCE-PRIMARY: źródło techniczne → technical (deterministycznie, nie z treści).
    assert nr.classify_category("cokolwiek", source="alert_onfailure") == "technical"
    assert nr.classify_category("parser timeout", source="parser_health", priority="high") == "technical"
    assert nr.classify_category("brak snapshotu 24h", source="backup_sentinel") == "technical"
    assert nr.classify_category("degraded routing", source="osrm_client") == "technical"


def test_category_critical_by_keyword(tmp_feed):
    assert nr.classify_category("Problem z płatnością P24 zamówienia 900") == "critical"
    assert nr.classify_category("Nowy kurier do sparowania: Jan K.") == "critical"
    assert nr.classify_category("Awaria integracji restauracji Foodage") == "critical"


def test_category_source_override_only_nonbusiness(tmp_feed):
    # source_category działa TYLKO dla treści nie-biznesowej. Biznes wygrywa PRZED
    # source (P0-1): 'płatność' + tech source = critical, NIE technical.
    assert nr.classify_category("płatność", source="alert_onfailure") == "critical"
    # treść nie-biznesowa + tech source → technical
    assert nr.classify_category("cokolwiek", source="backup_sentinel") == "technical"
    assert nr.classify_category("restart serwisu", source="alert_onfailure") == "technical"


def test_category_business_wins_over_technical(tmp_feed):
    # ORACLE kolejności: alert ma i business (restauracja), i technikę (padła)
    # → critical (Adrian MUSI zobaczyć). Odwrócenie kolejności = ten test RED.
    assert nr.classify_category("Restauracja Foodage padła — awaria integracji") == "critical"
    assert nr.classify_category("płatność P24 failed exit=1") == "critical"


def test_category_unknown_high_is_critical_failsafe(tmp_feed):
    # nieznany HIGH (nic nie pasuje) → critical (nigdy na kanał napraw)
    assert nr.classify_category("zupełnie nietypowy komunikat bez słów kluczowych") == "critical"


def test_category_technical_source_ignores_low_priority(tmp_feed):
    # SOURCE-PRIMARY: źródło techniczne → technical NAWET przy priority=low
    # (source decyduje, nie priorytet). Bez treści biznesowej.
    assert nr.classify_category("rutynowy heartbeat", source="alert_onfailure",
                                priority="low") == "technical"


def test_category_unknown_source_high_is_critical(tmp_feed):
    # SOURCE-PRIMARY: nieznane/puste źródło + HIGH → critical (fail-safe do ownera).
    assert nr.classify_category("cokolwiek nietypowe", source="ZUPELNIE_INNE", priority="high") == "critical"
    assert nr.classify_category("cokolwiek", source=None, priority="high") == "critical"
    assert nr.classify_category("cokolwiek", source="", priority="high") == "critical"


def test_category_business_beats_unknown_low_but_narrow(tmp_feed):
    # WĄSKI biznes → critical nawet przy nieznanym źródle + priority=low (P0-2)...
    assert nr.classify_category("Problem z płatnością P24", source=None, priority="low") == "critical"
    # ...i eskaluje źródło techniczne (payment po angielsku) — round-2 P0.
    assert nr.classify_category("payment-gateway down", source="alert_onfailure", priority="high") == "critical"
    # ...ale SZEROKIE 'zamówieni' NIE przejmuje technicznego alertu (round-2 P1 regresja).
    assert nr.classify_category("parser timeout zamówienia 5", source="parser_health", priority="high") == "technical"


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
    assert rows[0]["sent_main"] is True         # nie odcięte (flagi off)
    assert rows[0]["sent_silent"] is False
    # BYTE-PARITY (P1-5): OFF/OFF = legacy schema, ZERO nowych kluczy.
    assert set(rows[0]) == {"ts", "priority", "source", "text", "sent_main", "sent_silent"}


def test_route_split_off_technical_stays_main(tmp_feed, monkeypatch):
    # ON≠OFF: ten sam alert techniczny przy split OFF zostaje na main (legacy).
    _flags(monkeypatch)  # obie OFF
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda text: pytest.fail("split OFF nie może dotknąć kanału napraw"),
    )
    assert nr.route("🔴 OnFailure awaria", source="alert_onfailure", priority="high") is True
    rows = _feed_rows(tmp_feed)
    assert rows[0]["sent_main"] is True
    # BYTE-PARITY (P1-5): OFF/OFF = legacy schema (category BY było technical, ale nie w feedzie).
    assert set(rows[0]) == {"ts", "priority", "source", "text", "sent_main", "sent_silent"}


def test_route_shadow_flag_adds_category_without_routing(tmp_feed, monkeypatch):
    # SHADOW: category w feedzie do obserwacji PRZED aktywacją, ZERO routingu
    # (nie tknie cichego ani napraw). Byte-parity łamane świadomie za tą flagą.
    _flags(monkeypatch, ENABLE_NOTIFY_CHANNEL_SPLIT_SHADOW=True)
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda text: pytest.fail("shadow NIE rutuje na kanał napraw"),
    )
    monkeypatch.setattr(
        nr, "_send_silent",
        lambda text: pytest.fail("shadow NIE rutuje na cichy bot"),
    )
    assert nr.route("🔴 OnFailure awaria", source="alert_onfailure", priority="high") is True
    row = _feed_rows(tmp_feed)[0]
    assert row["category"] == "technical"       # obserwacja: DOKĄD BY poszło
    assert row["sent_main"] is True              # ale realnie zostaje na main
    assert row["sent_repairs"] is False


# ── routing: LOW→cichy (ENABLE_NOTIFY_PRIORITY_ROUTING) ───────────────────

def test_route_low_routing_on_low_diverted(tmp_feed, monkeypatch):
    _flags(monkeypatch, ENABLE_NOTIFY_PRIORITY_ROUTING=True)
    monkeypatch.setattr(nr, "_send_silent", lambda text: True)
    assert nr.route("Briefing dzienny", source="daily_briefing") is False
    rows = _feed_rows(tmp_feed)
    assert rows[0]["priority"] == "low"
    assert rows[0]["sent_main"] is False       # odcięte od głównego bota
    assert rows[0]["sent_silent"] is True
    # split OFF → feed legacy (category dopisywane tylko przy split/shadow ON)
    assert "category" not in rows[0]


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


# ═══════════════════════════════════════════════════════════════════════════
# KILL-TEST Sol 2026-08-02 — adwersarialne oracle bezpieczeństwa (TWARDA BRAMKA)
# Wciągnięte z worktree Sola (notify-split-killtest). P0-4 (writery omijające
# choke-point: shift_notifications/worker.py, delivered_integrity_monitor.py) jest
# POZA zakresem splitu — osobna bramka ownera (decyzja Fable 2026-08-02), route()
# fail-open wystarcza dla ścieżki choke-point.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text", [
    "płatność P24 failed exit=1",
    "restauracja Foodage padła — OnFailure",
    "nowy kurier niesparowany — awaria serwisu",
])
def test_kill_business_critical_beats_technical_source_override(
        tmp_feed, monkeypatch, text):
    """P0-1: biznes krytyczny nigdy nie trafia do NAPRAW, także z tech source."""
    _flags(monkeypatch, ENABLE_NOTIFY_CHANNEL_SPLIT=True)
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda _text: pytest.fail("biznes krytyczny trafił do kanału NAPRAW"),
    )
    assert nr.classify_category(text, source="alert_onfailure",
                                priority="high") == "critical"
    assert nr.route(text, source="alert_onfailure", priority="high") is True


def test_kill_business_critical_cannot_be_forced_low_and_silent(
        tmp_feed, monkeypatch):
    """P0-2: jawny priorytet LOW nie może schować płatności na cichym kanale."""
    _flags(monkeypatch, ENABLE_NOTIFY_PRIORITY_ROUTING=True,
           ENABLE_NOTIFY_CHANNEL_SPLIT=True)
    monkeypatch.setattr(
        nr, "_send_silent",
        lambda _text: pytest.fail("biznes krytyczny został wysłany tylko cicho"),
    )
    text = "Problem z płatnością P24 zamówienia"
    assert nr.classify_category(text, priority="low") == "critical"
    assert nr.route(text, priority="low") is True


def test_kill_unknown_high_is_critical_even_if_config_requests_technical(
        tmp_feed, monkeypatch):
    """P0-3: hot-config nie może osłabić fail-safe unknown HIGH -> critical."""
    monkeypatch.setattr(
        nr, "_config_cache",
        {**nr._DEFAULT_CONFIG, "default_category": "technical"},
    )
    monkeypatch.setattr(nr, "_config_mtime", 9e18)
    _flags(monkeypatch, ENABLE_NOTIFY_CHANNEL_SPLIT=True)
    monkeypatch.setattr(
        nr, "_send_repairs",
        lambda _text: pytest.fail("nieznany HIGH trafił do kanału NAPRAW"),
    )
    text = "zupełnie nieznany alert bez słów kluczowych"
    assert nr.classify_category(text, priority="high") == "critical"
    assert nr.route(text, priority="high") is True


def test_kill_flags_off_off_preserves_legacy_feed_schema(tmp_feed, monkeypatch):
    """P1-5: OFF/OFF byte-parity — legacy feed nie może dostać nowych kluczy."""
    _flags(monkeypatch)
    assert nr.route("Briefing dzienny", source="daily_briefing") is True
    assert set(_feed_rows(tmp_feed)[0]) == {
        "ts", "priority", "source", "text", "sent_main", "sent_silent",
    }
