#!/usr/bin/env python3
"""Odzyskiwalność fikcji no_gps (2026-06-22) — read-only.

Pytanie: ile z momentów no_gps+empty dałoby się zakotwiczyć pozycją z OSTATNIEJ
aktywności (gdzie kurier był/odbierał/doręczył), gdyby okno/TTL było dłuższe?

Metoda (bez surowych lat/lon — log ich nie ma): oś czasu pos_source per kurier.
Dla każdego momentu no_gps+empty patrzę wstecz na NAJNOWSZĄ decyzję tego samego
kuriera z REALNĄ kotwicą (pos_source != no_gps). Odstęp = jak świeża była
dostępna pozycja. Bucket 25/45/60/90/120 min. Brak kotwicy w całej historii =
genuinie bez danych (np. zepsuty GPS — robota operacyjna, nie algorytm).

To DOLNA granica odzysku (kotwica = decyzja, nie ciągły GPS), ale pokazuje, czy
luka pokrycia jest realna i jak duża. Fail-soft.
"""
import json
import os
from collections import defaultdict, Counter
from datetime import datetime

LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]
ANCHORED = {"gps", "last_delivered", "last_picked_up_pickup", "last_picked_up_recent",
            "last_picked_up_delivery", "last_picked_up_interp", "last_assigned_pickup",
            "post_wave"}
BUCKETS = [25, 45, 60, 90, 120]


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _pos_source(c):
    if not isinstance(c, dict):
        return None
    ps = c.get("pos_source")
    if ps is None and isinstance(c.get("metrics"), dict):
        ps = c["metrics"].get("pos_source")
    return ps


def _is_no_gps_empty(c):
    if _pos_source(c) != "no_gps":
        return False
    m = c.get("metrics") if isinstance(c.get("metrics"), dict) else c
    bsize = (m.get("r6_bag_size") or m.get("bag_size_before") or m.get("r7_bag_size") or 0)
    return (bsize or 0) == 0


def main():
    # per courier: list of (ts, pos_source)
    timeline = defaultdict(list)
    for p in LOGS:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                best = d.get("best") or {}
                cid = str(best.get("courier_id"))
                ps = _pos_source(best)
                t = _ts(d.get("ts"))
                if cid and ps and t:
                    timeline[cid].append((t, ps, _is_no_gps_empty(best)))

    for cid in timeline:
        timeline[cid].sort(key=lambda x: x[0])

    total_nogps = 0
    recoverable = Counter()          # bucket -> count
    anchor_kind = Counter()          # pos_source kotwicy użytej do odzysku (≤120)
    no_prior_anchor = 0              # brak jakiejkolwiek kotwicy wstecz
    genuinely_anchorless_cids = []   # kurierzy: 100% no_gps, zero kotwic w historii
    gap_recoverable_cids = Counter()

    for cid, events in timeline.items():
        has_anchor_ever = any(ps in ANCHORED for (_, ps, _) in events)
        nogps_here = [e for e in events if e[2]]
        if nogps_here and not has_anchor_ever:
            genuinely_anchorless_cids.append((cid, len(nogps_here)))
        for i, (t, ps, isng) in enumerate(events):
            if not isng:
                continue
            total_nogps += 1
            # szukaj wstecz najnowszej kotwicy
            prior = None
            for j in range(i - 1, -1, -1):
                if events[j][1] in ANCHORED:
                    prior = events[j]
                    break
            if prior is None:
                no_prior_anchor += 1
                continue
            gap = (t - prior[0]).total_seconds() / 60.0
            placed = False
            for b in BUCKETS:
                if gap <= b:
                    recoverable[b] += 1
                    placed = True
                    break
            if placed:
                anchor_kind[prior[1]] += 1
                # najmniejszy bucket, w którym wpadł — przypisz do cid jeśli ≤60
                if gap <= 60:
                    gap_recoverable_cids[cid] += 1
            # gap > max bucket → liczony jako no_prior w praktyce (za stary)
            if not placed:
                no_prior_anchor += 1

    print("=== ODZYSKIWALNOŚĆ no_gps+empty z kotwicy ostatniej aktywności ===")
    print(f"momentów no_gps+empty (best): {total_nogps}")
    print(f"kurierów w osi czasu: {len(timeline)}")
    print()
    print("Czy istniała świeża REALNA pozycja kuriera tuż przed momentem fikcji?")
    cum = 0
    for b in BUCKETS:
        cum += recoverable[b]
        seg = recoverable[b]
        print(f"  kotwica ≤{b:3d} min wstecz: +{seg:4d}   (skumulowane ≤{b}: {cum}  = {100.0*cum/total_nogps:.1f}% fikcji)")
    print(f"  brak kotwicy ≤120 min / nigdy: {no_prior_anchor}  ({100.0*no_prior_anchor/total_nogps:.1f}%)")
    print()
    print(f"Typ kotwicy, którą dałoby się użyć (≤120 min): {dict(anchor_kind.most_common())}")
    print()
    print("=== GENUINIE bez danych (100% no_gps, zero kotwic — robota operacyjna/GPS) ===")
    ga_total = sum(n for _, n in genuinely_anchorless_cids)
    for cid, n in sorted(genuinely_anchorless_cids, key=lambda x: -x[1])[:10]:
        print(f"  cid={cid}: {n} momentów")
    print(f"  RAZEM genuinie bez danych: {ga_total} momentów "
          f"({100.0*ga_total/total_nogps:.1f}% fikcji)")
    print()
    rec60 = recoverable[25] + recoverable[45] + recoverable[60]
    print(f">>> PODSUMOWANIE: ~{rec60} ({100.0*rec60/total_nogps:.1f}%) miało realną pozycję ≤60 min wcześniej "
          f"→ luka pokrycia (algorytm). ~{ga_total} ({100.0*ga_total/total_nogps:.1f}%) genuinie bez GPS (operacyjne).")


if __name__ == "__main__":
    main()
