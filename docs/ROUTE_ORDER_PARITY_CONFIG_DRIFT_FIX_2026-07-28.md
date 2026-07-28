# Route-order parity — config-drift fix (2026-07-28)

## Mikro-fix grupowania wspólnych pickupów

Żywy bieg po naprawie config-drift ujawnił fałszywy `BROKEN`: kanon miał
`["pickup", ["A", "B"]]`, a projekcja apki dwa kolejne kroki
`["pickup", ["A"]]`, `["pickup", ["B"]]`. Kolejność `(kind, order_id)` była
identyczna; różniły się wyłącznie granice grup.

Komparator schematu v3 spłaszcza teraz **obie** projekcje do sekwencji
`(kind, order_id)`, zachowując kolejność grup i kolejność wewnątrz grup.
Werdykt jest liczony ze spłaszczonych sekwencji. Gdy surowe projekcje są różne,
ale spłaszczone równe:

- wynik pozostaje `OK`, nie `BROKEN`;
- obie surowe, zanonimizowane projekcje pozostają w `mismatches`;
- rekord ma `grouping_only_difference=true`;
- `grouping_only_difference_bags` rośnie jako INFO, a `mismatch_bags`
  pozostaje `0`.

Rzeczywista zamiana kolejności po spłaszczeniu nadal daje `BROKEN`, exit 1,
`grouping_only_difference=false` i `mismatch_bags=1`.

Dowody: RED-first `2 failed`; po fixie focused `14 passed`; mutation usuwająca
porównanie spłaszczonych sekwencji ponownie dała RED dokładnego grouped-vs-split
oracle; po przywróceniu focused ponownie `14 passed`. Nowe przypadki są częścią
istniejących nodeidów night-guarda, więc zbiór manifestu nie dryfuje. Zero
deployu, restartu, flipa flagi i modyfikacji runtime.

Końcowe `py_compile`, import-check i focused 14P przeszły lokalnym Pythonem.
Kanoniczny venv `dispatch` działał podczas RED-first i pierwszego biegu 2P,
ale później sandbox cofnął do niego dostęp (`Permission denied`, również przy
próbie pełnego `pytest tests/ -q`). Pełna regresja pozostaje zatem jawnie HOLD
do powtórzenia przez CTO w kanonicznym venv; nie zastąpiono jej niewiarygodną
pełną suitą na systemowym Pythonie.

## Wynik

Monitor nie importuje już konfiguracji trasy z własnego środowiska i nie
wywołuje produkcyjnego wrappera, który ponownie czyta live state. Jeden cykl:

1. pobiera `MainPID` `courier-api.service`;
2. czyta z `/proc/<MainPID>/environ` wyłącznie cztery nazwane flagi;
3. tworzy `courier_orders.RouteConfig`;
4. czyta `orders_state.json` i `courier_plans.json` dokładnie raz;
5. przekazuje te same obiekty snapshotu i ten sam config do kanonu i
   `build_view_from_snapshots`;
6. dopiero wtedy porównuje projekcję Kotlin.

Patch cross-repo do zastosowania przez CTO:
`docs/COURIER_ORDERS_SNAPSHOT_CONFIG.patch`. Powstał względem czytelnej kopii
referencyjnej `/root/sprint2_wt/courier_api`; `patch --dry-run -p1` przechodzi.
Kanoniczny plik produkcyjny był dla tej sesji read-only-denied, dlatego nie
został zmieniony.

## Kontrakt werdyktu

| verdict | exit | class | znaczenie |
|---|---:|---|---|
| `OK` | 0 | `OK` | ≥1 worek, pełne coverage, trasy równe |
| `EXPECTED_NO_DATA` | 3 | `EXPECTED_NO_DATA` | brak kwalifikujących worków |
| `BROKEN` | 1 | `PARITY_BROKEN` | trasy realnie różne |
| `BROKEN` | 2 | `INFRA_BROKEN` | import/process-config/read/coverage/write fail |
| `CONFIG_DRIFT` | 4 | `INFRA_BROKEN` | cztery flagi procesu różne od goldena |

To są dokładnie cztery stany. Określenie `INFRA_BROKEN` jest klasą awarii
stanu `BROKEN` albo `CONFIG_DRIFT`, nie piątym verdict-em. Drift jest
sprawdzany przed builderem, więc nie może zostać opisany jako „routes differ”.

## Cztery flagi i jeden owner

Golden ma teraz jawne `meta.courier_api_route_config`:

- `ENABLE_APP_ROUTE_FROM_CONSOLE=1`;
- `ENABLE_ROUTE_ORDER_UNIFIED=1`;
- `ENABLE_PLAN_AWARE_PODJAZDY=1`;
- `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=1`.

Semantykę `value == "1"` posiada `courier_orders.RouteConfig`. Monitor ma
jedynie fail-closed allowlistę nazw potrzebną do selektywnego odczytu `/proc`
i sprawdza jej dokładny parytet z `RouteConfig.ENV_BY_FIELD`. Nie przechowuje
ani nie raportuje innych wpisów process environ.

## Mapa kompletności

| miejsce | rola | writer/consumer | status |
|---|---|---|---|
| `courier_api/config.py` | produkcyjne wartości module-level | writer podczas startu procesu | N-D: zachowanie bez zmian |
| `RouteConfig.from_module_config` (PATCH) | adapter produkcyjnego configu | jedyny writer obiektu config | TAK |
| `RouteConfig.from_environ` (PATCH) | parser efektywnego `/proc` | jedyny parser czterech flag | TAK |
| `build_view` (PATCH) | produkcyjny I/O wrapper + earnings writer | consumer | TAK, publiczny kontrakt zachowany |
| `build_view_from_snapshots` (PATCH) | snapshot/config-explicit DTO builder | consumer | TAK |
| `_reorder_steps_to_canon` (PATCH) | bliźniacza ścieżka fallbacku | consumer config | TAK |
| `orders_state.json` | stan zleceń | live writer poza zakresem; monitor reader | TAK, jeden odczyt/cykl |
| `courier_plans.json` | stan planów | live writer poza zakresem; monitor reader | TAK, jeden odczyt/cykl |
| `route_order` / `route_podjazdy` | kanon kolejności | consumer snapshot/config | TAK, bez zmiany semantyki |
| Kotlin projection | klientowy consumer DTO | consumer | TAK, bez zmiany |
| corpus golden | expected config + route oracle | oracle | TAK |
| gate/open_gates line | operator | consumer verdictu | TAK, osobny alarm drift |
| systemd unit | launcher | consumer exitów | TAK w spec; bez kopii flag |
| panel `fleet_state` | sąsiedni renderer | twin | N-D: nie jest używany przez monitor |
| plan/recanon/feasibility/scoring | silnik | twins | N-D: zero zmiany reguły/HARD/SOFT |

Konkurencyjny writer configu nie został dodany. Cztery flagi w unicie monitora
są jawnie zabronione.

## RED-first, parity i mutation

Baseline w dostępnym harnessie: `10 passed, 1 skipped`. Normalny globalny
conftest nie działa w tym sandboxie z powodu odmowy odczytu live `flags.json`
i istniejącego `telegram_approver._log NameError`, więc użyto `--noconftest`.

Po dodaniu nowych oracles, przed implementacją: `12 failed, 2 passed,
1 skipped`. Po implementacji:

- monitor: `14 passed, 1 skipped, 0 failed`;
- patch courier-api zastosowany w izolowanym `/tmp`: istniejące testy planu i
  unifikacji + trzy nowe testy: `40 passed, 0 failed`;
- test bajt-identyczności: publiczny `build_view` i
  `build_view_from_snapshots` dają identyczny sorted-JSON na tym samym
  snapshot/config;
- mutation jawnego boundary: `dto_builder(courier_id, orders, plans, config)`
  zmieniony na `dto_builder(courier_id)` daje `3 failed`;
- mutation module globals: globalne plan/trust ustawione odwrotnie niż jawny
  `RouteConfig`; helper przekazuje wartości jawne — PASS;
- snapshot mutation: pliki podmienione po pierwszym odczycie, obie strony nadal
  dostają stare, identyczne obiekty — PASS;
- proces config missing: nawet przy lokalnym env=ON wynik to
  `BROKEN/INFRA_BROKEN`, exit 2 — PASS;
- `CONFIG_DRIFT` short-circuit: DTO builder nie jest wywołany — PASS.
- mechaniczny `ziomek-cto dod`: PASS; pełny DoD nadal HOLD na pełne suity,
  apply patcha, review i realny smoke;
- `tools/entropy_dashboard.py`: exit 0, ale sandbox widział `0` plików live,
  więc jest to wyłącznie strukturalny check bez prawa do aktualizacji baseline.

Szerszy klaster dispatch dał `28 passed, 1 skipped` i jeden harness fail na
import `plan_recheck` przez niedostępny live `flags.json`. Pełna suita
`courier_api` pod systemowym Pythonem zatrzymała kolekcję na brakujących
`fastapi/uvicorn`. Te dwa biegi nie są zielonymi pełnymi regresjami i CTO musi
powtórzyć je we właściwych venv.

## Unit, instalacja i rollback

Zmiana treści unitu nie jest potrzebna. Istniejąca specyfikacja uruchamia
oneshot jako root, ma `After=courier-api.service` i nie ukrywa `/proc`.
Preflight po instalacji musi jednak potwierdzić odczyt
`/proc/<MainPID>/environ`. Odmowa = exit 2; nie wolno jej „naprawiać” przez
skopiowanie flag do `Environment=`.

`SuccessExitStatus=3` pozostaje. Exit 4 jest celowo failure systemd.
Instalacja patcha courier-api, restart courier-api, instalacja/zmiana monitora,
daemon-reload i timer wymagają osobnego ACK. Nic z tego nie wykonano.

Rollback source-only: revert plików dispatch oraz patcha cross-repo. Rollback
po przyszłym wdrożeniu: przywrócić backup `courier_orders.py` i testów,
`py_compile` + import + suity, jeden kontrolowany restart courier-api za ACK,
potem sprawdzić PID/health/NRestarts. Unit monitora nie wymaga rollbacku tej
zmiany, bo nie zmienia treści.

## Bramka dla CTO

1. Zastosować patch do aktualnego, kanonicznego `courier_orders.py`; przy
   przesuniętym kontekście przenieść semantycznie, nie `--fuzz` w ciemno.
2. Uruchomić courier-api venv: testy plan/unified oraz pełną suitę.
3. Uruchomić dispatch venv: focused cluster, pełne `tests/`, flag/invariant
   checkery i `tools/entropy_dashboard.py`.
4. Re-seed night-guard wyłącznie kanonicznym `--update-manifest` po pełnym
   zielonym biegu; ta sesja nie miała gita/venv i nie fałszowała manifestu.
5. Przed restartem: backup, `py_compile`, import check i owner ACK.
6. Po restarcie: odczytać realny PID env, potwierdzić 4×ON, wykonać monitor
   bez `--result-path`, oczekiwać `OK` lub `EXPECTED_NO_DATA`, nigdy drift.
7. Dopiero potem instalacja/timer monitora według
   `ROUTE_ORDER_PARITY_MONITOR_SYSTEMD_SPEC.md`.
