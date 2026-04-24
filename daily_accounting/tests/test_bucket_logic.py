"""Tests — bucket_logic (Step 2).

Cases planowane:
 - test_monday_weekend_bucket — 27.04 Mon → from=24.04 Fri, to=26.04 Sun, C=26.04
 - test_tuesday_regular       — 28.04 Tue → from=27.04, to=27.04, C=27.04
 - test_saturday_exits        — 25.04 Sat → exit(0)
 - test_sunday_exits          — 26.04 Sun → exit(0)
"""
