"""Raw-reader shadow_decisions w daily_stats_sheets pomija obserwacje lifecycle.

Kontekst: recenzja reclaim-v3 (22.07) — rekordy `decision_kind=lifecycle_observation`
(np. czasowka-reclaim shadow) maja `best.feasibility=MAYBE`, ale NIE sa decyzjami
dispatchu i nie moga zasilac puli feasible w statystykach dziennych.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import date

# daily_stats_sheets biega produkcyjnie w venv "sheets" (gspread); w kanonicznym
# venv dispatch gspread nie istnieje (znany wzorzec: test_daily_stats_presnapshot
# = xfail środowiskowy). Testujemy czysty raw-reader, więc stubujemy gspread,
# żeby import modułu przeszedł — funkcje Sheets nie są tu wołane.
_stubbed = "gspread" not in sys.modules
if _stubbed:
    _g = types.ModuleType("gspread")
    _g.service_account = lambda *a, **k: None
    _utils = types.ModuleType("gspread.utils")
    _utils.rowcol_to_a1 = lambda *a, **k: "A1"
    _exc = types.ModuleType("gspread.exceptions")
    _exc.APIError = type("APIError", (Exception,), {})
    _exc.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
    _g.utils = _utils
    _g.exceptions = _exc
    sys.modules["gspread"] = _g
    sys.modules["gspread.utils"] = _utils
    sys.modules["gspread.exceptions"] = _exc

import daily_stats_sheets as dss

if _stubbed:
    # sprzatamy stub NATYCHMIAST po imporcie — inaczej wycieka na cala sesje
    # pytest i testy skipujace na braku gspread (test_cod_weekly) dostaja stuba
    # i failuja. daily_stats_sheets trzyma wlasne referencje, sys.modules czysty.
    for _name in ("gspread", "gspread.utils", "gspread.exceptions"):
        sys.modules.pop(_name, None)


def _rec(ts: str, cid: str, **extra):
    rec = {
        "ts": ts,
        "order_id": "12345",
        "verdict": "PROPOSE",
        "best": {"courier_id": cid, "feasibility": "MAYBE"},
        "alternatives": [],
    }
    rec.update(extra)
    return rec


def test_lifecycle_observation_excluded_from_feasible_pool(tmp_path, monkeypatch):
    day = date(2026, 7, 21)
    # 10:30 lokalnie (Warszawa, +2) = 08:30 UTC
    normal = _rec("2026-07-21T08:30:00+00:00", "111")
    observation = _rec(
        "2026-07-21T08:31:00+00:00",
        "222",
        decision_kind="lifecycle_observation",
        verdict="OBSERVE",
    )
    log = tmp_path / "shadow_decisions.jsonl"
    log.write_text(
        json.dumps(normal) + "\n" + json.dumps(observation) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(dss, "SHADOW_DECISIONS_PATH", str(log))

    pools = dss.load_shadow_feasible_pool(day)

    assert "111" in pools[10]
    assert all("222" not in pool for pool in pools.values())
