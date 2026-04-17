"""Auto-detekcja kolumny docelowej + walidacje + (KROK 4) batch write.

Identyfikacja kolumny po DACIE WYPŁATY (pos 3 bloku) — najpewniejszy klucz
w obecnej konwencji arkusza 'Wynagrodzenia Gastro'. Nie polega na pos 4
(zakres dat), który bywa niespójny.

Reguła: wypłata = środa (+3 dni) po niedzieli tygodnia.
"""
import calendar
import logging
import re
from datetime import date, timedelta
from typing import List, Tuple, Dict

import gspread
from google.oauth2.service_account import Credentials

from dispatch_v2.cod_weekly.config import (
    SERVICE_ACCOUNT_PATH,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
    ROW_START,
    SEARCH_BLOCK_START_COL,
)
from dispatch_v2.cod_weekly.aliases import SHEET_SKIP_PREFIXES

log = logging.getLogger("cod_weekly.sheet")

RANGE_RE = re.compile(r"^\s*(\d{1,2})-(\d{1,2})\.(\d{2})\.(\d{4})\s*$")

SCOPES_RW = ["https://www.googleapis.com/auth/spreadsheets"]


class NoTargetColumnError(Exception):
    pass


class AmbiguousTargetError(Exception):
    pass


def col_idx_to_letter(n_zero_based: int) -> str:
    """0 → 'A', 25 → 'Z', 26 → 'AA', 54 → 'BC'."""
    n = n_zero_based + 1
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _norm_header(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\-]+", " ", s)
    return s


def split_week_by_month(start: date, end: date) -> List[Dict]:
    """1 segment (cały miesiąc) lub 2 segmenty (krosuje miesiąc)."""
    if start.month == end.month and start.year == end.year:
        return [{"start": start, "end": end}]
    last_of_start = date(
        start.year, start.month,
        calendar.monthrange(start.year, start.month)[1],
    )
    first_of_end = date(end.year, end.month, 1)
    return [
        {"start": start, "end": last_of_start},
        {"start": first_of_end, "end": end},
    ]


def compute_payday(week_start: date, week_end: date) -> date:
    """Środa (+3 dni) po niedzieli tygodnia.

    Gdy segment jest częścią rozbitego tygodnia, używamy pełnej niedzieli
    tygodnia (week_start..+6 dni), nie końca segmentu.
    """
    sunday = week_start + timedelta(days=(6 - week_start.weekday()))
    if sunday.weekday() != 6:
        sunday = week_end if week_end.weekday() == 6 else week_start + timedelta(days=6)
    return sunday + timedelta(days=3)


def find_target_cod_columns(
    row1: list,
    row2: list,
    target_week_start: date,
    target_week_end: date,
) -> List[Dict]:
    """Zwraca listę [{col_idx, col_letter, segment_start, segment_end, payday}].

    Strategia:
      - Policz datę wypłaty dla pełnego tygodnia (środa po niedzieli).
      - Kandydaci: kolumny gdzie row2[i] ≈ "cod - transport" AND row1[i+2] == payday_str.
      - Segmentów 1 → 1 kandydat; segmentów 2 → 2 kandydaci rozróżnieni po miesiącu pos 4.
    """
    segments = split_week_by_month(target_week_start, target_week_end)
    payday = compute_payday(target_week_start, target_week_end)
    payday_str = payday.strftime("%d-%m-%Y")

    candidates = []
    max_scan = min(len(row1), len(row2))
    start_col = SEARCH_BLOCK_START_COL - 1  # 0-based
    for i in range(start_col, max_scan - 3):
        r2 = row2[i] if i < len(row2) else ""
        if _norm_header(r2) != "cod transport":
            continue
        r1_payday = (row1[i + 2] if i + 2 < len(row1) else "").strip()
        if r1_payday != payday_str:
            continue
        candidates.append(i)

    log.info(
        f"find_target: payday={payday_str}, segments={len(segments)}, "
        f"candidates={[col_idx_to_letter(c) for c in candidates]}"
    )

    if not candidates:
        raise NoTargetColumnError(
            f"Brak bloku z payday={payday_str} w row1 pos 3. "
            f"Dodaj ręcznie w arkuszu datę wypłaty."
        )

    if len(segments) == 1:
        if len(candidates) > 1:
            raise AmbiguousTargetError(
                f"{len(candidates)} kandydatów dla single-segment tygodnia: "
                f"{[col_idx_to_letter(c) for c in candidates]}"
            )
        seg = segments[0]
        ci = candidates[0]
        return [
            {
                "col_idx": ci,
                "col_letter": col_idx_to_letter(ci),
                "segment_start": seg["start"],
                "segment_end": seg["end"],
                "payday": payday,
            }
        ]

    # 2 segmenty — rozbity tydzień
    if len(candidates) != 2:
        raise AmbiguousTargetError(
            f"Oczekiwano 2 kandydatów dla rozbitego tygodnia, "
            f"znaleziono {len(candidates)}"
        )
    by_month = {}
    for ci in candidates:
        pos4 = row1[ci + 3] if ci + 3 < len(row1) else ""
        m = RANGE_RE.match(pos4)
        if not m:
            raise ValueError(
                f"Nie mogę sparsować zakresu w pos 4 kol {col_idx_to_letter(ci + 3)}: "
                f"{pos4!r}. Uzupełnij format 'DD-DD.MM.YYYY'."
            )
        month = int(m.group(3))
        if month in by_month:
            raise AmbiguousTargetError(
                f"2 bloki dla miesiąca {month}: {col_idx_to_letter(by_month[month])} "
                f"i {col_idx_to_letter(ci)}"
            )
        by_month[month] = ci

    result = []
    for seg in segments:
        ci = by_month.get(seg["start"].month)
        if ci is None:
            raise NoTargetColumnError(
                f"Brak kandydata dla miesiąca {seg['start'].month} (segment {seg['start']}..{seg['end']})"
            )
        result.append(
            {
                "col_idx": ci,
                "col_letter": col_idx_to_letter(ci),
                "segment_start": seg["start"],
                "segment_end": seg["end"],
                "payday": payday,
            }
        )
    return result


def _open_worksheet():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=SCOPES_RW)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)


def fetch_sheet_grid() -> Dict:
    """Zwraca {ws, row1, row2, restaurants: [(row_idx_1based, name)]}."""
    ws = _open_worksheet()
    batch = ws.batch_get(["1:2", "A:A"])
    rows_12 = batch[0] if len(batch) > 0 else []
    row1 = rows_12[0] if len(rows_12) > 0 else []
    row2 = rows_12[1] if len(rows_12) > 1 else []
    col_a_raw = batch[1] if len(batch) > 1 else []
    restaurants = []
    for idx0, row in enumerate(col_a_raw):
        row_idx = idx0 + 1
        if row_idx < ROW_START:
            continue
        val = (row[0] if row else "").strip()
        if not val:
            continue
        if val.startswith(SHEET_SKIP_PREFIXES):
            continue
        restaurants.append((row_idx, val))
    return {"ws": ws, "row1": row1, "row2": row2, "restaurants": restaurants}


def write_cod_column_skip_filled(
    ws,
    col_letter: str,
    row_to_value: Dict[int, float],
    dry_run: bool = False,
) -> Dict:
    """Batch write float do col_letter. SKIP wiersze które już mają niepustą wartość.

    row_to_value: {row_1based: cod_float}
    Returns: {written_rows, skipped_filled, skipped_errors}
    Wykonuje MAX 2 API calls (1× batch_get, 1× batch_update).
    """
    if not row_to_value:
        return {"written_rows": [], "skipped_filled": [], "skipped_errors": []}
    rows_sorted = sorted(row_to_value.keys())
    lo = rows_sorted[0]
    hi = rows_sorted[-1]
    current = ws.batch_get([f"{col_letter}{lo}:{col_letter}{hi}"])
    rows = current[0] if current else []
    skipped_filled = []
    updates = []
    for row in rows_sorted:
        idx0 = row - lo
        existing = ""
        if idx0 < len(rows):
            cell = rows[idx0]
            existing = (cell[0] if cell else "").strip()
        if existing:
            skipped_filled.append({"row": row, "existing": existing})
            continue
        updates.append(
            {"range": f"{col_letter}{row}", "values": [[row_to_value[row]]]}
        )
    if updates and not dry_run:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    return {
        "dry_run": dry_run,
        "written_rows": [(u["range"], u["values"][0][0]) for u in updates],
        "skipped_filled": skipped_filled,
        "skipped_errors": [],
    }


def validate_column_empty_ratio(
    ws,
    col_letter: str,
    row_indices: List[int],
    threshold: float = 0.8,
) -> Dict:
    """Sprawdza że kolumna col_letter jest pusta dla ≥threshold% wierszy restauracji."""
    if not row_indices:
        return {"ok": True, "empty_count": 0, "total": 0, "ratio": 1.0, "filled_sample": []}
    lo = min(row_indices)
    hi = max(row_indices)
    range_str = f"{col_letter}{lo}:{col_letter}{hi}"
    vals_raw = ws.batch_get([range_str])
    vals = vals_raw[0] if vals_raw else []
    row_set = set(row_indices)
    empty_count = 0
    filled_sample = []
    for idx0, row in enumerate(vals):
        row_idx = lo + idx0
        if row_idx not in row_set:
            continue
        v = (row[0] if row else "").strip()
        if v == "":
            empty_count += 1
        else:
            if len(filled_sample) < 5:
                filled_sample.append((row_idx, v))
    # Wiersze poza zakresem vals — traktujemy jako puste (brak danych = pusta komórka)
    covered = 0
    for idx0, row in enumerate(vals):
        row_idx = lo + idx0
        if row_idx in row_set:
            covered += 1
    uncovered = len(row_set) - covered
    empty_count += uncovered
    total = len(row_indices)
    ratio = empty_count / total if total > 0 else 1.0
    return {
        "ok": ratio >= threshold,
        "empty_count": empty_count,
        "total": total,
        "ratio": ratio,
        "filled_sample": filled_sample,
    }
