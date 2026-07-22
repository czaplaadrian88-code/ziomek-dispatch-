"""Jawny model pozycji kuriera i kosztu pierwszej nogi.

Moduł jest liściem domenowym: nie czyta flag, stanu ani sieci.  Klasyfikacja
pozycji odbywa się z provenance rezolwera, a nie z etykiety użytej przez UI.
UNKNOWN nigdy nie niesie współrzędnych.  Białostocki profil v1 jest jednym
źródłem stałych 6.5 km / 15 min SOFT / 22 min HARD.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


Coords = Tuple[float, float]


class PositionKind(str, Enum):
    KNOWN_LIVE = "KNOWN_LIVE"
    KNOWN_ANCHOR = "KNOWN_ANCHOR"
    UNKNOWN = "UNKNOWN"


class PositionProvenance(str, Enum):
    GPS_LIVE = "GPS_LIVE"
    LAST_KNOWN_TTL = "LAST_KNOWN_TTL"
    BAG_EVENT_ANCHOR = "BAG_EVENT_ANCHOR"
    RECENT_EVENT_ANCHOR = "RECENT_EVENT_ANCHOR"
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"
    INVALID_COORDS = "INVALID_COORDS"


@dataclass(frozen=True)
class ResolvedPosition:
    position_kind: PositionKind
    provenance: PositionProvenance
    coords: Optional[Coords]
    source: Optional[str]
    age_min: Optional[float]

    def __post_init__(self) -> None:
        if self.position_kind is PositionKind.UNKNOWN and self.coords is not None:
            raise ValueError("UNKNOWN position cannot carry coordinates")
        if self.position_kind is not PositionKind.UNKNOWN and self.coords is None:
            raise ValueError("known position requires coordinates")

    @property
    def known(self) -> bool:
        """Kompatybilny predykat dla dotychczasowych konsumentów rezolwera."""
        return self.position_kind is not PositionKind.UNKNOWN


@dataclass(frozen=True)
class OriginTravelEstimate:
    provenance: str
    road_km: float
    drive_min_soft: float
    drive_min_hard: float
    has_origin_geometry: bool

    def __post_init__(self) -> None:
        if self.road_km < 0 or self.drive_min_soft < 0 or self.drive_min_hard < 0:
            raise ValueError("origin travel estimates must be non-negative")
        if self.drive_min_hard < self.drive_min_soft:
            raise ValueError("HARD origin cost cannot be below SOFT cost")


UNKNOWN_BIALYSTOK_V1_ROAD_KM = 6.5
UNKNOWN_BIALYSTOK_V1_DRIVE_MIN_SOFT = 15.0
UNKNOWN_BIALYSTOK_V1_DRIVE_MIN_HARD = 22.0
UNKNOWN_BIALYSTOK_V1_PROVENANCE = "UNKNOWN_PROFILE/BIALYSTOK_V1"


_UNKNOWN_SOURCES = frozenset({
    "no_gps",
    "pre_shift",
    "none",
    "pin",
    "post_shift_start_synthetic",
    "working_override_synthetic",
})
_BAG_ANCHOR_SOURCES = frozenset({
    "last_picked_up_interp",
    "last_picked_up_pickup",
    "last_picked_up_delivery",
    "last_assigned_pickup",
})
_RECENT_ANCHOR_SOURCES = frozenset({
    "last_delivered",
    "last_picked_up_recent",
})


def _valid_coords(coords) -> Optional[Coords]:
    if not isinstance(coords, (tuple, list)) or len(coords) != 2:
        return None
    try:
        lat, lon = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if (lat, lon) == (0.0, 0.0):
        return None
    return (lat, lon)


def resolve_position(
    *,
    coords,
    source: Optional[str],
    age_min: Optional[float] = None,
    from_store: bool = False,
    provenance: Optional[PositionProvenance] = None,
) -> ResolvedPosition:
    """Zbuduj domenową pozycję z provenance rezolwera.

    ``source`` jest sygnałem provenance, nie etykietą prezentacyjną: GPS
    odtworzony ze store jest kotwicą, a syntetyczne źródło pozostaje UNKNOWN
    nawet jeśli legacy ``coords`` zawiera centrum miasta.
    """
    src = str(source) if source is not None else None
    if provenance is None:
        if src in _UNKNOWN_SOURCES or src is None:
            provenance = PositionProvenance.UNKNOWN_PROFILE
        elif src == "gps" and not from_store:
            provenance = PositionProvenance.GPS_LIVE
        elif from_store:
            provenance = PositionProvenance.LAST_KNOWN_TTL
        elif src in _BAG_ANCHOR_SOURCES:
            provenance = PositionProvenance.BAG_EVENT_ANCHOR
        elif src in _RECENT_ANCHOR_SOURCES:
            provenance = PositionProvenance.RECENT_EVENT_ANCHOR
        else:
            provenance = PositionProvenance.BAG_EVENT_ANCHOR
    elif not isinstance(provenance, PositionProvenance):
        provenance = PositionProvenance(str(provenance))

    if provenance in {PositionProvenance.UNKNOWN_PROFILE, PositionProvenance.INVALID_COORDS}:
        return ResolvedPosition(
            PositionKind.UNKNOWN,
            provenance,
            None,
            src,
            age_min,
        )

    valid = _valid_coords(coords)
    if valid is None:
        return ResolvedPosition(
            PositionKind.UNKNOWN,
            PositionProvenance.INVALID_COORDS,
            None,
            src,
            age_min,
        )

    if provenance is PositionProvenance.GPS_LIVE:
        kind = PositionKind.KNOWN_LIVE
    else:
        kind = PositionKind.KNOWN_ANCHOR
    return ResolvedPosition(kind, provenance, valid, src, age_min)


def resolve_courier_position(courier_state) -> ResolvedPosition:
    provenance = getattr(courier_state, "position_provenance", None)
    return resolve_position(
        coords=getattr(courier_state, "pos", None),
        source=getattr(courier_state, "pos_source", None),
        age_min=getattr(courier_state, "pos_age_min", None),
        from_store=bool(getattr(courier_state, "pos_from_store", False)),
        provenance=provenance,
    )


def unknown_origin_estimate() -> OriginTravelEstimate:
    return OriginTravelEstimate(
        provenance=UNKNOWN_BIALYSTOK_V1_PROVENANCE,
        road_km=UNKNOWN_BIALYSTOK_V1_ROAD_KM,
        drive_min_soft=UNKNOWN_BIALYSTOK_V1_DRIVE_MIN_SOFT,
        drive_min_hard=UNKNOWN_BIALYSTOK_V1_DRIVE_MIN_HARD,
        has_origin_geometry=False,
    )


def origin_estimate_for(position: ResolvedPosition) -> Optional[OriginTravelEstimate]:
    """UNKNOWN ma profil stały; znana pozycja jest liczona realnym routingiem."""
    return unknown_origin_estimate() if position.position_kind is PositionKind.UNKNOWN else None


def shadow_position(position: ResolvedPosition) -> dict:
    """PII-free projekcja do shadow logu (bez współrzędnych)."""
    return {
        "position_kind": position.position_kind.value,
        "position_source": position.source,
        "position_provenance": position.provenance.value,
        "position_age_min": (
            round(float(position.age_min), 1) if position.age_min is not None else None
        ),
    }
