#!/usr/bin/env python3
"""Harness weryfikacji: jak działa pozycjonowanie kurierów BEZ GPS (last-known-pos
store, flaga ENABLE_COURIER_LAST_KNOWN_POS flipnięta 2026-06-08 19:02 UTC) +
pomiar czy same-restaurant stacking (committed-time parity) faktycznie wymaga fixu.

Read-only. Uruchom JUTRO po peaku (np. wieczorem):
    /root/.openclaw/venvs/dispatch/bin/python eod_drafts/2026-06-08/no_gps_positioning_test.py

Odpowiada na 3 pytania Adriana:
  1. Czy działa jak powinno?  → rescue events > 0, brak błędów, brak phantomów.
  2. Czy istnieje różnica?     → no_gps-best rate PRZED vs PO flipie + rescued-as-best.
  3. Czy jest na plus?         → rescued-best akceptowane (nie override) + brak R6 breach.

Plus część B: czy bundle_level1 (same-restaurant assigned stacking) wygrywa już
dziś (bonus_l1=+25) — jeśli tak, committed-time parity NIE jest potrzebny.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
RESOLVER_LOG = "/root/.openclaw/workspace/scripts/logs/courier_resolver.log"
LEARNING = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"

# Moment flipu flagi (UTC). Przed = flaga OFF (fikcja), po = flaga ON (rescue).
FLIP_TS = datetime(2026, 6, 8, 19, 2, 13, tzinfo=timezone.utc)
# Okno „czystej produkcji" — po tym czasie żaden pytest nie biegł na prod boxie
# (08.06 testy do ~19:14). JUTRO ustaw na początek dnia/peaku. Wyklucza residue.
PROD_CLEAN_TS = datetime(2026, 6, 8, 19, 20, 0, tzinfo=timezone.utc)
# Testowe cid (fixtures) — nigdy nie są realnym rescue produkcyjnym.
TEST_CIDS = {"520", "888", "999"}

RESCUE_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*LAST_KNOWN_POS_USED kid=(\S+) src=(\S+) age=([\d.]+)min")


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _pct(n, d):
    return f"{100.0*n/d:.1f}%" if d else "—"


# ── A. RESCUE EVENTS (courier_resolver.log) ──────────────────────────────────
def rescue_events():
    ev = []
    try:
        with open(RESOLVER_LOG, errors="ignore") as f:
            for line in f:
                m = RESCUE_RE.search(line)
                if not m:
                    continue
                t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                kid = m.group(2)
                if t < PROD_CLEAN_TS or kid in TEST_CIDS:
                    continue  # residue testów (pytest na prod boxie do ~19:14)
                ev.append((t, kid, m.group(3), float(m.group(4))))
    except FileNotFoundError:
        pass
    return ev


# ── shadow_decisions split na PRZED / PO flipie ──────────────────────────────
def scan_decisions():
    before, after = [], []
    try:
        with open(SHADOW, errors="ignore") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                t = _ts(r.get("ts"))
                if not t:
                    continue
                (after if t >= FLIP_TS else before).append(r)
    except FileNotFoundError:
        print("BRAK shadow_decisions.jsonl"); sys.exit(1)
    return before, after


def cands(r):
    out = []
    if r.get("best"):
        out.append(r["best"])
    out.extend(r.get("alternatives") or [])
    return out


def overrides_map():
    """order_id → True jeśli koordynator nadpisał propozycję (PANEL_OVERRIDE)."""
    ovr = {}
    try:
        with open(LEARNING, errors="ignore") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("action") == "PANEL_OVERRIDE":
                    ovr[str(r.get("order_id"))] = (
                        str(r.get("proposed_courier_id")) != str(r.get("actual_courier_id")))
    except FileNotFoundError:
        pass
    return ovr


def no_gps_best_rate(decisions):
    n = sum(1 for r in decisions if (r.get("best") or {}).get("pos_source") == "no_gps")
    return n, len(decisions)


# ── B. SAME-RESTAURANT STACKING (bundle_level1) ──────────────────────────────
def stacking_analysis(decisions):
    avail = won = 0
    lost_waitpen = []
    for r in decisions:
        cs = cands(r)
        bl1 = [c for c in cs if c.get("bundle_level1")]
        if not bl1:
            continue
        avail += 1
        best = r.get("best") or {}
        if best.get("bundle_level1"):
            won += 1
        else:
            # bundle candidate przegrał — jaka kara wait_courier (cel parytetu)?
            for c in bl1:
                wp = c.get("v3273_wait_courier_max_min")
                if wp is not None:
                    lost_waitpen.append(float(wp))
    return avail, won, lost_waitpen


def main():
    before, after = scan_decisions()
    ev = rescue_events()
    ovr = overrides_map()

    print("=" * 72)
    print("WERYFIKACJA POZYCJONOWANIA BEZ GPS — last-known-pos store")
    print(f"flip flagi: {FLIP_TS.isoformat()}  |  decyzji PRZED={len(before)} PO={len(after)}")
    print("=" * 72)

    # ── PYTANIE 1: czy działa ──
    print("\n[1] CZY DZIAŁA — rescue events (produkcyjne, po flipie):")
    print(f"    rescues: {len(ev)} | unikalni kurierzy: {len(set(e[1] for e in ev))}")
    if ev:
        ages = sorted(e[3] for e in ev)
        print(f"    age min: p50={ages[len(ages)//2]:.1f} p90={ages[int(len(ages)*0.9)]:.1f} max={ages[-1]:.1f} (TTL=25)")
        print(f"    źródła odtworzone: {dict(Counter(e[2] for e in ev))}")
        print(f"    top kurierzy: {Counter(e[1] for e in ev).most_common(5)}")
    else:
        print("    (0 — peak jeszcze nie był / kurierzy nie gubili GPS w oknie TTL)")

    # ── PYTANIE 2: czy jest różnica ──
    nb, db = no_gps_best_rate(before)
    na, da = no_gps_best_rate(after)
    resc_best = sum(1 for r in after if (r.get("best") or {}).get("pos_from_store"))
    resc_pool = sum(1 for r in after if any(c.get("pos_from_store") for c in cands(r)))
    print("\n[2] CZY ISTNIEJE RÓŻNICA — no_gps jako BEST (fikcja wygrywa pulę):")
    print(f"    PRZED flipem: {nb}/{db} = {_pct(nb,db)}")
    print(f"    PO flipie:    {na}/{da} = {_pct(na,da)}   (spadek = fikcja zastąpiona realną pozycją)")
    print(f"    rescued jako BEST: {resc_best}/{da} = {_pct(resc_best,da)} | rescued w PULI: {resc_pool}/{da} = {_pct(resc_pool,da)}")

    # ── PYTANIE 3: czy na plus ──
    rb = [r for r in after if (r.get("best") or {}).get("pos_from_store")]
    ov = sum(1 for r in rb if ovr.get(str(r.get("order_id")), False))
    print("\n[3] CZY NA PLUS — los propozycji z rescued-best:")
    if rb:
        print(f"    rescued-best propozycji: {len(rb)} | nadpisane przez koordynatora: {ov} = {_pct(ov,len(rb))}")
        print(f"    → niski override = trafne (kurier realnie był blisko); wysoki = pozycja myliła")
    else:
        print("    (brak rescued-best w oknie — uruchom po peaku)")
    print("    ⚠ R6 breach rescued-best: sprawdź backfill_decisions_outcomes_v1.jsonl po dniu (osobno)")

    # ── B. parytet potrzebny? ──
    av, wn, lwp = stacking_analysis(after if len(after) > 50 else before + after)
    print("\n[B] SAME-RESTAURANT STACKING (bundle_level1, bonus_l1=+25) — czy parytet potrzebny:")
    print(f"    decyzji z dostępnym stackingiem: {av} | bundle-kandydat WYGRAŁ: {wn} = {_pct(wn,av)}")
    if lwp:
        lwp.sort()
        print(f"    gdy PRZEGRAŁ — kara wait_courier (cel parytetu): p50={lwp[len(lwp)//2]:.1f}min max={lwp[-1]:.1f}min n={len(lwp)}")
        big = sum(1 for x in lwp if x > 10)
        print(f"    przegrane z karą wait>10min: {big} → TYLE realnie zyskałby committed-time parytet")
    else:
        print("    (brak przegranych z karą — stacking już wygrywa, parytet NIE potrzebny)")

    # ── VERDICT ──
    print("\n" + "=" * 72)
    print("VERDICT (heurystyka):")
    diff = (db and da and (nb/db) - (na/da) > 0.0) or resc_best > 0
    print(f"  • działa: {'TAK' if ev or resc_pool else 'PEAK PENDING'}")
    print(f"  • różnica: {'TAK' if diff else 'nie wykryto (za mało danych / brak luk GPS)'}")
    plus = rb and (ov / len(rb) <= 0.5)
    print(f"  • na plus: {'TAK (rescued-best akceptowane)' if plus else 'PENDING — potrzeba rescued-best + R6 outcome'}")
    parity = bool(lwp and sum(1 for x in lwp if x > 10) >= max(3, 0.2 * av))
    print(f"  • parytet committed-time POTRZEBNY: {'TAK — bundle często przegrywa z karą wait>10' if parity else 'NIE / marginalnie (bonus_l1 wystarcza)'}")
    print("=" * 72)


if __name__ == "__main__":
    main()
