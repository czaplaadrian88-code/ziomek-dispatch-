"""Ratchet + oracle: notify 5-caller `source=` scope-gap fix (2026-08-02).

Kontekst: notify channel-split jest LIVE (ENABLE_NOTIFY_CHANNEL_SPLIT). Klasyfikacja
kategorii jest SOURCE-PRIMARY (notify_router.classify_category). Pięć technicznych
callerów alertów NIE podawało `source=`, więc alerty klasyfikowały się jako
fail-safe 'critical' (→ owner) zamiast 'technical' (→ kanał NAPRAW).

Ten plik jest:
  - ORACLE (część B) reprodukującym defekt: gdy któryś caller zgubi `source=`,
    ratchet czerwienieje.
  - Kontrakt klasyfikacji (część A): 5 źródeł → 'technical'; fail-safe zachowany
    (biznes → 'critical'; nieznane źródło → 'critical'). Zero zgubionego alertu.
"""
import types

import pytest

from dispatch_v2 import notify_router

FIVE_SOURCES = [
    "parser_health",
    "parser_health_layer3",
    "liveness_probe",
    "detector_419",
    "osrm_client",
]


# ─────────────────────────── Część A: kontrakt klasyfikacji ───────────────────────────

@pytest.mark.parametrize("source", FIVE_SOURCES)
def test_five_sources_are_in_hardcoded_technical_set(source):
    # Mapa źródło→kategoria jest ZAHARDKODOWANA (owner 02.08); każde z 5 źródeł MUSI
    # być w zbiorze technicznym, inaczej routing do NAPRAW nie zadziała.
    assert source in notify_router._SOURCE_TECHNICAL


@pytest.mark.parametrize("source", FIVE_SOURCES)
def test_five_sources_benign_text_classify_technical(source):
    # Treść czysto infra/health (bez słowa biznesowego) → 'technical' (→ NAPRAW).
    text = "[health] TCP probe failed x3 on :8888 — thread appears dead; exit=1"
    assert notify_router.classify_category(text, source=source) == "technical"


@pytest.mark.parametrize("source", FIVE_SOURCES)
@pytest.mark.parametrize("biz_text", [
    "OSRM degraded — payment gateway p24 timeout",
    "awaria: płatność nie przeszła przez gateway",
    "nowa restauracja zgłoszona do onboardingu",
])
def test_business_content_escalates_technical_source_to_critical(source, biz_text):
    # FAIL-SAFE: treść biznesowa z technicznego źródła ESKALUJE technical → 'critical'
    # (biznesu nie da się schować na NAPRAW). Eskalacja tylko w górę.
    assert notify_router.classify_category(biz_text, source=source) == "critical"


def test_unknown_source_benign_high_is_critical_failsafe():
    # Nieznane źródło, brak słowa biznesowego, priorytet HIGH → fail-safe 'critical'
    # (nigdy nie zgubione, nigdy ciche).
    assert notify_router.classify_category(
        "random unknown high alert exit=1", source="mystery", priority="high"
    ) == "critical"
    assert notify_router.classify_category(
        "random unknown high alert exit=1", source=None, priority="high"
    ) == "critical"


def test_business_content_unknown_source_is_critical():
    # Treść biznesowa nawet przy nieznanym źródle → 'critical' (→ owner).
    assert notify_router.classify_category(
        "nowa restauracja rejestracja", source="whatever"
    ) == "critical"


# ─────────────────────── Część B: ratchet callerów (source= przekazane) ───────────────────────

class _Capture:
    """Przechwytuje wywołania send_admin_alert; zwraca True (sukces)."""
    def __init__(self):
        self.calls = []

    def __call__(self, text, *, source=None, priority=None):
        self.calls.append({"text": text, "source": source, "priority": priority})
        return True


def test_osrm_client_funnel_passes_source(monkeypatch):
    from dispatch_v2 import osrm_client, telegram_utils
    cap = _Capture()
    monkeypatch.setattr(telegram_utils, "send_admin_alert", cap)
    osrm_client._mp13_send_alert_safe("✅ OSRM recovery — z powrotem healthy mode")
    assert cap.calls, "osrm_client nie wywołał send_admin_alert"
    assert cap.calls[-1]["source"] == "osrm_client"


def test_liveness_probe_funnel_passes_source(monkeypatch):
    from dispatch_v2 import telegram_utils
    from dispatch_v2.observability import liveness_probe
    cap = _Capture()
    monkeypatch.setattr(telegram_utils, "send_admin_alert", cap)
    liveness_probe._send_alert("parser-health-8888", "down x3 :8888 not accepting")
    assert cap.calls, "liveness_probe nie wywołał send_admin_alert"
    assert cap.calls[-1]["source"] == "liveness_probe"


def test_detector_419_funnel_passes_source(monkeypatch):
    from dispatch_v2.monitoring import detector_419
    cap = _Capture()
    # detector_419 importuje nazwę na poziomie modułu → patch tam.
    monkeypatch.setattr(detector_419, "send_admin_alert", cap)
    monkeypatch.setattr(detector_419, "_last_alert_at", 0.0)  # obejdź debounce
    detector_419._maybe_alert(5)
    assert cap.calls, "detector_419 nie wywołał send_admin_alert"
    assert cap.calls[-1]["source"] == "detector_419"


def test_parser_health_funnel_passes_source(monkeypatch):
    from dispatch_v2 import parser_health, telegram_utils
    cap = _Capture()
    monkeypatch.setattr(telegram_utils, "send_admin_alert", cap)
    inst = parser_health.ParserHealthMonitor.__new__(parser_health.ParserHealthMonitor)
    inst._last_alert_at = {}
    parser_health.ParserHealthMonitor._maybe_send_alert(
        inst, {"type": "TEST", "severity": "warning", "message": "benign health anomaly"}
    )
    assert cap.calls, "parser_health nie wywołał send_admin_alert"
    assert cap.calls[-1]["source"] == "parser_health"


def test_parser_health_layer3_funnel_passes_source(monkeypatch):
    from dispatch_v2 import parser_health_layer3, telegram_utils
    cap = _Capture()
    monkeypatch.setattr(telegram_utils, "send_admin_alert", cap)
    ns = types.SimpleNamespace(_last_alert_at={}, _error_count=0)
    parser_health_layer3._maybe_send_alert_v3(
        ns, {"type": "TEST", "severity": "warning", "message": "benign health anomaly"}
    )
    assert cap.calls, "parser_health_layer3 nie wywołał send_admin_alert"
    assert cap.calls[-1]["source"] == "parser_health_layer3"
