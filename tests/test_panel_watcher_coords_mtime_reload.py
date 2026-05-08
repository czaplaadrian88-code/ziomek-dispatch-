"""MP-#12 (2026-05-08): _COORDS mtime hot-reload test.

panel_watcher._maybe_reload_coords() polls mtime co 15s; jeśli plik zmieniony
(np. nowy address_id mapping od Adriana), reload bez restart procesu.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _write_coords(path, mapping):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f)


def _setup_module():
    """Patch panel_watcher._COORDS_PATH przed import użycia."""
    import dispatch_v2.panel_watcher as pw
    return pw


def test_mtime_check_skip_within_interval():
    """Drugi call w <_COORDS_CHECK_INTERVAL_S → no-op (skip stat)."""
    pw = _setup_module()
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"1": {"lat": 53.1, "lng": 23.1}}, tmp)
    tmp.close()
    pw._COORDS_PATH = tmp.name
    pw._COORDS_LAST_CHECK_TS = 0.0
    pw._COORDS_MTIME = 0.0
    pw._load_coords()
    first_check_ts = pw._COORDS_LAST_CHECK_TS
    assert pw._maybe_reload_coords() is False  # first call sets _COORDS_LAST_CHECK_TS
    # Second call w <15s — skip (no stat call)
    second_check_ts = pw._COORDS_LAST_CHECK_TS
    assert pw._maybe_reload_coords() is False
    assert pw._COORDS_LAST_CHECK_TS == second_check_ts, "second call should skip stat"
    os.unlink(tmp.name)


def test_mtime_unchanged_no_reload():
    """Plik nie zmieniony → no reload mimo upływu interval."""
    pw = _setup_module()
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"1": {"lat": 53.1, "lng": 23.1}}, tmp)
    tmp.close()
    pw._COORDS_PATH = tmp.name
    pw._COORDS_LAST_CHECK_TS = 0.0
    pw._load_coords()
    initial_count = len(pw._COORDS)
    initial_mtime = pw._COORDS_MTIME

    # Force interval expiry
    pw._COORDS_LAST_CHECK_TS = time.time() - pw._COORDS_CHECK_INTERVAL_S - 1
    reloaded = pw._maybe_reload_coords()
    assert reloaded is False, "no mtime change → no reload"
    assert pw._COORDS_MTIME == initial_mtime
    assert len(pw._COORDS) == initial_count
    os.unlink(tmp.name)


def test_mtime_changed_triggers_reload():
    """Plik zmieniony post interval → hot-reload, _COORDS updated."""
    pw = _setup_module()
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"1": {"lat": 53.1, "lng": 23.1}}, tmp)
    tmp.close()
    pw._COORDS_PATH = tmp.name
    pw._COORDS_LAST_CHECK_TS = 0.0
    pw._load_coords()
    assert "1" in pw._COORDS
    assert "2" not in pw._COORDS

    # Mutate plik z nowym entry + force mtime jump
    time.sleep(0.05)
    _write_coords(tmp.name, {
        "1": {"lat": 53.1, "lng": 23.1},
        "2": {"lat": 53.2, "lng": 23.2},
    })
    new_mtime = os.path.getmtime(tmp.name) + 10.0
    os.utime(tmp.name, (new_mtime, new_mtime))

    pw._COORDS_LAST_CHECK_TS = time.time() - pw._COORDS_CHECK_INTERVAL_S - 1
    reloaded = pw._maybe_reload_coords()
    assert reloaded is True, "mtime change → must reload"
    assert "2" in pw._COORDS, f"new entry '2' missing post-reload: {list(pw._COORDS.keys())}"
    assert pw._COORDS["2"] == (53.2, 23.2)
    os.unlink(tmp.name)


def test_missing_file_graceful():
    """Plik usunięty post-load → _maybe_reload_coords nie crash, log warning."""
    pw = _setup_module()
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"1": {"lat": 53.1, "lng": 23.1}}, tmp)
    tmp.close()
    pw._COORDS_PATH = tmp.name
    pw._COORDS_LAST_CHECK_TS = 0.0
    pw._load_coords()
    initial = dict(pw._COORDS)

    os.unlink(tmp.name)
    pw._COORDS_LAST_CHECK_TS = time.time() - pw._COORDS_CHECK_INTERVAL_S - 1
    # Must NOT raise
    result = pw._maybe_reload_coords()
    assert result is False
    # _COORDS preserved (old data, defense-in-depth)
    assert pw._COORDS == initial, "_COORDS should be preserved gdy plik missing post-load"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
