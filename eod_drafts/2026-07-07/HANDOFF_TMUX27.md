# HANDOFF — tmux 27 = WYKONAWCA Sprintu 27 (silnik: kolejność trasy + realizm ETA)

**Jesteś wykonawcą.** Koordynator = główna sesja Claude (Adrian steruje przez nią). **START:** `dispatch_v2/CLAUDE.md` (🧭 START TUTAJ + Przykazanie #0) → `docs/CODEMAP.md` → nie skanuj repo. **Pełny plan + zasady bezkolizyjności:** `eod_drafts/2026-07-07/SPRINTY_27_28_PLAN.md` (READ FIRST). venv: `/root/.openclaw/venvs/dispatch/bin/python`.

## ⛔ REGUŁY (bez wyjątków)
- **ZERO flip / restart / merge-do-master / Telegram bez jawnego ACK Adriana** (przez koordynatora). Protokół #0 ETAP 0→7.
- **`flags.json` NIE dotykaj** — jest jeden flipmaster; Ty tylko czytasz (27-A).
- **tmux 28 robi narzędzia/testy** (`tools/*`, checkery, `world_replay_gate`, perf). **NIE wchodź w te pliki** — to gwarancja bezkolizyjności 27↔28. Ty ruszasz TYLKO silnik/predykcję/kanon.
- **Worktree per zadanie** (ADR-007). Oceniaj regresję po **LIŚCIE ID faili PRE vs POST** — ~25 faili `canon_static_check`/`decide_facade`/`tz`/`courier_reliability`/`a2_selection` = ARTEFAKT WORKTREE (na kanonie przechodzą; memory [[feedback-worktree-shared-pkgroot-false-fails]]). **Sprzątaj worktree po sobie** (`git worktree remove`).
- Raporty → `eod_drafts/2026-07-07/S27x_*.md`. Gdy zadanie gotowe do decyzji → zgłoś koordynatorowi „GOTOWE do ACK".

## TWOJE 4 ZADANIA (rozłączne pliki)

### 27-A FLIPMASTER (read-only, NIE flipuje teraz)
Zamknij werdykt dzisiejszego **O2-K1** (flip 19:05: `flip_o2k1_20260707.log` + `monitor_o2k1_20260707.log` z at-212 20:10 + journalctl dispatch-shadow błędy + czy `o2_capz` się odpala). Rozszerz `PAS0_FLIPMASTER_RUNBOOK.md` o 2 procedury: **route-order** (`ENABLE_ROUTE_ORDER_UNIFIED`; wymaga 1 restart plan-recheck+shadow, nie hot) i **conditional-ETA** (`ENABLE_ETA_CELL_RESIDUAL_CORRECTION`; HOT). Tabela gotowości. → `S27A_o2k1_verdict.md`.

### 27-B Route-order — dowód 0-diff + mapa migracji (worktree)
Kod jest na gałęzi `worktree-agent-a8a36495468ae05f0` (moduł `route_order.py`; NIE w master). Wciągnij: `git checkout worktree-agent-a8a36495468ae05f0 -- route_order.py route_podjazdy.py plan_recheck.py tests/test_route_order_unified_parity.py`. Zrób **replay 0-diff na żywym korpusie 2 dni** (ON vs OFF = identyczna kolejność — dowód do flipu ETAP 5) + **mapę migracji** K3 panel / K4 apka Kotlin / 5. prymityw apki / pogłębienie K2. → `S27B_routeorder_proof.md`.

### 27-C Conditional-ETA — fix u źródła + karta (worktree)
Fix HTML-escape (znalezisko A3: `Sweet Fit &amp; Eat` itd. gubią warstwę restauracji — odescape `result.restaurant` PRZED lookupem w `calib_maps.eta_cell_residual_correct`, u źródła) + test ON≠OFF (pokrycie 31/208 wraca) + **karta wpięcia** w obietnicę (dowód +5,14% MAE, okno domyka ~09.07, flaga OFF→ON za ACK). ⚠ oś OBIETNICY, feasibility NIETKNIĘTE. → `S27C_eta_fix.md`.

### 27-D Pomiary (read-only)
Wczesny wpływ O2-K1 na żywo (applied%/regres z shadow) · re-pomiar O2-K2 parytet (`python -m dispatch_v2.tools.o2_k2_pick_parity`, cwd=scripts/) · mapa 0a `eta_truth_map --since 2026-07-02T12:00` (za wcześnie <7d → oznacz). → `S27D_*.md`.

**Kolejność sugerowana:** 27-A + 27-D od razu (read-only), 27-B i 27-C w worktree równolegle (rozłączne: B=route_order/podjazdy/plan_recheck, C=calib_maps/serializer). Flipy — dopiero gdy okna dojrzeją + ACK.
