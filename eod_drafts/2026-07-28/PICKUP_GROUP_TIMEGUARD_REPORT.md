# Pickup group timeguard — raport wykonawczy Sol

Data: 2026-07-28

Gate: `engine.pickup-group-time-spread-guard`

Zakres: source-only, bez deployu, restartu, flag i modyfikacji runtime

## Wynik

Root cause został usunięty w kanonicznym `dispatch_v2.route_order`:

- `PICKUP_MERGE_MIN=10` pozostaje bez zmiany;
- klaster plan-aware zawsze przechodzi guard wewnętrznego rozrzutu committed;
- `<=10 min` nadal grupuje, `>10 min` zawsze rozdziela;
- stop ma `stop_id` i `order_ids`, nigdy jeden prezentowany czas;
- każdy krok pickup niesie dokładny `committed_at` z własnego zlecenia;
- `live_eta` konsumuje tożsamość stopu, a koordynaty służą tylko trasowaniu;
- wspólne `latest_ready` nadal wyznacza wyjazd/ETA fizycznego stopu, bez
  nadpisania committed zleceń.

Panel backend i courier-api są poza zapisywalnym worktree. Gotowe, oparte na
snapshotach CURRENT patche są w `patches/`.

## Diff dispatch_v2

Kod i kontrakt:

- `route_order.py`
- `route_podjazdy.py`
- `live_eta.py`
- `live_eta_daemon.py`
- `tools/route_order_live_parity_check.py`
- `tools/route_order_golden_corpus_gen.py`

Oracle i regresja:

- `tests/golden/route_order_corpus.json`
- `tests/test_pickup_group_timeguard.py`
- `tests/test_route_podjazdy_plan_aware.py`
- `tests/test_route_podjazdy_trust_canon.py` (uruchomiony, bez zmiany)
- `tests/test_route_order_unify_s30.py`
- `tests/test_route_order_golden.py`
- `tests/test_route_order_live_parity.py`
- `tests/test_live_eta_single_source.py`
- `tests/test_live_eta_coverage_r3.py`

Dokumentacja i handoff:

- `docs/decisions/ADR-010-stop-odbioru-i-czas-per-zlecenie.md`
- `docs/ROUTE_ORDER_PARITY_MONITOR_SYSTEMD_SPEC.md`
- `docs/ROUTE_ORDER_PARITY_MONITOR_REPORT_2026-07-28.md`
- `ZIOMEK_BACKLOG.md`
- ten raport i plik DoD evidence.

Pełny `git diff` musi wygenerować CTO, ponieważ gitdir tego worktree jest poza
sandboxem. Lista wyżej rozróżnia jawnie pliki zmienione od jednego uruchomionego
bez zmiany.

## ETAP 0 — negatywny oracle

Zamrożony worek 492:

| order | restauracja | committed |
|---|---|---|
| 490836 | Grill Kebab | `2026-07-28T21:26:00+02:00` |
| 490832 | Grill Kebab | `2026-07-28T21:52:00+02:00` |

Plan ma kolejne pickupy obu zleceń. Stan sprzed fixu:

`pickup_runs = [["490836", "490832"]]`

czyli jeden pickup z floorem 21:52. Rozrzut wynosi 26 minut, więc przekracza
kanoniczne 10 minut. Po fixie wynik to dwa stopy:

`pickup:490836 -> 21:26` oraz `pickup:490832 -> 21:52`.

Ledger zawiera starszą notę projektową z kandydatem `T=5`. Jest sprzeczna z
najnowszą decyzją ownera z 28.07; implementacja świadomie pinuje `T=10`.
CTO powinien dołączyć ten dowód do kolejnej tranzycji gate'a.

## Mapa kompletności

| miejsce | rola | writer/consumer | status | uzasadnienie |
|---|---|---|---|---|
| `route_order.py` | kanon membershipu | writer | TAK | jedyny próg, guard plan-aware i fallback, `stop_id`, committed per order |
| `route_podjazdy.py` | kompatybilny alias | consumer/re-export | TAK | eksportuje nowe API bez drugiej implementacji |
| `live_eta_daemon.py` | budowa wejścia ETA | consumer | TAK | konsumuje `build_route_stops`, przekazuje `stop_id` i membership |
| `live_eta.py` | kalkulator ETA | consumer/writer snapshotu | TAK | nie scala po coord; latest-ready dotyczy ETA, nie prezentowanego committed |
| `plan_recheck.py` | kanon planu/revisit | writer planu | N-D | przesuwa węzły, ale nie tworzy DTO ani czasu grupy; końcowy guard jest w ownerze `route_order` |
| `route_simulator_v2.py` super-pickup | feasibility/ETA silnika | writer planu | N-D | osobny, flagowany kontrakt grupowania z własnym ostrzejszym limitem 5 min; nie prezentuje committed i nie omija guardu 10 |
| panel `fleet_state.py` | DTO konsoli | consumer | PATCH | usuwa martwy konkurencyjny writer i deleguje do `build_route_stops`; serializuje `committed_by_order` |
| `courier_orders.build_view` | DTO apki | consumer | PATCH | propaguje `stop_id`, membership, `committed_at` kroku i `pickup_committed_at` karty |
| `route_order_live_parity_check.py` | monitor E2E | consumer | TAK | konsumuje backendowy stop contract; brak własnego progu/nazwy/coord grouping |
| corpus route-order | golden | oracle | TAK | dodane far 21:26/21:52 i close 21:26/21:34 |
| Android `RouteLogic`/`RouteScreen` | renderer/confirm | consumer | N-D WB3-F1 | backend contract gotowy; bulk-confirm różnych `stop_id` wymaga osobnego APK |

## Oracle, mutation i ratchet

- Golden 490836/490832: dwa pickupy, dokładne committed 21:26 i 21:52.
- Legalna grupa 21:26/21:34: jeden `stop_id`, dwa order IDs, dwa dokładne
  committed.
- Oracle nadrzędny porównuje committed kroku z wejściem bajt-w-bajt.
- Mutacja usuwająca spread guard ponownie skleja 26-minutowy przypadek i
  czerwieni.
- Mutacje podstawiające minimum albo maksimum grupy czerwienią oracle
  per-order committed.
- Ratchet sprawdza pełny spread grupy (nie tylko sąsiednią różnicę), brak coord
  w `stop_id`/membershipie i brak agregatora czasu w builderze prezentacji.
- Dwa różne `stop_id` przy tych samych koordynatach pozostają dwoma stopami ETA.
- Monitor daje `BROKEN` po zmianie membershipu lub committed i `OK` dla
  poprawnego backendowego kontraktu.

## Dowody wykonane w sandboxie

1. `py_compile` wszystkich zmienionych modułów i testów: PASS.
2. JSON corpus: poprawny.
3. Wszystkie testy plików route-order/podjazdy/live-ETA + timeguard,
   `HERMETIC_STRICT=1`: `77 passed, 1 skipped, 0 failed`.
4. Skip: wyłącznie live-parity smoke, bo sandbox nie udostępnia panelowego
   venv/runtime; nie jest traktowany jako OK produkcji.
5. `tools/flag_lifecycle_check.py`: PASS, 0 błędów; panel/apka SKIP z powodu
   niedostępnych repo.
6. `tools/entropy_dashboard.py`: exit 0, ale widział 0 plików live, więc wynik
   jest tylko strukturalny, nie pomiarem produkcji.
7. Oba patche cross-repo przechodzą `patch --dry-run`, pełną aplikację na
   snapshotach CURRENT i `py_compile`. `ziomek-cto dod` dla każdego:
   mechaniczny PASS. To nie zastępuje DoD całego atomowego zestawu.
8. `git status/diff/check`: N-D; gitdir worktree wskazuje na niedostępny
   `/root/.openclaw/.../.git/worktrees/...`. CTO wykonuje bramkę z hosta.
9. Pełna kanoniczna suita i realny DTO panel/courier-api: N-D zgodnie ze
   zleceniem; wykonuje CTO po zastosowaniu obu patchy.
10. Globalne `todo_master.md`, `sprint_timeline.md` i ledger są read-only poza
    sandboxem. Wygenerowano checkpoint:
    `/tmp/codex_handoff_2026-07-28_2015_pickup_group_timeguard.md`; CTO ma
    przepisać go do kanonicznej pamięci i wykonać tranzycję gate'a z SHA/hash.

## PATCH

- panel backend:
  `patches/PICKUP_GROUP_TIMEGUARD_PANEL.patch`
- courier-api:
  `patches/PICKUP_GROUP_TIMEGUARD_COURIER_API.patch`

Patche bazują odpowiednio na:

- `/root/handover/fleet_state.py.CURRENT`;
- `/root/handover/courier_orders.py.CURRENT`.

Po aplikacji trzeba dołożyć w repo panelu i courier-api testy ich realnych
serializerów na oba przypadki corpus. Dispatchowy monitor jest fail-closed:
brak nowych pól DTO nie da fałszywego OK.

## DEPLOY-CHECKLIST — wymaga osobnego ACK ownera i okna poza peakiem

1. CTO: zastosować oba patche do najnowszych HEAD, rozwiązać semantycznie
   ewentualny dryf i sprawdzić brak cudzych zmian.
2. Uruchomić `py_compile`, import check, focused testy trzech repo,
   `HERMETIC_STRICT=1` oraz pełną kanoniczną suitę dispatchu.
3. Uruchomić realny replay 490836/490832 przez:
   `route_order -> live_eta -> fleet_state` oraz
   `route_order -> courier_orders DTO -> monitor projection`.
4. Potwierdzić odpowiedź konsoli i `/orders`:
   osobne `stop_id` dla 21:26/21:52; dla close-group wspólny `stop_id`, ale dwa
   różne committed.
5. Przygotować backup źródeł i punkty rewertu w każdym repo.
6. Po ACK ownera wdrożyć kod dispatchu i wykonać dokładnie jeden kontrolowany
   restart `dispatch-live-eta.service`; sprawdzić PID, `NRestarts`, health i
   świeży snapshot schema/stop IDs.
7. Wdrożyć panel backend i wykonać dokładnie jeden restart
   `nadajesz-panel.service`; smoke konsoli na obu goldenach.
8. Wdrożyć courier-api i wykonać dokładnie jeden restart
   `courier-api.service`; smoke `/orders`, PID, `NRestarts`, logi i monitor
   parytetu. Kolejność 6 -> 7 -> 8 zapobiega konsumentom widzącym nowy kontrakt
   przed producentem.
9. Nie restartować `dispatch-telegram`. Nie ma zmiany flag ani migracji danych.
10. Android/APK nie wchodzi do tego deployu. WB3-F1 osobno: RouteLogic/RouteScreen
    mają grupować i bulk-confirmować wyłącznie po backendowym `stop_id`;
    różne/null `stop_id` znaczą „nie potwierdzaj grupowo”, potem miękki release
    APK bez `--force`.
11. Po dwóch przebiegach monitora przejść gate z dowodami SHA/hash; nie zamykać
    na podstawie samego review.

## Rollback

Rewert jawnych commitów w odwrotnej kolejności: courier-api, panel, dispatch;
następnie po osobnym ACK po jednym restarcie odpowiednich usług i smoke
snapshotu/DTO. Brak migracji, flagi i zmiany danych runtime. Rollback ma
przywrócić cały atomowy zestaw, nigdy mieszany kontrakt; przywraca też znany
defekt, więc jest wyłącznie awaryjny i pozostawia gate w `HOLD`.
