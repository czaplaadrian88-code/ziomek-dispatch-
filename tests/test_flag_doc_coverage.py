"""C-FLAG-DRIFT (audyt 2026-06-24 §6.C): gate przeciw dryftowi dokumentacji flag
decyzyjnych. Ratchet — NIE wymaga 100% coverage (ref to doc logiki, nie rejestr
flag), tylko: żadna NOWA flaga ENABLE_/USE_ nie pojawia się niedokumentowana poza
zamrożonym baseline. Komplement do C-ORPHAN (`flag_hygiene_check`).

Mechanizm w `tools/flag_doc_coverage_check.py` (też CI-uruchamialny standalone).
"""
import importlib.util
import os

_TOOL = os.path.join(os.path.dirname(__file__), "..", "tools", "flag_doc_coverage_check.py")
_spec = importlib.util.spec_from_file_location("flag_doc_coverage_check", _TOOL)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_no_new_undocumented_decision_flag():
    """NOWA flaga decyzyjna niedokumentowana i poza baseline → fail (drift).
    Naprawa: udokumentuj w ZIOMEK_LOGIC_REFERENCE.md ALBO świadomie dopisz do
    tools/flag_doc_baseline.json."""
    r = _mod.compute()
    assert not r["new_drift"], (
        f"NOWY DRYFT dokumentacji — {len(r['new_drift'])} flaga(i) decyzyjna "
        f"niedokumentowana i poza baseline: {r['new_drift']}. Udokumentuj w ref "
        f"albo dopisz świadomie do flag_doc_baseline.json.")


def test_baseline_is_not_stale():
    """Baseline nie gnije: entry która zniknęła z flags.json lub została już
    udokumentowana → usuń z baseline (utrzymanie higieny ratchetu, kurczenie długu)."""
    r = _mod.compute()
    assert not r["stale_baseline"], (
        f"baseline zawiera nieaktualne wpisy ({len(r['stale_baseline'])}) — "
        f"zniknęły z flags.json lub już udokumentowane: {r['stale_baseline']}. "
        f"Usuń je z tools/flag_doc_baseline.json.")


def test_coverage_reported():
    """Sanity: checker liczy coverage (gdyby ref/flags zniknęły → 0 kluczy)."""
    r = _mod.compute()
    assert r["total_decision_keys"] > 0, "brak flag decyzyjnych — sprawdź ścieżki flags.json/ref"
    assert 0.0 <= r["coverage_pct"] <= 100.0
