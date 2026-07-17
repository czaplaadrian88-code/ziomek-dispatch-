# PROPOZYCJA — NIE INSTALOWANA. Instalacja do /root/.codex/AGENTS.md = decyzja właściciela/MAIN.

<!-- META — NIE WKLEJAĆ do AGENTS.md. Dowód, że sekcja poniżej NIE duplikuje AGENTS.md. -->

**Co ta propozycja świadomie POMIJA (bo JUŻ jest w AGENTS.md):**
- Protokół ETAP 0-7 → AGENTS.md linie **256-366** (ETAP 0=261, 1=282, 2=290, 3=296, 4=318, 5=328, 6=338, 7=356).
- Brief 5-punktowy → AGENTS.md linie **229-241** („Komunikacja i bramki", 5 punktów 233-237).
- Mapa kompletności (tabela `miejsce | rola | writer/consumer | TAK/N-D | powod | test`) → AGENTS.md **296-316** (wzór w linii 300).
- HARD/SOFT (SOFT nie osłabia HARD) → AGENTS.md **290-294** (ETAP 2) + **380** (kanon).
- Biznesowy ACK (bramki live) → AGENTS.md **243-254**. Floor modelu/effort per ryzyko → **104-126**.

**Dowód, że 4 rzeczy poniżej są NOWE (grep AGENTS.md = 0 trafień, wielkość liter ignorowana):**
`blocker_codes`=0, `effect_boundary`=0, `write_set`=0, `mutation_surface`=0, `read_only_no_effect`=0,
`AUTHOR_STATIC_ORACLE`=0, `gate tuple`/`GATE_TUPLE`=0, `disposition`=0, `READY_FOR_REVIEW`=0, `READINESS_CONTEXT`=0.
Najbliższa istniejąca proza o niezależnym oracle (bez statusu maszynowego) = AGENTS.md **335**. Cała reszta = 0.
Źródło faktów: `references/gate-contract.md` + schema `ziomek-change-gate-result-v1.schema.json` + `validate.py` (mapowanie krotek 946-959, „disposition must be derived from blocker_codes" 1707).

<!-- ═══════════ WKLEJ PONIŻEJ jako nową sekcję AGENTS.md ═══════════ -->

## Kontrakt wyniku bramy zmian

Brama zmian (skill `$ziomek-change-gate`) zwraca JSON zgodny ze schematem
`ziomek-change-gate-result-v1` (`schema_version` = "1.0"). Poniżej jest TYLKO
warstwa maszynowa wyniku; sam protokół pracy (ETAP 0-7, brief, mapa
kompletności, HARD/SOFT) jest w sekcjach wyżej i tu się go nie powtarza.

### Dyspozycja jest wyliczana, nie deklarowana
- `disposition` (`READY_FOR_IMPLEMENTATION` / `READY_FOR_REVIEW` / `HOLD`) jest
  WYLICZANA z `blocker_codes`, nie wpisywana ręcznie.
- `blocker_codes` to zamknięty enum (~32 kody) liczony jedną zamkniętą tabelą relacji.
- Niepusty `blocker_codes` ZAWSZE → `HOLD`. Pusty zbiór → READY tylko dla jednej
  jawnie obsłużonej kombinacji trybu/bram/targetu; każda inna → kod
  `UNHANDLED_STATE_COMBINATION` → `HOLD`.
- Nie dopisuj ani nie ukrywaj blockera przez wpisanie wygodniejszej dyspozycji.

### Granica efektu jest strukturą, nie prozą
`effect_boundary` zastępuje opis słowny „N-D:" i jest źródłem prawdy o powierzchni zmiany:
- `write_set` — dokładne ścieżki plików objęte kandydatem (względne wobec repo);
- `mutation_surface` — zamknięte klasy efektu: STAGED_ARTIFACTS, PRODUCT_CODE,
  PRODUCT_RUNTIME, FLAGS, DATA, SERVICES, TMUX, LEASE, DISCOVERY_ACTIVATION, BUSINESS_SEMANTICS;
- `read_only_no_effect` (bool) — `true` dozwolone TYLKO gdy obie listy są puste.
Wolna proza nie może zamaskować dotknięcia runtime, flag, danych, usług, tmuxa,
lease'u ani semantyki biznesowej — liczy się struktura.

### Trzy dozwolone krotki bram → dyspozycja
Bramy to krotka 4 pól `independent_review / implementation / production_operation / activation`.
READY dają WYŁĄCZNIE 3 krotki (drift dowolnego pola → `HOLD`):
- Analiza (ANALYSIS_ONLY): `NOT_REQUIRED / READY / N-D / N-D` → `READY_FOR_IMPLEMENTATION` (read-only, obie listy efektu puste).
- Kandydat autora do review (IMPLEMENTATION_CANDIDATE): `PENDING / READY / N-D / REVIEW_REQUIRED` → `READY_FOR_REVIEW`.
- Implementacja staged (IMPLEMENTATION_CANDIDATE): `NOT_REQUIRED / READY / N-D / REVIEW_REQUIRED` → `READY_FOR_IMPLEMENTATION`.

### Oracle autora ≠ niezależne review
- Każda krotka READY dopuszcza tylko `oracle = AUTHOR_STATIC_ORACLE` (statyczny korpus autora).
- `AUTHOR_STATIC_ORACLE` to NIE behawioralny PASS i NIE niezależne review.
- `INDEPENDENT` nie może zadeklarować autor — należy do osobnego, świeżego review.
- `SELF_CONFIRMING`, `N-D`, `MISSING` zawsze blokują READY.
