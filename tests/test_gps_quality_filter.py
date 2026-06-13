"""GPS-02 (audyt 2026-06-10): filtr jakości fixu GPS — accuracy + teleport.

Dwie warstwy:
  A) Czyste funkcje gps_quality.py (deterministyczne, zero I/O).
  B) Integracja w courier_resolver.build_fleet_snapshot:
     - SHADOW (flaga OFF): werdykt logowany, flota BEZ zmian (regresja-guard).
     - ACTIVE (flaga ON): reject → fall-through (fix nie wchodzi jako gps).
     - Brak GPS NIGDY nie jest karany filtrem (korekta Adriana 13.06).

Uruchom:
    PYTHONPATH=/root/_auton_wt/gps02 /root/.openclaw/venvs/dispatch/bin/python \
        -m pytest dispatch_v2/tests/test_gps_quality_filter.py -p no:cacheprovider -q
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import gps_quality as q  # noqa: E402
from dispatch_v2 import courier_resolver as cr  # noqa: E402
from dispatch_v2.common import flag as _real_flag  # noqa: E402

VALID = (53.13, 23.16)          # centrum Białegostoku


# ─────────────────────────── A. CZYSTE FUNKCJE ───────────────────────────

def test_good_fix_clean():
    v = q.assess_gps_quality(VALID, 7.6)
    assert v.accept is True
    assert v.reasons == []
    assert v.low_accuracy is False and v.teleport is False
    assert v.has_accuracy_field is True
    assert v.accuracy_m == 7.6


def test_low_accuracy_rejected():
    v = q.assess_gps_quality(VALID, 300.0)
    assert v.accept is False
    assert v.low_accuracy is True
    assert any("low_accuracy" in r for r in v.reasons)
    assert v.accuracy_m == 300.0


def test_accuracy_at_threshold_boundary_ok():
    # dokładnie próg = NIE odrzucamy (>próg, nie >=)
    v = q.assess_gps_quality(VALID, q.GPS_ACCURACY_MAX_M)
    assert v.accept is True
    assert v.low_accuracy is False


def test_missing_accuracy_is_lenient():
    # Brak pola accuracy NIE jest złym fixem — degraduj łagodnie.
    v = q.assess_gps_quality(VALID, None)
    assert v.accept is True
    assert v.low_accuracy is False
    assert v.has_accuracy_field is False


def test_garbage_accuracy_is_lenient():
    for bad in ("abc", float("nan"), 0, -5):
        v = q.assess_gps_quality(VALID, bad)
        assert v.accept is True, f"{bad!r} powinno być łagodne"
        assert v.low_accuracy is False
        assert v.has_accuracy_field is False


def test_teleport_detected():
    # ~5.4 km w 60 s = ~324 km/h → teleport
    far = (53.13, 23.24)  # ~5.3 km na wschód
    v = q.assess_gps_quality(VALID, 8.0, anchor_pos=far, dt_seconds=60.0, anchor_age_min=1.0)
    assert v.accept is False
    assert v.teleport is True
    assert v.implied_speed_kmh is not None and v.implied_speed_kmh > 120
    assert v.jump_km is not None and v.jump_km > 2.0


def test_small_jitter_not_teleport():
    # ~0.3 km w 1 s — sub-min dt → prędkości NIE liczymy, NIE teleport.
    near = (53.1327, 23.1645)
    v = q.assess_gps_quality(VALID, 8.0, anchor_pos=near, dt_seconds=1.0, anchor_age_min=0.5)
    assert v.accept is True
    assert v.teleport is False
    assert v.implied_speed_kmh is None


def test_moderate_speed_not_teleport():
    # ~1.5 km w 90 s = 60 km/h — realny ruch miejski, NIE teleport.
    near = (53.1325, 23.1810)  # ~1.5 km
    v = q.assess_gps_quality(VALID, 8.0, anchor_pos=near, dt_seconds=90.0, anchor_age_min=1.5)
    assert v.accept is True
    assert v.teleport is False


def test_old_anchor_big_jump_not_teleport():
    # Kotwica >8 min stara — duży skok może być realny (kurier przejechał).
    far = (53.13, 23.30)
    v = q.assess_gps_quality(VALID, 8.0, anchor_pos=far, dt_seconds=700.0, anchor_age_min=12.0)
    assert v.accept is True
    assert v.teleport is False
    # jump policzony (telemetria), ale prędkości nie ma
    assert v.jump_km is not None
    assert v.implied_speed_kmh is None


def test_no_anchor_lenient():
    # Brak poprzedniej pozycji (pierwszy fix) → nie da się ocenić teleportu.
    v = q.assess_gps_quality(VALID, 8.0, anchor_pos=None, dt_seconds=None)
    assert v.accept is True
    assert v.teleport is False


def test_low_accuracy_and_teleport_both():
    far = (53.13, 23.24)
    v = q.assess_gps_quality(VALID, 400.0, anchor_pos=far, dt_seconds=60.0, anchor_age_min=1.0)
    assert v.accept is False
    assert v.low_accuracy is True and v.teleport is True
    assert len(v.reasons) == 2


def test_non_monotone_dt_lenient():
    # dt<=0 (fix "wcześniej" niż kotwica) → nie liczymy prędkości.
    far = (53.13, 23.30)
    v = q.assess_gps_quality(VALID, 8.0, anchor_pos=far, dt_seconds=-30.0, anchor_age_min=1.0)
    assert v.accept is True
    assert v.teleport is False


def test_to_log_dict_shape():
    v = q.assess_gps_quality(VALID, 300.0)
    d = v.to_log_dict()
    for key in ("accept", "reasons", "low_accuracy", "teleport", "accuracy_m",
                "has_accuracy_field", "implied_speed_kmh", "jump_km", "anchor_age_min"):
        assert key in d


# ─────────────── B. INTEGRACJA build_fleet_snapshot ───────────────

def _gps(lat, lon, accuracy=None, age_min=1.0, now=None):
    now = now or datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=age_min)).isoformat()
    rec = {"timestamp": ts, "lat": lat, "lon": lon}
    if accuracy is not None:
        rec["accuracy"] = accuracy
    return {"888": rec}


def _flag_factory(overrides):
    def _f(name, default=False):
        if name in overrides:
            return overrides[name]
        return _real_flag(name, default)
    return _f


def _run(gps, *, flag_overrides=None, store=None, captured=None):
    """Uruchom build_fleet_snapshot z zamockowanymi I/O. Przechwyć shadow log."""
    flag_overrides = flag_overrides or {}
    ctx = [
        mock.patch.object(cr, "_load_kurier_piny", return_value={}),
        mock.patch.object(cr, "_load_courier_names", return_value={"888": "GPS Test"}),
        mock.patch.object(cr, "_load_gps_positions", return_value=gps or {}),
        mock.patch.object(cr, "_load_courier_tiers", return_value={}),
        mock.patch.object(cr, "_load_last_known_pos", return_value=store or {}),
        mock.patch("dispatch_v2.state_machine.get_all", return_value={}),
        mock.patch.object(cr, "flag", side_effect=_flag_factory(flag_overrides)),
    ]
    # Przechwyć shadow log zamiast pisać na dysk
    if captured is not None:
        def _cap(kid, verdict, ts, active, pos, age):
            captured.append((kid, verdict, active))
        ctx.append(mock.patch.object(cr, "_log_gps_quality_shadow", side_effect=_cap))
    for c in ctx:
        c.start()
    try:
        return cr.build_fleet_snapshot()
    finally:
        for c in reversed(ctx):
            c.stop()


def test_shadow_good_fix_trusted_and_logged():
    cap = []
    fleet = _run(_gps(*VALID, accuracy=7.6),
                 flag_overrides={"ENABLE_GPS_QUALITY_SHADOW": True,
                                 "ENABLE_GPS_ACCURACY_TELEPORT_FILTER": False},
                 captured=cap)
    assert fleet["888"].pos_source == "gps"
    assert len(cap) == 1
    kid, verdict, active = cap[0]
    assert verdict.accept is True
    assert active is False  # shadow


def test_shadow_low_accuracy_logged_but_still_trusted():
    # SHADOW: nawet zły werdykt NIE zmienia floty (regresja-guard).
    cap = []
    fleet = _run(_gps(*VALID, accuracy=400.0),
                 flag_overrides={"ENABLE_GPS_QUALITY_SHADOW": True,
                                 "ENABLE_GPS_ACCURACY_TELEPORT_FILTER": False},
                 captured=cap)
    assert fleet["888"].pos_source == "gps", "SHADOW nie może zmienić decyzji floty"
    assert cap and cap[0][1].low_accuracy is True


def test_active_low_accuracy_falls_through_to_no_gps():
    # ACTIVE: zły fix odrzucony → brak bagu/historii → no_gps (NIE (0,0)).
    fleet = _run(_gps(*VALID, accuracy=400.0),
                 flag_overrides={"ENABLE_GPS_QUALITY_SHADOW": True,
                                 "ENABLE_GPS_ACCURACY_TELEPORT_FILTER": True})
    cs = fleet["888"]
    assert cs.pos_source != "gps", "ACTIVE: zły fix nie może wejść jako gps"
    assert cs.pos_source == "no_gps"
    assert tuple(cs.pos) == cr.BIALYSTOK_CENTER


def test_active_good_fix_still_trusted():
    fleet = _run(_gps(*VALID, accuracy=7.6),
                 flag_overrides={"ENABLE_GPS_QUALITY_SHADOW": True,
                                 "ENABLE_GPS_ACCURACY_TELEPORT_FILTER": True})
    assert fleet["888"].pos_source == "gps"


def test_missing_accuracy_not_penalized_even_active():
    # Brak accuracy + ACTIVE → fix nadal zaufany (brak danych ≠ zły fix).
    fleet = _run(_gps(*VALID, accuracy=None),
                 flag_overrides={"ENABLE_GPS_QUALITY_SHADOW": True,
                                 "ENABLE_GPS_ACCURACY_TELEPORT_FILTER": True})
    assert fleet["888"].pos_source == "gps"


def test_no_gps_at_all_never_penalized():
    # Kurier zupełnie bez fixu GPS — filtr NIE może go dotknąć (compute-shadow
    # nie ma czego oceniać). Brak GPS = celowa polityka, NIE awaria.
    cap = []
    fleet = _run({},  # zero GPS
                 flag_overrides={"ENABLE_GPS_QUALITY_SHADOW": True,
                                 "ENABLE_GPS_ACCURACY_TELEPORT_FILTER": True},
                 captured=cap)
    cs = fleet["888"]
    assert cs.pos_source == "no_gps"
    assert tuple(cs.pos) == cr.BIALYSTOK_CENTER
    assert cap == [], "brak fixu → brak werdyktu jakości (nic do oceny)"


def test_active_teleport_falls_through():
    # Świeży fix daleko od kotwicy GPS (poprzedni fix) w krótkim czasie → reject.
    now = datetime.now(timezone.utc)
    # kotwica: poprzedni fix gps 2 min temu, ~5.3 km na wschód
    anchor = {"888": {"lat": 53.13, "lon": 23.24, "source": "gps",
                      "ts": (now - timedelta(minutes=2.0)).isoformat()}}
    # nowy fix: centrum, 1 min temu → dt = 1 min = 60s, jump ~5.3km → ~318 km/h
    fleet = _run(_gps(*VALID, accuracy=8.0, age_min=1.0, now=now),
                 flag_overrides={"ENABLE_GPS_QUALITY_SHADOW": True,
                                 "ENABLE_GPS_ACCURACY_TELEPORT_FILTER": True,
                                 "ENABLE_COURIER_LAST_KNOWN_POS": False},
                 store=anchor)
    cs = fleet["888"]
    assert cs.pos_source != "gps", f"teleport powinien być odrzucony, jest {cs.pos_source}"


def test_anchor_only_from_gps_source():
    # Kotwica z innego źródła niż gps (np. last_delivered) NIE liczy się do
    # teleportu — geometrycznie grubsza, dałaby false-positive.
    now = datetime.now(timezone.utc)
    anchor = {"888": {"lat": 53.13, "lon": 23.24, "source": "last_delivered",
                      "ts": (now - timedelta(minutes=2.0)).isoformat()}}
    a_pos, dt, age = cr._gps_quality_anchor(anchor["888"], 1.0, now)
    assert a_pos is None and dt is None


def test_shadow_log_blocked_on_prod_path_under_pytest(tmp_path):
    # Lekcja #176: pod pytest shadow log NIE pisze do PRODUKCYJNEJ ścieżki
    # (testowe cid by ją zatruły). Test patchujący ścieżkę na tmp — pisze.
    import os

    class _V:
        def to_log_dict(self):
            return {"accept": True, "reasons": []}

    # 1. domyślna (prod) ścieżka pod pytest → NO-OP (plik nie powstaje)
    assert "PYTEST_CURRENT_TEST" in os.environ
    cr._log_gps_quality_shadow("999", _V(), "2026-06-13T00:00:00+00:00",
                               False, (53.13, 23.16), 1.0)
    assert not os.path.exists(cr.GPS_QUALITY_SHADOW_LOG_PATH), \
        "shadow log NIE może pisać do prod ścieżki pod pytest (lekcja #176)"

    # 2. ścieżka patchnięta na tmp → pisze (round-trip dozwolony)
    tmp_log = tmp_path / "gps_quality_shadow.jsonl"
    with mock.patch.object(cr, "GPS_QUALITY_SHADOW_LOG_PATH", str(tmp_log)):
        cr._log_gps_quality_shadow("999", _V(), "2026-06-13T00:00:00+00:00",
                                   False, (53.13, 23.16), 1.0)
    assert tmp_log.exists() and tmp_log.read_text().strip(), \
        "shadow log na tmp powinien pisać normalnie"


def test_anchor_helper_computes_dt():
    now = datetime.now(timezone.utc)
    entry = {"lat": 53.13, "lon": 23.24, "source": "gps",
             "ts": (now - timedelta(minutes=2.0)).isoformat()}
    a_pos, dt, age = cr._gps_quality_anchor(entry, 0.5, now)
    assert a_pos == (53.13, 23.24)
    # dt = (anchor_age 2.0 - new_age 0.5)*60 = 90s
    assert abs(dt - 90.0) < 1.0
    assert abs(age - 2.0) < 0.05
