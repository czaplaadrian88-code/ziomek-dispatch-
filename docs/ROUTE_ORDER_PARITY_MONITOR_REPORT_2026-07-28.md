# Route-order parity monitor — raport kandydata 2026-07-28

> **SUPERSEDED (config boundary only):** późniejsza diagnoza 28.07 wykazała,
> że ten kandydat importował `build_view` pod własnym env i ponownie czytał live
> state. Obowiązuje
> `ROUTE_ORDER_PARITY_CONFIG_DRIFT_FIX_2026-07-28.md` +
> `COURIER_ORDERS_SNAPSHOT_CONFIG.patch`. Historyczne dowody tri-state/Kotlin
> poniżej pozostają użyteczne, lecz opis configu, buildera i exitów nie jest
> aktualnym kontraktem.
>
> **SUPERSEDED (stop contract):** ADR-010 zastąpił rekonstrukcję legacy
> `restaurantKey` kontraktem backendowego `stop_id` + `order_ids` oraz dokładnym
> committed per zlecenie. Aktualny monitor nie grupuje po koordynatach, nazwie
> restauracji ani własnym progu.

## Wynik

Kandydat naprawia błąd instrumentu u źródła. Poprzedni checker porównywał
kanon Pythona z panelowym `_build_route` i zwracał `OK` przy
`checked_bags=0`. Nowy łańcuch to:

`route_order canon -> courier_orders.build_view (/orders DTO) ->
project_kotlin_build_steps (wierny port RouteLogic.kt legacy fields)`.

Nie dodano `stop_id` ani drugiego goldena WB3. Projekcja używa istniejącego
`tests/golden/route_order_corpus.json`.

## Mapa kompletności

| miejsce | rola | status |
|---|---|---|
| `route_order.py` / `route_podjazdy` | kanon | TAK, read-only consumer |
| `courier_orders.build_view` | writer realnego DTO `/orders` | TAK |
| `earnings_history.record_day` z `build_view` | ukryty writer read-side | TAK, zneutralizowany lokalnie + restore `finally` + test |
| Kotlin `restaurantKey/buildSteps` | transformacja klienta | TAK, port legacy |
| panel `fleet_state._build_route` | stary bliźniak | TAK, usunięty z certyfikacji; ratchet zabrania powrotu |
| golden route-order | oracle | TAK, reuse 3 przypadków; brak nowego pliku golden |
| wynik/heartbeat/coverage | operator | TAK, atomic 0600 |
| OPEN_GATES | alarm | TAK, pole `open_gates_line` |
| legacy monitor/unit/timer | retirement | TAK w spec; wykonanie N-D bez ACK |
| systemd install/restart/live write | runtime | N-D: jawnie zabronione w tym sprincie |

## Kontrakt i testy

- exit `0=OK`, `3=EXPECTED_NO_DATA`, `1=BROKEN parity/config`,
  `2=BROKEN infra/coverage/output`;
- `EXPECTED_NO_DATA` wyłącznie dla denominatora zero;
- kwalifikujący worek bez DTO daje `BROKEN`, nie no-data;
- heartbeat na każdym przebiegu: timestamp, run id, denominator/numerator,
  mismatch/error count;
- mismatch artifacts haszują CID i order ID;
- wynik do pliku tylko po `--result-path`, atomowo temp→fsync→rename→dir fsync,
  mode 0600.

Wyniki:

1. `py_compile` tool + test: PASS.
2. Klaster route-order przez symlink-pkgroot, system Python:
   **38 passed, 1 skipped, 0 failed**.
3. `HERMETIC_STRICT=1`, monitor + quarantine oracle:
   **10 passed, 1 skipped, 0 failed**.
4. Mechaniczny manifest night-guarda: schema/load PASS, v32→v33,
   5842 posortowane unikalne nodeidy,
   SHA-256 `44160e5b604298df8945a9537f2721a933082e23e952a91ac0337269d5134aa1`.
5. Entropy dashboard: wykonał się exit 0; sandbox widział 0 live files, więc
   wynik jest wyłącznie strukturalny i nie jest pomiarem produkcji.
6. `ziomek-cto dod` na wąskim diffie kandydata: mechaniczny PASS. Pełny DoD
   nadal HOLD przed merge przez brak pełnej suity i kanonicznego live runu.

Oracles/mutation:

- OK na pełnym 1/1;
- zero worków → `EXPECTED_NO_DATA`, exit 3;
- odwrócona/pominięta projekcja klienta → `BROKEN`, exit 1;
- błąd DTO przy denominatorze 1 → `BROKEN`, exit 2, coverage 0/1;
- 3 realne kształty DTO z Kotlin tests + 3 przypadki z istniejącego korpusu;
- znany writer `record_day` nie zostaje wywołany i jest przywracany;
- strukturalny ratchet: brak `fleet_state`/`_build_route` w monitorze.

## Ręczny przebieg read-only

Komenda bez `--result-path` wykonała się 2026-07-27 22:20:57 UTC i poprawnie
zakończyła fail-closed:

- verdict `BROKEN`, exit 2;
- qualifying `0`, checked `0`, mismatches `0`, errors `1`;
- przyczyna: sandbox nie udostępnia panelowego `app` ani żywego state/venv
  (`Permission denied` / `ModuleNotFoundError`);
- wyemitowana linia:
  `| — | obs.route-parity-telemetry | WAIT_DATA | CTO | 2026-08-01 | ALARM: route parity BROKEN; coverage=0/0; monitor infrastructure failure |`.

To jest wynik infrastruktury tej sesji, **nie werdykt o parytecie produkcji**.
Nie ma uczciwej liczby z żywych danych. Ponowienie pod dokładnym środowiskiem
przyszłego unitu jest obowiązkową bramką przed instalacją.

## Ograniczenia i rollback

Kanoniczny venv dispatch, panelowy venv, live state i Git worktree metadata były
niedostępne w sandboxie. Dlatego nie wykonano pełnej suity, realnego live
parity runu, `git diff/status/log`, commitów ani automatycznego
`night_guard --update-manifest` z pełnym outcome reportem. Statyczny manifest
jest spójny, ale przed merge wymaga kanonicznej pełnej suity/reseed.

Rollback source-only: revert jawnych plików kandydata. Runtime rollback nie
istnieje, bo nie było instalacji, deployu, restartu, flagi ani zapisu live.
Dokładny ACKed install/retirement/rollback opisuje
`docs/ROUTE_ORDER_PARITY_MONITOR_SYSTEMD_SPEC.md`.

Mechaniczny format evidence:

- `regresja: 38 passed, 1 skipped, 0 failed` (focused; pełna suita N-D — venv niedostępny);
- `e2e: czysty łańcuch canon -> DTO -> Kotlin projection pokryty injected-builder oracle; live E2E BROKEN infra 0/0`;
- `pozytywny-wplyw: zero-work nie może już dać OK; realny DTO i Kotlin zastępują panelowy proxy`;
- `rollback: source-only revert jawnych plików; zero runtime do cofnięcia`;
- `N-D: systemd install, legacy unit removal — osobny owner ACK`;
- `N-D: full suite and canonical manifest reseed — dispatch venv/gitdir denied by sandbox`.

## Proponowane commity dla CTO

Git jest niedostępny, więc commitów nie utworzono. Proponowany podział:

1. `feat(PAR): monitor backend DTO through Kotlin route projection`
   - `tools/route_order_live_parity_check.py`
2. `test(PAR): enforce tri-state coverage Kotlin golden and hermeticity`
   - `tests/test_route_order_live_parity.py`
   - `tests/hermetic_quarantine.json`
   - `tests/test_hermetic_guard_zp207.py`
   - `tools/night_guard_suite_manifest.json`
3. `docs(PAR): specify 15m timer legacy retirement and candidate evidence`
   - `docs/ROUTE_ORDER_PARITY_MONITOR_SYSTEMD_SPEC.md`
   - `docs/ROUTE_ORDER_PARITY_MONITOR_REPORT_2026-07-28.md`
   - `docs/ROUTE_ORDER_PARITY_MONITOR_DOD_EVIDENCE.txt`
   - `ZIOMEK_BACKLOG.md`
