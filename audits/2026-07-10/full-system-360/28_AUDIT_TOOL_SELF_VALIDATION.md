# Samowalidacja narzędzia audytu

## Co sprawdza validator

`_validate_package.py` jest fail-closed dla następujących warunków:

- obecność dokładnie 35 wymaganych artefaktów: README, raporty 00–29, cztery
  pliki structured data;
- findings JSON/CSV: 110 unikalnych ID, 106 odzyskanych + 4 procesowe i pełny
  parytet eksportowanych pól; Markdown: parity ID/domain/severity/status/evidence/
  reverify/title oraz zgodny summary i bazowy SHA;
- tool matrix: 15 unikalnych instrumentów i zgodne liczniki trust;
- zgodność bazowego SHA w manifestach;
- istnienie lokalnych linków plikowych z README;
- brak dopasowań skonfigurowanych wzorców: raw verdict, `OPS--`, numeryczne
  entity IDs, e-mail/GPS, credential assignment i absolutne ścieżki wrażliwe —
  w 35 wymaganych artefaktach;
- poprawny parsing wszystkich JSON/CSV i kompilacja helperów.

Oczekiwany wynik:

```text
AUDIT360_VALIDATE OK required=35 findings=110 tools=15
```

Jeden negative control wykonano na tymczasowej kopii pakietu: dodanie syntetycznego
adresu e-mail zatrzymało validator z `unsafe content ... email`, rc=1. Kopię
usunięto po teście; repo i produkcja nie zostały zmienione.

## Samowalidacja builderów

- źródłowy załącznik odzysku ma przypięty SHA-256 i stałą lokalizację;
- parser używa tych samych zweryfikowanych bajtów, więc nie ma drugiego odczytu
  podatnego na TOCTOU;
- sanitizer działa przed eksportem, a końcowy skan blokuje skonfigurowane klasy;
- raw verdict źródła nie trafia do JSON;
- zapisy MD/JSON/CSV są temp+fsync+rename;
- tool CSV jest generowany z kanonicznego JSON, nie ręcznie duplikowany; oba
  writery używają atomowego replace i fsync katalogu.

## Ograniczenia niezależności

Validator dowodzi integralności pakietu, nie prawdziwości findings. Builder i
validator są kodem tej samej sesji, więc finalny review nadal wymaga człowieka lub
niezależnego review. Pin źródła chroni przed dryfem, ale nie czyni starego źródła
aktualnym. Sprawdzono negatywnie detektor e-mail; pozostałe wzorce mają kontrolę
statyczną, nie osobny mutation case. Brak matcha nie zastępuje review człowieka
ani klasyfikacji prawnej danych. Pin źródła oznacza, że jego zmiana wymaga jawnej
zmiany checksumu w kodzie i ponownego review; regex nie rozpoznaje dowolnej nazwy
osoby lub każdego możliwego formatu adresu.
