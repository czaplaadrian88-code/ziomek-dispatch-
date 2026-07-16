# Ziomek Change Gate — remediation cycle 6

Data autora: 2026-07-16 UTC

Status: `STAGED_ONLY_REVIEW_REQUIRED`

Wynik autora: `READY_FOR_SUPERVISOR_FALSE_GREEN_AUDIT`
Klasa dowodu: `AUTHOR_ONLY_STATIC_SELF_CHECK_NON_INDEPENDENT`

Ten raport nie jest niezależnym review, zgodą na aktywację ani authority live.
Kandydat pozostaje poza discovery. Nie wykonano instalacji, aktywacji, merge,
rebase, cherry-pick, push, deploy, restartu, flipa, migracji, odczytu lub
mutacji danych runtime, lease'u, routera MAIN ani tmuxa.

## Tożsamość i wejście

- rola: `NON-MAIN/internal-only`;
- model tier: `sol`;
- exact model: `gpt-5.6-sol`, lokalnie atestowany w cache modeli;
- effort: `max`;
- uzasadnienie: R4, bo publiczna granica readiness dawała dwa niezależne
  false READY dla niezweryfikowanych danych zaufania;
- task SHA-256:
  `5862863e02de9fb1f511e1906d29b2972ce797142555b4819eadaafdddd7921d`;
- branch: `codex/ziomek-skill-gate-remediation6-20260716T200118Z`;
- worktree:
  `/root/ziomek_skill_gate_remediation6_20260716T200118Z/dispatch_v2`;
- exact input commit: `2f12fd4c7c2bb3a2ce351ad4c9139ef9ef512e90`;
- input tree: `3d7453fb6eca6bd8befd11223b84d019628fafc5`;
- exact parent: `0bcf1b7711a1e0e5e761c77a6fab5d668e005cfa`;
- original base: `6b4b040032d54db5be7643648676d835e0db9146`;
- poprzedni tag pozostaje bez zmian:
  `ziomek-change-gate-remediation4-staged-20260716T173743Z`.

## Potwierdzony false-green przed edycją

Baseline na exact input był zielony: official quick validator rc=0, custom
validator rc=0, 12 cases i dokładnie 207 unikalnych wcześniejszych mutacji.
SHA-256 posortowanej listy 207 etykiet wynosił
`e4962df95146acb369863a553b325de0faf56dcdcd10815d98c3aa1af022346d`.

Dwa niezależne replaye przeszły publicznym
`validate_result_relations()` bez wcześniejszego helpera:

1. minimalny forged registry przypisał realny `core/selection.py` do
   podrobionego staged rootu; publiczna relacja zwróciła rc=0, exception=none,
   `blocker_codes=[]`, `disposition=READY_FOR_REVIEW` — `SURVIVED`, podczas gdy
   jawne `validate_registry_object()` odrzucało ten registry;
2. kanoniczny registry oraz prywatny alternatywny `artifact_root` zawierający
   wszystkie cztery oczekiwane ścieżki, lecz wyłącznie błędne bajty, także dały
   rc=0, exception=none, `blocker_codes=[]`,
   `disposition=READY_FOR_REVIEW` — `SURVIVED`.

Root cause: publiczny łańcuch przyjmował `registry`, `skill_id` i
`artifact_root` jako osobne, caller-controlled fakty, ale sprawdzał głównie
kształt `candidate_effect_boundary` i obecność plików. Schema registry, pełne
relacje, kompletność pakietu, dokładne bajty, tryb oraz wspólna tożsamość rootu
nie stanowiły jednej obowiązkowej precondition. Helper mógł odrzucić input, lecz
publiczna dyspozycja nie musiała go wcześniej wywołać.

## Jedna granica zaufania

Implementacja używa jednego immutable `VerifiedReadinessContext`:

1. tworzy detached strict-JSON snapshot całego registry;
2. sprawdza go trusted registry schema i wszystkimi relacjami;
3. wiąże dokładnie jeden `skill_id`, identity-derived staged root i jego piny;
4. sprawdza pełny zbiór plików pakietu, bez braków i dodatkowych plików;
5. odrzuca unsafe path, sibling/cross-skill, symlink i plik specjalny;
6. wymaga dokładnego trybu `100644` i SHA-256 każdego pinu;
7. wiąże registry, root, staged root, piny i package digest własnym integrity
   digestem oraz powtarza weryfikację przy użyciu kontekstu.

Wybrana i jedyna polityka rootu to
`ALTERNATE_ALLOWED_AFTER_COMPLETE_EXACT_PIN_VALIDATION`. Alternatywny root jest
dozwolony tylko po pełnej kontroli exact pakietu. Dzięki temu kanoniczny skill,
exact kopia alternatywna i drugi niezależny poprawny skill mogą być READY, ale
błędne bajty, brak, symlink, FIFO, executable, dodatkowy plik lub cross-skill
zawsze failują zamknięcie.

Każdy nieważny kontekst produkuje dokładnie jeden centralny blocker
`READINESS_CONTEXT_INVALID`. Nie zależy on od kolejności błędów wyniku.
`derive_disposition()` ponownie wylicza oczekiwane blockery, nie przyjmuje
pustej listy jako side door i wyprowadza `HOLD`.

## Mapa kompletności

| miejsce | rola | writer/consumer | dotknięte TAK/N-D | powód | test |
|---|---|---|---|---|---|
| registry schema | zamknięty kontrakt registry | registry, trusted validator | TAK | root policy i mode policy są wymagane | strict schema, semantic drift |
| registry object + relations | granica zaufania | context constructor | TAK | wszystkie caller values muszą być zweryfikowane | forged minimal, schema-valid noncanonical |
| canonical i drugi `skill_id` | identity i multi-skill | registry resolver, readiness | TAK | brak canonical-only hardcode | canonical READY, synthetic READY, cross-skill HOLD |
| staged roots i piny | package allowlist | registry, package verifier | TAK | exact identity-derived containment | sibling, traversal, NFKC/case, pin drift |
| artifact-root policy | efektywny root | public readiness APIs | TAK | jawny wybór alternate-after-exact | exact alternate READY, wrong alternate HOLD |
| pełne exact-byte package | integrity | package verifier | TAK | obecność nie dowodzi tożsamości | wrong SHA, missing, extra |
| result schema | central blocker enum | result producer, validator | TAK | dodano `READINESS_CONTEXT_INVALID` | constructed HOLD schema pass |
| case schema | kształt pojedynczego case | corpus validator | N-D | struktura case bez zmiany | meta-schema, strict JSON |
| corpus schema | inventory 12 cases | corpus validator | N-D | liczba i kształt bez zmiany | 12/12, min/max |
| `cases.json` | author golden corpus | result/corpus APIs | N-D | semantyka 12 cases pozostaje poprawna | ZCG-07/08/10 READY, role 12/12 |
| context constructor | tworzenie trusted boundary | wszystkie publiczne wejścia | TAK | usuwa opcjonalną precondition | forged/wrong root public replays |
| context validator | recheck immutable boundary | helpery `_with_context` | TAK | brak bocznego wejścia przez fabricated context | integrity tamper KILLED |
| artifact-pin validator | exact package consumer | official registry, mutations | TAK | usunięto caller byte-map | 12 byte-pin probes |
| candidate write-set validator | registry-bound subset | candidate readiness | TAK | korzysta wyłącznie z verified context | product, flags, shared governance KILLED |
| result relations | public oracle | result producer | TAK | wspólny context przed blockerami | forged i wrong bytes KILLED |
| corpus object | public corpus oracle | 12 cases | TAK | ta sama precondition co result | oba false-green corpus KILLED |
| blocker derivation | central source of truth | relations, disposition | TAK | invalid context zastępuje kolejność błędów | exact singleton blocker |
| disposition derivation | READY/HOLD | relations, caller | TAK | recompute blockerów zamyka empty-list bypass | forged/wrong/boundary HOLD |
| READY/HOLD matrix | lane oracle | author cases | TAK | nowe context precondition bez zmiany lane tuples | ZCG-07/08/10 positive |
| path/component checks | containment | pins i write-set | TAK | tekstowy prefix nie wystarcza | absolute, `..`, slash, Unicode, sibling |
| file type/mode | filesystem trust | package verifier | TAK | każdy pin musi być regular 100644 | symlink, FIFO, executable, missing |
| mutation matrix | author negative oracle | validator, supervisor | TAK | oba false-green muszą być publicznie zabite | 236/236, 0 SURVIVED |
| `SKILL.md` | kontrakt użytkowy | jawny invoker, reviewer | TAK | dokumentuje verified context i root policy | official quick validator, byte pin |
| `openai.yaml` | metadata i implicit policy | przyszły loader | N-D | exact bytes już poprawne | hash parity, implicit=false raz |
| navigation | ordered bootstrap | skill, operator | N-D | luka nie dotyczy źródeł | hash parity, order mutations |
| gate contract | kontrakt result/corpus | skill, validator, reviewer | TAK | central blocker i root policy | literal pins, byte pin |
| source pins | exact candidate bytes | registry, context | TAK | zmienione dwa pliki pakietu | SHA-256 wszystkich 4 plików |
| report/commit/tag/rollback | audyt i punkt cofnięcia | supervisor | TAK | cycle 6 ma być jednym lokalnym postimage | diff/pathset, annotated tag, revert plan |
| discovery/activation | loader boundary | przyszły osobny sprint | N-D | staged path pozostaje poza discovery | target absent, implicit=false |
| product/runtime/flags/live | system Ziomka | procesy produkcyjne | N-D | governance-only, jawny zakaz tasku | zero product paths/imports/live calls |
| shared memory/backlog/handover | wspólny stan MAIN | aktywny MAIN | N-D | ta sesja jest internal-only | zero shared edits |

## Goldeny i mutacje autora

Po zmianie:

- oba wcześniejsze false READY są KILLED przez publiczne result relations,
  corpus, blockers i disposition;
- forged minimal i schema-valid, lecz relacyjnie błędny registry dają wyłącznie
  `READINESS_CONTEXT_INVALID` oraz `HOLD`;
- drift `artifact_root_policy` albo `file_mode_policy` w registry daje ten sam
  centralny blocker i `HOLD`;
- wrong-byte alternate root, missing, symlink, FIFO, executable i extra file
  dają ten sam centralny blocker i `HOLD`;
- skonstruowany wynik z centralnym blockerem oraz `HOLD` przechodzi result
  schema i publiczne relation validation;
- exact kanoniczny root i exact alternatywny pakiet dają
  `READY_FOR_REVIEW`;
- drugi niezależny `ZIOMEK_FUTURE_SKILL` daje READY tylko dla własnego exact
  pakietu; cross-skill daje HOLD;
- `core/selection.py` i `flags.json` zachowują
  `CANDIDATE_WRITE_SET_OUTSIDE_REGISTRY_BOUNDARY` oraz HOLD;
- ZCG-07 i ZCG-10 pozostają `READY_FOR_IMPLEMENTATION`, ZCG-08 pozostaje
  `READY_FOR_REVIEW`;
- `ROLE_ATTESTATION` występuje dokładnie raz w 12/12; removal, duplicate i
  downgrade są KILLED;
- duplicate keys oraz `NaN`, `Infinity`, `-Infinity` pozostają KILLED dla
  registry, cases i wszystkich czterech schematów.

Macierz mutacji:

- wcześniejszy zbiór: 207/207 KILLED, exact SHA-256 posortowanych etykiet
  `e4962df95146acb369863a553b325de0faf56dcdcd10815d98c3aa1af022346d`;
- nowe cycle 6: 29/29 KILLED, SHA-256
  `b91f254eee7a479ff33796ae3bd1d3432fa9fb7fb90612f11db0ed6fb63b6f81`;
- pełny zbiór: 236/236 KILLED, 0 SURVIVED, SHA-256
  `32f3334a6968ae063498856c31df9822a46a6a299b874d0990b8c7de875ea1fa`;
- historyczny podzbiór 163 nadal ma SHA-256
  `0156c4559182e4186b53df92d8cd665fdb90f00769a15d2a4aaa98022cfa4a4b`.

## Walidacja statyczna

- official quick validator: rc=0, `Skill is valid!`;
- custom validator: rc=0, `validated_static_scope`;
- strict schemas: 4; strict JSON files: 6; author cases: 12;
- AST/import: wyłącznie stdlib (`__future__`, `copy`, `dataclasses`, `hashlib`,
  `json`, `math`, `os`, `pathlib`, `re`, `stat`, `sys`, `tempfile`, `typing`,
  `unicodedata`); product imports: 0;
- isolated `py_compile`: rc=0; prywatny `PYTHONPYCACHEPREFIX` usunięty;
- unconsumed top-level symbols: 0;
- `git diff --check`: rc=0;
- zmienione pliki: regularne, tryb `100644`; staged candidate bez symlinków i
  executable;
- repo `__pycache__`: 0;
- product pytest, runtime, flags, PID, NRestarts, health i live replay: N-D —
  jawnie zabronione i bez product consumera.

Source pins pakietu:

- `SKILL.md`:
  `34b95dbd2a53b3f892dc7cc56caadf6e7c94ae6a9323614e1624fed0104d3f4c`;
- `agents/openai.yaml` bez zmiany:
  `d791a50a4ffcb7d2def662405ae30fbb452682c5cd272f82081a2e1c84c5d901`;
- `references/canonical-navigation.md` bez zmiany:
  `725d579b3a4a4614456f95db49df7adbccda5cd984ed96127ff5b1aa3bb4c5e6`;
- `references/gate-contract.md`:
  `4d04002128d0ce23bbf50fce754c2634e0abc9e34e3eb41a3a05cacd6a874ff3`.

## Granice wydania i rollback

Cycle 6 zmienia wyłącznie governance w jawnie dozwolonym 12-path scope. Exact
cycle-6 diff nie zawiera produktu. Pełny original-base-to-target pathset ma
pozostać tym samym zamkniętym zbiorem 12 ścieżek skill gate, wszystkie jako
regularne `100644`, bez symlinków i executable.

Plan rollbacku:

1. przed integracją usunąć wyłącznie ten izolowany branch/worktree i lokalny
   annotated tag `ziomek-change-gate-remediation6-staged-20260716T200118Z`;
2. po hipotetycznej integracji wykonać jawny revert wyłącznie jednego commita
   cycle 6;
3. zweryfikować powrót registry version, root/mode policy, central blocker,
   source pins i tree do exact input;
4. nie restartować niczego — rollback jest repo-only i nie dotyka live.

`candidate_commit` i `candidate_tree` celowo pozostają
`UNPINNED_UNTIL_INDEPENDENT_REVIEW`, aby autor nie tworzył self-referential
approval. Exact commit, tree, annotated tag object i peel są atestowane dopiero
w prywatnym handoffie po utworzeniu lokalnego postimage. Następny krok to
świeży supervisor-controlled false-green audit; aktywacja wymaga osobnej
decyzji i osobnego zakresu.
