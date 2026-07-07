"""T2.4 — FSM mode-layer S1/S2/S3 (advisory Tura 2, werdykt E-4).

Testuje CZYSTY rdzeń: wejście S2 = 2-z-3 podtrzymane ≥10′; histereza (wyjście <
wejścia + dwell); S3 rate ∨ capitulation; kanon (relaks R6 tylko S3 — tu test
że FSM NIE wchodzi w S2/S3 bez sygnałów). Shadow-first (obserwacja trybu).
"""
from __future__ import annotations

from dispatch_v2 import mode_layer as M


def _sig(t, L=0.0, q=0, lat=0.0, rate=0.0, dr=5):
    return M.ModeSignals(load_inflight_per_active=L, queue_pending=q,
                         assign_latency_med_min=lat, s2_infeasible_rate=rate,
                         defers_and_reassigns=dr, now_min=t)


def _run(sigs, s3_rate=M.S3_RATE_DEFAULT):
    st = M.ModeState(mode=M.S1, entered_at_min=sigs[0].now_min)
    out = []
    for s in sigs:
        st = M.step(st, s, s3_rate)
        out.append(st.mode)
    return out, st


def test_s1_stays_calm():
    sigs = [_sig(t, L=2, q=3, lat=1) for t in range(0, 30, 5)]
    modes, _ = _run(sigs)
    assert all(m == M.S1 for m in modes)


def test_s2_requires_two_of_three_sustained():
    # 2 z 3 (L + queue) od t=0, ale musi być UTRZYMANE ≥10 min
    sigs = [_sig(t, L=7, q=12, lat=1) for t in (0, 5, 10, 15)]
    modes, _ = _run(sigs)
    assert modes[0] == M.S1          # dopiero co przekroczone (sustain=0)
    assert modes[1] == M.S1          # 5′ < 10′
    assert modes[2] == M.S2          # 10′ ≥ sustain → S2
    assert modes[3] == M.S2


def test_one_of_three_never_s2():
    sigs = [_sig(t, L=7, q=3, lat=1) for t in range(0, 40, 5)]  # tylko L
    modes, _ = _run(sigs)
    assert all(m == M.S1 for m in modes)


def test_hysteresis_exit_needs_below_and_dwell():
    # wejdź w S2, potem sygnały spadają — wyjście dopiero po dwell≥15
    sigs = [_sig(0, L=7, q=12), _sig(10, L=7, q=12), _sig(15, L=7, q=12),  # S2 @10
            _sig(20, L=3, q=3), _sig(28, L=3, q=3)]
    modes, _ = _run(sigs)
    assert modes[2] == M.S2
    # @20: below-exit ale dwell (20-10=10) <15 → zostaje S2
    assert modes[3] == M.S2
    # @28: dwell (28-10=18) ≥15 + below-exit → S1
    assert modes[4] == M.S1


def test_s3_on_rate():
    sigs = [_sig(0, L=7, q=12), _sig(10, L=7, q=12, rate=0.25)]
    modes, st = _run(sigs)
    assert modes[1] == M.S3
    assert "rate" in st.reason


def test_s3_on_capitulation():
    # defery+przerzuty=0 ∧ kolejka≥20 → S3 nawet bez rate
    sigs = [_sig(0, L=5, q=22, dr=0)]
    modes, st = _run(sigs)
    assert modes[0] == M.S3
    assert "capitulation" in st.reason


def test_s3_exit_needs_dwell():
    sigs = [_sig(0, L=7, q=12, rate=0.3), _sig(5, L=3, q=3, rate=0.0),
            _sig(20, L=3, q=3, rate=0.0)]
    modes, _ = _run(sigs)
    assert modes[0] == M.S3
    assert modes[1] == M.S3      # dwell (5-0) <15 → zostaje S3
    assert modes[2] in (M.S1, M.S2)  # dwell (20-0=20) ≥15 → wyjście


def test_pure_no_mutation():
    st = M.ModeState(mode=M.S1, entered_at_min=0)
    M.step(st, _sig(0, L=7, q=12), M.S3_RATE_DEFAULT)
    assert st.mode == M.S1 and st.entered_at_min == 0  # wejście nietknięte
