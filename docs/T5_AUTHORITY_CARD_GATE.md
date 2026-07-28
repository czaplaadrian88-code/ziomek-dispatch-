# T5 — runtime-gate karty `auto.canary.v1`

## Mechanizm

`auto_assign_executor.maybe_execute` zachowuje kolejność:

1. hot killswitch `ENABLE_AUTO_ASSIGN`;
2. świeże podniesienie ownera z PIN-em (T2);
3. karta T5: latch → podpis audytowy → ważność → fingerprint → scope → limity;
4. istniejąca quality gate i bezpieczniki wykonania.

Po fresh solve executor pobiera drugi, nowy zegar i na nim ponawia owner-auth,
źródłową flagę, cały gate karty oraz świeżość i jawny werdykt `OK` heartbeat,
bezpośrednio przed trwałą rezerwacją. Rezerwacja powstaje pod lockami przed subprocess-em:
idempotencja oid, budżet, `in_flight` i `pending_verification` są fsyncowane.
Odwołanie/wygaśnięcie karty albo latch w oknie TOCTOU wygrywa, a crash po skutku
w panelu nie otwiera replayowi drugiego wykonania.

Tail audytu podniesienia ownera jest czytany fail-closed. Wybrana semantyka jest
ostrzejsza niż minimum: każdy niepusty, nieparsowalny albo niedictowy wiersz
w całym skanowanym oknie unieważnia autoryzację jako
`authorization_audit_corrupt`, także gdy wcześniej wystąpił poprawny toggle.
Jedyny pomijany fragment to pierwszy potencjalnie ucięty rekord wynikający
technicznie z rozpoczęcia ograniczonego tail-skanu w środku starszego wiersza.

Body karty jest hashowane jako JSON UTF-8 z `sort_keys=True` i
`separators=(",", ":")`. Liczy się ostatni wiersz
`kind=authority_card_signed` dla `class_id=auto.canary.v1`; musi mieć identyczny
`card_sha256` i `pin_verified=true`. Brak lub błąd dowodu zamyka bramkę.
`stop_contract_sha256` jest porównywany ze stałą
`authority_card.EXPECTED_STOP_CONTRACT_SHA256`, która wiąże kanoniczny tekst
sekcji 3+4 karty launchowej. Odtwarzalny algorytm normalizacji jest opisany 1:1
przy stałej w kodzie.

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
`receipt` i learning-log; natomiast `executed_total`, `executed_ts`,
`in_flight` i `pending_verification` są rezerwowane jeszcze przed runnerem.
Writery rezerwacji, rollbacku pre-send, stanu nieznanego, weryfikacji
koordynatora i latcha zawsze
re-czytają stan pod tym samym `state_lock` i mergują wyłącznie własne pola.
Podpis nie jest kompletny bez atomowego `initialize_state`: plik stanu zawiera
`initialized_for_card=<card_sha256>` i `initialized_at`. Istniejący receipt
podpisu przy brakującym stanie daje `state_missing` i trwały latch, nigdy świeży
budżet.

`ok=false` po uruchomieniu runnera nie oznacza automatycznie porażki. Timeout,
exit bez sentinela, wyjątek runnera i każdy nierozpoznany wynik mogły nastąpić
po commicie w panelu, więc są stanem **NIEZNANYM**: pozostawiają wcześniejszą
rezerwację, zatrzaskują `runner_outcome_unknown` oraz każą wykonać reconcile 5b
karty. Wyłącznie dowód z samego `Popen`, że proces nie został utworzony
(`blocked_pytest_context` albo błąd spawn `FileNotFoundError`/`PermissionError`/
`OSError` zapisany jako `pre_send_refusal:`), jest twardą odmową przed
wysłaniem: wycofuje oba skorelowane liczniki i idempotencję pod lockami, dopisuje
fsyncowany audyt `reservation_rolled_back` i nie latchuje.
Po zwróceniu dziecka przez `Popen` timeout, `OSError`, błąd `communicate` i
`exit != 0` są stanem nieznanym, bo child mógł wcześniej wykonać side-effect.

Przed uruchomieniem nazwowego runnera executor rozwiązuje nazwę przez kanoniczny
`dispatch_v2.identity.Registry` z profilem `worker` i wymaga dokładnie zamierzonego
`canon_cid`. Brak, tie albo rozjazd daje `runner_identity_ambiguous` bez rezerwacji
i bez latcha (staleness konfiguracji). Obecny `gastro_assign.py` nie przyjmuje CID
jako argumentu; pozostaje więc niezmieniony w tej karcie. Jego `--verify` niesie
jednak `verify_ok_kid=...`: brak tego pola jest stanem nieznanym
`runner_outcome_unknown`, a rozjazd read-back z intencją executora pozostawia
rezerwację, zatrzaskuje `runner_identity_mismatch` i wymaga reconcile. Osobna karta
powinna dodać do współdzielonego runnera jawny argument CID i jego walidację
end-to-end.

Po wejściu pod lock stanu karty i lifecycle executor pobiera świeży zegar dla
heartbeat i 15-sekundowej świeżości proposal. Po fresh solve pobiera go ponownie:
ta druga próbka zasila finalny TTL autoryzacji ownera, okno ważności karty,
ponowną kontrolę świeżości i `checks.verdict=="OK"` heartbeat oraz rezerwację.
Ostatni gate czyta też `ENABLE_AUTO_ASSIGN` i fingerprint bezpośrednio ze
źródłowego `flags.json`, z pominięciem cache i per-tick `FlagSnapshot`; OFF, błąd
odczytu albo heartbeat, który zestarzał się podczas solve, odmawia przed rezerwacją.

## Fail-closed matrix

| Warunek | Powód | Latch |
|---|---|---|
| brak/nieczytelna/uszkodzona karta | `card_missing/read_error/parse_error` | tak |
| schema/klasa/wersja/kontrakt scope lub limitów niezgodny | odpowiedni `*_mismatch` / `*_schema_error` | tak |
| brak/nieczytelny/uszkodzony audyt lub brak wiersza klasy | `audit_*` | tak |
| PIN niepotwierdzony albo SHA różny | `pin_not_verified` / `sha_mismatch` | tak |
| hash sekcji stop różny od kanonu | `stop_contract_mismatch` | tak |
| karta jeszcze nieważna lub wygasła | `card_not_yet_valid` / `card_expired` | tak |
| SHA buildu lub fingerprint flag niedostępny/różny | `*_unavailable` / `*_mismatch` | tak |
| stan uszkodzony lub niedokończony zapis atomowy | `state_corrupt` / `state_atomic_write_incomplete` | stan traktowany jak latch |
| receipt podpisu istnieje, ale brak stanu | `state_missing` | tak; brak świeżego budżetu |
| stan związany z inną/nieznaną kartą | `state_card_mismatch` | tak |
| latch już włączony | `latch_on` | pozostaje |
| brak albo negatywny dowód predykatu 1–7 | `scope_*` | nie; order jest `recommend-only` |
| limit 1/h, 1 in-flight, 3 total lub pending verification | `max_per_hour`, `in_flight`, `max_total`, `pending_verification` | nie |
| nazwa nie rozwiązuje się jednoznacznie do zamierzonego CID | `runner_identity_ambiguous` | nie; brak rezerwacji |
| heartbeat nie ma jawnego `checks.verdict=="OK"` | `monitor_verdict_not_ok` | tak |
| heartbeat nie istnieje, jest nieczytelny albo nieświeży | `monitor_heartbeat_stale` | tak |
| sukces runnera nie niesie CID z read-back | `runner_outcome_unknown` | tak + rezerwacja zostaje |
| read-back runnera wskazuje inny CID | `runner_identity_mismatch` | tak + rezerwacja zostaje |
| końcowy źródłowy killswitch jest OFF/nieczytelny | `flag_off_at_execution` | nie; brak rezerwacji |
| runner nie potwierdził wyniku po możliwym wysłaniu | `runner_outcome_unknown` | tak + budżet jak wykonanie |
| jawny brak startu procesu | `definitive_pre_send_refusal` | rezerwacja wycofana w obu stanach; audyt `reservation_rolled_back` |

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
Jeżeli istnieje już żywy rekord sprzed ery markerów, duplikat `NEW_ORDER`
uzupełnia wyłącznie pierwszy marker; status, CID, payload, historia i `updated_at`
pozostają bajtowo niezmienione.
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

## Ograniczenia poświadczenia buildu

`tools/write_build_sha.py` przy zapisie i `--verify` odmawia, jeżeli
`git status --porcelain --untracked-files=no` pokazuje zmianę śledzonego pliku.
Untracked (w tym robocze `eod_drafts`) są świadomie poza tym checkiem. BUILD_SHA
poświadcza czysty commit HEAD; pełna atestacja wszystkich bajtów procesu,
interpretera, zależności, generowanych artefaktów i hosta pozostaje poza zakresem
T5.

## Rytuał uruchomienia klasy — kolejność twarda

Każdy krok live wymaga właściwego ACK ownera. Kolejność jest niezamienna:

1. z katalogu wdrożonego `dispatch_v2` zapisz i zweryfikuj SHA:
   `python3 tools/write_build_sha.py`;
2. natychmiast sprawdź
   `python3 tools/write_build_sha.py --verify` — wymagany exit `0`;
3. wykonaj jeden autoryzowany restart właściwej usługi i zweryfikuj health/PID;
4. owner podpisuje kartę w torze z PIN-em, dopiero po zgodnym BUILD_SHA;
5. ten sam tor, zanim potwierdzi gotowość podpisu, wykonuje
   `authority_card_verify.py initialize-state`; brak tego kroku = podpis niegotowy;
6. jeżeli stan jest zatrzaśnięty, wykonaj ręczny reconcile 5b, następnie
   `verify-execution` dla każdego pending oid i dopiero za osobnym ACK użyj
   `latch-clear` opisanego niżej;
7. dopiero na końcu owner może flipnąć `ENABLE_AUTO_ASSIGN=true`.

Writer jest idempotentny i zapisuje `temp → fsync → rename → fsync katalogu`.
Nie ma timera ani importu w silniku.

### Pułapka poranna

Zakazana kolejność to: **flip ON → pierwszy event → brak karty/BUILD_SHA →
latch → podpis karty**. Podpis nie zdejmuje istniejącego latcha. Skasowanie
pliku stanu nie resetuje już budżetu: przy istniejącym receipt executor zatrzaskuje
`state_missing`, ale nadal wymaga to ręcznego odzyskania historii. Dokładna
bezpieczna sekwencja:

`write_build_sha + --verify` → `restart + health` → `podpis karty z PIN-em` →
`initialize-state` →
`reconcile 5b` → `verify-execution` → `latch-clear, tylko jeśli latch był ON`
→ `flip flagi`.

## CLI

`tools/authority_card_verify.py` ma read-only `show`, `verify` (exit 0/1) i
`template`, a także trzy wąskie writery. Nie podpisuje karty; podpis pozostaje w
torze panelu z PIN-em. Po trwałym receipt tor podpisu musi wykonać:

```bash
python3 tools/authority_card_verify.py initialize-state
```

Komenda najpierw weryfikuje podpis, SHA buildu, fingerprint flag i kontrakt
stopu, potem atomowo tworzy stan związany z SHA karty. Jest idempotentna dla tej
samej karty i odmawia nadpisania stanu innej/nieznanej karty.

Po ręcznym sprawdzeniu wykonania:

```bash
python3 tools/authority_card_verify.py \
  verify-execution --oid OID --operator OPERATOR
```

Komenda usuwa tylko ten oid z `pending_verification`, zeruje `in_flight`
wyłącznie gdy wskazuje ten sam oid i dopisuje
`kind=authority_execution_verified`. Nie wykonuje przypisania ani reconcile.

### Granica 2D — proceduralny ACK dla `latch-clear`

Zdjęcie latcha jest dozwolone **TYLKO po reconcile 5b i jawnym ACK ownera**.
CLI ufa operatorowi: ACK jest proceduralny, a dowód stanowią wiersz audytu
`authority_latch_cleared` z dokładną frazą i datą oraz receipt toru
operatorskiego. Kryptograficzna niepodrabialność tego ACK pozostaje poza
zakresem 2D karty.

Operator musi wpisać dokładnie `ODBLOKOWUJE AUTO-CANARY YYYY-MM-DD`, gdzie data
jest dzisiejszą datą UTC. Brak parametru, literówka, dodatkowy znak albo stara
data kończą się odmową przed zapisem audytu i stanu:

```bash
python3 tools/authority_card_verify.py \
  latch-clear --reason "OWNER_ACK: po reconcile 5b" --operator OPERATOR \
  --owner-ack-phrase "ODBLOKOWUJE AUTO-CANARY YYYY-MM-DD"
```

Jeżeli latch powstał przez `runner_outcome_unknown`, reconcile obejmuje najpierw
`verify-execution` dla tego oid; dopiero potem wolno wykonać `latch-clear`.
Komenda odmawia, dopóki `in_flight` nie jest `null` i
`pending_verification` nie jest puste. Po czystym reconcile zmienia wyłącznie
`auto_off_latch` na false; liczniki, timestampy oraz pierwotny reason/ts zostają
zachowane, a audyt dostaje `kind=authority_latch_cleared` wraz z polem
`owner_ack_phrase`. Nigdy nie usuwaj pliku stanu, bo resetuje budżet i niszczy
ślad.

## Rollback

Przed wydaniem `ENABLE_AUTO_ASSIGN` pozostaje false. Rollback kodu to revert
T5 bez migracji danych. Po rollbacku i autoryzowanym restarcie uruchom ponownie
`write_build_sha.py` oraz `--verify`, aby plik opisywał faktycznie uruchomiony
HEAD; stara karta z poprzednim SHA ma nadal odmówić. Operacyjny stop przyszłych
wykonań to flaga false oraz latch w stanie karty. Usunięcie/wygaszenie karty
również zamyka gate, lecz nie cofa już wykonanego przypisania; order w toku
wymaga ręcznego reconcile zgodnie z kartą klasy.
