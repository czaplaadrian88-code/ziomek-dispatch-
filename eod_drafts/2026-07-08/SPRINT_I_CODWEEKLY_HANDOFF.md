# HANDOFF — SPRINT I (NAPRAWA) „cod-weekly: korzeń nawracającego FAILED w poniedziałki (split-tygodnia)"
**Sesja: tmux 42. Baseline: master `8760ee6`.**
**Worktree (TYLKO TU): `/root/.openclaw/workspace/scripts/wt-codweekly` (branch `fix/codweekly-split-week`).**

## 0. PROTOKÓŁ #0
Wklej `memory/ziomek-change-protocol.md`. ETAP 0: cd do worktree → **zielony baseline `pytest tests/`** (są testy `test_cod_weekly*.py` — w tym `test_cod_weekly_split_week.py`/`_missing_block.py`; użyj ich jako sieci). Fix U ŹRÓDŁA. **⚠ ŻADEN `--write` do żywego arkusza Google bez ACK — pracuj TYLKO `--dry-run`.** Restart/flip usług = ACK.

## 1. PROBLEM
`dispatch-cod-weekly` (settlement pobrań → arkusz Google) **pada w poniedziałki na tygodniach-splitach** (gdy tydzień rozliczeniowy przechodzi przez granicę miesiąca → potrzebne 2 bloki w arkuszu). 02.07 i 06.07 = FAILED, domykane RĘCZNIE (dodawanie bloku + manual write). Korzeń: **detekcja/obsługa split-tygodnia jest krucha** — przypadek „ambiguous" (segment bez odpowiadającego bloku, np. 01-05.07 gdy DD=tylko 29-30.06) wymaga człowieka (Rafał). Cel: żeby przestało PADAĆ (exit1) i albo obsłużyło się samo (gdy jednoznaczne), albo dawało **czysty, actionable stan** zamiast crasha.

## 2. CEL
- **I1:** zdiagnozuj dokładnie ścieżkę split-tygodnia w kodzie cod-weekly (skrypt + logika bloków/mappera) — kiedy exit1 vs kiedy „ambiguous".
- **I2:** **fix u źródła:** (a) gdy split JEDNOZNACZNY (oba bloki istnieją/da się bezpiecznie utworzyć) → obsłuż automatycznie; (b) gdy naprawdę potrzebny człowiek → **graceful actionable**: jasny komunikat „utwórz blok X za okres Y", exit-kod odróżniający „błąd" od „czekam na blok", ZERO utraty danych (dopisywalne później), ZERO crasha. Idempotencja zachowana (empty-check nie nadpisuje ręcznych wartości).
- **I3:** dowód na `--dry-run` na realnym split-tygodniu (np. 29.06-05.07) — pokazuje poprawną decyzję bez zapisu.

## 3. ZAKRES / GRANICE
**WOLNO:** skrypt/logika cod-weekly (settlement pobrań), jego testy `test_cod_weekly*.py`, docs. Drop-iny systemd cod-weekly TYLKO jako propozycja (instalacja/enable = ACK).
**⛔ NIE WOLNO:** silnik decyzyjny (solver/feasibility/route/pipeline/ETA), grafik `fetch_schedule` (Sprint H), ścieżka współrzędnych (Sprint F), `flags.json`, **`--write` do żywego arkusza** (tylko dry-run), restart usług bez ACK.

## 4. DoD
1. Regresja `pytest tests/` zielona (+ testy cod-weekly rozszerzone o naprawiany przypadek).
2. Split-tydzień: jednoznaczny → auto; niejednoznaczny → graceful actionable (nie crash), udowodnione `--dry-run`.
3. Commit PRZED końcem. Merge sekwencyjny po ACK. Raport `eod_drafts/2026-07-08/S_I_CODWEEKLY_raport.md`.

## 5. WĄTPLIWOŚĆ (np. konwencja bloków arkusza = Rafał) → PYTAJ ADRIANA, NIE ZGADUJ.
