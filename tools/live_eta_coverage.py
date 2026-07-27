#!/usr/bin/env python3
"""Read-only pomiar pokrycia snapshotu live ETA per źródło R3.

Mianownik to wszystkie stopy opublikowane przez jedynego producenta
``live_eta_daemon``. Narzędzie niczego nie przelicza i niczego nie zapisuje.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Mapping

from dispatch_v2 import live_eta


def summarize(store: Mapping[str, object]) -> dict:
    entries = store.get("entries")
    if not isinstance(entries, Mapping):
        entries = {}
    sources: Counter[str] = Counter()
    unpriced_reasons: Counter[str] = Counter()
    stops_total = 0
    stops_priced = 0
    invalid_sources = 0
    couriers_with_stops = 0

    for entry in entries.values():
        snapshot = entry.get("snapshot") if isinstance(entry, Mapping) else None
        stops = snapshot.get("stops") if isinstance(snapshot, Mapping) else None
        if not isinstance(stops, list):
            continue
        if stops:
            couriers_with_stops += 1
        for stop in stops:
            if not isinstance(stop, Mapping):
                continue
            stops_total += 1
            source = str(stop.get("source") or "")
            if source in live_eta.ETA_SOURCES:
                sources[source] += 1
            else:
                invalid_sources += 1
            if stop.get("eta_at"):
                stops_priced += 1
            else:
                unpriced_reasons[
                    str(stop.get("unpriced_reason") or "unknown")
                ] += 1

    ordered_sources = {
        name: sources.get(name, 0)
        for name in (
            live_eta.SOURCE_LIVE,
            live_eta.SOURCE_WARM,
            live_eta.SOURCE_PLANNED,
        )
    }
    return {
        "couriers_total": len(entries),
        "couriers_with_stops": couriers_with_stops,
        "stops_total": stops_total,
        "stops_priced": stops_priced,
        "unpriced": stops_total - stops_priced,
        "sources": ordered_sources,
        "unpriced_reasons": dict(sorted(unpriced_reasons.items())),
        "invalid_sources": invalid_sources,
        "coverage_priced_pct": round(
            100.0 * stops_priced / stops_total, 1
        )
        if stops_total
        else 0.0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only coverage LIVE/WARM/PLANNED snapshotu live ETA"
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=live_eta.SNAPSHOT_FILE,
        help="plik live_eta_snapshot.json (tylko odczyt)",
    )
    parser.add_argument("--json", action="store_true", help="wyjście JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with args.snapshot.open(encoding="utf-8") as handle:
            store = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        print(f"HOLD: nie można odczytać snapshotu: {exc}")
        return 2
    report = summarize(store if isinstance(store, Mapping) else {})
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        src = report["sources"]
        print(
            "LIVE_ETA_COVERAGE "
            f"stops={report['stops_total']} priced={report['stops_priced']} "
            f"live={src['live']} warm={src['warm']} planned={src['planned']} "
            f"unpriced={report['unpriced']} "
            f"invalid_source={report['invalid_sources']} "
            f"coverage={report['coverage_priced_pct']:.1f}%"
        )
        if report["unpriced_reasons"]:
            print(
                "UNPRICED_REASONS "
                + json.dumps(
                    report["unpriced_reasons"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
    return 0 if report["invalid_sources"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
