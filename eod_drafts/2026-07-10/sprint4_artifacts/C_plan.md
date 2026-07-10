# C_plan — Z-P1-07 Faza A: Rejestr i cykl życia flag (RAPORT PLANU, agent C)

Agent C (builder), Sprint 4. **FAZA 1 = plan (ZERO edycji worktree).** Worktree: `/root/sprint4_wt/wt-flags/dispatch_v2` (branch `sprint4/z-p1-07-flags`, baza `c2bde58`, czysty). Przestrzenie `docs/flags/` i `tools/flag_lifecycle*` = ABSENT (wolne, potwierdzone). **Wartości flag NIETYKANE — zero flipów, zero retirementu, zero edycji nośników.**

Przeczytane wejścia: A3_flags.md + wszystkie A3_flags_*.txt, A1_kolizje.md, B_plan.md, karta Z-P1-07 (backlog l.265-276), ADR-004, `tools/flag_registry.py` (cały), `tools/flag_fingerprint_check.py` (wzorzec journalctl), `tools/flag_hygiene_check.py`. Recon read-only na hoście: `common.py` kotwice tupli (144/654/700/714 + funkcje 70/102/776/792), panel `app/core/flags.py` (DEFAULT_FLAGS dict-literal), `flags.systemd.env` (PANEL_FLAG_*=1), courier_api/config.py (env-get pattern), 35× `dispatch-*.service.d`, drop-iny panel/courier.

---

## 0. USTALENIA EMPIRYCZNE, KTÓRE ZMIENIAJĄ PLAN vs A3 (przeczytaj najpierw)

1. **Świat 1b jest DUŻO szerszy niż 11 flag z A3.** A3 zmapował tylko rdzeń-5 (`ENGINE_UNITS` znane `flag_registry`). Na hoście jest **35 katalogów `dispatch-*.service.d`**, a satelity pinują dziesiątki flag decyzyjnych silnika przez drop-iny `*-parity.conf`:
   - `dispatch-carried-first-guard/engine-env-parity.conf`, `dispatch-b-route-shadow/route-flag-parity.conf` → `ENABLE_CARRIED_FIRST_RELAX`, `ENABLE_GPS_FREE_ANCHOR_LAST_POS`, `ENABLE_CARRIED_AGE_TZ_FIX`, `ENABLE_LEX_COMMITTED_WINDOW(_SHADOW)`, `ENABLE_NONCARRIED_DROPOFF_REORDER`, `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP`, `ENABLE_PLAN_CANON_ORDER_INVARIANTS`, `ENABLE_PLAN_REAL_PICKED_UP_AT`, `ENABLE_PLAN_SEQUENCE_LOCK`, `ENABLE_REASSIGN_*`, `ENABLE_RELAX_COLOC_PICKUP` itd.
   - `dispatch-bundle-calib-shadow/lambda0-recollect.conf` → `BUNDLE_CALIB_LAMBDA_CZAS=0`.
   **Wniosek:** seeder skanuje **cały glob `/etc/systemd/system/dispatch-*.service.d/*.conf` + main-unity `dispatch-*.service`**, nie tylko rdzeń-5. To realny zysk kompletności (bliźniacze ścieżki parity RAZEM).
2. **`Environment=` bywa WIELOPAROWE.** Rdzeń-5 ma 1 parę/linię, ale satelity potrafią spakować `Environment=A=1 B=1 C=1 …`. `flag_registry.scan_unit_env` robi `body.split("=",1)` → gubi 2..n parę (latentne dla core-5, realne dla satelitów). Seeder dostanie **własny, odporny parser** `_parse_systemd_env` (split po whitespace z poszanowaniem cudzysłowów, każdy token `K=V`). To NIE poprawka `flag_registry` (nie tykam) — to szerszy zakres 1b; różnicę odnotuję w raporcie jako known-limitation istniejącego skanera (bez naprawy).
3. **APKA: kanoniczną TOŻSAMOŚCIĄ flagi jest NAZWA ENV** (to co ustawia drop-in: `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER`), a stała modułu (`BUILD_VIEW_TRUST_CANON_ORDER`) to consumer-binding. Kluczuję po nazwie env, consumer = `courier_api/config.py:<CONST>`.
4. **PANEL namespace:** `flag(name)` = env `PANEL_FLAG_<name>` → else `DEFAULT_FLAGS[name]`; drop-in wygrywa nad env-file. Kluczuję po nazwie z DEFAULT_FLAGS (bez prefiksu), carriers rejestrują `PANEL_FLAG_<name>` gdzie występuje.
5. **`import common` — NIE używam w hermetycznej ścieżce.** `flag_registry` świadomie parsuje ŹRÓDŁO (`scan_decision_lists` „bez importu modułu — tool ma działać na drzewie bez venv"). Idę tym samym torem (AST/source-parse) → checker/test niezależne od venv/ortools i wolne od side-effectów import-time (setup_logger pisze pliki). To celowe odstępstwo od „import LUB AST" z karty na rzecz AST.

---

## 1. WRITE-SET (wyłącznie NOWE pliki — zgodne z zatwierdzoną listą lidera)

| # | Plik (worktree-relative) | Rola |
|---|---|---|
| 1 | `tools/flag_lifecycle_seed.py` | Generator/refresher rejestru — skan 3 światów (+1b broad). Uruchamiany NA HOŚCIE, wynik commitowany. |
| 2 | `tools/flag_lifecycle_check.py` | Checker: `--repo-hermetic` (default, CI) + `--live` (host). Exit 0/≠0. |
| 3 | `tools/flag_lifecycle_registry.json` | Zacommitowany rejestr (seed 2026-07-10 + ręczne adnotacje twins/known_drift/notes). |
| 4 | `tests/test_flag_lifecycle_zp107.py` | Test CI — hermetyczny (struktura + coverage vs source-parse + fixture tmp flags.json). |
| 5 | `docs/flags/README.md` | Ludzki opis: 3 światy, cykl życia, jak dodać flagę, relacja do istniejących checkerów. |
| 6 | `docs/flags/INVENTORY_2026-07-10.md` | Projekcja: liczności per świat, dryfy, twins, known_drift, lifecycle histogram. |
| 7 | `eod_drafts/2026-07-10/SPRINT4_ZP107_FLAGS_RAPORT.md` | Raport końcowy sprintu. |

Uwaga: zatwierdzona ścieżka to **`tools/flag_lifecycle_registry.json`** (płasko, prefiks `flag_lifecycle_`), NIE `tools/flag_lifecycle/registry.json` z rekomendacji A3 — idę wg listy lidera.

**ZAKAZ edycji (potwierdzam):** `common.py`, `flags.json`/`flags.snapshot.json` i JAKIKOLWIEK nośnik flag, istniejące `tools/flag_*.py`, panel repo (`nadajesz_clone`), `courier_api/**`, `tests/conftest.py`, `ZIOMEK_LOGIC_REFERENCE.md`, pliki S2 (na master) i S3 (domniemane). Nie tworzę root `conftest.py`. Test self-lokalizuje fixtury.

---

## 2. SCHEMA WPISU REJESTRU (pola + przykład)

Rejestr = `{"_meta": {...}, "flags": { "<NAME>": {…} }}`. Deterministyczny (klucze sort, `json.dumps(…, indent=2, ensure_ascii=False, sort_keys=True)`).

Pola per flaga (wszystkie wymagane przez kartę pkt 1 + D):
- `name` — kanoniczna nazwa (engine/panel = nazwa flagi; apka = nazwa ENV).
- `worlds[]` — podzbiór `["engine","panel","apka"]` (twin wieloświatowy → >1).
- `source_of_truth` — jeden z: `flags.json` | `common.py-const` | `DEFAULT_FLAGS` | `flags.systemd.env` | `drop-in:<plik>` | `courier_api/config.py`.
- `carriers[]` — WSZYSTKIE fizyczne nośniki (dual-carrier), np. `["flags.json","common.py:ETAP4_DECISION_FLAGS","drop-in:dispatch-b-route-shadow.service.d/route-flag-parity.conf"]`.
- `owner` — `{"service": "<unit właściciel-wykonawca>", "business": "Adrian"}`.
- `lifecycle` — `planned|shadow|live|deprecated|dead` (heurystyka, patrz §3) + `lifecycle_seeded: true`.
- `default` — wartość fallback (z common.py/DEFAULT_FLAGS/env-get default).
- `current_snapshot` — mapa (świat 1b: per-service; inne: `{"effective": <v>}` lub `{"flags.json": <v>}`). Wartości Z SEEDA, dzień 2026-07-10.
- `consumers[]` — `plik:symbol` bez numerów linii (§4 metoda).
- `rollback` — tekst: `"flags.json OFF hot-reload"` / `"rm drop-in <plik> + restart <unit> ZA ACK"` / `"env OFF w common.py + restart <unit>"` / `"n/d — wartość niezmieniana w tym zadaniu"`.
- `review_date` — seed `2026-08-10`.
- `removal_condition` — seed: opis lub `"n/d-live"` (dla LIVE core-flag).
- `twin_of[]` — nazwy bliźniaków cross-world (także różno-nazwa).
- `intentional_per_process` — `{"value": bool, "reason": "<import z flag_registry.INTENTIONAL_PER_PROCESS / SERVICE_SCOPED>"}`.
- `known_drift` — `bool` (seed `true` tylko dla `USE_V2_PARSER` z `flag_registry.KNOWN_DIVERGENCES`), + `known_drift_note`.
- `notes` — wolny tekst (m.in. wynik weryfikacji geocode §7, adnotacje service-scoped).

**Przykład (twin różno-nazwa, apka):**
```json
"ENABLE_BUILD_VIEW_TRUST_CANON_ORDER": {
  "name": "ENABLE_BUILD_VIEW_TRUST_CANON_ORDER",
  "worlds": ["apka"],
  "source_of_truth": "drop-in:courier-api.service.d/build-view-trust-canon.conf",
  "carriers": ["courier_api/config.py", "drop-in:courier-api.service.d/build-view-trust-canon.conf"],
  "owner": {"service": "courier-api.service", "business": "Adrian"},
  "lifecycle": "live", "lifecycle_seeded": true,
  "default": false,
  "current_snapshot": {"courier-api.service": true},
  "consumers": ["courier_api/config.py:BUILD_VIEW_TRUST_CANON_ORDER"],
  "rollback": "rm drop-in build-view-trust-canon.conf + restart courier-api.service ZA ACK",
  "review_date": "2026-08-10",
  "removal_condition": "n/d-live",
  "twin_of": ["TRUST_CANON_ORDER"],
  "intentional_per_process": {"value": false, "reason": ""},
  "known_drift": false,
  "notes": "Twin różno-nazwa: koncept 'ufaj kanonicznej kolejności planu'; panel = TRUST_CANON_ORDER."
}
```

---

## 3. HEURYSTYKA `lifecycle` (seed; ZAWSZE `lifecycle_seeded: true` → kuracja Adriana)

Kolejność decyzji (pierwsza pasująca wygrywa):
1. `dead` — klucz flags.json bez ŻADNEGO literalnego czytelnika (reużyj logikę `flag_hygiene_check`: `scan_code_tokens`/literal-scan) LUB flaga w tupli ETAP4 bez konsumenta. (A3: dziś 0 sierot — spodziewane 0.)
2. `deprecated` — jawny sygnał (nazwa zawiera `LEGACY`/`_OLD`/`_DEPRECATED`, albo w `flag_doc_baseline` oznaczona retired). Bez dowodu → nie deprecated.
3. `shadow` — nazwa zawiera `SHADOW`, albo istnieje bliźniak `<X>` + `<X>_SHADOW(_ONLY)`, albo decyzyjna z `current=False` mająca companion-shadow ON.
4. `live` — efektywna wartość ON/True i flaga konsumowana.
5. `planned` — default OFF, brak drop-inu włączającego, nie-shadow (panel DEFAULT_FLAGS=False bez env override).
Reszta → `planned` z notatką „heurystyka niepewna".

---

## 4. ARCHITEKTURA seeder / checker (repo-hermetic vs --live) + REUŻYCIE

### 4a. `flag_lifecycle_seed.py` (HOST-only; wynik = commit)
Loader `flag_registry` wzorem `flag_fingerprint_check._load_flag_registry()` (import-jako-pakiet → fallback load-by-path). **REUŻYWA z `flag_registry`:** `scan_common`, `scan_literal_defaults`, `scan_decision_lists`, `load_flags_json`, `scan_code_tokens`, `_extract_paren_body`, `ENGINE_UNITS`, `INTENTIONAL_PER_PROCESS`, `SERVICE_SCOPED`, `KNOWN_DIVERGENCES`, `DYNAMIC_KEY_FAMILIES`.
Skan per świat:
- **SILNIK:** flags.json (filtr `_comment*`) ∪ ETAP4 ∪ FP_EXTRA ∪ NUMERIC ∪ TEST_ISOLATED (source-parse: `scan_decision_lists` + mały AST/`_extract_paren_body` dla rozdzielenia FP_EXTRA i dla `TEST_ISOLATED_INFRA_FLAGS`) ∪ env-frozen module (regex autorytatywny `(?:_os|os)\.environ\.get|getenv` — z aliasem `_os`, NIE grep z karty).
- **ŚWIAT 1b (broad):** własny `_parse_systemd_env` po CAŁYM `dispatch-*.service.d/*.conf` + main-unity; **filtr flago-podobny** (nazwa `^[A-Z][A-Z0-9_]*$`, wartość ∈ {0,1,liczba,enum-słowo}); **filtr sekretów** (odrzuć zawierające `TOKEN|SECRET|PASS|KEY|DSN|http|CRED`); pomiń infra (`PYTHONPATH`, `*_JSONL`, `*_OUT`, ścieżki). Per-service mapa do `current_snapshot`.
- **PANEL:** AST-parse `DEFAULT_FLAGS` (dict-literal, klucze=Constant str, wartości True/False/inne) + `flags.systemd.env` (regex `PANEL_FLAG_<n>=…`) + drop-iny `nadajesz-panel.service.d/*.conf` (ten sam parser + secret-filter). Skip-if-absent (cross-repo).
- **APKA:** env-get regex po CAŁYM `courier_api/*.py` (nie sam config.py — ratelimit czyta 2 flagi indziej) + `courier_api_panelsync/*.py` + drop-iny `courier-api.service.d/*.conf`. Kluczowanie po nazwie ENV. Skip-if-absent.
- **TWINS:** tablica-seed 5 par (4 znane + różno-nazwa) w kodzie seedera; seeder wpisuje `twin_of` DWUSTRONNIE (obie strony) i waliduje symetrię.
Deterministyczny, idempotentny (ponowny seed = identyczny plik modulo ręczne adnotacje — patrz §8 strategia merge adnotacji).

### 4b. `flag_lifecycle_check.py`
- **`--repo-hermetic` (default, CI-safe):** waliduje `registry.json`:
  - STRUKTURA: każdy wpis ma wszystkie pola; brak duplikatów `name`; `twin_of` dwustronny (A↔B); `worlds` niepuste; `lifecycle` ∈ dozwolone.
  - COVERAGE vs źródła REPO-DERIVABLE: każda flaga z ETAP4∪FP_EXTRA∪NUMERIC∪TEST_ISOLATED (source-parse worktree `common.py`) MA wpis (worlds∋engine); wpis-widmo engine (claim ETAP4 a nie ma w tuplach) = błąd.
  - flags.json: przez `--flags-json PATH` (w CI = fixture tmp); porównanie kluczy (sierota-w-źródle / wpis-widmo) + dryf `current_snapshot["flags.json"]`. Bez PATH lub `--skip-external` → pomija część flags.json-value, reszta działa.
  - cross-repo (panel/apka/dropiny 1b): **skip-if-absent** (katalog nieobecny → skip, nie fail). Gdy obecny (host) → dokłada coverage panel/apka.
  - Exit ≠0 gdy jakikolwiek błąd. **BEZ baseline'owych wyjątków** (karta: rejestr kompletny w dniu seeda).
- **`--live` (HOST, READ-ONLY):** wszystko z hermetic + porównanie `current_snapshot` vs REALNE nośniki: żywy flags.json, `/etc/systemd/*.d` (przez `_parse_systemd_env`), opcjonalnie `FLAG_FINGERPRINT` z journala **wzorem `flag_fingerprint_check`** (`journalctl -u <unit> --no-pager -o cat`, NIGDY `systemctl show -p Environment`). Reużywam import `flag_fingerprint_check.parse_fingerprints`/`scan_unit_env` gdy dostępne. `known_drift=true` (USE_V2_PARSER) NIE liczy się jako błąd (odnotowany, nie naprawiany).

### 4c. Zasada nie-dublowania (karta pkt 4)
Rejestr = **warstwa metadanych PONAD** `flag_registry` (silnik 3-źródła + klasyfikacja intentional/scoped/known — importowane, nie kopiowane) + rozszerzenie PANEL/APKA/1b-broad + TWINS + lifecycle. Dead-flag (`flag_hygiene_check`), doc-coverage (`flag_doc_coverage_check`), effect-coverage (`flag_effect_coverage_check`), per-service fingerprint (`flag_fingerprint_check`) — **README odsyła, checker NIE reimplementuje**. Jedyny własny kod low-level: `_parse_systemd_env` (multi-pair, bo `flag_registry` gubi) + AST `DEFAULT_FLAGS`/tupli (bo panel to inny repo, a TEST_ISOLATED `flag_registry` nie parsuje).

---

## 5. PLAN TESTU CI (hermetyczny — ZERO /etc, /root/.openclaw/…/dispatch_state, journalctl)

`tests/test_flag_lifecycle_zp107.py` (nazwa zp107 unikalna). Fixtury: własna tmp flags.json (z `tmp_path`) LUB reużycie conftest `_isolate_flags_json` jeśli obecny; cross-repo = `pytest.skip` gdy katalog nieobecny.
Przypadki:
1. **Struktura rejestru:** `registry.json` parsuje się; każdy wpis ma komplet pól; brak duplikatów; `lifecycle` legalne; `review_date` = data.
2. **Twins dwustronne:** dla każdego `twin_of: [B]` w A → A ∈ `twin_of` B. Para różno-nazwa TRUST_CANON_ORDER↔ENABLE_BUILD_VIEW_TRUST_CANON_ORDER obecna i symetryczna.
3. **Coverage engine (niezależny od rejestru):** test sam liczy ETAP4∪FP_EXTRA∪NUMERIC∪TEST_ISOLATED przez `flag_registry.scan_decision_lists` + AST na **worktree** `common.py` → każda flaga ma wpis. (Anty-tautologia: źródło = common.py, nie registry.)
4. **Checker repo-hermetic zielony:** `flag_lifecycle_check.main(["--repo-hermetic","--flags-json",<tmp>,"--skip-external"])` → exit 0 na commitowanym rejestrze.
5. **Checker ŁAPIE regresję (dowód nietrywialności):** po wstrzyknięciu do tmp-kopii rejestru (a) usunięcia wpisu ETAP4, (b) zerwania twin-linku, (c) braku pola lifecycle → checker exit ≠0 (3 pod-asercje). Dowodzi flaga-ON≠OFF checkera.
6. **Cross-repo skip-safe:** wywołanie z nieobecnym panel/courier → skip, nie fail.
Cel: **0 failed, +6 nowych passed**; baseline **4710 passed/24 skipped/10 xfailed** nienaruszony.

---

## 6. WERYFIKACJA 3× geocode dual-carrier (karta pkt 5 — TYLKO diagnoza, ZERO naprawy)
Faza 2: grep konsumentów `ENABLE_GEOCODE_NOMINATIM_FALLBACK` / `ENABLE_GEOCODE_PIN_MEMORY_FALLBACK` / `ENABLE_GEOCODE_VERIFICATION_ENFORCE` — czy czytają `flag()`/`decision_flag()`/`load_flags()` (hot-reload, json wygrywa) czy zamrożoną STAŁĄ modułu (`NAME = os.environ.get(...)` przy imporcie → antywzorzec #9 „json wygląda live, moduł czyta env"). Wynik (per flaga: json-read vs env-frozen-const + plik:symbol) → `registry.notes` odnośnej flagi + sekcja raportu. NIE zmieniam kodu.

---

## 7. RAPORT (`SPRINT4_ZP107_FLAGS_RAPORT.md`) — zawartość
Liczności per świat (silnik: flags.json REAL 242 / ETAP4 126 / FP_EXTRA 33 / NUMERIC 26 / TEST_ISOLATED 4 / universe 159 / env-frozen-module; 1b-broad: realna liczba z seeda — >11 A3; panel: DEFAULT_FLAGS 81 / env 45 / dropin 3; apka: config env ~19-21 / panelsync / dropin 10); wynik seeda (liczba wpisów rejestru); wynik checkera repo-hermetic + `--live` (wklejony host-run, oczekiwane 0 dryfów bo seed=snapshot z dziś); **known_drift USE_V2_PARSER** (odnotowany, nie naprawiony); wynik weryfikacji geocode; lista twins; lifecycle histogram; co ODŁOŻONE (kuracja ownerów/review/removal przez Adriana; migracja 1b→flags.json = osobne zadanie ZA ACK; ewentualny retirement martwych = osobno); rollback (= `git revert` commita — rejestr to DANE, zero runtime).

---

## 8. RYZYKA / ODSTĘPSTWA / ODŁOŻONE
- **R1 — merge ręcznych adnotacji przy re-seedzie:** seeder generuje bazę; twins/known_drift/notes/geocode-verdict dokładam ręcznie. Re-run seedera nie może ich zdmuchnąć. **Rozwiązanie:** seeder ma tryb `--merge` (czyta istniejący registry, zachowuje pola `notes/twin_of/known_drift/*_seeded=false-po-kuracji`), a pola auto (default/current_snapshot/carriers/consumers) nadpisuje. W tym sprincie: seed raz + ręczne adnotacje + `--merge` idempotencja udowodniona testem.
- **R2 — `import common` w CI:** świadomie NIE importuję (side-effecty/venv). Source-parse przez `flag_registry`. Gdyby test-coverage wymagał czegoś spoza tupli — dokładam AST, nie import.
- **R3 — multi-pair `Environment=`:** własny parser; `flag_registry.scan_unit_env` NIE tykam (odnotowane jako known-limitation, nie fix — poza zakresem).
- **R4 — cross-repo w CI:** panel (`nadajesz_clone`) i courier_api mogą być nieobecne w pkgroot testowym → wszystkie cross-repo checki `skip-if-absent`. Seed (host) je widzi; CI nie wymaga.
- **R5 — sierota/known_drift a exit-code:** checker rozróżnia BŁĄD (brak wpisu/zerwany twin/brak pola → exit≠0) od ODNOTOWANEGO (known_drift, service-scoped, intentional → exit 0). USE_V2_PARSER nie wywala CI.
- **R6 — consumers best-effort:** dla engine consumer-discovery = grep literalu `"NAME"` + najbliższy `def`/`class`/module-symbol (bez nr linii). Dla panel/apka mam dokładny binding. Odnotowane jako best-effort; twardą kompletność testu/doc pilnują istniejące coverage-checkery (odsyłam).
- **R7 — `flags.snapshot.json` (test harness):** to izolowany flags.json; seed rejestru robię z ŻYWEGO `/root/.openclaw/workspace/scripts/flags.json` (stan produkcyjny 2026-07-10), nie ze snapshotu testowego. `--live` porównuje z żywym; CI z fixturą tmp.
- **ODŁOŻONE (poza tym zadaniem, ZA ACK):** kuracja owner/review/removal (Adrian); migracja świata 1b i plan-recheck-env → flags.json (hot-reload cross-service, rekomendacja L0.1); realny retirement flag `dead`; naprawa geocode env-frozen-const jeśli wykryta; poprawka `flag_registry.scan_unit_env` multi-pair.

**Zero odstępstw od zakazów:** zero zmian wartości flag, zero systemctl (poza czytaniem plików w /etc — read-only), zero `systemctl show -p Environment`, zero sekretów w rejestrze/raportach (filtry §4a), zero zapisu poza worktree, brak push, kanon repo tylko odczyt.

## 9. ŚRODOWISKO / DoD (Faza 2, po „GO")
- `PKG=/root/sprint4_wt/pkgroot_flags`; `mkdir -p $PKG`; `ln -sfn /root/sprint4_wt/wt-flags/dispatch_v2 $PKG/dispatch_v2`; `ln -sfn /root/sprint4_wt/flags.snapshot.json $PKG/flags.json`; test: `cd /root/sprint4_wt/wt-flags/dispatch_v2 && ZIOMEK_SCRIPTS_ROOT=$PKG /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q -rf -p no:cacheprovider`. Cel: **0 failed, +6 nowych passed**.
- Seed na HOŚCIE (read-only źródeł): `python tools/flag_lifecycle_seed.py` → `flag_lifecycle_registry.json`; ręczne adnotacje; `--live` host-run do raportu (dowód 0 dryfów + USE_V2_PARSER known).
- py_compile obu narzędzi; checker repo-hermetic exit 0; commity jawne ścieżki (`git add <pliki>`, NIGDY `-A`), stopka `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`, wszystko zacommitowane przed końcem, BEZ push. Rollback = `git revert` (rejestr to dane, zero wpływu na runtime).

**Czekam na „GO" lidera przed jakąkolwiek edycją.**
