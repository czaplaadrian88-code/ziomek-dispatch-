"""Testy SLA join + kontraktu compute_on_time (track A1/A4).

Pokrycie:
  * compute_on_time: poprawny on_time z syntetycznych eventów delivered
  * przypadek braku pickup_ready_at → grace=True, on_time=None
  * próg 35 min: dokładnie 35 → on_time; 35.1 → late
  * ujemny delivery_time (delivered < ready) → flaga data-quality, on_time=True
  * normalizacja stref: naiwny timestamp traktowany jako UTC; mieszane offsety
  * worker: idempotencja (dwa biegi nie dublują rekordów), pokrycie, peak/off-peak
"""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tools import ontime_lib
from tools import sla_join_worker


# --------------------------------------------------------------------------- #
# compute_on_time                                                             #
# --------------------------------------------------------------------------- #
def test_on_time_basic_delivered_within_35():
    dec = {"480001": {"pickup_ready_at": "2026-06-15T10:00:00+00:00"}}
    deliv = {"480001": {"delivered_at": "2026-06-15T10:20:00+00:00",
                        "status": "delivered", "courier_id": "457"}}
    r = ontime_lib.compute_on_time("480001", dec, deliv)
    assert r["delivery_time_minutes"] == pytest.approx(20.0)
    assert r["on_time"] is True
    assert r["grace"] is False
    assert r["status"] == "delivered"
    assert r["courier_id"] == "457"
    assert r["reason"] is None


def test_on_time_late_over_35():
    dec = {"480002": {"pickup_ready_at": "2026-06-15T10:00:00+00:00"}}
    deliv = {"480002": {"delivered_at": "2026-06-15T10:50:00+00:00"}}
    r = ontime_lib.compute_on_time("480002", dec, deliv)
    assert r["delivery_time_minutes"] == pytest.approx(50.0)
    assert r["on_time"] is False
    assert r["grace"] is False


def test_on_time_exact_threshold_is_on_time():
    # dokładnie 35.0 min → on_time (próg <=)
    dec = {"480003": {"pickup_ready_at": "2026-06-15T10:00:00+00:00"}}
    deliv = {"480003": {"delivered_at": "2026-06-15T10:35:00+00:00"}}
    r = ontime_lib.compute_on_time("480003", dec, deliv)
    assert r["delivery_time_minutes"] == pytest.approx(35.0)
    assert r["on_time"] is True


def test_on_time_just_over_threshold_is_late():
    dec = {"480004": {"pickup_ready_at": "2026-06-15T10:00:00+00:00"}}
    deliv = {"480004": {"delivered_at": "2026-06-15T10:35:06+00:00"}}  # +35.1 min
    r = ontime_lib.compute_on_time("480004", dec, deliv)
    assert r["delivery_time_minutes"] > 35.0
    assert r["on_time"] is False


def test_grace_when_no_pickup_ready():
    # brak ready_at w decyzji → grace, on_time None, nie liczy się jako breach
    dec = {}  # brak rekordu decyzji
    deliv = {"480005": {"delivered_at": "2026-06-15T10:20:00+00:00"}}
    r = ontime_lib.compute_on_time("480005", dec, deliv)
    assert r["grace"] is True
    assert r["on_time"] is None
    assert r["reason"] == "grace_no_ready"
    assert r["delivered_at"] is not None  # dostawę znamy


def test_grace_when_ready_explicit_none():
    dec = {"480006": {"pickup_ready_at": None}}
    deliv = {"480006": {"delivered_at": "2026-06-15T10:20:00+00:00"}}
    r = ontime_lib.compute_on_time("480006", dec, deliv)
    assert r["grace"] is True
    assert r["on_time"] is None


def test_no_delivery_record():
    dec = {"480007": {"pickup_ready_at": "2026-06-15T10:00:00+00:00"}}
    deliv = {}
    r = ontime_lib.compute_on_time("480007", dec, deliv)
    assert r["on_time"] is None
    assert r["grace"] is False
    assert r["reason"] == "no_delivery"


def test_no_delivery_when_delivered_ts_missing():
    dec = {"480008": {"pickup_ready_at": "2026-06-15T10:00:00+00:00"}}
    deliv = {"480008": {"delivered_at": None, "status": "picked_up"}}
    r = ontime_lib.compute_on_time("480008", dec, deliv)
    assert r["reason"] == "no_delivery"
    assert r["on_time"] is None


def test_negative_delivery_time_flagged_but_on_time():
    # delivered PRZED ready (artefakt declared-ready / prep-bias) → DQ flag,
    # ale on_time liczone normalnie (ujemny < 35 → True)
    dec = {"480009": {"pickup_ready_at": "2026-06-15T10:10:00+00:00"}}
    deliv = {"480009": {"delivered_at": "2026-06-15T10:00:00+00:00"}}
    r = ontime_lib.compute_on_time("480009", dec, deliv)
    assert r["delivery_time_minutes"] == pytest.approx(-10.0)
    assert r["on_time"] is True
    assert r["reason"] == "negative_delivery_time"


def test_order_id_coerced_to_str():
    dec = {"480010": {"pickup_ready_at": "2026-06-15T10:00:00+00:00"}}
    deliv = {"480010": {"delivered_at": "2026-06-15T10:20:00+00:00"}}
    r = ontime_lib.compute_on_time(480010, dec, deliv)  # int wejście
    assert r["order_id"] == "480010"
    assert r["on_time"] is True


# --------------------------------------------------------------------------- #
# Strefy czasowe                                                              #
# --------------------------------------------------------------------------- #
def test_naive_timestamp_treated_as_utc():
    # naiwny (bez offsetu) ready vs aware delivered — różnica liczona spójnie
    dec = {"480011": {"pickup_ready_at": "2026-06-15T10:00:00"}}      # naiwny
    deliv = {"480011": {"delivered_at": "2026-06-15T10:20:00+00:00"}}  # aware
    r = ontime_lib.compute_on_time("480011", dec, deliv)
    assert r["delivery_time_minutes"] == pytest.approx(20.0)


def test_mixed_offsets_difference_is_correct():
    # ready w +02:00 (Warszawa), delivered w UTC — różnica realna = 20 min
    # 12:00+02 == 10:00 UTC; delivered 10:20 UTC → 20 min
    dec = {"480012": {"pickup_ready_at": "2026-06-15T12:00:00+02:00"}}
    deliv = {"480012": {"delivered_at": "2026-06-15T10:20:00+00:00"}}
    r = ontime_lib.compute_on_time("480012", dec, deliv)
    assert r["delivery_time_minutes"] == pytest.approx(20.0)


def test_z_suffix_parsed():
    assert ontime_lib.parse_ts("2026-06-15T10:00:00Z") == \
        datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)


def test_parse_ts_failsoft():
    assert ontime_lib.parse_ts(None) is None
    assert ontime_lib.parse_ts("") is None
    assert ontime_lib.parse_ts("nie-data") is None
    assert ontime_lib.parse_ts("   ") is None


def test_peak_classification_warsaw():
    # 12:30 Warsaw (CEST = +02) w czerwcu → peak.  10:20 UTC = 12:20 Warsaw → peak
    assert ontime_lib.is_peak("2026-06-15T10:20:00+00:00") is True
    # 08:00 UTC = 10:00 Warsaw → off-peak
    assert ontime_lib.is_peak("2026-06-15T08:00:00+00:00") is False
    # 18:00 UTC = 20:00 Warsaw → peak (okno wieczorne)
    assert ontime_lib.is_peak("2026-06-15T18:00:00+00:00") is True


def test_warsaw_dst_winter_vs_summer():
    # styczeń → CET (+1), lipiec → CEST (+2)
    w = ontime_lib.to_warsaw("2026-01-15T12:00:00+00:00")
    assert w.utcoffset().total_seconds() == 3600
    s = ontime_lib.to_warsaw("2026-07-15T12:00:00+00:00")
    assert s.utcoffset().total_seconds() == 7200


# --------------------------------------------------------------------------- #
# build_indices na syntetycznych plikach                                     #
# --------------------------------------------------------------------------- #
def _write_jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_build_decisions_index_toplevel_and_nested(tmp_path):
    older = tmp_path / "learning.1"
    newer = tmp_path / "learning"
    # older: nested w decision
    _write_jsonl(older, [
        {"order_id": "A", "ts": "2026-06-10T08:00:00+00:00",
         "decision": {"pickup_ready_at": "2026-06-10T08:05:00+00:00"}},
    ])
    # newer: top-level, w tym override dla A
    _write_jsonl(newer, [
        {"order_id": "A", "ts": "2026-06-15T08:00:00+00:00",
         "pickup_ready_at": "2026-06-15T08:09:00+00:00"},
        {"order_id": "B", "ts": "2026-06-15T09:00:00+00:00",
         "pickup_ready_at": "2026-06-15T09:09:00+00:00"},
    ])
    idx = ontime_lib.build_decisions_index([str(older), str(newer)])
    # newer nadpisuje older dla A
    assert idx["A"]["pickup_ready_at"] == "2026-06-15T08:09:00+00:00"
    assert idx["B"]["pickup_ready_at"] == "2026-06-15T09:09:00+00:00"


def test_build_deliveries_index_closed_only_and_latest(tmp_path):
    deliv = tmp_path / "backfill"
    _write_jsonl(deliv, [
        # zamknięta dostawa
        {"order_id": "A", "outcome": {"delivered_ts": "2026-06-15T10:20:00+00:00",
                                      "picked_up_ts": "2026-06-15T10:05:00+00:00",
                                      "status": "delivered", "courier_id_final": "457"}},
        # niezamknięta (brak delivered_ts) → pominięta przy closed_only
        {"order_id": "C", "outcome": {"status": "picked_up"}},
        # duplikat A z PÓŹNIEJSZYM delivered → wygrywa
        {"order_id": "A", "outcome": {"delivered_ts": "2026-06-15T10:30:00+00:00",
                                      "status": "delivered", "courier_id_final": "999"}},
    ])
    idx = ontime_lib.build_deliveries_index([str(deliv)])
    assert "C" not in idx
    assert idx["A"]["delivered_at"] == "2026-06-15T10:30:00+00:00"
    assert idx["A"]["courier_id"] == "999"


def test_iter_jsonl_failsoft_on_bad_lines(tmp_path):
    p = tmp_path / "bad.jsonl"
    with open(p, "w") as f:
        f.write('{"ok": 1}\n')
        f.write("to nie json\n")
        f.write("\n")
        f.write('{"ok": 2}\n')
    rows = list(ontime_lib._iter_jsonl(str(p)))
    assert len(rows) == 2


def test_build_indices_missing_files_failsoft():
    dec, deliv = ontime_lib.build_indices(
        decision_paths=["/nie/ma/takiego.jsonl"],
        delivery_paths=["/tez/nie.jsonl"],
    )
    assert dec == {}
    assert deliv == {}


# --------------------------------------------------------------------------- #
# Worker end-to-end: syntetyczne logi → sla_log + idempotencja                #
# --------------------------------------------------------------------------- #
@pytest.fixture
def synthetic_logs(tmp_path):
    """Zwraca (decision_path, delivery_path, out_path) z 4 zamówieniami:
       X1 on-time(20m), X2 late(50m), X3 grace(brak ready), X4 peak on-time(25m)."""
    dec = tmp_path / "learning.jsonl"
    deliv = tmp_path / "backfill.jsonl"
    out = tmp_path / "sla_log.jsonl"
    _write_jsonl(dec, [
        {"order_id": "X1", "ts": "2026-06-15T08:00:00+00:00",
         "pickup_ready_at": "2026-06-15T08:00:00+00:00"},
        {"order_id": "X2", "ts": "2026-06-15T08:00:00+00:00",
         "pickup_ready_at": "2026-06-15T08:00:00+00:00"},
        # X3 celowo bez ready → grace
        {"order_id": "X4", "ts": "2026-06-15T10:00:00+00:00",
         "pickup_ready_at": "2026-06-15T10:30:00+00:00"},  # 10:30 UTC=12:30 Warsaw→peak
    ])
    _write_jsonl(deliv, [
        {"order_id": "X1", "outcome": {"delivered_ts": "2026-06-15T08:20:00+00:00",
                                       "status": "delivered", "courier_id_final": "1"}},
        {"order_id": "X2", "outcome": {"delivered_ts": "2026-06-15T08:50:00+00:00",
                                       "status": "delivered", "courier_id_final": "2"}},
        {"order_id": "X3", "outcome": {"delivered_ts": "2026-06-15T09:20:00+00:00",
                                       "status": "delivered", "courier_id_final": "3"}},
        {"order_id": "X4", "outcome": {"delivered_ts": "2026-06-15T10:55:00+00:00",
                                       "status": "delivered", "courier_id_final": "4"}},
    ])
    return str(dec), str(deliv), str(out)


def test_worker_writes_and_metrics(synthetic_logs):
    dec, deliv, out = synthetic_logs
    # now ustawione tak, by okno 30 dni objęło 2026-06-15
    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    m = sla_join_worker.run(out_path=out, since_days=30,
                            decision_paths=[dec], delivery_paths=[deliv], now=now)
    assert m["n_closed_deliveries"] == 4
    assert m["n_with_ontime_value"] == 3   # X1,X2,X4 (X3 grace)
    assert m["n_grace"] == 1
    # pokrycie: 3 z werdyktem + 1 grace = 4/4 = 100% (incl grace), strict 3/4=75%
    assert m["coverage_incl_grace"] == pytest.approx(1.0)
    assert m["coverage_strict"] == pytest.approx(0.75)
    # on-time: X1(20<=35)True, X2(50)False, X4(25)True → 2/3
    assert m["on_time_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert os.path.exists(out)
    # X4 to peak; X1,X2 off-peak
    assert m["n_peak"] == 1
    assert m["n_offpeak"] == 2


def test_worker_record_contents(synthetic_logs):
    dec, deliv, out = synthetic_logs
    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    sla_join_worker.run(out_path=out, since_days=30,
                        decision_paths=[dec], delivery_paths=[deliv], now=now)
    by_oid = {r["order_id"]: r for r in ontime_lib._iter_jsonl(out)}
    assert by_oid["X1"]["on_time"] is True
    assert by_oid["X1"]["delivery_time_minutes"] == pytest.approx(20.0)
    assert by_oid["X2"]["on_time"] is False
    assert by_oid["X3"]["grace"] is True
    assert by_oid["X3"]["on_time"] is None
    # kontrakt: pola obecne
    assert by_oid["X1"]["sla_threshold_min"] == 35.0
    assert "schema_version" in by_oid["X1"]


def test_worker_idempotent_two_runs(synthetic_logs):
    dec, deliv, out = synthetic_logs
    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    m1 = sla_join_worker.run(out_path=out, since_days=30,
                             decision_paths=[dec], delivery_paths=[deliv], now=now)
    lines1 = list(ontime_lib._iter_jsonl(out))
    m2 = sla_join_worker.run(out_path=out, since_days=30,
                             decision_paths=[dec], delivery_paths=[deliv], now=now)
    lines2 = list(ontime_lib._iter_jsonl(out))
    # ta sama liczba rekordów (bez duplikatów po drugim biegu)
    assert len(lines1) == len(lines2) == 4
    # drugi bieg: nic nowego, nic zaktualizowane (te same delivered_at)
    assert m1["records_written_new"] == 4
    assert m2["records_written_new"] == 0
    assert m2["records_updated"] == 0
    # po order_id unikalność
    oids = [r["order_id"] for r in lines2]
    assert len(oids) == len(set(oids))


def test_worker_idempotent_updates_on_changed_delivery(synthetic_logs, tmp_path):
    dec, deliv, out = synthetic_logs
    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    sla_join_worker.run(out_path=out, since_days=30,
                        decision_paths=[dec], delivery_paths=[deliv], now=now)
    # zmień delivered_ts X1 (np. korekta) → drugi bieg ma zaktualizować, nie dublować
    deliv2 = tmp_path / "backfill2.jsonl"
    _write_jsonl(deliv2, [
        {"order_id": "X1", "outcome": {"delivered_ts": "2026-06-15T08:40:00+00:00",
                                       "status": "delivered", "courier_id_final": "1"}},
        {"order_id": "X2", "outcome": {"delivered_ts": "2026-06-15T08:50:00+00:00",
                                       "status": "delivered", "courier_id_final": "2"}},
        {"order_id": "X3", "outcome": {"delivered_ts": "2026-06-15T09:20:00+00:00",
                                       "status": "delivered", "courier_id_final": "3"}},
        {"order_id": "X4", "outcome": {"delivered_ts": "2026-06-15T10:55:00+00:00",
                                       "status": "delivered", "courier_id_final": "4"}},
    ])
    m2 = sla_join_worker.run(out_path=out, since_days=30,
                             decision_paths=[dec], delivery_paths=[str(deliv2)], now=now)
    lines = list(ontime_lib._iter_jsonl(out))
    assert len(lines) == 4  # nadal 4, bez duplikatu
    assert m2["records_updated"] == 1  # tylko X1
    by_oid = {r["order_id"]: r for r in lines}
    # X1 teraz 40 min → late
    assert by_oid["X1"]["delivery_time_minutes"] == pytest.approx(40.0)
    assert by_oid["X1"]["on_time"] is False


def test_worker_since_days_filters_out_old(synthetic_logs, tmp_path):
    dec, deliv, out = synthetic_logs
    # now daleko w przyszłości → okno 1 dzień NIE obejmie 2026-06-15
    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    m = sla_join_worker.run(out_path=out, since_days=1,
                            decision_paths=[dec], delivery_paths=[deliv], now=now)
    assert m["n_closed_deliveries"] == 0
