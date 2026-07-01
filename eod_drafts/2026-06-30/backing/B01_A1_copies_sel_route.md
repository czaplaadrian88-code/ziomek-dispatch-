# B01 — KLASA A1 (ta sama reguła w N kopiach kodu) — pasma SELEKCJA + ROUTE

**Agent:** B01-A1-copies-SEL-ROUTE · **Lane B** · **TRYB READ-ONLY** · **Data:** 2026-06-30 ~14:1x UTC · sesja tmux 2
**HEAD:** `80247056` (working tree silnika `.py` czysty — tylko logi/jsonl `M` + audyt `.md` `??`).
**Zakres:** klasa A1 w modułach SELEKCJA+ROUTE: `dispatch_pipeline`, `feasibility_v2`, `scoring`, `objm_lexr6`, `route_simulator_v2`, `tsp_solver`, `plan_recheck`, `plan_manager`, `same_restaurant_grouper`, `auto_assign_gate`, `wave_scoring`.
**Metoda:** świeży `grep -rn` + `Read` ciał funkcji + odczyt EFEKTYWNYCH flag (`flags.json` venv). Każdy `plik:linia` z grepu z DZIŚ (linie DRYFUJĄ — ≥3 sesje na repo). Dedup-do-rootu (A6 distinct-root rollup R1-R5) ZACHOWANY — NIE liczę zunifikowanych bliźniaków jako chaosu.
**Flagi efektywne (zmierzone dziś):** `ENABLE_OBJM_LEXR6_SELECT=True` · `_SELECT_SHADOW=False` · `ENABLE_BEST_EFFORT_OBJM_R6_KEY=True` · `ENABLE_POST_SHIFT_OVERRUN_PENALTY=ABSENT(→False)` · `ENABLE_BUNDLE_DELIV_SPREAD_CAP=True` · `ENABLE_PACZKA_R6_THERMAL_EXEMPT=True` · `ENABLE_O2_READY_ANCHOR_SWEEP=ABSENT(→False)`.

---

## 0. STRESZCZENIE — 5 KLASTRÓW A1, 11 INSTANCJI

| # | Klaster A1 | Kopie | Parytet | Stan | Root (A6) | Sev |
|---|---|---|---|---|---|---|
| **C1** | `lex_qual` (klucz jakości R6-breach selekcji) | 1 kanon + 5 importerów (UNIFIED) + **1 frozen inline** | WSPÓLNY-IMPORT+GOLDEN / **NIC** (frozen) | FRAGILE (3 vs 4-krotka; post-shift 3 sposoby) | R1 K1 | P2/P3 |
| **C2** | bucket pozycji + best-effort winner-key | `_selection_bucket` 6 kluczy (UNIFIED) + **auto_assign_gate G7** + best-effort sort_key↔objm | GOLDEN (engine) / **NIC** (G7) | engine UNIFIED, G7 LATENT, 2 best-effort keys | R1 K1 | P3 |
| **C3** | **SLA/plan-ranking anchor** `(sla_violations,…)` | **2 inline-mirror** SLA-loop + **3 kopie** klucza `(sla_viol,dur)` + anchor≠R6-thermal | **NIC** (ręczne lustro) | DIVERGED (paczka LIVE) + FRAGILE | R3 K1+K3 | **P1/P2** |
| **C4** | R6 cap = 35 | `BAG_TIME_HARD_MAX_MIN` kanon + **4 niezależne stałe =35** + **hardcoded literał 35** ×7 + tier-40 | **NIC** (stałe rozsypane) | DIVERGED-by-construction | R6-cap N | P2 |
| **C5** | geometria-spread / bearing | `R1_MAX_DELIV_SPREAD_KM` vs `BUNDLE_MAX_DELIV_SPREAD_KM` (2× 8.0) + bearing 2 kopie | **NIC** | DIVERGED (2 stałe LIVE) + dead-end-selekcji | geometry P0-A | P3 |

**Wniosek dedup:** C1+C2 = root **R1 (one selection key)**; C3 = root **R3 (one SLA/R6 anchor)**; C4 ⊂ R3/R6-tier rodzina; C5 = oś P0-A z `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md` (geometria ślepa w selekcji) — **NIE re-derywuję P0-A, cross-ref**. Najpilniejsze A1 = **C3** (paczka-divergence LIVE bo `ENABLE_PACZKA_R6_THERMAL_EXEMPT=True`) + **C4 hardcoded-35 w plan_recheck**.

---

## C1 — `lex_qual` (klucz jakości leksykograficznej R6-breach selekcji)

**Reguła:** zwycięzca w grupie tier×bucket = `min(R6-breach → committed-late → new-pickup-late)`, opcjonalnie z wiodącym `post_shift_overrun_penalty`.

### C1.a — Kanon + importery (UNIFIED, dobry parytet)
| Rola | plik:func:linia (świeże) | Krotka | Import kanon? |
|---|---|---|---|
| **KANON** | `objm_lexr6.py:29 lex_qual(c)` | 3-elem. (OFF) / **4-elem.** (ON `ENABLE_POST_SHIFT_OVERRUN_PENALTY`, l.44-46) | — źródło |
| importer A | `dispatch_pipeline.py:665,667 _best_effort_objm_pick` → `_OL.lex_qual` | kanon | ✅ `import objm_lexr6 as _OL` (l.663) |
| importer B | `dispatch_pipeline.py:710 _best_effort_objm_shadow` → `_OL.lex_qual` | kanon | ✅ (l.703) |
| importer C | `dispatch_pipeline.py:1230,1250,1252 _best_effort_r6_would_redirect` → `_OL.lex_qual` | kanon | ✅ |
| importer D | `dispatch_pipeline.py:1307,1339,1340 _feas_carry_readmit_pick` → `_OL.lex_qual` | kanon | ✅ |
| importer E | `dispatch_pipeline.py:1378 _objm_lexr6_d2_pick` → `_olx.pick(..., bucket_fn=_selection_bucket)` (klucz=`lex_qual`) | kanon | ✅ **LIVE** (`ENABLE_OBJM_LEXR6_SELECT=True`, wpięte `:5995-5996`) |

Parytet kanon↔A-E = **WSPÓLNY-IMPORT + GOLDEN** (`tests/test_objm_lexr6_unify_2026_06_25.py` asercja `"def _lex_qual" not in src`). To jest UNIFIED — **NIE liczyć jako chaos** (dedup R1).

### C1.b — FROZEN INLINE (jedyna otwarta kopia)
- `dispatch_pipeline.py:1122 _objm_lexr6_shadow._lex_qual` = **3-krotka HARD-CODED** `(r6|9e9, late_committed|0, new_pickup_late|0)`, NIE post-shift-aware. ✗ własna kopia, ZERO importu kanonu.
- **Parytet = NIC.** Świadomie zamrożony pod walidację at#152 (docstring `objm_lexr6.py:12-16`). Bucket-część scalona na `_selection_bucket` (l.1119-1120, B2 28.06); rozjazd siedzi WYŁĄCZNIE w `_lex_qual`.
- **Stan FRAGILE:** dziś bajt-identyczny z kanonem TYLKO bo `ENABLE_POST_SHIFT_OVERRUN_PENALTY=ABSENT(→False)` → kanon też 3-krotka. Flaga ON → kanon 4-krotka, frozen zostaje 3-krotka → **cień rankowałby INACZEJ niż live d2-pick = kłamiący przyrząd przy walidacji at#152** (klasa E przy flipie).
- ⚠ **Podwójnie uśpiony dziś:** `ENABLE_OBJM_LEXR6_SELECT_SHADOW=False` → `_objm_lexr6_shadow` NIE jest nawet wołany (`:6249-6250` za flagą OFF). Czyli rozjazd jest LATENTNY×2 (flaga shadow OFF + post-shift OFF). **Ale to nadal otwarta strukturalna kopia** — kandydat #1 protokołu (`ziomek-change-protocol.md:47-48`).

### C1.c — `post_shift_overrun_penalty` term — 3 RÓŻNE SPOSOBY (sub-A1 w rodzinie lex)
Ta sama reguła „kurier kończący PO zmianie spada" zakodowana 3× niespójnie:
| Powierzchnia | plik:linia | Jak |
|---|---|---|
| `_best_effort_sort_key` | `dispatch_pipeline.py:591` `ps_pen=_post_shift_overrun_penalty_of(c)` → ZAWSZE wiodący term (0.0 gdy OFF) | bezwarunkowy slot |
| `objm_lexr6.lex_qual` | `objm_lexr6.py:44-46` prepend TYLKO gdy flaga ON | warunkowa krotka |
| `_objm_lexr6_shadow._lex_qual` | `dispatch_pipeline.py:1122` — NIGDY | brak |
| main `_late_pickup_score_first_key` | `dispatch_pipeline.py:533-549` — brak | brak (selekcja główna feasible≥1 nie ma post-shift!) |

→ Flip `ENABLE_POST_SHIFT_OVERRUN_PENALTY` ON: best_effort_sort_key i objm_lexr6 dostają term, frozen i main NIE → **selekcja główna ≠ best-effort na osi post-shift** + cień rozjeżdża się z kanonem. P3 latent (flaga absent), arms-on-flip.

---

## C2 — bucket pozycji + best-effort winner-key

### C2.a — `_selection_bucket` UNIFIED (engine, dobry parytet)
KANON `dispatch_pipeline.py:2451 _selection_bucket(c)` (equal-treatment-aware). Konsumenci (wszyscy → kanon):
- `_late_pickup_score_first_key:546` ✅ · `_best_effort_sort_key:583` ✅ · `_best_effort_fastest_pickup_key:618` ✅ (**scalone 29.06** — seed/protokół „HARDCODED informed0/blind2" jest STALE; dziś `_selection_bucket`) · `_pln_pure_resort:1086` ✅ · `_objm_lexr6_shadow:1119-1120` ✅ · `_objm_lexr6_d2_pick:1378 bucket_fn=_selection_bucket` ✅.
Parytet = GOLDEN (`test_position_bucket_single_source_2026_06_29.py`, `test_b2_e2_equal_treatment_bucket.py`). **UNIFIED — dedup R1, NIE chaos.**

### C2.b — out-of-engine: auto_assign_gate G7 (własna kopia informed-check)
- `auto_assign_gate.py:163` `if pos_source not in tuple(informed_pos_sources): blocks.append("pos_not_informed:...")` — WŁASNY informed-check, NIE `_selection_bucket`/`_is_informed_cand`. `informed_pos_sources` wstrzykiwane parametrem (`:92`).
- Parytet z `_selection_bucket` = **NIC**. To 6. z 8 bliźniaków pozycji (protokół `:44`).
- **LATENT:** `ENABLE_AUTO_ASSIGN=False` → gałąź egzekucji martwa; uzbroi się na autonomii (AUTON). Klasa A1/B. P3.
- (Replay-tool `tools/nogps_preshift_bucket_replay.py:35-52` ma własne `_bucket_live/_bucket_equal` — instrument poza pasmem, cross-ref Faza C.)

### C2.c — best-effort winner-key: carry-blind ↔ carry-inclusive (2 implementacje, OBIE live)
Ta sama decyzja „kogo wybrać w best_effort (feasible=0)" liczona 2 osiami R6:
- `_best_effort_sort_key:564` → `(ps_pen, r6_pov, sla, bucket, -score, dur)` gdzie `r6_pov`=**LICZBA** `r6_per_order_violations` (`:581-582`). Pre-sortuje `with_plan` (`:6751`, `ENABLE_BEST_EFFORT_POS_SOURCE_KEY=True`).
- `_best_effort_objm_pick:633` → `objm_lexr6.lex_qual` gdzie PRIMARY=`objm_r6_breach_max_min`=**MINUTY** breach. Override `:6771-6787` (`ENABLE_BEST_EFFORT_OBJM_R6_KEY=True`) PODMIENIA `best` na objm-pick.
→ ten sam wybór rankowany raz po LICZBIE naruszeń R6, raz po MAKS minutach breach. Świadoma tranzycja (carry-blind→carry-inclusive, case #482817), obie R6-aware → P3, ale to dwa współistniejące klucze tej samej reguły (A1/B). Dedup R1.

---

## C3 — SLA / plan-ranking anchor `(sla_violations, …)` ★ NAJPILNIEJSZE A1

**Reguła:** naruszenie SLA = `predicted_delivered − pickup_anchor > sla_minutes`; plan-ranking = `min(sla_violations, total_duration_min)`.

### C3.a — Pętla anchora SLA: 2 INLINE-MIRROR (rozjazd na paczce — LIVE)
| Kopia | plik:func:linia | Anchor | paczka-exempt? |
|---|---|---|---|
| **A** | `route_simulator_v2.py:635 _count_sla_violations` (l.648-656) | `pickup_at[oid]` (TSP-proj) → `picked_up_at` → `now` | **NIE** |
| **B** | `feasibility_v2.py:1146-1166 SLA-loop` (w `:1135 if plan.sla_violations>0`) | `plan.pickup_at[oid]` → `picked_up_at` → `now` (IDENTYCZNA logika) | **TAK** (`:1152 ENABLE_PACZKA_R6_THERMAL_EXEMPT` + `_is_paczka_sim`) |

- A i B = ręczne lustro tej samej pętli, **rozjechane na paczce.** `ENABLE_PACZKA_R6_THERMAL_EXEMPT=True` (zmierzone) → **rozjazd ŻYWY, nie latentny:**
  - A (`_count_sla_violations`, paczka-ślepa) produkuje `plan.sla_violations` (count).
  - `plan.sla_violations` zasila (1) bramkę wejścia do B (`:1135 if >0`) ORAZ (2) **klucz plan-rankingu** `_o2_primary`/`_o2_key` (C3.b).
  - Skutek: worek z paczką dostaje ZAWYŻONY `sla_violations` w **rankingu planów** (A liczy paczkę jako violation), mimo że paczka jest R6/SLA-exempt → plany z paczką deprioritetyzowane w wyborze trasy. B (decyzja reject) to potem re-filtruje, ale ranking już skażony.
- Parytet A↔B = **NIC** (brak golden-testu wiążącego). FRAGILE: następny term exempt cicho rozjedzie dalej.

### C3.b — Klucz plan-rankingu `(sla_violations, total_duration_min)` = 3 KOPIE
| Rola | plik:func:linia | Krotka |
|---|---|---|
| route_sim selektor planu | `route_simulator_v2.py:152/157 _o2_primary` → `:159 sorted(key=(_o2_primary(p), p.total_duration_min))` | `(sla_violations\|o2, dur)` |
| plan_recheck helper | `plan_recheck.py:683 _o2_key(p)` → `:690 (p.sla_violations, round(dur,3))` (użyte `:704, :722`) | `(sla_violations, dur)` |
| plan_recheck **INLINE 2. kopia** | `plan_recheck.py:1670` `key = (p.sla_violations, round(dur,3), tuple(p.sequence))` — **NIE używa `_o2_key`** mimo że ten istnieje w tym samym pliku | `(sla_violations, dur, seq)` |

→ Klucz rankingu planów re-zakodowany 3× (2 w `plan_recheck` osobno!). `plan_recheck:1670` ignoruje własny `_o2_key:683`. A1 czysty w paśmie ROUTE. Konsument C = `plan_recheck._o2_key` (`:722` committed-ok gate) — czyta `p.sla_violations` (count z A, paczka-ślepy) nie re-derywuje anchora.

### C3.c — anchor SLA ≠ anchor R6-thermal (A1 + I/C)
- A/B kotwiczą na `pickup_at` (TSP-projected, jazda-zależny).
- `route_simulator_v2.py:663 r6_thermal_anchor` (kanon R6) kotwiczy na `pickup_ready_at` (gotowość jedzenia), INV-R6-ANCHOR-CONSISTENCY.
- → **DWIE HARD-bramki tej samej decyzji (R6-thermal i SLA) liczą INNY anchor.** Znana luka O2 (review 02.07, `ENABLE_O2_READY_ANCHOR_SWEEP=ABSENT→OFF`). P1/P2 (LIVE decision: `sla_violations` gateuje feasibility `:1135`).

**Dedup:** C3 = root **R3 (one SLA/R6 anchor)** — 3 bliźniaki RAZEM per protokół (`route_simulator._count_sla_violations` + `feasibility_v2` SLA-loop + `plan_recheck._o2_key`). Co-design z `ENABLE_ETA_QUANTILE_R6_BAGCAP` + `ENABLE_PACZKA_R6_THERMAL_EXEMPT` (paczka 3-site, brak w O2).

---

## C4 — R6 cap = 35 (rozsyp stałych + hardcoded literał) ★ ROUTE-band

**Reguła:** R6 BAG_TIME ≤ 35 min (tier-aware: 35 T1/2, 40 T3). Kanon = `common.py:763 BAG_TIME_HARD_MAX_MIN=35`.

### C4.a — 4 NIEZALEŻNE stałe = 35 (ta sama reguła, osobne definicje)
| Stała | plik:linia | Rola | =35? |
|---|---|---|---|
| `BAG_TIME_HARD_MAX_MIN` | `common.py:763` | R6 HARD gate (feasibility `:1105/1128`, pipeline `:4788/6859/6862/6872`) | 35 (KANON) |
| `DEFAULT_SLA_MINUTES` | `feasibility_v2.py:53` | domyślny `sla_minutes` check_feasibility (`:432`) | 35 (osobna) |
| `C2_PER_ORDER_THRESHOLD_MIN` | `feasibility_v2.py:38` | C2 per-order gate (`:291`, reject `:1306`) | 35.0 (osobna) |
| `O2_OVERAGE_CAP_MIN` / `O2_CAP_Z_MIN` | `common.py:2661-2662` | O2 świeżość cap (route_sim `:148/776`, plan_recheck `:681`) | 35 (osobne ×2) |

→ R6=35 ma 5 niezależnych definicji. Jeśli ktoś przestroi `BAG_TIME_HARD_MAX_MIN`, `DEFAULT_SLA_MINUTES`/`C2`/`O2` zostają 35 → HARD-gate ≠ SLA-check ≠ O2. A1/N.

### C4.b — HARDCODED literał `35` (NIE stała) — ROUTE band, K2-cofacz
| plik:linia | Kontekst |
|---|---|
| `plan_recheck.py:699` | `R.simulate_bag_route_v2(..., sla_minutes=35, ...)` — **literał** w regeneratorze kanonu (timer 5min) |
| `plan_recheck.py:1668` | `simulate_bag_route_v2(..., sla_minutes=35, ...)` — **literał** (2. ścieżka) |
| `plan_recheck.py:1137/1262/1416` | `if bag > 35.0` — **literał** porównania carry |
| `plan_recheck.py:1424` | `carry_cap = max(35.0, bcarry)` — **literał** |
| `dispatch_pipeline.py:3756` | `sla_minutes = 35` |
| `dispatch_pipeline.py:6976` | `sla_minutes=35` |
| `route_simulator_v2.py:248` | `sla_minutes: int = 35` (default param) |
| `tsp_solver.py:57` | `sla_minutes_hard: float = 35.0` (default param) |

→ **`plan_recheck` (K2 „cofacz", 5-min regen kanonu) re-symuluje z literałem `sla_minutes=35` ODCZEPIONYM od `BAG_TIME_HARD_MAX_MIN`.** Retuning R6 → tick decyzyjny (shadow, via DEFAULT_SLA) i regen kanonu (plan_recheck, =35) rozjadą się cicho. Klasa A1 w paśmie ROUTE. P2.

### C4.c — flat-35 vs tier-40 (by-design, kontekst N)
`BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` (`common.py:2651`, cap_min `_best_effort_objm_pick:633`) = Tier-3 cap-stretch — ŚWIADOMIE 40, NIE bug (protokół „R6 tier-aware"). Ale `tools/bundle_calib_shadow.py:280 overage=max(0,age-35)` PŁASKO 35 over-penalizuje T3 (instrument, poza pasmem — cross-ref Faza C / A4 smell N). Odnotowane jako N-context, nie A1-defekt rdzenia.

---

## C5 — geometria-spread / bearing (cross-ref P0-A)

### C5.a — `deliv_spread_km` cap = 2 NIEZALEŻNE stałe (obie 8.0, obie LIVE)
| Stała | plik:linia | Rola |
|---|---|---|
| `R1_MAX_DELIV_SPREAD_KM` | `feasibility_v2.py:90` =8.0 | metryka `r1_violation_km` (`:504-505`), kara SOFT `bonus_r1_soft_pen` (pipeline `:4623`) |
| `BUNDLE_MAX_DELIV_SPREAD_KM` | `common.py:2280-2281` =8.0 (env-override) | FIX_C cap (pipeline `:4874/4904`, `ENABLE_BUNDLE_DELIV_SPREAD_CAP=True` LIVE) |

→ ten sam `deliv_spread_km` (producent `feasibility_v2.py:172 _max_deliv_spread_km`, serializowany `:501`) bramkowany przeciw DWÓM stałym 8.0 zdefiniowanym niezależnie. Retuning jednej → druga zostaje. A1/N. P3.
(`R5_MAX_MIXED_PICKUP_SPREAD_KM=2.5` feas:95 = pickup-spread, osobna reguła R5 — nie ta sama.)

### C5.b — bearing/cosine geometria w 2-3 kopiach
- `geometry.py:30 bearing_deg` → `(degrees(atan2(x,y))+360)%360` (KANON L2).
- `wave_scoring.py:242 _bearing_deg` → `(degrees(atan2(x,y))+360.0)%360.0` — **BAJT-IDENTYCZNA re-implementacja inline**, NIE importuje `geometry.bearing_deg`. + `:252 _cosine_similarity_bearings`.
- Kierunkowa spójność (cosine) liczona też w `feasibility_v2.py:536 r1_avg_pairwise_cosine` (osobny producent).
→ formuła bearing w 2 kopiach (geometry vs wave_scoring); oś cosine w 2 producentach. A1. P3.

### C5.c — cross-ref P0-A (NIE re-derywuję)
Geometria (`deliv_spread_km`, `r1_avg_pairwise_cosine`, `r1_new_drop_dist_km`) jest LICZONA (`feasibility_v2:500-547`) i serializowana, ale **żaden klucz selekcji jej NIE czyta** — żyje tylko jako SOFT-kara w `score` (`dispatch_pipeline:4623-4907`), którą ścieżka best_effort wyrzuca. To **P0-A z `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md`** (geometria ślepa w selekcji pod scarcity = case 447 Dawid). **Klasa C (decyzja tylko jako SOFT) — NIE moja A1; cross-ref, anty-double-count.** A1-część = sama duplikacja stałej/formuły (C5.a/b).

---

## TABELA POKRYCIA (moduł × klaster — co sprawdzone świeżym grepem/Read)

| Moduł | C1 lex_qual | C2 bucket/be-key | C3 SLA-anchor | C4 R6=35 | C5 geom | Uwaga |
|---|:--:|:--:|:--:|:--:|:--:|---|
| `objm_lexr6.py` | ✅ kanon `:29` | ✅ `bucket:50` | — | — | — | pełny Read 1-89 |
| `dispatch_pipeline.py` | ✅ frozen `:1122`+5 imp. | ✅ `_selection_bucket:2451`+6 | ✅ klucz konsument | ✅ literał `:3756/6976` + `BAG_TIME` użycia | ✅ FIX_C cap `:4874` | Read 533-720, 1097-1151, 1355-1385, 6740-6794 |
| `feasibility_v2.py` | — | — | ✅ SLA-loop B `:1146` | ✅ `DEFAULT_SLA:53`/`C2:38` | ✅ `R1_MAX:90` | Read 1135-1189 |
| `route_simulator_v2.py` | — | — | ✅ A `:635`+`_o2_primary:152` + `r6_thermal_anchor:663` | ✅ default `:248`/`O2:776` | — | Read 635-733 |
| `plan_recheck.py` | — | — | ✅ `_o2_key:683`+inline `:1670` | ✅ literał `:699/1668/1137/1424` | — | Read 695-706 |
| `tsp_solver.py` | — | — | (`sla_minutes_hard` param) | ✅ default `:57` | — | grep defs |
| `scoring.py` | — | (konsumuje score) | — | — | (kary geom w pipeline, nie scoring.py) | grep |
| `same_restaurant_grouper.py` | — | — | — | — | — (`GROUP_TIME_TOLERANCE_MIN` window, brak A1) | grep defs |
| `auto_assign_gate.py` | — | ✅ G7 `:163` | — | — | — | grep + Read |
| `wave_scoring.py` | — | — | — | — | ✅ `_bearing_deg:242` | grep defs |
| `plan_manager.py` | — | — | — | — | — | (route-order = inny agent route-band; brak A1 w pasmie SLA/lex/bucket/R6) |

---

## COVERAGE_GAPS (jawne luki, nie cisza)

1. **Wartości runtime parytetu NIE udowodnione** — że kanon `lex_qual` ≡ frozen `_lex_qual` bajtowo przy OBU stanach `ENABLE_POST_SHIFT_OVERRUN_PENALTY`; że A (`_count_sla_violations`) ≡ B (SLA-loop) poza paczką. Deklarowane z lektury, NIE z odpalenia (Faza C oracle / Faza E adversarial).
2. **`scoring.py` (288 L) NIE czytany w całości** — geometria-kary (bonus_r1/fix_c) są w `dispatch_pipeline`, nie w `scoring.py`; potwierdziłem grepem brak `lex_qual/_selection_bucket/deliv_spread` w scoring.py, ale ~19 kar `bonus_` nie zmapowane 1:1 (to Faza B scoring-agent).
3. **`plan_manager.py` route-order** — pominięty pod kątem A1 SLA/lex/bucket/R6 (brak trafień grepem); jego kopie kolejności-trasy = root R2 (inny agent route-order, A6 grupa 2). NIE moja klasa.
4. **`same_restaurant_grouper`** — `are_orders_groupable` ma własny `GROUP_TIME_TOLERANCE_MIN=5min` window; NIE duplikuje pickup-span/R5/R8 (osobna reguła grupowania). Brak A1.
5. **tier-40 vs flat-35 w `bundle_calib_shadow`** — instrument poza pasmem SEL+ROUTE; cross-ref A4 smell N / Faza C.
6. **Cross-repo kopie** (konsola `fleet_state`, apka `courier_orders`, courier_api) — route-order/ETA kopie = root R2/J (A5/A6 grupa 2), poza moim pasmem A1-SEL+ROUTE silnika. NIE re-derywuję.
7. **`auto_assign_gate` `informed_pos_sources`** — sprawdziłem definicję G7, NIE prześledziłem co dokładnie woła przekazuje jako `informed_pos_sources` (czy lustro `_is_informed_cand`) — Faza B/C trace.

---

## HANDOFF Faza E (dedup-do-rootu)

- **C1+C2 → R1 (one selection key, K1):** engine UNIFIED; otwarte = frozen `_lex_qual` (`:1122`, podwójnie uśpiony) + post-shift 3-sposoby + auto_assign_gate G7 (latent) + 2 best-effort keys. PoC „one selection key" musi objąć przepięcie frozen po at#152 (peak verdict 03.07, at-200) + zrównanie G7 przed autonomią.
- **C3 → R3 (one SLA/R6 anchor, K1+K3):** 3 bliźniaki RAZEM (`_count_sla_violations`+SLA-loop+`_o2_key`) + 3 kopie `(sla_viol,dur)` + anchor pickup_at→pickup_ready_at. **Paczka-divergence LIVE** (`ENABLE_PACZKA_R6_THERMAL_EXEMPT=True`) = najpilniejsze. O2 sprint 02.07 (at-168/200). Faza D: SLA-anchor vs R6-anchor = potencjalny konflikt precedencji (I).
- **C4 ⊂ R6-cap/N:** literał-35 w plan_recheck = najwyższy zwrot (K2 regen odczepiony od kanonu); unifikacja = 1 stała `BAG_TIME_HARD_MAX_MIN` wszędzie (z tier-aware override jawnym).
- **C5 = P0-A (geometria, cross-ref allocation-audit):** A1-część = stała 8.0 ×2 + bearing ×2; reszta (geometria ślepa w selekcji) NALEŻY do P0-A — NIE double-count.
