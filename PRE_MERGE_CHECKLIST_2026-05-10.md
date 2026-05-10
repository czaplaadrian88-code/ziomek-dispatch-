# Pre-merge checklist — sprint-07-05-event-bus-opcja-c → master (gate 2026-05-10)

## Co to jest pre-merge checklist

Lista kroków do **wykonania w kolejności** przed `git merge sprint-XX → master` żeby:

1. **Zabezpieczyć rollback** — gdy merge wprowadzi regresję, MUSI być sposób żeby cofnąć w <5 min
2. **Audit trail** — pojedynczy merge commit jako kotwica (rzeczywiście "co się zmieniło" widoczne jednym `git log master`)
3. **LIVE state ≠ branch state** — branch może mieć kod LIVE od dawna, ale nie wszystkie restartowane (deferred). Pre-merge moment wymusza weryfikację że deployment jest zsynchronizowany z kodem
4. **Nie ma niedokończonych eksperymentów** — flag w `flags.json` które wisiały "shadow only" muszą mieć zdefiniowany next-step (flip, defer, revert)
5. **Test baseline aktualny** — `master` po merge'u staje się referencją dla kolejnego sprintu; jeśli regression broken, każdy następny sprint zaczyna od zepsutego baseline'u
6. **Memory + docs spójne** — sprint_timeline + tech_debt + ZIOMEK_MASTER_KB odzwierciedlają faktyczny stan post-merge

Bez checklisty łatwo o:
- Merge w peak-hour z deferred restartem = mass-incident
- Niezauważony stale flag w shadow → mass score drift po flip
- "Zapomniany" tech-debt item → wraca jako incident po 2 tygodniach

---

## Stan branchu (przygotowane 2026-05-08 wieczór)

```
Branch:       sprint-07-05-event-bus-opcja-c
Base:         master @ 10c754d (merge: sprint-06-05-debug — F2/F3/F4)
Diff:         +65 commits, 98 files, +15903/-457 LOC
Pierwsze:     opcja-c etap 1: audit_log table + emit_audit (07.05)
Ostatnie:     A4 CONFIG_RELOAD broadcast pub/sub (08.05)
Tagi w gałęzi: ~25 milestone tags (v327x, MP-#X, A1-A4, ETAP 1+2)
```

### Zawartość high-level

| Kategoria | Co |
|---|---|
| **Architektura events.db** | Opcja C — split AUDIT/QUEUE/BROADCAST sets, audit_log table, lazy init |
| **Master Plan TOP-15** | 15/15 LIVE/READY (MP-#1..#15) |
| **A-series (audit-driven)** | A1 silent killers cross-codebase, A2 startup pending scan, A3 geocode TTL, A4 CONFIG_RELOAD |
| **Sprinty produkcyjne** | parser_health structural fix, firmowe konto Nadajesz.pl, ETAP B kurier roster, mockup v2 Telegram, ETAP 1+2 pickup label |
| **Tech debt cleanup** | 21/24 items DONE (#7/#1/#13/#5/#6 Step 1/#4/#3/#8/#11/#10/#9/#12/#14/#15/#16/#18/#19abc/#21/#22 + A1-A4) |

---

## Checklist (wykonać sekwencyjnie 09.05 wieczór lub 10.05 rano)

### Sekcja 1 — LIVE state weryfikacja (~10 min)

- [ ] **1.1 Wszystkie 15 services healthy:**
  ```bash
  for s in dispatch-shadow dispatch-panel-watcher dispatch-telegram dispatch-sla-tracker \
           dispatch-gps dispatch-czasowka dispatch-shift-notify.timer \
           dispatch-overrides-reset.timer dispatch-r04-evaluator.timer \
           dispatch-cod-weekly.timer dispatch-daily-accounting.timer \
           dispatch-plan-recheck.service dispatch-watchdog.timer; do
    echo -n "$s: "; systemctl is-active "$s"
  done
  ```
  Expected: wszystkie `active` (timery — `active waiting`, oneshot — może być `inactive` między tickami).

- [ ] **1.2 Health endpoints zielone:**
  ```bash
  curl -s http://localhost:8888/health/parser | python3 -m json.tool | grep -E "status|anomaly_detected"
  curl -s http://localhost:8888/health/all 2>/dev/null | python3 -m json.tool | head -30
  ```
  Expected: `status=healthy, anomaly_detected=false`. `/health/all` (MP-#14) = `overall_status=ok`.

- [ ] **1.3 Zero ERROR w journal ostatnia godzina (poza znanymi false-pos):**
  ```bash
  for s in dispatch-shadow dispatch-panel-watcher dispatch-telegram dispatch-sla-tracker; do
    echo "=== $s ==="
    journalctl -u $s --since "1 hour ago" -p err -n 5 --no-pager
  done
  ```

- [ ] **1.4 ETAP 2 morning report 09.05 07:00 Warsaw przeczytany:**
  - Telegram message + `/tmp/etap2_morning_report.txt`
  - `pre_shift_clamp_applied=true count > 0` ✓
  - Ranking unchanged ✓ (tylko czas się przesunął)
  - Zero errors w shadow journal od 18:30 UTC 08.05 ✓
  - **Decyzja:** jeśli ⚠ → rollback drop-in `etap2-flip.conf` PRZED merge.

### Sekcja 2 — Deferred restart pickup (~30 min)

Wszystkie A1-A4 + część MP-#X są **restart-deferred**. Master merge nie wymaga żeby każdy był LIVE pre-merge ALE jeśli któryś rollback wymagany, lepiej znaleźć to teraz niż post-merge.

- [ ] **2.1 Inwentarz "co dormant w jakim procesie":**
  | Service | Co czeka na restart |
  |---|---|
  | dispatch-telegram | A2 startup scan, MP-#11 jsonl_appender (drugi callsite append_learning) |
  | dispatch-shadow | A1 fix #6/#7/#8 (plan_manager + scoring), A3 geocode TTL/drift |
  | dispatch-panel-watcher | A1 fix #1-#5 (courier_resolver), A3 geocode TTL, MP-#14 /health/all |
  | dispatch-sla-tracker | A3 geocode TTL |

- [ ] **2.2 Decyzja restart strategy pre-merge:**
  - **Opcja A (rekomendowana)** — restart wszystkich 4 services off-peak (np. 09.05 noc 22-06 UTC = 00-08 Warsaw) bundlowany. Jeśli coś sypie, znajdziemy to przed merge'em.
  - **Opcja B** — defer do natural restart cycle (dispatch-telegram nigdy nie restartuje sam — wymaga ACK; reszta restartuje się okazjonalnie). Risk: A1-A4 LIVE może ujawnić bug po merge'u.
  - **Adrian decyzja:** _____________

- [ ] **2.3 Smoke test post-restart każdego service:**
  ```bash
  systemctl restart dispatch-shadow && sleep 10
  journalctl -u dispatch-shadow --since "1 minute ago" -p warning -n 20 --no-pager
  # repeat dla panel-watcher, sla-tracker
  # dispatch-telegram WYMAGA explicit ACK Adrian
  ```

### Sekcja 3 — Test regression baseline (~5 min)

- [ ] **3.1 Pełny pytest suite:**
  ```bash
  cd /root/.openclaw/workspace/scripts
  /root/.openclaw/venvs/dispatch/bin/python -m pytest dispatch_v2/tests/ \
    --ignore=dispatch_v2/tests/test_cod_weekly.py \
    --ignore=dispatch_v2/tests/test_feasibility_c3.py \
    --ignore=dispatch_v2/tests/test_decision_engine_f21.py \
    -q 2>&1 | tail -10
  ```
  Expected: **PASS rate >= baseline** (pre-existing FAIL ~10 udokumentowanych — verified identical pre/post).

- [ ] **3.2 Custom-runner tests (Mailek-style w dispatch_v2):**
  ```bash
  for f in dispatch_v2/tests/test_*.py; do
    grep -l "if __name__ == .__main__." "$f" 2>/dev/null
  done | head -10
  ```
  Lista w sprint_timeline (post-Master Plan close: ~31 custom-runners). Run każdy z osobna.

- [ ] **3.3 Test fixtures NIE powinny zmienić baseline:**
  ```bash
  git diff master..HEAD -- "**/tests/" | head -50
  ```
  (sanity że tylko PASS-rate baseline'u zmienił się przez nowe testy A1-A4 + MP-#X — nie zmiana w istniejących).

### Sekcja 4 — Pending tech debt + flags (~10 min)

- [ ] **4.1 Aktualne flagi w `flags.json` — wszystkie mają deklarowany state:**
  ```bash
  cat /root/.openclaw/workspace/scripts/flags.json | python3 -m json.tool
  ```
  Każda flaga ma być w jednym z trzech stanów:
  - **LIVE** (oczekiwane production behavior — flag wartość zgodna z intencją)
  - **SHADOW** (logging-only, czeka calibration data — ma deklarowany flip-date)
  - **DEFER** (out-of-scope, ma adnotację `_comment_*` w flags.json)

  Sprawdzić specjalnie:
  - `AUTO_PROXIMITY_*` — Etap 0 shadow LIVE, calibration ~13.05 (POST merge OK)
  - `FAZA7_AGREEMENT_BUTTONS_ENABLED` — flag OFF (UX collision z PROPOSAL_FORMAT_V2)
  - `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` — ETAP 2 — flip 08.05 18:30 UTC (verify POST gate)
  - `ENABLE_GEOCODE_CACHE_TTL` — A3 — default ON (env-overridable)
  - `ENABLE_GEOCODE_CACHE_DRIFT_ALERT` — A3 — default OFF (opt-in)

- [ ] **4.2 Pozostałe tech debt items udokumentowane jako defer/post-merge:**
  - **#6 R-04 Step 2-3** — gate 14.05 (OK, post-merge)
  - **#17 firmowe_konto static map** — P2, Q3 defer (OK)
  - **#20 POSTPONE auto-replan** — P3, stub OK (OK)
  - **#23 ETAP 2 verify** — auto 09.05 07:00 (OK, sekcja 1.4)

- [ ] **4.3 A4.1 follow-up wpisany do tech_debt_backlog:**
  Wire `BroadcastSubscriber` w 4 workers + GC broadcast events >7d. Aktualnie infrastruktura LIVE ale 0 subscriberów = zero behavior change. **Dopisać item przed merge.**

### Sekcja 5 — Memory + docs (~10 min)

- [ ] **5.1 `sprint_timeline.md` CURRENT HANDOFF udokumentowany dla sesji 08.05 wieczór:**
  - A1+A2+A3+A4 LIVE/READY
  - +65 commits ahead pre-merge
  - Wszystkie nowe lekcje (#100+ jeśli były) dopisane

- [ ] **5.2 `tech_debt_backlog.md` zsynchronizowany:**
  - 21 → ?? items DONE (po A1-A4: 25/29? — recount)
  - A4.1 follow-up dopisany

- [ ] **5.3 `MEMORY.md` index aktualny** (≤200 linii, najnowszy snapshot na końcu).

- [ ] **5.4 `dispatch_v2/CLAUDE.md` sprint snapshot** — ostatnia sekcja "## CLAUDE.md — XYZ sprint" data + tagi.

- [ ] **5.5 `ZIOMEK_MASTER_KB.md` Część I (current state) sprawdzony** — feature flags + master plan status + V3.X/F2.X tag chronologiczny.

### Sekcja 6 — Backup pre-merge (~3 min)

- [ ] **6.1 Memory backup z timestampem:**
  ```bash
  cd /root/.claude/projects/-root/
  tar -czf memory_backup_2026-05-10_pre_merge.tar.gz memory/ && ls -la memory_backup_*.tar.gz
  ```

- [ ] **6.2 Live state snapshot:**
  ```bash
  mkdir -p /root/pre_merge_snapshot_2026-05-10
  cp /root/.openclaw/workspace/dispatch_state/*.json /root/pre_merge_snapshot_2026-05-10/
  cp /root/.openclaw/workspace/scripts/flags.json /root/pre_merge_snapshot_2026-05-10/
  systemctl list-units --state=active 'dispatch-*' > /root/pre_merge_snapshot_2026-05-10/services.txt
  git tag pre-merge-baseline-2026-05-10
  ```

### Sekcja 7 — Merge mechanics (~5 min)

- [ ] **7.1 Pre-merge sanity:**
  ```bash
  cd /root/.openclaw/workspace/scripts/dispatch_v2
  git fetch origin
  git checkout sprint-07-05-event-bus-opcja-c
  git status                                      # clean
  git log master..HEAD --oneline | wc -l          # ~65 commits
  git diff master..HEAD --stat | tail -5
  ```

- [ ] **7.2 Pull master (jeśli ktoś commitnął bezpośrednio):**
  ```bash
  git checkout master
  git pull origin master
  git log -3 --oneline                            # zweryfikować że to nadal 10c754d
  ```

- [ ] **7.3 Merge `--no-ff` z descriptive message:**
  ```bash
  git merge sprint-07-05-event-bus-opcja-c --no-ff -m "$(cat <<'EOF'
  merge: sprint-07-05-event-bus-opcja-c (Master Plan TOP-15 + A1-A4 + Etap 1+2)

  ~65 commits, 98 files, +15903/-457 LOC over 7 dni (07.05 → 10.05).

  Highlights:
  - Architektura: events.db opcja C (AUDIT/QUEUE/BROADCAST 3-way disjoint)
  - Master Plan TOP-15: 15/15 LIVE/READY (MP-#1..#15)
  - A-series audit-driven: A1 silent killers cross-codebase, A2 startup
    pending scan, A3 geocode cache TTL+drift, A4 CONFIG_RELOAD pub/sub
  - Sprinty produkcyjne: parser_health structural fix, firmowe konto
    Nadajesz.pl, ETAP B kurier roster, mockup v2 Telegram redesign,
    ETAP 1+2 pickup label
  - Tech debt: 25/29 items DONE (#1/#3/#4/#5/#6.1/#7/#8-#23 + A1-A4)

  Pending post-merge:
  - #6 R-04 Step 2-3 (gate 14.05)
  - #17/#20 (P2/P3 defer)
  - A4.1 wire subscribers (follow-up scope-driven)
  - Storage Box BX11 + MP-#9 OVH SMS (Adrian provisioning)
  EOF
  )"
  ```

- [ ] **7.4 Tag merge commit:**
  ```bash
  git tag master-merge-2026-05-10
  ```

- [ ] **7.5 Push master (jeśli używany remote):**
  ```bash
  git push origin master --tags
  ```
  Albo skip jeśli local-only repo (per CLAUDE.md "Github working remote" — sprawdzić).

### Sekcja 8 — Post-merge smoke (~15 min)

- [ ] **8.1 Branch sprint-07-05-event-bus-opcja-c ZACHOWANY** (NIE delete) jako historical reference 2-4 tyg.

- [ ] **8.2 Health endpoints po-merge:**
  ```bash
  curl -s http://localhost:8888/health/parser | python3 -m json.tool | head -20
  curl -s http://localhost:8888/health/all 2>/dev/null | python3 -m json.tool | head -30
  ```
  Expected: zero zmian behavior — merge nie restartuje services.

- [ ] **8.3 30-min observation window** (Adrian):
  - Tail Telegram bot dla dziwnych alertów
  - `journalctl -f -u dispatch-shadow -u dispatch-panel-watcher -u dispatch-telegram` przez 5 min
  - Smoke 1-2 real propozycji jeśli peak (lunch 11-14 / dinner 17-20)

- [ ] **8.4 Update memory:**
  - `sprint_timeline.md` HANDOFF section: "## CURRENT HANDOFF (post-merge 2026-05-10)"
  - Reset branch headers w `MEMORY.md` (`+65 commits` → `merged 10.05, +0 commits`)
  - Tag `post-merge-stable-2026-05-10` jeśli 30 min smoke clean

---

## Rollback procedury (gdy coś się posypie)

### Soft rollback — flag flip (5 sek, in-process)

Każdy LIVE feature ma flag w `flags.json` lub env var. Patrz `dispatch_v2/CLAUDE.md` per-sprint sekcje.

### Hard rollback — git revert merge commit

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
git checkout master
git revert -m 1 <merge_commit_sha> --no-edit
# Restart wszystkich 4 long-running services + telegram explicit ACK
```

### Nuclear — reset master do pre-merge

```bash
git checkout master
git reset --hard pre-merge-baseline-2026-05-10
git push origin master --force-with-lease  # ⚠ TYLKO Adrian explicit
# Restart services
```

⚠ Force-push do master = **NIGDY bez explicit Adrian ACK** (per CLAUDE.md hard rule).

---

## Decyzje pre-merge (do zapisania w sprint_timeline)

1. **Restart strategy** (Sekcja 2.2): bundlowany off-peak vs natural cycle
2. **Push do remote** (Sekcja 7.5): czy aktywny `origin` używany czy local-only
3. **Branch retention** (Sekcja 8.1): 2 tyg vs 4 tyg vs forever
4. **Storage Box BX11 / MP-#9 OVH** — w scopie merge'u (DEFER post)? — TAK, deferred do Adrian provisioning 09-10.05

---

## Następny sprint (post-merge target)

| Priorytet | Item | Effort |
|---|---|---|
| **P1** | A4.1 wire BroadcastSubscriber w 4 workers | ~2-3h |
| **P1** | B1 asyncio.create_subprocess_exec native | ~2h |
| **P2** | B2 core/state_io.py centralizacja (Z3 prep) | ~4-6h |
| **P2** | B3 Prometheus exporter + SLO declared | ~2-3h |
| **P0** | #6 R-04 Step 3 ENFORCE flip (gate 14-15.05) | ~30min Adrian + 1h CC |
| **P3** | A3 cache_gc_stale weekly cron | ~30min |

Faza 7 100% target ~31.05. Decyzja #2 Postgres prep Q3 (~01.07) — duża fala odrębna.
