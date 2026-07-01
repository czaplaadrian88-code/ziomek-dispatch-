# A6 — GRAF BLIŹNIAKÓW / KOPII REGUŁ (OS 6, FAZA A inwentarz)

**Tryb:** READ-ONLY. **Data:** 2026-06-30 ~13:45 UTC. **Sesja:** tmux 2.
**Cel:** zasilić Fazę E (dedup-do-źródła, anty-double-count) + Fazę B (sweep B/C asymetria) + Fazę D (precedencja).
**Metoda:** świeży `grep -rn` per grupa (linie zweryfikowane na żywo dziś; DRYFUJĄ — re-grepuj przed użyciem jako pewnik).
**Konwencja statusu parytetu:** `WSPÓLNY-IMPORT` (jedno źródło, reszta importuje) / `GOLDEN-TEST` (osobne kopie, test inspekcyjny pinuje parytet) / `RUNTIME-MONITOR` (jsonl liczy rozjazd na żywo) / `NIC` (brak mechanizmu — rozjazd cichy).
**Konwencja rozjazdu:** `UNIFIED` (scalone) / `DIVERGED` (już się rozjechały) / `FRAGILE` (zgodne dziś, następny term rozjedzie) / `BY-DESIGN-SPLIT` (świadomie osobne).

---

## STRESZCZENIE — 7 GRUP, STAN PARYTETU

| # | Grupa | Kopie | Parytet | Stan | Root |
|---|---|---|---|---|---|
| 1 | `lex_qual` (klucz jakości selekcji) | 1 kanon + **5 live-importerów** + **1 FROZEN inline** | WSPÓLNY-IMPORT + GOLDEN-TEST (kanon) / **NIC** (frozen shadow) | **FRAGILE** (shadow 3-krotka vs kanon 4-krotka) | K1 |
| 2 | Kolejność trasy / kanon | **5 kopii** (1 engine-choke + 4 rendery, 2 repo) | GOLDEN-TEST (engine) + RUNTIME-MONITOR (cross-repo) / **NIC** (brak wspólnego importu repo↔repo) | **DIVERGED** (twin #11, 44-75/d) | K1+K7 |
| 3 | Bucket pozycji (8 bliźniaków) | 1 kanon + **7 engine-twins** (UNIFIED) + **≥4 out-of-engine** (SPLIT) | GOLDEN-TEST (engine) / **NIC** (gates/shadow/feed) | **engine UNIFIED, gates DIVERGED** (klasa wraca ≥4×) | K1+K6 |
| 4 | SLA-anchor | **3 kopie** (2 inline-mirror + 1 czyta count) | **NIC** (ręczne lustro) | **FRAGILE + DIVERGED** (SLA na pickup_at vs R6 na ready-anchor) | K1+K3 |
| 5 | `_bucket` inline kopie | 3 historyczne (`_pln_pure_resort`, `_objm_lexr6_shadow`, `fastest_pickup_key`) | GOLDEN-TEST | **UNIFIED** (28-29.06) — POZA `_lex_qual` w shadow (→ grupa 1) | K1 |
| 6 | Floor `pickup ≥ shift_start` | **17 powierzchni** liczy czas-najwcześn.-odbioru, **4 mają floor** | **NIC** (brak jednego źródła `available_from`; brak runtime-inwariantu) | **DIVERGED by-construction** (13/17 bez floor) | K1+K2+K4 |
| 7 | `eta_pickup` (display vs decyzja) | 1 pole = display ∧ decision-value | **NIC** (brak separacji) | **DRYF SEMANTYKI** (karmi scoring+hard-reject+committed) | F1 |

**Najważniejszy wniosek dla Fazy E:** grupy 1/3/5 to TEN SAM korzeń K1 manifestujący się w sweepie selekcji 24-29.06 — **NIE liczyć ich jako 3 niezależne „chaosy"**. Realne otwarte rozjazdy = (a) frozen `_lex_qual` w shadow (gr.1), (b) out-of-engine gates pozycji (gr.3: `reassignment_forward_shadow`+`auto_assign_gate G7`+`feed.py`), (c) cross-repo route order (gr.2), (d) SLA-anchor≠R6-anchor (gr.4), (e) floor 17-miejsc (gr.6). To **5 distinct rootów**, nie 7 grup × N kopii.

---

## GRUPA 1 — `lex_qual` (klucz jakości leksykograficznej selekcji)

**Reguła:** ranking kandydata wewnątrz grupy (tier × bucket) zwycięzcy score = `(R6-breach → committed-late → new-pickup-late)`, opcjonalnie z wiodącym `post_shift_overrun_penalty`.

### Kopie (świeży grep)
| Rola | Plik:func:linia | Krotka | Importuje kanon? |
|---|---|---|---|
| **KANON** | `objm_lexr6.py:29` `lex_qual(c)` | **3-krotka** (OFF) / **4-krotka** (ON `ENABLE_POST_SHIFT_OVERRUN_PENALTY`, l.44-46) | — (źródło) |
| live-importer A | `dispatch_pipeline.py:633` `_best_effort_objm_pick` → `_OL.lex_qual` (l.665, 710) | kanon | ✅ `from dispatch_v2 import objm_lexr6 as _OL` (l.663, 703) |
| live-importer B | `dispatch_pipeline.py:1355` `_objm_lexr6_d2_pick` → przez `_olx.pick(..., bucket_fn=_selection_bucket)` (l.1369-1379) | kanon | ✅ (l.1369) — **FAZA2 LIVE, flaga `ENABLE_OBJM_LEXR6_SELECT`**, podpięte `dispatch_pipeline.py:5996` |
| live-importer C | `dispatch_pipeline.py:1289` `_feas_carry_readmit_pick` → `_OL.lex_qual` (l.1307, 1339-1340) | kanon | ✅ (l.1301) |
| live-importer D | `dispatch_pipeline.py:~1198` `_best_effort_r6_would_redirect` → `_OL.lex_qual` (l.1230, 1250, 1252) | kanon | ✅ (l.1224) |
| live-importer E | `dispatch_pipeline.py:1122` (wewn. `_d2`-ścieżki) `min(_grp, key=_lex_qual)` przy E2 tie-break (l.1146) | lokalny `_lex_qual` (l.1122) — **TO JEST kopia w shadow, patrz niżej** | ✗ |
| **FROZEN INLINE** | `dispatch_pipeline.py:1097` `_objm_lexr6_shadow` → wewn. `_lex_qual` (l.1122-1126) | **3-krotka HARD-CODED**, NIE post-shift-aware | ✗ **własna kopia** |

### Parytet
- **Kanon ↔ live-importery A-D:** `WSPÓLNY-IMPORT` + `GOLDEN-TEST`.
  - `tests/test_objm_lexr6_unify_2026_06_25.py` — `inspect.getsource(_best_effort_objm_pick)`: asercja `"objm_lexr6" in src` ORAZ `"def _lex_qual" not in src` (l.99-101) = anty-re-dywergencja.
  - `tests/test_objm_lexr6_module.py` — golden równoważność: `pick` == ręczna kopia dawnego inline (l.116-140).
- **Kanon ↔ FROZEN `_objm_lexr6_shadow._lex_qual`:** `NIC`. Świadomie ZAMROŻONY pod walidację at#152 (`objm_lexr6.py:12-16` docstring: „NA RAZIE trzyma własne kopie inline. Po PASS at#152 → przepiąć też cień"). Test unify JAWNIE go wyłącza (`test_objm_lexr6_unify_2026_06_25.py:9` „_objm_lexr6_shadow (D2 shadow) jest CELOWO zamrozony").

### Stan: **FRAGILE**
- Kanon ma warunkową krotkę **3 vs 4** (l.44-47): OFF → 3-elem. bajt-identyczna z frozen inline; ON → 4-elem. (prepend `post_shift_overrun_penalty`). Frozen `_objm_lexr6_shadow._lex_qual` (l.1122-1126) ZAWSZE 3-elem.
- **Dziś zgodne TYLKO bo `ENABLE_POST_SHIFT_OVERRUN_PENALTY` wiodące 0.0 = no-op** przy OFF. Gdyby flaga była ON, shadow-cień rankowałby INACZEJ niż live-selekcja → cień przestaje być wierny (kłamiący przyrząd, klasa E #15).
- Protokół zmiany (`ziomek-change-protocol.md:47-48`) WPROST nazywa to „1. testem protokołu" / kandydatem na unifikację. **To distinct otwarty root (część K1).**
- ⚠ Bucket-część `_objm_lexr6_shadow` ZOSTAŁA scalona na `_selection_bucket` (l.1119-1120, B2 fix 28.06) — więc rozjazd siedzi WYŁĄCZNIE w `_lex_qual`, nie w bucketcie.

### Handoff Faza E
Distinct root = „one selection key" (kandydat PoC z DESIGN §4). Konsolidacja: przepiąć `_objm_lexr6_shadow._lex_qual` (l.1122-1126) na `objm_lexr6.lex_qual` PO PASS at#152 (peak verdict 03.07, at-job #200). Parytet wtedy „czysty" (docstring twierdzi bajt-identyczność). Test do dołożenia: golden że shadow `_lex_qual` ≡ kanon przy OBU stanach flagi.

---

## GRUPA 2 — KOLEJNOŚĆ TRASY / KANON (execution-order kuriera)

**Reguła:** kolejność podjazdów = kanon Ziomka (`courier_plans`) VERBATIM, z carried-first-relax („odbierz po drodze zanim dowieziesz niesione") + no-return-to-departed-pickup. **To NIE display — to realna kolejność jazdy** (C8: rozjeżdża committed pickup R27).

### Kopie (świeży grep, 2 repo)
| Rola | Plik:func:linia | Repo | Importuje engine-choke? |
|---|---|---|---|
| **ENGINE CHOKE** | `plan_recheck.py:1478` `_apply_canon_order_invariants` (woła: build `:780`, retime `:1582`) | dispatch_v2 | — (źródło, „JEDYNY choke" wg testu) |
| render apka (dispatch) | `route_podjazdy.py:190` `order_podjazdy` + `:141` `_canon_order_from_plan` | dispatch_v2 | ✗ **własna kopia** (docstring `:5` „NIE importuje tego modułu" o konsoli) |
| render KONSOLA | `nadajesz_clone/panel/.../ziomek/fleet_state.py:342` `_order_from_plan_seq` + `:395` `_build_route` | **panel (cross-repo)** | ✗ **NIE importuje** `route_podjazdy` (C7 near-miss: docstring route_podjazdy „konsola deleguje tutaj" = NIEPRAWDA) |
| render APKA-API (live) | `courier_api/courier_orders.py:1072` `build_view` → `route_podjazdy.order_podjazdy` (l.1118, gdy `BUILD_VIEW_TRUST_CANON_ORDER`); else własny `_plan_stop_sequence:672`+`_prioritize_carried_dropoffs:467` | scripts/courier_api | ⚠ częściowo (importuje route_podjazdy TYLKO za flagą; inaczej własna kopia) |
| render APKA-API (DEAD) | `courier_api_panelsync/courier_orders.py:558` `build_view` → własny `_plan_stop_sequence:366` + `optimize_route` | scripts/courier_api_panelsync | ✗ **martwa kopia** (665 vs 1285 linii; floor-audit: „zdegenerowany, MARTWY — nie serwowany") |

⚠ **`courier_api/` i `courier_api_panelsync/` to DWIE fizyczne kopie repo** (`diff` = DIFFER, 1285 vs 665 linii). Sam ten duplikat repo = J-class.

### Parytet
- **Engine-choke (jeden):** `GOLDEN-TEST` — `tests/test_precedence_hierarchy_snapshot.py` pinuje PEŁNĄ hierarchię precedencji w `_apply_canon_order_invariants` (l.24, 50) + asercja że to „JEDYNY choke" wołany przy BUDOWIE i retime (l.97-103).
- **Engine ↔ render apka (`route_podjazdy`):** `GOLDEN-TEST` — `tests/test_route_podjazdy_trust_canon.py` (trust_canon ON==kanon).
- **Konsola ↔ apka (cross-repo):** `RUNTIME-MONITOR` — `ziomek_time_route_monitor.jsonl` (932KB, świeży 13:42; review `ziomek_time_route_review.py`). **JEDYNY mechanizm parytetu repo↔repo — brak wspólnego importu.**
- **panelsync DEAD:** `NIC` (martwa).

### Stan: **DIVERGED**
- **Twin #11 (potwierdzony żywym monitorem):** konsola `_order_from_plan_seq` dostała carried-first-relax/TRUST_CANON (22.06), apka `route_podjazdy.order_podjazdy` NIE → **44-75 rozjazdów/dzień** (`ziomek-change-protocol.md:84`). Częściowo łatane 29.06 (`TRUST_CANON_WHEN_COVERS_BAG`, fleet_state.py:375-379, 877) — parytet do potwierdzenia LICZBĄ z monitora (C4/C10, NIE lekturą).
- Flagi kontrolujące rozjazd (różne per powierzchnia): `TRUST_CANON_ORDER`, `BUILD_VIEW_TRUST_CANON_ORDER`, `ENABLE_APP_ROUTE_FROM_CONSOLE`, `TRUST_CANON_WHEN_COVERS_BAG`, `PIN_AGREED_PICKUP_TIME`, `CLAMP_PRESHIFT_PICKUP_ETA`. **C5 near-miss:** `BUILD_VIEW_TRUST_CANON_ORDER` była MARTWA bo `ENABLE_APP_ROUTE_FROM_CONSOLE=1` short-circuituje przed jej konsumentem (`courier_orders.py:1146`).

### Handoff Faza E
Distinct root = „one route-order module" (drugi kandydat PoC z DESIGN §4). Cross-repo = brak wspólnego importu → konsolidacja musi albo (a) wydzielić wspólny pakiet importowany przez 3 repo, albo (b) twardy golden-fixture parytet engine↔route_podjazdy↔fleet_state↔courier_api. **Faza C MUSI odczytać świeży `ziomek_time_route_monitor` werdykt (mismatches==?) — to instrument-werdykt, kalibruj oracle (C9).** panelsync DEAD → kandydat na K (martwy kod do usunięcia).

---

## GRUPA 3 — BUCKET POZYCJI (8 bliźniaków dyskryminacji no_gps/pre_shift)

**Reguła (Adrian 29.06 HARD):** kurier BEZ GPS / przed zmianą = LICZONY RÓWNO (Białystok dojazd ~15min wykonalny). Bucket pozycji: informed→0 / other→1 / blind-empty|pre_shift→2, ale **equal-treatment ON → no_gps/pre_shift konkurują PO SCORE** (nie demote).

### 3a. ENGINE-TWINS (UNIFIED na `_selection_bucket`)
| Rola | Plik:func:linia | Bucket źródło |
|---|---|---|
| **KANON** | `dispatch_pipeline.py:2451` `_selection_bucket(c)` (equal-treatment-aware, `_equal_bucket_on`) | — |
| twin 1 | `dispatch_pipeline.py:533` `_late_pickup_score_first_key` → `_selection_bucket` (l.546) | ✅ kanon |
| twin 2 | `dispatch_pipeline.py:564` `_best_effort_sort_key` → `_selection_bucket` (l.583) | ✅ kanon |
| twin 3 | `dispatch_pipeline.py:1355` `_objm_lexr6_d2_pick` → `bucket_fn=_selection_bucket` (l.1378) | ✅ kanon |
| twin 4 | `dispatch_pipeline.py:595` `_best_effort_fastest_pickup_key` → `_selection_bucket` (l.618) | ✅ kanon (scalone 29.06; było HARD-CODED informed0/blind2; SHADOW/LOG-ONLY l.~6803) |
| twin 5 | `dispatch_pipeline.py:1034` `_pln_pure_resort` (E2) → `_selection_bucket` (l.1086) | ✅ kanon (B2 fix 28.06; **LIVE 20% E2 PLN arm**) |
| twin 6 | `dispatch_pipeline.py:1097` `_objm_lexr6_shadow` → `_selection_bucket` (l.1119-1120) | ✅ kanon (B2 fix 28.06) — ⚠ ale `_lex_qual` wciąż inline (grupa 1) |
| twin 7 (osobny mech.) | `dispatch_pipeline.py:~1812` `_demote_blind_empty` (V3.16 read-modify score+re-sort) → klasyfikatory `_is_blind_empty_cand:477`/`_is_informed_cand:490` | klasyfikatory (NIE _selection_bucket) |
| (metryka) | `dispatch_pipeline.py:~5862-5884` F1.7 score-neutral no_gps (km=śr.floty, ETA=max15,prep) + clamp pre_shift | osobna warstwa (write metrics) |

### 3b. OUT-OF-ENGINE TWINS (NIE scalone — „klasa wraca ≥4×")
| Rola | Plik:func:linia | Własna fikcja pozycji | Stan |
|---|---|---|---|
| **DUCH PRZERZUTU** | `tools/reassignment_forward_shadow.py:64` `_SYNTH_POS={none,pin,pre_shift,""}` + `:231` `_quality_gate` `a_late=(a_cand is None)` (l.256, 260) | własna `a_late`/`_pos_trusted:444` | **BY-DESIGN-SPLIT → DIVERGED**: 59% `quality_reassign` to fałszywy „ratunek" ripujący no_gps/pre_shift (holder wypadł z hipotetycznej puli ≠ spóźniony). NIGDY niezrównany z silnikiem. |
| auto-assign G7 | `auto_assign_gate.py:160-164` `pos_not_informed:{pos_source}` (vs `informed_pos_sources`) | własny informed-check | **LATENT** (`ENABLE_AUTO_ASSIGN` OFF; ugryzie na autonomii) |
| konsola feed | `nadajesz_clone/panel/.../ziomek/feed.py` overlay `quality_reassign` bez filtra `_pos_trusted` | brak filtra pewnej pozycji | **DIVERGED** (Telegram ma `_pos_trusted`, konsola nie) |
| drive_min calib | `drive_min_calibration.py` OFFSET no_gps+6,5/pre_shift+15,3 | osobny offset | **MAIN OFF** (artefakt, NIE flipować) |

⚠ **resweep/reassignment_global_select NIE są osobnym bucket-twinem:** re-uruchamiają PRAWDZIWY `assess_order` (`pending_global_resweep.py:139`, „zero dryftu scoringu") → bucket DZIEDZICZONY z `_selection_bucket`. Twin siedzi w SHADOW-gate (`reassignment_forward_shadow`), nie w global-select.

### Parytet
- **Engine 3a:** `GOLDEN-TEST` mocny — `tests/test_position_bucket_single_source_2026_06_29.py` (`inspect`: jedyna inline-kopia `_is_blind_empty_cand(c) or _is_pre_shift_cand(c)` MA być TYLKO w `_selection_bucket`, l.26-34) + `test_equal_treatment_bucket.py` + `test_b2_e2_equal_treatment_bucket.py` (asercja `_pln_pure_resort` ORAZ `_objm_lexr6_shadow` używają `_selection_bucket`, l.70-78) + `test_wait_pre_shift_2026_05_31.py` (l.113-114).
- **Out-of-engine 3b:** `NIC`. ŻADEN test nie wiąże `reassignment_forward_shadow._SYNTH_POS` / `auto_assign_gate G7` / `feed.py` z `_selection_bucket`. **To dlatego klasa wraca** (`ziomek-change-protocol.md:21,44`).

### Stan: **engine UNIFIED (28-29.06), gates DIVERGED**
Audyt no_gps: `scratchpad/audyt_no_gps_rowne_traktowanie_2026-06-29.md`. Flagi żywe ON: `ENABLE_NO_GPS_EQUAL_TREATMENT`(22.06), `ENABLE_EQUAL_TREATMENT_BUCKET`(24.06), `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY`.

### Handoff Faza E
Distinct root = ten sam K1 co grupa 1/5 (selekcja), ALE otwarty rozjazd = **out-of-engine gates (3b)**, nie engine. Naprawiając cokolwiek pozycyjnego → tknąć WSZYSTKIE 8 RAZEM (protokół Załącznik mapy). PoC „one selection key" musi objąć też przepięcie/zrównanie SHADOW-gate, inaczej `reassignment_forward_shadow` resuscytuje dyskryminację (wzorzec #2). Cross-ref K5 (sentinele): `_SYNTH_POS` traktuje `pin/pre_shift` jak fikcję = MOST do K5.

---

## GRUPA 4 — SLA-ANCHOR (kotwica liczenia naruszeń SLA)

**Reguła:** naruszenie SLA = `predicted_delivered - pickup_anchor > sla_minutes`. Anchor: `pickup_at[oid]` (plan) → `picked_up_at` (odebrane) → `now`.

### Kopie (świeży grep)
| Rola | Plik:func:linia | Kotwica | Importuje wspólne? |
|---|---|---|---|
| kopia A | `route_simulator_v2.py:635` `_count_sla_violations` (l.644-660) | **inline** pickup_at→picked_up_at→now | ✗ |
| kopia B | `feasibility_v2.py:~1146-1166` SLA-loop (w `:1135 if plan.sla_violations>0`) | **inline** pickup_at→picked_up_at→now (IDENTYCZNA logika, l.1156-1164) | ✗ **ręczne lustro A** |
| konsument C | `plan_recheck.py:683` `_o2_key(p)` = `(p.sla_violations, dur)` (l.690) + inline klucz `:1670` | czyta PRECOMPUTED `p.sla_violations` (nie re-derywuje anchora) | czyta count |
| (powiązany, ALE inny anchor) | `route_simulator_v2.py:663` `r6_thermal_anchor` — JEDNO źródło R6 (picked_up_at→**pickup_ready_at**→tsp→now) | **ready-anchor** | INV-R6-ANCHOR-CONSISTENCY |

### Parytet: **NIC** (ręczne lustro A↔B)
- Brak wspólnej funkcji: `_count_sla_violations` (route_simulator) i SLA-loop (feasibility) trzymają DWIE inline-kopie tej samej pętli anchora. `plan_recheck` czyta tylko `p.sla_violations`.
- Brak golden-testu wiążącego A≡B.

### Stan: **FRAGILE + DIVERGED (znana luka O2)**
- **DIVERGED względem R6:** SLA-loop kotwiczy na `pickup_at` (TSP-projected), a R6-thermal kotwiczy na `pickup_ready_at` (gotowość jedzenia) przez `r6_thermal_anchor`. **Dwie HARD-bramki tej samej decyzji liczą inny anchor.** Protokół (`ziomek-change-protocol.md:41`): „`_count_sla_violations` + `feasibility_v2:~1156` wciąż kotwiczą na pickup_at = przedmiot sprintu O2 (review 02.07)".
- **Asymetria paczka-exempt:** `ENABLE_PACZKA_R6_THERMAL_EXEMPT` jest w feasibility SLA-loop (l.1152) ale protokół (`:96`) notuje „3 HARD sites, brak w O2" → kopia A (`_count_sla_violations`) NIE ma exempt → rozjazd A vs B na paczkach.
- **FRAGILE:** następny term w jednej pętli (np. nowy typ exempt) cicho rozjedzie A↔B.

### Handoff Faza E/D
Distinct root (K1+K3). 3 bliźniaki RAZEM (protokół Załącznik B): `route_simulator._count_sla_violations` + `feasibility_v2` SLA-loop + `plan_recheck._o2_key`. Co-design z `ENABLE_ETA_QUANTILE_R6_BAGCAP` (gold≤4 p80 — jedyne >35 ready-anchored co przechodzi) i `ENABLE_PACZKA_R6_THERMAL_EXEMPT`. Faza D: SLA-anchor vs R6-anchor = potencjalny KONFLIKT precedencji (I-class) — dwie HARD reguły, różny anchor → która wygrywa. O2 sprint 02.07 (at-job #168/#200).

---

## GRUPA 5 — `_bucket` INLINE KOPIE (historyczne, dziś scalone)

**Status:** to PODZBIÓR grupy 3 — wszystkie INLINE `_bucket` zostały scalone na `_selection_bucket` w sweepie 28-29.06. Tu jawnie dla kompletności mapy.

| Plik:func:linia | Było (inline `_bucket`) | Jest | Data fix |
|---|---|---|---|
| `dispatch_pipeline.py:1034` `_pln_pure_resort` | inline informed>other>blind sprzed equal-treatment | `_selection_bucket` (l.1086) | B2, 28.06 |
| `dispatch_pipeline.py:1097` `_objm_lexr6_shadow` | inline `_bucket` | `_selection_bucket` (l.1119-1120) | B2, 28.06 |
| `dispatch_pipeline.py:595` `_best_effort_fastest_pickup_key` | HARD-CODED informed0/blind2 | `_selection_bucket` (l.618) | Sprint3, 29.06 |

### Parytet: `GOLDEN-TEST`
`tests/test_b2_e2_equal_treatment_bucket.py:78` `inspect.getsource(dp._objm_lexr6_shadow)` zawiera `_selection_bucket`. + `test_position_bucket_single_source_2026_06_29.py` (single-source assertion).

### Stan: **UNIFIED** — POZA jedną resztką
⚠ **`_objm_lexr6_shadow._lex_qual` (l.1122-1126) NADAL inline** — to NIE bucket, to klucz jakości → **przeniesione do GRUPY 1** (frozen pod at#152). Bucket części tych 3 funkcji = scalone; lex-część shadow = jedyna otwarta inline-resztka selekcji.

### Handoff Faza E
Grupa 5 = **NIE-distinct-root** (scalone z grupą 3). Liczyć RAZEM z grupą 1+3 jako jeden root K1-selekcja. Anty-double-count: NIE raportować jako osobny „chaos".

---

## GRUPA 6 — FLOOR `pickup ≥ shift_start` / `available_from`

**Reguła (brak kanonu!):** „najwcześniej kurier może odebrać" = `max(now, shift_start)`. **NIE istnieje jedna definicja** — re-liczona/pominięta w 17 miejscach (audyt `preshift-pickup-floor-audit-2026-06-30.md`, 9 agentów).

### Świeże potwierdzenia
- `grep available_from --include=*.py` = **PUSTE** → **L0 single-source `courier.available_from` NIE istnieje** (potwierdzone dziś).
- `grep` runtime-guard „pickup ≥ shift_start" = **PUSTE** → **ZERO inwariantu/strażnika** (potwierdzone dziś).

### MASTER-LISTA: 17 powierzchni liczących czas-najwcześniejszego-odbioru
**MAJĄ FLOOR (4):**
| # | Plik:linia | Mechanizm |
|---|---|---|
| #1 | `dispatch_pipeline.py:~5869-5884` candidate eta pre_shift = shift_start (clamp) | ⚠ **no_gps POMINIĘTY** (`:5856`) |
| #3 | `feasibility_v2.py:789-819` + `route_simulator_v2.py:273-277` (plan, flaga `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` ON) | departure clamp |
| #7 | `telegram_approver.py` (dziedziczy #1) | passthrough sclampowanego |
| #16 | `nadajesz_clone/panel/.../fleet_state.py:250` `_eta_chain` (flaga `CLAMP_PRESHIFT_PICKUP_ETA`, env ON 30.06, l.755/853) | floruje TYLKO ścieżkę OSRM |

**BRAK FLOOR (13):**
| # | Plik | Powód |
|---|---|---|
| #4 | `chain_eta.py` | floor do ready, NIE shift_start |
| **#5** | `plan_recheck.py:554-594` `_start_anchor`/`_earliest_committed_pickup_anchor` (l.31-33 committed-pickup, BEZ shift_start) | **LEAK — najszersza dziura:** regen `courier_plans.json` co 5min → odclampowuje co tick |
| #6 | `plan_manager.refloor_pickup` | floor do committed |
| #9 | `courier_api/courier_orders.py` `_committed_pickup_eta` | apka, committed |
| #10 | `courier_api/courier_orders.py:~794` `_compute_live_eta` | apka self-compute now+drive |
| #11 | `courier_api/courier_orders.py` `_attach_fallback_eta` | apka fallback |
| #12-13 | plan pass (apka) | dziedziczy niesclampowany |
| #14-15 | `courier_api_panelsync/courier_orders.py` (martwy) | DEAD bliźniak #10/#11 |
| #17-18 | `fleet_state.py` plan-path / committed-ready (konsola) | self-compute |
| #20 | `canon_eta` | — |
| #21 | restaurant_view (`deliveries`) | — |
| #22 | public_tracking | — |
| (+) | restauracja `adapter.propose_assignment:~187` pisze `promised_pickup_at=now+drive` do DB | bez sprawdzenia zmiany |

### Parytet: **NIC** (najgorszy przypadek A1/A2)
Każda powierzchnia re-liczy lub pomija. Brak wspólnego źródła. Brak runtime-inwariantu. Testy UTRWALAJĄ „floor tylko committed/ready" (`test_courier_orders_plan.py`, `test_fleet_route.py:323-393`, `test_floor_pickups_at_birth`, `test_gps_free_anchor::committed_pickup_anchor`) — czyli golden-test PINUJE ZŁY stan.

### Stan: **DIVERGED by-construction** (13/17 bez floor)
Polityka pre_shift AKTYWNIE produkuje takie przydziały (best=pre_shift 7,4% propozycji). HARD-reject dopiero `shift_start−30min` (`V325_PRE_SHIFT_HARD_REJECT_MIN=30`) → „10:59 przy 11:00" = DOZWOLONA strefa warm-up. **To dziura definicyjna + polityka, NIE bug renderu.**

### Handoff Faza E/F
Distinct root (K1+K2+K4) — KANONICZNY przykład „brak jednego źródła". Plan L0-L4 (audyt) NIE wykonany. Bliźniaki RAZEM: (#1↔#3↔**#5**) engine clamp; (#10↔#14)(#11↔#15) apka↔panelsync; (#18↔#21↔#22↔#9) display floor 4-powierzchniowy. **Najwyższy zwrot = L2 #5 plan_recheck leak.** Faza D: sprzeczność kanonu (`ZIOMEK_REGULY_KANON.md:86` „równo ON" vs `:151` „kara −20 w kodzie"). Faza C: `CLAMP_PRESHIFT_PICKUP_ETA` env-ON = flaga-mina (reset flags.json wywraca floor).

---

## GRUPA 7 — `eta_pickup` (DISPLAY vs DECYZJA)

**Pole:** `eta_pickup_utc` (decision) / `eta_pickup_hhmm` (display derived). **Wygląda na display, JEST zmienną decyzyjną** (wzorzec #8).

### Writerzy (świeży grep)
| Plik:linia | Co pisze |
|---|---|
| `dispatch_pipeline.py:4057/4061/4077` | `eta_pickup_utc` = arrive_pickup / drive_arrival / now+travel |
| `dispatch_pipeline.py:4063-4067` | R-07 v2 CHAIN-ETA override (flaga) |
| `dispatch_pipeline.py:5287` | `metrics["eta_pickup_utc"]` = isoformat |
| `dispatch_pipeline.py:5862/5877` | clamp pre_shift/no_gps → shift_start |
| `shadow_dispatcher.py:291/627` | `eta_pickup_hhmm` = `_eta_hhmm_warsaw(eta_pickup_utc)` (display derived) |

### Konsumenci DECYZYJNI (NIE display — to czyni go decision-value)
| Plik:linia | Użycie decyzyjne |
|---|---|
| `dispatch_pipeline.py:5162-5172` | `extension = eta_pickup_utc − pickup_ready_at` → **kara scoringu `extension_penalty`** (V3.24-A) |
| `dispatch_pipeline.py:3189-3193` | `overrun = (eta_pickup − created) − PACZKA_PICKUP_SOFT_CAP` → **HARD/soft paczka** |
| (cross-repo) `Ops13Console:661` | na akceptacji ducha → `time_arg` → ustawia committed `czas_kuriera` |

### Konsumenci DISPLAY
`telegram_approver.py:343-361/871/1318` (ETA linia), `feed.py` (passthrough), `shadow_dispatcher` hhmm.

### Parytet: **NIC (brak separacji display/decision)**
- Display `eta_pickup_hhmm` DERYWOWANY z decision `eta_pickup_utc` przez `_eta_hhmm_warsaw` → display ZAWSZE śledzi decyzję (to akurat OK).
- **RYZYKO = odwrotne:** edycja „napisu" = zmiana decyzji (karmi scoring+hard-reject+committed). Brak osobnego pola.

### Stan: **DRYF SEMANTYKI PÓL (F1)** — nie „rozjazd kopii" lecz „jedno pole = dwie role"
Protokół (`ziomek-change-protocol.md:27`, wzorzec #8): „display-only UDOWODNIJ grepem; jak karmi decyzję → fix = NOWE pole obok (additive), nie podmiana".

### Handoff Faza E/F
Distinct root (klasa F, nie A). Kontrakt docelowy (DESIGN §4.6): „display oddzielony od decision-value". NIE liczyć jako kopię-reguły (to inny anty-wzorzec). Faza B-F (sweep semantyki pól) bierze to + bliźniacze pola sprzężone (`delivery_address`↔`delivery_coords`).

---

## CROSS-CUTTING: SENTINELE jako MOST do twin-grafu (K5, kontekst dla Fazy B/D)

Sentinele NIE są twin-grupą, ale ZASILAJĄ position-twiny (grupa 3) i floor (grupa 6):
- `BIALYSTOK_CENTER` fiction → no_gps candidate eta (dispatch_pipeline ~5862, twin grupy 3 metryka).
- `(0,0)` zatrute `delivery_coords` → haversine sentinel → `V328_CP_SOLVER_FAIL` wyrzuca ZAJĘTEGO kuriera (floor-audit BUG#2; `_compute_repo_cost_km:2147` guarda `not coords` ale NIE `(0,0)`).
- `_SYNTH_POS={none,pin,pre_shift,""}` (`reassignment_forward_shadow.py:64`) = sentinel-jako-klasyfikator → most K5↔gr.3b.
- `geocode-centroid guard` (`ENABLE_BUNDLE_COLOC_CENTROID_GUARD` ON) chroni przed 0km coloc na BIALYSTOK_CENTER (122 zatrute adresy).

---

## DISTINCT-ROOT ROLLUP (dla Fazy E — anty-double-count)

| Root | Klasy | Grupy A6 | Kopie otwarte | Status |
|---|---|---|---|---|
| **R1 — one selection key** | A1/B/C (K1) | 1 + 3 + 5 | frozen `_lex_qual` (gr.1) + out-of-engine gates `reassignment_forward_shadow`/`auto_assign_gate G7`/`feed.py` (gr.3b) | engine UNIFIED, resztki OTWARTE |
| **R2 — one route-order module** | A1/B/J (K1+K7) | 2 | 5 kopii / 2 repo, brak wspólnego importu | DIVERGED (44-75/d monitor) |
| **R3 — one SLA/R6 anchor** | A1/C (K1+K3) | 4 | 2 inline-mirror + anchor≠R6-anchor | FRAGILE+DIVERGED (O2 02.07) |
| **R4 — one earliest-pickup floor** | A1/A2/H (K1+K2+K4) | 6 | 17 powierzchni, 4 floor, 0 inwariant, 0 `available_from` | DIVERGED by-construction |
| **R5 — display≠decision (eta_pickup)** | F | 7 | 1 pole, 2 role | DRYF SEMANTYKI |

**Grupy 5 ⊂ R1 (NIE osobny root). Sentinele = K5 most, raportowane przez agenta sentineli, tu tylko cross-ref.**

---

## DEKLARACJA POKRYCIA (jawne luki, nie cisza — C11-c)

**Sprawdzone świeżym grepem dziś:** wszystkie 7 grup zlecenia + 5 distinct-rootów. Kopie zweryfikowane `grep -rn` na żywym kodzie (nie z seed-doców).

**Pokryte powierzchnie:** dispatch_v2 silnik (105 plików `.py`), `nadajesz_clone/panel/.../ziomek/{fleet_state,feed,route}.py`, `scripts/courier_api/courier_orders.py`, `scripts/courier_api_panelsync/courier_orders.py`, `dispatch_v2/route_podjazdy.py`, `tools/{reassignment_forward_shadow,reassignment_global_select,pending_global_resweep}.py`, `auto_assign_gate.py`.

**LUKI (jawne):**
1. **courier-app Kotlin** — NIE czytany (apka mobilna API-driven przez courier_api wg floor-audit; route-order renderowany serwerowo → pokryty przez courier_api build_view; ale lokalny re-sort/ETA w Kotlin NIE zweryfikowany kodem). **Faza B/J: potwierdzić czy Kotlin re-liczy kolejność lokalnie.**
2. **Most paczki (parcel lane)** — `parcel_assign.py`/`parcel_lane_merge.py` NIE prześwietlone pod kątem własnej kopii route-order/bucket (parcel ma natywny tor orders_state, klucz `900M+id`). **Faza B: czy parcel używa `_selection_bucket`/`order_podjazdy` czy własnej ścieżki.**
3. **Wartości LICZBOWE parytetu** (czy A≡B bajtowo dla SLA-anchor; czy `_objm_lexr6_shadow._lex_qual` ≡ kanon przy OFF) — **deklarowane z lektury, NIE udowodnione runtime.** To zadanie Fazy C (oracle) / Fazy E (adversarial verify), NIE Fazy A.
4. **`ziomek_time_route_monitor` świeża liczba mismatchy** — plik istnieje (932KB, 13:42), NIE sparsowany (read-only, Faza C odpala oracle). Twin #11 „44-75/d" z protokołu, nie z dzisiejszego odczytu.
5. **Pełna lista 17 floor-powierzchni** wzięta z `preshift-pickup-floor-audit-2026-06-30.md` (9 agentów 30.06) + spot-confirmed grepem (`available_from`=∅, guard=∅, #1/#3/#5 linie); NIE re-zwalidowałem każdej z 13 „NIE-floor" linia-po-linii (zaufanie do świeżego audytu z dziś rano + spot-check kluczowych #5/#16).

**NIE-luki (świadomie poza zakresem OS6):** Mailek, Papu (granica STOP na dyspozytorni). Sentinele jako klasa M (osobny agent). Flagi efektywne (A3). Przyrządy-prawda (A4/Faza C).
