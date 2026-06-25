#!/usr/bin/env python3
"""PORANNE PODSUMOWANIE smoke objm-lexr6 — one-shot 2026-06-26 08:30 UTC (=10:30 Warsaw),
po flipie (07:30) i werdykcie (08:15). Adrian 2026-06-25: „napisz mi rano podsumowanie smoke".

READ-ONLY: czyta logs/objm_lexr6_smoke.log (zdarzenia START/FLIP/VERDICT/ROLLBACK) + flag_state
+ metryki okna od flipu (przez zahartowany monitor) → składa jedno „Dzień dobry" na Telegram.
Nic nie mutuje. Fail-soft.
"""
import sys
from datetime import datetime, timezone

SCRIPTS = "/root/.openclaw/workspace/scripts"
SMOKE_LOG = f"{SCRIPTS}/logs/objm_lexr6_smoke.log"


def _tg(msg):
    try:
        sys.path.insert(0, SCRIPTS)
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg, priority="low")
    except Exception as e:
        print(f"[telegram pominięte: {e!r}]")


def _log_tail(markers, n=400):
    """Zwróć ostatnie linie smoke-logu zawierające którykolwiek marker."""
    try:
        lines = open(SMOKE_LOG, encoding="utf-8", errors="replace").read().splitlines()[-n:]
    except Exception:
        return []
    return [l for l in lines if any(m in l for m in markers)]


def main():
    try:
        sys.path.insert(0, SCRIPTS)
        from dispatch_v2.tools import objm_lexr6_canary_monitor as M
        now = datetime.now(timezone.utc)
        since = now.replace(hour=7, minute=30, second=0, microsecond=0)  # od flipu 07:30 UTC

        flags = M.flag_state()
        cur = M.shadow_metrics(since) or {}
        log = M.log_signals(since)
        n, reord, errs = cur.get("n", 0), log.get("reorders", 0), log.get("errors", 0)
        p95 = cur.get("lat_p95")

        ev_start = _log_tail(["SMOKE START wysłany", "FLIP OK"])
        ev_clean = _log_tail(["SMOKE CLEAN"])
        ev_stop = _log_tail(["SMOKE STOP", "ROLLBACK done"])
        ev_skip = _log_tail(["SKIP:", "ABORT:"])

        select_on = flags.get("select_on")
        # ustal nagłówek stanu
        if errs > 0 or ev_stop:
            head = "🔴 SMOKE STOP — cofnięte (auto-rollback)"
            tail = "Flaga z powrotem OFF. Przyczyna: pick-failed. Sprawdź logs/dispatch.log."
        elif select_on and (ev_clean or ev_start):
            head = "🟢 SMOKE CLEAN — canary LECI"
            tail = ("SELECT został ON → wjeżdża w lunch/dinner peak jako canary; monitor co 10 min "
                    "pilnuje gate'y (STOP/WARN→tu). Faza 3 (sustain 2-3 dni) + Faza 4 wg runbooka.")
        elif ev_skip:
            head = "⚪ SMOKE SKIP/ABORT — flip NIE wykonany"
            tail = "Patrz logs/objm_lexr6_smoke.log; flip można ponowić ręcznie wg runbooka."
        elif select_on:
            head = "🟢 SMOKE — SELECT ON (canary aktywne)"
            tail = "Werdykt szczegółowy: brak w logu (sprawdź), ale flaga ON i 0 błędów w oknie."
        else:
            head = "⚪ SMOKE — SELECT OFF"
            tail = "Flip nie odpalił lub cofnięty. Sprawdź logs/objm_lexr6_smoke.log."

        base = ""
        try:
            import json, os
            bp = "/root/.openclaw/workspace/dispatch_state/objm_lexr6_canary_baseline.json"
            if os.path.exists(bp):
                b = json.load(open(bp))
                base = (f"\nbaseline(peak): KOORD {b.get('koord_pct')}% · ACK+ALERT "
                        f"{b.get('ack_alert_pct')}% · p95 {b.get('lat_p95')}ms")
        except Exception:
            pass

        msg = (
            f"☀️ Dzień dobry — podsumowanie SMOKE objm-lexr6 (Faza 2)\n"
            f"{head}\n"
            f"Okno od flipu 07:30 UTC: decyzji {n} · reorderów {reord} · pick-failed {errs} · "
            f"p95 {p95}ms\n"
            f"KOORD {cur.get('koord_pct')}% · ACK+ALERT {cur.get('ack_alert_pct')}% · "
            f"AUTO {cur.get('auto_pct')}%{base}\n"
            f"SELECT={select_on} SHADOW={flags.get('shadow_on')}\n"
            f"{tail}"
        )
        print(msg)
        _tg(msg)
        return 0
    except Exception as e:
        _tg(f"🟡 Poranne podsumowanie smoke objm-lexr6 — błąd ({e!r}). Sprawdź ręcznie "
            f"logs/objm_lexr6_smoke.log + flags.json.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
