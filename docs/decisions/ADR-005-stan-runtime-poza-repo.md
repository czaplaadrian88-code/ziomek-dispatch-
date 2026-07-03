# ADR-005: Stan runtime POZA repo + rozdwojenie logów

Status: obowiązuje (od dawna; udokumentowane jako pułapka nazewnicza w audycie 2026-07-03)

## Kontekst
Pułapka nazewnicza wysokiego ryzyka: katalog `dispatch_v2/dispatch_state/` WEWNĄTRZ repo NIE jest żywym stanem silnika — zawiera wyłącznie `epaka_data/` (CSV/JSON cennika epaki, ~15 MB, temat odrębny; zbieg nazw). Prawdziwy stan runtime silnika (orders_state, courier_plans, shadow logi, ~318 plików, ~1,1 GB) leży POZA gitem. Kto analizuje „stan systemu" patrząc na repo, patrzy w złe miejsce.

## Decyzja
Stan runtime silnika żyje w `/root/.openclaw/workspace/dispatch_state/` (POZA gitem; ścieżka hardcoded w `common.py`, guardy hardcodują ją np. `carried_first_guard.py`). Logi rozdwojone: kanoniczny log decyzji `shadow_decisions.jsonl` (~84 MB) w `scripts/logs/`; pozostałe shadow-jsonl (`r6_breach_shadow`, `obj_replay_capture`, `reassignment_shadow`, `v319c_read_shadow_log`) w `dispatch_state/`.

## Konsekwencje
- Nie wolno: czytać `dispatch_v2/dispatch_state/` jako stanu silnika — to dane epaki; ani zakładać, że repo zawiera stan.
- Trzeba: szukać planów/guardów/shadow pod ścieżką absolutną workspace; kotwiczyć ścieżki w docs od `workspace/` (relatywne `dispatch_state/…` dają fałszywe „MISS").
- `shadow_decisions.jsonl` szukaj w `scripts/logs/`, resztę shadow-jsonl w `dispatch_state/` — nie mylić lokalizacji przy `grep -c <key>`.
- Konsekwencja: `.git` nie puchnie od runtime; backup stanu = osobny mechanizm (restic), nie git.
- Kandydat porządków: README-ostrzeżenie w `dispatch_v2/dispatch_state/` lub rename `epaka_data_staging/` (dotyka epaka_fetcher — za WD-9).

## Źródła
`docs/audyt/00-INWENTARYZACJA.md` §0 (odkrycie kluczowe) + §4; `docs/audyt/01-ZALEZNOSCI.md` §2b + §3a (tabela plików stanu, rozdwojenie logów); `docs/audyt/04-TESTY.md` §1a; `common.py` (hardcode ścieżki), `CLAUDE.md` CRITICAL PATHS.
