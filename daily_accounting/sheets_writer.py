"""Daily Accounting — Google Sheets R/W na arkusz 'Obliczenia'.

Odpowiedzialności:
 - Read A:C dla idempotent check (existing (name, date) pairs)
 - Read A:B dla car_lookup source data
 - Find first empty row (skan kolumny A)
 - Batch write A/B/C/F/H/P dla nowych wierszy (jeden batchUpdate call)
 - Count free rows pod ostatnim filled (alert threshold)

Auth: service_account.json, SCOPES spreadsheets (RW).
Number format: valueInputOption='USER_ENTERED' + raw float → Sheets lokalizuje PL.
Date format: DD-MM-YYYY z myślnikami (np. '23-04-2026') zgodnie z istniejącymi wpisami.
"""
import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from dispatch_v2.daily_accounting.config import SHEET_NAME, SPREADSHEET_ID

log = logging.getLogger("daily_accounting.sheets")

SERVICE_ACCOUNT_PATH = "/root/.openclaw/workspace/scripts/service_account.json"
SCOPES_RW = ["https://www.googleapis.com/auth/spreadsheets"]

DATE_FMT = "%d-%m-%Y"


def _gc():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=SCOPES_RW)
    return gspread.authorize(creds)


def open_worksheet():
    gc = _gc()
    sh = gc.open_by_key(SPREADSHEET_ID)
    return sh.worksheet(SHEET_NAME)


def fetch_grid() -> Dict:
    """Read columns A, B, C at once; also last-non-empty index for A.

    Returns: {'col_a': [...], 'col_b': [...], 'col_c': [...], 'last_filled': int}
    """
    ws = open_worksheet()
    col_a = ws.col_values(1)  # stops at last non-empty
    col_b = ws.col_values(2)
    col_c = ws.col_values(3)
    last_filled = len(col_a)  # 1-based count = index of last non-empty row
    return {
        "ws": ws,
        "col_a": col_a,
        "col_b": col_b,
        "col_c": col_c,
        "last_filled": last_filled,
    }


def already_written(
    employee_name: str,
    target_date: date,
    col_a: List[str],
    col_c: List[str],
) -> bool:
    """Idempotent check — czy (employee, date) para już istnieje."""
    name_target = employee_name.strip().lower()
    date_target = target_date.strftime(DATE_FMT)
    n = min(len(col_a), len(col_c))
    for i in range(n):
        a = col_a[i]
        c = col_c[i]
        if a and a.strip().lower() == name_target and c == date_target:
            return True
    return False


def first_empty_row(col_a: List[str]) -> int:
    """Return 1-indexed row number for pierwszy wolny wiersz (po ostatnim niepustym w A)."""
    return len(col_a) + 1


def count_free_rows_after(ws, last_filled: int, sample_limit: int = 500) -> int:
    """Policz puste wiersze między last_filled+1 a physical row_count (albo sample limit).

    col_values(1) zwraca wartości tylko do ostatniego niepustego → nie daje info
    ile jest pustych wierszy fizycznie dostępnych. Używamy ws.row_count + last_filled.
    """
    total_rows = ws.row_count
    return max(0, total_rows - last_filled)


def build_batch_data(ws, rows: List[Dict]) -> List[Dict]:
    """Zbuduj listę {'range': "'Obliczenia'!A123", 'values': [[val]]} per komórka.

    Range MUSI mieć prefiks z nazwą zakładki — bez niego Google Sheets API używa
    pierwszej zakładki w spreadsheecie (per docs: "If absent, the title of the
    first sheet is used"), co spowodowało historyczny silent-misroute do 'Pobrania'
    od 28.04.2026 (DIFF C 04.05.2026).
    """
    data = []
    for r in rows:
        row_idx = r["row"]
        for col_letter in ("A", "B", "C", "F", "H", "P"):
            val = r.get(col_letter)
            if val is None:
                continue
            data.append({
                "range": f"'{ws.title}'!{col_letter}{row_idx}",
                "values": [[val]],
            })
    return data


def batch_write_rows(
    ws,
    rows: List[Dict],
) -> Dict:
    """Batch write wszystkich wierszy jednym values.batchUpdate call.

    Args:
        rows: lista dict z kluczami 'row' (1-indexed), 'A', 'B', 'C', 'F', 'H', 'P'.
              Wartości numeryczne jako raw float → USER_ENTERED lokalizuje PL przecinek.

    Returns: {'written': int, 'first_row': int, 'last_row': int,
              'api_success': bool, 'api_total_updated_cells': int,
              'api_expected_cells': int}.
    `written` = len(rows) tylko gdy api_total_updated_cells == api_expected_cells;
    inaczej 0 (DIFF A — uczciwy raport).
    """
    if not rows:
        return {
            "written": 0, "first_row": None, "last_row": None,
            "api_success": True, "api_total_updated_cells": 0,
            "api_expected_cells": 0,
        }

    # Strategia: per-cell updates zebrane w batch_update. Każda komórka osobny range
    # bo piszemy kolumny nieciągłe (A, B, C, F, H, P — G/D/E pomijamy).
    data = build_batch_data(ws, rows)
    expected_cells = len(data)
    log.info(
        f"batch_write: {len(rows)} rows, {expected_cells} cells, "
        f"sheet={ws.title!r}, sample range={data[0]['range']!r}"
    )

    # batch_update z value_input_option USER_ENTERED (Sheets lokalizuje liczby PL)
    resp = ws.spreadsheet.values_batch_update(body={
        "valueInputOption": "USER_ENTERED",
        "data": data,
    })

    total_updated = int(resp.get("totalUpdatedCells", 0))
    api_success = (total_updated == expected_cells)

    return {
        "written": len(rows) if api_success else 0,
        "first_row": rows[0]["row"],
        "last_row": rows[-1]["row"],
        "api_success": api_success,
        "api_total_updated_cells": total_updated,
        "api_expected_cells": expected_cells,
    }


def verify_writes(ws, rows: List[Dict]) -> Dict:
    """Re-fetch col A i C; weryfikuj że każdy expected row ma expected wartości
    A (full_name) i C (target_date).

    Defense-in-depth (DIFF B): nawet jeśli API zwróci api_success=True,
    weryfikujemy fizyczną obecność danych w docelowej zakładce. Łapie cases
    gdzie API zaakceptowało write ale fizycznie poszło nie tam (np. wrong
    target sheet, jak DIFF C bug).

    Returns: {'verified': N, 'mismatches': [...]}
    """
    col_a = ws.col_values(1)
    col_c = ws.col_values(3)
    mismatches = []
    for r in rows:
        i = r["row"] - 1  # 0-based
        actual_a = (col_a[i] if i < len(col_a) else "").strip()
        actual_c = (col_c[i] if i < len(col_c) else "").strip()
        expected_a = (r.get("A") or "").strip()
        expected_c = (r.get("C") or "").strip()
        if actual_a != expected_a or actual_c != expected_c:
            mismatches.append({
                "row": r["row"],
                "expected_A": expected_a, "actual_A": actual_a,
                "expected_C": expected_c, "actual_C": actual_c,
            })
    return {"verified": len(rows) - len(mismatches), "mismatches": mismatches}
