"""Floor ETA w linii „Kandydaci" propozycji do umówionego czas_kuriera (Adrian 2026-06-25).

Kandydat dojeżdżający PRZED umówionym pokazuje umówiony (czasówka=czas restauracji /
elastyk=czas Ziomka), dojazd PO umówionym zostaje (spóźnienie). Parytet z konsolą/apką/
widokiem restauracji. Łapie też pre_shift (eta_pickup_hhmm = start zmiany). Czysta funkcja.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dispatch_v2.telegram_approver import _candidate_line_v2 as line


def _cand(**kw):
    base = dict(courier_id=413, name="Test", pos_source="gps",
                eta_pickup_hhmm="11:00", r6_bag_size=0)
    base.update(kw)
    return base


def test_early_floored_to_committed():
    # kurier dojechałby 11:00, umówione 11:17 → ETA 11:17 (nie 11:00)
    assert "ETA 11:17" in line(1, _cand(), True, committed_hhmm="11:17")


def test_late_kept_shows_lateness():
    # kurier dojedzie 11:24 > umówione 11:17 → zostaje 11:24 (spóźnienie pokazane)
    assert "ETA 11:24" in line(1, _cand(eta_pickup_hhmm="11:24"), True, committed_hhmm="11:17")


def test_no_committed_raw_eta():
    # brak umówionego (elastyk pre-akceptacja) → surowy kanon 11:00
    assert "ETA 11:00" in line(1, _cand(), True, committed_hhmm=None)


def test_pre_shift_start_floored():
    # pre_shift: eta_pickup_hhmm = start zmiany 12:00, umówione 12:33 → ETA 12:33
    assert "ETA 12:33" in line(1, _cand(pos_source="pre_shift", eta_pickup_hhmm="12:00"),
                               True, committed_hhmm="12:33")


def test_dash_eta_untouched():
    # brak ETA ("—") nie jest floor-owany
    assert "ETA —" in line(1, _cand(eta_pickup_hhmm=None, eta_drive_hhmm=None),
                           True, committed_hhmm="11:17")


if __name__ == "__main__":
    test_early_floored_to_committed()
    test_late_kept_shows_lateness()
    test_no_committed_raw_eta()
    test_pre_shift_start_floored()
    test_dash_eta_untouched()
    print("OK")
