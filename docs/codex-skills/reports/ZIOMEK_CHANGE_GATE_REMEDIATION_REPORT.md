# Ziomek Change Gate — remediation cycle 3

## Werdykt autora i granica authority

Status autorski: `READY_FOR_FRESH_INDEPENDENT_REVIEW`.

To jest wyłącznie `AUTHOR_ONLY_STATIC_SELF_CHECK_NON_INDEPENDENT`. Pakiet
pozostaje `STAGED_ONLY_REVIEW_REQUIRED`, poza discovery Codex. Nie został
zainstalowany, aktywowany, scalony ani wypchnięty. Żadna dyspozycja corpusu,
commit ani lokalny tag nie nadaje execution authority.

- rola: `INTERNAL_ONLY REMEDIATION_AUTHOR`, `ATTESTED_NON_MAIN`;
- owner channel: brak; routing wyłącznie do aktywnego MAIN-a/supervisora;
- `risk_class=R4`, `model_tier=sol`, exact model `gpt-5.6-sol`, `effort=max`;
- lokalna dostępność modelu i effort została potwierdzona w lokalnym katalogu
  Codex; nie użyto fallbacku ani sieci;
- network, production, deploy, restart, flags, dane, migracja, lease, tmux,
  owner ACK i business semantics: wszystkie `false`.

Product tests, runtime, flag fingerprint i entropy dashboard mają `N-D`, nie
`PASS`: staged docs/evals/schemas nie mają product runtime consumera.

## Przypięte wejście

| Fakt | Wartość |
|---|---|
| Task | `/tmp/ZIOMEK_SKILL_GATE_REMEDIATION3_TASK_20260716T152915Z.md` |
| Task SHA-256 | `55f8575fe43eabbfa951dbcd482c8cf1132e58d8d5a17b44eb075aa6f127a7e3` |
| Przeczytane linie | `300/300` |
| Branch | `codex/ziomek-skill-gate-remediation3-20260716T152915Z` |
| Worktree | `/root/ziomek_skill_gate_remediation3_20260716T152915Z/dispatch_v2` |
| Input commit | `a83ba55f463f7b38c5d4643d81e875d64d9f7444` |
| Input tree | `76b36d9c23cbefbf21e18bf81379b887d1542e58` |
| Original base | `6b4b040032d54db5be7643648676d835e0db9146` |
| Output commit/tree | `UNPINNED_UNTIL_FRESH_INDEPENDENT_REVIEW` |
| Rollback tag name | `ziomek-change-gate-remediation3-staged-20260716T152915Z` |

`candidate_commit` i `candidate_tree` pozostają celowo nieself-referential.
Exact output commit/tree/tag object/peel należy zapisać w zapieczętowanym
handoffie po commicie, nie w drzewie tego samego commitu.

## Dowód, że problem nadal istniał

Na niezmienionym input HEAD official quick validator i custom validator były
zielone (`104/104 KILLED`), lecz pięć publicznych mutacji nadal przechodziło:

1. `ANALYSIS_ONLY` z `production_operation=HOLD`;
2. `ANALYSIS_ONLY` z `activation=HOLD`;
3. pozytywna lane z `oracle=N-D`;
4. proza `N-D:` maskująca zapis do `flags.json` i zmianę runtime;
5. skoordynowane `minimum=false`, `minItems=false`, `total=0`, `entries=[]`.

To potwierdziło cztery root causes P1-A..D w relacjach walidatora. Zielony
baseline cycle 2 był false-green, a nie dowodem braku problemu.

## Root cause, naprawa i oracle

| Finding | Root cause | Naprawa u źródła | Publiczny negatywny oracle | Wynik |
|---|---|---|---|---|
| P1-A | READY sprawdzało tylko część gates | `READY_LANE_SPECS` wiąże exact tuple wszystkich czterech gates z jedną disposition | pełna macierz każdej alternatywnej wartości gates dla ZCG-07/08/10, w tym production HOLD i activation HOLD | KILLED |
| P1-B | `startswith("N-D:")` udawało granicę braku efektów | zamknięty `effect_boundary` z exact `write_set`, enum `mutation_surface` i boolean `read_only_no_effect`; READY analiz wymaga pustych list i `true` | product write, mutation surface, flags/runtime, erased/contradictory no-effect oraz proza N-D z prawdziwymi faktami strukturalnymi | KILLED |
| P1-C | brak closed oracle allowlist dla dodatnich lane'ów | każda dodatnia lane dopuszcza wyłącznie `AUTHOR_STATIC_ORACLE`; independent review jest osobnym procesem | N-D, MISSING, SELF_CONFIRMING i drift do INDEPENDENT na każdej z trzech lane'ów | KILLED |
| P1-D | Python przyjmował bool jako int w numeric schema keywords | pre-corpus meta-kontrakt typuje wszystkie wspierane keywords; bool jest wykluczony, unknown validation keyword odróżniony od jawnego `x-annotation-*` | 11 bool-as-number probes oraz skoordynowany schema+case attack | KILLED przed disposition |

Po naprawie niezależny od deklarowanej disposition repro pięciu wejściowych
ataków daje dokładnie `5/5 KILLED`.

## Zamknięte dodatnie lane'y

Kolejność tuple:
`independent_review/implementation/production_operation/activation`.

| Case/lane | Exact tuple | Oracle | Effect boundary | Disposition |
|---|---|---|---|---|
| ZCG-07 analysis | `NOT_REQUIRED/READY/N-D/N-D` | `AUTHOR_STATIC_ORACLE` | empty/empty/`true` | `READY_FOR_IMPLEMENTATION` |
| ZCG-08 author candidate | `PENDING/READY/N-D/REVIEW_REQUIRED` | `AUTHOR_STATIC_ORACLE` | staged write-set/`STAGED_ARTIFACTS`/`false` | `READY_FOR_REVIEW` |
| ZCG-10 local staged implementation | `NOT_REQUIRED/READY/N-D/REVIEW_REQUIRED` | `AUTHOR_STATIC_ORACLE` | staged write-set/`STAGED_ARTIFACTS`/`false` | `READY_FOR_IMPLEMENTATION` |

Te trzy wyniki pozostają pozytywne wyłącznie w lokalnej granicy. Wszystkie
pola `authority` są `false`; activation i live nadal są zabronione.

## Mapa kompletności

| miejsce | rola | writer/consumer | dotknięte TAK/N-D | powód | test |
|---|---|---|---|---|---|
| result schema | kontrakt wyniku | validator + 12 wyników | TAK | zamknięte effect facts i nowe blockery | schema + relacje |
| case/corpus schemas | kontrakt corpusu | cases + `$ref` resolver | TAK | exact jeden output i exact 12 cases | positive + mutations |
| registry schema | kontrakt rejestru | registry validator | TAK | pełna meta-kontrola bez zmiany publicznego kształtu | schema positive/numeric attacks |
| wszystkie 12 expected results | fixtures | central disposition | TAK | każdy ma strukturalny effect boundary | 12/12 green |
| centralized blockers/disposition | fail-closed oracle | wszystkie lane'y | TAK | brak deklaratywnego READY | blocker mutations |
| lane tuples | readiness classifier | ZCG-07/08/10 | TAK | exact cztery gates | pełna enum matrix |
| structural effect boundary | no-effect/write/live facts | wszystkie wyniki | TAK | proza nie jest źródłem prawdy | write/mutation/no-effect attacks |
| oracle allowlists | evidence classifier | trzy dodatnie lane'y | TAK | closed status per lane | cztery drifty × trzy lane'y |
| meta-schema validator | schema trust root | cztery schemas | TAK | bool nie jest liczbą | 11 keyword attacks + coordinated attack |
| author mutation matrix | negatywny oracle | custom validator | TAK | zachowanie 104 i nowe P1-A..D | 163/163 KILLED |
| positive cases | positive oracle | central classifier | TAK | zachować brak false-negative | ZCG-07/08/10 PASS |
| candidate SKILL | human routing contract | przyszły użytkownik | TAK | opis structural facts, tuples i oracle | quick validator + byte pin |
| `openai.yaml` | explicit trigger | Codex loader | N-D | semantyka i bajty bez zmian | exact pin; implicit false raz |
| canonical navigation | bootstrap | sesja używająca skilla | N-D | kolejność bez zmian | istniejąca pełna mutation matrix |
| gate contract | human/machine bridge | result author + reviewer | TAK | opis exact facts i lanes | semantic pins + byte pin |
| registry pins/rollback | staged provenance | reviewer + rollback | TAK | nowe SHA i tag cycle 3 | exact hash/tag checks |
| report | trwały dowód autora | fresh reviewer | TAK | cycle 3 i process HOLD | diff/review |
| discovery/Git boundary | activation i rollback | loader + Git | TAK | staged poza discovery; local tag po commit | absence, mode, peel, pathset |
| produkt/runtime/flags | system produkcyjny | usługi Ziomka | N-D | brak importu i product consumera | exact diff bez product paths |

## Exact repo pathsets

Cycle 3 zmienia dziewięć ścieżek:

1. `docs/codex-skills/ZIOMEK_SKILLS_REGISTRY.json`
2. `docs/codex-skills/candidates/ziomek-change-gate/SKILL.md`
3. `docs/codex-skills/candidates/ziomek-change-gate/references/gate-contract.md`
4. `docs/codex-skills/evals/ziomek-change-gate/cases.json`
5. `docs/codex-skills/evals/ziomek-change-gate/validate.py`
6. `docs/codex-skills/reports/ZIOMEK_CHANGE_GATE_REMEDIATION_REPORT.md`
7. `docs/codex-skills/schemas/ziomek-change-gate-case-v1.schema.json`
8. `docs/codex-skills/schemas/ziomek-change-gate-corpus-v1.schema.json`
9. `docs/codex-skills/schemas/ziomek-change-gate-result-v1.schema.json`

Original base→final tag musi pozostać dokładnie w dozwolonym 12-path scope;
dodatkowo obejmuje niezmienione w cycle 3, ale zmienione we wcześniejszych
commitach: `agents/openai.yaml`, `references/canonical-navigation.md` i
`ziomek-change-gate-registry-v1.schema.json`. Każda z 12 ścieżek ma być
regularnym niesymlinkowym plikiem `0644`; zero product paths i executables.

## Piny źródeł policy

| Źródło | SHA-256 |
|---|---|
| `SKILL.md` | `b14b59ce16e200390e564b826f3945b201d995d633d09f31a716cabb48e5f09c` |
| `agents/openai.yaml` | `d791a50a4ffcb7d2def662405ae30fbb452682c5cd272f82081a2e1c84c5d901` |
| `references/canonical-navigation.md` | `725d579b3a4a4614456f95db49df7adbccda5cd984ed96127ff5b1aa3bb4c5e6` |
| `references/gate-contract.md` | `d88d3d0cda3b19ae442510c4ad0d6f1f03606759b5d89906ec386c0c3afdb869` |

Zwykła mutacja treści bez aktualizacji pinu jest zabijana. Skoordynowana zmiana
treści i pinu nie jest kryptograficznie niemożliwa: tworzy nowe bytes/tree i
wymaga nowego fresh independent review exact commit/tree.

## Walidacja autora

| Kontrola | Wynik |
|---|---|
| Official skill-creator quick validator | rc 0, `Skill is valid!` |
| Custom offline validator | rc 0; 4 schemas; 6 strict JSON; 12 cases |
| Mutation matrix | `163/163 KILLED`, `0 SURVIVED`; 104/104 starych etykiet zachowane, 0 brakujących |
| Full positive gate matrix | 3/3 canonical PASS; wszystkie alternatywne wartości KILLED |
| Oracle allowlists | 3 positives; N-D/MISSING/SELF_CONFIRMING/INDEPENDENT drifts KILLED |
| Schema meta-contract | 4/4 positive; 11/11 bool keyword attacks KILLED |
| Coordinated schema+case | `minimum=false`, `minItems=false`, zero completeness KILLED przed disposition |
| Input false-green replay | 5/5 KILLED po naprawie |
| AST/import | PASS; standard library only; 0 product imports |
| Strict duplicate-key JSON | PASS dla registry, cases i 4 schemas |
| `python -m py_compile` | PASS dla validatora, bez repo `__pycache__` |
| `git diff --check` | PASS |
| Discovery | repo/user/admin/system/plugin cache ABSENT |
| Implicit invocation | dokładnie jeden `allow_implicit_invocation: false` |

Pełne finalne rc, modes, commit/tree/tag i handoff hash są zapisywane po
commicie w prywatnym handoffie. Ten raport nie udaje finalnego Git seala.

## Process HOLD poprzedniego review

Cycle-2 candidate pozostaje odrzucony. Ustalenia review były użyte wyłącznie
jako jawne diagnostyczne task input, nie jako pozytywne review ani immutable
evidence. Nie modyfikowano dwóch zastanych artefaktów w `/tmp`.

| Stan | Blind | Review |
|---|---|---|
| pierwotny seal | `6085518f47ba8e85b4a6fb600d14602968644148f2457259f6010667c828ab93`, 5528 B, 0600 | `bd4816e79b1acdc92259f3bdf910e8854cd3116c7388e36497dc3279e0c3a60c`, 28649 B, 0600 |
| po naruszeniu | `cfc567f665ab7a0dffe8953a3694da9f220ade249303d3efc1fcfa5a4385d9cd` | `19f57ccf163c8dac6b6cb656ded5dd74606e1a8959898699a578e9bef151200d` |

Procesowy status to `HOLD_REVIEW_INCOMPLETE`: zapieczętowany blind artifact
został zmodyfikowany po queued follow-up, a drugie safety menu zakończyło
review bez drugiego `Keep waiting`. Dlatego cycle 3 wymaga całkowicie świeżego,
supervisor-controlled independent review exact final bytes.

Safety-menu truth bieżącej sesji autora:

- menu widziane/atestowane przez supervisora:
  `PENDING_SUPERVISOR_ATTESTATION`;
- wybory `Keep waiting` wykonane przez supervisora:
  `PENDING_SUPERVISOR_ATTESTATION`;
- drugie menu:
  `PENDING_SUPERVISOR_ATTESTATION`.

Autor nie zgaduje tych liczników. Ewentualne uzupełnienie należy zapisać w
osobnym supervisor artifact, bez przepisywania seala autora lub handoffu.

## Discovery, brak live i rollback

Candidate nie istnieje w `.agents/skills`, `$HOME/.agents/skills`,
`/etc/codex/skills`, user/system bundle ani plugin cache. Candidate tree nie ma
symlinków ani executables. `allow_implicit_invocation: false` występuje
dokładnie raz. `candidate_commit/tree` pozostają placeholderem do fresh review.

Nie wykonano network, runtime/log access, product testów, deployu, restartu,
flipa, migracji, zmiany danych, tmuxa, lease'u, merge ani push. Nie edytowano
`ZIOMEK_BACKLOG.md`, repo memory, `todo_master.md`, `sprint_timeline.md` ani
innych wspólnych handoffów.

Rollback cycle 3:

1. przed integracją supervisor może usunąć prywatny branch/worktree/tag po
   potwierdzeniu braku integracji albo odtworzyć exact tag w nowym worktree;
2. po integracji wykonać jawny `git revert` wyłącznie nowego top commitu cycle
   3, bez cofania wcześniejszych commitów i bez live restartu;
3. zweryfikować powrót dziewięciu cycle-3 paths do input tree, zachowanie
   pozostałych trzech ścieżek kandydata oraz brak zmian produktu.

Oczekiwany newest-first łańcuch pięciu commitów ponad original base to:
`<OUTPUT_CYCLE3> → a83ba55 → c2dffdb → 6e55814 → 7daa5e6`, z base
`6b4b040`. Exact wartości finalne należą do handoffu i Git peela.
