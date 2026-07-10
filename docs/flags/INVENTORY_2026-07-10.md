# Inwentarz flag — projekcja rejestru (seed 2026-07-10)

Wygenerowane z `tools/flag_lifecycle_registry.json` (seed z żywych źródeł hosta
2026-07-10). Liczby to snapshot dnia seeda; kanon = plik JSON.

## Liczności per świat
| Świat | Flag | Uwaga |
|---|---:|---|
| **Razem** | **504** | unia 3 światów (nazwy różne → brak same-name multi-world) |
| SILNIK (engine) | 391 | flags.json=242, ETAP4=126, FP_EXTRA=33, NUMERIC=26, TEST_ISO=4, +boolowskie toggle-consty modułu i pin 1b |
| PANEL | 86 | DEFAULT_FLAGS=81 (+5 env-only `PANEL_FLAG_*` spoza DEFAULT_FLAGS) |
| APKA | 27 | env-frozen `courier_api` + drop-iny (bool + numeryczne knoby zachowania) |

Sekrety odrzucone przy skanie env: engine_1b=0, panel=0, apka=1 (`ETA_API_TOKEN`/
`COURIER_ADMIN_PASS`-klasa — nazwy/wartości NIE trafiają do rejestru).

## Cykl życia (heurystyka `lifecycle_seeded` — do kuracji Adriana)
| lifecycle | ALL | engine | panel | apka |
|---|---:|---:|---:|---:|
| live | 378 | 278 | 77 | 23 |
| shadow | 44 | 40 | 4 | 0 |
| planned | 82 | 73 | 5 | 4 |
| deprecated | 0 | 0 | 0 | 0 |
| dead | 0 | 0 | 0 | 0 |

`dead=0` spójne z `flag_hygiene_check` (0 sierot w flags.json). Retirement martwych
= osobne, kontrolowane zadanie (żadnej flagi tu nie usuwamy).

## Świat 1b — flagi silnika ZAMROŻONE w systemd (per-service)
**12 unitów** pinuje flagi (nie tylko rdzeń-5 — to główny zysk kompletności vs A3):

| Unit | Flag pinowanych | Charakter |
|---|---:|---|
| `dispatch-b-route-shadow.service` | 15 | `route-flag-parity.conf` — parytet flag decyzyjnych dla shadow trasy |
| `dispatch-pickup-floor-guard.service` | 5 | guard floor odbioru |
| `dispatch-shadow.service` | 5 | rdzeń (LGBM/pending/replay/bg-refresh) |
| `dispatch-czasowka.service` | 3 | czasówka (dryrun/retroactive/max-emit) |
| `dispatch-bundle-calib-shadow.service` | 3 | kalibracja bundli |
| `dispatch-reassignment-shadow.service` | 3 | shadow przerzutów |
| `dispatch-carried-first-guard.service` | 2 | parytet carried-first |
| `dispatch-nogps-equal-watch.service` | 2 | no-GPS equal-treatment |
| `dispatch-panel-watcher.service` | 2 | `USE_V2_PARSER`(known_drift) + bg-refresh=0 |
| `dispatch-plan-recheck.service` | 2 | committed-propagation + live-eta-refresh |
| `dispatch-cod-weekly.service` | 1 | autocreate |
| `dispatch-pending-pool.service` | 1 | pending-pool |

Zapisane w `current_snapshot` jako mapa per-service (parity-guardy pinują env dla
SWOICH procesów). Dla flag decyzyjnych kanon = `flags.json`; per-service pin =
snapshot parytetu (rekoncyliowany runtime przez `flag_fingerprint_check`).

## Bliźniaki cross-world (5 konceptów, wszystkie dziś KOHERENTNE — oba ON)
Nazwy RÓŻNE (panel gubi prefiks `ENABLE_`; `TRUST_CANON_ORDER↔BUILD_VIEW` = głębszy
rename) → zlinkowane `twin_of` dwustronnie:

| PANEL | APKA |
|---|---|
| `DELIVERY_DASH_WHEN_NO_PLAN` | `ENABLE_DELIVERY_DASH_WHEN_NO_PLAN` |
| `LIVE_ETA_COURIER_GUARD` | `ENABLE_LIVE_ETA_COURIER_GUARD` |
| `PLAN_AWARE_PODJAZDY` | `ENABLE_PLAN_AWARE_PODJAZDY` |
| `TRUST_CANON_ORDER` | `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER` (różna nazwa!) |
| `LIVE_ETA_FRESH_OVERRIDE_ONLY` | `ENABLE_LIVE_ETA_FRESH_OVERRIDE_ONLY` |

## Rozjazdy / adnotacje
- **known_drift = 1:** `USE_V2_PARSER` (env=1 tylko w `dispatch-panel-watcher`;
  cross-service GENUINE — z `flag_registry.KNOWN_DIVERGENCES`). ODNOTOWANY,
  NIE naprawiany (migracja do flags.json+ACK = osobne zadanie).
- **intentional_per_process = 11:** `ENABLE_PANEL_BG_REFRESH`, `ENABLE_LGBM_SHADOW`,
  `ENABLE_LGBM_METRICS_READ`, `ENABLE_PENDING_POOL`, `ENABLE_OBJ_REPLAY_CAPTURE`,
  `ENABLE_LOADAWARE_SELECTION_SHADOW`, `CZASOWKA_TELEGRAM_DRYRUN`,
  `CZASOWKA_RETROACTIVE_HOURS`, `CZASOWKA_MAX_EMIT_PER_TICK`,
  `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION`, `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH`
  (import z `flag_registry` — celowy split per-proces, NIE bug).
- **geocode dual-carrier (3):** `ENABLE_GEOCODE_NOMINATIM_FALLBACK`,
  `ENABLE_GEOCODE_PIN_MEMORY_FALLBACK`, `ENABLE_GEOCODE_VERIFICATION_ENFORCE` —
  weryfikacja: `geocoding.py` czyta `C.flag(NAME, C.NAME)` → **flags.json hot-reload
  wygrywa, stała modułu = TYLKO default**. NIE antywzorzec #9. (`notes` w rejestrze.)

## Wynik checkera (host, 2026-07-10)
- `--repo-hermetic --flags-json <żywy>`: **0 błędów** (504 flag).
- `--live --fingerprint`: **0 błędów**; FLAG_FINGERPRINT obecny w procesach
  `shadow / plan-recheck / panel-watcher / czasowka`. Seed=snapshot z dziś → 0 dryfów.

## Kuracja 2026-07-10 (ACK Adrian)
Wszystkie **504/504** wpisy skurowane (`curated_at=2026-07-10`, `lifecycle_seeded=false`):
- **owner**: techniczny = serwis wyprowadzony z nośników (panel→`nadajesz-panel`, apka→`courier-api`, 1b→konkretny unit pinujący, np. `dispatch-b-route-shadow`; czyste flags.json→`dispatch-shadow` jako główny konsument silnika); biznesowy = **Adrian** (100%).
- **lifecycle z DOWODÓW** (wartość niedefaultowa/ON w jakimkolwiek nośniku → live; nazwa/semantyka shadow-log → shadow; wszędzie OFF/default → planned): **live 401 / shadow 48 / planned 55 / dead 0**. Zmiany klasy vs seed odnotowane w `notes` per wpis; niepewne heurystyki (numeryk=0) oznaczone.
- **review_date per klasa**: live→2026-10-10, shadow→2026-08-10, planned→2026-09-10, intentional_per_process→2026-08-10 (kandydaci migracji 1b za ACK).
- **removal_condition** per klasa (live „n/d dopóki live"; shadow „po werdykcie flip/reject + 2 dni"; planned „jeśli nie wpięta do 2026-12 → retire za ACK").
- **RE-SEED**: `flag_lifecycle_seed.py --merge --out <kanoniczny plik>` zachowuje pola kuracji W MIEJSCU (dowód: 504/504 zachowane, 0 rozjazdów; test `test_reseed_merge_preserves_curation_pure`). ⚠ `--merge` czyta stary rejestr Z `--out` — re-seed pod inną ścieżką nie ma czego zachować (wypisze „zachowano 0"). Seed bez `--merge` na skurowany plik → głośne ostrzeżenie.
- Checker wymusza kurację (`check_curation`): brak ownera/removal/spójności curated_at↔seeded = błąd (exit 1). USE_V2_PARSER pozostaje `known_drift` (migracja = osobny ACK).
