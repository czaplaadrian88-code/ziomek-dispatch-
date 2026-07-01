# B10 — KLASA H (luki cyklu życia / janitorial) — backing

**Agent:** B10-H-lifecycle · **Lane:** B · **Tryb:** READ-ONLY (zero edycji/restartów/flipów) · **Data:** 2026-06-30 ~15:00 UTC · **HEAD recon:** `8024705`
**Zakres:** cykl życia KAŻDEGO trwałego stanu silnika: kto CREATE / MUTATE / GC przez cały cykl. Plus H1 (brak-GC-gdy-pustoszeje / zombie courier_plans), H2 (read-with-side-effect load_plan), H3 (recanon nie-potrafi-prune). STOP na dyspozytorni (bez Mailek/Papu).
**Metoda:** świeży `grep -rn`/`sed` per moduł (linie z grepu z DZIŚ — DRYFUJĄ), inspekcja `flags.json` hot, `systemctl is-enabled`/`list-timers`, analiza zawartości plików stanu w `dispatch_state/` (read-only, NIE pisane).

> **Dedup z Fazą A i seedami:** A6 zmapował twin-grafy (route-order, lex_qual, floor) — NIE powtarzam. Floor pickup≥shift_start (A6 grupa 6 / preshift-audit) = inny root (R4), tu tylko cross-ref przy plan-stuck. Moje H to **janitorial/cykl-życia stanu**, ortogonalne do A1/A2/J. H3 (plan_recheck nie prune) zwija się do K2 „plan_recheck=cofacz" z unified-audit. **Healthy stores wyliczam jawnie jako kontrast (nie-findingi), żeby luka była jawna nie cicha.**

---

## 0. MASTER TABELA — CYKL ŻYCIA 11 TRWAŁYCH STANÓW (create → mutate → GC)

| Stan (plik) | Klucz / wzrost | CREATE | MUTATE | GC / EVICTION | Stan cyklu |
|---|---|---|---|---|---|
| **courier_plans.json** (39KB) | per-cid (bounded ~flota, churn-unbounded) | `plan_manager.save_plan:163` | `advance_plan:258`/`invalidate_plan:213`/`touch_plan:229`/`remove_stops:299`/`mark_picked_up:327`/`refloor_pickup:382`/`recanon`→`_retime_one_bag_plan:1560` | **`gc_invalidated:501` — ORPHAN (zero callerów live)** | 🔴 **H1: GC martwy, 33/47 zombie** |
| **orders_state.json** | per-oid (był 8.4MB/99% terminal) | `state_machine.upsert_order:418` | `set_status`/`delete_order:~875` (RMW flock) | `prune_terminal_orders:933` (retention 12h) ← `prune_orders_state.timer` daily 03:30, **flaga ON** | 🟢 ZDROWY (wired) |
| **pending_proposals.json** (1.5MB) | per-oid, TTL 30min | `pending_proposals_store.upsert_proposals:87` (writer live = `shadow_dispatcher.py:1355`) | upsert (RMW **bez locka** = klasa O) | `sweep_expired:64` na każdym upsert | 🟢 GC dział. (14 wpisów, 0 expired-kept) — ⚠ O nie-H |
| **courier_last_pos.json** (1.6KB) | per-cid, TTL 25min read | `courier_resolver._save_last_known_pos:171` | merge-by-ts | **prune-on-write** `>LAST_KNOWN_POS_PRUNE_MIN=360min` (`:187-189`) | 🟢 ZDROWY (prune-on-write) |
| **panel_packs_cache.json** (428B) | snapshot, overwrite | `panel_watcher.py:2486` (atomic replace) | full-overwrite/cykl | **N/D — overwrite-in-place** (stale monitorowany `:6395`+liveness_probe) | 🟢 ZDROWY (brak akumulacji) |
| **events.db** (30.5MB SQLite) | append events+audit | `event_bus.emit`/`emit_audit` | processed-flag | `event_bus_cleanup` (events 48h / audit 90d / broadcast 7d) ← timer daily 04:00 **enabled** | 🟢 ZDROWY (wired) |
| **courier_ground_truth.json** (98KB) | per-oid, **fact-bearing** | `courier_api status_store.write_ground_truth` | — | `ground_truth_gc.find_artifacts:41` **TYLKO status-only artefakty**; fakty GPS (picked/delivered) = **KEPT FOREVER** | 🟡 **H: fakty rosną bez age-bound** |
| **geocode_cache.json** (3.2MB) | per-adres | `geocoding.geocode` write | — | **BRAK cap/LRU/age** | 🟡 **H: append-only, bez eviction** |
| **customer_dwell.json** (2.2MB) | per-klient/adres | dwell writer | — | **BRAK** | 🟡 H: append-only |
| **address_pin_index.json** (1.3MB) | per-pin | address_pin aggregator | — | **BRAK** | 🟡 H: append-only |
| **delivery_town_cache.json** (64KB) | per-adres | town-cache writer | — | **BRAK** | 🟡 H: append-only |

**Wniosek:** GC-infrastruktura ISTNIEJE i jest podpięta dla 5 stanów (orders_state, last_pos, events.db, pending_proposals, panel_packs). **`courier_plans.json` to JEDYNY stan z napisanym GC, który NIE jest podpięty.** Drugi wzorzec: **6 append-only cache/telemetry stores bez żadnej eviction** (geocode/dwell/pins/town/ground-truth-facts) — wolny, ale prawdziwie nieograniczony wzrost.

---

## 1. H1 — courier_plans.json: GC `gc_invalidated` ORPHAN + 33 zombie

### Dowód kodu (świeży)
- `plan_manager.py:501` `def gc_invalidated(older_than_hours=24.0)` — docstring l.503: **„Manual / cron hook — no auto-schedule."**
- Jedyny `del plans[cid]` w całym repo = `plan_manager.py:529` (wewnątrz `gc_invalidated`). `save_plan:208` robi `plans[cid] = saved` (overwrite tej samej cid).
- **Callerzy `gc_invalidated` (grep całego repo):** tylko `tests/test_v319c_sub_a.py`, `tests/test_a1_silent_killers_cross_codebase.py` + self-ref (`:519/524` `_warned_inv`). **ZERO produkcyjnych callerów. ZERO timera/crona** (`grep gc_invalidated /etc/systemd` = pusto). `dispatch-plan-recheck.service` jest jedynym co dotyka plików, ale `run_recheck` NIE woła `gc_invalidated`.

### Dowód danych (live `courier_plans.json`, read-only)
- **47 wpisów cid; 33 invalidated (zombie); 14 active.** 100% zombie reason = `ORDER_DELIVERED_ALL`.
- **Najstarszy zombie: cid 414 @ 2026-04-28** (>2 miesiące). cid 284 @ 05-03, cid 523 @ 05-14.
- Mechanizm: `invalidate_plan` (l.213-226) zostawia wpis z `invalidated_at` set („Plan stays in file for debug + GC-able"). `load_plan:151` zwraca None dla invalidated → **decyzji NIE psuje**. Ale wpis tkwi do następnego `save_plan(tej-samej-cid)` LUB `gc_invalidated` (nigdy). Kurier który odszedł z floty = wpis NA ZAWSZE.

### Charakter
- **kind=source.** Klasa H (luka cyklu) + **K (martwy kod** — gc_invalidated to dead function). Docstring `:214` „GC-able" = obietnica, której nic nie spełnia (dryf intencja↔rzeczywistość).
- **Wpływ:** brak wpływu na DECYZJE (load_plan skip invalidated). Szkoda = bloat pliku (70% śmieci), martwy GC, churn-unbounded wzrost (każdy kurier który kiedykolwiek pracował i odszedł zostaje). is_patched=False (A1 dorzucił tylko warn-dedup na parse-fail `:517-524`, NIE podpiął GC).
- **Fix-u-źródła:** podpiąć `gc_invalidated` do janitora (np. dorzucić do `run_recheck` koniec-ticku albo do `prune_orders_state.timer`), ALBO świadomie usunąć martwą funkcję. **severity P2** (czysta luka janitorial + dead code; bez korupcji decyzji).

---

## 2. H1b — plan „tkwi invalidated" bez regeneracji (lifecycle dead-end)

### Dowód
- `plan_recheck.py:348-353` (komentarz przy `ENABLE_GPS_FREE_ANCHOR_LAST_POS`): *„Bez tego `_start_anchor`=None → `_gen_one_bag_plan` pomija CAŁEGO kuriera → plan nigdy się nie regeneruje (tkwi invalidated ze starymi dowiezionymi + bez nowych aktywnych)."*
- Ścieżka: kurier z planem invalidated (ORDER_DELIVERED_ALL) dostaje nowe zlecenia, ale `_start_anchor` (`:554`) = None (brak świeżego GPS <10min + brak kotwicy zdarzeniowej/committed) → `_gen_one_bag_plan` (`:612`) pomija → plan zostaje invalidated → apka/konsola/Telegram lecą na fallbacku per-tick zamiast spójnego planu.
- **Mitygacja LIVE:** `ENABLE_GPS_FREE_ANCHOR_LAST_POS` — kod default OFF (`:354` `os.environ.get(...,"0")`), ale **drop-in `dispatch-plan-recheck.service.d` = `1` (ON)** (recon C). Sięga do `courier_last_pos.json` jako ostatnia deska. → flaga-mina: reset/usunięcie drop-inu = dead-end wraca.

### Charakter
- kind=source. Klasa H (dead-end cyklu) + D (flaga-mina default-OFF-w-kodzie). is_patched=True (drop-in ON), still_open=True (default OFF = source nie utwardzony). **severity P3** (mitygowane live).
- dedup→ courier_plans lifecycle (R-H-plans). Cross-ref R4 floor (oba dotykają `_start_anchor`).

---

## 3. H2 — `load_plan` read-with-side-effect (mutacja przy odczycie)

### Dowód
- `plan_manager.py:121` `load_plan(courier_id, active_bag_oids=None, invalidate_on_mismatch=True)`. Gdy `active_bag_oids` podane i `plan_oids` NIE są podzbiorem worka (`:156`): `if invalidate_on_mismatch: invalidate_plan(cid, "ORDER_DELIVERED_ALL")` (`:157-158`) — **PERSYSTUJE invalidację podczas READ.**
- **Default = True (opt-OUT).** Docstring `:132-141` opisuje incydent: czytelnicy-podglądy (`_soon_free_probe`/base_sequence) wołali per-tick z workiem KANDYDATA → wyścig z `advance_plan` → read „widzi stop spoza worka" i **DRZE CAŁY plan** (ORDER_DELIVERED_ALL mimo żywych stopów) → konsola mruga carried-first co tick (case Jakub W / Piotr K, 29.06).
- **Callerzy z `active_bag_oids` (jedyni odpalający side-effect):** `dispatch_pipeline.py:2359-2361` i `:3768-3770` — OBA `invalidate_on_mismatch=not C.flag("ENABLE_LOAD_PLAN_PURE_READ")`. Flaga **`ENABLE_LOAD_PLAN_PURE_READ=True`** (flags.json) → przekazują False → side-effect SUPPRESSED live.
- Pozostali callerzy (`panel_watcher.py:543`, `plan_recheck.py:1777/1828`, `tools/b_route_shadow.py:265`, `tools/bundle_calib_shadow.py:369`) wołają BEZ `active_bag_oids` → gałąź side-effect nieosiągalna dla nich.

### Charakter
- kind=source. Klasa H (read-with-side-effect) + D (flaga-mina). is_patched=True (flaga ON ratuje produkcję), **still_open=True bo DEFAULT param wciąż True** = opt-out: każdy NOWY caller z `active_bag_oids` który zapomni flagi LUB reset flags.json reaktywuje „read drze plan". Fix-u-źródła per protokół = odwrócić default na pure-read (opt-IN side-effect). **severity P2** (root realnego incydentu oscylacji; produkcja mitygowana flagą ale źródło nie domknięte).
- dedup→ courier_plans lifecycle (R-H-plans).

---

## 4. H3 — recanon/retime NIE POTRAFI PRUNE (subset-gate + retime-only)

### Dowód — subset gate (grow-only)
- `plan_recheck.py:1832` (świeże): `covered = {order_id w stops}` (l.1831); `if not (set(oids) <= covered): return False` (l.1832-1833 „plan nie pokrywa worka → tick/gen"). `oids` = aktywne zlecenia kuriera (`status in ACTIVE_STATUSES`, `:1823-1825`).
- Gate broni TYLKO kierunku WZROSTU: nowe zlecenie poza planem → `oids ⊄ covered` → bail (poprawnie, pełna decyzja). **Gdy worek SKURCZYŁ** (zlecenie dostarczone/anulowane, ale jego stop WCIĄŻ w `covered`): `oids` = ścisły podzbiór `covered` → `oids <= covered` = **True → recanon PROCEEDS** mimo phantom-stopu.
- Bliźniaczy gate w `redecide_courier:1780` (`if set(oids) <= covered: ... return False`) — też traktuje superset-covered jako „pokryte, no-op" → też nie prune (poza reason='pickup' + stale bag_signature).

### Dowód — retime nie filtruje statusu
- `_retime_one_bag_plan:1560` → `_retime_stops:820` iteruje po `plan.stops` (`:828`) i przelicza KAŻDY stop. Filtr = tylko `_coords_ok` (`:832`): jeśli phantom-order ma jeszcze coords w orders_state → **retime go zachowuje i przeczasowuje**; jeśli coords zniknęły (pruned z orders_state) → `_coords_ok` fail → `return None` → **cały retime abort** (recanon=False, plan zostaje nietknięty ze starym phantomem). Żaden wariant NIE usuwa stopu.
- `_apply_canon_order_invariants:1478` (wołany w retime `:1582`): filtruje `status=="picked_up"` tylko po to by **przesunąć niesione dropoffy na przód** (`:1489`) — REORDER, **nie prune**. Nie ma gałęzi usuwającej delivered/cancelled.

### Pruning = WYŁĄCZNIE chirurgiczny event-driven
- Jedyne co prune phantom-stop: `advance_plan` (delivered, `:277-279`), `mark_picked_up` (pickup, `:344-347`), `remove_stops` (cancelled, `:310`). Wołane z `panel_watcher` na zdarzenie.
- **Luka:** `panel_watcher` reconcile MA udokumentowany lag — `:1876` `MAX_RECONCILE_PER_CYCLE = 25 # F2.1c: zwiększone z 10 (zombie backlog)` (throttle GC < backlog), `:2044` *„plan_recheck planuje FANTOMOWY odbiór już-niesionego (zawyżone ETA, dostawa-przed-odbiorem)"*, `:2504` *„phantom MISSING_FROM_STATE 4h+ później"*. Gdy event lagguje/gubi się → phantom-stop tkwi w planie, a recanon/retime go WIERNIE przeczasowują (lub abortują), zamiast pociąć.

### Charakter
- kind=source. Klasa H (recanon-nie-prune). is_patched=False, still_open=True.
- **Wpływ:** phantom dropoff w planie → zawyżone ETA, „dostawa-przed-odbiorem", konsola/apka pokazują nieaktualną trasę dopóki nie strzeli surgical event / nie zmieni się bag_signature (→ pełny `_gen`) / nie invaliduje cały plan. **severity P2** (symptom realny: błędne ETA + trasa; objaw `:2044`).
- dedup→ **K2 „plan_recheck=cofacz"** (unified-audit) / R-H-plan-prune. Powiązany upstream: panel_watcher reconcile throttle (pkt 5).

---

## 5. H — panel_watcher reconcile: throttle GC < backlog (upstream H3)

- `panel_watcher.py:1876` `MAX_RECONCILE_PER_CYCLE = 25` — bounded przepustowość domykania terminalnych na cykl. Komentarz „(zombie backlog)" = już raz podbite 10→25 bo backlog > przepustowość.
- Skutek: gdy w peaku terminali domyka się > 25/cykl, reconcile lagguje → terminalne zlecenia tkwią aktywne → (a) feed do H3 (plan trzyma phantom-stop), (b) `:2504` phantom MISSING_FROM_STATE.
- kind=source. is_patched=True (10→25), still_open=True (wciąż bounded, nie adaptacyjne). **severity P3** (znane, podbite; źródło = bounded throughput vs unbounded backlog).
- dedup→ R-H-plan-prune (wspólny z H3).

---

## 6. H — append-only cache/telemetry stores bez eviction (6 plików)

- **`courier_ground_truth.json`:** `observability/ground_truth_gc.py:41` `find_artifacts` prune TYLKO wpisy „status-only" (bez `picked_up_at`/`delivered_at`) dla TERMINALNYCH. Wpisy z faktem GPS = `continue` (KEPT — „kalibracja", `:46-47`). Brak age-bound → fakt-bearing rośnie nieograniczenie (per delivered-z-GPS). 98KB dziś, ale unbounded.
- **`geocode_cache.json`** (3.2MB), **`customer_dwell.json`** (2.2MB), **`address_pin_index.json`** (1.3MB), **`delivery_town_cache.json`** (64KB): grep `prune|cap|MAX|LRU|older|evict|trim` = **PUSTO** dla każdego. Append-only, klucz per-adres/klient/pin (wolno-unbounded).
- kind=source. Klasa H (brak bound cyklu) + N (rozsyp — różne stany, wspólny anty-wzorzec). is_patched=False, still_open=True. **severity P3** (wolny wzrost, niskie ryzyko korupcji; ale prawdziwie nieograniczone — disk/RMW-cost long-tail). geocode/dwell czytane w hot-path → rozmiar = koszt I/O.
- dedup→ R-H-unbounded-cache.

---

## 7. STANY ZDROWE (kontrast — jawne, nie cisza)

Wyliczam by luka była jawna (te NIE są findingami H):
- **orders_state.json** — `prune_terminal_orders:933` retention 12h, flaga `ENABLE_ORDERS_STATE_PRUNE=True`, `ORDERS_STATE_PRUNE_DRY_RUN=False`, timer `dispatch-orders-state-prune.timer` **enabled** (daily 03:30, last 03:30 dziś). RMW flock + `_read_state_strict` (raise zamiast cichego {}). ✅ (latent smell: prune NOCNY → terminale akumulują do 24h+ między prune; już udokumentowane STATE-RMW-02).
- **courier_last_pos.json** — prune-on-write 6h (`:187-189`), merge-by-ts multi-proces-safe, atomic. ✅
- **pending_proposals.json** — `sweep_expired` na każdym `upsert_proposals` (TTL 30min); live = 14 wpisów, 0 expired-kept, 0 bez expires_at. Writer = `shadow_dispatcher:1355` (NIE telegram — telegram muted). ✅ dla H. ⚠ **klasa O** (3-writer RMW bez locka) — NIE moja lane, cross-ref. (1.5MB = grube payloady decision_record ~107KB/wpis, nie count-bloat.)
- **panel_packs_cache.json** — overwrite-in-place per cykl, stale-monitorowany (`dispatch_pipeline:6395`, liveness_probe). ✅
- **events.db** — `event_bus_cleanup` (events 48h/audit 90d/broadcast 7d), timer enabled daily 04:00. ✅ (30.5MB — duży ale zarządzany).

---

## 8. DEKLARACJA POKRYCIA (jawne luki — C11-c)

**Zbadane świeżym grepem/danymi dziś:** `plan_manager.py` (cały, 644L), `plan_recheck.py` (load_plan callsites 1777/1828, recanon 1798-1844, redecide 1736-1795, _retime_stops 820-861, _retime_one_bag 1560-1604, canon_invariants 1478-1500, _bag_signature+kotwice 327-363), `courier_resolver.py` (last_pos 121-220, save 171-198), `prune_orders_state.py` (cały), `state_machine.py` (delete_order 875-892, prune_terminal_orders 933), `pending_proposals_store.py` (cały), `panel_watcher.py` (reconcile 1876/2040/2498-2510, packs 2436-2489), `observability/ground_truth_gc.py`+`event_bus_cleanup.py` (def+retention), `courier_ground_truth.py`. Dane live (read-only): courier_plans.json (zombie count), pending_proposals.json (sweep audit), ls dispatch_state/*.json. Flagi: flags.json hot (PRUNE/GC/PURE_READ), `systemctl is-enabled`/`list-timers`.

**LUKI (jawne):**
1. **`_apply_canon_order_invariants` pełne ciało (1478-1559)** — sprawdziłem brak prune-by-status (grep), NIE prześledziłem każdej gałęzi reorderu (to lane route-order A2/J, nie H).
2. **`_gen_one_bag_plan:612` / `_gap_fill_plans:1876` / `run_recheck:2017` pełna logika regeneracji** — potwierdziłem że NIE wołają `gc_invalidated` i że stuck-invalidated istnieje (komentarz `:352`), ale nie zmierzyłem ILE kurierów realnie tkwi (wymaga join courier_plans×orders_state — Faza C oracle).
3. **Cross-repo stany** (`courier_api/status_store` ground_truth writer, konsola `nadajesz_clone/panel` własne cache) — ground_truth writer potwierdzony jako jedyny (courier-api), ale wewnętrzne cache konsoli/apki NIE prześwietlone pod GC (granica; route-order=A6/J).
4. **events.db rzeczywista skuteczność cleanup** — timer enabled + retencja zadeklarowana, ale NIE zweryfikowałem czy DELETE realnie zmniejsza plik (VACUUM?) — 30.5MB sugeruje brak VACUUM (SQLite free-list rośnie). Faza C/oracle.
5. **Liczbowy wzrost append-only cache w czasie** — stwierdzam BRAK eviction z kodu; tempo wzrostu (MB/mies) NIE zmierzone (jeden snapshot).
6. **read-with-side-effect POZA load_plan** — preshift-audit (#7 tropy) wskazał „sweep read-with-side-effect"; sprawdziłem load_plan (główny, ugryzł 29.06); NIE zrobiłem pełnego sweepu wszystkich `_read_*` pod side-effect (wskazane osobne przejście).

**NIE-luki (świadomie poza H):** floor pickup≥shift_start (R4, A6 gr.6), route-order kopie (A2/J, A6 gr.2), pending_proposals no-lock (klasa O), sentinele (0,0)/BIALYSTOK_CENTER (klasa M), Mailek/Papu (granica).

---

## 9. ROLLUP H-rootów (dla Fazy E — anty-double-count)

| Root H | Klasy | Instancje | Status |
|---|---|---|---|
| **R-H-plans-lifecycle** (courier_plans create/mutate bez wired-GC/pure-read) | H+K+D | H1 (gc_invalidated orphan, 33 zombie) · H1b (stuck-invalidated) · H2 (load_plan side-effect default-True) | GC napisany-niepodpięty; flagi ratują 2/3 ale defaulty nie utwardzone |
| **R-H-plan-prune** (= K2 plan_recheck=cofacz) | H | H3 (recanon subset-gate + retime-only, brak prune) · panel_watcher reconcile throttle (upstream) | prune wyłącznie surgical-event; lag→phantom |
| **R-H-unbounded-cache** | H+N | ground_truth-facts · geocode_cache · customer_dwell · address_pin_index · delivery_town_cache | append-only, zero eviction |

**Healthy (kontrast, nie root):** orders_state · last_pos · pending_proposals(TTL) · panel_packs · events.db — GC podpięty.
