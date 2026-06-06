#!/usr/bin/env python3
"""Live-check fixu front-load Ziomka (commit 6715d6f, plan-regen-near-pickup).

Analizuje plan_recheck.log za zadany dzien: regeny near-pickup (lacznie +
rozklad godzinowy UTC), rozklad sla z BAG_PLAN_GENERATED (naruszenia R6),
rozmiary workow (stops=), bledy/tracebacki, churn (auto_invalidated /
pickup_refloored / liczba roznych kurierow), max active_plans (wolumen).
Porownuje z sobota 06.06 (baseline: 40 regen, 51/52 sla=0, 0 bledow, 1-2 akt.).
Werdykt PO POLSKU -> Telegram Adriana (telegram_utils.send_admin_alert).

at-job jednorazowy 2026-06-07 19:00 UTC (= 21:00 Warsaw). Uzycie:
  python frontload_sunday_check.py --day 2026-06-07 --notify
Bez --notify: tylko druk na stdout (walidacja, np. na danych soboty).
"""
import argparse
import re
import sys
from collections import Counter

ROOT = "/root/.openclaw/workspace/scripts"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOG = "/root/.openclaw/workspace/scripts/logs/plan_recheck.log"
SHADOW_LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"

# baseline soboty 06.06 (z recznej weryfikacji) do porownania
SAT_REGEN = 40
SAT_SLA_OK = 51
SAT_SLA_TOTAL = 52


def _send(text: str) -> bool:
    try:
        from dispatch_v2 import telegram_utils
        return telegram_utils.send_admin_alert(text)
    except Exception as e:  # noqa: BLE001 - fail-soft, raport i tak jest na stdout
        print(f"[WARN] send_admin_alert fail: {type(e).__name__}: {e}")
        return False


def prep_variance_summary(day: str) -> dict:
    """FAIL-04 shadow: zlicz prep_variance_anomaly z shadow_decisions.jsonl za dzien.

    Zwraca {total, anomalies, by_rest (Counter), gaps (dict), available (bool)}.
    available=False gdy brak pliku/0 decyzji (np. flaga byla OFF).
    """
    import json as _json
    total = 0
    anomalies = 0
    by_rest = Counter()
    gaps = {}
    try:
        with open(SHADOW_LOG, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if day not in ln:  # tani pre-filtr (ts zawiera dzien)
                    continue
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = _json.loads(ln)
                except Exception:
                    continue
                if not str(d.get("ts", "")).startswith(day):
                    continue
                if "prep_variance_anomaly" not in d:
                    continue  # decyzja sprzed deployu FAIL-04 (detektor nie biegl)
                total += 1
                pva = d.get("prep_variance_anomaly")
                if pva:
                    anomalies += 1
                    r = pva.get("restaurant", "?")
                    by_rest[r] += 1
                    gaps[r] = pva.get("gap_min")
    except FileNotFoundError:
        return {"available": False, "total": 0, "anomalies": 0,
                "by_rest": by_rest, "gaps": gaps}
    return {"available": total > 0, "total": total, "anomalies": anomalies,
            "by_rest": by_rest, "gaps": gaps}


def analyze(day: str) -> str:
    try:
        with open(LOG, encoding="utf-8", errors="replace") as fh:
            lines = [ln for ln in fh if ln.startswith(day)]
    except FileNotFoundError:
        return f"⚠ Front-load {day}: brak pliku {LOG}"

    if not lines:
        return (f"⚠ Front-load {day}: 0 wpisow w plan_recheck.log za ten dzien "
                f"(plan-recheck nie biegl? sprawdz timer).")

    regen = [ln for ln in lines if "BAG_PLAN_NEAR_PICKUP_REGEN" in ln]
    gen = [ln for ln in lines if "BAG_PLAN_GENERATED" in ln]
    summaries = [ln for ln in lines if "PLAN_RECHECK summary=" in ln]

    # rozklad godzinowy regenow (UTC)
    hour_re = re.compile(re.escape(day) + r" (\d{2}):")
    hours = Counter()
    for ln in regen:
        m = hour_re.search(ln)
        if m:
            hours[m.group(1)] += 1
    lunch = sum(v for h, v in hours.items() if "09" <= h <= "12")   # 11-14 Warsaw
    dinner = sum(v for h, v in hours.items() if "15" <= h <= "18")  # 17-20 Warsaw

    # sla + stops z BAG_PLAN_GENERATED
    sla_bad = []   # (czas, sla, stops, seq)
    sla_ok = 0
    stops_dist = Counter()
    for ln in gen:
        m_sla = re.search(r"sla=(\d+)", ln)
        m_stops = re.search(r"stops=(\d+)", ln)
        if m_stops:
            stops_dist[int(m_stops.group(1))] += 1
        if m_sla:
            v = int(m_sla.group(1))
            if v == 0:
                sla_ok += 1
            else:
                ts = ln.split(" [")[0]
                seq = re.search(r"seq=(\[[^\]]*\])", ln)
                stops = m_stops.group(1) if m_stops else "?"
                sla_bad.append((ts, v, stops, seq.group(1) if seq else "?"))
    gen_total = sla_ok + len(sla_bad)

    # churn z summary
    def _last_int(key, ln):
        m = re.search(r"'%s':\s*(\d+)" % key, ln)
        return int(m.group(1)) if m else None

    max_active = 0
    any_auto_inval = 0
    any_refloor = 0
    for ln in summaries:
        a = _last_int("active_plans", ln)
        if a is not None:
            max_active = max(max_active, a)
        ai = _last_int("auto_invalidated", ln)
        if ai:
            any_auto_inval += ai
        pr = _last_int("pickup_refloored", ln)
        if pr:
            any_refloor += pr

    cids = Counter(re.search(r"cid=(\d+)", ln).group(1)
                   for ln in regen if re.search(r"cid=(\d+)", ln))

    # bledy (twarde) vs gap_fill skip-warningi
    errors = [ln for ln in lines
              if re.search(r"\[ERROR\]|Traceback|Exception", ln)]
    gapfail = [ln for ln in lines if "gap_fill" in ln and "fail" in ln]

    # ---- werdykt ----
    breach_rate = (len(sla_bad) / gen_total * 100) if gen_total else 0.0
    verdict_bits = []
    if not regen:
        verdict_bits.append("‼ ZERO regenow near-pickup — fix nie odpalil (sprawdz flage/timer)")
    if errors:
        verdict_bits.append(f"‼ {len(errors)} bledow/tracebackow")
    if any_auto_inval or any_refloor:
        verdict_bits.append(
            f"⚠ churn: auto_invalidated={any_auto_inval} refloored={any_refloor}")
    if gen_total and breach_rate <= 5:
        verdict_bits.append(f"✅ R6 OK ({breach_rate:.1f}% naruszen)")
    elif gen_total:
        verdict_bits.append(f"⚠ R6 {breach_rate:.1f}% naruszen (>5%)")
    if max_active <= 2:
        verdict_bits.append(f"ℹ niski wolumen (max {max_active} akt. kurierow) — proba mala jak sobota")
    else:
        verdict_bits.append(f"✅ realny wolumen (max {max_active} akt. kurierow)")

    hours_str = " ".join(f"{h}h:{hours[h]}" for h in sorted(hours)) or "(brak)"
    cids_str = ", ".join(f"{c}×{n}" for c, n in cids.most_common(6)) or "(brak)"
    bad_str = "\n".join(
        f"   • {ts} sla={v} stops={st} seq={sq}" for ts, v, st, sq in sla_bad[:8]
    ) or "   (brak — 0 naruszen R6)"
    stops_str = " ".join(f"{k}:{stops_dist[k]}" for k in sorted(stops_dist)) or "(brak)"

    # FAIL-04 prep-variance (shadow_decisions.jsonl, flaga ON od 06.06 20:19)
    pv = prep_variance_summary(day)
    if not pv["available"]:
        pv_str = "Prep-variance (FAIL-04): brak danych shadow za ten dzien (flaga OFF?)"
    else:
        rests = ", ".join(
            f"{r}×{n}(gap~{pv['gaps'].get(r)})"
            for r, n in pv["by_rest"].most_common(6)
        ) or "(brak)"
        pv_str = (f"Prep-variance anomalie (FAIL-04 shadow): "
                  f"{pv['anomalies']}/{pv['total']} decyzji\n  restauracje: {rests}")
        if pv["anomalies"] > 0:
            verdict_bits.append(f"✅ FAIL-04 wykryl {pv['anomalies']} anomalii prep")
        else:
            verdict_bits.append("ℹ FAIL-04: 0 anomalii prep (brak ryzykownych zlecen)")

    report = (
        f"🚚 Front-load Ziomek — niedziela {day} (21:00 check)\n"
        f"\n"
        f"Regeny near-pickup: {len(regen)} (lunch UTC09-12={lunch}, dinner UTC15-18={dinner})\n"
        f"  godzinowo: {hours_str}\n"
        f"  kurierzy z regenem: {cids_str}\n"
        f"\n"
        f"Naruszenia R6 w planach: {sla_ok}/{gen_total} sla=0"
        f" ({breach_rate:.1f}% breach)\n"
        f"  worki z sla>0:\n{bad_str}\n"
        f"  rozmiary workow (stops): {stops_str}\n"
        f"\n"
        f"Churn: auto_invalidated={any_auto_inval}, pickup_refloored={any_refloor},"
        f" gap_fill-fail={len(gapfail)}, bledy={len(errors)}\n"
        f"Wolumen: max active_plans={max_active}\n"
        f"\n"
        f"{pv_str}\n"
        f"\n"
        f"vs sobota 06.06: {SAT_REGEN} regen, {SAT_SLA_OK}/{SAT_SLA_TOTAL} sla=0, 0 bledow, 1-2 akt.\n"
        f"\n"
        f"WERDYKT: " + " | ".join(verdict_bits)
    )
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-06-07")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args()
    report = analyze(args.day)
    print(report)
    if args.notify:
        ok = _send(report)
        print(f"\n[notify] telegram sent={ok}")


if __name__ == "__main__":
    main()
