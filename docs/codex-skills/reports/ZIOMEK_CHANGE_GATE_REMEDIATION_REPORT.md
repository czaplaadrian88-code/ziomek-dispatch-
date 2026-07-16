# Ziomek Change Gate — raport autora remediation

## Status i granica

- Rola: świeży `INTERNAL_ONLY_NON_MAIN remediation successor author/reviewer`.
- Status pakietu: `STAGED_ONLY_REVIEW_REQUIRED`.
- Dyspozycja autora: `REMEDIATION_AUTHOR_COMPLETE_STAGED_ONLY_REVIEW_REQUIRED`
  dopiero po finalnych kontrolach i lokalnym follow-up commicie; ten raport,
  zastany commit poprzednika i self-check następcy nie są niezależnym review.
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
   są strict schemas, niezależne literalne piny, 12-case author oracle i 28
   mutation probes. Rollback to późniejszy revert exact lokalnego commita.
5. Bramki: nie potrzeba decyzji biznesowej, migracji, flagi, restartu ani
   deployu. Nadal wymagane są fresh independent review i osobna activation
   gate; non-MAIN przekazuje wynik aktywnemu MAIN-owi prywatnym handoffem.

## Pochodzenie i model

| Wejście | SHA-256 / wynik |
|---|---|
| successor task | `466ad4a065fdedf2c795e09450f5e9cda4ec9a0f36752ce93affb51ecad0d90e` |
| remediation task | `65df62d1d2c835804e39c98af15a7bcefca1153a424211e5bd9fa821e8da0ee8` |
| original author task | `b1f3c67a9c0114f3b433498f65d2cd27d175747bb2baad077d7fd231fcf27112` |
| original author handoff | `9fb5e21105985ae0679bc0b9f5f8b8a8cdc575965763ba89cc42bc14c5bedf66` |
| independent review | `bf46475eedc3adb748f8bf7d78d0af40b852b01b2f547a6c559421db7df8abd7` |
| blind forward artifact | `42a733ffbf86e74ebfda992eb759cdc0d4194c02bcb39dfdcf93df3b54ef0582` |
| local Codex manual | `084f81886e62bd0d8eafdc9cbc0b297f026880dbd212bf55796759fe9115ccc9` |
| local model catalog | `7f6194f5bf476ad3a111478a85d2ca573968ca73fb397eb4ef94d52bbb67cceb` |

- `model_tier=sol`, exact `gpt-5.6-sol`, `effort=max`.
- Uzasadnienie: remediation R4 governance/authority ma wysoki koszt fałszywego
  PASS.
- Dowód dostępności: `codex-cli 0.144.5`; lokalny `models_cache.json` zawiera
  exact slug oraz wspierany effort `max`.
- Binding bieżącego procesu do sluga nie jest osobnym polem interfejsu; dowód
  nie jest rozszerzany ponad lokalną dostępność i exact task pin.

## Git, late-commit anomaly i rejected input

- Worktree: `/root/ziomek_skill_gate_remediation_20260716T122436Z/dispatch_v2`.
- Branch: `codex/ziomek-skill-gate-remediation-20260716T122436Z`.
- Exact zadaniowy base:
  `6b4b040032d54db5be7643648676d835e0db9146`.
- Successor zastał clean HEAD
  `7daa5e6f4c15019a113205361b5aa7b10896ded8`, parent exact base, tree
  `555862fe948a9b65ae717105e0231d6161b6da65`. Commit powstał o
  `2026-07-16 13:10:40 UTC`, po utworzeniu successor tasku. Supervisor atestuje,
  że poprzednik był zamrożony przy drugim jawnym menu safety; dlatego ten późny
  commit zapisano jako anomalię i nie uznano go za review ani trusted input.
- Pierwszy recon potwierdził niezmienny commit/tree/parent, czysty status oraz
  brak aktywnego tool calla; procesy poprzednika czekały w stanie
  `futex_wait_queue`/`ep_poll`. Successor nie wznowił sesji, nie wysłał wejścia
  i nie mutował tmuxa. Supervisor następnie zakończył późną sesję; fresh
  precommit recon potwierdził brak tej sesji i procesów, więc successor jest
  jedynym writerem. Istniejące 12 plików ponownie zahashowano i sprawdzono.
- Rejected commit:
  `f0947daff9b5544c0c5bf637a011a3cd0c128cd3`; parent jest exact base, tree
  `4d2c1779ea031c021b24e155b8c67c032af0b399`.
- W poprzednim cyklu osiem rejected plików i ich diff odczytano przez obiekty
  Git. Successor ponownie przeczytał review, cały bieżący 12-plikowy pakiet i
  jego diff; nie wykonał cherry-pick ani kopiowania do discovery path.
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
| P1-02 | rola i ACK były zlane z capability | `UNATTESTED_NON_MAIN` default; MAIN/non-MAIN routing; osobne status/scope/reask; non-MAIN z current ACK musi trafić do active MAIN | ZCG-09/11/12, exact pozytywne piny i current/stale/route-bypass mutations |
| P1-03 | bootstrap miał złą kolejność i względny CLAUDE | jawny blok: CLAUDE→5 map→memory→backlog→conditional handover→ADR→CODEMAP files | parser realnych Markdown targetów, reversed-order i broken-target mutations |
| P1-04 | wynik był luźnym przykładem | Draft 2020-12 result schema z zamkniętymi obiektami i pełnym required | wszystkie expected results + missing/extra/empty/type/count invariants |
| P1-05 | validator sprawdzał tokeny | trzy pełne literalne policy sentences, relacje per case, wielojęzyczny policy scan i strict schema engine | pełna mutation matrix, w tym dopisane sprzeczności przy zachowanych exact pinach |
| P2-01 | registry nie opisywał Phase 0 boundary | strict registry schema: source, owner, trigger, allowed/forbidden, exact paths, threat i authority; read-only w explicit scope jest oddzielony od mutacji | missing/extra/empty/authority/bool mutations |
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
  semantycznie unikalnych, lowercase-ASCII `name`, znajduje exact jeden wpis
  `ziomek-change-gate` i tylko dla niego
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

Finalna macierz zabija 28 unikalnie nazwanych mutacji; count i pełna lista są
emitowane przez validator zamiast utrzymywania nieweryfikowanej stałej raportowej:

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
10. non-MAIN z bieżącym ACK omijający active MAIN i wskazujący bezpośrednio
    execution lane;
11. current ACK rozszerzony do broad self-execute;
12. polski pozytywny action claim oraz angielski pozytywny
    capability/action claim;
13. authority=true w registry, result i case;
14. boolean jako integer w registry i result;
15. duplicate key w registry, result i case.

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
- ETAP 7: rollback finalnego successor delta wskazuje exact commit z prywatnego
  handoffu. Usunięcie całego pakietu wymaga dodatkowo odwrócenia zastanego
  late-commit anomaly `7daa5e6`; niezależne review i activation pozostają
  niewykonane.

## Autorski command log i wyniki

Dozwolone odczyty/operacje lokalne:

1. `sha256sum` successor tasku: exact
   `466ad4a065fdedf2c795e09450f5e9cda4ec9a0f36752ce93affb51ecad0d90e`;
   wcześniejszy remediation task i independent review również zgodne.
2. Lokalny catalog zawiera dokładny `gpt-5.6-sol` oraz effort `max`; hash
   katalogu `7f6194f5…cceb`. Binding sesji nie jest nadmiernie deklarowany.
3. Git/tmux recon: branch poprawny; zastany HEAD `7daa5e6` zamiast oczekiwanego
   base; exact 12 plików 100644. Początkowo poprzednik był odłączony i idle;
   precommit recon potwierdził brak jego sesji i procesów, bez wznowienia lub
   mutacji tmux przez successora.
4. Baseline zastanych bajtów: `quick_validate.py` exit 0, `Skill is valid!`;
   custom validator exit 0, 12 case’ów, multi-entry=true i 27/27 killed.
5. Static successor review wykrył niespójny globalny zakaz production-read oraz
   niepełne exact pozytywne piny dwóch sprzeczności. Naprawiono rozdział
   safe-read/mutation, pełne piny i relację non-MAIN→active MAIN.
6. Po poprawce `quick_validate.py` exit 0; custom validator exit 0,
   `status=validated_static_scope`, 4 schemas, 6 strict JSON files,
   12 author-oracle cases, multi-entry=true, `en`/`pl`, 28/28 unikalnych
   mutations killed i activation target absent.
7. Niezależny strict parser: `STRICT_JSON_OK files=6`. Compile/import validatora:
   `VALIDATOR_COMPILE_IMPORT_OK product_imports=0`; importy są wyłącznie ze
   standard library. Multi-entry probe przechodzi, duplicate-name failuje
   zamknięcie z powodem `registry skill names must be unique`.
8. Exact inventory względem base zawiera 12 addytywnych ścieżek; wszystkie są
   regular 0644, zero symlinków i executable. Target skilla jest nieobecny w
   repo, `$HOME/.agents/skills` i `/etc/codex/skills`; implicit=false.
9. Author-only static self-check: PASS z jawnym
   `independent_oracle=false`, exact paths=12, cases=12, authority=false i
   docs-only entropy=`N-D`. To nie jest fresh blind forward ani review.
10. `git diff --check` dla successor delta oraz całego base→working tree: exit
    0. Finalny `git diff --cached --check` i ponowny quick/custom run są
    warunkiem commita; ich exact wynik oraz finalne hashe zapisuje prywatny
    handoff następcy.

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
- Sieć i operacje live: none. Nie czytano product runtime, usług, flag, logów,
  DB, Docker ani PII. Odczytano wyłącznie bezpieczne metadata Git/tmux/procesu
  (`comm`, state i wait channel) wymagane do ownership recon; nie czytano argv,
  environ ani treści chronionych carrierów i nie mutowano tmuxa.

## Rollback i pozostałe bramy

Rollback source-only successor delta: `git revert <successor_commit>` dopiero po
review i jawnej decyzji integratora. Powrót całego staged package do zadaniowego
base wymaga następnie `git revert 7daa5e6f4c15019a113205361b5aa7b10896ded8`;
nie wykonywać żadnego revertu teraz. Brak flagi, danych, restartu, migracji i
runtime rollbacku.

Pozostają wymagane:

1. supervisor-controlled independent review exact remediation commit/tree;
2. świeży blind forward bez expected leakage;
3. osobna integration decision;
4. osobny exact-byte move do `.agents/skills/ziomek-change-gate`, traktowany
   jako activation, z pierwszym canary explicit-only.

Ten raport nie wykonuje żadnej z tych bramek i nie jest self-review.
