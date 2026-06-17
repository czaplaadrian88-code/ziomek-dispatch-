"""E7 2026-06-17 — cap kary R6-soft (robustness, zombie-pickup hygiene).

Próg -2000 dobrany replayem flipów (0 zmian selekcji na 7d). Test pokrywa:
- brak capa (cap_floor=None) → zachowanie identyczne jak przed E7 (3-tuple);
- cap łapie astronomiczną wartość zombie-pickup;
- legalny best-effort (r6=60, kara -688) NIE jest ucapowany przy -2000;
- raw zawsze zwraca wartość przed capem (telemetria);
- granica: pen dokładnie == floor nie jest dalej obcinane.
"""
from dispatch_v2.dispatch_pipeline import _r6_soft_penalty

SOFT, PER = 30.0, 8.0
DAN_ON, DAN_MIN, DAN_PER = True, 32.0, 16.0


def test_no_cap_backward_compatible():
    pen, legacy, raw = _r6_soft_penalty(35.0, SOFT, PER, DAN_ON, DAN_MIN, DAN_PER)
    assert pen == -88.0          # -(35-30)*8 - (35-32)*16
    assert legacy == -40.0       # -(35-30)*8
    assert raw == -88.0          # bez capa raw == pen


def test_below_soft_zero():
    assert _r6_soft_penalty(29.0, SOFT, PER, DAN_ON, DAN_MIN, DAN_PER) == (0.0, 0.0, 0.0)


def test_cap_clamps_zombie():
    pen, legacy, raw = _r6_soft_penalty(10000.0, SOFT, PER, DAN_ON, DAN_MIN, DAN_PER, cap_floor=-2000.0)
    assert pen == -2000.0        # ucapowane
    assert raw < -200000         # raw zachowuje astronomiczną wartość (telemetria)
    assert raw != pen


def test_cap_does_not_touch_legit_besteffort():
    # r6=60 → -(60-30)*8 - (60-32)*16 = -240 -448 = -688 > -2000 → NIE ucapowane
    pen, legacy, raw = _r6_soft_penalty(60.0, SOFT, PER, DAN_ON, DAN_MIN, DAN_PER, cap_floor=-2000.0)
    assert pen == -688.0
    assert raw == -688.0         # raw == pen gdy nie ucapowane


def test_cap_boundary_exact_floor_not_clamped_further():
    # dobierz r6 tak by pen == -2000 dokładnie nie istnieje czysto; sprawdź tuż-pod i tuż-nad
    # tuż nad floor (mniej ujemne) nie ucapowane:
    pen_above = _r6_soft_penalty(114.0, SOFT, PER, DAN_ON, DAN_MIN, DAN_PER, cap_floor=-2000.0)[0]
    assert pen_above > -2000.0   # -(114-30)*8 -(114-32)*16 = -672-1312 = -1984
    # tuż pod floor (bardziej ujemne) ucapowane do floor:
    pen_below = _r6_soft_penalty(116.0, SOFT, PER, DAN_ON, DAN_MIN, DAN_PER, cap_floor=-2000.0)[0]
    assert pen_below == -2000.0


def test_cap_none_is_default_passthrough():
    # cap_floor pominięty == None == brak capa nawet dla zombie
    pen = _r6_soft_penalty(10000.0, SOFT, PER, DAN_ON, DAN_MIN, DAN_PER)[0]
    assert pen < -200000
