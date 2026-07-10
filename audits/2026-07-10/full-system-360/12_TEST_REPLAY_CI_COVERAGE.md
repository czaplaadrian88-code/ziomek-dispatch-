# Testy, replay i pokrycie CI

## Baseline odzyskanej sesji

Komenda: venv dispatch, `python -m pytest tests/ -q`, worktree na `70af4fa`,
`DISPATCH_UNDER_PYTEST=1`, poprawny pkgroot i tymczasowa kopia flag. Odzyskany
bieg nie ustawił `HERMETIC_STRICT=1`; wykonano więc osobny kanoniczny rerun
STRICT.

Wynik: **4846 passed, 1 failed, 27 skipped, 8 xfailed, 2 xpassed**.

Jedyny fail: `test_flag_registry_f3::test_open_and_accepted_partition_issues`.
Po migracji/flipie `USE_V2_PARSER` narzędzie klasyfikuje pozostały env carrier
jako zwykły `open` (`json-overrides-env`). Test nadal wymaga historycznego
`known-open` cross-service. To `TOOL-FALSE-RED + STALE-EVIDENCE`, nie regres
parsera. Dodatkowo `flag_registry.FLAGS_JSON` ma twardą ścieżkę do live
`flags.json`, więc tymczasowa kopia nie izoluje tego wejścia. Baseline jest
czerwony i częściowo zależny od runtime; nie wolno nazywać go hermetycznym.

Pierwszy bieg audytu z pkgrootem bez `flags.json` dał 10 faili i został
unieważniony. Po dodaniu tymczasowej kopii dziewięć zniknęło; to dowód, że sam
wynik pytest bez provenance środowiska nie jest wystarczający.

## Kanoniczny rerun STRICT

Wynik: **4792 passed, 6 failed, 76 skipped, 8 xfailed, 2 xpassed**.

- TEST-11 pozostaje czerwony z przyczyny opisanej wyżej;
- pięć script-tests próbuje czytać live pliki kurierów i nie ma wpisu w
  `tests/hermetic_quarantine.json`;
- HERMETIC-GUARD poprawnie zablokował te odczyty/zapisy, więc nie doszło do
  dotknięcia produkcji;
- to pre-existing baseline na `70af4fa`: raporty audytu nie uczestniczą w
  kolekcji `tests/` i nie mogą wywołać tych faili.

Nie dopisano ad hoc skipów i nie osłabiono guarda. TEST-11 oraz jawna, zewnętrzna
kwarantanna pięciu testów wymagają osobnego sprintu test-hygiene.

Celowany klaster mechanizmów FEAS/best-effort, plan/CAS, carry-chain i replay
gate w STRICT: **77 passed, 1 xfailed, 0 failed**.

## Replay

- world replay gate ma cztery rozłączne klasy: 185 parity bez miss, 1 soft diff
  bez miss, 22 soft diff + OSRM miss i 2 miss-only; razem 210;
- agregaty narzędzia to 23 miękkie różnice, 0 krytycznych i 24 rekordy z OSRM
  miss. Diff i miss nakładają się, więc nie wolno ich sumować jak rozłącznych;
- 22/23 miękkich różnic współwystępuje z missem, więc nie wolno przypisać ich
  automatycznie brakującym plikom live input;
- gate jest informacyjny i czerwienieje głośno — to partial trust, nie false green;
- paired replay Sprintu 3 był poprawniejszym oraclem dla flagi niż podmiana live
  `flags.json`, bo rekord odtwarza własny snapshot flag.

## Luki pokrycia

- P3 z odzyskanego audytu nie miało niezależnej weryfikacji;
- brak network deny-guard i hardcoded read live `flags.json` pokazują, że write
  guard nie jest pełną izolacją wejść;
- xpass/skip oceniać listą nodeid, nie samą sumą;
- nocny health scoreboard może przez dobę pokazywać już naprawiony fail.
