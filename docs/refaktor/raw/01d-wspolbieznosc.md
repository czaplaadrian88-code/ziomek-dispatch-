# RAW 01d — raport agenta f1-wspolbieznosc (współbieżność)

> Surowy raport subagenta read-only (Faza 1). Zweryfikowany na HEAD `fcf1342`. Synteza → `../01-stan-obecny.md`.

# WSPÓŁBIEŻNOŚĆ — audyt read-only, HEAD `fcf1342` (master), 2026-07-06

**Metoda:** wyłącznie Read/Grep/Glob + `systemctl`/`cat` read-only + odczyt `flags.json`/state. Zero zapisu. Każdy wiersz zweryfikowany na HEAD (linie mogą dryfować — podane z bieżącego drzewa).

**Werdykt jednym zdaniem:** system jest DZIŚ w dobrym stanie współbieżnościowym — dominujący kanon to `fcntl.LOCK_EX` na dedykowanym lockfile obejmujący cały cykl read-modify-write (pending, orders_state, courier_plans, ground_truth), a większość realnych wyścigów jest rozbrojona przez `ENABLE_AUTO_ASSIGN=false` (człowiek w pętli) + wyciszony/wyłączony Telegram. Ryzyka realne są **latentne-uzbrojone** (odpalą się przy flipie flagi / re-enable serwisu), nie żywe. Finding O1 z audytu 2.0 (pending dualwriter) = **ZAMKNIĘTY na HEAD**; finding #1.8 z audytu 27.06 (postpone schema-mismatch) = **NADAL OBECNY, uśpiony ale timer uzbrojony**.

---

## TABELA RYZYK

| # | Ryzyko | FAKT/HIPOTEZA | Dowód (plik:linia HEAD) | Co chroni DZIŚ | Jak zweryfikować |
|---|---|---|---|---|---|
| R1 | `pending_proposals.json` lost-update między pisarzami | **FAKT — ROZBROJONE** | `pending_proposals_store.py:49-66` (`_locked` LOCK_EX), `:204` upsert, `:136` locked_mutate | Wszyscy 4 pisarze idą przez kanon PPS; dziś realnie 1 żywy (shadow) | grep pisarzy niżej; test `tests/test_pending_fcntl_concurrency_l75.py` |
| R2 | postpone_sweeper re-emituje duplikat propozycji dla już-przypisanego zlecenia | **FAKT — bug obecny, uśpiony** | `postpone_sweeper.py:104` `orders_state.get("orders",{})` przy `_read_state()` zwracającym PŁASKI dict → `current` zawsze `{}` → `POSTPONE_RESOLVED` nieosiągalny | `postponed_proposals.json={}` + jedyny pisarz `telegram_approver.py:3948` (serwis inactive+disabled) → sweeper no-op | flip Telegramu ON + postpone → duplikat; test na `courier_id` w `_read_state()` |
| R3 | Podwójne przypisanie tego samego zlecenia 2 kurierom przez silnik | **FAKT — brak ścieżki (AUTO OFF)** | `flags.json` `ENABLE_AUTO_ASSIGN=False`; gate `auto_assign_executor.py:285,335` (decision_flag + dry-first 45s + idempotencja) | Ziomek nigdy nie przypisuje; przypisuje TYLKO człowiek (konsola/gastro) | flip AUTO_ASSIGN → obserwacja dry-first handshake |
| R4 | Pile-on: jeden kurier proponowany na wiele zleceń w 1 ticku | **FAKT — brak ochrony (ledger OFF)** | `flags.json` `ENABLE_ENGINE_CLAIM_LEDGER=False`; `claim_ledger.py:8-10` (docstring: g_maxpile=7, 127×/32 zlecenia) | To PROPOZYCJE (human-review, AUTO OFF), nie przypisania | replay ticku z ledger ON↔OFF; `shadow_decisions.jsonl` maxpile |
| R5 | courier_plans oscylacja: plan-recheck vs panel-watcher — różne env dla tego samego kodu recanon | **FAKT — env-frozen divergence** | plan-recheck ma `committed-propagation.conf` (`ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1`); panel-watcher NIE ma tego drop-inu | Zapis serializowany (plan_manager LOCK_EX → brak lost-update); SOFT tie-break, self-heal po ticku; AUTO OFF | `diff` drop-inów `/etc/systemd/system/dispatch-{plan-recheck,panel-watcher}.service.d/` |
| R6 | `courier_last_pos.json` lost-update rozłącznych cid przez wiele procesów | **FAKT — okno istnieje, niska waga** | `courier_resolver.py:171-198` — `os.replace` atomowy, BEZ fcntl; merge-by-ts chroni TEN SAM cid, nie rozłączne | Best-effort cache TTL 25min; konsument `ENABLE_GPS_AGE_DISCOUNT` OFF; lat/lon i tak ta sama | 2 procesy build_fleet równolegle → diff store |
| R7 | `global_alloc.json` kolizja na wspólnym `.tmp` | **FAKT — latentny anty-wzorzec, nie żywy** | `global_alloc_store.py:35` `tmp=f"{path}.tmp"` (nie mkstemp), `:40` replace, bez fcntl | Jedyny pisarz = timer resweep (oneshot, systemd nie nakłada instancji) | 2 instancje resweep równolegle (nie zdarza się przy oneshot) |
| R8 | Ciche błędy maskujące utratę decyzji/serializacji | **FAKT — brak w rdzeniu** | bare `except:` w rdzeniu = **0** (wszystkie 90 w `eod_drafts/`+`deploy_staging/`); serializer `shadow_dispatcher.py` 0× silent-pass; zapis pending loguje `:1387` | Typowane `except Exception` + log; fail-soft telemetria nie gubi decyzji | grep niżej |
| R9 | Retry bez końca (OSRM/panel) | **FAKT — wszystkie ograniczone** | OSRM `osrm_client.py:598,762,810` timeout 3-5s single-shot→haversine; panel `panel_client.py:325,471` max 1 retry; `:103` bg-refresh sleep(15min) | Ograniczone timeouty + fallback | — |

---

## SZCZEGÓŁY PER RYZYKO

### R1 — pending_proposals.json: kanon fcntl DOMKNIĘTY (O1 zamknięty)
Wszyscy pisarze przechodzą przez `pending_proposals_store` z `fcntl.LOCK_EX` na dedykowanym lockfile `pending_proposals.json.lock`, obejmującym cały cykl load→mutate→save, z unikalnym `mkstemp` (koniec kolizji wspólnego `.tmp`):
- **shadow_dispatcher** (`dispatch-shadow`, ACTIVE) → `upsert_proposals` pod LOCK_EX — `shadow_dispatcher.py:1384`; błąd loguje warning `:1387`. `ENABLE_PENDING_PROPOSALS_WRITE=True` → **jedyny żywy pisarz dziś**.
- **postpone_sweeper** (timer ACTIVE) → `locked_mutate` `postpone_sweeper.py:161` — ale no-op (R2).
- **telegram_approver** → `locked_save`/`locked_set`/`locked_pop` `telegram_approver.py:1768,1779,1788` — serwis **inactive+disabled**.
- **resweep `_live_apply`** → `PPS.locked_mutate` z TOCTOU-guardem `tools/pending_global_resweep.py:306-325` — **nieosiągane** (`PENDING_RESWEEP_LIVE=False` + druga bramka `live_gate_open()` `:242`).
- **panel_watcher** — tylko CZYTA (`:222`, `:452`), nie pisze.
Residuum: `telegram_approver.save_pending` = blind-overwrite (`pending_proposals_store.py:149-157`); martwe (Telegram off), kanon delta gotowy.

### R2 — postpone_sweeper: schema-mismatch NADAL na HEAD (finding 27.06 #1.8 nienaprawiony)
`postpone_sweeper.py:103-106`: `orders_state = state_machine._read_state()` zwraca PŁASKI `{oid: rec}`, a kod robi `orders_state.get("orders", {}).get(oid)` → zawsze `{}` → `current_cid=None` → gałąź `POSTPONE_RESOLVED` (`:106-110`) **strukturalnie nieosiągalna** (dodatkowo pole to `courier_id`, nie `cid`). Przy re-enable postpone: zlecenie odłożone → ręcznie przypisane → sweeper po oknie wywoła `assess_order` i wpisze DUPLIKAT propozycji dla żywo-przypisanego zlecenia. Re-emit sam jest już bezpieczny (`:161` locked_mutate). **Dziś uśpione:** `postponed_proposals.json={}`, jedyny pisarz `telegram_approver.py:3948` w wyłączonym serwisie. **ALE timer `dispatch-postpone-sweeper.timer` biega co ~minutę (uzbrojony).** Do decyzji Adriana: naprawić schemat (`orders_state.get(oid)` + `courier_id`) + zrekonstruować `order_event`, ALBO wygasić timer — przed re-enable Telegramu/postpone.

### R3+R4 — podwójne przypisanie / pile-on
Realnego podwójnego przypisania przez silnik NIE MA: `ENABLE_AUTO_ASSIGN=False`, `auto_assign_executor.entrypoint` przy OFF zwraca None (`:285`), + dry-first handshake 45s po każdej zmianie flags.json (`:179,310`) + idempotencja. Bez claim-ledgera (`ENABLE_ENGINE_CLAIM_LEDGER=False`) kolejne eventy ticku oceniają niemutowaną flotę → pile-on PROPOZYCJI (weryfikowane przez koordynatora, nie przypisania). Jedyna realna powierzchnia podwójnego przypisania = dwóch ludzi w panelu gastro (poza silnikiem). Wpis `pending` czyszczony dopiero TTL 30min, ale konsumenci/resweep filtrują `status=='planned'` (`tools/pending_global_resweep.py:368`) → przypisane ignorowane (self-heal).

### R5 — env-frozen divergence recanon (plan-recheck vs panel-watcher)
`courier_plans.json` piszą DWA procesy przez `plan_manager` mutatory pod `_locked(exclusive=True)` (`plan_manager.py:249,289,341,377,406,476,579`, `_atomic_write:69`) → **brak lost-update**. Problem to SEMANTYKA: `dispatch-plan-recheck.service.d/committed-propagation.conf` = `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1`, a `dispatch-panel-watcher` **nie ma tego drop-inu** → ten sam kod recanon regeneruje kanon trasy różnie per proces. Flaga env-frozen (nie flags.json hot-reload). Dodatkowo plan-recheck ma `unified-route-f1-f2`, panel-watcher `unified-route-f3` (różne fazy — z projektu). Waga niska (SOFT tie-break, ~4.5pp planów, self-heal, AUTO OFF). Klasa `pr-committed-prop-twin-path-gap`.

### R8 — ciche błędy: rdzeń czysty
Bare `except:` w rdzeniu silnika = **0** — wszystkie 90 wystąpień to jednorazowe skrypty `eod_drafts/2026-06-*` (85) + `deploy_staging/` (5). Lepiej niż claim z 05.07 („88→8"). Silent `except Exception: pass` (bez logu) w 9 plikach rdzenia = 49 (30 w `dispatch_pipeline.py`) — celowe fail-soft tripwire'y/telemetria. Serializer decyzji (`shadow_dispatcher`) = 0 silent-pass; zapis pending loguje warning. Decyzje nie giną cicho na granicy zapisu.

---

## PLIKI STANU × PISARZE (atomowość + serializacja)

| Plik | Pisarze (proces) | Atomowy? | Lock międzyproc.? | Ryzyko lost-update |
|---|---|---|---|---|
| `pending_proposals.json` | shadow (żywy); postpone/telegram/resweep (uśpione) | ✓ mkstemp+fsync+replace | ✓ fcntl LOCK_EX / .lock | **BRAK** — kanon PPS |
| `orders_state.json` | shadow, panel-watcher, parcel-merge, gastro_edit | ✓ mkstemp+fsync+replace + `.prev` | ✓ LOCK_EX `state_machine.py:213` | **BRAK** — RMW pod LOCK_EX (`:549`) + count-guard `:272` |
| `courier_plans.json` | panel-watcher, plan-recheck | ✓ mkstemp+fsync+replace | ✓ LOCK_EX `plan_manager.py:56` | BRAK lost-update; **oscylacja env** (R5) |
| `courier_ground_truth.json` | courier-api (1 pisarz z projektu) | ✓ replace `status_store.py:127` | ✓ LOCK_EX `:100` | BRAK (single-writer + lock) |
| `courier_last_pos.json` | build_fleet w wielu procesach | ✓ replace `:196` | ✗ brak fcntl; merge-by-ts | **NISKIE** — rozłączne cid (R6) |
| `global_alloc.json` | resweep (1 timer) | ✓ replace, ✗ **wspólny `.tmp`** `:35` | ✗ brak fcntl | latentne (single-writer) (R7) |
| `reassign_global_alloc.json` | reassign-global-select (1 timer) | ✓ mkstemp+replace `:157` | ✗ (single-writer) | BRAK |
| `postponed_proposals.json` | telegram (wyłączony) | ✓ replace `:48` | ✗ (single-writer) | BRAK (martwe) |

---

## TOP-5 REALNYCH RYZYK WYŚCIGÓW (ważone dla skali setek zleceń/d, ~62 kurierów)

1. **R2 — postpone re-emit duplikatu (uśpione, timer uzbrojony).** Najgroźniejszy „armed-on-flip": bug schema-mismatch nadal w kodzie, timer biega co minutę, aktywuje się w chwili re-enable Telegramu/postpone → duplikat propozycji dla żywo-przypisanego zlecenia. Prawdopodobieństwo dziś ~0 (postponed pusty), ale to mina rozbrajana WŁAŚNIE dźwignią, którą operator pociągnie pod stresem. Naprawić schemat albo wygasić timer przed re-enable.
2. **R5 — oscylacja courier_plans (plan-recheck↔panel-watcher).** Jedyny ŻYWY (nie-uśpiony) rozjazd na współdzielonym pliku: dwa procesy z różnym env przepisują wzajemnie kolejność podjazdów tego samego kuriera. Widoczne w konsoli/apce jako „trasa się przestawia". Waga umiarkowana (SOFT, self-heal), ale realne dziś, na każdym multi-order kurierze. Dodać `committed-propagation.conf` do panel-watcher albo udokumentować jako tick-only.
3. **R4 — pile-on propozycji bez claim-ledgera.** Żywy efekt jakościowy (nie bezpieczeństwa): koordynator widzi tego samego kuriera na kilku zleceniach jednego ticku (g_maxpile do 7). Bounded przez human-review, ale obciąża konsolę i myli. Ledger zbudowany, `ENABLE_ENGINE_CLAIM_LEDGER=False`.
4. **R6 — courier_last_pos lost-update rozłącznych cid.** Realne okno (brak fcntl, merge-by-ts chroni tylko ten sam cid), niska waga: best-effort rescue-cache, konsument `GPS_AGE_DISCOUNT` OFF, lat/lon identyczne. Istotne dopiero po kalibracji GPS_AGE_DISCOUNT ON.
5. **R7 — wspólny `.tmp` w global_alloc_store.** Dokładnie anty-wzorzec naprawiony w `pending_proposals_store` (mkstemp). Dziś nie-żywy (jeden timer-pisarz oneshot). Tania profilaktyka: unikalny mkstemp jak w PPS.

**Poza TOP-5, potwierdzone jako NIE-ryzyka współbieżne dziś:** pending dualwriter (O1 — zamknięty fcntl), orders_state lost-update (LOCK_EX + count-guard), courier_plans lost-update (LOCK_EX), ground_truth (LOCK_EX single-writer), bare-except w rdzeniu (=0), retry OSRM/panel (ograniczone), auto-assign double-assign (gated OFF + dry-first + idempotencja).

**Czego NIE zweryfikowałem osobno (HIPOTEZA/nie dociągane):** (a) czy `courier-api` jest jedynym realnym pisarzem `courier_ground_truth.json` w każdych warunkach — oparte na docstringu `courier_ground_truth.py:4,19` + locku `status_store.py:100`, nie na obserwacji dwóch procesów pod obciążeniem; (b) dokładna częstotliwość pile-onu (R4) na żywym `shadow_decisions.jsonl` — nie liczyłem, cytuję docstring `claim_ledger.py`; (c) czy istnieją NIEudokumentowane skrypty ad-hoc / at-joby piszące te pliki poza serwisami — nie audytowałem `atq` pod kątem pisarzy stanu.
