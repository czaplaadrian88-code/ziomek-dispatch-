# Skille Ziomka (`.claude/skills/`)

Skille odkrywalne natywnie przez Claude Code dla sesji pracującej w `dispatch_v2`.
Każdy ma **driver** (kod, który uruchamiasz) i **selftest** wpięty w nocną regresję
(`tests/test_skills_selftest.py`) — oracle egzekwowany co noc, nie raz.

| skill | do czego | driver | selftest |
|---|---|---|---|
| **run-dispatch-v2** | uruchom/zdiagnozuj Ziomka (usługi, strażnik, przecieki, flagi) | `driver.sh health` | `selftest.sh` (5/5) |
| **ziomek-blind-review** | niezależna ślepa recenzja kandydata przed promocją | `driver.py blind/check/eval` | `selftest.sh` (8/8) |
| **ziomek-cto** | cykl sesji CTO: brief stanu, mapa kompletności zmiany (ETAP 3 #0), bramka DoD na diffie, szablon handoffu | `driver.py brief/scope/dod/handoff` | `selftest.sh` (16/16) |

## Zasada, z której wyrosły (audyt 2026-07-17)

Skill bez drivera, który realnie coś robi, i bez oracle, który realnie coś sprawdza,
to README z dodatkowymi krokami. Zdeprecjonowana brama `ziomek-change-gate` (3185
linii) miała trzy zielone walidatory, a mimo to przepuszczała `SKILL.md` z instrukcją
„deploy na produkcję bez ACK" — bo wszystkie sprawdzały **kształt**, nie **treść**.
Stąd twarde reguły tego katalogu:

- **driver jest deliverable'em**, SKILL.md to jego instrukcja obsługi;
- każdy blok kodu w SKILL.md to komenda, która **została uruchomiona**;
- oracle to **zewnętrzne, potwierdzone** fakty, nie autowalidacja;
- co dotyka żywego stanu — za bramką ACK, nie za skrótem.

## Nie-skille tu nieobecne (świadomie)

`ziomek-change-gate` → **DEPRECATE**. Nie ma go w tym katalogu ani w masterze —
żyje na gałęzi `codex/ziomek-skill-gate-remediation7-*` (decyzja o kasacji gałęzi
= MAIN, nie tu). ~25% wartej uratowania treści → `docs/proposals/` (propozycja do
`/root/.codex/AGENTS.md`, NIE zainstalowana). Pełny werdykt i plan:
`memory/ziomek-skills-plan-2026-07-17.md`.
