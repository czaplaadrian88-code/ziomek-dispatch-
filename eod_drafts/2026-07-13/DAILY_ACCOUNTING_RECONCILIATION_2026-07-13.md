# Daily Accounting — rekonsyliacja historycznych wpisów

Data: 2026-07-13 UTC  
Status: DONE/LIVE po jawnym ACK

## Problem i przyczyna źródłowa

Dzienne rozliczenie używało listy wykluczeń tożsamości jako filtra wypłat.
Lista zawierała także konta oznaczone obecnie jako nieaktywne, choć mogły mieć
zlecenia w historycznym okresie rozliczeniowym. Stary warunek idempotencji
`nazwa+data` uznawał istniejący wiersz za poprawny bez porównania H i P, więc
nie pozwalał ani wykryć, ani skorygować rozbieżności.

## Zmiana

- `NON_SETTLEMENT_CIDS` zawiera wyłącznie trzy trwałe konta techniczne.
- Nazwa rozliczeniowa i warianty historyczne pochodzą z canonical `identity`.
- Kolumna S przechowuje stabilny klucz `daily-accounting/v1:CID:od:do`.
- Przed nowym wpisem proces klasyfikuje istniejący wiersz: zgodny, rozbieżny
  albo niejednoznaczny. Rozbieżność powoduje HOLD; jawny tryb
  `--reconcile-legacy --apply-reconciliation` może skorygować tylko H/P/S.
- Błąd źródła Eljot lub brak kanonicznej nazwy powoduje HOLD przed zapisem.
- Po zapisie odczytywane są wszystkie zmienione pola. Błąd API albo read-back
  uruchamia kompensację z preimage pobranym w trybie `FORMULA`, więc formula nie
  zostanie zastąpiona jej obliczoną wartością.
- Podgląd nie wysyła alertów i zapisuje raport `/tmp` bez nazwisk i kwot, z
  prawami 0600.

## Dowody

- Baseline: 5187 testów, 0 fail/error, 32 skip.
- Final: 5188 testów, 0 fail/error, 32 skip.
- Hermetic strict: 5188 testów, 0 fail/error, 82 skipy kwarantanny.
- Moduł rozliczeń: 36/36; focused z identity: 53/53.
- Podgląd aktywnego kodu: 0 konfliktów, 2 jawne korekty legacy, bez appendu.
- Live: zapisano wyłącznie H/P/S w dwóch istniejących wierszach; niezależny
  odczyt panel→arkusz potwierdził H, P i klucz S dla obu CID.

## Wydanie i rollback

- Base: `4e470b6`; commity: `c10e1eb`, `c244d0b`; aktywny master: `c244d0b`.
- Backup kodu: tag `daily-accounting-pre-c10e1eb`.
- Nie zmieniono flag, timerów, procesu ani nie wykonano restartu.
- Timer `dispatch-daily-accounting.timer` pozostaje enabled; następny naturalny
  bieg ładuje kod z mastera.
- Rollback kodu: jawny revert obu commitów względem taga w kontrolowanym
  wydaniu. Rollback danych po potwierdzonym sukcesie: historia wersji Google
  Sheets bezpośrednio sprzed 2026-07-13 10:13 UTC, ograniczona do H/P/S dwóch
  skorygowanych wierszy. W trakcie pojedynczej operacji rollback jest
  automatyczny i formula-preserving.
