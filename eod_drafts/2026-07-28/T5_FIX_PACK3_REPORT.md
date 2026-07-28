# T5 FIX-PACK 3 — raport wykonawczy po blind review r3

Data: 2026-07-28
Rola: Sol, wykonawca pod nadzorem CTO
Worktree: `/root/worktrees/dispatch_v2/active/20260728-t5-authority-card-gate-cto`
Baza wskazana przez ownera: `a54ea2572`
Operacje live: brak; zero flag, deployu, restartu, migracji i zapisu runtime.

## Wynik

Sześć findings H1–H6 zostało naprawionych u źródła na granicy executor ↔
identity/runner, w stanie authority-card i w finalnych gate'ach wykonania.
`maybe_execute` zachowuje kontrakt „nigdy nie rzuca”.

| Finding | Fix u źródła | Oracle / ratchet | Klasyfikacja odmowy i latch |
|---|---|---|---|
| H1 CID przez łańcuch | Przed runnerem canonical `identity.Registry.resolve(..., worker, bare_key_strict=True)` musi dać zamierzony `canon_cid`; CID z `verify_ok_kid=` jest porównywany po sukcesie | ambiguous/mismatch: runner nieuruchomiony albo read-back innego CID | `runner_identity_ambiguous`: staleness config, bez latcha i rezerwacji; `runner_identity_mismatch`: unknown, rezerwacja zostaje, latch + reconcile |
| H2 OSError pre/post start | `Popen` jest osobno od `communicate`; tylko wyjątek z utworzenia procesu jest pre-send | child zapisuje marker skutku, potem `communicate` rzuca `OSError`; liczniki zostają | `pre_send_refusal:*`: rollback bez latcha; każdy błąd po starcie: `runner_outcome_unknown`, latch, rezerwacja zostaje |
| H3 brak stanu | `initialize_state` atomowo tworzy 0600 stan z `initialized_for_card`/`initialized_at`; executor wiąże istniejący receipt z markerem stanu | RED + mutation control: usunięcie obu guardów odtwarza wykonanie na świeżym `empty_state`; clear latcha `state_missing` jest zabroniony; monitor receipt-vs-brak-stanu alarmuje | `state_missing` albo `state_card_mismatch`: latch; brak resetu budżetu |
| H4 zegar po lockach | Jeden `_fresh_execution_now` jest pobierany zaraz po obu lockach i zasila finalny auth TTL, card window, heartbeat oraz proposal age | wejście świeże, zegar po lockach +61 s → `monitor_heartbeat_stale`, runner bez wywołania | heartbeat stale: latch; proposal staleness pozostaje klasą bez latcha |
| H5 finalny hot gate | Końcowy odczyt `ENABLE_AUTO_ASSIGN` i fingerprint pochodzi bezpośrednio z `flags.json`, z pominięciem cache/FlagSnapshot, tuż przed rezerwacją | flip OFF w hooku recheck → odmowa; mutation zamrażająca stare ON ponownie wykonuje; osobny ratchet snapshot ON/source OFF | `flag_off_at_execution`: bez latcha i bez rezerwacji |
| H6 stop contract | `verify_card` porównuje z `EXPECTED_STOP_CONTRACT_SHA256`; executor/CLI przekazują stałą; template ją emituje | karta z `0*64` i poprawnym podpisem → `stop_contract_mismatch` | mismatch przechodzi przez fail-closed authority gate i latch |

## Hash stop-contract

Źródło: `/root/handover/KARTA_KLASY_AUTO_CANARY_2026-07-29.md`, sekcje 3+4.
Algorytm 1:1 jest przy stałej w `authority_card.py`: od nagłówka `## 3.` do
ostatniego niepustego wiersza sekcji 4, bez separatora przed `## 5.`;
`rstrip(" \t")` per linia; połączenie `\n`; bez końcowego newline; UTF-8; SHA-256.

Wartość:
`91997392295092fd6cd3bc0d54926261d2074e94e74f67731cf4cd50c2aff42d`.
Niezależne odtworzenie w sandboxie dało identyczny wynik.

## RED-first i testy

- RED przed implementacją: `8 failed, 49 deselected`.
- Bezpośredni klaster authority/executor/CLI/monitor/toctou:
  `111 passed in 4.28s`.
- Pełna dotknięta powierzchnia authority, auto-assign, identity registry,
  collision/onboarding, FlagSnapshot i ETAP4 flags:
  `260 passed in 12.07s`.
- `py_compile`: 8 zmienionych plików Python — OK; artefakty wyłącznie `/tmp`.
- Hermetyczny import: authority_card, auto_assign_executor, common — OK.
- trailing whitespace: brak.
- Pełne `tests/`: pierwszy start zatrzymał się na 12 błędach collection
  wynikających z sandboxowych braków hostowych modułów/konfiguracji. Po podaniu
  repozytoryjnych, hermetycznych ścieżek przebieg wykonywał bardzo wolne testy
  procesowe i został świadomie przerwany bez werdyktu; nie jest raportowany jako
  pass ani jako regresja kodu.

## Świadomie poza zakresem

- Współdzielony bojowy `deploy_staging/scripts/gastro_assign.py` nie został
  zmieniony. Nie ma trybu przyjęcia oczekiwanego CID; obecny `--verify` zwraca
  jednak `verify_ok_kid`, który executor teraz sprawdza. Rekomendowana osobna
  karta: dodać jawny argument CID i zweryfikować go end-to-end bez zmiany
  semantyki innych callerów.
- Repo nie zawiera writera podpisu PIN panelu. Dodano egzekwowalny
  `authority_card_verify.py initialize-state`; dokumentacja stanowi, że tor
  podpisu nie jest gotowy przed jego sukcesem. Integracja wywołania z zewnętrznym
  panelem podpisującym należy do osobnej, właścicielskiej ścieżki.
- Brak repozytoryjnego venv i niedostępny git były zadeklarowaną cechą sandboxu.
  Nie wykonano commita/tagu ani kanonicznej pełnej suity venv.

## Rollback

Source-only: revert jawnych zmian w `authority_card.py`,
`auto_assign_executor.py`, `common.py`, CLI, dokumentacji i testach. Nie ma
rollbacku runtime, bo runtime nie został dotknięty. Killswitch produkcyjny
pozostał bez zmian i nie był odczytywany ani przełączany live.

## Linie dowodowe dla bramki DoD

regresja: 260 passed / 0 failed na całej dotkniętej powierzchni; pełne tests/ niewerdyktowe z opisanym ograniczeniem sandboxu.
e2e: 111 passed / 0 failed przez authority card → locki → identity → rezerwację → runner/read-back → latch/rollback/monitor.
pozytywny-wplyw: sześć niezależnie reprodukowanych fałszywych sukcesów/resetów/TOCTOU jest teraz fail-closed; H3 i H5 mają kontrolę mutacyjną przywracającą defekt.
rollback: source-only revert jawnych plików; zero runtime/live, a killswitch pozostał nietknięty.
N-D: pełny replay produkcyjny, 2-dniowe shadow i deploy są N-D, bo FIX-PACK jest source-only i owner zabronił flag/deploy/live.
