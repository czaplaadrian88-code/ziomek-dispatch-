"""[E3] Wspólne definicje dla dwóch wyspecjalizowanych modeli LGBM (solo / bundle).

CAŁKOWICIE OFFLINE. Zero wpływu na żywy silnik. Ten moduł NIE jest importowany
przez żaden produkcyjny moduł — jest używany wyłącznie przez:
  - ml_data_prep/train_two_models.py   (trening + ewaluacja)
  - tests/test_ml_twomodel.py          (parity train-vs-serve)

Kontekst (audyt): jeden LGBM Ranker daje pairwise 88.45% globalnie, ale dla
decyzji SOLO (winner z pustym workiem) zapada się do ~44.6% — gorzej niż losowo.
Hipoteza: w pointwise-reframe cechy ładunku worka (bag_*) winner-a w trybie solo
są STAŁE (0), więc model uczy się skrótu "pusty worek => wygrana", który nie
generalizuje. Rozwiązanie: dwa modele — solo (bez cech worka) i bundle (pełny zestaw).

Definicja SOLO vs BUNDLE (na poziomie decyzji, klucz = stan WINNER-a):
  SOLO   = winner ma pusty worek: bag_size==0 AND bag_drops_pending==0
           AND bag_pickup_pending==0   (== winner_level "B" w datasecie v2.0)
  BUNDLE = winner ma cokolwiek w worku / pending (== winner_level "A")

Źródło danych (read-only, NIE nadpisujemy): datasety produkcyjne v2.0.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

# ── Ścieżki (read-only wejście) ──────────────────────────────────────────────
# Produkcyjny pipeline ML leży OBOK dispatch_v2 (w scripts/ml_data_prep).
PROD_ML_DIR = Path("/root/.openclaw/workspace/scripts/ml_data_prep")
DATASET_DIR = PROD_ML_DIR / "data" / "datasets" / "v2.0"
COURIER_TIERS_PATH = Path("/root/.openclaw/workspace/dispatch_state/courier_tiers.json")

# ── Cechy "bundlowe" usuwane z modelu SOLO ──────────────────────────────────
# To cechy opisujące ŁADUNEK worka kandydata. W trybie solo winner ma pusty
# worek => te kolumny są dla niego deterministycznie 0/False (zero wariancji po
# stronie winner-a). Trzymanie ich daje modelowi skrót, który nie generalizuje.
# Usuwamy je dla OBU stron (winner+loser), bo decyzja solo = "który świeży
# kurier najlepiej obsłuży ten pojedynczy odbiór" — skład worka jest nieistotny.
# Nazwy bazowe (po zdjęciu prefiksu winner_/loser_ w pointwise-reframe):
BUNDLE_ONLY_BASE_FEATURES: List[str] = [
    "bag_size",
    "bag_drops_pending",
    "bag_pickup_pending",
    "bag_size_category",
    "bag_n_distinct_districts",
    "bag_has_distant_drop",
]

# ── Cechy „rekonstrukcyjne" NIEdostępne w żywym dispatchu (usuwane z OBU modeli) ─
# Produkcyjny v1.1 zdjął te 7 cech w hot-swapie „Faza 5.1" (ml_inference.py:
# GROUP_A_DEFAULTS={}) DOKŁADNIE dlatego, że da się je policzyć tylko przy
# rekonstrukcji historii, a w żywej inferencji ich NIE ma. Dwumodel MUSI uczyć
# się wyłącznie cech serwowalnych live — inaczej wprowadzamy nowy train/serve
# skew (model widzi realne wartości w treningu, dostaje stałe -1 w serwowaniu).
# Decyzja: [[lgbm-twomodel-prod-skew-2026-06-20]].
RECON_ONLY_DECISION_FEATURES: List[str] = [
    "level_A_count",
    "level_B_count",
    "level_C_excluded_count",
    "exclude_virtual",
    "exclude_historical",
    "exclude_not_active",
    "exclude_low_day",
]

# Kategoryczna cecha tieru/poziomu kandydata. W produkcji (`ml_inference.py`)
# `level` mapuje się na `cs_tier_label` (gold/std+/std/slow/new). W datasecie
# v2.0 `level` ma tylko A/B (poziom dostępności rekonstrukcji). Zadanie: zamiast
# label-encodingu (porządkowego) — ONE-HOT. Robimy to niezależnie od kardynalności.
TIER_ORD_COL = "level"

# ── PROD-shaping cech ciągłych (naprawa train/serve skew #2 i #3) ────────────
# Żywa ścieżka serwowania (`dispatch_v2/ml_inference.py`) liczy DWIE cechy ciągłe
# inaczej niż surowy dataset v2.0. Aby NOWY dwumodel nauczył się DOKŁADNIE tego,
# co dostanie w produkcji (a żywej, wdrożonej ścieżki v1.1 NIE ruszać), shapujemy
# cechy w treningu do definicji produkcyjnych. Decyzja+dowód: memory
# [[lgbm-twomodel-prod-skew-2026-06-20]].
#
#   skew #3 (haversine): prod = haversine × 1.42 (HAVERSINE_FACTOR), dataset = surowy.
#   skew #2 (delta_dist_km): prod = kandydat_road − średnia_puli(road) (pointwise),
#                            dataset = winner_road − loser_road (parowy, NIE-odtwarzalny
#                            pointwise przy inferencji). Pool-mean = średnia
#                            `dist_to_pickup_km` po `decision_id` w pointwise.
HAVERSINE_FACTOR_BIALYSTOK = 1.42  # = ml_inference.py / src.feature_engineering


def apply_prod_feature_shaping(pw: "pd.DataFrame") -> "pd.DataFrame":
    """Przekształć cechy ciągłe pointwise do definicji PRODUKCYJNYCH (parity z serwowaniem).

    Wejście: pointwise DataFrame PO build_pointwise (kolumny już bez prefiksu
    winner_/loser_): wymaga `dist_to_pickup_km`, `dist_to_pickup_haversine_km`,
    `decision_id` oraz (opcjonalnie) `delta_dist_km`.

    Operacje (idempotentne na poziomie wartości — patrz flagi-markery niżej):
      1. haversine: surowy → ×1.42 (jeśli kolumna obecna i jeszcze nie shapowana).
      2. delta_dist_km: parowy → kandydat − pool_mean(dist_to_pickup_km) per decision_id.
         Braki dystansu (NaN) → delta 0.0 (jak produkcja: `else 0.0`).

    Zwraca NOWY DataFrame (kopię). NIE modyfikuje wejścia.
    """
    import numpy as _np

    df = pw.copy()
    # (3) haversine ×1.42 — produkcja mnoży surowy haversine przez HAVERSINE_FACTOR.
    hv = "dist_to_pickup_haversine_km"
    if hv in df.columns:
        col = pd.to_numeric(df[hv], errors="coerce")
        df[hv] = col * HAVERSINE_FACTOR_BIALYSTOK

    # (2) delta_dist_km = kandydat_road − pool_mean(road) per decyzja (definicja prod).
    if "dist_to_pickup_km" in df.columns and "decision_id" in df.columns:
        road = pd.to_numeric(df["dist_to_pickup_km"], errors="coerce")
        # Produkcja: pool_mean liczona z dystansów ważnych (nie-NaN) w puli.
        valid = road.notna()
        tmp = pd.DataFrame({"decision_id": df["decision_id"], "_road": road})
        # mean tylko po ważnych; transform zachowuje indeks wierszy
        pool_mean = tmp.assign(_road=tmp["_road"].where(valid)).groupby(
            "decision_id"
        )["_road"].transform("mean")
        delta = road - pool_mean
        # produkcja: gdy kandydat NaN lub brak ważnej puli → 0.0
        delta = delta.where(valid & pool_mean.notna(), 0.0)
        df["delta_dist_km"] = delta.astype(float)
    return df


def solo_mask(df: pd.DataFrame) -> pd.Series:
    """Maska wierszy par należących do decyzji SOLO (winner z pustym workiem).

    Identyczna logika używana w treningu i w teście parity, więc definicja
    nie może się rozjechać.
    """
    bs = df["winner_bag_size"].fillna(-1)
    dp = df["winner_bag_drops_pending"].fillna(0)
    pp = df["winner_bag_pickup_pending"].fillna(0)
    return (bs == 0) & (dp == 0) & (pp == 0)


def load_split(split: str) -> pd.DataFrame:
    """Wczytaj produkcyjny split par (train/val/test) — TYLKO do odczytu."""
    path = DATASET_DIR / f"{split}.parquet"
    return pd.read_parquet(path)


def load_name_to_tier() -> Dict[str, str]:
    """Mapowanie courier_name -> tier (gold/std+/...) z courier_tiers.json.

    Używane WYŁĄCZNIE do raportowego breakdownu per-tier (model nie widzi nazw).
    """
    import json

    raw = json.load(open(COURIER_TIERS_PATH, encoding="utf-8"))
    out: Dict[str, str] = {}
    for cid, info in raw.items():
        if cid == "_meta" or not isinstance(info, dict):
            continue
        name = info.get("name")
        tier = info.get("bag", {}).get("tier")
        if name and tier:
            out[name] = tier
    return out
