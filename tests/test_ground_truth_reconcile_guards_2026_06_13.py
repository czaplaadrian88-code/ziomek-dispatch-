"""B6 (2026-06-13) — regresja guardów konsumenta ground_truth (courier_gps_commitment_shadow.reconcile).

Lock zachowań, które czynią reassign-artefakty (B6) NIESZKODLIWYMI — żeby refaktor ich
nie usunął:
- wpis status-only (dojazd/odbior, BEZ picked_up_at/delivered_at) jest POMIJANY (zero
  rekordów) niezależnie od rozjazdu cid/statusu → phantom 370/dojazd dla doręczonego
  480342 nie zaśmiecał kalibracji.
- wpis Z FAKTEM GPS + rozjazd cid (gt vs state) → COURIER_MISMATCH (guard mis-atrybucji).
- wpis poza oknem (8h) → pominięty (nie re-flaguje trupów).
- sierota (fakt, brak ordera) → GPS_ORPHAN.

Czysta funkcja reconcile() — zero I/O. Tło: GC reassign-artefaktów =
eod_drafts/2026-06-13/gc_ground_truth_reassign_artifacts.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import courier_gps_commitment_shadow as shadow  # noqa: E402

NOW = 1_781_350_000.0  # stały epoch (test deterministyczny)


def _types(records):
    return [r["divergence_type"] for r in records]


def test_status_only_reassign_artifact_is_skipped():
    """B6: dojazd-only dla doręczonego (reassign 370→530, bez faktu GPS) → POMINIĘTY."""
    gt = {"480342": {"courier_id": "370", "last_status_code": 3, "last_status_label": "dojazd",
                     "last_status_at": int(NOW - 60), "source": "auto_geofence",
                     "updated_at": int(NOW - 60)}}
    orders = {"480342": {"courier_id": "530", "status": "delivered", "commitment_level": "planned"}}
    assert shadow.reconcile(gt, orders, NOW) == []


def test_fact_bearing_courier_mismatch_flagged():
    """Wpis z picked_up_at + rozjazd cid → COURIER_MISMATCH (guard nie zniknął)."""
    gt = {"X": {"courier_id": "370", "last_status_code": 5, "picked_up_at": int(NOW - 300),
                "last_status_at": int(NOW - 300), "updated_at": int(NOW - 300)}}
    orders = {"X": {"courier_id": "530", "status": "picked_up", "commitment_level": "picked_up",
                    "picked_up_at": "2026-06-13 13:00:00"}}
    assert "COURIER_MISMATCH" in _types(shadow.reconcile(gt, orders, NOW))


def test_orphan_flagged():
    """Wpis z faktem, brak ordera w state → GPS_ORPHAN."""
    gt = {"Z": {"courier_id": "1", "delivered_at": int(NOW - 100),
                "last_status_at": int(NOW - 100), "updated_at": int(NOW - 100)}}
    assert "GPS_ORPHAN" in _types(shadow.reconcile(gt, {}, NOW))


def test_stale_entry_outside_window_skipped():
    """Wpis starszy niż okno (8h) → pominięty (nie re-flaguje trupów)."""
    old = int(NOW - 20 * 3600)
    gt = {"Y": {"courier_id": "1", "picked_up_at": old, "last_status_at": old, "updated_at": old}}
    orders = {"Y": {"courier_id": "2", "status": "assigned"}}
    assert shadow.reconcile(gt, orders, NOW) == []


if __name__ == "__main__":
    fails = 0
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f(); print(f"  PASS  {_n}")
            except AssertionError as e:
                fails += 1; print(f"  FAIL  {_n}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAIL")
    sys.exit(1 if fails else 0)
