# B09 — KLASA G (kalibracja na ZŁEJ OSI) — backing

**Agent:** B09-G-calibration-axis · **lane B** · **READ-ONLY** · 2026-06-30 ~14:1x UTC · HEAD `8024705`.
**Teza klasy G:** strojenie tam, gdzie błędu NIE MA (noga-jazdy / delivery-pesymizm z próby matched-courier), a OŚ gdzie błąd realnie siedzi (poślizg odbioru + prep-bias) jest OFF / shadow / pod-wymiarowana. Każda kalibracja → czy celuje w oś gdzie błąd realnie siedzi (join ground-truth: `pickup_slip_monitor`, `restaurant_prep_bias.json`, decyzja 29.06).
**Numery linii ze świeżego grepu dziś. Dedup: cała rodzina G zwija się do K3 (optymistyczny poślizg) + R3 (SLA/R6 anchor) z audytu zunifikowanego — NIE liczyć każdej flagi jako osobny chaos.**

---

## 0. GROUND TRUTH — gdzie błąd FIZYCZNIE siedzi (oś prawdy)

| Oś | Pomiar (świeży) | Znak | Źródło |
|---|---|---|---|
| **Poślizg ODBIORU** (assign→pickup) | **ciasno/solo med +27.4** (n=43), srednio/solo +17.7, luzno/solo +6.2; bundle 3-10 | **DODATNI=optymistyczny** (silnik zakłada odbiór ZA WCZEŚNIE), rośnie z load | `dispatch_state/pickup_slip_monitor.jsonl` (29.06 23:26, n=684) |
| **Prep-bias** (deklaracja ready vs real) | global bias_med peak_lunch **+12**, high_risk **+13**, all +11; **p80 +24-27, p90 +32-35**, n=25912 | DODATNI (jedzenie gotowe później niż deklaracja) | `dispatch_state/restaurant_prep_bias.json` (FRESH 30.06 04:15) |
| **Noga JAZDY** (OSRM leg→real) | **~0 błędu** (29.06: „noga jazdy ~0; wcześniejsze 2× OSRM = ZŁA kolumna ref") | ~0 | decyzja 29.06 [[ziomek-calibration-2026-06-29]] |
| **drive_min backfill** (assign→pickup) | median **+12.9**, 69.2% under-est >5min, n=3013 | DODATNI (pod-szacowane) | `drive_min_calibration.py:7-8` |

**Wniosek osi:** błąd siedzi na ODBIORZE (poślizg +18..+27) i PREP (+11..+13), OBA DODATNIE (silnik OPTYMISTYCZNY → realny bag_time DŁUŻSZY niż liczy). Noga jazdy ≈ czysta.

---

## 1. INSTANCJE (plik:linia świeży)

### G-1 ★ P1 LIVE — `ENABLE_ETA_QUANTILE_R6_BAGCAP` LUZUJE HARD-R6 na osi delivery-pesymizmu z próby SELEKCYJNEJ
- **flaga LIVE:** `flags.json ENABLE_ETA_QUANTILE_R6_BAGCAP=True` (zmierzone dziś) — mimo `common.py:236 ENABLE_ETA_QUANTILE_R6_BAGCAP=False` (const) → **flags.json nadpisuje na True**. (A3 §2d miało rację; RECON i const mówiły OFF — sam ten rozjazd to D-smell.)
- **konsumpcja HARD:** `feasibility_v2.py:1088-1100` — `_gate_bt = bag_time_min`; jeśli `ENABLE_ETA_QUANTILE_R6_BAGCAP` ∧ `courier_tier=='gold'` ∧ `len(bag)+1<=4` → `_c = eta_quantile_calibrate(bag_time_min, now=anchor, quantile="p80")`; `_gate_bt=_c`. Bramka `if _gate_bt > 35` (`:1105`) → przy `_c<=35` worek przechodzi mimo surowego >35. `metrics["r6_gold4_gate_recovered"]` (`:1098`).
- **mapa = oś delivery-pesymizmu, próba SELEKCYJNA:** generator `tools/eta_quantile_calib.py:29` „DEFAULT tylko `matched_courier=True`" + własny caveat `:30` „pary unmatched mieszają szum selekcji"; premisa `:3-6` „ETA pesymistyczna, mediana bias pred 30-40→-10, 40+→-25". Mapa LIVE `eta_quantile_map.json` (FRESH 30.06 04:35): bucket 25-30 **bias_med -8.8**, 30-40 **-11.2** (p80=**33.0**), 40+ **-24.1**. → gold-worek surowo 38min ⇒ p80=33 ⇒ **R6 odzyskany (przechodzi)**, oddane 5min budżetu świeżości.
- **oś = ZŁA:** `bag_time_min` i `predicted_delivery_min` mapy to TA SAMA oś ready→delivered (`route_simulator_v2.py:732,769` `_compute_per_order_delivery_minutes` = ready-anchor = `r6_thermal_anchor`) — więc NIE unit-mismatch, lecz **mapa mówi „skróć", a fizyka (poślizg +18-27, prep +11-13, OBA DODATNIE) mówi „realny bag_time DŁUŻSZY"**. „Pesymizm" mapy = artefakt matched-courier (silnik commituje tylko kurierów o pasującym ETA; wśród dowiezionych nad-reprezentowani ci o pesymistycznym ETA). Luzowanie HARD-świeżości sygnałem delivery-optymizmu = kalibracja na złej osi.
- **MAGNITUDA luzowania:** `r6_breach_shadow.jsonl` ostatnie 5000 R6_HARD_REJECT: **1621 (32,4%) `would_pass_calibrated=True`** (kalibracja przerzuciłaby co trzeci reject na PASS). Gold within_tier_cap≤4: 1155, z tego 66 would_pass. Live scope (gold≤4) ogranicza blast, ale dźwignia zbudowana na całej 1/3.
- **kind: SOURCE.** still_open: TAK (LIVE). **dedup → K3/R3.**

### G-2 P1 — efekt LIVE-gate G-1 NIEWIDOCZNY w master-ledgerze (kłamiący/brak przyrządu)
- `feasibility_v2.py:1098` ustawia `metrics["r6_gold4_gate_recovered"]`, ale **0 wystąpień w `shadow_dispatcher.py` serializerze ORAZ 0 w całym `logs/shadow_decisions.jsonl`** (grep dziś). LIVE bramka zmieniająca decyzję R6 nie ma licznika w księdze, którą A4 nazywa MASTER LEDGER → nie da się zaudytować jak często luzuje na żywo. Jedyny ślad = `r6_breach_shadow.would_pass_calibrated` (kontrfaktyk na rejectach, NIE realne recovery).
- ⚠ **ANOMALIA do oracle (PLAUSIBLE):** 66 rekordów gold∧within_tier_cap≤4∧would_pass_calibrated w `r6_breach_shadow` to przypadki, które LIVE-gate POWINIEN odzyskać (gold,≤4,cal≤35) a mimo to zalogowane jako R6_HARD_REJECT → rozjazd shadow-recompute (`worst_bt`/`bag_total`) vs live-gate (`bag_time_min` per-order/`len(bag)+1`). Wymaga trace Fazy C (czy live gate realnie odpala, czy shadow liczy inną wielkość).
- **kind: SYMPTOM** (na G-1). still_open: TAK. **dedup → K3/R3.**

### G-3 ★ P2 PARKED-LANDMINE — `DRIVE_SPEED_MULT_BY_TIER<1.0` kalibruje oś o ~0 błędu, luzuje R6/feasibility
- `common.py:2188` `DRIVE_SPEED_MULT_BY_TIER = {gold:0.78, std+:0.82, std:0.82, slow:1.0, new:1.0}`; `:2197 speed_mult_for_tier` bramka `:2207 ENABLE_DRIVE_SPEED_TIER_CORRECTION` (**flags.json=False, OFF**).
- **droga luzowania potwierdzona:** `feasibility_v2.py:811` `_drive_speed_mult=C.speed_mult_for_tier(courier_tier)` → `:818 simulate_bag_route_v2(... drive_speed_mult=_drive_speed_mult)` (R6-plan!) + `route_simulator_v2.py:408 leg_min = (dur_s/60)*drive_speed_mult` (WSZYSTKIE nogi, w t.cz. dojazd-do-odbioru) + `plan_recheck.py:667` bliźniak. ON ⇒ nogi −18..22% ⇒ bag_time krótszy ⇒ R6 luźniejszy.
- **oś ZŁA + podwójnie:** noga jazdy ~0 błędu (29.06), więc −22% to strojenie osi bez błędu; nogi obejmują też dojazd-do-odbioru który jest POD-szacowany (poślizg) → skracanie pogłębia lukę. Sam tool werdyktu nazywa ryzyko: `tools/drive_speed_overshoot_verdict.py:5-7` „za mocno ściśnięte ETA → optymizm → kurier dostarcza PÓŹNIEJ + feasibility/R6 przepuszcza za długi worek → realny breach/zimne jedzenie". Premisa `common.py:2181-2187` „model zawyżał czas jazdy" + baseline bias −4,7 = TA SAMA miara matched-courier, którą 29.06 obaliło. Flip ON 26.06 17:25 → **rollback po ~15min** 17:40 (`:32-36`).
- **kind: SOURCE (parked).** still_open: TAK (stałe uzbrojone, jeden flip = systemowy optymizm na osi bez błędu). **dedup → K3.**

### G-4 ★ P1 — oś REALNEGO błędu (poślizg odbioru) NIE-skorygowana live: PICKUP_DEBIAS płaski 4,5 vs zmierzone 18-27 + drive_min_calibration OFF
- `common.py:3131 PICKUP_DEBIAS_MIN = 4.5` (komentarz `:3124-3130` „czas_kuriera optymistyczny ~4.5min, med 4.3 OOS 10 dni"). Konsumpcja **SHADOW-only:** `shadow_dispatcher.py:562-566` za `ENABLE_PICKUP_DEBIAS_SHADOW` (flags.json True=shadow), „ZERO zmiany decyzji/committed ck" (`:3129`).
- **pod-wymiar 4-6×:** zmierzony poślizg `pickup_slip_monitor` ciasno/solo **+27.4**, srednio/solo **+17.7** → płaski 4,5 koryguje 4-6× za mało, na DODATEK tylko w shadow. Kalibracja zrobiona na innym (mniejszym, czystym 10-dniowym czas_kuriera↔dwell) sygnale niż realny load-zależny poślizg.
- **prawdziwa oś PARKED:** `drive_min_calibration.py:52 OFFSET_TABLE` (assign→pickup = oś poślizgu): `pre_shift +15.3, gps +35.1, last_assigned_pickup +30.9, last_picked_up_pickup +34.7, post_wave +30.9`; median backfill +12.9 (`:7-8`). Bramka `ENABLE_DRIVE_MIN_CALIBRATION_V2=False` (main OFF, shadow ON; `auto_proximity_classifier.py:285`). To JEDYNA tabela na właściwej osi — i jest OFF.
- **asymetria osi:** oś gdzie błąd FIZYCZNIE siedzi (poślizg) = OFF/shadow/pod-wymiar; osie luzujące (G-1 delivery-pesymizm LIVE, G-3 jazda parked) = zbudowane/żywe. To jest klasa G w czystej postaci.
- **kind: SOURCE.** still_open: TAK. **dedup → K3.**

### G-5 P2 — `ENABLE_PREP_BIAS_TABLE` OFF: oś prep (+11..+13 med, p90 +32) NIE-skorygowana w kotwicy R6
- `common.py:2061 ENABLE_PREP_BIAS_TABLE` (flags.json=False); konsumpcja `feasibility_v2.py:1063-1078` (anchor shift gdy ON) + `prep_bias_anchor.py`. Komentarz `:1056-1060` projektuje korektę jako GATE-STRICTER („kotwica WCZEŚNIEJ → bag_time rośnie → R6 bije wcześniej, NIGDY bardziej liberalna; bias ujemny→0") = właściwy (zachowawczy) kierunek.
- **dane potwierdzają oś:** `restaurant_prep_bias.json` (FRESH 30.06): global bias_med +11..+13, p80 +24-27, p90 +32-35, n=25912 — realny, duży bias prep. Korekta na właściwej osi i właściwym kierunku — **wyłączona**, podczas gdy luzowanie (G-1) żywe.
- ⚠ **DWA tory prep-bias / D-smell:** feasibility czyta `prep_bias_anchor` z `dispatch_state/prep_bias_table.json` (STALE 20.06 09:20!), a shadow `shadow_dispatcher.py:519 calib_maps.prep_bias_for` z `restaurant_prep_bias.json` (FRESH 30.06). Dwie różne mapy/ścieżki tej samej osi (A1) — gdyby PREP_BIAS_TABLE flipnięto, feasibility czytałoby ANTYK (20.06).
- **kind: SOURCE (parked).** still_open: TAK. **dedup → K3 (prep-oś) + A1 (dwie mapy).**

### G-6 P3 — `repo_cost` SOFT parked (oś dystansu, NIE ETA) — w zakresie, NIE wrong-axis defect
- `dispatch_pipeline.py:2088 _repo_cost_penalty` (`-REPO_COST_MAX_PENALTY*min(1,km/scale)`, max 30 @ ≥4km) + `:2108 _compute_repo_cost_km` (haversine last_drop→new_pickup). Bramka `ENABLE_REPO_COST_LIVE=False`/`_SHADOW=True` (`:5226/4986`). To SOFT-kara dystansu dead-head, nie kalibracja ETA → **NIE klasa G wrong-axis**; odnotowane bo w przydziale. Magnituda ~-27 @ mediana floty 3,56km (komentarz `:2093`) = znaczna gdyby LIVE, ale to inny temat (geometria SOFT), nie oś-ETA. Wzmianka cross: `_compute_repo_cost_km:2147` guarda `not drop_coords` ale NIE `(0,0)` (most do K5/sentineli — patrz agent sentineli, NIE liczyć tu).
- **kind: SYMPTOM/obs.** still_open: NIE (parked, poza klasą G). **dedup → (poza G).**

---

## 2. SYNTEZA — DLACZEGO TO JEDEN ROOT (anty-double-count, Faza E)

Wszystkie LIVE/żywe kalibracje SKRACAJĄ ETA na osi o niskim/zerowym błędzie albo selekcyjnie-zatrutej (G-1 delivery-pesymizm matched-courier; G-3 jazda) → luzują sufit R6/feasibility. Wszystkie kalibracje na FIZYCZNEJ osi błędu (G-4 poślizg odbioru: PICKUP_DEBIAS, drive_min_calibration; G-5 prep) są OFF/shadow/pod-wymiarowane. **Net efekt = silnik systematycznie OPTYMISTYCZNY na R6 (jedzenie siedzi dłużej niż liczy), a jedyne ŻYWE strojenie pogłębia optymizm.** To dokładnie K3 (optymistyczny poślizg) z audytu zunifikowanego + R3 (SLA/R6 anchor) — NIE pięć osobnych chaosów.

**Sprzężenie z R3/SLA-anchor (A6 grupa 4, Faza D):** G-1 luzuje R6 ready-anchored mapą delivery-axis; współprojekt z `ENABLE_PACZKA_R6_THERMAL_EXEMPT` i niespójnym SLA-anchorem (feasibility na pickup_at vs R6 ready). O2 review 02.07 (at#168/#200) MUSI rozstrzygnąć łącznie: anchor + eta_quantile bagcap + prep-bias, inaczej luzowanie i pod-korekta pracują przeciw sobie na tej samej decyzji.

---

## 3. TABELA POKRYCIA

| Moduł / przyrząd / flaga | Zbadane? | Oś | Stan | Werdykt G |
|---|---|---|---|---|
| `feasibility_v2.py:1088-1100` ETA_QUANTILE_R6_BAGCAP | ✅ | delivery-pesymizm (selekcyjna) | **LIVE** | G-1 wrong-axis loosening |
| `calib_maps.eta_quantile_calibrate:126` + `eta_quantile_map.json` | ✅ (mapa odczytana) | delivery ready→deliv | LIVE shadow + R6 bagcap | G-1 |
| `tools/eta_quantile_calib.py` (generator) | ✅ | matched_courier only | cron 04:35 | G-1 (selection bias źródłowo) |
| `r6_gold4_gate_recovered` serializacja | ✅ | — | **0 w ledgerze** | G-2 invisible |
| `r6_breach_shadow.would_pass_calibrated` | ✅ (5000 sampli) | — | 32,4% flip | G-1 magnituda |
| `common.py:2188 DRIVE_SPEED_MULT_BY_TIER` + `speed_mult_for_tier` | ✅ | jazda (~0 błędu) | OFF parked | G-3 landmine |
| `feasibility_v2.py:811/818` + `route_simulator_v2.py:408` + `plan_recheck.py:667` drive_speed_mult wiring | ✅ | jazda→R6-plan | parked | G-3 droga luzowania |
| `tools/drive_speed_overshoot_verdict.py` | ✅ (header) | — | N/A (flaga OFF) | G-3 ryzyko nazwane |
| `common.py:3131 PICKUP_DEBIAS_MIN=4.5` + `shadow_dispatcher.py:562` | ✅ | odbiór (poślizg) | SHADOW only | G-4 pod-wymiar 4-6× |
| `drive_min_calibration.py:52 OFFSET_TABLE` | ✅ | assign→pickup (poślizg) | OFF main | G-4 prawdziwa oś parked |
| `pickup_slip_monitor.jsonl` (ground-truth poślizg) | ✅ (n=684) | odbiór | FRESH 29.06 | oś prawdy |
| `common.py:2061 ENABLE_PREP_BIAS_TABLE` + `prep_bias_anchor.py` + `feasibility_v2.py:1063` | ✅ | prep | OFF | G-5 |
| `restaurant_prep_bias.json` vs `prep_bias_table.json` | ✅ (oba) | prep | FRESH vs STALE 20.06 | G-5 dwie mapy/D-smell |
| `dispatch_pipeline.py:2088/2108 repo_cost` | ✅ | dystans SOFT | shadow | G-6 poza-G |
| `dispatch_pipeline.py:5283 travel_min_cal` (eta_quantile shadow) | ✅ | delivery | shadow (ENABLE_ETA_QUANTILE_LIVE absent=OFF) | część G-1 (shadow tor) |

### Luki pokrycia (jawne)
- **`r6_gold4_gate_recovered` live-fire trace** — NIE zrobiony (read-only; G-2 anomalia 66-rekordów = PLAUSIBLE, wymaga Fazy C oracle: czy live gate realnie odpala vs shadow liczy `worst_bt` zamiast per-order `bag_time_min`).
- **`predicted_delivery_min` ≡ `bag_time_min` bajtowo** — potwierdzone z lektury (oba ready-anchor, `route_simulator_v2.py:732`), NIE zwalidowane runtime joinem (Faza C).
- **prep_bias_anchor.anchor_shift_min znak/kierunek** — komentarz mówi gate-stricter, NIE prześledziłem ciała `prep_bias_anchor.py` linia-po-linii pod znak (czy +shift faktycznie rośnie bag_time). Zaufanie do komentarza feasibility_v2.py:1056-1060.
- **eta_r3 residual** (`eta_residual_infer`, ENABLE_ETA_R3_SHADOW) — shadow-only, NIE prześwietlony pod oś (poza budżetem; deklarowany shadow w A4).
- **Cross-repo render kalibracji** (konsola `_eta_chain`, apka `_attach_fallback_eta`) — czy renderują skalibrowane czy surowe ETA = poza klasą G (to G byłoby kalibracją; render to L10), zostawione agentowi cross-repo/J.
- **LGBM ETA** — shadow, poza rdzeniem.
