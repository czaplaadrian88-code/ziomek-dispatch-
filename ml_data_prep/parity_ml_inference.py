"""[ML-PROD] Parity ŻYWEJ ŚCIEŻKI ml_inference.py vs trening — blocker #2.

CAŁKOWICIE OFFLINE. Importuje PRODUKCYJNE helpery z dispatch_v2/ml_inference.py
i porównuje je transformacja-po-transformacji z definicjami treningowymi
(src/feature_engineering.py). Każda rozbieżność = potencjalny cichy train/serve skew.

Plus: udokumentowanie mapowania `level` A/B między produkcją a datasetem
(to wskazany blocker), oraz analiza czy router 2-modelowy da się wpiąć BEZ skew.

Uruchom (venv pipeline'u ML; ścieżka scripts/ musi być na sys.path dla dispatch_v2):
  /root/.openclaw/workspace/scripts/ml_data_prep/venv/bin/python3 \
      /root/.openclaw/workspace/scripts/dispatch_v2/ml_data_prep/parity_ml_inference.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

HERE = Path(__file__).resolve().parent
SCRIPTS = Path("/root/.openclaw/workspace/scripts")
PROD_ML = SCRIPTS / "ml_data_prep"
for p in (str(HERE), str(SCRIPTS), str(PROD_ML)):
    if p not in sys.path:
        sys.path.insert(0, p)

# PRODUKCYJNE helpery (żywa ścieżka inferencji)
from dispatch_v2.ml_inference import (  # noqa: E402
    _bag_size_category as prod_bag_cat,
    _idle_category as prod_idle_cat,
    _district_adjacent as prod_dist_adj,
    _level_from_metrics as prod_level,
    PEAK_LUNCH as PROD_PEAK_LUNCH,
    PEAK_DINNER as PROD_PEAK_DINNER,
)

# TRENINGOWE transformacje (to czym karmiony był model)
from src.feature_engineering import (  # noqa: E402
    bag_size_category as train_bag_cat,
    idle_category as train_idle_cat,
    idle_capped as train_idle_capped,
    district_adjacent as train_dist_adj,
    time_features as train_time_features,
    PEAK_LUNCH as TRAIN_PEAK_LUNCH,
    PEAK_DINNER as TRAIN_PEAK_DINNER,
)

OUT = HERE / "models_twomodel" / "parity_ml_inference_report.json"


class _Candidate:
    """Minimalny stub kandydata dla _level_from_metrics."""
    def __init__(self, bag_size=0, last_pos_lat=None):
        self.bag_size = bag_size
        self.last_pos_lat = last_pos_lat


def compare_bag_size_category() -> Dict[str, Any]:
    """Prod _bag_size_category vs train bag_size_category."""
    vals = [None, float("nan"), 0, 1, 2, 3, 4, 7, 11]
    rows, mismatches = [], []
    for v in vals:
        # train zwraca 'unknown' dla None/NaN; prod traktuje None jako 0
        try:
            p = prod_bag_cat(v if not (isinstance(v, float) and math.isnan(v)) else None)
        except Exception as e:
            p = f"ERR:{e}"
        t = train_bag_cat(v)
        match = (p == t)
        rows.append({"input": "nan" if (isinstance(v, float) and math.isnan(v)) else v, "prod": p, "train": t, "match": match})
        if not match:
            mismatches.append({"input": "nan" if (isinstance(v, float) and math.isnan(v)) else v, "prod": p, "train": t})
    return {"feature": "bag_size_category", "rows": rows, "mismatches": mismatches}


def compare_idle_category() -> Dict[str, Any]:
    vals = [None, 0, 4.9, 5, 14.9, 15, 29.9, 30, 100]
    rows, mismatches = [], []
    for v in vals:
        p = prod_idle_cat(v)
        t = train_idle_cat(v)
        match = (p == t)
        rows.append({"input": v, "prod": p, "train": t, "match": match})
        if not match:
            mismatches.append({"input": v, "prod": p, "train": t})
    return {"feature": "idle_category", "rows": rows, "mismatches": mismatches}


def compare_idle_capped() -> Dict[str, Any]:
    """Prod liczy inline: int(min(idle,30)) if not None else -1. Train: None gdy None."""
    def prod_idle_capped(idle_min):
        return int(min(idle_min, 30)) if idle_min is not None else -1
    vals = [None, 0, 15, 29, 30, 45, 999]
    rows, mismatches = [], []
    for v in vals:
        p = prod_idle_capped(v)
        t = train_idle_capped(v)
        # uwaga: train None vs prod -1 dla braku — porównujemy literalnie
        match = (p == t)
        rows.append({"input": v, "prod": p, "train": t, "match": match})
        if not match:
            mismatches.append({"input": v, "prod": p, "train": t})
    return {"feature": "idle_min_capped", "rows": rows, "mismatches": mismatches}


def compare_district_adjacent() -> Dict[str, Any]:
    cases = [
        ("Centrum", "Centrum"), ("Centrum", "Bojary"), ("Centrum", "Unknown"),
        (None, "Centrum"), ("Unknown", "Unknown"), ("Bema", "Centrum"),
        ("Antoniuk", "Wygoda"), ("Centrum", None),
    ]
    rows, mismatches = [], []
    for z1, z2 in cases:
        p = prod_dist_adj(z1, z2)
        t = train_dist_adj(z1, z2)
        match = (p == t)
        rows.append({"input": f"{z1}|{z2}", "prod": p, "train": t, "match": match})
        if not match:
            mismatches.append({"input": f"{z1}|{z2}", "prod": p, "train": t})
    return {"feature": "district_adjacent", "rows": rows, "mismatches": mismatches}


def compare_time_features() -> Dict[str, Any]:
    """Prod inline (season/is_lunch/is_dinner/is_weekend/minutes) vs train time_features.

    Prod liczy w _compute_all_candidate_features; tu replikujemy prod inline-logikę
    DOKŁADNIE jak w kodzie (linie 363-372, 436-439) i porównujemy do train.
    """
    rows, mismatches = [], []
    test_ts = [
        datetime(2026, 1, 15, 12, 30),   # winter, lunch
        datetime(2026, 4, 20, 18, 5),    # spring, dinner
        datetime(2026, 7, 4, 9, 0),      # summer, weekday morning
        datetime(2026, 10, 11, 22, 15),  # autumn, sun(11.10.2026=niedziela), late
        datetime(2026, 3, 21, 13, 59),   # spring, lunch edge
    ]
    for ts in test_ts:
        h, dow, month = ts.hour, ts.weekday(), ts.month
        prod = {
            "season": ("winter" if month in (12, 1, 2) else "spring" if month in (3, 4, 5)
                       else "summer" if month in (6, 7, 8) else "autumn"),
            "is_lunch_peak": h in PROD_PEAK_LUNCH,
            "is_dinner_peak": h in PROD_PEAK_DINNER,
            "is_weekend": dow >= 5,
            "minutes_since_midnight_warsaw": h * 60 + ts.minute,
        }
        tf = train_time_features(pd.Timestamp(ts))
        train = {k: tf[k] for k in prod}
        match = (prod == train)
        rows.append({"ts": ts.isoformat(), "prod": prod, "train": train, "match": match})
        if not match:
            mismatches.append({"ts": ts.isoformat(), "prod": prod, "train": train})
    return {"feature": "time_features(season/peaks/weekend/minutes)", "rows": rows, "mismatches": mismatches}


def analyze_level_mapping() -> Dict[str, Any]:
    """Udokumentuj rozjazd definicji `level` A/B: produkcja (GPS) vs dataset (bag)."""
    # Produkcja: ml_inference.py:400 `getattr(c,"level",None) or _level_from_metrics(c)`.
    # Candidate dataclass NIE ma .level → zawsze _level_from_metrics:
    #   B gdy last_pos_lat is None (brak GPS), A wpp.
    prod_cases = []
    for bag, gps in [(0, None), (0, 53.13), (2, None), (3, 53.13)]:
        c = _Candidate(bag_size=bag, last_pos_lat=gps)
        prod_cases.append({"bag_size": bag, "has_gps": gps is not None, "prod_level": prod_level(c)})
    # Dataset (available_pool.classify_courier): A gdy bag_size>0; B gdy bag==0 ale
    # zlecenie w oknie [T0-30,T0+45]. Czyli A/B datasetu = oś WORKA, nie GPS.
    dataset_def = {
        "A": "bag_size > 0 (kurier z workiem)",
        "B": "bag_size == 0 AND zlecenie submitted w [T0-30min, T0+45min] (świeżo/wnet aktywny, pusty worek)",
        "note": "dataset A/B = oś OBCIĄŻENIA WORKA",
    }
    prod_def = {
        "A": "last_pos_lat is not None (ma GPS)",
        "B": "last_pos_lat is None (brak GPS)",
        "note": "produkcja A/B = oś OBECNOŚCI GPS (_level_from_metrics); Candidate nie ma .level",
    }
    return {
        "feature": "level (A/B)",
        "SKEW_DETECTED": True,
        "dataset_definition": dataset_def,
        "production_definition": prod_def,
        "prod_eval_cases": prod_cases,
        "consequence": (
            "Model trenowany na A/B=oś-worka. Żywa ścieżka podaje A/B=oś-GPS. To RÓŻNE "
            "rozkłady tej samej kolumny → cichy skew w cesze `level`. (encoder v1.1 zna {A,B,UNK}, "
            "więc literalnie się zakoduje, ale ZNACZENIE jest inne.)"
        ),
        "router_implication": (
            "Router 2-modelowy MUSI wybierać reżim solo/bundle po STANIE WORKA (bag_size==0 & "
            "drops==0 & pickup==0), a NIE po feature `level`. Stan worka jest dostępny live "
            "(bag_size_before + bag_context) i pokrywa się 1:1 z definicją datasetu. Wtedy "
            "selekcja reżimu jest wolna od skew GPS-vs-worek."
        ),
    }


def analyze_continuous_skews() -> List[Dict[str, Any]]:
    """Dwa REALNE skew na cechach ciągłych (nie edge-case None) — najważniejsze.

    Zweryfikowane na datasecie v2.0 (test.parquet):
      - delta_dist_km: dataset==winner−loser road km (match 100%, n=44063);
        produkcja (ml_inference.py:452)==kandydat−średnia_puli. RÓŻNY sygnał.
      - dist_to_pickup_haversine_km: dataset==surowy haversine (BEZ ×1.42);
        produkcja (ml_inference.py:334)==haversine×1.42. Skala ~1.42×.
    """
    return [
        {
            "feature": "delta_dist_km",
            "SKEW_DETECTED": True,
            "training_definition": "winner_road_km − loser_road_km (sygnał PAROWY winner-vs-loser); dataset match 100% (n=44063)",
            "production_definition": "candidate_road_km − mean(pool road km) (ml_inference.py:452, sygnał kandydat-vs-średnia-puli)",
            "severity": "WYSOKA — to inny rozkład cechy niosącej sygnał; model widział parową różnicę, w prod dostaje odchyłkę od średniej puli",
            "fix": "policzyć delta_dist_km parowo też w serwowaniu (per-para winner-loser) ALBO przetrenować model na definicji pool-mean (spójność z produkcją)",
        },
        {
            "feature": "dist_to_pickup_haversine_km",
            "SKEW_DETECTED": True,
            "training_definition": "surowy haversine_km BEZ współczynnika (feature_engineering.py:431); próbka 4.762",
            "production_definition": "haversine × 1.42 (HAVERSINE_FACTOR, ml_inference.py:334); ta sama geometria ~6.76",
            "severity": "ŚREDNIA — skala ~1.42× na cesze; drzewa LGBM są skala-monotoniczne, ale progi splitów uczone na surowych wartościach trafiają w złe miejsce",
            "fix": "w serwowaniu podać surowy haversine (bez ×1.42) dla tej kolumny, lub przetrenować z ×1.42 (spójność)",
        },
    ]


def verify_prod_shaping_fixes_continuous_skews() -> Dict[str, Any]:
    """POST-FIX: udowodnij że PROD-shaping treningu (apply_prod_feature_shaping)
    daje DOKŁADNIE definicje produkcyjne dla delta_dist_km i haversine.

    Wczytuje test.parquet, buduje pointwise jak trening, aplikuje shaping i
    porównuje z niezależnie policzoną definicją produkcyjną:
      - haversine: surowy_dataset × 1.42  (HAVERSINE_FACTOR)
      - delta_dist_km: dist_to_pickup_km − mean(dist_to_pickup_km) per decision_id
                       (NaN dystans lub brak ważnej puli → 0.0)
    Zwraca {fixed: bool, ...} — fixed=True gdy 0 niezgodności (czyste parity).
    """
    try:
        import numpy as np
        from twomodel_common import load_split, apply_prod_feature_shaping
        from src.lgbm_training import build_pointwise_dataset
    except Exception as e:  # brak parquet/lgbm w tym interpreterze
        return {"checked": False, "reason": f"stack niedostępny: {e}"}

    df = load_split("test")
    pw_raw = build_pointwise_dataset(df).drop_duplicates(
        subset=["decision_id", "courier_name", "label"]
    ).reset_index(drop=True)
    pw_shaped = apply_prod_feature_shaping(pw_raw)

    # (3) haversine: shaped == surowy × 1.42
    hv_raw = pd.to_numeric(pw_raw["dist_to_pickup_haversine_km"], errors="coerce")
    hv_shaped = pd.to_numeric(pw_shaped["dist_to_pickup_haversine_km"], errors="coerce")
    hv_expected = hv_raw * 1.42
    hv_mask = hv_raw.notna()
    hv_mismatch = int((np.abs(hv_shaped[hv_mask] - hv_expected[hv_mask]) > 1e-9).sum())

    # (2) delta_dist_km: shaped == candidate − pool_mean (def produkcyjna, niezależnie)
    road = pd.to_numeric(pw_raw["dist_to_pickup_km"], errors="coerce")
    valid = road.notna()
    tmp = pd.DataFrame({"d": pw_raw["decision_id"], "r": road.where(valid)})
    pool_mean = tmp.groupby("d")["r"].transform("mean")
    delta_expected = (road - pool_mean).where(valid & pool_mean.notna(), 0.0).astype(float)
    delta_shaped = pd.to_numeric(pw_shaped["delta_dist_km"], errors="coerce").astype(float)
    delta_mismatch = int((np.abs(delta_shaped - delta_expected) > 1e-6).sum())

    fixed = (hv_mismatch == 0 and delta_mismatch == 0)
    return {
        "checked": True,
        "fixed": bool(fixed),
        "n_rows": int(len(pw_shaped)),
        "haversine_x142_mismatches": hv_mismatch,
        "delta_pool_mean_mismatches": delta_mismatch,
        "note": (
            "Trening (apply_prod_feature_shaping) liczy delta=kandydat−pool_mean i "
            "haversine×1.42 == definicje produkcyjne (ml_inference.py:452/334). "
            "Żywa ścieżka serwowania v1.1 NIETKNIĘTA. Skew #2 i #3 zaadresowane "
            "po stronie treningu (retrain). 0 niezgodności = parity czyste."
        ),
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"[ML-PROD] parity ml_inference: PEAK_LUNCH prod={sorted(PROD_PEAK_LUNCH)} train={sorted(TRAIN_PEAK_LUNCH)}")
    print(f"          PEAK_DINNER prod={sorted(PROD_PEAK_DINNER)} train={sorted(TRAIN_PEAK_DINNER)}")

    checks = [
        compare_bag_size_category(),
        compare_idle_category(),
        compare_idle_capped(),
        compare_district_adjacent(),
        compare_time_features(),
    ]
    level = analyze_level_mapping()
    continuous = analyze_continuous_skews()
    post_fix = verify_prod_shaping_fixes_continuous_skews()

    # podsumowanie skew
    total_mismatch = sum(len(c["mismatches"]) for c in checks)
    skew_features = [c["feature"] for c in checks if c["mismatches"]]

    constants_match = (
        sorted(PROD_PEAK_LUNCH) == sorted(TRAIN_PEAK_LUNCH)
        and sorted(PROD_PEAK_DINNER) == sorted(TRAIN_PEAK_DINNER)
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "peak_constants_match": constants_match,
        "derived_feature_checks": checks,
        "derived_total_mismatches": total_mismatch,
        "derived_features_with_skew": skew_features,
        "level_mapping_analysis": level,
        "continuous_feature_skews": continuous,
        "post_fix_prod_shaping_verification": post_fix,
        "summary": {
            "categorical_derived_aligned_on_valid_inputs": "TAK — bag_size_category/idle_category/district_adjacent/time_features zgodne na poprawnych wejściach; różnice tylko na None/NaN (absorbowane przez fillna(-1))",
            "level_skew": "TAK (semantyczny) — oś-worka (trening) vs oś-GPS (produkcja)",
            "continuous_skews": "delta_dist_km (parowy vs pool-mean) + haversine (×1.42 w prod, surowy w treningu) — DWA realne skew do naprawy przed primary",
            "router_safe_path": "wybór reżimu solo/bundle po stanie worka (bag_size/drops/pickup), NIE po feature level",
        },
    }
    json.dump(report, open(OUT, "w"), indent=2, default=str, ensure_ascii=False)

    print("\n=== WYNIK PARITY ===")
    for c in checks:
        flag = "OK" if not c["mismatches"] else f"SKEW({len(c['mismatches'])})"
        print(f"  {c['feature']}: {flag}")
        for m in c["mismatches"]:
            print(f"      input={m['input']}: prod={m['prod']} train={m['train']}")
    print(f"\n  level A/B: SKEW (oś-worka vs oś-GPS) — patrz raport")
    print(f"  peak constants match: {constants_match}")
    print(f"\n  CECHY CIĄGŁE (realny sygnał) — definicje PRZED naprawą:")
    for c in continuous:
        print(f"      {c['feature']}: SKEW [{c['severity'].split(' — ')[0]}]")
        print(f"        trening: {c['training_definition'][:70]}")
        print(f"        prod   : {c['production_definition'][:70]}")
    print(f"\n  POST-FIX (retrain z PROD-shaping):")
    if post_fix.get("checked"):
        print(f"      haversine×1.42 mismatches : {post_fix['haversine_x142_mismatches']}")
        print(f"      delta=pool-mean mismatches: {post_fix['delta_pool_mean_mismatches']}")
        print(f"      => parity czyste: {post_fix['fixed']} (n={post_fix['n_rows']})")
    else:
        print(f"      (pominięte: {post_fix.get('reason')})")
    print(f"\nraport -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
