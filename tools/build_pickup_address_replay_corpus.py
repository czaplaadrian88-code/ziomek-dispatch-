#!/usr/bin/env python3
"""Zbuduj hermetyczny korpus WYŁĄCZNIE z pól pickup żywego orders_state.

Skrypt czyta źródło bez zapisu/lockowania i nie wypisuje adresów.  Wynik pomija
ID, nazwy restauracji, adresy dostawy, współrzędne, kurierów i historię.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


RUNTIME_STATE = Path("/root/.openclaw/workspace/dispatch_state").resolve()


def _stable_read(path: Path) -> tuple[bytes, os.stat_result]:
    for _attempt in range(5):
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        identity_before = (before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before == identity_after and len(raw) == after.st_size:
            return raw, after
    raise RuntimeError("orders_state changed during every read attempt")


def _atomic_json_write(path: Path, payload: dict) -> None:
    resolved_parent = path.parent.resolve()
    if resolved_parent == RUNTIME_STATE or RUNTIME_STATE in resolved_parent.parents:
        raise ValueError("output inside live dispatch_state is forbidden")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def build(source: Path) -> dict:
    raw, source_stat = _stable_read(source)
    state = json.loads(raw)
    if not isinstance(state, dict):
        raise ValueError("orders_state root must be an object")
    pairs = set()
    records_with_pickup = 0
    for record in state.values():
        if not isinstance(record, dict):
            continue
        address = record.get("pickup_address")
        if not isinstance(address, str) or not address.strip():
            continue
        records_with_pickup += 1
        clean_address = " ".join(address.strip().split())
        city = record.get("pickup_city")
        clean_city = " ".join(city.strip().split()) if isinstance(city, str) else ""
        pairs.add((clean_address, clean_city))
    cases = [
        {"pickup_address": address, "pickup_city": city}
        for address, city in sorted(pairs, key=lambda item: (item[0].casefold(), item[1].casefold()))
    ]
    return {
        "schema": "pickup-address-live-replay.v1",
        "source": {
            "kind": "read-only-orders-state",
            "basename": source.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            "mtime_ns": source_stat.st_mtime_ns,
            "records_total": len(state),
            "records_with_pickup": records_with_pickup,
            "unique_pickup_pairs": len(cases),
        },
        "privacy": {
            "included_fields": ["pickup_address", "pickup_city"],
            "excluded_classes": [
                "order_ids",
                "restaurant_names",
                "delivery_addresses",
                "coordinates",
                "courier_identity",
                "history",
            ],
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.source)
    _atomic_json_write(args.output, payload)
    summary = {
        "output": str(args.output),
        "source_sha256": payload["source"]["sha256"],
        "records_total": payload["source"]["records_total"],
        "unique_pickup_pairs": payload["source"]["unique_pickup_pairs"],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
