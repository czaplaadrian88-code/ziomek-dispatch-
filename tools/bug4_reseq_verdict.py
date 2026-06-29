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
    suspect = 0   # #1 audyt: invariant_violation (delta<−0.5 = fresh gorszy = pomiar skażony) — WYKLUCZ
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
            # #1 audyt: rekord z naruszonym inwariantem (fresh GORSZY od frozen) = pomiar skażony
            # (semantyka ≠ live) → NIE licz do differ/material. Stare rekordy (przed fix 29.06) nie
            # mają tego klucza — i tak zarchiwizowane jako widmo (czytaj tylko --since 2026-06-29).
            if d.get("invariant_violation"):
                suspect += 1
                continue
            slot = by_day.setdefault(day, [0, 0, 0, 0.0])
            slot[0] += 1
            dm = d.get("delta_min")
            # REALNY sygnał = kolejność DOSTAW inna (deliv_seq_differs); fallback seq_differs (stare)
            if d.get("deliv_seq_differs", d.get("seq_differs")):
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
        f"próbek (wielo-zlec. RETIME): {n}  (z tego suspect/inwariant-naruszony WYKLUCZONE: {suspect})",
        f"  deliv_seq_differs (inna kolejność DOSTAW): {differ}  ({100*differ/max(1,n-suspect):.0f}% z ważnych)",
        f"  delta>={MATERIAL_MIN:g}min (realny zygzak):  {material}  ({100*material/max(1,n-suspect):.0f}% z ważnych)",
        f"  delta drive: median={med:.1f}  p90={p90:.1f}  max={deltas[-1] if deltas else 0:.1f}  SUMA={total} min",
        "--- per dzień (próbek / seq_differs / material / suma_min) ---",
    ]
    for day in sorted(by_day):
        s = by_day[day]
        lines.append(f"  {day}: {s[0]:4d} / {s[1]:4d} / {s[2]:4d} / {s[3]:.0f} min")
    lines.append("--- 6 najgorszych ---")
    for dm, ts, cid, bag in sorted(worst, reverse=True)[:6]:
        lines.append(f"  +{dm:4.1f}min  {str(ts)[:19]}  cid={cid} bag={bag}")
    # rekomendacja: GO jeśli material>=20% WAŻNYCH prób i median>=1.5min. Gdy suspect>10% ważnych
    # → instrument jeszcze skażony, NIE ufać werdyktowi (oracle-recheck przed GO).
    valid = max(1, n - suspect)
    pct = 100 * material / valid
    suspect_pct = 100 * suspect / max(1, n)
    go = pct >= 20.0 and med >= 1.5 and suspect_pct <= 10.0
    lines.append("")
    lines.append(f"ZDROWIE INSTRUMENTU: suspect (inwariant delta<−0.5 naruszony) {suspect}/{n} = {suspect_pct:.0f}% "
                 f"{'⚠ >10% — pomiar wciąż skażony, oracle-recheck PRZED GO' if suspect_pct > 10 else '✓ ≤10% (instrument zdrowy)'}")
    lines.append(f"WERDYKT: {'GO — materialność spełniona, sprint naprawy źródła (feasibility↔route_simulator↔plan_recheck)' if go else 'WAIT/NO — materialność poniżej progu (≥20% ważnych + median≥1.5min, suspect≤10%) lub mała próba'}")
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
