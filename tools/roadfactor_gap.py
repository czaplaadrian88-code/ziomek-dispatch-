#!/usr/bin/env python3
"""ROADFACTOR-GAP — czy fleet-wide ×k (C3: OSRM zaniża miejską jazdę ~42%) jest WARTE
dodania, czy JUŻ zaszyte w ścieżce ETA R6 (wtedy no-op / podwójne liczenie). OFFLINE.

USTALENIE ZE ŚCIEŻKI KODU (czytane, nie edytowane):
- R6/route ETA bierze drive z `osrm_client.route()`, a KAŻDY return route() przechodzi
  przez `_apply_traffic_multiplier` (osrm_client.py:564/569/581/593/608/620/626 + matrix
  689/714/737). Flaga `ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER` default "1" = LIVE, nigdzie
  nie nadpisana (systemd/env) → mnożnik aplikowany do produkcyjnych durationów.
- Mnożnik = TABELA godzinowa `V326_OSRM_TRAFFIC_TABLE` (common.py:478) via
  get_traffic_multiplier: weekday peak 12-17 = 1.40-1.55, wieczór 1.25, noc 1.0.
  Czyli C3 „×1.42" JUŻ jest pokryte — czasowo, nie flat. (Ranker `ml_inference.py:334`
  haversine×1.42 to INNA, równoległa ścieżka — NIE R6-ETA.)
- `HAVERSINE_ROAD_FACTOR_BIALYSTOK=1.37` (common.py:367) dotyczy tylko DYSTANSU w
  fallbacku haversine (gdy OSRM padnie), nie czasu R6 z żywego OSRM.

CO TEN HARNESS MIERZY (na danych, weryfikacja twierdzenia kodu):
`predicted_delivery_min` w eta_calibration_log = ETA PO korekcie V326 (z per_order_delivery_times
silnika, który woła traffic-corrected route()). Residual = real − predicted. Jeśli:
  • mediana(residual) ~0 i nie zaniża systematycznie → korekta JUŻ wystarczy → dodanie ×k
    SZKODLIWE (podwójne liczenie, przestrzeli w górę).
  • residual systematycznie DODATNI (real > predicted, zaniżamy) o ~X% → LUKA warta domknięcia.
Symulacja: corrected_k = predicted × k dla siatki k (w tym 1.42) → MAE/bias residualne;
szukamy k* min-MAE i czy k*>1 (luka) czy k*≈1 (zaszyte). Per-godzina (korekta jest godzinowa)
+ split bundle/single (drive-dominated). Fail-soft, python3, nic nie mutuje."""
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone

DS = "/root/.openclaw/workspace/dispatch_state"
ETA_LOG = f"{DS}/eta_calibration_log.jsonl"
# C3 globalny współczynnik do przetestowania + siatka wokół 1.0 (zaszyte) i 1.42.
C3_FACTOR = 1.42
K_GRID = [0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.30, 1.42, 1.55]

try:
    from zoneinfo import ZoneInfo
    WARSAW = ZoneInfo("Europe/Warsaw")
except Exception:                      # pragma: no cover
    WARSAW = timezone.utc


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _read(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return out


# Filtr fizyczności: pojedynczy dowóz w Białymstoku ma sens w (0, MAX_PLAUSIBLE_MIN].
# Log zawiera śmieciowe predicted (zaobserwowane do 20013 min = sentinel/outlier) które
# zatruwają bias/MAE (godz 13/14/21 sztucznie −20..−63 min). Real >120 min też nie jest
# czasem JAZDY (kurier idle / multi-task). Bez tego werdykt jest oparty na <1% śmieci.
MAX_PLAUSIBLE_MIN = 120.0


def load_matched(path=ETA_LOG, *, max_plausible_min=MAX_PLAUSIBLE_MIN):
    """Wiersze z dopasowaniem (matched_courier) + predicted + real, nie-czasówka,
    z filtrem fizyczności (0 < pred,real ≤ max_plausible_min). max_plausible_min=None
    wyłącza filtr (do diagnostyki surowych danych).
    To ETA dla realnie wybranego kuriera = najbliżej tego co R6 policzył dla decyzji."""
    out = []
    dropped = 0
    for r in _read(path):
        if not r.get("matched_courier"):
            continue
        pd = _num(r.get("predicted_delivery_min"))
        rd = _num(r.get("real_delivery_min"))
        if pd is None or rd is None or r.get("was_czasowka"):
            continue
        if max_plausible_min is not None and not (
                0 < pd <= max_plausible_min and 0 < rd <= max_plausible_min):
            dropped += 1
            continue
        out.append(r)
    load_matched.last_dropped = dropped
    return out


def _hour(r):
    """Godzina Warszawska — z hour_warsaw jeśli jest, inaczej z delivered_at/logged_at."""
    h = _num(r.get("hour_warsaw"))
    if h is not None:
        return int(h)
    for k in ("delivered_at", "picked_up_at", "logged_at"):
        v = r.get(k)
        if v:
            try:
                s = v.replace(" ", "T")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(WARSAW).hour
            except Exception:
                continue
    return None


# ───────────────────────── statystyki residualne ─────────────────────────
def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    import math
    k = (len(sorted_vals) - 1) * (q / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def resid_stats(preds, reals):
    """err = real − pred (DODATNI = ETA ZANIŻA, real dłuższy → kierunek C3 'underestimate').
    Zwraca n, mae, bias(mean err), median_signed, p90_abs, p95_abs, frac_under_pred
    (=P(real>pred)=udział niedoszacowania), rel_bias (% względem real)."""
    errs = [g - p for p, g in zip(preds, reals)]
    if not errs:
        return None
    n = len(errs)
    abserr = sorted(abs(e) for e in errs)
    under = sum(1 for e in errs if e > 0)         # real > pred → ETA zaniżyła
    sum_real = sum(reals)
    return {
        "n": n,
        "mae": sum(abserr) / n,
        "bias": sum(errs) / n,                     # >0 → systematycznie zaniżamy
        "median_signed": statistics.median(errs),
        "p90_abs": _pct(abserr, 90),
        "p95_abs": _pct(abserr, 95),
        "frac_under_pred": under / n,              # P(real>pred)
        "rel_bias_pct": (100 * sum(errs) / sum_real) if sum_real else None,
    }


def simulate_k(records, k):
    """MAE/bias residualne przy corrected = predicted × k."""
    preds = [k * _num(r.get("predicted_delivery_min")) for r in records]
    reals = [_num(r.get("real_delivery_min")) for r in records]
    return resid_stats(preds, reals)


def best_k(records, grid=None):
    """k* minimalizujące MAE residualne na siatce + dokładniejsze przeszukanie 0.80-1.60."""
    grid = grid or [round(0.80 + 0.01 * i, 2) for i in range(81)]   # 0.80..1.60 co 0.01
    best = None
    for k in grid:
        s = simulate_k(records, k)
        if s is None:
            continue
        if best is None or s["mae"] < best[1]:
            best = (k, s["mae"], s["bias"])
    return best  # (k*, mae*, bias_at_k*)


# ───────────────────────── driver ─────────────────────────
def run(path=ETA_LOG):
    recs = load_matched(path)
    preds = [_num(r.get("predicted_delivery_min")) for r in recs]
    reals = [_num(r.get("real_delivery_min")) for r in recs]
    overall = resid_stats(preds, reals)

    # per-godzina (korekta V326 jest godzinowa → tu wychodzą luki w konkretnych oknach)
    by_hour = defaultdict(list)
    for r in recs:
        h = _hour(r)
        if h is not None:
            by_hour[h].append(r)
    hourly = {}
    for h in sorted(by_hour):
        rs = by_hour[h]
        if len(rs) < 20:
            continue
        hourly[h] = {
            "stats": resid_stats([_num(r.get("predicted_delivery_min")) for r in rs],
                                 [_num(r.get("real_delivery_min")) for r in rs]),
            "best_k": best_k(rs),
        }

    # split bundle (drive niedominujący) vs single (drive-dominated → bliżej C3 drive×k)
    single = [r for r in recs if not r.get("is_bundle")]
    bundle = [r for r in recs if r.get("is_bundle")]

    # siatka k globalnie
    grid = {f"{k:.2f}": simulate_k(recs, k) for k in K_GRID}

    return {
        "n": len(recs),
        "dropped": getattr(load_matched, "last_dropped", 0),
        "overall": overall,
        "best_k_global": best_k(recs),
        "k_at_c3": simulate_k(recs, C3_FACTOR),
        "grid": grid,
        "single": {"stats": resid_stats([_num(r.get("predicted_delivery_min")) for r in single],
                                        [_num(r.get("real_delivery_min")) for r in single]),
                   "best_k": best_k(single), "n": len(single)},
        "bundle": {"stats": resid_stats([_num(r.get("predicted_delivery_min")) for r in bundle],
                                        [_num(r.get("real_delivery_min")) for r in bundle]),
                   "best_k": best_k(bundle), "n": len(bundle)},
        "hourly": hourly,
    }


def _f(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def print_report(res):
    print("=" * 84)
    print("ROADFACTOR-GAP — residual obecnego ETA (PO korekcie V326) + symulacja globalnego ×k")
    print("=" * 84)
    print("USTALENIE Z KODU: R6-ETA bierze drive z osrm_client.route() → KAŻDY return")
    print("  przechodzi _apply_traffic_multiplier; flaga V326_OSRM_TRAFFIC_MULTIPLIER default")
    print("  '1'=LIVE, nienadpisana → mnożnik godzinowy (peak 1.40-1.55) JUŻ aplikowany.")
    print("  Ranker ml_inference.py:334 (haversine×1.42) = INNA ścieżka, nie R6-ETA.")
    print("-" * 84)
    o = res["overall"]
    print(f"PRÓBKA: n={res['n']} (matched, nie-czasówka, 0<pred,real≤{int(MAX_PLAUSIBLE_MIN)}min; "
          f"odrzucono {res.get('dropped', 0)} nie-fizycznych, np. predicted=20013min)")
    print("RESIDUAL OBECNEGO ETA (err = real − predicted; +bias = ZANIŻA):")
    print(f"  MAE={_f(o['mae'])}  bias={_f(o['bias'])} min  median_signed={_f(o['median_signed'])} min")
    print(f"  rel_bias={_f(o['rel_bias_pct'])}%  P(real>pred)={_f(o['frac_under_pred'])}  "
          f"p90={_f(o['p90_abs'])} p95={_f(o['p95_abs'])}")
    bk = res["best_k_global"]
    kc3 = res["k_at_c3"]
    print("-" * 84)
    print("SYMULACJA GLOBALNEGO ×k (corrected = predicted × k):")
    print(f"  {'k':>5s}  {'MAE':>7s}  {'bias':>7s}  {'P(real>pred)':>12s}")
    for k in [f"{x:.2f}" for x in K_GRID]:
        s = res["grid"][k]
        mark = "  ← C3 1.42" if k == "1.42" else ("  ← obecny (zaszyte)" if k == "1.00" else "")
        print(f"  {k:>5s}  {s['mae']:7.2f}  {s['bias']:+7.2f}  {s['frac_under_pred']:12.2f}{mark}")
    print(f"  k* (min-MAE, fine grid 0.80-1.60): k={_f(bk[0])}  MAE={_f(bk[1])}  bias@k*={_f(bk[2])}")
    print(f"  MAE@k=1.42: {_f(kc3['mae'])} (bias {_f(kc3['bias'])})  vs  MAE@k=1.00: {_f(res['grid']['1.00']['mae'])}")
    print("-" * 84)
    for lab, key in [("SINGLE (drive-dominated)", "single"), ("BUNDLE", "bundle")]:
        d = res[key]
        s = d["stats"]
        if s:
            print(f"{lab}: n={d['n']}  MAE={_f(s['mae'])} bias={_f(s['bias'])} "
                  f"P(real>pred)={_f(s['frac_under_pred'])}  k*={_f(d['best_k'][0])}")
    print("-" * 84)
    print("PER-GODZINA (Warsaw; bias>0=zaniża; k*=min-MAE per godzina):")
    print(f"  {'h':>3s} {'n':>5s} {'MAE':>6s} {'bias':>7s} {'rel%':>6s} {'k*':>5s}")
    for h in sorted(res["hourly"]):
        st = res["hourly"][h]["stats"]
        kk = res["hourly"][h]["best_k"]
        print(f"  {h:3d} {st['n']:5d} {st['mae']:6.2f} {st['bias']:+7.2f} "
              f"{_f(st['rel_bias_pct']):>6s} {_f(kk[0]):>5s}")
    print("=" * 84)
    return res


if __name__ == "__main__":
    print_report(run())
