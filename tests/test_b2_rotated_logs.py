"""SP-B2-LOGROT — testy helpera tools/_rotated_logs.py (2026-06-11)."""
import gzip
import json
import os
import time
from datetime import datetime, timedelta, timezone

from dispatch_v2.tools import _rotated_logs as rl


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _write_jsonl_gz(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_files_in_window_order_and_live_last(tmp_path):
    base = tmp_path / "log.jsonl"
    _write_jsonl(base, [{"i": "live"}])
    _write_jsonl(str(base) + ".1", [{"i": "r1"}])
    _write_jsonl_gz(str(base) + ".2.gz", [{"i": "r2"}])

    files = rl.files_in_window(str(base))
    assert files == [str(base) + ".2.gz", str(base) + ".1", str(base)]


def test_iter_records_reads_gz_transparently_chronological(tmp_path):
    base = tmp_path / "log.jsonl"
    _write_jsonl(base, [{"i": 3}])
    _write_jsonl(str(base) + ".1", [{"i": 2}])
    _write_jsonl_gz(str(base) + ".2.gz", [{"i": 1}])

    got = [r["i"] for r in rl.iter_jsonl_records(str(base))]
    assert got == [1, 2, 3]


def test_cutoff_prunes_old_rotated_by_mtime(tmp_path):
    base = tmp_path / "log.jsonl"
    _write_jsonl(base, [{"i": "live"}])
    old = str(base) + ".2"
    fresh = str(base) + ".1"
    _write_jsonl(old, [{"i": "old"}])
    _write_jsonl(fresh, [{"i": "fresh"}])
    # mtime .2 = 10 dni temu (rotacja dawno → cała zawartość poza oknem)
    old_ts = time.time() - 10 * 86400
    os.utime(old, (old_ts, old_ts))

    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    files = rl.files_in_window(str(base), cutoff_dt=cutoff)
    assert old not in files
    assert fresh in files and str(base) in files


def test_missing_rotated_and_live_only(tmp_path):
    base = tmp_path / "solo.jsonl"
    _write_jsonl(base, [{"a": 1}])
    assert [r["a"] for r in rl.iter_jsonl_records(str(base))] == [1]


def test_missing_live_yields_nothing(tmp_path):
    base = tmp_path / "absent.jsonl"
    assert list(rl.iter_jsonl_records(str(base))) == []


def test_decode_errors_and_non_dicts_skipped(tmp_path):
    base = tmp_path / "log.jsonl"
    with open(base, "w", encoding="utf-8") as f:
        f.write('{"ok": 1}\n')
        f.write("{not json\n")
        f.write("\n")
        f.write("[1,2,3]\n")
        f.write('{"ok": 2}\n')
    got = [r["ok"] for r in rl.iter_jsonl_records(str(base))]
    assert got == [1, 2]


def test_lock_and_bak_siblings_ignored(tmp_path):
    base = tmp_path / "log.jsonl"
    _write_jsonl(base, [{"i": "live"}])
    (tmp_path / "log.jsonl.lock").write_text("")
    (tmp_path / "log.jsonl.bak-pre-x").write_text("{}")
    files = rl.files_in_window(str(base))
    assert files == [str(base)]
