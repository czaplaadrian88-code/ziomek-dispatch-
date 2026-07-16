# Ziomek Change Gate — raport autora remediation

## Status i granica

- Rola: `INTERNAL_ONLY_NON_MAIN remediation author`.
- Status pakietu: `STAGED_ONLY_REVIEW_REQUIRED`.
- Dyspozycja autora: `REMEDIATION_AUTHOR_COMPLETE_STAGED_ONLY_REVIEW_REQUIRED`
  dopiero po finalnych kontrolach i lokalnym commicie; ten raport nie jest
  niezależnym review.
- Instalacja, activation, merge, rebase, push, owner contact i operacje live:
  none.
- Authority: network, production, deploy, restart, flag/data mutation,
  migration, lease, tmux, owner ACK i business semantics = `false`.

Pakiet istnieje wyłącznie pod
`docs/codex-skills/candidates/ziomek-change-gate/`. Nie istnieje pod
`.agents/skills/ziomek-change-gate`; przyszły move do tej ścieżki byłby
faktyczną activation i wymaga osobnego exact-byte gate. Pierwszy canary ma być
explicit-only.

## Pięciopunktowy brief prostym polskim

1. Problem i dowód: niezależne review odrzuciło poprzedni kandydat z pięcioma
   P1 i trzema P2; potwierdzone false-green obejmowały discovery, role/ACK,
   bootstrap, kontrakt wyniku i semantyczny validator.
2. Powierzchnie: tylko staged skill, registry, cztery schemas, syntetyczny
   corpus, offline validator i ten raport pod `docs/codex-skills/`.
3. Efekt: nowe sesje Codex nie odkryją pakietu automatycznie; kandydat zapisuje
   role-aware ACK fact, dokładny bootstrap i schema-driven wynik, lecz nie
   zmienia produktu ani runtime.
4. Ryzyka/testy/rollback: główne ryzyko to kolejny false-green; zabezpieczeniem
   są strict schemas, niezależne literalne piny, 12-case author oracle i 27
   mutation probes. Rollback to późniejszy revert exact lokalnego commita.
5. Bramki: nie potrzeba decyzji biznesowej, migracji, flagi, restartu ani
   deployu. Nadal wymagane są fresh independent review i osobna activation
   gate; non-MAIN przekazuje wynik aktywnemu MAIN-owi prywatnym handoffem.

## Pochodzenie i model

| Wejście | SHA-256 / wynik |
|---|---|
| remediation task | `65df62d1d2c835804e39c98af15a7bcefca1153a424211e5bd9fa821e8da0ee8` |
| original author task | `b1f3c67a9c0114f3b433498f65d2cd27d175747bb2baad077d7fd231fcf27112` |
| original author handoff | `9fb5e21105985ae0679bc0b9f5f8b8a8cdc575965763ba89cc42bc14c5bedf66` |
| independent review | `bf46475eedc3adb748f8bf7d78d0af40b852b01b2f547a6c559421db7df8abd7` |
| blind forward artifact | `42a733ffbf86e74ebfda992eb759cdc0d4194c02bcb39dfdcf93df3b54ef0582` |
| local Codex manual | `084f81886e62bd0d8eafdc9cbc0b297f026880dbd212bf55796759fe9115ccc9` |
| local model catalog | `1cf1b64a94a2ee56791c265baa227e045eeb40671a07cfbd1753d96a9fe83012` |

- `model_tier=sol`, exact `gpt-5.6-sol`, `effort=max`.
- Uzasadnienie: remediation R4 governance/authority ma wysoki koszt fałszywego
  PASS.
- Dowód dostępności: `codex-cli 0.144.5`; lokalny `models_cache.json` zawiera
  exact slug oraz wspierany effort `max`.
- Binding bieżącego procesu do sluga nie jest osobnym polem interfejsu; dowód
  nie jest rozszerzany ponad lokalną dostępność i exact task pin.

## Git i rejected input

- Worktree: `/root/ziomek_skill_gate_remediation_20260716T122436Z/dispatch_v2`.
- Branch: `codex/ziomek-skill-gate-remediation-20260716T122436Z`.
- Exact base/HEAD przed edycją:
  `6b4b040032d54db5be7643648676d835e0db9146`; status clean.
- Rejected commit:
  `f0947daff9b5544c0c5bf637a011a3cd0c128cd3`; parent jest exact base, tree
  `4d2c1779ea031c021b24e155b8c67c032af0b399`.
- Osiem rejected plików i ich diff odczytano przez obiekty Git; nie wykonano
  cherry-pick ani mechanicznego kopiowania do discovery path.
- Chroniony foreign dirty w głównym checkoutcie:
  `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`; sprawdzono wyłącznie
  status/stat, bez odczytu treści i bez modyfikacji.

## Wpływ wymaganych skills i manuala

- `skill-creator` wyznaczył minimalny układ `SKILL.md`, `agents/openai.yaml`,
  progresywne references oraz obowiązek `quick_validate.py`; nie użyto
  instalatora ani `init_skill.py`, bo exact staged lokalizacja była już podana.
- `openai-docs` użyto wyłącznie z lokalnym cache manuala, bez sieci. Manual
  potwierdza, że repo `.agents/skills` jest automatycznie skanowane od CWD do
  repo root, a `allow_implicit_invocation=false` wyłącza implicit matching,
  pozostawiając jawne `$skill`. Zweryfikowano stat regularnego pliku i odczytano
  tylko sekcje manuala `How Codex uses skills`, `Where to save skills` oraz
  invocation policy; nie rozszerzano lookupu.

## Mapa zamknięcia findings

| finding | root cause | naprawa | autorski dowód |
|---|---|---|---|
| P1-01 | candidate znajdował się w aktywnej ścieżce loadera | staged path poza `.agents`; registry rozdziela activation target; implicit=false | negative discovery check, exact YAML, symlink/mode/pathset checks |
| P1-02 | rola i ACK były zlane z capability | `UNATTESTED_NON_MAIN` default; MAIN/non-MAIN routing; osobne status/scope/reask | ZCG-09/11/12 i current/stale ACK mutations |
| P1-03 | bootstrap miał złą kolejność i względny CLAUDE | jawny blok: CLAUDE→5 map→memory→backlog→conditional handover→ADR→CODEMAP files | parser realnych Markdown targetów, reversed-order i broken-target mutations |
| P1-04 | wynik był luźnym przykładem | Draft 2020-12 result schema z zamkniętymi obiektami i pełnym required | wszystkie expected results + missing/extra/empty/type/count invariants |
| P1-05 | validator sprawdzał tokeny | literalne policy pins, relacje per case, wielojęzyczny policy scan i strict schema engine | pełna mutation matrix, w tym odwrócenia z zachowanym tokenem |
| P2-01 | registry nie opisywał Phase 0 boundary | strict registry schema: source, owner, trigger, allowed/forbidden, exact paths, threat i authority | missing/extra/empty/authority/bool mutations |
| P2-02 | brak briefu i entropy direction | pięć polskich pól, role-aware delivery, `NON_INCREASE` albo konkretne `N-D:` | result relations i 12 canonical expected results |
| P2-03 | poprzedni author użył xhigh | nowy author cycle ma `sol/max` z lokalnym dowodem | wersja CLI i exact model catalog pin |
| P3-01 | no-effect booleany były stałymi | validator raportuje tylko `validated_static_scope`; no-effect pochodzi z command logu i exact diff/pathset | brak hardcoded telemetry claims, zewnętrzne checks przed commitem |

## Schema, corpus i validator

- Cztery schemas używają Draft 2020-12, versioned `$id`, zamkniętych obiektów,
  pełnego `required`, typów, enumów, nonempty i lokalnych fail-closed `$ref`.
- Własny engine waliduje użyty podzbiór schematu oraz rozróżnia boolean od
  integer (`false` nie może być `0`). Każdy `$ref` musi trafić do exact
  czteroelementowej allowlisty; nie ma filesystem/network fallbacku.
- Registry schema dopuszcza wiele wpisów i nie ma `maxItems=1`; wspólny entry
  contract jest ogólny dla kolejnych staged skilli. Relacyjny validator wymaga
  unikalnych `name`, znajduje exact wpis `ziomek-change-gate` i tylko dla niego
  sprawdza candidate-specific 12 paths. Syntetyczny probe dwóch unikalnych
  wpisów przechodzi, a drugi różny wpis z powtórzoną nazwą jest odrzucany.
- Corpus ma 12 syntetycznych case’ów, w tym trzy jawne warianty roli:
  active MAIN/current ACK, non-MAIN/current ACK i brak atestacji roli.
- ZCG-09 zachowuje `CURRENT_EXACT_ACK`, exact scope i `requires_reask=false`;
  `HOLD` dotyczy tylko tej bramy przygotowania i nie odwołuje ani nie blokuje
  odrębnego autoryzowanego workflow.
- Corpus jest nazwany `AUTHOR_STATIC_ORACLE`; nie stanowi behavioral PASS ani
  independent review.
- Validator normalizuje polskie i angielskie warianty pozytywnych claimów/
  akcji, a structured forbidden policy codes są przypięte per case.
- Staged tree ma dokładnie cztery non-executable regular files, bez symlinków,
  scripts, dependencies, URL-i, komend sieciowych, instalacyjnych, systemd lub
  mutujących tmux. Registry ma niezależnie przypięte exact allowed/forbidden
  actions i wszystkie capability pozostają `false`.

Finalna macierz zabija 27 mutacji:

1. dopisana sprzeczna reguła SOFT-over-HARD przy zachowanym exact pozytywnym
   zdaniu;
2. dopisana sprzeczna reguła stale-ACK-valid przy zachowanej regule revoke;
3. dopisana sprzeczna reguła direct-owner-contact-non-MAIN przy zachowanym
   role-aware routing;
4. bootstrap reversed przy wszystkich tokenach;
5. broken Markdown target przy poprawnym tokenie w komentarzu;
6. missing/extra/empty registry field oraz duplicate skill name;
7. missing/extra/empty result field;
8. missing/extra/empty case field;
9. stale ACK oznaczony jako current;
10. current ACK rozszerzony do broad self-execute;
11. polski pozytywny action claim oraz angielski pozytywny
    capability/action claim;
12. authority=true w registry, result i case;
13. boolean jako integer w registry i result;
14. duplicate key w registry, result i case.

## Przykazanie numer zero — proporcjonalne ETAP 0–7

- ETAP 0: exact base/branch/worktree i dirty ownership potwierdzone. Ten task
  jawnie zabrania odczytów runtime, usług, flag, logów i danych, więc są N-D;
  ogólny skill nie żąda osobnej capability dla bezpiecznego read-only baseline
  już mieszczącego się w zleconym scope. Capability nadal pozostaje `false`.
- ETAP 1: root cause leży w discovery location i luźnych kontraktach, nie w
  produkcie.
- ETAP 2: semantyka produktu jest niezmieniona; skill utrwala HARD przed SOFT.
- ETAP 3: mapa P1-01..P3-01 powstała przed pierwszą edycją.
- ETAP 4: consumer chain to staged files→schemas→validator→future reviewer;
  Codex loader jest osobno uwzględniony i nie jest N-D.
- ETAP 5: product ON/OFF, replay i pełna regresja są N-D z exact boundary.
  Entropia także jest konkretnym `N-D`: docs-only staged diff nie zasila
  `tools/entropy_dashboard.py`, nie ma product consumera ani zmierzonego oracle
  kierunku, więc raport nie deklaruje spadku ani braku wzrostu.
- ETAP 6: wyłącznie lokalny staged candidate; zero install/activation/release.
- ETAP 7: source rollback to exact revert przyszłego lokalnego commita;
  niezależne review i activation pozostają niewykonane.

## Autorski command log i wyniki

Dozwolone odczyty/operacje lokalne:

1. `sha256sum` przypiętych wejść: 5/5 exact zgodnych, remediation task zgodny.
2. `codex --version`: `codex-cli 0.144.5`; lokalny catalog zawiera
   `gpt-5.6-sol` i `max`.
3. Git metadata: clean exact base, rejected parent/tree zgodne; foreign dirty
   sprawdzony tylko status/stat.
4. Pierwszy `quick_validate.py`: exit 0, `Skill is valid!`.
5. Pierwszy custom validator: fail-closed na brakującym literalnym
   `AUTHOR_STATIC_ORACLE` w `SKILL.md`; tekst poprawiono.
6. Po korekcie nadzorczej pierwszy probe sprzecznej polskiej reguły wykrył lukę
   normalizacji litery `ł`; normalizację poprawiono bez osłabiania policy.
7. Finalny custom validator: exit 0, `status=validated_static_scope`, 4 schemas,
   12 author-oracle cases, multi-entry registry probe true, policy languages
   `en`/`pl`, 27/27 mutations
   killed, activation target absent.
8. Final staged-byte gates: `quick_validate.py` exit 0; AST parse OK;
   `git diff --cached --check` exit 0; independent Git literal oracle
   `GIT_EXACT_12_PATHSET_OK`; wszystkie 12 plików mode `100644`, zero symlinków
   i executable; activation target oraz product references poza
   `docs/codex-skills/**` nieobecne.

Żadne pole validatora nie deklaruje historii sieci lub produkcji. Fakty
no-live/no-network wynikają z jawnego task boundary, listy wykonanych komend i
exact source diff; nie są telemetrycznym claimem skryptu.

## Product regression, loader i no-effect

- Pełna regresja produktu: `N-D`, ponieważ exact final diff jest ograniczony do
  `docs/codex-skills/**` i nie ma importera/consumera produktu. Jeśli finalny
  diff przekroczy tę granicę, N-D wygasa i wymagany jest kanoniczny full run.
- Solver, feasibility, scoring, selection, plan, product serializery, flagi,
  systemd, state, DB, logi, memory, backlog i handover: N-D przez exact pathset.
- Oficjalny Codex loader: nie N-D. Dowód naprawy to brak
  `.agents/skills/ziomek-change-gate`, staged package poza discovery path i
  exact `allow_implicit_invocation: false`.
- Sieć i operacje live: none. Nie czytano runtime, procesów, usług, flag, logów,
  DB, Docker, tmux ani PII.

## Rollback i pozostałe bramy

Rollback source-only: osobny revert exact lokalnego remediation commita po jego
utworzeniu. Brak flagi, danych, restartu, migracji i runtime rollbacku.

Pozostają wymagane:

1. supervisor-controlled independent review exact remediation commit/tree;
2. świeży blind forward bez expected leakage;
3. osobna integration decision;
4. osobny exact-byte move do `.agents/skills/ziomek-change-gate`, traktowany
   jako activation, z pierwszym canary explicit-only.

Ten raport nie wykonuje żadnej z tych bramek i nie jest self-review.
