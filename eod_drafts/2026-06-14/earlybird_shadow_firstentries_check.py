#!/usr/bin/env python3
"""EARLYBIRD-01 shadow — sanity „czy pierwsze wpisy wpadają" (zaplanowane 14.06, ACK „jutro w dzień").

Jednorazowy at-job: 2026-06-15 ~10:00 UTC (12:00 Warsaw, ruch). READ-ONLY end-to-end
dowód w prodzie: czy early_bird KOORD odpalają na żywo (obs) I czy forward-shadow je
loguje (earlybird_shadow.jsonl). Łapie regresję „early_birdy są, shadow milczy".
Wysyła krótki status na Telegram. NIE jest to finalna analiza (ta = at#143, 18.06).

Dry-run: EB_DRY=1 venv python dispatch_v2/eod_drafts/2026-06-14/earlybird_shadow_firstentries_check.py
"""
import json
import os
import sys
from datetime import datetime, timezone

SHADOW_LOG = "/root/.openclaw/workspace/dispatch_state/earlybird_shadow.jsonl"
OBS_DIR = "/root/.openclaw/workspace/dispatch_state/observability"
DEPLOY_TS = "2026-06-14T21:05"  # shadow LIVE od restartu


def _iter_jsonl(path):
    try:
        with open(path, "rb") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except FileNotFoundError:
        return
    except Exception:
        return


def shadow_records():
    return [d for d in _iter_jsonl(SHADOW_LOG) if str(d.get("ts", "")) >= DEPLOY_TS]


def live_earlybird_count():
    """Liczba early_bird KOORD w obs od deployu (dziś + 15.06 — oba post-deploy)."""
    n = 0
    for fn in ("candidate_decisions_20260614.jsonl", "candidate_decisions_20260615.jsonl"):
        for d in _iter_jsonl(os.path.join(OBS_DIR, fn)):
            if str(d.get("ts", "")) < DEPLOY_TS:
                continue
            dec = d.get("decision") or {}
            if "early_bird" in str(dec.get("reason") or ""):
                n += 1
    return n


def build_message():
    sh = shadow_records()
    live_eb = live_earlybird_count()
    n = len(sh)
    L = ["🔎 EARLYBIRD shadow — sanity pierwszych wpisów (15.06)", ""]
    L.append(f"shadow rekordów (od 14.06 21:05): {n}")
    L.append(f"early_bird KOORD na żywo (obs, post-deploy): {live_eb}")
    L.append("")
    if n > 0:
        res = sum(1 for d in sh if d.get("would_resolve") is True)
        L.append(f"✅ WPISY WPADAJĄ — end-to-end działa w prodzie.")
        L.append(f"   would_resolve=True: {res}/{n} ({100.0*res/n:.0f}%) (wstępnie, pełny werdykt at#143 18.06)")
        sample = sh[-2:]
        for d in sample:
            L.append(f"   • oid={d.get('order_id')} {d.get('minutes_ahead')}min cf={d.get('cf_verdict')} resolve={d.get('would_resolve')}")
    elif live_eb > 0:
        L.append("🔴 UWAGA: early_birdy ODPALAJĄ na żywo, ale shadow PUSTY → możliwy bug zapisu.")
        L.append("   Sprawdź: flags.json ENABLE_EARLYBIRD_T30_SHADOW=true + uprawnienia do dispatch_state/")
        L.append("   + journalctl dispatch-shadow | grep earlybird_t30_shadow (fail-soft warning?).")
    else:
        L.append("🟡 Jeszcze 0 early_birdów post-deploy (wcześnie / mało ruchu). Re-check później; at#143 18.06 zrobi pełną analizę.")
    return "\n".join(L)


def main():
    msg = build_message()
    if os.environ.get("EB_DRY") == "1":
        print(msg)
        return 0
    sys.path.insert(0, "/root/.openclaw/workspace/scripts")
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg)
        print(f"{datetime.now(timezone.utc).isoformat()} earlybird_firstentries sent={ok}")
        return 0 if ok else 1
    except Exception as e:
        print(f"{datetime.now(timezone.utc).isoformat()} earlybird_firstentries SEND FAIL {type(e).__name__}: {e}")
        print(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
