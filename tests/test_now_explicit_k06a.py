"""K06a (refaktor, 2026-07-06): _tick przekazuje JAWNE now do process_event
(zegar decyzji nagrywalny w world_record; wcześniej now=None → impl-default,
replay bit-w-bit niemożliwy). Semantyka bez zmian: impl wiąże 1 now/decyzję."""
from datetime import datetime, timezone

import dispatch_v2.shadow_dispatcher as sd


def test_process_event_forwards_now(monkeypatch):
    # K09: process_event woła fasadę core.decide → dispatch_pipeline.assess_order
    # (atrybut modułu, lookup w call-time) — mock siedzi na dispatch_pipeline.
    import dispatch_v2.dispatch_pipeline as dp

    captured = {}

    def fake_assess(order_event, fleet, meta=None, now=None, **kw):
        captured["now"] = now

        class _R:
            verdict = "PROPOSE"
        return _R()

    monkeypatch.setattr(dp, "assess_order", fake_assess)
    explicit = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    sd.process_event({"order_id": "1", "payload": {}}, {}, None, now=explicit)
    assert captured["now"] is explicit, "process_event MUSI przekazać now do assess"
