#!/usr/bin/env python3
"""Diagnostyka pokrycia pos_source (2026-06-22) — read-only.

Pytanie Adriana: czy Ziomek przewiduje pozycję kuriera bez GPS z ostatniej
aktywności (last_delivered/last_picked_up/last_assigned), czy spada do fikcji
centrum (no_gps)? Ile i KTÓRZY kurierzy lądują na czystym no_gps mimo że są
aktywni (mają ostatnie doręczenia → powinni mieć kotwicę)?

Liczy rozkład pos_source dla best wszystkich decyzji + dla no_gps+empty
sprawdza, czy ten sam kurier MA inne decyzje z realną kotwicą (last_*) w
oknie — jeśli tak, to luka pokrycia, nie brak danych. Fail-soft.
"""
import json
import os
from collections import Counter, defaultdict

LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]
ANCHORED = {"gps", "last_delivered", "last_picked_up_pickup", "last_picked_up_recent",
            "last_picked_up_delivery", "last_assigned_pickup", "post_wave"}


def _pos_source(c):
    if not isinstance(c, dict):
        return None
    ps = c.get("pos_source")
    if ps is None and isinstance(c.get("metrics"), dict):
        ps = c["metrics"].get("pos_source")
    return ps


def _pos_from_store(c):
    if not isinstance(c, dict):
        return None
    v = c.get("pos_from_store")
    if v is None and isinstance(c.get("metrics"), dict):
        v = c["metrics"].get("pos_from_store")
    return v


def main():
    best_ps = Counter()
    nogps_empty_cids = Counter()
    cid_pos_sources = defaultdict(Counter)   # cid -> Counter(pos_source) across all its best appearances
    nogps_from_store = Counter()
    lines = 0
    for p in LOGS:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                lines += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                best = d.get("best") or {}
                ps = _pos_source(best)
                if ps is None:
                    continue
                best_ps[ps] += 1
                cid = str(best.get("courier_id"))
                cid_pos_sources[cid][ps] += 1
                if ps == "no_gps":
                    m = best.get("metrics") or {}
                    bsize = (m.get("r6_bag_size") or m.get("bag_size_before")
                             or m.get("r7_bag_size") or 0)
                    if (bsize or 0) == 0:
                        nogps_empty_cids[cid] += 1
                        nogps_from_store[str(_pos_from_store(best))] += 1

    print("=== ROZKŁAD pos_source dla best (cały żywy log) ===")
    tot = sum(best_ps.values())
    for ps, n in best_ps.most_common():
        tag = "FIKCJA-CENTRUM" if ps == "no_gps" else ("kotwica" if ps in ANCHORED else "inne")
        print(f"  {ps:24s} {n:5d}  {100.0*n/tot:5.1f}%   [{tag}]")
    anchored_n = sum(n for ps, n in best_ps.items() if ps in ANCHORED)
    nogps_n = best_ps.get("no_gps", 0)
    print(f"  --- kotwica łącznie: {anchored_n} ({100.0*anchored_n/tot:.1f}%)  "
          f"| czysta fikcja no_gps: {nogps_n} ({100.0*nogps_n/tot:.1f}%)")

    print("\n=== no_gps+empty best — per kurier + czy MA kotwicę gdzie indziej ===")
    print("(jeśli kurier ma też decyzje z last_*/gps → łańcuch DZIAŁA, fikcja = chwilowa luka, nie brak danych)")
    for cid, n in nogps_empty_cids.most_common(12):
        others = cid_pos_sources[cid]
        anchored_for_cid = {ps: c for ps, c in others.items() if ps in ANCHORED}
        has_anchor = sum(anchored_for_cid.values())
        verdict = "MA kotwicę gdzie indziej → LUKA POKRYCIA" if has_anchor else "nigdy nie miał kotwicy (genuinie brak)"
        print(f"  cid={cid:5s} no_gps+empty={n:4d} | inne pos_source tego cid: "
              f"{dict(others)} → {verdict}")

    print(f"\n=== no_gps+empty: pos_from_store flaga ===")
    print(f"  {dict(nogps_from_store)}  (True=rescue ze store zadziałał ale dalej no_gps? / None=brak pola)")
    print(f"\nlinie={lines}")


if __name__ == "__main__":
    main()
