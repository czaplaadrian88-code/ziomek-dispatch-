# T5 MINI FIX-PACK 5 — raport po blind r5

Data: 2026-07-28 UTC

Baza wskazana przez zlecenie: `7a32b9cbd` (lokalna atestacja HEAD niemożliwa:
metadata worktree wskazuje poza granicę odczytu sandboxa).

Zakres: source-only; zero flag, runtime, deployu i restartu.

## Wynik

- **J1:** `clear_latch` wymaga dokładnej frazy
  `ODBLOKOWUJE AUTO-CANARY YYYY-MM-DD` z bieżącą datą UTC. Brak, literówka,
  dodatkowy znak i stara data odmawiają przed zapisem audytu lub stanu. CLI
  wymaga `--owner-ack-phrase`; poprawna fraza trafia do
  `authority_latch_cleared.owner_ack_phrase` i receiptu CLI. Dokumentacja i
  docstring jawnie opisują proceduralne zaufanie do operatora oraz granicę 2D.
- **J2:** finalna bramka po fresh solve ponawia `heartbeat_fresh` na tym samym
  świeżym zegarze co owner-auth, flaga i karta. Stary lub nie-OK heartbeat
  odmawia przed rezerwacją, latchuje właściwy reason i nie wywołuje runnera.

## Dowody

- baseline przed testami: `71 passed`;
- RED-first: `6 failed` — trzy warianty frazy, brak parametru CLI, happy CLI
  bez implementacji i heartbeat stary po solve;
- po fixie bezpośredni klaster J1/J2: `78 passed`;
- rozszerzony klaster authority/scope/executor/gate/owner-auth/TOCTOU:
  `233 passed` z `HERMETIC_STRICT=1`;
- `py_compile`: 5/5 plików Python; hermetyczny import: `IMPORT_OK`;
- mutation ratchet: usunięcie walidacji frazy ponownie zapisuje/clearuje, a
  usunięcie finalnego heartbeat gate ponownie dochodzi do runnera — oba nowe
  oracles wtedy czerwienieją.

Pełna suita nie została uruchomiona: kanoniczny skill zatrzymał `test` przed
startem, ponieważ jego kontrakt wymaga osobnego ACK na operację mogącą dotknąć
live; sandbox odciął też kanoniczny venv po pierwszej próbie. To pozostaje bramką
przed merge, nie jest failem asercji fix-packa.

regresja: 233 passed, failed=0 w rozszerzonym klastrze HERMETIC_STRICT; pełna suita HOLD przed merge.
e2e: maybe_execute od wejścia przez owner/card/heartbeat/fresh solve/final gate do odmowy przed rezerwacją i runnerem.
pozytywny-wplyw: negatywne safety-oracles J1/J2 zmieniły wynik ze zdjęcia latcha/execute na fail-closed bez side-effectu.
rollback: source-only revert jawnych plików fix-packa; zero live mutation.
N-D: flags.json, registry flag, shadow serializer i runtime — brak nowej flagi, metryki, schematu lub deployu.

## Rollback

Odwrócić wyłącznie zmiany tego fix-packa w `authority_card.py`,
`auto_assign_executor.py`, `tools/authority_card_verify.py`,
`docs/T5_AUTHORITY_CARD_GATE.md` i dwóch plikach testowych. Nie wdrożono żadnej
zmiany live; przed ewentualnym deployem nadal obowiązują pełna regresja,
`py_compile`/import check oraz osobny ACK ownera na deploy/restart/flip.
