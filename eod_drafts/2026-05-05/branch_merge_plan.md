# Branch Merge Plan — Sprint 2026-05-05

**Status:** DRAFT — Adrian decision required przed `integration_commands.sh` execute (~19:00 UTC)
**Branch:** `sprint-05-05-tb-phase2-task-a`
**Base:** `master` HEAD `d6f742e docs(task-b)` (post Phase 0+1 close 04.05)

---

## Section 1 — Branch state today

Branch `sprint-05-05-tb-phase2-task-a` zawiera **5 deploy commits + 1 EOD docs commit pending** (~19:00 UTC) ahead of `master`:

| # | Commit | Message |
|---|--------|---------|
| 1 | `47d974f` | fix(shift): Issue #1 — SHIFT routing grupa hot-reload |
| 2 | `785808a` | feat(czasowka): TASK A + TB-1 Bartek activate |
| 3 | `09b41ac` | feat(telegram): TB-1 Bartek DM routing + TB-3 /poprawa |
| 4 | `ec68635` | refactor(tests): TB-2 unified isolated_shift_state() helper + Lekcja #71 |
| 5 | `71affb2` | fix(telegram): security gate accept SHIFT_* callbacks via DM |
| 6 | (pending) | docs(eod): 2026-05-05 sprint close — TASK B Phase 2 + TASK A + Issue #1 + Geocoding |

**Tagi (8 total dla sprint, prefix `*-2026-05-05`):**

1. `shift-callback-auth-fix-2026-05-05`
2. `tb-2-test-isolation-lekcja-71-2026-05-05`
3. `tb-1-3-bundled-2026-05-05`
4. `tb-1-bartek-activate-2026-05-05`
5. `task-a-czasowka-proactive-2026-05-05`
6. `issue-1-shift-routing-grupa-2026-05-05`
7. `sprint-05-05-end-of-day-2026-05-05` (pending — created przez `integration_commands.sh`)
8. (reserved branch-merge tag jeśli Option B/C — np. `sprint-05-05-merged-master-2026-05-05`)

**Production state:** 5/5 deploys LIVE (4/4 flagi TASK B + 1 TASK A). 114 nowych testów PASS. 3× restart `dispatch-telegram` (off-peak). 0 rollbacks.

---

## Section 2 — Trzy timing options

### Option A — Pt 08.05 (oryginalny plan z `INDEX.md`)

- **Pros:**
  - 4 dni stabilizacji (Wt/Sr/Cz/Pt) post-deploy z observation lunch+dinner peaks
  - Defensive tygodniowy rhythm (1 sprint = 1 tydzień = 1 merge)
  - Bundled merge z Faza 7 GO/NO-GO outcome (oryginal założenie)
- **Cons:**
  - **Faza 7 GO/NO-GO Pt 08.05 INVALIDATED** — RAW NO-GO + Plan B Pn 06.05 debug + re-baseline 15.05 (per `faza_7_go_nogo_criteria.md`); merge gate już nie istnieje
  - **Lekcja #74 candidate live obs 14:24-15:32 UTC** TASK A 5/5 NO_CANDIDATE evaluator divergence — debug może wymagać kolejnych commitów do branch w nadchodzących dniach (long-lived branch divergence)
  - Geocoding Phase 1 Sr 06.05 + Faza 7 Plan B debug Pn 06.05 będą zostawiać commits na master niezależnie — branch coraz bardziej diverguje
- **Risk:** Long-lived branch merge conflict surface area rośnie liniowo z czasem (3 dni × ~3 expected master commits)

### Option B — EOD dziś ~19:00 UTC (REC)

- **Pros:**
  - Master up-to-date dla wszystkich nadchodzących sprintów (Pn 06.05 Plan B debug + Sr Geocoding + Cz TASK D + Pt re-baseline prep)
  - Brak long-lived branch — debugging Lekcja #74 / TASK A divergence pracuje na master (clean state)
  - Standard sprint closure: 1 sprint = 1 branch = 1 merge (idiom Z3 build for years)
  - Zero merge conflict risk (master untouched od 04.05 wieczór)
  - Sprint clean close — tag `sprint-05-05-end-of-day` + merge w jednej sekwencji
- **Cons:**
  - Brak formal stabilization period (deploys LIVE od ~17h ale brak peak-window observation Pn lunch)
  - Jeśli regression jutro lunch peak → rollback przez `git revert` na master (vs branch reset)
- **Risk:** Nieobserwowane regression w lunch peak Sr 06.05 (mitigated: per-flag rollback, NIE wymaga revert merge commit)

### Option C — Sr 06.05 EOD (post-Geocoding Phase 1 + Faza 7 Plan B debug)

- **Pros:**
  - 1 dzień obs (Wt-Sr morning) lunch peak Wt + Sr
  - Geocoding Phase 1 + Faza 7 debug bundled w jednym merge cycle
- **Cons:**
  - Geocoding Phase 1 może destabilizować — merge włącza zmiany NIE zwalidowane w peak (cumulative risk)
  - Faza 7 Plan B debug może wprowadzić zmiany do branch (jeśli root cause `ml_inference.py:170-177` dotyczy shadow flow consumed przez branch code)
  - 2 dni opóźnienia bez clear technical justyfikacji
- **Risk:** Cumulative regression surface (Sprint 05.05 + Geocoding + Faza 7 debug) trudniejszy do diagnozy

---

### Recommendation

**Option B (EOD dziś ~19:00 UTC)** — reasoning:

1. **Faza 7 deferred 15.05 invalidates Pt 08.05 plan** — oryginal merge gate (Faza 7 GO/NO-GO bundled) już nie istnieje, brak technical justyfikacji dla Pt 08.05
2. **Lekcja #74 candidate** suggests TASK A może wymagać debug w nadchodzących dniach — lepiej żeby debug pracował na master niż long-lived branch (Lekcja #62: minimization w branch lifecycle)
3. **Sanity rule** 1-sprint-1-branch-1-merge — sprint-05-05 wszystkie deploys LIVE w prod, branch lifecycle powinien match prod state
4. **Per-flag rollback path** mitiguje brak stabilization window (każdy z 4 flag TASK B + flag TASK A flippable bez revert merge commit)

**Counter-argument:** Adrian może wybrać Option A defensive (tygodniowy rhythm Z2 quality > brak technical urgency) — valid jeśli priorytet stabilization > clean branch lifecycle. Brak hard technical blocker przeciwko Option A.

---

## Section 3 — Merge mechanics (Option B reference)

```bash
# 0. Pre-merge state checks (READ-ONLY)
cd /root/.openclaw/workspace/scripts/dispatch_v2

git status                        # expect: clean working tree (post-EOD commit)
git log master..HEAD --oneline    # expect: 6 commits (5 deploy + 1 EOD docs)
git tag --list 'sprint-05-05-*' '*-2026-05-05' | sort  # expect: 8 tagów
git fetch origin                  # ensure master not advanced unexpectedly
git log origin/master..master --oneline  # expect: empty (master in sync)

# 1. Switch to master + verify clean
git checkout master
git pull --ff-only                # fast-forward only (defensive)

# 2. Merge --no-ff (preserve sprint history as merge commit)
git merge sprint-05-05-tb-phase2-task-a --no-ff -m "$(cat <<'EOF'
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
EOF
)"

# 3. Optional merge tag
git tag sprint-05-05-merged-master-2026-05-05

# 4. Push z tagami (Adrian explicit ACK wymagany)
git push origin master --tags
```

**Idempotency:** `--ff-only` na pull + `--no-ff` na merge gwarantuje że ponowne uruchomienie po fail zawsze idzie z znanego state. Tag `sprint-05-05-merged-master-2026-05-05` fail jeśli already exists (signal że merge already done).

---

## Section 4 — Risks + mitigations

| # | Risk | P × I | Mitigation |
|---|------|-------|------------|
| 1 | Merge conflict z parallel branch (gdyby ktoś commit master 04-05.05) | L × M | `git fetch + log master..HEAD` pre-merge verify; jeśli conflict → STOP + ack Adrian |
| 2 | Master broken po merge (test fail) | L × H | Pre-merge full test baseline run (smoke); rollback przez `git revert -m 1 <merge-sha>` (preserves history) |
| 3 | Tags duplicate jeśli re-merge accidental | L × L | Tags unique `sprint-05-05-*` + `*-2026-05-05` prefix; `git tag` fail jeśli istnieje |
| 4 | Long-lived branch divergence (Option A only) | M × M | Avoided via Option B EOD merge |
| 5 | Lekcje #72+#73 promotion before Adrian explicit ACK | L × M | Adrian's "akceptuję rekomendacje" 16:23 UTC (pre-EOD verified per `lekcje_72_73_verification_report.md`) |
| 6 | Push master ze złymi tagami (typo) | L × M | `git push --tags --dry-run` opcjonalny pre-step; tag list verify przed push |
| 7 | Post-merge regression peak Sr 06.05 lunch | M × M | Per-flag rollback path (4 flagi TASK B + 1 TASK A flippable bez revert); `git revert -m 1` jako fallback |
| 8 | Faza 7 Plan B debug Pn 06.05 wymaga zmian na branch | L × M | Option B merges dziś → debug na master (no branch needed); Option A by hit this risk Wt-Cz |

**Defensive note:** Push master z `--tags` jest atomic — jeśli network fail po push commits ale przed tags, retry safe (commits już na origin, tags re-push idempotent).

---

## Section 5 — Recommendation summary

**Cloud Claude rec:** **Option B (EOD dziś ~19:00 UTC)**

**Reasoning compressed:**
- Faza 7 GO/NO-GO Pt 08.05 → deferred 15.05 invalidates oryginal merge gate
- Lekcja #74 TASK A divergence → debug branch path lepszy niż long-lived sprint branch
- 1-sprint-1-branch-1-merge sanity (Z3 build-for-years)
- Zero merge conflict risk teraz (master untouched 04.05)

**Adrian decision wymagany przed `integration_commands.sh` execute (~19:00 UTC).** Decyzja A/B/C wpływa na pre-tag step w skripcie — Option B uruchamia merge inline, Option A/C tylko commit + tag bez merge.

**Default jeśli Adrian skip read:** Option A (defensive Pt 08.05) — `integration_commands.sh` zachowuje obecne zachowanie (commit + tag tylko, NO merge).
