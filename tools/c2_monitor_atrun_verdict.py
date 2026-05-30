#!/usr/bin/env python3
"""Meta-verdict at#96 (C2 monitor pełny re-run po peaku) → Telegram.

Czyta log z runu monitora odpalonego przez `at` po zamknięciu lunch-peaku i
wysyła JEDNO potwierdzenie na Telegram: czy job FAKTYCZNIE się odpalił (log
istnieje, niepusty, ma linię `[report]` = doszedł do końca) + wyciągniętą linię
werdyktu (🟢/🟡/🔴) i licznik flipów. Read-only — nic nie liczy od nowa, tylko
parsuje artefakt at#96. Cel: potwierdzenie bez wracania do sesji.
"""
import os
import sys

SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

ATRUN_LOG = "/root/.openclaw/workspace/scripts/logs/c2_monitor_peak_atrun.log"
REPORT = (
    "/root/.openclaw/workspace/scripts/dispatch_v2/"
    "eod_drafts/2026-05-30/c2_monitor_peak.md"
)


def build_message():
    if not os.path.exists(ATRUN_LOG):
        return (
            "🔴 at#96 C2 monitor: BRAK LOGU — job się NIE odpalił "
            f"(no file {ATRUN_LOG}). Sprawdź atd / `atq` ręcznie."
        )
    with open(ATRUN_LOG) as fh:
        body = fh.read().strip()
    if not body:
        return "🔴 at#96 C2 monitor: log PUSTY — job odpalił ale 0 outputu (crash?)."

    lines = body.splitlines()
    verdict_line = next(
        (ln for ln in lines if ln.lstrip().startswith(("🟢", "🟡", "🔴"))),
        "(brak linii werdyktu w logu)",
    )
    counts_line = next((ln for ln in lines if ln.startswith("decyzji")), "")
    reached_end = any(ln.startswith("[report]") for ln in lines)
    tg_line = next((ln for ln in lines if "[telegram]" in ln), "")
    tg_ok = "ok=True" in tg_line

    head = "🟢 at#96 OK" if reached_end else "🟡 at#96 NIEPEŁNY (brak [report] — możliwy crash w trakcie)"
    parts = [
        f"{head} — C2 post-flip monitor (pełne okno po peaku)",
        counts_line,
        verdict_line.strip(),
        f"digest→Telegram (at#96): {'wysłany ok' if tg_ok else 'NIE potwierdzony (' + (tg_line or 'brak linii [telegram]') + ')'}",
        f"raport: {REPORT}{'' if os.path.exists(REPORT) else ' (BRAK PLIKU!)'}",
    ]
    return "\n".join(p for p in parts if p)


def main():
    msg = build_message()
    print(msg)
    try:
        from dispatch_v2 import telegram_utils as T

        ok = T.send_admin_alert(msg)
        print(f"\n[telegram] send_admin_alert ok={ok}")
    except Exception as e:  # noqa: BLE001
        print(f"\n[telegram] FAIL {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
