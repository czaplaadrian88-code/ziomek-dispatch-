# FAZA E — adwersaryjna weryfikacja (lens B) ROOT: carried-first-guard-empty-env-void

**WERDYKT: CONFIRMED** · is_really_source=TRUE · is_really_open=TRUE · KLASY D,E · R3 · (severity P1→sporna, patrz dissent)

## Co twierdzi root
Strażnik #1 (`tools/carried_first_guard.py`) biegnie z PUSTYM env systemd → reużyte funkcje silnika
(`plan_recheck._start_anchor`, `_apply_canon_order_invariants`) czytają 14 route/canon flag jako default-OFF
(os.environ at-import, module-globals) → okrojony kanon, `no_position` dominuje ~87%.

## Dowody (4 niezależne metody)

### M1 — konfiguracja systemd (env)
- Guard: `systemctl show -p Environment dispatch-carried-first-guard.service` → **`Environment=` (PUSTE)**, brak `.d/`, brak EnvironmentFile.
- Silnik ŻYWY: `dispatch-panel-watcher.service` (ACTIVE) + `dispatch-plan-recheck.service` →
  `ENABLE_GPS_FREE_ANCHOR=1`, `ENABLE_GPS_FREE_ANCHOR_LAST_POS=1`, `ENABLE_PLAN_CANON_ORDER_INVARIANTS=1`,
  `ENABLE_CARRIED_FIRST_RELAX=1`, `ENABLE_NONCARRIED_DROPOFF_REORDER=1` (drop-iny gps-free-anchor.conf itd.).
- Flagi czytane module-level: `ENABLE_GPS_FREE_ANCHOR = os.environ.get(...) == "1"` (plan_recheck.py:347 i okolice),
  `_start_anchor` (554-594) czyta bare-name → pusty env = OFF. **DIVERGENCJA env potwierdzona.**

### M2 — empiryczny jsonl (NIEZALEŻNY od configu) — najmocniejszy
`carried_first_guard.jsonl` (1317 rek. dziś 07:08–20:17):
- no_position **88.5%** (1165), ok 8.4%, plan_invalidated 2.4%, canon_divergence 0.6%, carried_first 0.1%.
- **Spośród 152 rekordów gdzie guard ROZWIĄZAŁ pozycję: 100% `pos_source=gps_pwa`, ZERO przez last_event/committed_pickup/gps_stale/last_known_pos.**
  To NIEMOŻLIWE przy flagach ON dla floty „z założenia bez GPS" → empiryczny dowód, że guard biegnie flags-OFF
  i jest strukturalnie ślepy na całe GPS-free kotwiczenie, którego silnik używa. `_start_anchor` przy `not ENABLE_GPS_FREE_ANCHOR`:
  `return gps if has_gps else None` → bez świeżego GPS = None = no_position.

### M3 — log silnika (cross-ref)
`plan_recheck.log`: `START_ANCHOR_LAST_POS cid=207` (06-28) — **cid=207 to jeden z 11 no_position kurierów guardu** (69 rek.).
Silnik rozwiązał last_known_pos tam, gdzie guard mówił „no_position". 11/11 no_position kurierów MA żywy plan ze stopami
w courier_plans.json (silnik miał kotwicę by go zapisać).

### M4 — rozkład godzinowy (obala „artefakt końca dnia")
no_position dominuje przez CAŁY aktywny dzień: 07h=100%, peak-lunch 10–15 UTC = **79–92%**, 16–20 UTC = 100% (GPS PWA znika).
`resolved_gps` tylko 08–15 UTC (garstka świeżo-GPS kurierów), po 16 UTC = 0. **cid=179: 166 rek. WSZYSTKIE no_position,
span 09:45–18:20 (~8,5h ciągłego multi-baggingu)** — guard ślepy na tego kuriera przez cały dyżur, podczas gdy silnik go trasował (plan, 29 oids).

## Dlaczego CONFIRMED (refutacja lens B nie obala)
- NIE inertne/latentne: timer ENABLED, OnUnitActiveSec=3min, manifestuje co tick TERAZ.
- NIE za flagą-OFF: defektem SĄ flagi-OFF; instrument biega live.
- Fix NIE net-szkodliwy: dziedziczenie env silnika (drop-in / wspólne źródło stanu-flag) = ściśle poprawia zgodność guard↔silnik.
- Guard łamie WŁASNY kontrakt z docstringa („liczy IDENTYCZNIE jak silnik") → klasa D/E (instrument ≠ źródło-decyzji).
- Ślepy dokładnie na GPS-free flotę = tam gdzie carried-first historycznie gryzł (np. „Adrian R 400 bez GPS").

## DISSENT (uczciwie — co osłabia, ale NIE obala source/open)
1. **Zero wpływu na DECYZJE live** — read-only detektor, żadne zlecenie/kurier źle nie wysłane. P1 sporne vs P2 (to przyrząd, nie ścieżka decyzji).
2. **„fikcja" niezbyt trafne** — no_position to UCZCIWY brak-fabrykacji (lekcja served_synthetic chwali no_position jako poprawną etykietę przy braku kotwicy); to flag-stłumiona pod-rozdzielczość, nie zmyślone dane.
3. **Alarm risk ZACHOWANY** — no_position I plan_invalidated oba risk=True (1198/1317=91% risk). Nagłówkowy sygnał „skok reżimu ryzyka" wciąż strzela; tracona jest DROBNA kontrola kanonu carried-first.
4. **Snapshot końca dnia**: 94,2% no_position rek. ma plan AKTUALNIE invalidated → flags-ON przeetykietowałby je plan_invalidated (też risk, też BEZ canon-checku). Czysta strata carried-first-checku = ~5,8% rek. wg snapshotu (ALE M4 pokazuje, że w godzinach aktywnych plany bywały ważne → strata realnie większa niż 5,8%).
5. **Nie odpalono oracle na tym-samym-snapshocie peak** (0 multi-bag kurierów o 22:36, brak archiwum GPS per-tick) — magnituda redukcji no_position pod flags-ON jest dowodzona pośrednio (M2/M3/M4), nie bezpośrednim replayem.

## Fix (consolidation_target)
Przyrząd reużywający funkcje silnika MUSI dziedziczyć env silnika (drop-in / jawny config), nie pusty default.
Jedno źródło stanu-flag dla N procesów (guard, plan-recheck, panel-watcher) — zgodne z K1/F-fundament audytu zunifikowanego.
