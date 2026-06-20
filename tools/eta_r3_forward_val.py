#!/usr/bin/env python3
"""ETA R3 — forward-validation harness (CZYSTO OFFLINE, 2026-06-20, track C5).

Cel: ZANIM ktokolwiek rozważy flip modelu residual ETA R3 (LightGBM, shadow,
flaga ENABLE_ETA_R3_SHADOW=true) na "primary", udowodnić na NOWYCH, held-out
danych w oknach kroczących, że korekta R3 obniża MAE względem bazy (OSRM) — i że
nie pojawia się train/serve skew. Bez tego flip byłby na wiarę.

Co liczy:
  • Dla BAZY (predicted_delivery_min) i dla R3 (eta_r3_corrected_delivery_min)
    w oknach kroczących: MAE, mediana |err|, p90, p95 oraz BŁĄD ZE ZNAKIEM
    (mediana err, P(err<0) = czy model systematycznie zawyża/zaniża).
  • Poprawę % R3 vs baza per okno (czy ≥próg trzyma się w KAŻDYM oknie).
  • KS-test parity per cecha (9 cech modelu) serwowanie-vs-trening — wykrycie
    train/serve skew. tier_ord (porządkowa) rozbita na one-hot PRZED parity.

Dane są held-out: trening R3 ciął do TRAIN_MAX=2026-06-13, log forward zaczyna
się 18.06 (pierwsze shadow-wiring). To pierwsza realna forward-walidacja R3.

KRYTYCZNE — feature parity: rekonstrukcja 9 cech jest LUSTREM `feats()` z treningu
(eod_drafts/2026-06-18/eta_residual_model.py): tier z real_courier_id||best_courier_id,
rest_freq z zapisanego rest_freq.json (train-time, NIE-żywy), pool_feasible z
backfill_decisions_outcomes_v1.jsonl, braki → -1. Inaczej parity porównuje jabłka
z gruszkami. KS-test policzony INLINE (bez scipy) — harness biega pod gołym python3.

Fail-soft: brak pliku/modelu/cechy → pomijamy i raportujemy, zero wyjątków w górę.
NIE edytuje modułów silnika; tylko czyta dane i artefakty modelu."""
import json
import math
import os
import statistics
from collections import defaultdict

DS = "/root/.openclaw/workspace/dispatch_state"
MODEL_DIR = "/root/.openclaw/workspace/scripts/ml_data_prep/models/eta_residual_v1"
ETA_LOG = f"{DS}/eta_calibration_log.jsonl"
BACKFILL = f"{DS}/backfill_decisions_outcomes_v1.jsonl"
TIERS_PATH = f"{DS}/courier_tiers.json"

# Musi być identyczne z treningiem (TIER_ORD w eta_residual_model.py / eta_residual_infer.py).
TRAIN_MAX = "2026-06-13"          # data graniczna treningu R3 (held-out > tej daty)
TIER_ORD = {"gold": 4, "std+": 3, "std": 2, "slow": 1, "new": 0}
TIER_ORD_DEFAULT = 2
FEATURE_NAMES = ["bag_size", "pred_delivery_min", "hour", "is_weekend",
                 "is_bundle", "peak", "tier_ord", "rest_freq", "pool_feasible"]
# Próg poprawy MAE, który chcemy widzieć w KAŻDYM oknie, by w ogóle rozważać flip.
IMPROVEMENT_TARGET_PCT = 8.0
# Próg p-value KS poniżej którego flagujemy skew (z prostą poprawką Bonferroniego
# na liczbę testowanych cech raportujemy też surowe p).
KS_ALPHA = 0.01


# ───────────────────────── util / IO ─────────────────────────
def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _read_jsonl(path):
    """Czyta jsonl fail-soft (pomija puste/niepoprawne linie). Zwraca listę dict."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _tier_of(v):
    if isinstance(v, dict):
        return ((v.get("bag") or {}).get("tier") or v.get("tier") or v.get("tier_label"))
    return v


def load_cid2tier(path=TIERS_PATH):
    if not os.path.exists(path):
        return {}
    try:
        T = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    return {k: _tier_of(v) for k, v in T.items() if k != "_meta"}


def load_rest_freq(model_dir=MODEL_DIR):
    """Train-time częstość restauracji (zapisana przy treningu). Lustro `restcnt`."""
    p = f"{model_dir}/rest_freq.json"
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def load_pool_feasible(path=BACKFILL):
    pool = {}
    for d in _read_jsonl(path):
        oid = d.get("order_id")
        if oid is not None:
            pool[str(oid)] = _num(d.get("pool_feasible"))
    return pool


# ───────────────────────── feature reconstruction (mirror feats()) ─────────────────────────
def build_features(r, *, cid2tier, rest_freq, pool):
    """LUSTRO feats() z treningu — zwraca [9] w kolejności FEATURE_NAMES.
    tier z real_courier_id||best_courier_id; rest_freq z zapisanej tabeli (lower);
    pool_feasible z backfill po oid; braki → -1 (num) / 0 (bool)."""
    bs = _num(r.get("bag_size"))
    pdm = _num(r.get("predicted_delivery_min"))
    hr = _num(r.get("hour_warsaw"))
    cid = str(r.get("real_courier_id") or r.get("best_courier_id") or "")
    tier = cid2tier.get(cid)
    peak = 1 if (hr is not None and (11 <= hr < 14 or 17 <= hr < 20)) else 0
    rfreq = rest_freq.get((r.get("restaurant") or "").lower(), 0)
    oid = str(r.get("oid") or r.get("order_id"))
    pf = pool.get(oid)
    return [
        bs if bs is not None else -1,
        pdm if pdm is not None else -1,
        hr if hr is not None else -1,
        1 if r.get("is_weekend") else 0,
        1 if r.get("is_bundle") else 0,
        peak,
        TIER_ORD.get(tier, TIER_ORD_DEFAULT),
        rfreq,
        pf if pf is not None else -1,
    ]


# ───────────────────────── record selection / join ─────────────────────────
def _valid_for_mae(r):
    """Identyczne z valid() w treningu: baza i ground-truth obecne i nie-czasówka."""
    return (_num(r.get("predicted_delivery_min")) is not None
            and _num(r.get("real_delivery_min")) is not None
            and not r.get("was_czasowka"))


def load_serve_records(path=ETA_LOG):
    """Rekordy SERWOWANIA z korektą R3: per-oid wybiera linię z R3≠null preferując tę,
    która ma też real_delivery_min. Zwraca listę dict (po jednym na oid)."""
    by_oid = defaultdict(list)
    for r in _read_jsonl(path):
        by_oid[r.get("oid")].append(r)
    serve = []
    for oid, recs in by_oid.items():
        if oid is None:
            continue
        r3 = [x for x in recs if x.get("eta_r3_corrected_delivery_min") is not None]
        if not r3:
            continue
        with_real = [x for x in r3 if _num(x.get("real_delivery_min")) is not None]
        serve.append(with_real[0] if with_real else r3[0])
    return serve


def load_train_records(path=ETA_LOG, train_max=TRAIN_MAX):
    """Rekordy TRENINGU (held-in, logged_at[:10] ≤ train_max, valid()) — baza parity."""
    return [r for r in _read_jsonl(path)
            if _valid_for_mae(r) and (r.get("logged_at") or "")[:10] <= train_max]


# ───────────────────────── metrics ─────────────────────────
def _pct(sorted_vals, q):
    """Percentyl (nearest-rank, q w [0,100]) na POSORTOWANEJ liście. None gdy pusto."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (q / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def error_stats(preds, reals):
    """Statystyki błędu dla jednej serii predykcji vs ground truth.
    err = pred - real (dodatni = model ZAWYŻA czas; ujemny = ZANIŻA).
    Zwraca dict: n, mae, median_abs, p90_abs, p95_abs, median_signed, mean_signed,
    frac_under (P(err<0)), frac_over (P(err>0))."""
    errs = [p - g for p, g in zip(preds, reals)]
    if not errs:
        return None
    abserr = sorted(abs(e) for e in errs)
    n = len(errs)
    under = sum(1 for e in errs if e < 0)
    over = sum(1 for e in errs if e > 0)
    return {
        "n": n,
        "mae": sum(abserr) / n,
        "median_abs": statistics.median(abserr),
        "p90_abs": _pct(abserr, 90),
        "p95_abs": _pct(abserr, 95),
        "median_signed": statistics.median(errs),
        "mean_signed": sum(errs) / n,
        "frac_under": under / n,   # P(err<0): model zaniża (real > pred) → optymizm
        "frac_over": over / n,     # P(err>0): model zawyża (real < pred) → pesymizm
    }


# ───────────────────────── KS-test (two-sample, inline, no scipy) ─────────────────────────
def ks_2samp(a, b):
    """Dwupróbkowy test Kołmogorowa-Smirnowa, implementacja własna (bez scipy).
    Zwraca (D, p_value). D = max |F_a - F_b|; p z asymptotyki Kołmogorowa.
    Stałe próbki (zero wariancji) obsłużone: identyczne → D=0,p=1; różne stałe → D=1."""
    a = sorted(float(x) for x in a)
    b = sorted(float(x) for x in b)
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return (None, None)
    # zbiór wszystkich punktów; ECDF każdej próbki w tych punktach
    allv = sorted(set(a) | set(b))

    def ecdf(sample, x):
        # frakcja elementów <= x (bisekcja byłaby szybsza; n tu małe)
        lo, hi = 0, len(sample)
        # ręczny bisect_right
        while lo < hi:
            mid = (lo + hi) // 2
            if sample[mid] <= x:
                lo = mid + 1
            else:
                hi = mid
        return lo / len(sample)

    D = 0.0
    for x in allv:
        d = abs(ecdf(a, x) - ecdf(b, x))
        if d > D:
            D = d
    # asymptotyczne p (Kołmogorow): en = sqrt(na*nb/(na+nb))
    en = math.sqrt(na * nb / (na + nb))
    lam = (en + 0.12 + 0.11 / en) * D
    # Q_KS(lam) = 2 * sum_{j=1..inf} (-1)^{j-1} exp(-2 j^2 lam^2)
    if lam < 1e-9:
        p = 1.0
    else:
        s = 0.0
        for j in range(1, 101):
            term = 2.0 * ((-1) ** (j - 1)) * math.exp(-2.0 * j * j * lam * lam)
            s += term
            if abs(term) < 1e-12:
                break
        p = max(0.0, min(1.0, s))
    return (D, p)


def feature_columns(records, *, cid2tier, rest_freq, pool):
    """Zwraca dict feature_name -> list[float] dla podanych rekordów (kolumny cech)."""
    cols = {fn: [] for fn in FEATURE_NAMES}
    for r in records:
        fv = build_features(r, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)
        for fn, v in zip(FEATURE_NAMES, fv):
            cols[fn].append(v)
    return cols


def parity_report(train_records, serve_records, *, cid2tier, rest_freq, pool):
    """KS parity per cecha train-vs-serve. tier_ord rozbity na one-hot (po 1 wpis na poziom).
    Zwraca listę dict {feature, D, p, n_train, n_serve, train_mean, serve_mean, skew(bool)}."""
    tcols = feature_columns(train_records, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)
    scols = feature_columns(serve_records, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)
    rows = []
    for fn in FEATURE_NAMES:
        if fn == "tier_ord":
            # one-hot: każdy poziom porządkowy → wskaźnik {0,1}, KS na proporcji
            levels = sorted(set(TIER_ORD.values()))
            for lvl in levels:
                ta = [1.0 if v == lvl else 0.0 for v in tcols[fn]]
                sa = [1.0 if v == lvl else 0.0 for v in scols[fn]]
                D, p = ks_2samp(ta, sa)
                rows.append(_parity_row(f"tier_ord==%d" % lvl, ta, sa, D, p))
        else:
            D, p = ks_2samp(tcols[fn], scols[fn])
            rows.append(_parity_row(fn, tcols[fn], scols[fn], D, p))
    return rows


def _parity_row(name, ta, sa, D, p):
    tm = sum(ta) / len(ta) if ta else None
    sm = sum(sa) / len(sa) if sa else None
    return {
        "feature": name, "D": D, "p": p,
        "n_train": len(ta), "n_serve": len(sa),
        "train_mean": tm, "serve_mean": sm,
        "skew": (p is not None and p < KS_ALPHA),
    }


# ───────────────────────── rolling windows ─────────────────────────
def _day(r):
    return (r.get("logged_at") or "")[:10]


def rolling_windows(serve_records, window_days, step_days=1):
    """Zwraca listę okien (start_date, end_date, [rekordy]) krocząc po dniach.
    Gdy zakres danych < window_days, degraduje do JEDNEGO okna obejmującego całość
    oraz (informacyjnie) okien DZIENNYCH — sygnalizuje to flagą 'degraded'.
    Każde okno: [start, end] inkluzywnie (end-start+1 = window_days dla pełnych)."""
    days = sorted({_day(r) for r in serve_records if _day(r)})
    if not days:
        return []
    from datetime import date, timedelta

    def d(s):
        y, m, dd = s.split("-")
        return date(int(y), int(m), int(dd))

    d0, d1 = d(days[0]), d(days[-1])
    span = (d1 - d0).days + 1
    windows = []
    if span >= window_days:
        start = d0
        while (start + timedelta(days=window_days - 1)) <= d1:
            end = start + timedelta(days=window_days - 1)
            sset = {start + timedelta(days=i) for i in range(window_days)}
            recs = [r for r in serve_records if _day(r) and d(_day(r)) in sset]
            windows.append({
                "start": start.isoformat(), "end": end.isoformat(),
                "records": recs, "degraded": False,
            })
            start = start + timedelta(days=step_days)
    else:
        # za mało dni na pełne okno kroczące — całość jako 1 okno + okna dzienne
        windows.append({
            "start": days[0], "end": days[-1],
            "records": list(serve_records), "degraded": True,
        })
        for day in days:
            recs = [r for r in serve_records if _day(r) == day]
            windows.append({
                "start": day, "end": day,
                "records": recs, "degraded": True, "daily": True,
            })
    return windows


def window_metrics(window, *, min_n=10):
    """MAE/p95/signed dla bazy i R3 w jednym oknie + poprawa %. None gdy n<min_n."""
    recs = [r for r in window["records"] if _valid_for_mae(r)
            and _num(r.get("eta_r3_corrected_delivery_min")) is not None]
    if len(recs) < min_n:
        return {"start": window["start"], "end": window["end"],
                "degraded": window.get("degraded", False), "n": len(recs),
                "insufficient": True, "daily": window.get("daily", False)}
    reals = [_num(r.get("real_delivery_min")) for r in recs]
    base = [_num(r.get("predicted_delivery_min")) for r in recs]
    r3 = [_num(r.get("eta_r3_corrected_delivery_min")) for r in recs]
    sb = error_stats(base, reals)
    sr = error_stats(r3, reals)
    impr = (100.0 * (sb["mae"] - sr["mae"]) / sb["mae"]) if sb["mae"] > 0 else 0.0
    return {
        "start": window["start"], "end": window["end"],
        "degraded": window.get("degraded", False), "daily": window.get("daily", False),
        "n": len(recs), "insufficient": False,
        "base": sb, "r3": sr,
        "improvement_pct": impr,
        "meets_target": impr >= IMPROVEMENT_TARGET_PCT,
    }


# ───────────────────────── driver / report ─────────────────────────
def run(window_days=7, min_n=10, eta_log=ETA_LOG):
    cid2tier = load_cid2tier()
    rest_freq = load_rest_freq()
    pool = load_pool_feasible()
    serve = load_serve_records(eta_log)
    train = load_train_records(eta_log)

    usable = [r for r in serve if _valid_for_mae(r)
              and _num(r.get("eta_r3_corrected_delivery_min")) is not None]

    windows = rolling_windows(serve, window_days=window_days)
    wmetrics = [window_metrics(w, min_n=min_n) for w in windows]
    parity = parity_report(train, serve, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)

    return {
        "n_serve_oids": len(serve),
        "n_usable": len(usable),
        "n_train": len(train),
        "train_max": TRAIN_MAX,
        "window_days": window_days,
        "windows": wmetrics,
        "parity": parity,
        "overall": window_metrics({"records": serve, "start": "ALL", "end": "ALL"}, min_n=min_n),
    }


def _fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def print_report(res):
    print("=" * 78)
    print("ETA R3 — FORWARD-VALIDATION (offline)  | track C5")
    print("=" * 78)
    print(f"serve oids z R3: {res['n_serve_oids']}   usable (base+R3+real, nie-czasówka): {res['n_usable']}")
    print(f"train (held-in ≤ {res['train_max']}): {res['n_train']}   okno kroczące: {res['window_days']}d")
    deg = any(w.get("degraded") for w in res["windows"])
    if deg:
        print(f"⚠ ZAKRES DANYCH < {res['window_days']}d → okna kroczące zdegradowane do "
              "całości + okien DZIENNYCH (forward-walidacja wczesna, n mały).")
    print("-" * 78)
    print("OKNA (MAE: baza→R3, poprawa%, p95, błąd-ze-znakiem P(err<0)=zaniżanie):")
    for w in res["windows"]:
        tag = "dzień" if w.get("daily") else "okno "
        if w.get("insufficient"):
            print(f"  {tag} {w['start']}..{w['end']}  n={w['n']:<4d} — za mało (min_n)")
            continue
        b, r = w["base"], w["r3"]
        star = "✅" if w["meets_target"] else "❌"
        print(f"  {tag} {w['start']}..{w['end']}  n={w['n']:<4d}  "
              f"MAE {b['mae']:5.2f}→{r['mae']:5.2f} ({w['improvement_pct']:+5.1f}% {star})  "
              f"p95 {_fmt(b['p95_abs'])}→{_fmt(r['p95_abs'])}  "
              f"P(err<0) {b['frac_under']:.2f}→{r['frac_under']:.2f}")
    ov = res["overall"]
    if not ov.get("insufficient"):
        b, r = ov["base"], ov["r3"]
        print("-" * 78)
        print("CAŁOŚĆ:")
        print(f"  n={ov['n']}  MAE baza={b['mae']:.2f}  R3={r['mae']:.2f}  poprawa {ov['improvement_pct']:+.1f}%")
        print(f"  baza : median|e|={_fmt(b['median_abs'])} p90={_fmt(b['p90_abs'])} p95={_fmt(b['p95_abs'])} "
              f"median_signed={_fmt(b['median_signed'])} P(err<0)={b['frac_under']:.2f}")
        print(f"  R3   : median|e|={_fmt(r['median_abs'])} p90={_fmt(r['p90_abs'])} p95={_fmt(r['p95_abs'])} "
              f"median_signed={_fmt(r['median_signed'])} P(err<0)={r['frac_under']:.2f}")
    print("-" * 78)
    print(f"KS PARITY train-vs-serve (skew gdy p<{KS_ALPHA}; tier_ord → one-hot):")
    skewed = []
    for row in res["parity"]:
        flag = "  ⚠ SKEW" if row["skew"] else ""
        if row["skew"]:
            skewed.append(row["feature"])
        print(f"  {row['feature']:16s} D={_fmt(row['D'],3)} p={_fmt(row['p'],4)} "
              f"train μ={_fmt(row['train_mean'])} serve μ={_fmt(row['serve_mean'])}{flag}")
    print("-" * 78)
    if skewed:
        print(f"CECHY ZE SKEW train/serve: {', '.join(skewed)}")
    else:
        print("CECHY ZE SKEW train/serve: brak (p≥alpha wszędzie)")
    print("=" * 78)
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ETA R3 forward-validation (offline)")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--min-n", type=int, default=10)
    ap.add_argument("--eta-log", default=ETA_LOG)
    a = ap.parse_args()
    print_report(run(window_days=a.window_days, min_n=a.min_n, eta_log=a.eta_log))
