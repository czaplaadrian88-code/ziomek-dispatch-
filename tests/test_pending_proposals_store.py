#!/usr/bin/env python3
"""Testy pending_proposals_store (Opcja B — zasilanie pending_proposals z silnika)."""
import sys, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import pending_proposals_store as S

_N = datetime(2026, 6, 26, 20, 0, 0, tzinfo=timezone.utc)


def _rec(cid="A"):
    return {"verdict": "PROPOSE", "best": {"courier_id": cid, "plan": {"sequence": ["o1"]}},
            "auto_route": "ACK"}


def test_build_entry_schema():
    e = S.build_entry(_rec("X"), _N, ttl_sec=600)
    assert e["message_id"] is None
    assert e["sent_at"] == _N.isoformat()
    assert e["decision_record"]["best"]["courier_id"] == "X"
    assert S._parse_iso(e["expires_at"]) == _N + timedelta(seconds=600)


def test_upsert_writes_and_reads(tmp_path):
    p = str(tmp_path / "pp.json")
    n = S.upsert_proposals([("o1", _rec("A")), ("o2", _rec("B"))], _N, path=p)
    assert n == 2
    d = json.load(open(p))
    assert set(d.keys()) == {"o1", "o2"}
    assert d["o1"]["decision_record"]["best"]["courier_id"] == "A"
    assert d["o1"]["message_id"] is None


def test_upsert_merges_and_sweeps(tmp_path):
    p = str(tmp_path / "pp.json")
    # stan startowy: o_old wygasły, o_keep świeży
    S.save({
        "o_old": {"expires_at": (_N - timedelta(minutes=1)).isoformat(), "decision_record": {}},
        "o_keep": {"expires_at": (_N + timedelta(hours=1)).isoformat(), "decision_record": {}},
    }, p)
    n = S.upsert_proposals([("o_new", _rec("C"))], _N, path=p)
    assert n == 1
    d = json.load(open(p))
    assert "o_old" not in d          # wygasły wymieciony
    assert "o_keep" in d             # świeży zachowany
    assert "o_new" in d              # nowy dodany


def test_upsert_empty_noop_does_not_clobber(tmp_path):
    p = str(tmp_path / "pp.json")
    S.save({"o_keep": {"expires_at": (_N + timedelta(hours=1)).isoformat()}}, p)
    n = S.upsert_proposals([], _N, path=p)
    assert n == 0
    assert "o_keep" in json.load(open(p))   # nietknięte


def test_upsert_failsoft_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "save", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    n = S.upsert_proposals([("o1", _rec())], _N, path=str(tmp_path / "x.json"))
    assert n == 0   # błąd nie propaguje


def test_load_missing_file_returns_empty(tmp_path):
    assert S.load(str(tmp_path / "nope.json")) == {}
