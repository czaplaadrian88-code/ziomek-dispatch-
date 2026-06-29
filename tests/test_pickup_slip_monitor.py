"""Testy monitora poślizgu odbioru (#2, read-only).

Logika kubełków obciążenia + agregacja + bramka rekomendacji (n>=30). Read-only,
deps przez monkeypatch collect (file-IO izolowane).
"""
from datetime import datetime

from dispatch_v2.tools import pickup_slip_monitor as M


def test_load_bucket():
    assert M._load_bucket(None) == "unknown"
    assert M._load_bucket(0) == "ciasno"
    assert M._load_bucket(1) == "ciasno"
    assert M._load_bucket(2) == "srednio"
    assert M._load_bucket(4) == "srednio"
    assert M._load_bucket(5) == "luzno"
    assert M._load_bucket(9) == "luzno"


def test_trimmed_mean():
    assert M._trimmed_mean([]) is None
    assert M._trimmed_mean([5.0]) == 5.0
    # trim 10% z 20 elem = po 2 z każdej strony; skrajne odcięte
    xs = [0.0] + [10.0] * 18 + [100.0]
    assert M._trimmed_mean(xs) == 10.0


def test_summarize_buffer_gate(monkeypatch):
    # ciasno solo n=40 (>=30 → bufor), bundle n=10 (<30 → None)
    cells = {"ciasno": {"solo": [20.0] * 40, "bundle": [5.0] * 10}}
    monkeypatch.setattr(M, "collect", lambda days, now=None: (cells, 50, 0))
    rep = M.summarize(days=3, now=datetime(2026, 6, 29, 22, 0))
    s = rep["segments"]["ciasno"]
    assert s["solo"]["median"] == 20.0
    assert s["solo"]["n"] == 40
    assert s["solo"]["recommend_buffer_min"] == 20.0      # n>=30
    assert s["bundle"]["recommend_buffer_min"] is None    # n<30 za cienko
    assert rep["n_total"] == 50


def test_summarize_empty(monkeypatch):
    monkeypatch.setattr(M, "collect", lambda days, now=None: ({}, 0, 0))
    rep = M.summarize(days=3, now=datetime(2026, 6, 29))
    assert rep["segments"] == {}
    assert rep["n_total"] == 0
