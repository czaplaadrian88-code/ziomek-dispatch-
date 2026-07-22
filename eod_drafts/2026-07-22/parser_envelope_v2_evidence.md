# Parser envelope v2 — evidence (2026-07-22)

## Zakres i baseline

- branch: `parser-envelope-v2`
- base: `af4dc7c61c7c13791fabd657bfa42829eeda1f72`
- worktree: `/tmp/sol-parser` (repozytoryjne `.git` jest read-only w sandboxie;
  zapisy refów wykonano przez odseparowany `GIT_DIR` w `/tmp`)
- produkcja: bez zmian; bez odczytu/mutacji live state, flags i usług
- model_tier: `sol`; effort: `ultra` — provenance, injection i fail-closed
  cross-process są zmianą wysokiego ryzyka.

## Mapa kompletności

| miejsce | rola | writer/consumer | status | dowód/test |
|---|---|---|---|---|
| `uwagi_bridge_envelope.py` | kontrakt v2 | writer + verifier | TAK | HMAC, 0600, escaping, raw-prefix tests |
| `integration_patches/drtusz_bridge_hmac_v2.patch` | producent zewnętrzny | writer | TAK jako artefakt patcha; aplikacja N-D w tym klonie | shared-builder PoC; wymaga osobnego review repo mostu |
| `uwagi_address_parser.py` | parser P0 | consumer | TAK | 3 PoC + granice + kotwice |
| `panel_watcher.py::_diff_and_emit` | ingest/geocode/metric | writer + consumer | TAK | call-site, bbox, KOORD, PII-free JSONL |
| `shadow_dispatcher.py` | geocode bliźniaczy | consumer | TAK | trwały marker blokuje re-geocode |
| `czasowka_scheduler.py` | fallback bliźniaczy | consumer | TAK | marker blokuje centralę nawet przy późniejszym reject=OFF |
| `core/gates.py` | HARD missing-coords | consumer | N-D | istniejący `no_pickup_geocode` daje SKIP/KOORD; test call-site |
| `tools/pending_global_resweep.py` | resweep | consumer | N-D | istniejący filtr `pickup_coords` w linii ok. 464 odrzuca rekord |
| `tools/reassignment_forward_shadow.py` | reassignment shadow | consumer | N-D | istniejący filtr `pickup_coords` w linii ok. 132 odrzuca rekord |
| `state_machine.py` | persist boundary | writer | N-D | istniejąca propagacja całego `uwagi_pickup_parsed` zachowuje marker |
| `core/jsonl_rotation.py` + logrotate | retencja metryki | consumer | TAK | test rotacji + writer registry |
| `common.py` + lifecycle registry | binding flag | invariant/checker | TAK | macierz 4 stanów + checker 518/518 |

Mechaniczna bramka szerokich rodzin (false-positive od nazw plików):

- N-D: claim_ledger.py — zmiana nie dotyka claimów, selekcji ani mutacji floty;
  `shadow_dispatcher.py` tylko respektuje upstreamowy brak pickup coords.
- N-D: core/candidates.py — brak zmiany kandydatów lub scoringu.
- N-D: scoring.py — brak zmiany termów, wyniku i precedencji HARD/SOFT.
- N-D: plan_recheck.py — brak zmiany kanonu/planowania; istniejący HARD missing
  coords pozostaje źródłem decyzji.
- N-D: route_order.py — brak zmiany kolejności trasy.
- N-D: route_podjazdy.py — brak zmiany renderu/planu podjazdów.

## Testy i pozytywny wpływ

- baseline klonu, ten sam hermetyczny symlink-pkgroot i testowy `flags.json`
  wygenerowany z repozytoryjnego registry: `5650 passed, 74 skipped,
  8 xfailed, 149 warnings`.
- pakiet: `5667 passed, 74 skipped, 8 xfailed, 149 warnings`.
- delta: `+17 passed, +0 failed, +0 errors, +0 skipped, +0 xfailed`.
- testy celu wraz z checkerami doc/effect i replay: `71 passed`.
- flag lifecycle checker repo-hermetic: `518/518`, `0 błędów`.
- `py_compile`, JSON registry, `git diff --check`: PASS.
- entropy dashboard: wykonał się read-only; brak dostępu do live daje `0 plików
  żywego silnika`, więc wynik nie jest dowodem produkcyjnym.

regresja: 5667 passed, 0 failed; baseline 5650 passed, 0 failed; delta +17.

e2e: podpisany producent → verifier/parser → `panel_watcher` → geocode/bbox →
`assess_order`/`core.gates` został sprawdzony; reject kończy jako
`no_pickup_geocode`, a marker blokuje re-geocode w shadow i fallback schedulera.

pozytywny-wplyw: ON odrzuca wszystkie 3 konkretne PoC injection/provenance oraz
geocode poza bboxem, OFF zachowuje legacy; central_fallback pozostaje false.

Pozytywny wpływ / oracle:

1. pełna ręczna koperta aid=161 z podrobionym markerem/podpisem kończy jako
   `hmac_mismatch`, bez geocode i bez centrali;
2. poprawnie podpisane `NADAWCA: NADAWCA:` ma raw count 2 i jest odrzucone;
3. `|` w nazwie nadawcy jest `%7C`, nie tworzy segmentu, a parser wybiera
   rzeczywisty adres nadawcy;
4. geocode poza bboxem kończy z `pickup_coords=None` i markerem odrzucenia;
5. kod pocztowy po granicy `| Odbiorca:` nie konkuruje z kotwicą nadawcy;
6. OFF pozostawia legacy parser bez HMAC; ON działa inaczej tylko przy spójnej
   parze flag.

## Rollback i bramki

- hot rollback po przyszłym wdrożeniu: `ENABLE_UWAGI_BRIDGE_NADAWCA=false`;
- kod: jawny revert commitów pakietu; producent: reverse external patch;
- brak migracji danych; format v1 pozostaje odrzucany;
- przed flipem: zastosować i niezależnie przetestować patch w repo mostu,
  dostarczyć ten sam losowy materiał HMAC do obu procesów jako pliki 0600,
  przejść review CTO i uzyskać ACK ownera na deploy/restart/flip.

rollback: `ENABLE_UWAGI_BRIDGE_NADAWCA=false`, następnie jawny `git revert`
commitów pakietu; w repo producenta reverse patch; brak migracji danych.

Obserwacja po flipie: co najmniej 2 pełne dni. GO dopiero gdy każda oczekiwana
koperta v2 ma `parsed=true` i `geocode_ok=true`, `central_fallback` pozostaje
zawsze false, brak `hmac_*`/nieznanych wersji dla danych producenta, brak bbox
rejectów wymagających korekty oraz brak wzrostu KOORD względem korpusu OFF.
