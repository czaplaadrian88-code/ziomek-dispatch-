#!/usr/bin/env python3
"""SPRINT0 A0-ROUTEORDER (2026-07-05) — NASTĘPCA `ziomek_time_route_monitor` Q3.

Monitor (panel repo, timer 10-min) wygasa SAM 2026-07-10 (MONITOR_STOP_AFTER)
i decyzją konsolidacji 05.07 NIE jest przedłużany. Ten tool = jego pion Q3
(parytet kolejności trasy) jako ONE-SHOT bez daty wygaśnięcia:

  INV-SRC-ROUTE-ORDER: dla każdego ŻYWEGO worka kolejność stopów liczona przez
  kanon apki (`dispatch_v2.route_podjazdy.order_podjazdy`) == kolejność konsoli
  (`fleet_state._build_route`) na TYCH SAMYCH wejściach (bag + plan Ziomka),
  przy EFEKTYWNYCH flagach produkcyjnych (drop-iny courier-api + env panelu —
  wzorzec #15: harness mirroruje produkcję, nie defaulty).

Dodatkowo pilnuje DRYFU KONFIGURACJI: flagi porządkotwórcze odczytane z żywej
produkcji muszą równać się flagom zamrożonym w meta golden-korpusu
(`tests/golden/route_order_corpus.json`). Legalny flip flagi => regeneracja
korpusu generatorem + commit razem ze zmianą; czerwony check bez regeneracji
= niezamierzony dryf konfiguracji (klasa #9/#15).

URUCHAMIAĆ venv-em PANELU (deps fleet_state):
  /root/.openclaw/workspace/nadajesz_clone/panel/backend/.venv/bin/python \
      tools/route_order_live_parity_check.py [--json]

Exit: 0 = parytet + brak dryfu (lub zero żywych worków — noc), 1 = rozjazd
kolejności LUB dryf flag, 2 = błąd infrastruktury (brak venv/state/importu).
READ-ONLY — zero zapisu poza stdout.

Aktywacja w CI (ZA ACK Adriana): tests/test_route_order_live_parity.py
odpala ten tool przy env ENABLE_ROUTE_ORDER_LIVE_PARITY=1 (domyślnie SKIP,
żeby regresja była deterministyczna offline; po ACK gate znika/env wchodzi
do kanonicznej komendy regresji). Q1/Q2 monitora (czas przekazany / drift
czasu) mają OSOBNYCH strażników — patrz raport SPRINT0_ZAD2.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path("/root/.openclaw/workspace/nadajesz_clone/panel/backend")
_SCRIPTS = Path("/root/.openclaw/workspace/scripts")
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_SCRIPTS))

STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
ORDERS_PATH = STATE_DIR / "orders_state.json"
PLANS_PATH = STATE_DIR / "courier_plans.json"
CORPUS_PATH = _SCRIPTS / "dispatch_v2" / "tests" / "golden" / "route_order_corpus.json"

ACTIVE_STATES = {"assigned", "picked_up", "en_route"}


def _load_gen():
    """Reużyj helperów generatora (jedno źródło odczytu flag prod — bez kopii)."""
    sys.path.insert(0, str(_SCRIPTS / "dispatch_v2" / "tools"))
    import route_order_golden_corpus_gen as gen  # noqa: PLC0415
    return gen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="pełny werdykt JSON na stdout")
    args = ap.parse_args()

    try:
        gen = _load_gen()
        plan_aware = gen._dropin_on(gen.PLAN_AWARE_DROPIN, "ENABLE_PLAN_AWARE_PODJAZDY")
        trust_canon = gen._dropin_on(gen.TRUST_CANON_DROPIN,
                                     "ENABLE_BUILD_VIEW_TRUST_CANON_ORDER")
        panel_flags = gen._panel_prod_flags()
        import os  # noqa: PLC0415
        for name, val in panel_flags.items():
            os.environ[f"PANEL_FLAG_{name}"] = "1" if val else "0"
        from app.integrations.ziomek.fleet_state import _build_route, BagOrder  # noqa: E402,PLC0415
        from dispatch_v2 import route_podjazdy as RP  # noqa: E402,PLC0415
        raw = json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
        orders = raw if isinstance(raw, list) else list(
            (raw.get("orders", raw) or {}).values()) if isinstance(raw, dict) else []
        plans = json.loads(PLANS_PATH.read_text(encoding="utf-8"))
        corpus_meta = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["meta"]
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "INFRA_ERROR",
                          "error": f"{type(e).__name__}: {e}"[:300]}))
        return 2

    # --- dryf konfiguracji vs golden-pin (wzorzec #9/#15) ---
    live_flags = {"plan_aware": plan_aware, "trust_canon": trust_canon,
                  "panel": panel_flags}
    flag_drift = (live_flags != corpus_meta.get("flags"))

    # --- parytet kolejności na żywych workach ---
    bags = gen._bag_dicts_live(orders)
    checked, mismatches, errors = 0, [], []
    for cid, bag in sorted(bags.items()):
        plan_doc = plans.get(cid) if isinstance(plans, dict) else None
        bag_objs = [BagOrder(**{k: v for k, v in d.items() if k in gen.BAG_FIELDS})
                    for d in bag]
        canon = gen._proj(RP.order_podjazdy(bag_objs, plan_doc,
                                            plan_aware=plan_aware,
                                            trust_canon=trust_canon))
        try:
            stops, _src = _build_route(plan_doc, bag_objs, None,
                                       {b.order_id: {} for b in bag_objs})
            console = gen._proj(stops)
        except Exception as e:  # noqa: BLE001
            errors.append({"cid": cid, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        checked += 1
        if console != canon:
            mismatches.append({"cid": cid, "canon": canon, "console": console})

    ok = not mismatches and not flag_drift and not errors
    verdict = {
        "verdict": "OK" if ok else "FAIL",
        "checked_bags": checked,
        "mismatches": mismatches,
        "errors": errors,
        "flag_drift": flag_drift,
        "live_flags": live_flags,
        "corpus_flags": corpus_meta.get("flags"),
    }
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=1))
    else:
        print(json.dumps({k: verdict[k] for k in
                          ("verdict", "checked_bags", "flag_drift")},
                         ensure_ascii=False)
              + (f" mismatches={len(mismatches)} errors={len(errors)}" if not ok else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
