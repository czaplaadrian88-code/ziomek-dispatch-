# AUDYT 2.0 — SWEEP: parser panelu + API akcji konsoli

**Lane:** PARSER PANELU (`panel_html_parser.py` ↔ `panel_client.py`) + API AKCJI KONSOLI (`nadajesz_clone/panel/backend/app/api/`)
**Tryb:** READ-ONLY wobec produkcji. Zero edycji kodu/flag/env, zero restartów, zero POST. Dozwolone: odczyt, `journalctl`, `systemctl show`, GET `:8888/health/parser`.
**Data:** 2026-07-01/02
**Metoda:** odczyt kodu + efektywne env per proces (`systemctl show -p Environment`) + oracle health endpoint + 30-dniowy journal + próbka żywego stanu (read-only).

---

## TL;DR — TOP-5

1. **WERDYKT USE_V2_PARSER: rozjazd config REALNY, ale MARTWY w praktyce (🧊).** `dispatch-panel-watcher` = V2 (env `USE_V2_PARSER=1`), `dispatch-shadow` = V1 (brak env → default `"0"`). ALE **shadow NIGDY nie parsuje HTML** (`shadow_dispatcher.py:9` docstring: „nie dotyka panel_client"; tylko pre-warm `login()`). Jedyny żywy parser HTML→stan to `panel_watcher` (V2). Rozjazd nie jest load-bearing dla decyzji.
2. **🔥 P1 — `gastro_assign.py:11` zaszyty `_WARSAW = timezone(timedelta(hours=2))`.** Konwersja HH:MM→minuty (linia 153) używa STAŁEGO offsetu +2. Poprawny latem (CEST), BŁĘDNY zimą (CET=+1) → czas odbioru z ołówka HH:MM konsoli policzy się o **60 min źle** po zmianie czasu (~2026-10-26). Teraz uśpione (lipiec = +2).
3. **🔥 P1 — `parser_health` monitoruje TYLKO licznik** (order_ids/active_ids/assigned). Częściowy strukturalny break kolumn kurierów (`widok_kurier`/`name_kurier`/`zlec` — rename klasy/atrybutu) → `courier_packs`/`courier_load` puste → **kurierzy wyglądają na wolnych, NIEWIDOCZNE dla health** (echo incydentów V3.13/V3.15 „phantom free").
4. **🧊 P2 — V1 legacy `46\d{4}` (`panel_client.py:354`) = 100% MARTWY.** Wszystkie 357 bieżących ID mają prefiks `48` (próbka żywa). Latent mina: gdyby ktoś dodał parse do procesu shadow (USE_V2_PARSER=0), V1 zwróciłby **0 zleceń** + `ENABLE_V2_SHADOW_COMPARE=1` spamowałby „V2_SHADOW_DELTA 100% v1=0".
5. **🧊 P2 — `coordinator.py:731 /create` BRAK idempotency** (`delivery_id = timestamp`), w przeciwieństwie do `dispatch.py:240 POST /orders` (409 na duplikat klucza). Double-submit koordynatora → 2 zlecenia w gastro.

**Architektura API akcji = zdrowa.** Wszystkie zapisy albo delegują do KANONICZNYCH narzędzi silnika przez subprocess w venv Ziomka (Z3: `manual_overrides`, `coordinator_time_recheck.enqueue`, `flags_admin`, `parcel_assign`), albo idą do gastro przez ten sam API co silnik (reconcile domyka pętlę PANEL_OVERRIDE). Panel NIGDY nie pisze `flags.json`/`orders_state`/`manual_overrides` bezpośrednio. Auth: token→401, rola `ziomek_admin`→403 na WSZYSTKICH akcjach konsoli.

---

# CZĘŚĆ A — PARSER PANELU

## A0. WERDYKT USE_V2_PARSER (domknięcie luki Audytu 1.0)

**Pytanie 1.0:** „shadow=V1 vs watcher=V2 = PLAUSIBLE, env nie zmierzony". **Zmierzono.**

### Efektywne env per proces (`systemctl show -p Environment`)
| Proces | `USE_V2_PARSER` | Efektywny parser (kod) | Parsuje HTML? |
|---|---|---|---|
| `dispatch-panel-watcher` | **`=1`** (jawnie w unicie) | **V2** (`panel_html_parser.parse_panel_html_v2`) | **TAK** — `panel_watcher.py:2452-2453` (jedyny żywy) |
| `dispatch-shadow` | **brak** → default `"0"` (`panel_client.py:93`) | V1 (`_parse_panel_html_v1_legacy`) | **NIE** — tylko pre-warm `login()` (`shadow_dispatcher.py:1547`) |

- Brak `EnvironmentFiles` w obu unitach (puste). Wartość z `systemctl show -p Environment` = efektywna (drop-iny wmergowane). Drop-iny watchera (11 plików) NIE zmieniają `USE_V2_PARSER`; shadow (4 drop-iny) też nie.
- `flags.json` NIE steruje parserem — to zmienne env module-level czytane RAZ przy imporcie (`panel_client.py:93,96`).

### Dowód że rozjazd jest MARTWY
- `shadow_dispatcher.py:9` (docstring): *„Nie emituje żadnych eventów, **nie dotyka panel_client**, nie wysyła Telegramów."*
- Grep żywych callerów `fetch_panel_html`/`parse_panel_html`: **wyłącznie** `panel_watcher.py:2452-2453` + `health_check()` (`panel_client.py:786`, wołany z `panel_watcher.py:2591` — ten sam proces V2) + `extract_restaurant_addresses.py` (narzędzie offline, nie serwis).
- Pozostałe serwisy (`dispatch-telegram`, `dispatch-sla-tracker`, `dispatch-czasowka`, `dispatch-plan-recheck`, `dispatch-gps`, `courier-api`) — ZERO callerów parse/fetch HTML. `pre_proposal_recheck` używa `fetch_order_details` (JSON pojedynczego zlecenia), NIE listy HTML.
- Health endpoint `:8888` żyje WEWNĄTRZ watchera (`panel_watcher.py:2614 start_health_endpoint()`). Oracle: `"parser_version": "v2"`. `record_tick_full(_parser_health, stats, parsed)` (`panel_watcher.py:2680`) dostaje dict V2.

### WERDYKT
> **Rozjazd USE_V2_PARSER jest REALNY na poziomie konfiguracji, ale NIE-LOAD-BEARING.** W systemie istnieje DOKŁADNIE JEDEN żywy parser HTML→stan: `panel_watcher`, uruchomiony na **V2**. `dispatch-shadow` konsumuje stan zapisany przez watcher (orders_state / panel_packs_cache), sam nie parsuje — jego `USE_V2_PARSER=0` jest bezczynne. **Żadne dane produkcyjne dispatchu nie są parsowane V1.** Obawa Audytu 1.0 → degradacja z „PLAUSIBLE" do „config-divergence, inert". Pozostaje latent mina (patrz A1/🧊 P2).

---

## A1. FINDINGI — PARSER

### 🔥 P1 — parser_health widzi TYLKO liczbę, nie pola (partial-break blind)
**Plik:** `parser_health.py:104-113` (4 checki) + `panel_html_parser.py:133-151` (courier_packs/load)
**Dowód:** 4 anomaly-checki opierają się WYŁĄCZNIE o liczności: (1) `ZERO_OUTPUT` na `orders_in_panel`, (2) `DELTA_SPIKE` na count, (3) `STUCK` na wariancji count, (4) `ASYMMETRY` = `len(assigned_ids − order_ids)`. Żaden nie sprawdza czy `courier_packs`/`courier_load`/`rest_names`/`pickup_addresses`/`delivery_addresses` są sensownie wypełnione.
**Ryzyko:** panel gastro zmienia klasę `widok_kurier` / `name_kurier` / atrybut `id="zlec_"` → `courier_packs`={} (parser cicho zwraca puste), ale `order_ids` (z JS `id:`) nienaruszone → **licznik zdrowy, health `healthy`, a Ziomek widzi kurierów z bagiem jako wolnych**. To dokładnie klasa V3.13/V3.15 (phantom-free/assignment-lag), tyle że wywołana strukturą HTML, nie stanem. Cross-source check (`panel_html_parser.py:171-192`) porównuje tylko DWA źródła TEGO SAMEGO pola (order_ids DOM vs JS) — nie dotyka packs/load.
**Waga P1:** wysoka szkodliwość (błędna alokacja floty), średnie prawdopodobieństwo (zależne od zmiany HTML panelu, poza naszą kontrolą — Laravel gastro).

### 🧊 P2 — V1 legacy `46\d{4}` martwy + latent mina w procesie shadow
**Plik:** `panel_client.py:354` (`re.findall(r"id:\s*(46\d{4})", ...)`) + `panel_client.py:93,96`
**Dowód:** żywa próbka `orders_state.json` = 357 kluczy, **100% prefiks `48`** (np. `484521`), 0× `46`. Regex V1 łapie ZERO. Oracle: `orders_count=255` (V2). Rollover 46→47 był 2026-05-01, dziś jesteśmy na 48xxxx.
**Ryzyko (latent):** `dispatch-shadow` ma `USE_V2_PARSER=0` + `ENABLE_V2_SHADOW_COMPARE` default `"1"`. Dziś bezczynne (shadow nie parsuje). Ale gdyby KTOKOLWIEK dodał parse HTML do shadow (albo do innego procesu bez env), dostałby `order_ids=[]` (V1) i log-spam „V2_SHADOW_DELTA 100.0%: v1=0 v2=255". Fallback `parse_panel_html` przy `USE_V2_PARSER=1` łapie wyjątek→V1 (`panel_client.py:440-442`) — czyli w watcherze awaryjny V1 też dałby 0 zleceń (ale to defense-in-depth, akceptowalne bo V2 stabilne).
**Rekomendacja (nie w tym audycie):** albo usunąć V1 (skoro V2 = kanon od tygodni), albo dać `USE_V2_PARSER=1` także `dispatch-shadow` dla spójności env i wyzerować latent.

### 🧊 P3 — `html_times` = Warsaw wall-clock, parser TZ-agnostyczny
**Plik:** `panel_html_parser.py:129-131` (V2) / `panel_client.py:373-375` (V1)
**Dowód:** parser wyciąga surowe `\d{1,2}:\d{2}` jako `[created_warsaw, pickup_warsaw]` — dwa pierwsze czasy z bloku, bez konwersji TZ. Kanon (CLAUDE.md Panel API): `czas_odbioru_timestamp`=Warsaw, `created_at`=UTC. Ryzyko Warsaw/UTC NIE leży w parserze (on tylko przepisuje stringi), lecz downstream w `normalize_order`/`_czas_kuriera_to_datetime` (`panel_client`, `state_machine`). **Poza zakresem parsera**, notuję dla kompletności mapy TZ.

### 🧊 P3 — V2 `closed_ids` liczone nad RAW `html` (nie `html_clean`)
**Plik:** `panel_html_parser.py:157` (`re.finditer(..., html, ...)` — RAW, gdy reszta używa `html_clean` po strip SVG)
**Dowód:** identyczna asymetria jest w V1 (`panel_client.py:398`), więc **parytet V1↔V2 zachowany**. `data-idkurier` nie występuje w SVG → benign. Notka: jedyna niespójność wejścia między polami parsera.

## A2. PARYTET PÓL V1 ↔ V2 (diff struktur)
Oba zwracają **identyczne 10 kluczy** (`order_ids, assigned_ids, unassigned_ids, rest_names, courier_packs, courier_load, html_times, closed_ids, pickup_addresses, delivery_addresses`). Różnice:
| Pole | V1 | V2 | Ocena |
|---|---|---|---|
| `order_ids` regex | `46\d{4}` (martwy) | `\d{5,7}` universal (`panel_html_parser.py:87`) | **TO jest fix** — jedyna znacząca różnica |
| walidacja ID | brak | `_is_valid_order_id()` na KAŻDYM polu | V2 twardsze (odrzuca <5/>7 cyfr) |
| reszta technik | regex | identyczny regex | parytet OK |
**Wniosek:** parytet pełny; jedyna materialna divergencja to `order_ids` (V1 martwy, V2 działa). „PARSER_STUCK" historyczny (count plateauje wieczorem) zaadresowany przez `active_ids = order_ids − closed_ids` (motion-aware, `parser_health.py:80-93`).

## A3. ODPORNOŚĆ NA ZMIANĘ HTML (co pęknie → czy health złapie)
| Zmiana panelu gastro | Co pęknie | parser_health złapie? |
|---|---|---|
| Format JS `id: N` → `"id":N` / `id:"N"` | `order_ids`=[] | **TAK** (ZERO_OUTPUT po 3 cyklach) |
| Klasa `widok_kurier` rename | `courier_packs`/`courier_load`={} | **NIE** (🔥 P1) |
| `name_kurier` rename | packs bez nazwy kuriera | **NIE** (🔥 P1) |
| `id="zlec_"` rename | `assigned_ids`={} → wszyscy „nieprzypisani" | Częściowo (ASYMMETRY łapie odwrotny kierunek, nie ten) |
| `box_zam_name` rename | `rest_names`={} | **NIE** |
| `data-address_from/to` rename | adresy puste → fallback geokod | **NIE** (defense w pipeline, ale cicho) |
| `data-idkurier` rename | wszystkie → `closed_ids` (terminalne) | **TAK** pośrednio (active_ids→0→STUCK/ZERO) |
| `<div>X/Y</div>` load | `courier_load`={} | **NIE** |

## A4. ORACLE — parser_health 30 dni
- `GET :8888/health/parser` (2026-07-01 22:08): `status=healthy, anomaly_detected=false, orders_count=255, delta_last_5=[255×5], parser_version=v2, error_count=0, cycles_recorded=10, init_count=108, downstream_status=ok`.
- Journal `dispatch-panel-watcher` retencja od **26.05** (36+ dni pokrycia).
- **30 dni, WSZYSTKIE unity: 0× `V2_SHADOW_DELTA`/`PARSER_STUCK`/`PARSER_ZERO_OUTPUT`/`PARSER_ASYMMETRY`.** Zero anomalii. Spójne z V2 stabilnym. ⚠ Uwaga: brak anomalii NIE dowodzi parytetu pól — checki są licznikowe (P1), więc partial-break i tak by się nie pokazał w tej liczbie.
- `init_count=108` = watcher restartuje się względnie często; stan health świeży (uptime ~42 min w chwili sondy), rolling window 10 cykli.

---

# CZĘŚĆ B — API AKCJI KONSOLI

## B0. MAPA ENDPOINTÓW AKCJI + AUTH + ŚCIEŻKA ZAPISU

Auth rdzeń (`app/core/deps.py`): `get_current_user`→**401** (brak/zły token, l.54/58); `require_roles`→**403** (l.71); `require_entitlement`→**403** (l.90); `require_pin`→**403** (l.101). Konsola koordynatora: `_OperatorOnly = require_roles("ziomek_admin")` + `_gate()` (404 gdy flaga `COORDINATOR_CONSOLE` OFF, `coordinator.py:56`).

| Endpoint | Auth | Pisze CO | Przez CO (mechanizm) | LIVE-gate | Idemp. | Silnik? |
|---|---|---|---|---|---|---|
| `POST /api/coordinator/assign` | ziomek_admin | kurier w gastro | subprocess `gastro_assign.py` → `przypisz-zamowienie` | `COORDINATOR_ASSIGN_LIVE` | gastro (overwrite) | **BYPASS** (reconcile PANEL_OVERRIDE) |
| `POST /coordinator/cancel` | ziomek_admin | status 9 gastro | subprocess `panel_sync._write_panel_status` (hardened) | `COORDINATOR_CANCEL_LIVE` | gastro | BYPASS |
| `POST /coordinator/edit` | ziomek_admin (+PIN no-op) | adres/tel/COD/status/kurier gastro | subprocess `gastro_edit.py --commit` → `update-zamowienie` | `COORDINATOR_EDIT_LIVE` | merge (safe) | BYPASS |
| `POST /coordinator/route` | ziomek_admin | plan kolejności stopów | `route_mod.save_route` → plan (dispatch_state) | `COORDINATOR_PLAN_LIVE` | overwrite | kanon plan |
| `POST /coordinator/courier-block` | ziomek_admin | `manual_overrides` excluded/working | subprocess `dispatch_v2.manual_overrides` (atomic) | LIVE always (reset 06:00) | dedup w mo | **kanon (Z3)** |
| `POST /coordinator/refresh-time` | ziomek_admin | kolejka force-recheck | subprocess `dispatch_v2.coordinator_time_recheck.enqueue` (atomic+flock+TTL) | LIVE | kolejka | **kanon (Z3)** |
| `POST /coordinator/auto-assign` | ziomek_admin | `ENABLE_AUTO_ASSIGN` w flags.json | subprocess `dispatch_v2.flags_admin` (atomic+flock+event_bus) | — | set idempotent | **kanon (Z3)** |
| `POST /coordinator/create` | ziomek_admin | nowe zlecenie gastro | `dispatch_push.push_delivery` (+ opcjonalnie assign) | `DISPATCH_PUSH_LIVE` | **BRAK** (🧊 P2) | BYPASS |
| `POST /coordinator/courier-message` | ziomek_admin | wiadomość do kuriera | `courier_messages_mod` → courier_api.db | flaga konsoli | id msg | poza silnikiem |
| `POST /coordinator/assign` (paczka) | ziomek_admin | orders_state | subprocess `dispatch_v2.parcel_assign` + panel Delivery sync | `COORDINATOR_ASSIGN_LIVE` | — | tor natywny |
| `POST /coordinator/ziomek/control` | ziomek_admin **+PIN** | start/stop/restart silnika | `health.control` | — | — | steruje silnikiem |
| `POST /api/dispatch/orders` | owner/manager/staff + scope | zlecenie panel DB + push gastro | `svc.create_delivery` + `_push_to_ziomek` | `DISPATCH_PUSH_LIVE` | **409 idempotency_key** (l.240) | B2C |
| `POST /dispatch/orders/{id}/manual-assign` | owner/manager + tenant | przydział w panel DB | `svc.manual_assign` | `OPS10` | — | B2C |
| `PUT /dispatch/bag-config` | owner/manager | torby lokalu panel DB | `db.commit` (walidacja BAG_TYPES) | — | idempotent | poza silnikiem |
| `POST /api/notify-feed/dismiss` | ziomek_admin | sidecar dismissed (plik) | tempfile atomic + `_lock` | — | **idempotent** (set) | sidecar, nie feed Ziomka |
| `POST /api/fleet/overflow/partners/{id}/enable` | ziomek_admin | rejestr partnera panel DB | `db.commit` | `FLT03` | idempotent | „ZERO dispatchu" |
| `POST/PATCH /api/fleet/shift-plan*` | require_entitlement("shift_planning")+role | grafik panel DB | `db.commit` | flaga shift | — | domena grafiku |
| `GET /api/parcel/*`, `GET /api/ziomek/*` | ziomek_admin | — | **READ-ONLY** (0× POST/PUT) | — | — | — |

## B1. FINDINGI — API AKCJI

### 🔥 P1 — zaszyty offset TZ w konwersji HH:MM→minuty (sezonowa mina)
**Plik:** `scripts/gastro_assign.py:11` (`_WARSAW = timezone(timedelta(hours=2))`) użyty w `:153`
**Ścieżka:** konsola ołówek HH:MM → `AssignIn.time_arg` (`coordinator.py:178`) → `assign.py:_build_cmd:42` przekazuje **dosłownie** → `gastro_assign.py --time "13:00"`. Auto-detekcja `:` (l.152) → gałąź HH:MM → `now_waw = datetime.now(_WARSAW)` z zaszytym +2.
**Bug:** `_WARSAW` to STAŁY offset, nie `ZoneInfo("Europe/Warsaw")` (potwierdzone: brak ZoneInfo w pliku). Latem (CEST=+2) OK. Zimą (CET=+1, od ~2026-10-26) `now_waw` zawyżone o 1h → `time_minutes = target − now_waw` **zaniżone o 60 min**; gdy wpisany czas < now → rollover „+1 dzień" (`:158-159`) = +23,5h absurd. Dotyczy TYLKO gałęzi HH:MM (konsola pencil + legacy CLI); telegram używa minut (gałąź `else`, l.166) — niedotknięty.
**Status:** **uśpione teraz** (lipiec = +2 poprawny), **zamanifestuje się po zmianie czasu jesienią**. Semantyczny rozjazd z resztą silnika, który TZ liczy przez `ZoneInfo` (CLAUDE.md hard constraint: „Warsaw TZ zawsze via `ZoneInfo`").

### 🧊 P2 — `/create` konsoli bez idempotency (kontrast z `/dispatch/orders`)
**Plik:** `coordinator.py:731` (`delivery_id = int(datetime.now(timezone.utc).timestamp())`)
**Dowód:** `dispatch_push.push_delivery` kluczuje po tym `delivery_id`. Dwa kliknięcia „Utwórz" w odstępie >1s → dwa różne `delivery_id` → **dwa zlecenia w gastro**. Dla porównania `dispatch.py:240` (`svc.find_by_idempotency` → 409 zwraca istniejące) — wzorzec JEST w kodzie, tylko nie użyty w konsoli. Ryzyko realne przy laggującym LIVE push (operator klika ponownie).

### 🧊 P2 — sukces akcji przez kruchy string-sentinel stdout
**Plik:** `assign.py:89` (`"ASSIGN_OK" in out`), `:142` (`CANCEL_OK`), `:303` (`EDIT_OK`), `:185` (`PARCEL_ASSIGN_OK`); `auto_assign_flag.py:51,74` (`json.loads(out.splitlines()[-1])`); `coordinator_time_recheck.py:56` (`ENQUEUE_OK`); `courier_block.py:130` (`BLOCK_OK`)
**Dowód:** ok/live liczony z obecności magic-stringa lub „JSON w ostatniej linii stdout". Jeśli kanoniczny skrypt dołoży warning PO sentinelu/JSON, lub wypisze sukces na stderr → `ok=False` mimo realnego sukcesu (fałszywy „błąd" w UI, operator klika ponownie → patrz idempotency P2). Kontrakt stdout niejawny, kruchy. Nie błąd dziś, ale dług.

### 🧊 P2 — DWIE ścieżki gastro na przypisanie kuriera
**Plik:** `assign.py:36-46` (`/assign` → `przypisz-zamowienie` z `--time`) vs `assign.py:236` (`/edit` `kurier` → `gastro_edit.py --kurier` → `update-zamowienie`)
**Dowód:** koordynator może przypisać kuriera DWOMA endpointami gastro o różnej semantyce: `przypisz-zamowienie` (ustawia kuriera + czas odbioru, pełny flow rutcom) vs `update-zamowienie` (nadpisuje pole `kurier` bez ścieżki czasu). Potencjalny rozjazd: przydział przez `/edit` nie ustawia `--time`/nie idzie tą samą drogą co `/assign`. Świadome (edit = masowa edycja pól), ale warto by UI kierowało przypisanie tylko przez `/assign`.

### 🧊 P3 — `/edit status` (1..10) surowy do gastro bez walidacji przejść
**Plik:** `coordinator.py:433` + `assign.py:235` (`--status`)
**Dowód:** `EditIn.status` opisany „id_status_zamowienia (1..10)" i idzie wprost do `gastro_edit.py --status`. Brak walidacji dozwolonych przejść (np. ręczne ustawienie 7=doręczone/9=anulowane pomijając flow). Zależność od gastro. Niskie ryzyko (operator zaufany, LIVE-gate + audyt).

### 🧊 P3 — `courier_block` neutral = load→save→load→save (mini-TOCTOU)
**Plik:** `courier_block.py:77-86` (gałąź `neutral`)
**Dowód:** dwa osobne `mo.save()` (najpierw `_do_include` zdejmuje excluded, potem osobno pop `working`). Crash między zapisami → excluded zdjęte, working zostaje. Mini-TOCTOU, ograniczone resetem 06:00 i rzadkością. Pozostałe gałęzie (block/unblock) = pojedynczy atomic `_do_exclude`/`_do_include`.

## B2. KTÓRE AKCJE OMIJAJĄ SILNIK / ROZJAZD SEMANTYKI
- **Omijają scoring/feasibility silnika (write-direct do gastro), Z DESIGNU (human override):** `/assign`, `/cancel`, `/edit`, `/create`. NIE są „ciche" — `panel_watcher.reconcile` wychwytuje je jako `PANEL_OVERRIDE`/`COURIER_ASSIGNED(source=packs)` i domyka pętlę do stanu silnika. Semantyka spójna PO stronie reconcile.
- **NIE omijają — delegują do kanonu (Z3):** `/courier-block` (`manual_overrides`), `/refresh-time` (`coordinator_time_recheck.enqueue`), `/auto-assign` (`flags_admin`). Panel NIGDY nie pisze tych plików bezpośrednio — subprocess w venv Ziomka do sprawdzonych funkcji. **Wzorcowe.**
- **Rozjazd semantyki minuty vs HH:MM:** obsłużony poprawnie (auto-detekcja `:` w `gastro_assign.py:152-166`) — czas z konsoli może być HH:MM albo minuty, oba działają. JEDYNA realna wada w tym torze = zaszyty offset TZ (🔥 P1 wyżej), nie sam dual-format.

---

## POKRYCIE

**Część A (parser):**
- `panel_html_parser.py` — CAŁY plік (V2, 255 linii): regex order_ids `\d{5,7}`, walidacja ID, wszystkie 10 pól, cross-source check, `parse_compare_v1_v2`.
- `panel_client.py` — dispatcher `parse_panel_html` (l.424-461), V1 legacy (l.342-421), flagi (l.93-96), `health_check` (l.786+), region sesji/login (l.80-199).
- `parser_health.py` — monitor, 4 checki, motion-aware, active_ids, persist (l.80-230).
- Env: `systemctl show -p Environment/EnvironmentFiles/ExecStart/DropInPaths` dla `dispatch-shadow`+`dispatch-panel-watcher` (+ 6 innych serwisów zmapowane do modułów).
- Oracle: `GET :8888/health/parser` (żywe) + journal 30 dni (retencja od 26.05) + próbka `orders_state.json` (357 kluczy, prefiksy).
- Grep całościowy żywych callerów `parse_panel_html`/`fetch_panel_html`/`record_tick`.

**Część B (API akcji):**
- Zmapowane WSZYSTKIE endpointy 7 plików: `coordinator.py` (24 trasy, wszystkie akcje POST przeczytane), `dispatch.py` (POST /orders, manual-assign, PUT /bag-config, queue, emergency), `fleet.py` (shift-plan write'y — auth), `parcel_ops.py` (read-only potwierdzone), `notify_feed.py` (dismiss/dismiss-done), `fleet_overflow.py` (enable), `ziomek.py` (read-only).
- Warstwa integracji (realny zapis): `assign.py` (CAŁY — assign/cancel/edit/parcel/time), `courier_block.py` (CAŁY), `coordinator_time_recheck.py` (CAŁY), `auto_assign_flag.py` (CAŁY).
- Kanoniczne narzędzie docelowe: `scripts/gastro_assign.py` (parsowanie `--time`, TZ), `gastro_edit.py` (mapowanie pól, brak czasu).
- Auth core: `deps.py` (require_roles/pin/entitlement/get_current_user, kody 401/403).

## JAWNE LUKI (czego NIE zweryfikowano — do domknięcia)

1. **Front (TSX) nie czytany** — czy konsola wysyła do `/assign` czas jako HH:MM czy minuty w praktyce (kod backendu obsługuje oba; realny format z UI niezweryfikowany bezpośrednio). Memory sugeruje HH:MM (ołówek OrderModal) — stąd ekspozycja na 🔥 P1, ale nie potwierdzone przechwyceniem requestu (READ-ONLY, zakaz POST).
2. **`gastro_edit.py` wnętrze** — potwierdzono brak obsługi czasu i komentarz o merge; NIE prześledzono pełnej logiki „safe merge" (czy naprawdę czyta-nadpisuje tylko zmienione pola, czy jest race z równoległym edit). Deklaracja w docstring `assign.py:296`, nie zweryfikowana kodem skryptu.
3. **`route_mod.save_route` / `dispatch_push.push_delivery` / `svc.create_delivery`** — ścieżki zapisu policzone z sygnatur i docstringów, NIE przeczytane liniowo (poza zakresem lane „parser + API akcji"; to warstwa serwisów).
4. **Reconcile PANEL_OVERRIDE** — twierdzenie „reconcile domyka pętlę po write-direct" oparte o architekturę/memory, NIE zweryfikowane end-to-end w tym audycie (należy do lane silnika/watchera).
5. **parser_health field-parity** — NIE istnieje test/monitor który by złapał partial-break kolumn kurierów (🔥 P1); brak danych historycznych o takim zdarzeniu w 30-dniowym oknie (bo i tak niewidoczne w licznikach — nie da się wykluczyć że wystąpiło i przeszło niezauważone).
6. **`gastro_assign.py` provenance TZ** — nie ustalono czy zaszyty `+2` to regresja czy zawsze taki był; potwierdzono tylko obecny stan (brak ZoneInfo). Weryfikacja historyczna = poza READ-ONLY zakazem (git blame OK, ale nie kluczowe dla werdyktu).

---

### Załącznik — dowody env (surowe)
```
dispatch-panel-watcher  Environment=... USE_V2_PARSER=1 ...
dispatch-shadow         Environment=ENABLE_OBJ_REPLAY_CAPTURE=1 ENABLE_PANEL_BG_REFRESH=1
                        ENABLE_LGBM_SHADOW=1 ENABLE_LGBM_METRICS_READ=1 ENABLE_PENDING_POOL=1
                        (brak USE_V2_PARSER → default "0" wg panel_client.py:93)
EnvironmentFiles: (puste dla obu)
oracle :8888/health/parser → parser_version=v2, status=healthy, orders_count=255
orders_state.json → 357 kluczy, 100% prefiks 48 (V1 46\d{4} = 0 trafień)
journal 30d wszystkie unity → 0× V2_SHADOW_DELTA/PARSER_STUCK/ZERO_OUTPUT/ASYMMETRY
```
