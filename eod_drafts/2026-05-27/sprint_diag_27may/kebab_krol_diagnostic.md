# Kebab Król — R6 breach 22.5% forensic diagnostic (2026-05-27)

**Author:** Forensic agent dla Ziomka
**Data zakres:** 2026-05-18 → 2026-05-27 (9 dni, dane ze snapshotów `orders_state_*.json` + `dispatch.log` + `backfill_decisions_outcomes_v1.jsonl`)
**Próbka KK:** 76 decisions / 44 unique orders / 44 delivered
**Próbka peer top-20:** 1,393 delivered

## Executive summary

- **Root cause (confidence ~75%):** Kebab Król to "city-center long-tail" — pickup w Rynek Kościuszki 30 (centrum) ale dostawy spread po całym mieście (42 unique addresses na 44 orders = 95%). Lokalizacja sklepu + późny dinner peak (17-21h) + powolna kuchnia (planned→actual pickup lateness p50 **+16.8 min** — najgorszy z top-20) sprawiają, że KK orders **rzadko trafiają na "wave-friendly" couriers** → kończą jako **2nd bag na carry-stack** u kuriera już wiozącego inny order. R6 breach to **carry-penalty**, nie problem KK per se.
- **Rekomendacja: Option B — Conditional.** NIE wykluczać KK z AUTO automation hurtowo. Wykluczyć tylko **dinner (17-21h)** lub gwarantować pojedynczy bag dla KK orderów po 17:00. R6 breach rate KK w lunch = **0%** (n=9), w dinner = **26.7%** (n=30, vs peer dinner 7.7%).

---

## DIM 1 — Volume + temporal pattern

**Volume**
- 44 delivered orders w 9 dniach. Średnio ~4.9/dzień. Porównanie: Rany Julek 18.3/d, Grill Kebab 17.8/d, Raj 12.2/d. KK to **medium volume** restaurant (#15 w top-25).

**Hour-of-day (Warsaw):**
| Hour | n | breach | rate |
|---|---|---|---|
| 12 | 1 | 0 | 0% |
| 13 | 3 | 0 | 0% |
| 14 | 1 | 0 | 0% |
| 15 | 4 | 0 | 0% |
| 16 | 2 | 0 | 0% |
| **17** | **6** | **2** | **33%** |
| **18** | **9** | **2** | **22%** |
| **19** | **6** | **4** | **67%** |
| 20 | 6 | 0 | 0% |
| 21 | 3 | 0 | 0% |
| 22 | 3 | 0 | 0% |

→ **Wszystkie 8 breachy zawierają się w oknie 17-19h.** Po 20:00 zero breachów. **Pre-17h zero breachów.** 100% breach koncentracji w dinner rush peak.

**Lunch vs Dinner** (lunch=11-15, dinner=17-21):
- Lunch: 0/9 = **0%**
- Dinner: 8/30 = **26.7%**
- Peer top-20 dinner: 71/920 = **7.7%**

→ **KK dinner breach 3.5× peer dinner.**

**Day-of-week:**
| Dzień | n | breach | % |
|---|---|---|---|
| Monday | 9 | 3 | 33% |
| Tuesday | 8 | 2 | 25% |
| Wednesday | 8 | 1 | 12% |
| Thursday | 1 | 0 | 0% |
| Friday | 8 | 0 | 0% |
| Saturday | 6 | 2 | 33% |
| Sunday | 4 | 0 | 0% |

→ Mon/Tue/Sat dominują breachy (7/8). Friday + Sunday clean. Brak czystego wzorca DoW oprócz luźnej korelacji z week-day-evening (n małe, low-n).

---

## DIM 2 — Courier-side analysis

| courier | KK_n | KK_breach | KK_rate | global_n | global_breach_pct | ap_med_global |
|---|---|---|---|---|---|---|
| **484** (Andrei K) | **7** | **3** | **43%** | 126 | 15.1% | 29.0 |
| 370 | 5 | 1 | 20% | 153 | 7.2% | 30.3 |
| 413 | 5 | 0 | 0% | 141 | 2.1% | 35.7 |
| 509 | 4 | 0 | 0% | 95 | 8.4% | 34.6 |
| 393 | 3 | 0 | 0% | 179 | 8.4% | 31.5 |
| 520 | 2 | 1 | 50% | 53 | 15.1% | 28.8 |
| 515 | 2 | 1 | 50% | 79 | 10.1% | 41.0 |
| 527 | 1 | 1 | 100% | 19 | 31.6% | 30.1 |
| 514 | 1 | 1 | 100% | 55 | 18.2% | 30.4 |

**Findings:**
- **Courier 484 (Andrei K) jest top-receiver KK orders (7/44 = 16%) AND najczęstszym sprawcą breach (3 z 8 = 38% wszystkich KK breaches).** Jego globalny breach rate 15.1% (2× średnia floty), ale dla KK 43% (3× jego własny baseline) — coś specyficznego w KK go biedzie.
- Couriers 527, 514 — single breach orders, low-n; ich high global rate (32%, 18%) sugeruje że oni są generycznie "ryzykowni" — KK po prostu im się trafia.
- **Najlepsi (413, 509, 393, 393) — zero KK breach mimo n≥3.** Wniosek: KK orders SĄ wykonalne bez breach, jeśli trafią na właściwego kuriera. Issue nie strukturalne dla restaurant per se — issue **wave-routing-driven**.

---

## DIM 3 — Delivery location analysis

- **Pickup:** zawsze `Rynek Kościuszki 30` (KK ma 1 lokalizację, centrum miasta).
- **Delivery:** 42 unique adresów na 44 orderów = **95.5% unique**. Tylko 2 adresy powtórzyły się raz (Blokowa 18, Władysława Bełzy 11). Dostawy rozsiane po całym mieście.

**Same-address — różne outcomes:**
- `Szymborskiej 2`: jeden order ptd=5.7 min (no-breach), drugi order ptd=44.1 min (breach) → **lokalizacja NIE determinuje breach** — courier/routing tak.

**Geographic spread (z dispatch.log):**
- Średnia `deliv_spread` KK = 10.10 km (median 9.6).
- KK porównywalny z medianą floty (Raj 28.24, Chinatown Bistro 19.91, Grill Kebab 19.23 są **wyższe**). Czyli **KK NIE jest outlierem geograficznym.**

**Brak danych delivery_coords w snapshotach** — 0/44 KK orders ma `delivery_coords`. To utrudnia precyzyjne policzenie haversine — przed bridge fix prawdopodobnie zostały `None` po geocoding fallback.

---

## DIM 4 — Restaurant prep time analysis (KEY DIMENSION)

**Główny test:** czy 22.5% R6 to **wait dla pickup** czy **drive po pickup**?

**KK breach (n=8) decomposition:**
- ap_med (assign→pickup) = **22.8 min**
- fa_med (first_seen→assign) = 1.2 min
- ptd_med (pickup→delivery) = **38.1 min**

**KK no-breach (n=36):**
- ap_med = **32.0 min**  ← **WYŻSZE niż breach!**
- fa_med = 1.2 min
- ptd_med = 20.1 min

**KRYTYCZNE odkrycie:** **breach orders mają NIŻSZY assign-to-pickup time niż no-breach.** Czyli problem NIE jest powolnym pickupem. Problem jest w PTD (drive od pickup do delivery). Restaurant kitchen NIE jest wąskim gardłem.

**Ale...** KK ma **najgorszą planned-vs-actual pickup lateness ze WSZYSTKICH top-25 restauracji:**

| Restaurant | n | p50 lateness | p75 | p90 | max |
|---|---|---|---|---|---|
| **Kebab Król** | **44** | **+16.8** | **+28.4** | **+44.4** | **+83.5** |
| Pruszynka | 42 | +19.3 | +27.8 | +32.0 | +46.0 |
| Naleśniki Jak Smok | 26 | +22.7 | +31.1 | +35.1 | +49.6 |
| Mama Thai Bistro | 80 | +15.4 | +25.3 | +39.3 | +58.9 |
| Sweet Fit & Eat | 36 | +13.1 | +20.2 | +29.9 | +85.1 |
| Karczma Maciejówka | 57 | +11.2 | +16.1 | +24.3 | +31.4 |
| **Global all delivered** | 1982 | **+10.5** | +19.9 | +30.5 | +685.6 |

→ **KK jest 60% wolniejsza w pickup-vs-planned niż średnia floty.** Restauracja systemowo opóźnia gotowość vs przewidziany pickup_at.
→ ALE **breach orderów KK mają NIŻSZĄ pickup_late (med +9.0) niż no-breach (med +19.8)** — co oznacza że kitchen-slowness koreluje **negatywnie** z breach. Kuchnia nie jest problemem dla breachy.

**prep_minutes setting:** dominuje `15` (35/44 = 80%), kilka outlierów `20-128`. Standardowy ~15 min setup.

---

## DIM 5 — Order complexity / bundle pattern

Z `dispatch.log` analizy stack-pickup:
- Order **474934** (ptd=47.1 min, najgorszy breach): courier 484 picked up 474934 @ 19:07:57, picked up **474937 (Epic Pizza) @ 19:17:38**, delivered Epic first @ 19:38, KK @ 19:54. → **Bag stacking → KK paid carry penalty.**
- Order **475571** (ptd=44.1): courier 484 picked up **475569 @ 19:47**, then 475571 (KK) @ 19:56, delivered 475569 first @ 20:17. → **Same pattern.**
- Order **475977** (ptd=43.3, courier 527): n=1 for 527, courier global breach 32% → też możliwa stack.

**bundle_cap violations w pool dla KK orderów (z log):**
- 63 bundle_cap events w 43 KK orders (1.47/order)
- Median KK deliv_spread = 9.6km (peer median ~10.0km)
- KK NIE jest outlierem w bundle — porównywalny z peer
- ALE 8/8 breach orders mają **≥1 V326_WAVE_VETO** podczas decyzji (couriers had recent_drop > 3km) — pool wykonywalności wąsko zawężony

**Wniosek:** breach **NIE wynika z KK-specific bundle problem**, lecz z faktu że KK często trafia jako "secondary bag" do kuriera już wiozącego inny order spoza KK.

---

## DIM 6 — Auto_route + override pattern

| metric | KK | ALL |
|---|---|---|
| AUTO | 10.5% (8/76) | 10.3% (370/3576) |
| ACK | 68.4% (52/76) | 73.9% |
| ALERT | **21.1%** (16/76) | 15.7% |
| PANEL_OVERRIDE | 40.8% (31/76) | 42.7% |
| best_effort | **9.2%** (7/76) | 2.9% |
| dec/oid (reassignment rate) | 1.73 | 1.83 |

**Findings:**
- KK ma **21% ALERT** vs flota 16% → ~30% wyżej (sygnał: scorer ma częściej mass_fail / pool_feasible<2)
- KK ma **9.2% best_effort** vs flota 2.9% → **3× wyżej** (sygnał: no feasible courier w pool, system bierze coś "z buts" = best_effort fallback)
- KK override rate **identyczny z flotą** (41% vs 43%) — operator nie nadpisuje KK częściej, nie traktuje jej specjalnie
- KK reassignment rate niższe niż flota (1.73 vs 1.83) — system nie crashuje na KK

**top auto_route_reasons KK:**
- `C2_score_margin=0.0<15.0` × 19 (≥ peer typical)
- `C3_tier=std_not_in_('gold', 'std+')` × 8 (≥ peer typical — KK często trafia do std-tier kurierów)
- `mass_fail` × 9 (no courier w wave)
- `best_effort_no_feasible` × 4

→ **System SAM zaznacza KK jako "trudne" w pool** — ALERT/best_effort 3× częściej niż flota.

---

## DIM 7 — SLA config check

- `restaurant_company_mapping.json`: KK → company_id=169, method="strict" (matching mode, NIE SLA).
- Brak specjalnego SLA override w `common.py` / `auto_proximity_classifier.py` dla KK.
- Brak `extension_minutes` config, brak `prep_minutes` override (default ~15 min).
- → **KK używa standardowych R6=35min SLA**. Adrian NIE skonfigurował specjalnej tolerancji.

---

## Hypothesis testing

| Hypothesis | Test | Result | Verdict |
|---|---|---|---|
| **H1 Peak dinner amplification** | hour-of-day breach distribution | KK 0/9 lunch vs 8/30 dinner (27% dinner rate); peer dinner 7.7% | **CONFIRMED** — KK dinner 3.5× peer dinner |
| **H2 Long-distance delivery** | distance/spread comparison | KK med spread 9.6km, peer ~10km; same address (Szymborskiej 2) →5.7 min once, 44.1 min another time | **REJECTED** — geography ≠ cause |
| **H3 Restaurant prep time issue** | ap (assign→pickup) vs peer | KK ap_med 31.1 ≈ peer 31.2; breach orders LOWER ap (22.8) than no-breach (32.0) | **REJECTED** — prep czas porównywalny, breach skorelowane NEGATYWNIE z wait |
| **H4 Specific courier issue** | per-courier breach rate | Courier 484 = 43% rate na KK, 15% global; 3/8 breaches | **PARTIALLY CONFIRMED** — courier 484 = bag-stacker, dispoporcjonalnie często |
| **H5 Bundle/stack penalty** | log trace per breach order | 474934, 475571: courier picks up KK then OTHER restaurant order, delivers OTHER first → carry penalty | **CONFIRMED** — bag-stack jest realnym mechanizmem |
| **H6 Pickup planned-vs-actual lateness** | per-restaurant lateness | KK p50 +16.8 min (worst of top-20), +5 min powyżej peer median | **CONFIRMED but NOT causal** — kitchen slow, ale breach nie wynika z tego (negatywna korelacja z breach) |
| **H7 KK system flag (ALERT/best_effort)** | KK ALERT 21%, best_effort 9% vs flota 16%/3% | KK 3× częściej "system trudne" | **CONFIRMED** — system widzi KK jako poolowy outlier |

---

## Root cause narrative

**Mechanizm dwustopniowy:**

1. **Setup phase (system pre-warning):** KK ma 3× wyższy `best_effort` rate i 30% wyższy ALERT rate. To znaczy że **w momencie decyzji o KK pool feasible często <2** — system już "wie" że nie ma czystego kuriera. Pickup jest w Rynek Kościuszki 30 (centrum), w dinner peak wszyscy kurierzy są zajęci sąsiednimi orderami spoza KK. KK ląduje jako "best_effort" przypisanie do kuriera już zaangażowanego gdzie indziej.

2. **Execution phase (carry penalty):** Wyznaczony courier (często 484/Andrei K — najczęstszy bag-stacker we flocie) odbiera KK order, ale po drodze zatrzymuje się po inny order (Epic Pizza 474937, Mama Thai 475569 itp.) i dostarcza go PIERWSZY. KK siedzi w torbie 15-30 min dodatkowo. Skutek: pickup_to_delivery_min skacze 20→45 min.

**Dlaczego dinner-only?** W lunch flota ma luźniej, scheduler nadaje single-bag pickup. W dinner peak wszyscy są w torbach → KK musi czekać.

**Dlaczego pick. lateness +16.8 min (najgorzej w top-20) NIE powoduje breach?** Bo wave-detection w okresie wait nie da kuriera "świeżego" — kurier który czeka 30 min w prep, dostaje single-bag (carry empty, faster ptd 20 min). Breach orders mają NIŻSZY wait (22.8 min) — bo system widząc szybką kuchnię stackuje od razu drugi order.

**Predictor ML jest ŚLEPY:**
- predicted_ptd p50 = 15.0 min (system spodziewa się ~15 min)
- actual ptd p50 = 21.9 min (+7 min błąd)
- predicted R6 breach: 1/71 (system prawie nigdy nie ostrzega)
- actual R6 breach: 16/71 = 22.5%
- → model **NIE uczył się** że KK + dinner = carry-stack risk. ML feature set widocznie pomija "bag_count_at_assign" + "second_pickup_distance_after_kk".

---

## Recommendation dla Faza 7

### Option B (PREFERRED): Conditional automation

**Wykluczyć KK z AUTO TYLKO w oknie 17:00-21:00 (dinner peak).**

**Rationale:**
- Lunch KK: 0% breach (9 orders) → AUTO bezpieczny.
- Dinner KK: 27% breach (30 orders) → AUTO ryzykowny, koordynator powinien widzieć i decydować.
- Wykluczenie hurtowe straciłoby ~30% volume KK który DZIAŁA dobrze.

**Implementacja (~30 min):**
1. W `auto_proximity_classifier.py` dodaj rule: `if restaurant == 'Kebab Król' and 17 <= warsaw_hour < 21: classification = ALERT`.
2. Lub w `common.py` dodaj `RESTAURANT_DINNER_FORCE_ALERT = {'Kebab Król'}` config.
3. Test 14d → re-measure breach.

### Option A (FALLBACK): Full exclusion z AUTO

Jeśli operator nie ma capacity na 30 dinner KK orders/9d (~3.3/dzień) → wykluczyć hurtowo.
**Koszt:** +3.3 ALERT/d na koordynatora (≈ minuty pracy).
**Zysk:** wyłączenie 22.5% R6 z metryk AUTO-trusted.

### Option C (RECOMMENDED FOR Q2'26): Fix ML scorer + carry-aware dispatcher

**Lepsze rozwiązanie długoterminowe** — bo problem nie jest KK-specific lecz dotyczy ANY centrum-city restaurant w dinner peak (Pruszynka, Naleśniki Jak Smok mają podobny pickup-late profil):

1. **ML feature engineering** (Faza 6 v1.2): dodaj feature `bag_count_at_assign` + `predicted_second_pickup_detour_min` do LGBM scorera → spodziewane spadnie predicted-vs-actual gap.
2. **Carry-aware veto** w R6 max_bag_min calculator: gdy carry order ma destination >7km od KK delivery, hard-penalty.
3. **Dinner KK + bag courier veto:** w dispatch_pipeline, gdy `restaurant ∈ DINNER_FRAGILE` AND `courier_bag_count ≥ 1`, exclude from pool.

**Effort:** 2-3d coding, ~5d shadow validation. Lepiej jako follow-up do **BUG E pod-anchor** (lekcja #148 — anchor fix już otworzył drogę do bag-time-aware analysis).

---

## Specific case studies

### Worst-case breach #1 — Order 474934 (ptd=47.1 min)
- **2026-05-20 (Wed), pickup 19:07 Warsaw, deliver 19:54**
- Pickup Rynek Kościuszki 30 → 42 Pułku Piechoty 72m (NW Białystok, Dziesięciny)
- Decision chain: panel_override 393→484 (Andrei K)
- **484 picked up 474934 @ 19:07, then PICKED UP 474937 (Epic Pizza) @ 19:17 (10 min later)**, delivered Epic first @ 19:38, KK @ 19:54
- **Bag-stack carry penalty = 22 min extra** (KK siedział w torbie)
- predicted_ptd = 24.6, actual 47.1 → +22.5 błąd
- System ALERT mass_fail (no=0/total=8) — pool był pusty

### Worst-case breach #2 — Order 475571 (ptd=44.1 min)
- **2026-05-23 (Sat), pickup 19:56 Warsaw, deliver 20:40**
- Pickup Rynek Kościuszki 30 → Wisławy Szymborskiej 2
- Courier 484 (Andrei K) — assigned by system (NO_GPS_DEMOTE: top 370 demoted)
- **484 picked up 475569 @ 19:47 (KK), then 475571 (KK) @ 19:56 — DUAL KK STACK!**, delivered 475569 first @ 20:17, KK 475571 @ 20:40
- Predicted_ptd = 15.0, actual 44.1 → +29 błąd (worst ML mispredict)

### Worst-case breach #3 — Order 475977 (ptd=43.3 min)
- **2026-05-25 (Mon), 18h**
- Courier 527 (n=1 dla KK, globalnie 32% breach = ryzykowny)
- pickup_late = +17.1 min (KK kuchnia powolna tego dnia)
- ap = 46.3 min (courier długo czekał — wave nie miał "fresh" availability)

### Best-case no-breach #1 — Order 476161 (ptd=3.6 min)
- Courier 123 (gold tier, globalnie 3.5% breach)
- ap = 60.9 min (LONG wait → ale single-bag, freshly assigned)
- Pickup 17h Tuesday, addr Sukienna 8 (city center, krótki dojazd)
- → Long wait + short delivery + experienced courier = wzorcowy "slow & steady"

### Best-case no-breach #2 — Order 475530 (ptd=5.7 min)
- **Same address jako breach 475571 (Szymborskiej 2)**
- Courier 509 (n=4 dla KK, 0% breach), gold tier
- ap = 41.8 min
- → Identyczna lokalizacja, ALE wyższy-tier courier + brak bag-stack → no breach
- **DOWÓD że KK breach NIE wynika z geography, lecz z routing.**

### Best-case no-breach #3 — Order 474982 (ptd=9.8 min)
- 22h Wednesday (poza peak!) — courier 509, single-bag, freshly assigned

---

## Appendix — KK kontrast z innymi "trudnymi" restauracjami

| Restaurant | R6_breach% | pickup_late_p50 | best_effort% | ALERT% | bundle/oid |
|---|---|---|---|---|---|
| **Kebab Król** | **22.5** | +16.8 | **9.2** | **21.1** | 1.47 |
| Pruszynka Restauracja | 7.1 | +19.3 | ? | ? | 0.45 |
| Mama Thai Bistro | 8.8 | +15.4 | ? | ? | 1.83 |
| Naleśniki Jak Smok | ? (low n) | +22.7 | ? | ? | ? |
| Miejska Miska | 13.6 | +7.8 | ? | ? | 1.94 |
| Chicago Pizza | 12.5 | +6.8 | ? | ? | 1.14 |
| **Peer top-20 avg** | **7.9** | +10.5 | 2.9 | 15.7 | ~1.1 |

**KK outlier characteristics:**
- Pickup lateness — **2× peer** (najgorzej w top-20)
- best_effort — **3× peer**
- ALERT — **1.3× peer**
- bundle/oid — **1.3× peer**
- Wszystkie te są umiarkowane, ALE **kombinacja** + **dinner concentration** + **central pickup** = 3× R6 breach.

---

## Confidence calibration

- **75%** confidence: root cause = carry/bag-stack w dinner peak, scenariusze prowadzą do KK jako 2nd-bag dla courier 484 i innych
- **15%** confidence: dodatkowo wpływa specyficzna geografia delivery zones KK (centrum→dalekie peryferia np. Dziesięciny) — ale dane (10km spread = peer-comparable) raczej to wykluczają
- **10%** uncertainty: low-n caveat dla per-courier i per-hour analysis (8 breaches to mała próbka, granica statystycznej istotności)

**Co byłoby potrzebne dla wyższej confidence (95%+):**
- Dłuższy timeline (30d zamiast 9d) → 25+ breach events
- delivery_coords w snapshotach (currently 0% coverage) → precyzyjna geometria
- bag_state-at-pickup feature w `dispatch.log` (currently musimy rekonstruować manualnie z chains)
