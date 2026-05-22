#!/usr/bin/env python3
"""2-dniowy przegląd obserwacyjny FIX1+FIX2 (phantom 'trajektoria' bonus).

Uruchamiany przez at-job 2026-05-24 06:30 UTC. Flagi ENABLE_BUG2_GAP_FROM_PLAN /
ENABLE_V326_WAVE_VETO_NEW_DROP są OFF — ale wszystkie pola potrzebne do
KONTRFAKTYCZNEJ oceny są już logowane w shadow_decisions.jsonl:
  - r1_new_drop_dist_km / r1_new_drop_cosine  (FIX2: km>2.5 i cos<0.5 → veto)
  - plan.pickup_at[oid] vs free_at_utc          (FIX1: real_gap>10 → druga fala → +30 phantom)
  - v319h_bug2_continuation_bonus               (czy w ogóle dostał +30)

Liczy: ile +30 by zniknęło (FIX1/FIX2), ile decyzji zmieniłoby najlepszego kuriera,
rozkład (km,cos) do kalibracji progów. Wysyła skrót na Telegram + zapis raportu.
NIE zmienia nic w produkcji. Werdykt flip/kalibracja = człowiek/CC.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path("/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl")
REPORT = Path("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-22/fix12_review_report.txt")
WIN_START = "2026-05-22"
WIN_END = "2026-05-25"  # exclusive-ish (string compare on ts prefix)
KM_THRESH = 2.5
COS_THRESH = 0.5
GATE_MIN = 10.0  # BUG2_INTERLEAVE_GATE_MIN


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _real_gap_min(cand, oid):
    """plan.pickup_at[oid] - free_at_utc (min). >GATE_MIN => druga fala."""
    plan = cand.get("plan") or {}
    pa = (plan.get("pickup_at") or {}).get(str(oid))
    fa = cand.get("free_at_utc")
    pu, fad = _parse(pa), _parse(fa)
    if pu is None or fad is None:
        return None
    return (pu - fad).total_seconds() / 60.0


def _would_fix2_veto(cand):
    km = cand.get("r1_new_drop_dist_km")
    cos = cand.get("r1_new_drop_cosine")
    if km is None or cos is None:
        return False
    return cand.get("v319h_bug2_continuation_bonus", 0) > 0 and km > KM_THRESH and cos < COS_THRESH


def _would_fix1_zero(cand, oid):
    if cand.get("v319h_bug2_continuation_bonus", 0) <= 0:
        return False
    g = _real_gap_min(cand, oid)
    return g is not None and g > GATE_MIN


def main():
    if not LOG.exists():
        print(f"brak {LOG}")
        return 1
    rows = []
    for line in LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = str(d.get("ts", ""))
        if not (WIN_START <= ts[:10] < WIN_END):
            continue
        rows.append(d)

    n = len(rows)
    bagged = 0          # decyzje gdzie best ma policzony r1_new_drop (bag>=1)
    fix2_best = 0       # best dostałby FIX2 veto
    fix1_best = 0       # best dostałby FIX1 zero (druga fala)
    either_best = 0     # best traci +30 którąkolwiek drogą
    rerank_changes = 0  # po usunięciu +30 z best zmienia się najlepszy kurier
    kmcos = []          # (km,cos) dla wszystkich kandydatów-bagów z +30

    for d in rows:
        best = d.get("best") or {}
        alts = d.get("alternatives") or []
        oid = d.get("order_id")
        cands = [best] + list(alts)
        for c in cands:
            if c.get("r1_new_drop_cosine") is not None and c.get("v319h_bug2_continuation_bonus", 0) > 0:
                kmcos.append((c.get("r1_new_drop_dist_km"), c.get("r1_new_drop_cosine")))
        if best.get("r1_new_drop_cosine") is not None:
            bagged += 1
        f2 = _would_fix2_veto(best)
        f1 = _would_fix1_zero(best, oid)
        if f2:
            fix2_best += 1
        if f1:
            fix1_best += 1
        if f1 or f2:
            either_best += 1
            # rerank: odejmij +30 od best, sprawdź czy alt go bije
            try:
                best_adj = best.get("score", 0) - best.get("v319h_bug2_continuation_bonus", 0)
                best_alt = None
                for a in alts:
                    a_adj = a.get("score", 0)
                    if _would_fix2_veto(a) or _would_fix1_zero(a, oid):
                        a_adj -= a.get("v319h_bug2_continuation_bonus", 0)
                    if best_alt is None or a_adj > best_alt[1]:
                        best_alt = (a.get("courier_id"), a_adj)
                if best_alt and best_alt[1] > best_adj and best_alt[0] != best.get("courier_id"):
                    rerank_changes += 1
            except Exception:
                pass

    lines = [
        f"FIX1+FIX2 — przegląd 2-dniowy ({WIN_START}..{WIN_END}), tag bug2-newdrop-fix-shadow-off-2026-05-22",
        f"decyzji w oknie: {n} | z policzonym r1_new_drop (bag>=1): {bagged}",
        f"BEST dostałby FIX2 veto (km>{KM_THRESH} & cos<{COS_THRESH}): {fix2_best}",
        f"BEST dostałby FIX1 zero (real_gap>{GATE_MIN}min = druga fala): {fix1_best}",
        f"BEST traci +30 którąkolwiek drogą: {either_best}",
        f"zmienia się najlepszy kurier po usunięciu +30: {rerank_changes}",
    ]
    if kmcos:
        kms = sorted(x[0] for x in kmcos if x[0] is not None)
        coss = sorted(x[1] for x in kmcos if x[1] is not None)
        def pct(a, p):
            return a[min(len(a) - 1, int(len(a) * p))] if a else None
        lines.append(
            f"rozkład km (bag+30): min {kms[0]:.1f} / p50 {pct(kms,0.5):.1f} / p90 {pct(kms,0.9):.1f} / max {kms[-1]:.1f}"
            if kms else "rozkład km: brak"
        )
        lines.append(
            f"rozkład cos (bag+30): min {coss[0]:.2f} / p50 {pct(coss,0.5):.2f} / p90 {pct(coss,0.9):.2f} / max {coss[-1]:.2f}"
            if coss else "rozkład cos: brak"
        )
    lines.append("")
    lines.append("DECYZJA (człowiek/CC): jeśli sygnał realny i bez fałszywych trafień → flip flag ON")
    lines.append("(potem 2 dni walidacji zachowania, reguła 'dwudniowa walidacja w dispatchu').")
    lines.append("Jeśli mało danych / dużo borderline → korekta progów KM/COS. Kontekst: lessons #143, sprint_timeline ARCHIVE 2026-05-22.")
    report = "\n".join(lines)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report + "\n", encoding="utf-8")
    print(report)

    msg = "⏰ Przegląd 2-dniowy FIX1+FIX2 (phantom 'trajektoria')\n\n" + report
    try:
        sys.path.insert(0, "/root/.openclaw/workspace/scripts")
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg[:3500])
        print(f"telegram send_admin_alert={ok}")
    except Exception as e:
        print(f"telegram fail (raport zapisany lokalnie): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
