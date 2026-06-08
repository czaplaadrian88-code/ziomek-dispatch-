"""Regresja 2026-06-08: backfill_decisions_outcomes musi czytać ZROTOWANE learning_log.

Root cause: logrotate (size 100M + copytruncate) truncuje żywy learning_log.jsonl
co ~tydzień → backfill --days 14 widział tylko bieżący ogon (o 04:00 = pusto) →
'Brak danych' → łańcuch retro/courier_reliability/A2 padał, feed zamarzał.
Fix: _learning_log_files_in_window dokłada .1/.2.gz w oknie; _iter_learning_lines
czyta je (w tym .gz). Test izoluje LEARNING_LOG na tmp i kontroluje mtime.
"""
import gzip
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dispatch_v2.tools.backfill_decisions_outcomes as b


def _set_mtime(p, dt):
    ts = dt.timestamp()
    os.utime(p, (ts, ts))


def _setup(tmp_path, monkeypatch):
    live = tmp_path / "learning_log.jsonl"
    live.write_text('{"ts":"live"}\n')
    monkeypatch.setattr(b, "LEARNING_LOG", live)
    now = datetime.now(timezone.utc)
    # .1 zrotowany wczoraj (w oknie 14d i 2d)
    r1 = tmp_path / "learning_log.jsonl.1"
    r1.write_text('{"ts":"r1a"}\n{"ts":"r1b"}\n')
    _set_mtime(r1, now - timedelta(days=1))
    # .2.gz zrotowany 7 dni temu (w oknie 14d, POZA 2d)
    r2 = tmp_path / "learning_log.jsonl.2.gz"
    with gzip.open(r2, "wt") as f:
        f.write('{"ts":"r2gz"}\n')
    _set_mtime(r2, now - timedelta(days=7))
    return live, r1, r2


def test_window_includes_rotated_within_window(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cut14 = datetime.now(timezone.utc) - timedelta(days=14)
    names = [Path(p).name for p in b._learning_log_files_in_window(cut14)]
    assert names == [
        "learning_log.jsonl",
        "learning_log.jsonl.1",
        "learning_log.jsonl.2.gz",
    ]


def test_window_excludes_rotated_outside_window(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cut2 = datetime.now(timezone.utc) - timedelta(days=2)
    names = [Path(p).name for p in b._learning_log_files_in_window(cut2)]
    # .2.gz (7d temu) wypada poza okno 2d; żywy + .1 zostają
    assert names == ["learning_log.jsonl", "learning_log.jsonl.1"]


def test_iter_reads_plain_and_gz(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cut14 = datetime.now(timezone.utc) - timedelta(days=14)
    files = b._learning_log_files_in_window(cut14)
    lines = [l.strip() for l in b._iter_learning_lines(files)]
    assert '{"ts":"live"}' in lines
    assert '{"ts":"r1a"}' in lines and '{"ts":"r1b"}' in lines
    assert '{"ts":"r2gz"}' in lines  # .gz odczytany transparentnie
    assert len(lines) == 4


def test_iter_skips_missing_file(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    files = [str(tmp_path / "learning_log.jsonl"), str(tmp_path / "nie_istnieje.1")]
    lines = [l.strip() for l in b._iter_learning_lines(files)]
    assert lines == ['{"ts":"live"}']
