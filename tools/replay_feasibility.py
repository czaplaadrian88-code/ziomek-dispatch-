"""replay_feasibility — instrumentowany replay feasibility per kurier (re-diagnoza #38).

Problem: w ścieżce PROPOSE `result.candidates` zawiera tylko kandydatów feasible.
Kandydaci z verdict NO (lub bez werdyktu) są niewidoczni — `feasibility_reason`
liczony w `_v327_eval_courier` i odrzucany. Dla archetypu 472791 znaczy to, że NIE
WIDAĆ, dlaczego picked_up-almost-done kurier (Piotr 470) wypadł z puli.

To narzędzie owija `dispatch_pipeline.check_feasibility_v2` wrapperem rejestrującym
per kurier `verdict + reason + metryki` dla WSZYSTKICH ocenianych — też infeasible.
Następnie uruchamia prawdziwy `assess_order` na bieżącym kodzie i renderuje tabelę.

ZERO dotknięcia produkcji — monkeypatch żyje tylko w procesie tego skryptu.
Replay leci na BIEŻĄCYM kodzie pipeline → odpowiada na pytanie „czy bug 472791
nadal istnieje", nie odtwarza bajt-wiernie stanu z 13.05 (events.db przeczyszczony).

Usage:
    python3 -m dispatch_v2.tools.replay_feasibility --fixture tools/fixtures/472791_archetype.json
    python3 -m dispatch_v2.tools.replay_feasibility --fixture <path> --output /tmp/replay.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_ROOT = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    """ISO string → tz-aware datetime (UTC gdy brak offsetu)."""
    if not val:
        return None
    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_fleet(fixture: Dict[str, Any]) -> Dict[str, Any]:
    """fixture['fleet'] (list dict) → {cid: CourierState}."""
    from dispatch_v2.courier_resolver import CourierState

    fleet: Dict[str, Any] = {}
    for c in fixture["fleet"]:
        cid = str(c["courier_id"])
        cs = CourierState(courier_id=cid)
        cs.pos = tuple(c["pos"]) if c.get("pos") else None
        cs.pos_source = c.get("pos_source", "none")
        cs.bag = list(c.get("bag") or [])
        cs.shift_start = _parse_dt(c.get("shift_start"))
        cs.shift_end = _parse_dt(c.get("shift_end"))
        cs.name = c.get("name")
        cs.tier_bag = c.get("tier_bag")
        cs.tier_label = c.get("tier_bag")
        fleet[cid] = cs
    return fleet


def replay(fixture_path: str) -> Dict[str, Any]:
    """Uruchom instrumentowany replay. Zwraca dict z capture per kurier + result."""
    fixture = json.loads(Path(fixture_path).read_text())
    now = _parse_dt(fixture["now"])
    order_event = dict(fixture["order_event"])
    fleet = _build_fleet(fixture)

    # pos → cid (capture check_feasibility_v2 dostaje courier_pos, nie cid)
    pos_to_cid: Dict[tuple, str] = {}
    for cid, cs in fleet.items():
        if cs.pos:
            pos_to_cid[(round(cs.pos[0], 5), round(cs.pos[1], 5))] = cid

    from dispatch_v2 import dispatch_pipeline
    from dispatch_v2 import common as C

    # Pre-proposal recheck robi network fetch (panel login) — wyłączamy dla
    # deterministycznego offline replay. Bag w fixture ma już czas_kuriera_warsaw,
    # więc recheck i tak nic by nie zmienił. Monkeypatch lokalny dla procesu.
    recheck_was = getattr(C, "ENABLE_V327_PRE_PROPOSAL_RECHECK", None)
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False

    capture: List[Dict[str, Any]] = []
    _orig_cf = dispatch_pipeline.check_feasibility_v2

    def _wrapped_cf(*args, **kwargs):
        verdict, reason, metrics, plan = _orig_cf(*args, **kwargs)
        cp = kwargs.get("courier_pos")
        if cp is None and args:
            cp = args[0]
        bag = kwargs.get("bag")
        if bag is None and len(args) > 1:
            bag = args[1]
        cid = None
        if cp:
            cid = pos_to_cid.get((round(cp[0], 5), round(cp[1], 5)))
        capture.append({
            "courier_id": cid,
            "courier_pos": list(cp) if cp else None,
            "bag_size": len(bag or []),
            "pos_source": kwargs.get("pos_source"),
            "courier_tier": kwargs.get("courier_tier"),
            "verdict": verdict,
            "reason": reason,
            "metrics": {k: metrics.get(k) for k in (
                "bag_size_before", "r6_bag_size", "r6_max_bag_time_min",
                "sla_violation", "r5_pickup_detour_total_km",
                "v3273_wait_courier_max_min", "v3273_wait_courier_hard_reject",
                "r1_avg_pairwise_cosine", "time_to_pickup_ready_min",
            ) if isinstance(metrics, dict) and k in metrics},
        })
        return verdict, reason, metrics, plan

    dispatch_pipeline.check_feasibility_v2 = _wrapped_cf
    try:
        # K09: fasada core.decide (delegacja 1:1 do assess_order)
        from dispatch_v2.core.decide import decide as _decide
        from dispatch_v2.core.world_state import WorldState
        result = _decide(WorldState(fleet_snapshot=fleet, now=now), order_event)
    finally:
        dispatch_pipeline.check_feasibility_v2 = _orig_cf
        if recheck_was is not None:
            C.ENABLE_V327_PRE_PROPOSAL_RECHECK = recheck_was

    cands = result.candidates or []
    best_cid = str(getattr(result.best, "courier_id", "")) if result.best else None

    # Layer 2 — finalny stan Candidate po post-processingu `_v327_eval_courier`.
    # check_feasibility_v2 (Layer 1) zwraca surowy werdykt; między call-site (1498)
    # a budową Candidate (2586) verdict bywa downgradowany przez R-gate. Diff
    # Layer1 vs Layer2 = lokalizacja bramki, która eliminuje kuriera.
    final_by_cid: Dict[str, Any] = {}
    for c in cands:
        cid = str(getattr(c, "courier_id", ""))
        final_by_cid[cid] = {
            "feasibility_verdict": getattr(c, "feasibility_verdict", None),
            "feasibility_reason": getattr(c, "feasibility_reason", None),
            "score": getattr(c, "score", None),
            "best_effort": getattr(c, "best_effort", None),
            "has_plan": getattr(c, "plan", None) is not None,
        }

    return {
        "fixture": fixture_path,
        "label": fixture.get("label"),
        "now": fixture["now"],
        "capture": capture,
        "final_by_cid": final_by_cid,
        "result": {
            "verdict": result.verdict,
            "reason": result.reason,
            "best_cid": best_cid,
            "best_score": getattr(result.best, "score", None) if result.best else None,
            "pool_total_count": result.pool_total_count,
            "pool_feasible_count": result.pool_feasible_count,
            "candidate_cids": sorted(final_by_cid.keys()),
            "auto_route": getattr(result, "auto_route", None),
            "auto_route_reason": getattr(result, "auto_route_reason", None),
        },
        "fleet_names": {cid: cs.name for cid, cs in fleet.items()},
    }


def render(out: Dict[str, Any]) -> str:
    """Tabela tekstowa dla terminala."""
    L: List[str] = []
    L.append("=" * 78)
    L.append(f"REPLAY FEASIBILITY — {out['label']}")
    L.append(f"now={out['now']}  fixture={out['fixture']}")
    L.append("=" * 78)
    L.append("")
    L.append("PER KURIER — Layer 1 (check_feasibility_v2) vs Layer 2 (finalny Candidate):")
    L.append("-" * 78)
    res = out["result"]
    final = out.get("final_by_cid", {})
    for rec in out["capture"]:
        cid = rec["courier_id"] or "?"
        name = out["fleet_names"].get(cid, "?")
        L.append(f"  cid={cid} ({name})  pos_source={rec['pos_source']}  "
                 f"tier={rec['courier_tier']}  bag={rec['bag_size']}")
        L.append(f"    L1 check_feasibility_v2 : verdict={rec['verdict']}  reason={rec['reason']}")
        f = final.get(cid)
        if f:
            L.append(f"    L2 finalny Candidate    : verdict={f['feasibility_verdict']}  "
                     f"reason={f['feasibility_reason']}")
            L.append(f"    L2 score={f['score']}  best_effort={f['best_effort']}  has_plan={f['has_plan']}")
            if rec["verdict"] != f["feasibility_verdict"]:
                L.append(f"    ⚠ DOWNGRADE L1→L2: {rec['verdict']}→{f['feasibility_verdict']} "
                         f"(bramka w _v327_eval_courier między call-site 1498 a Candidate 2586)")
        else:
            L.append(f"    L2 finalny Candidate    : (poza result.candidates)")
        m = rec["metrics"]
        if m:
            L.append(f"    metryki L1 = {json.dumps(m, ensure_ascii=False)}")
        tag = "BEST" if cid == res["best_cid"] else (
            "w result.candidates" if cid in res["candidate_cids"] else "ODPADŁ z puli")
        L.append(f"    → {tag}")
        L.append("")
    L.append("-" * 78)
    L.append("WYNIK assess_order:")
    L.append(f"  verdict={res['verdict']}  reason={res['reason']}")
    L.append(f"  best={res['best_cid']} (score={res['best_score']})")
    L.append(f"  pool_total={res['pool_total_count']}  pool_feasible={res['pool_feasible_count']}")
    L.append(f"  candidate_cids={res['candidate_cids']}")
    L.append(f"  auto_route={res['auto_route']} ({res['auto_route_reason']})")
    L.append("=" * 78)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(prog="replay_feasibility")
    ap.add_argument("--fixture", required=True, help="ścieżka do fixture JSON")
    ap.add_argument("--output", help="zapis pełnego wyniku JSON do pliku")
    args = ap.parse_args()

    if not Path(args.fixture).exists():
        print(f"FIXTURE NOT FOUND: {args.fixture}", file=sys.stderr)
        return 2

    out = replay(args.fixture)
    print(render(out))

    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        print(f"\n[output JSON → {args.output}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
