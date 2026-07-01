# FAZA 1 — DELIVERABLE #1: MAPA ANTY-WZORCÓW (15 klas → zdedup. do rootów → werdykt adwersaryjny)

**Audyt spójności Ziomka · sesja tmux 2 · 2026-06-30 · TRYB READ-ONLY** (zero kodu/edycji/restartów/flipów/git). HEAD silnika `8024705`.

> **Dedup PRZED liczeniem — „N findingów ≠ N problemów".** 241 findingów (Faza B) + 49 werdyktów przyrządów (Faza C) + 81 par konfliktowych (Faza D) → **53 distinct rooty** (Faza E dedup, 3 klastry) → **39 zweryfikowanych adwersaryjnie** (2 refuterzy/P0-P1, 1/P2-P3) + **14 P2/P3 odłożonych** (cap 64-thunki, JAWNIE wymienione §5).
> **Werdykt:** **19 CONFIRMED · 7 PLAUSIBLE · 13 REFUTED**. 26 przetrwałych (nie-REFUTED & otwarte).
> ⭐ **Kluczowy wzorzec, który ujawnił adwersaryjny pas:** większość CONFIRMED rootów to **„źródło STRUKTURALNIE realne, ale żywy blast-radius ≈ 0 / latentny DZIŚ"** — dług architektoniczny, który WRACA (klasa łatana ≥4×), nie aktywnie-strzelający bug. To rozróżnienie (dług-moat vs pożar) jest w kolumnie „żywy stan".

---

## 0. REFRAME (meta-wniosek 2 niezależnych pionów + pełnego sweepu)

To NIE „wiele niezależnych bugów" — to **garść chorób strukturalnych, które się rozmnożyły**, bo każda naprawa trafiała w JEDEN bliźniak albo w KRAWĘDŹ (render/instrument), nigdy w źródło reguły, a ta sama reguła żyje w 8+ kopiach. Dowód: rodzina pozycji łatana ≥4× wraca; carried-first „naprawiany 10×". **Wspólny mianownik napraw, które NIE wróciły:** jedno źródło reguły + trafienie u źródła + WSZYSTKIE bliźniaki RAZEM + strażnik nawrotu.

---

## 1. CONFIRMED (19) — źródło realne, refutacja nie przeszła

Kolumna **żywy stan**: 🔥 = strzela LIVE dziś · 🧊 = strukturalnie-realny, latentny/0-blast dziś (mina na flipie / dług nawrotu) · ⏱ = czasowo-krytyczny.

| Root | Sev | Rodzina | Klasy | Żywy stan | Co to jest (skrót) | Warstwa naprawy |
|---|---|---|---|---|---|---|
| **one-route-order-module** | P1 | R1 | A1·J | 🧊⏱ | Kolejność-jazdy w **5 kopiach / 3 repa / 3 języki**, brak importu repo↔repo. Monitor parytetu DZIŚ 100% ok ALE **wygasa 2026-07-10** → 0 sieci parytetu. | **L6.A** (PoC #1) |
| **earliest-pickup-floor-no-chokepoint** | P1 | R1 | A1·B·C·H | 🧊 | „Najwcześniej kurier odbierze" liczone w **17 powierzchniach, 4 mają floor**; `available_from`=**0 trafień**, runtime-inwariant `pickup≥shift_start`=**0**. Przy proposalu v324a-clamp łapie 83/83 pre_shift → ostry objaw NIE manifestuje live. | **L4** (F1) |
| **geometry-blind-selection** | P1 | R2 | C·I | 🧊 | `lex_qual` (klucz selekcji) czysto-czasowy, ZERO osi geometrii; spread>8km NIE rejectuje (metric-only). ALE główna ścieżka JEST geometry-aware (LATE_PICKUP_TIERING ON + bonus_r1_soft_pen 145×) → „blind" zawężone do best-effort/scarcity. | **L6.C** (z de-pile RAZEM) |
| **feas-carry-instruments-predict-not-outcome** | P1 | R3 | E | 🧊 | 3 przyrządy feas-carry mierzą PREDYKCJĘ (objm route-sim), ZERO joinu `delivered_at`/`gps_truth`; sentinel ~10000min dominuje regret. Napędziły flip ON 27.06 → rollback. Flaga OFF dziś. | **L7.4** (outcome-join) |
| **objm-shadow-canary-twins-alltick** | P1 | R3 | E·A1 | 🧊 | `objm_lexr6_peak_verdict` headline ALL-TICK ×7-11 zawyżka vs monitor per-decyzja (naprawiony #6a, peak NIE). Bramkuje at-200 (03.07). | **L6.D1** |
| **bug4-reseq-invariant-misspec** | P1 | R3 | E | 🧊 | Inwariant `delta≥0` na ZŁYM obiektywie (OSRM-drive zamiast SLA/total_duration którym wybiera plan); własny gate pada 11.5%. Shadow-only. | **L6.D3** (02.07) |
| **carried-first-guard-empty-env-void** | P1 | R3 | E·D | 🧊 | **Strażnik nawrotu carried-first SAM JEST VOID** — `systemctl show -p Environment`=PUSTE → 14 flag default-OFF → 90% rekordów fikcyjne `no_position`. Siatka, której ufamy, kłamie. | **L0.2** (env-parytet) |
| **serializer-allowlist-metrics-vanish** | P1 | R3 | E·F | 🧊 | `_AUTO_PROP_PREFIXES` allow-list bez kontroli kompletności → **38 kluczy ginie** z ledgera (`eta_source`=0/2000, `r6_gold4_gate`=0/2000 zmierzone). Bramkuje kalibrację O2. | **L1.1** (PoC kandydat) |
| **verdict-reader-wrong-stale-source** | P1 | R3 | E·H | 🧊 | Werdykt-tools czytają ZAMROŻONY `dispatch_state/sla_log` (20.06) zamiast żywego `scripts/logs/` → `real_joined=0`; `min_delivered` rotation-blind → fałszywe „inconclusive". | **L1.2** |
| **flag-state-3-layer-no-single-source** | P1 | R3 | D·J | 🧊 | Stan flag = 3 warstwy (flags.json + drop-in env + const), **112 flag poza rejestrami**, fingerprint 63/≥90, realna divergencja per-proces (`USE_V2_PARSER` shadow=V1 vs watcher=V2). | **L0.1** (keying-point) |
| **r6-cap-35-flat-vs-40-tier-plus-quantile** | P1 | R7 | N·I | 🧊 | R6 w **6 stałych** (35 bare / 40 hot / 35 O2×2 / 35 bundle / p80); HARD-reject ma 1 źródło, rozjazd w flag-gated quantile + best-effort tier-40 + bundle flat-35 (over-penalizuje T3). | **L6.B2** (02.07) |
| **calibration-on-wrong-axis** | P1 | R5 | G | 🔥 | Optymizm R6 = poślizg ODBIORU (+27.4 med, oracle C13), nie jazda (~0 błędu). Żywe strojenie (`ETA_QUANTILE_R6_BAGCAP` 32%) luzuje HARD na ZATRUTEJ osi; oś realna OFF. | **L5.1** (⛔HARD ACK) |
| **stale-txt-verdict-no-ttl** | P2 | R6 | H | 🧊 | Werdykt-`.txt` to ZAMROŻONE snapshoty bez TTL → sesja czyta nieaktualny stan jako bieżący (repro 7598-vs-14036). Brak programatycznych czytelników (sev P2). | **L1.2** |
| **dead-producer-orphan-consumer-shadow-logs** | P2 | R6 | K | 🧊 | `c5_shadow_log` 100% test-pollution (producent DEAD); orphan-konsumenci. Wave_scoring martwy. | **L8.3** |
| **post-shift-replay-validated-vs-void-ADVERSARIAL** | P2 | R7 | E | 🧊 | Sprzeczność audytów: alokacja-seed mówił VOID, lane-C oracle mówi VALIDATED → adwersarz potwierdził: przyrząd DZIAŁA dziś, VOID-claim odbijał WCZEŚNIEJSZY stan (pole doszło 28.06). Higiena void-claim. | **L7.8** |
| **paczka-r6-exempt-inverted-in-ranking** | P2 | R7 | I·A1 | 🧊 | Bramka zwalnia paczkę z R6 (3 HARD-sites), ale ranking/O2 liczy ją jako spóźnioną (`_count_sla` bez exempt) → exempt ODWRÓCONY w selekcji. Latentne (O2 OFF). | **L6.B3** (02.07) |
| **fleet-load-multi-mechanism-tax** | P2 | R7 | I·N | 🧊 | 2-3 mechanizmy obciążenia żywe naraz (`FLEET_LOAD_BALANCE` -15 + `LOADGOV` -40 = suma); A3-vs-A2 flag-drift (governor ON nie OFF). Deterministyczna potrójna kara. | **L7.6** (⛔ACK D5) |
| **r-declared-time-hard-no-runtime-invariant** | P2 | R7 | I | 🧊 | R-DECLARED-TIME deklarowana HARD (najwyższy priorytet) ale **0 runtime-bramki** (tylko komentarze); egzekucja pośrednia przez SOFT R27. Zmiana R27 cicho złamie. | **L7.1** (tripwire) |
| **numeric-threshold-scatter-mixed-override** | P2 | R3 | N | 🧊 | Progi (R27 ±5, margin 15, czasówka 60, 8km) w N miejscach, 3 ścieżki override (bare/env/flags.json-hot) niespójnie. | **L8.5** |

**Czytanie:** **18/19 CONFIRMED to 🧊** (strukturalnie realne, latentne/0-blast DZIŚ) — to DŁUG, który WRACA, nie pożar. Jedyny 🔥 LIVE = `calibration-on-wrong-axis` (i pośrednio sentinel, §4). To potwierdza tezę Adriana inaczej niż „pali się": **system nie strzela błędami masowo — on jest KRUCHY** (każda mina uzbraja się na flipie/re-enable/resecie-flagi). Naprawa = rozbroić miny u źródła + strażniki, żeby nie wracały.

---

## 2. PLAUSIBLE (7) — realne, ale dowód niepełny / framing sporny

| Root | Sev | Rodzina | Dlaczego PLAUSIBLE (nie CONFIRMED) |
|---|---|---|---|
| **no-global-deconflict-new-order** | P1 | R2 | Per-event greedy BEZ engine-claim (grep=0, pile-on 33%/d do 7) — ALE overlay de-pile dla NOWYCH zleceń JEST live od 27.06 (`GLOBAL_ALLOC_WRITE`+`PANEL_FLAG_GLOBAL_ALLOC_OVERLAY=1`). Silnik nie ma claim, konsola de-pile'uje. (P0-B z seedu, złagodzony.) |
| **frozen-committed-vs-preshift-floor** | P1 | R7 | Struktura source+open potwierdzona (frozen omija floor), ALE oś frozen NIE manifestuje live (ścieżka Ziomek-proposal dominuje); 1 refuter twierdzi że to oś koherencji ROOT-6 (floor), nie osobny root. |
| **schedule-data-3way-failopen-failclose** | P1 | R7 | Oracle (5 kształtów grafiku) dowodzi tri-stan osiągalny+konsumowany; ALE live ledger: fail-CLOSE nigdy nie odpalił, fail12_failopen=3/956 → realna manifestacja rzadka. |
| **frozen-lexqual-shadow** | P2 | R1 | Rozjazd kodu DOSŁOWNIE realny (3-krotka inline vs 3/4 kanon) ALE podwójnie uśpiony (shadow OFF + POST_SHIFT OFF) → 0 live; źródło=objaw K1 (nie niezależny root). |
| **hard-feasibility-split-layer** | P2 | R2 | Ścieżka egzekwuje HARD-przed-SOFT (nie bypass) na żywym stanie; ALE `_assert_feasibility_first` jest FAIL-SOFT + `FEAS_CARRY_READMIT` bypass latentny (flaga OFF). |
| **instrument-append-jsonl-silent-swallow** | P2 | R5 | Journal 14d: gałąź swallow NIGDY nie odpaliła, wszystkie instrumenty piszą; ALE 2 instancje `except OSError: pass` (address_mismatch, time_route_monitor) realnie nieme. |
| **name-vs-behavior-hard-misnomers** | P2 | R4 | Mylące nazwy (VETO=metric-only, HARD_GATE=selekcja-tier) realnie żyją, brak rootu nadrzędnego — ale to słownictwo (sev sporne P2/P3), 0 błędu decyzji. |

---

## 3. REFUTED (13) — adwersaryjnie obalone jako NIEZALEŻNY OTWARTY root (anty-overstate)

To jest pas, który chroni przed zawyżeniem „chaosu". REFUTED ≠ „nie istnieje" — najczęściej = **zwija się w inny CONFIRMED root** (dedup), **jest by-design**, **już-naprawione**, albo **latentne-nie-otwarte**.

| Root | flagi (src/open) | Dlaczego obalony jako osobny otwarty root |
|---|---|---|
| **out-of-engine-position-gates** | nie/nie | Equal-treatment UNIFIED w silniku; gates (reassignment_forward/auto_assign-G7/feed) = shadow/console-only LUB rozbrojone → nie żywy źródłowy root. |
| **out-of-engine-position-classifier-drift** | tak/tak | Realny, ale zwija się w `feas-carry`/`reassignment` void-instrument root (nie osobny). |
| **r6-anchor-vs-sla-anchor** | nie/nie | Zwija się w `r6-cap-35-vs-40` + O2-deferred (by-design do 02.07); nie osobny otwarty. |
| **equal-treatment-vs-discriminate-position** | tak/tak | Zwija się w position-gates/equal-stack; engine-część unified. |
| **commit-divergence-masking-and-silent-off** | tak/tak | Maskowanie const↔flags.json jest UDOKUMENTOWANE (flags.json wygrywa by-design); 1 przyrząd, nie systemowe. |
| **conftest-flag-leak-not-fixed** | tak/tak | Claim „NIE naprawione" obalony — strip+baseline pokrywa adekwatnie; resztka = część `flag-state-3-layer` (CONFIRMED), nie osobny. |
| **czasowka-60-threshold-silent-desync** | tak/tak | Zwija się w `numeric-threshold-scatter` (N2). |
| **coord-sentinel-no-ingest-chokepoint** | tak/tak | ⚠ **PATRZ §4 — framing obalony, HARM REALNY.** Walidator ISTNIEJE (`common.py:513`), tylko nie wpięty u ingest → „brak chokepointu" technicznie fałsz; substancja (8 ofiar/d) stoi. |
| **schedule-fail-open-vs-fail-close-asymmetry** | tak/tak | Zwija się w `schedule-data-3way` (PLAUSIBLE). |
| **one-delivery-eta-source** | nie/tak | ETA-dostawy = osobna oś od route-order; nie niezależny source (cross-ref R1-A', nie double-count). |
| **one-sla-r6-anchor** | nie/nie | Zwija się w `r6-cap` (anchor=część cap-rodziny). |
| **feas-first-guard-blind-koord-valves-masked** | nie/nie | Zawory KOORD maskowane = latentne (flagi OFF), nie otwarte; zwija w `hard-feasibility-split-layer`. |

---

## 4. ⚠ RECONCILIACJA: sentinel (0,0) — root REFUTED, ale HARM LIVE-POTWIERDZONY (uczciwość)

Adwersaryjny pas obalił root `coord-sentinel-no-ingest-chokepoint` **jako framing** (bo kompletny walidator `coords_in_bialystok_bbox` ISTNIEJE w `common.py:513` — więc „brak walidatora" jest fałszem). **ALE niezależny pomiar (dashboard §7, świeży log 30.06) pokazuje HARM REALNY I ŻYWY:** **2046× `V328_CP_SOLVER_FAIL` + 14456× `COORD_GUARD`, 8 distinct ofiar dziś** (cid=179×5, cid=492 Jakub W×3), BRAK alertu. Mechanizm: `(0,0)` truthy → haversine → V328 wyrzuca ZAJĘTEGO kuriera z puli (most K5 do P0 alokacji).

**Werdykt scalony (NIE pozwalam adwersarzowi zaniżyć żywej szkody):** **HARM = CONFIRMED LIVE**; obalone było tylko słowo „brak chokepointu". Poprawny opis rootu: **„walidator istnieje, NIE jest wpięty u ingest + 6 niespójnych definicji sentinela + truthy-guard `if coords:` w callerach geometrii"**. Fix = **wepnij ISTNIEJĄCY walidator u każdego ingest + `if coords:`→`_valid(coords)` RAZEM** (L2, P0). To 🔥 LIVE, nie 🧊.

---

## 5. ODŁOŻONE bez adwersaryjnej weryfikacji (14, cap — JAWNIE, nie cisza)

Cap 64-thunki priorytetyzował P0/P1. Te P2/P3 rooty są zmapowane i w roadmapie, ale NIE przeszły 2-refuterowego pasa (oznaczone jako „wykryte, nie-zweryfikowane-adwersaryjnie"):

`lying-docstrings-stale-protocol-seeds` · `eta-pickup-one-field-two-roles` · `coupled-location-fields-async-write` · `uwagi-field-boundary-loss` · `naive-datetime-tz-convention-split` · `tier-token-overload` · `shift-start-midnight-anchor` · `shared-state-no-lock-rmw` · `cookiejar-threadpool-shared-session` · `courier-plans-lifecycle` · `dead-decision-code-misleads-and-arms-mines` · `lexical-naming-units-rot` (P3) · `unbounded-append-only-caches` (P3) · `repo-clutter-retired-not-removed` (P3).

(Wszystkie z Faz B/C — instancje plik:linia w `backing/B08,B10,B12-B18`. Domykane w L6.E/L7/L8 roadmapy.)

---

## 6. POKRYCIE 15 KLAS — liczby instancji + reprezentatywne plik:linia

Pełne instancje: `backing/WF2_DIGEST.md` (241 findingów). Per-klasa (Faza B):

| Klasa | findings | reprezentatywne (plik:linia) | główny root |
|---|---|---|---|
| **K** martwy-kod | **35** | `r6_soft_penalty_c3_legacy` (feasibility_v2:1128), R7=99km nigdy-fires (common.py:800), 326+ `.bak`, panelsync DEAD (665L) | dead-code → L8 |
| **A1** N-kopii | 24 | lex_qual ×6, route-order ×5, SLA-anchor ×4, R6-cap ×6 | one-route-order / r6-cap |
| **E** kłamiące-przyrządy | 19 | 19 VOID / 49 (Deliverable #3) | feas-carry / serializer / canary |
| **M** sentinele | 19 | `(0,0)` truthy (dispatch_pipeline:4823/2149), 119 bare-except, BIALYSTOK fiction | sentinel (§4) |
| **N** rozsyp-progów | 17 | R6 35/40 ×6, czasówka 60 ×6, margin 15 ×5, R27 ±5 ×5 | threshold-scatter |
| **B** asymetria-bliźniaków | 16 | de-pile przerzut≠nowe, carried-relax konsola≠apka, floor 4/17 | (per rodzic) |
| **C** zła-warstwa | 16 | geometria SOFT-only (feasibility_v2:504), FEAS_CARRY bypass (dispatch_pipeline:6266) | geometry-blind / split-layer |
| **D** dryf-flag | 16 | 112 poza rejestrem, drop-in≠flags.json, COMMIT_DIVERGENCE masking | flag-3-layer |
| **J** cross-repo | 16 | route-order 3 repa, fleet_state nie-importuje, monitor wygasa 07-10 | one-route-order |
| **F** semantyka-pól | 15 | eta_pickup display=decyzja, delivery_coords bez address | eta / coupled-fields |
| **L** słownictwo/TZ | 13 | tier ×2-znaczenia, naive/aware, shift_start-północ, time-param-minuty | tier-overload / tz-split |
| **O** współbieżność | 12 | pending_proposals 3-writer no-lock, load_plan side-effect, stale-pos 25min | concurrency-no-lock |
| **H** cykl-życia | 7 | zombie-plany 43, recanon-no-prune, stale-.txt | plan-lifecycle |
| **G** kalibracja | 5 | PICKUP_DEBIAS 4.5 vs +27, PREP_BIAS OFF, quantile luzuje HARD | calibration-axis |
| **I** konflikt | 2 (+81 par→Deliverable #2) | (oś D, 13 klastrów) | (Deliverable #2) |

---

## STATUS / CAVEATY
- **DRAFT do przeglądu Adriana.** Werdykty z adwersaryjnego pasa (2 refuterzy P0/P1) — ufaj „REFUTED" jako anty-overstate, ale §4 pokazuje gdzie nie pozwoliłem zaniżyć żywej szkody.
- **Linie DRYFUJĄ** (≥3 sesje/dzień) — każdy fix re-grepuje (ETAP 0).
- **Pełne dowody:** `backing/E_dedup_1/2/3.md` (rooty), `backing/B*.md` `C*.md` `D*.md` (instancje + oracle + konflikty), `backing/WF2_DIGEST.md` (skonsolidowane 241+49+81).
- **STOP przed naprawą** — to audyt. Naprawa = Faza 3, osobne sesje, protokół ETAP 0→7, ACK per fala.
