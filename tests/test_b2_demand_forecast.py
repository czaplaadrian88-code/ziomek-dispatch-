"""SP-B2-OBSADA — testy tools/demand_forecast.py (2026-06-11)."""
from datetime import date, timedelta

from dispatch_v2.tools import demand_forecast as df


def test_calendar_multiplier():
    assert df.calendar_multiplier(date(2026, 2, 14)) == (1.75, "Walentynki")
    assert df.calendar_multiplier(date(2025, 11, 11))[0] == 1.96
    assert df.calendar_multiplier(date(2026, 6, 1)) == (1.4, "Dzień Dziecka")
    assert df.calendar_multiplier(date(2026, 1, 1))[0] == 1.5
    assert df.calendar_multiplier(date(2026, 5, 2)) == (0.8, "majówka")
    assert df.calendar_multiplier(date(2025, 12, 26))[0] == 0.6
    assert df.calendar_multiplier(date(2026, 6, 10)) == (1.2, "payday 9-12")
    assert df.calendar_multiplier(date(2026, 6, 18)) == (1.0, "")


def test_slot_for_hour():
    assert df.slot_for_hour(11) == "peak_lunch"
    assert df.slot_for_hour(14) == "high_risk"
    assert df.slot_for_hour(17) == "peak_dinner"
    assert df.slot_for_hour(20) == "evening"
    assert df.slot_for_hour(22) == "evening"
    assert df.slot_for_hour(9) is None
    assert df.slot_for_hour(23) is None


def test_ewma_skips_special_days_and_respects_until():
    # 4 kolejne poniedziałki, jeden payday (10ty = mnożnik 1,2) ma być pominięty
    mondays = [date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16),
               date(2026, 3, 23)]
    volumes = {d: {"peak_lunch": v} for d, v in zip(mondays, [40, 999, 44, 48])}
    # 09.03 = dzień 9 → payday → wykluczony z aktualizacji
    table = df.ewma_table(volumes)
    key = (0, "peak_lunch")
    # EWMA(40, 44, 48) z alpha 0.25: 40 → 41.0 → 42.75
    assert abs(table[key] - 42.75) < 1e-9
    # until: tylko pierwszy poniedziałek
    t2 = df.ewma_table(volumes, until=date(2026, 3, 9))
    assert t2[key] == 40.0


def test_forecast_applies_multiplier():
    table = {(5, "high_risk"): 60.0}
    d = date(2026, 2, 14)  # sobota, Walentynki
    fc, mult, label = df.forecast_for(d, table)
    assert mult == 1.75 and label == "Walentynki"
    assert abs(fc["high_risk"] - 105.0) < 1e-9
    assert fc["peak_lunch"] is None  # brak historii → None


def test_roster_slot_capacity_overlaps():
    roster = {
        "A": {"start": "11:00", "end": "21:00"},   # pełne pokrycie 11-21
        "B": {"start": "12:30", "end": "15:00"},   # częściowe lunch+hr
        "C": None,                                  # nie pracuje
        "D": {"start": "16:45", "end": "17:10"},   # overlap < 30 min wszędzie
    }
    cap = df.roster_slot_capacity(roster)
    assert cap["peak_lunch"]["headcount"] == 2
    assert abs(cap["peak_lunch"]["courier_hours"] - (3.0 + 1.5)) < 1e-9
    assert cap["high_risk"]["headcount"] == 2
    assert abs(cap["high_risk"]["courier_hours"] - (3.0 + 1.0)) < 1e-9
    assert cap["evening"]["headcount"] == 1
    assert abs(cap["evening"]["courier_hours"] - 1.0) < 1e-9


def test_assess_levels_hard_soft_ok():
    d = date(2026, 6, 18)  # czwartek, brak mnożnika
    dow = d.weekday()
    volumes = {}
    # zbuduj historię dającą EWMA=72 dla wszystkich slotów tego dow
    for k in range(1, 5):
        volumes[d - timedelta(weeks=k)] = {s: 72 for s, _l, _h in df.SLOTS}
    roster = {
        # 8 kurierów 11-23 → 24 kh/slot → load 3.0 (hard) … zależnie od slotu
        f"K{i}": {"start": "11:00", "end": "23:00"} for i in range(8)
    }
    a = df.assess(d, volumes=volumes, roster=roster)
    by_slot = {r["slot"]: r for r in a["rows"]}
    # 72 zlec. / 24 kh = 3.0 → hard wszędzie
    assert all(by_slot[s]["level"] == "hard" for s, _l, _h in df.SLOTS)
    assert a["any_alarm"] is True
    # dołóż: need_h = 72/2.5 - 24 = 4.8 → /3h → 2
    assert by_slot["peak_lunch"]["add_couriers"] == 2

    # 12 kurierów → 36 kh → load 2.0 → poniżej guard (2.077) → czysto
    roster12 = {f"K{i}": {"start": "11:00", "end": "23:00"} for i in range(12)}
    a2 = df.assess(d, volumes=volumes, roster=roster12)
    assert a2["any_alarm"] is False and a2["any_soft"] is False

    # 11 kurierów → 33 kh → load 2.18 → soft
    roster11 = {f"K{i}": {"start": "11:00", "end": "23:00"} for i in range(11)}
    a3 = df.assess(d, volumes=volumes, roster=roster11)
    assert a3["any_alarm"] is False and a3["any_soft"] is True


def test_assess_no_roster_degrades():
    d = date(2026, 6, 18)
    # 2 tygodnie wstecz = 04.06 (mult 1,0); tydzień wstecz = 11.06 payday
    # zostałby wykluczony z EWMA i prognoza wyszłaby pusta
    volumes = {d - timedelta(weeks=2): {"peak_lunch": 50}}
    a = df.assess(d, volumes=volumes, roster={})
    # pusty roster (dict) = "nikt nie pracuje" → capacity 0 przy prognozie > 0 → hard
    by_slot = {r["slot"]: r for r in a["rows"]}
    assert by_slot["peak_lunch"]["level"] == "hard"


def test_render_lines_icons():
    a = {"date": "2026-06-13", "dow": 5, "mult": 1.0, "mult_label": "",
         "any_alarm": False, "any_soft": True, "roster_available": True,
         "rows": [{"slot": "peak_lunch", "window": "11-14", "forecast": 60.0,
                   "courier_hours": 27.0, "headcount": 9, "load": 2.22,
                   "level": "soft", "add_couriers": 1}]}
    lines = df.render_lines(a, header_prefix="Obsada JUTRO (D-1)")
    assert lines[0].startswith("🟡 ")
    assert "szok ×1,3" in lines[1] and "+1" in lines[1]
