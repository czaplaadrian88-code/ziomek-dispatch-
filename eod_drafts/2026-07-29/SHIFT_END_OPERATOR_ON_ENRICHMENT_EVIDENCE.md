# OPERATOR_ON → typed shift interval — R3 freeze evidence

Status source: **READY_FOR_REVIEW**
Status live: **HOLD**
Model/effort implementacji: `sol/max` — zmiana przecina authority, czas Warsaw,
HARD feasibility i współbieżnych writerów.
Worktree:
`/root/worktrees/dispatch_v2/active/20260729-shift-end-enrichment-sol`
Branch: `wt/shift-end-enrichment-sol-20260729`
Base/HEAD: `68785d7ef597baada321d83699c51187f18c8484`
Operacje live: **zero** — bez flag, runtime state, systemd, deployu, restartu,
stage i commita.

## Wynik R3

Remediacja zamyka pięć findingów niezależnego review R2:

1. Aktywny, dokładnie dopasowany grafik zachowuje baseline precedence nad
   explicit/default końcem operatora. Dla grafiku `10:00–22:00` komendy
   `do 21` i `do 23` dają `shift_end=22:00` zarówno w puli/feasibility, jak
   i w raporcie HARD.
2. Typed parser nie zmienia globalnej semantyki `_mins_to_shift_start`,
   `_shift_start_dt` ani `_shift_end_dt`; obejmuje wyłącznie trwały
   `operator_window`.
3. Przyszłe okno operatora zachowuje rollback parity:
   `pos_source=pre_shift` i ten sam dodatni `shift_start_min` przy fladze
   kontraktu ON i OFF.
4. Zegar operatora przyjmuje wyłącznie cyfry ASCII i fail-soft odrzuca
   Unicode, zły format, zakres lub metadata.
5. Raport HARD nie czyta `manual_overrides.get_working`; używa tego samego
   `AvailabilityContext`, tego samego zamrożonego `now` i tego samego ownera
   okna co pula.

Source jest gotowy do świeżego review. Live pozostaje HOLD, bo zewnętrzny
reset writer nie został jeszcze przełączony na kanoniczny owner.

## Root cause

Trwały rekord CID miał jedną parę `provenance/updated_at`. Późniejszy
`assignment_event` zastępował cały rekord i usuwał jawne okno konsoli, mimo że
sam nie deklarował końca pracy. Równolegle konsola publikowała legacy
`working/excluded` i CID authority osobnymi RMW/write, a raport HARD ponownie
czytał legacy inną ścieżką niż pula.

Pierwsza wersja kandydata naprawiła utratę okna, ale wprowadziła dwa nowe
odchylenia: dała explicit końcowi operatora pierwszeństwo nad aktywnym
grafikiem i podmieniła historyczne globalne helpery grafiku typed parserem.
R3 usuwa oba odchylenia u źródła, bez render-only fallbacku.

## Kanoniczny kontrakt

Root rekordu nadal opisuje ostatni fakt authority:

```text
state / provenance / updated_at
```

Osobny fakt czasu ma własne metadata:

```text
operator_window = {
  start,
  end,
  end_explicit,
  added_at,
  provenance: "coordinator_console"
}
```

- Assignment może zmienić root provenance, ale nie usuwa poprawnego okna i
  nie fabrykuje własnego.
- `shift_interval.py` jest jedynym parserem trwałego okna operatora; kotwiczy
  `HH:MM` w `Europe/Warsaw` względem aware `added_at`, także przez północ.
- Aktywny exact schedule jest ownerem czasu. Poza aktywnym grafikiem czas
  pochodzi z typed operator window; miniony koniec fail-closed usuwa kuriera
  z puli.
- Bez okna assignment daje `operator_since` i nieznany koniec.
- Behavior flag wybiera semantykę konsumenta, ale bezpieczeństwo zapisu
  (lock/CAS/preserve CID) działa zawsze, również przy fladze OFF.

## Mapa writerów i konsumentów

| Miejsce | Rola | Status | Dowód / uzasadnienie |
|---|---|---:|---|
| `courier_availability.py` | kanoniczny store owner | TAK | jeden lock, atomic write, legacy revision CAS, field-level merge |
| `manual_overrides.py` | konsola ON/OFF/neutral/reset | TAK | każda gałąź ON/OFF deleguje do ownera; identity jest precondition |
| `state_machine.py` | assignment producer | N-D | już deleguje do `set_operator_availability`; nie deklaruje okna |
| `manual_overrides_daily_reset.py` w repo | source replacement resetu | TAK | deleguje do `reset_legacy_fields`, loguje wyłącznie liczności/typ błędu |
| hostowy `manual_overrides_daily_reset.py` | live konkurencyjny writer | HOLD | exact hash nadal omija wspólny lock; nieedytowany |
| `courier_availability.resolve` | authority + exact schedule/window decision | TAK | jeden snapshot store, schedule match i `real_on_shift_now` |
| `courier_resolver.dispatchable_fleet` | pula + wejście feasibility | TAK | wspólny `_operator_on_shift_window`; przywrócone future `pre_shift` |
| `courier_resolver.resolve_effective_shift_end_by_cid` | raport HARD | TAK | ten sam context/window owner; brak `manual_overrides.get_working` |
| `plan_recheck.py` | konsument raportu HARD | TAK | przekazuje dokładny `now` decyzji |
| `feasibility_v2.py` | Gate 2/Gate 3 | N-D | konsumuje `CourierState.shift_start/shift_end`; polityka nie skopiowana |
| `_mins/_shift_start/_shift_end` w resolverze | baseline schedule helpers | N-D | R3 przywraca ich wcześniejszą implementację bez typed parsera |
| history/freshness serializers | downstream | N-D | istniejące pola `CourierState`; brak zmiany schema |

## Writer safety i reset

`save_legacy_payload`, `commit_console_projection`,
`set_operator_availability` i `reset_legacy_fields` używają tego samego
lockfile oraz jednego atomic `fsync + rename`. Legacy RMW ma osobny
`legacy_updated_at` jako CAS revision; stary payload jest odrzucany zamiast
gubić cudzą zmianę. CID store jest zachowywany przez każdy legacy write.

Repozytoryjny reset:

- czyści tylko `excluded`, `excluded_cids` i `working`;
- zachowuje `availability_by_cid`;
- zwraca i drukuje tylko liczności, bez nazw/CID;
- nie tworzy własnego logu ani drugiego writera.

## Oracle, mutation i ratchety

R3 rozpoczął się od **11 RED** odtwarzających findings. Zielony zestaw obejmuje:

- active schedule `10–22` z explicit `do 21` i `do 23`, flaga ON/OFF,
  pula i raport HARD;
- aktywny grafik nadal dispatchable po krótszym operator window;
- future operator window z parytetem `pre_shift/shift_start_min` ON/OFF;
- historyczną semantykę globalnych helperów, w tym overnight;
- ASCII-only i fail-soft zegara;
- stale legacy CAS, concurrent assignment preserve i każdą gałąź zapisu
  przy fladze ON/OFF;
- source reset entrypoint, count-only output i zachowanie CID store;
- exact-hash-attested `xfail` dla zewnętrznego writera.

Mutation oracles czerwienieją po:

- usunięciu zachowania okna przez assignment;
- przywróceniu naiwnej projekcji future schedule;
- ustawieniu explicit window przed aktywnym grafikiem;
- ominięciu GRAFIK-CAP.

Structural ratchets zabraniają:

- typed parsera w globalnych helperach grafiku;
- `manual_overrides.get_working` w raporcie HARD;
- raw `os.replace` w repozytoryjnym console/reset entrypoincie;
- drugiego ownera `availability_by_cid`.

## Wyniki testów

Wszystkie testy użyły venv:
`/root/.openclaw/venvs/dispatch/bin/python` i izolowanego package-rootu
(`dispatch_v2` → właściwy worktree; `schedule_utils.py` → kanoniczne źródło).

Focused po R3:

```text
56 passed, 1 xfailed
```

Writer/consumer, availability i HARD cluster:

```text
114 passed, 1 xfailed
```

R4/fleet cluster:

```text
81 passed
```

Pełna suita kandydata:

```text
8 failed, 6235 passed, 27 skipped, 9 xfailed, 149 warnings
380.71s
```

Pełna suita exact base `68785d7` w osobnym czystym worktree i identycznym
package-root:

```text
8 failed, 6194 passed, 27 skipped, 8 xfailed, 149 warnings
416.63s
```

Delta kandydat vs exact base:

```text
+41 passed
+1 expected xfail (exact-hash HOLD_LIVE reset writer)
0 new failed
0 new skipped
0 new warnings
```

Identyczny zestaw ośmiu baseline nodeidów:

```text
tests/test_a41_broadcast_handlers.py::script_run
tests/test_authority_card.py::test_h2_oserror_after_child_start_is_unknown_and_never_rolls_back
tests/test_flag_doc_coverage.py::test_no_new_undocumented_decision_flag
tests/test_l6c_geometry_claim.py::test_tick_claim_ledger_on_off
tests/test_operator_route_override.py::test_carried_position_honored_with_hard_breach_logged
tests/test_operator_route_override.py::test_l3_reject_overridden_by_pin
tests/test_recanon_on_write.py::test_recanon_floors_carried_at_event_time
tests/test_wb1_lex_window_ledger_v2.py::test_kanon_zawiera_pola_wymagane_przez_kalibracje_wb2
```

Te same osiem nodeidów uruchomione deterministycznie daje na kandydacie i
base `7 failed, 1 passed`; `test_l3_reject_overridden_by_pin` jest znanym
suite-order baseline i przechodzi solo. Nowy plik testowy + ten nodeid daje
`42 passed, 1 xfailed`, więc nowy test nie jest jego contaminatorem.

Kontrole statyczne:

```text
py_compile: PASS
import dispatch_v2.{shift_interval,courier_availability,courier_resolver,
manual_overrides,manual_overrides_daily_reset,plan_recheck}: PASS
git diff --check: PASS
ziomek-cto dod (tracked + untracked diff in anonymous memfd): PASS mechaniczny
entropy dashboard: dead-flag=1, live sentinel=0; pozostałe AUDIT-BASELINE
```

## Freeze manifest

Aggregate SHA-256 posortowanego manifestu poniższych plików:

```text
d912d8da89440a05a94232cd523ed324a36622100c530897b75b40970672fd4c
```

| Plik | SHA-256 |
|---|---|
| `courier_availability.py` | `0c7ed749779c10ecb73a370e522035eabac0c56e1e79b0f1f764ac065289f5eb` |
| `courier_resolver.py` | `2a80a40af16ff9bdedb72b360e7dd9d3b030ca714d4f3da4b200901e3dddcc00` |
| `manual_overrides.py` | `6d7173d35f8e5301efb3566a2dd698bba433e1609fba57cbf5a4609aaa80a06e` |
| `manual_overrides_daily_reset.py` | `f92382956f3320c65408cff1a6f13d3fb5fb81f7201f35cd4852896e78c28510` |
| `plan_recheck.py` | `a575aa32e04c65405b0d7ddf28fc7c3a74066ae50c8e8aa8c9834006b2d851ef` |
| `shift_interval.py` | `e677cff616c80802089b6a705d7ed8f0bdaf8686a01093ce548e694eb1376ed3` |
| `tests/test_cid400_review_repro_2026_07_23.py` | `1f4adfe5528add59912d9acc75eb5395267c7dd9c656d7251a04519b711455c7` |
| `tests/test_cid_availability_contract_2026_07_23.py` | `4b80393e2f209830e3979b4c54231a65f30ce27b39476dfb09ef79d82e7d9a13` |
| `tests/test_operator_on_shift_enrichment_2026_07_29.py` | `519e0dcb8576f53a0821a64c6243eac97ae8deef28ad6e4e1f00f88eaad1f5de` |
| `tests/test_operator_route_override.py` | `7df74a1e9077f68b720bf9695ec252b8388d527e0c914bd24512d4f15f3ec03d` |

Tracked binary diff SHA-256:
`d72bafa60e206feec0eb2772730f367008d5cf9fadb95a937343e2697782f2bf`.

## HOLD_LIVE i plan cutoveru

Atestowany stan live, wyłącznie read-only:

```text
host reset source SHA-256:
12e4161424ccb16b2b5cb61b4dbc74904dfe3e442fc32f1da1efeb512444d462
dispatch-overrides-reset.timer: active/waiting
next trigger: 2026-07-30 04:00:00 UTC (06:00 Warsaw)
service ExecStart: venv python + host manual_overrides_daily_reset.py
shared log mode: 0644
```

Żaden z tych elementów nie został zmieniony. Live promotion wymaga osobnego
owner ACK i kontrolowanego okna:

1. ponownie zweryfikować source manifest, exact live hash, timer i brak
   równoległego writera;
2. wykonać backup hostowego skryptu i store; przygotować rollback;
3. zainstalować repozytoryjny entrypoint w ścieżce używanej przez unit,
   zachowując root ownership i executable mode;
4. ustawić istniejący log na `0600` oraz utrwalić `UMask=0077` w unit/drop-in;
5. `daemon-reload` tylko jeżeli zmieni się unit/drop-in; nie uruchamiać resetu
   na żywym store jako smoke;
6. zweryfikować import/compile, effective `ExecStart`, timer i hash;
7. po następnym planowym triggerze sprawdzić exit status, prywatny tryb logu
   i count-only output, bez odczytywania/raportowania nazw lub CID.

Rollback przed pierwszym triggerem: przywrócić backup hostowego skryptu i
drop-in, wykonać `daemon-reload` jeśli był potrzebny, ponownie sprawdzić timer.
Po triggerze reset legacy jest zamierzonym lifecycle; CID authority pozostaje
zachowane przez konstrukcję.

regresja: delta failed=0 vs exact base; candidate 8F/6235P, base 8F/6194P
e2e: console one-write → CID field provenance → assignment → shared window owner → fleet/feasibility/HARD report
pozytywny-wplyw: active schedule precedence and ON/OFF parity restored; 11 RED → green, +41 PASS, no new fail
rollback: source-only discard/revert frozen files; live untouched; cutover has pre-trigger host backup restore
N-D: state_machine.py — existing assignment producer already delegates to the canonical writer
N-D: feasibility_v2.py — consumes canonical CourierState shift bounds; no duplicate policy added
N-D: history/freshness serializers — existing CourierState schema unchanged
N-D: core/candidates.py — selection consumes feasibility outcome; it neither owns nor resolves courier shift bounds
N-D: route_simulator_v2.py — receives canonical shift inputs from feasibility; no independent operator-window reader
N-D: panel_watcher.py — display/observer surface; no writer or HARD consumer of this time contract
N-D: route_order.py — route ordering does not resolve availability or the effective shift end
N-D: route_podjazdy.py — route projection does not own availability or shift-window precedence
N-D: sla_anchor.py — SLA time anchors are a separate contract from courier shift authority
