#!/usr/bin/env python3
"""#5 walidacja FIZYCZNA pickup-debias: czy przyjazd pod restaurację (GPS, restaurant_dwell
arrived_at_restaurant) ≈ predykcja odbioru (+4,5 debias)? Join shadow_decisions.best
(target_pickup_at raw + target_pickup_debiased) ↔ restaurant_dwell (fizyczny przyjazd).
Jeśli median(physical − raw) ≈ +4,5 i median(physical − debiased) ≈ 0 → debias fizycznie
potwierdzony (raw systematycznie optymistyczny, +4,5 to naprawia). READ-ONLY."""
import json
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

WAW = ZoneInfo("Europe/Warsaw")
SD = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
RD = "/root/.openclaw/workspace/dispatch_state/restaurant_dwell.json"


def ep(ts):
    if not ts:
        return None
    s = str(ts).strip()
    try:
        if "+" in s or s.endswith("Z") or (("T" in s) and s[-6] in "+-"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        return datetime.strptime(s.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=WAW).timestamp()
    except Exception:
        return None


rd = json.load(open(RD))
phys = {oid: ep(v.get("arrived_at_restaurant"))
        for oid, v in rd.items() if isinstance(v, dict) and v.get("arrived_at_restaurant")}

raw_bias = []      # physical − raw_predicted (min); + = kurier dojechał PÓŹNIEJ (optymizm predykcji)
deb_bias = []      # physical − debiased (min); ≈0 = debias trafia
matched = 0
for line in open(SD):
    try:
        r = json.loads(line)
    except Exception:
        continue
    b = r.get("best")
    if not isinstance(b, dict):
        continue
    oid = str(r.get("order_id"))
    ph = phys.get(oid)
    if ph is None:
        continue
    raw = ep(b.get("target_pickup_at"))
    deb = ep(b.get("target_pickup_debiased"))
    if raw is None:
        continue
    matched += 1
    raw_bias.append((ph - raw) / 60.0)
    if deb is not None:
        deb_bias.append((ph - deb) / 60.0)


def stats(xs):
    xs = sorted(xs)
    if not xs:
        return "brak"
    n = len(xs)
    return (f"n={n} med={statistics.median(xs):+.2f} mean={statistics.mean(xs):+.2f} "
            f"p25={xs[n//4]:+.1f} p75={xs[3*n//4]:+.1f}")


print("=== #5 FIZYCZNA walidacja pickup-debias (restaurant_dwell arrived_at_restaurant) ===")
print(f"join shadow_decisions ↔ restaurant_dwell: {matched} zleceń")
print(f"bias RAW (physical − predykcja) [+ = kurier dojeżdża PÓŹNIEJ = predykcja optymistyczna]:")
print(f"  {stats(raw_bias)}")
print(f"bias DEBIASED (physical − (predykcja+4,5)) [≈0 = debias trafia]:")
print(f"  {stats(deb_bias)}")
if raw_bias:
    med_raw = statistics.median(raw_bias)
    med_deb = statistics.median(deb_bias) if deb_bias else None
    print(f"\nWNIOSEK: predykcja odbioru optymistyczna o medianę {med_raw:+.1f} min "
          f"(debias 4,5 {'TRAFIA' if med_deb is not None and abs(med_deb)<1.5 else 'rozjazd'}: "
          f"reszta po debias = {med_deb:+.1f} min)." if med_deb is not None else "")
    late_raw = sum(1 for x in raw_bias if x > 5)
    late_deb = sum(1 for x in deb_bias if x > 5) if deb_bias else 0
    if deb_bias:
        print(f"odbiory >5min po obietnicy: RAW {late_raw}/{len(raw_bias)} ({100*late_raw/len(raw_bias):.0f}%) "
              f"→ DEBIASED {late_deb}/{len(deb_bias)} ({100*late_deb/len(deb_bias):.0f}%)")
