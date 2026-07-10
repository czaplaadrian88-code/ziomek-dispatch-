"""Z-P0-02: wieloprocesowy kanon RMW cache geokodowania.

Dowody w tym pliku są hermetyczne: wyłącznie tmp_path, bez sieci i bez odczytu
produkcyjnych cache'y. Test legacy używa dokładnego starego antywzorca
(unikalny tempfile + flock na tempfile + replace) i barierą wymusza wspólny,
nieaktualny snapshot obu writerów.
"""
from __future__ import annotations

import fcntl
import json
import multiprocessing as mp
import os
import stat
import tempfile
import time
from pathlib import Path

import pytest

from dispatch_v2 import geocoding as G
from dispatch_v2 import bootstrap_restaurants as bootstrap
from dispatch_v2.tools import invalidate_city_bugged_geocodes as invalidate_tool
from dispatch_v2.tools import purge_streetless_geocode_keys as purge_tool


def _legacy_worker(path: str, key: str, ready, start) -> None:
    """Replika zapisu sprzed Z-P0-02: lockuje własny, unikalny tempfile."""
    target = Path(path)
    snapshot = json.loads(target.read_text(encoding="utf-8"))
    ready.put(key)
    if not start.wait(10):
        raise TimeoutError("legacy worker did not receive start signal")
    snapshot[key] = {"writer": key}
    fd, tmp = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.tmp-", suffix=".json"
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        json.dump(snapshot, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def _fixed_worker(path: str, key: str, ready, start) -> None:
    """Writer przez produkcyjny kanon: fresh load + merge pod wspólnym lockiem."""
    ready.put(key)
    if not start.wait(10):
        raise TimeoutError("fixed worker did not receive start signal")

    def _insert(cache: dict) -> bool:
        cache[key] = {"writer": key}
        return True

    G._mutate_cache(Path(path), _insert)


def _run_two_workers(target, path: Path) -> None:
    ctx = mp.get_context("fork")
    ready = ctx.Queue()
    start = ctx.Event()
    processes = [
        ctx.Process(target=target, args=(str(path), key, ready, start))
        for key in ("writer_a", "writer_b")
    ]
    for process in processes:
        process.start()
    try:
        assert {ready.get(timeout=10), ready.get(timeout=10)} == {
            "writer_a", "writer_b"
        }
        start.set()
        for process in processes:
            process.join(timeout=15)
            assert not process.is_alive(), "worker zawisł na blokadzie cache"
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        ready.close()


def test_legacy_unique_tempfile_lock_deterministically_loses_update(tmp_path):
    path = tmp_path / "geocode_cache.json"
    path.write_text(json.dumps({"base": {"ok": True}}), encoding="utf-8")

    _run_two_workers(_legacy_worker, path)

    final = json.loads(path.read_text(encoding="utf-8"))
    # Oba procesy na pewno czytały ten sam snapshot przed sygnałem start. Ostatni
    # replace wygrywa, dlatego dokładnie jeden poprawny wpis znika.
    assert "base" in final
    assert len({"writer_a", "writer_b"} & set(final)) == 1


@pytest.mark.parametrize(
    "filename",
    ["geocode_cache.json", "restaurant_coords.json", "geocode_neg_cache.json"],
)
def test_transaction_keeps_both_process_updates_for_every_cache(tmp_path, filename):
    path = tmp_path / filename
    path.write_text(json.dumps({"base": {"ok": True}}), encoding="utf-8")

    _run_two_workers(_fixed_worker, path)

    final = json.loads(path.read_text(encoding="utf-8"))
    assert set(final) == {"base", "writer_a", "writer_b"}
    lock_path = path.with_name(path.name + ".lock")
    assert lock_path.exists()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_pin_inserted_during_network_call_wins_transaction_recheck(
    tmp_path, monkeypatch
):
    cache_path = tmp_path / "address.json"
    neg_path = tmp_path / "negative.json"
    monkeypatch.setattr(G, "CACHE_PATH", cache_path)
    monkeypatch.setattr(G, "NEG_CACHE_PATH", neg_path)
    monkeypatch.setattr(G, "_ttl_config", lambda: (False, 86400.0, False, 200.0))
    monkeypatch.setattr(G, "_neg_cache_enabled", lambda: False)
    monkeypatch.setattr(G, "_in_service_bbox", lambda lat, lon: True)
    monkeypatch.setattr(G, "_run_verification", lambda *args, **kwargs: None)
    monkeypatch.setattr(G, "_audit_log", lambda *args, **kwargs: None)

    address = "Wyścigowa 7"
    city = "Białystok"
    key = G._normalize(address, city)
    pin = {
        "lat": 53.101,
        "lon": 23.101,
        "source": "pinned_manual",
        "cached_at": "pinned:inserted-during-network",
    }

    def _google_result(_query, timeout=5.0):
        # Symuluje drugi proces zapisujący autorytatywny pin po pierwszym cache
        # miss, ale przed commitem automatycznego wyniku Google.
        G._put_cache_entry(cache_path, key, pin, protect_pins=True)
        return (53.202, 23.202, {})

    monkeypatch.setattr(G, "_google_geocode", _google_result)

    assert G.geocode(address, city=city) == (pin["lat"], pin["lon"])
    assert json.loads(cache_path.read_text(encoding="utf-8"))[key] == pin


def test_gc_is_transactional_and_preserves_all_pin_markers(tmp_path):
    path = tmp_path / "geocode_cache.json"
    now = time.time()
    pin = {
        "lat": 53.1,
        "lon": 23.1,
        "source": "manual_override",
        # Numeryczny i stary celowo: ochrona wynika z markera source.
        "cached_at": now - 10_000,
    }
    path.write_text(
        json.dumps({
            "old": {"cached_at": now - 10_000},
            "fresh": {"cached_at": now},
            "legacy": {"lat": 53.2, "lon": 23.2},
            "pin": pin,
        }),
        encoding="utf-8",
    )

    result = G.cache_gc_stale(path, ttl_sec=100.0)

    final = json.loads(path.read_text(encoding="utf-8"))
    assert result == {"scanned": 4, "removed": 1, "kept_legacy": 2}
    assert set(final) == {"fresh", "legacy", "pin"}
    assert final["pin"] == pin
    assert path.with_name(path.name + ".lock").exists()


def test_strict_writer_never_replaces_corrupt_cache_with_empty_dict(tmp_path):
    path = tmp_path / "geocode_cache.json"
    corrupt = "{not-json"
    path.write_text(corrupt, encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        G._mutate_cache(path, lambda cache: True)

    assert path.read_text(encoding="utf-8") == corrupt
    assert not list(tmp_path.glob(".geocode_cache.json.tmp-*.json"))


def test_geocode_returns_fresh_coords_and_keeps_corrupt_cache(tmp_path, monkeypatch):
    path = tmp_path / "geocode_cache.json"
    neg_path = tmp_path / "geocode_neg_cache.json"
    corrupt = b'{"broken":'
    path.write_bytes(corrupt)
    monkeypatch.setattr(G, "CACHE_PATH", path)
    monkeypatch.setattr(G, "NEG_CACHE_PATH", neg_path)
    monkeypatch.setattr(G, "_ttl_config", lambda: (False, 86400.0, False, 200.0))
    monkeypatch.setattr(G, "_neg_cache_enabled", lambda: False)
    monkeypatch.setattr(G, "_google_geocode", lambda query, timeout=5.0: (53.2, 23.2, {}))
    monkeypatch.setattr(G, "_in_service_bbox", lambda lat, lon: True)
    monkeypatch.setattr(G, "_run_verification", lambda *args, **kwargs: None)
    monkeypatch.setattr(G, "_audit_log", lambda *args, **kwargs: None)

    assert G.geocode("Testowa 1", city="Białystok") == (53.2, 23.2)
    assert path.read_bytes() == corrupt


def test_geocode_restaurant_returns_fresh_coords_and_keeps_corrupt_cache(
    tmp_path, monkeypatch
):
    path = tmp_path / "restaurant_coords.json"
    corrupt = b"not-json"
    path.write_bytes(corrupt)
    monkeypatch.setattr(G, "RESTAURANT_CACHE_PATH", path)
    monkeypatch.setattr(G, "_ttl_config", lambda: (False, 86400.0, False, 200.0))
    monkeypatch.setattr(G, "_google_geocode", lambda query, timeout=5.0: (53.21, 23.21, {}))
    monkeypatch.setattr(G, "_in_service_bbox", lambda lat, lon: True)
    monkeypatch.setattr(G, "_audit_log", lambda *args, **kwargs: None)

    assert G.geocode_restaurant(
        "Test Bistro", "Testowa 1", city="Białystok"
    ) == (53.21, 23.21)
    assert path.read_bytes() == corrupt


def test_bootstrap_three_way_write_preserves_concurrent_changes_and_is_atomic(
    tmp_path, monkeypatch
):
    path = tmp_path / "restaurant_coords.json"
    baseline = {
        "same": {"lat": 1},
        "changed_during_run": {"lat": 2},
        "removed_during_run": {"lat": 3},
        "removed_by_bootstrap": {"lat": 4},
    }
    desired = {
        "same": {"lat": 10},
        "changed_during_run": {"lat": 20},
        "removed_during_run": {"lat": 30},
        "new_from_bootstrap": {"lat": 50},
    }
    # Stan, który inny writer zdążył zapisać po odczycie baseline.
    current = {
        "same": {"lat": 1},
        "changed_during_run": {"lat": 200},
        "removed_by_bootstrap": {"lat": 4},
        "new_concurrent": {"lat": 60},
    }
    path.write_text(json.dumps(current), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "OUT", path)

    merged, changed = bootstrap._write_results_three_way(baseline, desired)

    expected = {
        "changed_during_run": {"lat": 200},
        "new_concurrent": {"lat": 60},
        "new_from_bootstrap": {"lat": 50},
        "same": {"lat": 10},
    }
    assert changed is True
    assert merged == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert path.with_name(path.name + ".lock").exists()
    assert not list(tmp_path.glob(".restaurant_coords.json.tmp-*.json"))


def test_streetless_purge_apply_uses_shared_transaction(tmp_path, monkeypatch):
    path = tmp_path / "geocode_cache.json"
    path.write_text(
        json.dumps({
            "3, białystok": {"original": "Magazynowa 3"},
            "magazynowa 3, białystok": {"original": "Magazynowa 3"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(purge_tool, "CACHE", path)

    assert purge_tool.main(apply=True) == 0

    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "magazynowa 3, białystok"
    }
    assert path.with_name(path.name + ".lock").exists()
    assert len(list(tmp_path.glob("geocode_cache.json.bak-pre-streetless-*"))) == 1


def test_city_invalidation_execute_uses_shared_transaction(tmp_path, monkeypatch):
    path = tmp_path / "geocode_cache.json"
    path.write_text(
        json.dumps({
            "good": {"lat": 53.13, "lon": 23.16},
            "bad": {"lat": 50.0, "lon": 20.0},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(invalidate_tool, "CACHE_PATH", path)
    monkeypatch.setattr(invalidate_tool.sys, "argv", ["invalidate", "--execute"])

    invalidate_tool.main()

    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"good"}
    assert path.with_name(path.name + ".lock").exists()
    assert len(list(tmp_path.glob("geocode_cache.json.bak-city-invalidation-*"))) == 1
