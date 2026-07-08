#!/usr/bin/env python3
# STAGING COPY (NIE żywy plik) — podmiana za ACK koordynatora, off-peak.
# STAGING: kopia /root/.openclaw/workspace/scripts/fetch_schedule.py z fixami finding H.
# STAGING: diff vs żywy = TYLKO H1 (today=ZoneInfo Warsaw zamiast naive-UTC, helper
# STAGING: _today_warsaw) + H2 (salvage wpisu kuriera przy literówce godziny za flagą
# STAGING: ENABLE_GRAFIK_ENTRY_SALVAGE default OFF, reader _flag). Reszta bajt-identyczna.
# STAGING: sekwencja deployu + rollback = eod_drafts/2026-07-02/grafik-h_raport.md.
"""
fetch_schedule.py v2 — pobiera grafik kurierów z Google Sheets
Uruchamiany codziennie o 06:00 przez cron.
Zapisuje: /root/.openclaw/workspace/dispatch_state/schedule_today.json
"""

import csv, json, sys, os, urllib.request
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

# H1 (audyt 2.0 finding H): dzień grafiku w Warsaw wall-clock, NIE naive-UTC.
# Serwer=UTC; w oknie Warsaw 00:00-02:00 UTC to jeszcze POPRZEDNI dzień →
# ładował się WCZORAJSZY grafik dla całej floty. ZoneInfo=stdlib, fail-loud
# (BEZ fixed-offset fallbacku = brak bomby TZ po końcu DST 25-26.10).
_WARSAW = ZoneInfo("Europe/Warsaw")
# H2: flags.json (te same flagi co silnik dispatch_v2). Skrypt biega jako świeży
# subprocess per T3 tick → fresh read wystarcza, bez cache.
FLAGS_PATH = "/root/.openclaw/workspace/scripts/flags.json"

SHEET_ID  = "1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8"
SHEET_GID = "533254920"
OUTPUT    = "/root/.openclaw/workspace/dispatch_state/schedule_today.json"

# Wiersze które NIE są kurierami — filtrowane po zawartości
SKIP_PATTERNS = [
    "potrzeby", "cały dzień", "popołudnia", "gotowy", "linka",
    "baku", "oponie", "wkręt", "dostępność", "maja", "połowie",
    "w tygodniu", "w tyg", "sekcja", "razem", "suma"
]

def log(msg):
    print(f"[fetch_schedule] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)

def _flag(name, default=False):
    """H2: czyta flagę z flags.json (prosty json.load; brak pliku/klucza/parse
    error → default). Świadomie fail-safe: awaria odczytu flagi NIE zmienia
    zachowania (default OFF = kasowanie wpisu jak dziś)."""
    try:
        with open(FLAGS_PATH, encoding="utf-8") as f:
            return bool(json.load(f).get(name, default))
    except Exception:
        return default

def _today_warsaw():
    """H1: bieżący dzień w strefie Warsaw ('%d-%m-%y'). JEDNO źródło wyboru dnia."""
    return datetime.now(_WARSAW).strftime("%d-%m-%y")

def is_valid_courier_name(name):
    """Sprawdza czy wiersz to prawdziwy kurier, nie śmieciowy tekst."""
    if not name or len(name) < 3:
        return False
    name_lower = name.lower()
    for pattern in SKIP_PATTERNS:
        if pattern in name_lower:
            return False
    # Kurier ma imię i nazwisko (przynajmniej 2 słowa) lub 1 słowo >= 4 znaki
    words = name.split()
    if len(words) >= 2:
        return True
    if len(words) == 1 and len(words[0]) >= 4:
        return True
    return False

def parse_hour(raw):
    s = raw.strip()
    if not s: return None
    if ":" in s:
        parts = s.split(":")
        try: return f"{int(parts[0]):02d}:{parts[1].zfill(2)}"
        except: return None
    try:
        h = int(s)
        if 0 <= h <= 24: return f"{h:02d}:00"
    except: pass
    return None

def normalize_date(d):
    d = d.strip()
    parts = d.split("-")
    if len(parts) == 3:
        try: return f"{int(parts[0]):02d}-{int(parts[1]):02d}-{parts[2].zfill(2)}"
        except: pass
    return d

def fetch_csv():
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"
    log(f"Pobieranie CSV...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8-sig")
    log(f"Pobrano {len(raw)} bajtów")
    return raw

def find_date_columns(header_row, target_date):
    last_date = None
    count_for_date = 0
    col_start = None
    for i, cell in enumerate(header_row):
        stripped = cell.strip()
        if stripped and not stripped.upper().startswith("SUMA"):
            last_date = normalize_date(stripped)
            count_for_date = 0
        if last_date == target_date:
            count_for_date += 1
            if count_for_date == 1: col_start = i
            elif count_for_date == 2: return col_start, i
    return None, None

def parse_schedule(csv_text, target_date):
    reader = csv.reader(StringIO(csv_text))
    rows = list(reader)
    if not rows: raise ValueError("Pusty CSV")

    header = rows[0]
    log(f"Szukam daty {target_date} ({len(header)} kolumn w nagłówku)")

    col_start, col_end = find_date_columns(header, target_date)
    if col_start is None:
        found = []
        for cell in header:
            s = cell.strip()
            if s and not s.upper().startswith("SUMA"):
                found.append(normalize_date(s))
        raise ValueError(f"Nie znaleziono '{target_date}'. Znalezione: {found[:10]}")

    log(f"Kolumny dla {target_date}: start={col_start}, koniec={col_end}")

    schedule = {}
    skipped_garbage = []
    salvage_on = _flag("ENABLE_GRAFIK_ENTRY_SALVAGE", False)

    for row in rows[1:]:
        if len(row) < 2: continue
        name = row[1].strip()

        if not is_valid_courier_name(name):
            if name:
                skipped_garbage.append(name)
            continue

        raw_start = row[col_start].strip() if len(row) > col_start else ""
        raw_end   = row[col_end].strip()   if len(row) > col_end   else ""
        start_fmt = parse_hour(raw_start)
        end_fmt   = parse_hour(raw_end)

        if start_fmt and end_fmt:
            schedule[name] = {"start": start_fmt, "end": end_fmt}
        elif (start_fmt or end_fmt) and salvage_on:
            # H2 (audyt 2.0 finding H): literówka w JEDNEJ komórce (np. "1O:00" z
            # literą O) NIE kasuje całego wpisu → kurier ZOSTAJE w puli. WARNING
            # robi literówkę WIDOCZNĄ (nazwa + surowa komórka), żeby nie znikała
            # cicho. Konsument is_on_shift (schedule_utils) przy None jednej
            # godziny idzie fail-open = kurier dostępny (nie NO_ACTIVE_SHIFT).
            schedule[name] = {"start": start_fmt, "end": end_fmt, "parse_degraded": True}
            log(f"UWAGA parse_hour degraded kurier={name!r}: "
                f"start={raw_start!r} end={raw_end!r} — kurier ZOSTAJE w puli "
                f"(flaga ENABLE_GRAFIK_ENTRY_SALVAGE)")
        else:
            schedule[name] = None

    if skipped_garbage:
        log(f"Pominięto {len(skipped_garbage)} wierszy nie-kurierów: {skipped_garbage[:5]}")

    return schedule

def main():
    debug = "--debug" in sys.argv
    today = _today_warsaw()
    log(f"Start, data: {today}")

    try:
        csv_text = fetch_csv()
    except Exception as e:
        log(f"BŁĄD pobierania: {e}"); return 1

    if debug:
        lines = csv_text.splitlines()
        log("=== DEBUG nagłówek (pierwsze 400 znaków) ===")
        print(lines[0][:400])

    try:
        schedule = parse_schedule(csv_text, today)
    except Exception as e:
        log(f"BŁĄD parsowania: {e}"); return 1

    working     = [(k, v) for k, v in schedule.items() if v]
    not_working = [k for k, v in schedule.items() if not v]

    log(f"Pracuje dziś {len(working)}/{len(schedule)}:")
    # None-safe: wpisy salvage (parse_degraded, np. pusta godzina startu) mają
    # start=None → bez fallbacku sorted() rzuca TypeError i BLOKUJE zapis pliku
    # (log wyświetlania nie może wywalić fetchu). Degraded na koniec listy.
    for name, hours in sorted(working, key=lambda x: x[1]['start'] or "99:99"):
        log(f"  {hours['start']}–{hours['end']}  {name}")

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump({
            "date":       today,
            "fetched_at": datetime.now().isoformat(),
            "couriers":   schedule
        }, f, ensure_ascii=False, indent=2)

    log(f"Zapisano → {OUTPUT}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
