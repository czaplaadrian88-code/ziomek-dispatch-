# C7 normal path — instrument `c7_normal_path.v1`

Status: projekt log-only, domyślnie OFF. Dokument opisuje kontrakt przed
implementacją. Nie upoważnia do flipu C7, deployu ani restartu.

## Problem i oracle

Pomiar K1 z 2026-07-27 wykazał, że istniejące rekordy nie pozwalają uczciwie
odtworzyć pełnej puli normalnej selekcji z C7 OFF i ON. Brakuje między innymi
alternatyw, kary C7, prognozy dostawy, R-35MIN i obciążenia. Dlatego
`ENABLE_POST_SHIFT_OVERRUN_PENALTY` pozostaje HOLD.

Oracle instrumentu jest mocniejszy od zwykłego replayu:

1. przed uruchomieniem produkcyjnego selektora powstaje izolowany snapshot
   **całej** ocenionej puli, jeszcze przed `top[:16]`;
2. produkcyjny selektor zwraca niezmieniony `PipelineResult`;
3. na dwóch niezależnych kopiach snapshotu ten sam kanoniczny
   `core.selection.select_and_emit` wykonuje ramię C7 OFF i C7 ON;
4. ramię OFF musi mieć ten sam `best`, `verdict` i routing
   `AUTO/ACK/ALERT` co wynik produkcyjny. Każdy rozjazd daje
   `status=INSTRUMENT_MISMATCH`, nigdy ciche pominięcie.

Instrument jest miarodajny wyłącznie przy produkcyjnym C7=OFF. Nie jest
mechanizmem wdrożenia C7.

## Mapa kompletności i hook

| Miejsce | Rola | Writer / consumer | Dotknięte | Powód i test |
|---|---|---|---|---|
| `core/candidates.py` (blok `post_shift_overrun_*`) | źródło metryk C7 | writer `post_shift_overrun_min`, `post_shift_overrun_penalty`, `post_shift_overrun_score_delta` i `Candidate.score` | NIE | Instrument nie przelicza trasy ani feasibility. Normalizuje istniejący score przez odjęcie zapisanej delty, a następnie składa OFF/ON z tej samej kary. Test OFF/ON sprawdza rachunek. |
| `common.py` (`post_shift_overrun_penalty`) | kanoniczna krzywa C7 | writer wartości kary | NIE | Oba ramiona używają wartości już policzonej przez kanon. |
| `common.py` (`decision_flag`) | kanon flag hot-reload | consumer C7 oraz kill-switch instrumentu | TAK | Thread-local override C7 daje race-safe OFF/ON tylko w bieżącym kontrfaktyku. Globalna flaga ani `flags.json` nie są modyfikowane. |
| `dispatch_pipeline.py` (`_assess_order_impl`, bezpośrednio przed `_select_position_model`) | jedyny hook pełnej puli | writer snapshotu, consumer kanonicznej selekcji | TAK | To ostatnie miejsce, w którym istnieje komplet ocenionych kandydatów przed `core.selection` i `top[:16]`. Snapshot powstaje przed realną selekcją; pomiar jest dołączany dopiero po jej wyniku, aby wykonać oracle parytetu OFF. |
| `core/selection.py` (`select_and_emit`) | kanoniczna normalna i best-effort selekcja | consumer score, tiering, OBJM, E2, gate'ów | TAK | Oba ramiona wywołują dokładnie ten selektor na pełnej puli. Opcjonalny trace zapisuje zwycięzcę po `score`, `OBJM` i `E2`; nie zmienia kolejności ani wyniku. |
| `objm_lexr6.py` (`lex_qual`, `pick`) | kanoniczny OBJM | consumer C7 jako pierwszego termu | NIE | Thread-local override jest widoczny przez istniejący `decision_flag`; nie powstaje kopia polityki OBJM. |
| `dispatch_pipeline.py` (`_best_effort_sort_key`, `_best_effort_objm_pick`, `_feas_carry_readmit_pick`) | bliźniacze ścieżki selekcji | consumer C7 | NIE | Oba ramiona przechodzą przez te same helpery. Ratchet sprawdza, że C7 nie zmienia verdictu przez low-score/difficult-case gate, także przy always-propose. |
| `auto_proximity_classifier.py` (`classify_auto_route`) | routing `AUTO/ACK/ALERT` | consumer wyniku selekcji i marginesu | TAK | Ramiona używają tego samego klasyfikatora z wyłączoną uboczną emisją calibration-shadow. Rejestrowany jest routing i margines nawet przy niezmienionym zwycięzcy. |
| `c7_normal_path.py` | jeden owner kontrfaktyku i schematu | writer `c7_normal_path.v1` | TAK | Izolowane kopie, dwa wywołania selektora, parity oracle, fail-safe, brak PII, code SHA i fingerprint. |
| `shadow_dispatcher.py` (`_serialize_result`) | jedyny trwały writer decyzji | consumer payloadu | TAK | Addytywne pole `c7_normal_path`; brak osobnego pliku i konkurencyjnego writera. |
| `tools/flag_lifecycle_registry.json` | rejestr lifecycle | consumer źródeł flag | TAK | Seed wyłącznie przez `tools/flag_lifecycle_seed.py --merge`; kill-switch domyślnie OFF, rollback hot przez brak/false w `flags.json`. |
| `tests/test_c7_normal_path_instrument.py` | oracle i ratchet | consumer całego kontraktu | TAK | OFF parity, mismatch, dokładnie dwa wywołania, fail-safe, no-PII, score/OBJM/E2/gate i bliźniacze gate'y. |
| `tools/benchmark_c7_normal_path.py` | pomiar kosztu | consumer pełnego instrumentu | TAK | Syntetyczne pule 8/16/24 kandydatów, mediana i p95 narzutu per decyzja. |

### Dlaczego nie później

`PipelineResult.candidates` zawiera najwyżej 16 kandydatów. Hook w serializerze,
`decision_eta_log` albo panelu nie odzyska odciętych alternatyw i powtórzyłby
błąd K1. Instrument nie może też używać `max(score)`, ponieważ rzeczywisty
wynik mogą zmienić tiering, OBJM, E2, feasibility carry readmit i gate'y
werdyktu.

### Czego świadomie nie obejmuje

- Nie uruchamia ponownie kosztownej oceny kandydata, solvera trasy ani
  feasibility. C7 nie zmienia tych warstw; jego writer powstaje dopiero po
  wyliczeniu planu.
- Nie obejmuje early-bird ani innych returnów sprzed zbudowania puli.
- Nie zmienia R29 SOLO. Ta ścieżka nie konsumuje C7 i nie ma porównywalnej
  ocenionej puli.
- Nie zapisuje nazwisk, restauracji, adresów, współrzędnych, surowych eventów
  ani planów. Identyfikatory kurierów i zlecenia są technicznymi kluczami
  istniejącego decision logu; sam payload nie kopiuje PII z otoczenia.
- Nie łączy instrumentu z osobnym logiem `decision_eta_log`; trwałym ownerem
  jest istniejący rekord `shadow_decisions`.

## Format `c7_normal_path.v1`

Pole top-level `c7_normal_path` występuje tylko przy kill-switchu ON:

```json
{
  "schema": "c7_normal_path.v1",
  "status": "OK",
  "actual_c7_enabled": false,
  "full_pool_size": 18,
  "full_feasible_size": 15,
  "last_changed_stage": "OBJM",
  "winner_changed": true,
  "margin_changed": true,
  "routing_changed": false,
  "verdict_changed": false,
  "code_sha": "e7c0cc2ce...",
  "flag_fingerprint_sha256": "sha256:...",
  "flag_fingerprint": "ENABLE_...=0 ...",
  "off": {
    "winner_cid": "531",
    "verdict": "PROPOSE",
    "routing": "ACK",
    "score_margin": 8.2,
    "c7_penalty": 42.0,
    "c7_score_delta": 0.0,
    "predicted_delivery_iso": "2026-07-27T20:12:00+00:00",
    "r35_max_bag_time_min": 31.2,
    "r35_breach_max_min": 0.0,
    "committed_breach_min": 0.0,
    "new_pickup_late_min": 1.0,
    "load": 0.42,
    "bag_size": 2
  },
  "on": {
    "winner_cid": "75",
    "verdict": "PROPOSE",
    "routing": "ACK",
    "score_margin": 3.1,
    "c7_penalty": 0.0,
    "c7_score_delta": 0.0,
    "predicted_delivery_iso": "2026-07-27T20:09:00+00:00",
    "r35_max_bag_time_min": 34.8,
    "r35_breach_max_min": 0.0,
    "committed_breach_min": 0.0,
    "new_pickup_late_min": 0.0,
    "load": 0.55,
    "bag_size": 3
  }
}
```

`INSTRUMENT_MISMATCH` zawiera wyłącznie bezpieczną listę pól parytetu
(`winner_cid`, `verdict`, `routing`); wyjątek instrumentu daje
`status=INSTRUMENT_ERROR`. Oba stany pozostawiają produkcyjny
`PipelineResult` bez zmian. Code SHA jest czytane bez procesu z worktree
`.git/HEAD`/refów i cache'owane; `git rev-parse` jest wyłącznie krótkim
fallbackiem, więc pierwszy rekord nie płaci normalnie kosztu nowego procesu.

## Etapy zmiany

Porównywane są identyfikatory zwycięzcy po kolejnych etapach:

1. `score` — finalny score/tiering na pełnej puli;
2. `OBJM` — live OBJM/best-effort OBJM;
3. `E2` — ewentualny PLN-resort;
4. `gate` — finalny `best`, verdict, margines lub routing.

`last_changed_stage` jest etapem, na którym para `(winner_OFF, winner_ON)`
ostatnio faktycznie się zmieniła, a nie ostatnim późniejszym snapshotem, który
tylko zachował wcześniejszą różnicę. Dlatego rozjazd powstały w score i
utrzymany przez OBJM/E2 nadal ma etykietę `score`. Rekord powstaje również dla
tego samego zwycięzcy, gdy zmienił się margines, verdict albo routing.

## Wydajność i próbkowanie

Pierwsza wersja ma kill-switch OFF i mierzy własne `prepare_ms`,
`measurement_ms` oraz `overhead_ms`. Hermetyczny benchmark z 2026-07-27
(`tools/benchmark_c7_normal_path.py`, 8 warmupów + 80 iteracji na rozmiar,
systemowy Python 3.12 w sandboxie) dał:

| pełna pula | mediana | p95 | max |
|---:|---:|---:|---:|
| 8 | 1,926 ms | 1,987 ms | 2,354 ms |
| 16 | 2,970 ms | 3,335 ms | 3,709 ms |
| 24 | 3,540 ms | 3,973 ms | 13,005 ms |

Próg dla typowej puli 16 wynosi p95 > 5 ms. Nie został przekroczony, dlatego
v1 nie ogranicza korpusu próbkowaniem. Gdy benchmark albo live
`overhead_ms` przekroczy próg, przed dalszą aktywacją trzeba dodać hot-reload
`C7_NORMAL_PATH_SAMPLE_EVERY_N` (domyślnie co N-ta decyzja), z
deterministycznym hashem identyfikatora decyzji i osobnym testem pokrycia.

## K2

Ten instrument mierzy baseline **K2=OFF**, ponieważ K2 nie jest zbudowany.
Przed przyszłym flipem K2 obowiązuje replay tej samej puli w macierzy 2×2:

|  | K2 OFF | K2 ON |
|---|---:|---:|
| C7 OFF | baseline instrumentu | kontrfaktyk K2 |
| C7 ON | kontrfaktyk C7 | wspólny efekt C7×K2 |

Bez czterech komórek nie wolno przypisać interakcji jednej z flag.

## Rollback i aktywacja

Rollback instrumentu to hot `ENABLE_C7_NORMAL_PATH_LOG=false` albo brak klucza.
Kod jest addytywny i fail-safe. W tym worktree nie wolno modyfikować żywego
`flags.json`, deployować ani restartować usługi. Flip kill-switcha, C7, merge,
push i deploy pozostają osobną decyzją CTO/ownera.

## Wpis kandydata (przeniesiony z ZIOMEK_BACKLOG.md — plik był w cudzym WIP na masterze)

> **KANDYDAT 2026-07-27 — C7 NORMAL-PATH INSTRUMENT:** K1 obalił mierzalność normalnej ścieżki
> z dotychczasowych danych, więc flip C7 pozostaje HOLD mimo starszego replayu best-effort.
> Branch feat/c7-normal-path-instrument-20260727 dodaje default-OFF c7_normal_path.v1: pełna pula
> przed top[:16], dwa kanoniczne ramiona C7 OFF/ON, parity oracle realnego best/verdict/routing,
> stage score/OBJM/E2/gate, PII-free payload w istniejącym shadow recordzie i fail-safe.
> Benchmark puli 16: mediana 2,970 ms, p95 3,335 ms (sampling niewymagany przy progu 5 ms).
