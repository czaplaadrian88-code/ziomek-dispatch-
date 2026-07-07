# HANDOFF — tmux 30 = WYKONAWCA: odbudowa + migracja KOLEJNOŚCI TRASY (jedno źródło)

**Jesteś wykonawcą.** Koordynator = główna sesja Claude (Adrian). **START:** `dispatch_v2/CLAUDE.md` (🧭 START TUTAJ + Przykazanie #0) → `docs/CODEMAP.md`. venv: `/root/.openclaw/venvs/dispatch/bin/python`. **READ FIRST przed czymkolwiek:** `eod_drafts/2026-07-07/S27B_routeorder_proof.md` (mapa migracji 5 luster + kolejność — Twój fundament) + `SPRINTY_27_28_PLAN.md`.

## ⚠ KONTEKST KRYTYCZNY (przeczytaj, oszczędzi Ci błędu)
Poranny agent „C" zbudował moduł `route_order.py`, ale **go NIE zacommitował** i przy sprzątaniu worktree został skasowany (`--force`) — **modułu NIE MA, nie szukaj go**. To NIE jest problem: **`route_podjazdy.py` (już w master, `order_podjazdy` l.190) JEST czystym wspólnym źródłem** kolejności (OSRM-free, deterministyczne, pinowane goldenem, używane przez apkę). 27-B ustalił: **`route_order.py` ma być PROMOCJĄ `route_podjazdy`, nie nowym konkurentem** (inaczej robisz 2. kopię reguły = anty-cel).

## ⛔ REGUŁY
- **ZERO flip / merge-do-master / restart / Telegram / flags.json bez ACK Adriana** (przez koordynatora). Worktree per zadanie, **commituj pracę do gałęzi PRZED końcem** (lekcja: niezacommitowane ginie), sprzątaj worktree po sobie.
- Regresję oceniaj po **LIŚCIE ID faili PRE vs POST** (memory [[feedback-worktree-shared-pkgroot-false-fails]]). Fix u źródła nie objaw.
- **Twój zakres = KOLEJNOŚĆ przystanków.** NIE dotykaj: `calib_maps`/`eta_*`/predykcji czasów (**tmux 29 kalibracja ETA**), `feasibility_v2`-filtra/`route_simulator`-pruningu (**tmux 31 perf**). Bezkolizyjność.

## ZADANIA (kolejność wg 27-B — dźwignia P3: koniec „carried-first naprawiane 10×")
1. **Zamroź kontrakt** (już w goldenie: `PICKUP_MERGE_MIN=10`, coverage-gate, carried-first + committed-ascending + no-return).
2. **Promuj `route_podjazdy` → `route_order.py`** (pure, flaga `ENABLE_ROUTE_ORDER_UNIFIED` OFF=bajt-identyczne) + **dowód 0-diff** (replay ON vs OFF na żywym korpusie 2d = identyczna projekcja `[(typ, sorted(order_ids))]`). To dowód „warto (jedno źródło) + bez regresji (0 diff)".
3. **Migruj D — apka backend** `courier_api/courier_orders.py` (3 gałęzie build-view; skonwerguj bliźniaka `_repair_dropoffs_after_pickups(kind_key)` z `plan_recheck.py:1203`) — ten sam repo/venv = najtaniej.
4. **Migruj B — panel** `nadajesz_clone/.../fleet_state._build_route` OSTATNIE (⚠ CROSS-REPO, osobny deploy, [[feedback-multisession-shared-deploy]], równoległe sesje CC — tylko *kolejność* deleguje, ETA/floory zostają lokalne; live-parity + monitor = siatka).
5. **A zostaje źródłem** — `plan_recheck._apply_canon_order_invariants` pisze plan; relax/lex/no-return = logika DECYZYJNA (R6/SLA), **NIE renderer** — nie ruszać.

**DoD:** moduł + dowód 0-diff + migracje D (i przygotowanie B) w worktree, flaga OFF, ZERO merge/flip. Raporty → `eod_drafts/2026-07-07/S30x_*.md`. Zgłoś koordynatorowi „GOTOWE do ACK" gdy dowód 0-diff jest twardy. Flip/merge = następny cykl za ACK Adriana.