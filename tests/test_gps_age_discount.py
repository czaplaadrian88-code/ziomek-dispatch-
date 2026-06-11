"""GPS-03/DATA-04 (2026-06-11) — confidence-discount za wiek pozycji.

Wzorzec lekcji #186: bonus_gps_age_discount_shadow liczony ZAWSZE,
aplikacja do score + re-sort wyłącznie pod flagą (kanon flags.json).
"""
from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp


class _Cand:
    def __init__(self, cid, score, age=None):
        self.courier_id = cid
        self.score = score
        self.metrics = {} if age is None else {"pos_age_min": age}
        if age is None:
            self.metrics = {"pos_age_min": None}


def test_defaults_off_and_constants():
    assert C.decision_flag("ENABLE_GPS_AGE_DISCOUNT") is False
    assert C.GPS_AGE_DISCOUNT_FREE_MIN == 5.0
    assert C.GPS_AGE_DISCOUNT_PER_MIN == 0.8
    assert C.GPS_AGE_DISCOUNT_CAP == 12.0
    assert "ENABLE_GPS_AGE_DISCOUNT" in C.ETAP4_DECISION_FLAGS
    for k in ("GPS_AGE_DISCOUNT_FREE_MIN", "GPS_AGE_DISCOUNT_PER_MIN",
              "GPS_AGE_DISCOUNT_CAP"):
        assert k in C.FLAGS_JSON_NUMERIC_OVERRIDES


def test_off_computes_shadow_no_score_change():
    a = _Cand("1", 50.0, age=20.0)   # 15 min ponad free → -12 (cap)
    b = _Cand("2", 45.0, age=None)   # żywy fix → 0
    out = dp._gps_age_discount([a, b])
    assert [c.courier_id for c in out] == ["1", "2"]  # kolejność bez zmian
    assert a.score == 50.0 and b.score == 45.0        # score nietknięty
    assert a.metrics["bonus_gps_age_discount_shadow"] == -12.0
    assert a.metrics["bonus_gps_age_discount"] == 0.0
    assert b.metrics["bonus_gps_age_discount_shadow"] == 0.0


def test_on_applies_and_resorts(monkeypatch):
    monkeypatch.setattr(
        C, "load_flags", lambda: {"ENABLE_GPS_AGE_DISCOUNT": True})
    a = _Cand("stary", 50.0, age=20.0)   # -12 → 38
    b = _Cand("swiezy", 45.0, age=2.0)   # ≤ free → 0
    out = dp._gps_age_discount([a, b])
    assert [c.courier_id for c in out] == ["swiezy", "stary"]  # re-sort
    assert a.score == 38.0
    assert a.metrics["bonus_gps_age_discount"] == -12.0
    assert b.metrics["bonus_gps_age_discount"] == 0.0


def test_linear_band_below_cap():
    a = _Cand("1", 0.0, age=10.0)  # (10-5)*0.8 = -4.0, poniżej capa
    dp._gps_age_discount([a])
    assert a.metrics["bonus_gps_age_discount_shadow"] == -4.0


def test_numeric_override_from_flags_json(monkeypatch):
    monkeypatch.setattr(C, "load_flags", lambda: {
        "ENABLE_GPS_AGE_DISCOUNT": True,
        "GPS_AGE_DISCOUNT_PER_MIN": 2.0,
        "GPS_AGE_DISCOUNT_CAP": 100.0,
    })
    a = _Cand("1", 0.0, age=15.0)  # (15-5)*2.0 = -20
    dp._gps_age_discount([a])
    assert a.metrics["bonus_gps_age_discount"] == -20.0


def test_empty_and_age_at_threshold():
    assert dp._gps_age_discount([]) == []
    a = _Cand("1", 0.0, age=5.0)  # dokładnie free → 0
    dp._gps_age_discount([a])
    assert a.metrics["bonus_gps_age_discount_shadow"] == 0.0
