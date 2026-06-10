# ETAP 4 — INWENTARYZACJA FLAG CROSS-PROCES (KROK 1, do ACK Adriana)

Data: 2026-06-10 (sesja wieczór, ETAP 4 planu po audycie — finding Z-04).
Źródła odczytu: `systemctl cat` wszystkich unitów dispatch-* (main + drop-iny), `flags.json` live, defaulty `common.py` / `plan_recheck.py` / `panel_client.py` / `auto_proximity_classifier.py`, call-sites grep.

**KROK 0 (push):** NIEAKTUALNY — master == origin/master (ahead 0; sesje E2/E3 wypchnęły wieczorem, HEAD `4e83767`). Dirty tylko `restaurant_company_mapping.json` (runtime, nie commitujemy).

## Mechanika rozjazdu (potwierdzenie Z-04)

- Wszystkie flagi z override.conf shadow są w `common.py` jako **module-level env** (`_os.environ.get(...)` w czasie importu). Konsumenci czytają `C.ENABLE_X` lub `getattr(C, "ENABLE_X", ...)` — **oba style czytają atrybut modułu zamrożony przy imporcie**, więc wartość = env procesu w chwili startu.
- **dispatch-czasowka woła `assess_order`** (`czasowka_scheduler.py:26,341`) = pełny silnik (pipeline+feasibility+resolver+scoring+OR-Tools) — z defaultami common.py, bo jej unit ma TYLKO `CZASOWKA_TELEGRAM_DRYRUN/RETROACTIVE_HOURS/MAX_EMIT_PER_TICK`. Jej KOORD alerty idą LIVE do Telegrama (DRYRUN=0) → rozjazd silnika jest widoczny operacyjnie.
- **dispatch-plan-recheck NIE woła assess_order** — woła `simulate_bag_route_v2` (re-plan worka). Dotykają go flagi OBJ/route_simulator (ma już R6_SOFT_DEADLINE+SPAN_COST w drop-inie `objective-alignment.conf`) + feasibility pośrednio nie; flagi pipeline'owe (R1/A2/commit-div...) są dla niego inertne, ALE fingerprint i tak powinien być identyczny (koszt zerowy, strażnik na przyszłość gdyby zaczął wołać silnik).
- **dispatch-panel-watcher** nie ma silnika scoringu (brak assess_order/simulate) — flagi decyzyjne inertne; ma za to grupę (c) plan-machinery + parser.
- **dispatch-telegram**: czyta tylko `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT` (render markera) — env brak → default 0 = zgodny z shadow. Migracja do flags.json nic nie zmienia (default startowy = ta sama wartość). NIE restartujemy.

## TABELA GŁÓWNA — flagi z unitów shadow (override.conf + etap2-flip.conf + main unit)

Wartości live per proces. `def` = default z common.py (env nieustawiony). Pogrubienie = rozjazd vs shadow.

| Flaga | default common.py | shadow | panel-watcher | czasowka | plan-recheck | Klasa | Propozycja |
|---|---|---|---|---|---|---|---|
| ENABLE_BUNDLE_DELIV_SPREAD_CAP | 0 | **1** | 0 (inert) | **0** | 0 (inert) | **(a)** | flags.json `true` |
| ENABLE_R1_PROGRESSIVE_CLIP | 0 | **1** | 0 (inert) | **0** | 0 (inert) | **(a)** | flags.json `true` |
| ENABLE_V319H_CONTINUATION_GUARD | 0 | **1** | 0 (inert) | **0** | 0 (inert) | **(a)** | flags.json `true` |
| ENABLE_A2_RELIABILITY_SOFT_SCORE | 0 | **1** | 0 (inert) | **0** | 0 (inert) | **(a)** | flags.json `true` |
| A2_RELIABILITY_COEFF | 60 | 60 | 60 | 60 | 60 | (a)-param | **nic** (override=default; po przeniesieniu flagi linię z unitu usunąć) |
| ENABLE_FAIL12_SCHEDULE_FAILOPEN | 0 | **1** | 0 (inert) | **0** | 0 (inert) | **(a)** | flags.json `true` |
| ENABLE_F4_COURIER_POS_PICKUP_PROXY | 0 | **1** | 0 | **0** | 0 | **(a)** | flags.json `true` |
| ENABLE_F4_COURIER_POS_INTERP | 0 | **1** | 0 | **0** | 0 | **(a)** | flags.json `true` |
| ENABLE_C2_NEG_GAP_DECAY | 0 | **1** | 0 (inert) | **0** | 0 (inert) | **(a)** | flags.json `true` |
| ENABLE_PRE_SHIFT_DEPARTURE_CLAMP (etap2-flip.conf) | 0 | **1** | 0 | **0** | **0** (feasibility+route_sim!) | **(a)** | flags.json `true`; drop-in etap2-flip.conf usunąć |
| ENABLE_OBJ_SPAN_COST | 0 | **1** | 0 (inert) | **0** | 1 (drop-in) | **(a)** | flags.json `true` |
| OBJ_SPAN_COST_COEFF | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | (a)-param | nic (override=default; linie z unitów usunąć) |
| ENABLE_OBJ_R6_SOFT_DEADLINE (MAIN unit shadow; kanon per R6FRESH-DUP-CONFIG-01) | 0 | **1** | 0 (inert) | **0** | 1 (drop-in) | **(a)** | flags.json `true`; **kanon przenosi się z main unitu do flags.json** — zaktualizować komentarz R6FRESH-DUP-CONFIG-01 w override.conf i usunąć linie z main unitu + objective-alignment.conf |
| OBJ_R6_DEADLINE_PENALTY_COEFF | 100 | 100 | 100 | 100 | 100 | (a)-param | nic (override=default; linie z unitów usunąć) |
| ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD | 0 | **1** | 0 (inert) | **0** | 0 (inert) | **(a)** | flags.json `true` |
| ENABLE_OBJ_PICKUP_FRESHNESS | 0 | 0 (linia zakomentowana 06-08) | 0 | 0 | 0 | (a) | **już spójna** (wszędzie OFF); flags.json `false` jawnie + do fingerprinta (dokumentacja FRESH-SANITY-DATE-01 w override zostaje jako komentarz) |
| ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE | **1 (!)** | 0 (ALWAYS-PROPOSE 06-01) | **1** (inert) | **1 — ON wbrew dyrektywie** | **1** (inert) | **(a)** ⚠ NAJPILNIEJSZA | flags.json `false` — czasówka dziś ma gate ON wbrew [[feedback-always-propose-defer-pickup]]; to jedyny rozjazd „w złą stronę" |
| ENABLE_DIFFICULT_CASE_KOORD_REDIRECT | 0 | 0 | 0 | 0 | 0 | (a) | **już spójna**; flags.json `false` jawnie + fingerprint (telegram czyta render-marker — bez zmian) |
| ENABLE_SELECTION_VETO_SHADOW | 0 | **1** | 0 | 0 | 0 | **(b)** | **zostaje shadow-only** (counterfactual log — mnożenie zapisów z czasówki zaszumiłoby analizę dialów) |
| ENABLE_LGBM_SHADOW | 0 | **1** | 0 | 0 | 0 | **(b)** | zostaje shadow-only (LGBM = cień; korpus porównawczy ma być z jednego strumienia decyzji) |
| ENABLE_LGBM_METRICS_READ | 0 | **1** | 0 | 0 | 0 | **(b)** | zostaje shadow-only (para z LGBM_SHADOW) |
| ENABLE_PENDING_POOL | 0 | **1** | 0 | 0 | 0 | **(b)** | zostaje shadow-only — konsument WYŁĄCZNIE `shadow_dispatcher.py:1024` (+sweeper ma własny unit); w czasówce kod nieosiągalny |
| ENABLE_OBJ_REPLAY_CAPTURE (MAIN unit) | 0 | **1** | 0 | 0 | 0 | **(b)** | zostaje shadow-only — korpus replay (`obj_replay_capture.jsonl`) celowo = decyzje shadow; dopisywanie czasówek/recheck zmieniłoby skład korpusu kalibracyjnego (wszystkie dotychczasowe werdykty replay liczone na shadow-only) |
| ENABLE_PANEL_BG_REFRESH | 0 (`!= "0"` w panel_client.py) | **1** | **0** | 0 | 0 | **(c)** | zostaje w unitach — ZAMIERZONE: bg-refresh tylko w shadow (hot path latency); watcher ma własny cykl loginu |

## Flagi już ZUNIFIKOWANE wzorcem docelowym (E2, runtime `C.flag(name, default=env-const)`) — nic do zrobienia poza fingerprint

| Flaga | default | wszędzie | Wzorzec |
|---|---|---|---|
| ENABLE_V327_MULT_SIGN_GUARD | ON | ON | `dispatch_pipeline.py:2160` runtime flag() |
| ENABLE_FAIL12_STOREPOS_STRICT | ON | ON | `feasibility_v2.py:321` runtime flag() |
| ENABLE_F7_MARGIN_FINAL_RANKING | ON | ON | `auto_proximity_classifier.py:110-114` runtime |
| ENABLE_V328_HEURISTIC_SHIFT_END_GUARD | ON | ON | env default ON + kill-switch flags.json |
| ENABLE_WORKING_OVERRIDE_GRAFIK_CAP | ON | ON | wzorzec referencyjny z planu |
| flagi flags.json (EXCLUDE_BY_CID, COURIER_LAST_KNOWN_POS, GRAFIK_FULL_NAMES_SOURCE, PARSER_DEGRADED, …) | — | hot-reload cross-proces | już wspólne przez `C.flag()` |

## Grupa (c) — CELOWO PER-PROCES (zostają w unitach, komentarz „dlaczego" dopisać przy sprzątaniu)

| Flaga | Gdzie | Dlaczego per-proces |
|---|---|---|
| ENABLE_PANEL_BG_REFRESH | shadow=1 / panel-watcher=0 | bg-refresh CSRF tylko w hot path shadow (V3.27.7); watcher loguje się własnym cyklem |
| USE_V2_PARSER=1 | panel-watcher | parser HTML panelu — tylko proces parsujący |
| ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE=1 | panel-watcher | reakcja na PANEL_OVERRIDE — mechanika watchera (const w `plan_recheck.py:352`, importowana) |
| ENABLE_GPS_FREE_ANCHOR=1 | panel-watcher + plan-recheck | plan-machinery (`plan_recheck.py:333`); oba procesy które jej używają już spójne; shadow nie używa |
| ENABLE_PLAN_REAL_PICKED_UP_AT=1 | panel-watcher + plan-recheck | jw. — spójne między konsumentami |
| ENABLE_PLAN_SEQUENCE_LOCK=1 | plan-recheck | tylko logika rechecku planu |
| ENABLE_PLAN_CANON_ORDER_INVARIANTS=1 | panel-watcher + plan-recheck | jw. — spójne |
| CZASOWKA_TELEGRAM_DRYRUN=0 / RETROACTIVE_HOURS=2 / MAX_EMIT_PER_TICK=3 | czasowka | parametry operacyjne procesu, nie silnika |

(Plan-machinery można w przyszłości też przenieść do flags.json dla jednolitości, ale NIE w tym etapie — zero rozjazdu, zero zysku, ryzyko niepotrzebne.)

## Podsumowanie liczbowe rozjazdu (stan na 2026-06-10)

- **13 flag decyzyjnych (a)** do przeniesienia do flags.json: 12× czasówka liczy z OFF tym, co shadow ma ON, + 1× czasówka ma ON to, co shadow ma OFF (**commit_divergence gate — wbrew ALWAYS-PROPOSE**).
- 4 parametry-koeficjenty (A2=60, SPAN=1.0, R6=100, plus progi) — override == default ⇒ **zero zmiany wartości**, tylko sprzątnięcie linii z unitów.
- 5 flag telemetrii (b) zostaje w unitach shadow (REPLAY_CAPTURE, LGBM×2, SELECTION_VETO, PENDING_POOL).
- 8 wpisów grupy (c) zostaje per-proces z komentarzem.

## KROK 2 — mechanizm (do wykonania PO ACK)

Wzorzec per flaga (sprawdzony w E2, `feasibility_v2.py:316-324`):
1. `common.py`: const zostaje jako **env-default** (bez zmiany), call-sites przechodzą na `C.flag("ENABLE_X", default=C.ENABLE_X)` (flags.json → env → default). Tam gdzie call-site'ów wiele/hot-path — mały helper `_x_on()` per moduł (wzór `_f7_margin_final_ranking_on`).
2. Wpisy startowe w flags.json = **dokładnie dzisiejsze wartości shadow** (kolumna „Propozycja"): zero zmiany zachowania shadow; czasowka/plan-recheck DOGANIAJĄ.
3. Osobny commit per spójna paczka call-sites (grep każdej flagi przed edycją; część czyta `C.X`, część `getattr(C,...)`).
4. Bezpieczeństwo zapisu flags.json przez testy: załatwione w E1 (L1 guard PYTEST_CURRENT_TEST + L2 conftest `_isolate_flags_json`) — przenoszenie flag decyzyjnych do flags.json jest po E1 bezpieczne.

## KROK 3 — fingerprint

Log 1 linii przy starcie procesu + na żądanie: wszystkie flagi (a) + zunifikowane E2, rozwiązane ścieżką runtime (flags.json→env→default), np. `FLAG_FINGERPRINT proc=czasowka sha=<hash> ENABLE_R1_PROGRESSIVE_CLIP=1 ...` w shadow_dispatcher / czasowka_scheduler / plan_recheck (+ panel_watcher za darmo). Werdykt wdrożenia: fingerprinty shadow vs czasowka vs plan-recheck **IDENTYCZNE**.

## KROK 4 — sprzątanie unitów (po commitach + testach)

- override.conf shadow: usunąć 13 linii flag (a) + 4 koeficjenty; komentarze-rationale ZOSTAJĄ (historia decyzji) z dopiskiem „przeniesione do flags.json 2026-06-10 (ETAP 4)". Zostają: PANEL_BG_REFRESH (c) + 5×(b).
- main unit shadow: usunąć OBJ_REPLAY_CAPTURE? **NIE** — (b), zostaje. Usunąć ENABLE_OBJ_R6_SOFT_DEADLINE+COEFF (kanon → flags.json).
- etap2-flip.conf: usunąć cały drop-in.
- plan-recheck objective-alignment.conf: usunąć R6/SPAN (przejmuje flags.json); GPS_FREE_ANCHOR/PLAN_* zostają (c).
- `cp .bak` KAŻDEGO ruszanego pliku unitu; daemon-reload; restart: dispatch-shadow + dispatch-panel-watcher (skoordynować z sesją ETAPU 6 / sla_tracker); czasowka+plan-recheck = oneshot timery, wciągną same. dispatch-telegram NIE RUSZAĆ.

## Ryzyka / uwagi

1. **Kolejność wdrożenia ma znaczenie:** najpierw kod (runtime-read) + wpisy flags.json, POTEM usunięcie env z unitów. W oknie przejściowym env nadal działa jako default-fallback — wartości identyczne, zero okna rozjazdu.
2. flags.json edytowany ręcznie/hot-reload — literówka w nazwie = cichy fallback do env-default. Mitygacja: fingerprint (KROK 3) + wpisy startowe kopiowane skryptem, nie ręcznie.
3. Czasówka po zmianie zacznie liczyć nowym silnikiem → **spodziewana zmiana zachowania czasówek** (to jest CEL, prerequisite E5): metryki fail12_*/a2_reliability_delta itd. pojawią się w czasowka_eval_log; commit_divergence gate w czasówce gaśnie (zgodnie z dyrektywą).
4. Werdykt-gate'y (commit_div/difficult_case) shadow trzyma OFF — wpis flags.json `false` utrwala dyrektywę ALWAYS-PROPOSE cross-proces i naprawia doc-drift Z-12.

---
**STATUS: CZEKA NA ACK ADRIANA** (klasyfikacja + wartości startowe). Po ACK: KROK 2 (kod) → KROK 3 (fingerprint) → testy/replay → KROK 4 (unity+restarty) → walidacja.
