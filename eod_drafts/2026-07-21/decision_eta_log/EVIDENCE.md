# Evidence — decision-time ETA log

Status: SOURCE COMPLETE / LIVE HOLD.

- base: `95fff2e4`
- branch: `codex/decision-eta-log`
- produkcja: zero zmian, flipów, restartów i deployu
- rollback source: `git revert <commit>`
- test własny + klastry jsonl/czasówka/resweep/reassignment/plan:
  `113 passed, 1 skipped, 21 istniejących PytestReturnNotNoneWarning`, 6,14 s
- ON!=OFF: OFF nie tworzy pliku; ON zapisuje pełną pulę głównej selekcji,
  per-leg ETA i provenance; test fail-safe wymusza `OSError("disk full")` i
  potwierdza niezmieniony wynik + `errors=1`
- privacy: allowlista contextu i test odrzucenia adresu; brak nazw/adresów/coords
- `python3 -m py_compile` dla 11 zmienionych modułów/testu: PASS
- `git diff --check`: PASS
- `tools/flag_lifecycle_check.py`: PASS, 518/518 curated, 0 błędów
- `tools/entropy_dashboard.py`: PASS; brak wzrostu 8 ratchetów (repo-hermetic,
  żywy runtime poza sandboxem)
- kanoniczna pełna suita: HOLD, interpreter venv rc=126 `Permission denied`;
  systemowy Python nie jest substytutem (`ModuleNotFoundError: ortools`)
- pełna suita venv (CTO, przed merge):
  `DISPATCH_FLAGS_PATH=/tmp/cx-dtlog-pyroot/flags.json ZIOMEK_SCRIPTS_ROOT=/tmp/cx-dtlog-pyroot PYTHONPATH=/tmp/cx-dtlog-pyroot DISPATCH_UNDER_PYTEST=1 HERMETIC_STRICT=1 /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q`

regresja: klaster 113 passed, 0 failed, 1 skipped; pełna suita venv HOLD rc=126 i jest obowiązkowa przed merge/deploy
e2e: hermetyczny kontrakt writer→append JSONL→validator/coverage plus istniejące klastry pięciu call-site'ów przy OFF; live E2E zabronione w tym buildzie
pozytywny-wplyw: ON!=OFF — OFF zero pliku/I/O, ON snapshot 2 kandydatów i obu ETA; wymuszony błąd append nie zmienia wyniku i daje errors=1
rollback: przed live flaga false/brak klucza; source git revert commitu, bez migracji; restart/deploy nie wykonany
N-D: parcel_assign.py i panel manual override nie mają predykcji ETA/puli do zapisania; nie wolno fabrykować rekordu
N-D: auto_assign_gate.py, dispatch_pipeline.py i drive_min_calibration.py — hook w reassignment_forward_shadow.py tylko odczytuje gotowy wynik/pos_source; nie zmienia równego traktowania pozycji
N-D: claim_ledger.py — hooki shadow_dispatcher.py i pending_global_resweep.py są po finalnym claim disposition i nie zmieniają sprawdzania ani aplikacji claimu
N-D: core/candidates.py i scoring.py — hook shadow_dispatcher.py nie produkuje ani nie konsumuje score; loguje dopiero finalny PipelineResult
