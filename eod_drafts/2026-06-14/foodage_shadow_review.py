#!/usr/bin/env python3
"""Przegląd forward shadow comparatora food-age (BUG#5) — zaplanowany na 2026-06-21.

Analizuje wpisy best.food_age_shadow w shadow_decisions.jsonl od 2026-06-14:
  - % changed=True wśród ortools-multistop z polem
  - rozkład delty thermal (off vs on) i idle (off vs on)
  - KRYTYCZNIE: czy gdziekolwiek on_sla_viol > off_sla_viol (regresja R6/SLA = blocker)
Rule-based rekomendacja (flip / dłużej shadow / tuning coeff) — NIE flipuje flag,
tylko raport do pliku + Telegram do ACK Adriana.
"""
import json
import statistics as st
import sys
from datetime import datetime, timezone

SCRIPTS = "/root/.openclaw/workspace/scripts"
LOG = f"{SCRIPTS}/logs/shadow_decisions.jsonl"
REPORT = f"{SCRIPTS}/logs/foodage_shadow_review_2026-06-21.txt"
SINCE = "2026-06-14"
sys.path.insert(0, SCRIPTS)


def _pctile(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2)


def main():
    rows = []
    with open(LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass

    fa = []
    for r in rows:
        if (r.get("ts") or "") < SINCE:
            continue
        b = r.get("best")
        if isinstance(b, dict) and isinstance(b.get("food_age_shadow"), dict):
            d = dict(b["food_age_shadow"])
            d["_oid"] = r.get("order_id")
            d["_name"] = b.get("name")
            d["_ts"] = r.get("ts")
            fa.append(d)

    n = len(fa)
    changed = [d for d in fa if d.get("changed")]
    # regresja SLA: on_sla_viol > off_sla_viol gdziekolwiek
    sla_reg = [d for d in fa if (d.get("on_sla_viol") or 0) > (d.get("off_sla_viol") or 0)]
    # delty (tylko gdzie obie wartości są liczbą)
    th_delta = [round((d["off_thermal_max"] - d["on_thermal_max"]), 2)
                for d in fa
                if isinstance(d.get("off_thermal_max"), (int, float))
                and isinstance(d.get("on_thermal_max"), (int, float))]
    idle_delta = [round((d["off_idle"] - d["on_idle"]), 2)
                  for d in fa
                  if isinstance(d.get("off_idle"), (int, float))
                  and isinstance(d.get("on_idle"), (int, float))]
    # delty TYLKO na zmienionych trasach (tam gdzie food-age realnie przemeblował)
    th_delta_chg = [round((d["off_thermal_max"] - d["on_thermal_max"]), 2)
                    for d in changed
                    if isinstance(d.get("off_thermal_max"), (int, float))
                    and isinstance(d.get("on_thermal_max"), (int, float))]

    changed_rate = (100.0 * len(changed) / n) if n else 0.0
    th_mean_chg = round(st.mean(th_delta_chg), 2) if th_delta_chg else None

    # ── rule-based rekomendacja ──
    if n < 30:
        rec = (f"⏳ ZA MAŁO DANYCH (n={n} < 30). Zostaw shadow dłużej, "
               f"ponów przegląd za kolejne ~7 dni.")
    elif sla_reg:
        rec = (f"🛑 NIE FLIPOWAĆ. Regresja SLA na {len(sla_reg)} decyzjach "
               f"(on_sla_viol > off). Zbadać te przypadki PRZED jakimkolwiek flipem "
               f"(food-age zdejmuje SLA-grace+stromość R6).")
    elif changed_rate < 5:
        rec = (f"🔧 NISKI ZASIĘG (changed={changed_rate:.1f}% < 5%). Rozważyć "
               f"podkręcenie OBJ_DELIVERY_FOOD_AGE_COEFF (obecnie 6.0) i ponowny "
               f"tydzień shadow, albo uznać bug za rzadki/akceptowalny.")
    elif th_mean_chg is not None and th_mean_chg > 0:
        rec = (f"✅ KANDYDAT DO FLIPA (do ACK Adriana). changed={changed_rate:.1f}%, "
               f"zero regresji SLA, średnia poprawa thermal na zmienionych "
               f"trasach +{th_mean_chg} min. Sugestia: flip ENABLE_OBJ_DELIVERY_FOOD_AGE "
               f"(hot-reload) + obserwacja produkcyjna 48h.")
    else:
        rec = (f"🟡 NIEJEDNOZNACZNE. changed={changed_rate:.1f}%, brak regresji SLA, "
               f"ale poprawa thermal nieoczywista (mean_chg={th_mean_chg}). "
               f"Przejrzeć ręcznie zmienione trasy przed decyzją.")

    lines = [
        "=== PRZEGLĄD FOOD-AGE SHADOW (BUG#5) — 2026-06-21 ===",
        f"Źródło: {LOG}  (wpisy z food_age_shadow od {SINCE})",
        f"Decyzji ortools-multistop z polem: n={n}",
        f"  changed=True: {len(changed)} ({changed_rate:.1f}%)",
        f"  regresja SLA (on>off): {len(sla_reg)}  <-- 0 = OK, >0 = BLOCKER",
        "",
        "Delta thermal (off-on, dodatnie = food-age świeższe), WSZYSTKIE:",
        f"  n={len(th_delta)} median={_pctile(th_delta,0.5)} p90={_pctile(th_delta,0.9)} "
        f"max={max(th_delta) if th_delta else None} min={min(th_delta) if th_delta else None}",
        "Delta thermal na ZMIENIONYCH trasach (realny efekt):",
        f"  n={len(th_delta_chg)} mean={th_mean_chg} median={_pctile(th_delta_chg,0.5)} "
        f"max={max(th_delta_chg) if th_delta_chg else None} min={min(th_delta_chg) if th_delta_chg else None}",
        "Delta idle (off-on, dodatnie = mniej postoju jałowego), WSZYSTKIE:",
        f"  n={len(idle_delta)} median={_pctile(idle_delta,0.5)} p90={_pctile(idle_delta,0.9)} "
        f"max={max(idle_delta) if idle_delta else None}",
        "",
        "REKOMENDACJA (do ACK Adriana — skrypt NIE flipuje flag):",
        f"  {rec}",
    ]
    if sla_reg:
        lines.append("")
        lines.append("Przypadki regresji SLA (do zbadania):")
        for d in sla_reg[:10]:
            lines.append(f"  #{d['_oid']} {str(d.get('_name'))[:16]} "
                         f"off_sla={d.get('off_sla_viol')} on_sla={d.get('on_sla_viol')} "
                         f"order_changed={d.get('changed')}")
    report = "\n".join(lines)

    with open(REPORT, "w") as f:
        f.write(report + "\n")
    print(report)

    # Telegram (best-effort — nigdy nie wywala raportu plikowego)
    try:
        from dispatch_v2 import telegram_utils
        tg = (f"📊 *Przegląd food-age shadow (BUG#5)* — {datetime.now(timezone.utc).date()}\n"
              f"n={n}, changed={changed_rate:.1f}%, regresja SLA={len(sla_reg)}\n"
              f"{rec}\nPełny raport: {REPORT}")
        telegram_utils.send_admin_alert(tg)
    except Exception as e:
        print(f"[telegram skip] {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
