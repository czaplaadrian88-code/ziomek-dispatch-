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
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

CACHE_PATH = Path("/root/.openclaw/workspace/dispatch_state/geocode_cache.json")

# Szeroka bbox: Białystok + okolice (Kleosin, Ignatki, Wasilków, Supraśl, Choroszcz)
BBOX_LAT = (52.85, 53.35)
BBOX_LON = (22.85, 23.45)


def _atomic_write(path: Path, data: dict):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


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

    # Identify bad entries
    bad = []
    for k, v in cache.items():
        lat = v.get("lat")
        lon = v.get("lon")
        if lat is None or lon is None:
            bad.append((k, "no coords"))
            continue
        if not (BBOX_LAT[0] <= lat <= BBOX_LAT[1] and BBOX_LON[0] <= lon <= BBOX_LON[1]):
            bad.append((k, f"out of bbox: ({lat:.4f},{lon:.4f})"))

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

    # Backup
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = CACHE_PATH.with_name(f"{CACHE_PATH.name}.bak-city-invalidation-{ts}")
    backup.write_text(CACHE_PATH.read_text())
    print(f"\nBackup: {backup}")

    # Delete
    for k, _ in bad:
        del cache[k]

    _atomic_write(CACHE_PATH, cache)
    print(f"Zapisane: {CACHE_PATH} ({len(cache)} entries, było {total_before})")


if __name__ == "__main__":
    main()
