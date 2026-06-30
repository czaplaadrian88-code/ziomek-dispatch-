"""Force-recheck czasów na żądanie koordynatora (przycisk „Odśwież czas z rutcomu").

Pokrywa:
  • kolejka coordinator_time_recheck: enqueue → drain (roundtrip, czyszczenie, TTL),
  • EFEKT flagi ENABLE_COORDINATOR_FORCE_TIME_RECHECK przez parametr `deliberate`
    (klik koordynatora): ON (deliberate=True) ściąga zmianę elastyka w OBIE strony,
    OFF (deliberate=False) trzyma stary strażnik forward-only — ON≠OFF,
  • czasówka: kanał czas_kuriera ZAWSZE suppress (też deliberate) — committed=pickup_at.
"""
import importlib
import json
import os
import tempfile

import pytest

ctr = importlib.import_module("dispatch_v2.coordinator_time_recheck")
from dispatch_v2.panel_watcher import _diff_czas_kuriera, _diff_pickup_time


@pytest.fixture
def tmp_queue(monkeypatch):
    d = tempfile.mkdtemp()
    qp = os.path.join(d, "coordinator_time_recheck.json")
    monkeypatch.setattr(ctr, "QUEUE_PATH", qp)
    monkeypatch.setattr(ctr, "LOCK_PATH", qp + ".lock")
    return qp


def test_enqueue_drain_roundtrip(tmp_queue):
    assert ctr.enqueue(["111", "222"]) == 2
    assert json.load(open(tmp_queue)).keys() >= {"111", "222"}
    assert ctr.drain() == {"111", "222"}
    assert ctr.drain() == set()                 # wyczyszczone po drenażu
    assert json.load(open(tmp_queue)) == {}


def test_enqueue_dedup_refreshes_ttl(tmp_queue):
    ctr.enqueue(["333"])
    ctr.enqueue(["333"])                          # ponowny klik = idempotentny
    assert ctr.drain() == {"333"}


def test_drain_drops_expired(tmp_queue):
    # ręcznie wstaw przeterminowany wpis (TTL 5 min) — drain go wyrzuca, nie zwraca
    json.dump({"999": "2020-01-01T00:00:00+00:00"}, open(tmp_queue, "w"))
    assert ctr.drain() == set()
    assert json.load(open(tmp_queue)) == {}


# ---- EFEKT flagi (deliberate = klik koordynatora, włączany przez
#      ENABLE_COORDINATOR_FORCE_TIME_RECHECK): ON≠OFF dla elastyka w tył ----

_OLD_ELASTYK = {"czas_kuriera_warsaw": "2026-06-30T15:00:00+02:00",
                "czas_kuriera_hhmm": "15:00", "order_type": "elastic", "courier_id": "1"}
_FRESH_BACK = {"czas_kuriera_warsaw": "2026-06-30T14:30:00+02:00", "czas_kuriera_hhmm": "14:30"}


def test_elastyk_backward_blocked_when_not_deliberate():
    # OFF (automat): forward-only blokuje cofnięcie elastyka → brak eventu
    assert _diff_czas_kuriera(_OLD_ELASTYK, _FRESH_BACK, oid="9", deliberate=False) is None


def test_elastyk_backward_pulled_when_deliberate():
    # ON (klik): ściągamy w tył, źródło coordinator_force (state_machine przepuści)
    evt = _diff_czas_kuriera(_OLD_ELASTYK, _FRESH_BACK, oid="9", deliberate=True)
    assert evt is not None
    assert evt["payload"]["source"] == "coordinator_force"
    assert evt["payload"]["new_ck_hhmm"] == "14:30"


def test_czasowka_ck_suppressed_even_deliberate():
    # czasówka: czas_kuriera to śmieć (committed=pickup_at) — suppress ZAWSZE
    old = {"czas_kuriera_warsaw": "2026-06-30T16:00:00+02:00", "czas_kuriera_hhmm": "16:00",
           "order_type": "czasowka", "prep_minutes": 90, "courier_id": "1"}
    fresh = {"czas_kuriera_warsaw": "2026-06-30T15:04:00+02:00", "czas_kuriera_hhmm": "15:04"}
    assert _diff_czas_kuriera(old, fresh, oid="8", deliberate=True) is None


def test_czasowka_pickup_channel_pulled_when_deliberate():
    # czasówka idzie kanałem pickup_at (mirror→czas_kuriera w state_machine)
    old = {"pickup_at_warsaw": "2026-06-30T16:00:00+02:00", "order_type": "czasowka",
           "prep_minutes": 90, "courier_id": "1"}
    fresh = {"pickup_at_warsaw": "2026-06-30T17:10:00+02:00"}
    evt = _diff_pickup_time(old, fresh, oid="8", deliberate=True)
    assert evt is not None
    assert evt["payload"]["source"] == "coordinator_force"
