#!/usr/bin/env python3
"""Source-owned daily reset for the shared manual-overrides store.

The live unit is intentionally not switched by this source-only candidate.
Promotion requires a separate owner-approved cutover because the currently
installed host script still writes the store outside the canonical lock.
"""
from __future__ import annotations

import sys

from dispatch_v2 import courier_availability


def _reset_coordinator_activations() -> int:
    """Preserve the existing independent coordinator-activation lifecycle."""

    try:
        from dispatch_v2 import coordinator_activations

        count = coordinator_activations.reset_all(source="daily_06:00_reset")
        print(f"coordinator_activations: cleared={int(count)}")
    except Exception as exc:
        print(
            "WARN coordinator_activations reset failed: "
            f"{type(exc).__name__}",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    try:
        counts = courier_availability.reset_legacy_fields()
    except Exception as exc:
        print(
            f"ERROR manual_overrides reset failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1

    print(
        "manual_overrides: "
        f"excluded={counts['excluded']} "
        f"excluded_cids={counts['excluded_cids']} "
        f"working={counts['working']}"
    )
    return _reset_coordinator_activations()


if __name__ == "__main__":
    raise SystemExit(main())
