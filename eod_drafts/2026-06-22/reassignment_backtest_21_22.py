#!/usr/bin/env python3
"""Backtest 21-22.06: co forward-shadow przerzutów by zmieniło / czy coś by uratowało.

NIE rekonstruuje floty (lossy — shift_end/tiery z przeszłości nieodtwarzalne wiernie).
Zamiast tego używa TEGO, CO ZIOMEK REALNIE ZAPISAŁ w shadow_decisions.jsonl per zlecenie
(best + alternatives = pełny ranking kandydatów z prawdziwego scoringu w momencie decyzji)
i krzyżuje z 43 realnymi przerzutami człowieka (audit_log COURIER_ASSIGNED.previous_cid).

Pytania:
 1) „co by zmieniło" — dla każdego przerzutu A→B: gdzie w rankingu Ziomka był B vs A?
    best==B → Ziomek już chciał B (przerzut = realignment do Ziomka).
    best==A → Ziomek chciał zostawić u A (człowiek ruszył wbrew rankingowi — pewnie roster/idle).
 2) „co by uratowało" — w ilu Ziomek JUŻ stawiał B wyżej niż A w chwili przyjęcia zlecenia,
    i ile minut PÓŹNIEJ człowiek to wykonał (= okno, w którym live-shadow co 3 min mógł
    sflagować wcześniej). + ile przerzutów miało odpalony redirect-signal Ziomka.
OGRANICZENIE: ranking z chwili PRZYJĘCIA, nie z chwili przerzutu — flota mogła się zmienić
(B zwolnił się później). To NIEDOSZACOWUJE tego, co live-forward-shadow łapie (re-ocena co 3 min).
"""
import json, sqlite3
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import statistics as st

WAR = timezone(timedelta(hours=2))
DB = "/root/.openclaw/workspace/dispatch_state/events.db"
SD = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DAYS = ("2026-06-21", "2026-06-22")


def _cid(c): return str((c or {}).get("courier_id") or (c or {}).get("cid") or "")
def _score(c):
    try: return float((c or {}).get("score"))
    except (TypeError, ValueError): return None


# 1) przerzuty człowieka (final per oid)
con = sqlite3.connect(DB); cur = con.cursor()
rows = cur.execute("SELECT order_id,courier_id,created_at,payload FROM audit_log "
                   "WHERE event_type='COURIER_ASSIGNED' ORDER BY created_at").fetchall()
con.close()
reassigns = {}  # oid -> (A, B, T_reassign)
for oid, cid, created, pl in rows:
    pl = json.loads(pl) if pl else {}
    prev = pl.get("previous_cid")
    if prev and str(prev) not in ("None", str(cid), ""):
        try: d = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(WAR)
        except (ValueError, AttributeError): continue
        if d.strftime("%Y-%m-%d") in DAYS:
            reassigns[str(oid)] = (str(prev), str(cid), d)  # ostatni przerzut wygrywa

# 2) shadow_decisions index (per oid: najświeższa decyzja + arrival ts)
dec = {}
arrival = {}
with open(SD) as f:
    for line in f:
        try: r = json.loads(line)
        except ValueError: continue
        oid = str(r.get("order_id") or "")
        if oid in reassigns:
            dec[oid] = r  # ostatnia w pliku = najświeższa
            ats = r.get("order_created_at") or r.get("ts")
            if ats: arrival[oid] = ats

# 3) analiza
agree_b = keep_a = other = nodata = 0
redirect = 0
b_above_a = 0
gaps = []          # minuty arrival->reassign dla przypadków B>A u Ziomka
deltas = []        # score B - score A gdy oba znane
sample = []
struct_dumped = False
for oid, (A, B, T) in reassigns.items():
    d = dec.get(oid)
    if not d:
        nodata += 1; continue
    best = _cid(d.get("best"))
    alts = {}
    for c in (d.get("alternatives") or []):
        alts[_cid(c)] = _score(c)
    if best:
        alts[best] = _score(d.get("best"))
    if not struct_dumped and not alts:
        print("  [debug] decision keys:", sorted(d.keys()))
        print("  [debug] best:", json.dumps(d.get("best"))[:200])
        struct_dumped = True
    sa, sb = alts.get(A), alts.get(B)
    redir = any(d.get(k) for k in ("commit_divergence_redirect", "pickup_extension_redirect",
                                    "difficult_case_redirect"))
    if redir: redirect += 1
    if best == B: agree_b += 1
    elif best == A: keep_a += 1
    else: other += 1
    if sa is not None and sb is not None:
        deltas.append(sb - sa)
        if sb > sa:
            b_above_a += 1
            at = arrival.get(oid)
            if at:
                try:
                    a_dt = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(WAR)
                    gaps.append((T - a_dt).total_seconds() / 60.0)
                except (ValueError, AttributeError): pass
    sample.append((oid, A, B, best, None if sa is None else round(sa, 1),
                   None if sb is None else round(sb, 1), redir))

n = len(reassigns)
print("=" * 70)
print(f"BACKTEST przerzutów 21-22.06 — {n} przerzutów człowieka (audit_log)")
print("=" * 70)
print(f"  z zapisaną decyzją Ziomka (shadow_decisions): {n - nodata} | bez: {nodata}")
print(f"\n  CO BY ZMIENIŁO — ranking Ziomka w chwili przyjęcia:")
print(f"   • best == B (Ziomek już wskazywał biorcę → przerzut = realignment): {agree_b}")
print(f"   • best == A (Ziomek chciał zostawić u dawcy → człowiek ruszył wbrew): {keep_a}")
print(f"   • best == ktoś inny C (Ziomek wskazałby JESZCZE inny przerzut): {other}")
if deltas:
    print(f"   • B oceniony wyżej niż A u Ziomka: {b_above_a}/{len(deltas)} "
          f"(mediana Δscore B−A: {st.median(deltas):+.1f})")
print(f"\n  CO BY URATOWAŁO:")
print(f"   • przerzutów z odpalonym redirect-signal Ziomka (commit_div/pickup_ext/difficult): {redirect}")
if gaps:
    print(f"   • gdy Ziomek JUŻ stawiał B>A: człowiek wykonał przerzut medianę "
          f"{st.median(gaps):.0f} min PO przyjęciu (n={len(gaps)}) — okno, w którym live-shadow "
          f"co 3 min sflagowałby wcześniej")
print(f"\n  ⚠ OGRANICZENIE: ranking z chwili PRZYJĘCIA, nie przerzutu — NIEDOSZACOWUJE tego, co"
      f" live-forward-shadow (re-ocena co 3 min na zmienionej flocie) realnie łapie.")
print(f"\n  Próbka (oid, A→B, best_ziomka, score_A, score_B, redirect):")
for s in sample[:14]:
    print("   ", s)
