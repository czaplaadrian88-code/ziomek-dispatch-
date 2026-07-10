# Indeks reprodukcji

Nie używać bare `pytest` ani systemowego Pythona. W przykładach:

```bash
export AUDIT_REPO=/path/to/audit-worktree/dispatch_v2
export PKGROOT=/path/to/audit-worktree
export DISPATCH_PY=/path/to/dispatch-venv/bin/python
cd "$AUDIT_REPO"
```

`PKGROOT` musi zawierać kopię `flags.json` 0600 przypiętą do badanego snapshotu.
Każdy pytest dostaje `DISPATCH_UNDER_PYTEST=1`, `HERMETIC_STRICT=1`, poprawny
`PYTHONPATH`/`ZIOMEK_SCRIPTS_ROOT` i wyłączony cache provider. Testy nie mogą
pisać do live state, logów ani flag.

## Pakiet audytu

```bash
"$DISPATCH_PY" audits/2026-07-10/full-system-360/_build_recovered_findings.py
"$DISPATCH_PY" audits/2026-07-10/full-system-360/_build_tool_trust_matrix.py
"$DISPATCH_PY" audits/2026-07-10/full-system-360/_validate_package.py
```

Oczekiwane: pin źródła przechodzi, findings MD/JSON/CSV mają 110 rekordów,
tool JSON/CSV mają 15 instrumentów, validator zwraca
`AUDIT360_VALIDATE OK required=35 findings=110 tools=15`. Pełny załącznik
źródłowy nie jest częścią repo i nie wolno go commitować.

## Celowany klaster domen audytu — wykonany

```bash
env DISPATCH_UNDER_PYTEST=1 HERMETIC_STRICT=1 PYTHONDONTWRITEBYTECODE=1 \
  ZIOMEK_SCRIPTS_ROOT="$PKGROOT" PYTHONPATH="$PKGROOT" \
  "$DISPATCH_PY" -m pytest \
  tests/test_f3_hardrule_flags_effect.py \
  tests/test_best_effort_objm_livekey_2026_06_24.py \
  tests/test_feas_carry_readmit_2026_06_27.py \
  tests/test_v319b_panel_watcher_hooks.py \
  tests/test_v319d_read_integration.py \
  tests/test_plan_cas_stale_write.py \
  tests/test_carry_chain_penalty_v2.py \
  tests/test_candidates_k11.py \
  tests/test_world_replay_gate_k17.py \
  tests/test_world_replay_gate_schema_bucket.py \
  -q -p no:cacheprovider
```

Wynik: 77 passed, 1 xfailed, 0 failed. To nie jest ten sam mianownik co klaster
75 testów narzędzi opisany niżej.

## Klaster trust-tools — wykonany przez niezależnego reviewera

```bash
env DISPATCH_UNDER_PYTEST=1 HERMETIC_STRICT=1 PYTHONDONTWRITEBYTECODE=1 \
  ZIOMEK_SCRIPTS_ROOT="$PKGROOT" PYTHONPATH="$PKGROOT" \
  "$DISPATCH_PY" -m pytest \
  tests/test_health_scoreboard.py \
  tests/test_world_replay_k06.py \
  tests/test_world_replay_gate_k17.py \
  tests/test_world_replay_gate_schema_bucket.py \
  tests/test_scheduled_flip_gate_verify.py \
  tests/test_flag_lifecycle_zp107.py \
  tests/test_flag_fingerprint_check.py \
  tests/test_flag_doc_coverage.py \
  tests/test_flag_effect_coverage.py \
  -q -p no:cacheprovider
```

Wynik: 75 passed. Osobny branch Sprintu 3 uruchomił 28 testów wyłącznie
`tests/test_osrm_health_cache_zp206.py`; nie jest to dowód zachowania bazowego
mastera ani statusu LIVE.

## Pełny baseline

```bash
env DISPATCH_UNDER_PYTEST=1 HERMETIC_STRICT=1 \
  ZIOMEK_SCRIPTS_ROOT="$PKGROOT" PYTHONPATH="$PKGROOT" \
  "$DISPATCH_PY" -m pytest tests/ -q
```

Wynik na bazie: 4792 passed, 6 failed, 76 skipped, 8 xfailed, 2 xpassed.
Dokładna lista faili jest w TEST-11/12; nie dodawać skipów ad hoc.

## TEST-11 — dokładna reprodukcja i root-fix

```bash
env DISPATCH_UNDER_PYTEST=1 HERMETIC_STRICT=1 \
  ZIOMEK_SCRIPTS_ROOT="$PKGROOT" PYTHONPATH="$PKGROOT" \
  "$DISPATCH_PY" -m pytest \
  tests/test_flag_registry_f3.py::test_open_and_accepted_partition_issues \
  -q -p no:cacheprovider
```

Actual=`open`, expected=`known-open`. Root-fix musi izolować zarówno `flags.json`,
jak i `SYSTEMD_DIR`, usunąć historyczny kontrakt `USE_V2_PARSER` z
`KNOWN_DIVERGENCES`, zaktualizować assertion i lifecycle metadata oraz dodać
post-migration oracle: przy pozostałym env carrierze wynik to
`json-overrides-env/open`. Sama kopia pre-flip flag może fałszywie zazielenić test.

## TEST-12 — pięć dokładnych nodeidów

```text
tests/test_resolve_cid_score_based.py::script_run
tests/test_v319h_bug4_tier_cap_matrix.py::script_run
tests/test_v320_packs_ghost.py::script_run
tests/test_v325_step_d_r03.py::script_run
tests/test_v326_hotfix_parser.py::script_run
```

Pierwsze dwa łączą testy mechanizmu z sekcją live smoke; trzy kolejne mogą użyć
syntetycznej identity/state fixture. Najpierw rozdzielić i zhermetyzować testy.
Jeżeli po rozdzieleniu live smoke nadal celowo waliduje host, dopiero jego osobny
nodeid trafia do zewnętrznej kwarantanny z konkretnym powodem. Definition of Done:
STRICT 0 failed i jawna lista skipów, nie tylko zgodna suma.

## Runtime/ops

OPS-01/02/05 i BEZP-01/04 odtwarzać wyłącznie przez ETAP 0: metadane unitów,
listenerów, efektywnego procesu i zatwierdzone provider-side evidence. Raportować
agregaty bez adresów, sekretów i PII. Restart SSH, zmiana firewalla, usługi albo
flagi nie jest reprodukcją audytu i wymaga osobnego ACK.
