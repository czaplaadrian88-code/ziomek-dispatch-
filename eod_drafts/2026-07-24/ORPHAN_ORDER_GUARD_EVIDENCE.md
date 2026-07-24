# Orphan order guard — evidence

Base: `5cd94de968ef5db2b00bbd5b5eefee8d82094a3e`
Branch: `fix/orphan-order-900138096-master-20260724`
Tryb: sandbox-only; zero live/deploy/restart/push/merge/flag/runtime.

## Mapa kompletności

| miejsce | rola | writer/consumer | dotknięte | powód | test |
|---|---|---|---|---|---|
| `parcel_assign.py` | intencja | writer | N-D | szybki precheck UX zostaje; nie jest atomowym ownerem zapisu | `test_parcel_assign.py` |
| `durable_event_apply.py` | retry | writer transportu | N-D | retry jest poprawny; nie może znać schematu każdego eventu | focused |
| `state_machine.py` | locked commit funnel | kanoniczny writer | TAK | `require_existing` sprawdzane pod tym samym lockiem co RMW | orphan oracle + mutation |
| `parcel_lane_merge.py` | materializacja bazowa | writer | N-D | poprawny owner pełnego rekordu `NEW_ORDER` | istniejąca regresja |
| `panel_watcher.py` | gastro twin | writer | N-D | brak udziału w potwierdzonym `source=parcel_assign`; zachowany parytet | full regression |
| `courier_resolver.py` | bag/capacity | consumer | N-D | skutek znika u źródła; bez downstream fallbacku | full regression |
| `live_eta_daemon.py` | ETA bag | consumer | N-D | skutek znika u źródła; bez downstream fallbacku | full regression |
| `tools/ziomek_pred_calibration.py` | kalibracja | consumer | N-D | skutek znika u źródła; bez downstream fallbacku | full regression |
| `observability/data_alerts.py` | operacyjna czujka | consumer | TAK | sensor kanonicznego schematu aktywnego rekordu | 2 oracle |
| `tests/test_state_schema_validator.py` | hermetyczny test | consumer | N-D | nie przywracamy odczytu live do pytest | full regression |

## Dowody

regresja: 5822 passed, 0 failed, 27 skipped, 8 xfailed; baseline 5769/0, delta failów 0
e2e: PASS — parcel_assign COURIER_ASSIGNED dla absent order dochodzi do locked writera i nie zapisuje rekordu
replay: PASS — incident-shape 900138096 blokowany; legalny istniejący rekord zachowuje parytet
rollback: git revert commita; brak runtime, migracji, flag i danych do cofania

- RED-first na nowym baseline przed fixem: `3 failed / 3`.
- focused: `83 passed`.
- mutation: usunięcie sprawdzenia `require_existing` spod locka daje
  `test_courier_assigned_cannot_create_missing_order` RED (`DID NOT RAISE`);
  po przywróceniu GREEN.
- py_compile: PASS.
- import check: PASS.
- regresja: `5822 passed, 0 failed, 27 skipped, 8 xfailed`.
- delta failów vs podany baseline `5769 passed / 0 failed`: `0`.
- entropy: bez wzrostu (`dead-flag=1`, `sentinel live=0`).
- e2e: event `COURIER_ASSIGNED source=parcel_assign` dla nieobecnego rekordu
  dochodzi do kanonicznego locked writera i kończy się wyjątkiem bez zapisu.
- replay: deterministyczny incident-shape `900138096` jest blokowany; legalny
  istniejący rekord oraz pozostałe ścieżki assignmentu przechodzą focused suite.
- rollback: `git revert <commit>`; brak zmian runtime i brak migracji.

## Bliźniaki / N-D

N-D: `parcel_assign.py` — precheck UX nie jest ownerem atomowego kontraktu.
N-D: `durable_event_apply.py` — transport retry pozostaje generyczny.
N-D: `parcel_lane_merge.py` — pozostaje ownerem materializacji bazowej.
N-D: `panel_watcher.py` — gastro twin nie uczestniczy w incydencie parcel.
N-D: `courier_resolver.py` — bez łaty downstream.
N-D: `live_eta_daemon.py` — bez łaty downstream.
N-D: `tools/ziomek_pred_calibration.py` — bez łaty downstream.
N-D: `tests/test_state_schema_validator.py` — pozostaje hermetyczny.
