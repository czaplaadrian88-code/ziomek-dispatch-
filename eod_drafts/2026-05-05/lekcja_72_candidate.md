---
name: Lekcja #72 — Granular flag-based rollback enables safe production deploys
description: 5 deploys LIVE w 37-min code sprint, 0 rollbacks; flag-based hot-reload defaults > restart-triggered; 5s rollback przez flag-flip > 30 min incident response
type: feedback
---

# Lekcja #72 — Granular Flag-Based Rollback = Safe Production Sprint

## Pryncypium

**Granular flag-based deploys > big-bang.** Każda nowa feature musi mieć:
- Per-feature flag (NIE single global `ENABLE`)
- Default OFF state (`flag=false` przy deploy)
- Hot-reload mechanism (NIE restart-triggered)
- Rollback path = flag flip back (cel: ≤5s)

## Evidence (2026-05-05)

**5 deploys LIVE w ~37-min code sprint window:**
1. `shift-callback-auth-fix` (P0 DM auth)
2. `tb-2-test-isolation` (Lekcja #71)
3. `tb-1-3-bundled` (alert routing + /poprawa)
4. `tb-1-bartek-activate` + `task-a-czasowka-proactive` (Bartek hot-reload + TASK A)
5. `issue-1-shift-routing-grupa` (SHIFT routing fix)

**Wynik:**
- 0 rollbacks potrzeby
- 0 production incidents
- Każdy decision point ~5-10 min cycle
- TASK A default-OFF gradual flip post-deploy:
  - 10:35 UTC `PROACTIVE_ENABLED=true`
  - 10:45 UTC `T0_ALERT_ENABLED=true`
  - 10:53 UTC `T40_ENABLED=true`
  - 11:00 UTC `T50_ENABLED=true`

**Issue #1 hot-reload:** SHIFT routing fix LIVE bez restart dispatch-telegram (flag-based config refresh, worker oneshot per-tick → fresh `load_flags()` per minute).

**Bartek user_id activation — split deploy nuance (kluczowy uświadomienie):**
- `BARTEK_USER_ID=8753482870` + `COORDINATOR_DM_ROUTING_ENABLED=true` w `flags.json` → **hot-reload** od 06:43 UTC (alerts route do DM via `_resolve_bartek_alert_target()` reading `load_flags()` raw int).
- `KONIEC_AUTHORIZED_USER_IDS = [..., 8753482870]` w `telegram_approver.py` (Python module-level constant) → **wymagało restart** (long-running asyncio service, NIE re-import per-call). Bundled z TASK A restart 07:08 UTC.

**Lesson:** runtime config (flags.json) hot-reloadable, ale Python module-level constants wymagają restart. Per-feature design powinien preferować runtime config gdziekolwiek możliwe.

## Anti-Patterns (do unikania)

1. **Single global `ENABLE` flag** — granularność = 1; rollback = wszystko-lub-nic; debug ciężki gdy mix dobrych+złych zmian.
2. **Code change z restart wymaganym** — peak window blackout (Pn-Pt 11-14, 17-20; sob 16-21); deploy zablokowany 6+h dziennie; rollback = drugi restart = drugi blackout.
3. **Tests bez per-flag scenario** — test `ENABLE_X=true` bez test `ENABLE_X=false` = cannot verify default-OFF safety; missing rollback validation.
4. **Flag flip bez observability gate** — flip `T50_ENABLED=true` bez sprawdzenia logów `T0_ALERT_ENABLED` przez ≥10 min = composite failure mode niewidoczny.

## Cross-Refs

- **Lekcja #71** — test isolation `isolated_shift_state()` fixture pozwala na per-flag test bez cross-test contamination → enables checkbox 4 z Wytyczna #1.
- **Lekcja #58** — Z2 supremacy quality > deadline; flag-based design "wolniejszy" o 30 min ale eliminuje incident response 30+ min.
- **Lekcja #34** — restart-in-peak hard rule WYJĄTEK gdy Ziomek z bugiem bezużyteczny. Komplementarne do tej lekcji: granular flag rollback eliminuje większość restart-in-peak scenariuszy (rollback 5s przez flag flip = no restart needed dla większości regression cases).
- **TASK B Phase 1+2** — flag-based shift notifications (`SHIFT_*` 5 flag) enabled live deploy bez incident; Phase 0 default OFF → Phase 1 partial → Phase 2 full sequential.
- **Issue #1 hot-reload** — flag config NIE w code; `*.json` config file watch + reload; LIVE evidence że hot-reload ≠ restart.

## Operational Impact

- Peak blackout window NIE blokuje deploy gdy default-OFF + flag-flip post-peak.
- Rollback budget 5s × N flags << 30 min restart-cycle revert.
- Adrian ACK gates per-flag (NIE per-deploy) = finer-grained control + lower psychological cost.

## Implementation Checklist (template)

Przy każdej nowej feature dodać:
- [ ] Flag w config (default `false`)
- [ ] Hot-reload mechanism (file watch / signal / endpoint)
- [ ] Test `flag=false` ścieżki (no-op smoke)
- [ ] Test `flag=true` ścieżki (happy path)
- [ ] Test mid-state (flag flipped during operation — fallback graceful?)
- [ ] Rollback drill — flip back, verify within 5s
- [ ] Observability — log każdy flag flip event z timestampem
