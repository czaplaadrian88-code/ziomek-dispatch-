#!/usr/bin/env python3
"""
DRAFT (audyt SOTA 2026-07-03) — serwer MCP filtrujący stan dispatchu PRZED LLM.

NIE JEST WPIĘTY W SILNIK. Read-only. Uruchomienie/instalacja = osobna decyzja
(protokół ziomek-change-protocol, ETAP 0-7). Wymaga: pip install "mcp[cli]".

Cel (Filar B audytu): dziś jedyny konsument LLM (tools/llm_triage.py) dostaje
gotowe metryki, ale każda przyszła integracja AI (AI-HUB w konsoli, asystent
koordynatora) ma pokusę wrzucenia surowego orders_state.json (860 KB) albo
shadow_decisions.jsonl (80+ MB) do promptu. Ten serwer daje jedyny legalny
kanał: agregaty i wycinki liczone PO STRONIE RUNTIME, nigdy surowe dumpy.

Zasada: żadne narzędzie nie zwraca więcej niż ~4 KB JSON.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
ORDERS_STATE = STATE_DIR / "orders_state.json"
COURIER_PLANS = STATE_DIR / "courier_plans.json"
SHADOW_LOG = Path("/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl")

mcp = FastMCP("ziomek-dispatch-state", instructions=(
    "Read-only, zagregowany widok stanu dispatchu Ziomka. "
    "Zwraca wyłącznie przefiltrowane agregaty — nigdy surowy stan."
))


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tail_jsonl(path: Path, n: int = 200) -> list[dict]:
    """Ostatnie n rekordów bez wczytywania 80 MB pliku do pamięci."""
    out: list[dict] = []
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n * 16_000))
            lines = fh.read().split(b"\n")[1:]
        for ln in lines:
            if not ln.strip():
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    except OSError:
        pass
    return out[-n:]


@mcp.tool()
def fleet_summary() -> dict:
    """Migawka floty: liczności per status zlecenia + obciążenie kurierów.

    Zamiast 860 KB orders_state.json — ~0.5 KB agregatu.
    """
    orders = _load_json(ORDERS_STATE)
    plans = _load_json(COURIER_PLANS)
    by_status: dict[str, int] = {}
    for o in orders.values() if isinstance(orders, dict) else []:
        st = str(o.get("status", "unknown"))
        by_status[st] = by_status.get(st, 0) + 1
    bag_sizes = [
        len(p.get("stops", p.get("orders", [])) or [])
        for p in (plans.values() if isinstance(plans, dict) else [])
    ]
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "orders_by_status": by_status,
        "couriers_with_plan": len(bag_sizes),
        "bag_size_max": max(bag_sizes, default=0),
        "bag_size_median": statistics.median(bag_sizes) if bag_sizes else 0,
    }


@mcp.tool()
def order_context(order_id: str) -> dict:
    """Kontekst JEDNEGO zlecenia: status, restauracja, prep, przydział.

    Białolistowane pola — payload panelu, adresy pełne i telefony NIE wychodzą.
    """
    orders = _load_json(ORDERS_STATE)
    o = orders.get(str(order_id)) if isinstance(orders, dict) else None
    if not isinstance(o, dict):
        return {"order_id": order_id, "found": False}
    fields = (
        "status", "restaurant", "courier_id", "prep_minutes",
        "pickup_ready_at", "created_at", "czas_kuriera", "drop_district",
    )
    return {"order_id": order_id, "found": True,
            **{k: o.get(k) for k in fields if k in o}}


@mcp.tool()
def recent_decisions(limit: int = 20) -> list[dict]:
    """Skondensowane N ostatnich decyzji shadow (werdykt + zwycięzca + latencja).

    Pełny rekord ma ~12.4 KB; tu zwracamy ~10 pól na decyzję.
    """
    limit = max(1, min(int(limit), 50))
    rows = []
    for rec in _tail_jsonl(SHADOW_LOG, n=limit * 3)[-limit:]:
        best = rec.get("best") or {}
        rows.append({
            "ts": rec.get("ts"),
            "order_id": rec.get("order_id"),
            "verdict": rec.get("verdict"),
            "auto_route": rec.get("auto_route"),
            "best_courier": best.get("courier_id") or best.get("courier"),
            "best_score": best.get("score"),
            "pool_feasible": rec.get("pool_feasible_count"),
            "latency_ms": rec.get("latency_ms"),
        })
    return rows


@mcp.tool()
def anomaly_digest() -> dict:
    """Agregat anomalii z ostatnich ~200 decyzji: koord-rate, best-effort,
    redirecty R6 — dokładnie to, co dziś ręcznie skleja tools/llm_triage.py."""
    recs = _tail_jsonl(SHADOW_LOG, n=200)
    n = len(recs) or 1
    def frac(key: str) -> float:
        return round(sum(1 for r in recs if r.get(key)) / n, 3)
    verdicts: dict[str, int] = {}
    for r in recs:
        v = str(r.get("verdict", "?"))
        verdicts[v] = verdicts.get(v, 0) + 1
    lat = sorted(r.get("latency_ms") or 0 for r in recs)
    return {
        "n": len(recs),
        "verdicts": verdicts,
        "best_effort_r6_redirect_rate": frac("best_effort_r6_redirect"),
        "commit_divergence_redirect_rate": frac("commit_divergence_redirect"),
        "latency_p95_ms": lat[int(0.95 * (len(lat) - 1))] if lat else None,
    }


if __name__ == "__main__":
    mcp.run()  # stdio transport; do Claude Code: `claude mcp add ziomek-state -- python3 <ten plik>`
