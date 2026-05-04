"""TASK B SHIFT NOTIFICATIONS — pure batch-bucketing function (2026-05-04).

Groups candidates by (date, slot_index) where slot_index = (hour*60+min)//window.
Buckets with >= batch_min_couriers emit ('batch', [all]).
Otherwise each candidate emits ('individual', [single]).

Edge-8: cross-midnight handled correctly because date is part of bucket key
(23:55 today and 00:05 tomorrow live in different buckets).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class Candidate:
    full_name: str
    cid: str
    shift_dt: datetime  # tz-aware Warsaw


def bucket_by_slot(
    candidates: Iterable[Candidate],
    batch_window_min: int,
    batch_min_couriers: int,
) -> List[Tuple[str, List[Candidate]]]:
    """Returns list of ('batch'|'individual', [candidates]).

    Bucket key = (shift_dt.date(), (hour*60 + minute) // batch_window_min).
    Bucket with >= batch_min_couriers → single ('batch', candidates) tuple.
    Bucket with <  batch_min_couriers → one ('individual', [c]) per candidate.

    Stable ordering: emits buckets in order they first appear in the input.
    Within each emit, candidates preserve input order.
    """
    if batch_window_min <= 0:
        raise ValueError("batch_window_min must be > 0")
    if batch_min_couriers < 1:
        raise ValueError("batch_min_couriers must be >= 1")

    # Maintain insertion-order via list of bucket keys + dict of buckets
    buckets: dict = {}
    order: list = []
    for c in candidates:
        slot = (c.shift_dt.hour * 60 + c.shift_dt.minute) // batch_window_min
        key = (c.shift_dt.date(), slot)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(c)

    result: List[Tuple[str, List[Candidate]]] = []
    for key in order:
        members = buckets[key]
        if len(members) >= batch_min_couriers:
            result.append(("batch", list(members)))
        else:
            for m in members:
                result.append(("individual", [m]))
    return result
