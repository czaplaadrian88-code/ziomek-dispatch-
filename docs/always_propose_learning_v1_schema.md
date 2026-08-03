# A-3 always-propose — kontrakt obserwacji i learning-loop v1

Status implementacji: source-only, cały kontrakt za
`ENABLE_ALWAYS_PROPOSE=false`. Włączenie, deploy i wykonanie decyzji wymagają
osobnej bramki ownera.

## Jedno źródło prawdy

- Pełna decyzja silnika pozostaje wyłącznie w
  `scripts/logs/shadow_decisions.jsonl`.
- Końcowa para `[kontekst decyzji -> późniejszy wybór człowieka]` trafia do
  istniejącego `dispatch_state/learning_log.jsonl`. Jego trwałym writerem dla
  assignmentu pozostaje `panel_watcher._append_learning_record` z first-wins
  projekcją SQLite `panel_assignment_learning`.
- `dispatch_state/always_propose_decision_context.json` jest wyłącznie
  kompaktowym indeksem przyczynowym między tymi dwoma zdarzeniami, nie drugim
  logiem. Ma jednego writera w `dispatch-shadow`, zapis `0600`, flock,
  `fsync -> os.replace -> fsync(dir)`, TTL 48 h i CAS-pop po exact
  `decision_event_id`.

OFF oznacza brak nowych pól w shadow, brak indeksu, brak odczytu audytu i brak
rekordów D1/D3. Żaden element tego kontraktu nie zmienia `verdict`, `best`,
auto-route, planu ani przypisania.

## `always_propose.best_of_worst.v1`

Nested pole `proposal_best_of_worst` występuje tylko dla końcowego `no_solo`
przy fladze ON. Reguła jest deterministyczna i HARD-before-SOFT:

1. HARD-safe oznacza dokładnie `feasibility_verdict in {MAYBE, YES}` oraz
   materialny plan z kanonicznej próby R29 solo.
2. HARD-safe zawsze wygrywa z `NO`, brakiem planu/pozycji lub błędem oceny.
3. Gdy każdy kandydat w dozwolonej klasie ma porównywalny
   `pickup_dist_km`, kolejność to najmniejsza odległość, następnie najwyższy
   już policzony score, następnie `identity.schema.canon_cid`.
4. Gdy choć jednemu brakuje porównywalnego dowodu SOFT (np. brak GPS),
   odległość i score są ignorowane dla całej klasy, a remis rozstrzyga
   kanoniczny CID. Brak GPS nie dostaje ani ukrytej kary, ani bonusu.
5. Jeśli każdy członek floty jest HARD-NO, ten sam klucz wskazuje diagnostyczny
   CID, ale `hard_safe=false`, `recommend_only=true` i
   `proposal_output_type=COORDINATOR_ESCALATION`. Nie powstaje wykonywalny best.
6. Publiczna granica jest totalna. Przy `fleet_count>0` bez obserwowalnego CID
   albo przy dowolnym błędzie wewnętrznym nadal powstaje jawna diagnostyczna
   `COORDINATOR_ESCALATION` z `candidate=null`, stabilnym kodem w
   `diagnostic` oraz logiem klasy wyjątku. Instrument nigdy nie przerywa
   `select_and_emit`; już zbudowany `KOORD/no_solo` pozostaje decyzją.

Pola: `schema`, `selection_rule`, `fleet_count`,
`observed_candidate_count`, `hard_safe_count`, `soft_evidence_complete`,
`proposal_output_type`, `recommend_only`, `candidate` oraz opcjonalne
`diagnostic`/`invariant_violation`/`diagnostic_error`. Kandydat zawiera
wyłącznie CID, `diagnostic_marker`, wynik feasibility, reason, odległość,
`a3_solo_score` i `has_plan`; bez nazwy, adresu, GPS i trasy. Skala R29 nigdy
nie używa ogólnego pola `score`.

## Indeks `always_propose.decision_context_index.v1`

Top-level ma `schema` oraz mapę `entries[order_id]`. Wpis
`always_propose.decision_context.v1` zawiera:

| Pole | Znaczenie |
|---|---|
| `order_id`, `decision_event_id`, `decision_at` | exact tożsamość decyzji shadow |
| `proposal_output_type`, `coordinator_escalation_class`, `verdict`, `reason` | typ, nazwana klasa eskalacji i uzasadnienie silnika |
| `best_courier_id`, `best_score` | oryginalny best lub `null` |
| `proposal_best_of_worst` | PII-free nested podpowiedź albo `null` |
| `source` | zawsze `shadow_decisions` |
| `stored_at`, `expires_at` | lifecycle indeksu, nie czas decyzji/assignmentu |

Powtórny zapis tego samego `decision_event_id` jest first-wins no-op. Nowszy
event tego samego zamówienia atomowo zastępuje stary. Konsument usuwa wpis
tylko compare-and-swapem po exact ID, więc spóźniony callback nie skasuje
nowszej decyzji.

## Para `always_propose.learning_pair.v1`

Wspólne pola rekordu w `learning_log.jsonl`:

| Pole | Znaczenie |
|---|---|
| `learning_event_id` | stabilny hash pary, klucz przyszłego wyjaśnienia |
| `engine_decision_event_id` | exact `shadow.event_id` |
| `assignment_lifecycle_event_id` | exact durable `COURIER_ASSIGNED.event_id` |
| `lifecycle_event_id` | alias engine ID dla zgodności z istniejącym joinem E1 |
| `coordinator_escalation_class` | `STALE`, `GEOMETRY`, `COMMIT`, `DIFFICULT` albo jawne `UNKNOWN` |
| `proposed_courier_id`, `actual_courier_id` | wskazanie silnika i późniejszy H |
| `assignment.assigned_at` | czas uchwycony przed exact durable assignmentem |
| `assignment.source` | `panel_initial`, `panel_diff` lub `panel_reassign` |
| `assignment.assigned_by` | pseudonimowana atestacja albo jawny `UNKNOWN` |
| `engine_context` | kompaktowy wpis indeksu; pełna decyzja zostaje w shadow |

`assigned_by.status=ATTESTED` wymaga jednego skutecznego, niedawnego assignu
`mode=live` z audytu konsoli. Docelowy schema ma `kind=assign`; bieżący writer
panelu bez tego pola jest akceptowany wyłącznie przez wąski podpis
`gastro_assign.py` + obecność `courier` + jawne `ok=true, rc=0`. Pole
`audit_schema` zapisuje użyty wariant. Oba przechodzą wspólną allowlistę
`core.learning_actor`. Surowa tożsamość nigdy nie trafia do learning; emitowany
jest `actor_sha256:*` i nieodwracalny `audit_ref`. Brak, konto testowe lub remis
daje `UNKNOWN` z przyczyną, nigdy zgadywany aktor. Ten sam helper jest używany
przez writer i `decision_episode_v1`, więc nie ma dwóch polityk filtracji ani
dwóch sygnatur legacy.

### D1 — rozwiązanie eskalacji

- `action=COORDINATOR_ESCALATION_RESOLVED`
- `learning_event_type=proposal_output_type=COORDINATOR_ESCALATION`
- `coordinator_escalation_class` ma jednego ownera w
  `core.proposal_output`: `state_likely_stale→STALE`,
  `geometry_blind_fallback→GEOMETRY`, `commit_divergence_gate→COMMIT`,
  `difficult_geometry_redirect→DIFFICULT`; każde inne źródło jest jawnie
  `UNKNOWN`
- `reason` zachowuje reason eskalacji silnika
- actual CID, czas, source i actor pochodzą z exact assignmentu

`no_solo` i bramki jakości pozostają rozdzielone od czterech nazwanych klas
przez explicit `UNKNOWN`, zamiast mieszać się pod niejawny catch-all.

Techniczny hold na `KOORDYNATOR_ID` nie jest finalnym resolution D1 i nie
zużywa kontekstu A-3. Zachowuje jednak dotychczasowy legacy `PANEL_OVERRIDE`;
późniejszy assignment realnego kuriera dopisuje osobny
`COORDINATOR_ESCALATION_RESOLVED`. Dzięki temu włączenie A-3 nie wycina
historycznego strumienia KPI/replay.

### D3 — automatyczny OWNER_EXCEPTION

Gdy ręczny actual CID różni się od best silnika dla
`EXECUTABLE_PROPOSAL`/`LEAST_DAMAGE_ALERT`:

- zachowane `action=PANEL_OVERRIDE` dla obecnych konsumentów;
- `learning_event_type=proposal_output_type=OWNER_EXCEPTION`;
- `reason="nieokreślony"` bez pytania blokującego operatora;
- `explanation.status=PENDING` i
  `explanation.explains_learning_event_id=<learning_event_id>`.

Późniejsze wyjaśnienie ma być osobnym append-only zdarzeniem wskazującym ten
sam `learning_event_id`; nie wolno mutować historycznej pary ani wymagać
wyjaśnienia w chwili przypisania.

## Konsument analityczny

`tools/decision_episode_v1.py` akceptuje także
`COORDINATOR_ESCALATION_RESOLVED`, dołącza nested best-of-worst do zapisanej
puli jako namespaced overlay i łączy exact-first. Pełny `best/alternatives`
jest bazą; diagnostyka nie nadpisuje kanonicznego `score` ani pozostałych cech:

1. assignment po `assignment_lifecycle_event_id`;
2. shadow po `engine_decision_event_id`;
3. atestację aktora bezpośrednio z pseudonimowanego `assigned_by`, a dla
   starszych rekordów przez dotychczasowy audit join.

Brak joinu nadal daje `HOLD`; narzędzie nie imputuje świata ani wyniku
kontrfaktycznego.
