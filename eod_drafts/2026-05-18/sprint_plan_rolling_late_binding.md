# Sprint plan — Rolling late-binding w produkcyjnym Ziomku

**Data:** 2026-05-18 | **Autor:** CC | **Status:** PLAN (przed ACK Adriana)

## Cel

Zlecenie elastyk nie jest wiązane z kurierem przy utworzeniu, lecz trafia do
**puli pending**, jest re-optymalizowane przy każdym nowym zleceniu, i **zamrażane
dopiero `odbiór − 15 min`**. Replay (harness `sequential_replay.py`, niedz. 17.05)
udowodnił: −51% best_effort (118→57), churn znośny (44% zleceń nie ruszone,
2% przeskakuje 3+×), wynik ląduje na realnej produkcji (14:00 = 38% vs real 29%).

## Co już istnieje (fundamenty — NIE budujemy od zera)

| Komponent | Stan | Rola w sprincie |
|---|---|---|
| `czasowka_scheduler.py` | LIVE | **Wzorzec referencyjny** — late-binding dla czasówek (timer 1 min, scan pending, triggery 60/50/40, EMIT/WAIT/FORCE/KOORD) |
| `pending_queue_provider.py` | C7 skeleton, flag `ENABLE_PENDING_QUEUE_VIEW` | Hook puli pending |
| `plan_manager.py` | LIVE (V3.19b) | Wzorzec persystencji stanu (atomic, fcntl) — baza pod tentative store |
| `postpone_sweeper.py` | LIVE | Wzorzec re-ewaluacji odłożonych + escalacja KOORD |
| `auto_proximity_classifier` | LIVE shadow, **skalibrowany 18.05** | Decyzja AUTO/ACK/ALERT w momencie zamrożenia |
| `sequential_replay.py` | LIVE, deterministyczny | **Offline test-bed** — walidacja każdej fazy PRZED shadow |

**Wniosek:** rolling late-binding to **uogólnienie wzorca czasówki na elastyki +
re-optymalizacja**, nie nowa architektura.

## Non-goals (świadomie poza scope)

- Wczesna widoczność zlecenia dla kuriera / pre-pozycjonowanie — kurier widzi
  dopiero zamrożone (lead 15 min). Osobny temat.
- Unifikacja ścieżki czasówek z elastykami — czasówki zostają na swojej ścieżce;
  unifikacja to późniejszy refaktor.
- Zmiany w scoringu / feasibility / OR-Tools — bez zmian.

## Architektura docelowa

```
panel_watcher → NEW_ORDER event
                     ↓
        ┌─ pending pool (pending_assignments.json, atomic) ─┐
        │  każde zlecenie: tentative_cid + freeze_at         │
        │                                                    │
   trigger A: nowe zlecenie → re-opt puli (lokalny:           │
              kurier obciążony nowym + jego nie-zamrożone)    │
   trigger B: timer 1 min → freeze sweep:                     │
              zlecenie z freeze_at <= now → finalna ocena →   │
              auto_route classifier → AUTO/ACK/ALERT → emit   │
        └────────────────────────────────────────────────────┘
                     ↓
        zamrożone → istniejąca ścieżka (shadow_decisions /
                    telegram_approver / panel assign)
```

`freeze_at = max(created, pickup_ready − 15min)`. Zamrożone = niezmienne.

## Fazy (shadow-first — zgodnie z metodyką Z2)

### Faza 0 — Fundament: pula pending + tentative store (~3-4 dni)
- `pending_pool.py` — persystencja puli (wzorzec `plan_manager`: atomic temp→fsync→rename,
  fcntl). Schema: `{oid: {tentative_cid, freeze_at, created_at, churn_count, frozen}}`.
- Zasilanie puli z `NEW_ORDER` (konsument event_bus, jak `shadow_dispatcher`).
- Reconciliation: zlecenie przypisane w panelu (panel_watcher) → usuń z puli.
- Freeze-clock: timer 1 min liczy `freeze_at`, jeszcze NIC nie emituje.
- **SHADOW:** produkcyjna ścieżka (immediate propose) bez zmian. Pula tylko obserwuje.
- **Gate 0:** 3 dni — pula spójna ze stanem panelu, zero zgubionych/ghost orderów.

### Faza 1 — Pętla re-optymalizacji (~2-3 dni)
- Trigger A: na `NEW_ORDER` → re-ocena lokalna (kurier który dostał tentative nowego
  + jego nie-zamrożone zlecenia) przez `assess_order`. Zapis tentative + churn.
- **Walidacja w harnessie PRZED shadow** — logika musi dać te same liczby co
  `sequential_replay --rolling` (deterministyczny → regресja bajt-w-bajt).
- **SHADOW:** tentatywne decyzje tylko logowane (`pending_pool_log.jsonl`).
- **Gate 1:** 5 dni shadow — rozkład churn zgodny z replay (44/47/6/2%),
  best_effort puli ≈ replay.

### Faza 2 — Freeze → emit (~3-4 dni + tydzień shadow)
- Trigger B: `freeze_at <= now` → finalna `assess_order` → `auto_proximity_classifier`
  → AUTO/ACK/ALERT. Emit przez ISTNIEJĄCĄ ścieżkę (shadow_decisions → telegram).
- Hard fallback (wzorzec czasówki): zlecenie bez decyzji a `pickup − 5min` →
  FORCE_EMIT/KOORD. Scheduler-dead → watchdog alert.
- Flaga `ENABLE_ROLLING_LATE_BINDING`: OFF = immediate propose (dziś), ON = freeze-time propose.
- **SHADOW (flaga OFF):** porównanie freeze-time vs immediate propose na tych samych
  zleceniach — czy freeze-time bije immediate (mniej best_effort, lepszy kurier).
- **Gate 2:** tydzień shadow + ACK Adrian — freeze-time ≥ immediate na każdej metryce.

### Faza 3 — Flip + integracja z Faza 7 AUTO (~1-2 tyg ramp)
- Flip `ENABLE_ROLLING_LATE_BINDING=true` — freeze-time staje się ścieżką główną.
- AUTO w momencie zamrożenia = auto-assign (gdy Faza 7 `AUTO_PROXIMITY_ENABLED`).
- Ramp jak Faza 7: 30% → 70% → 100% zleceń, gate per próg.
- **Zależność:** Faza 3 wymaga Faza 7-AUTO-PROXIMITY poza shadow. Fazy 0-2
  niezależne — można robić równolegle do ramp-u Faza 7.

## Ryzyka i mitygacje

| Ryzyko | Mitygacja |
|---|---|
| Scheduler pada → zlecenia nieobsłużone w puli | Watchdog + hard fallback FORCE_EMIT na `pickup−5min` (wzorzec czasówki) |
| Panel/człowiek przypisze zlecenie z puli | Reconciliation z panel_watcher — każdy tick usuwa przypisane z puli |
| Re-opt per nowe zlecenie za ciężki w peaku | Lokalny re-opt (tylko obciążony kurier), nie cała pula — sprawdzone w harnessie |
| Restart traci pulę | Atomic persist `pending_assignments.json` (wzorzec plan_manager) |
| Rolling nie zniesie szczytu 14:00 (38%) | To capacity-bound, znane — rolling to nie naprawia, decyzja biznesowa osobno |
| Churn widoczny dla kuriera | Brak — kurier widzi tylko zamrożone; tentative re-opt jest wewnętrzny |

## Estymata

| Faza | Robota | Shadow/gate | Kalendarz |
|---|---|---|---|
| 0 | 3-4 dni | 3 dni | ~1 tydz |
| 1 | 2-3 dni | 5 dni | ~1.5 tydz |
| 2 | 3-4 dni | 7 dni | ~2 tydz |
| 3 | ramp | per próg | 1-2 tydz |
| **Razem** | | | **~5-6 tyg kalendarzowo** (Fazy 0-2 ~3.5 tyg do flipu) |

AIDER routing: Faza 0/1 persystencja+scheduler (repetytywne, wzorce istnieją) →
kandydat AIDER. Faza 2/3 integracja + classifier coupling → SELF.

## Rollback

- Per faza: flaga OFF (`ENABLE_PENDING_QUEUE_VIEW` / `ENABLE_ROLLING_LATE_BINDING`)
  → natychmiastowy powrót do immediate-propose, pula staje się no-op.
- Granularne tagi git per faza jako punkty rollback.
- Pula pending = warstwa równoległa do Fazy 2; do flipu zero wpływu na produkcję.

## Gate wejściowy (przed Faza 0)

Decyzje Adriana wymagane: (1) priorytet vs bieżące sprinty (ETA calib / OBJ F2-F4 /
Faza 7 ramp), (2) lead zamrożenia 15 min — potwierdzić czy nie 10/20, (3) czy czasówki
zostają osobno (rekomendacja: tak, w tym sprincie).
