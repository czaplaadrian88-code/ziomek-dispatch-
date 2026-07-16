# Kontrakt wyniku Ziomek Change Gate

## Źródło maszynowe

Wynik jest JSON-em zgodnym z
[`ziomek-change-gate-result-v1.schema.json`](../../../schemas/ziomek-change-gate-result-v1.schema.json).
Schema ma zamknięte obiekty, pełne `required`, typy i enumy. Nie dodawaj pól ani
nie pomijaj pól na podstawie trybu.

## Niezależne grupy faktów

- `model` zapisuje tier, exact model, effort, powód oraz stan i dowód atestacji.
- `role` zapisuje status, dowód i routing. Brak mechanicznej atestacji oznacza
  `UNATTESTED_NON_MAIN`.
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

- `HOLD` ma co najmniej jeden `hold_reason`; pozostałe disposition mają pustą
  listę.
- `READY_FOR_REVIEW` wymaga `independent_review=PENDING` i handoffu do
  `INDEPENDENT_REVIEWER`.
- `READY_FOR_IMPLEMENTATION` wymaga `implementation=READY`.
- nieatestowany model, niejasne HARD/SOFT, nieznane elementy kompletności,
  stale/unverified/missing ACK dla production albo brak rollbacku daje `HOLD`.
- `CURRENT_EXACT_ACK` ma niepusty exact scope, dowód i
  `requires_reask=false`. Nie rozszerzaj zakresu i nie traktuj `HOLD` tej bramy
  jako revoke ważnego ACK w odrębnym autoryzowanym torze.
- `STALE_OR_REVOKED`, `UNVERIFIED` i `MISSING_REQUIRED_ACK` dla żądania
  produkcyjnego wymagają `HOLD`.

## Disposition i handoff

- `READY_FOR_IMPLEMENTATION`: lokalna, nieprodukcyjna implementacja może zostać
  rozpoczęta przez właściwego autora.
- `READY_FOR_REVIEW`: exact lokalny kandydat i dowody autora są gotowe do
  niezależnego review.
- `HOLD`: ta brama nie może kontynuować bez wskazanego następnego kroku.

Żadna disposition nie instaluje skilla, nie aktywuje go, nie nadaje authority i
nie zastępuje owner-only promotion. Handoff zapisuje target, wymagania i zakaz
bezpośredniego owner contact dla non-MAIN.
