"""L5.1 (Sprint 1 Z3, 2026-07-05) — testy ETA load-aware (K3).

Moduł `eta_load_aware` (tabela+hierarchia+clamp+fail-soft) + e2e przez REALNY
`assess_order` na archetypie 472791 (fixture jak `replay_feasibility`, Lekcja
#28: nie mock silnika): SHADOW zawsze (metryki eta_la_*), decyzja ON≠OFF
(flaga przesuwa eta_pickup_utc/travel_min i taguje eta_source).
"""
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import eta_load_aware as ELA  # noqa: E402

FIXTURE = str(Path(__file__).resolve().parents[1]
              / "tools" / "fixtures" / "472791_archetype.json")


def _write_calib(tmp_path, segments, min_n=30):
    p = tmp_path / "calib.json"
    p.write_text(json.dumps({"min_n": min_n, "segments": segments}))
    return str(p)


def _use_calib(monkeypatch, tmp_path, segments, min_n=30):
    monkeypatch.setattr(ELA, "CALIB_PATH",
                        _write_calib(tmp_path, segments, min_n))
    monkeypatch.setattr(ELA, "_cache", {"mtime": None, "data": None})


# ---------- moduł: tabela / hierarchia / clamp / fail-soft ----------

def test_missing_table_is_failsoft_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(ELA, "CALIB_PATH", str(tmp_path / "nope.json"))
    monkeypatch.setattr(ELA, "_cache", {"mtime": None, "data": None})
    assert ELA.pickup_buffer_min("std", 0) == 0.0


def test_hierarchy_tier_solo_then_tier_then_global(monkeypatch, tmp_path):
    _use_calib(monkeypatch, tmp_path, {
        "std|solo": {"med_err_min": -8.5, "n": 40},
        "std": {"med_err_min": -4.4, "n": 150},
        "_global": {"med_err_min": -3.8, "n": 600},
    })
    assert ELA.pickup_buffer_min("std", 0) == 8.5        # tier|solo
    assert ELA.pickup_buffer_min("std", 2) == 4.4        # tier (brak std|bundle)
    assert ELA.pickup_buffer_min("gold", 1) == 3.8       # _global fallback


def test_min_n_skips_thin_segment(monkeypatch, tmp_path):
    _use_calib(monkeypatch, tmp_path, {
        "std|solo": {"med_err_min": -9.9, "n": 5},       # za cienki
        "_global": {"med_err_min": -3.0, "n": 600},
    })
    assert ELA.pickup_buffer_min("std", 0) == 3.0


def test_pessimistic_segment_gives_zero(monkeypatch, tmp_path):
    _use_calib(monkeypatch, tmp_path, {
        "unknown": {"med_err_min": 1.1, "n": 60},
        "_global": {"med_err_min": -3.8, "n": 600},
    })
    # segment znaleziony i pesymistyczny → 0.0 (nie spadaj do _global)
    assert ELA.pickup_buffer_min(None, 3) == 0.0


def test_clamp_to_cap(monkeypatch, tmp_path):
    _use_calib(monkeypatch, tmp_path, {
        "_global": {"med_err_min": -55.0, "n": 600},
    })
    assert ELA.pickup_buffer_min("std", 0) == ELA.BUFFER_CAP_MIN


# ---------- e2e: realny assess_order (archetyp 472791) ----------

def _assess(monkeypatch):
    from dispatch_v2.tools import replay_feasibility as RF
    from dispatch_v2 import dispatch_pipeline as DP
    fixture = json.loads(Path(FIXTURE).read_text())
    monkeypatch.setattr(C, "ENABLE_V327_PRE_PROPOSAL_RECHECK", False)
    return DP.assess_order(
        order_event=dict(fixture["order_event"]),
        fleet_snapshot=RF._build_fleet(fixture),
        restaurant_meta=None,
        now=RF._parse_dt(fixture["now"]),
    )


def _cand_metrics(result):
    for c in (result.candidates or []):
        m = getattr(c, "metrics", None) or {}
        # kandydat NIE nadpisany post-loop polityką no_gps/pre_shift
        if ("eta_la_buffer_min" in m
                and m.get("eta_source") not in ("no_gps_fallback", "pre_shift")):
            return m
    raise AssertionError("brak kandydata z metryką eta_la_buffer_min "
                         "poza polityką no_gps/pre_shift")


def test_e2e_shadow_off_metrics_present_decision_untouched(monkeypatch, tmp_path):
    _use_calib(monkeypatch, tmp_path, {"_global": {"med_err_min": -6.0, "n": 600}})
    monkeypatch.setattr(C, "decision_flag",
                        lambda n, d=False: False if n == "ENABLE_ETA_LOAD_AWARE"
                        else C.flag(n, d))
    m = _cand_metrics(_assess(monkeypatch))
    assert m["eta_la_buffer_min"] == 6.0
    assert "+load_aware" not in (m.get("eta_source") or "")
    # shadow: skorygowana obietnica ISTNIEJE obok, surowa nietknięta
    from datetime import datetime
    raw = datetime.fromisoformat(m["eta_pickup_utc"])
    la = datetime.fromisoformat(m["eta_pickup_load_aware_utc"])
    assert (la - raw).total_seconds() == 360.0


def test_e2e_on_shifts_promise(monkeypatch, tmp_path):
    _use_calib(monkeypatch, tmp_path, {"_global": {"med_err_min": -6.0, "n": 600}})
    monkeypatch.setattr(C, "decision_flag",
                        lambda n, d=False: True if n == "ENABLE_ETA_LOAD_AWARE"
                        else C.flag(n, d))
    m = _cand_metrics(_assess(monkeypatch))
    assert (m.get("eta_source") or "").endswith("+load_aware")
    # ON: eta_pickup_utc == skorygowana (przesunięta o bufor)
    assert m["eta_pickup_utc"] == m["eta_pickup_load_aware_utc"]


def test_e2e_no_table_byte_identical_shadow_fields_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(ELA, "CALIB_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr(ELA, "_cache", {"mtime": None, "data": None})
    m = _cand_metrics(_assess(monkeypatch))
    assert m["eta_la_buffer_min"] == 0.0
    assert m["eta_pickup_load_aware_utc"] == m["eta_pickup_utc"]
    assert "+load_aware" not in (m.get("eta_source") or "")
