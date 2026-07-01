#!/usr/bin/env python3
"""L6.A1 (Faza 3 audytu, 2026-07-01) — generator golden-korpusu parytetu route-order.

Zastępuje wygasający `ziomek_time_route_monitor` (MONITOR_STOP_AFTER=2026-07-10)
siecią parytetu BEZ daty wygaśnięcia: zamraża wspólne wejścia (bag+plan+flagi)
oraz kanoniczny PORZĄDEK (proj = [(typ, sorted(order_ids))]) do
`tests/golden/route_order_corpus.json`, konsumowanego przez:
  - silnik: `dispatch_v2/tests/test_route_order_golden.py` (kanon stabilny),
  - panel:  `nadajesz_clone/panel/backend/tests/test_route_order_parity_golden.py`
    (KONSOLA == KANON na tym samym wejściu; ratchet — zero nowych rozjazdów).

Granica (audyt F_poc_plan (a)): parytet = RÓWNOŚĆ PORZĄDKU; ETA/coords/dwell
per-powierzchnia legalnie różne i WYŁĄCZONE z porównania.

URUCHAMIAĆ venv-em PANELU (jak monitor — ma deps fleet_state):
  /root/.openclaw/workspace/nadajesz_clone/panel/backend/.venv/bin/python \
      tools/route_order_golden_corpus_gen.py [--out PATH]

Flagi pinowane w meta (wzorzec #15 — harness mirroruje PRODUKCJĘ, nie defaulty):
  - courier-api drop-iny: plan_aware / trust_canon (argumenty order_podjazdy),
  - nadajesz-panel env: PANEL_FLAG_* porządkotwórcze dla _build_route.
READ-ONLY poza zapisem pliku korpusu.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path("/root/.openclaw/workspace/nadajesz_clone/panel/backend")
_SCRIPTS = Path("/root/.openclaw/workspace/scripts")
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_SCRIPTS))

STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
ORDERS_PATH = STATE_DIR / "orders_state.json"
PLANS_PATH = STATE_DIR / "courier_plans.json"
OUT_DEFAULT = _SCRIPTS / "dispatch_v2" / "tests" / "golden" / "route_order_corpus.json"

PLAN_AWARE_DROPIN = "/etc/systemd/system/courier-api.service.d/plan-aware-podjazdy.conf"
TRUST_CANON_DROPIN = "/etc/systemd/system/courier-api.service.d/build-view-trust-canon.conf"

ACTIVE_STATES = {"assigned", "picked_up", "en_route"}
# Flagi panelu wpływające na PORZĄDEK w _build_route (fleet_state:443/453) +
# gałąź dropoff-dash (530). ETA/display-only flagi świadomie poza pinem.
PANEL_ORDER_FLAGS = (
    "TRUST_CANON_ORDER",
    "TRUST_CANON_WHEN_COVERS_BAG",
    "PLAN_AWARE_PODJAZDY",
    "DELIVERY_DASH_WHEN_NO_PLAN",
)

BAG_FIELDS = (
    "order_id", "status", "restaurant", "delivery_address", "czas_kuriera_warsaw",
    "pickup_address", "pickup_coords", "delivery_coords", "picked_up_at",
    "assigned_at", "created_at_utc", "pickup_at_warsaw",
)


def _dropin_on(path: str, key: str) -> bool:
    try:
        return f"{key}=1" in Path(path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return False


def _panel_prod_flags() -> dict[str, bool]:
    """Efektywne PANEL_FLAG_* z żywego unitu (agreguje drop-iny) + default flags."""
    env_txt = ""
    try:
        r = subprocess.run(["systemctl", "show", "nadajesz-panel.service", "-p", "Environment"],
                           capture_output=True, text=True, timeout=10)
        env_txt = r.stdout
    except Exception:  # noqa: BLE001
        pass
    from app.core.flags import DEFAULT_FLAGS  # noqa: PLC0415
    out = {}
    for name in PANEL_ORDER_FLAGS:
        marker = f"PANEL_FLAG_{name}="
        if marker in env_txt:
            val = env_txt.split(marker, 1)[1].split()[0].strip('"')
            out[name] = val.strip().lower() in {"1", "true", "yes", "on"}
        else:
            out[name] = bool(DEFAULT_FLAGS.get(name, False))
    return out


def _proj(stops) -> list:
    """Projekcja porządku: [[typ, sorted(order_ids)], ...] — JSON-stabilna."""
    out = []
    for s in stops:
        if isinstance(s, tuple):  # order_podjazdy: (typ, [ids])
            t, ids = s
        else:  # fleet_state PlanStop
            ids = list(s.order_ids) if getattr(s, "order_ids", None) else [s.order_id]
            t = s.type
        out.append([t, sorted(str(i) for i in ids)])
    return out


def _bag_dicts_live(orders: list[dict]) -> dict[str, list[dict]]:
    bags: dict[str, list[dict]] = {}
    for o in orders:
        if o.get("status") not in ACTIVE_STATES or not o.get("courier_id"):
            continue
        cid = str(o.get("courier_id"))
        bags.setdefault(cid, []).append(
            {f: o.get(f) for f in BAG_FIELDS} | {"order_id": str(o.get("order_id"))})
    return bags


def _synthetic_cases() -> list[dict]:
    """Edge-case'y kanonu (F_poc_plan C.2). Bag = plain dicty; plan_doc opcjonalny."""
    def o(oid, status="assigned", rest="R1", ck=None, picked=None,
          pc=None, dc=None):
        return {"order_id": oid, "status": status, "restaurant": rest,
                "delivery_address": f"Adres {oid}", "czas_kuriera_warsaw": ck,
                "pickup_address": f"{rest} ul. Testowa", "pickup_coords": pc,
                "delivery_coords": dc, "picked_up_at": picked,
                "assigned_at": "2026-07-01T10:00:00+00:00",
                "created_at_utc": "2026-07-01T09:50:00Z",
                "pickup_at_warsaw": None}

    P1 = [53.13, 23.16]
    P2 = [53.14, 23.17]
    return [
        {"id": "syn_empty_bag", "bag": [], "plan_doc": None,
         "note": "worek pusty -> pusta trasa"},
        {"id": "syn_single_order", "bag": [o("900001", ck="12:30", pc=P1, dc=P2)],
         "plan_doc": None, "note": "1 zlecenie: pickup->dropoff"},
        {"id": "syn_carried_first",
         "bag": [o("900011", status="picked_up", picked="2026-07-01T10:05:00+00:00",
                   ck="12:00", pc=P1, dc=P2),
                 o("900012", rest="R2", ck="12:20", pc=P2, dc=P1)],
         "plan_doc": None, "note": "picked_up niesiony + nowy odbior"},
        {"id": "syn_same_restaurant_bundle",
         "bag": [o("900021", ck="12:10", pc=P1, dc=P2),
                 o("900022", ck="12:15", pc=P1, dc=[53.15, 23.18])],
         "plan_doc": None, "note": "2x ta sama restauracja, ck w progu sklejania"},
        {"id": "syn_committed_ascending",
         "bag": [o("900031", rest="R3", ck="13:00", pc=P2, dc=P1),
                 o("900032", rest="R1", ck="12:00", pc=P1, dc=P2),
                 o("900033", rest="R2", ck="12:30", pc=[53.12, 23.15], dc=P2)],
         "plan_doc": None, "note": "odbiory wg committed rosnaco"},
        {"id": "syn_plan_covers_bag_trust_canon",
         "bag": [o("900041", ck="12:00", pc=P1, dc=P2),
                 o("900042", rest="R2", ck="12:40", pc=P2, dc=P1)],
         "plan_doc": {"sequence": [
             {"type": "pickup", "order_id": "900042"},
             {"type": "dropoff", "order_id": "900042"},
             {"type": "pickup", "order_id": "900041"},
             {"type": "dropoff", "order_id": "900041"}]},
         "note": "plan pokrywa CALY worek -> kanon verbatim (trust_canon)"},
        {"id": "syn_plan_partial_fallback",
         "bag": [o("900051", ck="12:00", pc=P1, dc=P2),
                 o("900052", rest="R2", ck="12:40", pc=P2, dc=P1)],
         "plan_doc": {"sequence": [
             {"type": "pickup", "order_id": "900051"},
             {"type": "dropoff", "order_id": "900051"}]},
         "note": "plan pokrywa CZESC worka -> fallback czasowy"},
        {"id": "syn_poisoned_zero_coords",
         "bag": [o("900061", ck="12:00", pc=[0.0, 0.0], dc=[0.0, 0.0]),
                 o("900062", rest="R2", ck="12:20", pc=P1, dc=P2)],
         "plan_doc": None,
         "note": "zatrute (0,0) -> porzadek deterministyczny mimo placeholdera"},
        {"id": "syn_no_ck_no_plan",
         "bag": [o("900071", ck=None, pc=P1, dc=P2),
                 o("900072", rest="R2", ck=None, pc=P2, dc=P1)],
         "plan_doc": None, "note": "brak committed i planu -> stabilny fallback"},
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    plan_aware = _dropin_on(PLAN_AWARE_DROPIN, "ENABLE_PLAN_AWARE_PODJAZDY")
    trust_canon = _dropin_on(TRUST_CANON_DROPIN, "ENABLE_BUILD_VIEW_TRUST_CANON_ORDER")
    panel_flags = _panel_prod_flags()
    # pin env dla _build_route ZANIM policzymy console_proj (harness = produkcja)
    for name, val in panel_flags.items():
        os.environ[f"PANEL_FLAG_{name}"] = "1" if val else "0"

    from app.integrations.ziomek.fleet_state import _build_route, BagOrder  # noqa: E402,PLC0415
    from dispatch_v2 import route_podjazdy as RP  # noqa: E402,PLC0415

    def as_bagorders(bag_dicts):
        return [BagOrder(**{k: v for k, v in d.items() if k in BAG_FIELDS}) for d in bag_dicts]

    cases = []
    # --- syntetyczne ---
    for sc in _synthetic_cases():
        cases.append({"id": sc["id"], "source": "synthetic", "note": sc["note"],
                      "bag": sc["bag"], "plan_doc": sc["plan_doc"]})
    # --- live replay (worki per kurier z orders_state + plan Ziomka) ---
    try:
        raw = json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
        orders = raw if isinstance(raw, list) else list(
            (raw.get("orders", raw) or {}).values()) if isinstance(raw, dict) else []
        plans = json.loads(PLANS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        orders, plans = [], {}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    for cid, bag in sorted(_bag_dicts_live(orders).items()):
        cases.append({"id": f"live_{stamp}_cid{cid}", "source": "live",
                      "note": f"zywy worek cid={cid} z {ORDERS_PATH.name}",
                      "bag": bag,
                      "plan_doc": plans.get(cid) if isinstance(plans, dict) else None})

    # --- kanon + konsola na wspólnym wejściu ---
    n_parity = n_diverged = 0
    for c in cases:
        bag_objs = as_bagorders(c["bag"])
        canon = RP.order_podjazdy(bag_objs, c["plan_doc"],
                                  plan_aware=plan_aware, trust_canon=trust_canon)
        c["expected_proj"] = _proj(canon)
        try:
            stops, _src = _build_route(c["plan_doc"], bag_objs, None,
                                       {b.order_id: {} for b in bag_objs})
            console = _proj(stops)
        except Exception as e:  # noqa: BLE001
            console = None
            c["console_error"] = f"{type(e).__name__}: {e}"[:200]
        c["console_parity"] = (console == c["expected_proj"])
        if not c["console_parity"]:
            c["console_proj"] = console
            n_diverged += 1
        else:
            n_parity += 1

    corpus = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "dispatch_v2/tools/route_order_golden_corpus_gen.py",
            "provenance": "L6.A1 Faza 3 audytu — zastępuje ziomek_time_route_monitor (expiry 2026-07-10)",
            "flags": {"plan_aware": plan_aware, "trust_canon": trust_canon,
                      "panel": panel_flags},
            "pickup_merge_min": RP.PICKUP_MERGE_MIN,
            "proj_contract": "[(typ, sorted(order_ids))] — ETA/coords wylaczone z parytetu",
        },
        "cases": cases,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"korpus: {len(cases)} cases (parity={n_parity}, diverged={n_diverged}) -> {out}")
    for c in cases:
        mark = "OK " if c["console_parity"] else "DIV"
        print(f"  [{mark}] {c['id']}: {len(c['bag'])} zlecen, stops={len(c['expected_proj'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
