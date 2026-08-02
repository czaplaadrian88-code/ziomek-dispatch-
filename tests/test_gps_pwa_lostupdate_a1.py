"""A-1 (2026-08-02) — GPS lost-update fix: kanoniczny writer gps_positions_pwa.json.

Bramka testowa Przykazania #0:
  • NEGATYWNY ORACLE reprodukujący defekt: dwaj writerzy CROSS-PROCES gubią cid
    (test_cross_process_off_loses) — legacy/OFF.
  • MUTATION PROBE: ten sam wyścig pod flagą ON (merge-lock) NIE gubi
    (test_cross_process_on_preserves) → usunięcie/odwrócenie fixu (lock, merge)
    czerwieni ten test.
  • RATCHET merge-by-ts: spóźniony batch nie cofa świeższej pozycji
    (test_merge_by_ts_stale_batch_no_rollback).
  • BAJT-PARYTET OFF vs legacy (test_off_byte_parity_vs_legacy).
  • Flaga faktycznie bramkuje zachowanie (test_flag_gates_lock).

Izolacja: wszystkie ścieżki w tmp_path (HERMETIC-GUARD spełniony). Wyścig
cross-proces MUSI być multiprocessing — threading.Lock legacy serializuje wątki
w jednym procesie i ukryłby defekt (osobne procesy = osobne _thread_lock, wspólny
tylko system plików).
"""
import json
import multiprocessing as mp
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

import dispatch_v2.gps_pwa_store as gps


# ---------- helpers ----------

def _rec(cid, ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"lat": 53.13, "lon": 23.16, "accuracy": 5.0,
            "timestamp": ts, "source": "pwa", "name": f"K{cid}"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _legacy_reference_write(path, cid, record):
    """1:1 zachowanie sprzed A-1 (load→set→json.dump indent=2) — do bajt-parytetu."""
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    data[cid] = record
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# workery multiprocessing (modułowe = fork-safe); _after_load=sleep wymusza
# nakładające się okno load↔write: OFF → oba trzymają stary snapshot (loss),
# ON → flock serializuje (drugi startuje load dopiero po release pierwszego).
def _sleep_after_load_worker(path, cid, use_lock, sleep_s):
    gps.write_pwa_position(path, cid, _rec(cid), use_lock=use_lock,
                           _after_load=lambda: time.sleep(sleep_s))


@pytest.fixture(autouse=True)
def _isolate_store_state():
    """Kanarek trzyma stan per-proces (`_last_written`) — wyczyść MIĘDZY testami,
    inaczej cid z tmp jednego testu „znika" w tmp następnego (fałszywy vanish)."""
    gps._last_written.clear()
    yield
    gps._last_written.clear()


# ---------- negatywny oracle: defekt reprodukowalny (OFF/legacy) ----------

def test_cross_process_off_loses(tmp_path):
    path = str(tmp_path / "gps_positions_pwa.json")
    ctx = mp.get_context("fork")
    ps = [ctx.Process(target=_sleep_after_load_worker, args=(path, c, False, 0.4))
          for c in ("A", "B")]
    for p in ps:
        p.start()
    for p in ps:
        p.join(15)
    for p in ps:
        assert not p.is_alive(), "worker zawisł (nieoczekiwany deadlock w OFF)"
    disk = _read(path)
    # DEFEKT: legacy cross-proces gubi jeden cid (flock tylko na pliku tymczasowym).
    assert len(disk) == 1, f"OFF miał zgubić cid, a jest {sorted(disk)} — repro nieudane"
    assert set(disk) < {"A", "B"}


# ---------- mutation probe: fix (ON) zamyka wyścig ----------

def test_cross_process_on_preserves(tmp_path):
    path = str(tmp_path / "gps_positions_pwa.json")
    ctx = mp.get_context("fork")
    ps = [ctx.Process(target=_sleep_after_load_worker, args=(path, c, True, 0.4))
          for c in ("A", "B")]
    for p in ps:
        p.start()
    for p in ps:
        p.join(15)
    for p in ps:
        assert not p.is_alive(), "worker zawisł"
    disk = _read(path)
    # FIX: dedykowany lockfile serializuje cross-proces → OBA cid przeżywają.
    # Odwrócenie fixu (brak locka/merge) → drugi writer nadpisze pierwszego → red.
    assert set(disk) == {"A", "B"}, f"lost-update mimo ENABLE_GPS_MERGE_LOCK: {sorted(disk)}"


def test_on_reload_preserves_other_cid(tmp_path):
    """Jednoprocesowy dowód sedna merge: writer reloaduje pod lockiem i dokłada
    swój cid, nie kasując cudzych."""
    path = str(tmp_path / "gps_positions_pwa.json")
    _legacy_reference_write(path, "X", _rec("X"))
    res = gps.write_pwa_position(path, "Y", _rec("Y"), use_lock=True)
    assert res == "written"
    assert set(_read(path)) == {"X", "Y"}
    assert os.path.exists(path + gps.LOCK_SUFFIX)  # dedykowany lockfile powstał


# ---------- ratchet merge-by-ts (negatywny oracle #2) ----------

def test_merge_by_ts_stale_batch_no_rollback(tmp_path):
    path = str(tmp_path / "gps_positions_pwa.json")
    now = datetime.now(timezone.utc)
    fresh = _rec("301", ts=now.isoformat(timespec="seconds"))
    fresh["source"] = "fresh"
    assert gps.write_pwa_position(path, "301", fresh, use_lock=True) == "written"

    stale = _rec("301", ts=(now - timedelta(minutes=3)).isoformat(timespec="seconds"))
    stale["source"] = "stale"
    before = gps.GPS_PWA_STALE_SKIPPED_TOTAL
    res = gps.write_pwa_position(path, "301", stale, use_lock=True)
    assert res == "stale_skipped"
    assert _read(path)["301"]["source"] == "fresh", "spóźniony batch cofnął świeższą pozycję"
    assert gps.GPS_PWA_STALE_SKIPPED_TOTAL == before + 1


def test_equal_ts_writes(tmp_path):
    """Równy ts (ten sam sekundowy stempel) NIE jest odrzucany (>=)."""
    path = str(tmp_path / "gps_positions_pwa.json")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    a = _rec("5", ts=ts); a["source"] = "a"
    b = _rec("5", ts=ts); b["source"] = "b"
    gps.write_pwa_position(path, "5", a, use_lock=True)
    assert gps.write_pwa_position(path, "5", b, use_lock=True) == "written"
    assert _read(path)["5"]["source"] == "b"


# ---------- bajt-parytet OFF ----------

def test_off_byte_parity_vs_legacy(tmp_path):
    p_new = str(tmp_path / "new.json")
    p_ref = str(tmp_path / "ref.json")
    seq = [("10", _rec("10", "2026-08-02T10:00:00+00:00")),
           ("11", _rec("11", "2026-08-02T10:00:05+00:00")),
           ("10", _rec("10", "2026-08-02T10:00:10+00:00"))]
    for cid, r in seq:
        gps.write_pwa_position(p_new, cid, r, use_lock=False)
        _legacy_reference_write(p_ref, cid, r)
    assert open(p_new, "rb").read() == open(p_ref, "rb").read(), "OFF ≠ legacy (bajt)"
    # OFF nie zostawia śladu dodatkowego (lockfile / shadow) — footprint legacy.
    assert not os.path.exists(p_new + gps.LOCK_SUFFIX)
    assert not os.path.exists(p_new + gps.SHADOW_SUFFIX)


# ---------- flaga bramkuje zachowanie ----------

def test_flag_gates_lock(tmp_path, monkeypatch):
    path = str(tmp_path / "gps_positions_pwa.json")
    # use_lock=None → decyzja z merge_lock_enabled() (czyta ENABLE_GPS_MERGE_LOCK).
    monkeypatch.setattr(gps, "merge_lock_enabled", lambda: False)
    gps.write_pwa_position(path, "1", _rec("1"), use_lock=None)
    assert not os.path.exists(path + gps.LOCK_SUFFIX), "OFF nie powinien tworzyć lockfile"

    monkeypatch.setattr(gps, "merge_lock_enabled", lambda: True)
    gps.write_pwa_position(path, "2", _rec("2"), use_lock=None)
    assert os.path.exists(path + gps.LOCK_SUFFIX), "ON powinien utworzyć lockfile"
    assert set(_read(path)) == {"1", "2"}


# ---------- telemetria vanish (KANAREK — realnie osiągalny) ----------

def test_vanished_canary_fires_on_external_clobber(tmp_path):
    """Symulacja niezaktualizowanego bliźniaka/legacy: po naszym świeżym zapisie
    KTOŚ nadpisuje plik POZA wspólnym lockiem i gubi nasz cid → kolejny zapis tego
    procesu WYKRYWA vanish (licznik+shadow). To dowód, że kanarek nie jest martwy."""
    path = str(tmp_path / "gps_positions_pwa.json")
    v0 = gps.GPS_PWA_VANISHED_TOTAL
    gps.write_pwa_position(path, "A", _rec("A"), use_lock=True)     # nasz świeży zapis
    with open(path, "w", encoding="utf-8") as f:                    # clobber POZA lockiem
        f.write("{}")
    gps.write_pwa_position(path, "C", _rec("C"), use_lock=True)     # kolejny zapis → skan
    assert gps.GPS_PWA_VANISHED_TOTAL == v0 + 1, "kanarek NIE wykrył zgubionego cid"
    shadow = open(path + gps.SHADOW_SUFFIX, encoding="utf-8").read()
    assert '"vanished"' in shadow and '"A"' in shadow


def test_vanished_canary_no_false_positive(tmp_path):
    """Nowszy/ten sam zapis tego cid (poprawny merge drugiego procesu) NIE jest
    anomalią; zapis innego cid też nie rusza kanarka."""
    path = str(tmp_path / "gps_positions_pwa.json")
    now = datetime.now(timezone.utc)
    v0 = gps.GPS_PWA_VANISHED_TOTAL
    gps.write_pwa_position(path, "A", _rec("A", now.isoformat(timespec="seconds")), use_lock=True)
    gps.write_pwa_position(path, "A",
                           _rec("A", (now + timedelta(seconds=30)).isoformat(timespec="seconds")),
                           use_lock=True)  # nowszy zapis A — brak vanish
    gps.write_pwa_position(path, "B", _rec("B"), use_lock=True)      # inny cid — brak vanish
    assert set(_read(path)) == {"A", "B"}
    assert gps.GPS_PWA_VANISHED_TOTAL == v0, "fałszywy alarm kanarka na poprawnym zapisie"
