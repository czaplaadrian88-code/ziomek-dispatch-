#!/usr/bin/env python3
"""NO-GPS-WHO — ACTIONABLE: ilu UNIKALNYCH kurierów stoi za 75 no_gps-zablokowanymi
KOORD (STORE_EMPTY_BUT_SCORE_OK) i jaki Pareto? Zamienia „75 KOORD" na konkretną
listę kurierów do włączenia GPS.

TYLKO ODCZYT. Nie edytuje silnika. Bez commita/flipa.

Populacja: te same 75 co `no_gps_rescue_coverage.py` STORE_EMPTY_BUT_SCORE_OK —
no_gps najszybciej-zwalniający kandydat w deferral-avoidable KOORD, score≥−100,
R6-clean (zablokowany przez _demote_blind_empty, nie przez score/pozycję).

Per cid liczy:
  - liczba KOORD + udział + skumulowany (Pareto)
  - name (alias z logu — `alternatives[].name`, do telefonu)
  - peak/off-peak (Warsaw 11-14 / 17-20)
  - CHRONIC vs SPORADIC: frakcja wystąpień tego cid z pos_source='no_gps' wśród
    WSZYSTKICH jego wystąpień w logu. ≥0,8 = chroniczny (prawie nigdy GPS →
    jeden telefon/nudge), <0,4 = sporadyczny (zwykle ma GPS, gubi okazjonalnie),
    pomiędzy = MIXED.

Cel: jeśli garść osób → enforcement = kilka telefonów; jeśli rozproszone →
systemowy problem adopcji GPS w apce. Fail-soft.
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
SENT = -1e8
R6_HARD_MAX = 35.0
LONGHAUL_KM = 4.5
COMMITTED_LATE = 10.0
DEFER_HORIZON_MIN = 15.0
GATE = -100.0
CHRONIC_FRAC = 0.8
SPORADIC_FRAC = 0.4

DEFAULT_LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]

PENALTY_COMPONENTS = [
    "bonus_r6_soft_pen", "bonus_r5_soft_pen", "bonus_r5_pickup_detour_penalty",
    "bonus_r1_soft_pen", "bonus_r8_soft_pen", "bonus_r9_wait_pen",
    "bonus_r9_stopover", "bonus_v3273_wait_courier", "bonus_r1_corridor",
    "bonus_r5_detour", "bonus_wave_clean", "bonus_inter_wave_deadhead",
    "v324a_extension_penalty", "bonus_bug4_cap_soft",
    "v325_pre_shift_soft_penalty", "bonus_r_return_rest",
]


def _num(d, k, default=None):
    v = d.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _dominant(best):
    wk, wv = None, 0.0
    for c in PENALTY_COMPONENTS:
        v = _num(best, c)
        if v is not None and v < wv:
            wk, wv = c, v
    return wk


def _is_structural(d, best):
    sc = _num(best, "score")
    pf = _num(d, "pool_feasible_count")
    if sc is not None and sc <= SENT:
        return True
    if pf is not None and pf <= 1:
        return True
    if _dominant(best) == "bonus_r6_soft_pen" and (
            (_num(best, "objm_r6_breach_count", 0) or 0) > 0
            or (_num(best, "r6_max_bag_time_min", 0) or 0) > R6_HARD_MAX):
        return True
    if (_num(best, "km_to_pickup", 0) or 0) > LONGHAUL_KM:
        return True
    if (_num(best, "late_pickup_committed_max", 0) or 0) > COMMITTED_LATE:
        return True
    return False


def _r6_clean(c):
    return not ((_num(c, "objm_r6_breach_count", 0) or 0) > 0
                or (_num(c, "r6_max_bag_time_min", 0) or 0) > R6_HARD_MAX)


def _not_late(c):
    return (_num(c, "late_pickup_committed_max", 0) or 0) <= COMMITTED_LATE


def _soonest_freeing(d):
    alts = d.get("alternatives") or []
    srcs = [a.get("pos_source") for a in alts]
    if alts and all(s == "pre_shift" for s in srcs):
        return None
    pf = _num(d, "pool_feasible_count")
    frees = [a for a in alts
             if _num(a, "free_at_min") is not None
             and _num(a, "free_at_min") <= DEFER_HORIZON_MIN]
    if pf is not None and pf <= 1 and not frees:
        return None
    if not frees:
        return None
    return min(frees, key=lambda a: _num(a, "free_at_min", 1e9))


def _peak(ts_iso):
    if not ts_iso:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        h = dt.astimezone(WARSAW).hour
        return (11 <= h < 14) or (17 <= h < 20)
    except Exception:
        return None


def analyze(paths=None):
    paths = paths or DEFAULT_LOGS
    s = {
        "lines": 0, "parse_fail": 0, "total_koord": 0,
        "cid_koord": Counter(), "cid_name": {},
        "cid_peak": Counter(), "cid_off": Counter(),
        "cid_total_appear": Counter(), "cid_nogps_appear": Counter(),
    }
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s["lines"] += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    s["parse_fail"] += 1
                    continue
                # appearance tally (KAŻDA decyzja, dla chronic/sporadic)
                for c in [d.get("best") or {}] + (d.get("alternatives") or []):
                    cd = c.get("courier_id")
                    if cd is None:
                        continue
                    cd = str(cd)
                    s["cid_total_appear"][cd] += 1
                    if c.get("pos_source") == "no_gps":
                        s["cid_nogps_appear"][cd] += 1
                if "all_candidates_low_score" not in str(d.get("reason") or ""):
                    continue
                best = d.get("best") or {}
                if _num(best, "score") is None or not _is_structural(d, best):
                    continue
                fc = _soonest_freeing(d)
                if fc is None or fc.get("pos_source") != "no_gps":
                    continue
                fcs = _num(fc, "score")
                if not (fcs is not None and fcs >= GATE
                        and _r6_clean(fc) and _not_late(fc)):
                    continue
                cid = str(fc.get("courier_id"))
                s["total_koord"] += 1
                s["cid_koord"][cid] += 1
                if fc.get("name"):
                    s["cid_name"][cid] = fc.get("name")
                pk = _peak(d.get("ts"))
                if pk is True:
                    s["cid_peak"][cid] += 1
                elif pk is False:
                    s["cid_off"][cid] += 1
    return s


def _chronic_label(frac):
    if frac >= CHRONIC_FRAC:
        return "CHRONIC"
    if frac < SPORADIC_FRAC:
        return "SPORADIC"
    return "MIXED"


def _pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def main():
    s = analyze()
    tot = s["total_koord"]
    n_cid = len(s["cid_koord"])
    print("=== no_gps_who — ACTIONABLE lista kurierów do GPS ===")
    print(f"linie: {s['lines']}  parse_fail: {s['parse_fail']}")
    print(f"no_gps-zablokowane KOORD (STORE_EMPTY_BUT_SCORE_OK): {tot}")
    print(f"UNIKALNYCH kurierów (cid): {n_cid}")
    have_name = sum(1 for c in s["cid_koord"] if c in s["cid_name"])
    print(f"name w logu dla: {have_name}/{n_cid} cid")
    print()
    print(f"{'cid':>5} {'KOORD':>5} {'%':>5} {'cum%':>5}  {'name':<20} "
          f"{'peak/off':>9}  chronic")
    cum = 0
    for cid, cnt in s["cid_koord"].most_common():
        cum += cnt
        frac = (s["cid_nogps_appear"][cid] / s["cid_total_appear"][cid]
                if s["cid_total_appear"][cid] else 0.0)
        lbl = _chronic_label(frac)
        pkoff = f"{s['cid_peak'][cid]}/{s['cid_off'][cid]}"
        print(f"{cid:>5} {cnt:>5} {_pct(cnt, tot):>5} {_pct(cum, tot):>5}  "
              f"{s['cid_name'].get(cid, '—'):<20} {pkoff:>9}  "
              f"{lbl} ({frac*100:.0f}% no_gps z {s['cid_total_appear'][cid]} wyst.)")
    print()
    # headline: ilu kurierów daje ~80%
    cum = 0
    n80 = 0
    for cid, cnt in s["cid_koord"].most_common():
        cum += cnt
        n80 += 1
        if cum >= 0.8 * tot:
            break
    print(f"PARETO: {n80} kurier(ów) generuje ≥80% tych KOORD.")
    return s


if __name__ == "__main__":
    main()
