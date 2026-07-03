# 🧬 Meta-audyt — Root Cause Analysis & Architecture Roadmap

**Data:** 2026-05-07 wieczór
**Źródło:** synteza 20 findings z `STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md`
**Cel:** oddzielić problemy strukturalne od symptomatycznych + 6-mc plan ewolucji architektury

**Pytanie centralne:** czy 20 findings to 20 niezależnych problemów, czy 5 fundamentalnych błędów architektonicznych ujawniających się 20 razy?

**Teza:** to drugie. Większość findings to **symptomy 6 strukturalnych braków**. Punktowy fix każdego symptomu da jednorazową ulgę — ale klasa wróci.

---

## Strukturalne vs symptomatyczne

| Symptom (= fix per finding) | Struktura (= fix klasy) |
|---|---|
| F3 `_COORDS` nie reloaduje | **Brak systemowego mechanizmu invalidacji caches** |
| F1 corrupt JSONL | **Brak dyscypliny single-writer** |
| F2 cron silent fail | **Observability covers anticipated failures only** |
| F11 `EXCLUDE_KEYWORDS` hardcoded | **Config-as-code conflated z config-as-data** |
| F4 per-process cache drift | **Brak shared coordination layer** |
| F8 brak race test | **Test infrastructure jako reaktywna, nie property-based** |
| F18 replay rerunning current code | **Brak event-sourced thinking** |
| F20 brak compile-time ownership | **Filesystem jako ad-hoc IPC bus** |

**Kluczowa obserwacja:** dispatch_v2 ewoluował organicznie przez sprinty V3.x rozwiązując konkretne incidenty. To dało **doskonałą jakość per-fix** (V3.27.5 Path B, V3.28 parser_health 4-layer, courier_admin atomic 4-file rollback). Ale **architektura całościowa nigdy nie była refactorowana** — wciąż używa modelu z V3.0 (JSON pliki + module-level globals + cron jobs), który dla 1 tenanta 30 kurierów pracuje, ale kompounduje koszt utrzymania.

---

## 6 fundamentalnych klas problemów (root cause groups)

### RC1. Filesystem jako IPC bus między procesami

**Przynależą:** F1 (learning_log), F4 (cache divergence), F5 (panel_bg_refresh per-proc), F16 (district cache), F20 (no ownership)

**Prawdziwa przyczyna:** Python multi-process model = każdy proces ma własną pamięć. Współdzielony stan musi przejść przez OS (fcntl, SQLite WAL, signals). Dispatch_v2 traktuje pliki JSON jako "wspólną pamięć" — ale każdy proces ma własną kopię w cache. Wynik: 4 procesy × N JSON files × M caches = M*N stale states.

**Długoterminowy skutek:** każdy nowy serwis (Bolt Food, daily_accounting, shift_notify) dorzuca **kolejne odbicie** współdzielonego stanu. Liczba stale-state interakcji rośnie kwadratowo z liczbą procesów. Multi-tenant = N tenantów × M procesów = niewykonalne bez centralizacji.

**Co wróci po punktowych fixach:** doda się dispatch-bolt-food.service, on znów otworzy `restaurant_coords.json` jako swój `_COORDS` singleton, znów stale, znów restart-only fix. Lekcja #47 i #48 (recurring bug = incomplete fix) są dokładnie o tym.

**Idealna architektura:** **single source of truth z explicit invalidation broadcast**. Ktokolwiek pisze stan, robi to przez 1 punkt API. Ten punkt emituje invalidation event. Konsumenci albo czytają fresh (bez cache) albo subskrybują invalidation.

**JSON+SQLite czy trzeba migrować?** SQLite WAL dla `events.db` działa świetnie. JSON file pattern dla mutable state (`orders_state.json`, `manual_overrides.json`, `courier_plans.json`) jest **niezdrowy at scale**. Postgres dla canonical state + Redis dla ephemeral coordination to logiczna ewolucja.

---

### RC2. Cache invalidation as architectural afterthought

**Przynależą:** F3 (_COORDS static), F6 (geocode no TTL), F7 (OSRM 60min stale peak), F11 (keywords hardcoded), F4/F16

**Prawdziwa przyczyna:** caches dodawane reaktywnie jako performance fix. Każdy z innym wzorcem invalidacji: mtime check (courier_tiers), TTL (OSRM, R-04), nigdy (geocode, _COORDS), restart-only (district cache). Brak globalnej semantyki.

**Długoterminowy skutek:** każda zmiana w configu (nowa restauracja, R-04 promotion, schedule update) wymaga ręcznej weryfikacji "czy każdy proces na pewno reload'ował?". To "klejenie" tradycji wokół tribal knowledge.

**Co wróci:** dodanie dynamicznego flagu z `flags.json` → niektóre moduły to widzą natychmiast (`common.flag()` reads fresh), inne mają derived caches które nie. Half-broken hot-reload robi gorzej niż brak hot-reload (Adrian klika flag, część systemu reaguje, część nie — debug pol-doby).

**Idealna architektura:** **explicit cache contract** per cache: invalidation trigger (TTL / mtime / event), staleness tolerance, fallback policy. Jeden config-broadcast bus (events.db `CONFIG_RELOAD` event lub Redis pub/sub).

**JSON+SQLite?** Ad-hoc invalidation jest niemożliwe do utrzymania. Redis pub/sub jest **prawie darmowy** (1 docker container, 50MB RAM) i rozwiązuje tę klasę kompletnie. Mocno rekomenduję.

---

### RC3. Observability covers anticipated failures only

**Przynależą:** F2 (cron silent), F12 (Sheets fail-open), F13 (GPS_STALE defined no emitter), F15 (silent except)

**Prawdziwa przyczyna:** alerty dodawane reaktywnie po każdym incydencie. Parser_health to model: 4-layer defense, motion-aware, hour-of-day suppressed, 4 anomaly checks. Ale to **jeden komponent**. Reszta systemu nie ma równoważnej obserwowalności. Jeśli serwis przestaje działać entirely (cron mart, panel_client w infinite loop, Sheets API down) — silent.

**Długoterminowy skutek:** Adrian musi być **active observer**. Każdego dnia spogląda w logi, panel, kuriery. Ziomek 90% autonomous (Tydzień 4 cel) wymaga eliminacji tego — system sam zauważa że "coś jest nie tak". Bez tego cel autonomy = nieosiągalny.

**Co wróci:** każdy nowy timer/serwis/integracja będzie kolejnym blind spot. Ktoś za 6 miesięcy doda integrację z Bolt Food API — ona umrze cicho gdy Bolt API zmieni format.

**Idealna architektura:** **liveness contract per komponent** = każdy element systemu ma odpowiedź na 3 pytania:
1. Kiedy ostatnio działałem? (`last_success_at`)
2. Co produkowałem? (`metrics: events emitted, decisions made, cache hits`)
3. Co jest nie tak? (`anomaly checks per component`)

Centralny dashboard (`/health/all`) lub lepiej: external monitoring (Grafana + Loki) scrapuje te endpointy.

**JSON+SQLite?** Health metrics nie potrzebują DB — file-based `cron_health.json` z atomic writes wystarczy dla początku. Później można promować do Prometheus.

---

### RC4. Append-only logs without writer discipline

**Przynależą:** F1 (learning_log triple writer), F10 (ghost duplicate audit), F19 (event ordering ms ties)

**Prawdziwa przyczyna:** append-only architecture jest powszechnie znana jako rozwiązanie multi-writer (kafka, kafka-like). Ale **wymaga jednego z dwóch**: (a) single writer per stream, (b) atomic append <PIPE_BUF z fcntl. Dispatch ma JSONL gdzie linie >4KB i 3 writerów bez locków → losowe corrupted lines.

**Długoterminowy skutek:** R-04 evaluator, LGBM validation, daily briefing — wszystkie konsumują learning_log. Decyzje strategiczne (kogo promote, kogo retire) **bazują na corrupted data**. Tier promotion robi się stochastically.

**Co wróci:** dodanie 4-go writera (np. auto_proximity Faza 7 logging) zwiększy collision rate. Maskowanie corruption za try/except w consumer'ach (JSON.parse failure → skip line) ukryje skalę problemu.

**Idealna architektura:** **events.db audit_log promote do first-class authority**. Wszystkie 3 writery dispatch'ują przez `event_bus.emit()` z `AUDIT_TYPES`. Jeden writer (`event_bus`) wie o swojej dyscyplinie (BEGIN IMMEDIATE, INSERT OR IGNORE). JSONL learning_log → deprecated → read-only freeze → delete.

**JSON+SQLite?** SQLite już to robi dobrze. Migration polega na **przekierowaniu writerów**, schemat już jest. ~1 dzień pracy.

---

### RC5. State ownership emergent, not enforced

**Przynależą:** F20 (no compile-time), F1/F10 (multi-writer), F4 (per-proc), F5 (per-proc bg_refresh), F11 (config scattered)

**Prawdziwa przyczyna:** Python tradycja "consenting adults" + brak typing dla side effects. Każdy moduł może `open('orders_state.json', 'w')`. Convention chroni (ZIOMEK_MASTER_KB.md mówi "tylko state_machine pisze do orders_state"). Ale **convention łamana co kwartał** gdy nowy developer / nowy moduł.

**Długoterminowy skutek:** onboarding nowego CC sesji (lub developera) = czytanie 20 plików folkloru żeby zrozumieć kto pisze do czego. Z3 cel "buduj na lata" znaczy że za 2 lata folklor ten zmienił się z V3.x notatek na coś niepoznawalnego — i nikt nie pamięta dlaczego.

**Co wróci:** każdy `bolt_food_integration.py` doda swoje calls do `orders_state.json` "bo szybciej niż nauczyć się state_machine API". Race + corruption guaranteed.

**Idealna architektura:** **state_io.py jako jedyne API**. Wszystkie inne moduły **importują z `core/`**, nie otwierają plików. Code review enforces. CI test grep'uje `open.*orders_state` → fail build. Plus typed Python (mypy strict) na boundary.

**JSON+SQLite?** Boundary jest agnostic backendowo. `state_io.py` może wewnętrznie używać JSON dziś, Postgres jutro — callerzy nie zauważą.

---

### RC6. Replayability fragmented

**Przynależą:** F8 (race test gap), F18 (replay current code), partly F1 (corrupt log = lost history)

**Prawdziwa przyczyna:** dispatch przechodzi gigantyczny refactor co tydzień (V3.27.x mini-versions). Ale audit trail nie pozwala odtworzyć "co Ziomek faktycznie myślał o orderze X w czasie T". `replay_failed.py` re-runs current code on past inputs — pokaże PASS bo bug już naprawiony. Real verdict zaginął.

**Długoterminowy skutek:** post-mortem incydentów stają się "best guess". Audit przed multi-tenant rollout (czy decyzje są fair, czy tier system nie dyskryminuje?) = niewykonalny bo dane historyczne mówią o innym kodzie niż real-time decyzje.

**Co wróci:** każda nowa wersja = strata "ground truth" dla orderów przed nią. ML training z `learning_log` używa danych z mieszanki kodu V3.10-V3.28 (różny scoring, różne thresholds) — model uczy się **artefakty refactor history**, nie real patterns.

**Idealna architektura:** **event sourcing dla decisions**. `shadow_decisions.jsonl` (już istnieje) promote do canonical: każda decyzja zapisuje **full snapshot** (input fleet, input order, full pipeline output, code_version_sha). Replay = `SELECT * WHERE order_id=X` → masz dokładnie co Ziomek widział. Re-runs nigdy nie potrzebne.

**JSON+SQLite?** Postgres z `decisions` table z jsonb kolumną dla snapshot. JSONL może zostać dla kompatybilności (export from DB).

---

## TOP 5 ROOT CAUSES

1. **Filesystem-as-IPC bez koordynacji** (RC1) — fundament wszystkich multi-process problemów
2. **Brak invalidation bus** (RC2) — każdy cache osobny ekosystem inwalidacji
3. **Observability tylko dla anticipated failures** (RC3) — Ziomek nie może być autonomous bez self-monitoring
4. **State ownership emergent, nie enforced** (RC5) — folklor zamiast kontraktu
5. **Replay = re-execute current code** (RC6) — zerowa zdolność post-mortem analysis przy szybkim refactor cycle

---

## TOP 5 ARCHITECTURAL MIGRATIONS (kolejność = ROI)

### M1. Postgres jako canonical state store [3-4 tyg]
**Co migrujesz:** `orders_state.json`, `courier_plans.json`, `learning_log.jsonl`, `shadow_decisions.jsonl`, `pending_proposals.json`, `tier_suggestions.json`, `tier_evolution.jsonl`
**Co zostaje JSON:** `flags.json`, `kurier_*.json` (rzadko zmienne config), `restaurant_coords.json` jako cache layer
**Korzyść:** rozwiązuje RC1 + RC4 + RC5 + RC6 jednym ruchem. ACID transactions, schema enforcement, query power, native pub/sub (LISTEN/NOTIFY).
**Koszt:** Postgres operacyjny (5GB instance Hetzner), schemat, dual-write staging period 2 tyg.
**Multi-tenant gain:** `tenant_id` kolumna, jedna instancja DB obsługuje 5 cities.

### M2. Redis dla ephemeral coordination [1-2 tyg]
**Co dostajesz:** distributed locks, pub/sub, leader election, ephemeral queues
**Use cases natychmiast:**
- `panel_bg_refresh` single-instance (Redis lock z TTL = 30min, leader bierze)
- Cache invalidation broadcast (`PUBLISH config:reload {file}`, każdy proces SUBSCRIBE)
- Cron leader election (multi-host ready)
**Koszt:** Redis docker (300MB RAM)
**Korzyść:** rozwiązuje RC2 całkowicie + część RC1 (per-proc singletons → single-system singletons)

### M3. Event sourcing dla decisions [2 tyg post-Postgres]
**Schema:** `decisions` table — każda decyzja Ziomka jako immutable row z full input/output JSONB snapshot + `code_version_sha` + `pipeline_features`
**Replay engine:** `replay.py --order-id X` → fetch decyzję → reconstruct OR rerun na request
**Korzyść:** rozwiązuje RC6 fundamentalnie. Audit pre-multi-tenant rollout staje się trywialny.
**Koszt:** ~50-100 GB / rok przy 300 orderów × 5 propozycji × 50KB snapshot. Tani.

### M4. State I/O boundary (`core/state_io.py`) [1 tyg]
**Pre-condition dla M1.** Wszystkie reads/writes do canonical state przez `state_io.{get,update,upsert,delete}_*` API. Initial implementation: thin wrapper na obecne JSON files. Po M1: same wrappers, Postgres backend.
**Korzyść:** rozwiązuje RC5. Migracja do Postgres staje się **zmianą backendu**, nie zmianą call sites.
**Koszt:** 1 tydzień refactoringu + CI grep test (`open.*orders_state.*"w"` poza state_io = fail build).

### M5. Liveness contract dla każdego komponentu [2 tyg]
**Każdy serwis/timer/cron pisze:** `last_success_at`, `last_attempt_at`, `last_error`, `metrics_per_minute` do `cron_health.json` lub Postgres `service_health` table
**External:** Grafana scrape `/health/cron` endpoint (extend istniejące :8888) → alert via Telegram gdy stale.
**Korzyść:** rozwiązuje RC3 całkowicie. Eliminuje **klasę silent-fail bugów** — nie patch per-cron, tylko framework.

---

## TOP 5 REFACTORS HIGHEST ROI (krótkoterminowe, weeks 1-4)

| # | Refactor | Effort | ROI | Adresuje |
|---|---|---|---|---|
| 1 | `OnFailure=` template service + cron-watchdog timer | 30 min | **EKSTREMALNY** — eliminuje całą klasę silent-fail bugów dziś | F2, częściowo F12 |
| 2 | Consolidate 3 learning_log writers → events.db audit_log | 6h | **WYSOKI** — eliminuje corruption, jednolite query API, retention strukturalny | F1 (audit RC4) |
| 3 | `core/state_io.py` boundary (read-only first) | 1 dzień | **WYSOKI Z3** — pre-condition dla wszystkich migracji, eliminuje folklor | F20 (RC5) |
| 4 | events.db `CONFIG_RELOAD` event + 4 procesy SUBSCRIBE | 3h | **WYSOKI** — fixes per-proc cache drift bez Redis (jeszcze) | F4, F16, częściowo F11 |
| 5 | `_COORDS` mtime-reload w panel_watcher cycle | 30 min | **ŚREDNI ale instant** — Z3 unblocker dla restaurant onboarding | F3 |

**Sumaryczny effort: ~3 dni pracy.** System staje się dramatycznie odporniejszy.

---

## TOP 5 RZECZY KTÓRYCH **NIE WARTO RUSZAĆ**

1. **`event_bus.py`** — deterministic event_id + INSERT OR IGNORE + WAL + BEGIN IMMEDIATE = **architektonicznie poprawne**. Tylko rozszerzaj (więcej AUDIT_TYPES), nie przerabiaj.

2. **`state_machine.py:312-326` V3.27.5 Path B** — działa, naprawiło 13.4% bug rate, każda zmiana = ryzyko regresji. Może doczekać rewrite na Postgres-backed (M1) ale obecny kod jest poprawny.

3. **`plan_manager.py` fcntl + CAS version** — robust, complex, well-tested. Optimistic concurrency to subtle topic. Nie naprawiaj co nie zepsute.

4. **`courier_admin.add_new_courier()` 4-file atomic z rollback** — to **wzorzec do skopiowania**, nie do przepisania. Pattern dla every multi-file write w przyszłości.

5. **`parser_health.py` 4-layer defense** — V3.28 sprint, motion-aware, set-detection, 7-day window. 4 sprinty iteracji, dojrzały. Tylko monitoruj, nie tknij.

**Kontr-intuicyjnie nie ruszać:** `flags.json` hot-reload via `common.flag()` — proste, działa, nie warto promować do `feature_flags` framework dopóki masz <50 flag.

---

## 6-miesięczna roadmapa architektury

### Miesiąc 1 (maj-czerwiec 2026) — Quick wins + foundation
**Cel:** wyeliminować silent-fail klasy + przygotować boundaries
- **W1:** Cron health framework + `OnFailure=` template alerts (F2 + RC3)
- **W2:** Learning_log → events.db audit_log consolidation (F1 + RC4)
- **W3:** `core/state_io.py` boundary read paths
- **W4:** `core/state_io.py` write paths + CI grep enforcement (RC5)

**Deliverables:** zero silent-fail dla cronów, learning_log deprecated, wszystkie procesy używają state_io API.

### Miesiąc 2 (czerwiec-lipiec) — Cache discipline
**Cel:** rozwiązać RC2 całkowicie
- **W1:** events.db `CONFIG_RELOAD` event + subscribe handlers w 4 procesach
- **W2:** Migrate `_COORDS`, `_COURIER_TIERS_CACHE`, `_RESTAURANT_DISTRICT_CACHE` na invalidation pattern
- **W3:** TTL discipline — geocode 30d, OSRM peak 15min/off-peak 60min, recipes
- **W4:** V3.27.5 race tests + replay snapshot infrastructure (event sourcing prep)

**Deliverables:** wszystkie caches mają explicit invalidation contract. Nowy cache = wymaga decyzji kontraktu.

### Miesiąc 3 (lipiec-sierpień) — Postgres prep
**Cel:** zaadresować RC1 + RC5 + RC6 startem M1
- **W1:** Schema design (orders, couriers, decisions, plans, events, audit) + migration scripts dry-run
- **W2:** Postgres setup + dual-write (state_machine pisze JSON + Postgres)
- **W3:** Read migration — query layer w state_io przełącza na Postgres dla 1 typu (orders), test 3 dni shadow
- **W4:** Read migration ciąg dalszy — courier_state, plans

**Deliverables:** Postgres LIVE, dual-write, readers korzystają.

### Miesiąc 4 (sierpień-wrzesień) — Postgres rollout
**Cel:** zakończyć M1 + start M3
- **W1:** Cut-over write paths — JSONy stają się read-only fallback
- **W2:** Decisions migration (shadow_decisions, learning_log → table)
- **W3:** Plans migration + remove courier_plans.json + plan_manager rewrite
- **W4:** Bake & cleanup — usuń dead code, deprecated paths

**Deliverables:** RC1 + RC5 + RC4 + RC6 rozwiązane fundamentalnie. Postgres jako single source of truth dla mutable state.

### Miesiąc 5 (wrzesień-październik) — Redis coordination
**Cel:** M2 + finalizacja RC2
- **W1:** Redis setup + distributed lock library (`redis-py` Lock)
- **W2:** `panel_bg_refresh` single-instance via Redis lock + leader election
- **W3:** Cache invalidation pub/sub (replace events.db `CONFIG_RELOAD`)
- **W4:** Cron leader election (multi-host ready) + scheduler resilience

**Deliverables:** RC2 zamknięty. System gotowy na multi-host deployment.

### Miesiąc 6 (październik-listopad) — Multi-tenant readiness
**Cel:** Restimo / Wolt Drive / Warszawa onboarding-ready
- **W1:** `tenant_id` column wszędzie + RLS policies (Row-Level Security)
- **W2:** Per-tenant `flags.json` + secrets management
- **W3:** Per-tenant Telegram bots, group chats, courier rosters
- **W4:** Bolt Food integration prototype + Restimo schema mapping

**Deliverables:** druga gastronomia może być onboardowana w 1 tydzień zamiast miesiąca.

---

## Decyzja architektoniczna: czy ewoluować?

**Odpowiedź na 7 wariantów ewolucji:**

| Wariant | Werdykt | Kiedy |
|---|---|---|
| **Event-sourced architecture** | ✅ **TAK, częściowo** — dla decisions only (M3). Pełne event-sourcing dla całego systemu = overkill. | Miesiąc 4 |
| **Actor model** | ❌ **NIE** — Python brak dojrzałego frameworka, retraining cost wysoki, dispatch nie ma akkurat dobrej domain-fit (orders ≠ aktor, są ephemeral) | nigdy |
| **Centralized state service** | ✅ **TAK** — to jest dokładnie M4 + M1 razem. `state_io.py` boundary + Postgres = de facto centralized state service | Miesiąc 1-4 |
| **Redis-backed coordination** | ✅ **TAK** — M2. Bardzo wysokie ROI, niskie ryzyko. | Miesiąc 5 |
| **PostgreSQL ownership model** | ✅ **TAK, primary** — M1. Fundament całej remediacji. | Miesiąc 3-4 |
| **Append-only event architecture** | ✅ **TAK, dla audit** — events.db już to robi częściowo, M3 promuje do first-class. Pełne CQRS = niepotrzebne. | Miesiąc 4 |
| **Pozostać przy JSON+SQLite** | ❌ **NIE** — at scale 30 kurierów × 1 miasto OK; przy multi-tenant Q3 2026 będzie gnijące. JSON dla configów ✓; JSON dla mutable state ✗. | n/a |

**Sumaryczny target architektury (miesiąc 6):**
```
canonical state:    PostgreSQL (orders, couriers, decisions, plans, events)
ephemeral coord:    Redis (locks, pub/sub, leader election)
configs:            JSON files (flags, kurier_*, restaurant_coords, schemas)
audit/decisions:    Postgres events table (event-sourced subset)
external API:       FastAPI (state_io REST wrapper, multi-host scaling)
```

---

## Anti-pattern do unikania w migracji

1. **Dual-write jako długoterminowe rozwiązanie** — must mieć plan cut-over, max 4 tyg dual-write per typ
2. **"Tylko tymczasowo" workarounds** — V3.27.7 override.conf był "tylko tymczasowo", żyje 7 dni i będzie żył kwartał. Każdy workaround = nowa konwencja folklor
3. **Nowe abstraction layer bez killing starego** — `state_io.py` może żyć obok bezpośrednich open() przez 1-2 sprinty MAX, potem CI enforcement
4. **Migration bez observability** — zanim uruchomisz dual-write, miesz Liveness contract (M5) gotowy
5. **Z2 capitulation pod presją Z1** — Z2 wins (Lekcja #58). Aggressive 6-mc roadmapa wymaga dyscypliny w odmawianiu shortcuts.

---

## Końcowa teza

Dispatch_v2 jest **inżyniersko świetny per-fix**, ale **architektonicznie ad-hoc per-system**. Sprinty V3.x dały dziesiątki lekcji (Lekcje #1-#88) — każda z nich rozwiązała jedno objawienie jednej z 6 root cause groups. Bez konsolidacji w architectural migrations, system jest sumą V3.x patches, nie systemem zaprojektowanym.

**Kluczowy punkt zwrotny:** moment przejścia z 1 tenanta do 2-go (Restimo lub Wolt Drive Q3 2026). Po tym momencie cofnięcie się do "JSON files + per-process caches" staje się **operacyjnie niemożliwe** — incident-rate × 2 tenanty × 2× ekspozycji folklor = nie utrzymasz.

**Dlatego 6-mc roadmapa jest agresywna ale racjonalna.** Pre-Restimo onboard musisz mieć M1 (Postgres) + M2 (Redis) + M5 (liveness) zrobione. Inaczej onboarding drugiego tenanta odsłoni 6 root causes naraz w produkcji. Każda następna gastronomia = znów odsłonięcie.

**Pierwsze 30 minut:** F2 cron-watchdog. Najlepsze wartość/wysiłek w całym 6-miesięcznym programie. Zacznij dziś.

---

## Cross-ref

- **Companion documents w tym folderze:**
  - `STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md` — szczegółowa lista 20 findings (F1-F20) z scenariuszami i fixami punktowymi
  - `ARCHITECTURE_AUDIT_2026-05-07.md` — god objects, fan-in, except count, 10 modułów Tier A/B/C
- **Memory:**
  - `lessons.md` #32 (silent except), #47 (service-scoped audit), #48 (recurring bug = incomplete fix), #58 (Z2 supremacy), #71 (decoupled lifecycles), #80-#83 (firmowe konto)
  - `tech_debt_backlog.md` — 18/22 DONE, 5 P2/P3 pozostałe
- **Re-audit cadence:**
  - **Pre-Faza 7 100% flip** (~Tydzień 4, ~30.05): zweryfikuj F2, F1, F8, F5 zrobione
  - **Pre-Restimo onboarding** (Q3 2026): zweryfikuj M1+M2+M5 LIVE
  - **Post-Restimo W2** (Q3 2026): re-run pełen audyt — które klasy dalej żyją?
