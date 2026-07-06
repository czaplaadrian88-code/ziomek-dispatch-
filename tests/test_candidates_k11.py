"""K11 (refaktor, 2026-07-06): pętla per-kurier w core.candidates — kontrakty przenosin.

Parytet treści dowodzi replay różnicowy na korpusie (bramka world_replay_gate,
master↔gałąź); tu strażnicy KONTRAKTÓW, które przenosiny mogłyby cicho zerwać:
1. monkeypatch `dispatch_pipeline.check_feasibility_v2` obowiązuje w core.candidates
   (kontrakt tools/replay_feasibility — aliasy prologu czytają atrybut per-call);
2. monkeypatch `dispatch_pipeline.get_fresh_czas_kuriera_for_bag` obowiązuje
   (kontrakt testów K07 — ścieżka legacy per-kandydat);
3. wrapper eval_courier ZAWSZE domyka TLS-tracking OSRM (idempotentny stop,
   także przy wyjątku inner);
4. delegacja w impl: pętla kandydatów dostarcza Candidate zbudowany w core
   (smoke przez pełny assess_order).
"""
from datetime import datetime, timedelta, timezone

import dispatch_v2.dispatch_pipeline as dp
from dispatch_v2.core import candidates as cand
from dispatch_v2.courier_resolver import CourierState

_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def _ctx(**over):
    kw = dict(
        now=_NOW, order_event={"order_id": "K11T"}, order_id="K11T",
        restaurant="Testownia", delivery_address="Testowa 1",
        pickup_coords=(53.13, 23.16), delivery_coords=(53.14, 23.17),
        pickup_at=None, pickup_ready_at=_NOW + timedelta(minutes=10),
        new_order=None, new_rest_norm="testownia", fleet_speed_kmh=18.0,
        fleet_context=None, k07_prefetched_ck=None,
        loadgov_now=None, loadgov_ewma=None, loadgov_orders=0, loadgov_couriers=0,
    )
    kw.update(over)
    return cand.EvalContext(**kw)


def _cs(cid="900", pos=(53.131, 23.161)):
    cs = CourierState(courier_id=cid)
    cs.pos = pos
    cs.pos_source = "gps"
    cs.pos_age_min = 1.0
    cs.bag = []
    cs.shift_start = _NOW - timedelta(hours=2)
    cs.shift_end = _NOW + timedelta(hours=4)
    cs.name = "K11 Tester"
    return cs


def test_monkeypatch_check_feasibility_obowiazuje_w_core(monkeypatch):
    captured = {}

    def fake_cf(**kw):
        captured.update(kw)
        return ("NO", "k11_fake_reason", {}, None)

    monkeypatch.setattr(dp, "check_feasibility_v2", fake_cf)
    from dispatch_v2.route_simulator_v2 import OrderSim
    order = OrderSim(order_id="K11T", pickup_coords=(53.13, 23.16),
                     delivery_coords=(53.14, 23.17), status="assigned",
                     pickup_ready_at=_NOW + timedelta(minutes=10))
    res = cand.eval_courier_inner(_ctx(new_order=order), "900", _cs())
    assert captured, "core.candidates MUSI wołać check_feasibility_v2 przez atrybut dispatch_pipeline"
    assert res is not None and res.feasibility_reason == "k11_fake_reason"


def test_tls_tracking_domkniety_takze_przy_wyjatku(monkeypatch):
    from dispatch_v2 import osrm_client as oc
    calls = {"start": 0, "stop": 0}
    monkeypatch.setattr(oc, "start_v2_request_tracking", lambda: calls.__setitem__("start", calls["start"] + 1))
    monkeypatch.setattr(oc, "stop_v2_request_tracking", lambda: calls.__setitem__("stop", calls["stop"] + 1) or None)

    def boom(ctx, cid, cs):
        raise RuntimeError("k11 boom")

    monkeypatch.setattr(cand, "eval_courier_inner", boom)
    try:
        cand.eval_courier(_ctx(), "900", _cs())
    except RuntimeError:
        pass
    assert calls["start"] == 1 and calls["stop"] >= 1, "TLS tracking musi być domknięty w finally"


def test_early_none_dla_kuriera_bez_pozycji():
    cs = _cs()
    cs.pos = None
    assert cand.eval_courier(_ctx(), "900", cs) is None


def test_smoke_przez_pelny_assess_order(monkeypatch):
    """Delegacja w impl działa end-to-end: kandydat z core trafia do wyniku."""
    def fake_cf(**kw):
        return ("MAYBE", "ok", {"r6_bag_size": 0, "eta_pickup_min": 5.0}, None)

    monkeypatch.setattr(dp, "check_feasibility_v2", fake_cf)
    ev = {"order_id": "K11S", "restaurant": "Testownia", "delivery_address": "Testowa 1",
          "pickup_coords": [53.13, 23.16], "delivery_coords": [53.14, 23.17]}
    res = dp.assess_order(ev, {"900": _cs()}, None, _NOW)
    assert res.pool_total_count == 1
    cids = [c.courier_id for c in (res.candidates or [])] + ([res.best.courier_id] if res.best else [])
    assert "900" in cids, f"kandydat z core.candidates musi dotrzeć do wyniku (got: {res.verdict}/{res.reason})"
