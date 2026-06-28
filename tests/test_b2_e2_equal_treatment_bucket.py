"""B2 (audyt 2026-06-28) — E2 PLN arm uzywa equal-treatment _selection_bucket.

Bug: `_pln_pure_resort` (arm E2, LIVE) mial inline `_bucket` sprzed equal-treatment
(24.06) -> demotowal no_gps/pre_shift do bucketu 2, ktorych glowna sciezka traktuje
rowno (bucket 0). Replay 10d: 49/378 decyzji E2-arm stary demote zmienial pick, 100%
przeciw no_gps/pre_shift. Fix: repoint na wspolny _selection_bucket (+ twin
_objm_lexr6_shadow). Sterowane ENABLE_EQUAL_TREATMENT_BUCKET (jak reszta selekcji).
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2 import common as C


def _cand(cid, ps, pln_v, bag=0):
    return SimpleNamespace(courier_id=cid,
                           metrics={"pos_source": ps, "pln_v": pln_v, "bag_size_before": bag})


def _force(monkeypatch, equal_on):
    _orig = C.load_flags
    monkeypatch.setattr(C, "load_flags", lambda: {
        **_orig(),
        "ENABLE_PLN_RESORT_WITHIN_TIER": True,
        "ENABLE_PLN_QUALITY_AWARE": False,   # _pln_key = czysty -pln_v
        "ENABLE_EQUAL_TREATMENT_BUCKET": equal_on,
    })


def test_equal_ON_no_gps_better_pln_wins(monkeypatch):
    """ON: no_gps (lepszy pln_v) konkuruje rowno (bucket 0) -> wygrywa."""
    _force(monkeypatch, equal_on=True)
    top = [_cand("INF", "gps", 1.0), _cand("NG", "no_gps", 5.0)]
    dp._pln_pure_resort(top)
    assert top[0].courier_id == "NG", f"ON powinien wybrac no_gps; got {top[0].courier_id}"


def test_equal_OFF_no_gps_demoted(monkeypatch):
    """OFF (legacy = stary stale bucket): no_gps -> bucket 2 -> informed wygrywa mimo gorszego pln."""
    _force(monkeypatch, equal_on=False)
    top = [_cand("INF", "gps", 1.0), _cand("NG", "no_gps", 5.0)]
    dp._pln_pure_resort(top)
    assert top[0].courier_id == "INF", f"OFF powinien zdemotowac no_gps; got {top[0].courier_id}"


def test_ON_vs_OFF_differ(monkeypatch):
    """Ten sam wsad -> rozny pick zaleznie od equal-treatment = dowod sterowania flaga."""
    _force(monkeypatch, True)
    t1 = [_cand("INF", "gps", 1.0), _cand("NG", "no_gps", 5.0)]
    dp._pln_pure_resort(t1)
    _force(monkeypatch, False)
    t2 = [_cand("INF", "gps", 1.0), _cand("NG", "no_gps", 5.0)]
    dp._pln_pure_resort(t2)
    assert t1[0].courier_id != t2[0].courier_id, "fix musi zmieniac pick ON vs OFF"


def test_pre_shift_also_equal_ON(monkeypatch):
    """pre_shift tez objety rownym traktowaniem (decyzja Adriana 24.06)."""
    _force(monkeypatch, equal_on=True)
    top = [_cand("INF", "gps", 1.0), _cand("PS", "pre_shift", 5.0)]
    dp._pln_pure_resort(top)
    assert top[0].courier_id == "PS"


def test_informed_never_demoted_by_fix():
    """Fix tylko PODNOSI no_gps/pre_shift; informed (gps) zostaje bucket 0 — nic nie obniza."""
    assert dp._selection_bucket(_cand("X", "gps", 0.0)) == 0


def test_no_stale_inline_bucket_left():
    import inspect
    src = inspect.getsource(dp._pln_pure_resort)
    assert "_selection_bucket(" in src, "E2 arm musi uzywac _selection_bucket"
    assert "def _bucket(" not in src, "stale inline _bucket musi byc usuniety"
    src2 = inspect.getsource(dp._objm_lexr6_shadow)
    assert "def _bucket(" not in src2, "twin shadow stale _bucket tez usuniety"
