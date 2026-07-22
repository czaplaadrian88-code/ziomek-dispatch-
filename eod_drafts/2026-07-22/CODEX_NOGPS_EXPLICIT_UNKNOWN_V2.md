# No-GPS explicit-unknown v2 — dowód poprawek P1/P2

Data: 2026-07-22
Branch: `nogps-explicit-unknown`
Baza poprawek: `dd741a42c976bada3593607fe13fd83ca66a8239`
Commit kodu: `15b48e4071e0651156c29255642f466e42a7cd8f`

## Wynik

Kandydat naprawia obie uwagi cross-checku i jest gotowy do ponownej,
niezależnej recenzji CTO. Nie wykonano merge, flipu flag, deployu, restartu
ani zapisu do żywego stanu.

## P1 — OFF nie wskrzesza odrzuconego kuriera

- `core/candidates.py:127`: wynik decyzyjny jest teraz wyłącznie wynikiem
  aktywnej polityki. Przy main OFF `primary = legacy`; brak fallbacku
  `legacy or explicit`.
- `core/candidates.py:121-126`: wynik explicit może zostać zachowany tylko
  w bocznym kanale wariantów dla shadow. Nie trafia do puli decyzyjnej OFF.
- `dispatch_pipeline.py:1166-1204`: kontrfakt shadow buduje osobną pulę z
  wariantów, dzięki czemu prawdziwy selektor widzi również kandydata
  explicit-only bez przecieku do puli legacy.
- `tests/test_explicit_unknown_position_model.py:250`: invalid position
  daje `legacy=None`; main OFF zwraca `None`, a main ON zwraca explicit.
- `tests/test_explicit_unknown_position_model.py:348`: test E2E selektora:
  faktyczna pula OFF wybiera G, osobna pula kontrfaktu wybiera explicit-only U.

## P2 — kosztowny shadow ma osobną bramkę

- `common.py:354,1793`: nowa decision flag
  `ENABLE_EXPLICIT_UNKNOWN_POSITION_SHADOW`, default OFF.
- `dispatch_pipeline.py:1217`: wrapper OFF wywołuje selektor dokładnie raz i
  nie tworzy payloadu shadow; dual-select działa tylko przy shadow ON.
- `dispatch_pipeline.py:4757,5007-5022`: flaga jest snapshotowana raz;
  mapa wariantów i lock powstają tylko przy shadow ON.
- `core/candidates.py:111-114`: shadow OFF prowadzi bezpośrednio do jednego
  wywołania legacy/active eval, bez explicit resolvera i bez dual-eval.
- `tools/flag_lifecycle_registry.json:8806`: shadow flag ma default OFF,
  lifecycle `shadow` i relację `twin_of` z flagą główną.
- `tests/test_explicit_unknown_position_model.py:291,323`: testy dowodzą
  inertności obu flag OFF oraz jednego selektora/braku payloadu shadow.
- `tests/test_explicit_unknown_position_model.py:348`: shadow ON dowodzi
  dual-select przez prawdziwy `core.selection.select_and_emit`.

Semantyka stanów:

| Main | Shadow | Zachowanie |
|---|---|---|
| OFF | OFF | legacy, jeden eval i jeden selector, bez payloadu shadow |
| OFF | ON | decyzja legacy; osobny kontrfakt explicit przez prawdziwy selector |
| ON | OFF | decyzja explicit, bez dual-eval |
| ON | ON | decyzja explicit plus kontrolowany kontrfakt legacy |

## Weryfikacja

- Testy nowego pliku: **17 passed, 0 failed**.
- Klaster 17 dotkniętych plików testowych: **136 passed, 0 failed**.
- P1: OFF nie wskrzesza invalid-position courier — PASS.
- P2: obie flagi OFF = jeden eval, resolver explicit niedotknięty, jeden
  selector, brak payloadu shadow — PASS.
- Shadow ON: kontrfakt przez prawdziwy selector — PASS.
- `py_compile` dotkniętego Pythona — PASS.
- Import `common`, `core.candidates`, `dispatch_pipeline`,
  `shadow_dispatcher` — PASS.
- `git diff --check` — PASS.
- `flag_lifecycle_check.py --skip-external`: **524/524 curated, 0 błędów**.
- `flag_lifecycle_seed.py --merge`: wykonany na tymczasowej kopii rejestru;
  nowa flaga wykryta z default OFF i lifecycle shadow. Nie zapisano żadnego
  zewnętrznego/live źródła flag.
- Entropy na rzeczywistym root klonu: exit 0; **423 files, poison 0,
  instrument 4**, bez delty względem v1.

Pełna suita: kanoniczny interpreter
`/root/.openclaw/venvs/dispatch/bin/python` jest niewykonywalny w tym
sandboxie. Uruchomiono identyczny symlink-pkgroot harness systemowym Pythonem
na bazie `dd741a42` i na v2. Obie rewizje zatrzymały kolekcję na tej samej
liście 10 brakujących/niedostępnych zależności, z 4 skipami:
**delta 0 nowych błędów (10→10), skip 4→4**. To jest dowód braku delty, nie
deklaracja kanonicznej zielonej pełnej suity; re-review powinien powtórzyć
pełną suitę w środowisku z działającym venv.

Mechaniczna bramka `ziomek-cto dod`: PASS. N-D rozliczono per plik dla
bliźniaków selection, feasibility, scoring i equal-treatment; ich kod nie
wybiera polityki pozycji, a prawdziwy selector został użyty bez modyfikacji.

## Ryzyko i rollback

Obie flagi są OFF, więc po samym merge nie ma zmiany decyzji ani dual-eval.
Włączenie shadow lub main nadal wymaga osobnego ACK i kontrolowanego okna.
Rollback kandydata: revert commitów v2. Nie ma migracji danych ani operacji
runtime.

## Rekomendacja

Przekazać bundle v2 do świeżego re-review CTO, z naciskiem na:

1. P1: explicit-only courier nie może znaleźć się w rzeczywistej puli OFF.
2. P2: OFF/OFF musi wykonać dokładnie jeden eval i jeden selector.
3. Shadow ON ma porównać osobne pule przez prawdziwy selector.
4. Powtórzyć kanoniczną pełną suitę w niesandboxowanym venv przed merge.
