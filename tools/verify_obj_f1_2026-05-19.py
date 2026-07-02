#!/usr/bin/env python3
"""Weryfikacja live: Sprint OBJ F1 — R6 soft deadline (coeff=100 skalibrowany 18.05).

Sprawdza na oknie lunch-peak wt 2026-05-19 (09:00-12:00 UTC = 11-14 Warsaw)
czy F1 faktycznie pracuje na realnych bundlach:
  - decyzje z kandydatem OR-Tools (best.plan.strategy == "ortools") — bag>=2,
    jedyna ścieżka dotykana przez soft deadline;
  - instrumentacja F0 `objm_*` obecna na kandydatach (żywa);
  - route_simulator.log: `OBJ_F1_DEADLINE_BUILD_FAIL` MUSI być 0 (budowa
    soft-deadline nie może rzucać);
  - rozkład `objm_r6_breach_max_min` na bundlach bag>=2 (monitoring — z F1
    aktywnym mean ~6-7 min wg sweepu kalibracyjnego; brak twardego progu).

Odpala się raz przez at-job wt 2026-05-19 ~12:45 UTC, raport na Telegram.
"""
import json
import statistics
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary odczyt TYLKO żywego pliku po cichu tracił
# okno po rotacji (logrotate size 100M / daily). Semantyka metryk BEZ ZMIAN
# (per-rekord filtry zostają w konsumencie; iter_jsonl_lines zachowuje
# prefiltry stringowe).
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io
from contextlib import nullcontext as _nullcontext

SD = ledger_io.LEDGER["shadow"]
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
sd_total = 0          # decyzje w oknie
dec_ortools = 0       # decyzje z >=1 kandydatem ortools
dec_objm = 0          # decyzje z >=1 kandydatem z objm_
breaches = []         # objm_r6_breach_max_min na bundlach bag>=2
spans = []
try:
    with _nullcontext(_rotated_logs.iter_jsonl_lines(SD, None)) as f:
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
                        b = c.get("objm_r6_breach_max_min")
                        s = c.get("objm_route_span_min")
                        if isinstance(b, (int, float)):
                            breaches.append(float(b))
                        if isinstance(s, (int, float)):
                            spans.append(float(s))
            dec_ortools += has_ot
            dec_objm += has_objm
except FileNotFoundError:
    _send(f"❌ verify OBJ F1: brak pliku {SD}")
    sys.exit(1)

# --- route_simulator.log: OBJ_F1_DEADLINE_BUILD_FAIL ---
build_fail = 0
try:
    with open(RSLOG, errors="replace") as f:
        for line in f:
            if line[:19] < RS_CUTOFF:
                continue
            if "OBJ_F1_DEADLINE_BUILD_FAIL" in line:
                build_fail += 1
except FileNotFoundError:
    pass

# --- Werdykt ---
if sd_total == 0:
    _send("⚠ verify OBJ F1: ZERO decyzji w oknie peaku wt 19.05 09-12 UTC — "
          "brak ruchu, re-check ręcznie.")
    sys.exit(0)

if dec_ortools == 0:
    _send(f"⚠ verify OBJ F1: {sd_total} decyzji w peaku, ale 0 z kandydatem "
          f"OR-Tools (bag>=2) — F1 nie miał czego dotknąć (solo-only okno). "
          f"objm_ obecne w {dec_objm} decyzjach, build_fail={build_fail}. "
          f"Niekonkluzywne — re-check w gęstszym oknie.")
    sys.exit(0)

healthy = build_fail == 0 and dec_objm > 0
head = "✅" if healthy else "❌"
n = len(breaches)
br_line = (
    f"r6_breach bundli bag>=2 (n={n}): mean={statistics.mean(breaches):.1f} "
    f"max={max(breaches):.1f} min · span mean={statistics.mean(spans):.1f}"
    if n else "brak bundli bag>=2 z metrykami objm")

verdict = (
    f"{head} verify OBJ F1 (R6 soft deadline coeff=100) — peak wt 19.05 "
    f"09-12 UTC, {sd_total} decyzji:\n"
    f"decyzje z OR-Tools (bag>=2) = {dec_ortools} · z objm_ = {dec_objm}\n"
    f"OBJ_F1_DEADLINE_BUILD_FAIL = {build_fail} (MUSI 0)\n"
    f"{br_line}"
)
if not healthy:
    verdict += ("\n⚠ NIEZDROWE — build_fail>0 lub brak objm_; "
                "sprawdź route_simulator.log + flagi dispatch-shadow.")

_send(verdict)
