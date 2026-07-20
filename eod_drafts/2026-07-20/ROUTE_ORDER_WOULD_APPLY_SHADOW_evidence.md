# Route-order override: compute-always `would_apply` do kanonicznego ledgera

Data: 2026-07-20. Katalog: `/root/cx-rt`. Model tier: `sol`; effort: `xhigh` —
zmiana przecina kanon planu, decyzję silnika i dwa serializery, a błąd mógłby
naruszyć parytet OFF lub semantykę ON.

## Root cause i zakres

`operator_route_override.pin_stops` już wykonywał pełny dry-run przy fladze OFF,
ale `would_apply=true` trafiało wyłącznie do osobnego
`operator_route_override_events.jsonl`. `shadow_decisions.jsonl` serializuje
`Candidate.metrics`, więc bramka oparta o kanoniczny ledger nie miała sygnału.

Zmiana:

- `operator_route_override.py`: opcjonalny output `shadow_metrics` korzysta z
  dokładnie tego samego odczytu, TTL, walidacji zbioru i `_build_pinned`, co ON;
  `shadow_metrics_for_route` uruchamia go bez zmiany stopów i bez duplikowania
  eventu dedykowanego;
- `dispatch_pipeline.py`: po finalnym firewallu projekcja `plan.sequence` na
  aktywny worek z `bag_context`, a następnie dopięcie czterech pól do metrics;
- `shadow_dispatcher.py`: N-D — istniejący wspólny deny-list helper automatycznie
  serializuje każdy klucz metrics w LOCATION A i B; parytet sprawdzony testem;
- `plan_recheck.py`: N-D — oba writery nadal wołają ten sam `pin_stops`; jego
  dotychczasowy kontrakt zwrotny i domyślne eventy pozostały bez zmian;
- dokumentacja flagi i nota lifecycle zaktualizowane.

Pola (wyłącznie po pełnej pozytywnej walidacji manual-seq):

- `route_order_would_apply` — `true`;
- `route_order_manual_seq` — deterministyczny skrót `id>id` (cap 12 pozycji,
  16 znaków/id; pełne listy służą do porównania);
- `route_order_engine_seq` — analogiczny skrót względnej kolejności aktywnego
  worka w planie kandydata;
- `route_order_divergence` — porównanie pełnych list.

Brak wpisu, wpis wygasły/niepoprawny, set mismatch albo niekompletny engine plan
= brak wszystkich czterech pól.

## Parytet i bliźniaki

| miejsce | rola | werdykt | dowód |
|---|---|---|---|
| `plan_recheck._gen_one_bag_plan` | writer ON/OFF | TAK, bez edycji | wspólny `pin_stops`; istniejące testy |
| `plan_recheck._retime_one_bag_plan` | writer ON/OFF | TAK, bez edycji | jw. |
| `dispatch_pipeline.assess_order` | producer metrics | TAK | hook po `_attach_final_rule_verdict` |
| serializer A alternatives | consumer | TAK | funkcjonalny test parytetu |
| serializer B best | consumer | TAK | funkcjonalny test parytetu |
| decyzja score/verdict/plan/winner | brak konsumpcji | N-D | snapshot przed/po + hook po firewallu |

N-D: auto_assign_gate.py — powód: route-order metrics są dopinane po finalnym
firewallu i nie są wejściem równego traktowania pozycji ani auto-assign.

N-D: core/selection.py — powód: selekcja kończy się przed producerem metryk;
nie ma odczytu żadnego z czterech nowych kluczy.

N-D: drive_min_calibration.py — powód: brak zmiany dystansu/ETA; sekwencje są
wyłącznie telemetrycznymi stringami.

N-D: tools/reassignment_forward_shadow.py — powód: reassignment nie konsumuje
manual route-order ani Candidate.metrics z bieżącej decyzji.

N-D: objm_lexr6.py — powód: bliźniak selekcji kończy ranking przed hookiem;
nowe pola nie uczestniczą w tie-breaku.

## Panel — niezależna weryfikacja

Aktualny panel w read-only klonie `/root/cx-konsola`, commit panelu `27bc152`:

- `panel/backend/app/core/flags.py`: default `ROUTE_REORDER=False`, env
  `PANEL_FLAG_ROUTE_REORDER`;
- `panel/backend/app/api/coordinator.py`: `_route_reorder_gate` zwraca 404 przy
  OFF przed GET/POST/DELETE;
- frontend `Ops13Console.tsx`: przy OFF nie odpytuje endpointu i nie pokazuje
  kontrolek;
- jedyny writer `route_override.set_override` jest osiągalny przez gated POST.

Wniosek: przy `PANEL_FLAG_ROUTE_REORDER=OFF` panel NIE zapisuje manual-seq.
Nowy cień naprawia transport istniejącej próbki do ledgera, ale sam nie tworzy
próbek. Organiczne okno cienia wymaga osobnej decyzji/ACK dla panelowej ścieżki
shadow albo kontrolowanego wpisu testowego; tego sprintu nie wykonano.

## Weryfikacja

- `python3 -m py_compile ...` — PASS;
- `git diff --check` — PASS;
- import przez pkgroot-symlink: `IMPORT_OK`, wszystkie moduły z `/root/cx-rt`;
- dotknięte node-idy: `6 passed in 0.79s`;
- operator + serializer A/B, wariant systemowy bez niedostępnych zależności:
  `42 passed, 8 deselected in 2.23s`;
- serializer completeness + LOCATION B: `10 passed in 1.39s`;
- `flag_lifecycle_check.py --skip-external`:
  `511/511`, `OK — 0 błędów`;
- zero nowych/usuniętych `def test_*`, więc manifest node-idów bez dryfu.

e2e: wspólny walidator `pin_stops` → producer po finalnym hooku `assess_order`
→ realny `Candidate.metrics` → `_serialize_candidate` (A) i `_serialize_result`
(B); wszystkie cztery wartości sprawdzone funkcjonalnie w tym samym teście.

pozytywny-wplyw: przy OFF ten sam ważny wpis przechodzi z 0 pól w kanonicznym
ledgerze do kompletu 4 pól (w tym `would_apply=true` i prawdziwej divergence),
przy identycznym score/verdict/reason/plan; ON nadal pinuje kolejność jak przed
zmianą i ma osobny test ON≠OFF.

regresja: HOLD — kanoniczna pełna suita nie została wykonana z powodu blokady
wykonania venv opisanej niżej; nie jest to deklaracja PASS.

Kanoniczna komenda wymagana przez ownera:

`/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q`

nie wystartowała w tym sandboxie: `Permission denied`, exit 126. Próba pełnej
suity systemowym Pythonem nie jest zamiennikiem i zatrzymała się w collection na
brakujących cross-root modułach/deps oraz zakazanych ścieżkach. Bazeline
Przekazany przez ownera baseline (5309 zielonych, zero czerwonych) nie jest
niezależnie odtworzonym dowodem tej sesji. Status pełnej regresji: HOLD do
uruchomienia poza sandboxem.

## Ryzyka i rollback

- Bez manual-seq (panel OFF) w ledgerze nadal będzie zero pól — to zależność
  danych wejściowych, nie błąd transportu.
- Shadow robi jeden cache'owany odczyt/`stat` pliku override per kandydat z
  niepustym workiem i kompletnym planem; parse jest cache'owany po mtime/size.
- Syntetyczny dry-run używa dropoff order z `RoutePlanV2.sequence`; pełna
  semantyka pickup/dropoff ON pozostaje w istniejącym plan_recheck i nie została
  zmieniona.
- Brak deployu, restartu, flipu i zmian żywych danych/flag.
- Rollback kodu: revert jednego przyszłego commita; operacyjny kill-switch ON
  pozostaje `ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE=false` (już OFF).

rollback: brak commita/deployu w tej sesji; wycofanie diffu albo `git revert`
przyszłego pojedynczego commita. Po wdrożeniu zachowanie ON wyłącza hot flaga
`ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE=false`; nowe pola telemetryczne usuwa
revert kodu bez migracji danych i bez kolejności restartów cross-service.
