#!/usr/bin/env bash
# P-BRANCHGC (Sprint 2.5-PREP tmux 18, 05.07) — kasacja 40 zmergowanych gałęzi z WD-14.
# ⛔ URUCHAMIAĆ WYŁĄCZNIE PO ACK ADRIANA. Zweryfikowane 05.07 ~18:15 UTC:
#   - wszystkie 40 nadal `git branch --merged master` (pełna zawartość w masterze),
#   - `git branch -d` (małe -d) odmówi przy niezmergowanej — dodatkowy bezpiecznik,
#   - ŚWIADOMIE POZA LISTĄ: fix/route-order-golden-sprint0 (żywy worktree tmux 15,
#     wt-routeorder), master, audyt/*.
# Rollback: kasacja gałęzi nie kasuje commitów — odtworzenie przez reflog/`git branch NAZWA SHA`
# (SHA wypisywane niżej przed kasacją do eod_drafts/2026-07-05/branch_gc_wd14_sha_backup.txt).
set -euo pipefail
cd /root/.openclaw/workspace/scripts/dispatch_v2

BRANCHES=(
  auton/geo-districts
  auton/gps02-accuracy-shadow
  auton/legacy-test-fixes
  auton/scale-01-caps-flags
  auton/tier-gt-regen
  auton/ziomek-hygiene
  fix/alerty-danowe
  fix/auton-blockers
  fix/bug4-logger
  fix/bug4-oracle
  fix/cod-weekly-diag
  fix/cron-health
  fix/d3-fala-ab
  fix/fingerprint-guard
  fix/frozen-objektyw
  fix/gc-observability
  fix/grafik-h
  fix/guard-teatr
  fix/l01-registry
  fix/l3-plan-recheck
  fix/l4-available-from
  fix/l71-r-declared-tripwire
  fix/l73-split-layer-guard
  fix/l74-feascarry-join
  fix/l78-verdict-direction
  fix/l8-iter1
  fix/l8-iter2
  fix/l8-iter3
  fix/l8-mapa
  fix/multicity
  fix/o2-capz
  fix/o2-odczyt
  fix/pending-fcntl
  fix/perf-lazy
  fix/perf-slo
  fix/sla-anchor
  fix/telegram-delta
  fix/tz-consolidate
  fix/tz-drobnica
  fix/watchdog-close
)

BACKUP=eod_drafts/2026-07-05/branch_gc_wd14_sha_backup.txt
echo "# SHA backup przed kasacją $(date -u +%FT%TZ)" > "$BACKUP"

# Pre-flight: każda gałąź musi istnieć, być zmergowana i NIE być wypięta w worktree.
for b in "${BRANCHES[@]}"; do
  git rev-parse --verify -q "refs/heads/$b" >/dev/null || { echo "⛔ brak gałęzi: $b — STOP"; exit 1; }
  git merge-base --is-ancestor "$b" master || { echo "⛔ NIEZMERGOWANA: $b — STOP"; exit 1; }
  if git worktree list --porcelain | grep -qx "branch refs/heads/$b"; then
    echo "⛔ gałąź wypięta w worktree: $b — STOP"; exit 1
  fi
  echo "$(git rev-parse "$b")  $b" >> "$BACKUP"
done
echo "Pre-flight OK (40/40 zmergowane, żadna w worktree). SHA backup: $BACKUP"

for b in "${BRANCHES[@]}"; do
  git branch -d "$b"
done
echo "Skasowano ${#BRANCHES[@]} gałęzi. Pozostałe:"
git branch
