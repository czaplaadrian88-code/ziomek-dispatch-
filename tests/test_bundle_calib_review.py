#!/usr/bin/env python3
"""Testy dla tools/bundle_calib_review.py — konsumpcja under_z (kalibracja X/Y/Z,
Opcja 3 Adriana 2026-06-25). Syntetyczne rekordy, zero realnych danych / I/O.

Sprawdza: (1) helpery _o2_of/_p90/_zkeys_in_corpus, (2) _calib_under_z liczy
feasible/improved/detour/calib_exceeds pod twardym capem świeżości carried,
(3) _verdict bazuje na policy-Z (nie surowym O2-pułapie): GO gdy któryś cap ≥20%,
NO-GO gdy pułap materialny ale ŻADEN cap nie spełnia, INCONCLUSIVE przy małym coverage.
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.tools import bundle_calib_review as B  # noqa: E402


# ── helpery ─────────────────────────────────────────────────────────────────
def test_o2_of():
    # GATE = overage-ONLY (audyt 28.06 #1 — parytet z silnikiem o2_score; czas_late=FAZA 2,
    # NIE wchodzi do gate'u GO/under_z). Inwariant: czas_late NIE wpływa na O2 gate.
    assert B._o2_of({"overage": 4.0, "czas_late": 2.0}) == 4.0
    assert B._o2_of({"overage": 4.0, "czas_late": 99.0}) == 4.0   # czas_late ignorowany w gate
    assert B._o2_of(None) == 0.0
    assert B._o2_of({}) == 0.0


def test_p90():
    assert B._p90([]) is None
    assert B._p90([5.0]) == 5.0
    assert B._p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 9   # nearest-rank: idx round(0.9*9)=8
    assert B._p90([None, 3.0, None, 1.0]) is not None


def test_zkeys_in_corpus_discovers_and_sorts():
    corpus = [{"under_z": None}, {"under_z": {"35": {}, "20": {}, "32": {}}}]
    assert B._zkeys_in_corpus(corpus) == ["20", "32", "35"]
    assert B._zkeys_in_corpus([{"foo": 1}]) == []


# ── _calib_under_z ──────────────────────────────────────────────────────────
def _mk(cid, s_over, s_cz, under_z, calib_age):
    return {
        "cid": cid, "bag_sig": f"sig{cid}", "n_orders": 2,
        "order_ids": [f"{cid}a", f"{cid}b"],
        "served_seq": [["pickup", f"{cid}a"]], "calib_seq": [["pickup", f"{cid}b"]],
        "m_served": {"overage": s_over, "czas_late": s_cz, "r6_ready": 0,
                     "finish_in_min": 90.0, "drive_min": 50.0},
        "m_calib": {"overage": 0.0, "czas_late": 0.0, "r6_ready": 0,
                    "finish_in_min": 88.0, "drive_min": 52.0},
        "calib_max_carried_age": calib_age,
        "under_z": under_z,
    }


def test_calib_under_z_improved_cap_and_exceeds():
    # served O2=20; pod Z≤32 istnieje przeplot O2=5 (gain 15), carried 30≤32, drive 51 (detour +1);
    # pod Z≤20 brak feasible (None); surowy CALIB carried=40 → przekracza WSZYSTKIE capy
    uz = {
        "20": None,
        "32": {"overage": 5.0, "czas_late": 0.0, "drive_min": 51.0, "max_carried_age": 30.0},
        "35": {"overage": 5.0, "czas_late": 0.0, "drive_min": 51.0, "max_carried_age": 30.0},
    }
    differs = [_mk(1, 20.0, 0.0, uz, 40.0)]
    out = B._calib_under_z(differs, differs, ["20", "32", "35"])
    assert out["_coverage"] == 1
    c20, c32 = out["caps"]["20"], out["caps"]["32"]
    assert c20["feasible"] == 0 and c20["improved"] == 0       # under_z[20]=None
    assert c32["feasible"] == 1 and c32["improved"] == 1       # gain 15 ≥ MATERIAL_O2_MIN
    assert c32["med_gain_o2"] == 15.0
    assert c32["med_detour_min"] == 1.0                        # 51 - 50
    assert c20["calib_exceeds_pct"] == 100.0                   # surowy CALIB 40 > 20
    assert c32["calib_exceeds_pct"] == 100.0                   # 40 > 32


def test_calib_under_z_feasible_but_not_improved():
    # pod Z≤35 istnieje przeplot, ale O2 nie lepszy od served (gain < MATERIAL_O2_MIN) → feasible, NIE improved
    uz = {"35": {"overage": 19.5, "czas_late": 0.0, "drive_min": 50.0, "max_carried_age": 34.0}}
    differs = [_mk(2, 20.0, 0.0, uz, 34.0)]   # served O2=20, uz O2=19.5 → gain 0.5 < 2
    out = B._calib_under_z(differs, differs, ["35"])
    c = out["caps"]["35"]
    assert c["feasible"] == 1 and c["improved"] == 0
    assert c["calib_exceeds_pct"] == 0.0       # calib age 34 ≤ 35


def test_calib_under_z_skips_records_without_under_z():
    differs = [{"cid": 9, "under_z": None, "m_served": {}, "calib_max_carried_age": 50}]
    out = B._calib_under_z(differs, differs, ["20"])
    assert out["_coverage"] == 0
    assert out["caps"]["20"]["feasible"] == 0


# ── _verdict (policy-Z, nie surowy O2-pułap) ────────────────────────────────
def _cap(impr, det_med=2.0, det_p90=5.0, gain=6.0, feas=60.0, exceeds=50.0):
    return {"improved_pct": impr, "feasible_pct": feas, "med_gain_o2": gain,
            "med_detour_min": det_med, "p90_detour_min": det_p90, "calib_exceeds_pct": exceeds}


def _rep(coverage, caps, impO2=40.0):
    return {"multi_uniq": 30, "improved_o2_pct": impO2, "med_d_o2": 5.0,
            "regress_o2_pct": 0.0, "under_z": {"_coverage": coverage, "caps": caps}}


def test_verdict_go_recommends_smallest_passing_cap(monkeypatch):
    monkeypatch.setattr(B, "MIN_MULTI", 2)
    monkeypatch.setattr(B, "MATERIAL_PCT", 20.0)  # pin progu — test LOGIKI (smallest passing cap),
    # niezależny od produkcyjnej wartości (Adrian 29.06 = 2%); caps 5/25/28% → passing {32,35} → Z=32
    rep = _rep(25, {"20": _cap(5.0), "32": _cap(25.0), "35": _cap(28.0)})
    v, rec = B._verdict(rep)
    assert v == "GO"
    assert "Z=32" in rec                       # najmniejszy cap ≥próg = max ochrona carried
    assert "NIE flipować surowego O2" in rec


def test_verdict_nogo_when_ceiling_material_but_no_cap_passes(monkeypatch):
    monkeypatch.setattr(B, "MIN_MULTI", 2)
    monkeypatch.setattr(B, "MATERIAL_PCT", 20.0)  # pin progu — caps 2/5/8% < 20% → żaden nie przechodzi
    rep = _rep(25, {"20": _cap(2.0), "32": _cap(5.0), "35": _cap(8.0)}, impO2=40.0)
    v, rec = B._verdict(rep)
    assert v == "NO-GO"
    assert "wyłącznie kosztem świeżości" in rec


def test_verdict_inconclusive_when_under_z_coverage_low(monkeypatch):
    monkeypatch.setattr(B, "MIN_MULTI", 20)
    rep = _rep(3, {"32": _cap(25.0)})
    v, rec = B._verdict(rep)
    assert v == "INCONCLUSIVE"
    assert "coverage" in rec


def test_verdict_inconclusive_when_too_few_multi(monkeypatch):
    monkeypatch.setattr(B, "MIN_MULTI", 20)
    rep = _rep(25, {"32": _cap(25.0)})
    rep["multi_uniq"] = 5
    v, rec = B._verdict(rep)
    assert v == "INCONCLUSIVE"
    assert "multi-order" in rec


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


# ── #5b: fizyczna prawda dostawy (GPS) priorytet nad klikiem ─────────────────
def test_physical_delivered_index_parses(tmp_path, monkeypatch):
    """#5b: _physical_delivered_index czyta physical_delivered_at z gps_delivery_truth.jsonl."""
    import json
    p = tmp_path / "gps_delivery_truth.jsonl"
    p.write_text(
        json.dumps({"order_id": "111", "physical_delivered_at": "2026-06-10T12:13:55+00:00"}) + "\n"
        + json.dumps({"order_id": "222", "physical_delivered_at": None}) + "\n",  # brak → pominięte
        encoding="utf-8")
    monkeypatch.setattr(B, "GPS_TRUTH", str(p))
    idx = B._physical_delivered_index()
    assert "111" in idx and idx["111"] is not None
    assert "222" not in idx  # None physical → nie w indeksie (fallback do kliku)
