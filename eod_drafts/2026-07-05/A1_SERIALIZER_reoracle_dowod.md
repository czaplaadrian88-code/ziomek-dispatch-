# A1-SERIALIZER (Sprint 1, tmux 17) — dowód re-oracle C9 + mutation-probes — 2026-07-05

## Werdykt
**Serializer −38 kluczy: VOID → 🟢 TEST.** Kod naprawiony już w L1.1 (`85d92f7`, 01.07,
allowlista `_AUTO_PROP_PREFIXES` → deny-lista `_METRICS_EXCLUDE`); dispatch-shadow
restartowany 03.07 13:18 UTC (proces zawiera L1.1). Status VOID w dashboardzie był
**STALE** — pochodził z oracle 30.06 (przed L1.1), a formalne zdjęcie wymagało
re-oracle na świeżym oknie (adnotacja L1.2 w ZIOMEK_INVARIANTS l.42). Ten plik = to re-oracle.

**INV-FLAG-CONFTEST-STRIP: VOID → 🟢 TEST (ratchet).** Claim „naprawione 257d315" był
fałszywy (łatka na 3 instancje); klasa = flagi w flags.json poza
`ETAP4_DECISION_FLAGS`∪`FLAGS_JSON_NUMERIC_OVERRIDES`∪`TEST_ISOLATED_INFRA_FLAGS`
przeciekają żywą wartością do testów (subprocess script-runnery). Nowy strażnik
zamienia cichy przeciek w głośny fail: klasa NIE MOŻE UROSNĄĆ (baseline 134 znanych
survivors, kierunek tylko w dół); pełne zamknięcie (baseline→0) = praca 🔴
INV-FLAG-REGISTRY (~112 flag), świadomie POZA zakresem de-VOID.

## Re-oracle serializera (okno świeże: od restartu shadow 03.07 13:19 → 05.07 17:21, n=229 decyzji)
Klucze „ginące" wg audytu B07 (`0/858` i `0/2000` przed L1.1) — teraz:

| klucz | świeże okno | status |
|---|---|---|
| eta_source, pickup_dist_km, r6_soft_zone_active, c2_passes, c2_violations_count, c2_max_elapsed_min, c2_per_order_data_available, sla_minutes_used, cs_tier_label, cs_tier_bag, shift_start_min, shift_remaining_min, wave_bonus, r1_violation_km | **221/229** | PŁYNĄ (8 rekordów = early-path bez best) |
| sla_violations_blocking_count, sla_violations_pre_existing | 67/229 | płyną (warunkowe: tylko przy naruszeniach) |
| r6_gold4_gate_recovered | 14/229 | płynie (warunkowy) |
| r6_paczka_exempt_oids | 0/229 | warunkowy — writer `feasibility_v2:1151` pod `if r6_paczka_exempt_oids:` (paczki rzadkie) |
| d2_stale_schedule_soft, d2_soft_penalty | 0/229 | warunkowe — `feasibility_v2:684-685` tylko przy stale grafiku |
| fallback_strategy, mass_fail_ratio | 0/229 | warunkowe — `dispatch_pipeline:6148-6150` tylko w gałęzi V328 mass-fail |
| inv_feasibility_first_violation | 0/229 | pisany TYLKO True przy naruszeniu (`dispatch_pipeline:2561`) — 0 = zdrowo |
| eta_src, drive_source | 0/229 | **brak producentów w silniku** (grep całego repo — nazwy z ery audytu, nie strata serializera) |

Twin A+B potwierdzony w kodzie: LOCATION A `_serialize_candidate` (call l.474) i
LOCATION B `_serialize_result.best` (call l.860) wołają WSPÓLNY
`_propagate_prefixed_metrics` + `_json_safe`.

## Strażnicy (stan po sprincie)
- `tests/test_serializer_completeness_l11.py` (6) — LOCATION A funkcjonalnie + deny-lista z powodami + twin tekstualnie. (istniał, L1.1)
- **NOWY** `tests/test_serializer_location_b_parity.py` (4) — LOCATION B **funkcjonalnie** na realnym `PipelineResult` (audit-keys → out["best"], nowy klucz od urodzenia, parytet zbiorów kluczy A↔B, json-safety całego rekordu).
- **NOWY** `tests/test_conftest_flag_strip_guard.py` (3) — strip działa (0 pokrytych kluczy w kopii), strip nie mutuje niedecyzyjnych (bajt-w-bajt), ratchet klasy przeciekowej.

## Mutation-probes (wzorzec L7.3 — zęby udowodnione, wszystko przywrócone, git diff = 0)
1. `eta_source` → `_METRICS_EXCLUDE` (symulacja cichej dziury): **2 FAILED** (vanished-keys A + B). ✅
2. Wycięcie call LOCATION B (`_propagate_prefixed_metrics(out["best"],…)`): **5 FAILED** (twin + wszystkie 4 testy B). ✅
3. Usunięcie `ENABLE_PLAN_RECHECK_GATES` z `ETAP4_DECISION_FLAGS`: **ratchet FAILED** (new_leak wykryty). ✅
4. Fail-open `_stripped_flags_copy()` (return ""): **2 FAILED** (strip-removes + preserves). ✅

## Konsekwencja dla bramek
- Kalibracja O2 („napraw serializer PRZED", 02.07): podstawa pomiarowa serializera = ZDROWA od restartu 03.07 13:18. Rekordy sprzed tej daty nadal mają dziury — okna kalibracyjne O2 ciąć od 2026-07-03T13:19Z.
- Dashboard `ZIOMEK_INVARIANTS.md`: ⚠️VOID 4 → 2 (zostają: `carried_first_guard`, `global_allocate` geometria — Zadanie 2 Sprintu 1).

## Baseline testów
Przed sprintem: 1 failed / 4180 passed — fail = niehermetyczny `test_v3273_free_courier_wait_skip::test_kill_switch_restores_hard_reject`
(3. odsłona klasy „żywy zegar w replayu": niedzielny wieczór → 0 feasible → best-effort legalnie wybrał 470; asercja `best_cid != 470`
była nadasercją sprzeczną z always-propose). Naprawione u źródła commit `65d497c` (traffic-mult zamrożony na czas replayu + asercje
kompletne dla obu gałęzi). Po naprawie + nowych strażnikach: pełna regresja zielona (patrz tracker).
