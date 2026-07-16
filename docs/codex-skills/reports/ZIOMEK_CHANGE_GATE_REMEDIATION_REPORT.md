# Ziomek Change Gate — remediation cycle 4

Data autora: 2026-07-16 UTC

Status: `STAGED_ONLY_REVIEW_REQUIRED`

Wynik autora: `READY_FOR_SUPERVISOR_FALSE_GREEN_AUDIT`
Klasa dowodu: `AUTHOR_ONLY_STATIC_SELF_CHECK_NON_INDEPENDENT`

Ten raport nie jest niezależnym review, zgodą na aktywację ani authority live.
Kandydat pozostaje poza ścieżką discovery, bez instalacji, merge, push, deploy,
restartu, flipa, migracji, danych runtime, lease'u i mutacji tmux.

## Wejście i odrzucony baseline

- model tier: `sol`;
- exact model: `gpt-5.6-sol`, lokalnie atestowany;
- effort: `max`;
- powód: R4 governance i false-green centralnej dyspozycji;
- branch: `codex/ziomek-skill-gate-remediation4-20260716T173743Z`;
- worktree: `/root/ziomek_skill_gate_remediation4_20260716T173743Z/dispatch_v2`;
- input commit: `0bcf1b7711a1e0e5e761c77a6fab5d668e005cfa`;
- input tree: `7f413ec1c858d8c7862aed35919e9b6f33344f68`;
- original base: `6b4b040032d54db5be7643648676d835e0db9146`;
- poprzedni sealed tag zachowany bez zmian:
  `ziomek-change-gate-remediation3-staged-20260716T152915Z`.

Cycle 3 był false-green. Publiczna mutacja case
`ZCG-08-COMPLETE-CANDIDATE-NO-LIVE-ACK` zachowywała
`mutation_surface=[STAGED_ARTIFACTS]`, ale ustawiała
`write_set=[dispatch_v2/core/selection.py]`. Baseline `validate_corpus_object()`
zwrócił rc=0 i `SURVIVED_PRODUCT_PATH_MISLABELED_STAGED_ARTIFACTS`.

Root cause był relacyjny, nie składniowy:

1. `validate_result_relations()` sprawdzał tylko bezpieczny zapis i unikalność
   ścieżek;
2. `candidate_effect_boundary_is_safe()` ufał etykiecie
   `STAGED_ARTIFACTS`, niepustemu `write_set` i `read_only_no_effect=false`;
3. validator nie wiązał każdej ścieżki z rootem oraz artefaktami dokładnie tego
   wpisu registry;
4. stara mutacja jednocześnie zmieniała surface na `PRODUCT_CODE`, więc nie
   testowała kłamliwej etykiety.

## Jedna semantyka `write_set`

Dla READY z `mutation_surface=[STAGED_ARTIFACTS]`, `write_set` oznacza wyłącznie
exact pliki runtime pakietu skilla faktycznie objęte kandydatem. Każdy plik:

- jest bezpieczną ścieżką względną bez absolutu, `..`, backslasha, pustego lub
  kropkowego segmentu;
- leży komponentowo pod `staged_candidate_path` tego samego wpisu registry;
- jest exact elementem `pin.candidate_artifacts.files[].path` tego wpisu;
- jest regularnym, niesymlinkowanym i niewykonywalnym plikiem, a każdy ancestor
  jest realnym katalogiem;
- jest porównywany exact, przy osobnym odrzuceniu kolizji NFKC/casefold.

`owned_paths` pozostaje szerszym zakresem autorstwa governance i nie jest
allowlistą wyniku. Dlatego wspólne registry, schema, eval i report, produkt,
`flags.json`, sibling-prefix oraz root innego skilla są poza candidate boundary.

Registry opisuje ten kontrakt strukturalnie przez
`candidate_effect_boundary`, a `validate_candidate_write_set()` konsumuje root
i exact allowlistę z tego samego wpisu. Poprawna etykieta z niedozwoloną ścieżką
dodaje centralny blocker
`CANDIDATE_WRITE_SET_OUTSIDE_REGISTRY_BOUNDARY`, co daje `HOLD`.

## Mapa kompletności

| miejsce | rola | writer/consumer | dotknięte TAK/N-D | powód | test |
|---|---|---|---|---|---|
| `ZIOMEK_SKILLS_REGISTRY.json` | kanoniczny boundary/piny | autor, validator, reviewer | TAK | nowa strukturalna semantyka i tag rollback | schema, relations, multi-entry |
| `candidates/.../SKILL.md` | kontrakt użytkowy | jawny invoker, reviewer | TAK | jawna definicja registry-bound write-set | official quick validator, byte pin |
| `agents/openai.yaml` | metadata UI | przyszły loader | N-D | treść i implicit=false są poprawne | hash bez zmiany, exact policy |
| `canonical-navigation.md` | bootstrap | skill, operator | N-D | luka nie dotyczy kolejności źródeł | hash bez zmiany, ordered bootstrap |
| `gate-contract.md` | kontrakt wyniku | skill, validator, reviewer | TAK | semantics, path defense i blocker | literal pins, mutation matrix |
| `cases.json` | 12 golden cases | validator | TAK | doprecyzowanie ZCG-08 i ZCG-10 | schema, relations, READY controls |
| `validate.py` | publiczny static oracle | corpus, registry, reviewer | TAK | root cause leżał w brakującej relacji | public API replays, 207 mutations |
| raport remediation | trwały audyt | supervisor | TAK | cycle 4 musi zastąpić VOID cycle 3 | reread, diff, source facts |
| case schema | kształt case | strict validator | N-D | brak zmiany struktury case | meta-schema |
| corpus schema | inventory | strict validator | N-D | nadal dokładnie 12 cases | min/max 12 |
| registry schema | kontrakt registry | registry, validator | TAK | `candidate_effect_boundary` jest wymagane i zamknięte | strict schema, multi-entry |
| result schema | blocker enum | result, validator | TAK | nowy jawny blocker relacji | strict schema, derived disposition |
| product/runtime/flags/live | poza zakresem | procesy Ziomka | N-D | staged governance nie ma product consumera | exact pathset i brak importów produktu |

## Implementacja i obrona

- komponentowe containment odrzuca sibling-prefix zamiast ufać tekstowemu
  `startswith`;
- exact allowlista pochodzi z przypiętych artefaktów danego skilla, nie ze
  współdzielonego authoring scope;
- sprawdzany jest finalny plik i każdy ancestor, więc symlink oraz non-regular
  artifact failują przed READY;
- synthetic drugi wpis `ZIOMEK_FUTURE_SKILL` osiąga `READY_FOR_REVIEW` wyłącznie
  dla własnego rootu; ten sam plik przypisany do `ZIOMEK_CHANGE_GATE` failuje;
- `effect_boundary` wymaga zgodności list z `read_only_no_effect` także w
  mutacjach READY i HOLD;
- strict loader odrzuca duplicate keys oraz `NaN`, `Infinity` i `-Infinity`;
- centralne blockers/disposition, exact lane tuples, oracle allowlists,
  role-aware ACK i wszystkie `authority=false` pozostały źródłem prawdy.

## Goldeny i wyniki autora

Baseline przed fixem:

- official quick validator: rc=0;
- custom validator: rc=0, 12 cases, 163/163 mutations KILLED;
- public `selection.py` mislabeled staged: rc=0, SURVIVED — finding potwierdzony.

Po fixie:

- official quick validator: rc=0, `Skill is valid!`;
- custom validator: rc=0, status `validated_static_scope`;
- strict schemas: 4; strict JSON files: 6;
- canonical cases: 12/12;
- mutation matrix: 207/207 KILLED, 0 SURVIVED;
- legacy inclusion: wszystkie 163 historyczne etykiety zachowane; 44 nowe
  etykiety dają łącznie 207;
- exact pin posortowanej listy legacy 163:
  `0156c4559182e4186b53df92d8cd665fdb90f00769a15d2a4aaa98022cfa4a4b`;
- oddzielny od mutation helpera replay publicznym `validate_corpus_object()`:
  `dispatch_v2/core/selection.py` → KILLED z
  `CANDIDATE_WRITE_SET_OUTSIDE_REGISTRY_BOUNDARY`;
- oddzielny od mutation helpera replay publicznym `validate_corpus_object()`:
  `flags.json` → KILLED z tym samym blockerem;
- poprawny root bieżącego skilla → READY;
- drugi niezależny skill/root → READY dla własnego artefaktu, cross-skill →
  KILLED;
- sibling, Unicode/case, absolute, traversal, backslash, empty/dot, shared
  governance, symlink i non-regular → KILLED;
- prompt-visible `ROLE_ATTESTATION`: dokładnie 1 w 12/12; removal, duplication i
  downgrade → KILLED;
- AST/import: tylko `__future__`, `copy`, `hashlib`, `json`, `math`, `pathlib`,
  `re`, `stat`, `sys`, `tempfile`, `typing`, `unicodedata`; product imports 0;
- isolated `python -m py_compile`: rc=0; dedykowany temp cache usunięty;
- product pytest: N-D — staged-only, brak product path;
- runtime/health/PID/NRestarts/effective flags: N-D — jawnie zabronione w tym
  cyklu.

Pozytywne cases:

- ZCG-07 pozostaje bez zmian semantycznych: czysta analiza R0, empty boundary,
  `READY_FOR_IMPLEMENTATION`;
- ZCG-08 pozostaje `READY_FOR_REVIEW`; doprecyzowano, że jego dwa pliki są exact
  podzbiorem pinów tego samego staged rootu;
- ZCG-10 pozostaje `READY_FOR_IMPLEMENTATION`; jego jeden dokument reference
  jest przypiętym artefaktem tego skilla, a solver nadal ma dowodowe N-D.

Source pins zmieniono tylko dla zmienionych bajtów candidate package:

- `SKILL.md`: `7ac2f0502a8231926e826f27e81426a390d5fe60e53af64d0bd22e2915229991`;
- `gate-contract.md`: `6ef486ad8772739ff930be12537165aa579080300babcdbe3624579507d8dda1`;
- `openai.yaml`: bez zmiany,
  `d791a50a4ffcb7d2def662405ae30fbb452682c5cd272f82081a2e1c84c5d901`;
- `canonical-navigation.md`: bez zmiany,
  `725d579b3a4a4614456f95db49df7adbccda5cd984ed96127ff5b1aa3bb4c5e6`.

## Granice wydania i rollback

Cycle 4 zmienia wyłącznie osiem ścieżek governance w zamkniętym 12-path scope.
`openai.yaml`, navigation, case schema i corpus schema są dowodowym N-D. Pełny
original-base-to-target pathset pozostaje ograniczony do tych samych 12
kanonicznych ścieżek skill gate; nie ma ścieżki produktu.

Plan rollbacku:

1. przed integracją supervisor może odrzucić wyłącznie nowy izolowany
   worktree/branch i tag
   `ziomek-change-gate-remediation4-staged-20260716T173743Z`;
2. po hipotetycznej integracji wykonać jawny revert wyłącznie jednego top
   commita cycle 4;
3. zweryfikować powrót registry version, blocker enum, source pins i 12-path
   tree do exact input;
4. nie wykonywać restartu ani żadnej operacji live — rollback jest repo-only.

`candidate_commit` i `candidate_tree` celowo pozostają
`UNPINNED_UNTIL_INDEPENDENT_REVIEW`, aby raport autora nie tworzył
self-referential approval. Następny krok to świeży, supervisor-controlled
false-green audit exact commita, tree i lokalnego annotated tagu. Dopiero osobna
decyzja może rozważyć activation; ta sesja jej nie wykonuje.
