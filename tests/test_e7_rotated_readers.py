"""E7-DOKLEJKA 1 (2026-06-11) — 3 czytniki learning_log czytają zrotowane pliki.

Po logrotate (100M copytruncate) r04_evaluator / validation_gate_lgbm /
learning_analyzer widziały tylko ogon żywego pliku — okno "30d" robiło się
~3-dniowe (incydent klasy 2026-06-08: zamrożony feed A2). Fix = wzorzec
tools/_rotated_logs (SP-B2-LOGROT). Te testy pilnują, że każdy z czytników
widzi rekordy ze zrotowanego siblinga .1 ORAZ że per-rekordowe filtry okna
nadal działają.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from dispatch_v2 import learning_analyzer as la
from dispatch_v2 import r04_evaluator as r04
from dispatch_v2 import validation_gate_lgbm as vg

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _entry(ts, action="INNY", cid="42", lgbm=None):
    best = {"courier_id": cid}
    if lgbm is not None:
        best["lgbm_shadow"] = lgbm
    return {"ts": _iso(ts), "action": action, "order_id": "1",
            "decision": {"best": best}}


# ── r04_evaluator ───────────────────────────────────────────────────────────

def test_r04_tg_negative_counts_reads_rotated_and_live(tmp_path):
    base = tmp_path / "learning_log.jsonl"
    # zrotowany: 2 INNY dla cid=42 w oknie + 1 sprzed okna (per-rekord filtr ts)
    _write_jsonl(str(base) + ".1", [
        _entry(NOW - timedelta(days=10)),
        _entry(NOW - timedelta(days=9)),
        _entry(NOW - timedelta(days=45)),
    ])
    # żywy: 1 INNY w oknie + 1 inna akcja (nie liczy się)
    _write_jsonl(base, [
        _entry(NOW - timedelta(days=1)),
        _entry(NOW - timedelta(days=1), action="TAK"),
    ])
    cutoff = (NOW - timedelta(days=30)).isoformat()
    counts = r04._tg_negative_counts(str(base), cutoff)
    assert counts.get("42") == 3  # 2 z .1 + 1 z żywego; sprzed okna odcięty


def test_r04_compute_courier_metrics_window_spans_rotation(tmp_path):
    base = tmp_path / "learning_log.jsonl"
    _write_jsonl(str(base) + ".1", [_entry(NOW - timedelta(days=20))])
    _write_jsonl(base, [_entry(NOW - timedelta(days=2))])
    db = tmp_path / "events.db"
    sqlite3.connect(str(db)).close()  # pusta baza → events query fail-soft
    m = r04.compute_courier_metrics(
        "42", "Test", {"peak_window_warsaw_hours": []},
        db_path=str(db), log_path=str(base), now_utc=NOW,
    )
    assert m.tg_negative_30d == 2


def test_r04_cache_shared_between_cids(tmp_path):
    base = tmp_path / "learning_log.jsonl"
    _write_jsonl(base, [_entry(NOW - timedelta(days=1), cid="7")])
    cutoff = (NOW - timedelta(days=30)).isoformat()
    c1 = r04._tg_negative_counts(str(base), cutoff)
    # drugi odczyt = cache hit (ten sam obiekt), inne cid czyta z tej samej mapy
    c2 = r04._tg_negative_counts(str(base), cutoff)
    assert c1 is c2
    assert c2.get("7") == 1 and c2.get("999") is None


def test_r04_missing_log_returns_zero(tmp_path):
    base = tmp_path / "missing.jsonl"
    cutoff = (NOW - timedelta(days=30)).isoformat()
    assert r04._tg_negative_counts(str(base), cutoff) == {}


# ── validation_gate_lgbm ────────────────────────────────────────────────────

def test_gate_load_entries_reads_rotated(tmp_path):
    base = tmp_path / "learning_log.jsonl"
    lg = {"chosen_cid": "42", "fallback": None, "latency_ms": 5}
    _write_jsonl(str(base) + ".1", [
        _entry(NOW - timedelta(days=5), lgbm=lg),
        _entry(NOW - timedelta(days=40), lgbm=lg),  # poza oknem
    ])
    _write_jsonl(base, [
        _entry(NOW - timedelta(days=1), lgbm=lg),
        _entry(NOW - timedelta(days=1)),  # bez lgbm_shadow → odfiltrowany
    ])
    since = NOW - timedelta(days=30)
    entries = vg.load_entries(base, since, NOW)
    assert len(entries) == 2
    assert all(e["lgbm"] == lg for e in entries)


# ── learning_analyzer ───────────────────────────────────────────────────────

def test_analyzer_load_entries_full_history_with_rotated(tmp_path):
    base = tmp_path / "learning_log.jsonl"
    _write_jsonl(str(base) + ".1", [_entry(NOW - timedelta(days=60))])
    _write_jsonl(base, [_entry(NOW - timedelta(days=1)), _entry(NOW)])
    entries = la.load_entries(base)
    assert len(entries) == 3  # całość = zrotowany + żywy


def test_analyzer_load_entries_missing_file(tmp_path):
    assert la.load_entries(tmp_path / "missing.jsonl") == []
