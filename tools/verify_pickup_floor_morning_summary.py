#!/usr/bin/env python3
"""Poranne podsumowanie werdyktów live-verify floora odbioru z OBU peaków (Adrian 2026-06-25:
„napisz mi rano podsumowanie werdyktów z obu peaków").

Czyta pliki wynikowe zapisane przez `verify_pickup_floor_peak.py` (lunch + dinner peak 26.06),
scala w jedno poranne podsumowanie i wysyła na Telegram (send_admin_alert). Read-only.
Brak pliku (job nie odpalił) = zaznaczone, nie wywala się.

Użycie (at-job sobota rano): python3 -m dispatch_v2.tools.verify_pickup_floor_morning_summary --notify
"""
import argparse
import os
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

LOGS = "/root/.openclaw/workspace/scripts/logs"
PEAKS = [("LUNCH", f"{LOGS}/verify_pickup_floor_lunch.txt"),
         ("DINNER", f"{LOGS}/verify_pickup_floor_dinner.txt")]


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _verdict_of(text):
    if not text:
        return "❓ BRAK WYNIKU"
    first = text.splitlines()[0]
    for tag in ("✅ PASS", "🟡 PASS", "❌ FAIL"):
        if tag in first:
            return tag
    return "❓ ?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()

    blocks = []
    verdicts = []
    for label, path in PEAKS:
        txt = _read(path)
        v = _verdict_of(txt)
        verdicts.append(v)
        if txt:
            blocks.append(f"━━ {label} ━━\n{txt}")
        else:
            blocks.append(f"━━ {label} ━━\n❓ brak pliku {os.path.basename(path)} — job nie odpalił? "
                          f"(sprawdź atq / logs/verify_pickup_floor_cron.log)")

    if all("✅ PASS" in v or "🟡 PASS" in v for v in verdicts):
        overall = "✅ floor działa na OBU peakach"
    elif any("❌ FAIL" in v for v in verdicts):
        overall = "❌ UWAGA — FAIL na którymś peaku, sprawdź szczegóły"
    else:
        overall = "❓ niepełne — brakuje wyniku z któregoś peaku"

    msg = (f"☀️ Dzień dobry — podsumowanie live-verify floora odbioru (peaki pt 26.06)\n"
           f"LUNCH: {verdicts[0]}   |   DINNER: {verdicts[1]}\n"
           f"Łącznie: {overall}\n\n"
           + "\n\n".join(blocks))
    print(msg)

    try:
        with open(f"{LOGS}/verify_pickup_floor_morning_summary.txt", "w") as f:
            f.write(msg + "\n")
    except OSError:
        pass

    if a.notify:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(msg, source="verify_pickup_floor_morning_summary")
            print("[Telegram wysłany]")
        except Exception as e:  # noqa: BLE001
            print(f"[Telegram fail: {e}]")


if __name__ == "__main__":
    main()
