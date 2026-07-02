"""Lane TZ-drobnica (FALA-2) — behawioralny kill-test TZ dla drive_speed_overshoot_verdict.

`drive_speed_overshoot_verdict.py` liczył bias dostawy interpretując naiwny
`delivered_at` jako Warsaw przez `.replace(tzinfo=WARSAW)`, gdzie WARSAW był
STAŁYM offsetem +2 (`timezone(timedelta(hours=2))`). Bias = (delivered_at_Warsaw
− delivery_pred_last_UTC) → różnica NA GRANICY strefy, więc zły offset zimą
KŁAMIE o 60 min (bomba po końcu DST 25-26.10.2026: CET=+1, stały +2 zawyża).

Test jest BEHAWIORALNY (C13), nie string-match:
  (a) ZIMA (CET=+1): poprawny bias liczony ZoneInfo; MUTATION-CHECK — podmiana
      ZoneInfo→stały +2 MUSI przesunąć bias o −60 min (dowód że fix ma zęby).
  (b) LATO (CEST=+2): ZoneInfo i stały +2 dają IDENTYCZNY wynik (zmiana neutralna dziś).

C12(e): ładujemy PO ŚCIEŻCE z worktree (conftest celuje sys.path w kanon),
z posprzątaniem sys.modules w try/finally.
"""
import importlib.util
import os
import sys
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

_WT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOL = os.path.join(_WT_ROOT, "tools", "drive_speed_overshoot_verdict.py")
_MODNAME = "drive_speed_overshoot_verdict_wt"

FIXED2 = timezone(timedelta(hours=2))  # stary, BŁĘDNY zimą offset (baseline mutacji)


@pytest.fixture()
def tool():
    assert os.path.exists(_TOOL), f"brak toola: {_TOOL}"
    spec = importlib.util.spec_from_file_location(_MODNAME, _TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODNAME] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(_MODNAME, None)


def test_tool_uses_zoneinfo(tool):
    assert getattr(tool.WARSAW, "key", None) == "Europe/Warsaw"


def test_winter_bias_zoneinfo_and_mutation(tool, monkeypatch):
    # ZIMA: delivered_at 10:30 naive = 10:30 Warsaw CET = 09:30 UTC;
    # pred 09:30 UTC ⇒ dostarczono DOKŁADNIE na ETA → bias 0.
    r = {
        "delivered_at": "2026-12-15T10:30:00",
        "delivery_pred_last": "2026-12-15T09:30:00+00:00",
    }
    good = tool._deliv_bias(r)
    assert good == pytest.approx(0.0, abs=1e-6), f"zimowy bias ma być ~0, jest {good}"
    # MUTATION-CHECK (C13): rewers fixu (ZoneInfo→stały +2) → 10:30+02:00 = 08:30 UTC,
    # 60 min PRZED pred → bias −60 (fałszywy pesymizm). Dowód że strażnik gryzie.
    monkeypatch.setattr(tool, "WARSAW", FIXED2)
    bug = tool._deliv_bias(r)
    assert bug == pytest.approx(-60.0, abs=1e-6), f"mutacja (+2) zimą ma dać −60 min, dała {bug}"
    assert good != bug


def test_summer_parity_zoneinfo_equals_fixed_offset(tool, monkeypatch):
    # LATO (CEST=+2): ZoneInfo i stały +2 IDENTYCZNE ⇒ neutralne dziś.
    r = {
        "delivered_at": "2026-07-15T10:30:00",
        "delivery_pred_last": "2026-07-15T08:30:00+00:00",
    }
    with_zoneinfo = tool._deliv_bias(r)
    monkeypatch.setattr(tool, "WARSAW", FIXED2)
    with_fixed = tool._deliv_bias(r)
    assert with_zoneinfo == pytest.approx(with_fixed, abs=1e-6) == pytest.approx(0.0, abs=1e-6)
