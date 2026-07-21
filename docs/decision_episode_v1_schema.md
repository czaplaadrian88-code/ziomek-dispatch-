# `decision_episode_v1` — schema i kontrakt censusu

## Cel i granica prawdy

`tools/decision_episode_v1.py` jest deterministycznym ekstraktorem read-only.
Populacja wejściowa to zapisane rekordy `PANEL_OVERRIDE` i `PANEL_AGREE` z
`learning_log`; narzędzie nie tworzy epizodów dla decyzji, których nie ma w
logu. Nie imputuje kandydatów, cech, aktora, czasu zmiany ani outcome.

Epizod opisuje obserwowaną decyzję/lifecycle, a nie parę treningową z
udowodnionym kontrfaktycznym wynikiem. `REASSIGN` jest osobną kategorią i nie
wchodzi do mianowników analizy pierwszego wyboru.

## Uruchomienie i zapis

Domyślnie pełny bundle (`episodes` + `census`) trafia na stdout:

```bash
python3 tools/decision_episode_v1.py
```

Sam raport jakości:

```bash
python3 tools/decision_episode_v1.py --census-only
```

Źródła są parametryzowane przez `--learning`, `--shadow`, `--audit`,
`--events-db`, `--gps`, `--outcomes`, `--restaurant-dwell` i
`--courier-ground-truth`. Domyślne ścieżki wskazują produkcyjne ledgery, ale
narzędzie otwiera je wyłącznie do odczytu. JSONL jest czytany wraz z rotacjami
`.N`/`.N.gz` przez kanoniczny `tools/_rotated_logs.py`.

Bez `--out` ekstraktor nie tworzy żadnego pliku. `--out PATH` jest jedyną
operacją zapisu; zapis jest atomowy do istniejącego katalogu i ma tryb `0600`.
Output nie zawiera czasu uruchomienia, więc dwa biegi na identycznych bajtach
wejścia i z tymi samymi argumentami dają identyczny JSON.

## Tożsamość epizodu i klasy decyzji

`decision_key` wybierany jest bez zgadywania, w kolejności:

1. `learning.lifecycle_event_id`;
2. `shadow.event_id` z jednoznacznie połączonego snapshotu;
3. `fallback_sha256` z `order_id|proposal_at|actual_courier_id`.

`decision_key_source` zapisuje użyty wariant. Historyczne dane bez
`lifecycle_event_id` mogą dać ten sam `shadow.event_id` kilku obserwacjom. Taka
kolizja nie jest sztucznie rozstrzygana: `joins.lifecycle_key=AMBIGUOUS`,
`JOIN_AMBIGUOUS` i `HOLD`, a census podaje liczbę grup/rekordów kolizji.
`episode_id` jest unikalnym, stabilnym SHA-256 klucza oraz obserwowanego
czasu/akcji/H; identyfikuje rekord epizodu, ale nie udaje brakującej tożsamości
lifecycle i nie zawiera danych wprost.

`category`:

- `FIRST_ASSIGNMENT` — pierwsze przypisanie;
- `REASSIGN` — `panel_source=panel_reassign`, jawny różny `previous_cid` albo
  wcześniejsze przypisanie tego zamówienia do innego kuriera. Dwa wpisy tego
  samego pierwszego przypisania (`_diff`/`_packs`) nie tworzą reassignu.

`first_choice_eligible=false` dla `REASSIGN`.

## Joiny exact-first

Każdy join ma `status` (`UNIQUE`, `UNMATCHED`, `AMBIGUOUS`), `method`,
`match_count` i podpisany `delta_seconds`. Zero lub wiele dopasowań nie jest
naprawiane przeszukiwaniem dalszej historii. Poza assignment/shadow/actor
emitowany jest wynik unikalności hierarchicznego `lifecycle_key`.

### Przypisanie

1. Exact po `lifecycle_event_id`, z kontrolą `order_id` i `courier_id`.
2. Fallback po tej samej parze `order_id` + H, wyłącznie w oknie ±30 s od
   rekordu learning.

### Snapshot wyboru

1. Osadzony `learning.decision`, tylko gdy zgadzają się `order_id`,
   `event_id` i najlepszy kandydat Z.
2. Exact po `shadow.event_id`.
3. Fallback: najnowszy snapshot dla tego samego zamówienia i Z, nie późniejszy
   niż decyzja oraz nie starszy niż 15 minut. Remis najnowszego czasu jest
   `AMBIGUOUS`; ekstraktor nie cofa się do wygodniejszego rekordu.

### Atestacja aktora konsoli

Join używa `order_id` + zapisanej nazwy H + czasu przypisania (exact, potem
±30 s). Nazwa służy tylko do joinu i nie jest emitowana. Do joinu trafiają
wyłącznie wykonane `mode=live`, `kind=assign`; `shadow`, `edit`, `cancel` i
`parcel_assign` są wykluczone.

Snapshot przejściowy z 21.07 nie ma `kind` dla części wpisów. Jawna normalizacja
legacy uznaje rekord za assign wyłącznie przy pełnym podpisie:
`mode=live`, brak `kind`, komplet pól audytu, `ok=true`, `rc=0` i komenda
`gastro_assign.py`. Sam `mode=live` nigdy nie wystarcza.

Aktora dopuszcza fail-closed allowlista domen (`nadajesz.pl`) oraz jawna lista
kont testowych/technicznych. E-mail nie trafia do outputu; poprawny aktor ma
stabilny identyfikator `actor_sha256:<16 hex>`.

`actor_provenance`:

- `ACTOR_ATTESTED_CONSOLE` — jednoznaczny, nietestowy wpis live assign;
- `ACTOR_UNKNOWN_GASTRO_DIRECT` — brak atestacji bezpośredniego przypisania;
- `ACTOR_TEST_FILTERED` — dopasowany wpis odfiltrowanego aktora testowego.

## Schema epizodu

Najważniejsze pola top-level:

| Pole | Typ / znaczenie |
|---|---|
| `schema_version` | zawsze `decision_episode_v1` |
| `episode_id`, `decision_key`, `decision_key_source` | stabilna tożsamość i provenance |
| `lifecycle_event_id`, `shadow_event_id`, `order_id` | bezpośrednie klucze źródłowe lub `null` |
| `category`, `first_choice_eligible` | pierwsze przypisanie kontra reassign |
| `action` | `OVERRIDE` albo `AGREE` |
| `panel_source` | techniczna droga zapisu, nigdy dowód osoby |
| `cohort` | `POST_A8` albo `PRE_A8` |
| `proposal_at`, `assignment_at`, `learning_at` | UTC ISO-8601 albo `null` |
| `proposed_courier_id`, `actual_courier_id` | Z i H zapisane w learning |
| `proposed_score_recorded`, `latency_s` | wartości direct z learning lub `null` |
| `actor`, `actor_id`, `actor_provenance` | wynik atestacji bez e-maila |
| `joins` | wyniki joinów assignment/shadow/actor |
| `recorded_pool` | rozmiary i ID wyłącznie zapisanej puli |
| `proposed_candidate`, `human_candidate` | bezpieczna allowlista bezpośrednio zapisanych cech |
| `decision_context` | bezpieczna allowlista `pickup_ready_at`, verdict i OSRM meta |
| `outcomes` | osobne obserwable, proxy i okna +15/+30/+60 |
| `source_coverage` | booleany pokrycia źródeł dla epizodu |
| `truth_class` | klasa prawdy obserwowanej decyzji |
| `comparison_truth_class` | prawda porównania H kontra Z |
| `analysis_state` | `ELIGIBLE` albo fail-closed `HOLD` |
| `missing_reasons` | wyłącznie kanoniczne enumy braków |

`recorded_pool.candidate_count` liczy unikalne `courier_id` z `best` i
`alternatives`; pierwszy zapis wygrywa przy historycznym duplikacie best.
`world_complete=false`: top-16 (w praktyce zwykle mniej alternatyw) nie jest
pełnym zapisem świata wyboru. H nieobecny w jednoznacznie połączonej puli daje
`human_candidate=null`, `OUT_OF_RECORDED_POOL` i `HOLD`. Nie powstaje
syntetyczny wektor H.

Allowlista cech kandydata obejmuje zapisane bezpośrednio cechy czasu/dojazdu,
planu, rozrzutu, R6, committed pickup, obciążenia i feasibility. Z `plan`
emitowane są tylko `pickup_at`, `total_duration_min`, `strategy` oraz
`predicted_delivered_at` dla badanego zamówienia. Nazwy, adresy, współrzędne,
telefony, `bag_context` i pełna sekwencja trasy są wykluczone.

## Outcome i cenzorowanie

Każdy epizod ma okna `plus_15m`, `plus_30m`, `plus_60m` liczące wyłącznie:

- nowe zamówienia;
- pierwsze przypisania obserwowanego Z pod faktycznie zrealizowaną gałęzią H;
- istniejący wcześniej backlog przypisany w oknie.

To jest fakt o użyciu Z po rzeczywistym wyborze H, nie kontrfakt ani efekt
wyboru. Brak historii grafiku oznacza w D1 `censored=true`,
`shift_exposure_state=SHIFT_EXPOSURE_UNKNOWN`; liczników nie wolno traktować
jak pełnej ekspozycji po końcu zmiany. Nakładające się okna tego samego Z są
łączone w stabilny `overlap_group_id`.

Obserwable outcome są rozdzielone:

- `restaurant_last_inside_at` — ostatni punkt wewnątrz geofence restauracji;
- `status_pickup_at` — status/przycisk pickup;
- `delivery_arrival_at` — przyjazd do geofence dostawy;
- `status_delivered_at` — status/przycisk delivery;
- `proxy_restaurant_to_arrival_min` i
  `proxy_status_pickup_to_status_delivered_min` — dwa osobne proxy;
- `r6_physical_possession_to_handoff_min=null` — possession i handoff nie są
  związane obserwacją;
- `truth_state=HANDOFF_UNBOUND`, `truth_class=UNIDENTIFIABLE` dla fizycznego R6.

Geofence-arrival nie jest handoffem. Pole historyczne
`physical_delivered_at` z `gps_delivery_truth` jest emitowane wyłącznie jako
`delivery_arrival_at`. `decision_outcomes` służy wyłącznie do licznika
pokrycia/parytetu; nie wypełnia primary observables ani fizycznego R6.

## Truth taxonomy

Pełny enum ma cztery klasy:

- `OBSERVED` — wartość bezpośrednio zapisana przez instrument źródłowy;
- `SAME_WORLD_REPLAY` — wynik tego samego świata z replayem i zwalidowanym
  oracle; dziś niedostępny i **nieemitowany w D1**;
- `STRUCTURAL_STRESS` — kontrolowany test strukturalny, nie estymata historii;
  **nieemitowany w D1**;
- `UNIDENTIFIABLE` — danych nie wystarcza do identyfikacji tezy.

Top-level `truth_class=OBSERVED` mówi tylko, że decyzja H/Z pochodzi z
`learning_log`. `comparison_truth_class=UNIDENTIFIABLE`: sam log nie mówi, co
stałoby się po niewybranym Z. Braków nie wolno zmieniać w zera ani wypełniać
poprzednią wartością.

## Enumy braków

`missing_reasons` może zawierać wyłącznie:

| Enum | Znaczenie |
|---|---|
| `ACTOR_UNKNOWN` | brak nietestowej atestacji osoby |
| `JOIN_AMBIGUOUS` | wymagany join ma zero lub wiele dopasowań |
| `PRE_A8_CONTAMINATED` | rekord sprzed `2026-07-19T23:39:21Z` |
| `OUT_OF_RECORDED_POOL` | H nie występuje w jednoznacznie zapisanej puli |
| `WORLD_INCOMPLETE` | brak pełnego `world_record` i ekspozycji całej floty |
| `OUTCOME_PROXY_ONLY` | dostępne są proxy, nie fizyczny outcome KPI |
| `HANDOFF_UNBOUND` | brak wiązania przekazania przesyłki klientowi |
| `SHIFT_EXPOSURE_UNKNOWN` | brak zweryfikowanego końca zmiany H |

Kohorta potwierdzająca zawiera wyłącznie rekordy z `proposal_at >=
2026-07-19T23:39:21Z`. Późniejszy zapis learning nie przenosi wcześniejszej
propozycji do kohorty post-A8. Starsze propozycje pozostają osobnym `PRE_A8`
censusem i zawsze niosą `PRE_A8_CONTAMINATED`.

## Census i mianowniki

Raport liczy osobno `POST_A8` i `PRE_A8`:

- wszystkie `OVERRIDE`/`AGREE` z learning oraz osobno populację pierwszego
  wyboru po wyłączeniu `REASSIGN`;
- `unique_join_rate`: unikalny join assignment, shadow **i lifecycle key** /
  wszystkie rekordy pierwszego wyboru; raport podaje też osobne stopy
  assignment, shadow i actor;
- `human_in_recorded_pool_rate`: H w puli / rekordy pierwszego wyboru typu
  `OVERRIDE` z jednoznacznym shadow (AGREE, gdzie H=Z z definicji, nie zawyża
  tej miary; brak joinu nie udaje H poza pulą);
- `n_actor_clean`, pokrycie atestacją i rozkład pseudonimów po filtrze testów;
- pokrycie `learning_log`, `shadow`, `events_db`, `eta_ground_truth`,
  `gps_delivery_truth` i dodatkowo `decision_outcomes`;
- rozkład wszystkich ośmiu enumów braków, również gdy licznik wynosi zero.

`eta_ground_truth` jest wspólnym polem pokrycia obserwabli z
`restaurant_dwell`/`courier_ground_truth`; ich surowe dostępności są osobno w
`source_inventory`. Liczniki są opisowe — D1 nie wykonuje testów istotności,
nie trenuje rankera i nie wydaje werdyktu o przewadze H ani Z.
