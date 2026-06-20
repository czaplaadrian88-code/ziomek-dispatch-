"""Test detekcji B1 (loadgov_gate_replay): logika "KOORD wyłącznie z winy
LOADGOV" na syntetycznych rekordach.

Sprawdza wzór:
    score(z deltą) < -100  ∧  score - loadgov_delta >= -100  ∧  loadgov_active

Przypadki:
  - WYPCHNIĘTY-TYLKO-PRZEZ-LOADGOV: score=-130, loadgov=-40 → bez loadgov -90 ≥ -100 → flagged
  - NISKO-Z-INNYCH-POWODÓW: score=-454, loadgov=-40 → bez loadgov -414 < -100 → NIE flagged
  - LOADGOV NIEAKTYWNY: score=-130, loadgov=0 → active=False → NIE flagged
  - GRANICA: score=-140, loadgov=-40 → bez loadgov -100 == próg → flagged (>=)
  - PROPOSE: ignorowany (nie KOORD)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import loadgov_gate_replay as R  # noqa: E402


def _rec(verdict="KOORD", reason="all_candidates_low_score (best=1 score=-130<-100; feasible=2)",
         score=-130.0, loadgov_delta=-40.0, bag=3, ewma=2.9, ts="2026-06-12T11:30:00+00:00"):
    best = {"score": score, "r6_bag_size": bag, "loadgov_load_ewma": ewma}
    if loadgov_delta is not None:
        best["bonus_loadgov_shadow_delta"] = loadgov_delta
    return {"verdict": verdict, "reason": reason, "ts": ts, "order_id": 999, "best": best}


def _write(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def test_pushed_only_by_loadgov_is_flagged():
    p = _write([_rec(score=-130.0, loadgov_delta=-40.0)])
    try:
        s = R.analyze([p])
    finally:
        os.unlink(p)
    assert s["blamed_on_loadgov"] == 1
    assert s["legit_even_without_loadgov"] == 0
    assert s["blamed_peak"] == 1  # 11:30 Warsaw = lunch peak


def test_low_from_other_reasons_not_flagged():
    p = _write([_rec(score=-454.0, loadgov_delta=-40.0)])
    try:
        s = R.analyze([p])
    finally:
        os.unlink(p)
    assert s["blamed_on_loadgov"] == 0
    # loadgov aktywny ale bez niego nadal < -100 → legit KOORD
    assert s["legit_even_without_loadgov"] == 1


def test_loadgov_inactive_not_flagged():
    p = _write([_rec(score=-130.0, loadgov_delta=0.0)])
    try:
        s = R.analyze([p])
    finally:
        os.unlink(p)
    assert s["blamed_on_loadgov"] == 0
    assert s["low_score_loadgov_active"] == 0


def test_boundary_exactly_at_threshold_is_flagged():
    # score=-140, loadgov=-40 → bez loadgov = -100 == próg, warunek >= → flagged
    p = _write([_rec(score=-140.0, loadgov_delta=-40.0)])
    try:
        s = R.analyze([p])
    finally:
        os.unlink(p)
    assert s["blamed_on_loadgov"] == 1


def test_propose_ignored():
    p = _write([_rec(verdict="PROPOSE", score=-130.0, loadgov_delta=-40.0)])
    try:
        s = R.analyze([p])
    finally:
        os.unlink(p)
    assert s["koord_total"] == 0
    assert s["low_score_total"] == 0
    assert s["blamed_on_loadgov"] == 0


def test_missing_loadgov_field_treated_as_inactive():
    # brak bonus_loadgov_shadow_delta → ld_eff=0, active=False → nie flagged
    p = _write([_rec(score=-130.0, loadgov_delta=None)])
    try:
        s = R.analyze([p])
    finally:
        os.unlink(p)
    assert s["low_score_with_loadgov_field"] == 0
    assert s["blamed_on_loadgov"] == 0


def test_peak_offpeak_split():
    recs = [
        _rec(score=-130.0, loadgov_delta=-40.0, ts="2026-06-12T11:30:00+00:00"),  # 13:30 W -> peak (13<14)
        _rec(score=-130.0, loadgov_delta=-40.0, ts="2026-06-12T06:00:00+00:00"),  # 08:00 W -> off-peak
    ]
    p = _write(recs)
    try:
        s = R.analyze([p])
    finally:
        os.unlink(p)
    assert s["blamed_on_loadgov"] == 2
    assert s["blamed_peak"] == 1
    assert s["blamed_offpeak"] == 1


def test_parse_fail_is_counted_not_crash():
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write("{ to nie jest json\n")
        f.write(json.dumps(_rec(score=-130.0, loadgov_delta=-40.0)) + "\n")
    try:
        s = R.analyze([p])
    finally:
        os.unlink(p)
    assert s["parse_fail"] == 1
    assert s["blamed_on_loadgov"] == 1
