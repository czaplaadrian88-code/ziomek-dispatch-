"""Sprint 2/O2 ETAP 0 (2026-07-05) — ORACLE przyrządu bundle_calib_review (C9).

Handoff S2: „zanim zaufasz liczbom (7,9% / +10,4 min), wstrzyknij znany
przypadek i sprawdź, że przyrząd go widzi". Rejestr shadow-jobs miał CORE
validated (selekcja vs brute/OSRM), ale REVIEW-agregator (gate improved_o2/
regress_o2/med + kalibracja under_z) nie miał member-testu z niezależnie
policzoną odpowiedzią. Ten plik = golden/oracle case: syntetyczny korpus
z RĘCZNIE policzonymi wartościami → raport MUSI je odtworzyć 1:1.

Hermetyczny: korpus w tmp (env BUNDLE_CALIB_CORPUS przed reloadem modułu —
CORPUS czytany na module-level), outcome-join zmockowany na pusty (nie
czyta żywych logów). Semantyka ręcznych oczekiwań = kod review 190-295 +
125-160 (overage-ONLY gate #1 audytu; d_o2 = served − calib, >0 = CALIB
lepszy; MATERIAL_O2_MIN=2.0; regress < −0.01; under_z: gain = o2(served) −
o2(under_z[Z]), detour = drive(under_z) − drive(served)).
"""
import importlib
import json
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")


def _rec(cid, sig, served_o2, calib_o2, *, differs=True, served_drive=40.0,
         calib_drive=42.0, under_z=None, calib_max_age=None):
    seq_s = [["pickup", "1"], ["dropoff", "1"], ["dropoff", "2"]]
    seq_c = ([["dropoff", "2"], ["pickup", "1"], ["dropoff", "1"]]
             if differs else seq_s)
    r = {
        "ts": "2026-07-04T12:00:00+00:00", "cid": cid, "bag_sig": sig,
        "order_ids": ["1", "2"], "n_orders": 2, "n_carried": 1,
        "served_seq": seq_s, "calib_seq": seq_c,
        # czas_late CELOWO niezerowy i ASYMETRYCZNY (calib gorszy o 10):
        # gate = overage-ONLY (#1 audytu) — gdyby ktoś przywrócił λ·czas_late,
        # d_o2 się przesunie i oracle PADNIE (mutation-probe to potwierdza).
        "m_served": {"overage": served_o2, "czas_late": 0.0, "r6_ready": 1,
                     "finish_in_min": 50.0, "drive_min": served_drive},
        "m_calib": {"overage": calib_o2, "czas_late": 10.0, "r6_ready": 1,
                    "finish_in_min": 48.0, "drive_min": calib_drive},
        "deadlines": {}, "czas_kuriera": {}, "bundle_improved": False,
    }
    if under_z is not None:
        r["under_z"] = under_z
    if calib_max_age is not None:
        r["calib_max_carried_age"] = calib_max_age
    return r


def _run_review(tmp_path, monkeypatch, records):
    p = tmp_path / "corpus.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    monkeypatch.setenv("BUNDLE_CALIB_CORPUS", str(p))
    from dispatch_v2.tools import bundle_calib_review as BCR
    importlib.reload(BCR)  # CORPUS czytany na module-level z env
    assert BCR.CORPUS == str(p), "env-override korpusu nie zadziałał"
    # hermetyzacja: outcome-join nie czyta żywych logów
    monkeypatch.setattr(BCR, "_sla_delivered_index", lambda: {})
    monkeypatch.setattr(BCR, "_physical_delivered_index", lambda: {})
    return BCR.build_report()


def test_oracle_known_corpus_reproduced_exactly(tmp_path, monkeypatch):
    """3 worki: improved (ΔO2=+20), bez-różnicy (poza differs), regress (−4).
    Ręcznie: multi=3, differs=2, improved_o2=1, regress_o2=1,
    med_d_o2 = med(+20, −4) = 8.0."""
    records = [
        _rec("A", "s1", 25.0, 5.0, under_z={"20": {"overage": 8.0,
             "drive_min": 43.5}}, calib_max_age=22.0),
        _rec("B", "s2", 10.0, 10.0, differs=False),
        _rec("C", "s3", 5.0, 9.0),
    ]
    rep = _run_review(tmp_path, monkeypatch, records)
    assert rep["multi_uniq"] == 3, rep
    assert rep["differs"] == 2, rep
    assert rep["improved_o2"] == 1, rep
    assert rep["regress_o2"] == 1, rep
    assert rep["med_d_o2"] == 8.0, rep


def test_oracle_under_z_calibration_axis(tmp_path, monkeypatch):
    """Oś kalibracji Z (X/Y/Z Opcja 3): under_z['20'] — ręcznie:
    feasible=1, gain=25−8=17≥2 → improved=1, med_gain=17.0,
    detour=43.5−40=+3.5, calib_exceeds_pct=100.0 (1/1 have_uz:
    surowy CALIB max_age 22 > Z=20)."""
    records = [
        _rec("A", "s1", 25.0, 5.0, under_z={"20": {"overage": 8.0,
             "drive_min": 43.5}}, calib_max_age=22.0),
        _rec("C", "s3", 5.0, 9.0),
    ]
    rep = _run_review(tmp_path, monkeypatch, records)
    cap = rep["under_z"]["caps"]["20"]
    assert cap["feasible"] == 1, cap
    assert cap["improved"] == 1, cap
    assert cap["med_gain_o2"] == 17.0, cap
    assert cap["med_detour_min"] == 3.5, cap
    assert cap["calib_exceeds_pct"] == 100.0, cap
    assert rep["under_z"]["_coverage"] == 1, rep["under_z"]


def test_oracle_dedup_last_wins_same_bag(tmp_path, monkeypatch):
    """Dedup (cid,bag_sig) last-wins: ten sam worek 2× (stary improved,
    świeży bez różnicy) → differs=0 (liczy się OSTATNI stan)."""
    records = [
        _rec("A", "s1", 25.0, 5.0),
        _rec("A", "s1", 10.0, 10.0, differs=False),
    ]
    rep = _run_review(tmp_path, monkeypatch, records)
    assert rep["multi_uniq"] == 1 and rep["differs"] == 0, rep
