"""Testy ensure_grid_capacity — auto-grow gridu arkusza.

Regresja dla awarii 2026-06-12: arkusz 'Obliczenia' miał 995 wierszy, batch write
od A991 wywalał caly run (APIError 400 'exceeds grid limits'). Fix rozszerza grid
PRZED zapisem z zapasem. FakeWs symuluje gspread Worksheet bez sieci.
"""
from dispatch_v2.daily_accounting.sheets_writer import ensure_grid_capacity


class _FakeWs:
    def __init__(self, row_count):
        self._row_count = row_count
        self.add_rows_calls = []

    @property
    def row_count(self):
        return self._row_count

    def add_rows(self, n):
        self.add_rows_calls.append(n)
        self._row_count += n


def test_no_grow_when_enough_room():
    ws = _FakeWs(1000)
    added = ensure_grid_capacity(ws, max_target_row=995)
    assert added == 0
    assert ws.add_rows_calls == []
    assert ws.row_count == 1000


def test_no_grow_at_exact_boundary():
    ws = _FakeWs(995)
    added = ensure_grid_capacity(ws, max_target_row=995)
    assert added == 0
    assert ws.row_count == 995


def test_grow_with_buffer():
    ws = _FakeWs(995)
    # potrzeba 1007-995=12 + 500 zapasu = 512 (reprodukcja realnego A1007 case)
    added = ensure_grid_capacity(ws, max_target_row=1007, buffer=500)
    assert added == 512
    assert ws.row_count == 1507
    assert ws.add_rows_calls == [512]


def test_grow_makes_target_fit():
    ws = _FakeWs(995)
    ensure_grid_capacity(ws, max_target_row=1007, buffer=500)
    assert ws.row_count >= 1007


def test_custom_buffer_zero():
    ws = _FakeWs(995)
    added = ensure_grid_capacity(ws, max_target_row=1000, buffer=0)
    assert added == 5
    assert ws.row_count == 1000


def test_empty_rows_default_zero_target():
    # batch_write_rows liczy max(..., default=0) dla pustej listy → target 0, brak grow
    ws = _FakeWs(995)
    added = ensure_grid_capacity(ws, max_target_row=0)
    assert added == 0
    assert ws.row_count == 995
