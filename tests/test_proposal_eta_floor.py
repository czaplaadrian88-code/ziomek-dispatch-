"""Floor ETA w linii „Kandydaci" propozycji do umówionego czas_kuriera (Adrian 2026-06-25).

Kandydat dojeżdżający PRZED umówionym pokazuje umówiony (czasówka=czas restauracji /
elastyk=czas Ziomka), dojazd PO umówionym zostaje (spóźnienie). Parytet z konsolą/apką/
widokiem restauracji. Łapie też pre_shift (eta_pickup_hhmm = start zmiany). Czysta funkcja.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dispatch_v2 import telegram_approver as ta
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


# ---------- integration: _format_proposal_v2 + flag toggle (ETAP4 ON≠OFF) ----------
# Bramka ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED przeniesiona z env-frozen stałej common
# na flag()/flags.json (parytet z bliźniakiem plan-floor, 2026-06-25). Ten test dowodzi
# ON≠OFF na poziomie flagi (poprzednie testy sprawdzają samo _candidate_line_v2).

class _CommittedFlag:
    """Monkey-patch ta.flag(): toggluje ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED,
    wyłącza plan-floor (izolacja committed), PROPOSAL_FORMAT_V2 True."""
    def __init__(self, committed_on: bool):
        self.on = committed_on
        self._orig = None

    def __enter__(self):
        self._orig = ta.flag

        def fake(name, default=False):
            if name == "ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED":
                return self.on
            if name == "ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN":
                return False
            if name == "PROPOSAL_FORMAT_V2":
                return True
            return self._orig(name, default)

        ta.flag = fake
        return self

    def __exit__(self, *exc):
        ta.flag = self._orig


def _decision_committed():
    # czas_kuriera 18:17 Warsaw (16:17 UTC); best dojechałby 18:00 → floor podnosi do 18:17
    best = {
        "courier_id": "75", "name": "Patryk", "score": 64.1,
        "pos_source": "gps", "r6_bag_size": 0, "free_at_min": 0.0,
        "travel_min": 10.0, "eta_pickup_hhmm": "18:00",
    }
    return {
        "order_id": "483301",
        "restaurant": "Piwo Kaczka Sushi",
        "delivery_address": "Rzemieślnicza 15a/12",
        "best": best,
        "alternatives": [],
        "auto_route": "ACK",
        "pool_total_count": 6, "pool_feasible_count": 6,
        "czas_kuriera_warsaw": "2026-06-25T16:17:00+00:00",
        "pickup_ready_at": "2026-06-25T16:00:00+00:00",
    }


def test_v2_committed_floor_on():
    with _CommittedFlag(True):
        out = ta._format_proposal_v2(_decision_committed())
    assert "ETA 18:17" in out, out
    assert "ETA 18:00" not in out, out


def test_v2_committed_floor_off():
    with _CommittedFlag(False):
        out = ta._format_proposal_v2(_decision_committed())
    assert "ETA 18:00" in out, out
    assert "ETA 18:17" not in out, out


if __name__ == "__main__":
    test_early_floored_to_committed()
    test_late_kept_shows_lateness()
    test_no_committed_raw_eta()
    test_pre_shift_start_floored()
    test_dash_eta_untouched()
    test_v2_committed_floor_on()
    test_v2_committed_floor_off()
    print("OK")
