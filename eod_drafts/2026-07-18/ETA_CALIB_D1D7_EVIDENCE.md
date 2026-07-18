# Sprint ETA-CALIB D1-D7 (2026-07-18 wieczór) — EVIDENCE

GO Adriana = odpowiedź na kartę: **„D1a, D2a, D3a, D4 ok, D5 ok, D6a, D7 zgoda"**
(OWNER_CONFIRMED; rekord: memory `owner-decision-eta-calib-d1-d7-2026-07-18`).
Protokół #0; scope-mapa skilla (klasy: nowa-flaga + artefakt-werdykt + r6-progi N-D).

## ETAP 0

- Baseline PRZED: **5173/0** (log etaserve_baseline). Sobota, peak 16-21 → restart shadow
  zaplanowany at#219 na 19:05 UTC (21:05 W-wa, PO peaku).
- Stan zastany: kalibrator HOLD (`artifact_legacy_or_unknown_schema`, n_common=0);
  „−52/−20" wycofane przez A0; brama właścicielska OD-01..03 niezwiązana.

## Wykonane (mapa → dotknięte)

| Miejsce | Co |
|---|---|
| D7 bootstrap (state) | kandydaci leak-free 18.07 → mapy championów (bajt-kopia; sha `a565499b…`/`b197b26b…` == rekord D7); backupy legacy `.bak-legacy-pre-d7-bootstrap`; sidecar `eta_calib_champion_provenance.json`. **Dowód mechaniki:** ręczny calibrate → gate `support_exact=True, n_common=3115/2542`, HOLD merytoryczny `improvement_below_config_threshold` (challenger==champion — OCZEKIWANE) |
| Binding D1-D5 | `tools/eta_ground_truth.KPI_BINDING_V1` (wersjonowany, owner_ack) + raport: `canonical_kpi_event` ZWIĄZANE (koniec „unbound"), `business_kpi` = thresholds D5 + coverage_gate D4 |
| Serving D6a (NOWY) | `eta_calib_serving.py`: champion-loader (mtime-cache, walidacja schema), **parytet cech = SUROWY OSRM ff** (lustro `features.OSRM.freeflow` — silnikowy `route()` dokłada traffic-mult, świadomie NIE użyty), fail-soft wszędzie |
| Lejek emisji | `dispatch_pipeline._classify_and_set_auto_route` (wspólny lejek 11 call-site'ów, obok `_split_layer_emit_assert`, osobny try) → NOWE metryki `eta_calib_promise_*` na best (wzorzec #8; auto-serializacja L1.1) |
| Flaga | `ENABLE_ETA_CALIB_PROMISE_SHADOW`: ETAP4 + const OFF (strip-safe) + flags.json **true**; lifecycle registry skurowany (506/506, lifecycle=shadow, seeded=False) |
| **FIX seedera (bonus)** | `--merge` wycierał `known_drift_note` po domknięciu dryfu (ugryzł 2× dziś); fix: pusta świeża nota nie nadpisuje niepustej starej; **dowód ON≠OFF: nota USE_V2_PARSER przeżyła kolejny merge** |
| N-D | feasibility/R6/scoring/route_sim/plan_recheck — NIE czytają flagi (D6a: decyzje nietknięte; strażnik = testy OFF-noop + istniejące suity); panel/apka — zero zmian (APPLY = przyszły krok za ACK) |

## Dowody

- **Testy 7/7** `test_eta_calib_promise_shadow.py`: OFF=total-noop · ON=nowe metryki (istniejące pola nietykalne) · fail-soft (brak championa → srv_skip, zero wyjątku) · semantyka was_czasowka · **binding==decyzje ownera (anti-drift D4/D5)** · flaga w ETAP4+const OFF.
- **E2E artefaktów w venv silnika:** realne championy load+predict (pickup p80 6.05 / delivery 19.95 na syntetycznym wierszu) — round-trip lightgbm OK.
- Checkery: lifecycle 506/506 OK · hygiene 246/246, 0 sierot · doc-coverage ✅ (wpis LOGIC_REFERENCE) · zp107 16/16.
- Pełna regresja: → Wyniki końcowe.

## Cień i flip

- **at#219 (dziś 19:05 UTC):** restart dispatch-shadow PO peaku + weryfikacja (active, 0 ERROR, ≤30 min na 1. metrykę w shadow_decisions; fail → SHADOW=false hot + raport `ETA_SHADOW_START_REPORT.txt`).
- **at#220 (wt 21.07 05:30 UTC):** `eta_promise_parity.py` → `ETA_PARITY_VERDICT.txt` (pokrycie ≥95%, skip-rate, dystrybucje P80, delta pickup calib−silnik) po ~2,4 doby cienia (peaki nd+pn w oknie).
- **Flip APPLY = OSOBNY krok za KOŃCOWYM ACK:** progi D5 na prawdzie + parytet cienia; warstwa wyłącznie obietnic (D6a). Rollback zawsze hot (`false`).

## DoD — tokeny

regresja: → Wyniki końcowe
e2e: realne artefakty championów odczytane i policzone w venv silnika (round-trip lgb) + żywy assert „metryka w shadow_decisions.jsonl" = krok weryfikacji at#219 (≤30 min od restartu; deploy-verify w raporcie startu cienia) — warstwy decyzji NIEDOTKNIĘTE (OFF-noop test + brak odczytów flagi poza servingiem)
replay: ON↔OFF w testach (OFF total-noop / ON nowe metryki); pozytyw etapu = ZWIĄZANIE bramy właścicielskiej (D1-D5 w kodzie, anti-drift), rozcięcie deadlocka D7 z dowodem gate'u i uruchomienie uczciwego parytetu — flip wartości ETA świadomie ZA progami D5 po cieniu (OD-03 fail-closed)
rollback: flags.json ENABLE_ETA_CALIB_PROMISE_SHADOW=false (hot; serving znika od następnej decyzji) / git revert / bootstrap: przywrócenie map z `.bak-legacy-pre-d7-bootstrap` + usunięcie provenance-sidecara

N-D: feasibility_v2.py — nie czyta flagi (grep 0); D6a: decyzje nietknięte
N-D: route_simulator_v2.py — jw.
N-D: core/candidates.py — jw.
N-D: core/selection.py — jw. (lejek w dispatch_pipeline, poza selekcją)
N-D: plan_recheck.py — jw.
N-D: sla_anchor.py — jw.
N-D: panel_watcher.py — jw. (konsument tylko dispatch-shadow przez lejek emitu)
N-D: route_order.py — render bez zmian (APPLY = przyszłość)
N-D: route_podjazdy.py — jw.
N-D: objm_lexr6.py — diff w dispatch_pipeline to WYŁĄCZNIE obserwacyjny hook w lejku pre-emit (PO selekcji); selektor/tie-break nietknięty (grep: hook nie czyta ani nie zmienia score/rankingu)
N-D: auto_assign_gate.py — klaster „równe traktowanie pozycji" nie dotknięty: hook nie czyta pos_source ani nie zmienia kandydatów; wyzwolone tylko nazwą pliku dispatch_pipeline w diffie
N-D: drive_min_calibration.py — jw. (zero związku z servingiem obietnic)
N-D: tools/reassignment_forward_shadow.py — jw. (duch przerzutu poza zakresem D6a; fala unifikacji bliźniaków ① osobno)

## Wyniki końcowe

- **Finalna pełna regresja: 5180 passed / 0 failed** (= baseline 5173 + dokładnie 7 nowych: 6 serving/binding + 1 zaktualizowany pin kontraktu `not_bound`→`bound_kpi_binding.v1`; 27 skip zegarowe, 8 xfail).

regresja: 5180 passed, 0 failed (baseline 5173 + dokładnie 7 nowych testów; log etaserve_final2)
