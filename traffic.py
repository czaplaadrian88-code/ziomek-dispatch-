"""Traffic multiplier - skaluje OSRM free-flow czasy do realnych warunkow.

Zrodlo wartosci: ground-truth Adrian 11.04.2026
- Off-peak (weekend caly dzien, wieczor/noc w tygodniu): 2-3 min miedzy punktami centrum
  OSRM pokazuje ~2.9 min -> multiplier ~1.0
- Peak (wt-pt 15:00-18:00): realnie 7-8 min dla tego samego dystansu
  multiplier ~2.7-3.0

TODO: kalibracja z sla_log.jsonl (actual delivery_time vs osrm predicted route)
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

WARSAW = ZoneInfo("Europe/Warsaw")

# Okna czasowe (godziny Warsaw)
PEAK_HOURS = {(15, 16, 17)}      # 15:00-17:59 wt-pt
SHOULDER_HOURS = {(12, 13, 14), (18, 19)}  # 12-14, 18-19 wt-pt
# reszta = off-peak

MULT_PEAK = 2.8
MULT_SHOULDER = 1.8
MULT_OFFPEAK = 1.0


def traffic_multiplier(dt: Optional[datetime] = None) -> float:
    """Zwraca mnoznik dla czasu podrozy OSRM -> realny.
    
    Args:
        dt: datetime (any tz, konwertowane do Warsaw). None = now.
    
    Returns:
        float mnoznik, min 1.0
    """
    if dt is None:
        dt = datetime.now(WARSAW)
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WARSAW)
        dt = dt.astimezone(WARSAW)
    
    # Weekend = off-peak niezaleznie od godziny
    if dt.weekday() >= 5:  # 5=sobota, 6=niedziela
        return MULT_OFFPEAK
    
    hour = dt.hour
    # Peak: 15-17
    if 15 <= hour <= 17:
        return MULT_PEAK
    # Shoulder: 12-14, 18-19
    if (12 <= hour <= 14) or (18 <= hour <= 19):
        return MULT_SHOULDER
    # Reszta (rano, wczesne popoludnie, noc) = off-peak
    return MULT_OFFPEAK


if __name__ == "__main__":
    # Self-test
    import datetime as dt_mod
    tests = [
        (dt_mod.datetime(2026, 4, 11, 17, 0, tzinfo=WARSAW), "sobota 17:00 (weekend)", 1.0),
        (dt_mod.datetime(2026, 4, 13, 16, 0, tzinfo=WARSAW), "poniedzialek 16:00", 2.8),
        (dt_mod.datetime(2026, 4, 13, 13, 0, tzinfo=WARSAW), "poniedzialek 13:00 shoulder", 1.8),
        (dt_mod.datetime(2026, 4, 13, 10, 0, tzinfo=WARSAW), "poniedzialek 10:00 off-peak", 1.0),
        (dt_mod.datetime(2026, 4, 13, 22, 0, tzinfo=WARSAW), "poniedzialek 22:00 off-peak", 1.0),
        (dt_mod.datetime(2026, 4, 12, 16, 0, tzinfo=WARSAW), "niedziela 16:00 weekend", 1.0),
    ]
    all_pass = True
    for d, name, expected in tests:
        got = traffic_multiplier(d)
        ok = abs(got - expected) < 0.01
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}: {got} (exp {expected})")
        if not ok:
            all_pass = False
    print("PASS" if all_pass else "FAIL")
