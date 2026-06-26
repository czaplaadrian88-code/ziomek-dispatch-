#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministyczny router severity (offline, read‑only).
Ocenia ostatni dzień raportu daily_rule_report.json względem mediany z dni wcześniejszych.
"""

import argparse
import json
import os
import statistics
import sys
from typing import Any, Dict, List, Optional, Tuple

# ---------- ścieżki (stałe) ----------
REPORT_DEFAULT = "/root/.openclaw/workspace/scripts/logs/reports/daily_rule_report.json"
OUT_DEFAULT = "/root/.openclaw/workspace/scripts/logs/reports/severity_verdict.json"

# ---------- progi (jawne, łatwe do strojenia) ----------
# R6_ACTUAL
R6_ACTUAL_BREACH_ABS_P0   = 0.20   # realny R6-breach > 20% (obs 7-18%) -> P0 klasa 'r6_actual'
R6_ACTUAL_BREACH_DELTA_P1 = 0.05   # wzrost vs baseline > 5pp -> P1 'r6_actual'

# LATENCY
LATENCY_P95_ABS_P1_MS     = 3000.0 # p95 > 3000ms (obs 1600-2400) -> P1 'latency'
LATENCY_P95_REL_P2        = 1.30   # p95 > baseline*1.30 -> P2 'latency'

# KOORD
KOORD_DELTA_P2            = 0.05   # koord_rate wzrost > 5pp -> P2 'koord'

# FEASIBILITY
ZERO_FEASIBLE_DELTA_P2    = 0.05   # zero_feasible_rate wzrost > 5pp -> P2 'feasibility'

# FLEET_FAIRNESS
FLEET_GINI_ABS_P2         = 0.60   # gini > 0.60 (obs 0.25-0.69) -> P2 'fleet_fairness'
FLEET_GINI_DELTA_P3       = 0.10   # gini wzrost > 0.10 -> P3 'fleet_fairness'

# R6_PRED
R6_PRED_OVER35_DELTA_P3   = 0.10   # r6_pred_over35 wzrost > 10pp -> P3 'r6_pred'

# BEST_EFFORT
BEST_EFFORT_DELTA_P3      = 0.10   # best_effort wzrost > 10pp -> P3 'best_effort'


# ----------------------------------------------------------------------
def load_report(path: str) -> List[Dict[str, Any]]:
    """Wczytaj dzienny raport (lista dictów). Fail‑soft: brak / błędny => []."""
    if not os.path.isfile(path):
        print(f"[severity_router] brak pliku raportu: {path}", file=sys.stderr)
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        print("[severity_router] raport nie jest listą – ignoruję", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"[severity_router] błąd wczytywania {path}: {exc}", file=sys.stderr)
        return []


def _baseline(prev_rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    """Mediana nie‑None wartości `key` dla poprzednich dni (lub None)."""
    vals = [r[key] for r in prev_rows if key in r and r[key] is not None]
    if not vals:
        return None
    try:
        return statistics.median(vals)
    except statistics.StatisticsError:
        return None


def _severity_order(sev: str) -> int:
    """Liczbowy wskaźnik dla sortowania P0 < P1 < … < P4."""
    mapping = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    return mapping.get(sev, 99)


def evaluate_latest(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Przeprowadź ocenę najnowszego dnia.

    Zwraca dict z polami: date, baseline_days, low_confidence,
    issues (posortowane po severity), top_severity, wake_llm.
    W przypadku braku danych zwraca {'error': 'brak danych'}.
    """
    if not rows:
        return {"error": "brak danych"}

    # sortowanie po dacie (chronologicznie)
    sorted_rows = sorted(rows, key=lambda r: r.get("date", "0000-00-00"))
    latest = sorted_rows[-1]
    prev = sorted_rows[:-1]

    baseline_days = len(prev)
    low_confidence = baseline_days < 2

    issues: List[Dict[str, Any]] = []

    # ------ pomocnicze funkcje oceny reguł ------

    def _add_issue(cls: str, sev: str, metric: str,
                   value: Optional[float],
                   baseline: Optional[float],
                   delta: Optional[float],
                   note: str = ""):
        issues.append({
            "class": cls,
            "severity": sev,
            "metric": metric,
            "value": value,
            "baseline": baseline,
            "delta": delta,
            "note": note
        })

    # pobranie wartości dla najnowszego dnia (None‑safe)
    def _v(key: str) -> Optional[float]:
        return latest.get(key)

    # -- R6_ACTUAL --
    r6_breach = _v("r6_actual_breach_rate")
    r6_bl = _baseline(prev, "r6_actual_breach_rate")

    # P0: absolutny
    if r6_breach is not None and r6_breach > R6_ACTUAL_BREACH_ABS_P0:
        _add_issue("r6_actual", "P0", "r6_actual_breach_rate",
                   r6_breach, r6_bl, None,
                   f"przekroczony absolutny próg {R6_ACTUAL_BREACH_ABS_P0*100:.0f}%")

    # P1: delta vs baseline (tylko jeżeli nie mamy już P0 dla tego samego?)
    # (specyfikacja nie zabrania wielu wpisów, ale logika: sprawdzamy kolejno)
    if r6_breach is not None and r6_bl is not None:
        delta_r6 = r6_breach - r6_bl
        if delta_r6 > R6_ACTUAL_BREACH_DELTA_P1:
            _add_issue("r6_actual", "P1", "r6_actual_breach_rate",
                       r6_breach, r6_bl, delta_r6,
                       f"wzrost vs baseline > {R6_ACTUAL_BREACH_DELTA_P1*100:.0f}pp")

    # -- LATENCY --
    p95 = _v("latency_p95")
    p95_bl = _baseline(prev, "latency_p95")

    # P1: absolutny
    if p95 is not None and p95 > LATENCY_P95_ABS_P1_MS:
        _add_issue("latency", "P1", "latency_p95",
                   p95, p95_bl, None,
                   f"bezwzględny próg {LATENCY_P95_ABS_P1_MS} ms")

    # P2: relatywny (tylko jeżeli baseline istnieje)
    if p95 is not None and p95_bl is not None and p95_bl > 0:
        if p95 > p95_bl * LATENCY_P95_REL_P2:
            _add_issue("latency", "P2", "latency_p95",
                       p95, p95_bl, p95 - p95_bl,
                       f"przekroczony {LATENCY_P95_REL_P2:.2f}x baseline")

    # -- KOORD --
    koord = _v("koord_rate")
    koord_bl = _baseline(prev, "koord_rate")
    if koord is not None and koord_bl is not None:
        delta_koord = koord - koord_bl
        if delta_koord > KOORD_DELTA_P2:
            _add_issue("koord", "P2", "koord_rate",
                       koord, koord_bl, delta_koord,
                       f"wzrost > {KOORD_DELTA_P2*100:.0f}pp")

    # -- ZERO_FEASIBLE --
    zf = _v("zero_feasible_rate")
    zf_bl = _baseline(prev, "zero_feasible_rate")
    if zf is not None and zf_bl is not None:
        delta_zf = zf - zf_bl
        if delta_zf > ZERO_FEASIBLE_DELTA_P2:
            _add_issue("feasibility", "P2", "zero_feasible_rate",
                       zf, zf_bl, delta_zf,
                       f"wzrost > {ZERO_FEASIBLE_DELTA_P2*100:.0f}pp")

    # -- FLEET_FAIRNESS --
    gini = _v("fleet_gini_load")
    gini_bl = _baseline(prev, "fleet_gini_load")

    # P2: absolutny gini
    if gini is not None and gini > FLEET_GINI_ABS_P2:
        _add_issue("fleet_fairness", "P2", "fleet_gini_load",
                   gini, gini_bl, None,
                   f"gini > {FLEET_GINI_ABS_P2:.2f}")

    # P3: delta gini
    if gini is not None and gini_bl is not None:
        delta_gini = gini - gini_bl
        if delta_gini > FLEET_GINI_DELTA_P3:
            _add_issue("fleet_fairness", "P3", "fleet_gini_load",
                       gini, gini_bl, delta_gini,
                       f"gini wzrost > {FLEET_GINI_DELTA_P3}")

    # -- R6_PRED --
    r6p = _v("r6_pred_over35_rate")
    r6p_bl = _baseline(prev, "r6_pred_over35_rate")
    if r6p is not None and r6p_bl is not None:
        delta_r6p = r6p - r6p_bl
        if delta_r6p > R6_PRED_OVER35_DELTA_P3:
            _add_issue("r6_pred", "P3", "r6_pred_over35_rate",
                       r6p, r6p_bl, delta_r6p,
                       f"wzrost > {R6_PRED_OVER35_DELTA_P3*100:.0f}pp")

    # -- BEST_EFFORT --
    be = _v("best_effort_rate")
    be_bl = _baseline(prev, "best_effort_rate")
    if be is not None and be_bl is not None:
        delta_be = be - be_bl
        if delta_be > BEST_EFFORT_DELTA_P3:
            _add_issue("best_effort", "P3", "best_effort_rate",
                       be, be_bl, delta_be,
                       f"wzrost > {BEST_EFFORT_DELTA_P3*100:.0f}pp")

    # ----- uporządkowanie severity -----
    issues.sort(key=lambda i: _severity_order(i["severity"]))

    top_sev = "P4"
    if issues:
        top_sev = issues[0]["severity"]

    wake = top_sev in ("P0", "P1", "P2")

    return {
        "date": latest.get("date"),
        "baseline_days": baseline_days,
        "low_confidence": low_confidence,
        "issues": issues,
        "top_severity": top_sev,
        "wake_llm": wake,
    }


def print_verdict(v: Dict[str, Any]) -> None:
    """Czytelny wydruk werdyktu."""
    if "error" in v:
        print(f"[!!] Błąd: {v['error']}")
        return

    print("=" * 60)
    print(f"Data ostatniego dnia     : {v['date']}")
    print(f"Dni w baseline           : {v['baseline_days']}")
    conf = "NISKA" if v.get("low_confidence") else "OK"
    print(f"Pewność baseline         : {conf}")
    print(f"Top severity             : {v['top_severity']}")
    print(f"Wake LLM                 : {v['wake_llm']}")
    print("-" * 40)

    issues: List[Dict[str, Any]] = v.get("issues", [])
    if not issues:
        print("Brak anomalii (P4).")
    else:
        for i, iss in enumerate(issues, 1):
            delta_str = f"{iss['delta']:.4f}" if iss['delta'] is not None else "—"
            base_str = f"{iss['baseline']:.4f}" if iss['baseline'] is not None else "—"
            val_str = f"{iss['value']:.4f}" if iss['value'] is not None else "—"
            print(f"{i:2d}) {iss['severity']} [{iss['class']}] "
                  f"{iss['metric']} = {val_str} "
                  f"(baseline {base_str}, delta {delta_str}) "
                  f"{iss.get('note','')}")
    print("=" * 60)


def save_verdict(v: Dict[str, Any], path: str) -> None:
    """Zapisz werdykt do pliku JSON."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(v, fh, indent=1, ensure_ascii=False, default=str)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Severity router – offline analiza daily_rule_report.json"
    )
    parser.add_argument("--report", default=REPORT_DEFAULT,
                        help="ścieżka do daily_rule_report.json")
    parser.add_argument("--out", default=OUT_DEFAULT,
                        help="ścieżka do severity_verdict.json")
    parser.add_argument("--no-save", action="store_true",
                        help="nie zapisuj pliku wynikowego")
    args = parser.parse_args()

    rows = load_report(args.report)
    verdict = evaluate_latest(rows)
    print_verdict(verdict)

    if not args.no_save and "error" not in verdict:
        save_verdict(verdict, args.out)
        print(f"Zapisano wynik do {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
