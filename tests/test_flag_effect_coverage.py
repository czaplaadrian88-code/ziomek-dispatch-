"""C-FLAG-EFFECT (2026-06-25): gate przeciw fladze decyzyjnej bez testu EFEKTU.
Ratchet — NIE wymaga 100% (część flag ETAP4 to shadow/AB świadomie bez toggle-testu),
tylko: żadna NOWA flaga z ETAP4_DECISION_FLAGS nie pojawia się bez testu dotykającego
jej, poza zamrożonym baseline. Łapie klasę „flaga wpięta, ale efekt nigdy nie sprawdzony"
(ENABLE_BEST_EFFORT_OBJM_R6_KEY). Komplement do C-ORPHAN (`flag_hygiene_check`) i
C-FLAG-DRIFT (`flag_doc_coverage_check`).

Mechanizm w `tools/flag_effect_coverage_check.py` (też CI-uruchamialny standalone).
"""
import importlib.util
import os

_TOOL = os.path.join(os.path.dirname(__file__), "..", "tools", "flag_effect_coverage_check.py")
_spec = importlib.util.spec_from_file_location("flag_effect_coverage_check", _TOOL)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_no_new_untested_decision_flag():
    """NOWA flaga decyzyjna ETAP4 bez testu efektu i poza baseline → fail.
    Naprawa: dodaj test togglujący flagę ON↔OFF z asercją zmiany decyzji ALBO
    świadomie dopisz do tools/flag_effect_baseline.json."""
    r = _mod.compute()
    assert not r["new_gap"], (
        f"NOWA LUKA testów efektu — {len(r['new_gap'])} flaga(i) decyzyjna ETAP4 "
        f"bez testu i poza baseline: {r['new_gap']}. Dodaj test toggle ON↔OFF z asercją "
        f"zmiany decyzji albo dopisz świadomie do flag_effect_baseline.json.")


def test_baseline_is_not_stale():
    """Baseline nie gnije: entry która zniknęła z ETAP4 lub dostała już test →
    usuń z baseline (kurczenie długu = cel ratchetu)."""
    r = _mod.compute()
    assert not r["stale_baseline"], (
        f"baseline zawiera nieaktualne wpisy ({len(r['stale_baseline'])}) — "
        f"zniknęły z ETAP4 lub już mają test: {r['stale_baseline']}. "
        f"Usuń je z tools/flag_effect_baseline.json.")


def test_coverage_reported():
    """Sanity: checker liczy coverage i widzi flagi ETAP4."""
    r = _mod.compute()
    assert r["total_decision_flags"] > 0, "brak flag ETAP4 — sprawdź import common.ETAP4_DECISION_FLAGS"
    assert 0.0 <= r["coverage_pct"] <= 100.0
