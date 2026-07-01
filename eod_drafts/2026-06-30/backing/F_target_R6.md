# FAZA F — STAN DOCELOWY rodziny **R6 „Cykl-życia / zgnilizna"** (klasy H · K)

> **⚠️ DRAFT — produkt syntezy audytu READ-ONLY (sesja tmux 2).** Zero kodu, zero flipów, zero restartów, zero `--notify`, zero git. Ten dokument definiuje **KANONICZNY STAN DOCELOWY + PLAN KONSOLIDACJI** dla rootów rodziny R6. Każda zmiana kodu = OSOBNY mini-sprint protokołem ETAP 0→7 + ACK Adriana. **Numery linii zweryfikowane ŚWIEŻYM grep DZIŚ — HEAD silnik `8024705` (2026-06-30) — DRYFUJĄ (≥3 żywe sesje/repo), re-grepuj przed dotknięciem jako pewnik.**

**Data:** 2026-06-30 · **Tryb:** READ-ONLY · **HEAD:** `8024705` (working tree `.py` czysty)
**Wejście:** `E_dedup_3_semantics_lifecycle.md` (podklaster R6 H·K: R6a courier-plans-lifecycle / R6b unbounded-caches / R6c-1 dead-decision-code / R6c-2 repo-clutter) + `E_dedup_2_truth_conflict.md` (R10 stale-txt, R12 dead-producer — §F jawnie deleguje „czysty K-deadcode + dead-producer-shadow" do lifecycle-agenta) + lane-B kod (`B10_H_lifecycle`, `B12_K_deadcode_core`, `B13_K_deadcode_periphery`) + werdykt R3 (`F_target_R3.md` — deleguje stale-txt/dead-producer do MNIE; trzyma H-manifestacje w SWOICH E-rootach bug4/verdict-reader) + świeże greppy/dane DZIŚ.
**Kontrakty referencyjne (DESIGN §4):** ①JEDNO-źródło/regułę(kopie=1) ②kontrakt-warstw(HARD-przed-SOFT+inwarianty) ③parytet-bliźniaków(divergence=0) ④prawda-flag(dead=0,rejestr) ⑤prawda-przyrządów(void=0 PRZED flipem) ⑥brak-dryfu-semantyki(display≠decision) ⑦kompletność-cyklu-życia(0-bez-GC) ⑧koherencja(0-konfliktów).

---

## 0. ZAKRES — które rooty należą do R6 (+ granice anty-double-count)

Rodzina **R6 = „Cykl-życia / zgnilizna"** — naruszenie kompletności CYKLU ŻYCIA artefaktu (create→mutate→**GC**) albo ZGNILIZNA (kod/plik/log/flaga ŻYJE w drzewie, ale NIE jest wykonywany / jest nieświeży / udaje żywy). Dwie klasy:
- **H (luka cyklu życia / janitorial):** stan rośnie bez GC (zombie, append-only-bez-eviction), read-with-side-effect, recanon nie-prune, werdykt-snapshot bez TTL → czytający dostaje nieaktualny stan.
- **K (martwy / szczątkowy / wycofany-nieusunięty):** gałąź nieosiągalna na żywej konfiguracji, symbol bez konsumenta, flaga-na-zawsze-OFF z kodem, reguła zneutralizowana stałą, kłamiący komentarz „ON" gdy OFF, dead-producer shadow-log, `.bak`-graveyard, retired-fork.

**Szkoda R6 jest ODROCZONA/POZNAWCZA (nie błędne wyjście live — z definicji martwy kod nie biega):** (a) **mina C2** — flip flagi-skeletonu uzbraja 2-mies. nietestowany kod w gorącej ścieżce; (b) **myli root-cause** — zombie-reguła/skeleton wygląda na żywy → sesja „naprawia" martwą gałąź; (c) **kłamiący dowód** — stale `.txt` / dead-producer log czytany jako „bieżący werdykt" → decyzja na nieaktualnym/fikcyjnym sygnale; (d) **zatruwa twin-graf + grep audytu** — DEAD 5. kopia route-order / 326 `.bak` / orphan drop-in. Jedyny ŻYWY objaw peryferyjny = `dispatch-cod-weekly.service` FAILED.

**2 przetrwałe rooty fam=R6 (survivor-lista po dedup+adwersaryjna weryfikacja) + 4 podstawy-rodziny H/K (E_dedup_3, sub-survivor — pełnią TĘ SAMĄ klasę, ten sam kontrakt §4.7/§4.4 je obejmuje):**

| # | Root | Sev | Klasy | Werdykt | source | survivor? | Objaw „cyklu/zgnilizny" (1 zdanie) |
|---|---|---|---|---|---|---|---|
| **R6-H-A** | `stale-txt-verdict-no-ttl` (E_dedup_2 R10) | P2 | H,L | CONFIRMED | TAK | **TAK** | ≥6 werdykt-`.txt` = zamrożone snapshoty BEZ TTL/„stale"-markera → sesja czyta nieaktualny stan jako bieżący. |
| **R6-K-A** | `dead-producer-orphan-consumer-shadow-logs` (E_dedup_2 R12) | P2 | E,K,M | CONFIRMED | TAK | **TAK** | shadow-log z MARTWYM producentem (`c5`) / OSIEROCONYM konsumentem udaje żywy dowód; test pisze do PROD-ścieżki. |
| **R6-H-B** | `courier-plans-lifecycle` (E_dedup_3 R6a / unified K2) | P2 | H,K,D,O | CONFIRMED | TAK | sub (most R2-no-deconflict) | `gc_invalidated` orphan → **43/47 (91,5%) zombie**; `load_plan` read-side-effect default-True; recanon nie-prune (phantom). |
| **R6-H-C** | `unbounded-append-only-caches` (E_dedup_3 R6b) | P3 | H,N | CONFIRMED | TAK | sub | 6 cache/telemetry stores ZERO eviction (geocode 3,2MB / dwell 2,3MB / pins 1,4MB / town / ground-truth-facts / events.db VACUUM?). |
| **R6-K-B** | `dead-decision-code-misleads-and-arms-mines` (E_dedup_3 R6c-1) | P2 | K,L | CONFIRMED | TAK | sub (most R2/R3) | C3-migracja niezaktywowana (kwarg+gałąź+producent+stała); R7=99km zombie; 3 skeletony C4/C6/C7; kłamiące komentarze „ON". |
| **R6-K-C** | `repo-clutter-retired-not-removed` (E_dedup_3 R6c-2) | P3 | K | CONFIRMED | TAK | sub (most R1) | 326+ `.bak` (polityka 24h MARTWA), shift-notify potrójny grób + orphan drop-in dir, panelsync DEAD fork, epaka misplaced. |

> **Dlaczego 2 survivory reprezentują 6-elementową rodzinę:** dedup+adwersaryjna pasja wyniosła R10+R12 jako P2-OPEN-SOURCE „survivory" (najczystsze, jednoznacznie-otwarte manifestacje klasy), a R6a/R6b/R6c-1/R6c-2 to **ta sama klasa H/K** o niższej/sprzężonej survival-pozycji (R6a most do R2-no-global-deconflict przez K2; R6c-1/c-2 most do R1/R2/R3 K-manifestacji). **Kontrakt §4.7 + §4.4 obejmuje WSZYSTKIE 6 jednym mechanizmem** — pomijając R6a-c2 zostawiłbym 91,5% zombie i 99% test-pollution poza target-stanem rodziny, której są one rdzeniem. Raportuję je więc PEŁNIEJ, z jawnym survivor-statusem i cross-refami (poniżej), NIE jako nowe rooty.

**Wiodący kontrakt §4 dla CAŁEJ rodziny = §4.7 „kompletność-cyklu-życia (0-bez-GC)"** — każdy trwały artefakt ma podpięty pełny cykl create→mutate→GC, a każdy martwy artefakt jest usunięty albo zarejestrowany z etykietą. Kontrakty wspierające: **§4.4** (prawda-flag — dead=0, K-deadcode/dead-but-ON), **§4.5** (prawda-przyrządów — dead-producer/stale-log NIE udaje dowodu), **§4.6** (brak-dryfu-semantyki — stale `.txt` „udaje bieżący", kłamiący komentarz „ON"≠OFF), **§4.1** (jedno-źródło — jedna migracja/jeden loader, NIE N-kopii), **§4.3** (parytet-bliźniaków — DEAD member route-order zatruwa twin-graf).

**Granice (NIE liczę podwójnie — cross-ref do innych rodzin/agentów):**
- **`panelsync DEAD fork` (courier_orders.py 665L, R6-K-C) = DEAD member rootu R1 `one-route-order-module`.** F_target_R1 prowadzi konsolidację route-order (PoC „jeden moduł kolejności trasy"). **R6 NIE re-derywuje route-order — przejmuje aspekt cyklu-życia: USUNĄĆ martwy fork PRZY R1-PoC, nie równać jako 5. żywą kopię** (J-pułapka: sesja „naprawiająca wszystkie kopie razem" zmarnuje pracę na martwej).
- **`R7 long-haul 99km zombie` (feasibility_v2:486, R6-K-B) = dowód „brak HARD-geometrii" w R2 `geometry-blind-selection`.** R2 owns geometrię selekcji (P0-A). **R6 owns USUNIĘCIE/REAKTYWACJĘ martwej gałęzi reject** (K-cleanup) — ale DECYZJA „usunąć vs reaktywować jako soft-geom" jest SPRZĘŻONA z R2 (jeśli R2 dostaje HARD-geometrię, R7-soft może być jej częścią). Ruszać po rozstrzygnięciu R2.
- **`stale .txt` bug4 (R3-E3/F5) + `result.txt`/`atrun.log` verdict-reader (R3-E5) = H-manifestacje w E-rootach R3.** R3 owns INSTRUMENT-TRUTH (inwariant bug4, rotation-aware reader). **R6 owns WSPÓLNĄ KONWENCJĘ stale-marker/TTL** (`.txt` z timestamp+kadencja) — mechanizm współdzielony, R3 jawnie deleguje go do mnie (F_target_R3:38,138,177). Fix JEDEN (konwencja TTL), konsumowany przez werdykty R3 i R6.
- **`post-shift-replay validated-vs-void` (E_dedup_2 R13, fam R7) = owner R7** (adwersaryjna: VALIDATED na świeżym ledgerze 454×/2000, VOID-claims STALE). **R6 cross-ref:** to META-finding (nieświeża analiza), nie martwy-kod — NIE czyścić jako K (B13 §7 ostrzega: „dormant-instrument ma rolę → Faza C/E, nie cleanup K").
- **`_append_jsonl silent-swallow` (E_dedup_2 R9, fam R5/M)** — wspólny `_append_jsonl` połyka wyjątek → agent M. **R6 most:** dead-producer/stale-log dziedziczą ten swallow (carried_first_guard `:118`, checkpoint_tz `:144` — „mogą milczeć null zamiast krzyczeć"), ale rdzeń-fix = R5.
- **`dead-but-ON flagi` (PANEL_IS_FREE_AUTHORITATIVE / TRANSPARENCY_SCORING, 0 konsumentów) = flag-drift R3-D2/R14.** R3/R1-D owns rejestr-flag. **R6 cross-ref:** te flagi to K-manifestacja (martwa-ON) — usunięcie idzie z migracją rejestru R1-D, R6 dostarcza INV „dead-flag=0" jako test-akceptacji.
- **`carried_first_guard empty-env` (R3-D1)** — guard reużywa funkcje plan_recheck z pustym env. Owner = R1-D 0.3 / R3-D1 (truth-manifest). **R6 cross-ref:** ten guard JEST przyrządem cyklu-życia carried-first; jego naprawa (env-parytet) to R1-D, R6 nie dubluje.
- **STOP na dyspozytorni** — Mailek/Papu poza zakresem.

---

## 1. CROSS-CUTTING — KONTRAKT CYKLU ŻYCIA + ZGNILIZNY (produkt §4.7 + §4.4 + §4.5 + §4.6)

Stan docelowy R6 zaczyna się od JEDNEGO żywego artefaktu = **REJESTR CYKLU ŻYCIA** (analogiczny do REJESTRU PRAWDY w R3 / MACIERZY-warstw w R2): każdy trwały artefakt (stan / shadow-log / werdykt-`.txt` / flaga-gałąź / unit) ma jawny **status-cyklu** i **kto go GC-uje**. Dziś rejestr nie istnieje jako kontrakt — `B10` ma master-tabelę (kontrast zdrowych/chorych), ale bez egzekwowanej kolumny „GC-wired / TTL / producer-alive".

### REJESTR CYKLU ŻYCIA (szkielet kontraktu — zweryfikowany świeżo DZIŚ)

| Artefakt | Klucz / wzrost | GC / eviction / TTL DZIŚ | Status | Kontrakt |
|---|---|---|---|---|
| **STANY TRWAŁE (H)** | | | | |
| `courier_plans.json` (39KB) | per-cid, churn-unbounded | `gc_invalidated:501` **ORPHAN (0 prod-callerów)** | 🔴 **43/47 zombie** (oldest cid 414 @ 28.04, >2 mies.) | §4.7 |
| `orders_state.json` | per-oid | `prune_terminal_orders:933` (12h) ← timer 03:30 ON | 🟢 ZDROWY (wired) | — |
| `pending_proposals.json` | per-oid, TTL 30min | `sweep_expired` na każdym upsert | 🟢 ZDROWY (⚠ klasa O nie-H) | — |
| `courier_last_pos.json` | per-cid | prune-on-write 6h `:187` | 🟢 ZDROWY | — |
| `panel_packs_cache.json` | snapshot overwrite | overwrite-in-place | 🟢 ZDROWY | — |
| `events.db` (31MB) | append events+audit | `event_bus_cleanup` 48h/90d/7d ← timer 04:00 | 🟡 wired ale **VACUUM?** (31MB → DELETE bez shrink) | §4.7 |
| `geocode_cache.json` (3,2MB) | per-adres | **BRAK cap/LRU/age** | 🟡 H append-only | §4.7 |
| `customer_dwell.json` (2,3MB) | per-klient | **BRAK** | 🟡 H append-only | §4.7 |
| `address_pin_index.json` (1,4MB) | per-pin | **BRAK** | 🟡 H append-only | §4.7 |
| `delivery_town_cache.json` (66KB) | per-adres | **BRAK** | 🟡 H append-only | §4.7 |
| `courier_ground_truth.json` (98KB) | per-oid fakty-GPS | `ground_truth_gc:41` TYLKO status-only; **fakty KEPT FOREVER** | 🟡 H fakty-unbounded | §4.7 |
| **SHADOW-LOGI (E/K/M)** | | | | |
| `c5_shadow_log.jsonl` (724KB) | C5_SHADOW_DIFF | producent `wave_scoring._emit_c5:310` ← `compute_wave_adjustment:388` **0 prod-callerów** | 🔴 **DEAD producent → 100% test-bleed** (mtime DZIŚ = test pisze prod-path) | §4.5 |
| `c2_shadow_log.jsonl` (11MB) | hot-path, 20280 wiernych rek. | producent żywy (feasibility) | 🟡 orphan-konsument (TYLKO `analyze_shadow_logs.py`) | §4.5(dok) |
| `a2_selection_shadow.jsonl` | hot-path | producent żywy (dispatch_pipeline) | 🟡 orphan-konsument (TYLKO `weekly_a2_digest.py`) | §4.5(dok) |
| **WERDYKT-`.txt` (H/L)** | | | | |
| `address_mismatch_review_verdict.txt` | snapshot | mtime 30.06 **07:00** (deklaruje 7598m, żywy log 14036m) | 🔴 STALE bez markera | §4.7/§4.6 |
| `drive_speed_overshoot_verdict.txt` | snapshot | mtime **29.06** 07:14 | 🔴 STALE bez markera | §4.7 |
| `objm_lexr6_peak_verdict_*.txt` | snapshot/dzień | mtime 26/28/29.06 | 🔴 STALE bez markera | §4.7 |
| `reassign_global_select_review_verdict.txt` | snapshot | mtime 29.06 18:30 | 🔴 STALE bez markera | §4.7 |
| `bug4_reseq_verdict.txt` | snapshot | mtime 30.06 20:22 (DZIŚ-świeży, ale **brak markera** by to wiedzieć) | 🟡 świeży-ale-nieoznaczony | §4.6 |
| **MARTWY KOD W ŚCIEŻCE DECYZJI (K)** | | | | |
| C3-migracja `DEPRECATE_LEGACY_HARD_GATES` | kwarg+gałąź+producent+stała | `common.py:912=False` (na zawsze), 0 live-callerów | 🔴 DEAD + metryka „kłamie 0" | §4.4 |
| R7 `LONG_HAUL_DISTANCE_KM=99.0` | HARD-reject branch | reject nigdy (>99km nieosiągalne) | 🔴 DEAD zombie (myli „HARD-geom żyje") | §4.4 |
| skeletony C4/C6/C7 | `ENABLE_{MID_TRIP_PICKUP,PENDING_QUEUE_VIEW,SPEED_TIER_LOADING_PLANNED}=False` literał | 0 live-importu | 🔴 DEAD skeleton (C2-mina na flipie) | §4.4 |
| komentarz O2 `route_simulator_v2:139` „ON" | — | effective OFF (`ENABLE_O2_READY_ANCHOR_SWEEP=False`) | 🔴 KŁAMIE (near-miss flip 02.07) | §4.6 |
| **PERYFERIA (K/M)** | | | | |
| `.bak` graveyard | — | polityka 24h MARTWA | 🔴 **176 top-level + 37 tools** (+courier_api 41 + panel 72) | §4.1(grep) |
| `shift_notifications/` | — | retired 15.06 | 🔴 potrójny grób (moduł 886L + `.retired×2` + **orphan `.service.d` dir**) | §4.7 |
| `dispatch-cod-weekly.service` | — | — | 🔴 FAILED (gspread env) — żywy objaw M | (M) |

**Cel rejestru: kolumna „Status" = 🟢 dla KAŻDEGO artefaktu — albo wired-GC/TTL/producer-alive, albo jawnie {RETIRED-do-usunięcia | LATENT-z-planem-flipu+data | bounded-by-design}.** „Zdrowych 5" (orders_state, last_pos, pending_proposals, panel_packs, events.db) = DOWÓD-WZORZEC że to osiągalne — target = „bądź jak te 5".

### INWARIANTY CYKLU/ZGNILIZNY (docelowa suite — czerwone-na-start, zielone-po-konsolidacji)

- **INV-LIFE-1 (GC/eviction wired, H/§4.7):** każdy trwały stan z rosnącym kluczem ma podpięty GC/eviction/prune (timer/janitor/on-write) ALBO jest jawnie „bounded-by-design". *Test:* `courier_plans` GC podpięty ⇒ `zombie >TTL = 0` (dziś 43); każdy append-only-cache ma age/LRU/cap; `events.db` VACUUM. Dowód-wzorzec = 5 zdrowych stores.
- **INV-LIFE-2 (dead-code-in-decision-path = 0, K/§4.4):** żadna gałąź/symbol/flaga/stała w MODULE DECYZJI nie jest „dead-but-present" bez etykiety {RETIRED-do-usunięcia | LATENT-z-planem-flipu+data}. *Test:* skeleton-flaga literał-False ⇒ albo usunięta albo oznaczona „flip = full deploy nietestowanego skeletonu"; `dead-but-ON` flaga (0 konsumentów) = CZERWONY; C3-migracja (4 site bajt-identyczne przy OFF) usunięta.
- **INV-LIFE-3 (świeżość/TTL artefaktu konsumowanego, H/§4.6+§4.7):** każdy artefakt-werdykt (`.txt`/`.jsonl`-snapshot) czytany przez człowieka/sesję ma `timestamp + kadencja + „stale gdy mtime>kadencja"` marker. *Test:* czytający dostaje „świeży | jawnie-stary", NIGDY „stary-udający-bieżący"; `ls` na `dispatch_state/` rozróżnia żywy↔zamrożony.
- **INV-LIFE-4 (żywy producent + udokumentowany konsument + zero test-bleed, E/M/§4.5):** każdy shadow-log ma (a) żywego producenta w PROD-ścieżce ALBO etykietę „DEAD-producer → archiwum/usunięty"; (b) konsumenta udokumentowanego w rejestrze; (c) test NIGDY nie pisze do PROD-ścieżki (path→tmp_path zawsze, nie tylko w 1 teście). *Test:* `c5` producent-dead ⇒ log usunięty/oznaczony; `test_wave_scoring` KAŻDY caller `compute_wave_adjustment` redirectuje `C5_SHADOW_LOG_PATH`→tmp; orphan-konsument (`c2`→analyze, `a2`→digest) udokumentowany.
- **INV-LIFE-5 (komentarz/docstring = efektywny stan, K/L/§4.6):** komentarz/docstring o stanie flagi/producencie zgodny z EFEKTYWNYM (nie „ON" gdy OFF; nie „nightly producer" gdy producent to inny tool). *Test:* komentarz-o-fladze ⇒ `== effective_flag_state` (lub generowany ze stanu); O2 `:139` i speed_tier `:904` zgodne.
- **INV-LIFE-6 (martwy artefakt ≠ żywa kopia w twin-grafie, K/§4.3):** martwy fork/duplikat NIE jest liczony jako żywy member rodziny-bliźniaków. *Test:* `panelsync/courier_orders.py` oznaczony DEAD-member route-order (R1) — usuwany, nie równany; `V325_DROPOFF` dead-twin usunięty.

Mapowanie inwariant→klasa: **H** → 1,3 · **K** → 2,5,6 · **E/M** → 4.

---

## 2. STAN DOCELOWY PER ROOT (twardy kontrakt + inwariant runtime)

### ▰ KLASA H (luka cyklu życia / janitorial / nieświeżość)

### R6-H-A — `stale-txt-verdict-no-ttl` (P2, CONFIRMED, źródło, OTWARTY, SURVIVOR) — most R3

**CO DZIŚ (entropia, świeżo zweryfikowane — `ls dispatch_state/*.txt` DZIŚ):**
- ≥6 werdykt-`.txt` = ZAMROŻONE point-in-time snapshoty BEZ `timestamp+TTL+„stale"`-markera. Sesja/cron czytająca je `cat`/ślepym `ls` dostaje NIEAKTUALNY werdykt jako „bieżący". Potwierdzone STALE DZIŚ: `address_mismatch_review_verdict.txt` (mtime **30.06 07:00** — deklaruje „max 7598m" gdy żywy log 14036m), `drive_speed_overshoot_verdict.txt` (29.06 07:14), `objm_lexr6_peak_verdict_*.txt` (26/28/29.06), `reassign_global_select_review_verdict.txt` (29.06 18:30). `bug4_reseq_verdict.txt` (30.06 20:22) jest DZIŚ-świeży — ale BEZ markera czytający NIE WIE czy to dziś-rano czy teraz.
- **Mechanizm:** point-in-time snapshot nadpisywany/nieodświeżany; brak konwencji „stale gdy mtime>kadencja". Sprzężone z `_append_jsonl`-swallow (R5/R9): jak emit padnie, plik milczy z poprzednią treścią.
- **Most R3:** `bug4_reseq_verdict.txt` (R3-E3 F5) i `result.txt`/`atrun.log` (R3-E5) to TE SAME klasy — R3 jawnie deleguje konwencję TTL do MNIE (F_target_R3:38,138,177). Fix JEDEN, konsumowany przez werdykty R3+R6.

**STAN DOCELOWY (kontrakt §4.7 + §4.6 + §4.1):**
1. **§4.7/§4.6** JEDNA konwencja `.txt`-werdyktu: każdy plik zaczyna nagłówkiem `# verdict @ <ISO-ts> | kadencja=<N>h | STALE gdy mtime > kadencja` (lub równoważny sidecar `.meta`). Czytający (człowiek/cron/sesja) dostaje „świeży | jawnie-stary", nie pusty-udający-bieżący. `stale-txt-no-TTL = 0`.
2. **§4.1** Lepszy wariant docelowy (jeśli tani): werdykt emitowany do durable `*.jsonl` z `ts` polem — `ls`-świeżość przestaje być sygnałem prawdy (rejestr czyta ostatni rekord po `ts`, nie mtime pliku). Wspólny helper-emitter, NIE per-tool `open(.txt,'w')`.
3. **Sprzężenie:** ta sama konwencja-marker spełnia INV-TRUTH-4 (R3-E5) — jeden mechanizm, dwa rooty zielone.

**INWARIANT RUNTIME:** każdy `.txt`/snapshot-werdykt ma `mtime + kadencja` marker; czytający porównuje `now − mtime` z kadencją i raportuje „stale" zamiast cytować nieświeżą liczbę (INV-LIFE-3). *Test:* `ls dispatch_state/*verdict*.txt` → 0 plików bez markera; reader address_mismatch zwraca „STALE (mtime 07:00, kadencja 1h)" zamiast „7598m".

**BRAMKA „ZERO NOWYCH KOPII":** 1 konwencja-marker / 1 wspólny emitter importowany przez N werdyktów (−N własnych `open(.txt)`), NIE N-ty snapshot bez markera. Każdy nowy werdykt powiela konwencję, nie pułapkę. **Niski-ryzyko (read/emit-tooling, nie silnik) — kandydat na wcześniejszą fazę.**

---

### R6-H-B — `courier-plans-lifecycle` (P2, CONFIRMED, źródło, OTWARTY, sub-survivor) — = unified K2, most R2

**CO DZIŚ (entropia, świeżo zweryfikowane — `courier_plans.json` + grep DZIŚ):**
- **H1 GC orphan:** `plan_manager.gc_invalidated:501` („Manual/cron hook — no auto-schedule") ma **ZERO prod-callerów** (grep DZIŚ: tylko `tests/test_v319c_sub_a.py` + `test_a1_silent_killers` + self-ref `:519/524`; brak timera/crona). Jedyny `del plans[cid]` = wewnątrz tej martwej funkcji. **Dane live DZIŚ: 47 cid, 43 invalidated (zombie), 4 active = 91,5% śmieci** (oldest cid 414 @ 2026-04-28 >2 mies., cid 284 @ 03.05, cid 523 @ 14.05). 100% reason = `ORDER_DELIVERED_ALL`. Docstring `:214` „GC-able" = obietnica niespełniona (+ dead-function = K). `load_plan` skip-invalidated → DECYZJI nie psuje, ale plik 91,5% śmieci, churn-unbounded.
- **H2 read-side-effect:** `load_plan:121` default `invalidate_on_mismatch=True` PERSYSTUJE `invalidate_plan(ORDER_DELIVERED_ALL)` podczas READ. Preview-reader (worek KANDYDATA) ściga się z `advance_plan` (TOCTOU) → spurious DRZE plan → oscylacja carried-first (Jakub W/Piotr K 29.06). Mitygowane `ENABLE_LOAD_PLAN_PURE_READ=True` (flags.json, zweryfikowane DZIŚ) przy 2 callerach, **default param wciąż True** = opt-out: NOWY caller z `active_bag_oids` bez flagi LUB reset flags.json reaktywuje „read drze plan".
- **H3 recanon nie-prune (K2):** `plan_recheck:1832` subset-gate `set(oids)<=covered` przepuszcza SKURCZONY worek (delivered stop wciąż w `covered`); `_retime_stops` retimuje WSZYSTKIE bez reconcile statusu → phantom dropoff przeczasowany ALBO retime-abort — żaden wariant NIE prune. Prune wyłącznie surgical-event (advance/mark_picked_up/remove). Upstream: `panel_watcher MAX_RECONCILE_PER_CYCLE=25` throttle < backlog → lag 15-90min karmi phantom.
- **H1b dead-end:** plan invalidated + nowe zlecenia + `_start_anchor=None` → `_gen_one_bag_plan` pomija → „tkwi invalidated"; mitygowane drop-in `ENABLE_GPS_FREE_ANCHOR_LAST_POS=1`, kod default OFF.
- **O7 multi-timer:** plan-recheck (5min) + panel-watcher (event) piszą `save_plan` (LOCK_EX zero-korupcji) ale `expected_version=None` → last-writer-wins; RÓŻNY env efektywny → mogą policzyć INNY kanon (most R3-D1/D2 env-parytet).
- **Survivor-status:** NIE na survivor-liście jako standalone — H3-aspekt (recanon=cofacz) zwija się do unified-audit **K2** i most do R2 `no-global-deconflict-new-order` (oba dotyczą plan_recheck nie-domykania). R6 owns CYKL-ŻYCIA pliku (GC+read-side-effect+prune); R2 owns deconflict-decyzję.

**STAN DOCELOWY (kontrakt §4.7 + §4.1 + §4.3):**
1. **§4.7** Podpiąć `gc_invalidated` do janitora (`run_recheck` koniec-ticku LUB `prune_orders_state.timer`) — zombie >TTL spada do 0; ALBO świadomie USUNĄĆ martwą funkcję + udokumentować „plik invalidated zostaje, czyszczony przy save_plan(cid)". Decyzja {wire | remove} — nie „GC napisany-niepodpięty".
2. **§4.7 (fix-u-źródła, NIE per-caller)** Odwrócić `load_plan` default na **pure-read** (`invalidate_on_mismatch=False` default; side-effect = opt-IN świadomy). Usuwa opt-out-minę: nowy caller domyślnie nie drze planu. Flaga `ENABLE_LOAD_PLAN_PURE_READ` po utwardzeniu defaultu → retire.
3. **§4.7 (K2)** Recanon/retime z **reconcile-statusu** (prune delivered/cancelled niezależnie od surgical-event, nie tylko subset-gate); throttle reconcile adaptacyjny (nie stały 25). **Bliźniacze ścieżki RAZEM** (feasibility↔greedy↔plan_recheck, 4 handlery recanon — per protokół MAPA KOMPLETNOŚCI).
4. **§4.1/§4.3** `expected_version` CAS między 2 timerami + parytet env (most R3-D1/D2 — wspólny rejestr-flag); usunąć H1b dead-end u źródła (utwardzić anchor-fallback, nie drop-in).

**INWARIANT RUNTIME:** `courier_plans` zombie >TTL = 0 (GC-wired); `load_plan` bez `active_bag_oids` LUB bez opt-IN NIGDY nie mutuje pliku (pure-read default); recanon prune phantom-stop gdy worek skurczył (INV-LIFE-1). *Test:* po fix `zombie_count(courier_plans.json) == 0` na żywych danych; `load_plan(preview)` nie zmienia mtime.

**BRAMKA „ZERO NOWYCH KOPII":** podpiąć ISTNIEJĄCY `gc_invalidated` (nie pisać 2. GC) + odwrócić 1 default param (nie dodać flagi) + prune w istniejącym recanon (nie 2. shadow). **DOTYKA SILNIKA — pełny protokół ETAP 0→7 + ACK + off-peak + replay ON↔OFF + parytet bliźniaków + pełna regresja.** 2/3 dziś mitygowane flagami → priorytet = utwardzić defaulty (źródło), nie dodać kolejną łatkę.

---

### R6-H-C — `unbounded-append-only-caches` (P3, CONFIRMED, źródło, OTWARTY, sub-survivor)

**CO DZIŚ (entropia, świeżo zweryfikowane — `ls dispatch_state/` DZIŚ):**
- 6 append-only cache/telemetry stores z ZERO eviction (vs 5 zdrowych z wired-GC): `geocode_cache.json` (3,2MB), `customer_dwell.json` (2,3MB), `address_pin_index.json` (1,4MB), `delivery_town_cache.json` (66KB), `courier_ground_truth.json` (98KB, `ground_truth_gc:41` prune TYLKO status-only → fakty-GPS KEPT FOREVER), `events.db` (31MB managed, ale DELETE bez VACUUM → free-list rośnie). Grep `prune|cap|MAX|LRU|older|evict|trim` = PUSTO dla cache-4.
- Wolny wzrost, niskie ryzyko korupcji, ale prawdziwie nieograniczony. `geocode`/`dwell` w HOT-PATH → rozmiar = koszt I/O (RMW każdego zapisu rośnie).

**STAN DOCELOWY (kontrakt §4.7):**
1. **§4.7** Age-bound / LRU / cap per store (jeden wspólny eviction-wrapper, nie 4 ad-hoc); `events.db` VACUUM cykliczny (timer). `ground_truth` fakty-GPS = age-bound (kalibracja nie potrzebuje >N dni). Wszystkie 6 → wired jak „zdrowa 5".

**INWARIANT RUNTIME:** każdy cache w `dispatch_state/` ma cap/age (rozmiar bounded) LUB etykietę „bounded-by-key-domain" (INV-LIFE-1). *Test:* rejestr cyklu-życia → 0 stores „BRAK eviction".

**BRAMKA „ZERO NOWYCH KOPII":** 1 wspólny eviction-wrapper dla N stores (−N ad-hoc), NIE per-cache własna logika. **Niskie ryzyko (nie ścieżka-decyzji), ale janitor-timer = ops-zmiana → ACK lekki.** P3 — po H-B.

---

### ▰ KLASA K (martwy / szczątkowy / wycofany-nieusunięty kod)

### R6-K-A — `dead-producer-orphan-consumer-shadow-logs` (P2, CONFIRMED, źródło, OTWARTY, SURVIVOR)

**CO DZIŚ (entropia, świeżo zweryfikowane — grep producent/konsument + peek DZIŚ):**
- **DEAD producent (c5):** jedyny writer `c5_shadow_log.jsonl` = `wave_scoring._emit_c5_shadow_diff:310`, osiągalny WYŁĄCZNIE przez `compute_wave_adjustment:388`. Docstring modułu SAM POTWIERDZA śmierć (`wave_scoring.py:7` „NIE jest wywoływany przez ŻADEN…"); grep DZIŚ: **0 prod-callerów** (tylko `tests/test_wave_scoring.py`), `ENABLE_WAVE_SCORING=False`. → **100% zawartości c5 = test-bleed.** Dowód świeży: c5 mtime DZIŚ 13:17 + ostatni rekord (`event_type=C5_SHADOW_DIFF`, klucze breakdown/context/flag_wave_scoring, ZERO test-markera) = wygląda jak prod-rekord, ale POWSTAĆ MÓGŁ TYLKO z testu (brak prod-producenta). Test `:341` redirectuje `C5_SHADOW_LOG_PATH→tmp_path` TYLKO w `test_compute_wave_adjustment_shadow_log_emits`; POZOSTAŁE testy (`flag_on_sums_features` `:253`, `all_features_combined` `:307`) wołają `compute_wave_adjustment` BEZ redirectu → gdy magnituda>próg, piszą do PROD-ścieżki. Świeży mtime = test-run DZIŚ wyciekł do prod-stanu. **Świeży-mtime MYLI** „żywy dowód".
- **Orphan-konsument (c2, a2):** `c2_shadow_log.jsonl` (11MB, 20280 wiernych hot-path rekordów) czytany w prod-grep WYŁĄCZNIE przez `tools/analyze_shadow_logs.py` (offline). `a2_selection_shadow.jsonl` czytany tylko przez `tools/weekly_a2_digest.py`. Nie martwe (producent żywy), ale single-reader nieudokumentowany → ryzyko „nikt nie wie po co ten 11MB".

**STAN DOCELOWY (kontrakt §4.5 + §4.4):**
1. **§4.5/§4.4** Producent DEAD (`compute_wave_adjustment` / c5) → log USUNIĘTY albo oznaczony „DEAD-producer, reaktywacja wymaga wpięcia w pipeline+flip" (docstring już to mówi — domknąć: usunąć stale `c5_shadow_log.jsonl` z `dispatch_state/` LUB sidecar „producer-dead"). `void-shadow-log = 0` (żaden nie udaje żywego dowodu).
2. **§4.5 (test-bleed)** `test_wave_scoring` — KAŻDY caller `compute_wave_adjustment` redirectuje `C5_SHADOW_LOG_PATH`→`tmp_path` (nie tylko `shadow_log_emits`); fixture autouse / monkeypatch path. Test NIGDY nie pisze do `/root/.openclaw/workspace/dispatch_state/`. (Most do conftest-isolation R3-D2/R7 — state-bleed, nie tylko flag-bleed.)
3. **§4.5(dok)** Orphan-konsument (`c2`→analyze, `a2`→digest) udokumentowany w rejestrze cyklu-życia (kto czyta, kadencja) — nie martwy, ale jawny.

**INWARIANT RUNTIME:** każdy shadow-log w `dispatch_state/` ma żywego prod-producenta ALBO etykietę „DEAD"; żaden test nie zapisuje do prod-ścieżki shadow-logu (INV-LIFE-4). *Test:* po fix `c5_shadow_log.jsonl` nie rośnie po `pytest tests/test_wave_scoring.py` (path→tmp); rejestr → 0 „dead-producer bez etykiety".

**BRAMKA „ZERO NOWYCH KOPII":** usunąć/oznaczyć 1 dead-log + naprawić test-path (edycja TEST-only, nie silnik) + udokumentować 2 orphan-konsumentów, NIE dodać 4. shadow-log. **Niskie ryzyko (test+state-hygiene, nie ścieżka-decyzji) — kandydat na wcześniejszą fazę.**

---

### R6-K-B — `dead-decision-code-misleads-and-arms-mines` (P2, CONFIRMED, źródło, OTWARTY, sub-survivor) — most R2/R3

**CO DZIŚ (entropia, świeżo zweryfikowane — grep stałych/flag DZIŚ):**
- **C3-migracja „deprecate-legacy-hard-gates" niezaktywowana (= JEDEN root w 4 site, NIE 4 chaosy):** `r6_soft_penalty_c3_legacy` (`scoring.py:200/228` martwy kwarg+gałąź + `feasibility_v2.py:1129` producent `-3/min` którego nikt nie czyta + `DEPRECATE_LEGACY_HARD_GATES=False` zweryfikowane DZIŚ `common.py:912`, na zawsze + metryka `r6_soft_penalty_applied` ZAWSZE 0 = **„kłamie 0"**). Podwójnie martwa (const-False ∧ 0-callerów).
- **R7 long-haul zombie:** `feasibility_v2:486` HARD-reject gated `LONG_HAUL_DISTANCE_KM=99.0` (zweryfikowane DZIŚ `common.py:800`) → reject NIGDY (Białystok ~15km). „Myli że HARD-geometria długiego przejazdu żyje" — **NIE żyje** → most R2 P0-A „brak HARD-geometrii". TODO-C3-soft (`:471`) nigdy nie zrobione.
- **3 skeletony Sprint-C F2.2 (nigdy nieaktywowane):** `commitment_emitter:82` (`ENABLE_MID_TRIP_PICKUP=False` literał, 0 callerów), `pending_queue_provider` (`ENABLE_PENDING_QUEUE_VIEW=False`, import dead-on-arrival `dispatch_pipeline:3372`), `speed_tier_tracker:904` (`ENABLE_SPEED_TIER_LOADING_PLANNED=False` + stale output Apr-18 + **MYLĄCY komentarz „nightly producer"** → producent to INNY tool `build_speed_tiers.py`). Flagi zweryfikowane literał-False DZIŚ. = **C2-miny** (flip uzbraja 2-mies. nietestowany kod).
- **Kłamiący komentarz (near-miss-generator):** `route_simulator_v2:139` „ENABLE_O2_READY_ANCHOR_SWEEP **ON**" gdy effective OFF → sesja planująca flip O2 02.07 może uwierzyć że żyje (most R3-E2/C9).
- **Retired-nieusunięte superseded:** F1.8e legacy-else (`:5890`, V324A ON maskuje), B3 wait-gradient (`scoring:95` env-frozen OFF, sentinel -1000 żywy), V325_DROPOFF dead-twin (most R3-N1).
- **Survivor-status:** NIE standalone na survivor-liście — `dead-but-ON` flagi zwijają się do flag-drift R3-D2/R14; R7=99 most do R2; kłamiący-komentarz O2 most do R3-E2. R6 owns USUNIĘCIE/ETYKIETOWANIE martwego kodu jako takiego.

**STAN DOCELOWY (kontrakt §4.4 + §4.6 + §4.1):**
1. **§4.4/§4.1** Usunąć C3-migrację jako JEDNĄ rzecz (kwarg+gałąź+producent+stała) — refaktor bez-zmiany-zachowania, **dowód bajt-identyczności** (`DEPRECATE_LEGACY_HARD_GATES` już nigdzie nie steruje żywą decyzją). Metryka `r6_soft_penalty_applied` (zawsze-0) usunięta lub przemianowana (przestaje „kłamać 0"). `dead-code-in-decision-path = 0` dla C3.
2. **§4.4 (sprzężone z R2)** R7=99km: USUNĄĆ martwą gałąź reject LUB reaktywować jako soft-geom — **decyzja sprzężona z R2 geometry-blind** (jeśli R2 dostaje HARD-geometrię, R7 może być jej członem). Nie ruszać w izolacji.
3. **§4.4 (skeletony)** C4/C6/C7: oznaczyć flagę „flip = FULL DEPLOY nietestowanego skeletonu" w rejestrze-sprzężeń (mapa min C2) ALBO usunąć moduł jeśli porzucony (potwierdzić z Adrianem porzucony-vs-zaplanowany — B12 §luka-4). `commitment_level`/`A4_TEST_FLAG` martwe klucze flags.json usunięte (most R1-D rejestr).
4. **§4.6 (kłamiące komentarze)** Naprawić komentarz O2 `:139` („OFF, review 02.07") + speed_tier `:904` (wskaż realnego producenta `build_speed_tiers.py`) — przestają generować near-missy. INV-LIFE-5.

**INWARIANT RUNTIME:** żadna gałąź/stała/flaga w module decyzji nie jest dead-but-present bez etykiety {RETIRED|LATENT+data}; żaden komentarz-o-fladze ≠ efektywny stan (INV-LIFE-2 + INV-LIFE-5). *Test:* grep `DEPRECATE_LEGACY_HARD_GATES` = 0 po usunięciu; komentarz O2 zgodny z `flag("ENABLE_O2_READY_ANCHOR_SWEEP")`.

**BRAMKA „ZERO NOWYCH KOPII":** usunąć/oznaczyć ISTNIEJĄCY martwy kod (−dead-LOC), NIE dodać „TODO-soft" 3. raz. C3 = jedna migracja w 4 site usunięta razem. **C3-usunięcie/komentarze = niskie ryzyko (bajt-identyczne / doc). R7+skeletony = sprzężone z R2/Adrian-ACK → po rozstrzygnięciu.**

---

### R6-K-C — `repo-clutter-retired-not-removed` (P3, CONFIRMED, źródło, OTWARTY, sub-survivor) — most R1

**CO DZIŚ (entropia, świeżo zweryfikowane — `find`/`ls` DZIŚ):**
- **`.bak` graveyard:** **176 top-level dispatch_v2 + 37 tools** (zweryfikowane DZIŚ; + courier_api 41 + panel 72 = 326+ cross-repo), Apr11→Jun30. Polityka „24h retencja" (CLAUDE.md) MARTWA — najstarszy >2,5 mies. Każdy `grep -rn --include=*.py` audytu MUSI filtrować `\.bak`; ryzyko trafienia w stale kopię.
- **shift_notifications RETIRED — potrójny grób:** moduł `worker.py` (886L) + nested in-repo `systemd/` + `/etc` `.retired-2026-06-15 ×2` + **ORPHAN `.service.d` dir bez unitu** (zweryfikowane DZIŚ: `dispatch-shift-notify.service.d/` istnieje, `.service` = `.retired`).
- **panelsync DEAD fork:** `courier_api_panelsync/courier_orders.py` (665L) = DEAD 5. kopia route-order, nie serwowana → **DEAD member R1** (NIE żywa kopia).
- **epaka misplaced:** `tools/epaka_fetcher.py` = projekt EPAKA w dispatch tools/ (cross-project contamination).
- **cod-weekly FAILED:** żywy objaw M (gspread env) — periferia, fix-vs-kill.
- **Survivor-status:** NIE standalone — czysty cleanup-dług. R1 owns panelsync-removal (przy route-order PoC); R6 owns resztę clutteru.

**STAN DOCELOWY (kontrakt §4.7 + §4.1 + §4.3):**
1. **§4.1/§4.7** Czyszczenie po GO: 326+ `.bak` (egzekwowalna retencja 24-48h, np. cron-prune), shift-notify potrójny grób (moduł + nested systemd + orphan dir + `.retired`), 4 drop-in `.bak`, epaka misplaced (przenieść do projektu EPAKA), cod-weekly (fix gspread-env vs kill). `clutter` = 0; grep audytu czysty.
2. **§4.3** panelsync DEAD fork USUNĄĆ **przy R1 route-order PoC** (NIE równać jako 5. kopię) — most R1, R6 dostarcza INV-LIFE-6.
3. **Granica:** dormant-instrumenty (`post_shift_overrun_forward_replay` i 10 innych z at-jobem) **NIE ruszać jako martwe** (B13 §6a/§7 — mają rolę, → Faza C/E).

**INWARIANT RUNTIME:** `find dispatch_v2 -name '*.bak*'` bounded (retencja egzekwowana); 0 orphan `.service.d` dir; 0 cross-project tool w `dispatch_v2/tools/` (INV-LIFE-1 peryferyjny). *Test:* policz `.bak` >48h = 0; `for d in *.service.d; do [ -f ${d%.d} ] || echo ORPHAN` = pusto.

**BRAMKA „ZERO NOWYCH KOPII":** usunięcie plików (−entropia grepu), zero nowego kodu. **P3, czysty cleanup — TYLKO po GO, niskie ryzyko / wysoka redukcja entropii audytu.** Ostatnia faza.

---

## 3. PLAN KONSOLIDACJI (zależnościowo; każdy krok REDUKUJE ≥1 metrykę entropii; bramka „ZERO NOWYCH KOPII")

**Zasada anty-entropii:** konsoliduj-nie-dodawaj; każdy krok ściśle redukuje zombie-count / dead-LOC / stale-txt-count / dead-producer-count / unbounded-cache-count / clutter — NIGDY nie dodaje N-tej kopii/łatki. **Lekcja przewodnia R6 = „GC napisany-ale-niepodpięty / retired-nieusunięty / TTL-brak" = intencja↔rzeczywistość; fix u ŹRÓDŁA (utwardź default, podepnij GC, usuń migrację), NIE łatka per-caller.** Wszystko dotykające kodu-decyzji = OSOBNY ACK + ETAP 0→7, off-peak, replay ON↔OFF, parytet bliźniaków (feasibility↔greedy↔plan_recheck, 4 handlery recanon), pełna regresja `pytest tests/` vs baseline.

> **Kolejność wymuszona naturą R6 + RYZYKIEM:** najpierw FUNDAMENT (rejestr cyklu-życia = czyni entropię MIERZALNĄ), potem NISKIE-RYZYKO non-engine hygiene (TTL-`.txt`, dead-producer-log+test-path, caches-eviction, clutter — nie dotykają ścieżki-decyzji), na końcu WYSOKIE-RYZYKO engine-touching (courier-plans GC+pure-read+prune, dead-decision-code removal — pełny protokół). Sprzężenia (R7/skeletony→R2/Adrian, panelsync→R1, TTL→R3, env-parytet→R1-D) ruszane Z właścicielem, nie w izolacji.

### FAZA 0 — FUNDAMENT (czyni cykl-życia/zgniliznę MIERZALNĄ — read/doc-only, brak ACK)
- **S0.1** Spisz **REJESTR CYKLU ŻYCIA** (§1) jako żywy artefakt: każdy trwały stan / shadow-log / werdykt-`.txt` / flaga-gałąź / unit → kolumna {wired-GC | TTL | producer-alive | RETIRED | LATENT+data | bounded-by-design}. *Czyni `zombie-count / dead-LOC / stale-txt / dead-producer / unbounded-cache / clutter` MIERZALNYMI.* Bramka: 0 artefaktów bez statusu.
- **S0.2** Szkielet suite **INV-LIFE-1..6** (czerwone-na-start). *Czyni regres widocznym.* Read/doc-only.
- **S0.3** Konwencja `.txt`-TTL-marker + wspólny rotation/freshness-loader = **WSPÓLNE z R3 S0.4** (NIE dubluję — R3 buduje rotation-aware master-loader; R6 dokłada `.txt`-stale-marker do tej samej konwencji). Bramka: 1 konwencja.

### FAZA 1 — NISKIE RYZYKO non-engine (NIE ścieżka-decyzji; lekki ACK)
- **S1.1 (R6-H-A)** `.txt`-werdykty → TTL-marker / durable-jsonl-z-ts (wspólny emitter). *Redukuje: stale-txt ≥6→0.* Spełnia też INV-TRUTH-4 (R3-E5). Bramka: NIE N-ty snapshot bez markera.
- **S1.2 (R6-K-A)** Dead-producer `c5_shadow_log` usunięty/oznaczony + `test_wave_scoring` path→tmp dla KAŻDEGO callera (edycja TEST-only) + orphan-konsumenci (c2/a2) udokumentowani. *Redukuje: dead-producer-log 1→0, test-state-bleed.* Bramka: c5 nie rośnie po pytest.
- **S1.3 (R6-H-C)** Append-only caches → wspólny eviction-wrapper (age/LRU/cap) + `events.db` VACUUM-timer. *Redukuje: unbounded-cache 6→0.* Ops-ACK lekki (janitor-timer). Bramka: 1 wrapper, nie 4 ad-hoc.

### FAZA 2 — K-deadcode-removal niskie-ryzyko (bajt-identyczne / doc)
- **S2.1 (R6-K-B część-1)** Usuń C3-migrację (kwarg+gałąź+producent+stała) — **dowód bajt-identyczności** (zero zmiany zachowania); metryka „kłamie-0" usunięta. *Redukuje: dead-LOC + lying-metric.* Bramka: pełna regresja bez-różnicy ledgera.
- **S2.2 (R6-K-B część-2)** Napraw kłamiące komentarze (O2 `:139`, speed_tier `:904`) — doc-only. *Redukuje: lying-comment, near-miss flip-02.07.* Spełnia INV-LIFE-5.
- **S2.3** Oznacz skeletony C4/C6/C7 w mapie-sprzężeń „flip=full-deploy" + usuń martwe klucze flags.json (`commitment_level`/`A4_TEST_FLAG`, most R1-D rejestr). *Redukuje: C2-mina-niewidoczna→jawna.*

### FAZA 3 — WYSOKIE RYZYKO engine-touching (pełny protokół ETAP 0→7 + ACK + off-peak + replay)
- **S3.1 (R6-H-B, najwyższa materialność H — 43 zombie + oscylacja)** Podepnij `gc_invalidated` do janitora LUB usuń (decyzja {wire|remove}); odwróć `load_plan` default na pure-read (źródło, nie per-caller); recanon prune-by-status (K2, **bliźniaki RAZEM**: feasibility↔greedy↔plan_recheck, 4 handlery recanon); `expected_version` CAS + env-parytet (most R1-D/R3-D). *Redukuje: zombie 43→0, oscylacja-source, phantom-stop.* Bramka: replay ON↔OFF dowodzi BEZ-regresji carried-first; zombie=0 na żywych danych.
- **S3.2 (R6-K-B część-3, sprzężone R2)** R7=99km {usuń|reaktywuj-soft} — **PO rozstrzygnięciu R2 geometry-blind** (jeśli R2 dostaje HARD-geom, R7-soft jej członem). Nie w izolacji.

### FAZA 4 — CLUTTER cleanup (P3, TYLKO po GO; zero kodu)
- **S4.1 (R6-K-C)** 326+ `.bak` (egzekwowalna retencja) + shift-notify potrójny grób + orphan drop-in + 4 drop-in `.bak` + epaka misplaced + cod-weekly {fix|kill}. *Redukuje: clutter, grep-entropia.*
- **S4.2 (R6-K-C, most R1)** panelsync DEAD fork USUŃ **przy R1 route-order PoC** (INV-LIFE-6) — nie wcześniej, nie w izolacji. Dormant-instrumenty (11) NIE ruszać.

**Metryki wyjścia (zielone = rodzina R6 domknięta):** `zombie(courier_plans)=0` · `dead-producer-log bez etykiety=0` · `stale-txt bez TTL=0` · `unbounded-cache=0` · `dead-code-in-decision-path (C3/R7/skeleton) usunięty/oznaczony` · `lying-comment=0` · `.bak>48h=0` · `orphan .service.d=0` · suite INV-LIFE-1..6 ZIELONA.

---

## 4. POKRYCIE / LUKI / ADWERSARIALNE (jawne, nie cisza)

**Zweryfikowane świeżym grep/danymi DZIŚ (HEAD `8024705`):** `gc_invalidated` callers (0 prod), `courier_plans.json` live (47/43/4, oldest 414@28.04), `LONG_HAUL_DISTANCE_KM=99.0`, `DEPRECATE_LEGACY_HARD_GATES=False`, `ENABLE_LOAD_PLAN_PURE_READ=true` (flags.json), skeleton-flagi literał-False, c5 producent `compute_wave_adjustment` 0-prod-callerów + docstring-self-confirm + c5 last-record (no test-marker), c2/a2 single-reader, append-only-cache rozmiary, `.bak` 176+37, shift-notify orphan `.service.d` + `.retired×2`, stale `.txt` mtimes.

**LUKI (jawne):**
1. **Tempo wzrostu append-only cache (MB/mies) NIE zmierzone** — jeden snapshot; „unbounded" z kodu (0 eviction), nie z trendu. (B10 §luka-5.)
2. **`events.db` VACUUM — czy DELETE realnie nie zmniejsza pliku** — 31MB sugeruje brak VACUUM, ale nie potwierdzone `PRAGMA`. (B10 §luka-4.)
3. **Skeletony C4/C6/C7 + B3/carry_chain: porzucone-vs-zaplanowane** — etykieta K/D-latent ostrożna; NIE potwierdzone z Adrianem czy świadomie-porzucone (czysty K) czy realnie-pending (D). (B12 §luka-4.) → przed usunięciem skeletonu: PYTAJ.
4. **Pełna lista 64 zero-ref tools NIE rozstrzygnięta 1:1** martwy-vs-dormant — heurystyka (verdict/review→dormant; date-stamped/probe→graveyard); ~5-8 borderline. (B13 §luka-1/2.)
5. **`load_plan` side-effect POZA głównym callsite** — sprawdzony main (2 callerzy z `active_bag_oids`); pełen sweep `_read_*` pod side-effect = osobne przejście. (B10 §luka-6.)

**ADWERSARIALNE (uczciwie — przeciw moim własnym wnioskom):**
- **c5 test-bleed — mechanizm CZĘŚCIOWO mitygowany:** test `:341` redirectuje path→tmp w `shadow_log_emits`. Świeży mtime DZIŚ + last-record-bez-markera DOWODZI że INNY test (bez redirectu) pisze prod-path — ale NIE odpaliłem `pytest` by zidentyfikować dokładny bleeding-test (READ-ONLY). Werdykt „100% test-bleed" = mocny (producent dead-w-prod ⇒ jedyne źródło to test), ale dokładny test do potwierdzenia w ETAP-0 fixu. NIE nadinterpretować jako „prod pisze c5".
- **courier_plans 43 zombie — wpływ na DECYZJE = ZERO** (`load_plan` skip-invalidated). Szkoda = bloat (91,5%) + martwy-GC + churn-unbounded, NIE błędne wyjście live. Sev P2 = janitorial+poznawcze, nie korupcja. Oscylacja (H2) to OSOBNY mechanizm (read-side-effect) niż zombie (H1) — nie konflować.
- **R6-H-B NIE-survivor:** świadomie raportuję pełniej mimo braku na survivor-liście (rdzeń klasy H, 43 zombie = największa materialna luka cyklu-życia). Jeśli decyzja-audytu = „tylko 2 survivory" → H-B/H-C/K-B/K-C degradują do cross-ref, ale §4.7-kontrakt i tak je obejmuje (zostawienie ich poza target-stanem = 91,5% zombie poza kanonem rodziny, której są rdzeniem).
- **Granica K↔E (dormant-instrumenty):** B13 §7 + C18 ostrzegają — `post_shift_overrun_forward_replay` i 10 innych są 0-ref ALE żywe (at-job spent); NIE czyścić jako K. Trzymam je POZA R6-K-C (→ R7/Faza C). Świeży grep ledgera `post_shift_overrun_min` 454×/2000 = OBECNY potwierdza (E_dedup_2 R13).

**NIE-luki (świadomie poza R6):** floor pickup≥shift_start (R4/pre-shift-agent), route-order semantyka (R1), geometry-blind selekcja (R2), instrument-truth bug4/verdict-reader (R3 — TYLKO TTL-konwencja moja), sentinele-produkcja (sentinel-agent/K5), `_append_jsonl`-swallow rdzeń (R5/M), oś-kalibracji (calibration-agent), flag-rejestr-migracja (R1-D — TYLKO INV dead-flag=0 moje), Mailek/Papu (granica).
