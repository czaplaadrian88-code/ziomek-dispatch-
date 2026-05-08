"""Core utilities for dispatch_v2.

Master plan TOP-15 #6 (audit 2026-05-07 wieczór): dedicated namespace dla
shared utility modules (atomic I/O, locking, etc.). Pierwszy resident:
flags_io — atomic flags.json write helper.

Future: jsonl_appender, state_io, lock_helpers per master plan #11/#13.
"""
