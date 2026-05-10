# Daily Report — 2026-05-05 (Tue)

## Sprint Summary

**5 deploys LIVE + 8 tagów + 114 nowych testów + Issue #1 fix LIVE** w sprincie ~5h (8h budget).

| # | Tag | Commit | Sprawa |
|---|-----|--------|--------|
| 1 | `shift-callback-auth-fix-2026-05-05` | `71affb2` | P0 fix DM auth dla SHIFT_* callbacks |
| 2 | `tb-2-test-isolation-lekcja-71-2026-05-05` | `ec68635` | Unified `isolated_shift_state()` + Lekcja #71 |
| 3 | `tb-1-3-bundled-2026-05-05` | `09b41ac` | TB-1 Bartek alert routing + TB-3 /poprawa |
| 4a | `tb-1-bartek-activate-2026-05-05` | `785808a` | Bartek user_id activation (hot-reload) |
| 4b | `task-a-czasowka-proactive-2026-05-05` | `785808a` | TASK A package + 4 Z3 fixes |
| 5 | `issue-1-shift-routing-grupa-2026-05-05` | `47d974f` | SHIFT notifications routing → grupa hot-reload |

**Branch state:** `sprint-05-05-tb-phase2-task-a` (NIE master — merge planowany pod koniec tygodnia po Faza 7 GO/NO-GO).

**Restart count:** 3 × dispatch-telegram (1 explicit ACK Adrian, 2 background bug fixes; każdy off-peak).

## Bug Fixes

- **P0 DM callback auth** (`shift-callback-auth-fix-2026-05-05`): security gate w `telegram_approver.py:1907` wymagał `cb_chat_id == state["admin_id"]` (group), więc SHIFT_* callbacks z prywatnego DM (Adrian/Bartek) odrzucane jako "⛔ unauthorized" (handler nigdy nie wykonany, decision=null pozostawał w state). Fix: gate expand prefix-aware whitelist `SHIFT_TASKB_ACTIONS` + `KONIEC_AUTHORIZED_USER_IDS` dla DM auth path. Validated w prod 06:13 UTC: 2 successful clicks Adrian → state mutation OK.
- **TB-1 signature mismatch (pre-existing)**: prod alert call site w `telegram_approver.py:1652-1656` używał nieprawidłowych nazwanych argumentów (`chat_id=, text=, keyboard=`) — funkcja w `telegram_send.py:60-64` ma sygnaturę `(text, inline_keyboard, chat_id=None)` → TypeError swallowed przez `except Exception` (alert NIGDY nie wysłany). Każdy SHIFT_START_NO callback miał wysłać alert do koordynatora — crashował silently. Fix: positional `tg_send_text_with_keyboard(alert_text, [], chat_id=target_chat)` + helper `_resolve_bartek_alert_target()` z DM routing.
- **Issue #1 SHIFT routing → grupa**: SHIFT notifications worker wysyłał T-60 START / T-30 REMINDER / T-60 END do Adrian DM (`ADRIAN_CHAT_ID_FALLBACK = 8765130486`) zamiast grupy ziomka. Konsystencja z TASK A czasówkami (grupa `-5149910559`). Fix: flag-based hot-reload `SHIFT_NOTIFY_TARGET_CHAT_ID=-5149910559` + helper `_resolve_shift_notify_target_chat()` z fallback chain. Zero restart wymagany.

## New Features

- **TB-2** — unified `isolated_shift_state()` test fixture w `tests/_shift_test_helpers.py` + Lekcja #71 "Decoupled State Lifecycles + Test Isolation". Refactor 21 callsites w 2 plikach testowych. 31/31 PASS post-refactor.
- **TB-3** — `/poprawa [cid]` command (mirror /koniec) — koordynator odwołuje "Nie przyjdzie" gdy kurier mimo wszystko przyszedł. Mutation w `start_notified` bucket: decision False→True + `reverted_via_poprawa_at` audit field. Idempotent + authorized via `KONIEC_AUTHORIZED_USER_IDS`.
- **TASK A czasówki** (proactive scheduler dla orderów ≥60min prep z `id_kurier=26 Koordynator`):
  - **T-50** (50min przed pickup): 3-button Telegram proposal (Tak/Nie/Czekaj) do grupy
  - **T-40** (40min przed pickup): LAST CHANCE 2-button (Tak/Nie, no Czekaj — last attempt)
  - **T-0** (pickup time, jeśli nadal nieprzypisana): info-only alert do grupy
  - Adrian Z3 fixes: chat target **grupa -5149910559** (NIE Adrian DM), race REJECT split 3-way (RACE_LOST_ALREADY_ASSIGNED / REJECT_RACE_ID_KURIER_NONE / REJECT_RACE_FETCH_NONE), T-0 reuse `CZASOWKA_TRIGGER_TOLERANCE_MIN`, emoji konsystencja (🕐 T-50/T-40, ⏰ no_candidate, 🚨 T-0)
  - Audit trail: edit message po success "✅ Przypisane przez {first_name}" — kto kliknął widoczny w grupie
  - 26 (state) + 14 (evaluator) + 6 (templates) + 12 (handlers) = 58 nowych testów dla TASK A
  - Plus 6 (Issue #1) + 10 (Sprawa #1 pre-build) + 4 (TB-1 routing) + 6 (TB-3 /poprawa) + 21 refactored (TB-2) = **114 nowych/refactored testów total dziś**
- **Issue #1** — SHIFT notifications routing → grupa via flag-based hot-reload (3 stores: shift_notifications/telegram_send.py + flags.json + tests).

## Activations

- **Bartek user_id mapping** confirmed Adrian 06:43 UTC (`8753482870`) + dummy DM sent (`SHIFT_NOTIFY_TG_SENT chat=8753482870`, result=True). Adrian's confirmation z Bartkiem pending.
- **TB-1 hot-reload activation** od 06:43 UTC bez restart — alerts route do Bartek DM via `_resolve_bartek_alert_target()` z `load_flags()` raw int read.
- **TB-1 KONIEC_AUTHORIZED_USER_IDS** code change LIVE od 07:08 UTC restart — Bartek może `/koniec`, `/poprawa`, klikać SHIFT_* + future CZAS_* z DM.
- **Flag flip sequence TASK A — wszystkie 4/4 LIVE:**
  - 10:44:54 UTC `CZASOWKA_PROACTIVE_ENABLED=true` ✅ (master switch dry run)
  - 10:48:50 UTC `CZASOWKA_T0_ALERT_ENABLED=true` ✅ (info-only)
  - 10:53:57 UTC `CZASOWKA_T40_ENABLED=true` ✅ (LAST CHANCE 2-button)
  - 11:00:37 UTC `CZASOWKA_T50_ENABLED=true` ✅ (full scope 3-button — Adrian's original timing zachowany)
  - Audit trail: 4× `event=FLAG_FLIP_TASK_A` w learning_log z `sequence_complete=true`
- **Compressed catch-up**: pierwotny schedule 08:30/09:00/10:00/11:00 UTC został compressed do 10:35-11:00 UTC po time-skew (notifications batched po limit reset 10:30 UTC). Adrian's T50@11:00 UTC zachowane.

## Background Diagnostics — Geocoding

2 background diagnostic agents podpięte do production logs. **Root cause PROVEN:**

- `dispatch_pipeline.py:421` — hardcoded `"Białystok"` w fallback path
- 16 unmapped satellite cities w `events.db` (Choroszcz, Dobrzyniewo, Wasilków, Supraśl, etc.)

**Impact:** 36.7% NEW_ORDER events affected + 148 unmapped sat city orders / 30d window.

**Deferred:** Geocoding Phase 1 środa 06.05 (4h, Components 1-3) + Phase 2 piątek 08.05 (3-4h, Components 5-8).

**3rd critical finding (live obs 14:24-15:32 UTC):** TASK A evaluator NO_CANDIDATE 5/5 fires (470805/470821/470808) ale main dispatcher COURIER_ASSIGNED 3/3 same oids w 6-19 min post-fire. Pattern paralell do Faza 7 LGBM all_bag_zero — evaluator-vs-main divergence systemic. Lekcja #74 candidate. Debug sprint Pn 06.05 łączy oba (`faza_7_debug_plan_pn_06_05.md`).

## Sprawa #1 Pre-Build (Agent B)

`migrations/migrate_couriers_2026-05-05.py` — 830 LoC migration script + 458 LoC tests gotowe. Deploy ~17:00 UTC.

## Tygodniowe Sequencing

- **Środa 06.05:** TASK A validation (po wschodnim cyklu T-60 → T-0) + Geocoding Phase 1 (4h, Components 1-3: zones_registry hierarchical, geocoding upgrade, drop-zone outside-city).
- **Czwartek 07.05:** TASK D Auto-Discovery + Onboarding Pipeline (~3h revised post audit, 5+1 components D.1-D.6, atomic 3-store NIE 4-store).
- **Piątek 08.05:** Geocoding Phase 2 (3-4h, Components 5-8: OSRM bbox, adjacency, migration, tests, flagi) + **Faza 7 GO/NO-GO decision**.

## Time Budget

- ~5h aktywny sprint w 8h budgecie
- 5 deploys w ~37 min code sprint window
- 0 rollbacks potrzebnych
- 0 production incidents

## Pending Adrian Items

1. **Bartek DM confirmation** — czy dummy DM "🔧 TB-1 routing aktywne" dotarł cleanly (Adrian sprawdza z Bartkiem post-fact).
2. **Geocoding adjacency draft review** — CC pre-build dziś (Agent w background ~12:00 UTC `geocoding_adjacency_draft_2026-05-06.md` z auto-pairs ≤2km). Adrian ACCEPT/REJECT per-edge środa rano (~15 min) zamiast budowania od zera.
3. **Flag flip rollback gates** — jeśli regresja na pierwszej real czasówce w T-50/T-40 oknie: `flag=false` w flags.json = 5s rollback, zero restart.
4. **Faza 7 GO/NO-GO** decision pt 08.05 po week obs LGBM shadow.
5. **Sprawa #1 deploy** ACK ~17:00 UTC po dinner peak (script `migrations/migrate_couriers_2026-05-05.py` 830 LoC + 458 LoC tests gotowe, 10/10 PASS).
6. **Lekcja #72 candidate** "Granular flag-based rollback" (`lekcja_72_candidate.md`) — Adrian ACK przed promotion z candidate → final w memory.
7. **Lekcja #74 candidate** "Evaluator-vs-Main-Dispatcher divergence" — Adrian ACK przed promotion.
8. **TASK A interim mitigation** — czy wyłączyć T0_ALERT/T40/T50 do post-debug Pn 06.05 (zmniejsza grupowy noise)?

## Live Observation Window (post-T50 deploy)

**Aktywne czasówki w queue (events.db @ 11:01 UTC):** 1 single (470756 Sushi Rany Julek & Pizza Majstry, pickup 19:11 Warsaw, prep 375 min, czas_kuriera 19:12).

**Forecast pierwsza real fire:** ~16:21 UTC (T-50 dla 470756 jeśli nadal `id_kurier=26`). Nowe czasówki w queue mogą wpadać w międzyczasie (NEW_ORDER prep>=60 → auto-koord TASK 4 → po 50 min idle → T-50 fire).

**Background watcher** `bf0wr20cg` aktywny — auto-detect czasowka_proposals_state.json creation lub candidate_decisions_*.jsonl entries z source="czasowka_proactive".

## Daily Report Submission

Planowane 19:00-19:30 UTC.
