# Kontrakt wyniku Ziomek Change Gate

## Źródło maszynowe

Wynik jest JSON-em zgodnym z
[`ziomek-change-gate-result-v1.schema.json`](../../../schemas/ziomek-change-gate-result-v1.schema.json).
Schema ma zamknięte obiekty, pełne `required`, typy i enumy. Nie dodawaj pól ani
nie pomijaj pól na podstawie trybu.

## Niezależne grupy faktów

- `model` zapisuje tier, exact model, effort, powód oraz stan i dowód atestacji.
- `role` zapisuje status, dowód i routing. Brak mechanicznej atestacji oznacza
  `UNATTESTED_NON_MAIN`. Status jest wyliczany wyłącznie z jednego jawnego
  `ROLE_ATTESTATION=...` widocznego w prompt-only blind input.
- `ack` zapisuje fakt ACK, exact scope i `requires_reask`; nie jest capability.
- `authority` pozostaje w całości `false` niezależnie od roli, ACK i disposition.
- `gates` opisuje następny tor, a nie pozwolenie skilla na wykonanie live.

## Strukturalna granica efektów

`effect_boundary` jest źródłem prawdy o powierzchni zmiany:

- `write_set` zawiera exact ścieżki objęte kandydatem;
- `mutation_surface` używa zamkniętych klas efektu;
- `read_only_no_effect=true` jest dozwolone tylko przy obu pustych listach.

Pozytywna analiza wymaga pustych list i `read_only_no_effect=true`. Pozytywny
kandydat lokalny wymaga niepustego staged write-setu, dokładnie powierzchni
`STAGED_ARTIFACTS` i `read_only_no_effect=false`. Wolna proza, w tym prefiks
`N-D:`, nie może zastąpić tych faktów ani zamaskować produktu, runtime, flag,
danych, usług, tmuxa, lease'u, discovery lub semantyki biznesowej.

Dla READY `STAGED_ARTIFACTS` `write_set` ma jedną semantykę: są to exact
zmieniane pliki runtime pakietu dokładnie tego kandydata. Każda ścieżka musi
być ściśle względna wobec repo, regularna, niesymlinkowana, pod exact
`staged_candidate_path` tego samego wpisu registry i należeć bajt-w-bajt do
`pin.candidate_artifacts.files[].path`. Porównanie odrzuca `..`, ścieżki
absolutne, backslash, puste i kropkowe segmenty, aliasy Unicode/case, sibling
prefix oraz root innego skilla. Szerokie `owned_paths` nie są allowlistą tego
pola: współdzielone registry, schema, eval i report pozostają poza granicą.

Publiczna readiness używa jednego zweryfikowanego kontekstu. Zanim dowolna
wartość registry wpłynie na blocker lub dyspozycję, cały obiekt przechodzi
trusted registry schema i wszystkie relacje. Następnie dla wybranego
`skill_id` pełny zbiór plików pakietu musi dokładnie równać się pinom w jednym
efektywnym `artifact_root`; każda ścieżka musi być względna i komponentowo
zawarta w staged root, regularna, bez symlinków, w trybie dokładnie `100644` i
zgodna z SHA-256. Polityka rootu to
`ALTERNATE_ALLOWED_AFTER_COMPLETE_EXACT_PIN_VALIDATION`: poprawny drugi skill i
exact kopia pakietu mogą użyć alternatywnego rootu, ale błędne bajty, brak,
symlink, plik specjalny, tryb wykonywalny lub cross-skill kończą się centralnym
`READINESS_CONTEXT_INVALID` i wyprowadzonym `HOLD`.

## Brief i kompletność

`sprint_brief` ma dokładnie pięć merytorycznych treści w prostym polskim:
problem/dowód, powierzchnie, efekt zachowania, ryzyka/testy/rollback i bramki
biznesowe. `delivery` wynika z roli: aktywny MAIN używa `OWNER_PRESENTED`, a
każdy non-MAIN `HANDOFF_RECORDED`.

`completeness.entries` używa pól `miejsce`, `rola`, `writer_consumer`, `status`,
`powod`, `test`. Liczba wpisów i statusów musi zgadzać się z licznikami:
`total = covered + not_applicable + unknown`. Każde `N-D` ma konkretny dowód
granicy; `unknown>0` oznacza `HOLD`.

## Dowody i entropia

Każdy wpis `tests` zawiera nazwę, status i dowód. `evidence` rozdziela baseline,
oracle, mutation, ON/OFF lub parity, regression, replay/impact i dowód loadera.
Statyczny corpus to `AUTHOR_STATIC_ORACLE`, nie behavioral PASS ani niezależne
review.

`entropy.status=NON_INCREASE` wymaga dowodu kierunku. Dla niezwiązanego zakresu
użyj `N-D`, a `entropy.evidence` rozpocznij od `N-D:` i opisz import/write/
serializer/consumer boundary.

## Relacje fail-closed

- Jedna zamknięta tabela relacji wylicza `blocker_codes`. Wynik musi zawierać
  dokładnie ten zbiór; nie wolno dopisywać ani ukrywać blockerów deklarowaną
  dyspozycją.
- Niepusty `blocker_codes` zawsze daje `HOLD`; pusty zbiór może dać READY tylko
  dla jednej jawnie obsługiwanej macierzy trybu, gate'ów i targetu. Każda inna
  kombinacja dostaje `UNHANDLED_STATE_COMBINATION` i `HOLD`.
- Nieważny albo niezweryfikowany kontekst readiness zastępuje pozostałą tabelę
  dokładnie jednym blockerem `READINESS_CONTEXT_INVALID`; caller nie może go
  ominąć przez bezpośrednie wywołanie result relations, corpus, blockerów ani
  dyspozycji.
- Zamknięte dodatnie lane'y używają kolejno tuple
  `independent_review/implementation/production_operation/activation`:
  `ANALYSIS_ONLY = NOT_REQUIRED/READY/N-D/N-D`, kandydat do review =
  `PENDING/READY/N-D/REVIEW_REQUIRED`, a lokalna implementacja staged =
  `NOT_REQUIRED/READY/N-D/REVIEW_REQUIRED`. Drift dowolnego z czterech pól
  blokuje READY.
- Każda dodatnia lane dopuszcza wyłącznie `oracle=AUTHOR_STATIC_ORACLE`.
  `INDEPENDENT` nie może być zadeklarowane przez autora i należy do osobnego
  fresh review; `N-D`, `MISSING` i `SELF_CONFIRMING` zawsze blokują READY.
- Każde READY wymaga `unknown=0`, wszystkich testów `PASS`, modelu i effortu co
  najmniej na floorze ryzyka oraz niepustych wymagań handoffu.
- `IMPLEMENTATION_CANDIDATE` READY wymaga baseline `PASS`, mutation `KILLED`,
  akceptowalnego oracle, rollbacku `READY` i spójnego targetu. `READY_FOR_REVIEW`
  wymaga `independent_review=PENDING` oraz targetu `INDEPENDENT_REVIEWER`;
  `READY_FOR_IMPLEMENTATION` wymaga `implementation=READY` i targetu
  `LOCAL_IMPLEMENTER`.
- READY z poprawną etykietą `STAGED_ARTIFACTS`, lecz choć jednym plikiem spoza
  registry-bound allowlisty dokładnie tego skilla, dostaje
  `CANDIDATE_WRITE_SET_OUTSIDE_REGISTRY_BOUNDARY` i `HOLD`.
- `ANALYSIS_ONLY` R0 może użyć baseline/mutation/rollback `N-D` wyłącznie dla
  kompletnej, jawnej granicy bez zapisu: testy `PASS`, brak luk i unknown,
  production/activation `N-D` oraz wszystkie dowody N-D zapisane jawnie.
- R4 wymaga co najmniej `sol/max`; niższa klasa może użyć równego lub
  silniejszego tieru/effortu, nigdy słabszego niż jej floor.
- Failed/missing/N-D test poza dozwoloną analizą, failed/missing baseline,
  mutation `MISSING` lub `SURVIVED`, pusty handoff, nieadekwatny model/effort,
  niespójny target oraz nieuzasadniony rollback `N-D` są blockerami.
- `CURRENT_EXACT_ACK` ma niepusty exact scope, dowód i
  `requires_reask=false`. Nie rozszerzaj zakresu i nie traktuj `HOLD` tej bramy
  jako revoke ważnego ACK w odrębnym autoryzowanym torze.
- `STALE_OR_REVOKED`, `UNVERIFIED` i `MISSING_REQUIRED_ACK` dla żądania
  produkcyjnego wymagają `HOLD`.
- `CURRENT_EXACT_ACK` + `ATTESTED_ACTIVE_MAIN` + `HANDOFF_REQUIRED` kieruje do
  `AUTHORIZED_EXECUTION_LANE`; ten sam ACK u non-MAIN kieruje wyłącznie do
  `ACTIVE_MAIN`.

## Disposition i handoff

- `READY_FOR_IMPLEMENTATION`: lokalna, nieprodukcyjna implementacja może zostać
  rozpoczęta przez właściwego autora.
- `READY_FOR_REVIEW`: exact lokalny kandydat i dowody autora są gotowe do
  niezależnego review.
- `HOLD`: ta brama nie może kontynuować bez wskazanego następnego kroku.

Żadna disposition nie instaluje skilla, nie aktywuje go, nie nadaje authority i
nie zastępuje owner-only promotion. Handoff zapisuje target, wymagania i zakaz
bezpośredniego owner contact dla non-MAIN.
