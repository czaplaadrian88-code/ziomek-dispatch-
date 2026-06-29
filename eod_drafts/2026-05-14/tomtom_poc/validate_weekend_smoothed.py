"""Walidacja WYGŁADZONYCH krzywych weekend przed wdrożeniem do common.py.

Surowa krzywa median-based jest poszarpana (pojedyncze cienkie godziny przestrzeliwują).
Te SMOOTHED to wersja do produkcji (analog weekday wariant B): zachowuje silny sygnał
dużych próbek, wygładza/konserwatywnie traktuje cienkie godziny (n<~8) i szum (niedz 21:00).
Sprawdzamy że SMOOTHED nadal bije OBECNĄ (bias↓, MAE nie gorszy) na tym samym zbiorze.
"""
import json
import math
import os
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
WARSAW = ZoneInfo("Europe/Warsaw")

CURRENT_SAT = [(0, 12, 1.0), (12, 15, 1.1), (15, 17, 1.2), (17, 21, 1.2), (21, 24, 1.0)]
CURRENT_SUN = [(0, 24, 1.0)]

# WYGŁADZONE kandydaty do wdrożenia
SMOOTH_SAT = [(0, 12, 1.0), (12, 13, 1.30), (13, 16, 1.20), (16, 17, 1.55),
              (17, 18, 1.45), (18, 21, 1.25), (21, 22, 1.10), (22, 24, 1.0)]
SMOOTH_SUN = [(0, 11, 1.0), (11, 12, 1.50), (12, 13, 1.40), (13, 15, 1.35),
              (15, 16, 1.45), (16, 19, 1.30), (19, 20, 1.15), (20, 24, 1.0)]


def _lookup(t, h):
    for lo, hi, m in t:
        if lo <= h < hi:
            return m
    return 1.0


def _load(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and '"oid"' in line:
            rows.append(json.loads(line))
    return rows


def _rmse(xs):
    return math.sqrt(statistics.mean(x * x for x in xs)) if xs else 0.0


def _eval(recs, cur_t, new_t, label):
    ec = [r["ff"] * _lookup(cur_t, r["hour"]) - r["gt"] for r in recs]
    en = [r["ff"] * _lookup(new_t, r["hour"]) - r["gt"] for r in recs]
    wins = sum(1 for a, b in zip(ec, en) if abs(b) < abs(a))
    ties = sum(1 for a, b in zip(ec, en) if abs(b) == abs(a))
    wr = (wins + 0.5 * ties) / len(recs) if recs else 0
    cb, nb = statistics.mean(ec), statistics.mean(en)
    cm = statistics.mean(abs(x) for x in ec)
    nm = statistics.mean(abs(x) for x in en)
    ok = abs(nb) < abs(cb) - 0.10 and nm <= cm + 0.05
    print(f"{label:9} n={len(recs):>3} | bias {cb:+.2f}→{nb:+.2f} | "
          f"MAE {cm:.2f}→{nm:.2f} | RMSE {_rmse(ec):.2f}→{_rmse(en):.2f} | "
          f"win {wr*100:.0f}% | {'✅ OK' if ok else '❌ NIE bije'}")
    return ok


def main():
    gt = {}
    for r in _load(os.path.join(HERE, "trips_realworld.jsonl")):
        if "tier2_underflow" not in (r.get("problems") or []):
            gt[r["oid"]] = r
    sat, sun = [], []
    for p in _load(os.path.join(HERE, "rw_results.jsonl")):
        g = gt.get(p["oid"])
        ff = p.get("osrm_freeflow_min")
        if not g or ff is None or g.get("ground_truth_drive_min") is None:
            continue
        dow = datetime.fromtimestamp(g["pu_epoch"], WARSAW).weekday()
        if dow <= 4:
            continue
        rec = {"hour": g["hour_warsaw"], "gt": g["ground_truth_drive_min"], "ff": ff}
        (sat if dow == 5 else sun).append(rec)

    print("WALIDACJA WYGŁADZONYCH KRZYWYCH WEEKEND (obecna → smoothed)")
    print("-" * 78)
    a = _eval(sat, CURRENT_SAT, SMOOTH_SAT, "SOBOTA")
    b = _eval(sun, CURRENT_SUN, SMOOTH_SUN, "NIEDZIELA")
    print("-" * 78)
    print("WERDYKT:", "✅ OBA biją obecną — można wdrażać" if (a and b)
          else "⚠ któraś nie bije — popraw przed wdrożeniem")
    print("\nsaturday =", SMOOTH_SAT)
    print("sunday   =", SMOOTH_SUN)


if __name__ == "__main__":
    main()
