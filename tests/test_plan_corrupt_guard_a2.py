"""A-2 (2026-08-02): guard korupcji ``courier_plans.json``.

PROBLEM: ładowanie planów przy korupcji CICHO zwracało ``{}`` (pusty plan) →
silnik gubił CAŁY plan floty i re-planował od zera, bez śladu (utrata stanu/
decyzji). FIX U ŹRÓDŁA (``plan_manager._read_raw`` = jeden kanoniczny owner),
za flagą ``ENABLE_PLAN_CORRUPT_RAISE`` (ETAP4, shadow-first, DEFAULT OFF):
  * backup ``.prev`` (ostatni dobry plan) na każdym udanym zapisie;
  * odczyt uszkodzonego pliku → recovery z ``.prev`` zamiast ``{}``;
  * brak/uszkodzony ``.prev`` → RAISE (nie ciche ``{}``); mutator NIE resetuje
    po cichu całego stanu floty;
  * CAS ``expected_version`` („nie nadpisać nowszego") zachowany przez recovery.

Zakres testów:
  - ORACLE NEGATYWNY: korupcja + brak .prev + flaga ON → RAISE (nie ``{}``);
  - MUTACJA/parytet: ta sama korupcja + flaga OFF → legacy ``{}`` (ON≠OFF);
  - RECOVERY z .prev; backup .prev na zapisie; brak backupu przy OFF;
  - MUTATOR nie resetuje stanu po cichu (save RAISE zamiast nadpisania ``{}``);
  - CAS ``expected_version`` przez recovery + współbieżny zapis (nie nadpisać
    nowszego); współbieżni writerzy → plik ważny + wersja monotoniczna;
  - RATCHET: ``_read_raw`` NIGDY nie zwraca cicho ``{}`` na korupcji (flaga ON).
"""
import json
import threading

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import plan_manager as PM


def _body(tag="base"):
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": tag},
        "start_ts": "2026-08-02T12:00:00+00:00",
        "stops": [{
            "order_id": tag,
            "type": "dropoff",
            "coords": {"lat": 53.14, "lng": 23.16},
            "dwell_min": 1.0,
            "status_at_plan_time": "assigned",
        }],
        "optimization_method": "incremental",
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Sandbox PLANS_FILE/LOCK_FILE do tmp. ``.prev`` jest POCHODNA od PLANS_FILE
    (``_prev_path``) → przekierowuje się automatycznie (HERMETIC-safe)."""
    monkeypatch.setattr(PM, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    with PM._perf_plans_lock:
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None
    return tmp_path


def _flag_on(monkeypatch):
    # conftest wycina klucze ETAP4 z tmp flags.json → decision_flag() spada na
    # stałą modułu; patch stałej = ENABLE_PLAN_CORRUPT_RAISE ON tylko w tym teście.
    monkeypatch.setattr(C, "ENABLE_PLAN_CORRUPT_RAISE", True, raising=False)


def _flag_off(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_PLAN_CORRUPT_RAISE", False, raising=False)


def _corrupt_main(store):
    (store / "courier_plans.json").write_text("{ this is : not json ]", encoding="utf-8")
    with PM._perf_plans_lock:  # unieważnij read-cache (korupcja poza _write_raw)
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None


# ── ORACLE NEGATYWNY: korupcja + brak .prev + flaga ON → RAISE (nie {}) ───────
def test_corrupt_no_prev_raises_when_flag_on(store, monkeypatch):
    _flag_on(monkeypatch)
    _corrupt_main(store)
    assert not PM._prev_path().exists()
    with pytest.raises((json.JSONDecodeError, ValueError)):
        PM.load_plans()
    with pytest.raises((json.JSONDecodeError, ValueError)):
        PM.load_plan("9")


# ── MUTACJA/parytet: ta sama korupcja + flaga OFF → legacy {} (ON≠OFF) ────────
def test_corrupt_flag_off_is_legacy_silent(store, monkeypatch):
    """Odwrócenie fixu (flaga OFF) = zachowanie SPRZED A-2: ciche {} / None.
    Kontrast do oracle powyżej = dowód ON≠OFF."""
    _flag_off(monkeypatch)
    _corrupt_main(store)
    assert PM.load_plans() == {}
    assert PM.load_plan("9") is None


# ── RECOVERY: korupcja + zdrowy .prev → ostatni dobry plan (nie {}) ───────────
def test_corrupt_recovers_from_prev_when_flag_on(store, monkeypatch):
    _flag_on(monkeypatch)
    PM.save_plan("9", _body("good-v1"))    # main=v1, .prev=v1
    PM.save_plan("9", _body("good-v2"))    # main=v2, .prev=v2
    assert PM._prev_path().exists()
    good = PM.load_plans()
    assert good["9"]["start_pos"]["source"] == "good-v2"

    _corrupt_main(store)

    recovered = PM.load_plans()
    assert recovered == good               # ostatni dobry plan odtworzony
    assert recovered["9"]["plan_version"] == 2
    one = PM.load_plan("9")
    assert one is not None and one["start_pos"]["source"] == "good-v2"


# ── backup .prev powstaje na zapisie (ON); brak nowych plików przy OFF ────────
def test_prev_backup_written_on_save_flag_on(store, monkeypatch):
    _flag_on(monkeypatch)
    assert not PM._prev_path().exists()
    PM.save_plan("9", _body("v1"))
    assert PM._prev_path().exists()
    prev = json.loads(PM._prev_path().read_text(encoding="utf-8"))
    assert prev["9"]["start_pos"]["source"] == "v1"


def test_no_prev_backup_when_flag_off(store, monkeypatch):
    _flag_off(monkeypatch)
    PM.save_plan("9", _body("v1"))
    assert not PM._prev_path().exists()    # shadow-first: OFF = zero nowych artefaktów


# ── MUTATOR nie resetuje stanu po cichu: korupcja bez .prev → save RAISE ──────
def test_mutator_raises_not_resets_on_unrecoverable_corruption(store, monkeypatch):
    """Krytyczna ścieżka WRITE: pod flagą ON save_plan na nieodwracalnej korupcji
    RAISE zamiast odczytać {} i nadpisać (= reset planów całej floty)."""
    _flag_on(monkeypatch)
    _corrupt_main(store)                    # brak .prev
    with pytest.raises((json.JSONDecodeError, ValueError)):
        PM.save_plan("9", _body("would-reset"))


# ── CAS „nie nadpisać nowszego" zachowany przez recovery ─────────────────────
def test_cas_preserved_through_recovery(store, monkeypatch):
    _flag_on(monkeypatch)
    PM.save_plan("9", _body("v1"))          # v1; .prev=v1
    _corrupt_main(store)                     # główny uszkodzony, .prev trzyma v1

    # writer zgodny z .prev (expected_version=1) → recovery + zapis v2, heal main
    saved = PM.save_plan("9", _body("v2"), expected_version=1)
    assert saved["plan_version"] == 2
    healed = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))
    assert healed["9"]["plan_version"] == 2  # główny plik uzdrowiony

    # spóźniony writer ze starym expected_version=1 → NIE nadpisuje nowszego (v2)
    before = PM.cas_conflicts_total()
    with pytest.raises(PM.ConcurrencyError):
        PM.save_plan("9", _body("stale"), expected_version=1)
    final = PM.load_plan("9")
    assert final["plan_version"] == 2
    assert final["start_pos"]["source"] == "v2"
    assert PM.cas_conflicts_total() == before + 1


# ── współbieżni writerzy (ON): plik ważny + wersja monotoniczna ───────────────
def test_concurrent_writers_flag_on_keep_valid_and_monotonic(store, monkeypatch):
    _flag_on(monkeypatch)
    PM.save_plan("1", _body("init"))         # v1

    errors = []

    def worker():
        try:
            for _ in range(25):
                PM.save_plan("1", _body("w"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    final = PM.load_plan("1")
    assert final["plan_version"] == 1 + 2 * 25   # monotonic, żaden zapis nie zgubiony
    assert isinstance(PM.load_plans(), dict)       # główny plik = ważny JSON
    assert isinstance(json.loads(PM._prev_path().read_text(encoding="utf-8")), dict)


# ── RATCHET: _read_raw NIGDY nie zwraca cicho {} na korupcji (flaga ON) ───────
def test_read_raw_never_silent_empty_on_corruption_flag_on(store, monkeypatch):
    """Blokuje powrót silent-{}: jedyny owner ładowania (_read_raw) MUSI podnieść
    wyjątek na korupcji bez .prev przy fladze ON. Return {} = czerwony."""
    _flag_on(monkeypatch)
    _corrupt_main(store)
    with PM._locked(exclusive=False):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            PM._read_raw()
