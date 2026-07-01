# B18 — KLASA O (współbieżność / kolejność / wyścig) — FAZA 1 audyt, lane B

**Agent:** B18-O-concurrency · **Tryb:** READ-ONLY (zero edycji/restartów/flipów) · **Sesja:** tmux 2 · **Data:** 2026-06-30 ~14:1x UTC
**HEAD recon:** `8024705` · **venv:** `/root/.openclaw/venvs/dispatch/bin/python`
**Wszystkie `plik:linia` z ŚWIEŻEGO grepu DZIŚ** (linie dryfują — re-grepuj przed użyciem jako pewnik). Numery zweryfikowane na żywym kodzie, nie z seed-doców.

**Zakres klasy O (zlecenie):** stan dzielony przez ≥2 pisarzy/procesy BEZ locka; `pending_proposals.json` 3-writer/no-lock; read-with-side-effect `load_plan`; zależność-od-kolejności-tick/serwisów; lag-reconcile; temporalna-osiągalność-hooka (#18); stale-pos 25min (rescue); shared git index multi-sesja; CookieJar nie-thread-safe.

---

## 0. METODA + LINIA PODZIAŁU „kto ma lock, kto nie"

Class O = **współdzielony zapis bez wzajemnego wykluczenia**. Kluczowy fakt strukturalny: w `dispatch_v2` istnieją DWIE dyscypliny zapisu współdzielonego pliku:

- **(A) fcntl LOCK_EX/SH + atomic replace** (poprawna, cross-proces): `plan_manager.py:60`, `state_machine.py:208`, `pending_pool.py:51`, `gps_server.py:74`, `coordinator_time_recheck.py:37`, `czasowka_scheduler.py:96`, `coordinator_activations.py:50`, `geocoding.py:88`, `geocoding_audit.py:76`, `shift_notifications/state.py:106`, `postpone_sweeper.py:59` (read-only LOCK_SH), `core/jsonl_appender` (learning_log, MP-#11). + osobne `threading.Lock`/`RLock` dla in-proc cache (`osrm_client.py:53`, `panel_client.py:72`, `prep_bias_anchor.py:45`, `parser_health.py`, `panel_detail_prefetch.py:95`).
- **(B) SAM atomic `os.replace`, BEZ fcntl** (zatrzymuje torn-read, NIE zatrzymuje lost-update RMW): **`pending_proposals_store.py:51`, `telegram_approver.py:1766`, `courier_resolver._save_last_known_pos:196`, `live_eta_cache.py:75`, `global_alloc_store.py:40`, `reassignment_global_select.py:161`, `postpone_sweeper._atomic_write_json:47`.**

**Wszystkie żywe wyścigi klasy O siedzą w grupie (B)** — pliki, które świadomie zrezygnowały z fcntl i polegają wyłącznie na atomowości `os.replace`. `os.replace` gwarantuje, że czytelnik nigdy nie zobaczy połowicznego pliku (torn read), ALE NIE chroni przed **lost-update**: pisarz A czyta `{x}`, pisarz B czyta `{x}`, A pisze `{x,a}`, B pisze `{x,b}` → `a` zgubione. To różnica „atomic-write" vs „atomic read-modify-write".

---

## 1. FINDINGS (instancja → plik:linia świeży → źródło/objaw → patched? → otwarte? → severity → dowód)

### O1 — `pending_proposals.json`: ≥2 żywych RMW-pisarzy, ZERO fcntl, współdzielony STAŁY `.tmp` (PRIMARY)
**Źródło. P2. OTWARTE (live race rzadki + uśpiony tmp-clobber).**

Pisarze (RMW = load→modify→save, każdy BEZ fcntl locka na pliku docelowym):
| Pisarz | plik:linia | tmp | proces / stan |
|---|---|---|---|
| `pending_proposals_store.save` | `:46` `tmp=f"{path}.tmp"` (STAŁY) → `:51 os.replace` | **STAŁY** | `dispatch-shadow` co tick, `ENABLE_PENDING_PROPOSALS_WRITE=true` (flags.json:229) → **LIVE** |
| `telegram_approver.save_pending` | `:1761 tmp=f"{path}.tmp"` (STAŁY) → `:1766 os.replace` | **STAŁY (ten sam co store)** | `dispatch-telegram` **MUTED** (inactive) |
| `postpone_sweeper._atomic_write_json` | `:36-47` `NamedTemporaryFile` (UNIKALNY) → `:47 os.replace`; RMW: `_load_json_safe:150` (LOCK_SH) → modify → `:158 write` (BEZ locka) | unikalny | `dispatch-postpone-sweeper.timer` → **LIVE** |
| `tools/pending_global_resweep` (LIVE re-propose) | `:416` | — | `PENDING_RESWEEP_LIVE=false` (flags.json:213) → **martwa gałąź** |

**Dowód „safe TYLKO bo Telegram muted":** `store.save:46` i `telegram.save_pending:1761` piszą do **TEGO SAMEGO stałego `{path}.tmp`**. Gdyby Telegram był aktywny, dwa procesy nadpisywałyby ten sam plik tymczasowy → `os.replace` mógłby wziąć za źródło na wpół-zapisany przez drugiego pisarza tmp → torn destination. Telegram inactive → ten konkretny clobber UŚPIONY. **Re-enable Telegrama = uzbrojenie wyścigu BEZ zmiany kodu** (potwierdza C2/Załącznik B protokołu).
**Dowód LIVE-race DZIŚ (mimo muted Telegrama):** `store` (shadow) + `postpone_sweeper` (timer) robią RMW na `pending_proposals.json` bez wspólnego locka. Różne `.tmp` (brak clobbera), ale **lost-update na pliku docelowym** wciąż możliwy (store upsertuje PROPOSE, postpone re-emituje — interleave gubi jeden zapis). Częstość niska (postpone pisze tylko wygasłe) → realne ryzyko małe, ale strukturalnie nie-zabezpieczone.
**Dodatkowo (lifecycle, H/O):** wpis znika TYLKO po TTL 30min (`pending_proposals_store.py:28 DEFAULT_TTL_SEC=1800`), NIE „na assign" → konsument (`panel_watcher._check_panel_override:222`, `_check_panel_agree:386`, `_save_plan_on_assign:452`) MUSI walidować `status=='planned'` vs orders_state. Czytelnicy `panel_watcher` czytają BEZ locka (`:222/:386/:452` goły `open()`), zdani na atomowość `os.replace` pisarza (OK na torn-read).
**dedup_hint:** O-no-lock-shared-state (rodzina B). „safe only because muted" = postura, nie kod.

---

### O2 — `load_plan` READ-with-side-effect: odczyt PERSYSTUJE `invalidate_plan`, wyścig z `advance_plan`
**Źródło. P2. PATCHED przy 2 callerach, OTWARTE strukturalnie (default + źródło niezmienione).**

`plan_manager.load_plan:121-160` z **domyślnym `invalidate_on_mismatch=True`** (`:124`): gdy `active_bag_oids` podane i stop planu spoza worka (`:156`) → `:157-158 invalidate_plan(cid,"ORDER_DELIVERED_ALL")` — **odczyt mutuje stan na dysku.** Lock NIE chroni: read zwalnia LOCK_SH (`:146-147`) ZANIM `invalidate_plan` bierze LOCK_EX (`:218`) → TOCTOU; między snapshotem a invalidacją `advance_plan` (po dostawie, chirurgicznie kreśli stop) może wejść.
**Objaw (potwierdzony w docstringu `:132-141`):** czytelnicy-PODGLĄDY (`dispatch_pipeline._soon_free_probe`/base_sequence) wołają per-tick z workiem KANDYDATA; wyścig z `advance_plan` → read widzi „stop spoza worka" i DRZE CAŁY plan mylnym `ORDER_DELIVERED_ALL` mimo żywych stopów → konsola mruga co tick na carried-first (case Jakub W / Piotr K).
**Patched:** flaga `ENABLE_LOAD_PLAN_PURE_READ` przy 2 hot-path callerach: `dispatch_pipeline.py:2361` + `:3770` (`invalidate_on_mismatch=not C.flag(...)`), flaga ON (MEMORY carried-first). Wtedy read CZYSTY (zwraca None, nie persystuje).
**OTWARTE strukturalnie:** (a) **default w kodzie wciąż `True`** — side-effect żyje w funkcji; (b) inni callerzy bez flagi: `panel_watcher.py:543`, `plan_recheck.py:1777/1828`, `tools/bundle_calib_shadow.py:369`, `tools/b_route_shadow.py:265` — wszystkie BEZ `active_bag_oids` (gałąź mismatch pomijana → dziś bezpieczne), ale **nowy caller z workiem kandydata bez flagi = re-uzbrojenie**; (c) fix = łatka-przy-callerze, nie usunięcie side-effectu u ŹRÓDŁA (`load_plan`). Klasyczne „patch na konsumencie, nie u źródła".
**dedup_hint:** K2 plan_recheck-cofacz / read-side-effect (most do floor-leak `_start_anchor`).

---

### O3 — `courier_last_pos.json`: multi-proces RMW bez fcntl; docstring KŁAMIE „multi-proces safe"
**Źródło + objaw-E. P3. OTWARTE (transient, self-healing, merge-by-ts łagodzi).**

`courier_resolver._save_last_known_pos:171-198`: RMW — `:177 disk=_load_last_known_pos()` → `:184` merge-by-ts (newer ts wygrywa) → prune → `:191` mkstemp (unikalny) → `:196 os.replace`. **ZERO fcntl.** Docstring `:172` deklaruje **„Atomic write z merge-by-ts (multi-proces safe)"** — to OVERSTATEMENT (klasa E/M: przyrząd/komentarz twierdzi bezpieczeństwo, którego nie ma). Read (`:177`) i write (`:196`) NIE są atomowe (brak locka spinającego) → okno lost-update: proces A pisze cid1, proces B (czytał disk PRZED zapisem A, nie ruszał cid1) nadpisuje cid1 stałą wartością. Merge-by-ts zawęża blast (tylko cidy nietknięte przez przegrywającego, tylko okno read→write), ale NIE eliminuje.
**Pisarze (≥4 procesy):** `build_fleet_snapshot` (→ `:1228 _save_last_known_pos`) wołany przez `dispatch-shadow` (każdy tick), `dispatch-czasowka` (`dispatchable_fleet`), `dispatch-postpone-sweeper`, instrumenty (`reassignment_forward_shadow`, `pending_global_resweep`, `b_route/bundle_calib` przez assess_order). Gate `_lp_on = ENABLE_COURIER_LAST_KNOWN_POS` (ON).
**Skutek:** store zasila no_gps rescue (`_rescue_from_last_pos:201`, TTL `LAST_KNOWN_POS_TTL_MIN=25.0` `:126`; ~5639 rescue/d wg MEMORY — patrz luka pokrycia). Lost update = pozycja kuriera chwilowo zgubiona → ten tick spada do no_gps → demote; następny tick re-zapisuje → self-healing.
**dedup_hint:** O-no-lock-shared-state + K5 sentinele/no_gps (most do bucket-pozycji gr.3 A6). Lying-comment „multi-proces safe" = klasa E.

---

### O4 — `live_eta_cache.json`: 2-procesowy RMW bez fcntl (shadow + plan_recheck)
**Źródło. P3. OTWARTE (display, self-healing).**

`live_eta_cache.upsert:84-124`: RMW — `:102 _read_raw()` (bez locka) → prune stale (`:105-109`) → upsert (`:115`) → `:121 _atomic_write` (`:69` mkstemp unikalny → `:75 os.replace`). **ZERO fcntl.**
**Pisarze (2 procesy):** `shadow_dispatcher.py:1255` (`dispatch-shadow`, per-decyzja) + `plan_recheck.py:1985` (`dispatch-plan-recheck`, `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH=1` na plan-recheck — A3 §1b). „latest-wins per oid" (`:90`) łagodzi, ale read-prune-write nieatomowy → lost update → stała ETA pokazana 1 tick w apce/konsoli, self-healing następnego ticka.
**dedup_hint:** O-no-lock-shared-state (rodzina B).

---

### O5 — CookieJar współdzielony przez ThreadPoolExecutor — ŁAMIE własną regułę „NIGDY"
**Źródło. P2. OTWARTE (latentne, samo-naprawcze przez 419-retry, tolerowane od kwietnia).**

CLAUDE.md NIGDY: *„edit-zamowienie calls sekwencyjnie, nie ThreadPoolExecutor (CookieJar thread-safety)"* + *„urllib CookieJar nie thread-safe"*. Tymczasem:
- `panel_client._open_with_relogin:464-483` czyta WSPÓŁDZIELONY `opener=_session["opener"]` (`:472`) i woła `opener.open(req)` (`:477`) **BEZ `_session_lock`** (lock tylko w `login():278`). Przy 419/401 woła `login(force=True)` (`:481`) który podmienia `_session["opener"]` (świeży opener+CookieJar) pod LOCKiem — ale inne wątki już złapały STARĄ referencję openera (`:472`) i są w trakcie `.open()` na unieważnianym CookieJar.
- `fetch_order_details:536-558` (`:551 _open_with_relogin`) — BEZ locka.
- Wołane z **`ThreadPoolExecutor`** w `dispatch_pipeline.py:427-433` (`executor.submit(_v327_safe_fetch_czas_kuriera, oid)` → `:279 panel_client.fetch_order_details`) — pre-proposal recheck, N wątków równolegle na WSPÓLNYM openerze.
**Skutek:** współbieżne `.open()` na jednym CookieJar + podmiana openera w locie = wyścig stanu cookies + kaskada 419 (każdy 419 → force login → unieważnia pozostałe). Dokładnie landmine 419, przed którym ostrzega projekt.
**Mityacja istnieje, ale NIE na tej ścieżce:** `panel_detail_prefetch.py:53` ma BEZPIECZNY wzorzec — własny opener+CookieJar per wątek (`:8-12` komentarz). Pre-proposal-recheck (`dispatch_pipeline:427`) tego NIE używa → asymetria: prefetch=safe, recheck=unsafe. Dodatkowo `_open_with_relogin` ma 1-retry na 419 (`:471-482`) → per-wątek samo-naprawcze, dlatego tolerowane od V3.27 (kwiecień) bez bycia top-incydentem.
**dedup_hint:** O-thread-shared-session + naruszenie własnej reguły NIGDY (klasa C „zła warstwa / known-landmine re-introduced").

---

### O6 — reconcile-lag 15-90 **MINUT** (eventual-consistency panel→orders_state) — NIE 15-90s
**Źródło. P2. MITYGOWANE (panel_packs fallback + TTL), strukturalnie otwarte. KOREKTA jednostki.**

`courier_resolver._bag_not_stale:555-560`: *„panel_watcher reconcile jeszcze nie dogonił (**lag 15-90 min** przez `MAX_RECONCILE_PER_CYCLE=25/tick` + FIFO closed_ids queue)"*. ⚠ Zlecenie mówiło „15-90s" — kod mówi **15-90 MIN**. To eventual-consistency: panel (ground truth) przypisuje/dostarcza, `orders_state.cid`/status nadrabia z budżetem 25/tick → przez do 90 min silnik decyduje na NIEAKTUALNYM `orders_state` (rodzina V3.13/14/15 „phantom free courier"/„phantom bag").
**Mityacja:** `STRICT_BAG_RECONCILIATION` + `BAG_STALE_THRESHOLD_MIN` TTL (`_bag_not_stale` filtruje stale assigned/picked_up), panel_packs fallback (V3.15, skraca lag dla rekonstrukcji worka). Lag bazowy = strukturalny (budżet reconcile).
**dedup_hint:** reconcile-lag / tick-ordering (O). Jednostka: MIN nie SEC.

---

### O7 — `courier_plans.json`: 2 niezależne timery piszą ten sam plan, last-writer-wins, RÓŻNE flagi efektywne
**Źródło. P2. OTWARTE (B+O — ordering wybiera kanon).**

Pisarze kanonu (oba przez `plan_manager.save_plan`, LOCK_EX → zero korupcji):
- `dispatch-plan-recheck.timer` (5min): `run_recheck` → `_gen_one_bag_plan:812 save_plan` / `_retime_one_bag_plan:1602 save_plan`.
- `dispatch-panel-watcher` (event-driven on write/pickup/override): `recanon_courier:1798` → `_retime_one_bag_plan:1838 save_plan` / `_gen_one_bag_plan:1789`.
**Problem O:** LOCK_EX chroni przed korupcją, ale **logiczny wynik = zależny od KOLEJNOŚCI** (który timer pisał ostatni). `save_plan` ma optymistyczny CAS (`:166 expected_version`), ale recanon/tick wołają BEZ `expected_version` (None=overwrite, `:172`) → ZERO ochrony optimistic-concurrency między timerami. Dodatkowo **dwie ścieżki mają RÓŻNY env efektywny** (A3 §1d / A5 B.1: panel-watcher NIE MA `ENABLE_PLAN_SEQUENCE_LOCK`/`_COMMITTED_PROPAGATION`/`_LIVE_ETA_REFRESH`, które plan-recheck MA) → ścieżka zdarzeniowa vs tickowa mogą policzyć INNY kanon, a ordering decyduje który zwygra.
**dedup_hint:** K2 plan_recheck-cofacz + B-twin-asymmetry (A5 B.1, A6 gr.2). Most do route-order divergence.

---

### O8 — temporalna-osiągalność-hooka (#18): `address_mismatch` = ROZWIĄZANY przykład (sweep nie NEW_ORDER); dedup process-local resetuje się przy restarcie
**Objaw/przykład-fixu. P3. INSTANCJA ZAMKNIĘTA, klasa-ostrzeżenie żywa.**

`address_mismatch.maybe_sweep_text_coords:223-234` = throttlowany sweep UTRWALONEGO `orders_state` (`:135` komentarz: *„NIE hook NEW_ORDER — tam tekst i pin zgadzają się"*), bo rozjazd tekst↔pin rodzi się DOWNSTREAM (po `gastro_edit.regeocode_and_update`). To POPRAWNE umiejscowienie (#18 fix), nie defekt. `_SWEEP_INTERVAL_S=300` (`:142`).
**Residualne O:** `_coords_logged:173` = dedup **process-local** (`set()` w pamięci procesu) → restart shadow resetuje → ten sam mismatch re-loguje raz po restarcie (duplikat w jsonl, nie błąd decyzyjny). Klasa #18 (hook tam, gdzie sygnał jeszcze nie istnieje) = żywe ryzyko dla KAŻDEGO nowego detektora — tu udokumentowane jako wzorzec, nie otwarty bug.
**dedup_hint:** #18 temporal-reachability (rozwiązana instancja, class-caution).

---

### O9 — shared git index multi-sesja (.git/index, ≥3-4 sesje równolegle)
**Proces/repo-concurrency. P3. PROCEDURALNIE mitygowane (reguła C1-git).**

Recon ETAP0 §A + protokół C1-git: `.git/index` WSPÓLNY dla working-tree → `git add` z wyprzedzeniem + cudzy `git commit` zgarnia Twoje staged pliki. **Near-miss 30.06:** AUTON-02 6 plików wpadło do cudzego `78401ed "test(force-recheck)"`, provenancja `976afbf`. Dziś żywe: `tmux` = 3 sesje claude (2=ja read-only, 3/4 mogą edytować → DRYF linii + index-race). Mitygacja = reguła C1-git (add+commit atomowo / worktree per sesja). NIE runtime, ale Class O wg taksonomii (shared state ≥2 pisarzy bez locka).
**dedup_hint:** K7 cross-repo/multi-sesja (J). Procedural, nie kod silnika.

---

### O10 — overlay cross-proces (global_alloc/reassign/parcel): single-writer + atomic replace, ale STAŁY `.tmp` + silent-vanish
**Objaw. P3. Niska istotność (single-writer-per-file).**

- `global_alloc_store.write:35` `tmp=f"{path}.tmp"` (STAŁY) → `:40 os.replace`. Pojedynczy pisarz (`pending_global_resweep` timer 1min, `ENABLE_GLOBAL_ALLOC_WRITE`) → ryzyko clobbera STAŁEGO `.tmp` TYLKO przy nakładce timera (tick >60s → systemd 2. instancja). Czytelnik = `feed.py` konsola (cross-repo) `load_fresh:56` TTL `DEFAULT_TTL_SEC=120` → fail-soft `{}` gdy stale = **overlay znika cicho (klasa M)**.
- `reassignment_global_select._atomic_write_channel:154-162` — tmp UNIKALNY (mkstemp), pojedynczy pisarz → bezpieczny.
- parcel: `parcel_lane_merge:134 sm.upsert_order` (LOCK_EX, `:5` komentarz „zero korupcji") — handoff `orders_state.parcels_shadow.json` (`:37`) single-writer (panel sidecar) → single-reader (merge 30s). Merge do orders_state = LOCK_EX safe.
**dedup_hint:** J cross-repo overlay + M silent-vanish-on-stale.

---

## 2. MAPA 9 PODOBSZARÓW ZLECENIA → FINDING

| Podobszar zlecenia (Class O) | Finding | Status |
|---|---|---|
| `pending_proposals.json` 3-writer/no-lock („safe TYLKO bo Telegram muted") | **O1** | CONFIRMED — 2 live RMW (store+postpone) + uśpiony tmp-clobber (telegram) |
| read-with-side-effect `load_plan` | **O2** | CONFIRMED — read persystuje invalidate; flaga-łatka u callera, źródło otwarte |
| zależność-od-kolejności-tick/serwisów | **O7** (+O1 handoff, O6) | CONFIRMED — courier_plans last-writer-wins, różne flagi/proces |
| lag-reconcile 15-90s | **O6** | CONFIRMED + KOREKTA: 15-90 **MIN** nie s |
| temporalna-osiągalność-hooka (sygnał downstream) | **O8** | ROZWIĄZANA instancja (address sweep); klasa-ostrzeżenie żywa |
| stale-pos 25min (rescue 5639×/d) | **O3** | CONFIRMED — RMW bez locka, docstring kłamie „multi-proces safe" |
| shared git index multi-sesja | **O9** | CONFIRMED — procedural (C1-git), near-miss 78401ed |
| CookieJar nie-thread-safe | **O5** | CONFIRMED LIVE — shared opener pod ThreadPoolExecutor, łamie regułę NIGDY |
| stan dzielony ≥2 pisarzy bez locka (ogólne) | **O1/O3/O4** + O10 | CONFIRMED — rodzina B (os.replace bez fcntl): pending/last_pos/live_eta |

---

## 3. WZORCE PRZEKROJOWE (dla Fazy E dedup)

- **Rodzina „os.replace bez fcntl" (B):** O1 (pending_proposals) + O3 (last_pos) + O4 (live_eta) + O10 (global_alloc) — ten sam root: zrezygnowano z fcntl, polega się na atomowości replace. `os.replace` ⇒ brak torn-read, ale **lost-update RMW NIEzabezpieczony**. Mityacje ad-hoc per plik: merge-by-ts (O3), latest-wins (O4), single-writer (O10), „Telegram muted" (O1). Żaden NIE jest lockiem. **Distinct-root: „shared-state writers bez wspólnego locka — rozsyp mityacji zamiast jednej dyscypliny".**
- **Most do innych klas:** O2→K2 (plan_recheck cofacz, read-side-effect = ten sam fundament co floor-leak `_start_anchor`); O3→K5 (sentinele/no_gps bucket gr.3); O7→B (twin plan-recheck↔panel-watcher, A5 B.1) + K2; O5→C (known-landmine NIGDY re-introduced); O10→J+M.
- **Lying-comment (E):** O3 docstring „multi-proces safe" — instrument/komentarz deklaruje bezpieczeństwo, którego kod nie ma. Faza C/E: traktuj jak kłamiący przyrząd (PLAUSIBLE do oracle: odpal 2 procesy, zmierz lost-update).
- **C2-mina (re-enable arms defect):** O1 (re-enable Telegram uzbraja tmp-clobber + martwy postpone schema) — potwierdza protokół C2/Załącznik B `:99,:112`.

## 4. DOBRE WZORCE (mityacje — NIE liczyć jako defekt; kontekst dla Fazy E PoC)
- `event_bus.py` = poprawny prymityw współbieżności: SQLite, `emit:256` idempotent (duplikat event_id→None), `mark_processed:302` INSERT OR IGNORE do processed_events, `ORDER BY created_at ASC:289` konsumpcja. Wzór dla „jednej dyscypliny".
- `plan_manager`/`state_machine`/`pending_pool`/`gps_server`/`coordinator_time_recheck`/`czasowka`/`shift_notifications` = fcntl LOCK_EX + atomic replace (poprawne cross-proces).
- `geocoding`/`gps_server` = DWA poziomy (threading.Lock in-proc + fcntl cross-proc) — wzorzec docelowy dla rodziny B.
- `panel_detail_prefetch` = bezpieczny per-wątek opener+CookieJar (kontra-przykład do O5 — fix już istnieje, niepodpięty na ścieżce recheck).

---

## 5. TABELA POKRYCIA (jawne — co zbadane, czego NIE)

### Zbadane (świeży grep + lektura kodu DZIŚ)
| Obszar | Pliki:linie zweryfikowane |
|---|---|
| pending_proposals writers/readers | `pending_proposals_store.py:27-107`, `telegram_approver.py:147,1748-1766,2932/2999/4016/4082/4101`, `postpone_sweeper.py:27-173`, `panel_watcher.py:207-459`, `tools/pending_global_resweep.py:60,416` |
| load_plan side-effect + callers | `plan_manager.py:115-160,163-319`, callerzy `dispatch_pipeline.py:2359-2361,3768-3770`, `panel_watcher.py:543`, `plan_recheck.py:1777,1828`, `tools/{bundle_calib_shadow:369,b_route_shadow:265}` |
| last_known_pos store | `courier_resolver.py:125-220,1205-1232` |
| live_eta_cache | `live_eta_cache.py:56-124`; pisarze `shadow_dispatcher.py:1255`, `plan_recheck.py:1985` |
| CookieJar/ThreadPoolExecutor | `panel_client.py:72,268-289,464-558`, `panel_detail_prefetch.py:3-53`, `dispatch_pipeline.py:260-284,427-433` |
| reconcile lag | `courier_resolver.py:555-571` |
| courier_plans multi-timer | `plan_recheck.py:612,812,1560,1602,1798-1843,1893-1963`, `panel_watcher.py:437` |
| overlay handoffs | `global_alloc_store.py:9-69`, `tools/reassignment_global_select.py:65,154-162`, `parcel_lane_merge.py:3-134` |
| locking inventory (cały silnik) | grep `fcntl/flock/LOCK_EX/threading.Lock/RLock` — 50+ trafień sklasyfikowanych (§0) |
| address hook #18 | `address_mismatch.py:133-234` |
| git index | recon ETAP0 §A (78401ed/976afbf) |

### LUKI POKRYCIA (jawne, nie cisza)
1. **Liczby częstotliwości NIE re-derywowane runtime** (read-only): „rescue 5639×/d" (O3) z MEMORY/telemetrii, NIE policzone z `shadow_decisions.jsonl`/`courier_match_debug.jsonl`; lost-update rzeczywista częstość (O1/O3/O4) NIEZMIERZONA — wymaga 2-proces repro/log-parse (Faza C oracle: odpal 2 procesy współbieżnie → policz zgubione zapisy). Severity oparta na strukturze + self-healing, nie na zmierzonej częstości.
2. **`_session_lock` pełne pokrycie ścieżek panelu** — potwierdziłem `login():278` pod lockiem i `_open_with_relogin`/`fetch_order_details` BEZ; NIE prześledziłem KAŻDEGO callera fetch_order_details (czy któryś jest pod lockiem zewnętrznie). `fetch_panel_html` ma własny re-login (`:468-469`), nie sprawdzony pod kątem współbieżności z recheck.
3. **Cross-repo procesy konsoli/apki** (`nadajesz-panel` uvicorn wielowątkowy `fleet_state`/`feed`, `courier-api` FastAPI) — czytają overlay/live_eta/pending współbieżnie; ich WEWNĘTRZNA współbieżność (async/threadpool uvicorn) NIE prześwietlona (granica „STOP na dyspozytorni"; A5 ma topologię). feed.py fail-soft `{}` (M) odnotowane, ale runtime-race wewnątrz uvicorn = poza tym OS.
4. **`dispatch-czasowka` / instrument-timery env + częstość zapisu last_pos** — A3 §9 też tego nie zmierzył; ile procesów REALNIE woła `_save_last_known_pos` współbieżnie w peaku = nie policzone (zakładam ≥2 z shadow+czasowka+postpone).
5. **Timer-overlap (tick >interval)** dla global_alloc STAŁY `.tmp` (O10) i pending RMW — czy systemd pozwala 2. instancji (zależy od `Type=oneshot` + brak `RefuseManualStart`/serializacji) — NIE zweryfikowane `systemctl cat` per timer.
6. **prep_bias_anchor._lock** (threading.Lock, `:45`) — czy plik, który guarduje, jest pisany cross-proces (wtedy threading.Lock = fałszywe bezpieczeństwo jak O3) — NIE prześledzony (MAIN OFF, niska istotność).
7. **postpone_sweeper realnie aktywny?** — `dispatch-postpone-sweeper.timer` w rejestrze A1, ale `is-active`/kadencja nie potwierdzona dziś; jeśli rzadko tickuje, O1 live-race (store+postpone) jeszcze rzadszy.

### NIE-luki (świadomie poza zakresem)
Mailek, Papu (granica). Sentinele jako klasa M (osobny agent — O3/O10 tylko cross-ref). Flagi efektywne (A3). Wartości oracle/breach (Faza C).

---

## 6. HANDOFF Faza C/D/E
- **Faza C (oracle):** O3 „multi-proces safe" = kłamiący-komentarz → repro 2-proces lost-update (PLAUSIBLE→CONFIRMED). O1 lost-update store↔postpone = repro. O5 CookieJar = repro ThreadPoolExecutor+419 (czy kaskada). Wszystkie „self-healing" — zmierz CZĘSTOŚĆ, nie tylko możliwość.
- **Faza D (precedencja):** O7 = ordering plan-recheck↔panel-watcher z RÓŻNYM env (A3 §1d) → która ścieżka kanonu wygrywa zależy od kolejności timerów = niezdefiniowana precedencja (most do klasy I). O6 lag = tick czyta stan sprzed reconcile.
- **Faza E (dedup→PoC):** distinct-root „shared-state writers bez wspólnego locka" (O1+O3+O4+O10, rodzina B) = kandydat na JEDNĄ dyscyplinę (fcntl LOCK_EX wrapper jak plan_manager/state_machine, zamiast 4 ad-hoc mityacji). O2 = przepiąć side-effect U ŹRÓDŁA (usunąć z `load_plan`, nie łatać per-caller). O5 = przepiąć recheck na wzorzec `panel_detail_prefetch` (per-wątek opener). NIE liczyć O3/O5/O7/O8 podwójnie z agentami K5/B/K2/#18 — to mosty, nie nowe rooty.
