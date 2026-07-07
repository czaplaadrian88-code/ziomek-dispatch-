# SPRINTY WIELOAGENTOWE 27 + 28 — plan bezkolizyjny (2026-07-07 wieczór)

**Kontekst:** po sprincie multiagentowym 07.07 (7/7) + flipie O2-K1 (LIVE 21:05). Master=origin `d808808`. Kolejny krok: dwa RÓWNOLEGŁE fronty, rozłączne, wieloagentowe. Protokół #0 obowiązuje; każdy flip/restart/merge = osobny ACK Adriana. Priorytety P1→P6 [[priorytety-stabilnosc-jakosc-skala]].

---

## 0. ZASADA BEZKOLIZYJNOŚCI 27 ↔ 28 (dlaczego mogą iść naraz)

| Zasób | Sprint 27 (silnik) | Sprint 28 (narzędzia) |
|---|---|---|
| `route_simulator_v2` / `plan_recheck` / `route_order` / `route_podjazdy` | ✏️ DOTYKA | ⛔ nie |
| `core/candidates` / `calib_maps` / `eta_*` / `shadow_dispatcher` (serializer) | ✏️ DOTYKA | ⛔ nie |
| `tools/*` / `canon_static_check` / `world_replay_gate` / `scheduled_flip_gate` / perf harness | ⛔ nie | ✏️ DOTYKA |
| `tests/*` | test_route_order_*, test_eta_* | test_inv_*, test_canon_*, test_perf_* (rozłączne nazwy) |
| `ZIOMEK_INVARIANTS.md` | ⛔ nie | ✏️ DOTYKA |
| **`flags.json` + restarty** | ✏️ **jedyny FLIPMASTER (27-A)** | ⛔ **NIE dotyka** |

→ **Rozłączne pliki + jeden pisarz flag = 27 i 28 równolegle bez kolizji.** Buildy w worktree (ADR-007; po sprincie `git worktree remove` — [[feedback-worktree-shared-pkgroot-false-fails]]). Peak: bez restartów; flipy off-peak.

---

## 🏗️ SPRINT 27 — „Domknięcie fundamentu + realizm ETA" (silnik; flipy za ACK)
**Cel:** włączyć zbudowane fundamenty (route-order) i poprawić realizm obietnicy (conditional-ETA), sekwencyjnie za bramkami + ACK. Dźwignia: P3 (stabilność kolejności) + jakość.

### 27-A — FLIPMASTER / dyżur na żądanie (①) — 1 agent, wyłączność flags.json
- Zamknięcie werdyktu **O2-K1** (monitor at-212 + 2 dni obs → wpis do memory).
- **route-order:** po dowodzie replay „0-diff" (27-B) → flip `ENABLE_ROUTE_ORDER_UNIFIED` za ACK (off-peak, 1 restart plan-recheck+shadow), monitor.
- **O2-K2** `ENABLE_SLA_GATE_READY_ANCHOR`: po O2-K1 ON (✓) + L3 ≥2d (~08.07 12:35) + ACK. Parytet picku już MEASURED.
- **conditional-ETA wpięcie w obietnicę:** po oknie 2d (~09.07) + HTML-escape fix (27-C) + karta dowodowa +5,14% + ACK.
- **DoD:** każdy flip = ACK + backup + monitor 1h + rollback gotowy. Runbook: `PAS0_FLIPMASTER_RUNBOOK.md` (rozszerzyć o route-order + conditional-ETA).

### 27-B — Route-order: dowód do flipu + mapa migracji (worktree)
- **Replay „0-diff"** na korpusie/oknie 2 dni: `ENABLE_ROUTE_ORDER_UNIFIED` ON vs OFF = identyczny porządek na żywych danych (nie tylko golden). To dowód „warto+bez regresji" (tu: warto = jedno źródło; bez regresji = 0 diff).
- **Mapa migracji pozostałych 4 luster** (bez wykonania): K3 `fleet_state._build_route` (panel), K4 `RouteLogic.kt` (apka), 5. prymityw `courier_api._repair_dropoffs_after_pickups(kind_key)`, pogłębienie K2 (`_apply_canon_order_invariants`). Każde: kontrakt parytetu + ryzyko + kolejność.
- **DoD:** raport z dowodem 0-diff + mapa migracji; gałąź route-order gotowa do flipu (już zmergowana? nie — najpierw ten dowód, potem merge+flip za ACK). ZERO merge/flip.

### 27-C — Conditional-ETA: fix u źródła + karta wpięcia (worktree)
- **HTML-escape fix (znalezisko A3):** 3 restauracje (`Sweet Fit &amp; Eat` itd.) gubią warstwę restauracji w lookupie mapy komórek. Fix u źródła: odescape `result.restaurant` na wejściu `calib_maps.eta_cell_residual_correct`. Test ON≠OFF (pokrycie 31/208 wraca). 
- **Karta wpięcia w obietnicę:** dowód +5,14% MAE na oknie 2d (do ~09.07) — hold-out, CI nieobejmujące 0, breach bez wzrostu; przygotować flip `ENABLE_ETA_CELL_RESIDUAL_CORRECTION` (OFF→shadow→ON) do ACK. ⚠ oś OBIETNICY, feasibility NIETKNIĘTE.
- **DoD:** fix + test + karta dowodowa; ZERO flip (to 27-A za ACK po 09.07).

### 27-D — Pomiary read-only (2-3 agentów, 0 kolizji)
- O2-K1 werdykt 2 dni (bias/regres z shadow po flipie).
- O2-K2 parytet re-pomiar po kolejnych peakach (korroboracja n).
- Świeża mapa 0a `eta_truth_map --since 2026-07-02T12:00` (miarodajna od ~10.07 — Fala A deep-dive).
- **DoD:** raporty do eod_drafts.

---

## 🧹 SPRINT 28 — „Odporność, higiena, skala" (narzędzia/testy/perf; ZERO silnika decyzyjnego)
**Cel:** dług strukturalny + odporność + perf pod skalę. Dźwignia: P3 (higiena) + P4 (perf) + P5 (ops). **Zero ryzyka dla żywego silnika — nie dotyka decyzji.** Może startować OD RAZU (brak bramek czasowych).

### 28-A — Hardening checkerów na worktree (worktree) — fix u źródła lekcji dziś
- **`.claude` → `_EXCLUDE_DIRS`** w `canon_static_check.py` + analogiczny fix w `test_decide_facade_k09` + `test_tz_zoneinfo_consolidation` (skanują repo, liczą sąsiednie worktree jako duplikaty → fałszywe faile). Żeby PRZYSZŁE sprinty w worktree nie generowały szumu ([[feedback-worktree-shared-pkgroot-false-fails]] korzeń #2).
- Rozważ `test_courier_reliability`/`test_a2_selection_shadow` `REPO=parents[2]` hardcode → odporna detekcja repo-root (korzeń #3).
- **DoD:** worktree, regresja zielona, dowód: te 4 checkery przechodzą NAWET z aktywnym sąsiednim worktree.

### 28-B — Pozostałe inwarianty (worktree) — dług P3
- Dozbroić kolejne puste sloty `ZIOMEK_INVARIANTS.md`, które NIE wymagają zmiany silnika (B2 zrobił 3; reszta = xfail-ratchety). Priorytet: te domykalne bez ruszania decyzji. Każdy: mutation-probe RED.
- **DoD:** worktree, regresja zielona, dashboard zaktualizowany, lista „zostaje 🔴 bo wymaga silnika" (→ backlog 27+).

### 28-C — Monitory / world-replay (worktree) — czystość sygnału
- **Schema-aware bucket wr0** w `world_replay_gate.py` (A2 opcjonalny): rekordy `schema=wr0` → „pominięte" zamiast fałszywej „ROZNICA-KRYTYCZNA". Wycisza 12 fałszywych alarmów (znikną same po dobie, ale trwały fix = odporność).
- Domknięcie reszty fixu (0,0)-coords jeśli B1 zostawił niejednoznaczne (diagnoza była: chokepoint — potwierdzić że guard wystarcza).
- **DoD:** worktree, world_replay_gate czysty na wr1, regresja zielona.

### 28-D — Perf peak p95 (read-only + ewentualny worktree) — P4 skala
- Pomiar ogona **peak p95** (osobne źródło niż naprawiony p50; L0.1 „compute-zawsze" z listy) — read-only harness na żywych danych peaku.
- Rekomendacja (bez flipu): gdzie ogon peaku, czy OR-Tools sufit na 4 vCPU, co przed multi-city.
- **DoD:** raport perf + rekomendacja; ZERO zmian.

---

## 📋 REKOMENDACJE — CO DALEJ (kolejność dźwigni)

1. **Sprint 28 startuje OD RAZU** (równolegle) — zero ryzyka, zero bramek, domyka dług P3/P4 + odporność. Najlepszy „bezpieczny front" na wieczór/noc.
2. **Sprint 27 startuje na tym, co gotowe teraz** (27-B replay route-order, 27-C HTML-escape fix, 27-D pomiary). Flipy (27-A) czekają na okna: L3 ~08.07 → O2-K2; okno ETA ~09.07 → conditional-ETA; route-order flip → po dowodzie 0-diff.
3. **Największa dźwignia P3:** route-order włączenie (jedno źródło kolejności = koniec „carried-first 10×"). To domknięcie fundamentu — priorytet w 27.
4. **Poza kodem, najpilniejsze:** **rozdystrybuowanie apki 5b** (Adrian) — odblokowuje werdykt 5b → feas_carry → dowód pod autonomię (P6). Bez tego cała gałąź autonomii stoi.
5. **NIE forsować autonomii (P6)** przed 5b + fundamentami — na niestabilnym gruncie = mnożnik błędów (zasada z priorytetów).
6. **Gold furtka** — decyzja biznesowa Adriana, nie kod; odłożona świadomie.
7. Po 27+28: **Fala A deep-dive** (~10.07, świeża mapa 0a) — kalibracja odbioru per segment + wariancja dostawy.

## CO WYMAGA ACK PRZED URUCHOMIENIEM
- **GO na uruchomienie** 27 i/lub 28 (ilu agentów, który front).
- Flipy w 27 (route-order / O2-K2 / conditional-ETA) = każdy osobny ACK przy dojrzałej bramce.
- Merge gałęzi 28 (higiena) do master = ACK po przeglądzie (jak dziś B).

Powiązane: [[ziomek-status-konsolidacja-2026-07-05]] · [[shadow-jobs-registry]] · [[ziomek-advisory-tura1-tura2-exec-2026-07-07]] · ADR-007.
