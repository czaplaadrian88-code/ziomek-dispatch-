"""WERDYKT min-delivered-at shadow (Adrian, przypomnienie 27.06).

Czyta shadow_decisions.jsonl, agreguje `min_delivered_at_shadow` (non-null), liczy:
(1) % decyzji `changed`, (2) rozkład `mda_delivers_sooner_min`, (3) regresja floty
Pareto (R6/spread/late: mda vs live). Materialność ≥20%. Wysyła Telegram do Adriana
+ zapisuje pełny raport. Uruchom ręcznie: cd /root/.openclaw/workspace/scripts &&
venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-06-25/min_delivered_at_verdict.py
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
OUT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-25/min_delivered_at_verdict_result.txt"
SINCE = "2026-06-25T12:24:00"  # od restartu z metryką


def _pct(a, b):
    return (100.0 * a / b) if b else 0.0


def main():
    total = 0
    nonnull = []
    try:
        for line in open(SHADOW, encoding="utf-8"):
            if "min_delivered_at_shadow" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts", "")
            if ts < SINCE:
                continue
            total += 1
            v = d.get("min_delivered_at_shadow")
            if v is not None:
                nonnull.append(v)
    except FileNotFoundError:
        msg = "WERDYKT min-delivered-at: brak shadow_decisions.jsonl — sprawdź ręcznie."
        _emit(msg, msg)
        return

    n = len(nonnull)
    changed = [v for v in nonnull if v.get("changed")]
    sooner = sorted(x for v in nonnull
                    if isinstance((x := v.get("mda_delivers_sooner_min")), (int, float)) and x > 0)
    # regresja floty Pareto: mda gorszy od live?
    def _worse(v, mk, lk):
        m, l = v.get(mk), v.get(lk)
        return isinstance(m, (int, float)) and isinstance(l, (int, float)) and m > l + 1e-9
    r6_worse = sum(1 for v in changed if _worse(v, "mda_r6_max_bag_time_min", "live_r6_max_bag_time_min"))
    spread_worse = sum(1 for v in changed if _worse(v, "mda_deliv_spread_km", "live_deliv_spread_km"))
    late_worse = sum(1 for v in changed if _worse(v, "mda_new_pickup_late_min", "live_new_pickup_late_min"))

    materiality = _pct(len(changed), n)
    med = sooner[len(sooner) // 2] if sooner else 0.0
    p90 = sooner[int(0.9 * len(sooner))] if sooner else 0.0
    mx = sooner[-1] if sooner else 0.0

    lines = [
        "🔔 WERDYKT min-delivered-at shadow (przypomnienie 27.06)",
        f"decyzje z metryką (non-null): {n} | changed (min-total≠live): {len(changed)} ({materiality:.0f}%)",
        f"materialność {'≥20% ✅' if materiality >= 20 else '<20% — przedłuż shadow ⚠'}",
        f"wcześniej do klienta (changed): mediana {med:.1f} / p90 {p90:.1f} / max {mx:.1f} min",
        f"regresja floty (mda gorszy niż live, z {len(changed)} changed): "
        f"R6 {r6_worse} | spread {spread_worse} | late {late_worse}",
        "",
        "Werdykt (decyzja Adriana):",
        "  A = flip 'min-total' jako primary obiektyw selekcji (osobny sprint, mapa kompletności"
        " _late_pickup_score_first_key + _best_effort_* + objm_lexr6.lex_qual RAZEM)",
        "  B = dostroić istniejące committed_pickup+food_age do sumy 1:1",
        "  neither = jeśli regresja floty znacząca (psuje R6/spread/late)",
        "Kontekst: memory/min-delivered-at-shadow-2026-06-25.md, commit 60cfa57.",
    ]
    if materiality < 20 or n < 20:
        lines.append("⚠ Mało danych — rozważ przedłużenie shadowa zamiast werdyktu.")
    # rekomendacja heurystyczna
    if len(changed) and (r6_worse + spread_worse + late_worse) == 0 and med >= 3:
        lines.append("→ Wstępna rekomendacja: A/B warte rozważenia (zysk czasu, brak regresji floty).")
    elif len(changed) and (r6_worse + spread_worse) > 0.3 * len(changed):
        lines.append("→ Wstępna rekomendacja: ostrożnie — widoczna regresja floty (skłania ku neither).")

    full = "\n".join(lines)
    short = full[:900]
    _emit(short, full)


def _emit(short, full):
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(full + "\n")
    except Exception:
        pass
    sent = False
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(short, source="min_delivered_at_verdict")
        sent = True
    except Exception as e:
        print("telegram send failed:", repr(e))
    print(full)
    print(f"\n[telegram sent={sent}] [result -> {OUT}]")


if __name__ == "__main__":
    main()
