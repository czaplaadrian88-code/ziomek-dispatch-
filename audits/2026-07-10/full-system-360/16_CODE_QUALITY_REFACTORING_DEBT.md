# Jakość kodu i dług refaktoryzacyjny

## Postęp

Refaktor `core/` zmniejszył monolit i ustanowił fasadę `decide`. Route-order,
identity, lifecycle flag i hermetyzacja mają konstrukcyjne punkty wspólne. To
realna redukcja entropii, nie tylko przesunięcie plików.

## Dług pozostający

- proces-globalne flagi/cache/bufory ograniczają czystość `DecisionContext`;
- `dispatch_pipeline` nadal ma duży fan-out i aliasy dwukierunkowe do `core`;
- broad exceptions w hot path wymagają reason codes, nie masowego rewrite;
- serializer i plan bridge opierają się na ukrytych założeniach kolejności;
- trzy światy flag nadal potrzebują jednego rejestru kontraktu carrierów;
- dokumenty „żywe” zawierają liczby, które dryfują szybciej niż ich status.

## Zasada refaktoru

Nie przenosić kodu dla samego LOC. Każda fala musi usunąć jeden kanał globalny lub
jedną kopię reguły, zachować byte/decision parity i zmniejszyć miernik entropii.
