#!/bin/bash
# AIDER deepseek-coder commands dla sprint Pn 06.05 — F2/F3/F4/F8 fixes
# Per CLAUDE.md routing rules: code generation > 30 LOC = AIDER (deepseek-coder).
# Per-step ACK gates: każdy AIDER session pokazuje diff → Adrian ACK → commit.
#
# Pre-flight:
#   - Branch: sprint-06-05-debug (isolated, per Unknown #1 REC B)
#   - Venv: source /root/.openclaw/venvs/dispatch/bin/activate
#   - DEEPSEEK_API_KEY musi być w env
#
# Sequencing (low risk → high risk):
#   1. F8 test isolation (zero prod risk, testy)
#   2. F2 geocoding hardcode (~30 LOC, low risk)
#   3. F3 TASK A WAIT branch (~20-30 LOC, scheduler config)
#   4. F4 LGBM signature (~150 LOC, biggest blast radius)

set -e
cd /root/.openclaw/workspace/scripts/dispatch_v2

# ============================================================
# F8 — test_shift_* test 7 STATE_FILE corruption (Lekcja #71 apply)
# ============================================================
echo "=== F8: test isolation ==="
aider \
  --model deepseek/deepseek-coder \
  --no-auto-commits \
  tests/test_shift_*.py \
  shift_notifications/*.py
# Prompt:
# Apply Lekcja #71 isolated_shift_state() ctx mgr do test 7 (test_shift_*.py).
# Wzór: identyfikuj który test brudzi prod-shaped STATE_FILE residual; wrap setup
# z `with isolated_shift_state(): ...` lub fixture. Backup file przed edit.
# Per-step: pokaż diff → ACK → uruchom pytest tests/test_shift_*.py → +1 regression test
# (test 7 nie zostawia residual w STATE_FILE po teardown). Zachowaj 95/95 V3.28 baseline.

# ============================================================
# F2 — Geocoding hardcode "Białystok" removal
# ============================================================
echo "=== F2: geocoding hardcode ==="
aider \
  --model deepseek/deepseek-coder \
  --no-auto-commits \
  dispatch_pipeline.py \
  geocoding.py \
  tests/test_geocoding*.py
# Prompt:
# Usuń hardcoded "Białystok" fallback w dispatch_pipeline.py:317 i :421.
# Zamiast hardcode, derive city z order data: raw.lokalizacja.name (top-level field
# w panel response) lub raw.miasto fallback. Jeśli oba brak — log WARNING + fallback
# do current default zamiast crash. Cel Z3: multi-tenant ready (Warsaw, future cities).
# Per-step: pokaż diff → ACK → +2 unit tests (city z lokalizacja.name + missing fallback).
# Backup files przed edit. Mantain 95/95 baseline.

# ============================================================
# F3 — TASK A czasowka_scheduler WAIT branch structural fix
# ============================================================
echo "=== F3: TASK A WAIT branch fix ==="
aider \
  --model deepseek/deepseek-coder \
  --no-auto-commits \
  czasowka_scheduler.py \
  czasowka_proactive/evaluator.py \
  tests/test_czasowka_proactive_evaluator.py \
  tests/test_v324b_czasowka_scheduler*.py
# Prompt:
# RC: czasowka_scheduler.py:310-318 (WAIT branch 40<mins≤60 gdy best_maybe=False)
# nullifikuje "best": None i "alternatives": [] mimo że result.candidates ma 46 entries.
# czasowka_proactive.maybe_fire_trigger dostaje pusto → 100% NO_CANDIDATE.
#
# Fix:
# 1. W czasowka_scheduler.py:310-318 (i analogous WAIT branches linie 246-260, 316-318
#    gdy są sister) zmienić nullification:
#    "best": best,
#    "alternatives": result.candidates[1:] if result.candidates else [],
#    + NEW field "all_candidates_for_proactive": list(result.candidates) if result.candidates else [],
# 2. W czasowka_proactive/evaluator.py:_filter_candidates czytać
#    eval_result.get("all_candidates_for_proactive") jako primary source, fallback do
#    eval_result.get("best") + eval_result.get("alternatives") (legacy).
# 3. Per Lekcja #72 granular flag: NEW flag CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES default False
#    żeby fix LIVE jako gradual flip.
#
# Per-step ACK gates. +3 unit tests:
#   - WAIT branch zachowuje candidates (NIE None)
#   - czasowka_proactive widzi candidates gdy main scheduler logger widzi
#   - flag=False legacy behavior preserved
#
# Backup files przed edit. Mantain 95/95 baseline.

# ============================================================
# F4 — LGBM Candidate signature fix (largest, ~150 LOC)
# ============================================================
echo "=== F4: LGBM signature fix ==="
aider \
  --model deepseek/deepseek-coder \
  --no-auto-commits \
  ml_inference.py \
  dispatch_pipeline.py \
  bag_state.py \
  tests/test_lgbm_shadow.py \
  tests/test_ml_inference*.py
# Prompt:
# RC: Candidate dataclass (dispatch_pipeline.py:857-865) NIE ma fields które LGBM
# oczekuje: bag_size, tier_bag, last_pos_lat, last_pos_lon, idle_min, level,
# bag_drops_pending, bag_pickup_pending, orders_today_before_T0, bag_n_distinct_districts,
# bag_has_distant_drop. ml_inference.py używa 12+ getattr(c, ...) → wszystko zero
# → all_bag_zero=True ZAWSZE → 100% fallback (502/502 emisji 02-05.05).
#
# Fix Opt 1 (REC, Z3 najczystszy):
# 1. Refaktor predict_for_decision signature:
#    OLD: predict_for_decision(decision_ctx, candidates: List[Candidate])
#    NEW: predict_for_decision(decision_ctx, courier_states: List[CourierBagState],
#                              cid_to_candidate: Dict[str, Candidate])
# 2. Source-of-truth: bag_state.py CourierBagState ma wszystkie real fields.
# 3. Compute features z courier_states; agreement_with_primary z cid_to_candidate map.
# 4. dispatch_pipeline.py:2473 call site update: pass list[CourierBagState] z
#    fleet_snapshot transform + cid->Candidate map z feasible list.
# 5. Per Lekcja #72 granular flag: NEW flag ENABLE_LGBM_PRIMARY_FIX_OPT1 default False,
#    feature toggle żeby legacy path zostawał przy False (rollback path).
#
# Per-step ACK gates każdy z:
#   - Krok 1: signature change + adapter (5min)
#   - Krok 2: feature compute z CourierBagState (15min)
#   - Krok 3: call site update dispatch_pipeline.py:2473 (10min)
#   - Krok 4: testy unit (20min)
#   - Krok 5: shadow validation 24h (post-deploy)
#
# +6 unit tests:
#   - all_bag_zero pomiar correct dla CourierBagState bag_size>=1
#   - feature compute zwraca non-zero dla real CourierBagState
#   - agreement_with_primary computable
#   - flag=False legacy path preserved
#   - fallback "courier_state_missing" jeśli cid w map ale brak state
#   - smoke 31ms latency target zachowany
#
# Backup files. Maintain 95/95 baseline. Cross-ref Lekcja #57 (training-prod parity).
# Cel: post-deploy shadow obs 1-week, fallback rate <50% (z 100%), Faza 7 re-baseline 15.05.

echo "=== AIDER sprint sequence READY. Adrian: review per-step diffs przed commit. ==="
