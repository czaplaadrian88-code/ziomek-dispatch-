#!/usr/bin/env python3
"""Weryfikacja live: Sprint OBJ F4 Krok 1 — proxy pozycji kuriera no-gps.

F4 Krok 1 (commit 7098fee, deploy 2026-05-18 ~21:32 UTC): krok 2
courier_resolver dla picked_up ordera ustawia cs.pos = pickup_coords
(realny punkt) zamiast delivery_coords. Nowy pos_source =
`last_picked_up_pickup`.

Sprawdza okno ~24h od deployu (obejmuje lunch-peak wt 19.05):
  - rozkład `pos_source` na kandydatach — KLUCZ: ile decyzji ma
    `last_picked_up_pickup` (F4 strzela) vs `last_picked_up_delivery`
    (fallback / kurier bez pickup_coords);
  - rate werdyktów KOORD — F4 daje uczciwe wejścia, więc realne ciasne
    R6 przestają być maskowane; oczekiwany lekki wzrost KOORD (obserwacja,
    brak twardego progu — porównanie do okna sprzed deployu);
  - shadow.log: brak nowych Traceback od momentu deployu.

Odpala się raz przez at-job wt 2026-05-19 ~21:00 UTC, raport na Telegram.
"""
import json
import sys
from collections import Counter

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

SD = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
SHLOG = "/root/.openclaw/workspace/scripts/logs/shadow.log"
DEPLOY = "2026-05-18T21:32:00"          # restart dispatch-shadow (UTC)
PRE_START = "2026-05-17T21:32:00"       # okno 24h sprzed deployu (baseline KOORD)


def _send(msg):
    print(msg)
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg)
        print(f"telegram send: {'OK' if ok else 'FAIL (ok!=True)'}")
    except Exception as e:
        print(f"telegram fail: {e}")


def _cands(d):
    out = []
    if isinstance(d.get("best"), dict):
        out.append(d["best"])
    out += [c for c in (d.get("alternatives") or []) if isinstance(c, dict)]
    return out


post_total = post_koord = 0
pre_total = pre_koord = 0
pos_src = Counter()
try:
    with open(SD) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts", "")
            verdict = (d.get("verdict") or "").upper()
            if ts >= DEPLOY:
                post_total += 1
                if verdict == "KOORD":
                    post_koord += 1
                for c in _cands(d):
                    ps = c.get("pos_source")
                    if ps:
                        pos_src[ps] += 1
            elif ts >= PRE_START:
                pre_total += 1
                if verdict == "KOORD":
                    pre_koord += 1
except FileNotFoundError:
    _send(f"❌ verify OBJ F4: brak pliku {SD}")
    sys.exit(1)

# --- shadow.log: Traceback od deployu ---
tracebacks = 0
try:
    with open(SHLOG, errors="replace") as f:
        for line in f:
            if line[:19].replace(" ", "T") < DEPLOY:
                continue
            if "Traceback" in line:
                tracebacks += 1
except FileNotFoundError:
    pass

if post_total == 0:
    _send("⚠ verify OBJ F4: ZERO decyzji od deployu 18.05 21:32 UTC — "
          "brak ruchu, re-check ręcznie.")
    sys.exit(0)

f4_fire = pos_src.get("last_picked_up_pickup", 0)
deliv = pos_src.get("last_picked_up_delivery", 0)
post_rate = 100.0 * post_koord / post_total
pre_rate = (100.0 * pre_koord / pre_total) if pre_total else 0.0

healthy = tracebacks == 0
head = "✅" if healthy else "❌"
top = ", ".join(f"{k}={v}" for k, v in pos_src.most_common(6)) or "brak"

verdict = (
    f"{head} verify OBJ F4 Krok 1 (proxy pos picked_up→pickup_coords) — "
    f"okno od deployu 18.05 21:32 UTC, {post_total} decyzji:\n"
    f"F4 strzela: pos_source=last_picked_up_pickup × {f4_fire} "
    f"(fallback last_picked_up_delivery × {deliv})\n"
    f"rozkład pos_source: {top}\n"
    f"KOORD rate: {post_rate:.1f}% ({post_koord}/{post_total}) · "
    f"baseline 24h pre-deploy {pre_rate:.1f}% ({pre_koord}/{pre_total})\n"
    f"Traceback od deployu = {tracebacks}"
)
if f4_fire == 0:
    verdict += ("\n⚠ F4 ani razu nie strzelił — brak kuriera no-gps z picked_up "
                "orderem w oknie; niekonkluzywne, re-check w gęstszym oknie.")
if not healthy:
    verdict += "\n⚠ NIEZDROWE — Traceback>0; sprawdź shadow.log."

_send(verdict)
