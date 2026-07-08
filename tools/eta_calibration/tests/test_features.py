"""Testy krytycznej logiki features: obciążenie, strefy czasu, pseudonimizacja, sloty."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dispatch_v2.tools.eta_calibration import features as F

WARSAW = ZoneInfo("Europe/Warsaw")


def test_pseudonymize_stable_and_no_pii():
    a = F.pseudonymize("523")
    b = F.pseudonymize("523")
    c = F.pseudonymize("999")
    assert a == b                        # deterministyczne
    assert a != c                        # różni kurierzy → różne pseudonimy
    assert a.startswith("KURIER_")
    assert "523" not in a                # brak realnego id w pseudonimie


def test_slot_boundaries():
    assert F.slot_of(10) == "off"
    assert F.slot_of(11) == "peak_lunch"
    assert F.slot_of(13) == "peak_lunch"
    assert F.slot_of(14) == "high_risk"
    assert F.slot_of(17) == "peak_dinner"
    assert F.slot_of(20) == "off"


def test_parse_naive_warsaw_is_utc_aware():
    dt = F.parse_naive_warsaw("2026-06-01 14:00:00")
    assert dt is not None and dt.tzinfo is not None
    # 14:00 Warsaw (lato UTC+2) == 12:00 UTC
    assert dt.astimezone(timezone.utc).hour == 12


def test_czas_kuriera_dt_same_day_warsaw():
    ref = datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc)  # 14:05 Warsaw
    ck = F.czas_kuriera_dt("14:00", ref)
    assert ck is not None
    # czas_kuriera 14:00 Warsaw == 12:00 UTC
    assert ck.astimezone(timezone.utc).hour == 12
    # poślizg realny (pickup 14:05) − obietnica (14:00) = +5 min
    slip = (ref - ck).total_seconds() / 60.0
    assert abs(slip - 5.0) < 0.01


def test_reconstruct_load_overlapping_intervals():
    # kurier ma 3 zlecenia; #2 odbierane gdy #1 jeszcze niedostarczone → load=2
    def row(oid, pu, dl):
        return {"order_id": oid, "courier_id": "C1", "picked_up_at": pu, "delivered_at": dl}
    rows = [
        row("A", "2026-06-01 12:00:00", "2026-06-01 12:20:00"),
        row("B", "2026-06-01 12:10:00", "2026-06-01 12:30:00"),  # odbiór w trakcie A → load 2
        row("C", "2026-06-01 13:00:00", "2026-06-01 13:15:00"),  # osobno → load 1
    ]
    load = F.reconstruct_load(rows)
    assert load["A"] == 1     # w chwili odbioru A nic innego nie niesie
    assert load["B"] == 2     # niesie A gdy odbiera B
    assert load["C"] == 1


def test_reconstruct_load_ignores_bad_intervals():
    rows = [{"order_id": "X", "courier_id": "C", "picked_up_at": None, "delivered_at": "2026-06-01 12:00:00"}]
    assert F.reconstruct_load(rows) == {}
