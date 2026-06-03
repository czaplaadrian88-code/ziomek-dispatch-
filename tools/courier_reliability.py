#!/usr/bin/env python3
"""courier_reliability.py — OFFLINE feed niezawodności kuriera (pętla uczenia Fazy 1).

CEL (mapa autonomii 2026-06-03, Faza 1):
Produkuje SKALIBROWANY profil niezawodności per kurier z REALNYCH dostaw, który
zasili soft-score selekcji ("komu zaufać przy wyborze"). Każdy wynik liczony jest
z tego, co realnie się wydarzyło — zero magic-numberów.

To NIE jest raport — to FEED (kontrakt JSON), który inni czytają:
  dispatch_state/courier_reliability.json
Schemat jest SZTYWNY (patrz docstring main / nagłówek output) — nie zmieniaj kluczy.

Atrybucja wyniku: do REALNEGO wykonawcy = outcome.courier_id_final (NIE proposed,
NIE actual_courier_id — to ostatnie populowane tylko przy override, artefakt).
BREACH = outcome.pickup_to_delivery_min > 35.0 (twarda reguła R6 / R-35MIN-MAX).

reliability: kompozyt, WYŻSZY = LEPSZY. Karze nadprzeciętny breach (vs mediana floty)
i powolność względem predykcji. confidence wg liczności próby (n).

Pokrewne w pętli: retro_learning.py (A2 też profiluje kurierów, ale szerzej);
ten skrypt jest WĄSKI i STABILNY — jeden kontrakt, jeden konsument (selekcja).

READ-ONLY. Zero wpływu na produkcję. Zero zależności poza stdlib (Z2/Z3).

Uruchom:
  /root/.openclaw/venvs/dispatch/bin/python tools/courier_reliability.py
Opcje: --min-history N (próg dostaw per kurier, default 5), --json-only
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
OUT_JSON = "/root/.openclaw/workspace/dispatch_state/courier_reliability.json"

R6_HARD_MAX = 35.0          # BAG_TIME_HARD_MAX_MIN — twarda reguła dostawy (breach > 35)


# ───────────────────────── helpers ─────────────────────────

def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _median(a):
    return statistics.median(a) if a else None


def _confidence(n):
    """Pewność profilu wg liczności realnych dostaw."""
    if n >= 20:
        return "high"
    if n >= 10:
        return "medium"
    return "low"   # tu trafia tylko n w [min_history, 9] bo niżej odfiltrowane


def load():
    """Wczytaj backfill (decyzja + realny outcome). Zwraca listę dictów."""
    if not os.path.exists(BACKFILL):
        print(f"BRAK pliku: {BACKFILL}", file=sys.stderr)
        return []
    rows = []
    with open(BACKFILL, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _delivered(rows):
    """Tylko wiersze z dostarczonym outcome."""
    out = []
    for r in rows:
        o = r.get("outcome") or {}
        if o.get("status") == "delivered":
            out.append(r)
    return out


def _breach_rate_loo(p2d):
    """Leave-one-out breach rate (diagnostyka stabilności breach_rate).

    Dla każdego wiersza i: liczymy breach_rate na pozostałych n-1 dostawach
    (z wyłączeniem i), a następnie uśredniamy te per-wierszowe wskaźniki.

    Algebraicznie to się upraszcza, ale liczymy wprost dla czytelności i
    odporności na przyszłe zmiany:
      - jeśli i był breachem  → rate_i = (B-1)/(n-1)
      - jeśli i NIE był       → rate_i =  B   /(n-1)
    gdzie B = łączna liczba breachy, n = liczba dostaw.
    Średnia z rate_i jest nieco niższa od full-sample breach_rate (każdy breach
    "usuwa sam siebie" raz) — to oczekiwane i właśnie dlatego trzymamy ją osobno
    jako sygnał diagnostyczny, NIE jako wartość produkcyjną.
    """
    n = len(p2d)
    if n < 2:
        return None
    flags = [1 if v > R6_HARD_MAX else 0 for v in p2d]
    B = sum(flags)
    rates = []
    for f in flags:
        rates.append((B - f) / (n - 1))
    return statistics.mean(rates)


# ───────────────────────── core ─────────────────────────

def build_profiles(rows, min_history):
    """Zbuduj profile per outcome.courier_id_final dla n_delivered >= min_history."""
    deliv = _delivered(rows)
    by_courier = defaultdict(list)        # cid -> [row, ...]
    for r in deliv:
        cid = str((r.get("outcome") or {}).get("courier_id_final") or "")
        if cid and cid != "None":
            by_courier[cid].append(r)

    # Najpierw policz surowe metryki (bez reliability — wymaga mediany floty).
    raw = {}
    for cid, rs in by_courier.items():
        if len(rs) < min_history:
            continue
        p2d = [r["outcome"]["pickup_to_delivery_min"] for r in rs
               if _num(r["outcome"].get("pickup_to_delivery_min"))]
        if not p2d:
            continue
        speed_resid = [r["outcome"]["pickup_to_delivery_min"] - r["predicted_drive_min"]
                       for r in rs
                       if _num(r["outcome"].get("pickup_to_delivery_min"))
                       and _num(r.get("predicted_drive_min"))]
        n_b = sum(1 for v in p2d if v > R6_HARD_MAX)
        raw[cid] = {
            "n_delivered": len(rs),
            "breach_rate": n_b / len(p2d),
            "breach_rate_loo": _breach_rate_loo(p2d),
            "median_pickup_to_delivery": _median(p2d),
            "speed_vs_pred_median": _median(speed_resid) if speed_resid else None,
        }

    # Mediany floty (po kurierach) — baza do kompozytu reliability.
    fleet_breach = _median([v["breach_rate"] for v in raw.values()])
    fleet_speed = _median([v["speed_vs_pred_median"] for v in raw.values()
                           if v["speed_vs_pred_median"] is not None])
    fleet_breach = fleet_breach if fleet_breach is not None else 0.0
    fleet_speed_for_calc = fleet_speed if fleet_speed is not None else 0.0

    # reliability: WYŻSZY = LEPSZY. Karze nadprzeciętny breach (vs mediana floty)
    # oraz powolność względem predykcji (tylko gdy wolniejszy niż przewidziano).
    profiles = {}
    for cid, m in raw.items():
        br = m["breach_rate"]
        spd = m["speed_vs_pred_median"] or 0.0
        reliability = 1.0 - (br - fleet_breach) - 0.02 * max(0.0, spd)
        profiles[cid] = {
            "n_delivered": m["n_delivered"],
            "breach_rate": round(br, 3),
            "breach_rate_loo": round(m["breach_rate_loo"], 3) if m["breach_rate_loo"] is not None else None,
            "median_pickup_to_delivery": round(m["median_pickup_to_delivery"], 1),
            "speed_vs_pred_median": round(m["speed_vs_pred_median"], 1) if m["speed_vs_pred_median"] is not None else None,
            "reliability": round(reliability, 3),
            "confidence": _confidence(m["n_delivered"]),
        }

    # sortuj po reliability malejąco (LEPSI najpierw)
    profiles = dict(sorted(profiles.items(), key=lambda kv: -kv[1]["reliability"]))
    return profiles, round(fleet_breach, 3), (round(fleet_speed, 1) if fleet_speed is not None else 0.0)


# ───────────────────────── raport ─────────────────────────

def print_report(payload):
    P = print
    meta = payload["meta"]
    profiles = payload["couriers"]
    P("=" * 74)
    P(f"  NIEZAWODNOŚĆ KURIERÓW — z {meta['n_delivered']} realnych dostaw "
      f"(z {meta['n_decisions']} decyzji)")
    P(f"  Atrybucja: outcome.courier_id_final | BREACH = dostawa > {int(R6_HARD_MAX)} min | "
      f"min_history={meta['min_history']}")
    P("=" * 74)
    P(f"\n  {'cid':<8}{'n':>4}{'breach%':>9}{'vs_pred':>9}{'reliab':>9}{'conf':>9}")
    P("  " + "-" * 46)
    for cid, p in profiles.items():
        spd = p["speed_vs_pred_median"]
        spd_s = ("+" if (spd or 0) >= 0 else "") + (str(spd) if spd is not None else "—")
        P(f"  {cid:<8}{p['n_delivered']:>4}"
          f"{str(int(round(p['breach_rate'] * 100))) + '%':>9}"
          f"{spd_s:>9}"
          f"{str(p['reliability']):>9}"
          f"{p['confidence']:>9}")
    P("  " + "-" * 46)
    P(f"  Flota: mediana breach = {int(round(payload['fleet_median_breach_rate'] * 100))}%  "
      f"| mediana vs_pred = {payload['fleet_median_speed_vs_pred']} min  "
      f"| kurierów w profilu = {len(profiles)}")
    P("=" * 74)


# ───────────────────────── main ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Offline feed niezawodności kuriera (Faza 1).")
    ap.add_argument("--min-history", type=int, default=5,
                    help="min liczba realnych dostaw per kurier (default 5)")
    ap.add_argument("--json-only", action="store_true",
                    help="tylko zapis JSON, bez raportu na stdout")
    args = ap.parse_args()

    rows = load()
    if not rows:
        print("Brak danych.", file=sys.stderr)
        return 1

    profiles, fleet_breach, fleet_speed = build_profiles(rows, args.min_history)

    # KONTRAKT JSON (sztywny — czytają inni):
    payload = {
        "meta": {
            "generated_from": BACKFILL,
            "n_decisions": len(rows),
            "n_delivered": len(_delivered(rows)),
            "min_history": args.min_history,
        },
        "fleet_median_breach_rate": fleet_breach,
        "fleet_median_speed_vs_pred": fleet_speed,
        "couriers": profiles,
    }

    # atomic write: tempfile + fsync + os.replace
    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT_JSON)

    if not args.json_only:
        print_report(payload)
    print(f"\n✓ Feed zapisany: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
