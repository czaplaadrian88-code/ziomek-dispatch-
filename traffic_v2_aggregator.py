"""BUG-D Faza 2b — per-route v2 traffic multiplier aggregator.

Pure function module: agreguje listę per-leg OSRM data (collected przez
TLS w `osrm_client._apply_traffic_multiplier`) do per-route summary.

Per-leg dict shape (input):
    {
        "distance_km": float | None,
        "raw_min": float | None,        # osrm_raw_duration_min
        "v1_mult": float,                # legacy hour-based multiplier
        "v2_mult": float,                # BUG-D distance-bin multiplier
        "bin": str,                      # 'short' | 'medium' | 'long' | 'none'
    }

Per-route output dict shape:
    {
        "n_legs": int,
        "legs": [<per-leg dict>, ...],  # full breakdown (NIE summary only)
        "avg_v2_mult": float | None,
        "max_v2_mult": float | None,
        "min_v2_mult": float | None,
        "bins_count": {"short": int, "medium": int, "long": int, "none": int},
        "total_raw_min": float,
        "total_v2_predicted_min": float,  # Σ (raw × v2_mult), co BY pokazał drive_min gdyby v2 ON
        "total_v1_predicted_min": float,  # Σ (raw × v1_mult), current LIVE
        "v2_v1_delta_min": float,         # difference whole route (positive = v2 > v1)
    }

Empty legs → None (no recordable data).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def aggregate_legs(legs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Aggregate per-leg shadow data into route-level summary.

    Returns None gdy `legs` is empty/None (no OSRM calls recorded).
    Tolerant na missing fields (None values skipped w averages).
    """
    if not legs:
        return None

    v2_mults = [leg["v2_mult"] for leg in legs if leg.get("v2_mult") is not None]
    raw_mins = [leg.get("raw_min", 0.0) or 0.0 for leg in legs]

    bins_count = {"short": 0, "medium": 0, "long": 0, "none": 0}
    for leg in legs:
        bin_name = leg.get("bin") or "none"
        if bin_name in bins_count:
            bins_count[bin_name] += 1

    total_raw = sum(raw_mins)
    total_v2 = sum(
        (leg.get("raw_min") or 0.0) * (leg.get("v2_mult") or 0.0)
        for leg in legs
    )
    total_v1 = sum(
        (leg.get("raw_min") or 0.0) * (leg.get("v1_mult") or 0.0)
        for leg in legs
    )

    return {
        "n_legs": len(legs),
        "legs": [dict(leg) for leg in legs],  # defensive copy, decouple from TLS storage
        "avg_v2_mult": round(sum(v2_mults) / len(v2_mults), 3) if v2_mults else None,
        "max_v2_mult": round(max(v2_mults), 3) if v2_mults else None,
        "min_v2_mult": round(min(v2_mults), 3) if v2_mults else None,
        "bins_count": bins_count,
        "total_raw_min": round(total_raw, 2),
        "total_v2_predicted_min": round(total_v2, 2),
        "total_v1_predicted_min": round(total_v1, 2),
        "v2_v1_delta_min": round(total_v2 - total_v1, 2),
    }
