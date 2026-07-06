# ADR-R01: FlagSnapshot — flagi czytane RAZ na tick

Status: proponowany (Faza 3 refaktoru; wdrożenie po akceptacji Fazy 4)

## Kontekst
`common.load_flags()` czyta `flags.json` z dysku (stat+parse) przy każdym `flag()`/`decision_flag()` — ~700 wywołań na JEDNĄ decyzję (`common.py:54-77`, komentarz `:25-27`). Skutki: (a) zmiana flags.json w środku ticku zmienia zachowanie między kandydatami tej samej decyzji (niedeterminizm, niereplayowalność), (b) istotny udział w regresji perf ×1,9 (SLO czerwone, raport 04.07), (c) dziesiątki flag env-frozen na poziomie modułów tworzą DRUGIE źródło obok flags.json (pułapka zweryfikowana: `ENABLE_SLA_ANCHOR_UNIFIED` kod=False vs flags.json=true LIVE).

## Decyzja
Na początku ticku silnik robi JEDEN odczyt flags.json → niemutowalny dict (FlagSnapshot) przekazywany w dół (docelowo jako część WorldState, ADR-R02). `C.flag()` dostaje tryb snapshot-first (jawnie przekazany snapshot wygrywa; brak snapshotu = zachowanie dzisiejsze — kompatybilność dla procesów poza tickiem). Hot-reload zostaje MIĘDZY tickami (własność operacyjna zachowana). Nowe flagi: zakaz module-level `os.environ.get` (ratchet w lint/CI); istniejące env-frozen migrowane przy okazji kroków, które ich dotykają.

## Konsekwencje
- Decyzja spójna wobec flag w całym przebiegu; replay dostaje komplet flag z nagrania.
- ~700 syscalli/decyzję → 1/tick (wprost pod czerwone SLO peak p50/p95).
- Rollback: flaga kroku OFF = stara ścieżka odczytu; zero zmian kontraktów.
- NIE zmienia semantyki 3 światów (panel/apka bez zmian) — tylko świat silnika czyta spójnie.

## Źródła
`docs/refaktor/02-diagnoza.md` D5/D6; `raw/01b-rdzen.md` §2/§5; perf `eod_drafts/2026-07-04/perf_budget_report_pre_slo_flip.txt`; ADR-004 (3 światy flag).
