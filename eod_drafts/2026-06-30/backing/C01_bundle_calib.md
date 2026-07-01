# C01 — bundle_calib (shadow collector + review gate) — LANE C RUNTIME-ORACLE

**Agent:** C01-bundle-calib · **Lane:** C (runtime-oracle, C9/C11) · **Tryb:** READ-ONLY · sesja tmux 2 · 2026-06-30 ~16:30 UTC
**Werdykt instrumentu: ✅ VALIDATED** (faithful + internally-consistent + conservative; fix 477b731 potwierdzony na świeżym korpusie) — z 4 udokumentowanymi caveatami (1 design-accepted, 3 hygiene).
**Co bramkuje:** flip silnika **`ENABLE_O2_READY_ANCHOR_SWEEP`** (review one-shot `dispatch-bundle-calib-review.timer` **02.07 07:00 UTC**, reminder at-168 02.07 08:00). Najważniejszy verdict-gate Fazy C.

Numery linii ze świeżego grepu 2026-06-30 (HEAD `8024705`). Korpus: `dispatch_state/bundle_calib_shadow.jsonl` **mtime 16:22 FRESH, 2199 wierszy, 25.06→30.06**.

---

## 0. METODA — DRUGA, NIEZALEŻNA (nie czytanie kodu)

| # | Oracle | Druga metoda (niezależna od instrumentu) | Determinizm |
|---|---|---|---|
| A | cap flat-35 vs tier-40 | recompute `overage = Σ max(0, age−cap)` z logowanych `carry_ready` ages, sweep cap∈{35,40}, rounding-aware + band-restricted | 2× identyczne |
| B | under_z inwarianty | recompute lambda-key `o2==overage+1.5·czas_late`; cap `max_carried_age≤Z`; `o2(under_z)≥o2(calib)`; monotonia o2[20]≥o2[32]≥o2[35] | 2× identyczne |
| C | gate overage-only (477b731) | reimplementacja gate DWIEMA wersjami (overage-only NEW vs overage+1.5·czas_late OLD) na świeżych differs; policz phantom + masked-regress | 2× identyczne |
| D | konserwatyzm λ-collector vs overage-gate | split differs po `deadlines`/`czas_late`: gdzie czas_late=0 → λ-argmin≡overage-argmin (EXACT) | 2× identyczne |
| E | outcome-join | reprodukcja z `gps_delivery_truth.jsonl` (fizyczny) + `sla_log.jsonl` (klik) | 2× identyczne |
| F | **brute-force permutacji + OSRM table** | (F1) liczba poprawnych przeplotów `(2p+c)!/2^p == n_candidates` exact; (F2) tripwire ZERO fikcyjnych pickupów / stops==ids; (F3) OSRM `osrm_client.table` localhost:5001, brute overage per perm, overage-argmin vs λ-argmin | F1/F2 exact, F3 2× identyczne |
| G | **uruchom INSTRUMENT** | import `bundle_calib_review.build_report()` READ-ONLY (bez `main()`/telegram/zapisu) → cross-check z A-F | 2× identyczne |

**Inwarianty-tripwire (wszystkie ZIELONE):** delta≥0 (under_z o2≥calib o2: 0/2015 viol) · ten sam zbiór+liczba stopów (F2: 0/8049 viol) · ZERO fikcyjnych pickupów (F2: 0 viol) · kolejność z p.sequence (served/calib/under_z seqs walidowane) · liczba przeplotów = brute (F1: 2162/2162).

**CAVEAT prawdy:** `delivered_at`/`czas_kuriera` = prawda-PRZYCISKOWA. Outcome-join: strona dostawy = **GROUND-TRUTH** (gps_delivery_truth, 408/408 fizyczny), strona gotowości (ready=czas_kuriera) = **proxy-certified**. Korpus overage liczony od czas_kuriera (button) → `proxy-certified`. OSRM route==table na osi peak = certyfikowany czysty (A4 §7, n=2644).

---

## 1. GROUND FACTS (parytet instrument↔silnik)

- **Collector cap:** `bundle_calib_shadow.py:56` `R6_MAX_MIN = 35.0`; overage `:280` `overage += max(0.0, age - R6_MAX_MIN)`. **FLAT 35.**
- **Engine O2 cap (parytet target):** `route_simulator_v2.py:776` `_compute_o2_metrics(..., getattr(_C_o2, "O2_OVERAGE_CAP_MIN", 35.0))`; `:751` `overage += max(0.0, age - cap_min)`. `common.py:2661` **`O2_OVERAGE_CAP_MIN = 35.0`** (zmierzone importem). → **instrument cap == engine cap = 35 flat. PARYTET ✓.**
- **Tier-40 to OSOBNY mechanizm:** `common.py:2651` `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN = 40.0` — cap-stretch best_effort/scarcity, **NIE** objektyw O2. Adrian (memory bag-resequence 27.06, decyzja #2): „wszędzie 40 w tierze 3; t3 = RZADKIE dni niedoboru" → O2 overage cap=35 default, 40 tylko w best_effort. Decyzja #4 (segmentacja per-tier) = „bezprzedmiotowa (T3 rzadki)".
- **Gate overage-only (477b731):** `bundle_calib_review.py:93-103` `_o2_of(m)→m.get("overage",0.0)`; `:196-197` `o2(m)→overage` (bez λ·czas_late). Komentarz `:41-51` dokumentuje fix.
- **Engine flip OFF (shadow):** `ENABLE_O2_READY_ANCHOR_SWEEP` = **False** (zmierzone). `route_simulator_v2.py:143`, `plan_recheck.py:679` czytają flagę; o2_score liczony compute-always, używany TYLKO gdy flaga ON.
- **Collector flaga (D-dryf):** `ENABLE_BUNDLE_CALIB_SHADOW` = **False w flags.json** (absent) ale `systemctl show dispatch-bundle-calib-shadow` → `Environment=ENABLE_BUNDLE_CALIB_SHADOW=1 ENABLE_GPS_FREE_ANCHOR=1 BUNDLE_CALIB_LAMBDA_CZAS=1.5`. Collector **efektywnie ON przez drop-in**, ostatni tick 16:22 (116ms).

---

## 2. WYNIKI ORACLE (świeży korpus 2199, multi_uniq 2196, differs 954/43.4%)

### A — cap flat-35 vs tier-40 (rozstrzygnięte)
Rounding-aware (tol 0.05·n_oid): **mismatch vs cap=35 = 0**, **mismatch vs cap=40 = 2165**. Band-restricted (bloki gdzie cap35≠cap40): **match ONLY cap35 = 2165, ONLY cap40 = 0**, ambig 17 (rounding).
Przykład: cid=531 ages{22.7, 37.7} → logged overage **2.7** = cap35 (2.7), cap40=0.0. cid=393 ages{30.8,36.3,51.5,31.3} → logged **17.7** = cap35 (17.8), cap40=11.5.
→ **overage jest FLAT-35, NIE tier-aware.** Band (35,40]: **1141 oid-instancji w 623 workach** (gdzie flat-35 over-penalizuje hipotetyczny Tier-3 cap-40). **Ale = parytet z engine O2 (też flat-35) + decyzja Adriana.** Instrument FAITHFUL do tego co bramkuje (O2 sweep, nie feasibility).

### B — under_z inwarianty (2015 wierszy z under_z) — WSZYSTKO 0 VIOLATIONS
`max_carried_age ≤ Z`: **0** · `o2 == overage+1.5·czas_late`: **0** · `o2(under_z) ≥ o2(calib)` (constrained ≥ unconstrained argmin): **0** · monotonia `o2[20]≥o2[32]≥o2[35]`: **0**. → under_z policzony POPRAWNIE; selekcja λ-key spójna; cap-Z respektowany.

### C — gate overage-only (477b731) — FIX POTWIERDZONY
| metryka | wartość |
|---|---|
| improved (NEW overage-only, ΔO2≥2) | **500 (22.8%)** |
| improved (OLD overage+1.5·czas_late) | **529 (24.1%)** |
| **phantom czas_late usunięte fixem** | **29** |
| regress_o2 (calib overage GORSZY) | 14 (1.5% differs) |
| **MASKED freshness-regresje (OLD=improved, overage realnie GORSZY)** | **14** |

Najgorsze masked (OLD λ-gate liczyło „improved" mimo gorszej świeżości — dokładnie case z komentarza `review:44-45`):
`cid=123 ids=[483418,483444,483445,483453,483456] **d_overage=−31.6** d_czas=+36.0 d_o2lam=+22.4` (calib 31.6 min MNIEJ świeży, ale λ-gate widziało +22.4 „poprawy" bo czas_late dominował). + cid=400 d_overage=−6.7/d_czas=+10.4.
→ **Fix 477b731 = VALIDATED:** usuwa 29 fantomów + ODSŁANIA 14 maskowanych regresji świeżości. Bez fixa werdykt zawyżony i ukrywał realne pogorszenia. (29.06 snapshot był 317→304/7 masked; korpus urósł ~2× → 529→500/14 masked — TEN SAM mechanizm.)

under_z cap-table (overage-only): Z≤20 improved **8.2%** (calib>cap 55.7%) · Z≤32 **12.1%** (38.0%) · Z≤35 **13.3%** (32.2%). Wszystkie ≥ MATERIAL_PCT=2% → **verdict GO** (Z=20 najmniejszy passing). `calib_exceeds 55.7%@Z20` = dowód freshness-blindness surowego O2 (CALIB wozi >20min w 55.7% — uzasadnia twardy cap-Z Opcji 3).

### D — konserwatyzm (λ-collector vs overage-gate)
differs z deadlinem: **75/954 (7.9%)**; z nonzero czas_late: **69/954 (7.2%)**. → na **885/954 (92.8%) differs czas_late=0 → λ-argmin ≡ overage-argmin → gate EXACT** (nie tylko konserwatywny). Na 7.2% (deadline) gate KONSERWATYWNY (collector λ-wybrany overage ≥ engine overage-argmin → mierzony zysk ≤ realny → brak fałszywego GO). Konserwatyzm gwarantowany analitycznie (argmin po tym samym zbiorze + cap 35=35).

### E — outcome-join
gps_truth idx=947 (FRESH), sla idx=1810 (STALE Jun20). joined **154 worki / 408 zleceń, phys=408 (100% na fizycznym GPS)**, served_viol_pct **36.8%** (36.8% zleceń served realnie >35min od gotowości). sla_log fallback **NIE użyty** (gps_truth pokrył wszystkie) → stale sla nie psuje bieżącego werdyktu.

### F — brute-force + OSRM (lane-C core)
- **F1:** `(2p+c)!/2^p == n_candidates` dla **2162/2162 brute rows, 0 mismatch** (np. cid=531 p=1,c=1 → 3!/2=3=n_candidates). Zbiór kandydatów COMPLETE+CORRECT = ten który silnik brute'uje.
- **F2:** **0 violations / 8049 seqs** (served+calib+under_z): ZERO fikcyjnych pickupów (carried nigdy nie ma pickup-stopu), pickup-before-delivery, stops-set==order_ids.
- **F3:** OSRM `osrm_client.table` localhost:5001, 12 worków (czas_late=0, proxy-pos): overage-argmin==λ-argmin **12/12** (selection-equivalence), każdy perm overage≥overage-argmin **12/12** (konserwatyzm na realnej geometrii). 2× determinizm.

### G — uruchomienie INSTRUMENTU (cross-check) — IDENTYCZNE z A-F
`bundle_calib_review.build_report()`: corpus_rows 2199, multi_uniq 2196, differs 954/43.4%, **improved_o2 500/22.8%**, med_d_o2 2.4, **regress_o2 14/1.5%**, under_z Z≤35 feasible 29.0%/improved 13.3%/calib_exceeds 32.2%, real_served_viol 36.8% (408/408 phys), **verdict GO** (Z=20). Legacy-wtórne: bundle_improved_flag 318/14.5%, regress_count 65/6.8% (count-lens, świadomie podrzędne). → **mój recompute == instrument co do cyfry.**

---

## 3. INSTANCJE (plik:linia świeży + klasa + źródło/objaw + open?)

| id | klasa | plik:linia | źródło/objaw | opis | open? | sev |
|---|---|---|---|---|---|---|
| C01-1 | N | `bundle_calib_shadow.py:56,280` + `route_simulator_v2.py:776` + `common.py:2661` | source | overage FLAT-35 (NIE tier-40) — POTWIERDZONE (0 vs 2165 mismatch). Parytet z engine O2_OVERAGE_CAP_MIN=35; tier-40 osobny (best_effort). Adrian-accepted („T3 rzadki"). 1141 oid w (35,40]. NIE defekt instrumentu (faithful). | nie (design) | P3 |
| C01-2 | E | `bundle_calib_review.py:93-103,196-197` | source | gate overage-only (477b731) — VALIDATED: usuwa 29 fantomów czas_late + odsłania 14 maskowanych regresji świeżości (cid=123 d_overage=−31.6 odtworzony). Pre-fix instrument KŁAMAŁ (zawyżał improved, ukrywał regresje). **is_patched.** | nie | P2 |
| C01-3 | A1/N | `bundle_calib_shadow.py:224-228,269-272` (carry anchor `min(ck,pu)`) vs `route_simulator_v2.py:726` (`r6_thermal_anchor` carried=pu-only) | source | **carried-anchor PARYTET-GAP:** collector ready=min(czas_kuriera,picked_up_at), engine=picked_up_at-only. Instrument carried-age (overage/under_z/max_carried_age) NIE 1:1 z silnikiem dla niesionych → bywa konserwatywny, MOŻE przesunąć bucket Z (kod: 214/603 differ ±7min). align-to-engine = sprint O2 02.07. | TAK | P2 |
| C01-4 | D | `flags.json` (absent) vs drop-in `dispatch-bundle-calib-shadow.service` `ENABLE_BUNDLE_CALIB_SHADOW=1`; `common.py:2661` O2_OVERAGE_CAP_MIN / `:249` ENABLE_O2_READY_ANCHOR_SWEEP poza flags.json | source | collector ON via env-drop-in, niewidoczny w kanonie flags.json; flip-flaga+cap const-only (nie hot-flippowalne). Dubluje A4 §8/A3 §2a. | TAK | P3 |
| C01-5 | M/H | `bundle_calib_review.py:27` (SLA_LOG) + `sla_log.jsonl` mtime Jun20 (STALE 10d) | source | outcome-join fallback sla_log STALE; bieżąco NIEużyty (408/408 gps_truth) → latentny. Gdy gps_truth coverage spadnie → real_served_viol_pct liczony na 10-dniowym kliku. | TAK (latent) | P3 |
| C01-6 | M | `bundle_calib_shadow.py:523` `_append_jsonl` `except Exception: _log.warning` | source | cicha awaria zapisu — utrata danych worka niewidoczna (instrument „milczy"). Dubluje A4 §8 M (twin reassignment_forward_shadow:414, carried_first_guard). | TAK | P3 |

---

## 4. TABELA POKRYCIA

| Zbadane (coverage_declared) | Jak |
|---|---|
| `bundle_calib_shadow.py` (collector: overage, under_z, _walk_calib, _all_valid_perms, _calib_route, _max_carried_age, _append_jsonl) | full read + recompute A/B/D/F |
| `bundle_calib_review.py` (gate: _o2_of, build_report, _calib_under_z, _verdict, outcome-join) | full read + import build_report() G |
| Engine parytet `route_simulator_v2.py:731-790` (_compute_o2_metrics, _plan_from_sequence, o2_score), `plan_recheck.py:683-722` (_o2_key) | read + const import |
| Korpus 2199 wierszy 25.06→30.06 (cap, under_z, gate, candidate-count, fictitious-pickup, outcome-join) | recompute A-G |
| OSRM table localhost:5001 (route-timing 12 worków, 2×) | F3 |
| Flagi efektywne (ENABLE_BUNDLE_CALIB_SHADOW, ENABLE_O2_READY_ANCHOR_SWEEP, O2_OVERAGE_CAP_MIN, BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN) | systemctl show + import common |

| NIE zbadane (coverage_gaps) | Powód |
|---|---|
| Engine O2 flip ON (rzeczywiste re-sekw. silnika z `ENABLE_O2_READY_ANCHOR_SWEEP=1`) | flaga OFF, flip = ETAP 6 protokołu (zakaz DoD); zwalidowałem o2_score=overage-only z kodu+const, nie z live-ON |
| carried-anchor rozjazd ILOŚCIOWO na świeżym korpusie (min(ck,pu) vs pu-only per oid) | collector NIE loguje picked_up_at → nie odtworzę engine pu-only anchor z logu; cytuję udokumentowane 214/603 ±7min (kod 28.06) |
| OSRM brute z REALNYM pos worka (absolute overage match z logiem) | `pos`/anchor NIE logowany w jsonl → F3 proxy-pos (struktura validated, absolutne wartości nie) |
| Faza-2 czas_late w silniku (deadline na OrderSim) | nie zaimplementowane (gate słusznie czas_late wyłączył); poza zakresem flip Fazy 1 |
| Telegram werdykt (`_fmt`/`main` send) | NIE odpalony (--no-telegram nie potrzebny — wołałem build_report() wprost) |

---

## 5. WERDYKT + CO FLIPUJE

**INSTRUMENT bundle_calib (collector+review) = VALIDATED.** Dowody drugą metodą:
1. cap flat-35 POTWIERDZONY (0/2165 mismatch) i w PARYTECIE z engine O2 (35=35) — faithful do tego co bramkuje.
2. under_z wszystkie inwarianty 0-viol; selekcja λ-key spójna; constrained≥unconstrained.
3. fix 477b731 (overage-only) DZIAŁA na świeżym korpusie — usuwa 29 fantomów, odsłania 14 maskowanych regresji (cid=123 odtworzony co do cyfry).
4. gate EXACT na 92.8% worków (czas_late=0), KONSERWATYWNY na 7.2% (analitycznie: argmin, brak fałszywego GO).
5. brute candidate-set COMPLETE (2162/2162), ZERO fikcyjnych pickupów (8049 seqs), OSRM route-timing sane.
6. import instrumentu build_report() == mój recompute co do cyfry; determinizm 2×.

**Co flipuje ten przyrząd:** werdykt 02.07 dla **`ENABLE_O2_READY_ANCHOR_SWEEP`** (re-sekwencja worka „wypełnij martwy czas") + kalibracja cap-Z X/Y/Z (Opcja 3). Bieżący werdykt instrumentu = **GO** (Z=20 @8.2% ≥2%), ale tool sam ramuje to jako „warto się przyjrzeć, NIE włącz" — flip osobno HOLD do ACK (#5b geofence dostarczony 29.06; protokół ETAP 1-7). Mój oracle potwierdza że te liczby są POPRAWNE i KONSERWATYWNE → werdykt GO jest wiarygodny (nie zawyżony).

**Caveaty bramkujące flip (NIE unieważniają instrumentu):**
- **C01-3 (P2):** carried-anchor min(ck,pu) vs engine pu-only — sprint O2 02.07 MUSI zrównać kotwicę w obu, inaczej under_z bucket-Z liczony na innej kotwicy niż silnik użyje. Konserwatywny kierunek (nie fałszywe GO), ale przesuwa rekomendowany Z.
- **C01-1 (P3, design):** flip włącza flat-35 O2 sweep → over-penalizuje T3 w (35,40] w dniach scarcity. Adrian-accepted; mitygacja = twardy cap-Z (under_z).
- **C01-5 (P3):** przed 02.07 zweryfikować gps_truth coverage (sla_log 10d stale jako fallback).

**Klasa E (kłamiące „naprawione 29.06") — rozstrzygnięte dla bundle_calib:** status „NAPRAWIONE 477b731" = **CONFIRMED drugą metodą** (nie tylko deklaracja). To jedyny z 11 „naprawionych" który tu przeszedł pełny oracle.
