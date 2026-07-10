# SPRINT 4 — KONTRAKTY SYSTEMOWE I BEZPIECZNE CI — HANDOFF (2026-07-10)

**Zakres:** Z-P1-05 (kanoniczna tożsamość kuriera, Faza A) + Z-P1-07 (rejestr i cykl życia flag, Faza A) + Z-P2-07 (hermetyczne testy i fixture).
**Tryb:** lider + 8 subagentów (4 read-only mappery A1-A4, 3 builderzy B/C/D w izolowanych worktree, 1 niezależny reviewer E). Wymóg nadrzędny: zero kolizji z żywymi sesjami 53 (Sprint 3) i 54 (Sprint 2) — **spełniony z konstrukcji: 100% zmian to NOWE pliki.**

---

## 1. BAZA I BRANCHE

| Branch | Baza | Commity | Stan |
|---|---|---|---|
| `sprint4/z-p1-05-identity` | c2bde58 | `e39fb8d` + `70a1a30` | review E: APPROVE |
| `sprint4/z-p1-07-flags` | c2bde58 | `814e2c6` + `c7f4536` | review E: APPROVE |
| `sprint4/z-p2-07-hermetic` | c2bde58 | `3c8175a` + `46421f5` + `6008bee` | review E: APPROVE |
| `sprint4/integration` | c2bde58 | merge B (`9ef7fa7`) → merge C (`8473060`) → merge origin/master 3c43573 (`0b13022`) → merge D (`385c5f7`) → handoff | **KOMPLETNY, zielony** |

Baza `c2bde58` = master z kodem Sprintu 2; `origin/master` w trakcie sprintu przesunął się o 1 commit docs (`3c43573`) — dosunięty do integracji merge'em (bezkonfliktowo). Worktree'y: `/root/sprint4_wt/{integration,wt-identity,wt-flags,wt-hermetic}/dispatch_v2` + pkgrooty per lane (ADR-007/C12e).

## 2. OWNERSHIP / WRITE-SET (agent → pliki; rozłączność 100%)

- **B (Z-P1-05):** `identity/` (8 modułów), `tests/test_identity_{registry,collisions,onboarding}_zp105.py`, `tests/fixtures/identity/` (10 anonimizowanych), raport ZP105. **22 pliki.**
- **C (Z-P1-07):** `tools/flag_lifecycle_{seed,check}.py`, `tools/flag_lifecycle_registry.json`, `tests/test_flag_lifecycle_zp107.py`, `docs/flags/{README,INVENTORY_2026-07-10}.md`, raport ZP107. **7 plików.**
- **D (Z-P2-07):** `conftest.py` (root — jedyny punkt aktywacji), `tests/hermetic_support.py`, `tests/hermetic_quarantine.json`, `tests/test_hermetic_guard_zp207.py`, `tests/fixtures/hermetic/` (4), `docs/HERMETIC_TESTS.md`, raport ZP207. **10 plików.**
- Żaden agent nie edytował pliku innego agenta ani ŻADNEGO pliku istniejącego. Builderzy: plan → GO lidera → implementacja; commit atomowo po jawnych ścieżkach; integrował wyłącznie lider.

## 3. TESTY I WYNIKI (venv dispatch, pkgroot per branch)

| Przebieg | Wynik |
|---|---|
| Baseline c2bde58 (przed czymkolwiek) | 4710 passed / 24 skipped / 10 xfailed / **0 failed** (124 s) |
| Branch B (niezależnie potwierdzone przez E) | 4742 / 24 / 10 / **0** (+32 nowe) |
| Branch C (potwierdzone przez E) | 4723 / 24 / 10 / **0** (+13) |
| Branch D DEFAULT (potwierdzone przez E) | 4717 / 27 / 10 / **0** (+10 kontrolnych; +3 skipy uzasadnione — patrz §6) |
| Branch D STRICT (potwierdzone przez E) | 4668 / 76 / 10 / **0 failed / 0 error** |
| **INTEGRACJA DEFAULT** | **4762 / 27 / 10 / 0** (= 4710+32+13+10−3, co do sztuki) |
| **INTEGRACJA STRICT (bez dispatch_state)** | **4713 / 76 / 10 / 0 failed / 0 error → DoD Z-P2-07 SPEŁNIONE** |

Diff skipów DEFAULT vs baseline: 24 bazowe zachowane bajt-w-bajt; +3 = kwarantanna panel_packs (realne prod-mutatory, §6). Logi: `/root/sprint4_wt/{baseline_c2bde58,integration_default,integration_strict}.log`.

## 4. CO POWSTAŁO (esencja per zadanie)

**Z-P1-05 — `dispatch_v2/identity/` (addytywny, runtime NIEwpięty):** kanoniczny rekord kuriera (CID=klucz jako `str`), wersjonowane aliasy per źródło (panel/grafik/app/accounting), schema+walidacja; normalizacja i DWA resolvery odtworzone 1:1 z legacy (worker ×10/×5 z bare-key-strict, panel_roster ×10/×10; rozbieżność przypięta testem — NIE zunifikowana, to Faza B); walidator kolizji (aliasy→multi-CID, bare-key poison, rozjazdy nazwisk, braki names/tier, PIN); `report.py` (raport braków + `--parity`); `onboarding.py` (dry-run default, `--apply` podwójnie bramkowany env+flagą, komponuje `courier_admin.add_new_courier` — zero reimplementacji zapisu). **Parytet shadow: 177/177 aliasów+nazwisk zgodnych na OBU profilach (0 mismatch), potwierdzony niezależnie przez E. Żywe liczby: 65 CID / 121 aliasów / 54 multi-alias / kolizje twarde 0 / bare-key poison 8 / konflikty nazwisk 3 (cid 370, 376, 504 — 504 NOWO ujawniony) / brak w courier_names 19 / duplikaty PIN 0.**

**Z-P1-07 — rejestr lifecycle flag 3 światów:** `tools/flag_lifecycle_registry.json` — **504 flagi** (silnik 391 [w tym świat 1b: **12 unitów systemd** pinujących flagi, nie tylko rdzeń-5], panel 86, apka 27), pola: worlds/source_of_truth/carriers/owner/lifecycle(seeded)/default/current_snapshot per-service/consumers/rollback/review_date/removal_condition/**twin_of** (5 par cross-world, w tym różno-nazwa TRUST_CANON_ORDER↔ENABLE_BUILD_VIEW_TRUST_CANON_ORDER)/intentional_per_process (11). Seeder deterministyczny (**re-seed == committed BAJT-W-BAJT**, zweryfikowane przez E). Checker: `--repo-hermetic` (CI, bez hosta; exit 0) + `--live` (host, journalctl wzorem fingerprint-check, NIGDY `systemctl show`; exit 0, 0 dryfów). Corruption-testy: checker ŁAPIE usunięty wpis / zerwany twin / brak pola (exit 1). `known_drift=1`: USE_V2_PARSER (odnotowany, NIE naprawiany). Weryfikacja 3× geocode dual-carrier: **poprawny wzorzec** `C.flag("N", C.N)` — flags.json wygrywa, NIE mina #9. Zero sekretów w rejestrze (genuine sekrety apki odfiltrowane; potwierdzone skanem E).

**Z-P2-07 — hermetyzacja suity:** NOWY root `dispatch_v2/conftest.py` (jedyny punkt aktywacji; **rollback = rm 1 pliku**): sandbox `DISPATCH_STATE_DIR`→tmp z fixture, WRITE+DELETE-guard na prymitywach FS (`builtins.open`, `os.open`, `os.replace/rename`, `os.unlink/remove`) blokujący żywe `dispatch_state`/`scripts/logs`/`flags.json` (denylist, whitelist tmp, realpath(parent), fail-open na edge-case'ach — potwierdzone przez E), tryb `HERMETIC_STRICT=1` (read-block dispatch_state = symulacja braku katalogu) + zewnętrzna kwarantanna per-nodeid (`tests/hermetic_quarantine.json`, 25 wpisów z powodami — ZERO edycji cudzych testów). 10 testów kontrolnych behawioralnych (negatyw: prod-writer→RAISE+mtime niezmieniony; pozytyw: tmp działa; unlink-block). Dokumentacja: `docs/HERMETIC_TESTS.md`.

## 5. DOWÓD BRAKU KOLIZJI

- `git diff --name-status origin/master..sprint4/integration` = **39 plików, KAŻDY status `A`** (zero `M`/`D`) — żaden istniejący plik nie został dotknięty, więc przecięcie z write-setami Sprintu 2 (event_bus/retry/FSM/panel_watcher/parcel_lane/courier_api/testy) i Sprintu 3 (ETA/SLA/OSRM/tracing: dispatch_pipeline, shadow_dispatcher, osrm_client, ledger_io, eta_truth_map, decision_outcomes, courier_ground_truth, tests/conftest.py…) = **puste z konstrukcji**.
- ETAP 0 wykonany przed jakąkolwiek edycją: recon tmux WSZYSTKICH sesji, write-set S2 potwierdzony diffem, write-set S3 wywnioskowany z transkryptu (raport A1); wolność planowanych ścieżek zweryfikowana w kanonie i 15 worktree'ach.
- Świadome uniki: zamiast appendu do `tests/conftest.py` (styk z S3) — nowy root conftest; zero dotknięcia `courier_ground_truth.py`/`courier_resolver.py`; kwarantanna testów wyłącznie zewnętrzna; nic nie stage'owano w kanonie (cudze niezacommitowane pliki nietknięte).
- Żywy stan/flagi/serwisy: **zero zapisów, zero restartów, zero flipów** (flags.json mtime sprzed sprintu; brak śladów testowych w dispatch_state; dowody bracketowane w raportach D i E).

## 6. ZNALEZISKO PRODUKCYJNE (bonus guarda) — do wiadomości ownera panel_watcher

`tests/test_panel_packs_signal_v328.py` przy KAŻDYM pełnym biegu suity **mutował produkcyjny** `panel_packs_cache.json` (`_write_packs_cache`: `mkstemp(dir=żywy dispatch_state)` + `os.replace` na żywy plik; `test_cache_missing`: `os.unlink` żywego pliku). `PANEL_PACKS_CACHE_PATH` = hardcode ignorujący `DISPATCH_STATE_DIR`. Skutek łagodzony tym, że panel_watcher odtwarza cache co tick — ale to klasa „testy piszą do PROD" (Załącznik C). **Mitygacja Sprintu 4:** 3 testy w kwarantannie (default+strict, z powodami), guard blokuje całą klasę na przyszłość. **Właściwy fix = u ownera pliku (obszar Sprint 2/panel_watcher):** test ma pisać do tmp → wtedy wychodzi z kwarantanny. NIE naprawialiśmy cudzego pliku.

## 7. RYZYKA / ZNANE LUKI (uczciwie)

1. **Script-runnery subprocess** nie dziedziczą in-process guarda (mają env sandbox + stripped flags + guardy per-writer: state_machine raise, setup_logger; pomiar A4: 0 literal-write). Pełne domknięcie = sitecustomize/import-hook — osobna faza za ACK.
2. Guard: `os.rmdir` poza spec (0 przypadków w suicie), symlink w liściu ścieżki nierozwiązywany (realpath tylko rodzica), fd-based API przepuszczane (fail-open) — udokumentowane.
3. Rejestr flag: `lifecycle`/`owner`/`review_date`/`removal_condition` = SEED heurystyczny (`lifecycle_seeded:true`) — wymaga kuracji Adriana; ~230 numeryczno-stringowych stałych env silnika świadomie sklasyfikowane jako KONFIG (poza rejestrem flag) — decyzja zakresu do ew. rewizji.
4. `flag_registry.scan_unit_env` gubi 2..n parę multi-pair `Environment=` (seeder ma własny parser; limitacja odnotowana, istniejący skaner NIEtykany).
5. Sesja 52 miała pending „wielki audyt max agentami" — nasze zmiany są w 100% addytywne, ale przy merge do master zweryfikować `git log -3` (C1).

## 8. ODŁOŻONE (Faza B / za ACK — NIE zrobione świadomie)

- **Z-P1-05 Faza B:** wpięcie registry w runtime (courier_resolver/common/telegram/daily_accounting), unifikacja 2 resolverów, podmiana 6 kopii inline `_norm`, backfill/retire `courier_names.json` (19 braków), konsolidacja denormalizowanego courier_api.db, jakikolwiek `--apply` onboardingu. Decyzje Adriana: cid 504 (Kmieć/Kmets), los 19 braków names.
- **Z-P1-07:** kuracja metadanych lifecycle przez Adriana; migracja świata 1b→flags.json (w tym USE_V2_PARSER — behavior-affecting, ACK); retirement martwych (dziś 0); wpięcie checkera `--live` w timer (instalacja unitów = ACK).
- **Z-P2-07:** fix `test_panel_packs_signal_v328` przez ownera (→ wyjście 3 testów z kwarantanny); sitecustomize dla subprocess; ewentualny `addopts -m "not nonhermetic"` w CI; rozszerzenie guarda o rmdir.
- **Merge `sprint4/integration` → master: WYMAGA ACK Adriana** (zgodnie z zakazem sprintu). Po merge: pełna suita z kanonu (nawyk C12e) — zmiany są test/tooling-only, restart NIEpotrzebny (runtime nietknięty).

## 9. ROLLBACK

- Całość przed merge: gałęzie po prostu nie są merge'owane; worktree'y do sprzątnięcia `git worktree remove` (wszystko zacommitowane — zweryfikowano czyste working tree wszystkich lane'ów).
- Po ewentualnym merge do master: Z-P2-07 = `rm dispatch_v2/conftest.py` (1 plik, natychmiast przywraca stare zachowanie suity); Z-P1-05/Z-P1-07 = `git revert` commitów (dane+pakiety nieużywane przez runtime — zero wpływu na silnik).

## 10. DECYZJE WYMAGAJĄCE ACK ADRIANA (zebrane)

1. Merge `sprint4/integration` (39 nowych plików) do master.
2. Kuracja rejestru flag (ownerzy, review-daty, warunki usunięcia) + decyzja o migracji USE_V2_PARSER i świata 1b do flags.json.
3. Zlecenie ownerowi panel_watcher fixu `test_panel_packs_signal_v328` (pisanie do tmp).
4. Faza B tożsamości (wpięcie registry w runtime) — osobny sprint protokołem #0.
5. cid 504 + 19 braków courier_names — decyzje danych.
6. (Opcjonalnie) sitecustomize dla pełnej hermetyzacji subprocess + `-m "not nonhermetic"` w CI.
