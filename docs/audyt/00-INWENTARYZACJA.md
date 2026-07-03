# 00 — Inwentaryzacja struktury repo dispatch_v2

**Data:** 2026-07-03 · **Agent:** A (Struktura) · **Zakres:** pełne drzewo katalogów, klasyfikacja plików root, git-vs-dysk.
**Metoda:** rekonesans WYŁĄCZNIE odczyt (`find`, `du`, `git ls-files/log/status/branch`, `systemctl show`, ekstrakcja docstringów AST) w żywym repo produkcyjnym `/root/.openclaw/workspace/scripts/dispatch_v2`. Zero zapisu do repo, zero systemctl start/stop, zero git add/commit.

---

## 0. Najważniejsze odkrycie (przed resztą raportu)

**`dispatch_v2/dispatch_state/` w repo TO NIE jest żywy stan silnika.** Zawiera wyłącznie 13 plików epaka (CSV/JSON, 15M — dane cennikowe/prowizyjne epaki, temat odrębny od Ziomka). Prawdziwy stan runtime silnika (orders_state, flags.json historia, shadow logi, ~308+ plików, **1.1 GB**) leży **poza repo git**, na `/root/.openclaw/workspace/dispatch_state/` (potwierdzone: `common.py:3226` hardkoduje tę ścieżkę; `CLAUDE.md` „CRITICAL PATHS" ją potwierdza). Same-name coincidence — dwa katalogi o identycznej nazwie, różne przeznaczenie, żaden nie jest podzbiorem drugiego. Ryzyko pomyłki przy audycie/gradleniu ścieżek wysokie.

Drugie odkrycie: usługi systemd mają **niespójny `WorkingDirectory`** — część (`dispatch-eta-calibration`) uruchamia się z `WorkingDirectory=…/dispatch_v2` + bezpośrednia ścieżka do skryptu, część (`dispatch-shadow`, `dispatch-new-courier-watch`, `dispatch-later-promises-monitor`) z `WorkingDirectory=…/scripts` (katalog NADRZĘDNY) + `python -m dispatch_v2.X`. Repo samo w sobie nie jest jednym spójnym „cwd" dla wszystkich procesów.

---

## 1. Drzewo katalogów — top-level z przeznaczeniem

Repo waży **213M** (bez `.git`), `.git` samo **100M** (1237 commitów, 2026-04-12 → 2026-07-03, **38 branchy lokalnych** + master, głównie `fix/*` i `auton/*` — ślad wielu równoległych sesji naprawczych).

| Katalog | Pliki (dysk) | Rozmiar | Przeznaczenie |
|---|---|---|---|
| `eod_drafts/` | 783 (688 tracked) | 48M | Dzienniki sesji „koniec dnia" — 50 podkatalogów wg daty (2026-05-05→2026-07-03). Mieszanka: raporty .md, skrypty jednorazowe, **dane eksperymentów trackowane w gicie** (patrz §5). |
| `tests/` | 470 (465 .py + fixtures 4 + golden 1) | 19M | Główny pakiet regresji pytest. `tests/fixtures/` (56K), `tests/golden/` (32K, parytet route-order z L6.A). |
| `dispatch_state/` | 13 | 15M | **NIE stan silnika** — patrz §0. Wyłącznie `epaka_data/` (CSV/JSON prowizji + zamówień epaki, `fetch.log`). |
| `tools/` | ~218 (177 tracked) | 5.5M | Największy grab-bag: 159 skryptów `.py` (monitory, werdykt-tools, jednorazowe audyty z fal L0-L4), `tools/fixtures/`, `tools/__pycache__/`. |
| `__pycache__/` | wiele | 2.6M | Bytecode roota, gitignored. |
| `ml_data_prep/` | 25 | 680K | Przygotowanie danych + trening modeli LGBM „two-model" (arbitrage/bundle/forward/solo); zawiera **wytrenowane artefakty `.pkl` trackowane w gicie**. |
| `docs/` | 40 | 504K | ⚠ Już opisane jako PRZESTARZAŁE (kwiecień-maj 2026) — potwierdzone: pliki datowane `2026-04-19`, `V3_5`/`V3_6`/`V3_7`. Nie duplikować analizy. |
| `cod_weekly/` | ~20 (9 tracked) | 404K | Cotygodniowe rozliczenie COD (cash-on-delivery) → Google Sheets. |
| `observability/` | ~20 (13 tracked) | 392K | Alerty, cron_health, data_alerts, delivered_integrity_monitor, ground_truth_gc, koord_cascade_monitor, liveness_probe, log_rotation, watchdog — „oficjalne" monitory Fazy audytowej. |
| `AUDIT_2026-05-07/` | 10 | 344K | 10 raportów .md pełnego audytu architektury z 07.05 (Tier A/B/C, top-20 ryzyk) — **w 100% trackowane**, czysty dokument, nie dane runtime. |
| `daily_accounting/` | ~30 (14 tracked) | 332K | Moduł rozliczeń dziennych/wypłat kurierów + własny `tests/` (4 pliki testowe + runner). |
| `AUDIT_2026-06-03/` | 3 | 312K | Audyt architektury 03.06 (STATUS_ROADMAP + ZIOMEK_AUDYT + extract) — w 100% trackowany. |
| `shift_notifications/` | ~15 (8 tracked) | 264K | Worker powiadomień T-60/T-30 o zmianach kurierów + własny `systemd/`. |
| `systemd/` | 51 tracked | 232K | Kopia repo jednostek systemd (service/timer/drop-in `.d/`) — mirror tego co realnie w `/etc/systemd/system/` (nie zweryfikowano 1:1, patrz „do wyjaśnienia"). |
| `deploy_staging/` | ~20 | 224K | Skrypty + jednostki systemd stage'owane (NIE live) dla `dispatch-bundle-calib-shadow`; `README.md` + `README_INSTALL.md`. |
| `reconciliation/` | ~20 (9 tracked) | 192K | Worker rekoncyliacji stanu (`auto_resync`, `phantom_detector`, `reconcile_worker`) + własny `systemd/` + `README.md`. |
| `sprint2_analysis/` | ~20 (21 tracked) | 172K | Analiza root-cause sprintu 2 (30.04-01.05, **przestarzałe**, self-contained z własnymi logami). |
| `czasowka_proactive/` | ~10 (6 tracked) | 156K | Submoduł proaktywnego harmonogramowania „czasówek" (evaluator/handlers/score_selector/state). |
| `monitoring/` | ~8 (4 tracked) | 112K | 3 detektory (consumer_stuck_alert, detector_419, gps_feed_health) — nakłada się tematycznie z `observability/` (patrz „do wyjaśnienia"). |
| `migrations/` | 6 (3 tracked) | 104K | 2 skrypty migracyjne jednorazowe (2026-05-05, 2026-05-07) + `__init__.py`. |
| `core/` | 5 (wszystkie tracked) | 76K | Najmniejszy, najczystszy katalog: `broadcast_handlers`, `config_reload_subscriber`, `flags_io`, `jsonl_appender` + init. Rola JASNA. |
| `sms/` | ~8 (5 tracked) | 64K | Abstrakcja SMS (`ovh.py` + `stub.py` + `provider.py`) + `SETUP.md`. |
| `telegram/` | ~4 (2 tracked) | 32K | Tylko `templates.py` + init (właściwy bot: `telegram_approver.py` w rootcie — nazewnictwo mylące, patrz „do wyjaśnienia"). |
| `deploy/` | 4 (wszystkie tracked) | 20K | 2 pary service/timer (`checkpoint-tz-shadow`, `reassignment-shadow`) — stage'owane. |
| `config/` | 1 | 16K | Wyłącznie `cities.json`. |
| `.claude/` | 1 | — | `settings.local.json` — lokalne ustawienia Claude Code (untracked, jak zwykle). |
| `.pytest_cache/`, `.git/` | — | 372K / 100M | Standardowe, gitignored poza `.git`. |

**Katalogi z WŁASNYM `systemd/`** (rozproszenie jednostek zamiast jednego miejsca): `reconciliation/systemd/`, `shift_notifications/systemd/` — obok głównego `systemd/` w rootcie i `deploy/`, `deploy_staging/etc/systemd/`. **4 różne miejsca trzymające jednostki systemd w repo.**

---

## 2. Korzeń repo — pliki luzem

Policzone na `find . -maxdepth 1`:

| Typ | Liczba | Uwagi |
|---|---|---|
| `.py` | 103 | **100% trackowane w git** (zero orphan .py w całym repo — dobra wiadomość, patrz §5). |
| `.md` | 12 | `CLAUDE.md` (89K, ⚠ zamrożony snapshot 2026-05-10 wg własnego nagłówka), `ZIOMEK_MASTER_KB.md` (80K), `ZIOMEK_LOGIC_REFERENCE.md` (77K, +4 warianty `.bak-pre-*`), `TECH_DEBT.md` (105K, root — osobny od `docs/TECH_DEBT.md`), `LESSONS.md` (16K), `ZIOMEK_ARCHITECTURE.md`/`ZIOMEK_INVARIANTS.md`/`ZIOMEK_DEFINITION_OF_DONE.md` (kanon Fazy 1 audytu, zatwierdzony 01.07), `ZIOMEK_STRATEGIC_AUDIT_2026-06-23.md`, `PRE_MERGE_CHECKLIST_2026-05-10.md`, `SESSION_HANDOFF_2026-04-30_evening.md` (oba przestarzałe, po dacie w nazwie). |
| `.bak-pre-*` / `.bak*` | 188 (root) / **339 całe repo** | 19M łącznie. Wzorzec nazwy `<plik>.bak-pre-<opis>-<data>` — snapshoty przed każdą zmianą per protokół `ziomek-change-protocol`. Prawidłowo gitignored (`*.bak-*`), z **2 wyjątkami** które je ominęły (§5). |
| `.json` | 1 | `restaurant_company_mapping.json` (obecnie modified w working tree). |
| inne luzem | 5 | `.claudeignore`, `.gitignore`, `events.db` (SQLite event_bus, 0 B, TRACKOWANY), `requirements-dispatch-venv.txt`, `geocoding.py.bounded-retry-wip-2026-06-14` (orphan WIP, TRACKOWANY — patrz §5). |

### Pliki `.py` w korzeniu — pogrupowane tematycznie (30 najważniejszych, rola z docstringu)

**Rdzeń silnika dispatchu (pipeline główny, ~20 plików):**
`dispatch_pipeline.py` (387K — per-order assessment feasibility→scoring→rank→verdict, największy plik repo), `common.py` (231K — config/logger/paths/flagi, drugi największy), `telegram_approver.py` (187K — bot Telegram shadow proposals), `panel_watcher.py` (127K — event-driven polling panelu), `plan_recheck.py` (123K — periodic consistency checker V3.19c), `shadow_dispatcher.py` (98K — systemd loop NEW_ORDER), `courier_resolver.py` (87K — fleet snapshot GPS+fallback), `route_simulator_v2.py` (84K — Hybrid PDP-TSP), `feasibility_v2.py` (74K — SLA-first check), `state_machine.py` (54K — jedyne źródło prawdy o stanie zlecenia), `plan_manager.py` (28K), `panel_client.py` (34K — dostęp do gastro.nadajesz.pl), `osrm_client.py` (37K), `geocoding.py` (32K), `sla_tracker.py` (32K), `czasowka_scheduler.py` (33K), `event_bus.py` (27K), `tsp_solver.py` (25K OR-Tools), `scoring.py`, `wave_scoring.py`, `objm_lexr6.py` (selektor lex-helperów).

**Auto-assign / autonomia (AUTON-01, sprint bieżący):** `auto_assign_executor.py`, `auto_assign_gate.py`, `auto_koord.py`, `auto_proximity_classifier.py`, `coordinator_activations.py`, `coordinator_time_recheck.py`.

**Narzędzia-monitory (health/quality/observability jako root scripts, nie w `observability/`):** `parser_health.py` + `parser_health_layer3.py` + `parser_health_endpoint.py` (3-warstwowa rezyliencja parsera), `parse_continuity_guard.py`, `courier_gps_commitment_shadow.py` + `_report.py`, `pickup_lateness_shadow.py`, `eta_calibration_logger.py`, `eta_residual_infer.py`, `learning_analyzer.py`, `validation_gate_lgbm.py`, `ml_inference.py`, `r04_apply.py` + `r04_evaluator.py`, `replay_failed.py`, `geocode_verify.py`, `geocoding_audit.py`.

**Skrypty jednorazowe/bootstrap/admin:** `bootstrap_restaurants.py`, `build_v319h_courier_tiers.py`, `extract_restaurant_addresses.py`, `courier_admin.py`, `flags_admin.py`, `gastro_edit.py`, `manual_overrides.py`, `new_courier_pairing.py`, `prune_orders_state.py`, `event_bus_cleanup.py`, `sync_courier_pay.py`.

**Czasówki/uwagi (deadline z free-textu):** `czasowka_uwagi.py`, `uwagi_address_parser.py`, `address_mismatch.py`, `address_pin_memory.py`.

**Paczki (parcel lane, sprint 29.06):** `parcel_assign.py`, `parcel_lane_merge.py`.

**Bez docstringu:** `uwagi_address_parser.py` — jedyny plik root bez modułowego docstringu.

---

## 3. Klasyfikacja całości repo

- **Kod silnika (produkcyjny, importowany przez usługi systemd):** ~103 pliki root + `core/`, `cod_weekly/`, `czasowka_proactive/`, `daily_accounting/`, `observability/`, `monitoring/`, `reconciliation/`, `shift_notifications/`, `sms/`, `telegram/`, `ml_data_prep/` (część inferencji) — trzon rzeczywiście uruchamiany przez `systemd/*.service`.
- **Narzędzia + monitory offline:** `tools/` (159 skryptów — werdykty, replaye, audyty jednorazowe fal L0-L8), spora część root-level health/shadow skryptów.
- **Testy:** `tests/` (465 plików) + `daily_accounting/tests/` (osobny mini-pakiet, custom runner nie pytest).
- **Dane runtime:** `dispatch_state/` (tylko epaka — patrz §0), `events.db`; **prawdziwy stan runtime poza repo**.
- **Dokumentacja:** 12 plików `.md` w rootcie + `docs/` (40, przestarzałe) + `AUDIT_2026-05-07/` + `AUDIT_2026-06-03/` (oba w pełni aktualne jako dokument historyczny, nie „bieżący stan").
- **Archiwum/backupy:** 339 plików `.bak-pre-*` (19M, gitignored) + `eod_drafts/` (48M, częściowo dokumentacja/częściowo dane eksperymentów — patrz §5) + `sprint2_analysis/` (przestarzały, samodzielny).
- **Kandydaci na śmieci:** `geocoding.py.bounded-retry-wip-2026-06-14` (orphan, tracked, brak referencji poza plikiem samym), `eod_drafts/2026-06-17/foodage_phase4_result.txt.proven-bak` (orphan tracked), `deploy/` + `deploy_staging/` (jednostki „staged" — sprawdzić czy nadal czekają na wdrożenie czy są martwe po flipie), `SESSION_HANDOFF_2026-04-30_evening.md` + `PRE_MERGE_CHECKLIST_2026-05-10.md` (jednorazowe artefakty z nazwą-datą w tytule, nigdy nie posprzątane).

---

## 4. Git vs dysk

**Zero luk w kodzie:** żaden plik `.py` ani `.md` „ważny" nie jest untracked-a-wygląda-na-kod. Cały untracked zbiór (1444 plików) to **1087 `__pycache__`/`.pyc`, 339 `.bak*`, i tylko 18 „innych"** — z tych 18: 13 to nowe dane epaki/raporty jeszcze niescommitowane z bieżącej sesji (`dispatch_state/epaka_data/*.{json,csv}`, `eod_drafts/2026-07-02/auton-blockers_raport.md`, `eod_drafts/2026-07-03/perf_budget_report_0905utc.{json,txt}`), 5 to wnętrze `.pytest_cache/` (samo poprawnie gitignored, ale ma własny zagnieżdżony `.gitignore`/`CACHEDIR.TAG` które i tak nie są trackowane — nieszkodliwe).

**Realna luka — TRACKOWANE dane runtime (potwierdza sygnał z briefu):**
- `eod_drafts/**` zawiera **~45 plików `.jsonl`/`.log`/`.out`/`.err`/`.diff`/`.csv`** trackowanych w git jako wynik eksperymentów (np. `2026-05-14/tomtom_poc/{rw_results.jsonl (5.5M), trips_realworld.jsonl (1.5M), measure_rw.log (1.2M), build_gt.log}`, `2026-05-08/here_poc/*.jsonl`, `2026-06-22/{sweep_*.out, harmed_*.jsonl, wins_*.jsonl}`, `2026-06-11/stash_archive/*.diff`, `2026-07-02/AUDYT2/findings_{new,old}.jsonl`).
- **Cztery z nich są AKTYWNIE MODYFIKOWANE właśnie teraz** (`git status` → modified, not staged): `eod_drafts/2026-05-14/tomtom_poc/{build_gt.log, measure_rw.log, rw_results.jsonl, trips_realworld.jsonl}` — proces/sesja z 14.05 wciąż dopisuje do plików sprzed 7 tygodni, mimo że katalog nazwą sugeruje jednorazowy PoC zamknięty w maju. To dokładnie wzorzec opisany w brifie („logi/jsonl w eod_drafts/2026-05-14/tomtom_poc trackowane i modyfikowane na żywo") — **potwierdzony, wciąż aktywny 03.07**.
- `ml_data_prep/models_twomodel/{bundle,solo}/label_encoders.pkl` — binarne artefakty wytrenowanych modeli w git (rosnący repo bloat przy retrainingu, brak `.gitattributes`/LFS).
- `dispatch_state/epaka_data/{2026-05-30_2026-06-29.csv, 2026-06-01_2026-06-29.csv, fetch.log}` — trackowane, ale nowsze warianty tych samych danych (05-31…, 06-01_07-01…, 06-02…) już NIE są trackowane — sygnał że commitowanie tych danych było doraźne/przypadkowe, nie świadomą polityką.

**Pliki, które POWINNY były zostać zignorowane, ale nazwa ominęła wzorce `.gitignore` (`*.bak-*`, `*.bak`, `*.bak.*`):**
- `geocoding.py.bounded-retry-wip-2026-06-14` (root, tracked) — konwencja „`.bounded-retry-wip-DATA`" zamiast `.bak-pre-`.
- `eod_drafts/2026-06-17/foodage_phase4_result.txt.proven-bak` (tracked) — sufiks `.proven-bak` nie pasuje do `*.bak`/`*.bak-*`/`*.bak.*` (brak kropki przed „bak").
- Dowód, że część sesji nie trzymała się konwencji nazewnictwa backupów z `CLAUDE.md`/protokołu — mechanizm gitignore jest string-matching na konwencję, nie na semantykę „to jest kopia zapasowa".

**Ocena `.gitignore`:** kompletny dla swojego zakresu (backupy, sekrety, `__pycache__`, edytor, OS) ale **nie adresuje**: (a) danych eksperymentalnych w `eod_drafts/**` (`.jsonl`/`.log`/`.csv`/`.out`/`.err`/`.diff` — brak wzorca), (b) `*.db` (SQLite), (c) `*.pkl` (artefakty ML), (d) niestandardowych nazw backupów/WIP spoza `bak-pre-` (żaden wzorzec go nie złapie, bo to problem konwencji nazw, nie gitignore). `.claude/settings.local.json` też nie ma dedykowanego wpisu (drobne, dziś nieszkodliwe bo nikt go nie dodał).

---

## 5. Katalogi/pliki o niejasnej roli — hipotezy

| Element | Hipoteza | Do potwierdzenia |
|---|---|---|
| `dispatch_v2/dispatch_state/epaka_data/` | Katalog nazwany jak stan silnika, ale to staging danych epaki (temat cennik/prowizje, patrz `memory/epaka-cennik-oferta-automation.md`) — prawdopodobnie ktoś potrzebował lokalnego miejsca na fetch i użył istniejącej nazwy katalogu bez sprawdzenia kolizji. | Czy to świadomy wybór ścieżki, czy przypadkowe utworzenie katalogu o tej samej nazwie co realny `/root/.openclaw/workspace/dispatch_state/`? |
| `monitoring/` vs `observability/` | Dwa katalogi o zachodzącej tematyce (monitor_419/gps_feed_health vs alert_onfailure/data_alerts/watchdog) — prawdopodobnie `monitoring/` starszy (pre-audyt), `observability/` nowszy (po Fazie audytu maj/czerwiec). | Czy `monitoring/` jest wygaszany na rzecz `observability/`, czy oba aktywne z osobnym zakresem? |
| `telegram/` (2 pliki) vs `telegram_approver.py`/`telegram_utils.py`/`notify_router.py` (root) | Nazewnictwo sugeruje że `telegram/` powinien być głównym miejscem logiki Telegram, ale faktyczny bot (187K!) siedzi w rootcie. `telegram/` to tylko `templates.py`. | Czy `telegram/` to zaczątek nieukończonej migracji z roota, porzucony? |
| `deploy/` + `deploy_staging/` | Nazwy sugerują „gotowe do wdrożenia, jeszcze nie wdrożone" (`checkpoint-tz-shadow`, `reassignment-shadow`, `bundle-calib-shadow`). Część tematów (np. bundle-calib) wg pamięci ma już status LIVE/flip w toku. | Czy zawartość tych katalogów jest już wdrożona (i to martwy relikt) czy wciąż czeka? Zestawić z realnymi jednostkami w `/etc/systemd/system/`. |
| 4 miejsca z `systemd/` (root `systemd/`, `deploy/`, `deploy_staging/etc/systemd/`, `reconciliation/systemd/`, `shift_notifications/systemd/`) | Brak jednego kanonicznego miejsca na jednostki systemd w repo — każdy submoduł trzyma swoje. | Czy root `systemd/` = "wdrożone", pozostałe = "per-moduł kopie robocze"? Zweryfikować przez `diff` z `/etc/systemd/system/*.service` (nie zrobione w tej fazie — read-only, ale bezpieczne: `systemctl cat`). |
| `sprint2_analysis/`, `AUDIT_2026-05-07/`, `AUDIT_2026-06-03/` | Trzy w pełni zamknięte, samodzielne katalogi audytowe/analityczne z różnych momentów — brak wspólnego indeksu/linkowania między nimi ani do `eod_drafts/2026-06-30/FAZA1_*` (najnowszy audyt). | Czy wart jeden zbiorczy indeks „historia audytów" (0 kosztu, czysto porządkowy)? |
| `geocoding.py.bounded-retry-wip-2026-06-14` | Porzucona gałąź eksperymentu (WIP = work in progress) z 14.06, nigdy nie scalona ani nie posprzątana — 18 dni „wisi" w repo. | Czy nadal potrzebny jako referencja, czy do usunięcia? |
| `.claude/settings.local.json` | Standardowe lokalne ustawienia Claude Code, untracked (zgodnie z konwencją, choć `.gitignore` tego jawnie nie deklaruje). | Brak akcji — kosmetyka `.gitignore`. |

---

## ⚠ DO WYJAŚNIENIA (dla Adriana)

1. **Krytyczne dla dalszego audytu:** czy pozostali agenci (B-F) mają świadomość, że `dispatch_v2/dispatch_state/` w repo **nie jest** żywym stanem silnika? Jeśli ktoś analizuje "stan systemu" patrząc na repo, będzie patrzeć w złe miejsce (prawdziwe dane są na `/root/.openclaw/workspace/dispatch_state/`, 1.1 GB, poza gitem).
2. Czy `eod_drafts/2026-05-14/tomtom_poc/` powinien nadal być aktywnie zapisywany (4 pliki modified live dzisiaj), czy to zombie-proces/cron który należało zamknąć w maju? Wpływa na rozmiar repo i szum w `git diff`.
3. Czy trackowanie plików `.jsonl`/`.log`/`.csv` w `eod_drafts/**` to świadoma polityka „dowody eksperymentów zostają w historii" (wtedy `.gitignore` nie trzeba zmieniać, tylko zaakceptować rozmiar), czy przypadek (wtedy warto rozważyć `.gitignore` dla wzorców typu `eod_drafts/**/*.jsonl` poza wybranymi „final" plikami)?
4. 4 rozproszone lokalizacje jednostek systemd w repo — czy warto (osobny, tani porządkowy temat) skonsolidować do jednego `systemd/` z podkatalogami per-moduł, zamiast równoległych kopii?
5. `deploy/` i `deploy_staging/` — czy zawartość jest już wdrożona (martwy relikt do archiwizacji) czy realnie oczekuje na flip?
6. Dwa orphan pliki (`geocoding.py.bounded-retry-wip-2026-06-14`, `eod_drafts/2026-06-17/foodage_phase4_result.txt.proven-bak`) — usunąć czy zachować? (Kosmetyka, zero ryzyka, ale trzeba ACK zanim ktokolwiek to ruszy zgodnie z Przykazaniem #0.)
