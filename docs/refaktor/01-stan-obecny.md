# 01 — Stan obecny (Faza 1: rekonesans z dowodami, bez oceniania)

**Data:** 2026-07-06 · **HEAD badany:** `fcf1342` (master == origin/master) · **Metoda:** 5 subagentów read-only (raporty surowe: `raw/01a-e2e.md`, `raw/01b-rdzen.md`, `raw/01c-sprzezenia.md`, `raw/01d-wspolbieznosc.md`, `raw/01e-samouczenie.md`) + weryfikacja własna agenta głównego (kanon `ZIOMEK_ARCHITECTURE.md`/`ZIOMEK_INVARIANTS.md`/`docs/ARCHITECTURE.md`/`docs/CODEMAP.md` + żywy `flags.json`). Zero zmian w systemie.

**Konwencja:** FAKT = zweryfikowane `plik:linia` na HEAD (linie dryfują — grepuj symbol). HIPOTEZA = oznaczona jawnie, z metodą weryfikacji (§7). Startowano z istniejących audytów (docs/audyt 03.07, deep-audit 27.06, Faza 1 spójności 30.06) — ich tezy WERYFIKOWANO, nie powtarzano; korekty w §6.

---

## 1. Przepływ krytyczny end-to-end (pełna tabela + linie → `raw/01a`)

**Ścieżka happy-path (5 procesów, 3 repa):**

```
[gastro.nadajesz.pl HTML] → poll → dispatch-panel-watcher (panel_watcher.tick:2441 → _diff_and_emit:1106)
  → orders_state.json (state_machine.update_from_event:612, fcntl) + event NEW_ORDER (events.db)
→ dispatch-shadow (shadow_dispatcher._tick:1107) → snapshot floty (courier_resolver.dispatchable_fleet:1511)
  → dispatch_pipeline.assess_order:3565 → _assess_order_impl:3629 (10 warstw, HARD→SOFT→selekcja→werdykt)
  → _serialize_result:478 → shadow_decisions.jsonl (single-writer) + pending_proposals.json (fcntl store)
→ KONSOLA (repo nadajesz_clone/panel, :8000) — koordynator 1-klik → assign.py:63 → subprocess scripts/gastro_assign.py:215
  → POST do gastro (przypisanie + czas_kuriera)
→ dispatch-panel-watcher wykrywa COURIER_ASSIGNED → _save_plan_on_assign_signal:641 + recanon_courier + redecide_courier
  → courier_plans.json (plan_manager, fcntl+atomic)
→ ETA: dispatch-plan-recheck (timer 5 min, run_recheck:2451, _apply_canon_order_invariants:1739)
  + apka (courier-api :8767, frozen pickup _attach_fallback_eta:846) + konsola (fleet_state._build_route:395)
→ pickup/deliver/cancel → 4 handlery recanon w panel_watcher (:641/:668/:703/:731)
```

Odgałęzienia: czasówka (osobny timer 1 min, ten sam `assess_order`), best_effort (0 feasible → `_best_effort_sort_key:610`, always-propose), KOORD (6 bramek, jedyna persystencja = shadow log; Telegram uśpiony od 26.06), paczki (parcel_lane_merge co 30 s emituje NEW_ORDER → ten sam pipeline).

**Fakt architektoniczny #1:** pętla domyka się PRZEZ ZEWNĘTRZNY panel gastro (HTML poll → POST → HTML poll) — silnik nie ma własnej transakcji przypisania; „prawda o przypisaniu" należy do gastro, a panel-watcher ją re-importuje.

## 2. Rdzeń decyzyjny (szczegóły → `raw/01b`)

- **Fizyczne miejsce decyzji:** `_assess_order_impl` (`dispatch_pipeline.py:3629`) — monolityczna funkcja **~3785 linii** (największa w repo; plik 7414 l.). Ocena kandydata = `_v327_eval_courier_inner` (~2145 l.) w `ThreadPoolExecutor` po ≤10 kurierach. HARD przed SOFT zachowane logicznie (feasibility `:4108` przed scoringiem `:4303` per kandydat; strażnik `_assert_feasibility_first:6320` — 1 call-site).
- **Warstwy 5-8 kanonu nie istnieją jako moduły** — są przeplecione wewnątrz jednej funkcji (selekcja `:6287-6388`, werdykt-bramki `:6799-7405`).
- **Determinizm:** `now` jest wstrzykiwane (dobrze), `random` brak, tie-breaki stabilne. Niedeterminizm wnoszą: **(a) flagi czytane z dysku ~700×/decyzję** (`common.load_flags`, `common.py:54-77` — zmiana flags.json w środku ticku zmienia zachowanie między kandydatami), **(b) OSRM na żywo** (route_simulator `:405/:638`, pipeline `:4077/:4321` + cache TTL + circuit-breaker na `time.time()`), **(c) żywy HTTP fetch panelu w środku oceny** (`:3913`, za flagą), **(d) zapisy shadow-logów i load-governor (plik+Telegram) wewnątrz assess** (`feasibility_v2.py:368/409`, `dispatch_pipeline.py:3694-3706`). **Frozen-clock/replay-provider NIE istnieje.**
- **Co już jest czyste (fundament pod F-2):** `scoring.score_candidate` (zero I/O), `route_simulator_v2`+`tsp_solver`+`chain_eta` (zero I/O poza wstrzykiwanym OSRM-callbackiem), `objm_lexr6` (pure), pozycja kuriera wchodzi argumentem ze snapshotu. `check_feasibility_v2` dostaje cały stan argumentami — brudne tylko wnętrze (flagi+OSRM+shadow-writy). **Wniosek agenta rdzenia: wstrzyknięcie 3 wejść (macierz OSRM, snapshot flag raz/tick, now) + wypięcie nazwanych helperów efektów daje ~90% czystości bez przepisywania.**
- **DRUGI rdzeń omijający HARD:** `plan_recheck` regeneruje sekwencje przez `simulate_bag_route_v2` **bez** `check_feasibility_v2` (komentarz wprost `plan_recheck.py:1019-1020` — „nowa sekwencja może być GORSZA R6"). Filar F-2 kanonu („plan_recheck przez TEN SAM rdzeń") = NIEzrealizowany.
- **Źródła prawdy:** kanon plikowy pod fcntl jest spójny dla zleceń (`orders_state.json`) i planów (`courier_plans.json`), ALE: **pozycja kuriera żyje w ≥4 magazynach z różnymi writerami** (`gps_positions.json`, `gps_positions_pwa.json`, `courier_last_pos.json`, `courier_api.db` + historia), a **sekwencja worka w 2 repach** (courier_plans vs courier_api.db).

## 3. Efekty uboczne i sprzężenia (szczegóły → `raw/01c`)

- **God-object `common.py`** (4111 l., prod-fan-in 88): config+logger+TZ+geometria HARD+mnożniki ruchu+**kary biznesowe** (`carry_chain_penalty:4025`, `bundle_score_multiplier:3624`, `extension_penalty:3753`…)+rejestr 106 flag (linie 95-391). `scoring.py` ma tylko 288 l. — nazwa myli, realny scoring rozlany po dispatch_pipeline+common.
- **Logika⨯I/O:** dispatch_pipeline 13×open/7×json.load w hot-path (m.in. hardcoded abs-path `restaurant_coords.json:878`, fleet-load-governor stan-przez-plik `:2226`); `courier_resolver` 8× open+json.load w budowie snapshotu; `feasibility_v2` pisze shadow-jsonl w środku HARD-checku (`:368/:409`). Shadow-jsonl piszą surowym `open('a')` z pominięciem `core/jsonl_appender`. Kanon stanu (plany/orders/pending) = atomic+fcntl ✅.
- **Cross-repo DWUKIERUNKOWE przez filesystem, nie API:** konsola importuje `dispatch_v2.common`/`plan_manager` (też przez `sys.path.insert` w stringu subprocessu); silnik czyta `.env`/DB panelu (`sync_courier_pay.py:24`) i odpala panelowy `.venv/bin/python` (golden-toole). Apka importuje `route_podjazdy`/`live_eta_cache`.
- **Bliźniaki — stan FAKTYCZNY na HEAD (korekta docs):** zunifikowane ✅: lex_qual (kanon `objm_lexr6.lex_qual:29`, pipeline deleguje), `_selection_bucket` (wspólny, `bucket_fn=` wstrzykiwane), serializer A+B (`_propagate_prefixed_metrics:235`), 4 handlery recanon (1 wejście `recanon_courier`). Żywe 🔴: **route-order w 3 repach/4 miejscach** (kanon `plan_recheck._apply_canon_order_invariants:1739` + `route_podjazdy` + `courier_orders.build_view:1096` + `fleet_state._build_route` — każde z WŁASNĄ flagą trust-canon, jedna env-frozen), **generatory planów** (`plan_recheck._gen_one_bag_plan:658` vs `route_simulator_v2._simulate_sequence:559`), **SLA-anchor** (3 inline + unifikator `sla_anchor.py` — patrz niżej).
- **Flagi = 3 światy + podwójne źródła:** rejestr `ETAP4_DECISION_FLAGS`=106; flags.json=229 kluczy; pełna populacja ~438 (wg flag_registry 03.07). Dziesiątki env-frozen `os.environ.get` na poziomie modułu (rodzina geocode w common, plan_recheck ×6, apka `BUILD_VIEW_TRUST_CANON_ORDER`), część nazw ma PODWÓJNE źródło (env-frozen + flags.json). **Zweryfikowany na żywo przykład pułapki:** `ENABLE_SLA_ANCHOR_UNIFIED` kod-default False + docstring „OFF", a `flags.json:260=true` → ścieżka UNIFIED **JEST LIVE** (potwierdzone przez agenta głównego 06.07, mtime flags.json 05.07 19:13; flip zgodny z kolejką FLIPMASTERA, notatka memory „tylko K2 ON" była nieprecyzyjna).

## 4. Współbieżność (szczegóły → `raw/01d`)

**Werdykt: stan DZIŚ dobry** — kanon `fcntl.LOCK_EX` + mkstemp+fsync+replace obejmuje pending/orders_state/courier_plans/ground_truth; finding O1 (pending dual-writer) ZAMKNIĘTY na HEAD; `bare except:` w rdzeniu = **0** (lepiej niż notatki z 05.07 — wszystkie 90 wystąpień w eod_drafts/deploy_staging). Realne ryzyka są **latentne-uzbrojone** (aktywują się flipem/re-enable), nie żywe:

| # | Ryzyko | Status | Dowód |
|---|---|---|---|
| W1 | `postpone_sweeper` schema-mismatch → duplikat propozycji dla przypisanego zlecenia | uśpione, **timer biega co ~1 min** (armed-on-flip Telegram/postpone) | `postpone_sweeper.py:103-110` (`.get("orders")` na płaskim dict + `cid` vs `courier_id`) |
| W2 | Oscylacja `courier_plans` plan-recheck↔panel-watcher (ten sam kod recanon, RÓŻNY env) | **JEDYNE ŻYWE** — drop-in `committed-propagation.conf` tylko u plan-rechecka | diff drop-inów `/etc/systemd/system/dispatch-{plan-recheck,panel-watcher}.service.d/` |
| W3 | Pile-on propozycji (1 kurier × N zleceń/tick) bez claim-ledgera | żywe jakościowo, bounded przez człowieka; ledger zbudowany, `ENABLE_ENGINE_CLAIM_LEDGER=false` | `claim_ledger.py:8-10` (g_maxpile do 7) |
| W4 | `courier_last_pos.json` lost-update rozłącznych cid (bez fcntl) | niska waga (best-effort cache) | `courier_resolver.py:171-198` |
| W5 | `global_alloc_store` wspólny `.tmp` (anty-wzorzec naprawiony w PPS, tu pozostał) | latentne (single-writer oneshot) | `global_alloc_store.py:35` |

Podwójne przypisanie przez SILNIK — brak ścieżki (`ENABLE_AUTO_ASSIGN=false` + dry-first handshake + idempotencja w executor). Jedyna powierzchnia = dwóch ludzi w gastro (poza systemem). Retry OSRM/panel ograniczone (timeouty 3-5 s, max 1 retry, fallback haversine).

## 5. Mechanizmy samouczące (szczegóły → `raw/01e`)

- **ML nie steruje decyzją.** 3 równoległe tory LightGBM (pairwise ranker v1.1, dwumodel solo/bundle, ETA-residual) — wszystkie SHADOW (`ENABLE_LGBM_PRIMARY`=OFF). **Brak pętli retreningu** — modele to zamrożone pliki (v1.1 z 01.05, twomodel z 20.06), trening ręczny offline.
- **Jedyna żywa ścieżka „wyuczone → HARD":** `eta_quantile_map.json` (cron nocny 04:35) w bramce R6/SLA gold≤4 (`feasibility_v2.py:1123-1135`, `:1235-1242`, flaga ON) + pośrednio GPS ground-truth→`picked_up_at`→kotwica R6 (`panel_watcher.py:2085`, ON) + tiery kurierów (`courier_tiers.json`). SOFT: `courier_reliability.json` (A2 + rampa nowego). Reszta (prep-bias→R6, drive-speed tier, eta_load_aware, auto-proximity, R04-enforce) = zbudowane, OFF.
- **Replay:** dwie rodziny — (A) `obj_replay_capture`/`obj_harness` (replay solvera, `now` z rekordu, ALE OSRM wołany NA ŻYWO, `picked_up_at`=proxy), (B) kontrfaktyczne re-scoringi z `shadow_decisions.jsonl` przez rotation-aware `ledger_io` (kanon werdyktów ON↔OFF do flipów). **Bit-w-bit replay decyzji dziś NIEMOŻLIWY**: OSRM nie nagrywany, brak frozen-clock, logrotate gubi ~29% okna bez ledger_io, dane fizyczne (gps_delivery_truth) pokrywają ~11,5% okna, paczki 0%.
- Serializer po L1.1 (deny-lista) = warunek konieczny wiarygodnych werdyktów; okna sprzed 2026-07-03T13:19Z mają dziury w kluczach HARD.

## 6. Weryfikacja/korekty istniejących artefaktów (wartość sama w sobie)

| Teza z docs/memory | Werdykt na HEAD | Konsekwencja |
|---|---|---|
| „lex_qual 3 kopie / inline _bucket / serializer A+B = żywe bliźniaki" (#0, 03-DLUG) | **NIEAKTUALNE — zunifikowane** | rejestr bliźniaków w ZIOMEK_ARCHITECTURE §4 do odświeżenia; entropia copy-count < 17 |
| „ENABLE_SLA_ANCHOR_UNIFIED OFF, flip za ACK" (INVARIANTS 05.07) | **NIEAKTUALNE — flags.json=true (LIVE)** | czytać flags.json, nie kod-default; INVARIANTS do aktualizacji |
| „bare-except 88→8 w silniku" (memory 05.07) | **rdzeń = 0** (90 wystąpień tylko w eod_drafts/deploy_staging) | lepszy stan niż raportowany |
| „pending .tmp współdzielony telegram+store" (enrichment 27.06) | **NIEAKTUALNE** — PPS używa mkstemp pod fcntl | ryzyko tylko przy re-enable Telegrama (blind-overwrite `save_pending`) |
| „postpone_sweeper schema-mismatch" (deep-audit #1.8) | **NADAL OBECNY** (`postpone_sweeper.py:103-110`) | mina armed-on-flip; wchodzi do Fazy 2 |
| in-degree z audytu 01a (np. feasibility_v2=21) | **zawyżone testami** — prod-only: feasibility ~2, route_sim ~12 | nie używać 01a jako miary sprzężenia prod |
| „ETAP4_DECISION_FLAGS 42 flagi" (#0) | **106 flag** (common.py:95-391) | protokół #0 do odświeżenia |
| „route-order 5 kopii" (kanon §4) | **4 żywe** (5. courier_api_panelsync usunięta) | zgodnie z kierunkiem; deadline monitora 07-10.07 aktualny |

## 7. HIPOTEZY do weryfikacji (wejście do Fazy 2 — każda z metodą)

1. **Kolejność `fleet_snapshot`** — czy `dispatchable_fleet` buduje dict w porządku deterministycznym? (test: 2× budowa snapshotu z tego samego stanu → identyczna kolejność kluczy).
2. **Parytet generatorów planów** — czy `plan_recheck._gen_one_bag_plan` i `route_simulator_v2._simulate_sequence` dają identyczne sekwencje dla tego samego worka? (golden-fixture, oba wywołania na tych samych danych).
3. **`APP_ROUTE_FROM_CONSOLE` LIVE** — kod-default „0" vs enrichment „=1 LIVE"; rozstrzyga `systemctl show courier-api -p Environment` + drop-iny.
4. **Rozjazd env drop-inów recanon** (W2) — `diff` pełnych env obu serwisów; skala oscylacji z `ziomek_time_route_monitor`/następcy (LICZBA, nie lektura — C4).
5. **Częstotliwość pile-on** (W3) — policzyć na świeżym oknie `shadow_decisions.jsonl` (ledger_io) rozkład „ile zleceń/tick dostaje ten sam best".
6. **Prod-fan-in z leniwymi importami** — grep importów w ciałach funkcji (uzupełnienie korekty in-degree).
7. **Pisarze plików stanu spoza serwisów** — audyt at-jobów/skryptów ad-hoc pod kątem zapisu orders_state/courier_plans.

## 8. Surowe wnioski pod Fazę 2 (fakty, jeszcze bez priorytetyzacji)

1. Rdzeń decyzyjny jest **blisko czystości** w sensie danych (stan wchodzi argumentami), a daleko w sensie środowiska (flagi z dysku ~700×/decyzję, OSRM live, side-effecty w środku) — dokładnie luka F-2 kanonu; koszt odcięcia niższy niż sugerowały wcześniejsze audyty (nazwane helpery efektów = gotowe punkty cięcia).
2. Największe pojedyncze źródła entropii: monolit `_assess_order_impl` (~3785 l.), god-object `common.py`, route-order ×4 cross-repo, pozycja kuriera ×≥4 magazyny, populacja ~438 flag w 3 światach z podwójnymi źródłami.
3. Współbieżność plikowa jest opanowana kanonem fcntl; ryzyka koncentrują się w klasie „armed-on-flip" (postpone W1, Telegram re-enable, claim-ledger OFF przy przyszłej autonomii) — spójne z tezą kanonu „system nie pali się, jest KRUCHY".
4. Samouczenie realnie = kalibracje plikowe z nocnych cronów; ML shadow. Dla architektury docelowej mapy kalibracyjne to wejścia HARD-krytyczne wymagające kontraktu świeżości/pokrycia, a replayowalność wymaga nagrywania OSRM + snapshotu flag + frozen-clock.
5. Pętla przypisania przechodzi przez zewnętrzny panel gastro (HTML/POST) — każdy przyszły wariant architektury musi jawnie zdecydować, czy gastro pozostaje źródłem prawdy przypisań (dziś TAK), bo to wiąże multi-tenant (400/d) z HTML-scrapingiem.

---
*Artefakt Fazy 1. Następny: `02-diagnoza.md` (Faza 2) — problemy systemowe + priorytetyzacja, po „dalej" od Adriana.*
