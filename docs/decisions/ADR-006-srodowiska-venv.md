# ADR-006: Trzy interpretery — venv dispatch / venv sheets / system py

Status: obowiązuje (od ~kwietnia 2026; dedykowany venv dispatch z ortools 9.15)

## Kontekst
System używa trzech różnych interpreterów ze świadomym rozdzieleniem. Silnik potrzebuje OR-Tools (TSP) i stosu ML; Google Sheets potrzebuje gspread+google-auth+requests; mosty biznesowe biegną na systemowym pythonie. Mina środowiskowa: naiwny `python3 -m pytest` daje 123 fałszywe faile (`ModuleNotFoundError: ortools`), bo systemowy `/usr/bin/python3` nie ma ortools — to tylko w venv dispatch.

## Decyzja
SILNIK + wszystkie usługi/timery `dispatch-*` + TESTY = `venvs/dispatch` (py3.12.3; ortools 9.15, lightgbm/sklearn/pandas/numpy/scipy, python-dateutil, pytest; ~21 pakietów, BRAK requests/httpx/flask/aiohttp — HTTP CELOWO przez stdlib `urllib`, gps_server przez `http.server`; lean = mała powierzchnia ataku). Google Sheets (`cod-weekly*`, `daily-accounting`, `daily_stats_sheets`) = `venvs/sheets` (gspread+google-auth+requests+oauthlib). Mosty (`drtusz-bridge`, `papu-bridge`) = system `/usr/bin/python3`. Testy uruchamiane WYŁĄCZNIE z venv dispatch.

## Konsekwencje
- Trzeba: testy zawsze `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/` (baseline 4109/0), nie `python3`.
- Nie wolno: dodawać requests/flask/aiohttp do silnika (łamie celowy lean-HTTP); mieszać interpretery (systemowy = 123 fałszywe faile ortools).
- Nowa zależność sieciowa w silniku → stdlib `urllib`, nie nowy pakiet.
- CI/audyt musi używać venv — inaczej werdykt regresji jest artefaktem złego interpretera (Agent E potwierdził: pierwszy bieg 123 failed = pomyłka interpretera, nie regres).

## Źródła
`docs/audyt/01-ZALEZNOSCI.md` §5 (tabela per-interpreter, `pip freeze` z ExecStart); `docs/audyt/04-TESTY.md` §1a + „uczciwość na wstępie" (123 fałszywe faile) + §5; `requirements-dispatch-venv.txt`.
