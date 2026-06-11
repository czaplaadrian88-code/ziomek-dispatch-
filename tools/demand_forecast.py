"""demand_forecast — prognoza popytu + alarm obsady D-1 (SP-B2-OBSADA, QW7).

Problem (raport Bartek 2.0 §3/§10 QW7; mining H7/H8/H9, krzywa 1f): krachy to
obsada, nie algorytm — 16.05: 384 zlec./11 kurierów = breach 32,8%; payday
(9-12 dzień miesiąca) +20% wolumenu bez skalowania floty (elastyczność podaży
0,28); load >2,7 zlec./kurier-h = strefa breach >10%. Symulator: +4 kurierów
na 16.05 → 0,5% breach. Alarm D-1 w briefingu = największy pojedynczy ROI.

Co robi:
  1. Wolumeny historyczne per (data × slot) z CSV historii panelu
     (/root/panel_history_new/*.csv — parsing współdzielony z
     restaurant_prep_bias: BOM/multiline/HH:MM-rollover/dedup zid).
  2. EWMA per (dow × slot) na dziennych wolumenach; dni specjalne (kalendarz
     niżej) NIE aktualizują EWMA (Walentynki nie zatruwają średniej soboty).
  3. Prognoza dla daty D = EWMA(dow,slot) × mnożnik kalendarza(D).
  4. Roster D z grafiku (Google Sheets przez fetch_schedule.fetch_csv +
     parse_schedule — działa dla dowolnej daty obecnej w arkuszu;
     fallback: dispatch_state/schedule_today.json gdy data pasuje).
  5. load = prognoza zleceń w slocie / kurierogodziny w slocie;
     ALARM gdy load > 2,7 (mining H-LOAD: 2,5-3 → breach 10,4%, 3,5+ → 17,7%)
     + konkret "dołóż N" (N = ile kurierogodzin brakuje do load 2,5).

Kalendarz korekt (mining REPORT 1a/1b):
  payday 9-12 dz.mies. ×1,2 · Walentynki 14.02 ×1,75 · Niepodległości 11.11
  ×1,96 · Dzień Dziecka 01.06 ×1,4 · Nowy Rok 01.01 ×1,5 · majówka 1-3.05
  ×0,8 · 26.12 ×0,6.

Konsumpcja: sekcja w daily_briefing (morning → DZIŚ, evening → JUTRO = D-1
alarm); briefing idzie istniejącym kanałem Telegram (zero nowego spamu —
przy przekroczeniu progu sekcja dostaje nagłówek 🔴).

Użycie:
  python3 -m dispatch_v2.tools.demand_forecast            # ocena jutra
  python3 -m dispatch_v2.tools.demand_forecast --date 2026-06-14
  python3 -m dispatch_v2.tools.demand_forecast --backtest 2026-05-16 2026-02-14 2026-04-10

Testy: dispatch_v2/tests/test_b2_demand_forecast.py.
"""
from __future__ import annotations

import argparse
import csv
import glob
import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

try:
    from dispatch_v2.tools.restaurant_prep_bias import (
        _combine_hhmm,
        _parse_created,
    )
except ImportError:  # uruchomienie bezpośrednie
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from dispatch_v2.tools.restaurant_prep_bias import (
        _combine_hhmm,
        _parse_created,
    )

CSV_GLOB = "/root/panel_history_new/*.csv"
SCHEDULE_TODAY = "/root/.openclaw/workspace/dispatch_state/schedule_today.json"
FETCH_SCHEDULE_PY = "/root/.openclaw/workspace/scripts/fetch_schedule.py"
_EXPECTED_HDR_FIRST = "nr zlecenia"

csv.field_size_limit(10_000_000)

# Sloty operacyjne obsady (godziny Warsaw). Evening poza kontraktem calib_maps
# (to tabela operacyjna, nie mapa kalibracyjna).
SLOTS: List[Tuple[str, int, int]] = [
    ("peak_lunch", 11, 14),
    ("high_risk", 14, 17),
    ("peak_dinner", 17, 20),
    ("evening", 20, 23),
]
EWMA_ALPHA = 0.25
LOAD_ALARM = 2.7      # 🔴 zlec./kurierogodzinę (mining H-LOAD: 2,5-3 → 10,4%)
SHOCK_MULT = 1.3      # mining 1f: dni z popytem >1,3× oczekiwanego → breach 16,2%
LOAD_SHOCK_GUARD = LOAD_ALARM / SHOCK_MULT  # 🟡 ≈2,08 — zero marginesu na szok
LOAD_TARGET = 2.5     # do wyliczenia "dołóż N"
MIN_SHIFT_OVERLAP_H = 0.5


def calendar_multiplier(d: date) -> Tuple[float, str]:
    """Mnożnik wolumenu + etykieta dla daty (mining 1a/1b)."""
    md = (d.month, d.day)
    if md == (2, 14):
        return 1.75, "Walentynki"
    if md == (11, 11):
        return 1.96, "Św. Niepodległości"
    if md == (6, 1):
        return 1.4, "Dzień Dziecka"
    if md == (1, 1):
        return 1.5, "Nowy Rok"
    if md in ((5, 1), (5, 2), (5, 3)):
        return 0.8, "majówka"
    if md == (12, 26):
        return 0.6, "II dzień Świąt"
    if 9 <= d.day <= 12:
        return 1.2, "payday 9-12"
    return 1.0, ""


def slot_for_hour(h: int) -> Optional[str]:
    for name, lo, hi in SLOTS:
        if lo <= h < hi:
            return name
    return None


def read_daily_volumes(days: int = 365):
    """Z CSV historii: {data: {slot: n_zlec}} + {data: set(kurierów ze zleceniem
    w slocie)} (delivered only; godzina = czas odbioru, fallback created)."""
    cutoff = (datetime.now() - timedelta(days=days)).date()
    seen_zid: set = set()
    volumes: Dict[date, Dict[str, int]] = {}
    couriers: Dict[date, Dict[str, set]] = {}
    paths = sorted(glob.glob(CSV_GLOB), key=lambda p: os.path.getmtime(p),
                   reverse=True)
    for path in paths:
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                hdr = reader.fieldnames or []
                if not hdr or (hdr[0] or "").strip().lower() != _EXPECTED_HDR_FIRST:
                    continue
                for row in reader:
                    zid = (row.get("nr zlecenia") or "").strip()
                    if not zid or zid in seen_zid:
                        continue
                    seen_zid.add(zid)
                    if (row.get("status") or "").strip() != "doręczone":
                        continue
                    created = _parse_created(row.get("data złożenia zlecenia") or "")
                    if created is None or created.date() < cutoff:
                        continue
                    t_odbior = _combine_hhmm(created, row.get("czas odbioru") or "")
                    when = t_odbior or created
                    slot = slot_for_hour(when.hour)
                    if slot is None:
                        continue
                    dkey = when.date()
                    volumes.setdefault(dkey, {})[slot] = (
                        volumes.get(dkey, {}).get(slot, 0) + 1
                    )
                    kur = (row.get("kurier") or "").strip()
                    if kur:
                        couriers.setdefault(dkey, {}).setdefault(slot, set()).add(kur)
        except OSError:
            continue
    return volumes, couriers


def ewma_table(volumes: Dict[date, Dict[str, int]],
               until: Optional[date] = None) -> Dict[Tuple[int, str], float]:
    """EWMA per (dow, slot) chronologicznie; dni z mnożnikiem ≠1 pomijane
    w aktualizacji; `until` (exclusive) = tryb backtest."""
    table: Dict[Tuple[int, str], float] = {}
    for d in sorted(volumes):
        if until is not None and d >= until:
            break
        mult, _ = calendar_multiplier(d)
        if abs(mult - 1.0) > 0.05:
            continue
        for slot, _lo, _hi in SLOTS:
            n = volumes[d].get(slot, 0)
            key = (d.weekday(), slot)
            prev = table.get(key)
            table[key] = float(n) if prev is None else (
                EWMA_ALPHA * n + (1 - EWMA_ALPHA) * prev
            )
    return table


def forecast_for(d: date, table: Dict[Tuple[int, str], float]):
    """{slot: prognoza_zleceń} + (mult, label)."""
    mult, label = calendar_multiplier(d)
    out = {}
    for slot, _lo, _hi in SLOTS:
        base = table.get((d.weekday(), slot))
        out[slot] = None if base is None else base * mult
    return out, mult, label


# ---- roster (grafik) ----

def _load_fetch_schedule_module():
    spec = importlib.util.spec_from_file_location("fetch_schedule",
                                                  FETCH_SCHEDULE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_roster(d: date) -> Optional[Dict[str, dict]]:
    """Grafik {kurier: {start,end}|None} dla daty d. Najpierw świeży arkusz
    (działa dla dowolnej daty w arkuszu), fallback schedule_today.json gdy
    data pasuje. None = brak danych (sekcja briefingu degraduje się jawnie)."""
    target = d.strftime("%d-%m-%y")
    try:
        fs = _load_fetch_schedule_module()
        sched = fs.parse_schedule(fs.fetch_csv(), target)
        if isinstance(sched, dict) and sched:
            return sched
    except Exception:
        pass
    try:
        with open(SCHEDULE_TODAY, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == target and isinstance(data.get("couriers"), dict):
            return data["couriers"]
    except Exception:
        pass
    return None


def _parse_hhmm_h(s: str) -> Optional[float]:
    try:
        hh, mm = str(s).strip().split(":")[:2]
        return int(hh) + int(mm) / 60.0
    except (ValueError, AttributeError, TypeError):
        return None


def roster_slot_capacity(roster: Dict[str, dict]):
    """{slot: {'courier_hours': float, 'headcount': int}} z grafiku."""
    out = {slot: {"courier_hours": 0.0, "headcount": 0} for slot, _l, _h in SLOTS}
    for _name, hours in (roster or {}).items():
        if not isinstance(hours, dict):
            continue
        st = _parse_hhmm_h(hours.get("start"))
        en = _parse_hhmm_h(hours.get("end"))
        if st is None or en is None:
            continue
        if en <= st:
            en = 23.99  # zmiana "do końca dnia" / błędny wpis — fail-open
        for slot, lo, hi in SLOTS:
            ov = max(0.0, min(en, hi) - max(st, lo))
            if ov >= MIN_SHIFT_OVERLAP_H:
                out[slot]["courier_hours"] += ov
                out[slot]["headcount"] += 1
    return out


# ---- ocena + render ----

def assess(d: date, volumes=None, roster: Optional[dict] = None) -> dict:
    """Pełna ocena obsady dla daty d (prognoza + grafik + alarmy)."""
    if volumes is None:
        volumes, _ = read_daily_volumes()
    table = ewma_table(volumes, until=d)
    fc, mult, label = forecast_for(d, table)
    if roster is None:
        roster = fetch_roster(d)
    # roster {} = "nikt nie pracuje" (alarm); None = brak danych (degradacja)
    cap = roster_slot_capacity(roster) if roster is not None else None

    rows = []
    any_hard = any_soft = False
    for slot, lo, hi in SLOTS:
        f = fc.get(slot)
        row = {"slot": slot, "window": f"{lo:02d}-{hi:02d}", "forecast": f,
               "courier_hours": None, "headcount": None, "load": None,
               "level": None, "add_couriers": 0}
        if cap is not None and f is not None:
            ch = cap[slot]["courier_hours"]
            row["courier_hours"] = round(ch, 1)
            row["headcount"] = cap[slot]["headcount"]
            slot_len = hi - lo
            if ch > 0:
                load = f / ch
                row["load"] = round(load, 2)
                if load > LOAD_ALARM:
                    row["level"] = "hard"
                    any_hard = True
                    # ile pełno-slotowych kurierów brakuje do LOAD_TARGET
                    need_h = f / LOAD_TARGET - ch
                    row["add_couriers"] = max(1, int(need_h / slot_len + 0.999))
                elif load > LOAD_SHOCK_GUARD:
                    # 🟡 margines na szok ×1,3 zerowy (wzorzec 16.05: wielkość
                    # szoku nieprzewidywalna z historii — bronimy się marginesem)
                    row["level"] = "soft"
                    any_soft = True
                    need_h = f * SHOCK_MULT / LOAD_ALARM - ch
                    row["add_couriers"] = max(1, int(need_h / slot_len + 0.999))
            elif f > 0:
                row["level"] = "hard"
                any_hard = True
                row["add_couriers"] = max(1, int(f / LOAD_TARGET / slot_len + 0.999))
        rows.append(row)
    return {"date": d.isoformat(), "dow": d.weekday(), "mult": mult,
            "mult_label": label, "rows": rows, "any_alarm": any_hard,
            "any_soft": any_soft, "roster_available": cap is not None}


_DOW_PL = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]


def render_lines(a: dict, header_prefix: str = "Obsada") -> List[str]:
    """Blok tekstu do briefingu (mobile-readable)."""
    d = date.fromisoformat(a["date"])
    head = f"{header_prefix} {_DOW_PL[a['dow']]} {d.strftime('%d.%m')}"
    if a["mult_label"]:
        head += f" [{a['mult_label']} ×{a['mult']}]"
    icon = "🔴 " if a["any_alarm"] else ("🟡 " if a.get("any_soft") else "✅ ")
    lines = [icon + head + ":"]
    if not a["roster_available"]:
        lines.append("• ⚠ brak grafiku na ten dzień — sama prognoza:")
    for r in a["rows"]:
        if r["forecast"] is None:
            continue
        seg = f"• {r['window']}: ~{r['forecast']:.0f} zlec."
        if r["load"] is not None:
            seg += f" / {r['headcount']}kur ({r['courier_hours']}kh) = {r['load']}/kh"
        if r["level"] == "hard":
            seg += f" 🔴 DOŁÓŻ {r['add_couriers']}"
        elif r["level"] == "soft":
            seg += f" 🟡 szok ×1,3 bez marginesu (+{r['add_couriers']} da zapas)"
        lines.append(seg)
    return lines


# ---- backtest ----

def backtest(dates: List[date]) -> List[dict]:
    """Czy alarm D-1 złapałby dzień D? Prognoza = EWMA do D (excl.) × mnożnik;
    pojemność = realni kurierzy dnia z CSV (distinct w slocie × długość slotu —
    przybliżenie kurierogodzin w górę, czyli load zaniżony = test konserwatywny).
    """
    volumes, couriers = read_daily_volumes(days=400)
    out = []
    for d in dates:
        table = ewma_table(volumes, until=d)
        fc, mult, label = forecast_for(d, table)
        rows = []
        fired = None
        for slot, lo, hi in SLOTS:
            f = fc.get(slot)
            actual = volumes.get(d, {}).get(slot, 0)
            ncour = len(couriers.get(d, {}).get(slot, set()))
            ch = ncour * (hi - lo)
            load_fc = (f / ch) if (f and ch) else None
            load_real = (actual / ch) if ch else None
            level = None
            if (load_fc and load_fc > LOAD_ALARM) or (f and ch == 0):
                level = "hard"
            elif load_fc and load_fc > LOAD_SHOCK_GUARD:
                level = "soft"
            if level == "hard" or (level == "soft" and fired is None):
                fired = level
            rows.append({"slot": slot, "forecast": round(f, 1) if f else None,
                         "actual": actual, "couriers": ncour,
                         "load_fc": round(load_fc, 2) if load_fc else None,
                         "load_real": round(load_real, 2) if load_real else None,
                         "level": level})
        out.append({"date": d.isoformat(), "mult": mult, "label": label,
                    "fired": fired, "rows": rows})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--date", help="YYYY-MM-DD (default: jutro)")
    p.add_argument("--backtest", nargs="+", metavar="YYYY-MM-DD",
                   help="tryb backtest dla podanych dat")
    args = p.parse_args(argv)

    if args.backtest:
        marks = {"hard": "🔴 ZŁAPANY (hard)", "soft": "🟡 ZŁAPANY (soft)",
                 None: "⚪ cisza"}
        for rep in backtest([date.fromisoformat(x) for x in args.backtest]):
            lbl = f" [{rep['label']} ×{rep['mult']}]" if rep["label"] else ""
            print(f"{rep['date']}{lbl}: {marks[rep['fired']]}")
            for r in rep["rows"]:
                ic = {"hard": " 🔴", "soft": " 🟡"}.get(r["level"], "")
                print(f"  {r['slot']:>12}: fc={r['forecast']} real={r['actual']} "
                      f"kur={r['couriers']} load_fc={r['load_fc']} "
                      f"load_real={r['load_real']}{ic}")
        return 0

    d = (date.fromisoformat(args.date) if args.date
         else date.today() + timedelta(days=1))
    a = assess(d)
    for line in render_lines(a):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
