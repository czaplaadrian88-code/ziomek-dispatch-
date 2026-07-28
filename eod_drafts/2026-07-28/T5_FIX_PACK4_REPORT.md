# T5 FIX-PACK 4 — raport po blind r4

Data: 2026-07-28 UTC

Baza wskazana przez zlecenie: `3e90df989`

Zakres: source-only, bez flag, runtime, deployu i restartu

Status: **7/7 findings naprawione w źródle; kandydat wymaga kanonicznej pełnej
regresji i atestacji gita poza tym sandboxem przed merge/promocją.**

## Macierz findings

| ID | Negatywny oracle RED-first | Naprawa u źródła | Dowód po zmianie |
|---|---|---|---|
| I1 | Karta ważna przy wejściu, solve przesuwa zegar o 2 s poza `valid_until`; runner musi pozostać niewywołany. | Po fresh solve executor pobiera nowy UTC `execution_now`; ponawia owner-auth i cały card gate, a ten sam czas zasila rezerwację. | `test_card_is_revalidated_with_fresh_time_after_solve_before_reservation` |
| I2 | Poprawiony test ma hook solve, licznik wywołań i dopiero w hooku przesuwa czas; stary test nie dochodził do solve. | Oracle wymaga `solve_calls == [NOW]`, więc nie może przejść na pierwszym gate. | Ten sam test I1/I2 |
| I3 | Runner zwraca sukces bez `verify_ok_kid`; oczekiwany UNKNOWN, latch i zachowana rezerwacja. | Na enforced lane sukces wymaga read-back CID równy intencji. Brak CID zmienia wynik na unknown; mismatch ma osobny reason. | `test_h1_missing_runner_readback_is_unknown_and_keeps_reservation` |
| I4 | Poprawny toggle ON, potem ucięte `{broken`; runner nie może ruszyć. | Każdy niepusty nieparsowalny/niedictowy wiersz skanowanego okna audytu unieważnia całość jako `authorization_audit_corrupt`; strict UTF-8. | `test_corrupt_tail_after_valid_toggle_invalidates_authorization` oraz corrupt-only oracle |
| I5 | `in_flight` i `pending_verification` niepuste; API i CLI latch-clear muszą odmówić bez zapisu audytu. | `authority_card.clear_latch` sprawdza czysty stan pod tym samym lockiem i instruuje: reconcile 5b → verify-execution → latch-clear za ACK. | Dwa testy API + dwa testy CLI (odmowa i czysty happy path) |
| I6 | Legacy `assigned`, CID 101, bez markera + retransmisja NEW_ORDER z innym payloadem. | Istniejący rekord bez markera przyjmuje wyłącznie first-write marker; nie scala payloadu, nie dotyka statusu/CID/historii/`updated_at`. Nowy rekord zachowuje dotychczasowe create + `setdefault`. | Kanoniczne bajty po usunięciu markera są identyczne; ręczny legacy-mutant odtwarza regres |
| I7 | Świeży heartbeat ALARM/brak/UNKNOWN/zły kształt musi odmówić i latchować; mutant ignorujący verdict uruchamia runner. | `heartbeat_fresh` wymaga jednocześnie świeżości i `checks.verdict == "OK"`. Producent ma ratchety na jawny `OK` i `ALARM`. | Parametryczny oracle, executor+latch mutation control, dwa producer ratchety |

## Mapa kompletności

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód / test |
|---|---|---|---|---|
| `state_machine.upsert_order` | kanoniczny merge lifecycle NEW_ORDER | writer | TAK | I6, wszystkie dalsze scope/plan consumers dostają zachowany agregat |
| `authority_scope` | dowód predykatu NEW/unassigned | consumer | N-D | kontrakt bez zmiany; real state-machine→scope oracle jest w `test_authority_scope.py` |
| `authority_card.py` | owner stanu karty, rezerwacji, pending i latch | writer+consumer | TAK | I5, wspólny lock i merge własnych pól |
| `auto_assign_executor.py` | card/owner/heartbeat/read-back orchestration | consumer+writer outcome | TAK | I1–I4, I7; hermetyczne maybe_execute E2E |
| `tools/auto_assign_monitor.py` | heartbeat producer i validator | writer+consumer | TAK | I7, jawny verdict zawsze obecny |
| `tools/authority_card_verify.py` | operator CLI | consumer | N-D w kodzie | deleguje do poprawionego `clear_latch`; CLI ma osobny oracle |
| `docs/T5_AUTHORITY_CARD_GATE.md` | runbook/operator contract | consumer | TAK | opisuje audit-corrupt, dwa zegary, read-back, verdict i kolejność reconcile |
| `ledger_io`, shadow registry, flags | inne kontrakty | N-D | NIE | brak nowej flagi, metryki historycznej, timera i joba; zero live mutation |

## Testy i dowody

- Baseline przed zmianą dla czterech głównych plików testowych:
  `111 passed`.
- RED-first po dodaniu findings: `12 failed, 3 passed`; czerwone asercje
  reprodukowały I1/I3/I4/I5/I6/I7, a I2 potwierdzał wejście do hooka solve.
- Po fixie ten sam główny klaster: `121 passed`.
- Rozłączne klastry rozszerzone dotkniętych ścieżek:
  - authority-card + CLI: `71 passed`;
  - executor/gate/owner-auth/monitor/TOCTOU: `124 passed`;
  - state-machine/lifecycle/authority-scope: `209 passed`;
  - razem: `404 passed, 0 failed`.
- Mutation/ratchet I6 + I7: `3 passed`; test I7 jawnie pokazuje, że mutant
  ignorujący verdict ponownie uruchamia runner.
- `py_compile`: 9/9 zmienionych plików Python.
- Hermetyczny import: `authority_card`, `auto_assign_executor`,
  `state_machine`, `auto_assign_monitor` — OK.
- Entropy dashboard uruchomiony read-only; pod odciętym sandboxem widział
  `pliki żywego silnika: 0`, więc wynik nie jest miarodajnym pomiarem hosta.

Pełna `tests/` nie weszła w fazę wykonania: kolekcję zatrzymało 12 błędów
środowiskowych (brak hostowych modułów/kanonicznego venv i odcięte ścieżki
hosta). To nie są failujące asercje fix-packa, ale oznacza, że pełna kanoniczna
regresja pozostaje obowiązkową bramką przed merge.

## Rollback

Zmiana nie była wdrażana. Rollback source polega na odwróceniu wyłącznie tego
fix-packa w 10 jawnych plikach; po przywróceniu starego executora I1/I3/I4/I7
wracają, a po przywróceniu starego state-machine wraca mutation I6. Nie wolno
używać rollbacku source do live bez ponownego `py_compile`, import check,
pełnej regresji i osobnego ACK ownera na deploy/restart.

## Poza zakresem / HOLD

- brak dostępu do gita: nie potwierdzono HEAD, nie wykonano diff-check,
  commita, tagu ani push;
- brak dostępu do kanonicznego venv: pełna suita pozostaje HOLD przed merge;
- zero flipu `ENABLE_AUTO_ASSIGN`, karty live, danych runtime, usługi, restartu
  lub deployu;
- brak zmiany wspólnego nazwowego runnera na jawny argument CID; obecny gate
  wymaga jego potwierdzonego read-back;
- wspólne `todo_master.md`, timeline, ledger `/var/lib` i cto-brain są poza
  zapisywalnym sandboxem tej sesji; raport trwały znajduje się w tym worktree.
