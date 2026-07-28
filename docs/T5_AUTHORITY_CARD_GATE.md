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

Stan karty ma jednego właściciela serializacji w `authority_card.py` i jest zapisywany
`temp → fsync → rename → fsync katalogu`. Uszkodzony JSON albo osierocony plik
temp jest traktowany jak latch ON. Po sukcesie jednym zapisem aktualizowane są:
`executed_total`, `executed_ts`, `in_flight` i `pending_verification`.
Writery sukcesu, stanu nieznanego, weryfikacji koordynatora i latcha zawsze
re-czytają stan pod tym samym `state_lock` i mergują wyłącznie własne pola.

`ok=false` po uruchomieniu runnera nie oznacza automatycznie porażki. Timeout,
exit bez sentinela, wyjątek runnera i każdy nierozpoznany wynik mogły nastąpić
po commicie w panelu, więc są stanem **NIEZNANYM**: zapisują idempotencję oid,
zużywają oba skorelowane liczniki wykonania, ustawiają `in_flight` i
`pending_verification`, zatrzaskują `runner_outcome_unknown` oraz każą wykonać
reconcile 5b karty. Wyłącznie dowód, że proces nie został utworzony
(`blocked_pytest_context`, błąd launch `FileNotFoundError`/`PermissionError`/
`OSError` albo jawny kontrakt `pre_send_refusal:`), jest twardą odmową przed
wysłaniem: zapisuje idempotencję, ale nie konsumuje budżetu i nie latchuje.
Sam `exit != 0` nigdy nie jest takim dowodem, bo child mógł wcześniej wykonać
side-effect.

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
| runner nie potwierdził wyniku po możliwym wysłaniu | `runner_outcome_unknown` | tak + budżet jak wykonanie |
| jawny brak startu procesu | `definitive_pre_send_refusal` | nie; tylko idempotencja oid |

## Uczciwa granica danych scope

Executor akceptuje wyłącznie `authority_scope.v1` z top-level rekordu decyzji.
`authority_scope.py` jest jedynym producentem; liczy blok raz z finalnego
`PipelineResult`, wejścia eventu i decision-time wiersza `orders_state`. Ten sam
blok przechodzi przez wspólny serializer L1.1 do LOCATION A+B. Każdy brak
źródła ma jawne `{"absent": "powód"}`; `check_scope` odmawia wtedy jako
`scope_<1..7>_absent`.

`state_machine.update_from_event` zapisuje przy pierwszym `NEW_ORDER`, pod
istniejącym lifecycle lockiem i w tym samym atomowym upsercie,
`last_lifecycle_event_id_new_order` oraz jawne `courier_id=None`. Retransmisja
tego samego event_id jest no-opem: nie dopisuje historii i nie dotyka pliku.
Dzięki temu predykat 1 jest dziś dowodliwy z realnego przepływu
state-machine→scope; usunięcie markera ponownie daje `scope_1_absent`.

Dzisiejsze dane pozwalają uczciwie udowodnić: predykat 1
(event/status/historię przypisania i bieżący brak przypisania),
snapshot worka z generacją, plan, `best_effort`, klasyfikację paczki oraz
pozycję z wiekiem w sekundach i wspólną klasyfikacją R3. Nadal jawnie `absent`
są: autorytatywny normal/Alarm, multi-brand, shared-pickup, pełny kontekst
override koordynatora i per-rekordowy parytet no-GPS. Ten ostatni jest własnością
polityki i wymaga osobnej, hash-bound atestacji testu strukturalnego. Dopóki te
źródła nie istnieją, odmowa AUTO jest poprawnym wynikiem. Na realistycznym
dzisiejszym rekordzie pierwszą odmową w kolejności gate'u jest dokładnie
`scope_4_absent`.

Kod nie uruchamia `git` w hot-path. Odczytuje SHA wyłącznie z `BUILD_SHA`.

## Rytuał uruchomienia klasy — kolejność twarda

Każdy krok live wymaga właściwego ACK ownera. Kolejność jest niezamienna:

1. z katalogu wdrożonego `dispatch_v2` zapisz i zweryfikuj SHA:
   `python3 tools/write_build_sha.py`;
2. natychmiast sprawdź
   `python3 tools/write_build_sha.py --verify` — wymagany exit `0`;
3. wykonaj jeden autoryzowany restart właściwej usługi i zweryfikuj health/PID;
4. owner podpisuje kartę w torze z PIN-em, dopiero po zgodnym BUILD_SHA;
5. jeżeli stan jest zatrzaśnięty, po ręcznym reconcile użyj za osobnym ACK
   `latch-clear` opisanym niżej;
6. dopiero na końcu owner może flipnąć `ENABLE_AUTO_ASSIGN=true`.

Writer jest idempotentny i zapisuje `temp → fsync → rename → fsync katalogu`.
Nie ma timera ani importu w silniku.

### Pułapka poranna

Zakazana kolejność to: **flip ON → pierwszy event → brak karty/BUILD_SHA →
latch → podpis karty**. Podpis nie zdejmuje istniejącego latcha, a skasowanie
pliku stanu resetowałoby również budżet wykonań. Dokładna bezpieczna sekwencja:

`write_build_sha + --verify` → `restart + health` → `podpis karty z PIN-em` →
`reconcile 5b i latch-clear, tylko jeśli latch był ON` → `flip flagi`.

## CLI

`tools/authority_card_verify.py` ma read-only `show`, `verify` (exit 0/1) i
`template`, a także dwa wąskie, audytowane writery. Nie podpisuje karty; podpis
pozostaje w torze panelu z PIN-em.

Po ręcznym sprawdzeniu wykonania:

```bash
python3 tools/authority_card_verify.py \
  verify-execution --oid OID --operator OPERATOR
```

Komenda usuwa tylko ten oid z `pending_verification`, zeruje `in_flight`
wyłącznie gdy wskazuje ten sam oid i dopisuje
`kind=authority_execution_verified`. Nie wykonuje przypisania ani reconcile.

Zdjęcie latcha jest dozwolone **TYLKO po reconcile 5b i jawnym ACK ownera**:

```bash
python3 tools/authority_card_verify.py \
  latch-clear --reason "OWNER_ACK: po reconcile 5b" --operator OPERATOR
```

Jeżeli latch powstał przez `runner_outcome_unknown`, reconcile obejmuje najpierw
`verify-execution` dla tego oid; dopiero potem wolno wykonać `latch-clear`.
Komenda zmienia wyłącznie `auto_off_latch` na false. Liczniki, timestampy,
`in_flight`, `pending_verification` oraz pierwotny reason/ts zostają zachowane;
audyt dostaje `kind=authority_latch_cleared`. Nigdy nie usuwaj pliku stanu,
bo resetuje budżet i niszczy ślad.

## Rollback

Przed wydaniem `ENABLE_AUTO_ASSIGN` pozostaje false. Rollback kodu to revert
T5 bez migracji danych. Po rollbacku i autoryzowanym restarcie uruchom ponownie
`write_build_sha.py` oraz `--verify`, aby plik opisywał faktycznie uruchomiony
HEAD; stara karta z poprzednim SHA ma nadal odmówić. Operacyjny stop przyszłych
wykonań to flaga false oraz latch w stanie karty. Usunięcie/wygaszenie karty
również zamyka gate, lecz nie cofa już wykonanego przypisania; order w toku
wymaga ręcznego reconcile zgodnie z kartą klasy.
