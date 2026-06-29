"""DARMOWY werdykt recalib — WEEKEND (sobota/niedziela) krzywa godzinowa vs obecna V326.

Bliźniak `analyze_recalib.py` (weekday), ale dla weekendu, który recalib 06-05
NIE dotknął. Obecna tabela weekend w common.py:V326_OSRM_TRAFFIC_TABLE:
  saturday = [(0,12,1.0),(12,15,1.1),(15,17,1.2),(17,21,1.2),(21,24,1.0)]
  sunday   = [(0,24,1.0)]   <- PŁASKIE 1.0 (podejrzane: F2.2 = niedz 13-19 peak)

Metoda median-based (identyczna z weekday):
  - dla każdej godziny odbioru: ideał_mult = median(gt / freeflow) po tropach tej godz
    → zeruje medianowy rezyduum z definicji.
  - err_cur = freeflow × obecny_mult − gt   (signed; ujemne = NIEDOSZACOWANIE)
  - raportuje per-godzina (sat / sun osobno + razem), z n, by ocenić gęstość.
  - PROPONUJE krzywą weekend (zaokrągloną), ale NIE wdraża (osobny ACK + restart).

ZERO API, ZERO zmiany produkcji — czyta tylko rw_results ⨝ trips_realworld.

CLI:  python3 analyze_recalib_weekend.py
"""
import json
import math
import os
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "rw_results.jsonl")
GROUND_TRUTH = os.path.join(HERE, "trips_realworld.jsonl")
WARSAW = ZoneInfo("Europe/Warsaw")

# OBECNE tabele weekend — kopia common.py:V326_OSRM_TRAFFIC_TABLE (2026-06-05)
CURRENT_SAT = [(0, 12, 1.0), (12, 15, 1.1), (15, 17, 1.2), (17, 21, 1.2), (21, 24, 1.0)]
CURRENT_SUN = [(0, 24, 1.0)]
# minimalny n/godzina by zaproponować mnożnik (poniżej = za cienko, fallback obecny)
MIN_N_HOUR = 6


def _lookup(table, hour):
    for lo, hi, mult in table:
        if lo <= hour < hi:
            return mult
    return 1.0


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and '"oid"' in line:
            rows.append(json.loads(line))
    return rows


def _median(xs):
    return statistics.median(xs) if xs else 0.0


def _rmse(xs):
    return math.sqrt(statistics.mean(x * x for x in xs)) if xs else 0.0


def _round_mult(x):
    """Zaokrąglij do 0.05 (jak krzywa weekday)."""
    return round(round(x / 0.05) * 0.05, 2)


def _per_hour_block(records, cur_table, label):
    """Per-godzina: median rezyduum obecny + proponowany mnożnik (median gt/ff)."""
    by_h = {}
    for r in records:
        by_h.setdefault(r["hour"], []).append(r)
    print(f"\n=== {label} (n={len(records)}) — median rezyduum + propozycja ===")
    print("  rezid = predykcja_obecna − realna jazda (min). Ujemne = NIEDOSZACOWANIE.")
    print(f"{'godz':>4} {'n':>4} | {'cur_mult':>8} {'rezid_cur':>10} | "
          f"{'ideał':>6} {'propon.':>8} {'rezid_prop':>11}")
    print("-" * 64)
    proposal = {}
    for h in sorted(by_h):
        rs = by_h[h]
        cm = _lookup(cur_table, h)
        med_resid_cur = _median([r["ff"] * cm - r["gt"] for r in rs])
        ideal = _median([r["gt"] / r["ff"] for r in rs if r["ff"] > 0])
        if len(rs) >= MIN_N_HOUR:
            prop = max(1.0, _round_mult(ideal))   # nie schodzimy poniżej 1.0 (free-flow)
        else:
            prop = cm                              # za cienko → zostaw obecny
        med_resid_prop = _median([r["ff"] * prop - r["gt"] for r in rs])
        proposal[h] = (prop, len(rs))
        thin = "" if len(rs) >= MIN_N_HOUR else "  (cienko→cur)"
        flag = "  <<" if abs(med_resid_cur) >= 1.0 and abs(med_resid_prop) < abs(med_resid_cur) else ""
        print(f"{h:>4} {len(rs):>4} | {cm:>8.2f} {med_resid_cur:>+10.2f} | "
              f"{ideal:>6.2f} {prop:>8.2f} {med_resid_prop:>+11.2f}{flag}{thin}")
    return proposal


def _summary(records, cur_table, proposal, label):
    if not records:
        print(f"\n{label}: brak tropów.")
        return None
    ec, ep = [], []
    for r in records:
        cm = _lookup(cur_table, r["hour"])
        pm = proposal.get(r["hour"], (cm, 0))[0]
        ec.append(r["ff"] * cm - r["gt"])
        ep.append(r["ff"] * pm - r["gt"])
    cur_bias, prop_bias = statistics.mean(ec), statistics.mean(ep)
    cur_mae = statistics.mean(abs(x) for x in ec)
    prop_mae = statistics.mean(abs(x) for x in ep)
    wins = sum(1 for a, b in zip(ec, ep) if abs(b) < abs(a))
    ties = sum(1 for a, b in zip(ec, ep) if abs(b) == abs(a))
    winrate = (wins + 0.5 * ties) / len(records)
    print(f"\n  {label} n={len(records)} | bias {cur_bias:+.2f}→{prop_bias:+.2f} "
          f"| rawMAE {cur_mae:.2f}→{prop_mae:.2f} | rawRMSE {_rmse(ec):.2f}→{_rmse(ep):.2f} "
          f"| win% {winrate*100:.0f}")
    return {"n": len(records), "cur_bias": cur_bias, "prop_bias": prop_bias,
            "cur_mae": cur_mae, "prop_mae": prop_mae, "winrate": winrate}


def _emit_table(proposal, cur_table, name):
    """Zbuduj listę (lo,hi,mult) z per-godzinowej propozycji, sklejając równe sąsiednie."""
    hours = {h: proposal.get(h, (_lookup(cur_table, h), 0))[0] for h in range(24)}
    # godziny bez danych → obecny mnożnik
    for h in range(24):
        if h not in proposal:
            hours[h] = _lookup(cur_table, h)
    segs = []
    lo = 0
    for h in range(1, 25):
        if h == 24 or hours[h] != hours[lo]:
            segs.append((lo, h, hours[lo]))
            lo = h
    print(f"\n  PROPOZYCJA {name} (kandydat do common.py, NIE wdrożone):")
    print(f"    {name} = {segs}")


def main():
    gt = {}
    for r in _load_jsonl(GROUND_TRUTH):
        if "tier2_underflow" not in (r.get("problems") or []):
            gt[r["oid"]] = r
    preds = _load_jsonl(RESULTS)

    sat, sun = [], []
    for p in preds:
        g = gt.get(p["oid"])
        ff = p.get("osrm_freeflow_min")
        if not g or ff is None or g.get("ground_truth_drive_min") is None:
            continue
        dow = datetime.fromtimestamp(g["pu_epoch"], WARSAW).weekday()
        if dow <= 4:
            continue
        rec = {"oid": p["oid"], "hour": g["hour_warsaw"], "tier": g["tier"],
               "gt": g["ground_truth_drive_min"], "ff": ff}
        (sat if dow == 5 else sun).append(rec)

    print("=" * 64)
    print("DARMOWY WERDYKT RECALIB — WEEKEND (offline, $0)")
    print("=" * 64)
    print(f"rw_results: {len(preds)}  ground truth: {len(gt)}")
    print(f"weekend po join: sobota={len(sat)}  niedziela={len(sun)}")
    if not (sat or sun):
        print("\nBrak tropów weekend — sprawdź dane.")
        return

    sat_prop = _per_hour_block(sat, CURRENT_SAT, "SOBOTA") if sat else {}
    sun_prop = _per_hour_block(sun, CURRENT_SUN, "NIEDZIELA") if sun else {}

    print("\n" + "=" * 64)
    print("PODSUMOWANIE (obecny → propozycja median-based)")
    print("-" * 64)
    s_sat = _summary(sat, CURRENT_SAT, sat_prop, "SOBOTA") if sat else None
    s_sun = _summary(sun, CURRENT_SUN, sun_prop, "NIEDZIELA") if sun else None

    if sat:
        _emit_table(sat_prop, CURRENT_SAT, "saturday")
    if sun:
        _emit_table(sun_prop, CURRENT_SUN, "sunday")

    print("\n" + "=" * 64)
    print("WERDYKT")
    print("-" * 64)
    for tag, s in [("SOBOTA", s_sat), ("NIEDZIELA", s_sun)]:
        if not s:
            continue
        bias_better = abs(s["prop_bias"]) < abs(s["cur_bias"]) - 0.10
        mae_ok = s["prop_mae"] <= s["cur_mae"] + 0.05
        thin = s["n"] < 50
        if bias_better and mae_ok:
            verd = "✅ rekalibracja pomaga"
        elif bias_better:
            verd = "⚠ bias↓ ale MAE↑ (przestrzelenie) — łagodniej"
        else:
            verd = "❌ brak poprawy — zostaw obecną"
        cav = "  ⚠ n<50 = SYGNAŁ SŁABY, zbieraj dalej" if thin else ""
        print(f"  {tag:9} n={s['n']:>3} | |bias| {abs(s['cur_bias']):.2f}→{abs(s['prop_bias']):.2f}"
              f" | MAE {s['cur_mae']:.2f}→{s['prop_mae']:.2f} | {verd}{cav}")
    print("-" * 64)
    print("  CAVEAT: weekend = mały sample (split sat/sun + per-godz cienkie).")
    print("  Godziny n<6 zostają na obecnym mnożniku (fallback). To kierunek+skala,")
    print("  nie finalna krzywa — przy n<50/dzień traktuj jako wstępne.")


if __name__ == "__main__":
    main()
