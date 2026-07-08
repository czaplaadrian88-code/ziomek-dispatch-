#!/usr/bin/env python3
"""Dowód 0-diff dla flip-cardu delegacji panelu (Sprint C, 2026-07-08).

Panel `fleet_state._build_route` ma WŁASNĄ kopię obliczenia kolejności `order`
(lista [(typ,[order_ids])]). Delegacja = zastąpienie jej wywołaniem
`route_order.order_podjazdy(...)`. Golden-test pilnuje tylko proj=[(typ,SORTED(oids))]
(sortuje oids w stopie) — NIE łapie różnicy KOLEJNOŚCI wewnątrz scalonego odbioru
ani reprezentanta (order_id=oids[0]). Ten skrypt porównuje RAW `order` (bez sortu
wewnątrz stopu) z obu ścieżek na korpusie golden + ŻYWYCH workach → jeśli 0 różnic,
delegacja jest bajt-identyczna także co do kolejności wewnątrz stopu.

Uruchamiać venvem PANELU (deps fleet_state):
  nadajesz_clone/panel/backend/.venv/bin/python <ten plik>
"""
import json
import sys
from pathlib import Path

_BACKEND = Path("/root/.openclaw/workspace/nadajesz_clone/panel/backend")
_SCRIPTS = Path("/root/.openclaw/workspace/scripts")
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_SCRIPTS))

from app.integrations.ziomek.fleet_state import _build_route, BagOrder  # noqa: E402
from dispatch_v2 import route_order as RO  # noqa: E402

CORPUS = _SCRIPTS / "dispatch_v2" / "tests" / "golden" / "route_order_corpus.json"
STATE = Path("/root/.openclaw/workspace/dispatch_state")


def panel_order(plan_doc, bag):
    """RAW order [(typ,[oids])] z panelu — odtwarzamy z PlanStop.order_ids."""
    stops, _ = _build_route(plan_doc, bag, None, {b.order_id: {} for b in bag})
    return [(s.type, list(s.order_ids)) for s in stops]


def canon_order(plan_doc, bag, plan_aware, trust_canon):
    return [(t, list(oids)) for (t, oids) in
            RO.order_podjazdy(bag, plan_doc, plan_aware=plan_aware, trust_canon=trust_canon)]


def _flags_from_corpus_meta():
    meta = json.loads(CORPUS.read_text(encoding="utf-8"))["meta"]["flags"]
    return meta["plan_aware"], meta["trust_canon"], meta["panel"]


def main():
    plan_aware, trust_canon, panel_flags = _flags_from_corpus_meta()
    import os
    # mirror produkcji: panel flagi wg meta korpusu (wzorzec #15)
    for k, v in panel_flags.items():
        os.environ[f"PANEL_FLAG_{k}"] = "1" if v else "0"

    bag_fields = {f.name for f in BagOrder.__dataclass_fields__.values()}
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    diffs = 0
    checked = 0
    for case in corpus["cases"]:
        if not case.get("console_parity", False):
            continue
        bag = [BagOrder(**{k: v for k, v in d.items() if k in bag_fields}) for d in case["bag"]]
        p = panel_order(case["plan_doc"], bag)
        c = canon_order(case["plan_doc"], bag, plan_aware, trust_canon)
        checked += 1
        if p != c:
            diffs += 1
            print(f"CORPUS DIFF {case['id']}:\n  panel={p}\n  canon={c}")
    print(f"[corpus] checked={checked} raw_diffs={diffs}")

    # --- żywe worki ---
    try:
        raw = json.loads((STATE / "orders_state.json").read_text(encoding="utf-8"))
        orders = raw if isinstance(raw, list) else list(
            (raw.get("orders", raw) or {}).values()) if isinstance(raw, dict) else []
        plans = json.loads((STATE / "courier_plans.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[live] skip ({type(e).__name__})")
        orders, plans = [], {}

    ACTIVE = {"assigned", "picked_up", "en_route"}
    by_cid = {}
    for o in orders:
        if isinstance(o, dict) and o.get("status") in ACTIVE and o.get("courier_id") is not None:
            by_cid.setdefault(str(o["courier_id"]), []).append(o)
    live_checked = live_diffs = 0
    for cid, ods in sorted(by_cid.items()):
        bag = [BagOrder(**{k: v for k, v in o.items() if k in bag_fields}) for o in ods]
        plan_doc = plans.get(cid) if isinstance(plans, dict) else None
        p = panel_order(plan_doc, bag)
        c = canon_order(plan_doc, bag, plan_aware, trust_canon)
        live_checked += 1
        if p != c:
            live_diffs += 1
            print(f"LIVE DIFF cid={cid}:\n  panel={p}\n  canon={c}")
    print(f"[live] checked={live_checked} raw_diffs={live_diffs}")
    total = diffs + live_diffs
    print(f"VERDICT: {'0-DIFF OK' if total == 0 else f'{total} DIFFS'}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
