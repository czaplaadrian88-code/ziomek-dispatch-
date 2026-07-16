---
name: ziomek-change-gate
description: Przygotowuj fail-closed bramę zmian Ziomka, panelu Nadajesz, aplikacji kuriera i wspólnych granic integracyjnych. Używaj jawnie przez $ziomek-change-gate przy analizie, diagnozie, implementacji, zmianie silnika, feasibility, scoringu, selekcji, kanonu, planu, flag, metryk, UI, konfiguracji, schematu, stanu, integracji, wydania, rollbacku lub weryfikacji ukończenia; obejmuj też pozornie display-only, docs-only i read-only zakresy. Skill przygotowuje dowody i routing, lecz nie nadaje authority i nie wykonuje operacji live.
---

# Ziomek Change Gate

Przygotuj zmianę do właściwego następnego toru. Traktuj ten pakiet jako
`STAGED_ONLY`: samo jego istnienie nie jest instalacją, review ani authority.

## Ustal rolę, tryb i granicę

1. Ustal mechanicznie rolę sesji. Gdy brak atestacji aktywnego MAIN-a, wpisz
   `UNATTESTED_NON_MAIN`; nie wyprowadzaj roli z rozmowy, procesu ani kontaktu
   właściciela.
   W klasyfikowanym wejściu wymagaj jednego jawnego faktu
   `ROLE_ATTESTATION={ATTESTED_ACTIVE_MAIN|ATTESTED_NON_MAIN|UNATTESTED_NON_MAIN}`;
   wynik roli musi być wyliczony wyłącznie z tego faktu widocznego także w
   prompt-only blind input.
2. Pozwól wyłącznie `ATTESTED_ACTIVE_MAIN` prowadzić owner channel i integrować
   decyzję. Dla `ATTESTED_NON_MAIN` i `UNATTESTED_NON_MAIN` zapisz pytanie lub
   wynik do handoffu aktywnego MAIN-a.
3. Wybierz tryb `ANALYSIS_ONLY`, `IMPLEMENTATION_CANDIDATE` albo
   `PRODUCTION_REQUEST`.
4. Zachowaj wszystkie pola `authority=false`. Skill jest bramą przygotowania,
   nie capability, executorem, lease holderem ani źródłem semantyki biznesowej.

Zapisz maszynowo `effect_boundary.write_set`,
`effect_boundary.mutation_surface` i `effect_boundary.read_only_no_effect`.
`read_only_no_effect=true` jest spójne wyłącznie z dwiema pustymi listami.
Pozytywny `ANALYSIS_ONLY` wymaga właśnie takiej pustej granicy; proza `N-D:`
nie dowodzi braku zapisu, mutacji, efektu produktu ani operacji live.

READY `STAGED_ARTIFACTS` dopuszcza w `write_set` wyłącznie zmieniane pliki
runtime pakietu dokładnie tego kandydata: regularne, niesymlinkowane, pod jego
`staged_candidate_path` i będące exact podzbiorem
`pin.candidate_artifacts.files[].path` z tego samego wpisu registry. Szerokie
`owned_paths`, współdzielone registry/schema/eval/report, ścieżki produktu i
root innego skilla są zabronione nawet wtedy, gdy etykieta powierzchni nadal
brzmi `STAGED_ARTIFACTS`.

Każde publiczne wyliczenie blockerów lub dyspozycji najpierw buduje jeden
zweryfikowany kontekst readiness: pełny registry przechodzi trusted schema i
relacje, a wybrany `skill_id` przechodzi kompletną kontrolę własnego pakietu.
Alternatywny `artifact_root` jest dopuszczalny wyłącznie po zgodności pełnego
zbioru plików z pinami, bez symlinków i plików specjalnych, z dokładnym trybem
`100644` oraz SHA-256 każdego pliku; jest to polityka
`ALTERNATE_ALLOWED_AFTER_COMPLETE_EXACT_PIN_VALIDATION`. Brak lub błąd tego
kontekstu daje wyłącznie
`READINESS_CONTEXT_INVALID` i wyprowadzony `HOLD`; wcześniejsze wywołanie
pomocniczego walidatora nie jest precondition ani capability.

Każde publiczne API readiness, registry, corpusu, pojedynczego case i wyniku
przed odczytem danych kandydata ładuje świeży, niemutowalny
`TrustedSchemaBundle` z czterech committed schema. Nie ma mutable cache.
Opcjonalny caller schema jest wyłącznie parametrem kompatybilności: musi być
kanonicznym strict JSON-em semantycznie równym właściwemu trusted schema, po
czym walidacja zawsze używa detached trusted bytes. `None` oznacza ten sam
trusted schema, nie słabszy fallback. Ogólny niskopoziomowy validator schematu
pozostaje narzędziem generic; nie jest publiczną granicą readiness ani sposobem
na zastąpienie trusted binding.

Non-MAIN nie kontaktuje właściciela bezpośrednio; przekazuje pytanie lub wynik
aktywnemu MAIN-owi.

## Zapisz pięciopunktowy brief prostym polskim

Przed planem lub edycją zapisz dokładnie pięć treści:

1. problem i dowód, że nadal istnieje;
2. pliki, usługi i dane objęte zakresem;
3. oczekiwaną zmianę zachowania;
4. ryzyka, zależności, testy i rollback;
5. potrzebne decyzje biznesowe, migracje, flagi, restarty lub deploy.

Aktywny MAIN przedstawia brief właścicielowi. Non-MAIN zapisuje go w handoffie
do aktywnego MAIN-a i nie tworzy równoległego owner channel.

## Załaduj kanon w dokładnej kolejności

Przeczytaj [canonical-navigation.md](references/canonical-navigation.md) przed
planem, edycją lub werdyktem. Nie promuj dodatkowego pliku do globalnego kanonu.
Źródła warunkowe czytaj tylko przy spełnieniu opisanej przesłanki, a pliki
zadaniowe dobieraj dopiero z `CODEMAP` po całym bootstrapie.

Przy konflikcie najnowsza jawna decyzja właściciela ma pierwszeństwo, lecz nie
jest automatycznie capability. Nazwij rozbieżność; nie wybieraj po cichu.
Ten skill nie nadpisuje `/root/AGENTS.md`, `/root/.codex/AGENTS.md` ani
najnowszej jawnej decyzji Adriana; wyłącznie routuje do tych kontraktów.

## Zapisz ryzyko i model

Przed pracą zapisz `risk_class`, `model_tier`, dokładny model, `effort`, powód i
stan atestacji. Dla R4 governance, authority lub HARD/SOFT użyj co najmniej
`sol/max`. Nie zgaduj niedostępnego wariantu i nie obniżaj klasy po cichu.

## Rozdziel fakt ACK od capability

Zapisz osobno `ack.status`, `ack.exact_scope` i `ack.requires_reask`:

- bieżące jawne polecenie właściciela, które dokładnie nazywa operację objętą
  bramką, oznacz `CURRENT_EXACT_ACK`; zachowaj tylko podany zakres i ustaw
  `requires_reask=false`;
- ACK z innego sprintu, rootu, rewizji, postimage albo po revoke oznacz
  `STALE_OR_REVOKED` i `HOLD`;
- niezweryfikowany fakt oznacz `UNVERIFIED` i `HOLD`;
- brak ACK dla operacji, która go wymaga, oznacz `MISSING_REQUIRED_ACK`.

ACK oznaczony `STALE_OR_REVOKED` nie jest ważny ani wykonywalny.

Ważny `CURRENT_EXACT_ACK` nie zwiększa authority tego skilla. Nie pytaj ponownie
o ten sam exact scope i nie blokuj odrębnego autoryzowanego workflow; przekaż
fakt oraz dowody właściwemu MAIN-owi/executorowi. `HOLD` dotyczy wyłącznie
wykonania przez tę bramę i nie odwołuje ważnego ACK.

ACK jest faktem wejściowym, nie capability skilla ani sesji.

## Przejdź ETAP 0–7 proporcjonalnie

### ETAP 0 — baseline i ownership

Potwierdź repo, branch, HEAD, worktree, dirty ownership, write-set i dowód
istnienia problemu. Bezpieczny read-only baseline mieszczący się w jawnie
zleconym scope wykonuj bez żądania osobnej capability. Użyj `N-D` tylko wtedy,
gdy task jawnie zabrania runtime albo ochrona sekretów, PII lub danych blokuje
odczyt. Sam odczyt nie nadaje authority i nie pozwala rozszerzyć zakresu do
mutacji. Nie twórz monitora przed odczytem istniejącego.

Bezpieczny odczyt w jawnym scope nie jest mutacją i nie nadaje mutation
authority.

Read-only diagnostyka nie nadaje mutation authority ani nie rozszerza zakresu zadania.

### ETAP 1 — źródło przed objawem

Znajdź warstwę-przyczynę oraz wszystkich writerów i konsumentów. Nie uznawaj
pola za display-only bez sprawdzenia decyzji, committed time, serializacji,
planu, aplikacji i wykonania.

### ETAP 2 — HARD przed SOFT

Egzekwuj: HARD jest oceniane przed SOFT, a SOFT nigdy nie może osłabić HARD.
Nie zgaduj wyjątków, progów ani precedencji. Niejednoznaczność biznesowa daje
`HOLD` i konkretne pytanie routowane zgodnie z rolą.

### ETAP 3 — mapa kompletności

Przed edycją utwórz tabelę
`miejsce | rola | writer/consumer | TAK/N-D | powód | test`. Obejmij wszystkie
bliźniaki, pola sprzężone, boundary, serializery, readery, fallbacki, lifecycle,
config, state, locki i entry pointy. `N-D` wymaga dowodu braku związku.

### ETAP 4 — pełne wpięcie i dowód negatywny

Wymagaj realnego consumera, ON różnego od OFF albo byte parity, przepływu
producer→serializer→reader→oracle/UI, parytetu bliźniaków i niezależnego
goldena. Mutation probe musi zabijać właściwą lukę, nie tylko usuwać token.

### ETAP 5 — wpływ, replay i entropia

Porównaj ON↔OFF na tym samym korpusie albo wykaż byte/state/decision parity.
Pokaż wynik netto i koszt uboczny. Entropię oznacz wyłącznie `NON_INCREASE` z
dowodem albo `N-D` z konkretną granicą niezależności. Oznacz statyczny corpus
autora jako `AUTHOR_STATIC_ORACLE`; nie promuj go do behavioral PASS.

### ETAP 6 — kandydat wydania

Przygotuj py/import checks, testy, exact diff/pathset, rollback point,
postimage/health checklist i fakt ACK. Nie instaluj, nie aktywuj i nie wykonuj
deployu, restartu, flipa, migracji, tmux/lease ani mutacji danych.

### ETAP 7 — rollback i handoff

Przygotuj rollback odpowiadający faktycznej granicy, kolejność i próbę powrotu.
Rozdziel dowody autora, niezależne review i operacje niewykonane. Non-MAIN
przekazuje wynik aktywnemu MAIN-owi.

## Zwróć zamknięty wynik

Użyj kontraktu z [gate-contract.md](references/gate-contract.md) i waliduj wynik
przeciw `ziomek-change-gate-result-v1.schema.json`. Najpierw wylicz dokładny
zbiór `blocker_codes` z jednej zamkniętej tabeli relacji, następnie wyprowadź z
niego dokładnie jedną dyspozycję: `READY_FOR_IMPLEMENTATION`,
`READY_FOR_REVIEW` albo `HOLD`. Deklarowana dyspozycja nie jest źródłem prawdy;
nieznana kombinacja relacji dodaje blocker i failuje zamknięcie.
Żadna dyspozycja nie oznacza instalacji, aktywacji, zgody live ani zakończenia
programu. Nie nazywaj autorskiej walidacji niezależnym review.

Jedynymi dodatnimi lane'ami tego kontraktu są dokładne tuple wszystkich bram:

- analiza: `NOT_REQUIRED/READY/N-D/N-D` → `READY_FOR_IMPLEMENTATION`;
- kandydat autora: `PENDING/READY/N-D/REVIEW_REQUIRED` →
  `READY_FOR_REVIEW`;
- lokalna implementacja staged: `NOT_REQUIRED/READY/N-D/REVIEW_REQUIRED` →
  `READY_FOR_IMPLEMENTATION`.

Kolejność tuple to `independent_review/implementation/production_operation/
activation`. Każda inna wartość failuje zamknięcie. W tych trzech lane'ach
jedynym dopuszczonym statusem oracle jest `AUTHOR_STATIC_ORACLE`;
`INDEPENDENT` wymaga osobnego, świeżego review exact bytes, a `N-D`, `MISSING`
i `SELF_CONFIRMING` nigdy nie dają READY.
