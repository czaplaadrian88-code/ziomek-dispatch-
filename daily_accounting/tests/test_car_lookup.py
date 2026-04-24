"""Tests — car_lookup."""
from dispatch_v2.daily_accounting.car_lookup import find_car, DEFAULT_CAR


def test_last_match_wins():
    # row 100 (idx 99) Firmowe, row 400 (idx 399) Własne
    col_a = ["Adrian Citko" if i in (99, 399) else "" for i in range(500)]
    col_b = [""] * 500
    col_b[99] = "Firmowe"
    col_b[399] = "Własne"
    assert find_car("Adrian Citko", col_a, col_b) == "Własne"


def test_no_match_default_firmowe():
    col_a = ["Jan Kowalski", "Anna Nowak"]
    col_b = ["Własne", "Firmowe"]
    assert find_car("Nowy Kurier", col_a, col_b) == "Firmowe"
    assert find_car("Nowy Kurier", col_a, col_b) == DEFAULT_CAR


def test_case_insensitive():
    col_a = ["adrian citko"]
    col_b = ["Własne"]
    assert find_car("Adrian Citko", col_a, col_b) == "Własne"
    assert find_car("ADRIAN CITKO", col_a, col_b) == "Własne"


def test_empty_B_default_firmowe():
    col_a = ["Adrian Citko"]
    col_b = [""]
    assert find_car("Adrian Citko", col_a, col_b) == DEFAULT_CAR


def test_whitespace_strip():
    col_a = ["  Adrian Citko  "]
    col_b = ["  Własne  "]
    assert find_car("Adrian Citko", col_a, col_b) == "Własne"


def test_b_shorter_than_a_returns_default():
    # Edge: last matching idx exists w col_a ale col_b krótszy (rzadki sheets quirk)
    col_a = ["Adrian Citko"]
    col_b = []
    assert find_car("Adrian Citko", col_a, col_b) == DEFAULT_CAR
