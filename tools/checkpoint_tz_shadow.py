#!/usr/bin/env python3
"""checkpoint_tz_shadow.py — ETAP 5 shadow ON↔OFF dla ENABLE_CHECKPOINT_TS_WARSAW_PARSE
(sprint TZ-fix checkpointów, 2026-06-26). READ-ONLY, OSOBNY PROCES.

Cel: udowodnić POZYTYWNY wpływ fixu PRZED flipem. `picked_up_at`/`delivered_at` =
Warsaw-naive; 4 miejsca w courier_resolver parsowały je jako UTC → predykcja pozycji
no-GPS martwa (interp 0/16984), staleness zaniżona. Fix (flaga ON) parsuje przez
parse_panel_timestamp. Tu liczymy DETERMINISTYCZNIE różnicę OFF↔ON na ŻYWYM stanie:
  • % odpaleń interp (last_picked_up_interp) — dziś 0,
  • # kurierów uratowanych z fikcji no_gps (recent-activity / interp dają realny punkt),
  • mediana/max przesunięcia pozycji [km] (restauracja → noga / realny punkt),
  • # bag-orderów nowo-uznanych za zombie (staleness — KONTROLA: czy to ghosty, nie legit).

DLACZEGO build_fleet_snapshot 2× (nie własny scoring): zero dryftu — ten sam silnik
pozycji co prod. Tryb parse FORSOWANY przez patch _f4_flag (odporne na stan flags.json,
działa też po flipie). READ-ONLY: w TYM procesie neutralizujemy oba zapisy
(_save_last_known_pos store + _log_gps_quality_shadow) — NIE dotykamy żywego stanu ani
logów współdzielonych z dispatch-shadow.

Użycie:
  cd /root/.openclaw/workspace/scripts
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.checkpoint_tz_shadow
  ... --summary [--since YYYY-MM-DD]   # agregacja okna z jsonl
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import argparse
import json
import logging
import os
import statistics as stx
import time
from datetime import datetime, timezone

from dispatch_v2 import common as C
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import osrm_client

_log = logging.getLogger("checkpoint_tz_shadow")

OUT_JSONL = "/root/.openclaw/workspace/dispatch_state/checkpoint_tz_shadow.jsonl"
FLAG = "ENABLE_CHECKPOINT_TZ_SHADOW"          # kill-switch (default ON gdy timer biega)
PARSE_FLAG = "ENABLE_CHECKPOINT_TS_WARSAW_PARSE"
_SYNTH = {"no_gps", "pre_shift", "none", "", None}


def _build_with(parse_on: bool) -> dict:
    """build_fleet_snapshot z WYMUSZONYM trybem parse checkpointów (patch _f4_flag),
    z zneutralizacją zapisów (read-only). Restore w finally."""
    orig_f4 = CR._f4_flag
    orig_save = CR._save_last_known_pos
    orig_gpslog = CR._log_gps_quality_shadow

    def _patched_f4(name: str) -> bool:
        if name == PARSE_FLAG:
            return parse_on
        return orig_f4(name)

    CR._f4_flag = _patched_f4
    CR._save_last_known_pos = lambda *a, **k: None
    CR._log_gps_quality_shadow = lambda *a, **k: None
    try:
        return CR.build_fleet_snapshot()
    finally:
        CR._f4_flag = orig_f4
        CR._save_last_known_pos = orig_save
        CR._log_gps_quality_shadow = orig_gpslog


def _km(a, b):
    try:
        return round(float(osrm_client.haversine(tuple(a), tuple(b))), 3)
    except Exception:
        return None


def run_once(now: datetime | None = None) -> dict:
    if not C.flag(FLAG, True):
        return {"skipped": "flag_off"}
    now = now or datetime.now(timezone.utc)
    _t0 = time.monotonic()
    try:
        off = _build_with(False)
        on = _build_with(True)
    except Exception as e:  # noqa: BLE001
        _log.warning(f"build fail: {type(e).__name__}: {e}")
        return {"error": "build", "detail": f"{type(e).__name__}: {e}"}

    kids = set(off) | set(on)
    interp_on = interp_off = rescued = src_changed = pos_moved = 0
    bag_dropped_couriers = bag_dropped_orders = 0
    shifts = []
    detail = []
    for kid in kids:
        a = off.get(kid)
        b = on.get(kid)
        sa = getattr(a, "pos_source", None) if a else None
        sb = getattr(b, "pos_source", None) if b else None
        if sb == "last_picked_up_interp":
            interp_on += 1
        if sa == "last_picked_up_interp":
            interp_off += 1
        if (sa in _SYNTH) and (sb not in _SYNTH):
            rescued += 1
        if sa != sb:
            src_changed += 1
        d = None
        if a and b and getattr(a, "pos", None) and getattr(b, "pos", None):
            d = _km(a.pos, b.pos)
            if d is not None and d > 0.05:
                pos_moved += 1
                shifts.append(d)
        nba = len(a.bag) if a and a.bag is not None else 0
        nbb = len(b.bag) if b and b.bag is not None else 0
        if nbb < nba:
            bag_dropped_couriers += 1
            bag_dropped_orders += (nba - nbb)
        if sa != sb or (d is not None and d > 0.05) or nbb != nba:
            detail.append({"cid": kid, "src_off": sa, "src_on": sb,
                           "km_shift": d, "bag_off": nba, "bag_on": nbb})

    rec = {
        "ts": now.isoformat(),
        "n_couriers": len(kids),
        "interp_off": interp_off,
        "interp_on": interp_on,            # cel: > 0 (dziś martwe)
        "rescued_from_synth": rescued,     # uratowani z no_gps/pre_shift fikcji
        "pos_source_changed": src_changed,
        "pos_moved_gt50m": pos_moved,
        "km_shift_med": round(stx.median(shifts), 3) if shifts else None,
        "km_shift_max": round(max(shifts), 3) if shifts else None,
        "bag_dropped_couriers": bag_dropped_couriers,   # KONTROLA bezpieczeństwa
        "bag_dropped_orders": bag_dropped_orders,
        "detail": detail,
        "duration_s": round(time.monotonic() - _t0, 2),
    }
    try:
        with open(OUT_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:  # noqa: BLE001
        _log.warning(f"append fail: {e}")
    summ = {k: v for k, v in rec.items() if k != "detail"}
    _log.info(f"CHECKPOINT_TZ_SHADOW {summ}")
    return summ


def summarize(since: str | None = None) -> str:
    """Agregacja okna z jsonl → materiał pod werdykt flip (NIE werdykt)."""
    if not os.path.exists(OUT_JSONL):
        return f"brak {OUT_JSONL} — shadow nic nie zapisał (flaga/timer?)."
    rows = []
    for ln in open(OUT_JSONL, encoding="utf-8"):
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if since and str(r.get("ts", ""))[:10] < since:
            continue
        if "n_couriers" in r:
            rows.append(r)
    if not rows:
        return "0 tików w oknie."
    def _s(k):
        return sum(r.get(k, 0) or 0 for r in rows)
    n_ticks = len(rows)
    tot_cour = _s("n_couriers")
    allshift = [r["km_shift_med"] for r in rows if r.get("km_shift_med") is not None]
    return (
        f"🛰 CHECKPOINT-TZ shadow — {n_ticks} tików"
        f"{(' od '+since) if since else ''}\n"
        f"• kurierów-ticków: {tot_cour}\n"
        f"• interp odpaleń: OFF {_s('interp_off')} → ON {_s('interp_on')}"
        f" (cel: ON≫0, dziś martwe)\n"
        f"• uratowani z fikcji no_gps/pre_shift: {_s('rescued_from_synth')}\n"
        f"• pos_source zmienionych: {_s('pos_source_changed')}"
        f" | pozycja >50m: {_s('pos_moved_gt50m')}\n"
        f"• mediana z median przesunięć: "
        f"{round(stx.median(allshift),3) if allshift else '—'} km\n"
        f"• bag-orderów nowo-zombie: {_s('bag_dropped_orders')} "
        f"(u {_s('bag_dropped_couriers')} kurierów) — KONTROLA: zweryfikuj że to ghosty\n"
        f"\n📊 Materiał pod flip. GO jeśli: interp_on≫0 + rescued≫0 + przesunięcia\n"
        f"realne, a bag-zombie to faktyczne ghosty (>90min, nigdy nie domknięte)."
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--since", default=None)
    ap.add_argument("--notify", action="store_true", help="wyslij summary na Telegram (review +2dni)")
    args = ap.parse_args()
    if args.summary or args.notify:
        report = summarize(args.since)
        print(report)
        if args.notify:
            try:
                from dispatch_v2.telegram_utils import send_admin_alert
                send_admin_alert(report, source="checkpoint_tz_shadow")
            except Exception as e:  # noqa: BLE001
                print(f"[telegram fail] {type(e).__name__}: {e}")
        return 0
    print(json.dumps(run_once(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
