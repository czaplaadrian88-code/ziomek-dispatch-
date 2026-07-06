"""W0.6 — staffing_alert smoke (advisory A3 część 1, shadow-only).

Tool jest read-only nowcast (zero wpływu na decyzje). Test sprawdza szkielet:
baseline/alert liczą się bez wyjątku, alert_for_day zwraca strukturę lub None,
backtest zwraca werdykt. Bramka merytoryczna (fire≥90%/false<10%) jest
DATA_LIMITED na delivery-only (patrz nota narzędzia) — to udokumentowane, nie bug.
"""
from __future__ import annotations

import json

from dispatch_v2.tools import staffing_alert as SA


def _mini(tmp_path):
    """2 dni × kilka godzin syntetycznego sla_log."""
    rows = []
    for day, base_h in (("2026-05-16", 12), ("2026-05-09", 12)):
        for h in range(10, 19):
            k = 20 if day == "2026-05-16" else 6  # heavy vs healthy wolumen
            for i in range(k):
                m = 40 if (day == "2026-05-16" and 13 <= h <= 16) else 20
                rows.append({"delivered_at": f"{day} {h:02d}:{(i*2)%60:02d}:00",
                             "delivery_time_minutes": m, "courier_id": f"c{i%5}",
                             "was_czasowka": False})
    p = tmp_path / "sla.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(p)


def test_load_and_baseline(tmp_path):
    dh = SA.load_day_hour(_mini(tmp_path))
    assert "2026-05-16" in dh and "2026-05-09" in dh
    base, gmean = SA.build_baseline(dh, exclude_day="2026-05-16")
    assert isinstance(base, dict) and isinstance(gmean, dict)


def test_alert_for_day_structure(tmp_path):
    dh = SA.load_day_hour(_mini(tmp_path))
    base, gmean = SA.build_baseline(dh)
    a = SA.alert_for_day("2026-05-16", dh, base, gmean)
    assert a is None or (a["shortfall_courier_hours"] > 0 and a["lead_min"] >= SA.LEAD_MIN)


def test_backtest_returns_verdict(tmp_path):
    res = SA.backtest(_mini(tmp_path), top_n=1)
    assert res["verdict"] in ("PASS", "DATA_LIMITED_NEEDS_ROSTER")
    assert "sep_orders_per_courier" in res  # diagnostyka separowalności obecna
