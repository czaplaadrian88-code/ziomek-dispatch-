# Sprint 4 · Z-P1-07 Faza A — Rejestr i cykl życia flag (RAPORT)

**Agent C (builder), 2026-07-10.** Worktree `sprint4/z-p1-07-flags` (baza `c2bde58`).
Cel: maszynowy rejestr lifecycle WSZYSTKICH trzech światów flag + checker CI
wykrywający dryft. **Wartości flag NIETKNIĘTE — zero flipów/retirementu/edycji
nośników.** Rejestr = warstwa DANYCH; runtime silnika/panelu/apki nietknięty.

## Co dostarczono (7 NOWYCH plików, zero edycji istniejących)
- `tools/flag_lifecycle_seed.py` — generator (skan 3 światów + szeroki 1b).
- `tools/flag_lifecycle_check.py` — checker `--repo-hermetic` (CI) + `--live` (host).
- `tools/flag_lifecycle_registry.json` — rejestr (504 flagi, deterministyczny).
- `tests/test_flag_lifecycle_zp107.py` — 13 testów CI (hermetyczne).
- `docs/flags/README.md` + `docs/flags/INVENTORY_2026-07-10.md`.
- `eod_drafts/2026-07-10/SPRINT4_ZP107_FLAGS_RAPORT.md` (ten plik).

## Liczności rejestru per świat
| Świat | Flag |
|---|---:|
| **Razem** | **504** |
| SILNIK | 391 (flags.json 242 / ETAP4 126 / FP_EXTRA 33 / NUMERIC 26 / TEST_ISO 4 / +bool-toggle modułu +1b) |
| PANEL | 86 (DEFAULT_FLAGS 81 + 5 env-only) |
| APKA | 27 |

Świat 1b: **12 unitów** systemd pinuje flagi (nie tylko rdzeń-5). Lifecycle
(heurystyka): live 378 / shadow 44 / planned 82 / deprecated 0 / dead 0.

## Wynik checkera (uruchomiony NA HOŚCIE)
- `--repo-hermetic --flags-json /root/.openclaw/workspace/scripts/flags.json` →
  **exit 0, 0 błędów** (504 flagi; struktura + coverage silnika + flags.json +
  cross-repo host).
- `--live --fingerprint` → **exit 0, 0 błędów**; FLAG_FINGERPRINT obecny w
  `shadow / plan-recheck / panel-watcher / czasowka`. Seed=snapshot z dziś → 0 dryfów.
- Corruption-test (dowód nietrywialności): usunięcie wpisu ETAP4 / zerwanie twina /
  brak pola → **exit 1** (checker łapie regresję).

## known_drift (ODNOTOWANY, NIE naprawiany)
`USE_V2_PARSER` — env=1 tylko w `dispatch-panel-watcher`, cross-service GENUINE
(z `flag_registry.KNOWN_DIVERGENCES`). W rejestrze `known_drift: true`; w `--live`
raportowany jako known (NIE liczy się do exit≠0). Domknięcie = migracja do
flags.json + ACK Adriana = OSOBNE zadanie (parser behavior-affecting).

## Weryfikacja 3× geocode dual-carrier (karta pkt 5 — diagnoza, ZERO naprawy)
`ENABLE_GEOCODE_NOMINATIM_FALLBACK` / `ENABLE_GEOCODE_PIN_MEMORY_FALLBACK` /
`ENABLE_GEOCODE_VERIFICATION_ENFORCE`: `geocoding.py` czyta je przez
`C.flag("<NAME>", C.<NAME>)` — **flags.json (hot-reload) WYGRYWA, zamrożona stała
modułu jest TYLKO defaultem**. To POPRAWNY dual-carrier, **NIE antywzorzec #9**
("json wygląda live, moduł czyta env"). Wynik zapisany w `registry.notes` tych flag
+ INVENTORY. Nic nie zmieniam.

## Regresja / testy
- Baseline przed: **4710 passed / 24 skipped / 10 xfailed / 0 failed**.
- Po (pełna suita `pytest tests/`): **4723 passed / 24 skipped / 10 xfailed / 0 failed**
  (+13 nowych, 0 regresji). `py_compile` obu narzędzi OK.
- Test hermetyczny: ZERO odczytu /etc, dispatch_state, journalctl, żywego flags.json
  (cross-repo wymuszony na skip przez nieistniejące ścieżki; flags.json = fixtura tmp).

## 3 ustalenia empiryczne (zatwierdzone przez lidera, doprecyzowują A3)
1. **Świat 1b szeroki:** 35 katalogów `dispatch-*.service.d`; satelity (`route-flag-
   parity.conf`, `engine-env-parity.conf`) pinują dziesiątki flag decyzyjnych.
   Seeder skanuje CAŁY glob + main-unity → 12 unitów z flagami (A3 miał ~11 flag/5 unitów).
2. **`Environment=` wieloparowe:** własny `_parse_systemd_env` (shlex + secret-filter
   per token) obsługuje `A=1 B=1`; `flag_registry.scan_unit_env` (split("=",1))
   gubi 2..n parę — **known-limitation ODNOTOWANA** (README §known-limitation),
   NIE naprawiam (poza zakresem; wartości flag nietykane).
3. **Source-parse zamiast `import common`:** checker/test parsują ŹRÓDŁO common.py
   (reużycie `flag_registry`), niezależnie od venv i side-effectów import-time.
   Coverage silnika w teście liczony NIEZALEŻNIE od rejestru (anty-tautologia).

## Reużycie (nie dublowanie)
Importuję z `flag_registry`: `scan_common`, `scan_literal_defaults`,
`scan_decision_lists`, `load_flags_json`, `scan_code_tokens`, `_extract_paren_body`,
`INTENTIONAL_PER_PROCESS`, `SERVICE_SCOPED`, `KNOWN_DIVERGENCES`,
`DYNAMIC_KEY_FAMILIES`. Wzorzec journalctl (`parse_fingerprints`) z
`flag_fingerprint_check` dla `--live --fingerprint`. Dead-flag/doc-coverage/
effect-coverage/per-serwis-fingerprint — README ODSYŁA, nie reimplementuję.

## Wymogi lidera — status
1. ✅ Drop-iny satelitów w `current_snapshot` per-SERVICE (parity-guardy pinują env
   dla swoich proc.) — np. `ENABLE_CARRIED_FIRST_RELAX` =
   `{flags.json: true, dispatch-b-route-shadow.service: true}`.
2. ✅ Filtr sekretów per linia env (TOKEN/SECRET/PASS/KEY/DSN/http…), logujemy tylko
   LICZBĘ (apka=1 odrzucona, nazwy/wartości nigdzie).
3. ✅ `registry.json` deterministyczny (`sort_keys`, indent 2) — idempotentny
   (identyczny na re-run).
4. ✅ 3 ustalenia zatwierdzone; limitacja `flag_registry.scan_unit_env` odnotowana
   w README + tu (bez naprawy).
5. ✅ Pełna suita 0 failed +13, py_compile.
6. ✅ Checker `--repo-hermetic` i `--live` uruchomione na hoście (wyniki wyżej).

## Odstępstwa / ODŁOŻONE (poza zakresem, ZA ACK)
- **Świadome zawężenie:** numeryczne/stringowe stałe env SILNIKA (nie-toggle, spoza
  flags.json/NUMERIC) = KONFIG, NIE flaga lifecycle (~230 consty odsiane; boolowskie
  toggle-e i numeryczne z flags.json/NUMERIC zostają). Apka zachowuje numeryczne
  knoby zachowania. Transparentne — gdyby Adrian chciał rejestr konfiguracji, to
  osobna warstwa.
- **Kuracja Adriana:** `lifecycle`/`owner`/`review_date`/`removal_condition` seedowane
  heurystyką (`lifecycle_seeded: true`) — do przeglądu.
- **Migracja 1b→flags.json** (hot-reload cross-service; `USE_V2_PARSER`,
  plan-recheck env) = osobne zadanie + ACK.
- **Retirement flag** `dead` — dziś 0; gdy pojawią się, retirement osobno.
- **Poprawka `flag_registry.scan_unit_env`** (multi-para) — osobne zadanie.

## Rollback
`git revert <commit>` — rejestr to DANE, zero wpływu na runtime (żaden serwis go nie
importuje; checker uruchamiany ręcznie/CI).
