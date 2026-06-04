"""Tests dla gps_feed_health module — GPS-01 (audyt 2026-06-03).

Pure unit tests (funkcje pure, zero I/O w hot path; tylko append_gps_feed_log
dotyka pliku — izolowany przez tmp_path). Telegram zablokowany autouse conftest
(_block_real_telegram_sends).

Grupy:
A. compute_gps_feed_health — denominator=active_ids, fresh, ratio, median, edge
B. evaluate_gps_feed_alert — state machine (ENTER/SUSTAINED/RECOVERY/NO-OP)
C. GpsFeedAlertConfig.from_flags / from_env (INERT default)
D. render_gps_feed_message
E. append_gps_feed_log (atomic, defensive)
F. regresje GPS-01 (denominator NIE len(gps_dict); INERT-by-default)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2.monitoring.gps_feed_health import (
    GpsFeedAlertConfig,
    GpsFeedAlertState,
    GpsFeedHealth,
    append_gps_feed_log,
    compute_gps_feed_health,
    evaluate_gps_feed_alert,
    render_gps_feed_message,
)

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def _gps_entry(minutes_ago: float):
    return {"lat": 53.13, "lon": 23.16, "timestamp": _ts(minutes_ago)}


def _cfg(**ov):
    base = dict(enabled=True, shadow_only=True, min_fresh_ratio=0.30,
               fresh_cutoff_min=5.0, sustain_cycles=2, realert_interval_sec=1800,
               heartbeat_interval_sec=60, min_active_fleet=3)
    base.update(ov)
    return GpsFeedAlertConfig(**base)


# ============== GROUP A: compute_gps_feed_health ==============

def test_all_fresh_ratio_one():
    gps = {"1": _gps_entry(1), "2": _gps_entry(2), "3": _gps_entry(3)}
    h = compute_gps_feed_health(["1", "2", "3"], gps, NOW, 5.0)
    assert h.total_active == 3
    assert h.fresh == 3
    assert h.fresh_ratio == 1.0


def test_all_stale_ratio_zero():
    gps = {"1": _gps_entry(60), "2": _gps_entry(120), "3": _gps_entry(6466)}
    h = compute_gps_feed_health(["1", "2", "3"], gps, NOW, 5.0)
    assert h.total_active == 3
    assert h.fresh == 0
    assert h.fresh_ratio == 0.0
    assert h.median_age_min is not None


def test_denominator_is_active_ids_not_gps_dict():
    """REGRESJA GPS-01: plik puchnie starymi wpisami. Denominator MUSI być
    aktywna flota (3), NIE len(gps_dict)=13. 1 fresh z 3 aktywnych = 0.33."""
    gps = {str(i): _gps_entry(9000) for i in range(1, 14)}  # 13 starych wpisów
    gps["1"] = _gps_entry(2)  # tylko 1 świeży
    active = ["1", "2", "3"]  # aktywna flota = 3
    h = compute_gps_feed_health(active, gps, NOW, 5.0)
    assert h.total_active == 3, "denominator = active_ids, nie len(gps_dict)=13"
    assert h.fresh == 1
    assert abs(h.fresh_ratio - (1 / 3)) < 1e-9


def test_missing_gps_entry_not_fresh():
    gps = {"1": _gps_entry(1)}  # kurier 2,3 brak wpisu
    h = compute_gps_feed_health(["1", "2", "3"], gps, NOW, 5.0)
    assert h.fresh == 1
    assert abs(h.fresh_ratio - (1 / 3)) < 1e-9


def test_unparsable_timestamp_not_fresh():
    gps = {"1": {"lat": 1, "lon": 2, "timestamp": "GARBAGE"}, "2": _gps_entry(1)}
    h = compute_gps_feed_health(["1", "2"], gps, NOW, 5.0)
    assert h.fresh == 1
    assert h.fresh_ratio == 0.5


def test_empty_active_fleet_ratio_one_neutral():
    """Brak aktywnej floty (noc) → fresh_ratio=1.0 neutralne (nie alarmuj)."""
    h = compute_gps_feed_health([], {"1": _gps_entry(9000)}, NOW, 5.0)
    assert h.total_active == 0
    assert h.fresh_ratio == 1.0
    assert h.median_age_min is None


def test_active_ids_dedup_and_blank_filtered():
    gps = {"1": _gps_entry(1)}
    h = compute_gps_feed_health(["1", "1", "", None], gps, NOW, 5.0)
    assert h.total_active == 1


def test_future_timestamp_clamped_zero_age():
    gps = {"1": {"lat": 1, "lon": 2, "timestamp": _ts(-3)}}  # 3 min w przyszłości
    h = compute_gps_feed_health(["1"], gps, NOW, 5.0)
    assert h.fresh == 1
    assert h.median_age_min == 0.0


def test_z_suffix_timestamp_parsed():
    z_ts = (NOW - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    gps = {"1": {"lat": 1, "lon": 2, "timestamp": z_ts}}
    h = compute_gps_feed_health(["1"], gps, NOW, 5.0)
    assert h.fresh == 1


# ============== GROUP B: evaluate_gps_feed_alert ==============

def _degraded_health(ratio=0.0, total=5):
    fresh = int(round(ratio * total))
    return GpsFeedHealth(total_active=total, fresh=fresh, fresh_ratio=ratio, median_age_min=120.0)


def _healthy_health():
    return GpsFeedHealth(total_active=5, fresh=5, fresh_ratio=1.0, median_age_min=2.0)


def test_enter_requires_sustain_cycles():
    cfg = _cfg(sustain_cycles=2)
    st = GpsFeedAlertState()
    emit, kind, st = evaluate_gps_feed_alert(st, _degraded_health(), 1000.0, cfg)
    assert emit is False and kind is None and st.streak == 1
    emit, kind, st = evaluate_gps_feed_alert(st, _degraded_health(), 1060.0, cfg)
    assert emit is True and kind == "ENTER" and st.alert_sent is True


def test_no_alert_when_fleet_below_min_active():
    """total_active < min_active_fleet → NIE degraded (1 kurier offline != feed down)."""
    cfg = _cfg(min_active_fleet=3, sustain_cycles=1)
    h = GpsFeedHealth(total_active=2, fresh=0, fresh_ratio=0.0, median_age_min=99.0)
    st = GpsFeedAlertState()
    emit, kind, st = evaluate_gps_feed_alert(st, h, 1000.0, cfg)
    assert emit is False and st.streak == 0


def test_sustained_realert_after_interval():
    cfg = _cfg(sustain_cycles=2, realert_interval_sec=1800)
    st = GpsFeedAlertState()
    _, _, st = evaluate_gps_feed_alert(st, _degraded_health(), 1000.0, cfg)
    emit, kind, st = evaluate_gps_feed_alert(st, _degraded_health(), 1060.0, cfg)
    assert kind == "ENTER"
    # przed cooldown: NO-OP
    emit, kind, st = evaluate_gps_feed_alert(st, _degraded_health(), 1120.0, cfg)
    assert emit is False
    # po cooldown: SUSTAINED
    emit, kind, st = evaluate_gps_feed_alert(st, _degraded_health(), 1060.0 + 1900.0, cfg)
    assert emit is True and kind == "SUSTAINED"


def test_recovery_resets_latch():
    cfg = _cfg(sustain_cycles=1)
    st = GpsFeedAlertState()
    emit, kind, st = evaluate_gps_feed_alert(st, _degraded_health(), 1000.0, cfg)
    assert kind == "ENTER" and st.alert_sent
    emit, kind, st = evaluate_gps_feed_alert(st, _healthy_health(), 1060.0, cfg)
    assert emit is True and kind == "RECOVERY"
    assert st.alert_sent is False and st.streak == 0


def test_recovery_precedence_over_enter():
    """Latched + healthy → RECOVERY nawet gdyby streak był wysoki."""
    cfg = _cfg(sustain_cycles=2)
    st = GpsFeedAlertState(alert_sent=True, streak=5, last_alert_ts=900.0, first_alert_ts=900.0)
    emit, kind, st = evaluate_gps_feed_alert(st, _healthy_health(), 1000.0, cfg)
    assert kind == "RECOVERY"


def test_streak_resets_on_recovery_blip():
    cfg = _cfg(sustain_cycles=3)
    st = GpsFeedAlertState()
    _, _, st = evaluate_gps_feed_alert(st, _degraded_health(), 1000.0, cfg)
    assert st.streak == 1
    _, _, st = evaluate_gps_feed_alert(st, _healthy_health(), 1060.0, cfg)
    assert st.streak == 0


# ============== GROUP C: config ==============

def test_from_flags_inert_by_default():
    """REGRESJA: gdy flaga brak → enabled=False (INERT). GPS celowo off teraz."""
    cfg = GpsFeedAlertConfig.from_flags(lambda name, default: default)
    assert cfg.enabled is False
    assert cfg.shadow_only is True
    assert cfg.min_fresh_ratio == 0.30
    assert cfg.sustain_cycles == 2


def test_from_flags_reads_overrides():
    flags = {"GPS_FEED_ALERT_ENABLED": True, "GPS_FEED_MIN_FRESH_RATIO": 0.5,
             "GPS_FEED_SUSTAIN_CYCLES": 4}
    cfg = GpsFeedAlertConfig.from_flags(lambda name, default: flags.get(name, default))
    assert cfg.enabled is True
    assert cfg.min_fresh_ratio == 0.5
    assert cfg.sustain_cycles == 4


def test_from_env_inert_by_default(monkeypatch):
    for k in ("GPS_FEED_ALERT_ENABLED", "GPS_FEED_ALERT_SHADOW_ONLY"):
        monkeypatch.delenv(k, raising=False)
    cfg = GpsFeedAlertConfig.from_env()
    assert cfg.enabled is False and cfg.shadow_only is True


# ============== GROUP D: render ==============

def test_render_enter_has_ratio_and_action():
    msg = render_gps_feed_message("ENTER", _degraded_health(ratio=0.2, total=5),
                                  GpsFeedAlertState(streak=2), _cfg(), 1000.0)
    assert "ENTER" in msg
    assert "PWA" in msg or "gps_server" in msg


def test_render_recovery():
    msg = render_gps_feed_message("RECOVERY", _healthy_health(),
                                  GpsFeedAlertState(first_alert_ts=900.0), _cfg(), 1900.0)
    assert "RECOVERED" in msg


# ============== GROUP E: append_gps_feed_log ==============

def test_append_log_writes_jsonl(tmp_path):
    p = tmp_path / "gps_feed_eval.jsonl"
    h = _degraded_health()
    sb = GpsFeedAlertState()
    sa = GpsFeedAlertState(alert_sent=True, streak=2)
    append_gps_feed_log(h, sb, sa, True, "ENTER", _cfg(), 1000.0, log_path=p)
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["emit"] is True and rec["kind"] == "ENTER"
    assert rec["health"]["total_active"] == 5
    assert rec["enabled"] is True


def test_append_log_defensive_on_bad_path():
    """Log fail NIE rzuca (hot path safety)."""
    from pathlib import Path
    bad = Path("/proc/nonexistent_dir/x.jsonl")
    append_gps_feed_log(_degraded_health(), GpsFeedAlertState(), GpsFeedAlertState(),
                        False, None, _cfg(), 1000.0, log_path=bad)  # brak wyjątku


# ============== GROUP F: end-to-end mass-stale scenario ==============

def test_mass_stale_feed_triggers_enter_after_sustain():
    """Scenariusz audytu: PWA down, wszystkie 13 wpisów stale (median ~6466 min),
    aktywna flota 4 → fresh_ratio=0.0 → ENTER po 2 cyklach."""
    gps = {str(i): _gps_entry(6466) for i in range(1, 14)}
    active = ["1", "2", "3", "4"]
    cfg = _cfg(sustain_cycles=2, min_active_fleet=3, min_fresh_ratio=0.30)
    st = GpsFeedAlertState()
    t = 1000.0
    kinds = []
    for _ in range(2):
        h = compute_gps_feed_health(active, gps, NOW, cfg.fresh_cutoff_min)
        assert h.fresh_ratio == 0.0
        emit, kind, st = evaluate_gps_feed_alert(st, h, t, cfg)
        kinds.append(kind)
        t += 60.0
    assert kinds[-1] == "ENTER"
