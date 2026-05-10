"""Czasówki fixes 2026-05-07 — Fix 1 (early_bird raw) + Fix #firmowe (czasowka fallback coords).

Adrian's report: czasówki nigdy nie mają kandydatów (97% KOORD ratio przez 5 dni).

Two root causes diagnosed (07.05 midday):

**Fix 1 (dispatch_pipeline.assess_order early_bird threshold):**
PRE: early_bird patrzy na czas_kuriera_warsaw (extended) gdy V3.19f flag ON.
     Czasowka_scheduler liczy mtp z raw pickup_at_warsaw → rozjazd źródeł czasu.
     Czasówka raw_mtp=37 (T-40 trigger window) + extension Ziomka +30min →
     ext_mtp=67 → assess_order minutes_ahead=67 ≥ EARLY_BIRD_THRESHOLD_MIN=60 →
     KOORD pool=0 → eval_czasowka emit "≤40min + zero MAYBE candidates".
POST: early_bird patrzy na RAW pickup_at_warsaw. Extension Ziomka mówi "kurier
      dotrze później" — NIE blokuje feasibility check (downstream wait_courier
      penalty dla bag≥1 i tak penalizuje).
Eliminuje 49% KOORD czasówek (`zero MAYBE` 19×/39 w 5-day eval_log).

**Fix #firmowe (czasowka_scheduler defense gate L2 fallback):**
PRE: gdy address_id ∈ FIRMOWE_KONTO_ADDRESS_IDS AND pickup_coords is None →
     KOORD/no_pickup_geocode (mimo że sprint #4 panel_watcher emit-side wpisuje
     fallback coords). Legacy state pre-deploy 07.05 morning #4 ma
     pickup_coords=None dla 471172/471173 → 20× KOORD/no_pickup_geocode.
POST: dla firmowych użyj FIRMOWE_KONTO_FALLBACK_COORDS mirror panel_watcher
      logic. Mutate local order_state copy NIE persist (state-machine SoT).
Eliminuje 51% KOORD czasówek (no_pickup_geocode 20×/39 w 5-day eval_log).

Lekcja #80 reinforced: consumer audit przy boundary changes.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C
from dispatch_v2 import czasowka_scheduler as cs
from dispatch_v2 import dispatch_pipeline
from dispatch_v2.courier_resolver import CourierState


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: early_bird threshold uses raw pickup_at_warsaw, not extended
# ─────────────────────────────────────────────────────────────────────────────

def _build_order_event(
    now_utc: datetime,
    raw_mtp_min: float,
    ext_mtp_min: float | None = None,
    pickup_coords=(53.13, 23.17),
):
    """Build assess_order order_event z raw + optional extension."""
    raw_iso = (now_utc + timedelta(minutes=raw_mtp_min)).astimezone().isoformat()
    ev = {
        "order_id": "TEST_EARLY_BIRD",
        "restaurant": "Test Restauracja",
        "pickup_address": "Pickup 1",
        "pickup_city": "Białystok",
        "delivery_address": "Drop 1",
        "delivery_city": "Białystok",
        "pickup_at_warsaw": raw_iso,
        "pickup_coords": list(pickup_coords),
        "delivery_coords": [53.14, 23.16],
        "status_id": 2,
        "first_seen": (now_utc - timedelta(minutes=5)).isoformat(),
        "address_id": 1,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }
    if ext_mtp_min is not None:
        ev["czas_kuriera_warsaw"] = (now_utc + timedelta(minutes=ext_mtp_min)).astimezone().isoformat()
    return ev


def test_fix1_early_bird_uses_raw_not_extended_czasowka_with_extension():
    """KEY: czasówka raw_mtp=40 (T-40 trigger) + extension delta +30min (ext_mtp=70).
    PRE-FIX: early_bird patrzył na ext=70 ≥ 60 → KOORD pool=0.
    POST-FIX: early_bird patrzy na raw=40 < 60 → przejdzie do feasibility loop.
    """
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    ev = _build_order_event(now_utc, raw_mtp_min=40.0, ext_mtp_min=70.0)

    fleet = {}  # empty fleet — assess_order returns NO_CANDIDATES, NIE early_bird
    res = dispatch_pipeline.assess_order(ev, fleet, now=now_utc)

    # KEY ASSERTION: NIE early_bird (raw=40 < 60). Verdict może być NO_CANDIDATES
    # bo fleet pusty, ale reason musi być różny od early_bird.
    assert "early_bird" not in (res.reason or ""), \
        f"FIX 1 BROKEN: early_bird fired mimo raw=40min < 60. reason={res.reason!r}"


def test_fix1_early_bird_still_kicks_in_when_raw_above_threshold():
    """SANITY: raw_mtp=70 (>60) + ext_mtp=80 → early_bird KOORD (oba >60).
    Verify że fix nie wyłączył early_bird globalnie.
    """
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    ev = _build_order_event(now_utc, raw_mtp_min=70.0, ext_mtp_min=80.0)

    res = dispatch_pipeline.assess_order(ev, {}, now=now_utc)
    assert res.verdict == "KOORD", f"oczekiwane KOORD dla raw=70min, got {res.verdict}"
    assert "early_bird" in (res.reason or ""), \
        f"oczekiwane early_bird w reason, got {res.reason!r}"


def test_fix1_early_bird_no_extension_falls_back_to_raw():
    """Backward-compat: order BEZ extension (czas_kuriera_warsaw=None) → patrzy na
    pickup_at_warsaw (raw). Identyczny path jak Fix 1.
    """
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    # raw=70, brak extension
    ev = _build_order_event(now_utc, raw_mtp_min=70.0, ext_mtp_min=None)
    res = dispatch_pipeline.assess_order(ev, {}, now=now_utc)
    assert res.verdict == "KOORD"
    assert "early_bird" in (res.reason or "")

    # raw=40, brak extension → przejdzie
    ev2 = _build_order_event(now_utc, raw_mtp_min=40.0, ext_mtp_min=None)
    res2 = dispatch_pipeline.assess_order(ev2, {}, now=now_utc)
    assert "early_bird" not in (res2.reason or ""), \
        f"raw=40 bez extension nie powinien fire early_bird: {res2.reason!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Fix #firmowe: czasowka_scheduler L2 defense gate uses fallback coords
# ─────────────────────────────────────────────────────────────────────────────

def _firmowe_order_state(now_utc: datetime, mins_to_pickup: float = 45.0) -> dict:
    """Order state mimicking firmowe Nadajesz.pl z pickup_coords=None (legacy state)."""
    return {
        "order_id": "FIRMOWE_TEST",
        "status": "planned",
        "courier_id": "26",
        "prep_minutes": 90,
        "pickup_at_warsaw": (now_utc + timedelta(minutes=mins_to_pickup)).astimezone().isoformat(),
        "first_seen": (now_utc - timedelta(minutes=10)).isoformat(),
        "updated_at": now_utc.isoformat(),
        "restaurant": "Nadajesz.pl",
        "delivery_address": "Some Drop 1",
        "pickup_address": "Centrala",
        "pickup_coords": None,  # KEY: brak coords, legacy state
        "delivery_coords": [53.14, 23.16],
        "address_id": 161,  # KEY: firmowe konto address_id
        "pickup_city": "Białystok",
        "delivery_city": "Białystok",
        "order_type": "czasowka",
        "status_id": 2,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }


def test_fix_firmowe_czasowka_uses_fallback_coords_when_pickup_coords_none():
    """Firmowe Nadajesz.pl (address_id=161) z pickup_coords=None → eval_czasowka
    NIE returns KOORD/no_pickup_geocode. Zamiast tego mutate order_state z
    FIRMOWE_KONTO_FALLBACK_COORDS i forward do assess_order.
    """
    now_utc = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    order_state = _firmowe_order_state(now_utc, mins_to_pickup=45.0)

    fake_courier = CourierState(
        courier_id="414",
        pos=(53.13, 23.16),
        pos_source="gps",
        shift_end=now_utc + timedelta(hours=4),
    )
    fake_res = mock.MagicMock()
    fake_res.best = None
    fake_res.candidates = []

    captured_order_event = {}
    def _capture_assess(order_event, fleet, now=None):
        captured_order_event.update(order_event)
        return fake_res

    with mock.patch.object(cs.courier_resolver, "dispatchable_fleet",
                           return_value=[fake_courier]), \
         mock.patch.object(cs, "assess_order", side_effect=_capture_assess):
        result = cs.eval_czasowka("FIRMOWE_TEST", order_state, now_utc)

    # KEY ASSERTIONS:
    # 1. NIE no_pickup_geocode reason
    assert result.get("reason") != "no_pickup_geocode", \
        f"FIX #firmowe BROKEN: KOORD/no_pickup_geocode wciąż firował dla address_id=161. result={result}"
    # 2. assess_order został zawołany z fallback coords (NIE None)
    assert captured_order_event.get("pickup_coords") == list(C.FIRMOWE_KONTO_FALLBACK_COORDS), \
        f"oczekiwane fallback coords w order_event, got {captured_order_event.get('pickup_coords')}"


def test_fix_firmowe_non_firmowe_still_koord_no_geocode():
    """Sanity: nie-firmowe order (address_id != 161) z pickup_coords=None
    nadal triggers KOORD/no_pickup_geocode (zachowuje legacy behavior).
    """
    now_utc = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    order_state = _firmowe_order_state(now_utc, mins_to_pickup=45.0)
    order_state["address_id"] = 999  # NIE firmowe

    with mock.patch.object(cs.courier_resolver, "dispatchable_fleet", return_value=[]), \
         mock.patch.object(cs, "assess_order") as mock_assess:
        result = cs.eval_czasowka("NON_FIRMOWE_TEST", order_state, now_utc)

    assert result.get("decision") == "KOORD"
    assert result.get("reason") == "no_pickup_geocode", \
        f"non-firmowe powinien KOORD/no_pickup_geocode, got reason={result.get('reason')!r}"
    mock_assess.assert_not_called()  # short-circuit przed assess_order call


def test_fix_firmowe_does_not_persist_fallback_coords_to_state():
    """Defense: fallback coords mutate LOCAL copy order_state (NIE state file).
    Verify że pickup_coords=None w original state pozostaje None (przekazany dict
    nie jest mutowany — ważne dla state machine atomic-write semantics).
    """
    now_utc = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    original_state = _firmowe_order_state(now_utc, mins_to_pickup=45.0)
    state_id_before = id(original_state)

    fake_res = mock.MagicMock()
    fake_res.best = None
    fake_res.candidates = []

    with mock.patch.object(cs.courier_resolver, "dispatchable_fleet", return_value=[]), \
         mock.patch.object(cs, "assess_order", return_value=fake_res):
        cs.eval_czasowka("FIRMOWE_TEST", original_state, now_utc)

    # ORIGINAL state nadal ma pickup_coords=None — NIE zostało persistowane
    assert original_state["pickup_coords"] is None, \
        f"FIX #firmowe REGRESSION: original state pickup_coords mutated to {original_state['pickup_coords']}"
    assert id(original_state) == state_id_before  # nie-zaskakująca paranoja
