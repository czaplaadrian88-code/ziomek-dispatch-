# Ziomek Change Gate — raport remediation cycle 2

## Status i granica authority

- Rola wykonawcza: `INTERNAL_ONLY_NON_MAIN`; brak owner channel i brak kontaktu
  z właścicielem.
- Model runtime został jawnie atestowany przez launcher i pasek runtime jako
  `gpt-5.6-sol`; zapis: `model_tier=sol`, `effort=max`. Wcześniejsze `xhigh`
  było wyłącznie omyłką tekstową.
- Pakiet pozostaje `STAGED_ONLY_REVIEW_REQUIRED`, poza wszystkimi ścieżkami
  discovery Codex. Nie został zainstalowany ani aktywowany.
- Network, produkcja, deploy, restart, flag/data mutation, migracja, lease,
  tmux, owner ACK i business semantics pozostają `false`.
- Ten raport i walidator są dowodami autora. Nie są niezależnym review,
  behavioral PASS ani approval.

Zakres jest wyłącznie docs-only. Pełna regresja produktu, runtime, flagi i
entropy dashboard mają uczciwe `N-D`; loader/discovery został sprawdzony i nie
jest `N-D`.

## Wejścia przypięte przed edycją

| Artefakt | SHA-256 / fakt |
|---|---|
| Task cycle 2 | `7d46ff40748e7413b1e38a5a377adb814319055f8553da60e125751ff6f0bd00` |
| Independent review | `f8beb4d9d669314c6a9977df8ee96fbcdfdeb2a820b74b784d542068437acd75` |
| Blind forward | `0eea9098608f881f6fc922dd48f8623b7fc11f202096927cd2f3cd010e79046e` |
| Supervisor handoff | `a05497b1d3780606ce895a7bfba7bc7e1b028291019e22b560ed214d8d873f19` |
| Lokalny Codex manual | mode `0644`, 787455 B, SHA-256 `084f81886e62bd0d8eafdc9cbc0b297f026880dbd212bf55796759fe9115ccc9` |
| Exact input HEAD | `c2dffdb29a1d266ff513a679a3905b31a505e5dd` |
| Exact input tree | `e77b8217f00555c5b3a83f0c608b81b8000ca149` |
| Oryginalny base | `6b4b040032d54db5be7643648676d835e0db9146` |

Nie użyto sieciowego fallbacku manuala.

## Prawda procesowa poprzedniego cyklu

Poprzedni author/successor handoff nie powstał, ponieważ sesja została
zatrzymana na drugim safety menu. Nie wolno odsyłać integratora do tego
nieistniejącego artefaktu. Istnieje zweryfikowany supervisor handoff wskazany
powyżej.

Znany łańcuch przed cycle 2 jest dokładnie taki:

1. `7daa5e6f4c15019a113205361b5aa7b10896ded8`
2. `6e55814fa48d43185b116415e383cd6f69c681f9`
3. `c2dffdb29a1d266ff513a679a3905b31a505e5dd`

Kolejność powyżej jest oldest-first. Rollback po integracji zawsze wylicza
pełną listę newest-first z trwałego tagu; nie zakłada liczby commitów i nie
pomija finalnego commitu cycle 2.

## Zamknięcie ustaleń review

| Finding | Remediation i fail-closed dowód autora |
|---|---|
| P1-R1 | Registry zawiera zamknięty `policy_contract` z pięcioma stabilnymi kodami oraz SHA-256 czterech źródeł kandydata. Validator sprawdza dokładne bajty, nie znaczenie wywnioskowane z regexu. `allowed_output` i `required_concepts` mają dokładne mapowania per case. Osiem nowych parafraz, mutacja każdego artefaktu, dodatkowy output revoked ACK i erasure concepts są zabijane. Literalne zdania pozostają jedynie czytelnymi tripwire. |
| P1-R2 | `DYNAMIC / CODEMAP_SELECTED_TASK_FILES` jest elementem pełnego tuple `(position, class, id, target)` na pozycji 17. Missing, extra, double, luka numeracji, zły target, reverse i wszystkie 16 alternatywnych pozycji są zabijane. |
| P1-R3 | Jedna tabela `BLOCKER_RULES` wylicza dokładne `blocker_codes`; disposition jest wyprowadzana z tej tabeli. Siedem dokładnych false-READY review jest zabijanych, a ZCG-07, ZCG-08 i ZCG-10 pozostają pozytywne. Nieobsłużony stan daje `UNHANDLED_STATE_COMBINATION`. |
| P2-R1 | Dodano `skill_id`. Globalna unikalność obejmuje ID, nazwę, staged target, activation target oraz owned paths pod NFKC casefold; owned paths są porównywane również prefiksowo. Absolute, `..`, backslash i alias wspólnego targetu są odrzucane. Niezależny drugi wpis przechodzi. |
| P2-R2 | Rollback jest zakotwiczony w dokładnym lokalnym annotated tagu opisanym niżej. Raport nie próbuje self-hashować finalnego commitu. |
| P3-R1 | Wszystkie 12 promptów zawiera dokładnie jeden jawny `ROLE_ATTESTATION=...`; wynik roli musi się z nim zgadzać. Siedem wskazanych driftów prompt/oracle oraz podwójny fact są zabijane. |

## Exact write-set

Od base do finalnego endpointu dozwolone jest dokładnie 12 istniejących ścieżek:

1. `docs/codex-skills/ZIOMEK_SKILLS_REGISTRY.json`
2. `docs/codex-skills/candidates/ziomek-change-gate/SKILL.md`
3. `docs/codex-skills/candidates/ziomek-change-gate/agents/openai.yaml`
4. `docs/codex-skills/candidates/ziomek-change-gate/references/canonical-navigation.md`
5. `docs/codex-skills/candidates/ziomek-change-gate/references/gate-contract.md`
6. `docs/codex-skills/evals/ziomek-change-gate/cases.json`
7. `docs/codex-skills/evals/ziomek-change-gate/validate.py`
8. `docs/codex-skills/reports/ZIOMEK_CHANGE_GATE_REMEDIATION_REPORT.md`
9. `docs/codex-skills/schemas/ziomek-change-gate-case-v1.schema.json`
10. `docs/codex-skills/schemas/ziomek-change-gate-corpus-v1.schema.json`
11. `docs/codex-skills/schemas/ziomek-change-gate-registry-v1.schema.json`
12. `docs/codex-skills/schemas/ziomek-change-gate-result-v1.schema.json`

Każda jest regularnym, niesymlinkowym plikiem `0644`. Nie ma trzynastej
ścieżki, executabla ani importu produktu.

## Piny bajtów kandydata

| Źródło | SHA-256 |
|---|---|
| `SKILL.md` | `f2b0ffff4f03ffbf137edb8cab4712277ae62722c00b09d4a05b7d5846767aee` |
| `agents/openai.yaml` | `d791a50a4ffcb7d2def662405ae30fbb452682c5cd272f82081a2e1c84c5d901` |
| `references/canonical-navigation.md` | `725d579b3a4a4614456f95db49df7adbccda5cd984ed96127ff5b1aa3bb4c5e6` |
| `references/gate-contract.md` | `7cd58ff7277b8ff845de15e569929c90f54b65273b6cad429c33c4f39a54752c` |

Registry nie zawiera własnego hasha.

## Walidacja autora na finalnych working bytes

| Kontrola | Wynik |
|---|---|
| Official skill-creator quick validator | PASS, `Skill is valid!` |
| Custom offline validator | PASS, 4 schemas, 6 strict JSON, 12 cases, 104/104 mutation probes KILLED |
| Strict duplicate-key parse | PASS, 6/6 JSON, 0 duplikatów |
| AST compile/import | PASS, wyłącznie standard library, 0 product imports |
| Base→working inventory | PASS, dokładnie powyższe 12 ścieżek |
| Typ/mode | PASS, 12/12 regular non-symlink `0644`, non-executable |
| Discovery | PASS, target nie istnieje w repo/user/admin/system; implicit invocation exact `false` |
| Registry | PASS: multi-entry positive oraz ID/name/casefold/alias/target/path/prefix/unsafe-path negatives |
| Candidate byte pins | PASS: positive exact bytes; 4/4 single-artifact mutations KILLED |
| Governance paraphrases | PASS: 8/8 KILLED bez dopisywania ich do regexów |
| Dynamic sequence | PASS: 16/16 innych pozycji oraz missing/extra/double/gap/target/reverse negatives KILLED |
| Central READY matrix | PASS: 7/7 review negatives KILLED; 3/3 READY positives PASS |
| Corpus exact maps | PASS: extra revoked output i 12 concept erasures KILLED |
| Prompt↔role | PASS, 12/12 zgodne; siedem review driftów i double fact KILLED |
| `git diff --check` | PASS |

To jest `AUTHOR_ONLY_STATIC_SELF_CHECK_NON_INDEPENDENT`. Fresh independent
review dokładnego finalnego commitu i tree pozostaje obowiązkowe.

## Trwały rollback bez self-reference

Dokładny endpoint ma nazwę:

`ziomek-change-gate-remediation2-staged-20260716T140544Z`

Reguła jest niezmienna: nie powstaje preimage tag. Lokalny annotated tag jest
tworzony dopiero po zielonym finalnym commicie cycle 2 i wskazuje ten commit.
Raport nie zapisuje nieznanego sobie hash finalnego commitu; finalny handoff
zapisuje tag object ID, peeled commit i peeled tree.

Read-only kontrola przed utworzeniem tagu:

```text
git check-ref-format refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z
git show-ref --verify refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z
```

Druga komenda musi przed finalnym commitem potwierdzić brak refa. Po utworzeniu
tagu wymagane są read-only kontrole:

```text
git rev-parse refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z^{tag}
git rev-parse refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z^{commit}
git rev-parse refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z^{tree}
git merge-base --is-ancestor 6b4b040032d54db5be7643648676d835e0db9146 refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z^{commit}
git rev-list 6b4b040032d54db5be7643648676d835e0db9146..refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z
git diff --name-only 6b4b040032d54db5be7643648676d835e0db9146 refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z
```

`rev-list` bez `--reverse` daje wymagany porządek newest-first. Tag musi być
annotated: tag object ID istnieje i różni się od peeled commit. Peeled commit
musi równać się finalnemu HEAD, a peeled tree finalnemu tree. Ref istnieje tylko
w lokalnym namespace `refs/tags/`; sam commit, patch ani worktree nie przenosi
tego refa. Brak network/push jest osobno zapisywany w finalnym handoffie.

Przed integracją rollback oznacza nadzorowane usunięcie prywatnego tagu,
brancha i worktree po potwierdzeniu, że nie zostały zintegrowane. Po integracji
najpierw wylicz dokładną listę:

```text
git rev-list 6b4b040032d54db5be7643648676d835e0db9146..refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z
```

Następnie revertuj dokładnie tę newest-first listę, bez pomijania elementu:

```text
git revert --no-commit $(git rev-list 6b4b040032d54db5be7643648676d835e0db9146..refs/tags/ziomek-change-gate-remediation2-staged-20260716T140544Z)
```

Przed commitem rollbacku zweryfikuj, że wszystkie candidate paths znikają,
12-ścieżkowy zakres wraca do base, a product tree pozostaje niezmieniony. W tym
cyklu nie wykonuje się revertu.

## Handoff gate

Finalny lokalny commit i tag nie są merge/activation approval. Następny
dozwolony tor to supervisor-controlled fresh independent review dokładnego
commit/tree/tag i blind prompt review. Instalacja, move do discovery, merge,
push oraz jakakolwiek operacja live wymagają odrębnego jawnego zakresu.
