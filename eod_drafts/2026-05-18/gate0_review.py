#!/usr/bin/env python3
"""Gate 0 — przegląd Fazy 0 rolling late-binding (pending pool).

Analizuje 3-dniowe okno obserwacji puli pending i wydaje werdykt Gate 0.
Uruchamiany jednorazowo przez at-job 2026-05-21 09:00 Warsaw.

4 kryteria Gate 0:
  K1 coverage — upsert do puli pokrywa NEW_ORDERy (≥80%, reszta = skip no-geocode)
  K2 removed_reason — każde usunięte zlecenie z ważnym powodem (nie 'stuck')
  K3 zero stuck — brak action=stuck w logu
  K4 brak wycieku — aktualna aktywna pula mała, najstarszy wpis nieprzeterminowany

Werdykt PASS → ruszać Fazę 1 (pętla re-optymalizacji). FAIL → lista problemów.
Wynik na Telegram do grupy ziomka.
"""
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

DS = "/root/.openclaw/workspace/dispatch_state"
LOG_PATH = f"{DS}/pending_pool_log.jsonl"
POOL_PATH = f"{DS}/pending_pool.json"
EVENTS_DB = f"{DS}/events.db"
TELEGRAM_ENV = "/root/.openclaw/workspace/.secrets/telegram.env"
GROUP_CHAT_ID = "-5149910559"

# deploy Fazy 0; nadpisywalne env GATE0_WINDOW_START dla re-runu na świeżym oknie
WINDOW_START = os.environ.get("GATE0_WINDOW_START", "2026-05-18T23:00:00")
VALID_REMOVE = {"assigned_in_panel", "picked_up", "delivered", "cancelled",
                "returned_to_pool"}
COVERAGE_MIN = 0.80
LEAK_AGE_H = 3.0   # aktywny wpis starszy niż tyle godzin = podejrzenie wycieku


def _parse(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_log():
    rows = []
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return rows


def _send_telegram(text):
    """Wyślij na grupę ziomka. Defensywne — loguje fail, nie rzuca."""
    try:
        token = ""
        with open(TELEGRAM_ENV, encoding="utf-8") as f:
            for line in f:
                if line.startswith("TELEGRAM_BOT_TOKEN"):
                    token = line.split("=", 1)[1].strip().strip('"')
                    break
        if not token:
            print("gate0: brak TELEGRAM_BOT_TOKEN", file=sys.stderr)
            return
        data = urllib.parse.urlencode({"chat_id": GROUP_CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as e:
        print(f"gate0: telegram send fail: {e}", file=sys.stderr)


def main():
    now = datetime.now(timezone.utc)
    log = _load_log()
    # filtruj log do okna po `ts` — inaczej K2/K3 liczyłyby zdarzenia z całej
    # (append-only) historii zamiast z ocenianego okna
    ws = _parse(WINDOW_START)
    if ws is not None:
        log = [r for r in log
               if (_parse(r.get("ts")) is not None and _parse(r.get("ts")) >= ws)]
    upserts = {r.get("order_id") for r in log if r.get("action") == "upsert"}
    removes = [r for r in log if r.get("action") == "remove"]
    reasons = Counter(r.get("reason") for r in removes)
    stuck = [r for r in log if r.get("action") == "stuck"]
    freeze_cross = sum(1 for r in log if r.get("action") == "freeze_cross")

    # NEW_ORDERy w oknie z events.db
    new_order_oids = set()
    try:
        con = sqlite3.connect(EVENTS_DB)
        for (oid,) in con.execute(
            "SELECT DISTINCT order_id FROM events WHERE event_type='NEW_ORDER' "
            "AND created_at >= ?", (WINDOW_START,)):
            new_order_oids.add(str(oid))
        con.close()
    except Exception as e:
        print(f"gate0: events.db read fail: {e}", file=sys.stderr)

    # aktualna pula
    try:
        with open(POOL_PATH, encoding="utf-8") as f:
            pool = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pool = {}
    active = [e for e in pool.values() if not e.get("frozen", False)]
    oldest_age_h = 0.0
    for e in active:
        c = _parse(e.get("created_at"))
        if c:
            oldest_age_h = max(oldest_age_h, (now - c).total_seconds() / 3600.0)

    # ── kryteria ──
    fails = []
    covered = upserts & new_order_oids
    coverage = (len(covered) / len(new_order_oids)) if new_order_oids else 1.0
    if coverage < COVERAGE_MIN:
        missing = len(new_order_oids) - len(covered)
        fails.append(f"K1 coverage {coverage:.0%} (<{COVERAGE_MIN:.0%}); {missing} NEW_ORDER poza pulą")
    bad_reasons = {r: n for r, n in reasons.items() if r not in VALID_REMOVE}
    if bad_reasons:
        fails.append(f"K2 removed_reason nieprawidłowe: {bad_reasons}")
    if stuck:
        fails.append(f"K3 STUCK: {len(stuck)} zleceń utknęło w puli {[s.get('order_id') for s in stuck][:8]}")
    if oldest_age_h > LEAK_AGE_H:
        fails.append(f"K4 wyciek: najstarszy aktywny wpis {oldest_age_h:.1f}h (>{LEAK_AGE_H}h); aktywnych={len(active)}")

    verdict = "PASS" if not fails else "FAIL"
    lines = [
        f"🚦 GATE 0 — rolling late-binding Faza 0 — {verdict}",
        f"okno od {WINDOW_START}Z do {now.strftime('%Y-%m-%dT%H:%MZ')}",
        f"NEW_ORDER: {len(new_order_oids)} | upsert do puli: {len(upserts)} "
        f"(coverage {coverage:.0%})",
        f"usunięte: {len(removes)} {dict(reasons)}",
        f"freeze_cross: {freeze_cross} | stuck: {len(stuck)} | aktywnych teraz: {len(active)}",
    ]
    if verdict == "PASS":
        lines.append("✅ Pula jest wiernym lustrem panelu — ruszać Fazę 1 (pętla re-optymalizacji).")
    else:
        lines.append("❌ Problemy:")
        lines += [f"  • {f}" for f in fails]
    msg = "\n".join(lines)
    print(msg)
    _send_telegram(msg)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
