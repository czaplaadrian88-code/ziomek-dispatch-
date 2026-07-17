#!/usr/bin/env python3
"""[C2 / TOR3] Decision-time replay korekty prep-bias na bramkę R6.

Czym różni się od starego prep_bias_r6_replay.py (HINDSIGHT):
  Stary replay brał REALNY wiek termiczny dostawy (delivered − declared_ready)
  jako proxy "o ile R6 jest ostrzejsza", a potem TĄ SAMĄ realną wartością mierzył
  korzyść → PODWÓJNE liczenie (korekta i tak by tego ordera nie naprawiła; sam
  fakt że wyszedł na >35 nie znaczy że korekta cokolwiek zmieniła).

Ten replay rozdziela DWIE rzeczy, których stary mieszał:
  (1) DECYZJA bramki R6 — liczona z informacji DOSTĘPNEJ W CHWILI DECYZJI:
        projekcja silnika `predicted_r6_max_bag_min` (= max bag_time worka w
        momencie decyzji) + korekta = bias restauracji ZNANY z przeszłości.
        Korekta przesuwa kotwicę WCZEŚNIEJ o `bias` → projekcja R6 rośnie o
        `bias` → R6 może bić wcześniej. FLIP = projekcja przekracza 35 PO
        korekcie, choć baseline ≤35.
  (2) OUTCOME — REALNY on-time (delivered − pickup_ready ≤ 35 z sla_join), użyty
        WYŁĄCZNIE do oceny czy FLIP był słuszny (ochrona świeżości) czy szkodliwy
        (false-reject ordera, który i tak doszedłby na czas).

Dwa framingi biasu (oba decision-time — żaden nie używa realnej dostawy DANEGO
ordera do policzenia jego biasu):
  * "table"  — stosuje zbudowaną tabelę prep_bias_table.json (tak jak zrobiłby
               to silnik produkcyjnie: tabela budowana z czystego sygnału
               kuchni, niezależna od on-time danej dostawy). To framing PRIMARY.
  * "lfo"    — strict leave-future-out: bias restauracji liczony WYŁĄCZNIE z jej
               czystych "waited" rekordów ze znacznikiem ts < decision_ts.
               Bardziej rygorystyczny, ale głodny danych (instrumentacja
               ready_at_log ruszyła 2026-06-14, decyzje sięgają 2026-06-08).

Warianty siły korekty (parametr CLI --variant lub pętla w main):
    p80      — bias_p80 (PRODUKCYJNY default prep_bias_anchor) [referencja]
    median   — bias_median
    half     — 0.5 × bias_median
    p70      — 0.7 × bias_median
    highbreach — korekta TYLKO dla restauracji z wysokim realnym breach-rate
                 (≥ HIGH_BREACH_RATE_MIN), inaczej 0.

Werdykt per wariant: realny on-time PRZED/PO + regresja świeżości
(false-reject = order on-time, który korekta wypycha do KOORD/innego kuriera).

NIE dotyka silnika ani flag. Czyta tylko logi. python3. Fail-soft.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_PARENT = os.path.dirname(os.path.dirname(_HERE))  # .../scripts
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from dispatch_v2.tools import ontime_lib  # noqa: E402
from dispatch_v2.tools import prep_bias_build  # noqa: E402

DISPATCH_STATE = "/root/.openclaw/workspace/dispatch_state"
READY_LOG = os.path.join(DISPATCH_STATE, "ready_at_log.jsonl")
DECISIONS_LOG = os.path.join(DISPATCH_STATE, "backfill_decisions_outcomes_v1.jsonl")
PREP_BIAS_TABLE = os.path.join(DISPATCH_STATE, "prep_bias_table.json")

HARD_MAX = ontime_lib.ON_TIME_THRESHOLD_MIN  # 35.0
# Cap przesunięcia kotwicy (spójny z prep_bias_anchor.MAX_ANCHOR_SHIFT_MIN).
MAX_SHIFT_MIN = 20.0
# Próg realnego breach-rate restauracji dla wariantu highbreach.
HIGH_BREACH_RATE_MIN = 0.30
# Min liczba realnych obserwacji on-time żeby breach-rate restauracji był wiarygodny.
HIGH_BREACH_MIN_N = 10


# --------------------------------------------------------------------------- #
# Wczytanie czystych obserwacji prep-bias z ts (do LFO i do per-rest tabeli).
# --------------------------------------------------------------------------- #
def load_clean_obs(path=READY_LOG):
    """rest -> posortowana lista (ts_dt, bias) z czystego sygnału 'waited'.

    Używa tych samych reguł czystości co prep_bias_build.is_clean_signal.
    """
    by_rest = defaultdict(list)
    for rec in ontime_lib._iter_jsonl(path):
        if not prep_bias_build.is_clean_signal(rec):
            continue
        rest = rec.get("restaurant")
        if not rest:
            continue
        ts = ontime_lib.parse_ts(rec.get("ts")) or ontime_lib.parse_ts(
            rec.get("picked_up_at_iso"))
        if ts is None:
            continue
        by_rest[rest].append((ts, float(rec["prep_bias_min"])))
    for r in by_rest:
        by_rest[r].sort(key=lambda t: t[0])
    return by_rest


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    m = n // 2
    return float(s[m]) if n % 2 else (s[m - 1] + s[m]) / 2.0


def _percentile(xs, pct):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    if n == 1:
        return float(s[0])
    rank = (pct / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


def lfo_bias(rest, decision_dt, clean_obs, global_obs_sorted):
    """Strict leave-future-out bias (median, p80) dla restauracji w chwili decyzji.

    Bierze tylko czyste obserwacje tej restauracji z ts < decision_dt. Gdy za
    mało (n<MIN), shrinkage do globalnej mediany z PRZESZŁOŚCI (też ts<decision).
    Zwraca (median, p80) lub (None, None).
    """
    obs = clean_obs.get(rest, [])
    past = [b for (ts, b) in obs if ts < decision_dt]
    # globalny prior z przeszłości
    g_past = [b for (ts, b) in global_obs_sorted if ts < decision_dt]
    if not past and not g_past:
        return None, None
    if not past:
        return _median(g_past), _percentile(g_past, 80)
    n = len(past)
    rm, rp = _median(past), _percentile(past, 80)
    if n < prep_bias_build.SHRINK_THRESHOLD and g_past:
        gm, gp = _median(g_past), _percentile(g_past, 80)
        ps = prep_bias_build.SHRINK_PRIOR_STRENGTH
        rm = (n * rm + ps * gm) / (n + ps)
        if rp is not None and gp is not None:
            rp = (n * rp + ps * gp) / (n + ps)
    return rm, rp


def load_table_bias(path=PREP_BIAS_TABLE):
    """Z prep_bias_table.json: rest -> (median, p80); + _global fallback."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}, (None, None)
    out = {}
    for k, v in data.items():
        if k == "_global" or not isinstance(v, dict):
            continue
        out[k] = (v.get("bias_median_min"), v.get("bias_p80_min"))
    g = data.get("_global") or {}
    gpair = (g.get("bias_median_min"), g.get("bias_p80_min"))
    return out, gpair


# --------------------------------------------------------------------------- #
# Korekta wg wariantu — zwraca przesunięcie kotwicy (minuty, >=0) do dodania
# do projekcji R6. Konwencja: dodatnie przesunięcie = projekcja rośnie = R6
# ostrzejsza (ochrona świeżości). Bias ujemny → 0 (nie rozluźniamy R6).
# --------------------------------------------------------------------------- #
def variant_shift(variant, bias_median, bias_p80, rest_breach_rate):
    if variant == "p80":
        b = bias_p80
    elif variant == "median":
        b = bias_median
    elif variant == "half":
        b = None if bias_median is None else 0.5 * bias_median
    elif variant == "p70":
        b = None if bias_median is None else 0.7 * bias_median
    elif variant == "highbreach":
        if rest_breach_rate is None or rest_breach_rate < HIGH_BREACH_RATE_MIN:
            return 0.0
        b = bias_median
    else:
        raise ValueError(f"nieznany wariant: {variant}")
    if b is None or b <= 0.0:
        return 0.0
    return min(b, MAX_SHIFT_MIN)


VARIANTS = ["p80", "median", "half", "p70", "highbreach"]


# --------------------------------------------------------------------------- #
# Główny replay.
# --------------------------------------------------------------------------- #
def run(framing="table", variants=None, decisions_log=DECISIONS_LOG,
        ready_log=READY_LOG, table_path=PREP_BIAS_TABLE,
        sla_decision_paths=None, sla_delivery_paths=None,
        matched_only=False):
    """matched_only=True → tylko decyzje gdzie proposed_courier_id == final.

    To jedyny podzbiór gdzie `predicted_r6_max_bag_min` (projekcja dla
    PROPONOWANEGO kuriera) odpowiada realnemu outcome (dla FINALNEGO kuriera).
    Bez tego filtra 89% decyzji to PANEL_OVERRIDE/TIMEOUT_SUPERSEDED — projekcja
    dotyczy innego kuriera niż ten, który faktycznie dowiózł → flip-precyzja
    jest zaszumiona (porównuje projekcję kuriera A z dostawą kuriera B).
    """
    variants = variants or VARIANTS
    clean_obs = load_clean_obs(ready_log)
    global_obs_sorted = sorted(
        [(ts, b) for obs in clean_obs.values() for (ts, b) in obs],
        key=lambda t: t[0])
    table_bias, table_global = load_table_bias(table_path)

    # SLA on-time prawda per order (z sla_join, kontrakt A4).
    dec_idx, deliv_idx = ontime_lib.build_indices(
        decision_paths=sla_decision_paths, delivery_paths=sla_delivery_paths)

    # PASS 1: realny breach-rate per restauracja (na potrzeby wariantu highbreach
    # i raportu). Liczony z realnych dostaw (delivered − pickup_ready).
    rest_real = defaultdict(lambda: {"n": 0, "breach": 0})
    decisions = []
    for rec in ontime_lib._iter_jsonl(decisions_log):
        oid = rec.get("order_id")
        if oid is None:
            continue
        oid = str(oid)
        pred = rec.get("predicted_r6_max_bag_min")
        if not isinstance(pred, (int, float)):
            continue
        rest = rec.get("restaurant") or ""
        dts = ontime_lib.parse_ts(rec.get("decision_ts"))
        prop = rec.get("proposed_courier_id")
        fin = (rec.get("outcome") or {}).get("courier_id_final")
        matched = (fin is not None and str(prop) == str(fin))
        if matched_only and not matched:
            continue
        ot = ontime_lib.compute_on_time(oid, dec_idx, deliv_idx)
        on_time = ot.get("on_time")
        # real_age = realny czas dostawy (delivered − pickup_ready) jeśli policzalny
        real_age = ot.get("delivery_time_minutes")
        decisions.append({
            "oid": oid, "rest": rest, "pred": float(pred), "dts": dts,
            "on_time": on_time, "real_age": real_age,
            "grace": ot.get("grace"), "matched": matched,
        })
        if on_time is not None:
            rest_real[rest]["n"] += 1
            if on_time is False:
                rest_real[rest]["breach"] += 1
    rest_breach_rate = {
        r: (v["breach"] / v["n"] if v["n"] >= HIGH_BREACH_MIN_N else None)
        for r, v in rest_real.items()
    }

    # DIAGNOSTYKA jakości projekcji R6: czy decision-time `predicted_r6` w ogóle
    # przewiduje realny breach? Liczona na decyzjach z outcome. Confusion matrix
    # względem progu 35 (baseline) — to sufit tego, ile korekta MOŻE pomóc:
    # jeśli pred>35 słabo koreluje z realnym >35, żaden shift nie naprawi bramki.
    cm = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}  # pred>35 vs real breach
    for d in decisions:
        if d["on_time"] is None:
            continue
        pred_breach = d["pred"] > HARD_MAX
        real_breach = d["on_time"] is False
        if pred_breach and real_breach:
            cm["tp"] += 1
        elif pred_breach and not real_breach:
            cm["fp"] += 1
        elif not pred_breach and not real_breach:
            cm["tn"] += 1
        else:
            cm["fn"] += 1
    _tp, _fp, _fn = cm["tp"], cm["fp"], cm["fn"]
    cm["precision_pred_gt35"] = round(_tp / (_tp + _fp), 4) if (_tp + _fp) else None
    cm["recall_pred_gt35"] = round(_tp / (_tp + _fn), 4) if (_tp + _fn) else None

    # PASS 2: per wariant policz flipy i ich słuszność.
    results = {}
    for variant in variants:
        agg = {
            "n_decisions_evaluable": 0,   # mają on_time != None (da się ocenić outcome)
            "n_already_gated": 0,         # baseline pred>35 (R6 i tak odrzuca)
            "n_zero_shift": 0,            # korekta=0 dla restauracji
            "n_flips": 0,                 # PASS->REJECT (newly gated)
            "n_flip_correct": 0,          # flip AND realnie breach (słuszna ochrona)
            "n_flip_false": 0,            # flip AND realnie on-time (FALSE-REJECT)
            "n_flip_unknown": 0,          # flip ale outcome nieznany (grace/no deliv)
            # baseline outcome na decyzjach przechodzących baseline (pred<=35):
            "baseline_pass_n": 0,
            "baseline_pass_ontime": 0,
        }
        per_rest = defaultdict(lambda: {
            "n": 0, "flips": 0, "flip_correct": 0, "flip_false": 0,
            "real_breach": 0, "real_n": 0, "shift_example": 0.0,
        })
        for d in decisions:
            rest = d["rest"]
            dts = d["dts"]
            # --- bias wg framingu ---
            if framing == "table":
                bm, bp = table_bias.get(rest, (None, None))
                if bm is None and bp is None:
                    bm, bp = table_global  # nieznana restauracja → global
            elif framing == "lfo":
                if dts is None:
                    bm = bp = None
                else:
                    bm, bp = lfo_bias(rest, dts, clean_obs, global_obs_sorted)
            else:
                raise ValueError(f"nieznany framing: {framing}")
            shift = variant_shift(variant, bm, bp, rest_breach_rate.get(rest))

            pr = per_rest[rest]
            pr["n"] += 1
            if shift > pr["shift_example"]:
                pr["shift_example"] = round(shift, 2)
            if d["on_time"] is not None:
                pr["real_n"] += 1
                if d["on_time"] is False:
                    pr["real_breach"] += 1

            pred = d["pred"]
            baseline_pass = pred <= HARD_MAX
            corrected_pred = pred + shift
            corrected_pass = corrected_pred <= HARD_MAX

            if d["on_time"] is not None:
                agg["n_decisions_evaluable"] += 1
            if baseline_pass and d["on_time"] is not None:
                agg["baseline_pass_n"] += 1
                if d["on_time"]:
                    agg["baseline_pass_ontime"] += 1

            if not baseline_pass:
                agg["n_already_gated"] += 1
                continue
            if shift <= 0:
                agg["n_zero_shift"] += 1
                continue
            # baseline PASS, korekta>0: czy flip na REJECT?
            if not corrected_pass:
                agg["n_flips"] += 1
                pr["flips"] += 1
                if d["on_time"] is True:
                    agg["n_flip_false"] += 1
                    pr["flip_false"] += 1
                elif d["on_time"] is False:
                    agg["n_flip_correct"] += 1
                    pr["flip_correct"] += 1
                else:
                    agg["n_flip_unknown"] += 1

        # --- agregaty on-time przed/po ---
        # PRZED = realny on-time na decyzjach baseline-PASS evaluable.
        # PO    = traktujemy flip jako "ten order NIE jedzie tym kurierem" — w
        #         najlepszym wypadku trafi do szybszego kuriera/KOORD i dojdzie
        #         na czas; w najgorszym KOORD = opóźnienie. Modelujemy DWA skraje:
        #         optymistyczny (flip ratuje breach, nie psuje on-time) i
        #         pesymistyczny (flip = order do KOORD, traktowany jak breach).
        base_n = agg["baseline_pass_n"]
        base_on = agg["baseline_pass_ontime"]
        before_rate = base_on / base_n if base_n else None
        # optymistyczny: każdy słuszny flip zamienia breach→on-time, każdy
        # false-reject ZOSTAJE on-time (zakładamy redirect doszedł na czas).
        after_on_opt = base_on + agg["n_flip_correct"]
        after_rate_opt = after_on_opt / base_n if base_n else None
        # pesymistyczny: false-reject = order wypchnięty → liczony jak NIE-on-time
        # (utrata pewnej dostawy), słuszny flip neutralny (breach tak czy tak).
        after_on_pess = base_on - agg["n_flip_false"]
        after_rate_pess = after_on_pess / base_n if base_n else None

        agg["on_time_before"] = round(before_rate, 4) if before_rate is not None else None
        agg["on_time_after_optimistic"] = round(after_rate_opt, 4) if after_rate_opt is not None else None
        agg["on_time_after_pessimistic"] = round(after_rate_pess, 4) if after_rate_pess is not None else None
        agg["freshness_regression_false_rejects"] = agg["n_flip_false"]
        agg["protected_breaches"] = agg["n_flip_correct"]
        agg["flip_precision"] = (
            round(agg["n_flip_correct"] / (agg["n_flip_correct"] + agg["n_flip_false"]), 4)
            if (agg["n_flip_correct"] + agg["n_flip_false"]) else None)
        results[variant] = {"agg": agg, "per_rest": per_rest}

    return {
        "framing": framing,
        "matched_only": matched_only,
        "n_decisions_total": len(decisions),
        "n_with_outcome": sum(1 for d in decisions if d["on_time"] is not None),
        "pred_quality_vs_real_breach": cm,
        "rest_breach_rate": rest_breach_rate,
        "rest_real": dict(rest_real),
        "variants": results,
        "table_global_bias": table_global,
    }


def _fmt_pct(x):
    return f"{100*x:.1f}%" if isinstance(x, (int, float)) else "  n/a"


def print_report(res, top_n=15):
    print("=" * 78)
    mo = " [MATCHED-ONLY proposed==final]" if res.get("matched_only") else ""
    print(f"DECISION-TIME prep-bias replay — framing={res['framing']}{mo}")
    print("=" * 78)
    print(f"decyzji total: {res['n_decisions_total']}  z realnym outcome: {res['n_with_outcome']}")
    print(f"tabela _global bias (median,p80): {res['table_global_bias']}")
    cm = res.get("pred_quality_vs_real_breach", {})
    if cm:
        print(f"JAKOŚĆ PROJEKCJI R6 (pred>35 vs realny breach>35): "
              f"TP={cm.get('tp')} FP={cm.get('fp')} TN={cm.get('tn')} FN={cm.get('fn')}  "
              f"precyzja={_fmt_pct(cm.get('precision_pred_gt35'))} "
              f"recall={_fmt_pct(cm.get('recall_pred_gt35'))}")
    print()
    hdr = (f"{'wariant':11} {'flips':>6} {'słuszne':>8} {'false-rej':>9} "
           f"{'precyzja':>9} {'on-time PRZED':>13} {'PO(opt)':>9} {'PO(pess)':>9}")
    print(hdr)
    print("-" * len(hdr))
    for variant, v in res["variants"].items():
        a = v["agg"]
        print(f"{variant:11} {a['n_flips']:>6} {a['protected_breaches']:>8} "
              f"{a['freshness_regression_false_rejects']:>9} "
              f"{_fmt_pct(a['flip_precision']):>9} "
              f"{_fmt_pct(a['on_time_before']):>13} "
              f"{_fmt_pct(a['on_time_after_optimistic']):>9} "
              f"{_fmt_pct(a['on_time_after_pessimistic']):>9}")
    print()
    print("Legenda: flips = decyzje baseline-PASS które korekta zmienia na REJECT.")
    print("  słuszne = flip ordera który REALNIE był breach (>35) → ochrona świeżości.")
    print("  false-rej = flip ordera który REALNIE doszedł on-time → REGRESJA (wypchnięty).")
    print("  PO(opt) = słuszne flipy ratują breach, redirecty docierają na czas.")
    print("  PO(pess) = false-rejecty liczone jak utracona dostawa (KOORD/opóźnienie).")
    # Per-restaurant dla wariantu p80 (najsilniejszy = najwięcej flipów do oceny).
    pr = res["variants"].get("p80", {}).get("per_rest", {})
    rows = []
    for r, v in pr.items():
        if v["flips"] == 0:
            continue
        prec = (v["flip_correct"] / v["flips"]) if v["flips"] else None
        rows.append((r, v["n"], v["real_n"], v["real_breach"], v["flips"],
                     v["flip_correct"], v["flip_false"], prec, v["shift_example"]))
    rows.sort(key=lambda t: -t[4])
    if rows:
        print()
        print(f"Per restauracja (wariant p80, sort: liczba flipów) — top {top_n}:")
        print(f"  {'restauracja':32} {'n':>4} {'real_n':>6} {'brch':>4} "
              f"{'flip':>4} {'OK':>3} {'FALSE':>5} {'prec':>5} {'shift':>5}")
        for r, n, rn, rb, fl, fc, ff, prec, sh in rows[:top_n]:
            ps = f"{100*prec:.0f}%" if prec is not None else " n/a"
            print(f"  {r[:32]:32} {n:>4} {rn:>6} {rb:>4} {fl:>4} {fc:>3} {ff:>5} "
                  f"{ps:>5} {sh:>5.1f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--framing", choices=["table", "lfo", "both"], default="both")
    ap.add_argument("--variant", choices=VARIANTS + ["all"], default="all")
    ap.add_argument("--top-n", type=int, default=15)
    ap.add_argument("--matched-only", action="store_true",
                    help="tylko decyzje proposed_courier==final (projekcja R6 "
                         "odpowiada realnemu kurierowi)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    variants = VARIANTS if args.variant == "all" else [args.variant]
    framings = ["table", "lfo"] if args.framing == "both" else [args.framing]

    out = {}
    for fr in framings:
        res = run(framing=fr, variants=variants, matched_only=args.matched_only)
        out[fr] = res
        if not args.json:
            print_report(res, top_n=args.top_n)
            print()

    if args.json:
        # per-rest defaultdicts → dict dla serializacji
        def _clean(o):
            if isinstance(o, defaultdict):
                o = dict(o)
            if isinstance(o, dict):
                return {k: _clean(v) for k, v in o.items()}
            return o
        print(json.dumps(_clean(out), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
