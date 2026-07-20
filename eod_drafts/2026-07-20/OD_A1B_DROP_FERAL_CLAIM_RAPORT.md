# OD-A1b — DROP-FERAL-CLAIM (2026-07-20)

Status: SOURCE-ONLY; base `2c72bb40`; `_HARD` OFF. Bez merge/live/restartu.
Bundle: `/tmp/OD-A1b-drop-feral-claim.bundle`.

## Mapa kompletności

| miejsce | rola | writer/consumer | stan | test/dowód |
|---|---|---|---|---|
| `claim_ledger.check_feral_claim` | oracle per claim | writer | TAK | oracle + log-loud |
| `global_allocate` | resweep | consumer/drop przed `results_out`, flotą i live | TAK | ON≠OFF, ciąg dalszy o3 |
| `pending_global_resweep.run_once` | jsonl+summary | producer `g_claim_ledger_feral_drops` | TAK | licznik=1, marker dropu |
| `shadow_dispatcher._tick` | bliźniak (ENGINE=ON) | drop przed ETA/pending/auto | TAK | P/D/D/P |
| `reassignment_global_select` | pośredni konsument resweepu | consumer | N-D | dziedziczy `global_allocate`; brak kopii |
| serializer Candidate A+B / `_METRICS_EXCLUDE` | metryki `Candidate.metrics` | consumer | N-D | nowy licznik jest top-level jsonl, nie Candidate.metrics |
| health scoreboard | alert breach | reader | N-D | breach nadal alarmuje; drop-counter jest audytem enforcementu |

N-D: `scoring.py` — brak zmiany SOFT.
N-D: `core/candidates.py` — brak zmiany selekcji.

regresja: 24 passed, 0 failed (`HERMETIC_STRICT=1`, hermetyczny flags/log bootstrap w `/tmp`).
e2e: oba bliźniaki: feral zdropowany, tick/sweep dochodzi do późniejszego claimu, metryka jsonl+summary.
pozytywny-wplyw: HARD ON blokuje sprzeczną propozycję bez zatrzymania całego ticku.
bajt-identycznosc: HARD OFF zachowuje allocation, rekordy i brak nowych pól; testy przypinają OFF oraz CHECK-only z wymuszonym stale claimem.
rollback: revert commitu; przed flipem nie potrzeba operacji runtime. Po przyszłym flipie kill-switch `_HARD=false` hot-reload.

Blind #1: totals/overcount naprawione; ENGINE=warunek. Blind final: CLEAN.
Mutation-probe: neutralizacja gate daje czerwoną asercję. Ryzyko: wyjątek checkera jest fail-soft; typy śladu są lokalnie kontrolowane. Pełna suita/replay/merge: CTO.
