"""V3.28 Faza 6 — LGBM shadow inference dla dispatch_pipeline.

Pure Behavioral Cloning model (Faza 5 v1.0) trained na 399K pairs z 5.5 miesięcy
CSV history reconstruction. Inference w shadow mode (parallel computation) —
ZERO production behavior change. Result attached to decision_record.

Defense-in-depth: każdy except → log + return ShadowResult(enabled=False, fallback_reason).
NIGDY nie raise to caller.

Architecture:
- Singleton pattern (model load once at module init)
- Reuse osrm_client + DistrictReverseLookup singletons
- Pointwise inference (predict score per candidate, sort)
- Group A reconstruction-only features defaulted (level_A_count, exclude_*)
"""
from __future__ import annotations

import json
import logging
import math
import os
import pickle
import time
import traceback
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dispatch_v2.common import ENABLE_LGBM_METRICS_READ

log = logging.getLogger(__name__)

WARSAW = ZoneInfo("Europe/Warsaw")

# Default paths (configurable via __init__)
# V3.28 Faza 5.1 hot-swap (01.05.2026): v1.0 → v1.1.
# v1.1 trained BEZ 7 reconstruction-only features (level_A_count, level_B_count,
# level_C_excluded_count, exclude_virtual/historical/not_active/low_day).
# Identical metrics (NDCG@5=0.852, pa=88.45%) — zero accuracy regression.
# Eliminuje gap training-vs-inference dla Faza 6 production deployment.
MODEL_PATH = "/root/.openclaw/workspace/scripts/ml_data_prep/models/v1.1/lgbm_ranker.txt"
ENCODERS_PATH = "/root/.openclaw/workspace/scripts/ml_data_prep/models/v1.1/encoders.pkl"
FEATURE_COLUMNS_PATH = "/root/.openclaw/workspace/scripts/ml_data_prep/models/v1.1/feature_columns.json"
MODEL_VERSION = "1.1"
TIERS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"

# ── A2 (2026-06-20): dwumodel LGBM solo/bundle (primary-candidate, OFF) ──────
# Jednolity v1.1 zapada się na decyzjach SOLO (winner pusty worek). Dwa modele:
# solo (bez cech worka) i bundle. Wpięcie ADDITIVE za flagą ENABLE_LGBM_PRIMARY
# (default OFF) — gdy OFF, ta ścieżka NIE jest wołana (zachowanie 1:1 dzisiejsze).
# Modele uczone na definicjach PRODUKCYJNYCH (delta=kandydat−pool_mean, haversine
# ×1.42 — to co liczy _compute_all_candidate_features), router PO STANIE WORKA
# (bag-state), level = oś-worka (NIE _level_from_metrics oś-GPS). FLIP = Adrian po
# ACK. Decyzja+dowód: memory [[lgbm-twomodel-prod-skew-2026-06-20]].
TWOMODEL_DIR = "/root/.openclaw/workspace/scripts/dispatch_v2/ml_data_prep/models_twomodel"

# Group A: reconstruction-only features (Faza 1 specific, NIE w live dispatch).
# v1.1: empty dict — features removed from training data, not needed at inference.
# v1.0 had 7 reconstruction defaults; v1.1 trained without them, zero accuracy delta.
GROUP_A_DEFAULTS: Dict[str, int] = {}

# Latency caps (configurable via env / common.py)
LATENCY_SOFT_CAP_MS = float(os.environ.get("LGBM_SHADOW_LATENCY_SOFT_CAP_MS", "200"))
LATENCY_HARD_CAP_MS = float(os.environ.get("LGBM_SHADOW_LATENCY_HARD_CAP_MS", "500"))

PEAK_LUNCH = {11, 12, 13}
PEAK_DINNER = {17, 18, 19}


@dataclass
class ShadowResult:
    enabled: bool
    fallback_reason: Optional[str]  # None | "all_bag_zero" | "lgbm_error" | "feature_compute_error" | "latency_timeout" | "model_not_loaded"
    winner_cid: Optional[str]
    winner_score: Optional[float]
    ranking: List[Dict[str, Any]] = field(default_factory=list)  # top 5
    agreement_with_primary: Optional[bool] = None  # filled by caller
    reconstruction_features_defaulted: bool = False  # v1.0=True (7 defaults), v1.1=False (features removed)
    model_version: str = "1.1"  # bumped 01.05.2026 hot-swap z v1.0
    evaluation_ts: str = ""
    latency_ms: float = 0.0
    feature_compute_ms: float = 0.0
    inference_ms: float = 0.0
    n_candidates_scored: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Singleton
_inferer = None


class LGBMShadowInferer:
    """LGBM ranker pointwise inference w shadow mode."""

    def __init__(
        self,
        model_path: str = MODEL_PATH,
        encoders_path: str = ENCODERS_PATH,
        feature_columns_path: str = FEATURE_COLUMNS_PATH,
        tiers_path: str = TIERS_PATH,
        osrm_client: Any = None,
        district_lookup: Any = None,
    ):
        self._model = None
        self._encoders: Dict[str, Any] = {}
        self._feature_columns: List[str] = []
        self._osrm = osrm_client
        self._district_lookup = district_lookup
        self._name_to_tier: Dict[str, str] = {}
        self._loaded = False
        self._predict_count = 0
        self._fallback_count: Counter = Counter()
        try:
            self._load(model_path, encoders_path, feature_columns_path, tiers_path)
        except Exception as e:
            log.error(f"LGBMShadowInferer init fail: {e}", exc_info=True)
            self._loaded = False

    # ── F4 helpers ──────────────────────────────────────────────────────────
    def _get_candidate_attr(self, c, attr_name: str, default):
        """Read field z metrics dict przez explicit mapping (Candidate dataclass nie ma fields)."""
        METRICS_MAP = {
            'bag_size': 'bag_size_before',
            'pos_source': 'pos_source',
            'level': 'cs_tier_label',
            'tier_bag': 'cs_tier_bag',
        }
        metrics_key = METRICS_MAP.get(attr_name)
        if metrics_key and hasattr(c, 'metrics') and isinstance(c.metrics, dict):
            val = c.metrics.get(metrics_key)
            if val is not None:
                return val
        return getattr(c, attr_name, default)

    @staticmethod
    def _derive_bag_fields(c):
        """Returns dict {bag_drops_pending, bag_pickup_pending, bag_n_distinct_districts, bag_has_distant_drop}
        from c.metrics['bag_context'] list. Defaults 0/False gdy brak."""
        bc = c.metrics.get('bag_context', []) if hasattr(c, 'metrics') and isinstance(c.metrics, dict) else []
        if not isinstance(bc, list):
            bc = []
        bag_drops_pending = sum(
            1 for b in bc
            if isinstance(b, dict) and b.get('delivered_at') is None and b.get('picked_up_at') is not None
        )
        bag_pickup_pending = sum(
            1 for b in bc
            if isinstance(b, dict) and b.get('picked_up_at') is None
        )
        districts = set()
        has_distant = False
        for b in bc:
            if not isinstance(b, dict):
                continue
            d = b.get('drop_district') or b.get('district')
            if d:
                districts.add(d)
            if b.get('has_distant_drop'):
                has_distant = True
        return {
            'bag_drops_pending': bag_drops_pending,
            'bag_pickup_pending': bag_pickup_pending,
            'bag_n_distinct_districts': len(districts),
            'bag_has_distant_drop': has_distant,
        }

    def _load(self, model_path: str, encoders_path: str, feature_columns_path: str, tiers_path: str) -> None:
        """Load model + encoders + columns + tier mapping."""
        import lightgbm as lgb
        self._model = lgb.Booster(model_file=model_path)
        with open(encoders_path, "rb") as f:
            self._encoders = pickle.load(f)
        with open(feature_columns_path, "r", encoding="utf-8") as f:
            self._feature_columns = json.load(f)
        try:
            tiers = json.load(open(tiers_path, encoding="utf-8"))
            for cid, info in tiers.items():
                if cid == "_meta":
                    continue
                if not isinstance(info, dict):
                    continue
                name = info.get("name")
                bag = info.get("bag", {}) or {}
                tier = bag.get("tier")
                if name and tier:
                    self._name_to_tier[name] = tier
        except Exception as e:
            log.warning(f"LGBMShadowInferer: courier_tiers load fail: {e}")
        self._loaded = True
        log.info(
            f"LGBMShadowInferer loaded: {self._model.num_trees()} trees, "
            f"{len(self._feature_columns)} features, {len(self._encoders)} encoders, "
            f"{len(self._name_to_tier)} courier tiers"
        )

    def predict_for_decision(
        self,
        decision_ctx: Dict[str, Any],
        candidates: List[Any],
    ) -> ShadowResult:
        """Per-decision shadow inference. NIGDY raise.

        decision_ctx required keys:
          - 'decision_ts' (datetime UTC), 'pickup_lat', 'pickup_lon',
            'pickup_district', 'drop_district' (optional)
        candidates: list of objects z attrs:
          - 'courier_id', 'courier_name', 'tier_bag', 'last_pos_lat/lon',
            'bag_size', 'bag_drops_pending', 'bag_pickup_pending',
            'idle_min', 'orders_today_before_T0' (z metrics)
        """
        t_start = time.time()
        eval_ts = datetime.now(timezone.utc).isoformat()
        result = ShadowResult(
            enabled=True, fallback_reason=None, winner_cid=None,
            winner_score=None, evaluation_ts=eval_ts,
        )

        if not self._loaded or self._model is None:
            result.enabled = False
            result.fallback_reason = "model_not_loaded"
            self._fallback_count["model_not_loaded"] += 1
            return result

        try:
            # Detect fallback condition: all candidates bag=0 (single-order pool)
            if ENABLE_LGBM_METRICS_READ:
                all_bag_zero = all(
                    (self._get_candidate_attr(c, "bag_size", 0) or 0) == 0 for c in candidates
                )
            else:
                all_bag_zero = all(
                    (getattr(c, "bag_size", 0) or 0) == 0 for c in candidates
                )
            if all_bag_zero:
                result.fallback_reason = "all_bag_zero"
                self._fallback_count["all_bag_zero"] += 1
                result.latency_ms = round((time.time() - t_start) * 1000, 2)
                return result

            # Compute per-candidate features
            t_feat = time.time()
            try:
                rows = self._compute_all_candidate_features(decision_ctx, candidates)
            except Exception as e:
                log.error(
                    f"LGBM feature compute fail order={decision_ctx.get('order_id')}: {e}",
                    exc_info=True,
                )
                result.enabled = False
                result.fallback_reason = "feature_compute_error"
                result.latency_ms = round((time.time() - t_start) * 1000, 2)
                self._fallback_count["feature_compute_error"] += 1
                return result
            result.feature_compute_ms = round((time.time() - t_feat) * 1000, 2)

            # Hard latency cap check
            elapsed = (time.time() - t_start) * 1000
            if elapsed > LATENCY_HARD_CAP_MS:
                log.warning(
                    f"LGBM shadow hard cap breach order={decision_ctx.get('order_id')}: "
                    f"{elapsed:.1f}ms > {LATENCY_HARD_CAP_MS}ms"
                )
                result.enabled = False
                result.fallback_reason = "latency_timeout"
                result.latency_ms = round(elapsed, 2)
                self._fallback_count["latency_timeout"] += 1
                return result

            # Encode + predict
            t_inf = time.time()
            try:
                X = self._build_feature_matrix(rows)
                scores = self._model.predict(X)
            except Exception as e:
                log.error(
                    f"LGBM predict fail order={decision_ctx.get('order_id')}: {e}",
                    exc_info=True,
                )
                result.enabled = False
                result.fallback_reason = "lgbm_error"
                result.latency_ms = round((time.time() - t_start) * 1000, 2)
                self._fallback_count["lgbm_error"] += 1
                return result
            result.inference_ms = round((time.time() - t_inf) * 1000, 2)

            # Build ranking sorted desc
            scored = [
                {
                    "cid": str(getattr(candidates[i], "courier_id", "")),
                    "name": getattr(candidates[i], "name", None) or getattr(candidates[i], "courier_name", None),
                    "score": round(float(scores[i]), 4),
                }
                for i in range(len(candidates))
            ]
            scored.sort(key=lambda x: -x["score"])
            result.winner_cid = scored[0]["cid"] if scored else None
            result.winner_score = scored[0]["score"] if scored else None
            result.ranking = scored[:5]
            result.n_candidates_scored = len(scored)
            result.latency_ms = round((time.time() - t_start) * 1000, 2)

            # Soft cap warning (informational)
            if result.latency_ms > LATENCY_SOFT_CAP_MS:
                log.warning(
                    f"LGBM shadow soft cap breach order={decision_ctx.get('order_id')}: "
                    f"{result.latency_ms}ms > {LATENCY_SOFT_CAP_MS}ms (n_cand={len(candidates)})"
                )
            self._predict_count += 1
            return result

        except Exception as e:
            log.error(
                f"LGBM shadow unexpected fail order={decision_ctx.get('order_id')}: {e}",
                exc_info=True,
            )
            result.enabled = False
            result.fallback_reason = "lgbm_error"
            result.latency_ms = round((time.time() - t_start) * 1000, 2)
            self._fallback_count["lgbm_error"] += 1
            return result

    def _compute_all_candidate_features(
        self,
        decision_ctx: Dict[str, Any],
        candidates: List[Any],
    ) -> List[Dict[str, Any]]:
        """Compute 49 features per candidate. Returns list of feature dicts."""
        decision_ts = decision_ctx.get("decision_ts") or datetime.now(timezone.utc)
        pickup_lat = decision_ctx.get("pickup_lat")
        pickup_lon = decision_ctx.get("pickup_lon")

        # Pool-level pre-compute
        pool_rows: List[Dict[str, Any]] = []
        pool_dists: List[float] = []
        tier_counts: Counter = Counter()
        for c in candidates:
            c_lat = getattr(c, "last_pos_lat", None) or _from_pos(getattr(c, "pos", None), 0)
            c_lon = getattr(c, "last_pos_lon", None) or _from_pos(getattr(c, "pos", None), 1)
            dist_road, osrm_used = self._compute_road_km(c_lat, c_lon, pickup_lat, pickup_lon)
            dist_hav = _haversine_km(c_lat, c_lon, pickup_lat, pickup_lon) * 1.42 if c_lat is not None and pickup_lat is not None else float("nan")
            if ENABLE_LGBM_METRICS_READ:
                name = self._get_candidate_attr(c, "name", None) or self._get_candidate_attr(c, "courier_name", None)
            else:
                name = getattr(c, "name", None) or getattr(c, "courier_name", None)
            tier = self._name_to_tier.get(name) or "unknown"
            tier_counts[tier] += 1
            pool_rows.append({
                "_lat": c_lat, "_lon": c_lon,
                "dist_road": dist_road, "dist_hav": dist_hav,
                "osrm_used": osrm_used,
            })
            if not _is_nan(dist_road):
                pool_dists.append(dist_road)

        pool_min = min(pool_dists) if pool_dists else float("nan")
        pool_max = max(pool_dists) if pool_dists else float("nan")
        # Rank by dist (1 = closest)
        sorted_by_dist = sorted(
            [(i, pool_rows[i]["dist_road"]) for i in range(len(pool_rows)) if not _is_nan(pool_rows[i]["dist_road"])],
            key=lambda x: x[1],
        )
        rank_map = {idx: rank + 1 for rank, (idx, _) in enumerate(sorted_by_dist)}

        # Decision time features
        if isinstance(decision_ts, datetime):
            warsaw_dt = decision_ts.astimezone(WARSAW) if decision_ts.tzinfo else decision_ts
        else:
            warsaw_dt = datetime.now(WARSAW)
        hour_w = int(warsaw_dt.hour)
        dow_w = int(warsaw_dt.weekday())
        month = int(warsaw_dt.month)
        season = (
            "winter" if month in (12, 1, 2)
            else "spring" if month in (3, 4, 5)
            else "summer" if month in (6, 7, 8)
            else "autumn"
        )
        decision_is_peak = hour_w in (PEAK_LUNCH | PEAK_DINNER)

        pickup_district = decision_ctx.get("pickup_district") or "Unknown"
        drop_district = decision_ctx.get("drop_district") or "Unknown"

        # Per-candidate feature row
        rows: List[Dict[str, Any]] = []
        for i, c in enumerate(candidates):
            pr = pool_rows[i]
            c_lat = pr["_lat"]
            c_lon = pr["_lon"]
            if ENABLE_LGBM_METRICS_READ:
                bag_size = int(self._get_candidate_attr(c, "bag_size", 0) or 0)
                derived = self._derive_bag_fields(c)
                bag_drops_pending = derived['bag_drops_pending']
                bag_pickup_pending = derived['bag_pickup_pending']
                bag_n_distinct_districts = derived['bag_n_distinct_districts']
                bag_has_distant_drop = derived['bag_has_distant_drop']
            else:
                bag_size = int(getattr(c, "bag_size", 0) or 0)
                bag_drops_pending = int(getattr(c, "bag_drops_pending", 0) or 0)
                bag_pickup_pending = int(getattr(c, "bag_pickup_pending", 0) or 0)
                bag_n_distinct_districts = int(getattr(c, "bag_n_distinct_districts", 0) or 0)
                bag_has_distant_drop = bool(getattr(c, "bag_has_distant_drop", False))
            idle_min = getattr(c, "idle_min", None)
            district = self._district_lookup.lookup(c_lat, c_lon) if self._district_lookup else "Unknown"
            row = {
                # Per-candidate base
                "level": getattr(c, "level", None) or _level_from_metrics(c),
                "bag_size": bag_size,
                "bag_drops_pending": bag_drops_pending,
                "bag_pickup_pending": bag_pickup_pending,
                "idle_min": idle_min if idle_min is not None else -1.0,
                "last_pos_lat": c_lat if c_lat is not None else 0.0,
                "last_pos_lon": c_lon if c_lon is not None else 0.0,
                "orders_today_before_T0": int(getattr(c, "orders_today_before_T0", 0) or 0),
                # Distance
                "dist_to_pickup_km": pr["dist_road"] if not _is_nan(pr["dist_road"]) else -1.0,
                "dist_to_pickup_haversine_km": pr["dist_hav"] if not _is_nan(pr["dist_hav"]) else -1.0,
                "osrm_used": int(pr["osrm_used"]),
                # District
                "district": district,
                "district_known": district != "Unknown",
                "district_match_pickup": district == pickup_district and district != "Unknown",
                "district_adjacent_pickup": _district_adjacent(district, pickup_district),
                # Bag categorical
                "bag_size_category": _bag_size_category(bag_size),
                "idle_min_capped": int(min(idle_min, 30)) if idle_min is not None else -1,
                "idle_category": _idle_category(idle_min),
                # Bag districts proxy
                "bag_n_distinct_districts": bag_n_distinct_districts,
                "bag_has_distant_drop": bag_has_distant_drop,
                # Rank
                "rank_by_dist": rank_map.get(i, len(candidates) + 1),
                # Decision context
                "decision_hour_warsaw": hour_w,
                "decision_dow_warsaw": dow_w,
                "decision_is_peak": decision_is_peak,
                "pool_size": len(candidates),
                # Group A defaults (reconstruction-only, NIE in live)
                **GROUP_A_DEFAULTS,
                "is_peak": decision_is_peak,
                "pickup_district": pickup_district,
                "drop_district": drop_district,
                "is_lunch_peak": hour_w in PEAK_LUNCH,
                "is_dinner_peak": hour_w in PEAK_DINNER,
                "is_weekend": dow_w >= 5,
                "minutes_since_midnight_warsaw": hour_w * 60 + int(warsaw_dt.minute),
                "season": season,
                # Pool tier counts
                "gold_in_pool": tier_counts.get("gold", 0),
                "std_plus_in_pool": tier_counts.get("std+", 0),
                "std_in_pool": tier_counts.get("std", 0),
                "slow_in_pool": tier_counts.get("slow", 0),
                "new_in_pool": tier_counts.get("new", 0),
                "unknown_in_pool": tier_counts.get("unknown", 0),
                # Pool dist
                "pool_min_dist_km": pool_min if not _is_nan(pool_min) else -1.0,
                "pool_max_dist_km": pool_max if not _is_nan(pool_max) else -1.0,
                # delta_dist_km: relative do pool median (per-pair → per-candidate adaptation)
                "delta_dist_km": (
                    pr["dist_road"] - (sum(pool_dists) / len(pool_dists))
                    if not _is_nan(pr["dist_road"]) and pool_dists else 0.0
                ),
            }
            rows.append(row)
        return rows

    def _build_feature_matrix(self, rows: List[Dict[str, Any]]):
        """Encode categoricals + arrange w feature_columns order."""
        import numpy as np
        # Encode categoricals
        for row in rows:
            for col, encoder in self._encoders.items():
                if col in row:
                    val = row[col]
                    val_str = "UNK" if val is None else str(val)
                    known = set(encoder.classes_)
                    if val_str not in known:
                        val_str = "UNK"
                    if val_str not in known:
                        # Defensive: encoder doesn't have UNK either
                        row[col] = 0
                    else:
                        row[col] = int(encoder.transform([val_str])[0])
        # Build matrix in feature_columns order
        matrix = []
        for row in rows:
            vec = []
            for col in self._feature_columns:
                val = row.get(col, -1)
                if isinstance(val, bool):
                    val = int(val)
                vec.append(val if val is not None else -1)
            matrix.append(vec)
        return np.array(matrix, dtype=float)

    def _compute_road_km(self, lat1, lon1, lat2, lon2) -> Tuple[float, bool]:
        """Returns (km, osrm_used). Falls back to haversine on osrm fail."""
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return float("nan"), False
        try:
            if self._osrm is None:
                return _haversine_km(lat1, lon1, lat2, lon2) * 1.42, False
            result = self._osrm.route((lat1, lon1), (lat2, lon2))
            if result and "distance_m" in result:
                return result["distance_m"] / 1000.0, True
        except Exception as e:
            log.debug(f"OSRM call fail in shadow: {e}")
        return _haversine_km(lat1, lon1, lat2, lon2) * 1.42, False

    def stats(self) -> Dict[str, Any]:
        return {
            "loaded": self._loaded,
            "predict_count": self._predict_count,
            "fallback_counts": dict(self._fallback_count),
        }


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _from_pos(pos, idx):
    if pos is None:
        return None
    try:
        return pos[idx]
    except (TypeError, IndexError):
        return None


def _is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    if any(_is_nan(v) for v in (lat1, lon1, lat2, lon2)):
        return float("nan")
    R = 6371.0
    p = math.pi / 180
    dlat = (lat2 - lat1) * p
    dlon = (lon2 - lon1) * p
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _bag_size_category(n) -> str:
    n = int(n) if n is not None else 0
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    if n == 2:
        return "2"
    return "3+"


def _idle_category(idle_min) -> str:
    if idle_min is None:
        return "unknown"
    if idle_min < 5:
        return "fresh"
    if idle_min < 15:
        return "medium"
    if idle_min < 30:
        return "stale"
    return "cold"


def _level_from_metrics(c) -> str:
    """Heuristic: derive 'level' z courier state. Default 'A' (active)."""
    bag = getattr(c, "bag_size", 0) or 0
    pos = getattr(c, "last_pos_lat", None)
    if pos is None:
        return "B"
    return "A"


def _bag_axis_level(bag_size, bag_drops_pending, bag_pickup_pending) -> str:
    """A2 dwumodel: `level` = oś-WORKA (definicja datasetu v2.0, 100% match).

    A = kurier ma cokolwiek w worku/pending; B = pusty worek. To NIE jest oś-GPS
    z _level_from_metrics — router/feature dwumodelu MUSI iść po stanie worka,
    inaczej cichy skew GPS-vs-worek. Zweryfikowane na dataset winner/loser_level.
    """
    bs = int(bag_size or 0)
    dp = int(bag_drops_pending or 0)
    pp = int(bag_pickup_pending or 0)
    return "A" if (bs > 0 or dp > 0 or pp > 0) else "B"


def _is_solo_candidate(bag_size, bag_drops_pending, bag_pickup_pending) -> bool:
    """Reżim SOLO (pusty worek) == bag_axis_level == 'B' == solo_mask datasetu."""
    return _bag_axis_level(bag_size, bag_drops_pending, bag_pickup_pending) == "B"


def _district_adjacent(zone1: Optional[str], zone2: Optional[str]) -> bool:
    if not zone1 or not zone2 or zone1 == "Unknown" or zone2 == "Unknown":
        return False
    if zone1 == zone2:
        return True
    try:
        from dispatch_v2.common import BIALYSTOK_DISTRICT_ADJACENCY
        return zone2 in BIALYSTOK_DISTRICT_ADJACENCY.get(zone1, set())
    except Exception:
        return False


def get_lgbm_inferer() -> LGBMShadowInferer:
    """Singleton accessor. First call ~3-5s (model load). Reuse across decisions."""
    global _inferer
    if _inferer is None:
        try:
            from dispatch_v2 import osrm_client
            from dispatch_v2.district_reverse_lookup import get_district_lookup
            _inferer = LGBMShadowInferer(
                osrm_client=osrm_client,
                district_lookup=get_district_lookup(),
            )
        except Exception as e:
            log.error(f"get_lgbm_inferer init fail: {e}", exc_info=True)
            _inferer = LGBMShadowInferer(osrm_client=None, district_lookup=None)
    return _inferer


# ════════════════════════════════════════════════════════════════════════════
# A2 (2026-06-20) — dwumodel solo/bundle (primary-candidate, za ENABLE_LGBM_PRIMARY OFF)
# ════════════════════════════════════════════════════════════════════════════

# Cechy „bundlowe" (ładunek worka) usuwane z modelu SOLO — MUSZĄ == twomodel_common.
_TWOMODEL_BUNDLE_ONLY = (
    "bag_size", "bag_drops_pending", "bag_pickup_pending",
    "bag_size_category", "bag_n_distinct_districts", "bag_has_distant_drop",
)


@dataclass
class TwoModelResult:
    """Wynik dwumodelu (shadow). Analogiczny do ShadowResult, osobne pole serializacji."""
    enabled: bool
    fallback_reason: Optional[str]
    winner_cid: Optional[str]
    winner_score: Optional[float]
    ranking: List[Dict[str, Any]] = field(default_factory=list)  # top 5
    regime_counts: Dict[str, int] = field(default_factory=dict)   # {solo:n, bundle:n}
    n_candidates_scored: int = 0
    evaluation_ts: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LGBMTwoModelInferer:
    """Dwumodel solo/bundle. Reużywa LGBMShadowInferer do liczenia cech (delta=pool_mean,
    haversine×1.42 — identyczne z definicją treningu), enkoduje wg ARTEFAKTÓW dwumodelu
    (one-hot level oś-worka + label-encodery v2.0), routuje per-kandydat po stanie worka:
    pusty worek → model SOLO, worek → model BUNDLE. Scalanie rankingu po RANKU
    wewnątrz-modelowym (score'y lambdarank nieporównywalne między modelami).

    NIGDY raise. Wołany WYŁĄCZNIE gdy ENABLE_LGBM_PRIMARY (flaga) — patrz
    predict_two_model_for_decision (flag-gated). FLIP = Adrian po ACK.
    """

    def __init__(self, base_inferer: "LGBMShadowInferer", twomodel_dir: str = TWOMODEL_DIR):
        self._base = base_inferer
        self._dir = twomodel_dir
        self._loaded = False
        self._models: Dict[str, Any] = {}            # {solo, bundle} -> Booster
        self._encoders: Dict[str, Dict[str, Any]] = {}   # regime -> {col: LabelEncoder}
        self._tier_cats: Dict[str, List[str]] = {}       # regime -> [A,B,UNK]
        self._feat_cols: Dict[str, List[str]] = {}       # regime -> feature order
        self._predict_count = 0
        self._fallback_count: Counter = Counter()
        try:
            self._load()
        except Exception as e:
            log.error(f"LGBMTwoModelInferer init fail: {e}", exc_info=True)
            self._loaded = False

    def _load(self) -> None:
        import lightgbm as lgb
        for regime in ("solo", "bundle"):
            base = os.path.join(self._dir, regime)
            self._models[regime] = lgb.Booster(model_file=os.path.join(base, "lgbm_ranker.txt"))
            with open(os.path.join(base, "label_encoders.pkl"), "rb") as f:
                self._encoders[regime] = pickle.load(f)
            with open(os.path.join(base, "tier_categories.json"), encoding="utf-8") as f:
                self._tier_cats[regime] = json.load(f)
            with open(os.path.join(base, "feature_columns.json"), encoding="utf-8") as f:
                self._feat_cols[regime] = json.load(f)
        self._loaded = True
        log.info(
            f"LGBMTwoModelInferer loaded: solo={self._models['solo'].num_trees()}trees/"
            f"{len(self._feat_cols['solo'])}feat, bundle={self._models['bundle'].num_trees()}trees/"
            f"{len(self._feat_cols['bundle'])}feat"
        )

    def _encode_row(self, row: Dict[str, Any], regime: str):
        """Enkoduj JEDEN wiersz cech → wektor wg feature_columns dwumodelu (regime).

        Replikuje ścieżkę treningu: label-encode (district/idle_category/pickup/drop/
        season) + one-hot `level` (oś-worka) + reszta numeryczna, kolejność = feature_cols.
        """
        enc = self._encoders[regime]
        tier_cats = self._tier_cats[regime]
        feat_cols = self._feat_cols[regime]
        r = dict(row)
        # one-hot level (oś-worka) — stałe kolumny niezależne od wejścia (parity)
        raw_level = str(r.get("level", "UNK"))
        if raw_level not in set(tier_cats):
            raw_level = "UNK"
        for cat in tier_cats:
            r[f"level__{cat}"] = 1 if raw_level == cat else 0
        # label-encode kategorycznych
        for col, le in enc.items():
            if col in r:
                val = r[col]
                val_str = "UNK" if val is None else str(val)
                known = set(le.classes_)
                if val_str not in known:
                    val_str = "UNK"
                r[col] = int(le.transform([val_str])[0]) if val_str in known else 0
        # wektor wg kolejności feature_columns; brak → -1 (jak to_arrays fillna(-1))
        vec = []
        for col in feat_cols:
            v = r.get(col, -1)
            if isinstance(v, bool):
                v = int(v)
            vec.append(v if v is not None else -1)
        return vec

    def predict_for_decision(self, decision_ctx: Dict[str, Any], candidates: List[Any]) -> TwoModelResult:
        """Dwumodel shadow inference. NIGDY raise. Router po stanie worka per-kandydat."""
        import numpy as np
        t_start = time.time()
        res = TwoModelResult(
            enabled=True, fallback_reason=None, winner_cid=None, winner_score=None,
            evaluation_ts=datetime.now(timezone.utc).isoformat(),
        )
        if not self._loaded:
            res.enabled = False
            res.fallback_reason = "twomodel_not_loaded"
            self._fallback_count["twomodel_not_loaded"] += 1
            return res
        if not candidates:
            res.enabled = False
            res.fallback_reason = "no_candidates"
            return res
        try:
            # Reużyj liczenia cech bazowego inferera (delta=pool_mean, haversine×1.42).
            rows = self._base._compute_all_candidate_features(decision_ctx, candidates)
            # Router per-kandydat po stanie worka + override level na oś-worka.
            solo_idx, bundle_idx = [], []
            for i, row in enumerate(rows):
                bag_axis = _bag_axis_level(
                    row.get("bag_size"), row.get("bag_drops_pending"), row.get("bag_pickup_pending")
                )
                row["level"] = bag_axis  # NADPISZ oś-GPS na oś-worka (parity z datasetem)
                (solo_idx if bag_axis == "B" else bundle_idx).append(i)

            # Skoruj każdą grupę jej modelem; ranking wewnątrz-grupowy (1=najlepszy).
            per_cand_rank: Dict[int, int] = {}
            per_cand_score: Dict[int, float] = {}
            for regime, idxs in (("solo", solo_idx), ("bundle", bundle_idx)):
                if not idxs:
                    continue
                X = np.array([self._encode_row(rows[i], regime) for i in idxs], dtype=float)
                scores = self._models[regime].predict(X)
                order = sorted(range(len(idxs)), key=lambda k: -float(scores[k]))
                for rank, k in enumerate(order):
                    per_cand_rank[idxs[k]] = rank + 1
                    per_cand_score[idxs[k]] = float(scores[k])

            # Scal: kandydaci empty-bag (solo) preferowani gdy istnieją, w obrębie grupy
            # po ranku; potem bundle.
            # ⚠ OGRANICZENIE (zmierzone online shadow-parity 2026-06-20): score'y
            # lambdarank solo vs bundle są NIEPORÓWNYWALNE, a reżim (solo/bundle) =
            # OUTCOME (stan worka zwycięzcy), niedostępny na wejściu. Ta reguła „solo
            # zawsze przed bundle" wymusza pick empty-bag gdy istnieje (1030/1487
            # decyzji), podczas gdy obecny system wybiera empty tylko ~53% takich
            # przypadków (resztę bundluje). Dwumodel optymalizuje WEWNĄTRZ reżimu, ale
            # NIE arbitruje między reżimami — a to jest realna decyzja (bundle vs świeży
            # kurier). To główny blocker GO na primary. Reguła jest jawna i mierzona —
            # NIE traktować jako rozwiązanej. Szczegóły: [[lgbm-twomodel-prod-skew-2026-06-20]].
            def merge_key(i: int):
                grp = 0 if i in solo_idx else 1  # solo grupa przed bundle
                return (grp, per_cand_rank.get(i, 10**6), -per_cand_score.get(i, -1e9))

            merged = sorted(range(len(candidates)), key=merge_key)
            scored = []
            for i in merged:
                scored.append({
                    "cid": str(getattr(candidates[i], "courier_id", "")),
                    "name": getattr(candidates[i], "name", None) or getattr(candidates[i], "courier_name", None),
                    "regime": "solo" if i in solo_idx else "bundle",
                    "model_score": round(per_cand_score.get(i, float("nan")), 4),
                    "model_rank": per_cand_rank.get(i),
                })
            res.winner_cid = scored[0]["cid"] if scored else None
            res.winner_score = scored[0]["model_score"] if scored else None
            res.ranking = scored[:5]
            res.regime_counts = {"solo": len(solo_idx), "bundle": len(bundle_idx)}
            res.n_candidates_scored = len(scored)
            res.latency_ms = round((time.time() - t_start) * 1000, 2)
            self._predict_count += 1
            return res
        except Exception as e:
            log.error(f"LGBMTwoModelInferer fail order={decision_ctx.get('order_id')}: {e}", exc_info=True)
            res.enabled = False
            res.fallback_reason = "twomodel_error"
            res.latency_ms = round((time.time() - t_start) * 1000, 2)
            self._fallback_count["twomodel_error"] += 1
            return res

    def stats(self) -> Dict[str, Any]:
        return {"loaded": self._loaded, "predict_count": self._predict_count,
                "fallback_counts": dict(self._fallback_count)}


# Singleton dwumodelu
_twomodel_inferer = None


def get_twomodel_inferer() -> "LGBMTwoModelInferer":
    """Singleton dwumodelu. Reużywa bazowy LGBMShadowInferer (osrm/district/cechy)."""
    global _twomodel_inferer
    if _twomodel_inferer is None:
        _twomodel_inferer = LGBMTwoModelInferer(base_inferer=get_lgbm_inferer())
    return _twomodel_inferer


def predict_two_model_for_decision(
    decision_ctx: Dict[str, Any], candidates: List[Any]
) -> Optional[TwoModelResult]:
    """Flag-gated entry. Zwraca TwoModelResult gdy ENABLE_LGBM_PRIMARY LUB
    ENABLE_LGBM_TWOMODEL_SHADOW, inaczej None.

    SHADOW (2026-06-20): liczenie dwumodelu OBOK selekcji reguł, wynik tylko do
    metrics/logu (NIE konsumowany przez werdykt — arbitraż solo↔bundle nierozwiązany,
    patrz predict_for_decision §OGRANICZENIE). Oba tryby = obecnie logging-only;
    różnica semantyczna pojawi się dopiero gdy selekcja zacznie konsumować winner_cid.
    Gdy obie flagi OFF (default) → None, ZERO obliczeń, zachowanie 1:1 dzisiejsze.
    NIGDY raise (defense-in-depth jak reszta ml_inference).
    """
    try:
        from dispatch_v2.common import flag
        if not (flag("ENABLE_LGBM_PRIMARY", False) or flag("ENABLE_LGBM_TWOMODEL_SHADOW", False)):
            return None
    except Exception:
        return None
    try:
        return get_twomodel_inferer().predict_for_decision(decision_ctx, candidates)
    except Exception as e:
        log.error(f"predict_two_model_for_decision fail: {e}", exc_info=True)
        return None
