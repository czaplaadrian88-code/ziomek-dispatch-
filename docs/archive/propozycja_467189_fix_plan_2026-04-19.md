# Fix plan #467189 proposal selection — V3.16

## Wybór strategii: **Opcja C — restrict no_gps empty bag elevation**

**Uzasadnienie (evidence-based):**
- 7/18 (39%) PANEL_OVERRIDE proposed=413 Mateusz O — **konkretny wzorzec**
- Strukturalna asymmetria: scoring.py nagradza empty bag (100 punktów wagi) bez penalty dla no_gps (synthetic BIALYSTOK_CENTER)
- Fix punktowy, nie ruszam scoring.py (Opcja C = post-scoring demotion)
- Zero konfliktu z C5 wave_scoring (wave pracuje przed final pick)

**Opcja A odrzucona:** panel_packs fallback już w V3.15 — nie dotyczy tego bugu (bug po V3.15 deploy).

**Opcja B odrzucona:** reward bundle aggressive może wpłynąć na wave_scoring → STOP + ACK trigger (Sprint C boundary).

**Opcja D odrzucona:** C8 pickup_span relaxed = feasibility_v2 edit → STOP + ACK trigger.

## Pliki (2 core + 1 test)

| # | Plik | Zmiana | LoC |
|---|---|---|---|
| 1 | `common.py` | Flag `ENABLE_NO_GPS_EMPTY_DEMOTE=True` + env override | +10 |
| 2 | `dispatch_pipeline.py` | Post-scoring demotion logic (przed final best pick) | +25 |
| 3 | `tests/test_proposal_selection_v316.py` | 6+ tests | +200 (new) |

## Mechanizm

Po finalnym sortowaniu candidates przez score, zanim pick top-1:

```python
if ENABLE_NO_GPS_EMPTY_DEMOTE and candidates:
    top = candidates[0]
    is_blind_empty = (top.pos_source == "no_gps" 
                      and top.bag_size == 0)
    if is_blind_empty:
        # Sprawdź czy istnieje alt candidate z GPS/bag i feasibility OK/MAYBE
        has_better = any(
            c.pos_source in ("gps", "last_assigned_pickup", 
                             "last_picked_up_delivery", 
                             "last_picked_up_recent", "last_delivered")
            and c.feasibility in ("OK", "MAYBE")
            for c in candidates[1:]
        )
        if has_better:
            # Demote: move top to after first GPS/bag candidate
            # (NIE ekskluduj — zostaw w liście jako ALT)
            reordered = (
                [c for c in candidates[1:] 
                 if c.pos_source != "no_gps" or c.bag_size > 0]
                + [top]
                + [c for c in candidates[1:]
                   if c.pos_source == "no_gps" and c.bag_size == 0]
            )
            log SOURCE_DEMOTE event
            candidates = reordered
```

**Guard**: jeśli wszyscy są no_gps empty (empty shift, brak GPS) → nie demote (no_fallback mode).

## Feature flag

```python
ENABLE_NO_GPS_EMPTY_DEMOTE = _os.environ.get("ENABLE_NO_GPS_EMPTY_DEMOTE", "1") == "1"
```

Default True, env `ENABLE_NO_GPS_EMPTY_DEMOTE=0` → rollback do pre-V3.16 behavior.

## Interakcja V3.12/V3.13/V3.14/V3.15

- **V3.12 (city)**: zero konfliktu, geocoding niezmienione
- **V3.13 (PIN-space)**: Mateusz O cid=413 (real, nie PIN), niezmienione
- **V3.14 (TTL)**: no_gps+bag=0 to świeży scenario, TTL nie aktywne
- **V3.15 (packs fallback)**: post-V3.15 bag catchup → wrong top-1 cnt się zmniejszy; V3.16 dorabia drugi layer defense

**Zero Sprint C (C1-C7) dotknięcia.**

## Kolejność commitów + rollback tagi

```
step 1 → common.py flag                tag: fix-propsel-flag-committed
step 2 → dispatch_pipeline.py demote   tag: fix-propsel-demote-committed  
step 3 → tests                          tag: fix-propsel-tests-committed
step 4 → docs + master                  tag: f22-proposal-selection-fix-live-V3.16
```

## Tests plan (KROK 4)

6+ asserts:
1. `test_regression_467189_mateusz_demoted` — fixture z propozycji #467189, Mateusz O demotowany, Gabriel (z bagiem) top-1
2. `test_no_gps_empty_bag_not_top_when_alt_with_gps_exists` — parametryzowany, 5 scenariuszy
3. `test_no_gps_empty_remains_top_when_all_blind` — wszyscy no_gps empty → pierwszy (nie degradacja)
4. `test_no_gps_with_bag_preserved` — no_gps z bagiem NIE demotowany (tylko bag=0)
5. `test_gps_empty_bag_not_demoted` — GPS z empty bag nie dotknięty (tylko no_gps+empty)
6. `test_flag_false_disables_demotion` — legacy behavior preserved
7. `test_v12_v15_preserved` — smoke all V3.12-V3.15 tests PASS

## Deploy plan

Pre-deploy: py_compile + import + **220/220 + 6/6 PASS**
1. Restart `dispatch-panel-watcher.service`
2. Restart `dispatch-shadow.service`
3. **`dispatch-telegram.service` NIE tknięte**

Live verification min 15 min — monitor PANEL_OVERRIDE rate, top-1 pos_source distribution.

## Risk/rollback

- Step 1 (flag): zero risk
- Step 2 (demote logic): sum risk średni — **but guard "all blind" chroni edge case**
- Step 3-4: zero risk

Rollback: `ENABLE_NO_GPS_EMPTY_DEMOTE=0` env + restart.

## Hard constraints

- ❌ dispatch-telegram NIE restartuję
- ❌ scoring.py nie tknięty (signal sign bezpieczny)
- ❌ wave_scoring.py + feasibility_v2 nie tknięte
- ✅ Flag default True + env kill-switch
- ✅ V3.12-V3.15 rozszerzone, nie zastępowane
- ✅ learning_log schema niezmieniona

## Idę — autonomic, bez STOP
