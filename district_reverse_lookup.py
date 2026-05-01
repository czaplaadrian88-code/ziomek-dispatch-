"""V3.28 Faza 6 — coord→district reverse lookup via kd-tree on address_cache.

Port from ml_data_prep/src/feature_engineering.py (Faza 4 component).
Reused dla LGBM shadow inference w live dispatch_pipeline.

Singleton pattern — initialized at module load, reused per-decision.
Memory cache (lat_round_3, lon_round_3) → district per query.

Defense-in-depth: missing address_cache or kd-tree init failure → return "Unknown" always.
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

ADDRESS_CACHE_PATH = "/root/.openclaw/workspace/scripts/ml_data_prep/data/address_cache.json"

# Lazy-loaded singleton instance
_instance = None


class DistrictReverseLookup:
    """kd-tree on geocoded address_cache. lookup(lat,lon) → district name."""

    def __init__(self, address_cache_path: str = ADDRESS_CACHE_PATH):
        self._coord_cache: Dict[Tuple[float, float], str] = {}
        self._loaded = False
        self._tree = None
        self._addresses: List[Dict] = []
        self._drop_zone_fn = None  # lazy-imported common.drop_zone_from_address
        self._init_count = 0
        self._lookup_count = 0
        self._cache_hits = 0
        self._load(address_cache_path)

    def _load(self, path: str) -> None:
        """Load address_cache.json + build kd-tree."""
        try:
            from scipy.spatial import cKDTree
            import numpy as np
        except ImportError as e:
            log.error(f"DistrictReverseLookup: scipy/numpy missing: {e}")
            return
        try:
            from dispatch_v2.common import drop_zone_from_address
            self._drop_zone_fn = drop_zone_from_address
        except ImportError as e:
            log.error(f"DistrictReverseLookup: drop_zone_from_address import fail: {e}")
            return
        try:
            data = json.load(open(path, encoding="utf-8"))
            entries = data.get("entries", {})
            coords = []
            for k, v in entries.items():
                if not isinstance(v, dict):
                    continue
                lat = v.get("lat")
                lon = v.get("lon")
                if lat is None or lon is None:
                    continue
                parsed = v.get("parsed") or {}
                self._addresses.append({
                    "street": parsed.get("street"),
                    "city": parsed.get("city"),
                    "lat": lat,
                    "lon": lon,
                })
                coords.append([lat, lon])
            if coords:
                self._tree = cKDTree(np.array(coords))
            self._loaded = True
            log.info(
                f"DistrictReverseLookup: loaded {len(self._addresses)} geocoded addresses, "
                f"kd-tree ready"
            )
        except FileNotFoundError:
            log.warning(f"DistrictReverseLookup: address_cache not found at {path}")
        except Exception as e:
            log.error(f"DistrictReverseLookup load fail: {e}", exc_info=True)
            self._loaded = False

    def lookup(self, lat, lon) -> str:
        """Coord → district name. Returns 'Unknown' on any failure."""
        self._lookup_count += 1
        if not self._loaded or self._tree is None or self._drop_zone_fn is None:
            return "Unknown"
        if lat is None or lon is None:
            return "Unknown"
        try:
            lat_f = float(lat)
            lon_f = float(lon)
            if math.isnan(lat_f) or math.isnan(lon_f):
                return "Unknown"
        except (TypeError, ValueError):
            return "Unknown"
        key = (round(lat_f, 3), round(lon_f, 3))
        if key in self._coord_cache:
            self._cache_hits += 1
            return self._coord_cache[key]
        try:
            _, idx = self._tree.query([lat_f, lon_f], k=1)
            entry = self._addresses[int(idx)]
            zone = self._drop_zone_fn(entry["street"], entry["city"]) or "Unknown"
            self._coord_cache[key] = zone
            return zone
        except Exception as e:
            log.debug(f"DistrictReverseLookup lookup fail ({lat}, {lon}): {e}")
            self._coord_cache[key] = "Unknown"
            return "Unknown"

    def stats(self) -> Dict:
        return {
            "loaded": self._loaded,
            "n_addresses": len(self._addresses),
            "lookups": self._lookup_count,
            "cache_hits": self._cache_hits,
            "cache_size": len(self._coord_cache),
        }


def get_district_lookup() -> DistrictReverseLookup:
    """Singleton accessor. First call inicjalizuje (~1-2s), kolejne reuse."""
    global _instance
    if _instance is None:
        _instance = DistrictReverseLookup()
    return _instance
