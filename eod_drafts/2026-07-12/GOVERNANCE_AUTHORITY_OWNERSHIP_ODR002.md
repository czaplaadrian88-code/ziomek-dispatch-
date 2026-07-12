# Governance authority ownership — ODR-002

Data: 2026-07-12

Status: `OWNER_DECISION_RECORDED`, zero zmiany runtime i execution authority.

## Problem i dowód

ODR-001 ustanowił authority per klasa, ale kanon nie rozstrzygał wyłącznie i
kompletnie: kto może awansować klasę, zakazu samopromocji, wymaganego łańcucha
evidence→independent review→owner signature→deterministic apply ani obowiązku
sprawdzania podpisanej karty przed każdym wykonaniem. Bieżąca jawna decyzja
właściciela zamyka tę lukę. Runtime nie ma jeszcze autorytatywnej podpisanej
karty ani pełnego gate'a, więc implementacja pozostaje `HOLD`.

## Mapa kompletności

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód | Test |
|---|---|---|---|---|---|
| `docs/decisions/ODR-002-*` | źródło intencji | owner / wszystkie sesje | TAK | pełny rekord decyzji | review 8/8 punktów |
| `docs/decisions/README.md` | nawigacja | sesje | TAK | ODR ma być odnajdywalny | link check |
| `ADR-002` / `ADR-003` | shadow-first / always-propose | release i selekcja | TAK | ACK/ALERT nie są authority | source grep |
| `ZIOMEK_ARCHITECTURE.md` | kanon źródeł prawdy | architektura | TAK | authority ≠ dokument/kod/flaga | grep kontraktu |
| `ZIOMEK_INVARIANTS.md` | strażnik docelowy | runtime/testy | TAK | nazwanie czerwonego SLOT-u | grep + review |
| `ZIOMEK_DEFINITION_OF_DONE.md` | anty-entropia | release/review | TAK | blokada samopromocji i fail-open | grep + review |
| `ZIOMEK_BACKLOG.md` | punkt wykonawczy | przyszły sprint R4 | TAK | implementacja bez domniemanej zgody | wpis P0/B-09 |
| memory kanon/protokół/handoff | trwała pamięć | kolejne sesje | TAK | decyzja nie może zginąć | osobny commit repo memory |
| `docs/chief-engineer/**` | karta/schema/parser/policy | aktywny obcy lease | N-D | owner ścieżek jest aktywny; zero przejęcia/edycji | status/lease metadata |
| runtime/flags/usługi/dane | wykonanie | procesy produkcyjne | N-D | ODR nie jest kartą ani deployem | zero changed paths/ops |
| shadow-jobs registry | termin werdyktu | at/timer | N-D | nie powstał job ani obserwacja | `atq` nadal tylko 214 |

## Baseline, testy i review

- Ostatni zielony produktowy baseline: 5152 passed, 27 skipped, 8 xfailed,
  zero fail/XPASS (2026-07-12 13:53–13:58 UTC).
- Od tego baseline do wejściowego HEAD `fd5678b` zmieniły się wyłącznie pliki
  Markdown. Pełnej suity nie powtarzano, bo docs-only sprint nie zmienia kodu,
  a ciężki run przed at-214 skaziłby aktywne canary.
- Wymagane dla tego sprintu: `git diff --check`, link/source grep, kontrola
  ośmiu punktów ODR, changed-path allowlist i adversarial exact-candidate
  self-review. Niezależna weryfikacja jest N-D dla samego zapisu decyzji bez
  promocji; pozostaje obowiązkowa przed każdym przyszłym zwiększeniem authority.

## Live, ryzyka i rollback

Nie zmieniono karty, schema/parsera, runtime gate, promotion policy, evala,
progu, flagi, danych, usługi ani procesu. Nie wykonano deployu, restartu,
migracji, canary ani flipa. Główne ryzyko to pomylenie ODR z execution
authority; dlatego ODR jawnie stwierdza `Runtime effect: none`.

Rollback dokumentacyjny: jawny revert commitu tego sprintu. Nie ma rollbacku
runtime, bo nie było operacji live. Aktywny `docs/chief-engineer/**` pozostaje
obcym, nietkniętym write-setem i musi później zrekoncyliować ODR-002 jako nowe
źródło właścicielskie przez własną governance-compatibility ścieżkę.
