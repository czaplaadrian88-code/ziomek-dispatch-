#!/usr/bin/env python3
"""B2/FLEET-T15 — DIAGNOZA: ile z 225 strukturalnych KOORD było DO UNIKNIĘCIA
lepszym pozycjonowaniem/deferralem ~15 min wcześniej, a ile to TWARDY niedobór
floty ("nie było kim")?

TYLKO ODCZYT. Nie dotyka żywych modułów silnika. Bez commita/flipa/restartu.

⚠️ OGRANICZENIE DANYCH (ważne, mówię wprost):
  shadow_decisions NIE loguje ABSOLUTNYCH pozycji kurierów (lat/lng) ani
  współrzędnych restauracji — restaurant/delivery_address to gołe stringi
  ("Doner Kebab" / "Owocowa 6"). Nie ma też per-minutowej historii GPS
  (courier_last_pos.json = 2 kurierów żywych, courier_plans.json = snapshot).
  → PRAWDZIWA geometryczna rekonstrukcja floty w T−15 (postawienie kurierów na
  mapie 15 min wcześniej) jest NIEMOŻLIWA z obecnych logów. Co trzeba by
  dologować: per-tick snapshot {cid, lat, lng, status, free_at, bag} co ~1-2
  min (np. `fleet_position_history.jsonl`).

PROXY (z pól które SĄ logowane per decyzja — alternatives[] = pełna pula
feasible w momencie decyzji, z `free_at_min`, `pos_source`, `r7_ride_km`):
  Zamiast cofać czas, używam stanu floty W MOMENCIE decyzji + horyzontu
  zwalniania. Jeśli któryś kurier zwalnia się w ≤15 min, to deferral/„poczekaj
  15 min" dałby feasible kuriera (proxy AVOIDABLE-DEFERRAL). Jeśli któryś był
  bezczynny (~0) i blisko (ride<LONGHAUL), proaktywne pozycjonowanie mogło go
  mieć pod ręką (AVOIDABLE-POSITIONING). Gdy pula=1 z głęboko zajętym, albo
  cała pula pre_shift (nikt nie zaczął zmiany), albo nikt nie zwalnia ≤15 i
  nikt idle-blisko → TRULY-NOBODY (twardy niedobór/obsada).

Klasy (priorytet: NOBODY-hard > DEFERRAL > POSITIONING > NOBODY-soft):
  - TRULY_NOBODY_STAFFING: cała pula pre_shift (obsada — nikt nie pracuje)
  - TRULY_NOBODY_SINGLE: pool_feasible<=1 i ten 1 zwalnia >15 min
  - AVOIDABLE_DEFERRAL: ktoś zwalnia ≤15 min (poczekać/odroczyć)
  - AVOIDABLE_POSITIONING: ktoś idle(~0) i ride<LONGHAUL (proaktywne ustawienie)
  - TRULY_NOBODY_LOAD: reszta — wszyscy zajęci >15 i nikt idle-blisko (load)

Raport: AVOIDABLE% vs TRULY-NOBODY% + rozbicie po przyczynie B1b + peak.
Fail-soft.
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
DEFER_HORIZON_MIN = 15.0     # „poczekaj 15 min" — okno deferralu
IDLE_FREE_MIN = 2.0          # free_at_min ~0 = bezczynny/zaraz wolny

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


def _dominant(best):
    wk, wv = None, 0.0
    for c in PENALTY_COMPONENTS:
        v = _num(best, c)
        if v is not None and v < wv:
            wk, wv = c, v
    return wk


def _struct_reasons(d, best):
    reasons = []
    sc = _num(best, "score")
    pf = _num(d, "pool_feasible_count")
    if sc is not None and sc <= SENT:
        reasons.append("sentinel_new_courier")
    if pf is not None and pf <= 1:
        reasons.append("pool<=1")
    if _dominant(best) == "bonus_r6_soft_pen" and (
            (_num(best, "objm_r6_breach_count", 0) or 0) > 0
            or (_num(best, "r6_max_bag_time_min", 0) or 0) > R6_HARD_MAX):
        reasons.append("r6_hard_breach")
    if (_num(best, "km_to_pickup", 0) or 0) > LONGHAUL_KM:
        reasons.append("longhaul_pickup")
    if (_num(best, "late_pickup_committed_max", 0) or 0) > COMMITTED_LATE:
        reasons.append("committed_late")
    return reasons


def _classify_t15(d, best):
    """Zwraca (klasa, avoidable_bool). Priorytet jak w docstringu."""
    alts = d.get("alternatives") or []
    pf = _num(d, "pool_feasible_count")
    srcs = [a.get("pos_source") for a in alts]

    # 1) obsada: cała pula pre_shift (nikt nie zaczął zmiany)
    if alts and all(s == "pre_shift" for s in srcs):
        return "TRULY_NOBODY_STAFFING", False

    # horyzonty zwalniania / idle w PEŁNEJ puli
    frees_15 = False
    idle_close = False
    for a in alts:
        fa = _num(a, "free_at_min")
        if fa is not None and fa <= DEFER_HORIZON_MIN:
            frees_15 = True
        ride = _num(a, "r7_ride_km")
        if fa is not None and fa <= IDLE_FREE_MIN and ride is not None and ride < LONGHAUL_KM:
            idle_close = True

    # 2) pojedynczy kandydat głęboko zajęty (>15) → twardo nie ma kim
    if pf is not None and pf <= 1 and not frees_15:
        return "TRULY_NOBODY_SINGLE", False

    # 3) ktoś zwalnia ≤15 → deferral pomógłby
    if frees_15:
        return "AVOIDABLE_DEFERRAL", True

    # 4) ktoś idle i blisko → pozycjonowanie pomogłoby
    if idle_close:
        return "AVOIDABLE_POSITIONING", True

    # 5) reszta — wszyscy zajęci >15 i nikt idle-blisko → load/niedobór
    return "TRULY_NOBODY_LOAD", False


def analyze(paths=None):
    paths = paths or DEFAULT_LOGS
    s = {
        "lines": 0, "parse_fail": 0, "low_score": 0, "structural": 0,
        "classes": Counter(),
        "avoidable": 0, "truly_nobody": 0,
        "by_reason": {},          # reason -> Counter(avoidable/nobody)
        "peak": Counter(), "offpeak": Counter(),
        "examples": {},
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
                if "all_candidates_low_score" not in str(d.get("reason") or ""):
                    continue
                best = d.get("best") or {}
                if _num(best, "score") is None:
                    continue
                s["low_score"] += 1
                reasons = _struct_reasons(d, best)
                if not reasons:
                    continue  # ALGORYTM — poza zakresem FLEET-T15
                s["structural"] += 1
                cls, avoidable = _classify_t15(d, best)
                s["classes"][cls] += 1
                if avoidable:
                    s["avoidable"] += 1
                else:
                    s["truly_nobody"] += 1
                for r in reasons:
                    s["by_reason"].setdefault(r, Counter())
                    s["by_reason"][r]["AVOIDABLE" if avoidable else "NOBODY"] += 1
                pk = _peak(d.get("ts"))
                tgt = s["peak"] if pk is True else s["offpeak"] if pk is False else None
                if tgt is not None:
                    tgt["AVOIDABLE" if avoidable else "NOBODY"] += 1
                if cls not in s["examples"]:
                    s["examples"][cls] = {
                        "score": round(_num(best, "score"), 1),
                        "pool_feasible": _num(d, "pool_feasible_count"),
                        "best_free_at_min": _num(best, "free_at_min"),
                        "best_pos_source": best.get("pos_source"),
                        "n_alts": len(d.get("alternatives") or []),
                        "reasons": reasons,
                    }
    return s


def _pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def main():
    s = analyze()
    tot = s["structural"]
    print("=== fleet_t15_replay — FLEET-T15 (avoidable vs truly-nobody) ===")
    print(f"linie: {s['lines']}  parse_fail: {s['parse_fail']}")
    print(f"all_candidates_low_score: {s['low_score']}")
    print(f"STRUKTURALNE (zakres FLEET-T15): {tot}")
    print()
    print(f">>> AVOIDABLE (deferral/pozycjonowanie 15 min by pomogło): "
          f"{s['avoidable']} ({_pct(s['avoidable'], tot)})")
    print(f">>> TRULY-NOBODY (twardy niedobór/obsada): "
          f"{s['truly_nobody']} ({_pct(s['truly_nobody'], tot)})")
    print()
    print("rozbicie po klasie:")
    for cls in ["AVOIDABLE_DEFERRAL", "AVOIDABLE_POSITIONING",
                "TRULY_NOBODY_STAFFING", "TRULY_NOBODY_SINGLE",
                "TRULY_NOBODY_LOAD"]:
        print(f"  {cls:24s} {s['classes'].get(cls, 0):4d} "
              f"({_pct(s['classes'].get(cls, 0), tot)})")
    print()
    print("rozbicie po przyczynie strukturalnej (AVOIDABLE / NOBODY):")
    for r, c in sorted(s["by_reason"].items(), key=lambda x: -sum(x[1].values())):
        a, nob = c.get("AVOIDABLE", 0), c.get("NOBODY", 0)
        print(f"  {r:22s} AVOIDABLE={a:4d}  NOBODY={nob:4d}  "
              f"(avoidable {_pct(a, a + nob)})")
    print()
    print("peak vs off-peak:")
    print(f"  peak:     AVOIDABLE={s['peak'].get('AVOIDABLE',0)} "
          f"NOBODY={s['peak'].get('NOBODY',0)}")
    print(f"  off-peak: AVOIDABLE={s['offpeak'].get('AVOIDABLE',0)} "
          f"NOBODY={s['offpeak'].get('NOBODY',0)}")
    print()
    print("przykłady per klasa:")
    for cls, e in s["examples"].items():
        print(f"  {cls}: score={e['score']} pool={e['pool_feasible']} "
              f"best_free={e['best_free_at_min']} src={e['best_pos_source']} "
              f"n_alts={e['n_alts']} reasons={e['reasons']}")
    return s


if __name__ == "__main__":
    main()
