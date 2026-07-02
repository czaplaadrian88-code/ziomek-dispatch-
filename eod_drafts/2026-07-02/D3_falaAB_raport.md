# D.3 fala A+B — migracja flag env-frozen → flags.json (KANON) — RAPORT

**Sesja 2026-07-02. Branch `fix/d3-fala-ab` (worktree `wt-d3ab`, bazuje na HEAD 7201ed8 = L3+L4).**
**Właściciel: agent d3-ab. Deploy: koordynator (tmux 9). ZERO deployu wykonane w tej sesji.**
Spec zatwierdzony: `eod_drafts/2026-07-02/D3_RECON_migracja_env_frozen_flags.md`.

---

## 1. STAN ZASTANY (recon zweryfikowany na żywym systemie)

### 1a. Ostateczna lista FALA A (15 flag) — z grepa `plan_recheck.py`
Wszystkie były `os.environ.get("ENABLE_X","0")=="1"` (env-frozen module-const), LIVE ON przez drop-iny systemd:

| # | Flaga | linia (przed) | read-site |
|---|---|---|---|
| 1 | `ENABLE_GPS_FREE_ANCHOR` | 347 | `_start_anchor` |
| 2 | `ENABLE_GPS_FREE_ANCHOR_LAST_POS` | 354 | `_start_anchor` |
| 3 | `ENABLE_PLAN_REAL_PICKED_UP_AT` | 359 | `_gen/_retime` |
| 4 | `ENABLE_PLAN_SEQUENCE_LOCK` | 363 | **`_gap_fill_plans` (2132) — JEDYNY read** |
| 5 | `ENABLE_PLAN_CANON_ORDER_INVARIANTS` | 368 | `_apply_canon_order_invariants` |
| 6 | `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` | 377 | `_apply_canon_order_invariants` |
| 7 | `ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE` | 394 | `redecide_courier` (1963) |
| 8 | `ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP` | 401 | `redecide_courier` (1961) |
| 9 | `ENABLE_RECANON_ON_WRITE` | 412 | `recanon_courier` (2022) |
| 10 | `ENABLE_CARRIED_FIRST_RELAX` | 425 | `_apply_canon_order_invariants` / `_relax_carried_first` |
| 11 | `ENABLE_CARRIED_AGE_TZ_FIX` | 444 | `_relax_carried_first` |
| 12 | `ENABLE_LEX_COMMITTED_WINDOW_SHADOW` | 457 | `_lex_committed_window_reorder` |
| 13 | `ENABLE_LEX_COMMITTED_WINDOW` | 458 | `_apply_canon_order_invariants` / lex reorder |
| 14 | `ENABLE_RELAX_COLOC_PICKUP` | 475 | `_relax_carried_first` |
| 15 | `ENABLE_NONCARRIED_DROPOFF_REORDER` | 488 | `_apply_canon_order_invariants` / `_reorder_noncarried_min_drive` |

**WYŁĄCZONE (Fala C, ZOSTAJĄ env-frozen — osobny pod-ACK):**
`ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION` (l.389/393) i `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH` (l.82/86). NIE ruszone — nadal `os.environ.get(...)` (potwierdzone grepem po zmianie: 2 wystąpienia zostały).

### 1b. FALA B (2 flagi w `common.py`) — para atomowa
`ENABLE_V326_OR_TOOLS_TSP` (l.2441) + `ENABLE_V326_SAME_RESTAURANT_GROUPING` (l.3244). Oba `_os.environ.get(...,"1")=="1"`, default "1", żaden drop-in ich nie nadpisuje → jednolicie ON. Konsument `route_simulator_v2:299` (getattr atrybutu modułu) + `:438` (function-local import). **route_simulator NIE zmieniany.**

### 1c. ⚠ WERDYKT REACHABILITY `ENABLE_PLAN_SEQUENCE_LOCK` w panel-watcherze — **NIEOSIĄGALNE → migruj**
- **Asymetria env potwierdzona** (`systemctl show ... -p Environment` / drop-iny): SEQUENCE_LOCK jest w env `dispatch-plan-recheck` (=1) i `dispatch-carried-first-guard` (=1), ale **BRAK w `dispatch-panel-watcher`** → w pw czytał się dziś `False` (env-default "0").
- **Dowód call-graphem (AST/grep):** jedyne wejścia pw do `plan_recheck` = `recanon_courier` (panel_watcher.py:654/698/726/759) i `redecide_courier` (663/760). `recanon_courier`→`_retime_one_bag_plan`; `redecide_courier`→`_gen_one_bag_plan`. **Żadna z tych funkcji ani ich poddrzewa NIE czytają SEQUENCE_LOCK** — jedyny read (l.2132) jest w `_gap_fill_plans`, wołanym WYŁĄCZNIE z `run_recheck` (plan_recheck.py:2380). `run_recheck` uruchamia tylko oneshot `dispatch-plan-recheck` (main l.2413), pw go NIE woła (grep: pw nie odwołuje się do `run_recheck`/`_gap_fill_plans`).
- **Wniosek:** w pw SEQUENCE_LOCK jest NIEOSIĄGALNY → migracja `pw: False→(flags.json/True)` jest **behawioralnie neutralna** (gałąź martwa w pw). Hipoteza „migracja zmienia pw" OBALONA. SEQUENCE_LOCK ZOSTAJE w Fali A.
- **Analogicznie 3 flagi self-gate obecne tylko w pw** (`RECANON_ON_WRITE`, `IMMEDIATE_REDECIDE_ON_OVERRIDE`, `IMMEDIATE_REDECIDE_ON_PICKUP`): `recanon/redecide_courier` wołane WYŁĄCZNIE z pw (grep całego repo). W `dispatch-plan-recheck` i `dispatch-carried-first-guard` te funkcje NIE są wołane → flagi inertne tam → migracja neutralna wszędzie.
- **Pozostałe 11 flag A** są obecne (=1) we WSZYSTKICH 3 envach → `env=1`≡`flags.json/True` → unifikacja neutralna.
- **`dispatch-shadow` NIE importuje `plan_recheck`** (grep pusty) → shadow nietknięty.

**Podsumowanie: migracja wszystkich 15 flag A jest behawioralnie neutralna w KAŻDYM procesie.**

---

## 2. ZMIANY (kod)

### `common.py`
1. **ETAP4_DECISION_FLAGS** — dopisane 15 flag A + 2 flagi B (blok komentarza „D.3 fala A/B"). → wchodzą do `flag_fingerprint` (parytet cross-proces) + conftest-strip (izolacja testów).
2. **Stałe-fallback** (blok „D.3 fala A fallbacki"): 15× `ENABLE_X = True` (intencja STEADY-STATE — utrata klucza flags.json NIE flipuje po cichu = anty-COMMIT_DIVERGENCE). KANON = flags.json (decision_flag: json > stała).
3. **Fala B:** `ENABLE_V326_OR_TOOLS_TSP` i `ENABLE_V326_SAME_RESTAURANT_GROUPING`: `_os.environ.get(...,"1")=="1"` → literał `True` (fallback + źródło odczytu konsumenta; env usunięty).
4. **`check_v326_pair_coherence(or_tools=None, grouping=None)`** — strażnik sprzężenia pary #13 (GROUPING=ON przy OR_TOOLS=OFF → `WARNING V326_PAIR_INCOHERENT`, log-only, zero zmiany zachowania). Wołany raz przy imporcie (startup-sanity) + z testu.

### `plan_recheck.py`
1. Top-level `from dispatch_v2 import common as _CF` (brak cyklu — common nie importuje plan_recheck).
2. 15× `os.environ.get("ENABLE_X",...)` → `_CF.decision_flag("ENABLE_X")` (odczyt na starcie procesu; ZERO odczytu env dla tych 15).
3. `_D3_FALA_A_FLAGS` (krotka 15) + `_refresh_d3_fala_a_flags()` — odświeża moduł-globale z flags.json **tylko gdy klucz obecny w flags.json** (produkcja → hot-reload; brak klucza w conftest-strip/testach → no-op → monkeypatch testów zachowany). Wołany na starcie `run_recheck` / `recanon_courier` / `redecide_courier` → hot-reload flip TAKŻE w długobieżnym panel-watcherze (ZYSK migracji; oneshoty i tak czytają świeżo per proces).

### Testy dostrojone (test-default-flip — patrz §4 „MAPA KOMPLETNOŚCI")
Miękkie optymalizatory kanonu (SEQUENCE_LOCK / LEX / NO_RETURN / NONCARRIED / CARRIED_AGE_TZ) miały env-default `False` w testach; po migracji fallback=True → domyślnie `True`. 4 pliki testów niejawnie polegały na `False` → **pinuję je do pre-migracyjnego test-defaultu (False)**, by izolowały testowaną jednostkę (parytet każdego optymalizatora ma własne testy; produkcja nietknięta):
- `test_gap_fill_partial_coverage.py` — pin `SEQUENCE_LOCK=False` w `_run` (testuje gałąź partial-regen non-sequence-lock).
- `test_carried_first_relax.py::test_flag_off_is_noop` — pin `LEX_COMMITTED_WINDOW/_SHADOW`, `NONCARRIED_DROPOFF_REORDER` = False (izolacja „carried-first bez relaxera").
- `test_canon_order_invariants.py` — autouse fixture pinuje 7 miękkich optymalizatorów off (plik testuje TWARDE niezmienniki F6 w izolacji).
- `test_carried_first_relax_ready_anchor_2026_06_29.py::test_...changes_decision` — pin `CARRIED_AGE_TZ_FIX/RELAX_COLOC/LEX` = False (izolacja efektu READY_ANCHOR).

### NOWY test `tests/test_d3_flag_migration.py` (77 przypadków)
Parametryzowane per 17 flag: (i) brak klucza+brak env→True, (ii) flags.json=false→False, (iii) env=0→True (env martwy), + stała-fallback True + rejestracja ETAP4 + fingerprint. Plus: ON≠OFF na realnej gałęzi (`redecide_courier`), refresh flags.json→moduł-global (hot-reload) z zachowaniem monkeypatch, sprzężenie pary B, MUTATION-CHECK (usunięcie fallbacku → (i) pada), strażnik strukturalny (0 odczytów env dla 15 flag A).

---

## 3. DOWODY (nie deklaracje)

- **py_compile**: common.py + plan_recheck.py + wszystkie testy — OK.
- **Nowy plik testów**: 77 passed.
- **Testy celowane flag-effect + strażnicy** (carried-first / canon-invariants / gap-fill / redecide / recanon / ready-anchor / golden route-order / L3 / etap4-unification / flag-effect-coverage / b4-grouping): zielone.
- **REGRESJA PEŁNA (overlay w układzie kanonicznym, kontrolowana kopia worktree):**
  - CLEAN baseline (moje zmiany schowane): **1 failed / 3826 passed / 23 skipped / 11 xfailed**.
  - Z D.3: **1 failed / 3903 passed / 23 skipped / 11 xfailed**.
  - **JEDYNY failed identyczny w OBU** = `test_flag_effect_coverage::test_no_new_untested_decision_flag` na `ENABLE_COURIER_PLANS_GC` — **flaga L3, NIE D.3, uncovered już w czystym worktree** (pre-existing, poza zakresem — patrz §6). **D.3 = 0 NOWYCH FAILi, +77 passing (mój plik testów).**
- **DEPLOYED-STATE end-to-end** (flags.json z 17 kluczami=true, env=0 = drop-iny usunięte):
  1. wszystkie 17 = True (env=0 zignorowany → **env martwy**), 2. fingerprint zawiera 17× `=1` (**parytet**), 3. rollback flags.json=false → moduł-global flip False po refresh (**hot-reload**), 4. decision_flag zgodny.
- **Parytet bliźniaków**: fingerprint identyczny cross-proces po unifikacji (wszystkie procesy czytają ten sam flags.json + te same stałe common). SEQUENCE_LOCK: dziś env pw=absent(False)/pr=1(True) — po migracji True wszędzie, ale w pw gałąź nieosiągalna → parytet bez zmiany zachowania.

---

## 4. MAPA KOMPLETNOŚCI (klasa: „migrowana flaga + jej test-default")
- Źródło odczytu: 15× plan_recheck + 2× common — WSZYSTKIE zmienione. Grep potwierdza 0 leftover `environ.get` dla 15 flag A; 2 flagi C zostały env-frozen.
- Konsumenci Fala B (route_simulator:299/438): NIE zmienieni (czytają atrybut modułu = źródło migracji).
- Rejestr: ETAP4 (fingerprint + conftest-strip) — 17 dopisanych; stałe-fallback — 15+2.
- Strażnicy testów, które karmią te flagi (carried-first / canon / gap-fill / ready-anchor): pinnięte do test-defaultu False (bliźniacze ścieżki RAZEM).
- `dispatch-carried-first-guard` (read-only): czyta te flagi przez moduł-global; oneshot → import-time decision_flag = świeży per proces (honoruje flags.json).

---

## 5. DEPLOY DLA KOORDYNATORA — DOKŁADNIE

> ⚠ **Kolejność jest bezpieczna dzięki `const=True`**: nawet jeśli klucz w flags.json chwilowo brak, decision_flag → stała True → produkcja zostaje ON (żadnego okna OFF).

### (1) Wpisy `flags.json` (canonical `/root/.openclaw/workspace/scripts/flags.json`) — 17 kluczy = true
```
"ENABLE_GPS_FREE_ANCHOR": true,
"ENABLE_GPS_FREE_ANCHOR_LAST_POS": true,
"ENABLE_PLAN_REAL_PICKED_UP_AT": true,
"ENABLE_PLAN_SEQUENCE_LOCK": true,
"ENABLE_PLAN_CANON_ORDER_INVARIANTS": true,
"ENABLE_NO_RETURN_TO_DEPARTED_PICKUP": true,
"ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE": true,
"ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP": true,
"ENABLE_RECANON_ON_WRITE": true,
"ENABLE_CARRIED_FIRST_RELAX": true,
"ENABLE_CARRIED_AGE_TZ_FIX": true,
"ENABLE_LEX_COMMITTED_WINDOW_SHADOW": true,
"ENABLE_LEX_COMMITTED_WINDOW": true,
"ENABLE_RELAX_COLOC_PICKUP": true,
"ENABLE_NONCARRIED_DROPOFF_REORDER": true,
"ENABLE_V326_OR_TOOLS_TSP": true,
"ENABLE_V326_SAME_RESTAURANT_GROUPING": true
```
Atomowy zapis (temp+rename), backup `flags.json.bak-pre-d3-ab-2026-07-02`. Hot-reload — bez restartu.

### (2) Linie doc do `ZIOMEK_LOGIC_REFERENCE.md` (11 nieudokumentowanych — `test_flag_doc_coverage` wymaga `k in ref` gdy klucz w flags.json). GOTOWY blok do wklejenia (7 pozostałych już udokumentowanych):
```
### D.3 fala A/B — flagi route/kanon (KANON=flags.json od 2026-07-02, migracja z env-frozen)
- `ENABLE_PLAN_REAL_PICKED_UP_AT` — przekazuje realny picked_up_at do symulatora (kara R6 chroni niesione).
- `ENABLE_PLAN_SEQUENCE_LOCK` — sekwencja worka zamrożona, tick tylko re-czasuje (bez re-TSP).
- `ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE` — natychmiastowa re-decyzja sekwencji na override/reassign (pw).
- `ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP` — re-decyzja także po ODEBRANE (zmiana bag_signature).
- `ENABLE_CARRIED_AGE_TZ_FIX` — poprawne parsowanie picked_up_at (naiwny Warsaw), realny wiek carried w relaxie.
- `ENABLE_LEX_COMMITTED_WINDOW` / `ENABLE_LEX_COMMITTED_WINDOW_SHADOW` — constrained-lex okno odbioru (APPLY / SHADOW).
- `ENABLE_RELAX_COLOC_PICKUP` — współlokalny odbiór (start==restauracja) brany od razu, nie po powrocie.
- `ENABLE_NONCARRIED_DROPOFF_REORDER` — min-jazda reorder dropoffów w worku bez niesionych.
- `ENABLE_V326_OR_TOOLS_TSP` / `ENABLE_V326_SAME_RESTAURANT_GROUPING` — para atomowa (OR-Tools TSP + same-restaurant grouping); rozjazd = double-insert super-pickupa (#13, check_v326_pair_coherence).
```

### (3) Usunięcie linii env z drop-inów (`/etc/systemd/system/`) — TYLKO 15 flag A. **COMMITTED_PROPAGATION i LIVE_ETA_REFRESH ZOSTAJĄ.** (Backup każdego pliku `.bak-pre-d3-ab`.)
**`dispatch-plan-recheck.service.d/`:** usuń `Environment=ENABLE_X=1` z: `carried-age-tzfix.conf` (CARRIED_AGE_TZ_FIX) · `carried-first-relax.conf` (CARRIED_FIRST_RELAX) · `gps-free-anchor.conf` (GPS_FREE_ANCHOR) · `gps-free-lastpos-anchor.conf` (GPS_FREE_ANCHOR_LAST_POS) · `lex-committed-window.conf` (LEX_COMMITTED_WINDOW + _SHADOW) · `route-reorder-fix-mk.conf` (NONCARRIED_DROPOFF_REORDER + RELAX_COLOC_PICKUP) · `unified-route-f1-f2.conf` (PLAN_REAL_PICKED_UP_AT + PLAN_SEQUENCE_LOCK + PLAN_CANON_ORDER_INVARIANTS + NO_RETURN_TO_DEPARTED_PICKUP). **ZOSTAW:** `committed-propagation.conf`, `live-eta-refresh.conf`.
**`dispatch-panel-watcher.service.d/`:** `carried-age-tzfix.conf` (CARRIED_AGE_TZ_FIX) · `carried-first-relax.conf` (CARRIED_FIRST_RELAX) · `gps-free-lastpos-anchor.conf` (GPS_FREE_ANCHOR_LAST_POS) · `lex-committed-window.conf` (LEX_COMMITTED_WINDOW + _SHADOW) · `recanon-on-write.conf` (RECANON_ON_WRITE) · `route-reorder-fix-mk.conf` (NONCARRIED_DROPOFF_REORDER + RELAX_COLOC_PICKUP) · `unified-route-f3.conf` (IMMEDIATE_REDECIDE_ON_OVERRIDE + GPS_FREE_ANCHOR + PLAN_REAL_PICKED_UP_AT + PLAN_CANON_ORDER_INVARIANTS + IMMEDIATE_REDECIDE_ON_PICKUP + NO_RETURN_TO_DEPARTED_PICKUP). **ZOSTAW:** `Environment=ENABLE_PANEL_BG_REFRESH=0` (INTENTIONAL_PER_PROCESS) i `Environment=USE_V2_PARSER=1` (Fala D).
**`dispatch-carried-first-guard.service.d/engine-env-parity.conf`:** usuń 15 linii Fala A; **ZOSTAW** `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1` + `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH=1`.

### (4) `daemon-reload` + restart
```
sudo systemctl daemon-reload
sudo systemctl restart dispatch-panel-watcher.service   # Type=simple — ładuje nowy kod plan_recheck (1 raz, off-peak)
# dispatch-plan-recheck / dispatch-carried-first-guard = oneshot timery → next tick same łapią nowy kod
```
NIE dotykaj `dispatch-telegram`. NIE w peaku bez ACK.

### (5) Weryfikacja po deployu
```
# parytet env (po usunięciu drop-inów Fala A):
systemctl show dispatch-panel-watcher.service -p Environment | tr ' ' '\n' | grep ENABLE_PLAN_SEQUENCE_LOCK   # spodziewane: PUSTE (env martwy, KANON=flags.json)
# fingerprint identyczny cross-proces + zawiera 17 flag:
journalctl -u dispatch-panel-watcher -u dispatch-plan-recheck --since "2 min ago" | grep FLAG_FINGERPRINT
grep -c "ENABLE_PLAN_SEQUENCE_LOCK=1" <(journalctl -u dispatch-plan-recheck --since "6 min ago" | grep FLAG_FINGERPRINT | tail -1)
# brak V326_PAIR_INCOHERENT w logach (para spójna):
journalctl -u dispatch-shadow --since "5 min ago" | grep V326_PAIR_INCOHERENT   # spodziewane: puste
```

### (6) Rollback (dwie ścieżki)
- **Miękki (bez restartu):** `flags.json` klucz → `false` (hot-reload; np. `ENABLE_CARRIED_FIRST_RELAX=false`). decision_flag natychmiast False → panel-watcher łapie następnym `recanon/redecide`, oneshoty następnym tickiem.
- **Twardy:** `git revert` commitów D.3 + `daemon-reload` + restart pw. LUB przywróć linie env w drop-inach z `.bak-pre-d3-ab` (ale env jest martwy po deployu kodu — realny rollback = flags.json=false albo revert kodu). Backup flags: `flags.json.bak-pre-d3-ab-2026-07-02`.

---

## 6. RYZYKA
- **NISKIE — migracja neutralna.** Reachability SEQUENCE_LOCK/self-gate udowodniona (martwe w procesach bez env). `const=True` eliminuje okno OFF przy dowolnej kolejności deployu.
- **Test-default-flip** (soft optimizery True-default w testach) — domknięty pinami w 4 plikach (produkcja nietknięta; każdy optymalizator ma osobne testy ON-path).
- **⚠ PRE-EXISTING (NIE D.3, do routingu L3):** `test_flag_effect_coverage::test_no_new_untested_decision_flag` pada na `ENABLE_COURIER_PLANS_GC` — flaga **L3** dodana do ETAP4 bez testu efektu/baseline. **Pada identycznie w czystym worktree (bez D.3)** → nie moja regresja. Fix = dodać efekt-test LUB wpis do `tools/flag_effect_baseline.json` (właściciel L3). Po merge D.3 (mój `test_d3_flag_migration.py` mieni wszystkie 17 flag D.3 → pokryte) zostaje TYLKO ta 1 luka L3.
- **Fala C/D poza zakresem** (COMMITTED_PROPAGATION/LIVE_ETA_REFRESH/USE_V2_PARSER) — nietknięte, wymagają osobnego pod-ACK.
- **Uwaga harness (nie produkcja):** regresję liczono na kopii worktree w układzie kanonicznym (`_SCRIPTS_ROOT`/checker-path przekierowane na overlay) — konieczne, bo `pytest` z worktree importuje KANONICZNY `dispatch_v2` (conftest wstrzykuje `scripts` do sys.path). Po merge do canonical wszystko jest współlokalne → checker widzi mój plik testów natywnie.
