"""Testy tools/ledger_io.py — READ-side kanon ledgerów (audyt spójności L0.3).

Wszystko na fikstury tmp_path + monkeypatch ścieżek LEDGER (żywych plików NIE
dotykamy). Czasy kotwiczone w `datetime.now(UTC)` — pliki pisane w teście mają
mtime≈teraz, więc zrotowane siblingi są „w oknie" względem cutoffów z przeszłości
(spójne z pruningiem po mtime w _rotated_logs).
"""
import gzip
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2.tools import ledger_io as lio

UTC = timezone.utc


def _iso(dt):
    return dt.isoformat()


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_jsonl_gz(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _shadow(oid, ts, **extra):
    d = {"order_id": oid, "ts": _iso(ts)}
    d.update(extra)
    return d


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Przekieruj wszystkie ścieżki LEDGER na tmp_path."""
    paths = {
        "shadow": str(tmp_path / "shadow_decisions.jsonl"),
        "sla": str(tmp_path / "sla_log.jsonl"),
        "gps_truth": str(tmp_path / "gps_delivery_truth.jsonl"),
        "outcomes": str(tmp_path / "decision_outcomes.jsonl"),
        "dwell": str(tmp_path / "restaurant_dwell.json"),
    }
    for k, v in paths.items():
        monkeypatch.setitem(lio.LEDGER, k, v)
    return paths


# ── iter_shadow_decisions: rotacja + cutoff + kanonizacja ───────────────────
def test_iter_shadow_rotation_reads_all_chronological(ledger):
    now = datetime.now(UTC)
    _write_jsonl_gz(ledger["shadow"] + ".2.gz", [_shadow("1", now - timedelta(hours=3))])
    _write_jsonl(ledger["shadow"] + ".1", [_shadow("2", now - timedelta(hours=2))])
    _write_jsonl(ledger["shadow"], [_shadow("3", now - timedelta(hours=1))])

    got = [r["order_id"] for r in lio.iter_shadow_decisions(now - timedelta(hours=4))]
    assert got == ["1", "2", "3"]  # zrotowane .2.gz/.1 doczytane, kolejność chronologiczna


def test_iter_shadow_cutoff_filters_per_record(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"], [
        _shadow("old", now - timedelta(minutes=40)),
        _shadow("new", now - timedelta(minutes=5)),
    ])
    got = [r["order_id"] for r in lio.iter_shadow_decisions(now - timedelta(minutes=20))]
    assert got == ["new"]


def test_iter_shadow_observations_are_opt_in_for_decision_consumers(ledger):
    now = datetime.now(UTC)
    observation = _shadow(
        "obs",
        now,
        decision_kind="lifecycle_observation",
        record_type="CZASOWKA_RECLAIM_EVALUATION",
    )
    _write_jsonl(ledger["shadow"], [_shadow("decision", now), observation])

    default = [r["order_id"] for r in lio.iter_shadow_decisions(None)]
    all_records = [
        r["order_id"]
        for r in lio.iter_shadow_decisions(None, include_observations=True)
    ]
    assert default == ["decision"]
    assert all_records == ["decision", "obs"]


def test_iter_shadow_canonicalizes_oid_to_str(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"], [{"order_id": 483655, "ts": _iso(now - timedelta(minutes=1))}])
    r = next(iter(lio.iter_shadow_decisions(now - timedelta(hours=1))))
    assert r["order_id"] == "483655" and isinstance(r["order_id"], str)


def test_iter_shadow_old_rotated_pruned_by_mtime(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"], [_shadow("live", now - timedelta(minutes=5))])
    old = ledger["shadow"] + ".2"
    _write_jsonl(old, [_shadow("old", now - timedelta(days=10))])
    old_mtime = time.time() - 10 * 86400  # rotacja 10 dni temu → poza oknem
    os.utime(old, (old_mtime, old_mtime))

    got = [r["order_id"] for r in lio.iter_shadow_decisions(now - timedelta(days=3))]
    assert got == ["live"]


# ── max_bytes: ogon == pełna ścieżka dla świeżego okna ──────────────────────
def _many_shadow(now, n=400):
    # i rosnące → ts rosnące (i=0 najstarszy now-n s, i=n-1 najświeższy now-1s)
    return [_shadow(str(i), now - timedelta(seconds=(n - i))) for i in range(n)]


def test_maxbytes_tail_equals_full_fresh_window(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"], _many_shadow(now, 400))
    size = os.path.getsize(ledger["shadow"])
    assert size > 4000  # plik większy niż max_bytes → ogon ma sens
    cutoff = now - timedelta(seconds=20)  # świeże okno: ostatnie ~20 rekordów

    full = [r["order_id"] for r in lio.iter_shadow_decisions(cutoff)]
    tail = [r["order_id"] for r in lio.iter_shadow_decisions(cutoff, max_bytes=size // 2)]
    assert tail == full and len(full) > 0


def test_read_live_tail_authoritative_matches_full(ledger):
    # Bezpośrednio: gdy brak zrotowanych, mtime świeży, cięcie przed cutoffem →
    # _read_live_tail zwraca autorytatywną listę identyczną z pełną ścieżką.
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"], _many_shadow(now, 400))
    size = os.path.getsize(ledger["shadow"])
    cutoff = now - timedelta(seconds=20)

    tail_direct = lio._read_live_tail(ledger["shadow"], cutoff, size // 2)
    assert tail_direct is not None  # ogon autorytatywny (nie fallback)
    full = [r["order_id"] for r in lio.iter_shadow_decisions(cutoff)]
    assert [r["order_id"] for r in tail_direct] == full


def test_maxbytes_falls_back_when_rotated_in_window(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"] + ".1", [_shadow("rot", now - timedelta(minutes=50))])
    _write_jsonl(ledger["shadow"], _many_shadow(now, 400))
    size = os.path.getsize(ledger["shadow"])
    cutoff = now - timedelta(hours=1)  # obejmuje zrotowany .1 (mtime≈teraz ≥ cutoff)

    full = [lio._oid_str(r) for r in lio.iter_shadow_decisions(cutoff)]
    tail = [lio._oid_str(r) for r in lio.iter_shadow_decisions(cutoff, max_bytes=size // 2)]
    assert "rot" in full          # pełna ścieżka widzi zrotowany rekord
    assert tail == full           # ogon MUSI się cofnąć do pełnej ścieżki


def test_maxbytes_small_live_uses_full_path(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"], [_shadow("a", now - timedelta(minutes=1))])
    size = os.path.getsize(ledger["shadow"])
    # max_bytes > rozmiar pliku → brak oszczędności → pełna ścieżka; wynik ten sam
    got = [r["order_id"] for r in lio.iter_shadow_decisions(now - timedelta(hours=1),
                                                            max_bytes=size + 10_000)]
    assert got == ["a"]


# ── iter_sla: rotacja + cutoff + kanonizacja (L1.2) ─────────────────────────
def _sla(oid, ts, **extra):
    d = {"order_id": oid, "logged_at": _iso(ts)}
    d.update(extra)
    return d


def test_iter_sla_rotation_reads_all_chronological(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["sla"] + ".1", [_sla("1", now - timedelta(hours=2))])
    _write_jsonl(ledger["sla"], [_sla("2", now - timedelta(hours=1))])
    got = [r["order_id"] for r in lio.iter_sla(now - timedelta(hours=3))]
    assert got == ["1", "2"]  # zrotowany .1 doczytany, kolejność chronologiczna


def test_iter_sla_cutoff_filters_per_record_and_canonizes(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["sla"], [
        _sla(100, now - timedelta(minutes=40)),
        _sla(200, now - timedelta(minutes=5), picked_up_at=_iso(now - timedelta(minutes=6))),
    ])
    got = list(lio.iter_sla(now - timedelta(minutes=20)))
    assert [r["order_id"] for r in got] == ["200"]  # cutoff per-rekord + oid→str
    assert got[0]["picked_up_at"]  # pola rekordu zachowane


def test_iter_sla_no_cutoff_reads_everything(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["sla"], [_sla("a", now - timedelta(days=30)), _sla("b", now)])
    got = [r["order_id"] for r in lio.iter_sla(None)]
    assert got == ["a", "b"]  # bez cutoffu nic nie odfiltrowane


def test_parse_sla_ts_naive_is_warsaw_not_utc():
    # Semantyka writera (sla_tracker/panel Rutcom): naive stemple = Warszawa.
    # Lipiec = CEST (+2): "22:48" Warsaw == 20:48 UTC. Parsowanie naive jako UTC
    # (stary odczyt pod martwy log) dawało +2h błędu joinu (near-miss L1.2).
    dt = lio.parse_sla_ts("2026-07-01 22:48:49")
    assert dt == datetime(2026, 7, 1, 20, 48, 49, tzinfo=UTC)
    # zima = CET (+1)
    dt_w = lio.parse_sla_ts("2026-01-15 10:00:00")
    assert dt_w == datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC)


def test_parse_sla_ts_aware_passthrough_to_utc():
    dt = lio.parse_sla_ts("2026-06-19T21:32:22.530167+00:00")  # format martwego loga
    assert dt == datetime(2026, 6, 19, 21, 32, 22, 530167, tzinfo=UTC)
    dt2 = lio.parse_sla_ts("2026-07-01T12:00:00Z")
    assert dt2 == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert lio.parse_sla_ts(None) is None and lio.parse_sla_ts("") is None


def test_iter_sla_points_at_live_scripts_logs_not_dead_state():
    # WRONG-SOURCE guard (L1.2): kanon sla = ŻYWY scripts/logs/sla_log.jsonl,
    # NIE martwy dispatch_state/sla_log.jsonl (zamrożony 2026-06-20).
    assert lio.LEDGER["sla"] == "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"


# ── loadery prawdy: klucz str(oid), cutoff, całościowy dwell ────────────────
def test_load_gps_truth_keyed_str_and_cutoff(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["gps_truth"], [
        {"order_id": 111, "confidence": "high", "button_delivered_at": _iso(now - timedelta(minutes=5))},
        {"order_id": 222, "confidence": "low", "button_delivered_at": _iso(now - timedelta(hours=3))},
    ])
    allg = lio.load_gps_truth()
    assert set(allg) == {"111", "222"} and allg["111"]["confidence"] == "high"
    assert all(isinstance(k, str) for k in allg)
    recent = lio.load_gps_truth(now - timedelta(minutes=30))
    assert set(recent) == {"111"}  # 222 odfiltrowany (button 3h temu)


def test_load_outcomes_keyed_str_and_cutoff(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["outcomes"], [
        {"order_id": "482945", "written_at": _iso(now - timedelta(minutes=5))},
        {"order_id": "480000", "written_at": _iso(now - timedelta(hours=6))},
    ])
    allo = lio.load_outcomes()
    assert set(allo) == {"482945", "480000"}
    recent = lio.load_outcomes(now - timedelta(hours=1))
    assert set(recent) == {"482945"}


def test_load_outcomes_last_wins(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["outcomes"], [
        {"order_id": "500", "written_at": _iso(now - timedelta(hours=2)), "v": 1},
        {"order_id": "500", "written_at": _iso(now - timedelta(minutes=1)), "v": 2},
    ])
    allo = lio.load_outcomes()
    assert allo["500"]["v"] == 2  # najświeższy rekord nadpisuje


def test_load_restaurant_dwell_str_keys(ledger):
    with open(ledger["dwell"], "w", encoding="utf-8") as f:
        json.dump({"479319": {"_source": "gps", "arrived_at_restaurant": "x"},
                   "479332": {"_source": "plan"}}, f)
    dw = lio.load_restaurant_dwell()
    assert set(dw) == {"479319", "479332"}
    assert all(isinstance(k, str) for k in dw)
    assert dw["479319"]["_source"] == "gps"


def test_load_restaurant_dwell_missing_returns_empty(ledger):
    assert lio.load_restaurant_dwell() == {}  # brak pliku → {}, nie wyjątek


# ── join: etykiety physical / proxy / none (+ paczka 900*) ───────────────────
def test_join_labels_physical_proxy_none(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"], [
        _shadow("A", now - timedelta(minutes=30)),
        _shadow("B", now - timedelta(minutes=29)),
        _shadow("900123", now - timedelta(minutes=28)),  # paczka: brak w OBU prawdach
    ])
    _write_jsonl(ledger["gps_truth"], [
        {"order_id": "A", "confidence": "high", "button_delivered_at": _iso(now)},
    ])
    _write_jsonl(ledger["outcomes"], [
        {"order_id": "A", "written_at": _iso(now)},  # A jest i w gps, i w outcomes
        {"order_id": "B", "written_at": _iso(now)},
    ])
    rows = {lio._oid_str(r): r for r in lio.join_decisions_with_truth(now - timedelta(hours=1))}

    assert rows["A"]["truth_source"] == lio.TRUTH_PHYSICAL   # fizyka bije proxy
    assert rows["A"]["truth_confidence"] == "high"
    assert rows["A"]["physical"] is not None and rows["A"]["proxy"] is not None

    assert rows["B"]["truth_source"] == lio.TRUTH_PROXY
    assert rows["B"]["truth_confidence"] is None
    assert rows["B"]["physical"] is None and rows["B"]["proxy"] is not None

    assert rows["900123"]["truth_source"] == lio.TRUTH_NONE  # paczka: NIGDY „brak = zgoda"
    assert rows["900123"]["physical"] is None and rows["900123"]["proxy"] is None


def test_join_preserves_decision_fields(ledger):
    now = datetime.now(UTC)
    _write_jsonl(ledger["shadow"], [_shadow("Z", now - timedelta(minutes=5), verdict="PROPOSE", latency_ms=42)])
    row = next(iter(lio.join_decisions_with_truth(now - timedelta(hours=1))))
    assert row["verdict"] == "PROPOSE" and row["latency_ms"] == 42  # pola decyzji zachowane


# ── coverage guard / label / header ─────────────────────────────────────────
def test_require_join_coverage_raises_and_passes(ledger):
    rows = [{"truth_source": lio.TRUTH_NONE}, {"truth_source": lio.TRUTH_PROXY}]
    with pytest.raises(lio.LedgerCoverageError):
        lio.require_join_coverage(rows, min_physical=1)

    rows_ok = rows + [{"truth_source": lio.TRUTH_PHYSICAL}]
    lio.require_join_coverage(rows_ok, min_physical=1)  # nie rzuca

    with pytest.raises(lio.LedgerCoverageError):
        lio.require_join_coverage(rows_ok, min_physical=2)  # za mało fizyki na wyższą poprzeczkę


def test_label_verdict_thresholds():
    assert lio.label_verdict(1, 0) == lio.VERDICT_GROUND_TRUTH
    assert lio.label_verdict(5, 10) == lio.VERDICT_GROUND_TRUTH
    assert lio.label_verdict(0, 1) == lio.VERDICT_PROXY_CERTIFIED
    assert lio.label_verdict(0, 0) == lio.VERDICT_VOID


def test_verdict_txt_header_format():
    since = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    until = datetime(2026, 7, 3, 0, 0, tzinfo=UTC)
    stale = datetime(2026, 7, 3, 6, 0, tzinfo=UTC)
    h = lio.verdict_txt_header(window_since=since, window_until=until,
                               n_physical=3, n_proxy=40, stale_after=stale)
    assert h.startswith("# ledger_io verdict | generated=")
    assert "window=2026-07-01T00:00:00+00:00..2026-07-03T00:00:00+00:00" in h
    assert "truth=phys 3/proxy 40" in h
    assert "stale_after=2026-07-03T06:00:00+00:00" in h
    assert "\n" not in h  # dokładnie jedna linia
