# FAZA F — ZBIEŻNA ROADMAPA KONSOLIDACJI ZIOMKA (z chaosu do ideału)

> **⚠️ DRAFT — produkt syntezy audytu READ-ONLY (sesja tmux 2).** Zero kodu, zero flipów, zero restartów, zero `--notify`, zero git. Ten dokument **scala 7 planów per-rodzina** (`F_target_R1..R7`) + FUNDAMENT F1-F7 (`ZIOMEK_UNIFIED_AUDIT`) + 8 metryk (`F_entropy_dashboard`) w **JEDNĄ zależnościowo-uporządkowaną drogę dojścia**. Każdy krok dotykający kodu = OSOBNY mini-sprint protokołem ETAP 0→7 + ACK Adriana. **Numery linii/daty DRYFUJĄ (≥3 sesje/dzień/repo) — re-grepuj przed dotknięciem jako pewnik.**

**Data:** 2026-06-30 · **Tryb:** READ-ONLY · **HEAD silnik:** `8024705` (2026-06-30 10:23, working tree `.py` czysty)
**Wejście:** `F_target_R1..R7.md` (7 planów rodzin) · `F_entropy_dashboard.md` (8 metryk DZIŚ→cel) · `ZIOMEK_UNIFIED_AUDIT_2026-06-30.md` (FUNDAMENT F1-F7, 7 wspólnych korzeni K1-K7) · `ZIOMEK_COHERENCE_AUDIT_DESIGN.md` §4 (8 kontraktów) · 26 przetrwałych rootów.
**Kontrakty (DESIGN §4):** ①JEDNO-źródło(kopie=1) ②kontrakt-warstw(HARD-przed-SOFT+inwarianty) ③parytet-bliźniaków(divergence=0) ④prawda-flag(dead=0,rejestr) ⑤prawda-przyrządów(void=0-przed-flipem) ⑥brak-dryfu-semantyki(display≠decision) ⑦kompletność-cyklu-życia(0-bez-GC) ⑧koherencja(0-konfliktów).

---

## 0. ZASADA POROZĄDKUJĄCA — 5 osi zbieżności (jak czytać tę roadmapę)

Roadmapa godzi **PIĘĆ** ograniczeń kolejności jednocześnie (żadne samo nie wystarcza):

1. **KRĘGOSŁUP ZALEŻNOŚCI F1-F7** (UNIFIED_AUDIT §5): `strażniki → sentinele → plan_recheck → dostępność → ETA → kanon+bliźniaki → hardening → objawy`. **Najgłębsze „nigdy nie wraca" = F1(dostępność)+F4(ETA)+F6(strażniki).**
2. **ZWROT NA NAWROTY** (klasa łatana ≥4× wraca): najpierw rodziny-rdzenie nawrotu — `plan_recheck-cofacz` (K2), `sentinel(0,0)` (K5), `floor` (17 powierzchni), `serializer` (35. prefiks), `route-order` (44-75/d). Te idą wcześnie (PO warstwie mierzącej).
3. **GRADIENT RYZYKA**: `doc/read (0) → shadow-strażnik (0) → tooling-nie-silnik (low) → silnik P0 (ETAP 0→7+ACK) → silnik+HARD-inwersja (ACK Adriana wprost)`. Niższe ryzyko najpierw — buduje dowód ON≠OFF zanim tknie decyzję.
4. **BRAMKI CZASOWE** (nadpisują 1-3 lokalnie): route-order golden **≤ 2026-07-10** (parytet wygasa); **sprint O2 02.07** (R6-cap+paczka-exempt+anchor+bundle_calib+quantile RAZEM); bug4 **02.07**; objm + frozen-lexqual **03.07**.
5. **BRAMKA „ZERO NOWYCH KOPII"** (na KAŻDYM kroku): konsoliduj-nie-dodawaj; każdy krok USUWA powierzchnię/literał/kopię i ściśle redukuje ≥1 z 8 metryk entropii; ŻADEN nie dodaje 6. renderu / 36. prefiksu / 4. mapy.

**Reframe (UNIFIED §0):** to NIE „dużo bugów" — to **garść chorób, które metastazują** bo każda naprawa trafiała w bliźniaka/krawędź, nie w źródło. Roadmapa leczy FUNDAMENT raz → oba objawy (alokacja + pre-shift) znikają + przyszłe się nie pojawiają. **Częściowa zmiana = NIEZAKOŃCZONA.**

---

## 1. WIDOK GŁÓWNY — 9 warstw L0-L8 (mapa na F1-F7 + metryki + ryzyko)

| L | Warstwa (co konsoliduje) | F1-F7 | Rooty domykane/wnoszone | Metryki ↓ | Ryzyko | Bramka czasu |
|---|---|---|---|---|---|---|
| **L0** | **Fundament wiarygodności**: rejestr-flag + harness-prawdy + rejestry-żywe + strażniki-shadow | **F6** + prereq | R1-D, R3-D1, R3-D2, R7§1.3, R6-H-A(seed) | dead-flag 5→0; void(false-parity)→0; fingerprint 63→all | **0** (doc/shadow) | — |
| **L1** | **Prawda przyrządów**: serializer-kompletność + reader-rotation + 1 append_jsonl | (F6 c.d.) | R3-E4, R3-E5, R5-M1, R6-H-A | metrics-vanish 14HARD→0; wrong-source 2→0; stale-txt→0; swallow ≥8→1 | **low** (tooling, behavior-neutral) | odblokowuje O2 02.07 |
| **L2** | **Sentinel chokepoint** (most K5): 1 walidator ingest + truthy→_valid RAZEM + catch-all rozróżnia | **F3** | R5-M2, R5-M3(część), R7-I-F(część) | sentinel-as-data 2046+14456→0; layer(truthy)→0; fail-open-silent→0 | **P0 ENGINE** (LIVE harm 8 ofiar/d) | — |
| **L3** | **plan_recheck przestaje cofać** (K2): courier-plans GC+pure-read+prune-by-status | **F2** | R6-H-B, R5-O3, carried-first(nawrót) | zombie 43→0; read-side-effect→0; twin(recanon)→0 | **P0 ENGINE + ACK** | — |
| **L4** | **Dostępność 1 źródło** (najgłębsze): `available_from=max(now,shift_start)` + północ + fail-policy | **F1** | R1-B, R4-L3, R7-I-F(część) | copy-floor 17→1; shift_start 2→1; inwariant 0→1; twin(start↔end)→0 | **P0 ENGINE + ACK** (Q1/Q2/Q2b ACK) | — |
| **L5** | **ETA load-aware** (K3): kalibracja na osi poślizgu + eta_pickup decision/display | **F4** | R5-G1, R4-S1, calibration-wrong-axis | wrong-axis-live→0; display-feeds-decision→0; compute-skew 2→1 | **P0 ENGINE + ACK** (inwersja HARD) | O2/04.07 review |
| **L6** | **Kanon + bliźniaki** (F5): route-order golden + sprint O2 + geometria/de-pile + objm/frozen-lex + słownictwo | **F5** | R1-A, R1-C, R7-I-A, R7-I-B, R3-E2, R3-E3, R2 ROOT7/8, R4(L1/L2/L4), R6-K-B(R7=99) | twin route 44-75→0; copy(r6-cap 6→1, route 5→1, anchor 4→1); threshold-sprawl; layer(geom); void(objm/bundle) | **P0 ENGINE + ACK** | **≤07-10 / 02.07 / 03.07** |
| **L7** | **Hardening / koherencja** (F7): R-DECLARED tripwire + frozen↔floor precedencja + split-layer guard + concurrency + load | **F7** | R7-I-C, R7-I-E, R2 ROOT9, R3-E1, R5-O1/O2, R7-I-D, R7-I-G, R4-NB | unresolved-conflict (frozen↔floor/R-DECL/load); layer(split); void(feas-carry); concurrency | mieszane P0/obserwacyjne + **ACK D5** | C2 przed re-enable TG |
| **L8** | **Objawy usunięte / sprzątanie** (po GO): dead-code + caches + dead-producer + clutter + reszta semantyki/progów | (cleanup) | R6-K-A, R6-K-B(reszta), R6-K-C, R6-H-C, R3-N1, R4 F2/F3/L4 | dead-code→0; unbounded-cache→0; dead-producer→0; clutter→0; threshold-copy→1 | **low** (po GO, bajt-identyczne/usuwanie) | — |

**Sekwencja krytyczna (skrót):**
```
L0 (fundament: flag-rejestr + harness + strażniki)   ← czyni WSZYSTKO weryfikowalnym+mierzalnym
 ├─ L1 (serializer+reader+append_jsonl)              ← odblokowuje widoczność HARD pod O2
 ├─ L2 (sentinel chokepoint, F3)                     ← odbudowuje pulę PRZED selekcją (most K5→P0)
 │    └─ L3 (plan_recheck, F2)                        ← najwyższy zwrot na nawroty (K2)
 │         └─ L4 (dostępność available_from, F1)      ← najgłębsze „nigdy nie wraca"
 │              └─ L5 (ETA load-aware, F4)            ← karmi feasibility+selekcję prawdą
 │                   └─ L6 (kanon+bliźniaki, F5)      ← DATE-GATED: route≤07-10, O2 02.07, objm 03.07
 │                        └─ L7 (hardening, F7)
 └────────────────────────────────────────────────────────→ L8 (sprzątanie po GO)
```

---

## 2. ROADMAPA SZCZEGÓŁOWA (per krok: co konsoliduje · rooty · metryki · zależy · bramka-zero-kopii · ryzyko/ACK · P0)

> **Legenda:** 🟢 = doc/shadow/read (0 ryzyka, brak ACK) · 🟡 = tooling nie-silnik (low, lekki ACK) · 🔴 = **P0 SILNIK** (ETAP 0→7 + ACK + off-peak>14:00 + replay ON↔OFF dowód POZYTYWNEGO wpływu + parytet bliźniaków + pełna regresja `pytest tests/` vs baseline) · ⛔HARD = dotyka inwersji HARD↔SOFT → **ACK Adriana WPROST** (P0 „SOFT/kalibracja nie osłabia HARD").

---

### ▰ L0 — FUNDAMENT WIARYGODNOŚCI (F6 + prereq) — 🟢 zero ryzyka, brak ACK

> **Dlaczego PIERWSZE (UNIFIED §5 + R1/R3 FAZA 0):** strażniki i inwarianty L1-L7 są WIARYGODNE tylko gdy (a) znany jest efektywny stan flag (inaczej golden-test biega na nieznanym stanie), (b) instrument reużywający silnik dziedziczy jego env (inaczej `no_position` 88% fikcji), (c) istnieje harness fizyki (inaczej „benefit ✅" mierzy predykcję). **Flag-rejestr to KEYING-POINT całej reszty.** Wszystko tu = doc/shadow → mierzy, nie zmienia.

| # | Krok | Co konsoliduje (źródło) | Rooty | Metryki ↓ | Zależy | Bramka „zero kopii" |
|---|---|---|---|---|---|---|
| **L0.1** | **Rejestr flag = 1 kanon** (R1-D / R3-D2): `flag_fingerprint` pokrywa WSZYSTKIE decyzyjne (dziś 63/≥90); conftest-strip + doc-baseline + flag-effect keyowane z TEGO rejestru; 2 martwe-ON (`PANEL_IS_FREE`/`TRANSPARENCY_SCORING`) usunięte; route/canon 23 env-frozen + `USE_V2_PARSER` + `OR_TOOLS/GROUPING` → ETAP4 hot-reload; usuń inwersję maskującą `COMMIT_DIVERGENCE_VERDICT_GATE` (const→False + json) | R1-D, R3-D2 | dead-flag 5→0; fingerprint 63→all; void(false-parity)→0; unresolved-conflict(silent-OFF)↓ | — | migracja env→rejestr USUWA warstwę (3→1 dla decyzyjnych); nowa flaga wchodzi przez rejestr |
| **L0.2** | **Instrumenty dziedziczą env silnika** (R1-D 0.3 → R3-D1): przyrząd reużywający funkcje silnika (carried_first_guard najpierw) dostaje drop-in = env silnika | R3-D1 | void(carried-guard env, no_position 88%)→validated | L0.1 | drop-in, nie 2. kopia stanu-flag |
| **L0.3** | **Harness fizyki + master-loader ledgera** (R3 FAZA 0): 1 join `gps_delivery_truth.jsonl`(GT)+`decision_outcomes.jsonl`(button-truth, proxy-caveat) po order_id; 1 rotation-aware loader (`.1`/`.gz`, repoint `scripts/logs/` NIE `dispatch_state/`); konwencja `.txt`-TTL marker (wspólna R6-H-A) | R3(fund), R6-H-A(seed) | (przygotowuje INV-TRUTH-1/4) | — | 1 harness + 1 loader (nie N joinów/openów ad-hoc) |
| **L0.4** | **Rejestry-żywe + dashboard + suity-inwariantów czerwone-na-start** (S0 wszystkich rodzin): MACIERZ-warstw(R2) · REJESTR-PRAWDY(R3) · REJESTR-SEMANTYKI(R4) · KONTRAKT-TRYBU-AWARII(R5) · REJESTR-CYKLU-ŻYCIA(R6) · REJESTR-KOHERENCJI/graf-precedencji(R7); INV-LAYER/TRUTH/SEM/FAIL/LIFE/COH jako testy-czerwone; re-run `F_entropy_dashboard` | wszystkie | czyni 8 metryk MIERZALNYMI (dziś niewidoczne) | — | doc-only |
| **L0.5** | **Strażniki-shadow MIERZĄ nawrót** (F6, wzór `tools/carried_first_guard.py`): floor-shadow (`pickup≥shift_start`) · route-order golden-equivalence harness · sentinel-(0,0) tripwire · etykieta „inversion-guard" flag (R7 §1.3: OFF=safe vs OFF=policy-revert) | R7§1.3 | (przygotowuje dowód ON≠OFF dla L2-L7) | L0.1, L0.2 | strażnik mierzy, nic nie zmienia |

**Wkład w dashboard:** dead-flag **5→0**, fingerprint **63→wszystkie decyzyjne**, void(false-parity) **1→0**, void(carried-guard) **→validated**, inwersja-maskująca **1→0**. **Kandydat PoC-rozgrzewka:** L0.1 fragment „rozszerz fingerprint + usuń 2 martwe-ON" (tani, kasuje fałszywy-parytet).

---

### ▰ L1 — PRAWDA PRZYRZĄDÓW (serializer + reader) — 🟡 low, behavior-neutral

> **Dlaczego TU (R3 FAZA 1-2):** TANIE + ODBLOKOWUJĄCE. Serializer odsłania 14 HARD-metryk bramkujących kalibrację O2 (02.07); reader przestaje produkować fałszywe „inconclusive". **Decyzyjnie-neutralne** (psują tylko obserwowalność dziś) → niskie ryzyko, wysoka dźwignia.

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Zależy | Bramka „zero kopii" |
|---|---|---|---|---|---|---|
| **L1.1** ⭐ | **Serializer allowlist → kontrola KOMPLETNOŚCI** (R3-E4): `_AUTO_PROP_PREFIXES`(`shadow_dispatcher:190/272`) → deny-list/test „każdy `metrics`-key serializowany ∨ jawnie-wykluczony-z-powodem"; **14 HARD najpierw** (SLA-detail dla O2 + R6-internal + `eta_source` prowenancja); LOCATION A≡B na polach kontrfaktycznych | R3-E4 | metrics-vanish 14HARD→0; A/B-asym→0 | L0.4 | allowlist→deny+test (1 mechanizm), NIE 36. prefiks |
| **L1.2** | **Rotation-aware reader + stale-txt TTL** (R3-E5 + R6-H-A): wszystkie werdykt-tools importują 1 master-loader (L0.3); `min_delivered`/`b_route` re-run rotation-aware; każdy `.txt` z `mtime+kadencja+stale`-marker | R3-E5, R6-H-A | wrong-source 2→0; stale-txt ≥6→0 | L0.3 | 1 loader/1 konwencja (−N własnych `open`) |
| **L1.3** | **1 fail-loud `append_jsonl`** (R5-M1): 6+ kopii w `tools/` (niespójne Exception/OSError, fsync/nie) → 1 wspólny helper z counterem utraty | R5-M1 | silent-swallow ≥8→1; copy-count→1 | L0.4 | scal 6+→1 helper, NIE 7. swallow |

**Wkład w dashboard:** void-instrument (serializer/reader/swallow): **5 rootów → validated**; metrics-vanish **14 HARD→0**; wrong-source **2→0**; stale-txt **≥6→0**. **⭐ PoC #1 (najwyższa dźwignia × najniższe ryzyko):** **L1.1 serializer-kompletność** — zamyka root RAZ (nie „35. prefiks po raz N-ty"), odblokowuje kalibrację 14 HARD + bramkuje O2 02.07, **zero zmiany zachowania LIVE** (dowód ON≠OFF = klucz HARD pojawia się w ledgerze).

---

### ▰ L2 — SENTINEL CHOKEPOINT (F3, most przyczynowy K5) — 🔴 P0 SILNIK, LIVE harm

> **Dlaczego TU (UNIFIED §1 K5 + R5 FAZA 0):** **MOST PRZYCZYNOWY.** Sentinel `(0,0)` truthy → `V328` wyrzuca ZAJĘTEGO kuriera z puli → `pool_feasible=0` → geometria-ślepa best-effort + brak global de-konflikcji → pile-on. **Bug#2 nie jest osobny — jest GÓRNYM biegiem P0 alokacji.** LIVE DZIŚ: 2046× V328 + 14456× COORD_GUARD + 8 ofiar 30.06, BRAK alertu. Naprawa **odbudowuje pulę ZANIM selekcja w ogóle rusza** → musi iść przed L6 (geometria/de-pile), inaczej tamto leczy objaw na zatrutej puli.

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Zależy | Bramka „zero kopii" |
|---|---|---|---|---|---|---|
| **L2.1** | **1 walidator coords u KAŻDEGO ingest** (R5-M2/K5/sentinel-agent): `coords_in_bialystok_bbox` u `gps_server`/`state_machine`/panel-parse; truthy-guard `if coords:`→`_valid(coords)` we WSZYSTKICH callerach geometrii **RAZEM** (haversine `:4823` + osrm + wave-veto `:4823` + repo_cost `:2149`); produkcja `or (0,0)` → fail-loud/SKIP; domknięcie u źródła geokodu (122 zatrute adresy, `geocode-centroid guard`) | R5-M2 | sentinel-as-data 2046+14456→0; truthy-guard ≥2→0; 6-definicji-sentinela→1 | L0.5 (tripwire mierzy baseline) | 1 chokepoint (NIE 17 łatek); 6 def→1 |
| **L2.2** | **Catch-all `_v328_eval_safe` ROZRÓŻNIA** poison/real-bug/infeasible + operator-alert na data-poison (zbiorczy, nie spam) | R5-M2 | cichy-drop-kuriera→widoczny; catch-all-undifferentiated→0 | L2.1 | rozróżnij, NIE połykaj real-bug |
| **L2.3** | **`is_on_shift` fail-loud** (R5-M3 / R7-I-F część-1): `log.warning` jak FAIL12 przy fail-open (koniec cichego 24/7) | R5-M3, R7-I-F | fail-open-silent→0 | — | most R5 fail-loud, wspólny mechanizm |

**Wkład w dashboard:** sentinel-as-data **2046+14456 zdarzeń/8 ofiar → 0 cichych**; truthy-guard **≥2→0**. **Most:** odbudowuje pulę → uzdrawia L6 geometria/de-pile u źródła scarcity. **PoC #3 (najwyższy realny harm, ale współwłasność sentinel-agent):** L2.1 — 8 kurierów/dzień przestaje znikać; bliźniaki haversine↔osrm MUSZĄ iść RAZEM (parytet obsługi `(0,0)`).

---

### ▰ L3 — plan_recheck PRZESTAJE COFAĆ (F2, K2 — najwyższy zwrot na nawroty) — 🔴 P0 SILNIK + ACK

> **Dlaczego TU (UNIFIED §1 K2 — „obie audyty trafiły w plan_recheck"):** najszersza dziura. `plan_recheck` regeneruje co 5 min: (A-audyt) woła `simulate_bag_route_v2` **bez `check_feasibility_v2`** → geometria nieliczona, zigzag wraca; (B-audyt) regeneruje `courier_plans.json` **bez floor `shift_start`** → plan SAM się odclampowuje. **„carried-first naprawiane 10×"** = ten mechanizm (mrugające `invalidated` + martwy `ORDER_DELIVERED_ALL`). Fix U ŹRÓDŁA = bliźniaki RAZEM.

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Zależy | Bramka „zero kopii" |
|---|---|---|---|---|---|---|
| **L3.1** | **Courier-plans cykl-życia** (R6-H-B/K2): `gc_invalidated` (0 prod-callerów, 43/47 zombie) {podepnij-do-janitora ∨ usuń}; `load_plan` default→**pure-read** (źródło, nie per-caller — kasuje opt-out-minę); recanon **prune-by-status** (delivered/cancelled niezależnie od surgical-event) | R6-H-B, R5-O3 | zombie 43→0; read-side-effect→0; twin(recanon)→0 | L0.5 (strażnik carried-first) | podepnij ISTNIEJĄCY GC + odwróć 1 default (nie dodaj flagi) + prune w istniejącym recanon |
| **L3.2** | **Regen przez TE SAME bramki co live** (UNIFIED F2): plan_recheck regen woła `check_feasibility_v2`/geometrię (A:R3) — **floor `shift_start` w regenie rida na L4** (`available_from`). **Bliźniaki RAZEM:** feasibility↔greedy↔plan_recheck + 4 handlery recanon (MAPA KOMPLETNOŚCI) | R6-H-B, R2(most) | layer(kanon-bez-inwariantów)→0 | L3.1; floor-część gated **L4** | bliźniacze ścieżki RAZEM, nie 1-z-N |
| **L3.3** | **`expected_version` CAS + env-parytet** między plan-recheck↔panel-watcher (most R1-D/R3-D2 env-parytet); utwardź anchor-fallback (H1b dead-end u źródła, nie drop-in) | R6-H-B | twin(2 timery różny kanon)→0 | L0.1 (flag-rejestr) | CAS, nie 2. ścieżka zapisu |

**Wkład w dashboard:** zombie **43→0**, read-side-effect **→0**, twin(recanon/2-timery) **→0**. **Most:** domyka „cofacz" dla OBU objawów (alokacja zigzag + pre-shift odclamp). **DOTYKA SILNIKA** → pełny protokół; replay ON↔OFF dowodzi BEZ-regresji carried-first.

---

### ▰ L4 — DOSTĘPNOŚĆ JEDNO ŹRÓDŁO (F1, najgłębsze „nigdy nie wraca") — 🔴 P0 SILNIK + ACK

> **Dlaczego TU (UNIFIED F1 + R1-B + Adrian Q1/Q2/Q1b/Q2b ZABLOKOWANE 30.06):** `available_from` = **0 trafień DZIŚ** (single-source NIE istnieje); runtime-guard `pickup≥shift_start` = **0**. 17 powierzchni liczy najwcześniejszy-odbiór, 4 mają floor. To L0 z roadmapy pre-shift + F1 z fundamentu. **F1+F4+F6 = najgłębsze „nigdy nie wraca".**

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Zależy | Bramka „zero kopii" |
|---|---|---|---|---|---|---|
| **L4.1** | **`available_from = max(now, shift_start)`** RAZ w `courier_resolver` (R1-B L0), konsumowane przez WSZYSTKIE 17 powierzchni (w t.cz. plan_recheck regen L3.2); obejmuje pre_shift+no_gps (no_gps on-shift = no-op `max(now,shift_start)=now`) | R1-B | copy-floor 17→1; runtime-inwariant 0→1 | L3 (regen konsumuje) | 17 re-liczeń→1 źródło; render-clampy USUWANE |
| **L4.2** | **`_shift_start_dt` north-symmetry** (R4-L3): mirror `_shift_end_dt:1278` (`24:00→+1`); zmiana 22:00-now-00:30 → start=wczoraj | R4-L3 | twin(start↔end)→0 | — | przeniesienie, nie 2. helper |
| **L4.3** | **`shift_start` = 1 źródło** (silnik datetime), konsola IMPORTUJE/odczytuje (nie własny HH:MM fetch) | R1-B | copy-count(shift_start def) 2→1 | L4.1 | konsola czyta źródło, nie re-liczy |
| **L4.4** | **JEDNA polityka fail grafiku** (R7-I-F część-2 + R5-M3): `is_on_shift`/`_shift_*_dt`/FAIL12 spójne (open LUB close, fail-loud); walidacja wpisów U ŹRÓDŁA (arkusz, „11.00") | R7-I-F | unresolved-conflict(grafik 3-way)→1; 3-traktowania→1 | L2.3 | 1 polityka, walidacja-u-źródła (−2 fallbacki) |
| **L4.5** | **RUNTIME-INWARIANT + strażnik** `pickup ≥ shift_start` (R1-B L6, wzór carried_first_guard) — fail-loud; sprzężony BUG#2 guard `(0,0)` (rida L2) | R1-B | inwariant 0→1 (jedyne co blokuje nawrót na zawsze) | L4.1, L2.1 | 1 strażnik, NIE N assertów |

**⚠ TWIST kalibracyjny (R1-B + R5-G1):** L4.4-„feasibility wyklucza pre-shift kuriera który nie zdąży" (decyzja Q2 — zmieniaj KTO nie czas) MUSI liczyć na **load-aware buforze** (poślizg ~18min rośnie z load), NIE surowym ETA → **bramkowane L5.1**. Q1b: równość ZOSTAJE (`PRE_SHIFT_EQUAL_NO_PENALTY` nietknięte; „nie zdąży"=FAKT feasibility/R-LATE-PICKUP, NIE nowa kara).

**Wkład w dashboard:** copy-count(floor) **17→1**, copy-count(shift_start) **2→1**, runtime-inwariant **0→1**, twin(start↔end) **→0**, render-clampy **4-floory→0-łatek**. **Decyzje Q1/Q2/Q1b/Q2b już ACK** (30.06).

---

### ▰ L5 — ETA LOAD-AWARE (F4, K3 optymistyczny estymator) — 🔴 P0 SILNIK + ⛔HARD ACK

> **Dlaczego TU (UNIFIED K3 + R5-G1 + kalibracja 29.06 ZAMKNIĘTA):** silnik systematycznie OPTYMISTYCZNY na świeżości — oś PRAWDY = poślizg ODBIORU (assign→pickup) **med +27.4** (DODATNI = potwierdzony oracle C13), prep **+11..+13**; noga JAZDY **~0 błędu**. Żywe strojenie LUZUJE HARD R6 na ZATRUTEJ osi (`ETA_QUANTILE_R6_BAGCAP=True`, 32,4% would_pass), oś realna OFF (`PREP_BIAS_TABLE=False`, `DRIVE_MIN_V2=False`, `PICKUP_DEBIAS` 4.5 shadow = 4-6× za mały). Karmi feasibility (L4.4) + selekcję (L6) PRAWDĄ czasu.

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Zależy | Bramka „zero kopii" |
|---|---|---|---|---|---|---|
| **L5.1** ⛔ | **Kalibracja na osi POŚLIZGU-ODBIORU + prep** (R5-G1, INV-FAIL-4/5): load-aware bufor ETA (segment po obciążeniu floty), kierunek GATE-STRICTER (bag_time ROŚNIE, R6 bije wcześniej), NIGDY do renderu (landmine F1.8g); 1 mapa prep (usuń ANTYK 20.06); serializuj `r6_gold4_gate_recovered` (rida L1.1) | R5-G1 | wrong-axis-live→0; real-axis-parked→live; 2-mapy-prep→1; calibration-invisible→0 | L1.1 (serializer); **gated O2 02.07 + load-aware 04.07** | przekieruj 1 żywą kalibrację + włącz 1 parked, NIE 4. mapa |
| **L5.2** | **`eta_pickup` decision/display rozdział** (R4-S1/F1, wzorzec #8 additive): `eta_pickup_decision`(surowy, 1 komputacja, jedyne wejście extension+>60-reject+committed) ⊥ `eta_pickup_display`(floored derywat); HARD-reject>60→L5; serializuj `eta_source` | R4-S1 | display-feeds-decision→0; compute-skew 2→1; semantic-role-overload(eta) 2→1 | L1.1(eta_source); L4(floor-overlay); R2(reject→L5) | additive NOWE pole, nie mutacja in-place |

**Wkład w dashboard:** wrong-axis-live **→0**, oś-realna **parked→live**, display-feeds-decision **1→0**, compute-skew **2→1**. **⛔HARD:** `ETA_QUANTILE_R6_BAGCAP` to ŚWIADOMA flaga luzująca HARD-R6 (ACK 14.06) — **NIE rwać, PRZEKIEROWAĆ oś** w O2 02.07 (ACK Adriana wprost; inwersja HARD↔SOFT). Co-design z L6.B (quantile-recovery rozstrzygany RAZEM z R6-cap+anchor).

---

### ▰ L6 — KANON + BLIŹNIAKI (F5) — 🔴 P0 SILNIK + ACK — **DATE-GATED**

> **Dlaczego TU + bramki czasu:** unifikacja kanonu reguł i renderów. **3 sub-bloki, każdy z własną bramką czasu**, ale wszystkie PO L2-L5 (zatruta pula naprawiona, plan_recheck nie cofa, floor jest, ETA prawdziwa — inaczej geometria/cap leczą objaw na zepsutym fundamencie).

#### L6.A — Route-order parytet (R1-A) — 🟡→🔴 **≤ 2026-07-10 (czasowo-krytyczne)**
> Monitor parytetu cross-repo (`ziomek-time-route-monitor.service`) **pod-certyfikuje** (B22-J6: hardkod `trust_canon_ok=True`, pomija invalidated) i — wg `F_target_R1`/dashboard — **SAM WYGASA ~2026-07-10** (⚠ exact `MONITOR_STOP_AFTER` NIE potwierdzony moim grepem DZIŚ → **ETAP-0 verify**; niezależnie od daty: monitor pod-certyfikuje → golden-CI lepszy). Po wygaśnięciu: 0 importu + golden iluzoryczny + monitor martwy = ZERO sieci parytetu.

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Bramka „zero kopii" |
|---|---|---|---|---|---|
| **L6.A1** ⭐ | **Golden-fixture equivalence CI** (R1-A 1.1): `order_podjazdy(X) ≡ _build_route(X) ≡ courier_orders-route(X)` na wspólnym wejściu, w CI obu repo — zastępuje wygasający monitor | R1-A | twin-divergence(route 44-75/d)→0 (mierzona, nie wygasa) | golden zastępuje monitor, NIE 2. monitor |
| **L6.A2** | **Usuń MARTWY `courier_api_panelsync/courier_orders.py`** (665L, R6-K-C/B22-J8 = DEAD member R1) + `PICKUP_MERGE_MIN` 5→1 stała + 2. producent `_save_plan_on_assign` woła `_apply_canon_order_invariants` + fail-soft import→fail-LOUD | R1-A, R6-K-C | copy-count(route 5→4, PICKUP_MERGE 5→1); dead-code; layer(kanon-bez-inwariantów) | usuwa powierzchnię, nie dodaje 6. renderu |
| **L6.A3** | (cel docelowy, większy) wspólny pakiet route-order importowany przez 3 repa + ETA-dostawy R1-A' (`chain_eta`/`live_eta_cache` autorytatywny) | R1-A, R1-A' | copy-count(route 4→1, ETA 3-4→1) | wspólny import, nie 4. kopia |

#### L6.B — Sprint O2 (R7-I-A + R7-I-B + R15 anchor + bundle_calib) — 🔴⛔ **02.07 RAZEM**
> **MAPA KOMPLETNOŚCI: flip O2 rusza 6 sites R6-cap + 4 sites paczka-exempt + anchor + bundle_calib RAZEM.** Bramkowane przez L1.1 (serializer odsłania SLA-detail) + bundle_calib-oracle-fix.

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Bramka „zero kopii" |
|---|---|---|---|---|---|
| **L6.B1** | **`bundle_calib` overage TIER-AWARE** (R7-I-A część-1, most R3, PRZED engine): `overage=max(0,age−r6_cap_for_tier(tier))` — przestaje kłamać dla T3; oracle bramkujący O2 dostaje wierny pomiar | R7-I-A, R3(C01) | void(bundle_calib flat-35)→validated (INV-COH-7) | tier-aware, nie 2. przyrząd |
| **L6.B2** | **`r6_cap_for_tier()` 1 helper** (R7-I-A część-2 + R15): zastępuje 6 stałych (`BAG_TIME_HARD_MAX_MIN=35` bare `common.py:763` / `BEST_EFFORT_OBJM..=40` hot `:2651` / `O2_OVERAGE_CAP=35` `:2661` / `R6_MAX_MIN=35` `bundle_calib:56` / p80-quantile); 35 baza WSZYSCY, 40 = `mode=ALARM` gated; konsumują feasibility+best_effort+O2+bundle_calib na 1 `r6_thermal_anchor` | R7-I-A, R15(R1) | threshold-sprawl(r6-cap 6→1); unresolved-conflict(35↔40); copy-count(anchor 4→1) | 1 helper −5 kopii, NIE 7. literał |
| **L6.B3** | **paczka-exempt w 1 anchor-helperze** (R7-I-B): auto-spójny feasibility-gate + SLA-count + O2-sweep; **4. site `_compute_per_order_delivery_minutes` Z flipem O2** (C3 coupling) | R7-I-B | unresolved-conflict(exempt-inversion)→0 (INV-COH-5) | exempt w helperze, nie 4. `if is_paczka` |
| **L6.B4** ⛔ | **quantile-recovery rozstrzygnięty** (R7-I-A część-3 + R5-G1 co-design): USUŃ (D3) + skalibruj prędkość gold na osi poślizgu (L5.1, NIE delivery-pesymizm) ALBO obwaruj `mode=ALARM` | R7-I-A, R5-G1 | HARD-softening sprzeczny z kanonem C5→rozstrzygnięty | przekieruj oś, nie 2. luzowanie |

#### L6.C — Geometria w selekcji + de-pile (R2 ROOT-7 + ROOT-8) — 🔴⛔ **RAZEM (osobno=no-op)**
> **🔒 Bramka nieprzekraczalna (C10-oracle):** flip `PENDING_RESWEEP_LIVE` (de-pile) BEZ geometrii w `lex_qual` = **279 propozycji spread>8km LIVE**. „P0-A + P0-B RAZEM" (MEMORY). Rida na L2 (pula odbudowana).

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Bramka „zero kopii" |
|---|---|---|---|---|---|
| **L6.C1** | **Dokończ `objm-lexr6-unify`** (R1-C precond): frozen `_lex_qual` shadow `dispatch_pipeline:1122` → import kanonu `objm_lexr6:29` | R1-C | copy-count(klucz selekcji frozen→0) | przepięcie na kanon, nie 2. cień |
| **L6.C2** | **Człon geometrii w `lex_qual`** (R2 ROOT-7, PO osi R6 — INV-LAYER-5): tie-break z JUŻ-serializowanej `deliv_spread_km`/cosine; scal stałe (`R1_MAX`+`BUNDLE_MAX`→1 `MAX_DELIV_SPREAD`, bearing 2→1, cosine 1); usuń martwą R7=99km (R6-K-B) | R2 ROOT-7, R6-K-B | layer-violation(geometria→warstwa decyzyjna); copy(spread/bearing 2→1); dead-code(R7=99)→0 | człon z istniejącej metryki (0 nowego producenta) |
| **L6.C3** | **Engine-level claim ledger + de-pile geom-aware** (R2 ROOT-8, RAZEM z C2): nowe=przerzut wspólny `global_allocate`; sentinel fail-loud (rida L2); flip `PENDING_RESWEEP_LIVE` GATE na C2 | R2 ROOT-8 | twin(nowe↔przerzut)→0; g_maxpile↓; sentinel-swallow→0 | wspólny import `global_allocate`, NIE 2. kopia |

#### L6.D — objm/bug4/frozen-lex (gated 02-03.07) — 🔴
| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Bramka |
|---|---|---|---|---|---|
| **L6.D1** | **objm `peak_verdict` per-decyzja** (R3-E2, gated **03.07** at-200): headline→`reorder_pct` per-decyzja (jak monitor, −zawyżka ×7-11) + golden twin-parity | R3-E2 | twin(peak↔monitor)→0; headline-zawyżka→0 | przepnij na istniejącą metrykę, nie 3. |
| **L6.D2** | **frozen-lexqual `shadow≡canon`** (R1-C, RAZEM z D1 przy POST_SHIFT): golden przy OBU stanach `POST_SHIFT`; przepnij/usuń cień; ujednolić `post_shift_overrun_penalty` 3-sposoby | R1-C | copy-count(klucz-jakości frozen)→0 | −1 frozen, nie 2. cień |
| **L6.D3** | **bug4 reseq re-spec inwariantu** (R3-E3, gated **02.07**): `delta` na objektywie `total_duration/SLA` (nie OSRM-drive); distinct-worki (−migotanie 25%); gate pod nowy inwariant | R3-E3 | inwariant-źle-zdefiniowany 1→0; false-suspect 11,5%→realny | re-spec istniejącego, nie 2. shadow |

#### L6.E — Słownictwo / semantyka (R4) — 🟡→🔴
| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Bramka |
|---|---|---|---|---|---|
| **L6.E1** ⭐ | **`tier` → 4 nazwy** (R4-L2, mechaniczny disarm join-by-tier): `courier_class`/`escalation_level`/`solver_dim`/`gps_tier`; serializowane `esc_tier`→`escalation_level` | R4-L2 | vocab-overload(tier 4→1/oś); serializer-ambiguity | golden ON==OFF, brak nowej osi „tier" |
| **L6.E2** | **TZ `assert-no-naive` w math-layer** (R4-L1, ⛔ACK): zamiast `if None:→UTC` (maskuje +2h) → `assert tzinfo is not None`; ujednolić `_iso`/`_parse_ts`, 1 parser `picked_up_at` | R4-L1 | tz-convention 2→1; twin(2-parsery)→1; dead-guard→fail-loud | nie 3. parser |
| **L6.E3** | enum `order_type` 1-język + sufiks-jednostki + `WARSAW` 6→1 + dual-60 jawne (R4-L4, P3 przy okazji) | R4-L4 | vocab-copy-count; implicit-unit | brak nowej nazwy/spellingu |

**Wkład w dashboard L6:** twin-divergence(route) **44-75/d→0**; copy-count(route 5→1, r6-cap 6→1, anchor 4→1, PICKUP_MERGE 5→1, geom-stałe 2→1, klucz-selekcji frozen→0); threshold-sprawl(r6-cap→1); layer-violation(geometria→warstwa decyzyjna); unresolved-conflict(R6-cap, paczka-exempt); void(objm/bundle/bug4)→validated. **PoC #2 (czasowo-krytyczny, niskie ryzyko):** **L6.A1 route-order golden** (test, nie zmiana zachowania, deadline 07-10). **PoC-rozgrzewka:** L6.E1 tier-rename (zero ryzyka, natychmiastowa redukcja audit-friction).

---

### ▰ L7 — HARDENING / KOHERENCJA (F7) — mieszane 🔴/🟡 + ⛔ACK D5

> **Dlaczego TU (UNIFIED F7 + R7):** RUNTIME-INWARIANTY + rozstrzygnięcia precedencji — „jedyne co blokuje nawrót na zawsze". Po naprawie fundamentu (L0-L6) większość konfliktów-rdzeni (K-A/B/D/E/F/H) już rozbrojona; tu domykane reszta + strażniki anty-nawrót.

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Ryzyko | Bramka „zero kopii" |
|---|---|---|---|---|---|---|
| **L7.1** | **R-DECLARED tripwire** (R7-I-E): `czas_kuriera>=czas_odbioru` fail-loud LOG (obserwacyjny, NIE reject — zgodne always-propose) w chokepoincie committed | R7-I-E | HARD-bez-inwariantu(R-DECLARED)→0 (INV-COH-3) | 🔴 obserw., ACK lekki | 1 tripwire, NIE N assertów |
| **L7.2** | **frozen↔floor precedencja: 1 chokepoint** (R7-I-C + R1-B): `effective_pickup_at=clamp_order(frozen,floor,osrm,debias)`; render apka+konsola czytają chokepoint; **frozen>floor, ALE floor obejmuje committed<shift_start** (Q2b); debias dożywa do żywego eta; floor-flagi=inversion-guard | R7-I-C | unresolved-conflict(frozen↔floor)→0; czas-odbioru 4-clampy→1; copy-clampy | 🔴 cross-repo, ACK (Q1/Q2/Q2b ACK) | 1 chokepoint −4 clampy×5 powierzchni; render czyta, nie re-clampuje |
| **L7.3** | **Split-layer guard** (R2 ROOT-9): INV-LAYER-1/2 re-assert `_assert_feasibility_first` na EMIT (nie 1× @5938); zakaz zapisu `feasibility_verdict` poza L5; `free_at_min` w L4 (soon-free z L6→L4) | R2 ROOT-9 | layer-violation(verdict poza L5 `:6278`)→0; unresolved-conflict(guard↔readmit)→0 | 🔴 (flagi OFF, INERT), ACK | strażnik, nie ścieżka |
| **L7.4** | **feas-carry outcome-join** (R3-E1, PRZED ewentualnym re-flipem #483000): join `gps_delivery_truth`/`decision_outcomes` (harness L0.3); log `newbag` (replay≡LIVE); re-spec etykiety regret | R3-E1 | void(feas-carry×3)→validated/archiwum; twin(blind↔readmit)→0 | 🟡 (flaga OFF), oracle PRZED | 1 join-harness + 1 pole, nie 4. przyrząd |
| **L7.5** | **Concurrency dyscyplina** (R5-O1/O2): 4 RMW no-lock → 1 fcntl LOCK_EX wrapper + unikalny tmp; recheck → per-wątek opener; lying-docstring O3 → prawda | R5-O1, R5-O2 | no-lock-RMW 4→0; thread-shared-session→0; lying-comment→0 | 🔴 **C2-mina: PRZED re-enable Telegrama** | 1 dyscyplina −4 mityacje |
| **L7.6** ⛔ | **Load multi-mechanism** (R7-I-D): sprostuj A3 governor (flags.json:165=ON, A2 błędne OFF) + `FLEET_LOAD_GOVERNOR` w fingerprint; 1 tabela `bag_cap`; **triple-tax PENDING-ACK D5** (measure-first: ile razy potrójna kara odbiera LEPSZEMU) | R7-I-D | flag-drift(A2-A3)→0; bag-cap 2-tabele→1; load undefined→rozstrzygnięte/PENDING | 🟡 doc + ⛔ACK D5 silnik | sprostowanie+1 tabela; reguła-rządząca=ACK |
| **L7.7** | **Nazwa==warstwa** (R4-NB): rename HARD-misnomerów (`PICKUP_SPAN_HARD`→`_PENALTY`, R-RETURN `VETO`→`_METRIC`, `LATE_PICKUP_HARD_GATE`) LUB re-layer (R2) | R4-NB | name-layer-mismatch 3-4→0 | 🟡 ⛔ACK (intencja „VETO") | rename golden ON==OFF |
| **L7.8** | **void-claim hygiene** (R7-I-G): konwencja „void-claim wymaga świeżego grepa master-ledgera"; napraw kierunek `sequential_replay._determine_verdict` (higher-better) | R7-I-G | void-claim-bez-grepa→0; I-inwersja-kierunku→0 | 🟢 nie-silnik | konwencja, nie 4. void-claim |

**Wkład w dashboard L7:** unresolved-conflict(frozen↔floor/R-DECL/load/guard↔readmit) → rozstrzygnięte; layer-violation(split `:6278`)→0; void(feas-carry)→validated; concurrency 4→0; flag-drift→0. **⛔ACK Adriana:** L7.6 D5 (load — measure-first), L7.2 (Q2b już ACK), L7.7 (intencja VETO).

---

### ▰ L8 — OBJAWY USUNIĘTE / SPRZĄTANIE (po GO) — 🟡 low, usuwanie

> **Dlaczego OSTATNIE:** martwy kod/clutter nie biega — szkoda ODROCZONA/POZNAWCZA (mina C2, myli root-cause, zatruwa grep). Usuwane PO rozstrzygnięciu właścicieli (R7=99 po R2; panelsync po R1-PoC). Czysta redukcja entropii audytu.

| # | Krok | Co konsoliduje | Rooty | Metryki ↓ | Ryzyko |
|---|---|---|---|---|---|
| **L8.1** | **Dead-decision-code removal** (R6-K-B): C3-migracja (kwarg+gałąź+producent+stała, **bajt-identyczne**); metryka „kłamie-0" usunięta; kłamiące komentarze (O2 `:139`/speed_tier `:904`); skeletony C4/C6/C7 oznaczone „flip=full-deploy"; R7=99 {usuń∨soft} **po R2 (L6.C2)** | R6-K-B | dead-code-in-decision-path→0; lying-comment→0 | 🟡 bajt-identyczne/doc |
| **L8.2** | **Caches eviction** (R6-H-C): 6 append-only (geocode 3,2MB/dwell 2,3MB/pins/town/ground-truth) → 1 eviction-wrapper (age/LRU/cap); `events.db` VACUUM-timer | R6-H-C | unbounded-cache 6→0 | 🟡 janitor-timer, ACK lekki |
| **L8.3** | **Dead-producer log + test-path** (R6-K-A): `c5_shadow_log` usunięty/oznaczony; `test_wave_scoring` path→tmp KAŻDY caller (TEST-only); orphan-konsumenci (c2/a2) udokumentowani | R6-K-A | dead-producer-log 1→0; test-state-bleed | 🟡 test+state-hygiene |
| **L8.4** | **Repo clutter** (R6-K-C): 326+ `.bak` (egzekwowalna retencja); shift-notify potrójny grób + orphan `.service.d`; epaka misplaced; cod-weekly {fix∨kill}; panelsync **przy L6.A2** | R6-K-C | clutter→0; grep-entropia | 🟡 usuwanie plików (po GO) |
| **L8.5** | **Reszta progów/semantyki** (R3-N1 + R4 F2/F3/L4): helpery 1-źródło (`r27_pickup_tol()` ×5, `score_margin_confident()` ×5, spread-8 z L6.C2, DWELL); lying-doc→docstring-ze-stałej; usuń V325_DROPOFF dead-twin; pair-writer `(coords,address,city)` (R4-F2) + retire `REGEOCODE_SYNC_TEXT`; uwagi derive-point happy∧fallback (R4-F3) | R3-N1, R4-F2/F3/L4 | threshold-copy-count→1; coupled-field-async→0; field-boundary-loss→0; vocab-copy(WARSAW) | 🟡 ⛔ACK (F2 retire-flag po oracle 01.07) |

**Wkład w dashboard L8:** dead-code **→0**, unbounded-cache **6→0**, dead-producer **1→0**, clutter **→0**, threshold-copy-count **→1**, coupled-field-async **→0**.

---

## 3. KALENDARZ BRAMEK CZASOWYCH (nadpisują kolejność lokalnie)

| Data | Bramka | Kroki roadmapy | Zależność |
|---|---|---|---|
| **02.07** | **at-168/at-200 O2-review + bug4 checkpoint + bundle_calib MATERIAL_PCT** | **L6.B (cały)** + L6.D3 (bug4) | L1.1 (serializer odsłania SLA-detail) MUSI być PRZED |
| **03.07** | **at-200 objm peak verdict + frozen-lexqual** | L6.D1 + L6.D2 (RAZEM przy POST_SHIFT) | L0.1 (flag-rejestr — POST_SHIFT znany) |
| **≤ 2026-07-10** | **route-order monitor wygasa** (⚠ ETAP-0 verify exact) + pod-certyfikuje DZIŚ | **L6.A1** (golden-CI PRZED wygaśnięciem) | niezależne od L2-L5 (test, nie zmiana zachowania) → może iść WCZEŚNIE równolegle |
| **04.07** | **load-aware ETA review** (`pickup_slip_monitor`/`prep_bias`) | L5.1 (kalibracja oś poślizgu) | monitor poślizgu LIVE od 29.06 (zbiera dowód) |
| **PENDING** | **D5 load triple-tax** (measure-first → ACK Adriana) | L7.6 | oracle „ile razy potrójna kara odbiera LEPSZEMU" |
| **PENDING** | **C2 re-enable Telegrama** (kiedyś) | **MUSI po L7.5** (fcntl O1 — uzbraja tmp-clobber) | twardy gate |

**⚠ Konflikt kolejności (jawnie):** L6.A1 (route-order golden, **≤07-10**) jest CZASOWO przed L6.B-D, ALE zależnościowo NIE wymaga L2-L5 (to test parytetu, nie zmiana zachowania) → **może iść równolegle z L1-L2** jako tani, czasowo-krytyczny tor. Rekomendacja: **L6.A1 startuje równolegle z L1** (oba niskie ryzyko), reszta L6 po L5.

---

## 4. BRAMKA „ZERO NOWYCH KOPII" — weryfikacja per warstwa (warunek zbieżności)

Każdy krok USUWA powierzchnię/literał/kopię — ŻADEN nie dodaje. Dowód, że roadmapa jest ZBIEŻNA (monotonicznie redukuje entropię):

| Warstwa | USUWA (−) | NIE dodaje (✗) |
|---|---|---|
| L0 | flag-warstwy 3→1 (decyzyjne); 2 martwe-ON; inwersję-maskującą | ✗ nowa flaga poza rejestrem |
| L1 | allowlist→deny (1 mech.); N `open`→1 loader; ≥8 append→1 helper | ✗ 36. prefiks; ✗ N-ty rotation-blind reader; ✗ 7. swallow |
| L2 | 6 def-sentinela→1; 17 łatek→1 chokepoint; truthy→_valid | ✗ 18. powierzchnia coords |
| L3 | zombie 43→0; 1 default param (nie flaga); prune w istniejącym recanon | ✗ 2. GC; ✗ 2. shadow recanon |
| L4 | 17 re-liczeń floor→1; shift_start 2→1; render-clampy USUWANE | ✗ 17. powierzchnia floor; ✗ render-łatka |
| L5 | 1 żywa kalibracja przekierowana + 1 parked włączona; 2-mapy-prep→1 | ✗ 4. mapa; ✗ eta_pickup do renderu |
| L6 | route 5→1; r6-cap 6→1; anchor 4→1; PICKUP_MERGE 5→1; geom-stałe 2→1; frozen-cień→0; panelsync DEAD usunięty | ✗ 6. render; ✗ 7. literał 35/40; ✗ 2. cień |
| L7 | 4 clampy→1 chokepoint; 4 RMW→1 fcntl; verdict-poza-L5→0; 2-tabele-bag-cap→1 | ✗ 17. floor-patch; ✗ 5. mityacja locka |
| L8 | dead-code; clutter; 6 cache→1 wrapper; N progów→1 helper | ✗ „TODO-soft" 3. raz; ✗ N-ty snapshot |

**Reguła zdrowia (samo-zachowawcza, rozszerzenie Przykazania #0):** żaden przyszły sprint NIE pogarsza żadnej z 8 metryk. RED-checki: nowa powierzchnia renderu kolejności/ETA · nowa flaga decyzyjna poza rejestrem · nowe re-liczenie czasu-odbioru bez `available_from` · nowy klucz HARD bez decyzji-widoczności · nowy reader bez rotation/master · nowy `.txt` bez TTL · nowy próg-kopia bez nazwanej-stałej · nowy caller geometrii z `if coords:` · nowa kalibracja luzująca HARD bez outcome-join · nowy plik multi-writer bez fcntl · nowy void-claim bez świeżego grepa.

---

## 5. MAPA 26 ROOTÓW → WARSTWA (gdzie każdy domykany; anty-double-count)

| Root (id z taksonomii) | Sev | Werdykt | Warstwa | Rola |
|---|---|---|---|---|
| `flag-state-3-layer-no-single-source` | P1 | CONFIRMED | **L0.1** | fundament keying-point |
| `carried-first-guard-empty-env-void` | P1 | CONFIRMED | **L0.2** | fix = R1-D 0.3 |
| `serializer-allowlist-metrics-vanish` | P1 | CONFIRMED | **L1.1** ⭐ PoC#1 | odblokowuje O2 |
| `verdict-reader-wrong-stale-source` | P1 | CONFIRMED | **L1.2** | reader-truth |
| `stale-txt-verdict-no-ttl` | P2 | CONFIRMED | **L1.2** (konw.) + L8 | TTL-marker |
| `instrument-append-jsonl-silent-swallow` | P2 | PLAUSIBLE | **L1.3** | 1 fail-loud helper |
| `coord-sentinel→no-ingest-chokepoint` (M2) | P1 | CONFIRMED | **L2.1** most K5 | odbudowa puli |
| `schedule-data-3way-failopen-failclose` | P1 | PLAUSIBLE | **L2.3 + L4.4** | fail-policy |
| `courier-plans-lifecycle` (R6-H-B/K2) | P2 | CONFIRMED | **L3** | plan_recheck-cofacz |
| `earliest-pickup-floor-no-chokepoint` | P1 | CONFIRMED | **L4** (F1) | najgłębsze |
| `calibration-on-wrong-axis` | P1 | CONFIRMED | **L5.1** ⛔ | K3 ETA |
| `one-route-order-module` | P1 | CONFIRMED | **L6.A** ⭐ ≤07-10 | twin route |
| `frozen-lexqual-shadow` | P2 | PLAUSIBLE(src=NIE) | **L6.C1 + L6.D2** | po 03.07 |
| `r6-cap-35-flat-vs-40-tier-plus-quantile` | P1 | CONFIRMED | **L6.B2/B4** | sprint O2 |
| `paczka-r6-exempt-inverted-in-ranking` | P2 | CONFIRMED | **L6.B3** | sprint O2 |
| `geometry-blind-selection` | P1 | CONFIRMED | **L6.C2** ⛔ RAZEM | P0-A |
| `no-global-deconflict-new-order` | P1 | PLAUSIBLE | **L6.C3** ⛔ RAZEM | P0-B |
| `objm-shadow-canary-twins-alltick` | P1 | CONFIRMED | **L6.D1** gated 03.07 | twin objm |
| `bug4-reseq-invariant-misspec` | P1 | CONFIRMED | **L6.D3** gated 02.07 | inwariant |
| `hard-feasibility-split-layer` | P2 | PLAUSIBLE | **L7.3** | guard-na-emit |
| `frozen-committed-vs-preshift-floor` | P1 | PLAUSIBLE | **L7.2** | precedencja clampów |
| `r-declared-time-hard-no-runtime-invariant` | P2 | CONFIRMED | **L7.1** | tripwire |
| `feas-carry-instruments-predict-not-outcome` | P1 | CONFIRMED | **L7.4** | outcome-join |
| `fleet-load-multi-mechanism-tax` | P2 | CONFIRMED | **L7.6** ⛔ D5 | PENDING-ACK |
| `name-vs-behavior-hard-misnomers` | P2 | PLAUSIBLE | **L7.7** | rename |
| `post-shift-replay-validated-vs-void-ADVERSARIAL` | P2 | CONFIRMED(src=NIE) | **L7.8** | hygiene |
| `numeric-threshold-scatter-mixed-override` | P2 | CONFIRMED | **L8.5** | helpery progów |
| `dead-producer-orphan-consumer-shadow-logs` | P2 | CONFIRMED | **L8.3** | dead-producer |
| `instrument-append-jsonl…` / `hard-feasibility-split` (P2 fam) | — | PLAUSIBLE | (L1.3 / L7.3) | (dublet listy) |

> **Anty-double-count:** rooty WSPÓLNE liczone raz w warstwie-primary, cross-ref w pozostałych. `R3-D1/D2`=L0 (R3 wnosi INV-TRUTH, R1 konsolidację). `R6-cap`(R7-I-A)=L6.B, aspekt-N(R3-N1)=L8.5, aspekt-G(quantile)=L5/L6.B4. `paczka`/`anchor`/`floor` na wspólnym `r6_thermal_anchor`/`available_from` (1 helper, wiele rodzin). Sentinel(K5)=L2 rdzeń, mosty w L4/L6.

---

## 6. PoC — rekomendacja (osobny ACK + ETAP 0→7; audyt zostaje read-only)

Wg dźwigni × ryzyka × deadline (zbieżne rekomendacje 7 rodzin):

| # | Kandydat | Warstwa | Dlaczego | Ryzyko |
|---|---|---|---|---|
| **PoC-1** ⭐ | **Serializer-kompletność** (deny-list+test) | L1.1 | Najwyższa dźwignia: odblokowuje 14 HARD + bramkuje O2 02.07; zamyka root RAZ (nie „35. prefiks"); **zero zmiany zachowania LIVE** | 🟡 najniższe (dowód ON≠OFF = klucz HARD w ledgerze) |
| **PoC-2** | **Route-order golden-equivalence CI** | L6.A1 | Czasowo-krytyczny (≤07-10); test parytetu nie zmiana zachowania; mierzy twin-divergence zamiast wygasać | 🟡 niskie (golden test) |
| **PoC-rozgrzewka** | **`tier`→4 nazwy** LUB **fingerprint+2 martwe-ON** | L6.E1 / L0.1 | Mechaniczny, zero ryzyka zachowania, natychmiastowa redukcja audit-friction / fałszywego-parytetu | 🟢 zerowe |

**Rekomendacja-DRAFT:** **PoC-1 (serializer)** jako pierwszy dowód-wykonalności fundamentu prawdy-przyrządów + **PoC-2 (route-order golden)** równolegle (deadline). Oba behavior-neutral → bezpieczna walidacja całego protokołu ETAP 0→7 przed dotknięciem decyzji.

---

## 7. CAVEATY / GRANICE / CO NIE ROZSTRZYGNIĘTE (jawnie, nie cisza)

1. **Magnitudy = Faza C oracle, NIE ten dokument** (read-only). Roadmapa deklaruje ŚCIEŻKĘ konsolidacji + kolejność zależności; ile worków/dzień każdy root realnie psuje (geom-ślepy pick, committed<shift_start, triple-tax, quantile would-pass) = replay/oracle PRZED każdym flipem (dowód POZYTYWNEGO wpływu, nie tylko brak-regresji — UNIFIED §5/ETAP 5).
2. **Data 2026-07-10 (route-order monitor) NIE potwierdzona moim grepem DZIŚ** — `ziomek-time-route-monitor.service` ISTNIEJE i pod-certyfikuje (B22-J6 hardkod `trust_canon_ok=True`), ale exact `MONITOR_STOP_AFTER` nie surfował → **ETAP-0 verify**. Niezależnie od daty: golden-CI > pod-certyfikujący monitor (uzasadnienie L6.A1 stoi samo).
3. **Numery linii DRYFUJĄ** (≥3 sesje/dzień/repo) — zweryfikowane DZIŚ HEAD `8024705` (`available_from`=0 · floor-guard=0 · `BAG_TIME_HARD_MAX=35` common.py:763 · `BEST_EFFORT_OBJM..=40` :2651 · `O2_OVERAGE=35` :2661 · `R6_MAX_MIN=35` bundle_calib:56 · `r6_cap_for_tier`=∅ · `_AUTO_PROP_PREFIXES` shadow_dispatcher:190/272 · `eta_source`=0/2000 ledger · `gc_invalidated` 0 prod-callerów). **Każdy fix re-grepuje (ETAP 0).**
4. **Sprzężenia RAZEM (osobno=no-op/SZKODA):** L6.C2+C3 (geometria+de-pile, C10-oracle: bez geometrii 279 złych propozycji) · L6.B cały (flip O2 rusza 6+4 sites) · L6.D1+D2 (objm+frozen-lex POST_SHIFT) · L4.4+L5.1 (feasibility-wyklucza na load-aware buforze) · L7.5 PRZED re-enable Telegrama (C2-mina).
5. **PENDING-ACK Adriana (NIE zgaduj):** L7.6 D5 (load triple-tax: rekalibracja vs jeden rządzący — measure-first) · L4.4/L7.2 polityka fail grafiku open/close · L7.7 intencja „VETO" (rename vs realnie-wetować) · L6.B4 quantile (usuń vs alarm-gate). Decyzje Q1/Q2/Q1b/Q2b (floor) JUŻ ACK 30.06.
6. **Granice zakresu:** STOP na dyspozytorni — Mailek/Papu poza. Cross-repo (konsola/apka/parcel-lane) liczone TYLKO w route-order/floor/ETA/R6 (granica zachowana). Apka Kotlin lokalny re-sort/ETA + most paczki = A6 luki #1/#2 (render serwerowy pokryty, lokalna kopia niezweryfikowana — Faza B/J).
7. **Kontrast UNIFIED F1-F7 vs warstwy L0-L8:** UNIFIED stawia F6(strażniki)→F3(sentinele) wcześnie; rodziny R1/R3 stawiają flag-rejestr jako FAZA-0. **Rozstrzygnięcie:** flag-rejestr (L0.1) jest PREREQ strażników (L0.5) — strażnik z pustym env kłamie (carried-guard); więc {flag-rejestr + harness + strażniki} = JEDEN fundament L0, potem F3 sentinele (L2). Zgodne z obiema lekturami (różne wejścia, ten sam fundament).
8. **To DRAFT syntezy, nie wykonanie** — żaden flip/edit/restart. Wszystkie cele 8 metryk = 0/1 (DESIGN §4), nie deklaracja osiągalności w 1 sprincie. PoC = osobny ACK po akceptacji targetu.

---

> **DRAFT — koniec.** Roadmapa = JEDNA zbieżna droga: **fundament-wiarygodności (L0) → prawda-przyrządów (L1) → sentinel-chokepoint (L2,F3) → plan_recheck (L3,F2) → dostępność (L4,F1) → ETA (L5,F4) → kanon+bliźniaki (L6,F5,date-gated) → hardening (L7,F7) → sprzątanie (L8).** Najgłębsze „nigdy nie wraca" = L4+L5+L0.5 (F1+F4+F6). Każdy krok redukuje ≥1 z 8 metryk entropii, żaden nie dodaje kopii. Napraw FUNDAMENT raz → oba objawy (alokacja + pre-shift) znikają, przyszłe tej rodziny prewencyjnie zablokowane. Wykonanie = osobny ACK + ETAP 0→7 per krok.
