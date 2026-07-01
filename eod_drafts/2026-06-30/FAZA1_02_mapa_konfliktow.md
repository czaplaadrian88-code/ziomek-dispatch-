# FAZA 1 — DELIVERABLE #2: MAPA KONFLIKTÓW (co się z czym bije + status precedencji)

**Audyt spójności Ziomka · sesja tmux 2 · 2026-06-30 · TRYB READ-ONLY.** Oś **I (koherencja)** — graf interakcji reguł/flag z 5 agentów Fazy D (D01 graf reguł, D02 precedencja-ścieżek, D03 sprzężenia-flag, D04 stos-równości, D05 frozen/floor/thermal).

> To oś, której poprzednie audyty NIE miały. Bezpośrednia odpowiedź na „wszystko walczy ze sobą".
> **81 par konfliktowych · 64 problematyczne** = **35 defined-inconsistent** (precedencja zdefiniowana ale niespójna) + **15 silent-inversion** (zachowanie cicho odwrócone) + **14 undefined** (kto wygrywa = nie wiadomo). Pozostałe 17 (14 defined-consistent + 3 ok) = precedencja zdrowa.
> Po light-dedupie konwergentnych powtórzeń (5 agentów trafiło w te same węzły) → **~13 KLASTRÓW konfliktu**. Faza E sformalizuje je jako rooty koherencji; Faza F da kontrakt precedencji.

---

## ⭐ 13 KLASTRÓW KONFLIKTU (zdedupowane — to jest „co walczy ze sobą")

| # | Klastra | Status | Ile agentów | Klasa |
|---|---|---|---|---|
| **K-A** | **R6 dwie kotwice: thermal `pickup_ready_at` vs SLA `pickup_at`** | undefined/inconsistent | 5 | A1·I·N |
| **K-B** | **R6 próg 35 vs 40 (płaski-vs-tier) — rozsyp w 6 stałych** | defined-inconsistent | 4 | N·I |
| **K-C** | **`ETA_QUANTILE_R6_BAGCAP` luzuje HARD-R6** (p80 gold≤4) | inwersja HARD↔SOFT | 3 | G·I |
| **K-D** | **pre-shift floor: feasibility clamp vs `plan_recheck` regen BEZ floor** (K2 „cofacz") | defined-inconsistent | 4 | B·C·H |
| **K-E** | **equal-treatment (silnik UNIFIED) vs out-of-engine gates (DIVERGED)** — 8 bliźniaków pozycji | sprzeczność+asymetria | 3 | B·I |
| **K-F** | **frozen-R27 committed broni złego czasu vs pre-shift floor** (floor omija frozen) | silent-inversion | 3 | F·I |
| **K-G** | **`_assert_feasibility_first` (P0 HARD) vs `FEAS_CARRY_READMIT` (bypass po guardzie)** | silent-inversion (#10) | 2 | C·I |
| **K-H** | **geometria-rozjazdu SOFT-only vs `lex_qual` czysto-czasowy** (P0-A) | inwersja+undefined | 2 | C·I |
| **K-I** | **`COMMIT_DIVERGENCE_VERDICT_GATE`: const=True maskowany flags.json=False** — jedyna prawdziwa inwersja json↔const | silent-inversion | 1 (D5) | D·I |
| **K-J** | **R-DECLARED-TIME deklarowana HARD, BEZ runtime-inwariantu** | undefined | 3 | I + brak-inwariantu |
| **K-K** | **podwójne reguły OBCIĄŻENIA: `FLEET_LOAD_BALANCE` (V326 ON) vs `FLEET_LOAD_GOVERNOR` (SP-B2, ON-przez-flags.json)** | inconsistent+sprzężenie | 3 | I·N |
| **K-L** | **nazwa-HARD vs zachowanie-SOFT:** `R_RETURN_TO_RESTAURANT_VETO` (metric-only), `LATE_PICKUP_HARD_GATE` (=SELEKCJA-tier) | inconsistent (mylące L) | 3 | L·I |
| **K-M** | **kanon reguł SAM ze sobą sprzeczny:** `REGULY_KANON §4:86 'No-GPS=ZAWSZE równo'` vs `§7:151 'pre-shift −20 żywe'` (+ żywa FAR-veto −1000 dla pre_shift>30min) | sprzeczność wewnątrz-dokumentu | 2 | I |

**Wniosek dla Adriana („wszystko walczy"):** to NIE 81 niezależnych wojen — to ~13 węzłów, a 6 z nich (K-A, K-B, K-D, K-E, K-F, K-H) zbiega się do TYCH SAMYCH korzeni co rodziny alokacji/pre-shift (jedno-źródło reguły + kotwica + warstwa). Naprawa fundamentu rozbraja większość.

---

## 🔴 SILENT-INVERSION (15) — cicha inwersja: zachowanie odwrócone bez jawnej decyzji

Najgroźniejsze — reset flagi / re-enable = regres bez zmiany kodu.

- **`COMMIT_DIVERGENCE_VERDICT_GATE` const=True (common.py:2805-6) ⟷ flags.json:148=False maskuje** → effective False. Usunięcie klucza z flags.json → `decision_flag` spada na const=True → **cichy FLIP na ON = utrata always-propose** (KOORD-redirect wraca). _(4 agentów — jedyna prawdziwa inwersja json↔const.)_
- **frozen-R27 committed NIETYKALNY (route_simulator_v2:1086, courier_orders:872, fleet_state:519) ⟷ pre-shift floor `pickup≥shift_start` (feasibility_v2:798, fleet_state:857)** → floor żyje na ścieżce OSRM/departure, frozen ją omija (plan_pv wybrany przed osrm[i]) → **floor = no-op gdy committed<shift_start; frozen AKTYWNIE broni złego pre-shift czasu** (czasówka/elastyk committed pre-shift). _(K-F)_
- **`_assert_feasibility_first` P0 INV (dispatch_pipeline:5938, fail-loud „żaden verdict=NO w puli") ⟷ `FEAS_CARRY_READMIT` (:6266, flags.json=false) promuje verdict=NO→MAYBE na top[0] ZA guardem (pop+insert :6278)** — HARD-bypass po guardzie (wzorzec #10). _(K-G; dziś latentne bo flaga OFF.)_
- **warm-up −20 (V325_PRE_SHIFT_SOFT_PENALTY feasibility_v2:763) ⟷ EQUAL_NO_PENALTY gate (dispatch_pipeline:5108→zeruje :2447)** — kara liczona U ŹRÓDŁA, zerowana flagą downstream — NIE usunięta (suppression-na-żywej-karze). _(K-M)_
- **equal-bucket/demote-exclusion oś POZYCJI (`_is_demotable_blind_empty` :2473 no_gps/:2475 pre_shift→False) ⟷ V3.16 `_demote_blind_empty` oś OBCIĄŻENIA (pusty bag s_obciazenie≈100, baseline ~82)** — flaga osi-pozycji wyłącza ochronę osi-OBCIĄŻENIA (gate sklejał blind∧empty=2 osie) → **regresja V3.16 demote tylnymi drzwiami** (pusty bag może wygrać z realnym GPS). _(K-E, oś krzyżowa.)_
- **`objm_lexr6.lex_qual` 4-krotka (POST_SHIFT ON) ⟷ `_objm_lexr6_shadow._lex_qual` 3-krotka FROZEN (at#152)** — zgodne TYLKO bo POST_SHIFT OFF (wiodące 0.0 no-op); flip C7 rozjedzie cień vs live-selekcję (kłamiący przyrząd E#15). _(K-H pokrewne.)_
- **`PACZKA_R6_THERMAL_EXEMPT` 3 HARD-site (feasibility_v2:1050-55, :1152-55) ⟷ O2-objektyw + SLA-count BEZ exempt (`_count_sla_violations`, `_o2_key`)** — bramka zwalnia paczkę z R6, ale ranking/objektyw liczy ją jako spóźnioną → **exempt ODWRÓCONY w warstwie selekcji.** _(K-A pokrewne.)_
- **SLA-gate zdominowana (luźniejsza niż R6, rzadko bije pierwsza, „wygląda martwa") ⟷ jej produkt uboczny `plan.sla_violations` (optymistyczny pickup_at) przecieka do rankingu O2** → kanon rankowany na kotwicy którą R6-gate (feasibility_v2:1009) nazywa BŁĘDNĄ. _(K-A — semantyka.)_
- **no-GPS/pre_shift=RÓWNO (HARD-zasada Adrian C3) ⟷ kod-default=dyskryminuj pozycję** — równość trzymana 3 flagami, kod-default = stan PRZED inwersją → reset flags.json wskrzesza dyskryminację. _(K-E.)_
- **`PRE_SHIFT_EQUAL_NO_PENALTY` (zeruje karę) ⟷ `V325_PRE_SHIFT_SOFT_PENALTY=-20` (stała ŻYWA w kodzie)** + **`EQUAL_TREATMENT_BUCKET` ⟷ `NO_GPS_EQUAL_TREATMENT`** (jedna reguła, dwie flagi; zgodne tylko bo obie ON). _(K-E/K-M.)_

## 🟠 DEFINED-INCONSISTENT (35→repr.) — precedencja zdefiniowana ale NIESPÓJNA

Ta sama reguła egzekwowana różnie (różne progi/kotwice/warstwy). _Pokazane reprezentatywne, konwergentne powtórzenia scalone:_

- **K-A — DWIE HARD-bramki „spóźnienia", różna kotwica:** R6-thermal anchor=`pickup_ready_at` (route_simulator_v2:663, INV-R6-ANCHOR, konsument feasibility_v2:1046) ⟷ SLA anchor=`plan.pickup_at` (`_count_sla_violations` route_simulator_v2:648 + lustro feasibility_v2:1156). Oba próg 35, oba HARD. SLA optymistyczny (TSP-projected gdy kurier zajęty), R6 ostry (ready). `_count_sla_violations` NIE woła `r6_thermal_anchor` mimo docstringu „JEDNO źródło". + asymetria paczka-exempt (feas SLA ma, `_count_sla` NIE). _(5 agentów.)_
- **K-B — R6 próg w 6 stałych:** `BAG_TIME_HARD_MAX_MIN=35` (feasibility HARD-reject) / `DEFAULT_SLA_MINUTES=35` / `C2_PER_ORDER_THRESHOLD_MIN=35` / `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` (best_effort dopuszcza) / `O2_OVERAGE_CAP_MIN`+`O2_CAP_Z_MIN=35` / `bundle_calib` flat-35 (over-penalizuje T3). Kandydat carry 38min: feasibility=NO ale best_effort go bierze (always-propose). Kanon C5: „40=ALARM-only auto dla WSZYSTKICH". _(4 agentów.)_
- **K-C — `ETA_QUANTILE_R6_BAGCAP` (flags.json:179=true, LIVE) rozluźnia HARD R6** dla gold+bag≤4 na `_gate_bt=p80` → >35 ready-anchored PRZECHODZI (feasibility_v2:1089-97); SLA-loop + inne tiery = legacy hard-35, NIE co-designowane. D3 explicit: USUŃ. _(3 agentów.)_
- **K-D — pre-shift floor:** feasibility HARD clamp `earliest_departure=max(now,shift_start)` (ENABLE_PRE_SHIFT_DEPARTURE_CLAMP ON, feasibility_v2:789-819) ⟷ `plan_recheck` regen courier_plans co 5min, kotwica TYLKO committed BEZ shift_start (plan_recheck:534/554) → **faza B cofa fazę A (K2 „cofacz"), czas SAM SIĘ ODCLAMPOWUJE co tick.** _(4-5 agentów.)_
- **K-E — równość vs dyskryminacja:** engine equal-treatment (`_selection_bucket`:2451, 7 twins) ⟷ out-of-engine gates: `reassignment_forward_shadow._SYNTH_POS`+`a_late` (:64,260, 59% fałszywych ratunków) + `auto_assign_gate G7 pos_not_informed` (:164) + `feed.py` bez `_pos_trusted`. Engine UNIFIED, gates DIVERGED → „klasa wraca ≥4×". + V3.16 `_demote_blind_empty` (:2504) używa własnych klasyfikatorów `_is_blind_empty_cand`/`_is_informed_cand`, NIE `_selection_bucket` → przeciwne kierunki.
- **K-K — podwójny load:** `ENABLE_V326_FLEET_LOAD_BALANCE` ON (dispatch_pipeline:1462, ±15 score) ⟷ `ENABLE_FLEET_LOAD_GOVERNOR` EFEKTYWNY ON (flags.json:165=true NADPISUJE const OFF common.py:2103, `bonus_loadgov_shadow_delta -40 LIVE`). Dwie reguły load naraz. + governor rozluźnia okno committed-pickup (load≥4.5 → 5→10min) modyfikując SOFT R27.
- **K-L — nazwa-HARD vs zachowanie-SOFT:** `R_RETURN_TO_RESTAURANT_VETO` (flags.json:86=true) = feasibility_v2:905 metric-only „NIGDY nie przerywa feasibility", realny zakaz w `NO_RETURN_TO_DEPARTED_PICKUP` (plan_recheck:1518, inna warstwa+flaga). `LATE_PICKUP_HARD_GATE` ON + `LATE_PICKUP_HARD_MAX_MIN=5` (common.py:2822) — nazwa HARD, zachowanie = SELEKCJA-tier demote (dispatch_pipeline:5655 „NIE hard-reject").
- **`CARRIED_FIRST_RELAX` OFF w dispatch-shadow ⟷ ON w plan-recheck/panel-watcher/b-route** — złamany parytet procesów (asymetria env per-serwis).
- **always-propose:** KOORD-gates guarded `and not _always_propose_on()` (dispatch_pipeline:6491/6864/6900) ⟷ `geometry_blind_fallback` zwraca KOORD BEZ tego checka (:6453) — asymetria pokrycia.
- **`OR_TOOLS_TSP` ON ⟷ `SAME_RESTAURANT_GROUPING` ON (sprzężone)** — flip OR-Tools OFF bez GROUPING OFF = double-insert super-pickupa w legacy greedy.
- **`R6_DANGER_ZONE_PENALTY` (-24/min strefa 32-35, ON) ⟷ `R6_SOFT_PEN_CAP` (cap, const False)** — bez capa kara eksploduje do -240000.
- **konsola floor `CLAMP_PRESHIFT_PICKUP_ETA` ON (fleet_state:857) ⟷ apka `_attach_fallback_eta`/`_compute_live_eta` BRAK floor** — konsola floruje, apka nie (asymetria bliźniaków).
- **`APP_ROUTE_FROM_CONSOLE` ON (courier_orders:1116) ⟷ `BUILD_VIEW_TRUST_CANON_ORDER` konsumowana za short-circuitem (:1120)** — flaga ON ale NIEOSIĄGALNA (C5 near-miss).
- **kanon §4:86 „No-GPS=ZAWSZE równo" ⟷ żywa `PRE_SHIFT_FAR_PEN −1000` (dispatch_pipeline:2443) dla pre_shift>30min** — deklarowana absolutna równość vs żywa kara.

## 🟡 UNDEFINED (14→repr.) — precedencja NIEZDEFINIOWANA (kto wygrywa = nie wiadomo)

- **K-J — R-DECLARED-TIME (HARD declared, najwyższy priorytet 22.04, „czas_kuriera≥czas_odbioru zawsze") ⟷ R27/R6/ready-anchor/czasówka (egzekutorzy POŚREDNI)** — żadna warstwa NIE sprawdza nierówności jako bramki; tylko komentarze (common.py:3494, dispatch_pipeline:3168). Przyszła zmiana R27 cicho złamie bez tripwire. _(3 agentów.)_
- **K-H — geometria SOFT-only (R1 spread>8km NIE rejectuje, feasibility_v2:501-547 metric-only) ⟷ `lex_qual` czysto-czasowy (objm_lexr6:29) ZERO osi geometrii; best_effort score NIE czyta** — geometria nie ma jak pobić czasu pod scarcity (P0-A). _(2 agentów.)_
- **kanon kolejności silnik `_apply_canon_order_invariants:1478` ⟷ konsola `fleet_state._build_route:395` (TRUST_CANON:443) ⟷ apka `courier_orders.build_view:1072` (APP_ROUTE/BUILD_VIEW_TRUST_CANON)** — brak wspólnego importu repo↔repo; 3 flagi TRUST_CANON w 3 systemach flag, parytet tylko statystyczny (monitor). _(K-A route — J.)_
- **early-bird/czasówka≥60 KOORD ORDER-LEVEL przed pulą (dispatch_pipeline:3503, anchor raw `pickup_at_warsaw`) ⟷ pre-shift floor COURIER-LEVEL w feasibility (anchor `pickup_ready_at` vs shift_start)** — early-bird zwiera obwód przed pulą → floor NIGDY nie biegnie dla ≥60-ahead; po release czasówki (T-60/50/40) committed<shift_start wpada w K-F (frozen). 3 różne kotwice czasu.
- **META — brak jednego chokepointu clampów:** precedencja 4 clampów `frozen(R27) > floor(shift_start) > OSRM` — każda powierzchnia bierze INNY podzbiór i kolejność; `debias PICKUP_DEBIAS_MIN=4.5` shadow-only (sierota) NIE dożywa do żywego floor/render → **floor floruje SUROWY optymistyczny estymat (~18min slip wg kalib. 29.06).**
- **podwójny load undefined:** `FLEET_LOAD_BALANCE` (ON) + LOADGOV (ON, −40 LIVE) + stopover `bonus_r9` + bug4-cap — 2-3 mechanizmy tego samego pojęcia, potrójna kara możliwa (odebrać zlecenie LEPSZEMU obciążonemu); która rządzi = nieokreślone.
- **`R6_DANGER_ZONE_PENALTY` const ON (getattr common.py:774) ⟷ ABSENT w flags.json + poza flag_fingerprint** — kara czytana getattr z const NIE `C.flag()`; operator nie wyłączy hot, niewidoczna w fingerprincie (dryf-flag env-frozen).
- **trójca flag równości (NO_GPS_EQUAL + EQUAL_BUCKET + PRE_SHIFT_EQUAL) ⟷ każda gated osobno (penalty-zero / bucket / demote-excl — RÓŻNE mechanizmy)** — częściowy flip = częściowa/niespójna równość.

## 🟢 DEFINED-CONSISTENT / OK (17) — precedencja zdrowa (NIE wymaga naprawy)

14 par z jasną, spójną precedencją (HARD-przed-SOFT egzekwowane `_assert_feasibility_first`; R6-tier-aware by-design; always-propose→sentinel framing; bundle-coloc; czasówka⟺prep≥60 u źródła) + 3 oznaczone wprost OK. Dla kompletności ledger — szczegóły w `backing/D01_rule_graph.md`...`D05_frozen_floor_thermal.md`.

---

## STATUS / CAVEATY
- **DRAFT do przeglądu Adriana.** Numery linii ze świeżego grepu Fazy D (DRYFUJĄ — re-grep przed zmianą).
- **Adversarial verify (Faza E/WF3) w toku** — część konfliktów może zejść do PLAUSIBLE/REFUTED (np. „dziś latentne bo flaga OFF" jak K-G). Tu zaraportowane jako wykryte; przefiltrowane rooty w Deliverable #1.
- **Pełne dowody plik:linia per konflikt:** `backing/D01..D05_*.md` + `backing/WF2_DIGEST.md` (sekcja FAZA D).
