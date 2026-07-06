# devlint — ratchet lint/typecheck (K01 programu refaktoru, 2026-07-06)

**Zasada:** naruszeń nie może PRZYBYĆ. `baseline.json` trzyma stan zastany; nowe naruszenie = exit 1.
Sprzątanie obniża licznik → wtedy świadomy `--update-baseline` w OSOBNYM commicie.

## Użycie
```
python3 tools/devlint/ratchet_check.py                    # vs baseline (exit 1 przy wzroście)
python3 tools/devlint/ratchet_check.py --update-baseline  # po sprzątnięciu (jawny commit)
DEVLINT_VENV=/inna/sciezka python3 tools/devlint/ratchet_check.py
```

## Środowisko
- Narzędzia w **osobnym venv** `/root/.openclaw/venvs/devlint` (ruff 0.15.x, mypy 2.1.x) — **venv silnika `venvs/dispatch` NIETKNIĘTY** (ADR-006 lean; ACK Adriana 06.07, Faza 0 pyt. 6).
- Skrypt sam działa na systemowym python3 (tylko subprocess do venv-owych binarek).

## Zakres i baseline startowy (2026-07-06, HEAD ca525c7)
- **ruff:** całe repo poza `eod_drafts/docs/migrations/deploy*/dispatch_state`; reguły E9/F/B/PLE (bez kosmetyki E501). Baseline: **608**.
- **mypy:** lista modułów rdzenia w `MYPY_MODULES` (ratchet rośnie zakresem wraz z krokami stranglera); tryb łagodny (`ignore_missing_imports`, bez strict). Baseline: **0** — kod nietypowany, mypy liczy błędy tylko w kodzie adnotowanym; licznik zacznie chronić nowe typowane moduły `core/` (K09+).
- Wpięcie do night-guard: NA RAZIE NIE (najpierw okrzepnięcie; plan: tryb informacyjny, potem egzekwujący — patrz `docs/refaktor/04-plan-migracji.md` K01/K17).

## Polityka
- Zero naruszeń NOWYCH w plikach dotykanych krokami refaktoru (sprzątaj przy okazji, dopisuj niżej baseline).
- `baseline.json` zmieniany TYLKO jawnym commitem z powodem; kierunek preferowany: w dół.
