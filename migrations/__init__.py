"""dispatch_v2 migrations package — one-shot migration scripts.

Each script is named with a date suffix (e.g. migrate_couriers_2026-05-05.py)
and is intended to run exactly once. Operations are atomic per record, with
fcntl.LOCK_EX + tempfile + fsync + rename per Lekcja #71.
"""
