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

# Kategoryczna cecha tieru/poziomu kandydata. W produkcji (`ml_inference.py`)
# `level` mapuje się na `cs_tier_label` (gold/std+/std/slow/new). W datasecie
# v2.0 `level` ma tylko A/B (poziom dostępności rekonstrukcji). Zadanie: zamiast
# label-encodingu (porządkowego) — ONE-HOT. Robimy to niezależnie od kardynalności.
TIER_ORD_COL = "level"


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
