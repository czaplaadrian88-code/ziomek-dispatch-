#!/usr/bin/env python3
"""freshness_shadow_monitor — SHADOW (read-only) pomiar lewara świeżości #3.

Adrian 2026-06-22: solver (route_simulator OR-tools) minimalizuje total_min
(makespan) + span — NIE ma członu kary za wożenie świeżego jedzenia. Skutek:
zlecenie którego zrzut jest TANI (blisko odbioru / adjacent) bywa odraczane za
inne stopy → jedzie 20-30 min zamiast 5. Wzorzec „Bar Merino".

Ten monitor (read-only, ZERO wpływu na decyzje — jak prep_bias/loadgov shadow)
mierzy DZIENNIE ile świeżości dałoby się odzyskać członem kosztu
    cost += LAMBDA * max(0, thermal_age_i − direct_i)
przy λ=0.1 (sweet spot z λ-sweep 22.06: łapie czyste wygrane, 0 zagrożeń
committed). Akumuluje dowód multi-day ZANIM cokolwiek tknie OR-tools.

Wzorzec wykrycia (per dostarczone zlecenie):
  carry = delivered − picked ; direct = OSRM(pickup→drop)
  pattern gdy direct < PATTERN_DIRECT_MAX i (carry−direct−1) > PATTERN_EXCESS_MIN
Ruch early-drop (lokalny): wstaw zrzut tuż za odbiór w faktycznej sekwencji
kuriera; detour = koszt wstawienia − oszczędność usunięcia (OSRM). „Czysta
wygrana" = detour ≤ DETOUR_OK i 0 nowych naruszeń committed (frozen R27±5).

Uruchom:  python3 -m dispatch_v2.tools.freshness_shadow_monitor [--day YYYY-MM-DD]
Wynik: linia podsumowania (do logu/Telegrama). Read-only, stdlib + urllib OSRM.
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")  # DST-safe CET/CEST — L2 audyt 2.0 (był fixed +2)
ORDERS_STATE = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
OSRM_URL = "http://localhost:5001/route/v1/driving/{},{};{},{}?overview=false"

LAMBDA = 0.1                # sweet spot (λ-sweep 22.06)
PATTERN_DIRECT_MAX = 6.0    # zrzut „blisko" odbioru (min jazdy)
PATTERN_EXCESS_MIN = 15.0   # nadmiar wożenia ponad direct (min)
DETOUR_OK = 3.0             # detour „tani" (min)
COMMITTED_WINDOW_S = 300    # R27 ±5 min

_oc = {}


def _osrm(a, b):
    if a is None or b is None:
        return None
    k = (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))
    if k in _oc:
        return _oc[k]
    try:
        u = OSRM_URL.format(a[1], a[0], b[1], b[0])
        v = json.load(urllib.request.urlopen(u, timeout=5))["routes"][0]["duration"] / 60.0
    except Exception:
        v = None
    _oc[k] = v
    return v


def _pt(s):
    try:
        d = datetime.fromisoformat(str(s))
        return d if d.tzinfo else d.replace(tzinfo=WARSAW)
    except Exception:
        return None


def _coord(e, kind):
    c = e.get("pickup_coords") if kind == "P" else e.get("delivery_coords")
    return (c[0], c[1]) if c and len(c) == 2 else None


def analyze_day(orders, day):
    by_cid = defaultdict(list)
    for oid, e in orders.items():
        if (e or {}).get("status") != "delivered":
            continue
        dl = _pt(e.get("delivered_at"))
        if not dl or dl.astimezone(WARSAW).strftime("%Y-%m-%d") != day:
            continue
        pu = _pt(e.get("picked_up_at"))
        cid = str(e.get("courier_id") or "")
        if cid and pu:
            by_cid[cid].append((oid, e, pu, dl))

    items = []
    for cid, lst in by_cid.items():
        ev = []
        for oid, e, pu, dl in lst:
            pc, dc = _coord(e, "P"), _coord(e, "D")
            if pc:
                ev.append((pu.timestamp(), "P", oid, pc))
            if dc:
                ev.append((dl.timestamp(), "D", oid, dc))
        ev.sort()
        seq = [(k, b, c) for _, k, b, c in ev]
        ts = [t for t, _, _, _ in ev]
        for oid, e, pu, dl in lst:
            pc, dc = _coord(e, "P"), _coord(e, "D")
            carry = (dl - pu).total_seconds() / 60.0
            direct = _osrm(pc, dc)
            if direct is None or not (0 <= carry <= 120):
                continue
            if not (direct < PATTERN_DIRECT_MAX and carry - direct - 1 > PATTERN_EXCESS_MIN):
                continue
            try:
                pi = next(i for i, (k, b, _) in enumerate(seq) if k == "P" and b == oid)
                di = next(i for i, (k, b, _) in enumerate(seq) if k == "D" and b == oid)
            except StopIteration:
                continue
            if di <= pi + 1:
                continue
            xc = seq[di][2]
            nxt = seq[pi + 1][2]
            ins = ((_osrm(seq[pi][2], xc) or 0) + (_osrm(xc, nxt) or 0)
                   - (_osrm(seq[pi][2], nxt) or 0))
            prevd = seq[di - 1][2]
            nextd = seq[di + 1][2] if di + 1 < len(seq) else None
            rem = ((_osrm(prevd, xc) or 0)
                   + ((_osrm(xc, nextd) or 0) if nextd else 0)
                   - ((_osrm(prevd, nextd) or 0) if nextd else 0))
            detour = ins - rem
            fresh = carry - direct - 1
            viol = 0
            for j in range(pi + 1, di):
                if seq[j][0] != "P":
                    continue
                cke = _pt((orders.get(seq[j][1]) or {}).get("czas_kuriera_warsaw"))
                if cke is None:
                    continue
                cke = cke.timestamp()
                if ts[j] <= cke + COMMITTED_WINDOW_S and ts[j] + ins * 60 > cke + COMMITTED_WINDOW_S:
                    viol += 1
            items.append({"fresh": fresh, "detour": detour, "viol": viol})
    return items


def main():
    day = None
    if "--day" in sys.argv:
        day = sys.argv[sys.argv.index("--day") + 1]
    if day is None:
        day = datetime.now(WARSAW).strftime("%Y-%m-%d")
    _d = json.load(open(ORDERS_STATE))
    orders = _d.get("orders", _d)
    items = analyze_day(orders, day)
    n = len(items)
    # ruch wykonany gdy λ*fresh > detour i 0 naruszeń committed (twardy constraint)
    trig = [it for it in items if it["viol"] == 0 and (it["detour"] <= 0 or LAMBDA * it["fresh"] > it["detour"])]
    fresh_sum = round(sum(it["fresh"] for it in trig))
    detour_sum = round(sum(max(0, it["detour"]) for it in trig))
    blocked = sum(1 for it in items if it["viol"] > 0)
    print(f"[freshness-shadow {day}] λ={LAMBDA} | wzorzec={n} | early-drop={len(trig)} "
          f"| świeżość_odzysk={fresh_sum}min | detour={detour_sum}min "
          f"| zablokowane_committed={blocked} | ZERO wpływu na decyzje (shadow)")


if __name__ == "__main__":
    main()
