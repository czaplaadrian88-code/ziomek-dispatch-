#!/usr/bin/env python3
"""Aggregate-only, read-only frozen replay eta_calibration.

Skrypt czyta SQLite w `mode=ro`, pseudonimizuje kazdy rekord natychmiast w
pamieci i emituje wylacznie agregaty + fingerprint supportu. Nie zapisuje DB,
map, logow ani rekordow per-order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3

from dispatch_v2.tools.eta_calibration import evaluate as E
from dispatch_v2.tools.eta_calibration import features as F
from dispatch_v2.tools.eta_calibration import models as M


def _hash(kind: str, value: object) -> str:
    raw = f"a360-a0-frozen-v1:{kind}:{value}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def _surrogate_coords(lat, lon):
    if lat is None or lon is None:
        return None, None, None
    key = _hash("restaurant", f"{lat}:{lon}")
    # Stary model v1 nie zna `restaurant_key`, dlatego dostaje stabilne,
    # syntetyczne coords. Nie zachowuja polozenia, tylko identycznosc kategorii.
    digest = hashlib.sha256(key.encode("ascii")).hexdigest()
    lat_s = int(digest[:8], 16) / 100_000_000.0
    lon_s = int(digest[8:16], 16) / 100_000_000.0
    return key, lat_s, lon_s


def anonymize_row(source: dict) -> dict:
    row = dict(source)
    row["order_id"] = _hash("order", source.get("order_id"))
    row["courier_id"] = _hash("courier", source.get("courier_id"))
    rest_key, rest_lat, rest_lon = _surrogate_coords(
        source.get("rest_lat"), source.get("rest_lon"),
    )
    row["restaurant_key"] = rest_key
    row["rest_lat"], row["rest_lon"] = rest_lat, rest_lon
    row["deliv_lat"] = None
    row["deliv_lon"] = None
    return row


def load_rows_readonly(db_path: str) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [
            anonymize_row(dict(record))
            for record in con.execute(
                "SELECT * FROM eta_calib_features ORDER BY ts_pickup"
            )
        ]
    finally:
        con.close()


def run(db_path: str, cfg: dict, label: str) -> dict:
    rows = load_rows_readonly(db_path)
    train, hold, cut = E.time_split(rows, cfg["window"]["holdout_days"])
    support_keys = sorted(
        f"{row.get('day')}:{row.get('order_id')}" for row in hold
    )
    out = {
        "label": label,
        "feature_contract": getattr(M, "FEATURE_CONTRACT_VERSION", "outcome_leaky_v1"),
        "verdict": "UNBOUND",
        "corpus": {
            "n_total": len(rows),
            "n_train": len(train),
            "n_holdout_rows": len(hold),
            "cut_day": cut,
            "support_fingerprint": hashlib.sha256(
                "|".join(support_keys).encode("utf-8")
            ).hexdigest(),
        },
        "legs": {},
    }
    for leg in (M.PICKUP, M.DELIVERY):
        result = E.evaluate_leg(train, hold, leg, cfg)
        champion = result["champion"]
        eligible = []
        for row in hold:
            target = (
                row.get("pickup_slip_koord_min")
                if leg == M.PICKUP else row.get("actual_deliver_min")
            )
            if target is None:
                continue
            if leg == M.DELIVERY and row.get("osrm_deliv_ff_min") is None:
                continue
            eligible.append(f"{row.get('day')}:{row.get('order_id')}")
        out["legs"][leg] = {
            "challenger": champion,
            "n_holdout": result["n_holdout"],
            "eligible_support_fingerprint": hashlib.sha256(
                "|".join(sorted(eligible)).encode("utf-8")
            ).hexdigest(),
            "mae": result["models"][champion]["mae"],
            "ci_mae": result["models"][champion]["ci_mae"],
            "baselines": {
                name: {"n": value.get("n"), "mae": value.get("mae")}
                for name, value in result["baselines"].items()
            },
            "coverage": result["coverage"],
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite feature-store; otwierany mode=ro")
    parser.add_argument("--config", default=None)
    parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    cfg = F.load_config(args.config)
    print(json.dumps(run(args.db, cfg, args.label), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
