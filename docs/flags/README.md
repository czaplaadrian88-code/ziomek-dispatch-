# Rejestr i cykl życia flag (Z-P1-07 Faza A)

**Maszynowy rejestr WSZYSTKICH flag trzech światów + checker CI wykrywający dryft.**
Cel: żadna flaga nie ominie rejestru, dokumentacji ani testów; martwe eksperymenty
nie mnożą kombinacji zachowań w nieskończoność. Wartości flag **NIE są tu zmieniane**
— to warstwa DANYCH/METADANYCH nad kodem (zero wpływu na runtime).

## Pliki
| Plik | Rola |
|---|---|
| `tools/flag_lifecycle_registry.json` | Kanon rejestru (generowany, commitowany, deterministyczny). |
| `tools/flag_lifecycle_seed.py` | Generator/refresher — skan 3 światów. Uruchamiany NA HOŚCIE. |
| `tools/flag_lifecycle_check.py` | Checker: `--repo-hermetic` (CI) + `--live` (host). |
| `tests/test_flag_lifecycle_zp107.py` | Test CI (hermetyczny). |
| `docs/flags/INVENTORY_*.md` | Ludzka projekcja (liczności/dryfy/twins) na dzień seeda. |

## Trzy światy flag (ADR-004 — `docs/decisions/ADR-004-flagi-trzy-swiaty.md`)
- **SILNIK** — kanon `flags.json` (hot-reload przez `C.flag()`/`decision_flag()`),
  definicje/defaulty w `common.py` (tuple `ETAP4_DECISION_FLAGS`,
  `_FINGERPRINT_EXTRA_FLAGS`, `FLAGS_JSON_NUMERIC_OVERRIDES`,
  `TEST_ISOLATED_INFRA_FLAGS`). **Świat 1b** = flagi silnika ZAMROŻONE w systemd
  (`Environment=` w `dispatch-*.service(.d)` — nie tylko rdzeń-5; satelitarne
  `*-parity.conf` pinują flagi decyzyjne dla SWOICH procesów shadow/guard).
- **PANEL** — `DEFAULT_FLAGS` w `app/core/flags.py` + `flags.systemd.env`
  (`PANEL_FLAG_<name>`) + inline drop-iny `nadajesz-panel.service.d/`. (Repo
  `nadajesz_clone` — osobny projekt; seeder tylko CZYTA.)
- **APKA** — `courier_api/*.py` (+`courier_api_panelsync/`) env-frozen consty +
  drop-iny `courier-api.service.d/`. Kanoniczna tożsamość flagi apki = **nazwa ENV**
  (to co ustawia drop-in), a stała modułu = binding konsumenta.

## Schema wpisu (per flaga)
`name`, `worlds[]`, `source_of_truth`, `carriers[]` (wszystkie nośniki fizyczne),
`owner{service,business}`, `lifecycle` (`planned|shadow|live|deprecated|dead`) +
`lifecycle_seeded` (heurystyka do kuracji Adriana), `default`, `current_snapshot`
(świat 1b: mapa **per-service**; kanon decyzyjny: `flags.json`), `consumers[]`
(plik:symbol, bez nr linii — dryfują), `rollback`, `review_date`,
`removal_condition`, `twin_of[]` (bliźniaki cross-world, też o RÓŻNEJ nazwie),
`intentional_per_process{value,reason}` (import z `flag_registry`), `known_drift`
(+`known_drift_note`), `notes`.

## Jak dodać flagę (żeby CI nie zapłakało)
1. Dodaj flagę w kodzie (silnik: `flags.json`+tuple `common.py`; panel:
   `DEFAULT_FLAGS`; apka: `os.environ.get` + drop-in).
2. Na hoście: `python3 tools/flag_lifecycle_seed.py` (regeneruje rejestr z żywych
   źródeł; zachowaj ręczne `notes` przez `--merge`).
3. `python3 tools/flag_lifecycle_check.py --flags-json /root/.openclaw/workspace/scripts/flags.json`
   → 0 błędów. Skoryguj `lifecycle`/`owner`/`review_date`/`removal_condition`
   (heurystyka `lifecycle_seeded` to punkt startowy, nie werdykt).
4. Commit rejestru RAZEM z kodem flagi.

## Tryby checkera
- **`--repo-hermetic` (domyślny, CI):** struktura + coverage silnika (source-parse
  `common.py`, NIEZALEŻNIE od rejestru) + `flags.json` przez `--flags-json PATH`.
  Cross-repo (panel/apka/systemd) = **skip-if-absent**. ZERO odczytu hosta/journalctl.
  Wykrywa: flagę w źródle bez wpisu (SIEROTA), wpis-widmo (flaga znikła), dryf
  wartości, twin bez linku zwrotnego, brak pól/lifecycle. Exit≠0 = błąd.
  **Bez baseline-wyjątków** — rejestr ma być kompletny w dniu seeda.
- **`--live` (host, READ-ONLY):** dokłada rekoncyliację `current_snapshot` vs żywe
  nośniki (flags.json + `/etc/systemd/*.d`) + opcjonalnie `--fingerprint`
  (`FLAG_FINGERPRINT` z **journalctl**, wzorem `flag_fingerprint_check` — **NIGDY**
  `systemctl show -p Environment`, bo nie renderuje `EnvironmentFile=`). `known_drift`
  (dziś `USE_V2_PARSER`) jest RAPORTOWANY, **nie liczy się jako błąd**.

## Relacja do istniejących checkerów (NIE dublujemy — odsyłamy)
| Narzędzie | Co robi | Rejestr lifecycle |
|---|---|---|
| `tools/flag_registry.py` | inwentarz SILNIKA (3 źródła) + klasyfikacja rozjazdów | **reużywamy** jego skanery + `INTENTIONAL_PER_PROCESS`/`SERVICE_SCOPED`/`KNOWN_DIVERGENCES` |
| `tools/flag_hygiene_check.py` | flagi-sieroty (dead-flag) w flags.json | odsyłamy (lifecycle `dead` heurystyką) |
| `tools/flag_doc_coverage_check.py` | coverage DOKUMENTACJI flag | odsyłamy |
| `tools/flag_effect_coverage_check.py` | coverage TESTÓW EFEKTU flag | odsyłamy |
| `tools/flag_fingerprint_check.py` | rekoncyliacja per-serwis z journalctl | odsyłamy; `--live --fingerprint` reużywa `parse_fingerprints` |

Rejestr lifecycle DOKŁADA: PANEL+APKA+szeroki 1b, TWINS cross-world, metadane
lifecycle (owner/review/removal/rollback) — czego żaden z powyższych nie robi.

## ⚠ Known-limitation (odnotowane, NIE naprawiane w tym zadaniu)
`flag_registry.scan_unit_env` parsuje `Environment=` przez `split("=",1)` → dla
linii WIELOPAROWEJ (`Environment=A=1 B=1`, legalnej w systemd) gubi pary 2..n.
Dla rdzenia-5 (1 para/linię) jest to nieszkodliwe, ale świat 1b bywa wieloparowy w
satelitach → seeder ma WŁASNY `_parse_systemd_env` (shlex, obsługuje wielo-parę +
filtr sekretów per token). Poprawka `flag_registry` = osobne zadanie (poza zakresem
Z-P1-07; wartości flag nietykane).

## Bezpieczeństwo / higiena
- Filtr sekretów na KAŻDEJ linii env (odrzuca `TOKEN|SECRET|PASS|KEY|DSN|CRED|COOKIE|AUTH`
  + URL/ścieżki); w rejestrze i raportach są WYŁĄCZNIE flagi (logujemy tylko LICZBĘ
  odrzuconych sekretów, nigdy ich nazwy/wartości).
- Rejestr deterministyczny (`sort_keys`, stały indent) — diff-friendly.
- Rollback całości = `git revert` commita (rejestr to dane; runtime nietknięty).
- Świadome zawężenie: numeryczne/stringowe stałe env SILNIKA (nie-toggle, spoza
  flags.json/NUMERIC) traktujemy jako KONFIG, nie flagę lifecycle. Apka zachowuje
  numeryczne knoby zachowania (np. `LIVE_ETA_MAX_AGE_MIN`).

## Kuracja metadanych (2026-07-10, ACK Adrian)
Rejestr jest SKUROWANY: 504/504 wpisów ma ownera (serwis + biznes=Adrian), lifecycle
z dowodów (401 live / 48 shadow / 55 planned), review_date i removal_condition per
klasa — szczegóły w `INVENTORY_2026-07-10.md`. Checker (`check_curation`) wymusza
kompletność kuracji. **Re-seed WYŁĄCZNIE przez `--merge` na kanonicznym pliku**
(zachowuje pola kuracji; seed bez `--merge` ostrzega, że by je nadpisał). Wartości
flag NIE są przez kurację dotykane; migracja 1b→flags.json (w tym USE_V2_PARSER)
= osobne zadanie za ACK.
