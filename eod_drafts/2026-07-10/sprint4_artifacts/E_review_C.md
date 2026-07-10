# E — Review BRANCH C (sprint4/z-p1-07-flags, commit 814e2c6)

**WERDYKT: APPROVE** — rejestr 504 flag maszynowo wygenerowany, w PEŁNI reprodukowalny z seedera (fresh seed == committed byte-identycznie), zero sekretów, checker zielony hermetycznie i live, testy łapią regresję, zero regresji w suicie. Brak P0/P1. Trzy drobiazgi.

## Zakres (a) — OK
- `git diff --numstat c2bde58..HEAD`: 7 plików, KAŻDY `+X/-0` (additywny). Zero modyfikacji istniejących, zero dotknięcia nośników flag (common.py/flags.json/config.py/drop-iny/DEFAULT_FLAGS NIE w diffie). Zero plików chronionych. Write-set == C_plan §1.
- Worktree git status CLEAN po moich runach; committed registry NIEZMIENIONY (moje seedy szły do scratchpad --out).

## registry.json — SKAN SEKRETÓW + spot-check (b) — OK
- **Secret scan (wszystkie wartości stringowe):** 36 trafień mojego CELOWO szerokiego regexu = 100% false-positive (ścieżki consumerów + nazwy flag z „KEY", np. `ENABLE_BEST_EFFORT_OBJM_R6_KEY`). **ZERO wartości token/hasło/DSN/URL.**
- Filtr seedera `TOKEN|SECRET|PASS|KEY|DSN|CRED|COOKIE|AUTH`+http POPRAWNIE odrzucił genuine sekrety apki (`COURIER_ADMIN_PASS`/`ETA_API_TOKEN`/`PANEL_INTERNAL_TOKEN` z courier_api/config.py) — `_meta.secret_lines_rejected apka=1`. Potwierdzone: te 4 nazwy = **0 wystąpień** w rejestrze.
- **Spot-check 10 wpisów vs żywe źródła:** 5 engine (`current_snapshot['flags.json']` == żywy flags.json: 5/5 OK), 3 panel (default vs DEFAULT_FLAGS: OPS02_DISPATCH/ANALYTICS_OPERATOR True==True; SOONEST_UNDER_LOAD poprawnie sot=flags.systemd.env→default None), 2 apka (consumer→realny symbol config.py). Wszystkie spójne.

## Determinizm seedera (c) — OK, wzorcowy
- Seeder ×2 do scratchpad → `diff` IDENTYCZNY (sort_keys=True, SEED_DATE stała nie runtime-timestamp).
- **Fresh seed == committed registry BYTE-IDENTYCZNIE** (504==504, 0 różnic w JAKIMKOLWIEK polu). Rejestr w 100% reprodukowalny, zero dryfu od commita, zero ręcznego majstrowania. Twins/known_drift/geocode-notes są code-seedowane (odtwarzalne), nie post-hoc.

## Test CI hermetyczny (d) — OK
- Czyta WYŁĄCZNIE: committed registry, worktree common.py (source-parse `SD._tuple_names`), tmp fixtury. `_run` wymusza `--panel-dir/--courier-dir/--panelsync-dir/--systemd-dir NONEXIST` → cross-repo SKIP. Zero /etc, dispatch_state, journalctl, żywego flags.json.
- **Czysty CI bez /etc i bez nadajesz_clone → SKIP nie FAIL** (`check_cross_repo` zwraca skips; coverage z in-repo common.py). Potwierdzone lekturą + zielona suita.
- 13 testów (plan mówił +6; finalnie +13 = szersze pokrycie korupcji — deklaracja +13 poprawna). 6 z nich MUTUJE rejestr (usuń wpis ETAP4 / zerwij twin / usuń pole / sierota flags.json / dryf / exit-code) i asertuje że checker łapie → dowód nietrywialności (C13 behawioralne).

## Niezależna suita (e) — OK
- Pełna suita w wt-flags (pkgroot_flags): **4723 passed / 24 skipped / 10 xfailed / 0 failed** (121s). == deklaracja C == baseline 4710 + 13. Zero regresji.

## Twins dwustronne (f) — OK
- Skan symetrii nad committed registry: **0 błędów** (każdy twin_of odwrotny). Para różno-nazwa `TRUST_CANON_ORDER`(panel)↔`ENABLE_BUILD_VIEW_TRUST_CANON_ORDER`(apka) obecna i symetryczna. twins_concepts=5.

## Deklaracje C — zweryfikowane
- Rejestr 504 (engine 391/panel 86/apka 27; 1b=12 unitów): `_meta` + seeder + moja analiza = zgodne. Lifecycle live378/shadow44/planned82.
- checker `--repo-hermetic --skip-external` exit **0**; `--live` (host read-only) exit **0**, 0 dryfów.
- corruption → exit 1: 6 testów potwierdza.
- **geocode 3× dual-carrier**: zweryfikowane vs REALNY `geocoding.py` (l.480/719/746/784) — wszystkie `C.flag("NAME", C.NAME)`: flags.json hot-reload WYGRYWA, stała modułu = default. NIE antywzorzec #9. Zgodne z notes rejestru i raportem.
- known_drift=1 `USE_V2_PARSER` (z `flag_registry.KNOWN_DIVERGENCES`, env=1 tylko panel-watcher) — obecny, w `--live` jako known (nie-błąd).

## Bezpieczeństwo/read-only — OK
- Checker i seeder read-only (seeder pisze tylko --out; checker nic). --live czyta /etc + żywy flags.json (odczyt). Zero systemctl, zero `systemctl show -p Environment` (używa file-parse + opcjonalnie journalctl wzorem flag_fingerprint_check).

## Znaleziska
- **P0: brak. P1: brak.**
- **P2 / drobiazgi (nie blokują):**
  1. Apka `consumers[]` mają zniekształcony prefiks względny `../../.openclaw/workspace/scripts/courier_api/config.py` (z dispatch_v2 poprawnie byłoby `../courier_api/config.py`; jak jest → rozwija się do zdublowanego `.openclaw`). Czysto opisowe metadane, żaden checker od tego nie zależy — kosmetyka. Niespójne z engine consumerami (`dispatch_v2/...`).
  2. Świadome zawężenie: ~230 numeryczno/stringowych stałych env SILNIKA (nie-toggle, spoza flags.json/NUMERIC) wyłączone jako KONFIG (nie flaga lifecycle). Transparentnie w raporcie §Odstępstwa. NIE defekt — decyzja zakresu; rejestr = flagi + flags.json-numeric, nie każdy env-const. Warto by Adrian wiedział, traktując rejestr jako inwentarz.
  3. (Info) known-limitation `flag_registry.scan_unit_env` (multi-para `Environment=A=1 B=1`) uczciwie odnotowana; seeder ma własny `_parse_systemd_env` zamiast tykać istniejący skaner — poprawne (nośniki nietykane).
