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

log = logging.getLogger(__name__)

WARSAW = ZoneInfo("Europe/Warsaw")

# Default paths (configurable via __init__)
MODEL_PATH = "/root/.openclaw/workspace/scripts/ml_data_prep/models/v1.0/lgbm_ranker.txt"
ENCODERS_PATH = "/root/.openclaw/workspace/scripts/ml_data_prep/models/v1.0/encoders.pkl"
FEATURE_COLUMNS_PATH = "/root/.openclaw/workspace/scripts/ml_data_prep/models/v1.0/feature_columns.json"
TIERS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"

# Group A: reconstruction-only features (Faza 1 specific, NIE w live dispatch)
GROUP_A_DEFAULTS = {
    "level_A_count": 0,
    "level_B_count": 0,
    "level_C_excluded_count": 0,
    "exclude_virtual": 0,
    "exclude_historical": 0,
    "exclude_not_active": 0,
    "exclude_low_day": 0,
}

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
    reconstruction_features_defaulted: bool = True  # always True for v1.0
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
            tier = self._name_to_tier.get(getattr(c, "name", None) or getattr(c, "courier_name", None)) or "unknown"
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
            bag_size = int(getattr(c, "bag_size", 0) or 0)
            idle_min = getattr(c, "idle_min", None)
            district = self._district_lookup.lookup(c_lat, c_lon) if self._district_lookup else "Unknown"
            row = {
                # Per-candidate base
                "level": getattr(c, "level", None) or _level_from_metrics(c),
                "bag_size": bag_size,
                "bag_drops_pending": int(getattr(c, "bag_drops_pending", 0) or 0),
                "bag_pickup_pending": int(getattr(c, "bag_pickup_pending", 0) or 0),
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
                "bag_n_distinct_districts": int(getattr(c, "bag_n_distinct_districts", 0) or 0),
                "bag_has_distant_drop": bool(getattr(c, "bag_has_distant_drop", False)),
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
