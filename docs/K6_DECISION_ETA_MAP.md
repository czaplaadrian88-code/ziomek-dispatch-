# K6 — mapa kontraktu `decision_eta.v1` P50/P80

**Stan:** source-only, worktree `fix/k6-pred-op-p50p80-20260727`, baza
zadania `8153a0afc`; zero deployu, restartu, flipa i zapisu runtime.
**Model/effort:** Sol/high — przekrojowy kontrakt telemetryczny i wiążąca
bramka KPI. **Cel:** odzyskać obliczalność D5, bez zmiany decyzji silnika.

## Root cause i owner kontraktu

`decision_eta_log.py` zapisywał finalny timestamp `pickup_eta_at`, ale nie
etykietował kwantyli leak-free kalibratora. Timestampu nie wolno uznać za P80.
Jednocześnie `eta_calib_serving.py` wyciągał z modelu tylko P80 zwycięzcy,
mimo że ta sama inferencja zwraca P50 i P80. Jednorazowy checker
`/root/handover/gps_remeasure_20260725.py` mógł więc policzyć stare ETA, ale
uczciwie zwracał `late-band P80=N/D`.

Własność jest rozdzielona bez duplikacji polityki:

- `eta_calib_serving.predict_pickup_quantiles_batch` — jedyny producent pary
  P50/P80 i jej provenance; target to poślizg pickup względem
  `czas_kuriera`, w minutach;
- `decision_eta_log.py` — jedyny writer JSONL i jedyne miejsce dodające
  wynik producenta do snapshotu każdego dostępnego kandydata;
- `tools/gps_decision_eta_remeasure.py` — read-only konsument; nie wylicza
  ani nie zgaduje predykcji, tylko mierzy etykietowaną parę wobec GPS.

## Writerzy i ścieżki bliźniacze

| miejsce | rola | writer/consumer | dotknięte | powód / test |
|---|---|---|---|---|
| `eta_calib_serving.py:predict_pickup_quantiles_batch` | kanoniczna inferencja pickup P50/P80 | producer | TAK | jeden load modelu na pulę, inferencja zwraca oba kwantyle; monotoniczność i finite fail-closed; wersja + provenance |
| `decision_eta_log.py:_candidate_snapshot` | serializacja kandydata | jedyny writer pól `pred_op`/`p80` do JSONL | TAK | opcjonalny `prediction_context`; utrata modelu nie usuwa bazowego snapshotu |
| `decision_eta_log.py:_emit` | append `decision_eta_log.jsonl` | jedyny fizyczny writer | TAK | wspólny append batch, flaga i fail-safe bez zmian |
| `shadow_dispatcher.py` | finalny primary snapshot przed późniejszym outcome | caller/writer-hook | TAK | przekazuje decision-time `payload`; główny licznik coverage |
| `czasowka_scheduler.py` | ocena proaktywnej czasówki | caller/writer-hook | TAK | przekazuje `order_state`; pola nadal opcjonalne |
| `tools/reassignment_forward_shadow.py` | kontrfaktyk przerzutu | caller/writer-hook | TAK | przekazuje ten sam `order_event`, którym oceniono kandydatów |
| `tools/pending_global_resweep.py` | globalny resweep | caller/writer-hook | TAK | przekazuje wiersz z wejściem orderu |
| `plan_manager.py` | snapshot po durable CAS planu | caller/writer-hook | N-D | commit planu nie ma decision-time cech modelu ani pełnej puli; nie zgadujemy P50/P80, stare rekordy pozostają poprawne |
| `core/jsonl_appender.py` | atomowy append/lock | transport writera | N-D | format addytywny, mechanika append bez zmian |
| `core/jsonl_rotation.py` + `deploy/dispatch-v2-jsonl-logrotate.conf` | rotacja | transport/retencja | N-D | ścieżka i schema top-level bez zmian |

Ratchet `test_single_prediction_producer_and_all_nonplan_hooks_are_ratcheted`
blokuje drugi `def predict_pickup_quantiles_batch`, pominięcie producenta przez logger
oraz brak `prediction_context` w czterech ścieżkach z decision-time inputem.

## Konsumenci

| miejsce | rola | writer/consumer | dotknięte | powód / test |
|---|---|---|---|---|
| `tools/decision_eta_coverage.py` | walidacja v1 i primary coverage | consumer/checker | N-D | nowe pola są opcjonalne; test legacy potwierdza brak regresji |
| `tools/gps_decision_eta_remeasure.py` | D4/D5 GPS↔CID↔outcome↔snapshot | consumer/checker | TAK | kanoniczny, repozytoryjny następca one-offu; osobno liczy eligible, timestamp-complete i labelled-complete |
| `/root/handover/gps_remeasure_20260725.py` | historyczny pomiar 25.07 | consumer historyczny | N-D / superseded | read-only artefakt poza worktree; nie wolno go modyfikować; nowy checker przejmuje kontrakt |
| `tools/eta_ground_truth.py:KPI_BINDING_V1` | owner-bound D4/D5 | kontrakt progów | N-D, importowany | `min_n=200`, coverage 60% i progi D5 bez kopii/liczb lokalnych |
| `tools/eta_calibration/*` | trening/champion-challenger | calibrator | N-D | nie czyta `decision_eta.v1`; K6 nie rekalibruje i nie promuje modelu |
| raporty/handoff/ledger `eta.gps-remeasure-checkpoint` | operator | consumer werdyktu | N-D live | przejście FSM i nowy pomiar dopiero po danych oraz osobnym procesie owner/CTO |
| `tests/test_decision_eta_log_2026_07_21.py` | kontrakt bazowego loggera | test consumer | TAK przez regresję | stare snapshoty i hooki nadal działają |
| `tests/test_k6_decision_eta_prediction.py` | oracle K6 | test consumer | TAK | RED→GREEN, mutation-ratchet, legacy i `n>=200` |

## Addytywny kontrakt kandydata

Pola istnieją tylko, gdy model i input decision-time dają poprawną, monotoniczną
parę:

```json
{
  "pred_op": 4.25,
  "p80": 7.5,
  "prediction_version": "eta_pickup_quantiles.v1",
  "prediction_provenance": {
    "producer": "eta_calib_serving.predict_pickup_quantiles_batch",
    "model_artifact_sha256_12": "…",
    "feature_contract_version": "decision_time_v2",
    "target": "pickup_slip_vs_czas_kuriera_min",
    "quantiles": {"pred_op": 0.5, "p80": 0.8}
  }
}
```

`pred_op` jest w tym kontrakcie punktem P50, nie historycznym aliasem
„operational quantile”. `p80` jest P80. Brak dowolnej części provenance albo
odwrócona para `p80 < pred_op` jest nieobliczalna, nigdy imputowana.

## Bramka próby i CI

Checker importuje D4/D5 z `KPI_BINDING_V1`, więc nie powstał drugi próg:

- `eligible_n` — ścisły denominator GPS/CID/outcome;
- `complete_n` — przypadki ze starym timestampem pickup (ciągłość raportu);
- `prediction_complete_n` — przypadki z pełną, wersjonowaną parą P50/P80;
- `n>=200` i coverage `>=60%` obowiązują labelled complete-cases;
- stare rekordy dają jawne `HOLD_PREDICTION_UNCOMPUTABLE`, nie wyjątek ani
  fałszywe zero;
- CI jest ustalone przed danymi: paired percentile bootstrap 95%,
  2000 replik, seed `20260727` dla improvement vs engine; Wilson 95% dla
  udziału spóźnień P80.

## Wpływ, koszt i rollback

Zmiana jest obserwacyjna. Nie dotyka feasibility, scoringu, selekcji, planu,
promesy ani wyświetlanego ETA. Dodanie P50 nie uruchamia drugiej inferencji:
`predict_quantiles()` już zwraca mapę 0.5/0.8/0.9; koszt to odczyt dwóch liczb,
walidacja i serializacja. Dodatkowe inferencje pełnej puli występują tylko po
włączeniu istniejącego loggera `ENABLE_DECISION_ETA_LOG` i poza lejkiem decyzji.
Batch ładuje/stat-uje model raz na pulę. Mikrobenchmark samej warstwy kontraktu
na 100 000 kandydatów: 0,201501 s, czyli 2,015 µs/kandydata i dokładnie
1 load/pulę. To nie obejmuje realnej inferencji LightGBM: sandbox K6 nie ma
LightGBM ani dostępu do artefaktu live, więc jej koszt musi zostać zmierzony
w kanonicznym venv przed przyszłym restartem/deployem; brak tego pomiaru nie
zmienia source-only poprawności, ale pozostaje bramką operacyjną.

Rollback source-only: revert commitów K6. Po przyszłym wdrożeniu stare rekordy
pozostają czytelne, a zatrzymanie nowych pól wymaga cofnięcia kodu/restartu
`dispatch-shadow` wyłącznie za osobnym ACK ownera. K6 nie wykonuje tego restartu.
