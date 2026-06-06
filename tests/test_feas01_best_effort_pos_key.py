"""FEAS-01 / SEL-01 (2026-06-06) — best_effort (feasible=0) klucz sortu spójny z
główną selekcją: bucket pos_source (informed<other<blind/pre_shift) + score, przy
zachowaniu PRYMARNOŚCI R6 violations + SLA.

Bug: best_effort sortował tylko (r6_pov, sla, duration) → no_gps z FIKCYJNYM
BIALYSTOK_CENTER (krótki „dojazd") bił informed kuriera z obrzeży. Testujemy czystą
funkcję _best_effort_sort_key bez budowania całego pipeline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.dispatch_pipeline import _best_effort_sort_key  # noqa: E402


class _FakePlan:
    def __init__(self, sla=0, dur=10.0):
        self.sla_violations = sla
        self.total_duration_min = dur


class _FakeCand:
    def __init__(self, cid, pos_source="gps", score=0.0, r6_pov=0,
                 bag_size=0, sla=0, dur=10.0, metrics_none=False):
        self.courier_id = cid
        self.score = score
        self.plan = _FakePlan(sla, dur)
        if metrics_none:
            self.metrics = None
            return
        m = {"pos_source": pos_source, "r6_bag_size": bag_size}
        if r6_pov:
            m["r6_per_order_violations"] = ["x"] * r6_pov
        self.metrics = m


def _order(cands):
    return [c.courier_id for c in sorted(cands, key=_best_effort_sort_key)]


def test_informed_beats_blind_empty_on_tie():
    # Tied r6_pov/sla. informed(gps) dłuższy dojazd vs blind(no_gps,bag0) krótszy →
    # informed wygrywa (bucket 0 < 2) MIMO dłuższego total_duration.
    informed = _FakeCand("INF", pos_source="gps", dur=20.0)
    blind = _FakeCand("BLIND", pos_source="no_gps", bag_size=0, dur=5.0)
    assert _order([blind, informed])[0] == "INF"


def test_r6_violations_primacy_over_bucket():
    # R6 PRIMARY: blind z 0 violations bije informed z 1 violation (nie proponuj
    # gorszego na R6 nawet jeśli pozycja lepsza).
    blind_clean = _FakeCand("BLIND0", pos_source="no_gps", bag_size=0, r6_pov=0)
    informed_breach = _FakeCand("INF1", pos_source="gps", r6_pov=1)
    assert _order([informed_breach, blind_clean])[0] == "BLIND0"


def test_sla_primacy_over_bucket():
    informed_sla = _FakeCand("INFsla", pos_source="gps", sla=1)
    blind_clean = _FakeCand("BLIND", pos_source="no_gps", bag_size=0, sla=0)
    assert _order([informed_sla, blind_clean])[0] == "BLIND"


def test_score_tiebreak_within_bucket():
    lo = _FakeCand("LO", pos_source="gps", score=10.0)
    hi = _FakeCand("HI", pos_source="gps", score=50.0)
    assert _order([lo, hi])[0] == "HI"


def test_other_bucket_between_informed_and_blind():
    # no_gps z bagiem (>0) = „other" (bucket 1): nie informed, nie blind+empty.
    informed = _FakeCand("INF", pos_source="gps")
    other = _FakeCand("OTHER", pos_source="no_gps", bag_size=2)
    blind = _FakeCand("BLIND", pos_source="no_gps", bag_size=0)
    assert _order([blind, other, informed]) == ["INF", "OTHER", "BLIND"]


def test_pre_shift_is_bucket2():
    informed = _FakeCand("INF", pos_source="gps")
    pre = _FakeCand("PRE", pos_source="pre_shift")
    assert _order([pre, informed])[0] == "INF"


def test_no_metrics_sorted_last():
    # Brak metrics → r6_pov=99 → na dół (mirror _r6_pov_count, NIE na górę).
    good = _FakeCand("GOOD", pos_source="gps")
    nometr = _FakeCand("NOMET", metrics_none=True)
    assert _order([nometr, good]) == ["GOOD", "NOMET"]


def test_duration_final_tiebreak():
    # Wszystko równe (r6/sla/bucket/score) → krótszy total_duration wygrywa.
    far = _FakeCand("FAR", pos_source="gps", score=10.0, dur=25.0)
    near = _FakeCand("NEAR", pos_source="gps", score=10.0, dur=5.0)
    assert _order([far, near])[0] == "NEAR"
