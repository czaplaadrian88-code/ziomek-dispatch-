#!/usr/bin/env python3
"""Raport SELECTION VETO SHADOW — czyta live shadow log (po deployu 2026-06-01 23:08).

Agreguje pole selection_veto_shadow (oba diale: informed + any): ile decyzji,
ile by veto przestawiło, redukcja przeciw-kierunkowości zwycięzcy, typ flipu
(pusty/solo vs bag-aligned). Read-only.

Użycie: python3 selection_veto_shadow_report.py [--since-iso 2026-06-01T23:08:19+00:00]
"""
import json, sys
from datetime import datetime, timezone

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DEPLOY = "2026-06-01T23:08:19+00:00"


def main():
    since = DEPLOY
    if "--since-iso" in sys.argv:
        since = sys.argv[sys.argv.index("--since-iso") + 1]
    cut = datetime.fromisoformat(since)

    rows = []
    with open(SHADOW) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts")
            if not ts:
                continue
            try:
                if datetime.fromisoformat(ts) < cut:
                    continue
            except Exception:
                continue
            sv = d.get("selection_veto_shadow")
            if isinstance(sv, dict):
                rows.append((d, sv))

    print(f"decyzje z selection_veto_shadow (od {since}): {len(rows)}")
    if not rows:
        print("  (brak — czekaj na ruch / kolejny peak)")
        return

    def is_cross(c, thr=-0.3):
        return isinstance(c, (int, float)) and c < thr

    live_cross = sum(1 for _, sv in rows if is_cross(sv.get("live_winner_cosine"), -0.3))
    live_cross5 = sum(1 for _, sv in rows if is_cross(sv.get("live_winner_cosine"), -0.5))
    print(f"  live zwycięzca cross: cos<-.3 {live_cross} ({100*live_cross/len(rows):.0f}%) | cos<-.5 {live_cross5}")

    for dial in ("informed", "any"):
        ch = [(d, sv) for d, sv in rows if isinstance(sv.get(dial), dict) and sv[dial].get("changed")]
        to_empty = sum(1 for _, sv in ch if (sv[dial].get("veto_winner_bag_size") or 0) == 0)
        to_bag = len(ch) - to_empty
        # cross-dir zwycięzcy PO zastosowaniu veta tego dialu (hipotetycznie)
        post_cross = 0
        for d, sv in rows:
            di = sv.get(dial) or {}
            cos = di.get("veto_winner_cosine") if di.get("changed") else sv.get("live_winner_cosine")
            if is_cross(cos, -0.5):
                post_cross += 1
        print(f"\n  [{dial:8s}] flipów: {len(ch)} ({100*len(ch)/len(rows):.0f}%) | →pusty/solo: {to_empty} | →bag-aligned: {to_bag}")
        print(f"             cross<-.5 zwycięzca: live {live_cross5} → veto-{dial} {post_cross}")
        for d, sv in ch[:6]:
            di = sv[dial]
            print(f"    oid={d['order_id']} live={sv.get('live_winner_cid')}(cos={sv.get('live_winner_cosine')},"
                  f"spread={sv.get('live_winner_deliv_spread_km')}) → {di.get('veto_winner_cid')}"
                  f"(cos={di.get('veto_winner_cosine')},pos={di.get('veto_winner_pos_source')},"
                  f"bag={di.get('veto_winner_bag_size')})")


if __name__ == "__main__":
    main()
