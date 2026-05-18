"""Sprint OBJ F3 / BUG-4 (2026-05-18) — best_effort R6-breach → KOORD escalation.

Gdy 0 feasible i najlepszy kandydat best_effort łamie hard R6 o > próg
(OBJ_F3_R6_BREACH_KOORD_MIN, domyślnie 20 min), dispatch_pipeline emituje
verdict=KOORD zamiast auto-PROPOSE. Diagnoza 474297 (BUG-4): kurier R6-doomed,
carry 47-82 min — trasa-potworek była proponowana zamiast eskalacji.

Pattern testu = source-regression (jak test_v328_p3d6_pathb_koord_escalation):
bramki głęboko w assess_order nie mają taniego fixture behawioralnego;
weryfikujemy obecność + pozycję + predykat + werdykt w źródle, plus kontrakt
flagi/progu w common.
"""
import inspect

from dispatch_v2 import common, dispatch_pipeline


def test_f3_gate_comment_header_present():
    src = inspect.getsource(dispatch_pipeline)
    assert "Sprint OBJ F3 / BUG-4" in src


def test_f3_flag_and_threshold_in_source():
    """Bramka czyta flagę ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD + próg const."""
    src = inspect.getsource(dispatch_pipeline)
    assert "ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD" in src
    assert "OBJ_F3_R6_BREACH_KOORD_MIN" in src


def test_f3_uses_objm_r6_breach_metric():
    """Magnituda przekroczenia z objm_r6_breach_max_min (route_metrics)."""
    src = inspect.getsource(dispatch_pipeline)
    assert "_r6_breach_max" in src
    assert "objm_r6_breach_max_min" in src


def test_f3_emits_koord_verdict():
    """Bramka emituje verdict=KOORD z reason best_effort_r6_breach."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("Sprint OBJ F3 / BUG-4 (2026-05-18): best_effort")
    assert start > 0
    section = src[start:start + 1400]
    assert 'verdict="KOORD"' in section
    assert "best_effort_r6_breach" in section


def test_f3_positioned_in_best_effort_after_marker_before_low_score():
    """Bramka po `best.best_effort = True`, przed `best_effort_low_score`."""
    src = inspect.getsource(dispatch_pipeline)
    marker = src.find("best.best_effort = True")
    gate = src.find("Sprint OBJ F3 / BUG-4 (2026-05-18): best_effort")
    low_score = src.find("best_effort_low_score")
    assert marker > 0 and gate > 0 and low_score > 0
    assert marker < gate < low_score, (
        f"pozycja bramki F3 błędna: marker={marker} gate={gate} low_score={low_score}")


def test_f3_helper_conservative_on_missing_metric():
    """_r6_breach_max: brak objm_r6_breach_max_min → 0.0 (brak eskalacji)."""
    src = inspect.getsource(dispatch_pipeline)
    h = src.find("def _r6_breach_max")
    assert h > 0
    body = src[h:h + 260]
    # zwraca float gdy liczba, 0.0 gdy brak/None
    assert "objm_r6_breach_max_min" in body
    assert "0.0" in body and "isinstance" in body


def test_f3_common_contract():
    """common: flaga default OFF, próg default 20.0 (wysoki — nie rusza R-BUFFER-OK)."""
    assert common.ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD is False
    assert common.OBJ_F3_R6_BREACH_KOORD_MIN == 20.0
