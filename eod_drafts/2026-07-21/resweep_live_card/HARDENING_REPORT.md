# Resweep live hardening — 2026-07-21

Base `323034299fbb`, `master`; `model_tier=sol`, `effort=xhigh` (race granicy stanu).

Zakres: `_live_apply` bierze lock lifecycle i pod lockiem pending czyta aktualny `orders_state`; podmienia tylko przy `status==planned`, inaczej skip/fail-closed. Shadow zapisuje `proposed_km` z tego samego `PipelineResult` co nową alokację. Selekcja i HARD/SOFT bez zmian; zero flag, runtime, deployu i restartu.

Mapa #0: state writer N-D; pending writer i producer/JSONL TAK. N-D: `claim_ledger.py`, `shadow_dispatcher.py` — claim bez zmian; `reassignment_global_select.py` — wspólne `global_allocate`, decyzje bez zmian.

regresja: baseline 33 passed, 0 failed; po zmianie klaster resweep/live/twin/ledger 62 passed, 0 failed.
e2e: `run_once` potwierdza assigned-after-snapshot bez podmiany oraz różne `proposed_km`/`new_km_to_pickup`.
pozytywny-wplyw: golden cases: assigned-after-snapshot oraz dystans 9.5→1.25 km.
rollback: `git revert` tego commitu; flaga live pozostaje nietknięta.
N-D: pełna suita — venv zablokowany; fallback przerwany na importach hosta. Wymagana przed merge/flipem. 48h G5 i ACK nadal HOLD.
