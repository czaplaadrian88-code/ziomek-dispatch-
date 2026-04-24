"""Daily Accounting — date/bucket logic (Step 2+).

Mon → weekend bucket (Fri..Sun, target_date = Sun).
Tue-Fri → yesterday only (target_date = yesterday).
Sat/Sun → exit(0).
All timestamps via zoneinfo.ZoneInfo('Europe/Warsaw').
"""
