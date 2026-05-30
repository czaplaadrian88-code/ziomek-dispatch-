#!/usr/bin/env python3
"""Meta-werdykt at#100 (OBJ FRESH merge-reminder) → Telegram.

Czyta log z runu remindera odpalonego przez `at #100` ~5 min wcześniej i wysyła
JEDNO potwierdzenie na Telegram: czy job FAKTYCZNIE się odpalił (log istnieje,
niepusty, ma linię `[telegram]` = doszedł do końca) + czy reminder-digest poszedł
(`ok=True`) + wyciągniętą linię „branch ... ahead". Read-only — nic nie liczy od
nowa, tylko parsuje artefakt at#100. Cel: pewność bez ręcznego tailowania logu.
"""
import os
import sys

SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

ATRUN_LOG = "/root/.openclaw/workspace/scripts/logs/obj_fresh_merge_reminder_atrun.log"


def build_message():
    if not os.path.exists(ATRUN_LOG):
        return (
            "🔴 at#100 merge-reminder: BRAK LOGU — job się NIE odpalił "
            f"(no file {ATRUN_LOG}). Sprawdź atd / `atq` ręcznie."
        )
    with open(ATRUN_LOG) as fh:
        body = fh.read().strip()
    if not body:
        return "🔴 at#100 merge-reminder: log PUSTY — job odpalił ale 0 outputu (crash?)."

    lines = body.splitlines()
    branch_line = next((ln for ln in lines if ln.startswith("branch ")), "")
    last_line = next((ln for ln in lines if ln.startswith("ostatni:")), "")
    tg_line = next((ln for ln in lines if "[telegram]" in ln), "")
    tg_ok = "ok=True" in tg_line
    reached_end = bool(tg_line)  # linia [telegram] = skrypt doszedł do wysyłki

    head = "🟢 at#100 OK" if reached_end else "🟡 at#100 NIEPEŁNY (brak linii [telegram] — możliwy crash w trakcie)"
    parts = [
        f"{head} — OBJ FRESH merge-reminder odpalony",
        branch_line,
        last_line,
        f"reminder→Telegram (at#100): {'wysłany ok' if tg_ok else 'NIE potwierdzony (' + (tg_line or 'brak linii [telegram]') + ')'}",
        "→ jeśli reminder doszedł: zdecyduj o merge brancha wg werdyktu +7d (#99).",
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
