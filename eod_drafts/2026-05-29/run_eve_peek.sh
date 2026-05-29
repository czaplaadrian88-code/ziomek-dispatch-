#!/bin/sh
# Wieczorny peek 29.05 (po piątkowej kolacji peak 17-20 Warsaw) — analiza
# SAME_REST_RACE_PROBE. Osobna etykieta + plik (nie koliduje z at-job #93 sobota).
export RACE_PROBE_LABEL="29.05 wieczór (po piątkowej kolacji)"
export RACE_PROBE_OUT="/root/.openclaw/workspace/scripts/logs/race_probe_analysis_2026-05-29_eve.txt"
exec /root/.openclaw/venvs/dispatch/bin/python3 \
  /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-29/analyze_race_probe.py
