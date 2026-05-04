"""Observability Layer — TASK 3 (2026-05-04).

Per-candidate structured logging dla dispatch decisions. Bez tego każda
diagnoza = ad-hoc rerun (Lekcja z TASK 1 phantom investigation).

Integracja:
  czasowka_scheduler.eval_czasowka — per-candidate verdicts dla T-50/T-40 retries
  dispatch_pipeline.assess_order   — per-candidate verdicts dla NEW_ORDER flow
  courier_resolver.dispatchable_fleet — fleet filter decisions

Architecture (Z3):
  Centralized CandidateLogger — single source of truth dla format
  Flag-gated default false (zero overhead w produkcji jeśli disabled)
  Performance: <5ms per decision (atomic JSONL append, no async overhead)
  Defensive: every log call try/except — never crashes dispatch flow
  Retention: log rotation per RETENTION_DAYS flag (default 14d)

Files:
  candidate_logger.py — CandidateLogger class + helper functions
  log_rotation.py     — daily rotation utility (cron-safe)
"""
__version__ = "1.0.0"
