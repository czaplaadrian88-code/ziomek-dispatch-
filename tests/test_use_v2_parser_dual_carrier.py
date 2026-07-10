"""Migracja 1b USE_V2_PARSER (ACK Adrian 2026-07-10): kontrakt dual-carrier.

Read-site dispatchera parsera czyta flags.json-FIRST z fallbackiem na env-frozen
stałą modułu: `flag("USE_V2_PARSER", USE_V2_PARSER)`. Testy przypinają OBIE gałęzie
nośnika (klucz w flags.json wygrywa; brak klucza → stała modułu), więc:
  - dziś (bez klucza) = bajt-parytet z zachowaniem sprzed migracji,
  - po flipie (klucz true) = v2 globalnie, hot-reload bez restartu.
Hermetyczne: własny tmp flags.json przez common.FLAGS_PATH (idiom repo), sentinele
zamiast realnych parserów (zero HTML/sieci).
"""
from __future__ import annotations

import json

import pytest

from dispatch_v2 import common
from dispatch_v2 import panel_client as PC
from dispatch_v2 import panel_html_parser as PHP


V1 = {"parser": "v1-sentinel"}
V2 = {"parser": "v2-sentinel"}


@pytest.fixture()
def _routing(monkeypatch, tmp_path):
    """Sentinele obu parserów + izolowany flags.json + shadow-compare OFF."""
    monkeypatch.setattr(PC, "_parse_panel_html_v1_legacy", lambda html: dict(V1))
    monkeypatch.setattr(PHP, "parse_panel_html_v2", lambda html: dict(V2))
    monkeypatch.setattr(PC, "ENABLE_V2_SHADOW_COMPARE", False)

    fj = tmp_path / "flags.json"

    def set_flags(d):
        fj.write_text(json.dumps(d), encoding="utf-8")
        monkeypatch.setattr(common, "FLAGS_PATH", fj)
        common._flags_cache = None
        common._flags_mtime = 0

    yield set_flags
    common._flags_cache = None
    common._flags_mtime = 0


def test_flags_json_key_true_wins_over_const(_routing, monkeypatch):
    """flags.json USE_V2_PARSER=true → v2, nawet gdy stała modułu (env) = False."""
    _routing({"USE_V2_PARSER": True})
    monkeypatch.setattr(PC, "USE_V2_PARSER", False)
    assert PC.parse_panel_html("<html/>") == V2


def test_flags_json_key_false_wins_over_const(_routing, monkeypatch):
    """flags.json USE_V2_PARSER=false → v1, nawet gdy stała modułu (env) = True.
    (= hot-rollback po flipie bez restartu)."""
    _routing({"USE_V2_PARSER": False})
    monkeypatch.setattr(PC, "USE_V2_PARSER", True)
    assert PC.parse_panel_html("<html/>") == V1


def test_absent_key_falls_back_to_module_const(_routing, monkeypatch):
    """Brak klucza (stan DZIŚ, przed flipem) → decyduje env-frozen stała modułu
    (per-service rzeczywistość: watcher drop-in=1 → v2, reszta default=0 → v1)."""
    _routing({})
    monkeypatch.setattr(PC, "USE_V2_PARSER", False)
    assert PC.parse_panel_html("<html/>") == V1
    monkeypatch.setattr(PC, "USE_V2_PARSER", True)
    assert PC.parse_panel_html("<html/>") == V2
