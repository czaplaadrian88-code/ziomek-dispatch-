#!/usr/bin/env python3
"""Weryfikacja live: Sprint OBJ F2 — koszt SPAN (idle) + retire P3-D1.

Sprawdza na oknie lunch-peak wt 2026-05-19 (09:00-12:00 UTC = 11-14 Warsaw)
czy F2 pracuje na realnych bundlach:
  - decyzje z kandydatem OR-Tools (plan.strategy == "ortools", bag>=2) —
    jedyna ścieżka dotykana przez span cost;
  - instrumentacja F0 `objm_*` obecna (żywa);
  - rozkład `objm_idle_total_min` / `objm_route_span_min` na bundlach bag>=2
    (monitoring — z F2 aktywnym idle floty ~-10% wg sweepu kalibracyjnego;
    brak twardego progu, porównanie obserwacyjne);
  - route_simulator.log: `V328_P3D1_COST` MUSI być 0 (P3-D1 retired sprintem
    F2 — jego log nie ma prawa się już pojawić); brak nowych Traceback.

Odpala się raz przez at-job wt 2026-05-19 ~13:15 UTC, raport na Telegram.
"""
import json
import statistics
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

SD = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
RSLOG = "/root/.openclaw/workspace/scripts/logs/route_simulator.log"
PEAK_START = "2026-05-19T09:00:00"
PEAK_END = "2026-05-19T12:00:00"
RS_CUTOFF = "2026-05-19 09:00:00"


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


# --- shadow_decisions: okno peaku ---
sd_total = 0
dec_ortools = 0
dec_objm = 0
idles = []
spans = []
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
            if not (PEAK_START <= ts <= PEAK_END):
                continue
            sd_total += 1
            has_ot = has_objm = False
            for c in _cands(d):
                plan = c.get("plan")
                if isinstance(plan, dict) and plan.get("strategy") == "ortools":
                    has_ot = True
                if any(k.startswith("objm") for k in c):
                    has_objm = True
                    if (c.get("r6_bag_size") or 0) >= 2:
                        i = c.get("objm_idle_total_min")
                        s = c.get("objm_route_span_min")
                        if isinstance(i, (int, float)):
                            idles.append(float(i))
                        if isinstance(s, (int, float)):
                            spans.append(float(s))
            dec_ortools += has_ot
            dec_objm += has_objm
except FileNotFoundError:
    _send(f"❌ verify OBJ F2: brak pliku {SD}")
    sys.exit(1)

# --- route_simulator.log: V328_P3D1_COST (retired — musi 0) + Traceback ---
p3d1_log = 0
tracebacks = 0
try:
    with open(RSLOG, errors="replace") as f:
        for line in f:
            if line[:19] < RS_CUTOFF:
                continue
            if "V328_P3D1_COST" in line:
                p3d1_log += 1
            if "Traceback" in line:
                tracebacks += 1
except FileNotFoundError:
    pass

# --- Werdykt ---
if sd_total == 0:
    _send("⚠ verify OBJ F2: ZERO decyzji w oknie peaku wt 19.05 09-12 UTC — "
          "brak ruchu, re-check ręcznie.")
    sys.exit(0)

if dec_ortools == 0:
    _send(f"⚠ verify OBJ F2: {sd_total} decyzji w peaku, ale 0 z kandydatem "
          f"OR-Tools (bag>=2) — span cost nie miał czego dotknąć (solo-only "
          f"okno). objm_ w {dec_objm} decyzjach, V328_P3D1_COST={p3d1_log}. "
          f"Niekonkluzywne — re-check w gęstszym oknie.")
    sys.exit(0)

healthy = p3d1_log == 0 and tracebacks == 0 and dec_objm > 0
head = "✅" if healthy else "❌"
n = len(idles)
metr = (
    f"bundle bag>=2 (n={n}): idle mean={statistics.mean(idles):.1f} "
    f"max={max(idles):.1f} min · span mean={statistics.mean(spans):.1f} min"
    if n else "brak bundli bag>=2 z metrykami objm")

verdict = (
    f"{head} verify OBJ F2 (span cost coeff=1.0, P3-D1 retired) — peak "
    f"wt 19.05 09-12 UTC, {sd_total} decyzji:\n"
    f"decyzje z OR-Tools (bag>=2) = {dec_ortools} · z objm_ = {dec_objm}\n"
    f"V328_P3D1_COST = {p3d1_log} (MUSI 0 — P3-D1 retired) · "
    f"Traceback = {tracebacks}\n"
    f"{metr}"
)
if not healthy:
    verdict += ("\n⚠ NIEZDROWE — P3D1-log>0 / Traceback>0 / brak objm_; "
                "sprawdź route_simulator.log + env dispatch-shadow.")

_send(verdict)
