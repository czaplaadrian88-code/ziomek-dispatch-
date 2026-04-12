"""
Dry-run test reconcile sekcji _diff_and_emit.
Monkey-patchuje emit, update_from_event, fetch_order_details i state_get_all.
Zero kontaktu z event_bus, state.json ani panelem.

Testuje 4 scenariusze:
1. Order w state=assigned + panel closed + status_id=7 -> emit COURIER_DELIVERED
2. Order w state=assigned + panel closed + status_id=8 -> emit ORDER_RETURNED_TO_POOL (undelivered)
3. Order w state=assigned + panel closed + status_id=9 -> emit ORDER_RETURNED_TO_POOL (cancelled)
4. Order w state=assigned ale NIE w panel closed -> zero wywolan (skip)
5. 15 kandydatow w closed, MAX_RECONCILE=10 -> tylko 10 fetchow
"""
import sys
sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import panel_watcher

# === Fake state ===
FAKE_STATE = {
    "T001": {"status": "assigned", "restaurant": "Rany Julek", "delivery_address": "Test 1"},
    "T002": {"status": "assigned", "restaurant": "Zapiecek",   "delivery_address": "Test 2"},
    "T003": {"status": "picked_up","restaurant": "Halva",       "delivery_address": "Test 3"},
    "T004": {"status": "assigned", "restaurant": "Goodboy",    "delivery_address": "Test 4"},
    "T005": {"status": "delivered","restaurant": "Ignored",    "delivery_address": "Test 5"},
}

# === Preparowane responses fetch_order_details per zid ===
FAKE_DETAILS = {
    "T001": {"id_status_zamowienia": 7, "id_kurier": 111, "czas_doreczenia": "2026-04-11 15:00:00"},
    "T002": {"id_status_zamowienia": 8, "id_kurier": 222, "czas_doreczenia": None},
    "T003": {"id_status_zamowienia": 9, "id_kurier": 333, "czas_doreczenia": None},
    "T004": {"id_status_zamowienia": 7, "id_kurier": 444, "czas_doreczenia": "2026-04-11 15:01:00"},
}

# === Mock collectors ===
emitted = []
updated = []
fetched = []

def fake_emit(event_type, order_id=None, courier_id=None, payload=None, event_id=None):
    emitted.append({"event_type": event_type, "order_id": order_id, "courier_id": courier_id, "payload": payload, "event_id": event_id})
    return event_id or f"FAKE_{event_type}_{order_id}"

def fake_update_from_event(event):
    updated.append(event)
    return {"order_id": event.get("order_id"), "status": "fake_updated"}

def fake_fetch_order_details(zid, csrf=None):
    fetched.append(zid)
    return FAKE_DETAILS.get(zid)

def fake_state_get_all():
    return FAKE_STATE

# === Install mocks ===
panel_watcher.emit = fake_emit
panel_watcher.update_from_event = fake_update_from_event
panel_watcher.fetch_order_details = fake_fetch_order_details
panel_watcher.state_get_all = fake_state_get_all

# === Fake parsed (minimum do _diff_and_emit) ===
# _diff_and_emit czyta: order_ids, assigned_ids, unassigned_ids, closed_ids, delivery_addresses
# + robi state_get_all() na gorze dla current_state
# Nowe zlecenia w panelu: puste (zeby sekcja NEW nie uruchamiala sie i nie psula mocka)

def build_parsed(closed_set, delivery_addresses=None, extra_order_ids=None):
    # order_ids MUSI zawierac wszystkie znane state zids + closed
    # zeby istniejaca sekcja "zniknal z HTML" nie strzelala (w prod panel trzyma wszystkie)
    state_zids = set(panel_watcher.state_get_all().keys())
    all_ids = state_zids | set(closed_set) | set(extra_order_ids or [])
    return {
        "order_ids": sorted(all_ids),
        "assigned_ids": set(),
        "unassigned_ids": [],
        "rest_names": {},
        "courier_packs": {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": set(closed_set),
        "pickup_addresses": {},
        "delivery_addresses": delivery_addresses or {},
    }

def reset_mocks():
    emitted.clear()
    updated.clear()
    fetched.clear()

def run(name, fn):
    reset_mocks()
    try:
        fn()
        print(f"  ✅ {name}")
        return True
    except AssertionError as e:
        print(f"  ❌ {name}: {e}")
        return False
    except Exception as e:
        print(f"  💥 {name}: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return False

# === Tests ===
print("=" * 60)
print("DRY-RUN TEST reconcile _diff_and_emit")
print("=" * 60)

def test_delivered_7():
    parsed = build_parsed({"T001"}, {"T001": "Dostawa 1 Białystok"})
    panel_watcher._diff_and_emit(parsed, csrf="dummy")
    assert len(emitted) >= 1, f"oczekiwano >=1 emit, dostano {len(emitted)}"
    deliv_events = [e for e in emitted if e["event_type"] == "COURIER_DELIVERED"]
    assert len(deliv_events) == 1, f"oczekiwano 1 COURIER_DELIVERED, dostano {len(deliv_events)}"
    ev = deliv_events[0]
    assert ev["order_id"] == "T001"
    assert ev["courier_id"] == "111"
    assert ev["payload"]["delivery_address"] == "Dostawa 1 Białystok"
    assert ev["event_id"] == "T001_COURIER_DELIVERED_reconcile"
    assert len(updated) == 1 and updated[0]["event_type"] == "COURIER_DELIVERED"
    assert "T001" in fetched

def test_undelivered_8():
    parsed = build_parsed({"T002"})
    panel_watcher._diff_and_emit(parsed, csrf="dummy")
    rtp = [e for e in emitted if e["event_type"] == "ORDER_RETURNED_TO_POOL"]
    assert len(rtp) == 1, f"oczekiwano 1 ORDER_RETURNED_TO_POOL, dostano {len(rtp)}"
    assert rtp[0]["payload"]["reason"] == "undelivered"
    assert "T002_ORDER_RETURNED_undelivered_reconcile" == rtp[0]["event_id"]

def test_cancelled_9():
    parsed = build_parsed({"T003"})
    panel_watcher._diff_and_emit(parsed, csrf="dummy")
    rtp = [e for e in emitted if e["event_type"] == "ORDER_RETURNED_TO_POOL"]
    assert len(rtp) == 1
    assert rtp[0]["payload"]["reason"] == "cancelled"
    assert rtp[0]["order_id"] == "T003"

def test_not_in_closed_skip():
    # Order istnieje w state jako assigned, ale panel go NIE pokazuje jako closed.
    # Delivered reconcile NIE powinien nic robic (bo closed pusty).
    # Picked_up reconcile MOZE fetchowac assigned orders zeby sprawdzic dzien_odbioru.
    # FAKE_DETAILS zwraca status=7 ktore picked_up reconcile IGNORUJE (tylko sid=5 emituje).
    parsed = build_parsed(set())  # pusty closed
    panel_watcher._diff_and_emit(parsed, csrf="dummy")
    # Zero COURIER_DELIVERED (bo closed pusty, delivered reconcile nic nie robi)
    deliv = [e for e in emitted if e["event_type"] == "COURIER_DELIVERED"]
    assert len(deliv) == 0, f"zero COURIER_DELIVERED oczekiwane, dostano {len(deliv)}"
    # Zero COURIER_PICKED_UP (bo FAKE_DETAILS[T001-T004] maja status=7/8/9, picked_up reconcile pomija)
    pu = [e for e in emitted if e["event_type"] == "COURIER_PICKED_UP"]
    assert len(pu) == 0, f"zero COURIER_PICKED_UP oczekiwane, dostano {len(pu)}"
    # Picked_up reconcile MOZE fetchowac assigned orderow (T001, T002, T004 - T005 jest delivered w state)
    # T003 tez jest assigned (picked_up w state) - wiec 4 assigned/picked_up. Fetchy <= 4.
    assert len(fetched) <= 4, f"max 4 fetche (assigned+picked_up w state), dostano {len(fetched)}"

def test_delivered_only_for_assigned_not_delivered():
    # T005 to delivered w state - reconcile nie powinno go tykac nawet jak jest w closed
    parsed = build_parsed({"T005"})
    panel_watcher._diff_and_emit(parsed, csrf="dummy")
    assert "T005" not in fetched, "delivered w state nie powinien byc fetchowany"

def test_budget_max_10():
    # 15 sztucznych orderow w state i w closed, wszystkie status=7
    global FAKE_STATE, FAKE_DETAILS
    big_state = {f"B{i:03d}": {"status": "assigned", "delivery_address": f"ulica {i}"} for i in range(15)}
    big_details = {f"B{i:03d}": {"id_status_zamowienia": 7, "id_kurier": 900+i, "czas_doreczenia": "2026-04-11 15:00:00"} for i in range(15)}
    # Save and swap
    orig_state = FAKE_STATE
    orig_details = dict(FAKE_DETAILS)
    FAKE_STATE = big_state
    FAKE_DETAILS = big_details
    panel_watcher.state_get_all = lambda: big_state
    def fetch_big(zid, csrf=None):
        fetched.append(zid)
        return big_details.get(zid)
    panel_watcher.fetch_order_details = fetch_big

    try:
        parsed = build_parsed(set(big_state.keys()))
        panel_watcher._diff_and_emit(parsed, csrf="dummy")
        assert len(fetched) == 10, f"budzet MAX_RECONCILE_PER_CYCLE=10, fetched={len(fetched)}"
        deliv = [e for e in emitted if e["event_type"] == "COURIER_DELIVERED"]
        assert len(deliv) == 10, f"oczekiwano 10 DELIVERED, dostano {len(deliv)}"
    finally:
        # Restore
        FAKE_STATE = orig_state
        FAKE_DETAILS = orig_details
        panel_watcher.state_get_all = fake_state_get_all
        panel_watcher.fetch_order_details = fake_fetch_order_details

tests = [
    ("delivered (status 7)",      test_delivered_7),
    ("undelivered (status 8)",    test_undelivered_8),
    ("cancelled (status 9)",      test_cancelled_9),
    ("nie w closed -> skip",      test_not_in_closed_skip),
    ("delivered w state -> skip", test_delivered_only_for_assigned_not_delivered),
    ("budzet MAX 10 na cykl",     test_budget_max_10),
]
passed = 0
for name, fn in tests:
    if run(name, fn):
        passed += 1

print()
print("=" * 60)
print(f"DELIVERED RECONCILE: {passed}/{len(tests)} PASS")
print("=" * 60)
# Zapamietaj wynik, sys.exit na koncu calego pliku


# ============ PICKED_UP RECONCILE TESTY ============
# Dodane po P0#2-b. Testuje sekcje PICKED_UP RECONCILE w _diff_and_emit.

# Nowy mock collector dla touch_check_cursor
touched = []

def fake_touch_check_cursor(order_id):
    touched.append(order_id)
    # Symulujemy ustawienie cursora w fake_state
    if order_id in FAKE_STATE:
        from dispatch_v2.common import now_iso
        FAKE_STATE[order_id]["assigned_check_ts"] = now_iso()
    return True

panel_watcher.touch_check_cursor = fake_touch_check_cursor

# Rozszerz reset_mocks o touched
_orig_reset = reset_mocks
def reset_mocks_v2():
    _orig_reset()
    touched.clear()
reset_mocks = reset_mocks_v2

print()
print("=" * 60)
print("PICKED_UP RECONCILE TESTY")
print("=" * 60)

def test_pu_status_5_with_dzien_odbioru():
    """Order assigned w state, panel zwraca status=5 + dzien_odbioru -> emit PICKED_UP"""
    global FAKE_STATE, FAKE_DETAILS
    FAKE_STATE = {
        "PU001": {"status": "assigned", "restaurant": "Zapiecek", "address_id": "170", "delivery_address": "Test 1"},
    }
    FAKE_DETAILS = {
        "PU001": {
            "id_status_zamowienia": 5,
            "id_kurier": 777,
            "dzien_odbioru": "2026-04-11 18:00:00",
            "czas_doreczenia": None,
            "address": {"id": 170},
        },
    }
    panel_watcher.state_get_all = lambda: FAKE_STATE
    panel_watcher.fetch_order_details = lambda zid, csrf=None: (fetched.append(zid), FAKE_DETAILS.get(zid))[1]

    parsed = build_parsed(set())  # pusty closed - zeby delivered reconcile nic nie robilo
    panel_watcher._diff_and_emit(parsed, csrf="dummy")

    pu_events = [e for e in emitted if e["event_type"] == "COURIER_PICKED_UP"]
    assert len(pu_events) == 1, f"oczekiwano 1 COURIER_PICKED_UP, dostano {len(pu_events)}"
    ev = pu_events[0]
    assert ev["order_id"] == "PU001"
    assert ev["courier_id"] == "777"
    assert ev["payload"]["timestamp"] == "2026-04-11 18:00:00"
    assert ev["event_id"] == "PU001_COURIER_PICKED_UP_reconcile"
    assert "PU001" in touched, "cursor nie zostal przesuniety"
    assert "PU001" in fetched

def test_pu_status_2_no_pickup():
    """Order assigned w state, panel zwraca status=2 (nowy, nie odebrany) -> zero emit + touch"""
    global FAKE_STATE, FAKE_DETAILS
    FAKE_STATE = {
        "PU002": {"status": "assigned", "restaurant": "Goodboy", "address_id": "191"},
    }
    FAKE_DETAILS = {
        "PU002": {
            "id_status_zamowienia": 2,
            "id_kurier": 888,
            "dzien_odbioru": None,
            "czas_doreczenia": None,
        },
    }
    panel_watcher.state_get_all = lambda: FAKE_STATE
    panel_watcher.fetch_order_details = lambda zid, csrf=None: (fetched.append(zid), FAKE_DETAILS.get(zid))[1]

    parsed = build_parsed(set())
    panel_watcher._diff_and_emit(parsed, csrf="dummy")

    pu_events = [e for e in emitted if e["event_type"] == "COURIER_PICKED_UP"]
    assert len(pu_events) == 0, f"oczekiwano 0 PICKED_UP, dostano {len(pu_events)}"
    assert "PU002" in touched, "cursor powinien byc touchniety nawet bez emit"
    assert "PU002" in fetched

def test_pu_status_5_no_dzien_odbioru():
    """Edge case: status=5 ale dzien_odbioru=None - zero emit, tylko touch"""
    global FAKE_STATE, FAKE_DETAILS
    FAKE_STATE = {
        "PU003": {"status": "assigned", "restaurant": "Halva", "address_id": "160"},
    }
    FAKE_DETAILS = {
        "PU003": {
            "id_status_zamowienia": 5,
            "id_kurier": 999,
            "dzien_odbioru": None,  # EDGE: brak mimo status 5
            "czas_doreczenia": None,
        },
    }
    panel_watcher.state_get_all = lambda: FAKE_STATE
    panel_watcher.fetch_order_details = lambda zid, csrf=None: (fetched.append(zid), FAKE_DETAILS.get(zid))[1]

    parsed = build_parsed(set())
    panel_watcher._diff_and_emit(parsed, csrf="dummy")

    pu_events = [e for e in emitted if e["event_type"] == "COURIER_PICKED_UP"]
    assert len(pu_events) == 0, f"oczekiwano 0 PICKED_UP (brak dzien_odbioru), dostano {len(pu_events)}"
    assert "PU003" in touched

def test_pu_budget_max_10():
    """15 assigned, wszystkie status=5 z dzien_odbioru -> dokladnie 10 emit"""
    global FAKE_STATE, FAKE_DETAILS
    FAKE_STATE = {f"PB{i:03d}": {"status": "assigned", "address_id": "170"} for i in range(15)}
    FAKE_DETAILS = {
        f"PB{i:03d}": {
            "id_status_zamowienia": 5,
            "id_kurier": 100 + i,
            "dzien_odbioru": f"2026-04-11 18:{i:02d}:00",
            "czas_doreczenia": None,
        } for i in range(15)
    }
    panel_watcher.state_get_all = lambda: FAKE_STATE
    panel_watcher.fetch_order_details = lambda zid, csrf=None: (fetched.append(zid), FAKE_DETAILS.get(zid))[1]

    parsed = build_parsed(set())
    panel_watcher._diff_and_emit(parsed, csrf="dummy")

    pu_events = [e for e in emitted if e["event_type"] == "COURIER_PICKED_UP"]
    assert len(pu_events) == 10, f"budzet MAX 10, dostano {len(pu_events)}"
    assert len(touched) == 10, f"touch tez powinien byc 10, dostano {len(touched)}"

def test_pu_round_robin_order():
    """3 ordery z roznymi assigned_check_ts - najstarszy powinien byc sprawdzony pierwszy"""
    global FAKE_STATE, FAKE_DETAILS
    FAKE_STATE = {
        "RR001": {"status": "assigned", "assigned_check_ts": "2026-04-11T15:00:00+00:00"},
        "RR002": {"status": "assigned", "assigned_check_ts": "2026-04-11T14:00:00+00:00"},  # najstarszy
        "RR003": {"status": "assigned", "assigned_check_ts": None},  # nigdy -> None < str, pierwszy
    }
    FAKE_DETAILS = {
        f: {"id_status_zamowienia": 2, "id_kurier": 1, "dzien_odbioru": None} for f in FAKE_STATE
    }
    panel_watcher.state_get_all = lambda: FAKE_STATE
    panel_watcher.fetch_order_details = lambda zid, csrf=None: (fetched.append(zid), FAKE_DETAILS.get(zid))[1]

    parsed = build_parsed(set())
    panel_watcher._diff_and_emit(parsed, csrf="dummy")

    # Wszystkie 3 sprawdzone (budzet 10 >> 3)
    assert len(fetched) == 3, f"3 fetche, dostano {len(fetched)}"
    # Kolejnosc: RR003 (None = "") -> RR002 (14:00) -> RR001 (15:00)
    assert fetched[0] == "RR003", f"pierwszy powinien byc RR003 (None), jest {fetched[0]}"
    assert fetched[1] == "RR002", f"drugi powinien byc RR002 (14:00), jest {fetched[1]}"
    assert fetched[2] == "RR001", f"trzeci powinien byc RR001 (15:00), jest {fetched[2]}"

pu_tests = [
    ("PICKED_UP status 5 + dzien_odbioru",     test_pu_status_5_with_dzien_odbioru),
    ("status 2 bez pickup -> touch only",       test_pu_status_2_no_pickup),
    ("status 5 bez dzien_odbioru -> touch only",test_pu_status_5_no_dzien_odbioru),
    ("budzet MAX 10 picked_up",                 test_pu_budget_max_10),
    ("round-robin kolejnosc",                   test_pu_round_robin_order),
]
pu_passed = 0
for name, fn in pu_tests:
    if run(name, fn):
        pu_passed += 1

print()
print("=" * 60)
print(f"PICKED_UP TESTY: {pu_passed}/{len(pu_tests)} PASS")
print("=" * 60)

# Wspolny exit
all_pass = (passed == len(tests)) and (pu_passed == len(pu_tests))
print()
print("=" * 60)
print(f"LACZNIE: delivered {passed}/{len(tests)}, picked_up {pu_passed}/{len(pu_tests)}")
print("=" * 60)
sys.exit(0 if all_pass else 1)
