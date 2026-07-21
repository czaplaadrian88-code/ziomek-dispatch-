#!/usr/bin/env python3
"""Czytelnik zbiorczej metryki czasowka-reclaim z deduplikacja generacji."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dispatch_v2.czasowka_reclaim import read_shadow_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(read_shadow_metrics(args.path), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
