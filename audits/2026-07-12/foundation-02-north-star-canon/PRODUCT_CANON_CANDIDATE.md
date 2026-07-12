# PRODUCT CANON CANDIDATE — NOT YET AUTHORITATIVE

Ten dokument jest kandydatem do review właściciela produktu. Nie aktualizuje `memory:ZIOMEK_REGULY_KANON.md`, nie zmienia kodu ani runtime i nie nadaje nowych uprawnień. Statusy i pełne provenance są w `CANON_CLAIMS_LEDGER.jsonl`; definicje w `SEMANTIC_GLOSSARY.md`.

## 1. Cel i zakres produktu

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Ziomek ma przejąć rutynowe decyzje dyspozytorskie i docelowo podejmować je samodzielnie lepiej od człowieka. | `CANON_CONFIRMED`; `NS-002`, `NS-003` | `memory:ZIOMEK_REGULY_KANON.md@ca55742b`; historycznie `project_overview.md@ca55742b` | Ryzykowne klasy pozostają human-gated do jawnego awansu. | Nie zdefiniowano jeszcze execution boundary dla ALERT/przerzutów (`UNK-006`). |
| Autonomia ma rosnąć bez utraty jakości, wiedzy i wcześniej naprawionych inwariantów. | `OWNER_CONFIRMED`; `NS-001`, `NS-004`, `NS-005` | bieżący Prompt 02; kanon i priorytety pamięci | Brak. | Konkretne KPI autonomii pozostają niezwiązane. |

## 2. Aktorzy i odpowiedzialności

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Właściciel produktu ustala semantykę, HARD/SOFT, wyjątki i awans autonomii; nowsza jawna decyzja nadpisuje starszą. | `OWNER_CONFIRMED`; `NS-006` | bieżący Prompt 02; kanon pamięci | Techniczne fakty rozstrzyga się z kodu/runtime. | Brak formalnego aktu promocji trwałej korekty (`UNK-004`). |
| Operator obecnie zatwierdza przerzut przed wykonaniem, a przerzut ma mieć operacyjne uzasadnienie. | `CANON_CONFIRMED`; `NS-007`, `BR-012` | kanon pamięci | Jawnie awansowana klasa może kiedyś nie wymagać approval. | Macierz klas nie jest jeszcze zatwierdzona (`UNK-006`). |
| Klik operatora jest feedbackiem o decyzji człowieka, nie ground truth wyniku. | `OWNER_CONFIRMED`; `GT-001` | bieżący Prompt 02; lessons/ETA contract | Może kotwiczyć assignment, gdy kontrakt to potwierdza. | Nie zastępuje późniejszego outcome. |
| Codex rozstrzyga technikę w granicach kanonu i nie wyprowadza intencji z samego kodu. | `OWNER_CONFIRMED`; `NS-008` | bieżący Prompt 02 | Brak. | Przyszłe karty autonomii nie powstały w tym etapie. |

## 3. Definicje

Obowiązuje rozdzielenie terminów w `SEMANTIC_GLOSSARY.md`, szczególnie:

- `declared time` ≠ `committed pickup` ≠ `predicted pickup` ≠ `physical pickup`;
- `restaurant arrival` ≠ `restaurant last-inside` ≠ `geofence exit`;
- `delivery arrival` ≠ `customer handoff` ≠ `delivery click`;
- `courier_capacity_class` ≠ `late_pickup_risk_level` ≠ `escalation_level` ≠ `alarm_mode`;
- `proposal` ≠ `assignment` ≠ `execution`.

Status: `CANON_CONFIRMED` dla zakazu mieszania semantyk ETA/commit (`GT-008`), `IMPLEMENTED_ONLY` dla dokładnych nazw observable kontraktu ETA v1 (`GT-003`, `GT-005`), `UNKNOWN` dla docelowych eventów KPI (`UNK-001`, `UNK-002`). Źródła: `ZIOMEK_INVARIANTS.md@c7de9f2`, `docs/eta/06_ground_truth_contract.md@c7de9f2`, bieżący Prompt 02.

## 4. Hierarchia celów

| Priorytet | Reguła | Status / claim | Źródło | Znana luka |
|---|---|---|---|---|
| 1 | Prawdziwe HARD i uczciwe deklaracje przed każdą optymalizacją. | `OWNER_CONFIRMED`; `BR-001`, `BR-004` | Prompt 02; ADR-001; kanon pamięci | R-DECLARED jest w baseline tylko częściowo egzekwowane (`IMP-006`). |
| 2 | Jakość i stabilność przed tempem autonomii. | `CANON_CONFIRMED`; `NS-004`, `NS-005` | kanon/priorytety pamięci | Brak liczbowego KPI jakości physical. |
| 3 | Normalna wykonalność przed least-damage; least-damage ma pozostać jawnie `NO/ALERT`. | `OWNER_CONFIRMED`; `BR-008` | Prompt 02; kanon; ADR-003 | Execution boundary jest osobną decyzją (`UNK-006`). |
| 4 | SOFT optymalizuje wynik planu/systemu, nie lokalny score. | `CANON_CONFIRMED`; `BR-001`, `BR-010`, `BR-011`, `BR-019` | kanon pamięci | Brak jednego kompletnego least-damage contract w baseline (`IMP-011`). |

## 5. Twarde inwarianty

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| HARD jest przed SOFT i nie może zostać odwrócone przez scoring. | `OWNER_CONFIRMED`; `BR-001` | Prompt 02; ADR-001; architektura | Least-damage jest pokazywane jako niewykonalne, nie legalizowane. | Latentny `NO→MAYBE` re-admit (`IMP-002`). |
| Zadeklarowany czas jest HARD i nie może być fałszowany. | `OWNER_CONFIRMED`; `BR-004` | Prompt 02; kanon pamięci | Uczciwe przesunięcie propozycji, nie zmiana deklaracji. | Tripwire jest obserwacyjny (`IMP-006`). |
| Polityka liczbowa R6 to 35 normalnie i 40 wyłącznie w Alarmie; żadna klasa kuriera nie otrzymuje wyjątku. | `OWNER_CONFIRMED`; `BR-002`, `BR-003` | Prompt 02; kanon; invariants | Paczki termicznie wyłączone. | Start/end interwału R6 pozostają conflicted (`CF-009`, `UNK-007`–`UNK-009`); baseline nie implementuje kompletnego Alarmu. |

## 6. Reguły wykonalności

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Feasibility jest oceną nazwanych HARD w danym trybie; `NO` nie staje się `MAYBE` przez selekcję. | `OWNER_CONFIRMED`; `BR-001` | Prompt 02; ADR-001 | Jawny Alarm ma osobny zatwierdzony kontrakt. | Baseline ma log-only guard i latentny re-admit. |
| Brak GPS/pre-shift nie jest sam w sobie ukrytą karą; realny brak możliwości dojazdu jest osobnym HARD. | `OWNER_CONFIRMED`; `BR-007` | Prompt 02; kanon pamięci | Prawdziwa niewykonalność fizyczna. | Pre-shift FAR pozostaje score-veto (`IMP-010`). |
| Paczka nie podlega termicznej R6. | `CANON_CONFIRMED`; `BR-014` | kanon pamięci | Wyłącznie termiczny charakter R6. | Efektywną flagę trzeba później zaatestować. |
| Exemption R6 nie znosi declared/committed, jeśli zobowiązanie istnieje. | `STRONG_CANON_CANDIDATE`; `BR-018` | kanon pamięci + baseline code | Brak. | Kanon nie wypowiada tej relacji jednym zdaniem. |

## 7. Wybór i kolejność

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Score służy do porównania wewnątrz właściwej warstwy, nie do obchodzenia feasibility. | `OWNER_CONFIRMED`; `BR-001` | Prompt 02; ADR-001 | Brak. | Wagi są implemented-only i nie należą do kanonu. |
| R1 delivery spread i R8 pickup span są SOFT, nie HARD. | `CANON_CONFIRMED`; `BR-015` | kanon pamięci; baseline code | Mogą zmieniać ranking wewnątrz właściwej warstwy. | Progi i wagi pozostają implemented-only. |
| Wybór optymalizuje flotę, nie pojedyncze zamówienie w izolacji. | `CANON_CONFIRMED`; `BR-019` | kanon pamięci | Nie kanonizuje konkretnej funkcji kosztu. | Globalny objective nie ma jednego formalnego KPI. |
| Niesione jedzenie ma pierwszeństwo, dopóki świeżość pozostaje wygrywalna. | `CANON_CONFIRMED`; `BR-010` | kanon pamięci | Guarded po-drodze relax, jeśli nie pogarsza chronionych celów. | Rozproszone bliźniaki planu (`IMP-011`). |
| Nie wracaj do opuszczonej restauracji po odebraniu z niej jedzenia. | `CANON_CONFIRMED`; `BR-020` | werdykt D4 z 29.06 | Brak. | Pełność implementacji bliźniaków nie była testowana w Prompt 02. |
| W już spóźnionym worku wybiera się least-damage z ochroną commitment, jazdy i świeżości. | `CANON_CONFIRMED`; `BR-011` | kanon pamięci | Nadal obowiązuje alarmowy sufit 40. | Alarmowe R27 i pełny switch są otwarte. |

## 8. Trasy i ETA

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Plan ma obejmować pełną sekwencję i konsekwencje dla worka, nie tylko odległość do nowego pickup. | `STRONG_CANON_CANDIDATE`; `BR-010`, `BR-011` | kanon pamięci; architektura | Brak. | Nie ustanawia jednego algorytmu solvera. |
| ETA zawsze nazywa event, prediction anchor i wersję. | `CANON_CONFIRMED`; `GT-008` | invariants; ETA contract | Brak. | Eventy physical KPI są unbound. |
| Nazwa R6 bez jawnego `<start>→<end>` jest semantycznie niekompletna. | `CONFLICTED`; `CF-009`, `UNK-007`–`UNK-009` | kanon; change protocol; ETA contract | Progi 35/40/no-class pozostają potwierdzone. | Wymaga OD-07; baseline hybrid anchors są tylko `IMPLEMENTED_ONLY` (`IMP-020`–`IMP-022`). |
| Obecny deterministyczny symulator jest faktem implementacji, nie produktem docelowym ani kanonem modelu. | `IMPLEMENTED_ONLY`; `IMP-014` | kod/docs ETA na c7 | Może być zastąpiony po dowodzie bez zmiany reguł produktu. | Brak. |

## 9. Declared i committed semantics

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Declared time jest prawdą HARD. | `OWNER_CONFIRMED`; `BR-004` | Prompt 02; kanon | Brak. | Enforcement częściowy. |
| Committed pickup po assignment nie jest cicho nadpisywany. | `OWNER_CONFIRMED`; `BR-005` | Prompt 02; kanon | Jawna korekta z provenance może utworzyć nowe zobowiązanie. | Akt takiej korekty musi być jednoznaczny. |
| Normalne ±5 committed pickup jest SOFT. | `OWNER_CONFIRMED`; `BR-006` | Prompt 02; kanon | Brak. | Znaczenie Alarmowego ±10 wobec nietykalnego commit jest conflicted (`CF-002`, `UNK-003`). |

## 10. Alarm i least-damage

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Alarm włącza Ziomek automatycznie per decyzja po niewykonalności Strategii 1 i 2; R6=40 dla wszystkich. | `CANON_CONFIRMED`; `BR-016` | kanon pamięci | Następna decyzja jest oceniana od nowa. | Baseline ma globalny zamiast per-decision FSM (`IMP-019`) i niepełny predicate; R27 precedence to `UNK-003`. |
| Least-damage pozostaje jawnie niewykonalne i musi pokazać szkodę/powód. | `CANON_CONFIRMED`; `BR-008` | kanon; ADR-003; Prompt 02 | Execute może wymagać człowieka. | `UNK-006`. |
| `ALERT` oznacza jawny problem wymagający działania, nie klasę kuriera ani samą flagę. | `CANON_CONFIRMED`; `NS-009`, `BR-008` | feedback/kanon pamięci | Brak. | Docelowa akcja execute/approve otwarta. |

## 11. Always-propose

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Istniejąca flota prowadzi do feasible albo least-damage/ALERT. | `OWNER_CONFIRMED`; `BR-008` | Prompt 02; kanon; ADR-003 | Jawny time hold i fizyczny brak floty (`BR-009`). | Baseline ma `no_solo_candidates` i nieosłonięte gates (`IMP-009`, `IMP-016`). |
| Techniczna nieocenialność musi być jawnie nazwana, nie automatycznie rozszerzać wyjątek „zero fleet”. | `PROPOSED_SYNTHESIS`; `SYN-008` | kanon + ADR-003 + code drift | Brak zatwierdzonej taksonomii. | Wymaga review właściciela wraz z kartami fallback. |

## 12. Dane i ground truth

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Twierdzenie o zdarzeniu nie jest szersze niż zakres sensora/procesu. | `PROPOSED_SYNTHESIS`; `SYN-006` | Prompt 02; ETA contract | Exact v1 observable definitions pozostają `IMPLEMENTED_ONLY`. | Docelowy KPI nadal unbound. |
| Pickup/delivery click pozostaje proxy, nie physical event. | `OWNER_CONFIRMED`; `GT-010` | Prompt 02; ETA contract | Może kotwiczyć status lub kohortę. | `F7AGREE`/anchor lineage ma lukę `IMP-018`. |
| KPI raportuje coverage/as_of/missingness i fail-closed przy niewystarczającym support. | `PROPOSED_SYNTHESIS`; `SYN-005` | ETA contract; Prompt 02 | Brak. | Minimalne coverage jest decyzją (`UNK-005`). |

## 13. Operator feedback

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Agree/override zapisuje decyzję człowieka, nie ocenę prawdy wyniku. | `OWNER_CONFIRMED`; `GT-001` | Prompt 02; lessons/ETA contract | Brak. | Ogólny reason/scope feedbacku pozostaje kandydatem, nie potwierdzoną regułą. |
| Pojedyncza korekta pozostaje case-level. | `OWNER_CONFIRMED`; `LRN-002` | Prompt 02 | Jawna decyzja właściciela może od razu ustanowić regułę. | Jak rozpoznać ten akt: `UNK-004`. |

## 14. Uczenie i kalibracja

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Uczenie ma optymalizować zweryfikowany outcome, nie imitację operatora. | `CANON_CONFIRMED`; `LRN-001` | lessons + Prompt 02 | Operator feedback może generować hipotezę. | Physical KPI unbound. |
| Zmiana modelu/reguły przechodzi ON/OFF, oracle, obserwację i rollback. | `CANON_CONFIRMED`; `LRN-003` | change protocol; DoD | Refaktor wymaga parytetu zamiast poprawy outcome. | Brak. |
| Istniejące modele/kalibratory shadow nie stanowią dowodu continuous autonomous learning. | `IMPLEMENTED_ONLY`; `LRN-004` | branch refactor analysis; code/reference | Brak. | Brak zatwierdzonego auto-promotion contract. |

## 15. Niepewność i eskalacja

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| System komunikuje brak danych, status feasibility, ograniczenie sensora i ryzyko. | `PROPOSED_SYNTHESIS`; `SYN-006`, `NS-009` | feedback; ETA contract | Brak. | Wspólny schema reason/uncertainty nie jest tu zatwierdzany. |
| Eskalacja nie jest cichym brakiem propozycji. | `CANON_CONFIRMED`; `BR-008` | Always-propose canon | Hold/zero fleet pozostają jawne. | Baseline częściowy. |

## 16. Audytowalność decyzji

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Decyzja i późniejszy outcome zachowują rozdzielne, wersjonowane lineage wejść, reguł, feasibility, planu i powodu. | `STRONG_CANON_CANDIDATE`; `ARCH-007` | architecture; change protocol; ETA contract | Dane mogą być pseudonimizowane. | Pełność implementacji nie jest twierdzeniem kanonu. |
| Fakt runtime musi mieć procesowy fingerprint i czas; registry lub defaults nie wystarczają. | `CANON_CONFIRMED`; `ARCH-004` | Prompt 02; ADR-004; Prompt 01 | Brak. | Prompt 01 atestował tylko wybrane flagi. |

## 17. Prywatność i bezpieczeństwo

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Product-wide minimalizacja danych dla artefaktów decyzji/uczenia jest kandydatem rozszerzającym sprawdzony zakres ETA v1. | `PROPOSED_SYNTHESIS`; `NS-010` | ETA contract privacy; granice Promptu 02 | Ściśle kontrolowane runtime operacyjne ma osobny zakres. | Brak kompletnej product-wide polityki retencji. |
| KPI z niewersjonowanego źródła nie udaje odtwarzalnego historycznego replay. | `PROPOSED_SYNTHESIS`; `SYN-005` | ETA contract | Jawny fail-loud zamiast fałszywego wyniku. | Zakres poza KPI wymaga osobnego kontraktu. |

## 18. Granice autonomii

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| Uprawnienie do rekomendacji i wykonania są rozdzielne. | `PROPOSED_SYNTHESIS`; `SYN-002` | canon/code/runtime | Jawnie awansowana klasa może mieć oba. | `UNK-006`. |
| Kod, flaga lub dobry replay nie nadają same execution authority. | `OWNER_CONFIRMED`; `NS-006`, `LRN-003`, `IMP-013` | Prompt 02; change protocol | Brak. | Brak. |
| `PROPOSED_SYNTHESIS`: awans odbywa się per klasa po spełnieniu kryteriów `SYN-001`. | `PROPOSED_SYNTHESIS`; `SYN-001` | Prompt 02 + protocol/DoD/ADR-002 | Nie jest zgodą na flip. | Wymaga review w Prompt 03 po decyzjach właściciela. |

## 19. Kryteria sukcesu

| Reguła | Status / claim | Źródło | Wyjątki | Znane luki |
|---|---|---|---|---|
| `PROPOSED_SYNTHESIS`: mierz autonomous decision share, właściwy outcome, regresje HARD, niepewność/coverage i rutynowe interwencje razem. | `PROPOSED_SYNTHESIS`; `SYN-003` | meta-cel; lessons/outcome | Brak jednej liczby zbiorczej. | Eventy/progi `UNK-001`, `UNK-002`, `UNK-005`. |
| Agreement/override rate sam nie jest sukcesem. | `CANON_CONFIRMED`; `GT-001`, `LRN-001` | Prompt 02; lessons | Może być wskaźnikiem UX lub driftu decyzji człowieka. | Brak. |

## 20. Jawne non-goals

| Non-goal | Status / claim | Źródło | Wyjątki / luka |
|---|---|---|---|
| Nie maksymalizować score kosztem HARD lub wyniku floty. | `CANON_CONFIRMED`; `BR-001`, `BR-019` | Prompt 02; kanon; ADR-001 | Brak. |
| Nie używać proxy jako physical truth. | `OWNER_CONFIRMED`; `GT-010` | Prompt 02; ETA contract | Brak. |
| Nie automatyzować przez imitację operatora albo jeden case. | `OWNER_CONFIRMED`; `LRN-001`, `LRN-002` | Prompt 02; lessons | Brak. |
| Nie traktować aktualnej flagi, parsera, modelu, wagi lub algorytmu jako celu produktu. | `PROPOSED_SYNTHESIS`; `SYN-007` | Prompt 02; architecture | Technika może się zmieniać przy zachowaniu kanonu. |

## Otwarte zależności kandydata

Kandydat jest spójny w rdzeniu liczbowym i hierarchii, lecz nie może stać się autorytatywny bez decyzji `UNK-001`–`UNK-009` zgrupowanych w siedem pytań właściciela. Szczegółowe pytania są wyłącznie w `OWNER_DECISION_PACKET.md`.
