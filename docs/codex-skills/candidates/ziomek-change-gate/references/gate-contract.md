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
- Każde READY wymaga `unknown=0`, wszystkich testów `PASS`, modelu i effortu co
  najmniej na floorze ryzyka oraz niepustych wymagań handoffu.
- `IMPLEMENTATION_CANDIDATE` READY wymaga baseline `PASS`, mutation `KILLED`,
  akceptowalnego oracle, rollbacku `READY` i spójnego targetu. `READY_FOR_REVIEW`
  wymaga `independent_review=PENDING` oraz targetu `INDEPENDENT_REVIEWER`;
  `READY_FOR_IMPLEMENTATION` wymaga `implementation=READY` i targetu
  `LOCAL_IMPLEMENTER`.
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
