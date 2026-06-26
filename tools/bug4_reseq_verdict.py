#!/usr/bin/env python3
"""Werdykt bug #4 reseq shadow: czyta dispatch_state/bug4_reseq_shadow.jsonl (frozen
RETIME seq vs fresh solve) i liczy materialność — ile worków/dzień ma seq_differs +
delta_min>0, median/p90 delta, suma straconych minut. Read-only.
Użycie: bug4_reseq_verdict.py [--since YYYY-MM-DD] [--notify]"""
import argparse, json, os
from datetime import datetime, timezone

JSONL = "/root/.openclaw/workspace/dispatch_state/bug4_reseq_shadow.jsonl"
OUT = "/root/.openclaw/workspace/dispatch_state/bug4_reseq_verdict.txt"
MATERIAL_MIN = 1.0  # delta_min >= → liczone jako realny zygzak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    since = a.since

    n = 0
    differ = 0
    material = 0
    deltas = []
    by_day = {}
    worst = []
    if not os.path.exists(JSONL):
        msg = f"BUG#4 reseq verdict: brak {JSONL} — logger nic nie zapisał (mało wielo-zlec. RETIME?)."
        print(msg)
        _emit(OUT, msg, a.notify)
        return
    with open(JSONL, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            day = (d.get("ts") or "")[:10]
            if since and day < since:
                continue
            n += 1
            slot = by_day.setdefault(day, [0, 0, 0, 0.0])
            slot[0] += 1
            dm = d.get("delta_min")
            if d.get("seq_differs"):
                differ += 1
                slot[1] += 1
            if isinstance(dm, (int, float)) and dm >= MATERIAL_MIN:
                material += 1
                deltas.append(dm)
                slot[2] += 1
                slot[3] += dm
                worst.append((dm, d.get("ts"), d.get("cid"), d.get("bag")))
    deltas.sort()
    k = len(deltas)
    med = deltas[k // 2] if k else 0.0
    p90 = deltas[int(k * 0.9)] if k else 0.0
    total = round(sum(deltas), 1)
    lines = [
        "=== BUG #4 reseq verdict (frozen RETIME seq vs fresh solve) ===",
        f"okno: {since or 'całość'}",
        f"próbek (wielo-zlec. RETIME): {n}",
        f"  seq_differs (inna kolejność): {differ}  ({100*differ/max(1,n):.0f}%)",
        f"  delta>={MATERIAL_MIN:g}min (realny zygzak):  {material}  ({100*material/max(1,n):.0f}%)",
        f"  delta drive: median={med:.1f}  p90={p90:.1f}  max={deltas[-1] if deltas else 0:.1f}  SUMA={total} min",
        "--- per dzień (próbek / seq_differs / material / suma_min) ---",
    ]
    for day in sorted(by_day):
        s = by_day[day]
        lines.append(f"  {day}: {s[0]:4d} / {s[1]:4d} / {s[2]:4d} / {s[3]:.0f} min")
    lines.append("--- 6 najgorszych ---")
    for dm, ts, cid, bag in sorted(worst, reverse=True)[:6]:
        lines.append(f"  +{dm:4.1f}min  {str(ts)[:19]}  cid={cid} bag={bag}")
    # rekomendacja: GO jeśli material>=20% prób i median>=1.5min
    pct = 100 * material / max(1, n)
    go = pct >= 20.0 and med >= 1.5
    lines.append("")
    lines.append(f"WERDYKT: {'GO — materialność spełniona, sprint naprawy źródła (feasibility↔route_simulator↔plan_recheck)' if go else 'WAIT/NO — materialność poniżej progu (≥20% prób + median≥1.5min) lub mała próba'}")
    msg = "\n".join(lines)
    print(msg)
    _emit(OUT, msg, a.notify)


def _emit(path, msg, notify):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass
    if notify:
        try:
            import sys
            sys.path.insert(0, "/root/.openclaw/workspace/scripts")
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert("🔵 " + msg[:3500])
        except Exception as e:
            print(f"(notify fail: {type(e).__name__}: {e})")


if __name__ == "__main__":
    main()
