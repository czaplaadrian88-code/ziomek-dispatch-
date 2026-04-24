"""Tests — car_lookup (Step 2).

Cases planowane:
 - test_last_match_wins             — 2 wpisy: row100 Firmowe, row400 Własne → Własne
 - test_no_match_default_firmowe    — nowy kurier → Firmowe
 - test_case_insensitive            — 'ADRIAN CITKO' vs 'Adrian Citko' → match
 - test_empty_B_default_firmowe     — match w A ale B puste → Firmowe
"""
