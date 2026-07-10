"""Jednorazowa inwalidacja corrupted geocode_cache entries (city bug 2026-04-19).

Target: 8 entries z coords poza bbox Białystok+15km — Google zwróciło match
w innym województwie mimo "Białystok, Polska" w query. Te pozostają stale
w cache i zwracają fałszywe coords dopóki nie są usunięte.

Usage:
    python3 -m dispatch_v2.tools.invalidate_city_bugged_geocodes [--dry-run]

Safety:
    - Backup pre-execution do geocode_cache.json.bak-city-invalidation-<ts>
    - Atomic write (temp → fsync → rename), LOCK_EX per _save_cache pattern
    - Dry-run mode default (--execute wymagany do faktycznego usunięcia)
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from dispatch_v2.geocoding import _mutate_cache

CACHE_PATH = Path("/root/.openclaw/workspace/dispatch_state/geocode_cache.json")

# Szeroka bbox: Białystok + okolice (Kleosin, Ignatki, Wasilków, Supraśl, Choroszcz)
BBOX_LAT = (52.85, 53.35)
BBOX_LON = (22.85, 23.45)


def _bad_entries(cache: dict) -> list[tuple[str, str]]:
    bad = []
    for key, value in cache.items():
        lat = value.get("lat")
        lon = value.get("lon")
        if lat is None or lon is None:
            bad.append((key, "no coords"))
            continue
        if not (BBOX_LAT[0] <= lat <= BBOX_LAT[1]
                and BBOX_LON[0] <= lon <= BBOX_LON[1]):
            bad.append((key, f"out of bbox: ({lat:.4f},{lon:.4f})"))
    return bad


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true",
                   help="Faktycznie usuń (domyślnie dry-run)")
    args = p.parse_args()

    if not CACHE_PATH.exists():
        print(f"NIE ZNALEZIONO: {CACHE_PATH}")
        sys.exit(1)

    cache = json.loads(CACHE_PATH.read_text())
    total_before = len(cache)

    # Identify bad entries for the dry-run/report. Execute recomputes this under
    # the canonical cache lock so a concurrent insert cannot be overwritten.
    bad = _bad_entries(cache)

    print(f"Cache: {total_before} entries")
    print(f"Corrupted (out of bbox or missing coords): {len(bad)}")
    print()
    for k, reason in bad:
        print(f"  DELETE {k!r} [{reason}]")

    if not bad:
        print("\nNic do zrobienia.")
        sys.exit(0)

    if not args.execute:
        print(f"\n[DRY-RUN] — dodaj --execute żeby faktycznie usunąć {len(bad)} entries")
        sys.exit(0)

    outcome = {"before": total_before, "after": total_before, "backup": None}

    def _delete_bad(current: dict) -> bool:
        current_bad = _bad_entries(current)
        outcome["before"] = len(current)
        if not current_bad:
            outcome["after"] = len(current)
            return False
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = CACHE_PATH.with_name(
            f"{CACHE_PATH.name}.bak-city-invalidation-{ts}"
        )
        backup.write_text(
            CACHE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        for key, _ in current_bad:
            del current[key]
        outcome.update({
            "after": len(current),
            "backup": backup,
        })
        return True

    _mutate_cache(CACHE_PATH, _delete_bad)
    if outcome["backup"] is not None:
        print(f"\nBackup: {outcome['backup']}")
    print(
        f"Zapisane: {CACHE_PATH} ({outcome['after']} entries, "
        f"było {outcome['before']})"
    )


if __name__ == "__main__":
    main()
