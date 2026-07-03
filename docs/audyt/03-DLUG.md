# 03 — DŁUG TECHNICZNY (Agent D) — Faza 0 rekonesans READ-ONLY

**Data:** 2026-07-03 · **Repo:** `/root/.openclaw/workspace/scripts/dispatch_v2` (żywe, tylko odczyt)
**Metoda:** własny skan (find/grep/git ls-files, read-only) + uruchomienie 2 czytających przyrządów (`entropy_dashboard.py`, `flag_registry.py` — oba zweryfikowane jako nie-piszące) + streszczenie skatalogowanego długu z pointerami. Zero zapisu do repo/`dispatch_state`/`/etc`. Sekrety = TYLKO `ścieżka:linia`, wartości zamaskowane.
**Rozmiar repo:** 213 MB (w tym `.git` 100 MB) · 1713 plików w git · 103 `.py` w korzeniu.
**⚠ KOREKTA (znalezisko Agenta A):** `dispatch_v2/dispatch_state/` w git ≠ żywy stan silnika — to TYLKO dane epaki (`dispatch_state/epaka_data/`, 5 plików tracked; zbieg nazw). Prawdziwy stan silnika = `/root/.openclaw/workspace/dispatch_state/` (POZA gitem). Uwzględnione poniżej. Sekcja 2i = klasyfikacja tropów A (bez dublowania jego inwentaryzacji).

---

## 0. MIERNIK ENTROPII — snapshot 2026-07-03 (uruchomiony na żywo)

`tools/entropy_dashboard.py` (read-only potwierdzone: tylko `ast.parse(open().read())` + `subprocess` do `flag_registry.py` z `capture_output`). Pliki żywego silnika: **337**.

| # | Metryka | DZIŚ (03.07) | Cel | Tag |
|---|---|---|---|---|
| 1 | copy-count (reguła >1 źródło) | **17** (≈90 inst.) | 0 | AUDIT-BASELINE |
| 2 | twin-divergence (bliźniaki DIVERGED) | ~13 (route 44-75/d) | 0 | AUDIT-BASELINE |
| 3 | void-instrument (przyrząd kłamie) | **19 VOID + 6 UNTESTED = 25/49** | 0 | AUDIT-BASELINE |
| 4 | dead-flag / rozjazdy flag | **1** | 0 | AUTO (żywe) |
| 5 | layer-violation (HARD w złej warstwie) | 7 | 0 | AUDIT-BASELINE |
| 6 | unresolved-conflict (precedencja) | 13 klastrów (64 par) | 0 | AUDIT-BASELINE |
| 7 | sentinel-as-data (trucizna pozycji) | **12 żywy silnik (+4 instr.)** | 0/1 | AUTO-oracle (żywe) |
| 8 | threshold-sprawl (próg w N miejscach) | 10 rodzin (≈40 sites) | 0 | AUDIT-BASELINE |

**#7 dokładne miejsca trucizny (żywe, policzone teraz):** `chain_eta.py:128,149` · `courier_resolver.py:1122,1592,1601,1655,1695,1707` · `dispatch_pipeline.py:1539,3248,3250,3619`. (6/12 w `courier_resolver` = most K5 no_gps/pre_shift — patrz [[ziomek-unified-audit]]).
Tylko #4 i #7 są liczone na żywo; reszta = baseline z Fazy 1 (re-measure = re-run narzędzia z kolumny „jak" w kodzie dashboardu).

---

## 1. DŁUG JUŻ SKATALOGOWANY (pointery — NIE przepisuję)

| Pozycja | Źródło | Status |
|---|---|---|
| Backlog P0-P3 (audyt 05.06) — cały majowy P0/P1 zamknięty | `memory/tech_debt_backlog.md` | naprawiony (maj) / otwarte: autonomia, geometry A/B/F shadow |
| 88 bare-except / 872 except Exception / 135 silent pass | `tech_debt_backlog.md` „audyt długu 18.06" | żywy (dziś **97 bare / 1743 except Exception** — urosło) |
| `_bucket` 2× + 3 impl. time-bucketu; monolity pipeline/telegram | `tech_debt_backlog.md` pkt 3 + todo_master „DŁUG" | żywy |
| Stan w ~80 plikach JSON bez schematu (geocode_cache 3MB…) | `tech_debt_backlog.md` pkt 6 | żywy |
| `TECH_DEBT.md` (105K) + `docs/TECH_DEBT.md` | korzeń repo | ⚠ STATYCZNY od ~05.2026 (snapshot, adnotacja na górze) |
| 19/49 przyrządów kłamie (VOID), 26 rootów, VOID instruments | `eod_drafts/2026-06-30/FAZA1_00..06` (m.in. `FAZA1_03_rejestr_przyrzadow.md`) | żywy (24 VALIDATED·19 VOID·6 UNTESTED; L1.2 zdjęło część WRONG-SOURCE) |
| SECURITY P0, martwe monitory, 2 bomby TZ, regres perf 2×, 16 sierocych findingów | `eod_drafts/2026-07-02/AUDYT2/MASTER_synteza.md` | żywy (monitory re-enabled za ACK; reszta otwarta) |
| Tracker stanu napraw audytów 1+2 (14 pasów, regresja 4064/0) | `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` | żywy — źródło prawdy stanu |
| Audyty architektoniczne (god objects, fan-in, except) | `AUDIT_2026-05-07/` (10 md), `AUDIT_2026-06-03/` (3 md) | archiwum (historyczne, wartościowe jako ref) |

---

## 2. NOWE ZNALEZISKA (własny skan 03.07)

### 2a. Pliki `.bak-*` — KANDYDAT #1 NA PORZĄDKI
- **338 plików · 17,5 MB · 0 trackowanych w git** (gitignored) · zakres mtime **2026-04-11 → 2026-07-03**.
- Rozkład wg miesiąca: kwi **2**, maj **86**, cze **229**, lip **21**.
- Rozkład wg katalogu (top): korzeń **187**, `tests/` **59**, `tools/` **42**, `daily_accounting/` 13, `cod_weekly/` 8, `reconciliation/` 6, `shift_notifications/` 5, `observability/` 5.
- Rekord pojedynczego pliku: `common.py.bak-*` ma **~24 kopie** (~200KB każda ≈ 4,8 MB samego `common`).
- Workflow deklaruje „retencja 24h" (`CLAUDE.md`), realnie kumulują się od kwietnia. Prawdziwy rollback = tagi git, nie `.bak`.
- 🟠 **2 pliki backup-style OMINĘŁY `.gitignore` i SĄ trackowane** (nazwa nie pasuje do wzorca `*.bak-*`): `geocoding.py.bounded-retry-wip-2026-06-14` (korzeń) + `eod_drafts/2026-06-17/foodage_phase4_result.txt.proven-bak`. To jedyne 2 w szerszym globie (`-wip`/`-bak`/`.orig`/`.old`) → dyscyplina nazewnictwa OK poza nimi. (A liczy 339 = moje 338 wzorca `.bak-` + te 2 minus nakładka; klasa ta sama).

### 2b. Duplikaty / bliźniaki
**Znane z kanonu — status ŻYWY (potwierdzone, nie scalone):**
| Bliźniak | Miejsca (korzeń, non-bak) | Status |
|---|---|---|
| `lex_qual` (3 kopie) | `common.py` · `objm_lexr6.py` · `dispatch_pipeline.py` | ŻYWY (=metryka #1) |
| best_effort ↔ objm_lexr6 | `pipeline_geometry.py` · `common.py` · `dispatch_pipeline.py` · `telegram_approver.py` | ŻYWY |
| recanon handlery | `common.py` · `plan_recheck.py` · `panel_watcher.py` (+3 tools) | ŻYWY (rozproszone) |
| serializer LOCATION A+B | `shadow_dispatcher.py` (`_serialize_candidate` + `_serialize_result`, oba w 1 pliku) | ŻYWY (deny-lista po L1.1) |
| 4 powierzchnie route-order | **cross-repo** (dispatch_v2 + panel `fleet_state.py` + courier_api `build_view`) — w dispatch_v2 tylko harness/golden | golden-test L6.A pilnuje parytetu |

**NOWE — wzorce `*_v2/_old/_copy`:** `feasibility_v2.py` i `route_simulator_v2.py` = **kanoniczne nazwy produkcyjne** (v1 dawno wycofany), NIE duplikaty. Reszta `_v2` to jednorazowe skrypty replay w `eod_drafts/` i warianty testów. **Brak nowych groźnych duplikatów w korzeniu.**
**NOWE — duża kopia-draft:** `eod_drafts/2026-06-17/courier_resolver.n1-earliest-due-draft.py` (**1620 linii** — pełna robocza kopia `courier_resolver`). Sierota w draftach.

### 2c. Martwy kod (skan lekki — Agent B robi pełny graf AST)
Heurystyka: 103 moduły korzenia × 0 referencji importu × nie-entry-point. **Wynik: 0 potwierdzenie martwych w korzeniu.** 6 „sierot importu" okazało się osiągalnych:
| Moduł | Werdykt |
|---|---|
| `state_panel_monitor.py` | ŻYWY — `dispatch-state-panel-monitor.service` (`-m dispatch_v2.state_panel_monitor`) |
| `event_bus_cleanup.py` | ŻYWY — systemd + 1 subprocess-ref |
| `prune_orders_state.py` | ŻYWY — systemd |
| `extract_restaurant_addresses.py` | pół-żywy — 1 subprocess-ref, mtime 04-11 (stary one-off) |
| `build_v319h_courier_tiers.py` | one-off manualny — 1 py-ref + 2 doc-ref |
| `__init__.py` | marker pakietu |
⚠ **Lekcja o heurystyce:** czysty grep-import PRZEGAPIŁ 3 entry-pointy `-m module` (systemd nie odwołuje się ścieżką `.py`). Realny martwy kod jest raczej w `tools/` (VOID instruments z FAZA1_03) i w jednorazowcach — **oddaję głęboki skan Agentowi B.** Kandydaci VOID-tool z pointerem: `min_delivered_at_verdict.py` (rotation-blind, nieprzepięty) i pozostałe 18 VOID z `FAZA1_03`.

### 2d. Pliki-sieroty NIE-kod (archiwum w katalogach kodu)
- **`eod_drafts/` = 48 MB** (największa masa nie-kodu). Ciężkie miesiące: **06-17 15M**, **06-22 9,2M**, **06-29 5,6M**, 06-30 2,1M, 07-02 1,2M.
  - Grube pojedyncze dumpy: `2026-06-17/slim_shadow_index.json` **8M**, `.../deepdive_full_records.json` **4,9M**; `2026-06-22/harmed_soft25.jsonl` **4,6M**, `harmed_guarded_s22.jsonl` 2,5M; `2026-06-29/calibration/decisions_outcomes_loadbucketed.jsonl` **5,4M**. Jednorazowe artefakty replay/kalibracji.
  - **25 katalogów `__pycache__` w `eod_drafts/` (1,0 MB `.pyc`, 0 trackowanych)** — bytecode-śmieci z uruchamiania skryptów draftów.
- `AUDIT_2026-05-07/` (344K, 10 md) + `AUDIT_2026-06-03/` (312K, 3 md) — raporty audytów, historyczne.
- `docs/` (504K) — md z **kwietnia** (fix-plany, `BARTEK_GOLD_STANDARD.md`, audyty lag/bag/city) — archiwum.
- Korzeń: `.aider.chat.history.md` **122K** (historia sesji aider, 06-26) — czysty clutter.

### 2e. TODO/FIXME/XXX/HACK w `.py`
- **Bardzo niski dług markerów: 6 TODO + 3 DEPRECATED, 0 FIXME/0 XXX/0 HACK** (non-bak). Repo praktycznie nie używa TODO-znaczników — dług siedzi w bare-except i monolitach, nie w markerach.

### 2f. Hardkody + SEKRETY
- 🔴 **SEKRET W GIT (HIGH):** `ZIOMEK_MASTER_KB.md` (trackowany) zawiera **4 żywe tokeny botów Telegram** — `:589`, `:590`, `:1257`, `:1258` (@NadajeszBot, @GastroBot; chat ID też). To jedyny trackowany plik dispatch_v2 z wzorcem tokenu. Pokrywa się z AUDYT 2.0 §2.5 **S5**. (Nazwy plików: `git ls-files | grep secret/token/.env` = **0** — leak jest w TREŚCI md, nie w nazwie/`.env`.)
- **Brak hardkodowanych literałów sekretów w `.py`** (grep wzorców token/secret/password/api_key = 0 w trackowanym kodzie).
- **Ścieżki absolutne `/root/.openclaw` w kodzie** (poza common/config), top: `courier_resolver.py` 13×, `new_courier_pairing.py` 8×, `sla_tracker.py` 7×, `shadow_dispatcher.py`/`geocoding.py`/`courier_ranking.py` 5× — działa, ale wiąże repo z jedną maszyną (dług przenośności).
- Progi magiczne (R6=35/40, czasówka=60) rozproszone = metryka #8 threshold-sprawl (pointer, nie re-derive).

### 2g. Flagi-sieroty
`tools/flag_registry.py` (read-only gdy bez `--md`; pisze TYLKO z `--md`, którego dashboard nie podaje). Uruchomiony:
- **438 flag** (decyzyjne 127 · w flags.json 221 · env-frozen gdziekolwiek 12).
- **ROZJAZDY: 1 genuine open** — `USE_V2_PARSER` (env-frozen tylko w panel-watcher, czytany modułowo cross-service; inne serwisy parsują v1). Domknięcie = migracja do hot-reload + ACK.
- **11 akceptowanych service-scoped/intentional** (czasówka caps, LGBM shadow, plan-recheck…). **BRAKI POKRYCIA rejestru = 0** (pełne pokrycie flags.json). Higiena flag jest DOBRA.

### 2h. ZOMBIE-WRITER (`tomtom_poc`) — ZIDENTYFIKOWANY
- **Źródło = ROOT CRONTAB** (`/var/spool/cron/crontabs/root`, „GATE B TomTom PoC", dodane 2026-05-16):
  - `*/10 7-22 * * *` → `measure_realworld.py >> measure_rw.log` (co 10 min 7-22h → `measure_rw.log` rósł, mtime 11:30).
  - `30 3 * * *` → `build_ground_truth.py >> build_gt.log` (nocny, mtime 03:30).
- 🟠 **Pisze do 4 plików TRACKOWANYCH w git** (`build_gt.log`, `measure_rw.log`, `rw_results.jsonl`, `trips_realworld.jsonl` — wszystkie `M` w `git status`) → **drzewo robocze jest wiecznie brudne**; ryzyko `git add -A` wciągającego churn (i potencjalnie sekrety z 2f). To NIE martwy proces — to żywy PoC piszący do repo. **Niczego nie zabito** (zgodnie z zakresem).

---

### 2i. Weryfikacja tropów Agenta A (klasyfikacja dług/nie-dług — NIE dubluję inwentaryzacji A)
| Trop A | Weryfikacja (read-only) | Werdykt |
|---|---|---|
| 2 backup-style tracked | oba potwierdzone w `git ls-files` (patrz 2a) | **DŁUG** — cruft-w-git; usuń + poszerz `.gitignore` (→ D12) |
| `events.db` 0 B tracked (korzeń) | `ls` = 0 bajtów, w git | **DŁUG-verify** — pusty placeholder DB; kod i tak stworzy przy connect (→ D13) |
| `label_encoders.pkl` ×2 (bundle 11,5K / solo 8,8K) | tracked binaria ML | **DŁUG-klasa, ZOSTAW** — nośne dla inferencji twomodel; binaria w git = model-registry-debt, nie kasować |
| ~45 `.jsonl/.log/.csv` tracked w eod_drafts | mój glob: **31** (21 jsonl + 10 log) — w tym tomtom_poc | **DŁUG** — artefakty danych churnują w repo kodu (→ D14) |
| `dispatch_state/epaka_data` tracked | 5 plików, `fetch.log` = `M` (churn) | **DŁUG (lekki)** — jak tomtom: dane w repo; nie stan silnika (korekta) |
| `monitoring/` vs `observability/` | `monitoring/`: 3 żywe .py (`detector_419`=service+import parser_health; `consumer_stuck_alert` 3-ref; `gps_feed_health` 2-ref). `observability/`: 12 .py | **NIE-martwe** — dwa domy jednej troski = **lekki dług organizacyjny** (konsolidacja low-pri), OBA żywe |
| `telegram/` (2 pliki) vs `telegram_approver.py` | `telegram/` = `__init__`+`templates.py`, importowane przez shift_notifications/czasowka/approver (+2 testy); approver 60 ref | **NIE-DŁUG (fałszywy trop)** — `telegram/templates.py` to żywy współdzielony pakiet szablonów, inna odpowiedzialność niż approver; zbieżność nazw przypadkowa |

## 3. REJESTR DŁUGU — propozycje (Faza 0: NIC nie usunięto/nie zmieniono)

| # | Pozycja | Ścieżka | Typ | Dowód | Propozycja | Ryzyko | ZATWIERDZIĆ |
|---|---|---|---|---|---|---|---|
| D1 | 4 tokeny botów w trackowanym md | `ZIOMEK_MASTER_KB.md:589,590,1257,1258` | sekret-w-git | grep (wartości zamask.) | ROTACJA tokenów + usunięcie z pliku (osobny sprint security, AUDYT 2.0 S5) | **wysokie** | ✅ DO ZATWIERDZENIA |
| D2 | Cron pisze do trackowanych plików | `crontab root` + `eod_drafts/2026-05-14/tomtom_poc/*` | zombie-writer / brudne drzewo | `git status` = 4×M; crontab l.29,31 | `.gitignore` na 4 logi PoC LUB przekierować output poza repo; ocenić czy PoC nadal potrzebny | średnie | ✅ (decyzja o losie PoC) |
| D3 | 338 `.bak-*` (17,5 MB) | cały repo (187 korzeń) | clutter/backup | find; 0 w git | USUŃ `.bak` starsze niż ~14 dni (rollback = tagi git); zostaw świeże | niskie | ✅ DO ZATWIERDZENIA |
| D4 | 25× `__pycache__` w draftach (1 MB) | `eod_drafts/**/__pycache__` | bytecode-śmieci | find; 0 w git | USUŃ + dodaj do `.gitignore` | niskie | ✅ DO ZATWIERDZENIA |
| D5 | Duże dumpy replay w eod_drafts (~30 MB) | `eod_drafts/2026-06-{17,22,29}/*.json[l]` | archiwum | du | PRZENIEŚ do archiwum poza repo (odchudza `.git`) | niskie | ✅ (jeśli USUŃ z historii) |
| D6 | `.aider.chat.history.md` 122K | korzeń | clutter | ls | USUŃ (+`.gitignore`) | niskie | ✅ DO ZATWIERDZENIA |
| D7 | Kopia-draft courier_resolver 1620 l. | `eod_drafts/2026-06-17/courier_resolver.n1-earliest-due-draft.py` | sierota-kod | wc -l | PRZENIEŚ/USUŃ (draft po N1) | niskie | ✅ DO ZATWIERDZENIA |
| D8 | 97 bare `except:` + 1743 `except Exception` | cały silnik | ciche łykanie błędów | grep | ZOSTAW jako backlog — przegląd bare-except → log+sentinel (polityka fail-loud #32) | średnie | — (backlog) |
| D9 | Monolity | `dispatch_pipeline.py` **7247 l.**, `telegram_approver.py` 4348, `common.py` 3985 (231KB), `panel_watcher.py` 2720, `plan_recheck.py` 2501 | god-object | wc -l | ZOSTAW — refaktor = osobny sprint pod Przykazaniem #0 | wysokie (dotyka silnika) | — |
| D10 | 19 VOID przyrządów | `FAZA1_03` + `tools/` | kłamiący/martwy tool | dashboard #3 | NAPRAW POMIAR (re-oracle) przy następnym użyciu; nie flipuj na ich liczbie | średnie | — (per-tool) |
| D11 | `TECH_DEBT.md`/`ZIOMEK_MASTER_KB.md` statyczne | korzeń | stale-docs | mtime 05-18/06-14 | ZOSTAW (mają adnotację STATUS) — ew. mocniejszy banner | niskie | — |
| D12 | 2 backup-style TRACKOWANE | `geocoding.py.bounded-retry-wip-2026-06-14`, `eod_drafts/2026-06-17/foodage_phase4_result.txt.proven-bak` | cruft-w-git | git ls-files | USUŃ z gita + poszerz `.gitignore` (`*-wip-*`/`*-bak`/`.orig`/`.old`) | niskie | ✅ DO ZATWIERDZENIA |
| D13 | `events.db` pusty tracked | `events.db` (0 B, korzeń) | orphan-binarny | ls | `git rm` + `.gitignore` (kod tworzy przy connect — zweryfikować) | niskie | ✅ (po weryfikacji) |
| D14 | 31 artefaktów danych tracked w draftach + epaka | `eod_drafts/**/*.{jsonl,log}` (31), `dispatch_state/epaka_data/*` (5) | dane-w-repo / churn | git ls-files + `M` | `.gitignore` na rozszerzenia danych w `eod_drafts/` i `epaka_data/` (spina D2 tomtom) | niskie/średnie | ✅ (decyzja o historii) |
| — | `label_encoders.pkl` ×2 | `ml_data_prep/models_twomodel/{bundle,solo}/` | binarium-ML w git | git ls-files | ZOSTAW (nośne dla inferencji) — dług klasy „model registry", nie kasować | wysokie (usunięcie łamie inferencję) | — |

---

## ⚠ DO WYJAŚNIENIA
1. **PoC TomTom (D2)** — czy „GATE B" nadal ma sens (dane od 05-16), czy cron do wygaszenia? Decyzja przed jakimkolwiek `.gitignore`/przenoszeniem — to żywy pomiar, nie zabijam.
2. **Odchudzanie `.git` (100 MB)** — D5/D14 dają efekt na `.git` tylko przy usunięciu z HISTORII (filter-repo), co przepisuje SHA → **wymaga świadomej decyzji Adriana** (współdzielone repo, inne sesje). Samo `git rm --cached` + `.gitignore` (D12/D13/D14) czyści drzewo robocze i zatrzymuje churn, ale NIE zmniejszy `.git` — i to wystarczy dla higieny (brudne drzewo, ryzyko `git add -A`).
3. **`events.db` (D13)** — potwierdzić, że żaden serwis nie zależy od trackowanej wersji (0 B = placeholder; connect stworzy plik) przed `git rm`.
4. **Tokeny D1** — rotacja rusza żywe boty (@NadajeszBot/@GastroBot) → koordynacja z sesją security (AUDYT 2.0 §2.5), poza Fazą 0.
5. **Martwy kod** — mój skan korzenia = 0 potwierdzeń (heurystyka ślepa na `-m`/subprocess). Realna lista martwych = graf AST Agenta B + 19 VOID `tools/`. Nie dubluję.
6. **`.bak` retencja (D3)** — potwierdzić z Adrianem próg (14 dni?) — świeże `.bak` bywają hot-rollbackiem w trakcie sprintu.
