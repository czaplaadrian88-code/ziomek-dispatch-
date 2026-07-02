"""L2.2 (+L2.3) — catch-all _v328_eval_safe ROZRÓŻNIA przyczyny + głośny fail-open grafiku.

Zakres (fala L2 audytu, most K5):
  * klasyfikacja przyczyny fail-u kuriera: data_poison (fail-loud strażnik coords)
    vs real_bug (nieoczekiwany wyjątek); anty-dryf = klasyfikator testowany na
    REALNYCH wyjątkach strażnika (osrm_client.haversine), nie na stringach z ręki;
  * doczepienie result.v328_fail_causes we WSPÓLNYM LEJKU _classify_and_set_auto_route
    + serializacja top-level w shadow_dispatcher._serialize_result (metryka w jsonl);
  * zbiorczy operator-alert data-poison za flagą ENABLE_V328_POISON_ALERT
    (default OFF → inert; ON ≠ OFF; okno+próg+realert — nie spam per-zdarzenie);
  * L2.3: is_on_shift fail-open (brak grafiku / brak w grafiku / brak godzin /
    błąd parsowania) → log.warning z dedupem (koniec cichego 24/7, wzór FAIL12).
"""
import logging
from datetime import datetime, timezone

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2.osrm_client import haversine


# ── klasyfikator: kontrakt z REALNYM strażnikiem (anty-dryf sygnatur) ────────
def _guard_exc(*args):
    with pytest.raises(ValueError) as ei:
        haversine(*args)
    return ei.value


def test_classify_data_poison_from_real_haversine_none_guard():
    exc = _guard_exc(None, (53.13, 23.16))
    assert dp._v328_classify_fail_cause(exc) == "data_poison"


def test_classify_data_poison_from_real_haversine_sentinel_guard():
    exc = _guard_exc((0.0, 0.0), (53.13, 23.16))
    assert dp._v328_classify_fail_cause(exc) == "data_poison"


def test_classify_real_bug_for_unexpected_exceptions():
    assert dp._v328_classify_fail_cause(KeyError("pickup_coords")) == "real_bug"
    assert dp._v328_classify_fail_cause(TypeError("unsupported operand")) == "real_bug"
    # ValueError NIE-coords też jest real_bug (klasyfikacja po sygnaturze strażnika)
    assert dp._v328_classify_fail_cause(ValueError("invalid literal for int()")) == "real_bug"


# ── lejek: v328_fail_causes doczepiane do result + serializacja ─────────────
def _mk_result(**kw):
    base = dict(order_id="483000", verdict="PROPOSE", reason="t", best=None,
                candidates=[], pickup_ready_at=None, restaurant="R",
                delivery_address="A")
    base.update(kw)
    return dp.PipelineResult(**base)


def test_funnel_attaches_fail_causes_to_result():
    res = _mk_result()
    dp._classify_and_set_auto_route(
        res, None, None, now=datetime.now(timezone.utc),
        v328_fail_causes={"413": "data_poison", "123": "real_bug"})
    assert res.v328_fail_causes == {"413": "data_poison", "123": "real_bug"}


def test_funnel_leaves_none_when_zero_fails():
    res = _mk_result()
    dp._classify_and_set_auto_route(res, None, None,
                                    now=datetime.now(timezone.utc),
                                    v328_fail_causes={})
    assert res.v328_fail_causes is None  # zero fail-ów → None (czysto), nie {}


def test_serializer_emits_v328_fail_causes_top_level():
    from dispatch_v2.shadow_dispatcher import _serialize_result
    res = _mk_result()
    res.v328_fail_causes = {"413": "data_poison"}
    rec = _serialize_result(res, "evt-1", 12.3)
    assert rec["v328_fail_causes"] == {"413": "data_poison"}
    rec2 = _serialize_result(_mk_result(), "evt-2", 1.0)
    assert rec2["v328_fail_causes"] is None  # klucz obecny ZAWSZE (grep-owalny)


# ── zbiorczy alert: flaga ON≠OFF + okno/próg/realert ────────────────────────
class _AlertSpy:
    def __init__(self):
        self.sent = []

    def __call__(self, msg, priority="low"):
        self.sent.append((msg, priority))


@pytest.fixture
def alert_spy(monkeypatch):
    spy = _AlertSpy()
    import dispatch_v2.telegram_utils as tg
    monkeypatch.setattr(tg, "send_admin_alert", spy)
    return spy


def _feed(state, n, t0=1000.0, oid="483001"):
    sent = 0
    for i in range(n):
        if dp._v328_maybe_poison_alert(oid, ["413"], now_ts=t0 + i, _state=state):
            sent += 1
    return sent


def test_alert_off_by_default_inert(alert_spy, monkeypatch):
    monkeypatch.setattr(C, "ENABLE_V328_POISON_ALERT", False)
    monkeypatch.setattr(C, "flag", lambda name, default=None: default)
    state = {"events": [], "last_sent_ts": 0.0}
    assert _feed(state, 20) == 0
    assert alert_spy.sent == []
    assert state["events"] == []  # OFF = zero zbierania (inert)


def test_alert_on_fires_after_threshold_and_realerts_throttled(alert_spy, monkeypatch):
    monkeypatch.setattr(C, "flag", lambda name, default=None: (
        True if name == "ENABLE_V328_POISON_ALERT" else default))
    state = {"events": [], "last_sent_ts": 0.0}
    thr = int(C.V328_POISON_ALERT_MIN_EVENTS)
    # baza czasu > realert-interwał (last_sent_ts=0.0 = "nigdy nie wysłano")
    base = float(C.V328_POISON_ALERT_REALERT_SEC) * 10.0
    # poniżej progu: cisza
    assert _feed(state, thr - 1, t0=base) == 0 and alert_spy.sent == []
    # próg osiągnięty: DOKŁADNIE jeden alert (realert throttluje kolejne)
    assert _feed(state, 10, t0=base + thr) == 1
    assert len(alert_spy.sent) == 1
    msg, prio = alert_spy.sent[0]
    assert "DATA-POISON" in msg and "413" in msg and prio == "low"
    # po upływie realert-interwału ORAZ ponownym osiągnięciu progu: drugi alert
    # (okno przycina stare zdarzenia — pojedynczy świeży poison NIE alertuje)
    later = base + float(C.V328_POISON_ALERT_REALERT_SEC) + 100.0
    assert dp._v328_maybe_poison_alert("483008", ["413"], now_ts=later, _state=state) is False
    assert _feed(state, thr, t0=later + 1) == 1
    assert len(alert_spy.sent) == 2


def test_alert_ignores_pure_real_bug_orders(alert_spy, monkeypatch):
    monkeypatch.setattr(C, "flag", lambda name, default=None: (
        True if name == "ENABLE_V328_POISON_ALERT" else default))
    state = {"events": [], "last_sent_ts": 0.0}
    for i in range(20):
        assert dp._v328_maybe_poison_alert("483002", [], now_ts=2000.0 + i,
                                           _state=state) is False
    assert alert_spy.sent == []  # real_bug bez poison → zero alertu (osobna klasa)


def test_alert_window_prunes_old_events(alert_spy, monkeypatch):
    monkeypatch.setattr(C, "flag", lambda name, default=None: (
        True if name == "ENABLE_V328_POISON_ALERT" else default))
    state = {"events": [], "last_sent_ts": 0.0}
    window_s = float(C.V328_POISON_ALERT_WINDOW_MIN) * 60.0
    thr = int(C.V328_POISON_ALERT_MIN_EVENTS)
    # thr-1 zdarzeń dawno temu + 1 świeże → okno przycięte, próg NIE osiągnięty
    _feed(state, thr - 1, t0=100.0)
    assert dp._v328_maybe_poison_alert("483003", ["413"],
                                       now_ts=100.0 + window_s + 10_000.0,
                                       _state=state) is False
    assert len(state["events"]) == 1 and alert_spy.sent == []


# ── E2E: realny łańcuch detonacji (wzór test_coord_sentinel_ingest_l21) ──────
# L2.1 OFF (legacy): zatruty plan (0,0) → haversine ValueError w serializerze
# metryk → _v328_eval_safe łapie → MUSI sklasyfikować data_poison i dowieźć
# ją przez lejek do result + serializera (wszystkie dotknięte warstwy).
from datetime import timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from dispatch_v2 import plan_manager  # noqa: E402


def _poisoned_plan(free_in_min=5.0):
    at = (datetime.now(timezone.utc) + timedelta(minutes=free_in_min)).isoformat()
    return {
        "stops": [{"order_id": "B1", "type": "dropoff",
                   "coords": {"lat": 0.0, "lng": 0.0}, "predicted_at": at}],
    }


def _fleet_with_poisoned_plan():
    now = datetime.now(timezone.utc)
    bag = [{
        "order_id": "B1", "status": "picked_up", "restaurant": "R1",
        "pickup_coords": [53.128, 23.152], "delivery_coords": [53.14, 23.17],
        "picked_up_at": (now - timedelta(minutes=8)).isoformat(),
    }]
    poisoned = SimpleNamespace(
        courier_id="C515", name="Test C515", pos=(53.131, 23.160),
        pos_source="gps", pos_age_min=1.0,
        shift_end=now + timedelta(hours=4), shift_start_min=0,
        bag=bag,
    )
    clean = SimpleNamespace(
        courier_id="C100", name="Test C100", pos=(53.125, 23.150),
        pos_source="gps", pos_age_min=1.0,
        shift_end=now + timedelta(hours=4), shift_start_min=0,
        bag=[],
    )
    return {"C515": poisoned, "C100": clean}


def _order_event_e2e():
    now = datetime.now(timezone.utc)
    return {
        "order_id": "NEW1", "restaurant": "R2",
        "pickup_address": "Pickup 2", "pickup_city": "Białystok",
        "delivery_address": "Drop 2", "delivery_city": "Białystok",
        "pickup_at_warsaw": (now + timedelta(minutes=15)).astimezone().isoformat(),
        "pickup_coords": [53.121879, 23.146168],
        "delivery_coords": [53.135, 23.155],
        "status_id": 2,
        "first_seen": (now - timedelta(minutes=2)).isoformat(),
        "address_id": 1,
        "czas_kuriera_warsaw": None, "czas_kuriera_hhmm": None,
    }


def test_e2e_v328_eject_classified_and_serialized(monkeypatch):
    from dispatch_v2.shadow_dispatcher import _serialize_result
    monkeypatch.setattr(C, "ENABLE_COORD_SENTINEL_INGEST_GUARD", False)
    monkeypatch.setattr(
        plan_manager, "load_plan",
        lambda cid, *a, **k: _poisoned_plan() if str(cid) == "C515" else None)
    res = dp.assess_order(_order_event_e2e(), _fleet_with_poisoned_plan(),
                          now=datetime.now(timezone.utc))
    cids = {str(c.courier_id) for c in res.candidates}
    assert "C515" not in cids and "C100" in cids  # detonacja jak w L2.1 e2e
    assert res.v328_fail_causes is not None, "fail causes nie doczepione do result"
    assert res.v328_fail_causes.get("C515") == "data_poison", res.v328_fail_causes
    rec = _serialize_result(res, "evt-e2e", 1.0)
    assert rec["v328_fail_causes"].get("C515") == "data_poison"


# ── L2.3: is_on_shift fail-open = GŁOŚNY (log.warning + dedup) ───────────────
import schedule_utils as su  # noqa: E402  (workspace scripts, jak w test_v325_step_a_r02)


@pytest.fixture
def fresh_failopen_state(monkeypatch):
    monkeypatch.setattr(su, "_FAIL_OPEN_LAST_WARN", {})
    monkeypatch.setattr(su, "_FAIL_OPEN_WARN_INTERVAL_S", 3600.0)


def test_is_on_shift_empty_schedule_warns_failopen(caplog, fresh_failopen_state):
    with caplog.at_level(logging.WARNING, logger="schedule_utils"):
        on, reason = su.is_on_shift("Jan K", {})
    assert on is True and reason == "brak grafiku"
    assert any("SHIFT_FAIL_OPEN" in r.message for r in caplog.records)


def test_is_on_shift_unknown_courier_warns_failopen(caplog, fresh_failopen_state):
    schedule = {"Adam Nowak": {"start": "09:00", "end": "17:00"}}
    with caplog.at_level(logging.WARNING, logger="schedule_utils"):
        on, reason = su.is_on_shift("Zenon Nieistniejący", schedule)
    assert on is True and reason == "nie znaleziono w grafiku"
    assert any("SHIFT_FAIL_OPEN" in r.message for r in caplog.records)


def test_is_on_shift_failopen_warning_deduped(caplog, fresh_failopen_state):
    with caplog.at_level(logging.WARNING, logger="schedule_utils"):
        su.is_on_shift("Jan K", {})
        su.is_on_shift("Jan K", {})  # ten sam (kurier, powód) < interwał → cisza
    warns = [r for r in caplog.records if "SHIFT_FAIL_OPEN" in r.message]
    assert len(warns) == 1


def test_is_on_shift_fail_closed_branch_does_not_warn(caplog, fresh_failopen_state):
    # kurier znaleziony, ale dziś NIE pracuje → False (fail-CLOSED) — bez warna
    schedule = {"Adam Nowak": None}
    with caplog.at_level(logging.WARNING, logger="schedule_utils"):
        on, reason = su.is_on_shift("Adam Nowak", schedule)
    assert on is False
    assert not any("SHIFT_FAIL_OPEN" in r.message for r in caplog.records)
