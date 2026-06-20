#!/usr/bin/env python3
"""Testy [TOR3] prep_bias_decision_time_replay — decision-time, leave-future-out.

Krytyczne kontrakty (to one bronią werdyktu GO/NO-GO):
  * FLIP liczony z decision-time projekcji (predicted_r6 + shift), NIE z realnej
    dostawy danego ordera (anty-hindsight).
  * false-reject = flip ordera który REALNIE doszedł on-time → regresja świeżości.
  * słuszny flip = flip ordera który REALNIE był breach.
  * LFO: bias restauracji NIE może użyć obserwacji ze znacznikiem ts >= decyzji.
  * variant_shift: bias ujemny → 0 (nigdy nie rozluźnia R6); cap MAX_SHIFT_MIN.
  * matched_only: tylko proposed==final.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(os.path.dirname(_HERE), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import prep_bias_decision_time_replay as dtr  # noqa: E402


# --------------------------------------------------------------------------- #
# variant_shift — czysta funkcja siły korekty.
# --------------------------------------------------------------------------- #
def test_variant_shift_p80_and_median():
    assert dtr.variant_shift("p80", 6.0, 11.0, None) == 11.0
    assert dtr.variant_shift("median", 6.0, 11.0, None) == 6.0
    assert dtr.variant_shift("half", 6.0, 11.0, None) == 3.0
    assert dtr.variant_shift("p70", 10.0, 11.0, None) == 7.0


def test_variant_shift_negative_bias_clamped_to_zero():
    # bias ujemny (kuchnia szybsza) → NIGDY nie rozluźniamy R6.
    assert dtr.variant_shift("p80", -5.0, -2.0, None) == 0.0
    assert dtr.variant_shift("median", -5.0, -2.0, None) == 0.0


def test_variant_shift_cap():
    assert dtr.variant_shift("p80", 50.0, 99.0, None) == dtr.MAX_SHIFT_MIN


def test_variant_shift_highbreach_gated_by_rate():
    # poniżej progu breach-rate → korekta 0
    assert dtr.variant_shift("highbreach", 8.0, 12.0, 0.10) == 0.0
    # powyżej progu → median
    assert dtr.variant_shift("highbreach", 8.0, 12.0, 0.50) == 8.0
    # None breach-rate (za mało próby) → 0
    assert dtr.variant_shift("highbreach", 8.0, 12.0, None) == 0.0


# --------------------------------------------------------------------------- #
# lfo_bias — leave-future-out: brak podglądania przyszłości.
# --------------------------------------------------------------------------- #
def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) \
        if "+" not in s and "Z" not in s else datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_lfo_bias_uses_only_past():
    rest = "R"
    obs = {
        rest: [
            (_dt("2026-06-10T10:00:00"), 4.0),
            (_dt("2026-06-11T10:00:00"), 6.0),
            (_dt("2026-06-20T10:00:00"), 100.0),  # PRZYSZŁOŚĆ — nie wolno użyć
        ]
    }
    g = sorted([o for v in obs.values() for o in v], key=lambda t: t[0])
    decision = _dt("2026-06-12T10:00:00")
    med, p80 = dtr.lfo_bias(rest, decision, obs, g)
    # tylko 4.0 i 6.0 → mediana 5.0, NIE skażone przez 100.0
    assert med == pytest.approx(5.0, abs=0.01)
    assert p80 < 10.0  # gdyby weszła przyszłość, byłoby ~99


def test_lfo_bias_no_past_returns_none():
    rest = "R"
    obs = {rest: [(_dt("2026-06-20T10:00:00"), 8.0)]}
    g = [(_dt("2026-06-20T10:00:00"), 8.0)]
    decision = _dt("2026-06-10T10:00:00")  # przed jakąkolwiek obserwacją
    med, p80 = dtr.lfo_bias(rest, decision, obs, g)
    assert med is None and p80 is None


# --------------------------------------------------------------------------- #
# run — pełny replay na syntetycznych logach (decision-time, anty-hindsight).
# --------------------------------------------------------------------------- #
def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_table(path, rest_bias, global_bias=(6.0, 11.0)):
    payload = {"_global": {"bias_median_min": global_bias[0],
                           "bias_p80_min": global_bias[1]}}
    for r, (m, p) in rest_bias.items():
        payload[r] = {"bias_median_min": m, "bias_p80_min": p}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_run_flip_classification(monkeypatch, tmp_path):
    """Trzy decyzje, wariant p80 z biasem +10:
      D1 pred=30 → corrected=40>35 FLIP; realnie breach → SŁUSZNY
      D2 pred=28 → corrected=38>35 FLIP; realnie on-time → FALSE-REJECT
      D3 pred=20 → corrected=30<=35 brak flipu
    """
    rest = "Kuchnia"
    decisions = tmp_path / "dec.jsonl"
    ready = tmp_path / "ready.jsonl"
    table = tmp_path / "table.json"

    _write_jsonl(str(decisions), [
        {"order_id": "1", "restaurant": rest, "decision_ts": "2026-06-15T10:00:00+00:00",
         "predicted_r6_max_bag_min": 30.0, "proposed_courier_id": "9",
         "outcome": {"courier_id_final": "9", "delivered_ts": "2026-06-15T11:00:00+00:00",
                     "status": "delivered"}},
        {"order_id": "2", "restaurant": rest, "decision_ts": "2026-06-15T10:05:00+00:00",
         "predicted_r6_max_bag_min": 28.0, "proposed_courier_id": "9",
         "outcome": {"courier_id_final": "9", "delivered_ts": "2026-06-15T10:20:00+00:00",
                     "status": "delivered"}},
        {"order_id": "3", "restaurant": rest, "decision_ts": "2026-06-15T10:10:00+00:00",
         "predicted_r6_max_bag_min": 20.0, "proposed_courier_id": "9",
         "outcome": {"courier_id_final": "9", "delivered_ts": "2026-06-15T10:30:00+00:00",
                     "status": "delivered"}},
    ])
    _write_jsonl(str(ready), [])  # framing=table nie potrzebuje ready
    _write_table(str(table), {rest: (10.0, 10.0)})

    # SLA on-time = (delivered − pickup_ready). Podajemy ready przez sla decision log.
    # D1 ready 10:00 → delivered 11:00 = 60 min → breach
    # D2 ready 10:05 → delivered 10:20 = 15 min → on-time
    # D3 ready 10:10 → delivered 10:30 = 20 min → on-time
    sla_dec = tmp_path / "learn.jsonl"
    _write_jsonl(str(sla_dec), [
        {"order_id": "1", "pickup_ready_at": "2026-06-15T10:00:00+00:00"},
        {"order_id": "2", "pickup_ready_at": "2026-06-15T10:05:00+00:00"},
        {"order_id": "3", "pickup_ready_at": "2026-06-15T10:10:00+00:00"},
    ])
    sla_deliv = tmp_path / "outcomes.jsonl"
    _write_jsonl(str(sla_deliv), [
        {"order_id": "1", "outcome": {"delivered_ts": "2026-06-15T11:00:00+00:00",
                                      "status": "delivered", "courier_id_final": "9"}},
        {"order_id": "2", "outcome": {"delivered_ts": "2026-06-15T10:20:00+00:00",
                                      "status": "delivered", "courier_id_final": "9"}},
        {"order_id": "3", "outcome": {"delivered_ts": "2026-06-15T10:30:00+00:00",
                                      "status": "delivered", "courier_id_final": "9"}},
    ])

    res = dtr.run(framing="table", variants=["p80"],
                  decisions_log=str(decisions), ready_log=str(ready),
                  table_path=str(table),
                  sla_decision_paths=[str(sla_dec)],
                  sla_delivery_paths=[str(sla_deliv)])
    a = res["variants"]["p80"]["agg"]
    assert a["n_flips"] == 2          # D1 + D2
    assert a["protected_breaches"] == 1   # D1 słuszny
    assert a["freshness_regression_false_rejects"] == 1  # D2 false-reject
    # on-time PRZED: baseline-PASS = wszystkie 3 (pred<=35), on-time = D2,D3 = 2/3
    assert a["on_time_before"] == pytest.approx(2 / 3, abs=0.01)


def test_run_matched_only_filters(tmp_path):
    rest = "K"
    decisions = tmp_path / "dec.jsonl"
    _write_jsonl(str(decisions), [
        # matched (proposed==final)
        {"order_id": "1", "restaurant": rest, "decision_ts": "2026-06-15T10:00:00+00:00",
         "predicted_r6_max_bag_min": 30.0, "proposed_courier_id": "9",
         "outcome": {"courier_id_final": "9", "delivered_ts": "2026-06-15T11:00:00+00:00",
                     "status": "delivered"}},
        # NIE matched (proposed != final) — powinno odpaść w matched_only
        {"order_id": "2", "restaurant": rest, "decision_ts": "2026-06-15T10:05:00+00:00",
         "predicted_r6_max_bag_min": 30.0, "proposed_courier_id": "9",
         "outcome": {"courier_id_final": "7", "delivered_ts": "2026-06-15T10:20:00+00:00",
                     "status": "delivered"}},
    ])
    ready = tmp_path / "ready.jsonl"; _write_jsonl(str(ready), [])
    table = tmp_path / "t.json"; _write_table(str(table), {rest: (10.0, 10.0)})
    sla_dec = tmp_path / "l.jsonl"
    _write_jsonl(str(sla_dec), [
        {"order_id": "1", "pickup_ready_at": "2026-06-15T10:00:00+00:00"},
        {"order_id": "2", "pickup_ready_at": "2026-06-15T10:05:00+00:00"},
    ])
    sla_deliv = tmp_path / "o.jsonl"
    _write_jsonl(str(sla_deliv), [
        {"order_id": "1", "outcome": {"delivered_ts": "2026-06-15T11:00:00+00:00",
                                      "status": "delivered", "courier_id_final": "9"}},
        {"order_id": "2", "outcome": {"delivered_ts": "2026-06-15T10:20:00+00:00",
                                      "status": "delivered", "courier_id_final": "7"}},
    ])
    res = dtr.run(framing="table", variants=["p80"], matched_only=True,
                  decisions_log=str(decisions), ready_log=str(ready),
                  table_path=str(table), sla_decision_paths=[str(sla_dec)],
                  sla_delivery_paths=[str(sla_deliv)])
    assert res["n_decisions_total"] == 1   # tylko order 1
    assert res["matched_only"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
