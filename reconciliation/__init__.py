"""Reconciliation Service — TASK 2 Część B (2026-05-04).

Strukturalna infrastruktura która gwarantuje że events.db nigdy nie rozjedzie się
z orders_state.json niezauważenie. Defense in depth — nawet jeśli Część A źródłowy
fix coś przepuści, reconciliation worker wykryje rozjazd i zresynkuje.

Architektura (Z3):
  Data source: orders_state.json (panel_watcher already-synced) + events.db
  Rationale: NIE używamy fresh panel_client API call — ryzyko CSRF collision
  z dispatch-panel-watcher (memory: feedback_panel_session_singleton).
  panel_watcher już syncs orders_state, więc to safe proxy panel reality.

  Worker frequency: cron 30 min (RECONCILIATION_INTERVAL_MIN configurable)
  Auto-resync gates: age >4h auto, <4h alert; hard_cap 5/run (configurable)
  Default flags: ALL FALSE (Adrian explicit włącza po smoke test)

Discrepancy types:
  PHANTOM (events.db active, state terminal/missing) → auto-resync if old
  GHOST   (events.db terminal, state active)         → alert only, never auto

Modules:
  phantom_detector — pure detection logic (zero side effects, testable)
  auto_resync       — safety-gated emit terminal events
  reconcile_log     — structured JSONL append + fsync
  reconcile_worker  — main entry orchestrator (CLI for systemd)
  health_endpoint   — /health/reconcile route helper
"""
__version__ = "1.0.0"
