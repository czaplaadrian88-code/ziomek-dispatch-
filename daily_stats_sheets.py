#!/usr/bin/env python3
"""daily_stats_sheets.py — dzienny zapis statystyk do Google Sheets.

Źródło:
  - liczba zleceń: state_machine.get_all() filtrowane po first_seen
    w strefie Warsaw, wszystkie statusy
  - kolumna Ziomek: shadow_decisions.jsonl — unikalne courier_id z
    feasibility=MAYBE w propozycjach w danej godzinie

Layout bloku tygodnia (25 kolumn):
  A  godzina
  B/C/D   pon DD.MM  | /3 | Ziomek
  E/F/G   wt DD.MM   | /3 | Ziomek
  H/I/J   śr DD.MM   | /3 | Ziomek
  K/L/M   czw DD.MM  | /3 | Ziomek
  N/O/P   pt DD.MM   | /3 | Ziomek
  Q/R/S   sob DD.MM  | /3 | Ziomek
  T/U/V   nd DD.MM   | /3 | Ziomek
  W  Średnia
  X  Śr/3
  Y  Śr Ziomek

Interpreter: /root/.openclaw/venvs/sheets/bin/python3 (gspread + google-auth).
"""
import sys
import math
import json
import argparse
import logging
from collections import Counter
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import state_machine

import gspread
from gspread.utils import rowcol_to_a1
from gspread.exceptions import APIError, WorksheetNotFound

# ---------- config ----------
SERVICE_ACCOUNT_PATH = "/root/.openclaw/workspace/scripts/service_account.json"
SPREADSHEET_ID = "1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8"
WORKSHEET_NAME = "Średnie"
SHADOW_DECISIONS_PATH = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
WARSAW = ZoneInfo("Europe/Warsaw")
UTC = ZoneInfo("UTC")

HOUR_START = 9
HOUR_END = 23   # inclusive → 15 godzin

# Column layout
COL_GODZINA = 1
COL_DAY_FIRST = 2   # B — pon count
COLS_PER_DAY = 3    # count / /3 / Ziomek
N_DAYS = 7
COL_AVG = COL_DAY_FIRST + N_DAYS * COLS_PER_DAY           # 2 + 21 = 23 → W
COL_AVG_DIV3 = COL_AVG + 1                                # 24 → X
COL_AVG_ZIOMEK = COL_AVG + 2                              # 25 → Y
BLOCK_WIDTH = COL_AVG_ZIOMEK                              # 25

DAYS_SHORT = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
MONTHS_PL = [
    None, "Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
    "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień",
]

# Bartek Gold Standard fallback (avg bag 2.40 per docs/BARTEK_GOLD_STANDARD.md)
BARTEK_BAG_AVG = 2.4


def _hex(h: str) -> dict:
    h = h.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


COLOR_0 = _hex("FFFFFF")
COLOR_1_10 = _hex("FFE0B2")
COLOR_11_20 = _hex("FF9800")
COLOR_21_30 = _hex("E53935")
COLOR_30P = _hex("B71C1C")


def color_for_count(n: int) -> dict:
    if n == 0:
        return COLOR_0
    if n <= 10:
        return COLOR_1_10
    if n <= 20:
        return COLOR_11_20
    if n <= 30:
        return COLOR_21_30
    return COLOR_30P


# ---------- data source: orders ----------

def count_orders_by_hour(target_day: date) -> dict:
    orders = state_machine.get_all()
    items = orders.items() if isinstance(orders, dict) else enumerate(orders)
    counts = Counter()
    for _, o in items:
        fs = o.get("first_seen") or o.get("created_at")
        if not fs:
            continue
        try:
            dt = datetime.fromisoformat(fs.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        local = dt.astimezone(WARSAW)
        if local.date() != target_day:
            continue
        h = local.hour
        if HOUR_START <= h <= HOUR_END:
            counts[h] += 1
    return {h: counts.get(h, 0) for h in range(HOUR_START, HOUR_END + 1)}


# ---------- data source: ziomek availability ----------

def _parse_iso_utc(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def load_shadow_feasible_pool(target_day: date) -> dict:
    """Zwraca {hour: set(courier_id)} z shadow_decisions.jsonl dla dnia.

    Union po wszystkich propozycjach w godzinie: kurier jest "dostępny"
    jeśli był feasible (MAYBE) w choć jednej propozycji.
    """
    path = Path(SHADOW_DECISIONS_PATH)
    pools: dict = {h: set() for h in range(HOUR_START, HOUR_END + 1)}
    if not path.exists():
        return pools
    with path.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = _parse_iso_utc(d.get("ts"))
            if ts is None:
                continue
            local = ts.astimezone(WARSAW)
            if local.date() != target_day:
                continue
            h = local.hour
            if not (HOUR_START <= h <= HOUR_END):
                continue
            best = d.get("best") or {}
            if best and best.get("feasibility") == "MAYBE":
                cid = best.get("courier_id")
                if cid:
                    pools[h].add(str(cid))
            for c in (d.get("alternatives") or []):
                if c.get("feasibility") == "MAYBE":
                    cid = c.get("courier_id")
                    if cid:
                        pools[h].add(str(cid))
    return pools


def ziomek_recommendation(n_orders: int, feasible_pool: set) -> int:
    """max(ceil(n/3), ceil(n/avg_feasible)). Brak danych → ceil(n/2.4)."""
    if n_orders == 0:
        return 0
    avg_feasible = len(feasible_pool)
    if avg_feasible == 0:
        return math.ceil(n_orders / BARTEK_BAG_AVG)
    theory_min = math.ceil(n_orders / 3)
    practical = math.ceil(n_orders / avg_feasible)
    return max(theory_min, practical)


# ---------- week logic ----------

def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def day_header_label(monday: date, weekday_idx: int) -> str:
    d = monday + timedelta(days=weekday_idx)
    return f"{DAYS_SHORT[weekday_idx]} {d.strftime('%d.%m')}"


def header_target_token(monday: date) -> str:
    """First-day header label used to find an existing block."""
    return day_header_label(monday, 0)


def month_label_if_needed(monday: date, prev_monday) -> str | None:
    if prev_monday is None:
        return MONTHS_PL[monday.month]
    if monday.month != prev_monday.month:
        return MONTHS_PL[monday.month]
    return None


# ---------- sheet ops ----------

def find_block_for_week(all_values, monday: date):
    target = header_target_token(monday)
    for i, row in enumerate(all_values, start=1):
        if len(row) >= COL_DAY_FIRST and row[COL_DAY_FIRST - 1].strip() == target:
            return i
    return None


def find_last_nonempty_row(all_values) -> int:
    for i in range(len(all_values), 0, -1):
        if any(cell.strip() for cell in all_values[i - 1]):
            return i
    return 0


def find_latest_monday_in_sheet(all_values, reference_year: int):
    """Szuka najświeższego poniedziałku po headerach. Nagłówek skrócony
    'pon DD.MM' — rok wyciągamy z reference_year (arkusz nie trzyma roku)."""
    latest = None
    for row in all_values:
        if len(row) < COL_DAY_FIRST:
            continue
        cell = row[COL_DAY_FIRST - 1].strip()
        if not cell.startswith("pon "):
            continue
        try:
            ds = cell.split(" ", 1)[1]
            d = datetime.strptime(f"{ds}.{reference_year}", "%d.%m.%Y").date()
            if latest is None or d > latest:
                latest = d
        except Exception:
            pass
    return latest


def build_new_block(monday: date, include_month: bool):
    rows = []
    if include_month:
        rows.append([MONTHS_PL[monday.month]] + [""] * (BLOCK_WIDTH - 1))
    # Day header cells prefixed with apostrophe → Sheets treats as literal
    # text. Without this, "06.04" is parsed as date and re-localized to
    # "czw. 06.04" (June 4 thu), breaking find_block_for_week lookups.
    header = ["godzina"]
    for i in range(N_DAYS):
        header += ["'" + day_header_label(monday, i), "/3", "Ziomek"]
    header += ["Średnia", "Śr/3", "Śr Ziomek"]
    rows.append(header)
    for h in range(HOUR_START, HOUR_END + 1):
        rows.append([str(h)] + [""] * (BLOCK_WIDTH - 1))
    return rows


def day_col_offset(weekday_idx: int) -> int:
    """Zero-indexed col offset within block for the count cell of day idx."""
    return (COL_DAY_FIRST - 1) + weekday_idx * COLS_PER_DAY


def write_day_triple(ws, block_header_row, weekday_idx, counts, pools, dry_run, logger):
    """Wypełnia 3 kolumny dla danego dnia: count, /3, Ziomek (wszystkie godziny)."""
    first_hour_row = block_header_row + 1
    col_count = COL_DAY_FIRST + weekday_idx * COLS_PER_DAY
    col_div3 = col_count + 1
    col_ziomek = col_count + 2

    cell_updates = []
    color_updates = []
    for i, h in enumerate(range(HOUR_START, HOUR_END + 1)):
        row = first_hour_row + i
        n = counts[h]
        div3 = math.ceil(n / 3) if n > 0 else 0
        zi = ziomek_recommendation(n, pools.get(h, set()))
        cell_updates.append({"range": rowcol_to_a1(row, col_count), "values": [[n]]})
        cell_updates.append({"range": rowcol_to_a1(row, col_div3), "values": [[div3]]})
        cell_updates.append({"range": rowcol_to_a1(row, col_ziomek), "values": [[zi]]})
        color_updates.append((rowcol_to_a1(row, col_count), color_for_count(n)))

    if dry_run:
        logger.info(f"[dry-run] day {DAYS_SHORT[weekday_idx]}: {len(cell_updates)} cell updates + {len(color_updates)} colors")
        for u in cell_updates[:9]:
            logger.info(f"  {u['range']} = {u['values'][0][0]}")
        if len(cell_updates) > 9:
            logger.info(f"  ... +{len(cell_updates) - 9} more")
        return

    ws.batch_update(cell_updates, value_input_option="USER_ENTERED")
    for a1, color in color_updates:
        ws.format(a1, {"backgroundColor": color})
    logger.info(f"wrote {len(cell_updates)} cells for day {DAYS_SHORT[weekday_idx]}")


def _safe_get(all_values, row, col):
    try:
        return all_values[row - 1][col - 1]
    except IndexError:
        return ""


def _parse_int(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def recompute_week_averages(ws, block_header_row, all_values, dry_run, logger):
    """Średnia zleceń, Śr/3, Śr Ziomek per wiersz godziny."""
    first_hour_row = block_header_row + 1
    last_hour_row = first_hour_row + (HOUR_END - HOUR_START)

    updates = []
    for row in range(first_hour_row, last_hour_row + 1):
        day_counts = []
        ziomek_vals = []
        for d in range(N_DAYS):
            c_col = COL_DAY_FIRST + d * COLS_PER_DAY
            z_col = c_col + 2
            cnt = _parse_int(_safe_get(all_values, row, c_col))
            zi = _parse_int(_safe_get(all_values, row, z_col))
            if cnt is not None:
                day_counts.append(cnt)
            if zi is not None:
                ziomek_vals.append(zi)

        if day_counts:
            avg = sum(day_counts) / len(day_counts)
            updates.append({"range": rowcol_to_a1(row, COL_AVG), "values": [[round(avg, 2)]]})
            updates.append({"range": rowcol_to_a1(row, COL_AVG_DIV3),
                            "values": [[math.ceil(avg / 3) if avg > 0 else 0]]})
        if ziomek_vals:
            avg_z = sum(ziomek_vals) / len(ziomek_vals)
            updates.append({"range": rowcol_to_a1(row, COL_AVG_ZIOMEK),
                            "values": [[round(avg_z, 1)]]})

    if dry_run:
        logger.info(f"[dry-run] would recompute {len(updates)} avg cells across hours")
        for u in updates[:6]:
            logger.info(f"  {u['range']} = {u['values'][0][0]}")
        if len(updates) > 6:
            logger.info(f"  ... +{len(updates) - 6} more")
        return

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        logger.info(f"recomputed {len(updates)} average cells")


def upsert_block(ws, monday: date, logger, dry_run: bool):
    all_values = ws.get_all_values()
    existing = find_block_for_week(all_values, monday)
    if existing is not None:
        logger.info(f"block for week {monday.isoformat()} exists at row {existing}")
        return existing, all_values, False

    last_row = find_last_nonempty_row(all_values)
    prev_monday = find_latest_monday_in_sheet(all_values, reference_year=monday.year)
    month_label = month_label_if_needed(monday, prev_monday)
    new_block = build_new_block(monday, include_month=month_label is not None)
    if last_row > 0:
        new_block = [[""] * BLOCK_WIDTH] + new_block

    start_row = last_row + 1
    logger.info(
        f"new block for week {monday.isoformat()} at row {start_row} "
        f"(month_label={month_label}, rows={len(new_block)})"
    )
    if dry_run:
        for i, row in enumerate(new_block):
            preview = [str(c) for c in row[:8]] + (["..."] if len(row) > 8 else [])
            logger.info(f"  [dry-run] row {start_row + i}: {preview}")
    else:
        needed = start_row + len(new_block) - 1
        if ws.row_count < needed:
            ws.add_rows(needed - ws.row_count + 10)
        if ws.col_count < BLOCK_WIDTH:
            ws.add_cols(BLOCK_WIDTH - ws.col_count)
        end_row = start_row + len(new_block) - 1
        rng = f"{rowcol_to_a1(start_row, 1)}:{rowcol_to_a1(end_row, BLOCK_WIDTH)}"
        ws.update(values=new_block, range_name=rng, value_input_option="USER_ENTERED")

    header_offset = 0
    if last_row > 0:
        header_offset += 1  # blank separator
    if month_label is not None:
        header_offset += 1
    header_row = start_row + header_offset

    refreshed = all_values if dry_run else ws.get_all_values()
    return header_row, refreshed, True


def column_already_populated(all_values, header_row, weekday_idx: int) -> bool:
    col = COL_DAY_FIRST + weekday_idx * COLS_PER_DAY
    first_hour_row = header_row + 1
    last_hour_row = first_hour_row + (HOUR_END - HOUR_START)
    for row in range(first_hour_row, last_hour_row + 1):
        if (_safe_get(all_values, row, col) or "").strip():
            return True
    return False


def apply_block_borders(ws, header_row: int, has_month_label: bool, logger):
    """Grube ramki zewnętrzne + cienkie wewnętrzne dla bloku tygodnia.

    Blok = optional month-label row + header row + 15 hour rows.
    Używa Sheets API batch_update — gspread.format() nie rozróżnia
    outer/inner granicznie.
    """
    first_row_1 = header_row - (1 if has_month_label else 0)
    last_row_1 = header_row + (HOUR_END - HOUR_START) + 1  # header + 15 hours

    solid_thick = {"style": "SOLID_THICK", "color": {"red": 0, "green": 0, "blue": 0}}
    solid = {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}}

    body = {
        "requests": [{
            "updateBorders": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": first_row_1 - 1,     # 0-indexed inclusive
                    "endRowIndex": last_row_1,            # 0-indexed exclusive
                    "startColumnIndex": 0,
                    "endColumnIndex": BLOCK_WIDTH,
                },
                "top": solid_thick,
                "bottom": solid_thick,
                "left": solid_thick,
                "right": solid_thick,
                "innerHorizontal": solid,
                "innerVertical": solid,
            }
        }]
    }
    ws.spreadsheet.batch_update(body)
    logger.info(
        f"borders: rows {first_row_1}..{last_row_1} "
        f"(has_month_label={has_month_label})"
    )


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Daily dispatch stats → Google Sheets")
    ap.add_argument("--dry-run", action="store_true", help="No writes, log only")
    ap.add_argument("--date", help="Override target date YYYY-MM-DD (Warsaw)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("daily_stats")

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target = (datetime.now(WARSAW) - timedelta(days=1)).date()

    weekday_idx = target.weekday()
    log.info(f"target: {target.isoformat()} ({DAYS_SHORT[weekday_idx]})")

    counts = count_orders_by_hour(target)
    total = sum(counts.values())
    log.info(f"orders total={total}  hourly={dict((h,counts[h]) for h in sorted(counts))}")

    pools = load_shadow_feasible_pool(target)
    pool_sizes = {h: len(pools.get(h, set())) for h in range(HOUR_START, HOUR_END + 1)}
    nonzero_pools = sum(1 for v in pool_sizes.values() if v > 0)
    log.info(f"ziomek pools: {nonzero_pools}/15 hours have shadow data  sizes={pool_sizes}")

    # Short-circuit dry-run if no sheet access needed for preview only
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_PATH)
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
    except APIError as e:
        log.error(f"APIError opening sheet — share with {gc.auth.service_account_email}? {e}")
        sys.exit(2)
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except WorksheetNotFound:
        log.error(f"worksheet '{WORKSHEET_NAME}' not found")
        sys.exit(3)
    log.info(f"opened '{WORKSHEET_NAME}' ({ws.row_count}x{ws.col_count})")

    monday = monday_of(target)
    header_row, all_values, created = upsert_block(ws, monday, log, args.dry_run)

    # Apply borders for freshly-created block (idempotent redraws are fine too,
    # but cheaper to do it only once per block creation).
    if created and not args.dry_run:
        # has_month_label determined by row above header — peek at A column
        has_month = False
        if header_row >= 2:
            try:
                cell_a = ws.cell(header_row - 1, 1).value or ""
                has_month = cell_a.strip() in set(MONTHS_PL[1:])
            except Exception:
                pass
        apply_block_borders(ws, header_row, has_month, log)

    if not created and column_already_populated(all_values, header_row, weekday_idx):
        log.info(f"{DAYS_SHORT[weekday_idx]} column already populated → skip (idempotent)")
        return

    write_day_triple(ws, header_row, weekday_idx, counts, pools, args.dry_run, log)

    # Refresh in-memory view to include what we just wrote so recompute works
    if not args.dry_run:
        all_values = ws.get_all_values()
    else:
        first_hour_row = header_row + 1
        c_off = day_col_offset(weekday_idx)
        while len(all_values) < first_hour_row + (HOUR_END - HOUR_START):
            all_values.append([""] * BLOCK_WIDTH)
        for i, h in enumerate(range(HOUR_START, HOUR_END + 1)):
            ri = first_hour_row - 1 + i
            while len(all_values[ri]) < BLOCK_WIDTH:
                all_values[ri].append("")
            n = counts[h]
            div3 = math.ceil(n / 3) if n > 0 else 0
            zi = ziomek_recommendation(n, pools.get(h, set()))
            all_values[ri][c_off] = str(n)
            all_values[ri][c_off + 1] = str(div3)
            all_values[ri][c_off + 2] = str(zi)

    recompute_week_averages(ws, header_row, all_values, args.dry_run, log)
    log.info("done")


if __name__ == "__main__":
    main()
