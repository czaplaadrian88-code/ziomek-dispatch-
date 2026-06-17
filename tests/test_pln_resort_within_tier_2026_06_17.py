"""Fix _pln_pure_resort: within-tier (B) + quality-aware pln_v (C) — bug E2-pln 2026-06-17.

B: ENABLE_PLN_RESORT_WITHIN_TIER → tier2 (łamanie committed) NIGDY nie bije tier0/1.
C: ENABLE_PLN_QUALITY_AWARE → w obrębie tieru pln_v dostaje karę za R6-breach +
   spóźniony nowy odbiór (pln liczy też jakość = ochrona klienta, nie tylko płacę)."""
import dispatch_v2.dispatch_pipeline as dp


class _Cand:
    def __init__(self, cid, pln_v, breach=False, ext=False, pos="gps", score=0.0,
                 r6=0.0, late=0.0):
        self.courier_id = cid
        self.score = score
        self.metrics = {
            "pln_v": pln_v,
            "late_pickup_committed_breach": breach,
            "new_pickup_needs_extension": ext,
            "pos_source": pos,
            "r6_bag_size": 1,
            "objm_r6_breach_max_min": r6,
            "new_pickup_late_min": late,
        }


def _resort(cands, within, monkeypatch, quality=False):
    flags = {"ENABLE_PLN_RESORT_WITHIN_TIER": within, "ENABLE_PLN_QUALITY_AWARE": quality}
    monkeypatch.setattr(dp.C, "flag", lambda name, default=False: flags.get(name, default))
    top = list(cands)
    dp._pln_pure_resort(top)
    return top


# ── B: within-tier tier2 floor ──
def test_off_pure_pln_breaks_committed(monkeypatch):
    clean = _Cand("123", pln_v=10.0, breach=False, score=100.0)
    bad = _Cand("179", pln_v=50.0, breach=True, score=-400.0)
    top = _resort([clean, bad], within=False, monkeypatch=monkeypatch)
    assert top[0].courier_id == "179"  # czysty pln_v → tier2 wygrywa (to jest bug)


def test_on_within_tier_protects_committed(monkeypatch):
    clean = _Cand("123", pln_v=10.0, breach=False, score=100.0)
    bad = _Cand("179", pln_v=50.0, breach=True, score=-400.0)
    top = _resort([clean, bad], within=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "123"  # tier0 chroniony


def test_on_within_tier_pln_decides_inside_tier(monkeypatch):
    lo = _Cand("400", pln_v=10.0, breach=False)
    hi = _Cand("123", pln_v=80.0, breach=False)
    top = _resort([lo, hi], within=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "123"
    assert top[0].metrics.get("pln_ab_flipped") is True


def test_on_within_tier_forced_when_all_tier2(monkeypatch):
    a = _Cand("1", pln_v=10.0, breach=True)
    b = _Cand("2", pln_v=40.0, breach=True)
    top = _resort([a, b], within=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "2"  # oba tier2 → wyższy pln_v


def test_empty_noop(monkeypatch):
    _resort([], within=True, monkeypatch=monkeypatch, quality=True)


# ── C: quality-aware pln_v (R6/late penalty within tier) ──
def test_quality_off_high_pln_wins_despite_r6(monkeypatch):
    """C OFF: wyższy pln_v wygrywa mimo R6-breach (czysty within-tier B)."""
    fast = _Cand("1", pln_v=20.0, breach=False, r6=20.0)
    clean = _Cand("2", pln_v=15.0, breach=False, r6=0.0)
    top = _resort([fast, clean], within=True, quality=False, monkeypatch=monkeypatch)
    assert top[0].courier_id == "1"


def test_quality_on_r6_penalty_flips_to_clean(monkeypatch):
    """C ON: kara R6 (0.5×20=-10) zbija pln_v 20→10 < 15 → kurier bez R6 wygrywa."""
    fast = _Cand("1", pln_v=20.0, breach=False, r6=20.0)
    clean = _Cand("2", pln_v=15.0, breach=False, r6=0.0)
    top = _resort([fast, clean], within=True, quality=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "2"


def test_quality_on_late_pickup_penalty(monkeypatch):
    """C ON: kara za spóźniony nowy odbiór (0.3/min ponad 5). 18-0.3*(40-5)=7.5 < 12."""
    late = _Cand("1", pln_v=18.0, breach=False, late=40.0)
    ontime = _Cand("2", pln_v=12.0, breach=False, late=1.0)
    top = _resort([late, ontime], within=True, quality=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "2"


def test_quality_does_not_override_tier2_floor(monkeypatch):
    """C ON: kara jakości NIE łamie podłogi tier2 (tier0 dalej bije tier2)."""
    bad = _Cand("179", pln_v=50.0, breach=True)            # tier2
    clean = _Cand("123", pln_v=10.0, breach=False, r6=10.0)  # tier0 z R6
    top = _resort([clean, bad], within=True, quality=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "123"  # tier2 floor trzyma niezależnie od pln/kary
