# Z-P1-02 — kontrakt obserwowalnej prawdy ETA/SLA v1

Status: Faza A, measurement-only. Ten kontrakt nie zmienia ETA live, nie ustala
progu KPI i nie jest bramką promocji modelu.

## Wynik audytu semantyki

Repo nie ma obecnie kompletnego, historycznie wersjonowanego zdarzenia
potwierdzającego fizyczny pickup ani przekazanie dostawy klientowi. Dostępne są
dwa obserwowalne zdarzenia GPS:

1. Writer
   `/root/.openclaw/workspace/nadajesz_clone/panel/backend/tools/restaurant_dwell_detector.py`
   zapisuje `arrived_at_restaurant` oraz historycznie nazwane
   `departed_restaurant`. Drugie pole jest **ostatnim punktem GPS nadal wewnątrz
   geofence** wizyty wybranej blisko kliknięcia pickup. Nie potwierdza przecięcia
   geofence na zewnątrz, odebrania jedzenia ani wyjazdu.
2. `courier_ground_truth.gps_arrived_at` (`gps_arrival_source=app_geofence`) i
   wtórny `gps_delivery_truth.physical_delivered_at` o `confidence=high`
   potwierdzają przyjazd pod adres. Nie potwierdzają przekazania klientowi.

Dlatego schema v1 używa nazw `restaurant_last_inside_at` i
`delivery_arrival_at`. Etykiety "physical pickup", "restaurant departure" i
"customer handoff" byłyby mocniejsze niż dowód i są zakazane w KPI v1.

## Artefakty i rozdzielenie legacy

- `tools/eta_ground_truth.py` — nowy dataset/report:
  `eta_truth.dataset.v1`, `eta_truth.manifest.v1`, `eta_truth.report.v1`.
- `tools/eta_truth_map.py` — pozostaje bez zmian jako legacy click/proxy join.
  `eta_load_aware_replay.py` i `eta_load_aware_calibrate.py` nadal importują z
  niego `build_rows` i `_parse_day`. Nie zostały po cichu przepięte na inną
  semantykę.

Legacy `eta_truth_map` nie jest dowodem fizycznego KPI i nie może samodzielnie
promować nowego ETA.

## Okno, kohorta i predykcja

- Okno jest jawne i półotwarte: `[start, end)` w UTC.
- `as_of >= end` jest wymagane. Rekordy SLA/shadow/outcomes/GPS z jawną wersją
  po `as_of` są odrzucane przed wyborem latest-per-order. Manifest rozdziela
  hashe/liczby surowego inputu od pełnego źródła dostępnego `as_of`.
  `restaurant_dwell` i `courier_ground_truth` są niewersjonowanymi whole-map:
  ich hash ma jawny scope `full_snapshot_nonversioned`, a hash wyłącznie
  dataset-effective jest oznaczony jako niedostępny. Timestampy zdarzeń GPS po
  `as_of` są niedostępne.
- Bazowa kohorta jest kotwiczona przez przyciskowe `sla.delivered_at`. To tylko
  stabilny membership anchor, nie delivery ground truth.
- Każda metryka raportuje ten sam `denominator_base`; complete-case ma osobne
  `n`, coverage i fingerprint wsparcia związany z `base_cohort_hash`.
- `non_czasowka` nie oznacza jedzenia. `address_id` pochodzi z SLA, a gdy go
  tam brak — wyłącznie z ostatniego rekordu shadow istniejącego przed
  assignment. Paczka jest klasyfikowana tylko przez kanoniczny
  `common.is_paczka_order`; rekord po assignment nie może zmienić kohorty.
  Znane paczki są wykluczane. Brak/corrupt `address_id` daje `unknown`, obniża
  `package_exclusion_coverage` i blokuje kwalifikację KPI.
- Assignment anchor wymaga `actual_cid` i jednej z akcji:
  `PANEL_OVERRIDE`, `PANEL_AGREE`, `ASSIGN_DIRECT`, `F7AGREE`.
  `TIMEOUT_SUPERSEDED`, `no_verdict` i nieznane akcje nie kotwiczą predykcji.
- Predykcja to najnowszy shadow nie później niż decyzja operatora, a następnie
  kandydat z faktycznie przypisanym kurierem w tym konkretnym rekordzie. Nie ma
  skanowania wstecz do wygodniejszego kandydata ani fallbacku po assignment.

## Zdarzenia i metryki

Restaurant observable jest akceptowany tylko dla dokładnego
`_source=gps_geofence`, jawnego identycznego `courier_id` po obu stronach oraz high confidence (`n_in>=2` i,
gdy oba pola są dostępne, `min_dist<=radius`). Arrival i last-inside mają
osobne coverage.

Delivery precedence:

1. `app_geofence_arrival`;
2. `server_geofence_arrival` z `confidence=high`.

Klik pickup/delivery pozostaje w osobnych polach `proxy_*` i nigdy nie wypełnia
braku GPS. Metryki v1:

- `pickup_last_inside_error_min = restaurant_last_inside_at - predicted_pickup_at`;
- `delivery_arrival_error_min = delivery_arrival_at - predicted_delivery_at`;
- MAE, mean/median bias, p10/p90 i coverage per obserwowalna noga;
- wspólny complete-case obu nóg z osobnym support hash.

Dodatni błąd oznacza obserwowalne zdarzenie późniejsze od predykcji. Nie ma
progu PASS/FAIL, automatycznej rekomendacji ani wyboru kanonicznego KPI event.
Manifest zapisuje `canonical_kpi_event=unbound` oraz pustą listę progów.

## Lineage, prywatność i publikacja

- Outcomes i GPS są czytane rotation-aware (`.1`, `.N.gz`) i fail-loud przy
  uszkodzonym JSON. SLA/shadow korzystają z kanonicznego `ledger_io` dla
  domyślnych ścieżek.
- Każdy plik ma `path_id`, path, size, mtime_ns i SHA-256. Stat/hash jest
  sprawdzany przed i po odczycie; zmiana zestawu rotacji lub zawartości przerywa
  generowanie.
- Whole-map/derived-index (`restaurant_dwell`, `courier_ground_truth`,
  `gps_delivery_truth`) nie zachowuje pełnej historii wersji. Manifest ma dla
  nich `snapshot_reconstructability=false`, a CLI odrzuca historyczny `as_of`
  starszy niż mtime tych plików. To celowy fail-loud zamiast fałszywego replay.
- Lineage kodu zawiera git HEAD, dirty marker i SHA-256 dokładnej treści
  `eta_ground_truth.py` oraz zależności zachowania: `common.py`, `ledger_io.py`
  i `_rotated_logs.py`. Zbiorczy behavior fingerprint sprawia, że sam SHA
  commita nie udaje kodu z dirty worktree.
- Requested/effective `prediction-lookback-hours` i wyliczony `shadow_cutoff`
  trafiają do manifestu i raportu, ponieważ zmieniają korpus predykcji i coverage.
- Dataset używa sekwencyjnych `row_id` i pseudonimów kurierów w obrębie
  generacji. Nie emituje order id, courier id, nazw, adresów ani koordynatów.
- Trzy outputy muszą być różne, nie mogą kolidować z wejściami ani katalogami
  runtime. Są stagingowane jako `0600`. Przed publikacją plików manifest ma
  `complete=false`; kompletny manifest z generation id i hashami dataset/report
  jest atomowo podmieniany jako ostatni. Przerwany bundle jest wykrywalny.

## Mapa kompletności

| miejsce | rola | writer/consumer | dotknięte | powód | test/dowód |
|---|---|---|---|---|---|
| `sla_log.jsonl` | kohorta, click proxy | SLA writer / dataset v1 | TAK (read-only) | wspólny `[start,end)` i denominator | window/end-exclusive, as_of versions |
| `shadow_decisions.jsonl` | plan sprzed decyzji | shadow serializer / dataset v1 | TAK (read-only) | brak leakage po assignment | mutation probe post-assignment |
| `decision_outcomes.jsonl` | assignment anchor | timer + legacy writer / dataset v1 | TAK (read-only) | jawna schema/action/actual_cid | mixed-schema fail, allowlist oracle, rotations |
| `restaurant_dwell.json` | arrival + last-inside | zewnętrzny detector / dataset v1 | TAK (read-only) | strict GPS observable | source/confidence/as_of/coverage tests |
| `courier_ground_truth.json` | primary delivery arrival | courier app / dataset v1 | TAK (read-only) | preferencja bezpośredniego geofence | precedence/future fallback test |
| `gps_delivery_truth.jsonl` | secondary delivery arrival | validation rebuild / dataset v1 | TAK (read-only) | jawny high-confidence fallback | confidence, version, `.1/.2.gz` tests |
| `common.is_paczka_order` | wykluczenie paczek | SLA/preassignment shadow + kanon / dataset v1 | TAK | bez zgadywania food cohort i bez post-assignment leakage | address_id package oracle |
| `tools/eta_ground_truth.py` | dataset/manifest/report v1 | offline CLI | TAK | nowy kontrakt bez zmiany live | unit + CLI E2E + fault injection |
| `tools/eta_truth_map.py` | legacy click/proxy API | load-aware replay/calibrate | N-D | zachowanie bazowe wymagane przez aktywnych konsumentów | exact base diff + import smoke |
| `eta_load_aware_*` | aktywni konsumenci legacy | offline tools | N-D | brak zgody na zmianę ich semantyki | import `build_rows/_parse_day` |
| ETA live, flags, mapy kalibracji | decyzje biznesowe | runtime | N-D | Faza A tylko obserwuje | brak zmian w diffie |
| dashboard/alert | konsument KPI | brak zatwierdzonego kontraktu | N-D | KPI event i próg są decyzją biznesową | `canonical_kpi_event=unbound` |

## Otwarte decyzje i bezpieczny etap następny

Przed użyciem do promocji potrzebne są osobne decyzje biznesowe:

1. Które zdarzenie jest KPI pickup: last-inside GPS, przyszły potwierdzony exit,
   czy inne zdarzenie operacyjne?
2. Czy delivery KPI oznacza arrival pod adres, czy potwierdzone przekazanie?
3. Jaki minimalny package-exclusion coverage i GPS coverage dopuszcza werdykt?
4. Jakie progi MAE/bias/tail i koszt uboczny tworzą bramkę promocji?

Bezpieczny kolejny etap to fixture/replay na wersjonowanym snapshotcie poza
runtime, przegląd coverage i wspólnego supportu, a następnie minimum 2 dni
obserwacji log-only po zatwierdzeniu KPI. Deploy, restart, flaga lub zapis do
runtime wymagają osobnego ACK. Rollback kodu to jawny revert nowego modułu,
testu i tego dokumentu; legacy pozostaje nietknięte.
