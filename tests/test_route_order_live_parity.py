"""SPRINT0 A0-ROUTEORDER (2026-07-05) — następca monitora `ziomek_time_route_monitor`
(pion Q3: parytet kolejności trasy konsola==apka na ŻYWYCH workach).

Monitor (timer 10-min w repo panelu) wygasa SAM 2026-07-10 i nie jest
przedłużany. Ten test przenosi jego pion Q3 do CI: odpala one-shot
`tools/route_order_live_parity_check.py` POD VENVEM PANELU (deps fleet_state
nie istnieją w dispatch-venv) i wymaga verdict=OK (pełny parytet + zero dryfu
flag porządkotwórczych vs golden-pin korpusu).

⛔ DOMYŚLNIE SKIP (aktywacja ZA ACK Adriana): żywy stan zmienia się w trakcie
regresji i test zależy od produkcyjnych plików/env — włączenie do kanonicznej
regresji = decyzja operacyjna. Aktywacja: env ENABLE_ROUTE_ORDER_LIVE_PARITY=1
(np. w komendzie regresji albo w conftest po ACK).

Golden-testy (test_route_order_golden.py + panelowy parity_golden) są
deterministycznym rdzeniem siatki i działają ZAWSZE; ten test dokłada żywą
weryfikację, którą dotąd dawał timer.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

PANEL_PY = Path("/root/.openclaw/workspace/nadajesz_clone/panel/backend/.venv/bin/python")
TOOL = Path(__file__).resolve().parents[1] / "tools" / "route_order_live_parity_check.py"


@pytest.mark.skipif(
    os.environ.get("ENABLE_ROUTE_ORDER_LIVE_PARITY", "1") != "1",
    reason="następca monitora route-order wyłączony jawnym opt-outem "
           "(ENABLE_ROUTE_ORDER_LIVE_PARITY=0)")
def test_live_route_order_parity_and_flag_pin():
    # AKTYWOWANY za ACK Adriana 2026-07-05 ~18:25 UTC (default ON, opt-out =0)
    # — od tej chwili KAŻDA pełna regresja weryfikuje żywy parytet + pin flag;
    # monitor ziomek_time_route_monitor wygasa 10.07 bez utraty klasy Q3.
    assert PANEL_PY.exists(), f"brak venv panelu: {PANEL_PY}"
    r = subprocess.run([str(PANEL_PY), str(TOOL), "--json"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 2, f"INFRA_ERROR następcy monitora:\n{r.stdout}\n{r.stderr}"
    verdict = json.loads(r.stdout)
    assert r.returncode == 0 and verdict["verdict"] == "OK", (
        "INV-SRC-ROUTE-ORDER naruszony na żywych workach (rozjazd kolejności "
        "konsola≠kanon LUB dryf flag porządkotwórczych vs golden-pin — legalny "
        "flip flagi wymaga re-generacji korpusu):\n"
        + json.dumps(verdict, ensure_ascii=False, indent=1))
