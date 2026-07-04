#!/usr/bin/env python3
"""lexqual_geometry_replay — dowód ETAP 5 dla L6.C2 (człon geometrii w lex_qual).

Replay ON↔OFF z SERIALIZOWANYCH kandydatów shadow_decisions (rotation-aware przez
ledger_io — L1.2): dla każdej decyzji z ≥2 kandydatami MAYBE liczy pick kanonem
`objm_lexr6.lex_qual` w 3 wariantach:
  OFF     — krotka czasowa (stan sprzed sprintu),
  GEOM    — + człon deliv_spread_km (quant=0; rozstrzyga tylko idealne remisy),
  GEOM+Q1 — + kwantyzacja termów czasowych do 1 min (kubełki floor).

Metryki werdyktu (NETTO, nie 1 case — protokół #0 ETAP 5):
  - flips: ile decyzji zmienia picka vs OFF,
  - Δspread na flipach (cel: ujemny = ciaśniejsze worki),
  - Δr6/Δcommitted na flipach (koszt czasowy; przy quant=0 strukturalnie 0,
    przy quant=1 ograniczony kubełkiem — RAPORTOWANY, nie zakładany),
  - picki spread>MAX_DELIV_SPREAD_KM przed/po (klasa „279").

PRZYBLIŻENIE (jawne): grupa tie-breaku = WSZYSCY serializowani kandydaci MAYBE
(top-N), bez podziału (tier,bucket) live-selektora — bucket nie jest serializowany.
Wynik czytać jako GÓRNE oszacowanie liczby flipów; kierunek Δspread/Δr6 pozostaje
miarodajny (te same metryki, ten sam klucz). Werdykt flipowy potwierdza replay,
decyzję podejmuje Adrian (ACK).

Użycie:
  python -m dispatch_v2.tools.lexqual_geometry_replay --days 3
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.tools import ledger_io  # noqa: E402

OUT_TXT = "/root/.openclaw/workspace/dispatch_state/lexqual_geometry_replay_verdict.txt"


def _f(m: dict, k: str):
    v = m.get(k)
    return float(v) if isinstance(v, (int, float)) else None


def _key(m: dict, geom: bool, quant: float):
    r6 = _f(m, "objm_r6_breach_max_min")
    t_r6 = r6 if r6 is not None else 9e9
    t_com = _f(m, "late_pickup_committed_max") or 0.0
    t_new = _f(m, "new_pickup_late_min") or 0.0
    if not geom:
        return (t_r6, t_com, t_new)
    if quant > 0.0:
        import math
        if t_r6 < 9e9:
            t_r6 = math.floor(t_r6 / quant) * quant
        t_com = math.floor(t_com / quant) * quant
        t_new = math.floor(t_new / quant) * quant
    return (t_r6, t_com, t_new, _f(m, "deliv_spread_km") or 0.0)


def run(days: float) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n_all = n_eval = 0
    variants = {"GEOM": {"flips": [], "q": 0.0}, "GEOM+Q1": {"flips": [], "q": 1.0}}
    over_cap = {"OFF": 0, "GEOM": 0, "GEOM+Q1": 0}
    cap = float(getattr(C, "MAX_DELIV_SPREAD_KM", 8.0))

    for rec in ledger_io.iter_shadow_decisions(cutoff):
        n_all += 1
        cands = rec.get("candidates") or rec.get("alternatives") or []
        pool = []
        for c in cands:
            m = c.get("metrics") if isinstance(c.get("metrics"), dict) else c
            if (c.get("feasibility_verdict") or c.get("feasibility")) != "MAYBE":
                continue
            if not isinstance(m, dict):
                continue
            pool.append((str(c.get("courier_id")), m))
        if len(pool) < 2:
            continue
        n_eval += 1
        pick_off = min(pool, key=lambda cm: _key(cm[1], False, 0.0))
        sp_off = _f(pick_off[1], "deliv_spread_km") or 0.0
        if sp_off > cap:
            over_cap["OFF"] += 1
        for vname, v in variants.items():
            pick_v = min(pool, key=lambda cm: _key(cm[1], True, v["q"]))
            sp_v = _f(pick_v[1], "deliv_spread_km") or 0.0
            if sp_v > cap:
                over_cap[vname] += 1
            if pick_v[0] != pick_off[0]:
                r6_off = _f(pick_off[1], "objm_r6_breach_max_min")
                r6_v = _f(pick_v[1], "objm_r6_breach_max_min")
                v["flips"].append({
                    "oid": rec.get("order_id"),
                    "d_spread": round(sp_v - sp_off, 2),
                    "d_r6": (round((r6_v or 0.0) - (r6_off or 0.0), 2)
                             if (r6_off is not None or r6_v is not None) else 0.0),
                    "d_committed": round((_f(pick_v[1], "late_pickup_committed_max") or 0.0)
                                         - (_f(pick_off[1], "late_pickup_committed_max") or 0.0), 2),
                })

    def _summ(fl):
        if not fl:
            return {"n": 0}
        ds = [x["d_spread"] for x in fl]
        dr = [x["d_r6"] for x in fl]
        dc = [x["d_committed"] for x in fl]
        return {
            "n": len(fl),
            "d_spread_med": round(statistics.median(ds), 2),
            "d_spread_mean": round(statistics.mean(ds), 2),
            "spread_improved_pct": round(100.0 * sum(1 for x in ds if x < 0) / len(ds), 1),
            "d_r6_med": round(statistics.median(dr), 2),
            "d_r6_max": round(max(dr), 2),
            "d_committed_med": round(statistics.median(dc), 2),
        }

    out = {
        "window_days": days, "records": n_all, "evaluable_ge2_maybe": n_eval,
        "over_cap_picks": over_cap, "cap_km": cap,
        "GEOM": _summ(variants["GEOM"]["flips"]),
        "GEOM+Q1": _summ(variants["GEOM+Q1"]["flips"]),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3.0)
    args = ap.parse_args()
    out = run(args.days)
    lines = [
        f"L6.C2 lexqual-geometry replay — okno {args.days}d, "
        f"rekordów {out['records']}, ocenialnych (≥2 MAYBE) {out['evaluable_ge2_maybe']}",
        f"picki spread>{out['cap_km']}km: OFF={out['over_cap_picks']['OFF']} "
        f"GEOM={out['over_cap_picks']['GEOM']} GEOM+Q1={out['over_cap_picks']['GEOM+Q1']}",
        f"GEOM (quant=0):  {json.dumps(out['GEOM'], ensure_ascii=False)}",
        f"GEOM+Q1 (1 min): {json.dumps(out['GEOM+Q1'], ensure_ascii=False)}",
        "Interpretacja: flips>0 + d_spread ujemny + d_r6 ~0/≤quant = geometria tnie",
        "rozrzut bez kosztu czasowego. Decyzja flipu (i wariantu quant) = ACK Adriana.",
    ]
    txt = "\n".join(lines)
    print(txt)
    try:
        with open(OUT_TXT, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
