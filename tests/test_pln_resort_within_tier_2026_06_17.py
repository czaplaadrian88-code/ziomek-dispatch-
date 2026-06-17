"""Fix _pln_pure_resort within-tier — bug E2-pln tier2 (2026-06-17).

Czysty sort po pln_v łamał committed odbiory (tier2 bił tier0 dla zysku pay).
Fix: ENABLE_PLN_RESORT_WITHIN_TIER → tier2 nigdy nie bije tier0/1; pln_v decyduje
tylko w obrębie tieru. Flaga OFF = legacy (bug zachowany dla porównania A/B)."""
import dispatch_v2.dispatch_pipeline as dp


class _Cand:
    def __init__(self, cid, pln_v, breach=False, ext=False, pos="gps", score=0.0):
        self.courier_id = cid
        self.score = score
        self.metrics = {
            "pln_v": pln_v,
            "late_pickup_committed_breach": breach,
            "new_pickup_needs_extension": ext,
            "pos_source": pos,
            "r6_bag_size": 1,  # nie-pusty → nie blind_empty
        }


def _resort(cands, within, monkeypatch):
    monkeypatch.setattr(
        dp.C, "flag",
        lambda name, default=False: within if name == "ENABLE_PLN_RESORT_WITHIN_TIER" else default,
    )
    top = list(cands)
    dp._pln_pure_resort(top)
    return top


def test_off_pure_pln_breaks_committed(monkeypatch):
    """OFF (legacy): tier2 z wyższym pln_v wygrywa = bug zachowany (kontrola A/B)."""
    clean = _Cand("123", pln_v=10.0, breach=False, pos="gps", score=100.0)  # tier0
    bad = _Cand("179", pln_v=50.0, breach=True, pos="gps", score=-400.0)    # tier2
    top = _resort([clean, bad], within=False, monkeypatch=monkeypatch)
    assert top[0].courier_id == "179"  # czysty pln_v → tier2 wygrywa (to jest bug)


def test_on_within_tier_protects_committed(monkeypatch):
    """ON (fix): tier2 NIGDY nie bije tier0 mimo wyższego pln_v (Bartek 123 chroniony)."""
    clean = _Cand("123", pln_v=10.0, breach=False, pos="gps", score=100.0)  # tier0
    bad = _Cand("179", pln_v=50.0, breach=True, pos="gps", score=-400.0)    # tier2
    top = _resort([clean, bad], within=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "123"  # tier0 chroniony


def test_on_within_tier_pln_decides_inside_tier(monkeypatch):
    """ON: w obrębie tego samego tieru pln_v dalej decyduje (eksperyment zachowany)."""
    lo = _Cand("400", pln_v=10.0, breach=False, pos="gps", score=50.0)  # tier0 low pln
    hi = _Cand("123", pln_v=80.0, breach=False, pos="gps", score=20.0)  # tier0 high pln
    top = _resort([lo, hi], within=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "123"  # wyższy pln_v wewnątrz tier0
    assert top[0].metrics.get("pln_ab_flipped") is True  # zmienił zwycięzcę


def test_on_within_tier_forced_when_all_tier2(monkeypatch):
    """ON: gdy WSZYSCY tier2 (saturacja, wymuszone) → pln_v decyduje między tier2."""
    a = _Cand("1", pln_v=10.0, breach=True, pos="gps")
    b = _Cand("2", pln_v=40.0, breach=True, pos="gps")
    top = _resort([a, b], within=True, monkeypatch=monkeypatch)
    assert top[0].courier_id == "2"  # oba tier2 → wyższy pln_v


def test_empty_noop(monkeypatch):
    _resort([], within=True, monkeypatch=monkeypatch)  # nie wybucha
