"""ETAP 6 (Z-19, 2026-06-10): naruszenia kontraktu restauracji ±5 min.

Testy formuły wait_min = real_pickup − max(commit, przyjazd) > 5 → JEDEN
wpis per oid do restaurant_violations.jsonl. Detektor: sla_tracker.
_check_restaurant_violations (wzorzec skanu R6 BAG_TIME + seen-flag).

Scenariusze wg planu ETAP 6:
- commit-only fallback (brak waiting_at) → arrival_source=commit_fallback
- waiting_at (status4) wcześniejszy od commitu → anchor=commit
- waiting_at późniejszy od commitu → anchor=waiting_at (kurier spóźniony,
  restauracji nie liczymy czasu zanim kurier był na miejscu)
- brak commitu → skip
- paczka (address_id ∈ PACZKA_ADDRESS_IDS) → skip
- TZ: picked_up_at naive Warsaw vs czas_kuriera_warsaw aware (+02:00)
- dedup: drugi skan nie dubluje wpisu
- flaga ENABLE_RESTAURANT_VIOLATIONS=False → no-op
"""
import json

import pytest

from dispatch_v2 import sla_tracker


def _order(oid="479001", **over):
    o = {
        "order_id": oid,
        "status": "picked_up",
        "restaurant": "Mama Thai",
        "courier_id": "370",
        "order_type": "elastyk",
        "address_id": "149",
        # commit 12:00 Warsaw (aware ISO, jak w orders_state)
        "czas_kuriera_warsaw": "2026-06-10T12:00:00+02:00",
        # realny odbiór 12:12 Warsaw (naive panel format, jak w orders_state)
        "picked_up_at": "2026-06-10 12:12:00",
    }
    o.update(over)
    return o


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Izolacja: orders in-memory, plik violations w tmp, flaga ON."""
    state = {"orders": [], "upserts": []}
    vpath = tmp_path / "restaurant_violations.jsonl"

    def fake_get_by_status(status):
        return [o for o in state["orders"] if o.get("status") == status]

    def fake_upsert(oid, data, event=None):
        state["upserts"].append((oid, data, event))
        for o in state["orders"]:
            if o.get("order_id") == oid:
                o.update(data)
        return {}

    monkeypatch.setattr(sla_tracker, "get_by_status", fake_get_by_status)
    monkeypatch.setattr(sla_tracker, "upsert_order", fake_upsert)
    monkeypatch.setattr(sla_tracker, "RESTAURANT_VIOLATIONS_PATH", vpath)
    monkeypatch.setattr(
        sla_tracker.C, "flag",
        lambda name, default=False: True
        if name == "ENABLE_RESTAURANT_VIOLATIONS" else default,
    )
    state["path"] = vpath
    return state


def _entries(harness):
    p = harness["path"]
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ---- formuła ----

def test_commit_fallback_violation(harness):
    harness["orders"] = [_order()]  # real 12:12 vs commit 12:00 → wait 12 min
    sla_tracker._check_restaurant_violations()
    e = _entries(harness)
    assert len(e) == 1
    rec = e[0]
    assert rec["order_id"] == "479001"
    assert rec["wait_min"] == 12.0
    assert rec["arrival_source"] == "commit_fallback"
    assert rec["committed_hhmm"] == "12:00"
    assert rec["real_pickup_hhmm"] == "12:12"
    assert rec["restaurant"] == "Mama Thai"
    assert rec["courier_id"] == "370"
    assert rec["order_type"] == "elastyk"


def test_status4_earlier_than_commit_anchors_commit(harness):
    # Kurier pod restauracją 11:50, commit 12:00 → liczymy od 12:00.
    harness["orders"] = [_order(waiting_at="2026-06-10 11:50:00")]
    sla_tracker._check_restaurant_violations()
    e = _entries(harness)
    assert len(e) == 1
    assert e[0]["wait_min"] == 12.0
    assert e[0]["arrival_source"] == "status4"


def test_status4_later_than_commit_anchors_status4(harness):
    # Kurier spóźniony: pod restauracją 12:09, odbiór 12:12 → wait 3 min,
    # mimo że vs commit byłoby 12 → BRAK naruszenia restauracji.
    harness["orders"] = [_order(waiting_at="2026-06-10 12:09:00")]
    sla_tracker._check_restaurant_violations()
    assert _entries(harness) == []


def test_wait_at_threshold_not_violation(harness):
    # Dokładnie 5 min = w kontrakcie (violation dopiero > 5).
    harness["orders"] = [_order(picked_up_at="2026-06-10 12:05:00")]
    sla_tracker._check_restaurant_violations()
    assert _entries(harness) == []


def test_pickup_before_commit_negative_wait(harness):
    harness["orders"] = [_order(picked_up_at="2026-06-10 11:55:00")]
    sla_tracker._check_restaurant_violations()
    assert _entries(harness) == []


# ---- skipy ----

def test_no_commit_skips(harness):
    harness["orders"] = [_order(czas_kuriera_warsaw=None)]
    sla_tracker._check_restaurant_violations()
    assert _entries(harness) == []
    assert harness["upserts"] == []


def test_no_real_pickup_skips(harness):
    harness["orders"] = [_order(picked_up_at=None)]
    sla_tracker._check_restaurant_violations()
    assert _entries(harness) == []


def test_paczka_skips(harness):
    harness["orders"] = [_order(address_id="161")]  # Nadajesz firmowe / paczka
    sla_tracker._check_restaurant_violations()
    assert _entries(harness) == []


def test_unparseable_timestamps_skip_no_crash(harness):
    harness["orders"] = [_order(picked_up_at="garbage")]
    sla_tracker._check_restaurant_violations()
    assert _entries(harness) == []


# ---- TZ ----

def test_tz_naive_warsaw_vs_aware_utc_commit(harness):
    # commit podany w UTC (10:00Z = 12:00 Warsaw latem), real naive Warsaw
    # 12:12 → wait 12 min, nie 132.
    harness["orders"] = [_order(czas_kuriera_warsaw="2026-06-10T10:00:00+00:00")]
    sla_tracker._check_restaurant_violations()
    e = _entries(harness)
    assert len(e) == 1
    assert e[0]["wait_min"] == 12.0
    assert e[0]["committed_hhmm"] == "12:00"  # render zawsze Warsaw


# ---- dedup / lifecycle ----

def test_dedup_one_entry_per_oid(harness):
    harness["orders"] = [_order()]
    sla_tracker._check_restaurant_violations()
    sla_tracker._check_restaurant_violations()  # drugi tick
    assert len(_entries(harness)) == 1
    # seen-flag persistowany przez upsert_order (event RESTAURANT_VIOLATION)
    assert harness["upserts"][0][1] == {"restaurant_violation_logged": True}
    assert harness["upserts"][0][2] == "RESTAURANT_VIOLATION"


def test_delivered_orders_also_scanned(harness):
    harness["orders"] = [_order(status="delivered")]
    sla_tracker._check_restaurant_violations()
    assert len(_entries(harness)) == 1


def test_flag_off_noop(harness, monkeypatch):
    monkeypatch.setattr(sla_tracker.C, "flag", lambda name, default=False: False)
    harness["orders"] = [_order()]
    sla_tracker._check_restaurant_violations()
    assert _entries(harness) == []


def test_one_bad_order_does_not_kill_scan(harness):
    harness["orders"] = [
        _order(oid="479001", picked_up_at=12345),  # int → parse None → skip
        _order(oid="479002"),
    ]
    sla_tracker._check_restaurant_violations()
    e = _entries(harness)
    assert [r["order_id"] for r in e] == ["479002"]


# ---- daily_briefing sekcja ----

def test_briefing_section(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from dispatch_v2 import daily_briefing as db

    vpath = tmp_path / "restaurant_violations.jsonl"
    slapath = tmp_path / "sla_log.jsonl"
    with open(vpath, "w") as f:
        for w in (8.0, 12.0, 20.0):
            f.write(json.dumps({
                "ts": "2026-06-10T11:00:00+00:00",
                "restaurant": "Mama Thai", "wait_min": w,
            }) + "\n")
        f.write(json.dumps({
            "ts": "2026-06-10T11:00:00+00:00",
            "restaurant": "Chicago", "wait_min": 7.0,
        }) + "\n")
    with open(slapath, "w") as f:
        for i in range(6):
            f.write(json.dumps({
                "logged_at": "2026-06-10T12:00:00+00:00",
                "restaurant": "Mama Thai",
            }) + "\n")
    monkeypatch.setattr(db, "RESTAURANT_VIOLATIONS_PATH", str(vpath))
    monkeypatch.setattr(db, "SLA_LOG_PATH", str(slapath))

    start = datetime(2026, 6, 9, tzinfo=timezone.utc)
    end = datetime(2026, 6, 11, tzinfo=timezone.utc)
    lines = db._restaurant_violations_lines(start, end)
    assert lines[0].startswith("Naruszenia restauracji 7d")
    assert lines[1] == "• Mama Thai: 3× (mediana czekania 12 min, 50% zleceń)"
    # Chicago bez mianownika w sla_log → bez %
    assert lines[2] == "• Chicago: 1× (mediana czekania 7 min)"


def test_briefing_section_empty_when_no_data(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from dispatch_v2 import daily_briefing as db
    monkeypatch.setattr(
        db, "RESTAURANT_VIOLATIONS_PATH", str(tmp_path / "missing.jsonl"))
    start = datetime(2026, 6, 9, tzinfo=timezone.utc)
    end = datetime(2026, 6, 11, tzinfo=timezone.utc)
    assert db._restaurant_violations_lines(start, end) == []
