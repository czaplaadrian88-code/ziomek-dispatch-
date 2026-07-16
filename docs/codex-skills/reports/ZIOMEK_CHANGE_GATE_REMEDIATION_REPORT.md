# Ziomek Change Gate — remediation cycle 7

Data autora: 2026-07-16 UTC

Status: `STAGED_ONLY_REVIEW_REQUIRED`

Wynik autora:
`AUTHOR_REMEDIATION7_COMPLETE_FRESH_INDEPENDENT_REVIEW_REQUIRED`

Klasa dowodu: `AUTHOR_ONLY_STATIC_SELF_CHECK_NON_INDEPENDENT`

Ten raport nie jest self-review, niezależnym review, authority ani zgodą na
aktywację. Kandydat pozostaje poza discovery. Nie wykonano instalacji,
aktywacji, merge, rebase, cherry-pick, push, deploy, restartu, flipa,
migracji, operacji danych/runtime, lease'u, routingu MAIN ani mutacji tmuxa.

## Tożsamość i exact input

- rola: `INTERNAL_ONLY_NON_MAIN`, bez owner channel i MAIN lease;
- model tier: `sol`;
- exact model: `gpt-5.6-sol`, dostępny w lokalnym katalogu modeli;
- effort: `max`;
- powód: R4 — publiczna granica schematów mogła wyprowadzić fałszywe READY;
- task: regular `0600`, 14976 bytes, SHA-256
  `d93bb47e4b362180ebc60fb93bfeaa77292a1e634263cdc87a411571e4735810`;
- branch: `codex/ziomek-skill-gate-remediation7-20260716T214230Z`;
- worktree:
  `/root/ziomek_skill_gate_remediation7_20260716T214230Z/dispatch_v2`;
- exact input commit: `2e0e9c79a99309004ae0161df6a7cbba751689d6`;
- input tree: `d10b2ed1e7b9dc54b852c88fa7ee142ba87a837c`;
- exact parent: `2f12fd4c7c2bb3a2ce351ad4c9139ef9ef512e90`;
- original base: `6b4b040032d54db5be7643648676d835e0db9146`;
- odrzucony tag cycle 6 pozostał bez zmian:
  `ziomek-change-gate-remediation6-staged-20260716T200118Z`.

## Potwierdzony false-green przed edycją

Literalny publiczny replay zmienił wyłącznie ZCG-08 przez dodanie
`ziomek_change_gate.authority_granted=true`. Słaby schema miał dokładnie
`{"type":"object"}`.

| publiczna ścieżka | wariant | wynik przed zmianą |
|---|---|---|
| `validate_corpus_object` | słaby corpus, trusted result | KILLED |
| `validate_corpus_object` | trusted corpus, słaby result | KILLED |
| `validate_corpus_object` | oba słabe | **SURVIVED**, rc=0 |
| `validate_cases_relations` | słaby result | **SURVIVED**, rc=0 |
| `validate_result_relations` | brak parametru schema | **SURVIVED**, rc=0 |
| publiczny ready helper | mutated result | **SURVIVED**, `READY_FOR_REVIEW` |
| publiczne blockery | mutated result | **SURVIVED**, `[]` |
| publiczna dyspozycja | mutated result | **SURVIVED**, `READY_FOR_REVIEW` |
| publiczna granica write-setu | mutated result | **SURVIVED**, `true` |
| `validate_registry_object` | słaby registry schema | KILLED, kontrola dodatnia |
| generic `validate_schema_instance` | słaby schema | SURVIVED zgodnie z kontraktem low-level |

Decydujący both-weak replay odtworzył review finding, więc nie wystąpił
`HOLD_INPUT_DRIFT`.

Root cause: registry miał własny trusted binding, lecz corpus i case relations
używały caller-controlled schema bez obowiązkowego porównania z committed
kontraktem. Publiczne readiness helpery zakładały, że ktoś wcześniej wykonał
schema validation. To była opcjonalna precondition, więc dodatkowe pole mogło
dojść do relacji i wyprowadzić READY.

## Fix źródłowy

Jedyny loader `load_trusted_schema_bundle()` ładuje przy każdym publicznym
wejściu wszystkie cztery committed schema. `TrustedSchemaBundle` jest frozen i
przechowuje wyłącznie kanoniczne strict-JSON strings oraz digest; nie istnieje
mutable cache. Każde wydanie dictu jest świeżym strict snapshotem.

`compatible_trusted_schema()` traktuje caller schema wyłącznie jako parametr
kompatybilności. `None` wybiera trusted schema. Jawny schema musi:

1. być obiektem reprezentowalnym jako strict JSON bez NaN/Infinity;
2. po canonical snapshot być semantycznie identyczny z trusted schema;
3. zostać odrzucony przy dowolnym osłabieniu lub rozszerzeniu;
4. nigdy nie być użyty do walidacji danych — używane są detached trusted bytes.

Granica obejmuje registry, corpus, pojedynczy case, dokument wyniku,
`validate_cases_relations`, `validate_result_relations`, wszystkie publiczne
blocker/disposition/ready/write-set helpery oraz ich warianty `with_context`.
Internal callers używają prywatnych funkcji z bundle/context przekazanym po
publicznym sprawdzeniu. Generic `validate_schema_instance()` zachował swój
niskopoziomowy kontrakt i nadal może świadomie walidować dowolny schema.

## Mapa kompletności

| miejsce | rola | writer/consumer | TAK/N-D | powód | test |
|---|---|---|---|---|---|
| cztery schema path/ID | committed trust roots | loader, refs, validator | TAK | jeden exact allowlist i fresh load | schema walk + bundle digest |
| `TrustedSchemaBundle` | immutable container | wszystkie publiczne entry pointy | TAK | canonical strings, bez mutable cache | local-dict mutation + fresh reload |
| canonical equality | caller compatibility | registry/corpus/case/result APIs | TAK | caller bytes nie są authority | weak/close/required/enum matrix |
| registry object | trust input | context constructor, registry API | TAK | wspólny loader zastąpił osobny tor | weak registry + multi-entry |
| corpus object | inventory 12 cases | corpus API, author validator | TAK | trusted corpus i result przed relacjami | weak-only/both-weak |
| pojedynczy case | direct public API | reviewer, corpus loop | TAK | brak bocznego wejścia poza corpus | 12/12 explicit i `None` |
| result object | direct public API | relations, caller | TAK | trusted result przed candidate data | authority extra + weak result |
| result/case relations | semantic oracle | corpus i direct callers | TAK | prywatne core po publicznym trust gate | weak result i direct attacks |
| public readiness helpers | READY/HOLD | blockery, dyspozycja, boundary | TAK | schema nie jest opcjonalną precondition | 10 direct public probes |
| internal callers | trusted propagation | context-aware helpers | TAK | brak ponownego caller schema | with-context attack matrix |
| verified readiness context | registry/package boundary | wszystkie READY lanes | TAK | cycle 6 pozostaje obowiązkowy | 29/29 wcześniejszych probes |
| artifact root i piny | package identity | context, write-set | TAK | bez zmiany semantyki root policy | exact/wrong/missing/symlink/FIFO/mode |
| blocker/disposition | closed decision | public relations | TAK | ZCG-07/08/10 bez driftu | 12/12 + trzy positive lanes |
| strict JSON | parser/schema boundary | loader i compatibility | TAK | duplicate/nonfinite nadal fail closed | wcześniejszy matrix KILLED |
| source mutant | causal oracle | corpus public API | TAK | usuwa dokładnie dwa trusted bindings | both-weak wtedy przeżywa, test failuje |
| `SKILL.md` | user contract | explicit invoker/reviewer | TAK | opisuje bundle i compatibility | quick validator + source pin |
| gate contract | result contract | skill/reviewer | TAK | opisuje detached trusted bytes | literal pins + source pin |
| registry version/pins | package identity | context/future promotion | TAK | version 0.7 i dwa nowe SHA | four-file byte verification |
| result/case/corpus/registry schema files | unchanged contracts | loader | N-D | root cause był w bindingu, nie schema bytes | exact HEAD parity + schema walk |
| `cases.json` | unchanged author corpus | corpus validator | N-D | ZCG-08 jest mutowany wyłącznie in-memory | exact HEAD parity + 12/12 |
| `openai.yaml` | discovery metadata | future loader | N-D | implicit=false już poprawne | SHA parity, dokładnie jeden false |
| canonical navigation | bootstrap order | skill/operator | N-D | luka nie dotyczy nawigacji | SHA parity + prior mutations |
| install/discovery/activation | future owner lane | Codex loader | N-D | staged path i jawny zakaz tasku | activation target absent |
| product/live/runtime/flags/data/services | Ziomek | produkcyjne procesy | N-D | governance-only i zakaz tasku | zero product paths/imports/live calls |
| lease/route/tmux/shared memory/backlog | MAIN governance | aktywny MAIN | N-D | internal-only, brak authority | zero shared edits; raport tylko do MAIN tmuxa |

## Dowody przyczynowe i mutacje

Po zmianie każdy wariant słabego schema jest deterministycznie odrzucony przed
użyciem danych kandydata. Usunięcie `additionalProperties:false`, usunięcie
required field, poszerzenie enum oraz słabe corpus/result/case/registry schema
są KILLED. ZCG-08 z `authority_granted=true` jest KILLED przez każdy publiczny
entry point. Exact committed schema jawnie i przez `None` dają byte/decision
parity. Mutacja caller dict po compatibility check nie wpływa na detached
trusted schema.

Kontrolowany source mutant zastępuje wyłącznie dwa bindings w
`validate_corpus_object` caller-controlled fallbackiem. Both-weak attack wtedy
ponownie przechodzi, a niezależny literal rejection oracle failuje. Mutant jest
więc przyczynowo związany z F-01, nie z ubocznym tokenem.

Tożsamości etykiet:

- legacy: 163/163 KILLED, SHA-256
  `0156c4559182e4186b53df92d8cd665fdb90f00769a15d2a4aaa98022cfa4a4b`;
- prior cycle 4: 207/207 KILLED, SHA-256
  `e4962df95146acb369863a553b325de0faf56dcdcd10815d98c3aa1af022346d`;
- cycle 6: 29/29 KILLED, SHA-256
  `b91f254eee7a479ff33796ae3bd1d3432fa9fb7fb90612f11db0ed6fb63b6f81`;
- pełny prior cycle 6: 236/236 KILLED, SHA-256
  `32f3334a6968ae063498856c31df9822a46a6a299b874d0990b8c7de875ea1fa`;
- nowe cycle 7: 28/28 KILLED, SHA-256
  `dc0b781ebc451bbd5edf55f627afb221bd6fc6b22093b1b3784474a1e443334e`;
- pełny zbiór: 264/264 KILLED, 0 SURVIVED, SHA-256
  `858c3419e6bda3504d1d251057e264d612e8cb10ee6056d875c64898c148532d`.

Trusted bundle SHA-256:
`cfed579f66622cf15378208f8ce8c952f90e8cf66412d6065f8e8190fc4c6e39`.

## Walidacja autora

- official skill quick validator: rc=0, `Skill is valid!`;
- custom validator: rc=0, `validated_static_scope`;
- schema files: 4; strict JSON files: 6; cases: 12;
- positive ZCG-07/ZCG-08/ZCG-10: bez zmiany decyzji;
- literal public matrix: wszystkie ataki KILLED, generic low-level control PASS;
- AST/import: tylko Python stdlib, dynamic import=0, product import=0;
- isolated `py_compile`: rc=0; prywatny pycache usunięty;
- `git diff --check`: rc=0;
- wszystkie pięć owned paths: regularne `100644`;
- product pytest, runtime, PID, NRestarts, health, flags i replay live: N-D —
  task jawnie zabrania i kod nie ma product consumera.

Source pins pakietu:

- `SKILL.md`:
  `5eb0a1d8f62db9a215746be84cb05c39fcf5e52abd79138f14d8692f2437576b`;
- `agents/openai.yaml` bez zmiany:
  `d791a50a4ffcb7d2def662405ae30fbb452682c5cd272f82081a2e1c84c5d901`;
- `references/canonical-navigation.md` bez zmiany:
  `725d579b3a4a4614456f95db49df7adbccda5cd984ed96127ff5b1aa3bb4c5e6`;
- `references/gate-contract.md`:
  `a2fbf795fec079f8a0cf7d85068e65824885d9f0f9639dfddfcae257714d88d3`.

`candidate_commit` i `candidate_tree` pozostają
`UNPINNED_UNTIL_INDEPENDENT_REVIEW`, bez self-referential approval. Exact seal
commit/tree/tag znajduje się dopiero w prywatnym handoffie po utworzeniu
postimage.

## Wydanie i rollback

Cycle 7 ma dokładnie pięć ścieżek i nie zawiera produktu. Nie wykonano żadnej
operacji live ani shared-state, więc nie istnieje live rollback.

Rollback przed integracją: uprawniony późniejszy lane odrzuca wyłącznie nowy
worktree/branch/tag cycle 7, zachowując wszystkie starsze refs. Po hipotetycznej
integracji rollback to jawny revert jednego commita cycle 7, następnie kontrola
version 0.7, dwóch source pins, bundle binding, 236 wcześniejszych etykiet oraz
powrotu tree do exact input. Bez restartu, flipa i migracji.

Następny krok: świeży, niezależny reviewer exact final bytes. Autor nie wydaje
niezależnego PASS i nie promuje kandydata.
