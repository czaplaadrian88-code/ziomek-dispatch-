"""Adwersarialna re-weryfikacja remediacji b0546fb88.

Plik dowodowy poza ``tests/``: pełna regresja kandydata zachowuje swój kanoniczny
zbiór nodeidów. Każdy transport jest atrapą; zero realnych wysyłek i zero live I/O.
"""

from __future__ import annotations

import json
from datetime import datetime
from itertools import product

import pytest

from dispatch_v2 import notify_router as nr


@pytest.fixture
def isolated_router(tmp_path, monkeypatch):
    feed = tmp_path / "notify_feed.jsonl"
    monkeypatch.setattr(nr, "FEED_PATH", feed)
    monkeypatch.setattr(nr, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(nr, "CONFIG_PATH", tmp_path / "no-config.json")
    monkeypatch.setattr(nr, "_config_cache", None)
    monkeypatch.setattr(nr, "_config_mtime", 0.0)
    return feed


def _set_flags(monkeypatch, *, priority, split, shadow):
    values = {
        "ENABLE_NOTIFY_PRIORITY_ROUTING": priority,
        "ENABLE_NOTIFY_CHANNEL_SPLIT": split,
        "ENABLE_NOTIFY_CHANNEL_SPLIT_SHADOW": shadow,
    }
    monkeypatch.setattr(
        "dispatch_v2.common.flag",
        lambda name, default=False: values.get(name, default),
    )


def _rows(feed):
    return [json.loads(line) for line in feed.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    "priority_on,split_on,shadow_on",
    list(product((False, True), repeat=3)),
)
def test_all_eight_flag_combinations_route_and_feed_coherently(
    isolated_router, monkeypatch, priority_on, split_on, shadow_on
):
    """8/8: shadow nie zmienia routingu; split/priority sterują własnym kanałem."""
    _set_flags(
        monkeypatch,
        priority=priority_on,
        split=split_on,
        shadow=shadow_on,
    )
    silent_calls = []
    repairs_calls = []
    monkeypatch.setattr(nr, "_send_silent", lambda text: not silent_calls.append(text))
    monkeypatch.setattr(nr, "_send_repairs", lambda text: not repairs_calls.append(text))

    results = [
        nr.route("Briefing dzienny", source="daily_briefing"),
        nr.route("parser timeout", source="parser_health", priority="high"),
        nr.route("Problem z płatnością P24", priority="high"),
    ]

    assert results == [not priority_on, not split_on, True]
    assert len(silent_calls) == int(priority_on)
    assert len(repairs_calls) == int(split_on)
    rows = _rows(isolated_router)
    assert len(rows) == 3
    split_fields = {"category", "sent_repairs"}
    for row in rows:
        assert split_fields.issubset(row) is (split_on or shadow_on)
    if split_on or shadow_on:
        assert [row["category"] for row in rows] == ["info", "technical", "critical"]


def test_off_off_off_is_exact_legacy_json_line(isolated_router, monkeypatch):
    """OFF/OFF/OFF: nie tylko zbiór kluczy, ale dokładna linia JSON legacy."""
    _set_flags(monkeypatch, priority=False, split=False, shadow=False)

    class FrozenDatetime:
        @classmethod
        def now(cls, tz):
            return datetime(2026, 8, 2, 12, 34, 56, tzinfo=tz)

    monkeypatch.setattr(nr, "datetime", FrozenDatetime)
    assert nr.route("Briefing dzienny", source="daily_briefing") is True
    expected = {
        "ts": "2026-08-02T12:34:56+02:00",
        "priority": "low",
        "source": "daily_briefing",
        "text": "Briefing dzienny",
        "sent_main": True,
        "sent_silent": False,
    }
    assert isolated_router.read_text(encoding="utf-8") == (
        json.dumps(expected, ensure_ascii=False) + "\n"
    )


def test_shadow_only_adds_observation_and_never_sends(isolated_router, monkeypatch):
    _set_flags(monkeypatch, priority=False, split=False, shadow=True)
    monkeypatch.setattr(
        nr, "_send_silent", lambda _text: pytest.fail("shadow wysłał na silent")
    )
    monkeypatch.setattr(
        nr, "_send_repairs", lambda _text: pytest.fail("shadow wysłał na repairs")
    )
    assert nr.route("parser timeout", source="parser_health", priority="high") is True
    assert _rows(isolated_router)[0] | {} == {
        **_rows(isolated_router)[0],
        "category": "technical",
        "sent_repairs": False,
        "sent_main": True,
        "sent_silent": False,
    }


@pytest.mark.parametrize("source", [None, "", "UNMAPPED_SOURCE"])
@pytest.mark.parametrize("priority", [None, "", "HIGH", " high "])
def test_unknown_and_empty_inputs_fail_safe_to_main(
    isolated_router, monkeypatch, source, priority
):
    _set_flags(monkeypatch, priority=True, split=True, shadow=True)
    monkeypatch.setattr(
        nr, "_send_silent", lambda _text: pytest.fail("unknown został wyciszony")
    )
    monkeypatch.setattr(
        nr, "_send_repairs", lambda _text: pytest.fail("unknown trafił do repairs")
    )
    assert nr.classify_category("", source=source, priority=priority) == "critical"
    assert nr.route("", source=source, priority=priority) is True


def test_case_and_known_collision_keep_business_critical(isolated_router, monkeypatch):
    _set_flags(monkeypatch, priority=True, split=True, shadow=True)
    monkeypatch.setattr(
        nr, "_send_repairs", lambda _text: pytest.fail("business trafił do repairs")
    )
    text = "PŁATNOŚĆ P24 FAILED EXIT=1 — RESTAURACJA PADŁA"
    assert nr.classify_category(text, source="alert_onfailure", priority="low") == "critical"
    assert nr.route(text, source="alert_onfailure", priority="low") is True


@pytest.mark.parametrize("mode", ["false", "raise"])
def test_repairs_transport_failure_is_fail_open(
    isolated_router, monkeypatch, mode
):
    _set_flags(monkeypatch, priority=True, split=True, shadow=True)
    if mode == "false":
        monkeypatch.setattr(nr, "_send_repairs", lambda _text: False)
    else:
        def fail(_text):
            raise TimeoutError("synthetic")
        monkeypatch.setattr(nr, "_send_repairs", fail)
    assert nr.route("parser timeout", source="parser_health", priority="high") is True
    row = _rows(isolated_router)[0]
    assert row["sent_main"] is True
    assert row["sent_repairs"] is False


def test_p0_payment_gateway_failure_cannot_be_diverted_to_repairs(
    isolated_router, monkeypatch
):
    """P0 repro: biznesowa płatność po angielsku omija listę critical_keywords."""
    _set_flags(monkeypatch, priority=False, split=True, shadow=True)
    repairs_calls = []
    monkeypatch.setattr(
        nr, "_send_repairs", lambda text: not repairs_calls.append(text)
    )
    text = "payment-gateway.service OnFailure Result=exit-code"
    category = nr.classify_category(text, source="alert_onfailure", priority="high")
    proceed_main = nr.route(text, source="alert_onfailure", priority="high")
    assert (category, proceed_main, repairs_calls) == ("critical", True, [])


def test_p1_parser_alert_with_order_context_stays_on_repairs(
    isolated_router, monkeypatch
):
    """P1 repro: szerokie 'zamówieni' nie może przejąć technicznego parser alertu."""
    _set_flags(monkeypatch, priority=False, split=True, shadow=True)
    monkeypatch.setattr(nr, "_send_repairs", lambda _text: True)
    text = "parser timeout podczas dekodowania zamówienia 123"
    category = nr.classify_category(text, source="parser_health", priority="high")
    proceed_main = nr.route(text, source="parser_health", priority="high")
    assert (category, proceed_main) == ("technical", False)


def test_p1_off_off_off_does_not_execute_new_split_classifier(
    isolated_router, monkeypatch
):
    """P1 repro: nowy classifier nie może być osiągalny przy pełnym legacy OFF."""
    _set_flags(monkeypatch, priority=False, split=False, shadow=False)
    monkeypatch.setattr(
        nr,
        "classify_category",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("new path")),
    )
    assert nr.route("Briefing dzienny", source="daily_briefing") is True
    assert set(_rows(isolated_router)[0]) == {
        "ts", "priority", "source", "text", "sent_main", "sent_silent",
    }
