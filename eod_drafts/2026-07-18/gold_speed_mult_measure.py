#!/usr/bin/env python3
"""Kalibracja speed-mult per tier (TODO werdyktu D3-gold) — pomiar composition-clean.

ratio_drive = (actual_deliver_min − dwell_dropoff(tier)) / (osrm_ff × traffic_mult_v1(ts))
= ile realnie trwa noga jazdy vs to, co silnik by policzył z mult=1.0 (flaga OFF, stan live).
Mediana ratio per tier ≈ poprawny DRIVE_SPEED_MULT_BY_TIER[tier].

Okna: PRIMARY ≥2026-07-04 (era gps5b/bieżący reżim), SANITY 2026-06-14..07-03.
Filtry: ff>0.5 min, actual>1.5 min (batch-click artefakty), 0.2<ratio<4 (outliery).
"""
import json
import sqlite3
import statistics as st
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import common as C  # noqa: E402

DB = "/root/.openclaw/workspace/dispatch_state/eta_calib.db"
TIERS = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"

tiers_raw = json.load(open(TIERS))
tier_of = {}
for cid, v in tiers_raw.items():
    if cid.startswith("_"):
        continue
    t = (v.get("bag") or {}).get("tier") if isinstance(v, dict) else None
    if t:
        tier_of[str(cid)] = t

def parse_ts(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def build_clean_direct(rows):
    """Zbiór order_id z CZYSTĄ bezpośrednią nogą: między ts_pickup a ts_deliver
    tego zlecenia NIE ma ŻADNEGO innego eventu (pickup/deliver innego zlecenia)
    tego samego kuriera — inaczej actual zawiera pośrednie stopy worka
    (kontaminacja kompozycji, którą pierwszy pomiar boleśnie pokazał: ~1.6×
    dla wszystkich tierów)."""
    events = {}
    span = {}
    for oid, cid, day, tsp, tsd, ff, actual in rows:
        p, d = parse_ts(tsp), parse_ts(tsd)
        if p is None or d is None or d <= p:
            continue
        events.setdefault(str(cid), []).append((p, oid))
        events.setdefault(str(cid), []).append((d, oid))
        span[oid] = (str(cid), p, d)
    for cid in events:
        events[cid].sort()
    clean = set()
    for oid, (cid, p, d) in span.items():
        ok = True
        for t, other in events.get(cid, []):
            if other == oid:
                continue
            if p < t < d:
                ok = False
                break
        if ok:
            clean.add(oid)
    return clean

def window(rows, lo, hi, clean):
    out = {}
    dropped = {"not_clean": 0, "no_tier": 0, "bad_ts": 0, "bad_ff": 0, "bad_actual": 0, "outlier": 0}
    for oid, cid, day, tsp, tsd, ff, actual in rows:
        if not (lo <= day <= hi):
            continue
        if oid not in clean:
            dropped["not_clean"] += 1
            continue
        tier = tier_of.get(str(cid))
        if not tier:
            dropped["no_tier"] += 1
            continue
        dt = parse_ts(tsp)
        if dt is None:
            dropped["bad_ts"] += 1
            continue
        if not ff or ff <= 0.5:
            dropped["bad_ff"] += 1
            continue
        if not actual or actual <= 1.5:
            dropped["bad_actual"] += 1
            continue
        pred = ff * C.get_traffic_multiplier(dt.astimezone(timezone.utc))
        dwell_do = C.dwell_for_tier(tier)[1]
        act_drive = actual - dwell_do
        if act_drive <= 0.3 or pred <= 0.3:
            dropped["bad_actual"] += 1
            continue
        r = act_drive / pred
        if not (0.2 < r < 4.0):
            dropped["outlier"] += 1
            continue
        out.setdefault(tier, []).append(r)
    return out, dropped

def report(tag, out, dropped):
    print(f"\n== {tag} ==  (dropped: {dropped})")
    print(f"{'tier':6} {'n':>5} {'median':>7} {'p25':>6} {'p75':>6} {'mean':>6}  tabela(26.06)")
    tab = C.DRIVE_SPEED_MULT_BY_TIER
    for tier in ("gold", "std+", "std", "slow", "new"):
        rs = sorted(out.get(tier, []))
        if len(rs) < 8:
            print(f"{tier:6} {len(rs):>5}  (za mało danych)")
            continue
        med = st.median(rs)
        p25 = rs[int(0.25 * len(rs))]
        p75 = rs[int(0.75 * len(rs))]
        print(f"{tier:6} {len(rs):>5} {med:7.3f} {p25:6.3f} {p75:6.3f} {st.mean(rs):6.3f}  {tab.get(tier)}")

db = sqlite3.connect(DB)
rows = db.execute(
    "SELECT order_id, courier_id, day, ts_pickup, ts_deliver, osrm_deliv_ff_min, actual_deliver_min "
    "FROM eta_calib_features").fetchall()
print(f"wierszy total: {len(rows)}; kurierów z tierem: {len(tier_of)}")
gold_cids = sorted(c for c, t in tier_of.items() if t == "gold")
print(f"gold cids ({len(gold_cids)}): {gold_cids}")
clean = build_clean_direct(rows)
print(f"CZYSTE bezpośrednie nogi: {len(clean)}/{len(rows)} ({100*len(clean)/max(1,len(rows)):.0f}%)")

o1, d1 = window(rows, "2026-07-04", "2026-07-31", clean)
report("PRIMARY 04.07-17.07 (era gps5b, clean-direct)", o1, d1)
o2, d2 = window(rows, "2026-06-14", "2026-07-03", clean)
report("SANITY 14.06-03.07 (clean-direct)", o2, d2)

# ── MAE ETA dostawy (drive+dwell) na PRIMARY: mult=1.0 (live) vs tabela 26.06 vs ZMIERZONA ──
MEASURED = {t: st.median(rs) for t, rs in o1.items() if len(rs) >= 8}
TABELA_26_06 = {'gold': 0.78, 'std+': 0.82, 'std': 0.82, 'slow': 1.0, 'new': 1.0}
VARIANTS = {"live(1.0)": lambda t: 1.0,
            "tabela 26.06": lambda t: TABELA_26_06.get(t, 1.0),
            "tabela common": lambda t: C.DRIVE_SPEED_MULT_BY_TIER.get(t, 1.0),
            "ZMIERZONA": lambda t: MEASURED.get(t, 1.0)}
errs = {v: {} for v in VARIANTS}
for oid, cid, day, tsp, tsd, ff, actual in rows:
    if not ("2026-07-04" <= day <= "2026-07-31") or oid not in clean:
        continue
    tier = tier_of.get(str(cid))
    dt = parse_ts(tsp)
    if not tier or dt is None or not ff or ff <= 0.5 or not actual or actual <= 1.5:
        continue
    base = ff * C.get_traffic_multiplier(dt.astimezone(timezone.utc))
    dwell_do = C.dwell_for_tier(tier)[1]
    for vname, vfn in VARIANTS.items():
        pred = base * vfn(tier) + dwell_do
        errs[vname].setdefault(tier, []).append(abs(pred - actual))
print("\n== MAE ETA dostawy [min] na PRIMARY (clean-direct; im mniej tym lepiej) ==")
print(f"{'tier':6}" + "".join(f" {v:>13}" for v in VARIANTS))
for tier in ("gold", "std+", "std", "new"):
    line = f"{tier:6}"
    for vname in VARIANTS:
        es = errs[vname].get(tier, [])
        line += f" {st.mean(es):13.2f}" if es else f" {'—':>13}"
    print(line)
tot = {v: [e for t in errs[v] for e in errs[v][t]] for v in VARIANTS}
print(f"{'TOTAL':6}" + "".join(f" {st.mean(tot[v]):13.2f}" for v in VARIANTS)
      + f"   (n={len(tot['live(1.0)'])})")
print("\nZMIERZONA tabela (mediany PRIMARY):",
      {t: round(v, 3) for t, v in sorted(MEASURED.items())})
