# A3 — Inwentaryzacja TRZECH ŚWIATÓW FLAG (Z-P1-07 Faza A)

**Zakres:** READ-ONLY mapper. Zero edycji, zero flipów, zero restartów. Wartości tylko flago-podobne (bool/enum); żadnych sekretów.
**Data:** 2026-07-10. **Metodyka stanu flag:** ADR-004 (`dispatch_v2/docs/decisions/ADR-004-flagi-trzy-swiaty.md`) — 3 rozdzielne nośniki: SILNIK=`flags.json` hot-reload, PANEL=`flags.systemd.env`+drop-iny+`DEFAULT_FLAGS`, APKA=drop-iny+`courier_api/config.py`.

**Pliki robocze (pełne listy nazw) w tym samym katalogu scratchpad:**
`A3_flags_engine_flagsjson.txt` (278) · `A3_flags_engine_ETAP4_DECISION_FLAGS.txt` (126) · `A3_flags_engine_FLAGS_JSON_NUMERIC_OVERRIDES.txt` (26) · `A3_flags_engine_TEST_ISOLATED_INFRA_FLAGS.txt` (4) · `A3_flags_engine__FINGERPRINT_EXTRA_FLAGS.txt` (33) · `A3_flags_engine_fingerprint_universe.txt` (159) · `A3_flags_engine_envfrozen_module.txt` (41) · `A3_flags_ENGINE_dropins.txt` · `A3_flags_panel_default.txt` (81) · `A3_flags_panel_env.txt` (45) · `A3_flags_PANEL_dropins.txt` (3) · `A3_flags_courier_config.txt` (21) · `A3_flags_COURIER_dropins.txt`.

---

## A. LICZNOŚCI per świat/nośnik (+ gdzie leży każda lista)

### SILNIK (kanon = `flags.json` hot-reload)
| Zbiór | Liczność | Lokalizacja (plik:linie) |
|---|---|---|
| `flags.json` — klucze raw | **278** | `/root/.openclaw/workspace/scripts/flags.json` |
| `flags.json` — REAL flagi | **242** | jw. (36 kluczy `_comment*` = inline-doc, NIE flagi → checker MUSI je odfiltrować) |
| `ETAP4_DECISION_FLAGS` | **126** | `common.py:144-500` |
| `FLAGS_JSON_NUMERIC_OVERRIDES` | **26** | `common.py:654-695` |
| `TEST_ISOLATED_INFRA_FLAGS` | **4** | `common.py:700-710` |
| `_FINGERPRINT_EXTRA_FLAGS` | **33** | `common.py:714-755` |
| Uniwersum `flag_fingerprint()` = ETAP4 ∪ FP_EXTRA | **159** | `common.py:798` |
| env-frozen module-level (grep task-card) | **41** | `dispatch_v2/*.py` + `core/*.py` — ⚠ patrz niżej: UNDERCOUNT |
| env-frozen *faktycznie w unicie* (flag_registry) | **12** | scanner `flag_registry.scan_common` |

- Typy realnych flag: przewaga `bool` (209 z 278 raw), reszta `int`/`float`/`str-enum`/`list`.
- **⚠ UNDERCOUNT env-frozen:** grep z karty `^[A-Z0-9_]+ *= *os\.environ\.get` łapie tylko `NAME = os.environ.get`, a MINĄŁ alias `_os.` (np. `panel_client.py:84 ENABLE_PANEL_BG_REFRESH`) i `getenv`. Autorytatywny regex to `(?:_os|os)\.environ\.get` (z `flag_registry`). Rejestr MUSI go użyć, inaczej świat 1b niekompletny.

**Mechanizm (kontrakt):**
- `load_flags()` (`common.py:70`) — hot-reload `flags.json` + K05 snapshot-override na czas TICKU (`ENABLE_FLAG_SNAPSHOT`) + perf-lazy TTL (pomija `stat()` w oknie).
- `flag(name, default=False)` (`common.py:102`) — prosty `load_flags().get(name, default)`.
- `decision_flag(name)` (`common.py:776`) — **flags.json → stała modułu (`globals()` W CZASIE WYWOŁANIA) → False**; wyjątek `ENABLE_OBJ_DELIVERY_FOOD_AGE` = thread-local override (`food_age_override`).
- `flag_fingerprint()` (`common.py:792`) — `decision_flag` po 159 nazwach (ETAP4 ∪ FP_EXTRA); logowany przy starcie każdego procesu → parytet fingerprintów shadow/czasowka/plan-recheck = wymóg.

### PANEL (repo ŻYWE: `/root/.openclaw/workspace/nadajesz_clone/panel/backend`, `nadajesz-panel.service` :8000)
| Nośnik | Liczność | Lokalizacja |
|---|---|---|
| `DEFAULT_FLAGS` | **81** | `app/core/flags.py:12-118` |
| `flags.systemd.env` (`PANEL_FLAG_*`, po filtrze) | **45** | `.../backend/flags.systemd.env` (EnvironmentFile) — ⚠ ADR-004 mówi „44" → **+1** |
| inline drop-iny `nadajesz-panel.service.d/` | **3** | DELIVERY_DASH_WHEN_NO_PLAN=1, TRUST_CANON_ORDER=1, TRUST_CANON_WHEN_COVERS_BAG=1 |

- `flag(name)` = env `PANEL_FLAG_<name>` nadpisuje → `DEFAULT_FLAGS.get(name, False)`. Namespace env = `PANEL_FLAG_` + nazwa z DEFAULT_FLAGS.
- 3 flagi z drop-inów NIE są w `flags.systemd.env` (drop-in = jedyny nośnik; DEFAULT_FLAGS=False, drop-in→1, drop-in wygrywa nad EnvironmentFile).

### APKA (kurier: `/root/.openclaw/workspace/scripts/courier_api/config.py`, `courier-api.service`)
| Nośnik | Liczność | Lokalizacja |
|---|---|---|
| module-level env-read (`os.environ.get`) | **21** (19 flag-ish) | `courier_api/config.py` — wszystkie zamrożone przy imporcie |
| drop-iny `courier-api.service.d/` | **10** flag-podobnych w 8 `.conf` | `/etc/systemd/system/courier-api.service.d/` |

- 2 flagi drop-in (ENABLE_COURIER_LOGIN_RATE_LIMIT_PER_IP / _GLOBAL) NIE czytane w `config.py` → konsument w innym module `courier_api` (ratelimit) → rejestr musi skanować CAŁY `courier_api`, nie sam `config.py`.
- ⚠ istnieje też `courier_api_panelsync/config.py` (osobny serwis) — nie skanowany głęboko; rejestr powinien go objąć.

### ŚWIAT 1b (flagi SILNIKA zamrożone w systemd, POZA `flags.json`)
`A3_flags_ENGINE_dropins.txt`:
- `dispatch-shadow.service.d/override.conf`: ENABLE_PANEL_BG_REFRESH=1, ENABLE_LGBM_SHADOW=1, ENABLE_LGBM_METRICS_READ=1, ENABLE_PENDING_POOL=1
- `dispatch-panel-watcher.service.d/override.conf`: ENABLE_PANEL_BG_REFRESH=**0**, USE_V2_PARSER=1
- `dispatch-plan-recheck.service.d/`: ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1 (`committed-propagation.conf`), ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH=1 (`live-eta-refresh.conf`)
- `dispatch-czasowka.service.d/override.conf`: CZASOWKA_TELEGRAM_DRYRUN=1, CZASOWKA_RETROACTIVE_HOURS=2, CZASOWKA_MAX_EMIT_PER_TICK=3
- (drop-iny `onfailure`/`oom-protect`/`resource_limits`/`cron_health_success` = infra, bez flag)

---

## B. ISTNIEJĄCE CHECKERY `tools/flag_*` (żeby rejestr NIE dublował)

| Checker (l.) | Co robi | Format wyjścia | Wpięcie w testy |
|---|---|---|---|
| `flag_registry.py` (465) | Inwentarz 3 źródeł **SILNIKA** (common.py env-def + 5 engine-unitów env-frozen + flags.json) + klasyfikacja rozjazdów (genuine / intentional-per-process / service-scoped). `ENGINE_UNITS`=shadow, panel-watcher, czasowka, plan-recheck, telegram | stdout tabela ROZJAZDY/AKCEPTOWANE/BRAKI + statystyki; `--all` pełny inwentarz; `--md PLIK` **pisze** md | `test_flag_registry_f3.py` |
| `flag_hygiene_check.py` (59) | Dead-flag: klucz `flags.json` bez literalnego czytelnika w `/scripts` + ostrzeżenie o dynamicznych czytelnikach. **Pure-read** | stdout „N kluczy / SIEROTY"; exit 1 gdy sieroty | **BRAK** (manual/CI, nie w pytest) |
| `flag_doc_coverage_check.py` (80) | Coverage DOKUMENTACJI flag decyzyjnych vs ref-doc; RATCHET vs `flag_doc_baseline.json` | dict documented/undocumented/new_drift/coverage_pct; exit≠0 = nowa niedok. poza baseline | `test_flag_doc_coverage.py` |
| `flag_effect_coverage_check.py` (102) | Coverage TESTÓW EFEKTU flag ETAP4; RATCHET vs `flag_effect_baseline.json`; `ZIOMEK_SCRIPTS_ROOT` dla worktree | dict; exit≠0 = nowa flaga bez testu efektu poza baseline | `test_flag_effect_coverage.py` |
| `flag_fingerprint.py` (26) | Drukuje `C.flag_fingerprint()` (1 linia decyzyjnych) | stdout `NAME=0/1 …` | pośrednio |
| `flag_fingerprint_check.py` (397) | Rekoncyliacja EFEKTYWNEGO stanu **per-serwis** (czyta linię FLAG_FINGERPRINT z `journalctl -u <unit> -o cat` — **NIE** banned `systemctl show`). L0.1 | raport rozjazdów per-serwis; exit code | `test_flag_fingerprint_check.py` |
| `flag_fingerprint_guard.py` (445) | Strażnik-TIMER rekoncyliacji (staged, READ-ONLY); **pisze** `dispatch_state/flag_fingerprint_guard.jsonl` | jsonl + log | `test_flag_fingerprint_guard.py` |

Baseline'y: `flag_doc_baseline.json`, `flag_effect_baseline.json` (w `tools/`).
Pozostałe testy flagowe: `test_conftest_flag_strip_guard`, `test_d3_flag_migration`, `test_etap4_flag_unification`, `test_f3_hardrule_flags_effect`, `test_flags_admin_effective`, `test_flags_io`, `test_flag_snapshot_k05`, `test_scale01_caps_flags`.

**Empiryczny wynik (uruchomione READ-ONLY):**
- `flag_hygiene_check.py` → `242 klucze | odwoływane 242 | SIEROTY: 0` (48 dyn-reader sites, głównie napcompane worktrees `wt-*`).
- `flag_registry.py` → `467 flag (decyzyjne 185, w flags.json 242, env-frozen gdziekolwiek 12)`; ROZJAZDY=1 (USE_V2_PARSER ⛔), AKCEPTOWANE=11, BRAKI POKRYCIA=0.

### Czego ŻADEN checker NIE robi (LUKA = miejsce dla rejestru lifecycle)
1. **Tylko SILNIK.** Zero PANEL (DEFAULT_FLAGS/PANEL_FLAG_*) i APKA (courier_api). Cross-WORLD drift (silnik↔panel↔apka) NIEPOKRYTY.
2. **Zero metadanych LIFECYCLE:** owner, review_date, removal_condition, rollback, stage (planned/shadow/live/deprecated). `doc_coverage` sprawdza że DOC istnieje — nie strukturalny lifecycle.
3. **Zero linkowania TWINS** cross-world (4 pary panel↔apka) — zmiana jednej nie wymusza review bliźniaka (łamie regułę „bliźniaki RAZEM").
4. **Zero committed „expected-state"** z ownerem/rollbackiem dla świata 1b — `fingerprint_check` porównuje runtime z hosta, brak zamrożonego kontraktu z metadanymi.
5. `flag_hygiene` bez baseline, skanuje worktrees (szum), poza pytest.

---

## C. PRZEKROJE / DRYFY między nośnikami

### GENUINE ENGINE DRIFT (flag_registry ⛔, potwierdzone runtime) — 1
- **USE_V2_PARSER** — env=1 tylko w `dispatch-panel-watcher`; pozostałe engine-serwisy default→v1. `panel_client.py:93/425` wybiera parser v1/v2 modułowo, a `panel_client` importują shadow/czasowka/state_machine → inne serwisy parsują v1 gdy same wołają parser. Domknięcie = migracja do `flags.json` (ETAP4 hot-reload) + ACK (parser=behavior-affecting).

### INTENCJONALNE per-process (flag_registry ✅, 11 — rejestr MUSI zachować adnotacje)
- **ENABLE_PANEL_BG_REFRESH** (shadow=1 / watcher=0) — udokumentowany, celowy split (`INTENTIONAL_PER_PROCESS`), **NIE bug**; konsument `panel_client.py:84` (alias `_os`, niewidoczny dla grepa z karty).
- ENABLE_LGBM_SHADOW, ENABLE_LGBM_METRICS_READ, ENABLE_OBJ_REPLAY_CAPTURE, ENABLE_PENDING_POOL (shadow-only)
- CZASOWKA_TELEGRAM_DRYRUN, CZASOWKA_RETROACTIVE_HOURS, CZASOWKA_MAX_EMIT_PER_TICK (czasowka-only)
- ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION, ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH (plan-recheck-only; L0.1 rekomenduje migrację do flags.json)
- PYTHONPATH (infra)

### CROSS-WORLD TWINS panel↔apka (4 — DZIŚ wszystkie KOHERENTNE = oba ON, ale NIC tego nie pilnuje)
| Twin (koncept) | PANEL | APKA |
|---|---|---|
| DELIVERY_DASH_WHEN_NO_PLAN | drop-in=1 | drop-in=1 |
| LIVE_ETA_COURIER_GUARD | env=1 | drop-in=1 |
| PLAN_AWARE_PODJAZDY | env=1 | drop-in=1 |
| **TRUST_CANON_ORDER ↔ BUILD_VIEW_TRUST_CANON_ORDER** (różne NAZWY, ten sam koncept) | drop-in=1 | drop-in=1 |
| (bonus) LIVE_ETA_FRESH_OVERRIDE_ONLY | default=True | default=1 |

→ Rejestr MUSI je zlinkować (`twin_of`), także parę o RÓŻNEJ nazwie.

### DUAL-CARRIER / DO WERYFIKACJI
- **PANEL-internal:** 3 flagi mają nośnik TYLKO w drop-in (nie w env-file): DELIVERY_DASH_WHEN_NO_PLAN, TRUST_CANON_ORDER, TRUST_CANON_WHEN_COVERS_BAG — DEFAULT_FLAGS=False, drop-in→1 (drop-in wygrywa).
- **flags.json ∩ engine env-frozen module (4):** ENABLE_GEOCODE_NOMINATIM_FALLBACK, ENABLE_GEOCODE_PIN_MEMORY_FALLBACK, ENABLE_GEOCODE_VERIFICATION_ENFORCE, ENABLE_PERF_LAZY_MEMBERS.
  - `ENABLE_PERF_LAZY_MEMBERS` — poprawnie re-derived z json w `load_flags()` (`common.py:96`), json wygrywa. OK.
  - 3× geocode — **DO WERYFIKACJI** czy konsument czyta json (`flag()`/`decision_flag()`) czy zamrożoną stałą modułu → potencjalna mina antywzorca #9 („json wygląda live, moduł czyta env").
- `flag_registry` ∩ drop-in ∩ env-frozen module (wiring 1b, poprawny): CZASOWKA_TELEGRAM_DRYRUN, ENABLE_PENDING_POOL, ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION, ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH.

### Lista env-frozen (świat 1b, do rejestru per-service value map)
Silnik (pinowane w unicie): ENABLE_PANEL_BG_REFRESH{shadow=1,watcher=0}, ENABLE_LGBM_SHADOW{shadow=1}, ENABLE_LGBM_METRICS_READ{shadow=1}, ENABLE_PENDING_POOL{shadow=1}, ENABLE_OBJ_REPLAY_CAPTURE{shadow}, USE_V2_PARSER{watcher=1}, ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION{plan-recheck=1}, ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH{plan-recheck=1}, CZASOWKA_TELEGRAM_DRYRUN/RETROACTIVE_HOURS/MAX_EMIT_PER_TICK{czasowka}.
Apka (pinowane w courier-api.service.d): ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=1, ENABLE_DELIVERED_TOO_FAST_GUARD=1, ENABLE_DELIVERY_DASH_WHEN_NO_PLAN=1, ENABLE_PICKUP_TIME_READY_FALLBACK=1, ENABLE_PLAN_AWARE_PODJAZDY=1, ENABLE_LIVE_ETA_COURIER_GUARD=1, ENABLE_APP_ROUTE_FROM_CONSOLE=1, ENABLE_ROUTE_ORDER_UNIFIED=1, ENABLE_COURIER_LOGIN_RATE_LIMIT_PER_IP=1, ENABLE_COURIER_LOGIN_RATE_LIMIT_GLOBAL=0.
Panel (pinowane w nadajesz-panel.service.d): PANEL_FLAG_DELIVERY_DASH_WHEN_NO_PLAN=1, PANEL_FLAG_TRUST_CANON_ORDER=1, PANEL_FLAG_TRUST_CANON_WHEN_COVERS_BAG=1.

---

## D. REKOMENDACJA FORMATU REJESTRU + TRYBY CHECKERA

**Przestrzenie WOLNE (potwierdzone):** `docs/flags/` (ABSENT), `tools/flag_lifecycle*` (ABSENT). Bezpieczne dla nowego rejestru.

### Format: JSON w repo — `tools/flag_lifecycle/registry.json` (+ generator; ludzka projekcja `docs/flags/*.md`)
Pola per flaga:
- `name`
- `worlds[]`: `engine|panel|apka` (lista — twins wieloświatowe)
- `source_of_truth`: `flags.json | flags.systemd.env | DEFAULT_FLAGS | drop-in:<plik> | common.py-const | courier_api/config.py`
- `carriers[]`: wszystkie fizyczne nośniki (dla dual-carrier)
- `owner`: serwis/osoba
- `lifecycle`: `planned|shadow|live|deprecated|dead`
- `default`: wartość-fallback
- `current_snapshot`: efektywna wartość (mapa per-service dla świata 1b)
- `consumers[]`: `plik:linia`
- `rollback`: `flag=OFF+restart X | rm drop-in | git revert`
- `review_date`, `removal_condition`
- `twin_of[]`: **NOWE, kluczowe** — bliźniaki cross-world, także o różnej nazwie (TRUST_CANON_ORDER↔BUILD_VIEW_TRUST_CANON_ORDER)
- `intentional_per_process`: bool + uzasadnienie (import z klasyfikacji `flag_registry`)

### Tryby checkera
- **repo-hermetic (CI, bez hosta):** waliduje rejestr vs źródła STATYCZNE w repo (flags.json po filtrze `_comment*`, tuple z common.py, panel `DEFAULT_FLAGS` przez AST, courier `config.py`, sparsowane `.conf` jeśli wersjonowane). Wykrywa: flaga w kodzie bez wpisu / wpis bez konsumenta / twin bez linku / brak owner-review-removal / dryf default. Reużyj `flag_registry.scan_common` + AST panel/courier. Deterministyczny, ZERO `journalctl`/`systemctl`.
- **`--live` (host, timer/guard):** dokłada efektywny stan per-proces (linia FLAG_FINGERPRINT z `journalctl` jak `flag_fingerprint_check` — **nigdy** `systemctl show -p Environment`) + realne `/etc/systemd/*.d` → runtime-drift vs rejestr (świat 1b).

### Zasada nie-dublowania
Rejestr lifecycle = **warstwa metadanych PONAD** `flag_registry` (engine 3-źródła + klasyfikacja intentional już gotowe) + rozszerzenie na PANEL/APKA + TWINS. Dead-flag (`flag_hygiene`), doc-coverage, effect-coverage — **odwołaj się, nie kopiuj**. ⚠ Rejestr MUSI używać regexu env-frozen `(?:_os|os)\.environ\.get` + `getenv` (z `flag_registry`), bo grep z karty milcząco gubi świat 1b (alias `_os`).
