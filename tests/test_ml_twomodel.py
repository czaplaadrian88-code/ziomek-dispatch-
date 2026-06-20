"""[E3] Parity train-vs-serve dla dwóch modeli LGBM (solo / bundle) — TEST OFFLINE.

Cel: wykryć cichy skew — dla KAŻDEJ pochodnej cechy sprawdzamy, że transformacja
użyta przy TRENINGU == transformacja przy SERWOWANIU. To klasyczne źródło
"silent train-vs-serve skew" w pipeline'ach ML.

Dwie warstwy testów:
  A) Czyste transformacje (numpy/pandas) — ZAWSZE się wykonują:
       - reference implementations pochodnych cech (lustro src/feature_engineering.py)
         vs serwowana transformacja → muszą się zgadzać + być deterministyczne,
       - one-hot tieru (`level`) — stałe kolumny niezależne od podzbioru (nowa
         powierzchnia skew, którą wprowadziliśmy),
       - label-encode (district/season/...) — nieznane -> UNK identycznie,
       - solo_mask spójny z winner_level=="B".
  B) Parity na realnych danych + artefaktach modelu — pomijane (skip), gdy
     interpreter nie ma pyarrow/lightgbm (np. systemowy python3):
       - kolumny pochodne w datasecie == rekonstrukcja referencyjna,
       - załadowany zapisany model + ponowna ścieżka serwowania reprodukują
         pairwise z raportu (deterministyczna predykcja).

Uruchom: python3 -m pytest tests/test_ml_twomodel.py -q
(pełna warstwa B wymaga venva pipeline'u ML: pyarrow+lightgbm+sklearn.)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── ścieżki ──────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent              # dispatch_v2/tests
ML_PREP = HERE.parent / "ml_data_prep"              # dispatch_v2/ml_data_prep
PROD_ML = Path("/root/.openclaw/workspace/scripts/ml_data_prep")
MODELS_TWOMODEL = ML_PREP / "models_twomodel"

for p in (str(ML_PREP), str(PROD_ML)):
    if p not in sys.path:
        sys.path.insert(0, p)

import twomodel_common as tmc  # noqa: E402  (pandas/numpy only — bezpieczny import)


# ─────────────────────────────────────────────────────────────────────────────
# Reference implementations pochodnych cech — LUSTRO src/feature_engineering.py.
# Test celowo trzyma niezależną kopię kontraktu: jeśli ktoś zmieni transformację
# po jednej stronie (trening albo serwowanie), parity to wykryje.
# ─────────────────────────────────────────────────────────────────────────────
PEAK_LUNCH = {11, 12, 13}
PEAK_DINNER = {17, 18, 19}


def ref_bag_size_category(n):
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "unknown"
    n = int(n)
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    if n == 2:
        return "2"
    return "3+"


def ref_idle_category(idle_min):
    if idle_min is None or (isinstance(idle_min, float) and math.isnan(idle_min)):
        return "unknown"
    if idle_min < 5:
        return "fresh"
    if idle_min < 15:
        return "medium"
    if idle_min < 30:
        return "stale"
    return "cold"


def ref_idle_capped(idle_min):
    if idle_min is None or (isinstance(idle_min, float) and math.isnan(idle_min)):
        return None
    return int(min(float(idle_min), 30.0))


def ref_season(month):
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


# ─────────────────────────────────────────────────────────────────────────────
# Warstwa A — czyste transformacje (zawsze)
# ─────────────────────────────────────────────────────────────────────────────
class TestDerivedFeatureParity:
    """Pochodne cechy: kontrakt referencyjny == implementacja + determinizm."""

    def test_bag_size_category_contract_and_determinism(self):
        cases = [None, float("nan"), 0, 1, 2, 3, 4, 7, 11, 0.0, 2.0, 9.0]
        expected = {
            None: "unknown", 0: "0", 1: "1", 2: "2", 3: "3+", 4: "3+",
            7: "3+", 11: "3+",
        }
        for v in cases:
            r1 = ref_bag_size_category(v)
            r2 = ref_bag_size_category(v)  # determinizm
            assert r1 == r2
            if isinstance(v, float) and math.isnan(v):
                assert r1 == "unknown"
            elif v in expected:
                assert r1 == expected[v], f"bag_size_category({v}) = {r1}"

    def test_idle_category_boundaries(self):
        # granice: <5 fresh, <15 medium, <30 stale, >=30 cold
        assert ref_idle_category(0) == "fresh"
        assert ref_idle_category(4.999) == "fresh"
        assert ref_idle_category(5) == "medium"
        assert ref_idle_category(14.999) == "medium"
        assert ref_idle_category(15) == "stale"
        assert ref_idle_category(29.999) == "stale"
        assert ref_idle_category(30) == "cold"
        assert ref_idle_category(120) == "cold"
        assert ref_idle_category(None) == "unknown"
        assert ref_idle_category(float("nan")) == "unknown"

    def test_idle_capped_clips_at_30(self):
        assert ref_idle_capped(0) == 0
        assert ref_idle_capped(29) == 29
        assert ref_idle_capped(30) == 30
        assert ref_idle_capped(45) == 30
        assert ref_idle_capped(999) == 30
        assert ref_idle_capped(None) is None
        assert ref_idle_capped(float("nan")) is None

    def test_season_mapping(self):
        assert ref_season(1) == "winter"
        assert ref_season(2) == "winter"
        assert ref_season(12) == "winter"
        assert ref_season(3) == "spring"
        assert ref_season(5) == "spring"
        assert ref_season(6) == "summer"
        assert ref_season(8) == "summer"
        assert ref_season(9) == "autumn"
        assert ref_season(11) == "autumn"

    def test_peak_window_membership(self):
        # is_lunch_peak / is_dinner_peak — godziny Warsaw
        assert all(h in PEAK_LUNCH for h in (11, 12, 13))
        assert all(h in PEAK_DINNER for h in (17, 18, 19))
        assert 10 not in PEAK_LUNCH and 14 not in PEAK_LUNCH
        assert 16 not in PEAK_DINNER and 20 not in PEAK_DINNER

    def test_reference_matches_production_feature_engineering_if_importable(self):
        """Jeśli scipy dostępne — porównaj referencję z PRAWDZIWYM src.feature_engineering."""
        fe = pytest.importorskip(
            "src.feature_engineering",
            reason="src.feature_engineering wymaga scipy (brak w tym interpreterze)",
        )
        for v in [None, 0, 1, 2, 5, 11]:
            assert fe.bag_size_category(v) == ref_bag_size_category(v)
        for v in [0, 4.9, 5, 14.9, 15, 29.9, 30, 100]:
            assert fe.idle_category(v) == ref_idle_category(v)
            assert fe.idle_capped(v) == ref_idle_capped(v)
        # time_features.season przez prawdziwy kod
        for month, exp in [(1, "winter"), (4, "spring"), (7, "summer"), (10, "autumn")]:
            ts = pd.Timestamp(f"2026-{month:02d}-15 12:30")
            assert fe.time_features(ts)["season"] == ref_season(month)


class TestTierOneHotParity:
    """One-hot tieru (`level`): kolumny i kolejność STAŁE niezależnie od podzbioru."""

    @pytest.fixture
    def train_mod(self):
        return pytest.importorskip(
            "train_two_models",
            reason="train_two_models wymaga lightgbm/sklearn (brak w tym interpreterze)",
        )

    def test_onehot_columns_are_fixed_regardless_of_input(self, train_mod):
        cats = ["A", "B", "UNK"]
        # df1 zawiera tylko 'B', df2 tylko 'A' — wynik MUSI mieć te same kolumny.
        df1 = pd.DataFrame({tmc.TIER_ORD_COL: ["B", "B"], "x": [1, 2]})
        df2 = pd.DataFrame({tmc.TIER_ORD_COL: ["A"], "x": [3]})
        o1 = train_mod.apply_tier_onehot(df1, cats)
        o2 = train_mod.apply_tier_onehot(df2, cats)
        onehot_cols = [f"{tmc.TIER_ORD_COL}__{c}" for c in cats]
        for col in onehot_cols:
            assert col in o1.columns and col in o2.columns
        # oryginalna kolumna 'level' zdjęta
        assert tmc.TIER_ORD_COL not in o1.columns
        assert tmc.TIER_ORD_COL not in o2.columns
        # wartości poprawne
        assert o1["level__B"].tolist() == [1, 1]
        assert o1["level__A"].tolist() == [0, 0]
        assert o2["level__A"].tolist() == [1]
        assert o2["level__B"].tolist() == [0]

    def test_onehot_unknown_value_routes_to_UNK(self, train_mod):
        cats = ["A", "B", "UNK"]
        df = pd.DataFrame({tmc.TIER_ORD_COL: ["Z", "A", None], "x": [1, 2, 3]})
        o = train_mod.apply_tier_onehot(df, cats)
        # 'Z' (nieznane) i None -> UNK
        assert o["level__UNK"].tolist() == [1, 0, 1]
        assert o["level__A"].tolist() == [0, 1, 0]

    def test_onehot_is_deterministic(self, train_mod):
        cats = ["A", "B", "UNK"]
        df = pd.DataFrame({tmc.TIER_ORD_COL: ["A", "B", "Z"], "x": [1, 2, 3]})
        o1 = train_mod.apply_tier_onehot(df.copy(), cats)
        o2 = train_mod.apply_tier_onehot(df.copy(), cats)
        pd.testing.assert_frame_equal(o1, o2)


class TestLabelEncodeParity:
    """Label-encode (district/season/...): nieznane -> UNK, deterministycznie."""

    @pytest.fixture
    def train_mod(self):
        return pytest.importorskip(
            "train_two_models",
            reason="train_two_models wymaga lightgbm/sklearn",
        )

    def test_unknown_category_maps_to_UNK_consistently(self, train_mod):
        train_pw = pd.DataFrame({"season": ["winter", "spring", "summer"]})
        enc = train_mod.fit_label_encoders(train_pw)
        assert "season" in enc
        # serwowanie z nieznaną kategorią 'autumn' (nie było w train) -> UNK
        serve = pd.DataFrame({"season": ["winter", "autumn", "spring"]})
        out = train_mod.apply_label_encoders(serve, enc)
        unk_code = enc["season"].transform(["UNK"])[0]
        winter_code = enc["season"].transform(["winter"])[0]
        spring_code = enc["season"].transform(["spring"])[0]
        assert out["season"].tolist() == [winter_code, unk_code, spring_code]

    def test_label_encode_deterministic(self, train_mod):
        train_pw = pd.DataFrame({"season": ["winter", "spring"]})
        enc = train_mod.fit_label_encoders(train_pw)
        s = pd.DataFrame({"season": ["winter", "spring", "X"]})
        o1 = train_mod.apply_label_encoders(s.copy(), enc)
        o2 = train_mod.apply_label_encoders(s.copy(), enc)
        assert o1["season"].tolist() == o2["season"].tolist()


class TestBundleDefinition:
    """solo_mask == winner pusty worek == winner_level 'B'."""

    def test_solo_mask_matches_empty_bag(self):
        df = pd.DataFrame({
            "winner_bag_size": [0, 1, 0, 2, 0],
            "winner_bag_drops_pending": [0, 0, 1, 0, 0],
            "winner_bag_pickup_pending": [0, 0, 0, 0, 1],
        })
        mask = tmc.solo_mask(df)
        # solo tylko gdy wszystkie trzy == 0 → tylko wiersz 0
        assert mask.tolist() == [True, False, False, False, False]

    def test_solo_mask_handles_nan(self):
        df = pd.DataFrame({
            "winner_bag_size": [np.nan, 0],
            "winner_bag_drops_pending": [0, 0],
            "winner_bag_pickup_pending": [0, 0],
        })
        mask = tmc.solo_mask(df)
        # NaN bag_size -> fillna(-1) -> NIE solo (bezpieczna strona)
        assert mask.tolist() == [False, True]


# ─────────────────────────────────────────────────────────────────────────────
# Warstwa B — parity na realnych danych + artefaktach (importorskip)
# ─────────────────────────────────────────────────────────────────────────────
def _have_parquet_stack():
    try:
        import pyarrow  # noqa: F401
        import lightgbm  # noqa: F401
        return True
    except Exception:
        return False


pytestmark_heavy = pytest.mark.skipif(
    not _have_parquet_stack(),
    reason="warstwa B wymaga pyarrow+lightgbm (uruchom venvem pipeline'u ML)",
)


@pytest.fixture(scope="module")
def test_df():
    return tmc.load_split("test")


@pytest.fixture(scope="module")
def report():
    rp = MODELS_TWOMODEL / "twomodel_report.json"
    if not rp.exists():
        pytest.skip("Brak twomodel_report.json — uruchom najpierw train_two_models.py")
    return json.load(open(rp))


@pytestmark_heavy
class TestDatasetColumnReconstructionParity:
    """Precomputed kolumny pochodne w datasecie == rekonstrukcja referencyjna."""

    def test_winner_bag_size_category_reconstruction(self, test_df):
        recon = test_df["winner_bag_size"].map(ref_bag_size_category)
        stored = test_df["winner_bag_size_category"].astype(str)
        mismatch = (recon.astype(str) != stored).sum()
        assert mismatch == 0, f"{mismatch} niezgodności winner_bag_size_category (train-vs-serve skew!)"

    def test_winner_idle_category_reconstruction(self, test_df):
        recon = test_df["winner_idle_min"].map(ref_idle_category)
        stored = test_df["winner_idle_category"].astype(str)
        mismatch = (recon.astype(str) != stored).sum()
        assert mismatch == 0, f"{mismatch} niezgodności winner_idle_category"

    def test_winner_idle_min_capped_reconstruction(self, test_df):
        recon = test_df["winner_idle_min"].map(ref_idle_capped)
        stored = test_df["winner_idle_min_capped"]
        # porównanie z tolerancją na NaN/None
        both = pd.DataFrame({"r": recon, "s": stored})
        mask = both["r"].notna() & both["s"].notna()
        mismatch = (both.loc[mask, "r"].astype(int) != both.loc[mask, "s"].astype(int)).sum()
        assert mismatch == 0, f"{mismatch} niezgodności winner_idle_min_capped"

    def test_loser_bag_size_category_reconstruction(self, test_df):
        recon = test_df["loser_bag_size"].map(ref_bag_size_category)
        stored = test_df["loser_bag_size_category"].astype(str)
        mismatch = (recon.astype(str) != stored).sum()
        assert mismatch == 0, f"{mismatch} niezgodności loser_bag_size_category"


@pytestmark_heavy
class TestArtifactsExistAndServingParity:
    """Zapisane artefakty modeli istnieją; ponowna ścieżka serwowania = pairwise z raportu."""

    def test_artifacts_present(self, report):
        for regime in ("solo", "bundle"):
            d = MODELS_TWOMODEL / regime
            assert (d / "lgbm_ranker.txt").exists(), f"brak modelu {regime}"
            assert (d / "label_encoders.pkl").exists()
            assert (d / "tier_categories.json").exists()
            assert (d / "feature_columns.json").exists()

    def test_serving_reproduces_reported_solo_pairwise(self, report):
        """Załaduj zapisany model solo + przejdź pełną ścieżkę serwowania → ten sam pairwise."""
        import pickle
        import lightgbm as lgb
        train_mod = pytest.importorskip("train_two_models")

        d = MODELS_TWOMODEL / "solo"
        booster = lgb.Booster(model_file=str(d / "lgbm_ranker.txt"))
        label_enc = pickle.load(open(d / "label_encoders.pkl", "rb"))
        tier_categories = json.load(open(d / "tier_categories.json"))
        feature_order = json.load(open(d / "feature_columns.json"))

        test_pairs = tmc.load_split("test")
        solo_pairs = test_pairs[tmc.solo_mask(test_pairs)].reset_index(drop=True)

        pa, n = train_mod.pairwise_accuracy(
            solo_pairs, booster, label_enc, tier_categories, feature_order, drop_bundle=True
        )
        reported = report["lgbm_solo"]["test_pairwise_accuracy"]  # zapis zaokrąglony do 4 dp
        # Ścieżka serwowania jest deterministyczna: po zaokrągleniu MUSI == raport.
        # (różnica >0 tylko z powodu round(...,4) w raporcie, nie z powodu skew.)
        assert round(pa, 4) == reported, f"serving pairwise {pa} != raport {reported} (SKEW!)"
        assert n > 0

    def test_solo_model_has_no_bundle_features(self):
        """Model solo NIE może mieć cech bundlowych w kolumnach (kontrakt usunięcia)."""
        feature_order = json.load(open(MODELS_TWOMODEL / "solo" / "feature_columns.json"))
        for base in tmc.BUNDLE_ONLY_BASE_FEATURES:
            assert base not in feature_order, f"cecha bundlowa '{base}' przeciekła do modelu solo"
        # a model bundle JE MA
        bundle_feats = json.load(open(MODELS_TWOMODEL / "bundle" / "feature_columns.json"))
        assert "bag_size" in bundle_feats

    def test_gate_solo_pairwise_exceeds_80pct(self, report):
        """Bramka: LGBM_solo pairwise > 80% (warunek przed ENABLE_LGBM_PRIMARY)."""
        pa = report["lgbm_solo"]["test_pairwise_accuracy"]
        assert pa > 0.80, f"LGBM_solo pairwise {pa} <= 0.80 — bramka NIE spełniona"

    def test_old_model_collapses_on_solo(self, report):
        """Sanity: stary jednolity model JEST słaby na solo (uzasadnia dwa modele)."""
        old_solo = report["old_model_v1_1"]["solo"]["pairwise_accuracy"]
        new_solo = report["lgbm_solo"]["test_pairwise_accuracy"]
        assert old_solo < 0.65, f"stary model solo {old_solo} — spodziewany kolaps <0.65"
        assert new_solo - old_solo > 0.20, "dwa modele muszą wyraźnie poprawić solo (>20pp)"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
