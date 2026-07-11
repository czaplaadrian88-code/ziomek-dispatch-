"""K17 — testy runnera bramki korpusowej `tools/world_replay_gate.py`.

Plumbing: filtr okna (now=null pomijane, since/until, dedup), agregacja
zgodne/różnice/missy/brak_zapisu/błędy, werdykt + exit code, zapis pliku
werdyktu (atomowy, ścieżka z testu — NIGDY prod, assert anty-prod C17).
Replay właściwy mockowany — realny bieg korpusowy = osobny artefakt K17b.
"""
import json
from pathlib import Path

import pytest

# import przez pakiet z conftest._SCRIPTS_ROOT (respektuje ZIOMEK_SCRIPTS_ROOT
# w biegu worktree — C12e: żadnych twardych ścieżek kanonu/worktree w teście)
from dispatch_v2.tools import world_replay_gate as G


def _write_records(dirpath: Path, recs):
    f = dirpath / "world_record-20260706.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return f


def _rec(oid, ts, now=True, schema="wr1"):
    return {"order_id": oid, "ts": ts, "schema": schema,
            "now": ts if now else None, "verdict": "PROPOSE",
            "order_event": {"order_id": oid}, "fleet": {}, "flags": {},
            "live_inputs": {"reliability": {}, "plans": {},
                            "eta_quantile": {}, "prep_bias": {},
                            "loadgov": [None, None, None, 0], "k07": None},
            "osrm_calls": []}


@pytest.fixture
def corpus(tmp_path):
    recs = [
        _rec("100", "2026-07-06T10:00:00+00:00"),
        _rec("101", "2026-07-06T12:00:00+00:00"),
        _rec("101", "2026-07-06T12:00:00+00:00"),          # duplikat → dedup
        _rec("102", "2026-07-06T13:00:00+00:00", now=False),  # now=null → pomijany
        _rec("103", "2026-07-06T14:00:00+00:00"),
    ]
    _write_records(tmp_path, recs)
    return tmp_path


def _extract_like(verdict="PROPOSE", cid="484", score=-1.0):
    return {"verdict": verdict, "reason": "r", "best_cid": cid,
            "best_score": score, "pool_feasible": 5, "pool_total": 10}


def _shadow_index_for(*oids):
    return {str(o): [{"order_id": str(o), "ts": f"2026-07-06T{h}:00:01+00:00",
                      "verdict": "PROPOSE", "reason": "r",
                      "best": {"courier_id": "484", "score": -1.0},
                      "pool_feasible_count": 5, "pool_total_count": 10}]
            for o, h in oids}


def test_window_filter_skips_no_now_and_dedups(corpus):
    recs, skipped, skipped_pre_wr1 = G._iter_window_records(str(corpus), None, None)
    assert [r["order_id"] for r in recs] == ["100", "101", "103"]
    assert skipped == 1
    assert skipped_pre_wr1 == 0


def test_window_since_until(corpus):
    since = G.WR._parse_dt("2026-07-06T11:00:00+00:00")
    until = G.WR._parse_dt("2026-07-06T13:30:00+00:00")
    recs, *_ = G._iter_window_records(str(corpus), since, until)
    assert [r["order_id"] for r in recs] == ["101"]


def test_gate_parity_all_match(corpus, monkeypatch):
    monkeypatch.setattr(G.WR, "replay_one", lambda rec: (_extract_like(), 0))
    idx = _shadow_index_for(("100", "10"), ("101", "12"), ("103", "14"))
    rep = G.run_gate(None, None, record_dir=str(corpus), shadow_index=idx)
    assert (rep["n"], rep["zgodne"], rep["roznice_n"]) == (4, 3, 0)
    assert sum(rep["class_counts"].values()) == rep["denominator"]
    assert rep["class_counts"]["INPUT_MISS"] == 1
    assert rep["verdict"] == "DIFFS"


def test_gate_detects_diff_and_miss(corpus, monkeypatch):
    def fake_replay(rec):
        if rec["order_id"] == "101":
            return _extract_like(cid="999"), 0   # różnica best_cid (KRYTYCZNA)
        if rec["order_id"] == "103":
            return _extract_like(), 2            # missy OSRM
        e = _extract_like()
        e["pool_feasible"] = 6                   # różnica miękka (pool only)
        return e, 0
    monkeypatch.setattr(G.WR, "replay_one", fake_replay)
    idx = _shadow_index_for(("100", "10"), ("101", "12"), ("103", "14"))
    rep = G.run_gate(None, None, record_dir=str(corpus), shadow_index=idx)
    assert rep["verdict"] == "DIFFS"
    assert rep["zgodne"] == 0 and rep["roznice_n"] == 2 and rep["missy_n"] == 1
    assert rep["roznice_krytyczne_n"] == 1 and rep["roznice_miekkie_n"] == 1
    kryt = [r for r in rep["roznice"] if r["krytyczna"]]
    assert "best_cid" in kryt[0]["diff_fields"]
    assert "order_id" not in kryt[0], "raport ma pseudonim, nie ID operacyjne"


def test_gate_brak_zapisu_and_errors(corpus, monkeypatch):
    def fake_replay(rec):
        if rec["order_id"] == "100":
            raise RuntimeError("boom")
        return _extract_like(), 0
    monkeypatch.setattr(G.WR, "replay_one", fake_replay)
    idx = _shadow_index_for(("100", "10"), ("101", "12"))  # 103 bez zapisu
    rep = G.run_gate(None, None, record_dir=str(corpus), shadow_index=idx)
    assert rep["bledy_n"] == 1 and rep["brak_zapisu_n"] == 1 and rep["zgodne"] == 1
    assert rep["verdict"] == "DIFFS"  # błąd = nie-zielono (uczciwy werdykt)


def test_empty_window_verdict(tmp_path):
    _write_records(tmp_path, [_rec("1", "2026-07-06T10:00:00+00:00", now=False)])
    rep = G.run_gate(None, None, record_dir=str(tmp_path), shadow_index={})
    assert rep["verdict"] == "DIFFS" and rep["n"] == 1
    assert rep["input_miss_reasons"] == {"missing_now": 1}


def test_join_picks_closest_within_tolerance():
    idx = {"7": [
        {"order_id": "7", "ts": "2026-07-06T10:04:00+00:00", "verdict": "A"},
        {"order_id": "7", "ts": "2026-07-06T10:00:30+00:00", "verdict": "B"},
        {"order_id": "7", "ts": "2026-07-06T11:00:00+00:00", "verdict": "C"},  # poza ±300s
    ]}
    got = G._join_shadow(idx, "7", "2026-07-06T10:00:00+00:00")
    assert got["verdict"] == "B"


def test_main_writes_verdict_file_and_exit_codes(corpus, tmp_path, monkeypatch):
    monkeypatch.setattr(G.WR, "replay_one", lambda rec: (_extract_like(), 0))
    monkeypatch.setattr(G, "_build_shadow_index",
                        lambda since: _shadow_index_for(("100", "10"), ("101", "12"),
                                                        ("103", "14")))
    out = tmp_path / "verdict.txt"
    # C17 anty-prod: efektywna ścieżka werdyktu z testu, nie default prod
    assert "/dispatch_state/" not in str(out) and str(tmp_path) in str(out)
    rc = G.main(["--record-dir", str(corpus), "--out", str(out),
                 "--since", "2026-07-06T13:30:00+00:00"])
    assert rc == 0
    txt = out.read_text(encoding="utf-8")
    assert "WERDYKT: PARITY" in txt and "denominator=1" in txt

    # różnica → exit 1
    monkeypatch.setattr(G.WR, "replay_one", lambda rec: (_extract_like(cid="X"), 0))
    rc = G.main(["--record-dir", str(corpus), "--out", str(out),
                 "--since", "2026-07-06T13:30:00+00:00"])
    assert rc == 1
    assert "WERDYKT: DIFFS" in out.read_text(encoding="utf-8")

    # puste okno → exit 2
    rc = G.main(["--record-dir", str(corpus), "--out", str(out),
                 "--since", "2027-01-01T00:00:00+00:00"])
    assert rc == 2
