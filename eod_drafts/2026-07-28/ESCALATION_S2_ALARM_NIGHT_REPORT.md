# Noc 2026-07-28 — drabina S1→S2→S3 (OFF/shadow, ZERO live)

## Werdykt wykonawczy

Kod A–D jest zbudowany wyłącznie za czterema nowymi flagami `default=False`.
Nie zmieniono `flags.json`, runtime state ani danych; nie wykonano flipu,
deployu, restartu, Telegrama ani APK. Poranna promocja pozostaje `HOLD` do
kanonicznej suity w venv, blind review i decyzji CTO/ownera.

Model/effort: `sol` / `xhigh` — root cause przekrojowy przez feasibility,
selekcję, plan-recheck i serializację, z wysokim kosztem błędu termicznego.

## A — kanoniczny carry i `carry_eval.v1`

Flaga: `ENABLE_CARRY_CANON_V2` (OFF).

- Kanoniczny owner: `core/carry_freshness.py`.
- Definicja: `carry = handoff - possession`; handoff to
  `predicted_delivered_at`, czyli czas po dwellu dropoff.
- Źródło possession jest jawne: fizyczny event z provenance = `bound`;
  `picked_up_at` i planowany pickup = `proxy`; brak = `UNEVALUABLE`, nigdy 0.
- `feasibility_v2` wylicza jeden obiekt `carry_eval.v1`; Alarm, S2 i selection
  konsumują ten obiekt bez ponownej derivacji.
- Pola per order: `carry_min`, `le_35`, `le_40`, `source`,
  `possession_source`, `handoff_source`; agregaty zawierają `max_carry_min`,
  `all_le_35`, `all_le_40`.
- `route_simulator_v2`, `plan_recheck`, guards WB2 i feasibility są spięte
  przez ten sam moduł carry.

Mutation/oracle:

- arrival zamiast handoff daje 32 min, literalny oracle wymaga 36 min;
- brak possession daje `UNEVALUABLE`;
- ratchet źródłowy wymaga importu kanonu w feasibility, route simulatorze
  i plan-recheck.

Shadow: top-level `carry_eval` oraz `best.carry_eval` /
`alternatives[].carry_eval` w `shadow_decisions.jsonl` po włączeniu flagi.

## B — kontrfaktyczny `alarm_certificate.v1`

Flaga: `ENABLE_ALARM_CERTIFICATE_SHADOW` (OFF).

- Producent liczy pełną ocenioną pulę po pozostałych HARD-ach:
  - `NORMAL`: istnieje kandydat `<=35`;
  - `ALARM_CANDIDATE`: zero `<=35`, co najmniej jeden `(35,40]`;
  - `HARD_NO_CANDIDATE`: nikt `<=40`;
  - `UNEVALUABLE`: niewiedza blokuje dowód „zero <=35”.
- Dowód zawiera liczniki/cid kontrfaktu, fingerprint puli, decision order,
  TTL i scope dokładnego worka.
- Jedyny writer jest w `dispatch-shadow`; zapis snapshotu to
  temp → flush/fsync → chmod → atomic `replace` → fsync katalogu.
- Czytnik sprawdza wersję, TTL, scope, fingerprint i spójność liczników.
- Dowolny dict, wysokie EWMA ani certyfikat z niepełnym kontrfaktem nie
  otwierają capa/tolerancji. `lex_window_guards` i G4 czytają ten sam cert.

Mutation/oracle:

- usunięcie kontrfaktu, fingerprintu lub list cid unieważnia certyfikat;
- nieznany kandydat blokuje Alarm;
- reject z innego HARD-a nie może stworzyć kandydata Alarm;
- forged dict pozostawia strict 35/5;
- ratchet wymaga dokładnie writera `shadow_dispatcher._tick`, nie pipeline.

Shadow: top-level `alarm_certificate` w rekordzie decyzji oraz wersjonowany
snapshot `alarm_certificate.json` po włączeniu flagi.

## C — rdzeń sondy Strategii 2

Flaga: `ENABLE_STRATEGY2_PROBE_SHADOW` (OFF).

- Uruchamia się wyłącznie po zerze `MAYBE` w S1.
- Skanuje sloty co 5 minut od najbliższego przyszłego slotu do twardego
  `created_at + 90 min`.
- Każdy slot przechodzi przez ten sam `core.candidates.eval_courier` i
  `check_feasibility_v2` dla całej floty, z R27 i wszystkimi HARD-ami.
- Wynik jest uznany tylko przy `MAYBE` i kanonicznym carry
  `status=EVALUATED`, `all_le_35=true`.
- Kontekst `shadow_probe` wycina poboczne writery/capture, invalidację
  saved-plan i ledger diff; nie zmienia realnej decyzji.
- Brak flagi A daje jawny `HOLD`, brak created/ready = `UNEVALUABLE`,
  awaria instrumentu = `INSTRUMENT_ERROR`.
- Nie ma żadnej propozycji ani writera do restauracji.

Mutation/oracle:

- test wymaga wszystkich kurierów w każdym slocie;
- przesunięcie/rozluźnienie horyzontu `created+90` czerwieni test;
- ratchet źródłowy wymaga prawdziwego evaluatora i `shadow_probe=True`.

Shadow: top-level `strategy2_probe.v1` oraz `order_created_at`.

## D — zamknięcie obejścia best-effort

Flaga: `ENABLE_HARD35_ENFORCE` (OFF).

- Hak jest przed legacy `ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT` i
  `ALWAYS_PROPOSE_ON_SATURATION`.
- Bez ważnego Alarmu przepuszcza wyłącznie kandydatów z carry `<=35`.
- Z ważnym Alarmem cap wynosi 40; `>40` nigdy nie staje się feasible.
- Gdy nikt mieści się w capie, najmniej szkodliwy plan pozostaje jawnie
  widoczny jako `KOORD/ALERT`, lecz nie wraca jako `PROPOSE`.

Mutation/oracle:

- para 34/37 musi wybrać 34;
- pula 37/42 bez Alarmu musi dać widoczny alert 37;
- Alarm przepuszcza 37, ale nie 41;
- ratchet blokuje przeniesienie haka za legacy always-propose.

Shadow/obs: `hard35_enforcement.v1` w candidate metrics albo top-level wyniku
least-damage.

## Mapa kompletności i bliźniaki

| Miejsce | Rola | Writer/consumer | Status | Powód / test |
|---|---|---|---|---|
| `core/carry_freshness.py` | jeden owner miary | writer obiektu | TAK | literal timestamps + dwell mutation |
| `route_simulator_v2.py` | cap-Z / handoff | consumer kanonu | TAK | WB2 ratchet |
| `feasibility_v2.py` | R6/HARD i carry record | writer/consumer | TAK | wspólny evaluator; OFF guard |
| `core/candidates.py` | cała flota / greedy twin | consumer | TAK | S2 używa `eval_courier`; probe bez writerów |
| `core/selection.py` | best-effort/ALWAYS_PROPOSE | consumer | TAK | D oracle + ordering ratchet |
| `plan_recheck.py` | G2/G4/lex reorder | consumer cert/carry | TAK | valid cert 35→40, forged→35 |
| `core/lex_window_guards.py` | próg 35/40 | consumer cert | TAK | WB2 cap oracle |
| `core/loadgov_snapshot.py` | G5 provenance | consumer cert | TAK | forged dict i EWMA nie otwierają loose |
| `dispatch_pipeline.py` | pełna pula + S2 orchestration | producer | TAK | ratchet full fleet/evaluator |
| `shadow_dispatcher.py` | serializer A+B + snapshot | jedyny writer | TAK | serializer A/B + atomic scope test |
| serializer `best` | LOCATION B | consumer | TAK | `best.carry_eval` test |
| serializer `alternatives` | LOCATION A | consumer | TAK | `alternatives[].carry_eval` test |
| restauracja / panel / APK / Telegram | pełne S2/live UX | writer/consumer | N-D | jawnie poza nocnym zakresem |
| physical GPS possession producer | ground truth | writer | N-D | schema/hook gotowe; brak potwierdzonego eventu |

## Flagi i lifecycle

Dodano do `common.py`, `ETAP4_DECISION_FLAGS` i
`tools/flag_lifecycle_registry.json`:

1. `ENABLE_CARRY_CANON_V2=false`
2. `ENABLE_ALARM_CERTIFICATE_SHADOW=false`
3. `ENABLE_STRATEGY2_PROBE_SHADOW=false`
4. `ENABLE_HARD35_ENFORCE=false`

Wykonano wymagany `flag_lifecycle_seed.py --merge`. Sandbox nie widział
zewnętrznych światów panel/apka, więc wynik seeda został odrzucony i rejestr
odtworzony z dokładnej niezmienionej bazy, po czym cztery wpisy nałożono
addytywnie. Końcowy checker: `547/547 curated`, `0 errors`; diff względem
bazy rejestru to wyłącznie jeden hunk z czterema wpisami.

## Testy i dowody

- RED-first: nowy plik testowy początkowo nie kolekcjonował się z brakiem
  `core.alarm_certificate` — oczekiwane RED fundamentu.
- Nowy oracle A–D: `21 passed`.
- Nowy oracle + WB2: `61 passed` (po końcowych testach kontrfaktu).
- Szerszy `HERMETIC_STRICT=1`: `118 passed`, 8 zastanych ostrzeżeń
  `PytestReturnNotNoneWarning`.
- Fokus baseline przed zmianą:
  `test_mode_defer_t24 + test_obj_f3_best_effort_r6_koord +
  test_v319f_integration + test_wb2_conditional_guards` = `56 passed`.
- Ten sam fokus po zmianie = `56 passed`; delta fail/skip = pusta.
- `py_compile` wszystkich zmienionych plików: PASS.
- flag lifecycle checker: PASS, 0 błędów.
- whitespace ratchet dla zmienionych plików: brak trailing whitespace.
- Serializer OFF porównany z niezmienioną bazą na identycznym, zamrożonym
  rekordzie: `OFF_BYTE_PARITY=PASS`, 4858 bajtów,
  SHA-256 `b6cc2e629ed7d8c09cc0cadd2065d376020508411979bb8684eb8714b6203c89`.

Pełna kanoniczna suita nie została uruchomiona: wykonanie venv jest zabronione
przez sandbox. Systemowy Python nie ma `ortools`, a test G5 wymaga
zewnętrznego `schedule_utils`; obserwowane 10 failów to wyłącznie
`ModuleNotFoundError: ortools`, plus jeden collection error `schedule_utils`.
Zgodnie ze zleceniem pełną suitę i blind review domyka rano CTO.

`tools/entropy_dashboard.py` uruchomiony read-only, ale w sandboxie widzi
`pliki żywego silnika: 0`, więc metryki auto są N/D i wymagają porannego
re-run w kanonicznym środowisku.

## Decyzje ownera — nie podjęto tej nocy

1. Czy i jak restauracja ma dostać pełną propozycję S2 późniejszego odbioru.
2. ACK na flip `ENABLE_HARD35_ENFORCE`.
3. ACK na live Alarm i semantykę capa 40.
4. Potwierdzenie absolutnego zakazu `>40`.
5. Kontrakt break-glass: kto, kiedy, audyt, TTL i rollback.
6. Źródło fizycznego GPS possession oraz kryterium awansu `proxy→bound`.

## Rekomendowana kolejność po nocy

1. Merge/deploy kodu ze wszystkimi czterema flagami OFF po pełnej zielonej
   suicie, blind review i rollback point.
2. Włączyć wyłącznie A (`carry_eval`) w shadow i zweryfikować coverage,
   proxy/bound oraz brak unknown.
3. Włączyć B producenta certyfikatu shadow; sprawdzić kontrfakt i TTL/scope.
4. Włączyć C sondę S2; obserwować co najmniej 2 dni i policzyć ratowalne worki.
5. Owner decyduje o pełnym S2/restauracji i break-glass; dopiero potem osobna
   implementacja/akceptacja ścieżki biznesowej.
6. Po dowodzie S2 i certyfikatu owner może włączyć Alarm live.
7. `ENABLE_HARD35_ENFORCE` włączyć jako ostatni krok, po smoke/rollback i ACK.

## Co zostaje

- pełne S2 z komunikatem/akceptacją restauracji;
- producent fizycznego GPS possession (obecnie carry jawnie oznacza proxy);
- co najmniej 2 dni shadow i replay na prawidłowym oracle;
- pełna suita venv, blind review, git diff/status/SHA, DoD driver i ledger
  gate — zlecone CTO, niedostępne w nocnym sandboxie;
- żaden live flip/deploy/restart nie został wykonany.
