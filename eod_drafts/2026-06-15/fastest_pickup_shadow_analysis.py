#!/usr/bin/env python3
"""POST-PEAK analiza shadow „najszybszy odbiór" (Adrian 2026-06-15).

Po dinner peaku (17-21 Warsaw): czy selekcja fastest-pickup realnie wybiera
wcześniejszy ODBIÓR vs live, ile, czy NIE wskakuje kurier blind z fikcyjnym ETA,
i czy shadow-pick realnie był wolny wcześniej (join orders_state).

Uruchom po ~21 Warsaw: python3 fastest_pickup_shadow_analysis.py [YYYY-MM-DD]
Read-only.
"""
import json, sys
from datetime import datetime, timezone, timedelta

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
ST = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
WAW = timezone(timedelta(hours=2))
DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-06-15"
BLIND = {"no_gps", "pre_shift", "none", None}  # pozycje fikcyjne/niepewne (BIALYSTOK_CENTER)

st = json.load(open(ST, encoding="utf-8"))
orders = st.get("orders", st)


def U(s):
    if not s:
        return None
    s = str(s).strip()
    try:
        if "T" in s and ("+" in s or s.endswith("Z")):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        d = datetime.fromisoformat(s)
        return (d.replace(tzinfo=WAW) if d.tzinfo is None else d).astimezone(timezone.utc)
    except Exception:
        return None


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


rows = []
for line in open(LOG, encoding="utf-8", errors="ignore"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    b = r.get("best") or {}
    sh = b.get("best_effort_fastest_pickup_shadow")
    if not sh:
        continue
    ts = str(r.get("ts") or "")
    if not ts.startswith(DAY):
        continue
    # pos_source shadow-picka — z metryki wprost (pewne; candidates ucięte do top-N)
    sh_cid = sh.get("shadow_cid")
    sh_pos = sh.get("shadow_pos_source", "?")
    rows.append({
        "oid": str(r.get("order_id")), "ts": ts,
        "live_cid": sh.get("live_cid"), "shadow_cid": sh_cid,
        "differ": sh.get("would_differ"), "earlier": sh.get("shadow_pickup_earlier_min"),
        "shadow_pos": sh_pos,
    })

n = len(rows)
differ = [r for r in rows if r["differ"]]
earl = [r["earlier"] for r in differ if isinstance(r["earlier"], (int, float))]
blind_picks = [r for r in differ if r["shadow_pos"] in BLIND]
print(f"=== SHADOW fastest-pickup — {DAY} (n={n} best_effort z metryką) ===")
print(f"  divergencje (shadow != live): {len(differ)} ({100*len(differ)/max(n,1):.0f}%)")
if earl:
    print(f"  wcześniejszy ODBIÓR shadow [min]: med={med(earl):.1f} min={min(earl):.1f} max={max(earl):.1f}")
    print(f"    <0 (shadow PÓŹNIEJ — źle!): {sum(1 for e in earl if e < 0)}")
print(f"  ⚠ KONTROLA BLIND: shadow-pick z fikcyjną pozycją (no_gps/pre_shift): {len(blind_picks)}/{len(differ)}")
for r in blind_picks[:8]:
    print(f"      oid={r['oid']} shadow_cid={r['shadow_cid']} pos={r['shadow_pos']} earlier={r['earlier']}")

# JOIN realny: czy shadow_cid realnie odebrał WCZEŚNIEJ niż live_cid (jeśli oba mają realny odbiór)
print(f"\n  === walidacja realna (orders_state) ===")
val_ok = val_bad = val_na = 0
for r in differ:
    o = orders.get(r["oid"], {})
    real_cid = o.get("courier_id")
    real_pu = U(o.get("picked_up_at"))
    # czy realnie obsłużył shadow_cid czy live_cid?
    if real_cid is not None and str(real_cid) == str(r["shadow_cid"]):
        val_ok += 1   # człowiek też wybrał kogo shadow proponuje
    elif real_cid is not None and str(real_cid) == str(r["live_cid"]):
        val_bad += 1  # człowiek wybrał live (shadow się mylił?)
    else:
        val_na += 1
print(f"  realnie obsłużył: shadow-pick={val_ok} | live-pick={val_bad} | inny/brak={val_na}")
print(f"\nWERDYKT do flipu: differ% sensowne + earlier>0 dominuje + blind-picks≈0 → flip live OK.")
print(f"Jeśli blind-picks duże → najpierw twardszy bucket-guard (blind nie wygrywa mimo wcześniejszego ETA).")
