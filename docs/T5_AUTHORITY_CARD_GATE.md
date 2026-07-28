# T5 — runtime-gate karty `auto.canary.v1`

## Mechanizm

`auto_assign_executor.maybe_execute` zachowuje kolejność:

1. hot killswitch `ENABLE_AUTO_ASSIGN`;
2. świeże podniesienie ownera z PIN-em (T2);
3. karta T5: latch → podpis audytowy → ważność → fingerprint → scope → limity;
4. istniejąca quality gate i bezpieczniki wykonania.

Ten sam gate jest ponawiany bezpośrednio przed subprocess-em przypisującym, aby
odwołanie karty, nowy latch albo zmiana liczników w oknie TOCTOU wygrały.

Body karty jest hashowane jako JSON UTF-8 z `sort_keys=True` i
`separators=(",", ":")`. Liczy się ostatni wiersz
`kind=authority_card_signed` dla `class_id=auto.canary.v1`; musi mieć identyczny
`card_sha256` i `pin_verified=true`. Brak lub błąd dowodu zamyka bramkę.

Gate nie ma osobnej flagi. Przy `ENABLE_AUTO_ASSIGN=false` executor nadal kończy
przed jakimkolwiek I/O karty, więc wdrożenie T5 nie zmienia zachowania live.

## Ścieżki

- karta: `/var/lib/ziomek-authority/cards/auto-canary-v1.json`
- stan: `/var/lib/ziomek-authority/state/auto-canary-v1.json`
- audyt: `/root/.openclaw/workspace/dispatch_state/coordinator_assign_audit.jsonl`
- SHA buildu: `/root/.openclaw/workspace/scripts/BUILD_SHA`

Funkcje przyjmują ścieżki jako argumenty. Testy używają wyłącznie `tmp_path`;
próba użycia ścieżek produkcyjnych pod pytest jest blokowana.

Stan karty ma jednego writera w `authority_card.py` i jest zapisywany
`temp → fsync → rename → fsync katalogu`. Uszkodzony JSON albo osierocony plik
temp jest traktowany jak latch ON. Po sukcesie jednym zapisem aktualizowane są:
`executed_total`, `executed_ts`, `in_flight` i `pending_verification`.

## Fail-closed matrix

| Warunek | Powód | Latch |
|---|---|---|
| brak/nieczytelna/uszkodzona karta | `card_missing/read_error/parse_error` | tak |
| schema/klasa/wersja/kontrakt scope lub limitów niezgodny | odpowiedni `*_mismatch` / `*_schema_error` | tak |
| brak/nieczytelny/uszkodzony audyt lub brak wiersza klasy | `audit_*` | tak |
| PIN niepotwierdzony albo SHA różny | `pin_not_verified` / `sha_mismatch` | tak |
| karta jeszcze nieważna lub wygasła | `card_not_yet_valid` / `card_expired` | tak |
| SHA buildu lub fingerprint flag niedostępny/różny | `*_unavailable` / `*_mismatch` | tak |
| stan uszkodzony lub niedokończony zapis atomowy | `state_corrupt` / `state_atomic_write_incomplete` | stan traktowany jak latch |
| latch już włączony | `latch_on` | pozostaje |
| brak albo negatywny dowód predykatu 1–7 | `scope_*` | nie; order jest `recommend-only` |
| limit 1/h, 1 in-flight, 3 total lub pending verification | `max_per_hour`, `in_flight`, `max_total`, `pending_verification` | nie |

## Uczciwa granica danych scope

Executor akceptuje wyłącznie pełny snapshot `authority_scope` w rekordzie,
payloadzie albo wyniku. Snapshot musi jawnie dowodzić wszystkich siedmiu
predykatów. Obecny serializer nie produkuje pełnego snapshotu: w szczególności
brakuje wiarygodnego dowodu historii wcześniejszych przypisań, normal/Alarm,
multi-brand/shared-pickup/override, wieku źródła LIVE GPS oraz kontrfaktycznego
parytetu no-GPS. Dopóki osobny producer tych dowodów nie zostanie zbudowany i
zwalidowany, nawet podpisana karta kończy się `scope_evidence_missing`.

Kod nie uruchamia `git` w hot-path. Odczytuje SHA z `BUILD_SHA`. Obecny deploy
nie tworzy tego pliku; bez dobudowania atomowego, hash-bound kroku deployu wynik
to `code_git_sha_unavailable` i latch.

## CLI

`tools/authority_card_verify.py` ma wyłącznie read-only podkomendy:
`show`, `verify` (exit 0/1) i `template`. Nie podpisuje i niczego nie zapisuje.
Podpis pozostaje w torze panelu z PIN-em.

## Rollback

Przed wydaniem `ENABLE_AUTO_ASSIGN` pozostaje false. Rollback kodu to revert
T5 bez migracji danych. Operacyjny stop przyszłych wykonań to flaga false oraz
latch w stanie karty. Usunięcie/wygaszenie karty również zamyka gate, lecz nie
cofa już wykonanego przypisania; order w toku wymaga ręcznego reconcile zgodnie
z kartą klasy.
