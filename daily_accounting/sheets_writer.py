"""Daily Accounting — Google Sheets R/W na arkusz 'Obliczenia'.

Odpowiedzialności:
 - Read A/C/H/P/S dla rekonsyliacji istniejącego wiersza
 - Read A:B dla car_lookup source data
 - Find first empty row (skan kolumny A)
 - Batch write A/C/H/P/S dla nowych wierszy (jeden batchUpdate call)
 - Count free rows pod ostatnim filled (alert threshold)

Auth: service_account.json, SCOPES spreadsheets (RW).
Number format: valueInputOption='USER_ENTERED' + raw float → Sheets lokalizuje PL.
Date format: DD-MM-YYYY z myślnikami (np. '23-04-2026') zgodnie z istniejącymi wpisami.
"""
import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from dispatch_v2.daily_accounting.config import SHEET_NAME, SPREADSHEET_ID
from dispatch_v2.daily_accounting.numbers import parse_zl

log = logging.getLogger("daily_accounting.sheets")

SERVICE_ACCOUNT_PATH = "/root/.openclaw/workspace/scripts/service_account.json"
SCOPES_RW = ["https://www.googleapis.com/auth/spreadsheets"]

DATE_FMT = "%d-%m-%Y"
SETTLEMENT_KEY_COLUMN = "S"
SETTLEMENT_KEY_PREFIX = "daily-accounting/v1"


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
    """Read columns needed for reconciliation; last-non-empty index comes from A.

    ``S`` is the machine key; legacy rows can have it empty. We intentionally
    read H/P too: name+date alone is not proof that a settlement is correct.
    """
    ws = open_worksheet()
    col_a = ws.col_values(1)  # stops at last non-empty
    col_b = ws.col_values(2)
    col_c = ws.col_values(3)
    col_h = ws.col_values(8)
    col_p = ws.col_values(16)
    col_s = ws.col_values(19)
    last_filled = len(col_a)  # 1-based count = index of last non-empty row
    return {
        "ws": ws,
        "col_a": col_a,
        "col_b": col_b,
        "col_c": col_c,
        "col_h": col_h,
        "col_p": col_p,
        "col_s": col_s,
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


def settlement_key(cid: int, date_from: date, date_to: date) -> str:
    """Stable source key, independent of alias or display-name changes."""
    return f"{SETTLEMENT_KEY_PREFIX}:{int(cid)}:{date_from.isoformat()}:{date_to.isoformat()}"


def _at(column: List[str], index: int) -> str:
    return str(column[index]) if index < len(column) else ""


def _same_number(actual: object, expected: object) -> bool:
    try:
        return abs(parse_zl(actual) - float(expected)) < 0.005
    except (TypeError, ValueError):
        return False


def reconcile_existing_row(
    *,
    key: str,
    employee_name: str,
    legacy_names: Optional[List[str]] = None,
    target_date: date,
    expected_h: float,
    expected_p: int,
    col_a: List[str],
    col_c: List[str],
    col_h: List[str],
    col_p: List[str],
    col_s: List[str],
) -> Dict:
    """Classify an existing settlement without silently accepting a mismatch.

    A keyed row is machine-owned. A legacy row is recognised only as a single
    matching ``name+date`` candidate and never becomes a successful duplicate
    when H or P differ. Ambiguity is deliberately a HOLD.
    """
    keyed_rows = [i for i, value in enumerate(col_s) if value.strip() == key]
    if len(keyed_rows) > 1:
        return {"status": "KEY_AMBIGUOUS", "rows": [i + 1 for i in keyed_rows]}

    name_targets = {employee_name.strip().casefold()}
    name_targets.update(
        str(name).strip().casefold() for name in (legacy_names or []) if str(name).strip()
    )
    date_target = target_date.strftime(DATE_FMT)
    legacy_rows = [
        i
        for i in range(min(len(col_a), len(col_c)))
        if _at(col_a, i).strip().casefold() in name_targets
        and _at(col_c, i).strip() == date_target
    ]

    if keyed_rows:
        row = keyed_rows[0]
        values_match = _same_number(_at(col_h, row), expected_h) and _same_number(
            _at(col_p, row), expected_p
        )
        return {
            "status": "MACHINE_MATCH" if values_match else "MACHINE_MISMATCH",
            "row": row + 1,
        }
    if len(legacy_rows) > 1:
        return {"status": "LEGACY_AMBIGUOUS", "rows": [i + 1 for i in legacy_rows]}
    if legacy_rows:
        row = legacy_rows[0]
        values_match = _same_number(_at(col_h, row), expected_h) and _same_number(
            _at(col_p, row), expected_p
        )
        return {
            "status": "LEGACY_MATCH" if values_match else "LEGACY_MISMATCH",
            "row": row + 1,
        }
    return {"status": "NEW"}


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


def ensure_grid_capacity(ws, max_target_row: int, buffer: int = 500) -> int:
    """Zapewnij że worksheet ma >= max_target_row wierszy (auto-grow z zapasem).

    Google Sheets ma twardy grid limit (gridProperties.rowCount). Batch write poza
    ten limit → APIError 400 'Range (...) exceeds grid limits' (root cause awarii
    2026-06-12: arkusz 'Obliczenia' miał 995 wierszy, write od A991 wywalał caly run).
    Rozszerzamy PRZED zapisem z zapasem (`buffer`), żeby kolejne dni miały miejsce
    i nie wołać API co wiersz.

    Args:
        max_target_row: najwyższy 1-indexed wiersz który zostanie zapisany.
        buffer: ile dodatkowych pustych wierszy dorzucić ponad potrzebę.

    Returns: liczba dodanych wierszy (0 = już dość miejsca).
    """
    current = ws.row_count
    if max_target_row <= current:
        return 0
    needed = (max_target_row - current) + buffer
    ws.add_rows(needed)
    log.info(
        f"grid auto-grow: row_count {current} -> {ws.row_count} "
        f"(+{needed}; target row {max_target_row})"
    )
    return needed


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
        # S = techniczny klucz źródłowy. B/F pozostają poza zakresem od 2026-05-14.
        for col_letter in ("A", "C", "H", "P", SETTLEMENT_KEY_COLUMN):
            val = r.get(col_letter)
            if val is None:
                continue
            data.append({
                "range": f"'{ws.title}'!{col_letter}{row_idx}",
                "values": [[val]],
            })
    return data


def snapshot_written_cells(rows: List[Dict], columns: Dict[str, List[str]]) -> List[Dict]:
    """Capture the in-memory preimage for exactly the cells a batch may change.

    This is deliberately not persisted: settlement values are operational data.
    The caller can use the returned rows for an immediate compensating write if
    the API response or read-back verification fails.
    """
    snapshots: List[Dict] = []
    for row in rows:
        row_index = int(row["row"]) - 1
        snapshot = {"row": row["row"]}
        for field in ("A", "C", "H", "P", SETTLEMENT_KEY_COLUMN):
            if field in row:
                snapshot[field] = _at(columns[field], row_index)
        snapshots.append(snapshot)
    return snapshots


def batch_write_rows(
    ws,
    rows: List[Dict],
) -> Dict:
    """Batch write wszystkich wierszy jednym values.batchUpdate call.

    Args:
        rows: lista dict z kluczami 'row' (1-indexed), 'A', 'C', 'H', 'P', 'S'.
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

    # Strategia: per-cell updates zebrane w batch_update dla nieciągłych A/C/H/P/S.
    data = build_batch_data(ws, rows)
    expected_cells = len(data)
    log.info(
        f"batch_write: {len(rows)} rows, {expected_cells} cells, "
        f"sheet={ws.title!r}, sample range={data[0]['range']!r}"
    )

    # Self-healing: zapewnij dość wierszy PRZED zapisem (eliminuje APIError 400
    # 'exceeds grid limits' niezależnie od wywołującego).
    max_target_row = max((r["row"] for r in rows), default=0)
    ensure_grid_capacity(ws, max_target_row)

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
    """Re-read every written field: identity, amounts and machine key.

    A/C-only verification allowed an API or formula mismatch in H/P to be
    reported as success. Fields absent from a patch row are deliberately not
    compared, which lets explicit legacy reconciliation change only H/P/S.
    """
    columns = {
        "A": ws.col_values(1),
        "C": ws.col_values(3),
        "H": ws.col_values(8),
        "P": ws.col_values(16),
        SETTLEMENT_KEY_COLUMN: ws.col_values(19),
    }
    mismatches = []
    for r in rows:
        i = r["row"] - 1  # 0-based
        actual = {field: _at(column, i).strip() for field, column in columns.items()}
        failed = []
        for field in ("A", "C", "H", "P", SETTLEMENT_KEY_COLUMN):
            if field not in r or r[field] is None:
                continue
            expected = r[field]
            matches = (
                _same_number(actual[field], expected)
                if field in ("H", "P")
                else actual[field] == str(expected).strip()
            )
            if not matches:
                failed.append(field)
        if failed:
            mismatches.append({
                "row": r["row"],
                "fields": failed,
                "expected": {field: r[field] for field in failed},
                "actual": {field: actual[field] for field in failed},
            })
    return {"verified": len(rows) - len(mismatches), "mismatches": mismatches}
