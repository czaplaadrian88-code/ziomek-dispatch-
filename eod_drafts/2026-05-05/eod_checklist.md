# EOD Checklist — 2026-05-05

Pre-exec checklist dla Adriana ~19:00 UTC, przed `bash integration_commands.sh`.

Time-box: 5-10 min review + 5 min exec.

## 1. Review drafts (5-10 min)

Przeczytaj ostatecznie po sprincie i potwierdź każdy item:

- [ ] `daily_report_draft.md` — 5 deploys + 8 tagów + 114 tests, accurate?
- [ ] `memory_project_sprint_05_05.md` — frontmatter + audit trail OK?
- [ ] `memory_pointer_line.md` — 1 linijka format zgodny z existing entries (`- [Title](file.md) — desc`)?
- [ ] `claude_md_taskd_taske_wytyczna.md` — TASK D D.4 4 stores per moja spec? Wytyczna #1 4-checkbox brzmi sensownie?
- [ ] `lekcja_72_candidate.md` — Granular flag-based rollback narrative OK? Cross-refs #71/#58/#34 prawidłowe?
- [ ] `lekcja_73_robust_median_pattern.md` — robust median pattern uznane jako general design (multi-region scaling)?
- [ ] `geocoding_adjacency_draft_2026-05-06.md` — Adrian Decision Method sekcja jasna? Option A vs B + per-quadrant batch?

## 2. Adrian decisions wymagane

- [ ] **Lekcja #72 promotion** candidate → final w memory? (Y/N)
- [ ] **Lekcja #73 promotion** candidate → final w memory? (Y/N)
- [ ] **Bartek DM confirmation** — czy dummy DM dotarł? (jeśli Bartek odpowiedział)
- [ ] **Sprawa #1 deployment** — czy 9 mappings response w jednym message? (Y/N)
- [ ] **Branch merge timing** — confirm Pt 08.05 po Faza 7 GO/NO-GO? (Y/N)

## 3. Pending agent drafts (sprawdź obecność przed exec)

Niektóre pliki mogą być missing jeśli agenci 1-5 nie ukończyli:

- [x] `daily_report_draft.md` — present
- [x] `memory_project_sprint_05_05.md` — present
- [x] `memory_pointer_line.md` — present
- [x] `claude_md_taskd_taske_wytyczna.md` — present
- [x] `lekcja_72_candidate.md` — present
- [x] `lekcja_73_robust_median_pattern.md` — present
- [x] `geocoding_adjacency_draft_2026-05-06.md` — present
- [ ] `task_d_courier_api_audit.md` — PENDING (agent #3, opcjonalny)
- [ ] `faza_7_go_nogo_criteria.md` — PENDING (agent #4, opcjonalny)
- [ ] `czasowka_observability_monitor.py` + README + snapshot — PENDING (agent #5, opcjonalny)
- [ ] `sprawa_1_response_template.md` — present
- [ ] `parser_validation.md` — PENDING (agent #2, opcjonalny — 5 test files _sprawa1_test_*.txt już są)

Pending pliki NIE blokują exec — script `integration_commands.sh` ma defensive
guards (check-file-exists, conditional cp + git add).

## 4. Exec sequence (~5 min)

```bash
# 1. Final review skryptu
cat /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-05/integration_commands.sh

# 2. Execute
cd /root/.openclaw/workspace/scripts/dispatch_v2
bash eod_drafts/2026-05-05/integration_commands.sh

# 3. Verify EOD commit + tag
git log --oneline -3

# 4. Verify pointer line w MEMORY.md
head -3 /root/.claude/projects/-root/memory/MEMORY.md

# 5. Verify nowe lekcje + project memory file
ls -la /root/.claude/projects/-root/memory/lekcja_7*_2026-05-05.md \
       /root/.claude/projects/-root/memory/project_sprint_05_05_2026-05-05.md
```

## 5. Manual steps embedded w skrypcie (3 razy `read -p`)

Skrypt zatrzymuje się 3 razy:

1. **Step 1 — MEMORY pointer line.** Manual prepend linia 1 do MEMORY.md.
2. **Step 2 — CLAUDE.md additions.** Manual append 3 sekcji do dispatch_v2/CLAUDE.md.
3. **Step 3 — Daily report submit.** Manual cp do docs/daily_reports/2026-05-05.md (lub Adrian's docs flow).

Każdy step naciśnij ENTER po wykonaniu.

## 6. Pre-condition / contingencies

- **Branch:** `sprint-05-05-tb-phase2-task-a` checked out (NIE master).
- **Adrian ACK:** Lekcji #72 + #73 promotion z candidate → final musi być explicit przed exec.
- **Nieuczony Lekcji ACK:** jeśli Adrian odrzuca Lekcja #73 → usuń `lekcja_73_*` z `MEMORY_DIR` przed git add.
- **Tag już istnieje:** `git tag sprint-05-05-end-of-day-2026-05-05` może fail jeśli tag istnieje (rerun) — skrypt nie ma `-f`, intencjonalnie. W razie konfliktu: `git tag -d sprint-05-05-end-of-day-2026-05-05 && rerun`.
