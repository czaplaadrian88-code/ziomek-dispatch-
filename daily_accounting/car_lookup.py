"""Daily Accounting — car lookup from Obliczenia!A:B.

Last-match-wins per employee. Case-insensitive + strip. Empty B → default 'Firmowe'.
Brak matcha w A → default 'Firmowe'.
"""
from typing import List

DEFAULT_CAR = "Firmowe"


def find_car(employee_name: str, col_a: List[str], col_b: List[str]) -> str:
    """Return B value for the LAST row where A matches employee_name (case-insensitive).

    Args:
        employee_name: pełna nazwa z kurier_full_names.json (e.g. 'Adrian Citko')
        col_a: wartości kolumny A (nazwiska)
        col_b: wartości kolumny B (własność auta)

    Returns: B value, albo 'Firmowe' gdy brak matcha / B puste.
    """
    target = employee_name.strip().lower()
    last_idx = None
    for i, name in enumerate(col_a):
        if name and name.strip().lower() == target:
            last_idx = i
    if last_idx is None:
        return DEFAULT_CAR
    b_val = col_b[last_idx] if last_idx < len(col_b) else ""
    b_clean = (b_val or "").strip()
    return b_clean if b_clean else DEFAULT_CAR
