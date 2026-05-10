#!/bin/bash
# EOD Integration Commands — 2026-05-05 sprint close
# Usage: bash eod_drafts/2026-05-05/integration_commands.sh
# Pre-conditions:
#   - Adrian ACK Lekcja #72 + #73 promotion z candidate -> final
#   - Branch sprint-05-05-tb-phase2-task-a checked out
#   - Adrian completed eod_checklist.md review (5 manual decisions)
#
# Idempotent: rerun safe (cp overwrites, git add -A na konkretnych plikach,
# git commit fail -> diagnose; tag fail -> already exists check).

set -e
cd /root/.openclaw/workspace/scripts/dispatch_v2

EOD=eod_drafts/2026-05-05
MEMORY_DIR=/root/.claude/projects/-root/memory

# ------------------------------------------------------------------
# 0. Pre-flight: weryfikacja drafts existence
# ------------------------------------------------------------------
echo "=== EOD Integration 2026-05-05 — pre-flight check ==="
for f in daily_report_draft.md \
         memory_project_sprint_05_05.md \
         memory_pointer_line.md \
         claude_md_taskd_taske_wytyczna.md \
         lekcja_72_candidate.md \
         lekcja_73_robust_median_pattern.md \
         geocoding_adjacency_draft_2026-05-06.md \
         branch_merge_plan.md; do
    if [ ! -f "$EOD/$f" ]; then
        echo "MISSING: $EOD/$f — abort"
        exit 1
    fi
done
echo "All required drafts present."

# ------------------------------------------------------------------
# 1. MEMORY pointer line — manual edit (ostrożnie, prepend linia 1)
# ------------------------------------------------------------------
echo ""
echo "MANUAL STEP 1: dodaj linię 1 z $EOD/memory_pointer_line.md"
echo "              do $MEMORY_DIR/MEMORY.md (prepend, NIE overwrite)"
echo "              Format jak existing entries (- [Title](file.md) — desc)"
read -p "Po wykonaniu naciśnij ENTER aby kontynuować..."

# ------------------------------------------------------------------
# 2. Project memory entry
# ------------------------------------------------------------------
cp "$EOD/memory_project_sprint_05_05.md" \
   "$MEMORY_DIR/project_sprint_05_05_2026-05-05.md"
echo "Copied project memory entry."

# ------------------------------------------------------------------
# 3. Lekcja #72 (post Adrian ACK)
# ------------------------------------------------------------------
cp "$EOD/lekcja_72_candidate.md" \
   "$MEMORY_DIR/lekcja_72_2026-05-05.md"
echo "Copied Lekcja #72."

# ------------------------------------------------------------------
# 4. Lekcja #73 (post Adrian ACK, jeśli agent #1 produced)
# ------------------------------------------------------------------
if [ -f "$EOD/lekcja_73_robust_median_pattern.md" ]; then
    cp "$EOD/lekcja_73_robust_median_pattern.md" \
       "$MEMORY_DIR/lekcja_73_2026-05-05.md"
    echo "Copied Lekcja #73."
else
    echo "SKIP: Lekcja #73 draft missing (agent #1 nie produced)"
fi

# ------------------------------------------------------------------
# 5. CLAUDE.md additions (manual review zalecane przed append)
# ------------------------------------------------------------------
echo ""
echo "MANUAL STEP 2: review $EOD/claude_md_taskd_taske_wytyczna.md"
echo "              append 3 sekcje (TASK D + TASK E + Wytyczna #1)"
echo "              do dispatch_v2/CLAUDE.md w sekcji 'Roadmap'"
echo "              (lub przed legacy 'V3.27.6 sprint summary' jako"
echo "              nowy header '## Sprint 2026-05-05 close — TASK B Phase 2 + TASK A')"
read -p "Po wykonaniu naciśnij ENTER aby kontynuować..."

# ------------------------------------------------------------------
# 6. Daily report submit (manual — docs flow)
# ------------------------------------------------------------------
echo ""
echo "MANUAL STEP 3: $EOD/daily_report_draft.md → docs/daily_reports/2026-05-05.md"
echo "              (lub equivalent path per Adrian's docs flow)"
read -p "Po wykonaniu naciśnij ENTER aby kontynuować..."

# ------------------------------------------------------------------
# 7. Git commit + tag
# ------------------------------------------------------------------
git add "$MEMORY_DIR/MEMORY.md" \
        "$MEMORY_DIR/project_sprint_05_05_2026-05-05.md" \
        "$MEMORY_DIR/lekcja_72_2026-05-05.md" \
        dispatch_v2/CLAUDE.md \
        dispatch_v2/eod_drafts/2026-05-05/

# Conditional add Lekcja #73 jeśli istnieje
if [ -f "$MEMORY_DIR/lekcja_73_2026-05-05.md" ]; then
    git add "$MEMORY_DIR/lekcja_73_2026-05-05.md"
fi

git commit -m "$(cat <<'EOF'
docs(eod): 2026-05-05 sprint close — TASK B Phase 2 + TASK A + Issue #1 + Geocoding diagnostics

5 deploys + 8 tagów dziennego sprintu:
- shift-callback-auth-fix (P0 DM auth)
- tb-2-test-isolation-lekcja-71 (refactor + memory)
- tb-1-3-bundled (Bartek alert routing + /poprawa)
- tb-1-bartek-activate + task-a-czasowka-proactive (hot-reload + TASK A)
- issue-1-shift-routing-grupa (SHIFT routing → grupa hot-reload)

114 nowych testów. 3 restart dispatch-telegram (off-peak). 0 rollbacks.
4/4 flagi TASK A LIVE. Geocoding root cause PROVEN, sprint Sr+Pt.
Sprawa #1 9 mappings (5 unmapped + 4 partial) deploy ~17:00 UTC.

Memory: project entry + Lekcja #72 (granular flag rollback) + #73 (robust median + outlier filter).
CLAUDE.md: TASK D + TASK E + Wytyczna #1 4-checkbox pre-implementation review.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"

git tag sprint-05-05-end-of-day-2026-05-05

# ------------------------------------------------------------------
# 8. Branch merge decision (per branch_merge_plan.md)
# ------------------------------------------------------------------
echo ""
echo "=== BRANCH MERGE DECISION ==="
echo "Review $EOD/branch_merge_plan.md — 3 options:"
echo "  A) Pt 08.05 (oryginal plan, INVALIDATED by Faza 7 deferral)"
echo "  B) EOD dziś (Cloud Claude REC)"
echo "  C) Sr 06.05 EOD (post-Geocoding+Faza-7-debug)"
echo ""
echo "Default (skip): Option A — branch zostaje, NO merge teraz."
read -p "Merge sprint-05-05-tb-phase2-task-a → master TERAZ (Option B)? (y/N): " merge_now

if [ "$merge_now" = "y" ] || [ "$merge_now" = "Y" ]; then
    echo "Pre-merge state checks..."
    git fetch origin
    AHEAD_REMOTE=$(git log origin/master..master --oneline 2>/dev/null | wc -l)
    if [ "$AHEAD_REMOTE" != "0" ]; then
        echo "WARN: master ahead of origin/master by $AHEAD_REMOTE commits — abort merge, manual review."
    else
        git checkout master
        git pull --ff-only
        git merge sprint-05-05-tb-phase2-task-a --no-ff -m "$(cat <<'MERGE_EOF'
Sprint 2026-05-05 close — TASK B Phase 2 + TASK A czasówki + Issue #1

5 deploys + 8 tagów dziennego sprintu LIVE od:
- shift-callback-auth-fix-2026-05-05 (P0 DM auth)
- tb-2-test-isolation-lekcja-71-2026-05-05 (refactor + memory)
- tb-1-3-bundled-2026-05-05 (alert routing + /poprawa)
- tb-1-bartek-activate-2026-05-05 (Bartek hot-reload)
- task-a-czasowka-proactive-2026-05-05 (TASK A package)
- issue-1-shift-routing-grupa-2026-05-05 (SHIFT routing)
- sprint-05-05-end-of-day-2026-05-05 (memory + docs)

114 nowych testów. 3 restart dispatch-telegram (off-peak). 0 rollbacks.
Faza 7 GO/NO-GO deferred 15.05 (Pn 06.05 Plan B debug all_bag_zero).
TASK D revised 4-step → 3-step (czwartek 07.05 ~3h post audit).
Lekcja #74 candidate — TASK A evaluator divergence (5/5 NO_CANDIDATE
live obs 14:24-15:32 UTC ale main dispatcher 3/3 cid przypisany).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
MERGE_EOF
)"
        git tag sprint-05-05-merged-master-2026-05-05
        echo ""
        echo "Push master z tagami? (Adrian explicit ACK wymagany)"
        read -p "git push origin master --tags? (y/N): " push_now
        if [ "$push_now" = "y" ] || [ "$push_now" = "Y" ]; then
            git push origin master --tags
            echo "Pushed master + tags."
        else
            echo "SKIP push — local merge only. Push manually gdy gotowy."
        fi
    fi
else
    echo "SKIP merge — branch sprint-05-05-tb-phase2-task-a zostaje, master untouched."
fi

echo ""
echo "=== EOD INTEGRATION COMPLETE ==="
echo "Tag: sprint-05-05-end-of-day-2026-05-05"
if [ "$merge_now" = "y" ] || [ "$merge_now" = "Y" ]; then
    echo "Merge: Option B (EOD dziś) — sprint-05-05-merged-master-2026-05-05"
else
    echo "Merge: deferred — Option A (Pt 08.05) lub Option C (Sr 06.05) per Adrian decision"
fi
git log --oneline -5
echo ""
echo "Verify memory:"
ls -la "$MEMORY_DIR"/lekcja_7*_2026-05-05.md "$MEMORY_DIR"/project_sprint_05_05_2026-05-05.md 2>/dev/null || true
