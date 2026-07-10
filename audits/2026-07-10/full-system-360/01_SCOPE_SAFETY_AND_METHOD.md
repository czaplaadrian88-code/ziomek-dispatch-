# Zakres, bezpieczeństwo i metoda

## Źródła

- kod na `70af4fa` i testy na tej samej bazie;
- żywe mapy: CODEMAP, ARCHITECTURE, kanon, inwarianty i backlog;
- ograniczone agregaty systemd, parser health, health scoreboard, replay gate i
  karty werdyktów;
- odzyskany raport Claude’a oraz załącznik 106 findings;
- niezależna ponowna kontrola najwyższego ryzyka i meta-audyt narzędzi.

## Bezpieczny przebieg

| Obszar | Dozwolone | Niedozwolone / niewykonane |
|---|---|---|
| Kod | grep, AST, git log/diff, testy w worktree | edycja produkcyjnego kodu, merge |
| Runtime | status, PID/NRestarts, agregaty, krótkie journal windows | restart, reload, chaos, load test |
| Dane | metadane, liczniki, zredagowane karty | pełne rekordy PII/GPS, mutacje baz |
| Flagi | odczyt wybranych booli | flip, zapis `flags.json` |
| Sekrety | istnienie/tryb/owner bez wartości | odczyt wartości lub kopiowanie do raportu |

Testy dostały `DISPATCH_UNDER_PYTEST=1`, worktree `PYTHONPATH` i tymczasową kopię
flag. Pierwszy bieg z błędnym rootem został jawnie odrzucony jako nieważny; brak
`flags.json` wywołał 10 faili środowiskowych. Po korekcie ten sam klaster miał
20/21, a pełna suita 4846/1. Późniejsza kontrola wykazała, że
`flag_registry.FLAGS_JSON` nadal czyta live ścieżkę, więc kopia nie izoluje
TEST-11. To rozróżnienie jest częścią dowodu, nie kosmetyką.

Kanoniczny rerun z `HERMETIC_STRICT=1` zakończył się wynikiem 4792/6/76/8/2
(pass/fail/skip/xfail/xpass). Pięć dodatkowych faili to script-tests zależne od
live plików kurierów, nieobecne w aktualnej kwarantannie. Guard zablokował odczyty;
nie wykonano żadnej mutacji produkcji.

## Taksonomia

- `CONFIRMED`: mechanizm potwierdzony w kodzie i co najmniej jednym niezależnym
  review; severity nadal może wymagać decyzji biznesowej.
- `PARTIAL`: fragment mechanizmu potwierdzony, ale przyczyna, skala albo wpływ
  pozostaje nieudowodniony.
- `PLAUSIBLE`: mechanizm jest spójny, lecz brakuje reprodukcji/oracle.
- `DISPUTED`: reviewerzy nie zgodzili się co do faktu lub wpływu.
- `REFUTED`: scenariusz centralny obalony; residual może zostać P3.
- `UNVERIFIED`: hipoteza, głównie odzyskane P3.
- `STALE`: dowód był prawdziwy w chwili T, ale nie opisuje aktualnego HEAD/runtime.

## Ograniczenia

Nie wykonano live fault injection, zewnętrznego skanu sieci, restore drill ani
pełnych reprodukcji opartych o rekordy zawierające PII. Dowód „brak” oznacza
niezweryfikowane, nie „bezpieczne”.
