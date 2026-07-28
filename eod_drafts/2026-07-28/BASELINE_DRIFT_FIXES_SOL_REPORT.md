# Baseline drift fixes — raport wykonawcy Sol

Data: 2026-07-28
Worktree: `/root/worktrees/dispatch_v2/active/20260728-baseline-drift-fixes-cto`
Branch deklarowany przez CTO: `wt/baseline-drift-fixes-cto-20260728`
Base deklarowany przez CTO: `7a63f8008`

## Wynik

Naprawa źródłowa dwóch kontraktów repo jest w worktree. Zmiana współdzielonego
`courier_api` nie została wykonana poza sandboxem; pełny patch do zastosowania
przez CTO jest w sekcji `PATCH`.

## Diff per plik i uzasadnienie

| Plik | Zmiana | Uzasadnienie |
|---|---|---|
| `common.py` | `ENABLE_LOADGOV_SNAPSHOT_PUBLISH` dodana do `ETAP4_DECISION_FLAGS`; komentarz opisuje podwójną rolę rejestru | Żywe ON jest teraz wycinane z kopii flags w testach i widoczne w fingerprintcie; fallback pozostaje OFF, brak zmiany decyzji |
| `core/loadgov_publisher.py` | komentarz kontraktu zsynchronizowany z rejestrem | Usuwa sprzeczną instrukcję „poza ETAP4”; semantyka producenta bez zmian |
| `docs/G5_LOADGOV_SNAPSHOT.md` | opis G5 zsynchronizowany z technicznym pokryciem ETAP4 | Flaga nadal jest niedecyzyjna bez Alarm certificate, lecz już strippowana i fingerprintowana |
| `tools/flag_lifecycle_registry.json` | notatka istniejącego wpisu G5 zsynchronizowana | Rejestr lifecycle nie twierdzi już, że flaga jest poza ETAP4 |
| `tests/test_g5_loadgov_snapshot_producer.py` | ratchet odwrócony na nowy kontrakt: membership + obecność we fingerprintcie | Stary zielony test wymuszał stan będący przyczyną nowego baseline faila; mutation (usunięcie wpisu) ponownie czerwieni |
| `ZIOMEK_LOGIC_REFERENCE.md` | po 1 krótkim wpisie dla G5 i R4 operator expiry | To jest dokładny plik czytany przez `flag_doc_coverage_check.py`; baseline ratcheta nie został rozszerzony |
| `eod_drafts/2026-07-28/BASELINE_DRIFT_FIXES_SOL_REPORT.md` | ten raport + patch zewnętrzny | Trwały handoff do CTO bez zapisu poza worktree |

## Mapa kompletności

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód/test |
|---|---|---|---|---|
| `common.py:ETAP4_DECISION_FLAGS` | rejestr strip + fingerprint | owner listy | TAK | G5 dopisana; pięć nocnych bliźniaków zweryfikowane |
| `tests/conftest.py::_isolate_flags_json` | consumer rejestru | strip | N-D | już iteruje po całym ETAP4; bez duplikatu polityki |
| `common.py:flag_fingerprint` | consumer rejestru | serializer fingerprinta | N-D | już iteruje po całym ETAP4; test G5 sprawdza obecność tokenu |
| `tools/flag_doc_coverage_check.py` | checker dokumentacji | consumer ref | N-D | prawidłowo czyta `ZIOMEK_LOGIC_REFERENCE.md`; naprawiono source, nie checker |
| `tools/flag_doc_baseline.json` | zamrożony dług | ratchet baseline | N-D | zakaz ucieczki; obie flagi nie zostały dopisane |
| `tools/flag_lifecycle_registry.json` | lifecycle | consumer/operator | TAK | istniejący wpis G5 zsynchronizowany |
| `core/loadgov_publisher.py` | runtime consumer flagi | consumer | komentarz TAK, kod N-D | kod już czyta `decision_flag`; zachowanie runtime bez zmian |
| `docs/G5_LOADGOV_SNAPSHOT.md` | spec G5 | dokumentacja | TAK | usunięta sprzeczność kontraktu |
| `ZIOMEK_LOGIC_REFERENCE.md` | kanoniczny ref checkera | dokumentacja | TAK | obie brakujące flagi opisane |
| `courier_api/courier_orders.py` | biblioteczny DTO builder | writer stdout | PATCH | wszystkie 19 `print()` w module przeniesione do loggingu |
| `tools/route_order_live_parity_check.py` | JSON CLI | consumer stdout | N-D | narzędzie zachowuje czysty kontrakt; brak filtrowania/maskowania |

Pokrycie nocnych flag po zmianie:

- `ENABLE_LOADGOV_SNAPSHOT_PUBLISH` → `ETAP4_DECISION_FLAGS`
- `ENABLE_OPERATOR_AVAILABILITY_EXPIRY` → `ETAP4_DECISION_FLAGS`
- `ENABLE_LIVE_ETA_WARM_SOURCE` → `ETAP4_DECISION_FLAGS`
- `ENABLE_ASSIGNMENT_EPISODE_LOG` → `ETAP4_DECISION_FLAGS`
- `ENABLE_C7_NORMAL_PATH_LOG` → `ETAP4_DECISION_FLAGS`
- `ENABLE_LEX_WINDOW_LEDGER_V2` → `TEST_ISOLATED_INFRA_FLAGS` (celowo niedecyzyjna; ten sam skuteczny strip)

## Wpływ na `flag_fingerprint`

`common.flag_fingerprint()` składa wynik z
`ETAP4_DECISION_FLAGS + _FINGERPRINT_EXTRA_FLAGS`. Po zmianie każdy fingerprint
dostaje jeden nowy token:

```text
ENABLE_LOADGOV_SNAPSHOT_PUBLISH=<0|1>
```

Wartość pochodzi z tego samego `decision_flag()`/`flags.json`, co runtime
producenta. Kolejność pozostałych tokenów nie zmienia się; nowy token pojawia się
po `ENABLE_FLEET_LOAD_GOVERNOR`. Nie znaleziono testu ani goldena porównującego
pełny fingerprint bajt-w-bajt. Konsumenci parsują go jako mapę nazw/wartości albo
hashują cały aktualny tekst; hashe G5/WB1 zmienią się oczekiwanie po zmianie
składu i nie są traktowane jako stałe goldeny. Jest to bezpieczne i pożądane:
fingerprint zaczyna ujawniać efektywną konfigurację producenta.

## Bliźniacze `print()` w `courier_api`

Grep całego dostępnego drzewa wykazał:

- 19 `print()` w bibliotecznym `courier_orders.py`; wszystkie mogą zaśmiecić
  stdout procesu, a warunkowe ścieżki `build_view()` mogą zaśmiecić JSON CLI
  zależnie od danych/flag. Patch niżej przenosi wszystkie 19 do loggera, bez
  zmiany logiki, danych ani wyjątków.
- 2 `print()` w importowanym `earnings_history.py`. Narzędzie parity zastępuje
  `earnings_history.record_day` no-opem na czas realnego `build_view`, więc te
  writery nie są osiągalne w jego read-only smoke. Rekomendacja: osobna karta
  porządkowa dla library-stdout, nie poszerzać tego współdzielonego patcha.
- Pozostałe printy są w endpointach/workerach/CLI (`main.py`,
  `schedule_service.py`, `status_store.py`, agregatory i skrypty administracyjne).
  Nie są importowane przez parity tool w badanej ścieżce. Rekomendacja:
  ratchet AST dla modułów bibliotecznych importowanych przez JSON CLI; CLI mogą
  zachować stdout jako interfejs użytkownika.

## PATCH — poza worktree (`courier_api`)

Patch jest celowo ograniczony do mechanizmu emisji logów w jednym module. Nie
zmienia DTO, kolejności, flag, stanu ani konfiguracji i nie wymaga restartu w
ramach tego zadania. CTO powinien wykonać wymagany backup przed aplikacją.

```diff
diff --git a/courier_api/courier_orders.py b/courier_api/courier_orders.py
--- a/courier_api/courier_orders.py
+++ b/courier_api/courier_orders.py
@@ -17,6 +17,7 @@ import hashlib
 import hmac
 import itertools
 import json
+import logging
 import math
 import os
 import sys
@@ -34,1 +35,2 @@ import panel_lite
 import payment_override
+logger = logging.getLogger(__name__)
@@ -50,7 +53,7 @@ except Exception as _e:  # pragma: no cover
     _msg = (f"import route_podjazdy FAIL: {type(_e).__name__}: {_e} — apka na "
             f"LOKALNEJ kopii kolejności (_plan_stop_sequence), trasa może "
             f"rozjechać się z konsolą")
-    print(f"[console_podjazdy] {_msg}", flush=True)
+    logger.warning("[console_podjazdy] %s", _msg)
     try:
         import urllib.parse as _up
         import urllib.request as _ur
@@ -609,1 +612,3 @@ def _reorder_steps_to_canon(seq, mine, plan):
-        print(f"[route_order_unified] build_stop_sequence fail: {type(e).__name__}: {e}", flush=True)
+        logger.warning(
+            "[route_order_unified] build_stop_sequence fail: %s: %s",
+            type(e).__name__, e)
@@ -1037,5 +1042,6 @@ def build_view(courier_id: str) -> dict:
     try:
         overrides = payment_override.overrides_for_courier(courier_id)
     except Exception as e:
-        print(f"[pay_override] read fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+        logger.warning("[pay_override] read fail cid=%s: %s: %s",
+                       courier_id, type(e).__name__, e)
         overrides = {}
@@ -1073,1 +1078,2 @@ def build_view(courier_id: str) -> dict:
-            print(f"[console_podjazdy] build fail cid={courier_id}: {type(_e).__name__}: {_e}", flush=True)
+            logger.warning("[console_podjazdy] build fail cid=%s: %s: %s",
+                           courier_id, type(_e).__name__, _e)
@@ -1100,20 +1107,27 @@ def build_view(courier_id: str) -> dict:
                 try:
                     seq, _ro = _reorder_steps_to_canon(seq, mine, plan)
                     if _ro:
-                        print(f"[route_order_unified] cid={courier_id} plan steps reordered to canon", flush=True)
+                        logger.info(
+                            "[route_order_unified] cid=%s plan steps reordered to canon",
+                            courier_id)
                 except Exception as e:
-                    print(f"[route_order_unified] plan fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+                    logger.warning("[route_order_unified] plan fail cid=%s: %s: %s",
+                                   courier_id, type(e).__name__, e)
             elif config.PLAN_ORDER_INVARIANTS and not config.BUILD_VIEW_TRUST_CANON_ORDER:
                 try:
                     seq, _cf = _prioritize_carried_dropoffs(seq, mine)
                     if _cf:
-                        print(f"[plan_carried] cid={courier_id} carried dropoffs moved to front", flush=True)
+                        logger.info("[plan_carried] cid=%s carried dropoffs moved to front",
+                                    courier_id)
                 except Exception as e:
-                    print(f"[plan_carried] fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+                    logger.warning("[plan_carried] fail cid=%s: %s: %s",
+                                   courier_id, type(e).__name__, e)
                 try:
                     seq, _rp = _reorder_pickup_steps_by_committed(seq, mine)
                     if _rp:
-                        print(f"[plan_reorder] cid={courier_id} pickups re-sorted by committed time", flush=True)
+                        logger.info("[plan_reorder] cid=%s pickups re-sorted by committed time",
+                                    courier_id)
                 except Exception as e:
-                    print(f"[plan_reorder] fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+                    logger.warning("[plan_reorder] fail cid=%s: %s: %s",
+                                   courier_id, type(e).__name__, e)
             stop_sequence = seq
@@ -1136,24 +1150,31 @@ def build_view(courier_id: str) -> dict:
             try:
                 stop_sequence, _ro = _reorder_steps_to_canon(stop_sequence, mine, plan)
                 if _ro:
-                    print(f"[route_order_unified] cid={courier_id} fallback steps reordered to canon", flush=True)
+                    logger.info(
+                        "[route_order_unified] cid=%s fallback steps reordered to canon",
+                        courier_id)
             except Exception as e:
-                print(f"[route_order_unified] fallback fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+                logger.warning("[route_order_unified] fallback fail cid=%s: %s: %s",
+                               courier_id, type(e).__name__, e)
         else:
             # Już odebrane (picked_up) dowieź zanim zbierzesz nowe — optymalizator
             # geo nie wie, że jedzenie stygnie i potrafi wrzucić je na koniec.
             try:
                 stop_sequence, _carried_first = _prioritize_carried_dropoffs(stop_sequence, mine)
                 if _carried_first:
-                    print(f"[fallback_carried] cid={courier_id} carried dropoffs moved to front", flush=True)
+                    logger.info("[fallback_carried] cid=%s carried dropoffs moved to front",
+                                courier_id)
             except Exception as e:
-                print(f"[fallback_carried] fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+                logger.warning("[fallback_carried] fail cid=%s: %s: %s",
+                               courier_id, type(e).__name__, e)
             # Odbiory wg ustalonego czasu (committed), nie wg geografii — geo-NN
             # potrafi odwrócić kolejność dwóch odbiorów o różnych czasach panelu.
             try:
                 stop_sequence, _reordered = _reorder_pickups_by_committed(stop_sequence, mine)
                 if _reordered:
-                    print(f"[fallback_reorder] cid={courier_id} pickups re-sorted by committed time", flush=True)
+                    logger.info("[fallback_reorder] cid=%s pickups re-sorted by committed time",
+                                courier_id)
             except Exception as e:
-                print(f"[fallback_reorder] fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+                logger.warning("[fallback_reorder] fail cid=%s: %s: %s",
+                               courier_id, type(e).__name__, e)
         # Dwell jest wejściem do wspólnego kalkulatora; fallback nie nadaje ETA.
@@ -1180,1 +1200,3 @@ def build_view(courier_id: str) -> dict:
-                print(f"[committed_pickup] cid={courier_id} doklejono committed do {_m} odbiorow", flush=True)
+                logger.info(
+                    "[committed_pickup] cid=%s doklejono committed do %s odbiorow",
+                    courier_id, _m)
@@ -1182,1 +1204,2 @@ def build_view(courier_id: str) -> dict:
-            print(f"[committed_pickup] fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+            logger.warning("[committed_pickup] fail cid=%s: %s: %s",
+                           courier_id, type(e).__name__, e)
@@ -1206,1 +1229,2 @@ def build_view(courier_id: str) -> dict:
-        print(f"[earnings_history] record fail cid={courier_id}: {type(e).__name__}: {e}", flush=True)
+        logger.warning("[earnings_history] record fail cid=%s: %s: %s",
+                       courier_id, type(e).__name__, e)
```

## Walidacja lokalna

- `python3 -m py_compile common.py core/loadgov_publisher.py tools/flag_doc_coverage_check.py tests/test_g5_loadgov_snapshot_producer.py` → PASS.
- `python3 -m json.tool tools/flag_lifecycle_registry.json` → PASS.
- AST membership: wszystkie 6 nocnych flag pokryte, `ETAP4_DECISION_FLAGS` bez duplikatów.
- Obie dokumentowane flagi występują w `ZIOMEK_LOGIC_REFERENCE.md` i nie występują w `flag_doc_baseline.json`.
- Patch `courier_orders.py` → `patch --dry-run` PASS; wynik patcha → `compile()` PASS i 0 pozostałych wywołań `print(`.
- Nie uruchomiono pytest: zgodnie z kontraktem zadania sandbox nie ma venv/pkgroot, a gitdir worktree wskazuje na niemontowalną ścieżkę. Pełne 3 testy i regresję uruchamia CTO po aplikacji patcha zewnętrznego.
- `ziomek-cto dod` → oczekiwany STOP: brak dowodu pełnej regresji, E2E i replay/parytetu, które w podziale obowiązków wykonuje CTO. Nie ogłoszono DoD ani `0 failed`.

## Rollback

- Repo: cofnąć jawnie zmiany powyższych sześciu plików; nie ruszać baseline ratchetów.
- Courier API: odtworzyć backup `courier_orders.py.bak-*` CTO albo odwrócić wyłącznie diff loggingowy.
- Nie wykonano flipa, deployu, restartu, migracji ani zapisu runtime.
