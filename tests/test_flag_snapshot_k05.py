"""K05 (program refaktoru, 2026-07-06, ADR-R01) — FlagSnapshot per tick.

Dowody:
- charakteryzujący (zielony PRZED i PO): bez snapshotu zmiana flags.json jest
  widoczna natychmiast (hot-reload) — dzisiejsza własność zachowana,
- ON≠OFF: pod aktywnym snapshotem zmiana pliku mid-tick jest NIEWIDOCZNA aż do
  end(); po end() hot-reload wraca,
- gate OFF/brak klucza = begin() no-op (None),
- fail-soft + idempotencja end().

Izolacja (C17): FLAGS_PATH → tmp_path; pełny reset cache'ów modułu w fixturze;
mtime wymuszany os.utime (granulacja zegara).
"""
import json
import os

import pytest

import dispatch_v2.common as C


@pytest.fixture
def flags_env(tmp_path, monkeypatch):
    p = tmp_path / "flags.json"

    def write(d, t):
        p.write_text(json.dumps(d, ensure_ascii=False))
        os.utime(p, (t, t))

    monkeypatch.setattr(C, "FLAGS_PATH", p)
    monkeypatch.setattr(C, "_flags_cache", None)
    monkeypatch.setattr(C, "_flags_mtime", 0)
    monkeypatch.setattr(C, "_flags_last_stat_mono", 0.0)
    monkeypatch.setattr(C, "_perf_lazy_members", False)
    monkeypatch.setattr(C, "_FLAGS_SNAPSHOT_OVERRIDE", None)
    return write


def test_charakteryzujacy_hot_reload_bez_snapshotu(flags_env):
    flags_env({"A": True}, 1_000_000)
    assert C.flag("A") is True
    flags_env({"A": False}, 1_000_100)
    assert C.flag("A") is False, "hot-reload (dzisiejsza własność) musi działać bez snapshotu"


def test_on_snapshot_izoluje_mid_tick_i_end_przywraca(flags_env):
    flags_env({"ENABLE_FLAG_SNAPSHOT": True, "A": True}, 1_000_000)
    snap = C.flags_snapshot_begin()
    assert snap is not None and snap["A"] is True
    # zmiana pliku W ŚRODKU ticku — decyzja NIE może jej zobaczyć
    flags_env({"ENABLE_FLAG_SNAPSHOT": True, "A": False}, 1_000_200)
    assert C.flag("A") is True, "snapshot musi izolować zmianę mid-tick (ON≠OFF)"
    assert C.load_flags() is snap
    C.flags_snapshot_end()
    assert C.flag("A") is False, "po end() hot-reload wraca (między tickami)"


def test_gate_off_begin_noop(flags_env):
    flags_env({"ENABLE_FLAG_SNAPSHOT": False, "A": True}, 1_000_000)
    assert C.flags_snapshot_begin() is None
    flags_env({"ENABLE_FLAG_SNAPSHOT": False, "A": False}, 1_000_300)
    assert C.flag("A") is False, "gate OFF = zero zamrożenia (zachowanie 1:1)"


def test_brak_klucza_to_off(flags_env):
    flags_env({"A": True}, 1_000_000)
    assert C.flags_snapshot_begin() is None, "brak klucza w flags.json = OFF (stała-fallback False)"


def test_end_idempotentne_i_finally_pattern(flags_env):
    flags_env({"ENABLE_FLAG_SNAPSHOT": True, "A": True}, 1_000_000)
    C.flags_snapshot_end()  # bez begin — no-op
    assert C.flags_snapshot_begin() is not None
    try:
        raise RuntimeError("tick pada")
    except RuntimeError:
        pass
    finally:
        C.flags_snapshot_end()
    assert C._FLAGS_SNAPSHOT_OVERRIDE is None
    C.flags_snapshot_end()  # podwójny end — no-op


def test_fail_soft_gdy_flags_json_nieczytelny(flags_env, tmp_path, monkeypatch):
    monkeypatch.setattr(C, "FLAGS_PATH", tmp_path / "nie_istnieje.json")
    monkeypatch.setattr(C, "_flags_cache", None)
    assert C.flags_snapshot_begin() is None, "błąd odczytu = brak snapshotu, nie wyjątek"
    assert C._FLAGS_SNAPSHOT_OVERRIDE is None
