#!/usr/bin/env python3
"""Testy global_alloc_store (dedykowany kanał globalnej alokacji dla konsoli)."""
import sys, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import global_alloc_store as G

_N = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)


def _rec(cid="A"):
    return {"order_id": "o1", "verdict": "PROPOSE", "best": {"courier_id": cid}}


def test_write_load_roundtrip(tmp_path):
    p = str(tmp_path / "ga.json")
    n = G.write({"o1": _rec("A"), "o2": _rec("B")}, _N, path=p)
    assert n == 2
    out = G.load_fresh(_N, path=p)
    assert set(out.keys()) == {"o1", "o2"}
    assert out["o1"]["best"]["courier_id"] == "A"


def test_load_stale_returns_empty(tmp_path):
    p = str(tmp_path / "ga.json")
    G.write({"o1": _rec()}, _N, path=p)
    # 3 min później przy TTL 120s → stale
    out = G.load_fresh(_N + timedelta(seconds=200), ttl_sec=120, path=p)
    assert out == {}


def test_load_fresh_within_ttl(tmp_path):
    p = str(tmp_path / "ga.json")
    G.write({"o1": _rec()}, _N, path=p)
    out = G.load_fresh(_N + timedelta(seconds=90), ttl_sec=120, path=p)
    assert "o1" in out


def test_write_overwrites_full(tmp_path):
    p = str(tmp_path / "ga.json")
    G.write({"o1": _rec(), "o2": _rec()}, _N, path=p)
    G.write({"o3": _rec()}, _N, path=p)   # pełne nadpisanie
    out = G.load_fresh(_N, path=p)
    assert set(out.keys()) == {"o3"}      # o1/o2 zniknęły (przestały wisieć)


def test_load_missing_returns_empty(tmp_path):
    assert G.load_fresh(_N, path=str(tmp_path / "nope.json")) == {}


def test_load_corrupt_returns_empty(tmp_path):
    p = str(tmp_path / "ga.json")
    open(p, "w").write("{bad json")
    assert G.load_fresh(_N, path=p) == {}


def test_write_failsoft(tmp_path, monkeypatch):
    # katalog nieistniejący-rodzic + os.replace wybuchnie → 0, bez wyjątku
    monkeypatch.setattr(G.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    n = G.write({"o1": _rec()}, _N, path=str(tmp_path / "x.json"))
    assert n == 0
