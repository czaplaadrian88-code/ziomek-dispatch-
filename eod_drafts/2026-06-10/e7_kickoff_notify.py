#!/usr/bin/env python3
"""E7 KICKOFF (zaplanowane 2026-06-10, sesja audytowa, ACK Adriana „ustaw na 17.06").

Jednorazowy at-job: 2026-06-17 06:00 UTC (08:00 Warsaw). Read-only — liczy tydzień
danych PANEL_AGREE (ETAP 3) + sygnały pomocnicze i wysyła Adrianowi raport gotowości
ETAPU 7 (re-tune wag) na Telegram. NICZEGO nie zmienia w systemie.

Dry-run (bez wysyłki, print na stdout): E7_DRY=1 python3 ...
Uruchomienie: cd /root/.openclaw/workspace/scripts && venv python dispatch_v2/eod_drafts/2026-06-10/e7_kickoff_notify.py
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta

LEARNING_LOG = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
SHADOW_LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
VIOLATIONS = "/root/.openclaw/workspace/dispatch_state/restaurant_violations.jsonl"
CZASOWKA_EVAL = "/root/.openclaw/workspace/dispatch_state/czasowka_eval_log.jsonl"
SINCE = "2026-06-10T19:20"  # PANEL_AGREE live od 10.06 19:20 UTC (ETAP 3) — wcześniejsze OVERRIDE bez pary AGREE zaniżałyby rate


def _iter_jsonl(path, tail_bytes=None):
    try:
        with open(path, "rb") as f:
            if tail_bytes:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - tail_bytes))
                f.readline()
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def acceptance_stats():
    agree, override = 0, 0
    per_tier = Counter()
    per_tier_all = Counter()
    for d in _iter_jsonl(LEARNING_LOG):
        a = d.get("action")
        if a not in ("PANEL_AGREE", "PANEL_OVERRIDE"):
            continue
        if str(d.get("ts", "")) < SINCE:
            continue
        tier = d.get("proposed_tier") or "?"
        per_tier_all[tier] += 1
        if a == "PANEL_AGREE":
            agree += 1
            per_tier[tier] += 1
        else:
            override += 1
    total = agree + override
    rate = (100.0 * agree / total) if total else None
    tier_lines = []
    for t, n_all in per_tier_all.most_common():
        r = 100.0 * per_tier.get(t, 0) / n_all if n_all else 0.0
        tier_lines.append(f"{t}: {r:.0f}% ({per_tier.get(t,0)}/{n_all})")
    return agree, override, rate, tier_lines


def shadow_stats(days=7):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    n = auto = bnst = 0
    for d in _iter_jsonl(SHADOW_LOG, tail_bytes=120_000_000):
        if str(d.get("ts", "")) < cutoff:
            continue
        n += 1
        if d.get("auto_route") == "AUTO":
            auto += 1
        if "best_not_score_top" in str(d.get("auto_route_reason") or ""):
            bnst += 1
    return n, auto, bnst


def violations_count():
    return sum(1 for _ in _iter_jsonl(VIOLATIONS))


def czasowka_proactive():
    """E5 KPI: shadow score-based (pola sb_* z KROKU 1) vs realne decyzje EMIT/FORCE."""
    would = force = emit = 0
    for d in _iter_jsonl(CZASOWKA_EVAL, tail_bytes=20_000_000):
        if str(d.get("ts", "")) < SINCE:
            continue
        if d.get("sb_would_assign") is True or d.get("would_assign") is True:
            would += 1
        dec = d.get("decision")
        if dec == "FORCE_ASSIGN":
            force += 1
        elif dec == "EMIT":
            emit += 1
    return would, emit, force


def build_message():
    lines = ["⏰ E7 KICKOFF — re-tune wag (zaplanowane 10.06 po audycie)", ""]
    try:
        agree, override, rate, tier_lines = acceptance_stats()
        if rate is None:
            lines.append("⚠ PANEL_AGREE: BRAK danych od 10.06 — sprawdź pętlę z ETAPU 3 zanim ruszysz E7!")
        else:
            lines.append(f"Acceptance 10-17.06: {rate:.0f}% (AGREE {agree} / OVERRIDE {override})")
            if tier_lines:
                lines.append("Per tier: " + " | ".join(tier_lines[:5]))
    except Exception as e:
        lines.append(f"⚠ acceptance: błąd ({type(e).__name__})")
    try:
        n, auto, bnst = shadow_stats()
        if n:
            lines.append(f"Shadow 7d: {n} decyzji, AUTO {auto}, best≠score-top {100.0*bnst/n:.0f}%")
    except Exception as e:
        lines.append(f"⚠ shadow: błąd ({type(e).__name__})")
    try:
        would, emit, force = czasowka_proactive()
        lines.append(f"Czasówki od 10.06: sb_would_assign {would} | EMIT {emit} | FORCE_ASSIGN {force} (E5)")
    except Exception:
        pass
    try:
        lines.append(f"Naruszenia restauracji (plik, od 10.06): {violations_count()}")
    except Exception:
        pass
    lines += [
        "",
        "Następny krok: sesja CC ETAP 7 (Fable 5, /effort high).",
        "READ FIRST: dispatch_v2/eod_drafts/2026-06-10/AUDIT_FIX_PLAN_2026-06-10.md → ETAP 7",
        "+ memory ziomek-audit-2026-06-10 (Z-07/Z-08/Z-14/Z-15) + panel_agree_baseline.md.",
        "Pamiętaj (odkrycie E2/Z-10): progi Fazy 7 przeliczyć na NOWYM marginie "
        "(best≠score-top było 68%, stary margin zawyżony o medianę 105 pkt).",
    ]
    return "\n".join(lines)


def main():
    msg = build_message()
    if os.environ.get("E7_DRY") == "1":
        print(msg)
        return 0
    sys.path.insert(0, "/root/.openclaw/workspace/scripts")
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg)
        print(f"{datetime.now(timezone.utc).isoformat()} e7_kickoff sent={ok}")
        return 0 if ok else 1
    except Exception as e:
        print(f"{datetime.now(timezone.utc).isoformat()} e7_kickoff SEND FAIL {type(e).__name__}: {e}")
        print(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
