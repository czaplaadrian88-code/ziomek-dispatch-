# Working-override „X pracuje" — design + deploy (2026-06-01)

**Cel (Adrian):** komenda „pracuje" ma działać dla DWÓCH przypadków —
(1) powracający po `/stop` (zdjęcie z excluded — było), (2) kurier SPOZA grafiku
który zaczyna (NOWE — staje się dispatchowalny mimo „brak w grafiku").

## Problem (diagnoza)
Dwie osobne bramki w `courier_resolver.dispatchable_fleet`: `excluded` (lista STOP)
ORAZ grafik (`match_courier` → „brak w grafiku"). Komenda „pracuje" dotykała tylko
`excluded`. Kurier spoza grafiku = hard-reject niezależnie od „pracuje" + mylące
potwierdzenie „przywrócony".

## Rozwiązanie
Sekcja `working` w `manual_overrides.json`: `{cid_str: {start,end,name,added_at}}`.
- **cid-keyed** (NIE name-merge do grafiku) → omija fuzzy `match_courier_strict`
  ambiguity (V3.25 landmine „Jakub OL"→„Jakub Leoniuk"). Zob. test_9.
- **FALLBACK, nie autorytatywny**: w `dispatchable_fleet` override bierze górę TYLKO
  gdy kurier NIE jest na realnej zmianie teraz (spoza grafiku / po zmianie / przed).
  Gdy JEST na realnej zmianie → realny grafik wygrywa (NIE rozszerza godzin
  powracającemu po /stop, np. grafik 08–16 nie staje się 08–24). Zob. test_13.
- **Syntetyczna pozycja** BIALYSTOK_CENTER (jak pre_shift) gdy brak GPS → kurier od
  razu dispatchowalny; realny GPS wygrywa (granted tylko gdy `pos is None`). test_10.
- **Lifecycle „do końca dnia"** — reset 06:00 czyści `working` razem z `excluded`.
- **Keyword split**: tylko „pracuje/wraca/wrócił/dodaj" + slash `/pracuje` `/wraca`
  dodają do grafiku; samo „jest" zostaje czystym un-exclude (anti „gdzie jest X").
- **Parsowanie**: „X pracuje do HH:MM" (zawęża koniec), „od HH:MM" (start), default
  start=teraz, end=24:00. test_3/test_4.
- **Uczciwe potwierdzenie**: „✅ X (cid=N) pracuje dziś (HH:MM–końca dnia) — będę go
  proponował" zamiast mylącego „przywrócony". Brak cid → instruuje `/dopisz`. test_6.

## Pliki
- `common.py` — flaga `ENABLE_WORKING_OVERRIDE` (env-latch default 1) + `WORKING_OVERRIDE_DEFAULT_END`.
- `manual_overrides.py` — `get_working()`, `_parse_shift_bounds`, `_add_working`,
  `_do_include(add_to_grafik)`, `_do_exclude` (usuwa też working), `_WORKING_ADD_KEYWORDS`,
  reset `working` w „reset", alias `/pracuje`.
- `courier_resolver.py` — wczytuje `working`, syntetyczna pozycja przed `pos is None`,
  FALLBACK gałąź (real-on-shift wins). Flaga = `flag("ENABLE_WORKING_OVERRIDE", default=env)`
  → **hot-reload kill-switch** (flags.json instant disable bez restartu).
- `manual_overrides_daily_reset.py` — czyści `working` (nie short-circuit gdy excluded puste).
- `tests/test_working_override_2026_06_01.py` — 14 testów.

## Testy
14/14 nowych + 57 regresji (R-03 T4 zaktualizowany do uczciwego komunikatu).
`test_v325_step_a_r02` 10 fail = PRE-EXISTING (stale „Albert Dec", potwierdzone na .bak).

## Deploy (PENDING — NIE LIVE)
Kod gotowy, NIE wdrożony. Restart wymagany:
- `dispatch-shadow` + `dispatch-panel-watcher` (courier_resolver + common).
- `dispatch-telegram` (manual_overrides parse) — **WYMAGA EXPLICIT ACK ADRIANA**.
- **Blackout:** Pn 01.06 17:15 = dinner peak (17–20). Deploy po 20:00 Warsaw lub
  jutro off-peak.

## Rollback
- Hot (bez restartu): `flags.json` → `"ENABLE_WORKING_OVERRIDE": false`.
- Soft: env `ENABLE_WORKING_OVERRIDE=0` w override.conf + restart shadow/panel-watcher.
- Hard: przywróć `.bak-pre-working-override-2026-06-01` (4 pliki) + restart.
- Dane: override auto-czyszczą się 06:00; ręcznie „reset" w Telegramie.

## GOTCHA (lekcja)
`manual_overrides_daily_reset.main()` ma hardcoded `_reset_coordinator_activations()`
na PRAWDZIWYM `coordinator_activations.json` — uruchomienie w teście wyzerowało realną
aktywację Bartka O (cid=123). Przywrócone wiernie. Na przyszłość: testować reset ze
stubniętym coordinator reset (monkeypatch), nie na żywym `main()`.
