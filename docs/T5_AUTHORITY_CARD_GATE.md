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

Executor akceptuje wyłącznie `authority_scope.v1` z top-level rekordu decyzji.
`authority_scope.py` jest jedynym producentem; liczy blok raz z finalnego
`PipelineResult`, wejścia eventu i decision-time wiersza `orders_state`. Ten sam
blok przechodzi przez wspólny serializer L1.1 do LOCATION A+B. Każdy brak
źródła ma jawne `{"absent": "powód"}`; `check_scope` odmawia wtedy jako
`scope_<1..7>_absent`.

Dzisiejsze dane pozwalają uczciwie udowodnić: event/status/historię przypisania,
snapshot worka z generacją, plan, `best_effort`, klasyfikację paczki oraz
pozycję z wiekiem w sekundach i wspólną klasyfikacją R3. Nadal jawnie `absent`
są: autorytatywny normal/Alarm, multi-brand, shared-pickup, pełny kontekst
override koordynatora i per-rekordowy parytet no-GPS. Ten ostatni jest własnością
polityki i wymaga osobnej, hash-bound atestacji testu strukturalnego. Dopóki te
źródła nie istnieją, odmowa AUTO jest poprawnym wynikiem.

Kod nie uruchamia `git` w hot-path. Odczytuje SHA wyłącznie z `BUILD_SHA`.

## Deploy BUILD_SHA (ręcznie, po autoryzowanym restarcie)

Po wdrożeniu i restarcie właściwej usługi, ale przed jakąkolwiek próbą AUTO:

1. z katalogu wdrożonego `dispatch_v2` uruchom
   `python3 tools/write_build_sha.py`;
2. natychmiast sprawdź
   `python3 tools/write_build_sha.py --verify` — wymagany exit `0`;
3. dopiero zgodny plik może zostać wpisany do `code_fingerprint.git_sha`
   podpisywanej karty.

Writer jest idempotentny i zapisuje `temp → fsync → rename → fsync katalogu`.
Nie ma timera ani importu w silniku. Okno między restartem a zapisem pozostaje
fail-closed (`code_git_sha_unavailable`).

## CLI

`tools/authority_card_verify.py` ma wyłącznie read-only podkomendy:
`show`, `verify` (exit 0/1) i `template`. Nie podpisuje i niczego nie zapisuje.
Podpis pozostaje w torze panelu z PIN-em.

## Rollback

Przed wydaniem `ENABLE_AUTO_ASSIGN` pozostaje false. Rollback kodu to revert
T5 bez migracji danych. Po rollbacku i autoryzowanym restarcie uruchom ponownie
`write_build_sha.py` oraz `--verify`, aby plik opisywał faktycznie uruchomiony
HEAD; stara karta z poprzednim SHA ma nadal odmówić. Operacyjny stop przyszłych
wykonań to flaga false oraz latch w stanie karty. Usunięcie/wygaszenie karty
również zamyka gate, lecz nie cofa już wykonanego przypisania; order w toku
wymaga ręcznego reconcile zgodnie z kartą klasy.
