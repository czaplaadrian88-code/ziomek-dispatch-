"""Odczyt pod wątpliwości Adriana do O2-K1 (06.07): koszt capa Z=20, objazd/km, przeploty.

Polityka jak w review/silniku: improved = overage_gain >= 2 AND detour <= 8 (X).
Dedup po bag_sig (pierwszy wpis wygrywa — obietnica z propozycji).
"""
import json
import sys
from statistics import median

CORPUS = "/root/.openclaw/workspace/dispatch_state/bundle_calib_shadow_l0.jsonl"
GAIN_MIN = 2.0
DETOUR_MAX = 8.0
KM_PER_MIN = 25.0 / 60.0  # zał.: 25 km/h miejskie

def interleave(seq):
    """Liczba przejść dropoff->pickup (wracanie po kolejne = proxy zygzaka)."""
    n = 0
    for a, b in zip(seq, seq[1:]):
        if a[0] == "dropoff" and b[0] == "pickup":
            n += 1
    return n

seen = set()
rows = []
for line in open(CORPUS):
    try:
        d = json.loads(line)
    except Exception:
        continue
    if (d.get("n_orders") or 0) < 2:
        continue
    sig = d.get("bag_sig")
    if sig in seen:
        continue
    seen.add(sig)
    rows.append(d)

def policy(d, z):
    uz = (d.get("under_z") or {}).get(str(z))
    ms = d.get("m_served") or {}
    if not uz or not isinstance(ms.get("overage"), (int, float)):
        return None
    gain = ms["overage"] - uz.get("overage", ms["overage"])
    detour = uz.get("drive_min", 0) - ms.get("drive_min", 0)
    ok = gain >= GAIN_MIN and detour <= DETOUR_MAX
    return {"ok": ok, "gain": gain, "detour": detour, "uz": uz, "ms": ms, "d": d}

applied, det = [], []
z20_blocked_but_z35_ok = 0
il_up = il_down = il_same = 0
for d in rows:
    p20 = policy(d, 20)
    if p20 and p20["ok"]:
        applied.append(p20)
        det.append(p20["detour"])
        i_old = interleave(d.get("served_seq") or [])
        i_new = interleave(p20["uz"].get("seq") or [])
        if i_new > i_old: il_up += 1
        elif i_new < i_old: il_down += 1
        else: il_same += 1
    else:
        p35 = policy(d, 35)
        if p35 and p35["ok"]:
            z20_blocked_but_z35_ok += 1

n = len(rows)
days = (max(r["ts"] for r in rows) or "")[:10], (min(r["ts"] for r in rows) or "")[:10]
from datetime import datetime
span_d = max((datetime.fromisoformat(max(r["ts"] for r in rows))
              - datetime.fromisoformat(min(r["ts"] for r in rows))).total_seconds() / 86400, 0.1)

sd = sorted(det)
q = lambda p: sd[int(p * len(sd))] if sd else 0
print(f"worki multi (uniq): {n}; zastosowane przestawienia (Z=20, X=8, gain>=2): {len(applied)} ({100*len(applied)/n:.1f}%)")
print(f"zysk świeżości: med {median([a['gain'] for a in applied]):+.1f} min")
print(f"OBJAZD zastosowanych: med {median(det):+.2f} min | p75 {q(.75):+.2f} | p90 {q(.90):+.2f} | max {max(det):+.2f} | średnia {sum(det)/len(det):+.2f}")
neg = sum(1 for x in det if x < 0)
print(f"  trasy KRÓTSZE po przestawieniu: {neg}/{len(det)} ({100*neg/len(det):.0f}%)")
print(f"  bilans floty: {sum(det):+.1f} min jazdy na {span_d:.1f} dnia = {sum(det)/span_d:+.1f} min/dzień ≈ {KM_PER_MIN*sum(det)/span_d:+.1f} km/dzień (przy 25 km/h)")
print(f"KOSZT CAPA Z=20: poprawek zablokowanych przez Z=20 (a przeszłyby przy Z=35): {z20_blocked_but_z35_ok} ({100*z20_blocked_but_z35_ok/n:.1f}% worków)")
print(f"PRZEPLOTY (dropoff→pickup, proxy zygzaka) w zastosowanych: WIĘCEJ {il_up} / MNIEJ {il_down} / BEZ ZMIAN {il_same}")
