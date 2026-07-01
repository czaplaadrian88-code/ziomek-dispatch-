# B17 — KLASA N: ROZSYP PROGÓW / WARTOŚCI KONFIG (threshold sprawl)

**Agent:** B17-N-threshold-sprawl · **Lane B** · **Faza 1 audyt spójności Ziomka** · **TRYB READ-ONLY** · sesja tmux 2 · 2026-06-30
**HEAD:** `8024705` (świeży `git log -1`). **Working tree silnika czysty.** Wszystkie `plik:linia` ze ŚWIEŻEGO grepu DZIŚ — linie DRYFUJĄ (≥3 żywe sesje), każdy konsument re-grepuje.

**Definicja klasy N (z taksonomii 15 klas):** ta sama liczba progowa/decyzyjna występuje w N miejscach z RÓŻNYMI wartościami lub RÓŻNĄ ścieżką override (const-literał vs env `os.environ.get` vs flags.json hot), albo magic-number bez nazwy. Skutek: zmiana reguły wymaga edycji N miejsc w lockstep; gdy 1 site jest hot-tunable a reszta zamrożona → CICHY rozjazd po flipie/strojeniu.

**Zależność od Fazy A:** A2 już oznaczył smell N #3 (R6 35 płaski vs tier-40 vs bundle_calib flat) + SLA-anchor niespójność. A3 zinwentaryzował `FLAGS_JSON_NUMERIC_OVERRIDES` (25 kluczy) jako "rejestr bez wartości efektywnych — OS dla klasy N". Ten dok ROZWIJA: pełne rodziny progów + ścieżki override + zmierzone wartości. NIE re-derywuję frozen-lex_qual / route-order / pozycji (inne lane).

---

## 0. TL;DR — 10 rodzin + meta

| # | Rodzina (pojęcie) | # sites | wartości | override-paths | sev | root |
|---|---|---|---|---|---|---|
| **N1** | R6 cap świeżości "≤35/40 min" | **6** | 35 / 40 / 35 / 35 / 35-flat / p80 | bare + env + flags.json-hot + instr-local | **P1** | K1 (A2 smell #3) |
| **N2** | "czasówka boundary = 60 min" | **6** | 60 ×6 | 1 hot-tunable + 5 bare/inline | **P1** | K1 + I (silent-divergence po flipie) |
| **N3** | pre-shift floor "30/20/60" | **5+** | 30 / 30 / 60 / -20 / 3.5 | bare + env (mieszane) | P2 | K1 (link preshift-audit) |
| **N4** | committed pickup window "±5" | **5** | 5 / 5 / 5 / 5 / 10 | bare + env (mieszane) | P2 | K1 (R27) |
| **N5** | bag-cap-per-tier (DWIE macierze) | **3** | std 4 vs 5, slow 3 vs 4 | env + env + flaga | P2 | K1/I (która wygrywa) |
| **N6** | dropoff-after-shift "5 min" | **2** | 5 / 5 (jedna MARTWA) | bare + bare | P2 | K1 + K (dead twin) |
| **N7** | DWELL fallback (twin const) | **2** | 1.0/3.5 vs 1.0/3.5 | bare + bare | P3 | K1 |
| **N8** | scoring.py wait docstring↔const | **1+doc** | doc 5/6/20/-5 ≠ const 3/-/15/-8 | — | P2 | E/L (lying-doc) |
| **N9** | "margin = 15" magic | **5** | 15 ×5 | flags.json + hardcode | P3 | N-magic |
| **N10** | "8.0 km deliv spread" | **2-3** | 8.0 ×3 | bare + env | P3 | K1 |
| **META** | brak jednej ścieżki override | — | — | bare/env/flags.json-hot wymieszane | P2 | D+N |

**Najważniejsze (P1):** N1 (R6 35-flat-feasibility vs 40-hot-best_effort — najważniejsza HARD-reguła ma najmniej tunable-bazę i hot-tunable wyjątek = mogą się rozjechać) + N2 (czasówka=60 hot-tunable w 1 z 6 miejsc → bump `EARLY_BIRD_THRESHOLD_MIN` w flags.json cicho desynchronizuje klasyfikację czasówki).

---

## N1 — R6 cap świeżości "≤35 / 40 min" — 6 SITES, 4 ŚCIEŻKI OVERRIDE  ★ NAJWAŻNIEJSZA

Reguła R6: "jedzenie dowiezione ≤35 min od gotowości" (HARD, `Z` taksonomii: tier-aware 35 T1/2, 40 T3). Liczona 6 RÓŻNYMI sposobami z RÓŻNYMI wartościami i ścieżkami override:

| # | symbol | wartość | plik:linia | override | warstwa/rola |
|---|---|---|---|---|---|
| 1 | `BAG_TIME_HARD_MAX_MIN` | **35** | common.py:763 | **BARE literał** (brak env, brak flags.json) | HARD gate feasibility (`C.BAG_TIME_HARD_MAX_MIN`) |
| 2 | `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN` | **40** | common.py:2651 | **env + flags.json:205 (HOT)** | best_effort/objm cap-stretch T3 |
| 3 | `O2_OVERAGE_CAP_MIN` | **35** | common.py:2661 | **env** (brak w flags.json) | objektyw O2 overage |
| 4 | `O2_CAP_Z_MIN` | **35** | common.py:2662 | **env** | sufit świeżości niesionego (carry) |
| 5 | `R6_MAX_MIN` (bundle_calib) | **35.0** | tools/bundle_calib_shadow.py:56 | **literał lokalny modułu** | instrument-werdykt (flip 02.07) |
| 6 | ETA-quantile p80 gold≤4 | (p80, NIE 35) | feasibility_v2.py:1089-1093 | flaga `ENABLE_ETA_QUANTILE_R6_BAGCAP` (OFF) | gold≤4 zastępuje flat-35 kalibrowanym p80 |

**Konsumpcja bazy (35) — bare attribute, NIE C.flag → ZERO hot-reload:** feasibility_v2.py:1096-1097, **1105** (główny HARD reject `_gate_bt > C.BAG_TIME_HARD_MAX_MIN`), :1128 (soft-zone 30-35); dispatch_pipeline.py:4788 (`r6_hard_max=C.BAG_TIME_HARD_MAX_MIN`), :6859/6862/6872.

**DEFEKT (potwierdza A2 smell #3):**
- (a) **Inwersja tunable↔ważność:** baza HARD R6=35 (site 1) = BARE literał, zmiana TYLKO przez edycję kodu+restart; wyjątek-stretch 40 (site 2) = HOT przez flags.json. Adrian może podbić cap-stretch 40→45 na żywo, ale baza 35 zostaje zamrożona → rozjazd magnitude rośnie cicho, nikt nie pilnuje relacji `40 == 35 + margines`.
- (b) **bundle_calib flat-35 (site 5) IGNORUJE tier-40** → over-penalizuje T3 (38 min legalne dla T3 best_effort, ale instrument liczy `overage=max(0,age-35)` = +3 kary). To NIE display — to WERDYKT bramkujący flip O2 02.07 (C9 = P0). Komentarz tools/bundle_calib_shadow.py:69 sam przyznaje: "Objektyw O2 sam jest ŚLEPY na pasmo 20→35".
- (c) **O2 (sites 3,4) env-only** — niespójne z flags.json-hot best_effort (site 2): O2 strojony restartem, best_effort hot. Flip O2 02.07 musi ruszyć OBA, inaczej O2-cap (35 env) ≠ best_effort-cap (40 flags.json).

**Dowód że to żywe:** A3 §2d potwierdza `BEST_EFFORT_OBJM_R6_KEY`=ON (LIVE), flags.json:205 `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN: 40`. feasibility:1105 = jedyny HARD-35. Order @38min: HARD-reject w feasibility, ZAAKCEPTOWANY w best_effort objm (always-propose). To z PROJEKTU (R6 tier-aware), ALE 6 niezsynchronizowanych źródeł = każda przyszła zmiana R6 = 6-miejscowa łatka, bez wspólnego helpera `r6_cap_for_tier()`.

---

## N2 — "czasówka boundary = 60 min" — 6 SITES, TYLKO 1 HOT-TUNABLE → SILENT-DIVERGENCE  ★ FLAG-COUPLING DEFEKT

Reguła: `czas_odbioru ≥ 60 min ⟺ czasówka` (twarda deklaracja restauracji; ≥60 → KOORD hold id_kurier=26 / early-bird-defer). Wartość 60 powtórzona w 6 niezależnych miejscach, ale TYLKO jedna ścieżka jest hot-tunable:

| # | symbol / inline | wartość | plik:linia | override | konsument |
|---|---|---|---|---|---|
| 1 | `EARLY_BIRD_THRESHOLD_MIN` | **60** | common.py:430 | **flags.json HOT** (`_early_bird_threshold_min()` dispatch_pipeline.py:2600 `load_flags().get("EARLY_BIRD_THRESHOLD_MIN", 60)`) | early-bird KOORD-defer (zwiera obwód PRZED pulą) |
| 2 | `CZASOWKA_THRESHOLD_MIN` | **60** | auto_koord.py:32 | **BARE** | `is_czasowka` auto_koord.py:49 |
| 3 | `CZASOWKA_THRESHOLD_MIN` (2. KOPIA!) | **60** | panel_client.py:53 | **BARE** | `order_type="czasowka"` panel_client.py:692 (stempel typu PRZY INGEST) |
| 4 | `V324B_CZASOWKA_EVAL_START_MIN` | **60** | common.py:1895 | **BARE** | czasowka_scheduler.py:254/382 (okno eval) |
| 5 | inline `>= 60` | 60 | czasowka_scheduler.py:128 | literał | docstring/check |
| 6 | inline `>=60` | 60 | common.py:3413 | literał | is_flex (`order_type=='czasowka'⟺prep≥60`) |

**DEFEKT — cicha inwersja po flipie (klasa N + I):**
`EARLY_BIRD_THRESHOLD_MIN` (site 1) jest JAWNIE zaprojektowany jako hot-tunable (SCALE-01: `_early_bird_threshold_min()` czyta flags.json). Gdy Adrian ustawi go w flags.json na np. **45** (legalna operacja strojenia):
- early-bird KOORD-defer fires @45 min,
- ale `panel_client` (site 3) WCIĄŻ stempluje `order_type="czasowka"` dopiero @≥60,
- `auto_koord.is_czasowka` (site 2) WCIĄŻ ≥60,
- `czasowka_scheduler` okno eval (site 4 V324B) WCIĄŻ 60.

→ Zlecenia w `[45,60)` zostają KOORD-defer'owane jako early-bird, ALE NIE sklasyfikowane jako czasówka → nie wchodzą do pipeline czasówek (T-60/50/40), nie trafiają do id_kurier=26 → wiszą w KOORD bez ścieżki obsługi. **Jeden hot-knob rozsynchronizowuje się z 5 zamrożonymi kopiami.** Brak runtime-inwariantu pilnującego `early_bird_threshold == czasowka_threshold`.

**Dodatkowo A1/N twin:** `CZASOWKA_THRESHOLD_MIN` to DWIE niezależne module-level definicje (auto_koord.py:32 ORAZ panel_client.py:53), nie wspólny import. Dziś zgodne (60==60), ale strukturalnie rozjechane — edycja jednej nie dotyka drugiej.

> Uwaga: "czasówka 3 definicje" było OBALONE w nocnym audycie (`order_type=='czasowka'⟺prep≥60` u źródła = jedno źródło SEMANTYKI). To prawda dla SEMANTYKI. Ale WARTOŚĆ progu 60 jest fizycznie skopiowana 6×, z czego 1 hot-tunable — to osobny defekt (rozsyp wartości/override), nie kwestionuje semantyki.

---

## N3 — pre-shift floor "30 / 20 / 60" — mieszane bare+env, dwa różne "30"

Reguła pre-shift (link: AUDYT_preshift_pickup_floor sekcja 1):

| symbol | wartość | plik:linia | override | rola |
|---|---|---|---|---|
| `V325_PRE_SHIFT_HARD_REJECT_MIN` | **30** | common.py:1972 | **BARE** | HARD-reject gdy pickup < shift_start−30 |
| `V325_PRE_SHIFT_SOFT_PENALTY` | **-20** | common.py:1975 | **BARE** | warm-up kara w [shift−30, shift) |
| `PRE_SHIFT_NEAR_MIN` | **30** | common.py:1990 | **env** | granica gradientu kary (m≤30 lekka) |
| `PRE_SHIFT_WINDOW_MAX_MIN` | **60** | common.py:1989 | **env** | cap puli pre-shift |
| `PRE_SHIFT_NEAR_PEN_PER_MIN` | -1.0 | common.py:1991 | env | gradient/min |
| `PRE_SHIFT_FAR_PEN` | -1000.0 | common.py:1992 | env | ~veto far |
| `PRE_SHIFT_FAR_UNLOCK_LOAD` | 3.5 | common.py:1993 | env | relaks pod load |

**DEFEKT:** "30" oznacza DWA różne progi (HARD-reject distance `V325_PRE_SHIFT_HARD_REJECT_MIN` site bare vs gradient-near boundary `PRE_SHIFT_NEAR_MIN` env) — różne mechanizmy, ta sama liczba, różny override (bare vs env). Zmiana okna pre-shift z 30 na np. 25 wymaga ręcznego uzgodnienia bare-literału (kod) z env-const — łatwo rozjechać. `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` (flags.json ON) zeruje `V325_PRE_SHIFT_SOFT_PENALTY=-20` → -20 efektywnie martwy ale wciąż w kodzie (redundancja, protokół: "do zdjęcia za flagą").

---

## N4 — committed pickup window "±5" (R27) — 5 KONSTANT, "spójne z" w komentarzu

R27: "odbiór w oknie [czas_kuriera−5, +5]". "5" zakodowane jako 4-5 niezależnych stałych:

| symbol | wartość | plik:linia | override | rola |
|---|---|---|---|---|
| `OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN` | **5.0** | common.py:2554 | bare | tolerancja committed strict |
| `OBJ_COMMITTED_PICKUP_TOL_LOOSE_MIN` | **10.0** | common.py:2555 | bare | tolerancja loose (pod load) |
| `LATE_PICKUP_HARD_MAX_MIN` | **5.0** | common.py:2824 | **env** | R-LATE-PICKUP próg |
| `LATE_PICKUP_SOFT_FREE_MIN` | **5.0** | common.py:2840 | **env** | kara 0 do 5 (komentarz: "spójne z HARD_MAX") |
| `V3274_FROZEN_PICKUP_WINDOW_MIN` | **5.0** | common.py:3122 | bare | TSP frozen ±5 (SetCumulVarSoftUpperBound) |

**DEFEKT:** pięć "5.0" reprezentujących TĘ SAMĄ tolerancję ±5 R27, w mieszanych ścieżkach (env vs bare). Komentarz common.py:2841 SAM przyznaje konieczność synchronizacji ("spójne z HARD_MAX") — czyli wie że to ręcznie utrzymywany invariant bez strażnika. Zmiana R27 z ±5 na ±7 = 4-5 edycji, część restartem (env), część kodem (bare). `LATE_PICKUP_SOFT_CAP=60.0` (common.py:2845) = górny cap kary, oddzielny.

---

## N5 — bag-cap-per-tier — DWIE macierze z RÓŻNYMI wartościami

Pojęcie "max worek per tier" ma DWIE niezależne tabele z ROZBIEŻNYMI wartościami:

| tier | `BUG4_TIER_CAP_MATRIX` peak (common.py:1310, SOFT, ON) | `HARD_TIER_BAG_CAP` (common.py:1326, HARD, flaga OFF) |
|---|---|---|
| gold | 6 | 6 |
| std+ | 5 | 6 |
| std | **4** | **5** |
| slow | **3** | **4** |
| new | (brak) | 4 |

- `BUG4_TIER_CAP_MATRIX` (common.py:1310): pora-aware (off_peak/normal/peak), SOFT-penalty, `ENABLE_V319H_BUG4_TIER_CAP_MATRIX`=ON (common.py:1318).
- `HARD_TIER_BAG_CAP` (common.py:1326): pora-blind, HARD-reject, `ENABLE_HARD_TIER_BAG_CAP`=OFF (common.py:1328), `HARD_TIER_BAG_CAP_DEFAULT=6` (common.py:1327).

**DEFEKT:** dla std/slow obie tabele dają RÓŻNE capy (std: 4 vs 5; slow: 3 vs 4). To samo pojęcie "ile zleceń max w worku tieru" rozjechane między SOFT (peak-aware) a HARD (flat). Gdy `ENABLE_HARD_TIER_BAG_CAP` zostanie flipnięty ON (kandydat), będzie egzekwował 5/4 podczas gdy BUG4 SOFT karze już od 4/3 → niespójna granica. A2 cytował "gold6/std5/slow4/new4" jako HARD_TIER — zgodne ze świeżym (z poprawką std+6). Faza D: która reguła load wygrywa (powiązane z R-10 vs LOADGOV z A2).

---

## N6 — dropoff-after-shift "5 min" — V324 ŻYWA / V325 MARTWA (N + dead-twin)

| symbol | wartość | plik:linia | konsument | status |
|---|---|---|---|---|
| `V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN` | **5** | common.py:1820 | feasibility_v2.py:**1265** (`excess_min > C.V324_...`) | **ŻYWA** |
| `V325_DROPOFF_AFTER_SHIFT_HARD_MIN` | **5** | common.py:1997 | (grep: ZERO konsumentów poza def+komentarz :1995) | **MARTWA** |

**DEFEKT:** dwie stałe, ten sam koncept ("dropoff > shift_end + 5 → reject"), ta sama wartość 5, ale TYLKO V324 wpięta. Komentarz common.py:1995 twierdzi że V325 jest "parallel ... flag-gated osobno dla rollout independence" — ale grep pokazuje że nigdy nie podpięto → V325_DROPOFF = martwy duplikat (N + K). Mina: ktoś zmieni V325 "bo to wygląda na aktywną wersję" → zero efektu, a feasibility liczy z V324.

---

## N7 — DWELL fallback — twin const common ↔ route_simulator

| symbol | wartość | plik:linia | rola |
|---|---|---|---|
| `DWELL_PICKUP_FLAT_MIN` / `DWELL_DEFAULT_MIN` | 1.0 / 3.5 | common.py:2146-2147 | KANON fallback (gdy tier nieznany) |
| `DWELL_PICKUP_MIN` / `DWELL_DROPOFF_MIN` | 1.0 / 3.5 | route_simulator_v2.py:34-35 | KOPIA fallback (gdy węzeł niestemplowany) |
| `DWELL_BY_TIER` | gold1.5/std+2.5/std4.5/slow6.5/new6.5 | common.py:2148 | PRODUKCJA (tier-aware, `dwell_for_tier` :2162) |

**DEFEKT (P3, niska — fallback path):** route_simulator_v2 ma WŁASNĄ kopię fallbacku dwell (1.0/3.5) duplikującą `DWELL_PICKUP_FLAT_MIN`/`DWELL_DEFAULT_MIN` z common. Dziś zgodne. Komentarz route_simulator_v2.py:34 sam mówi "produkcja używa C.dwell_for_tier" → ten module-const to tylko fallback dla niestemplowanego węzła. Ale gdy common's `DWELL_DEFAULT_MIN` zostanie rekalibrowany (jak DWELL_BY_TIER 06-10), bare 3.5 w route_simulator zostaje stale → rozjazd na ścieżce fallbacku. Historia potwierdza ruchliwość: `DWELL_DROPOFF_MIN` było 2.0 (V3.27.3) → 3.5 (komentarz :35).

---

## N8 — scoring.py wait penalty: DOCSTRING ≠ STAŁA (lying-doc, klasa E/L)

`scoring.py` `compute_wait_courier_penalty` docstring (scoring.py:126-129) opisuje:
- "≤5 min sweet spot → 0"
- "7-20 min → -10 + (wait-6)·-5"
- ">20 min → HARD REJECT"

ALE efektywne stałe (common.py):
- `V3273_WAIT_COURIER_THRESHOLD_MIN = 3.0` (common.py:2514, "tighten 5→3") — NIE 5
- `V3273_WAIT_COURIER_PER_MIN_PENALTY = -8.0` (common.py:2518-2519, "było -5.0") — NIE -5
- `V3273_WAIT_COURIER_HARD_REJECT_MIN = 15.0` (common.py:2521, "tighten 20→15") — NIE 20

Konsument scoring.py:150 czyta `_common.V3273_WAIT_COURIER_HARD_REJECT_MIN` (=15) POPRAWNIE — ale docstring kłamie (mówi 20/5/-5). **A2 SAM powtórzył stałe "20" z tego docstringa** (A2 smell #11 / R9 sekcja: "`V3273_WAIT_COURIER_HARD_REJECT_MIN=20`") — czyli lying-doc już zatruł audyt. Klasa E (przyrząd/doc kłamie) + L (słownictwo/wartość rozjechana z kodem). Niska szkoda runtime (kod czyta stałą), ale wysoka szkoda poznawcza (każda sesja czytająca docstring dostaje błędne progi).

Dodatkowo `V3273_WAIT_COURIER_PER_MIN_PENALTY_LEGACY = -5.0` (common.py:2520) żyje obok aktywnego -8.0 (shadow baseline) → 2 wartości /min.

---

## N9 — "margin = 15" — magic-number w 5 podsystemach bez wspólnego źródła

| symbol | wartość | plik:linia | podsystem |
|---|---|---|---|
| `REASSIGN_FWD_MARGIN` | 15.0 | flags.json:102 | reassignment forward shadow |
| `CZASOWKA_PROACTIVE_MIN_MARGIN` | 15 | flags.json:152 | czasówka proaktywna |
| `PENDING_RESWEEP_MARGIN` | 15.0 | flags.json:214 | pending resweep |
| T1 `min_score_margin` | 15.0 | auto_proximity_classifier.py:71 | auto-proximity T1 (hardcode) |
| `min_score_margin` default | 15.0 | auto_assign_gate.py:84 | auto-assign gate (fallback) |

**DEFEKT (P3):** "15 punktów przewagi score = wystarczy" pojawia się jako magic-number w 5 niezależnych miejscach (3× flags.json + 2× hardcode). Różne podsystemy, więc nie "ta sama reguła" ścisła — ale identyczny próg-decyzyjny bez nazwanej wspólnej stałej (`SCORE_MARGIN_CONFIDENT=15`). Strojenie jednego nie informuje pozostałych. Powiązany rozsyp "min_score": auto_proximity T1=50/T2=40/T3=30 (auto_proximity_classifier.py:73/80/87, hardcode) vs `CZASOWKA_MIN_PROPOSAL_SCORE=60` (flags.json:49) vs `CZASOWKA_PROACTIVE_MIN_SCORE=30` (flags.json:151) vs `MIN_PROPOSE_SCORE=-100` (common.py:795).

---

## N10 — "8.0 km delivery spread" — R1 ↔ BUNDLE (Fix C)

| symbol | wartość | plik:linia | override | rola |
|---|---|---|---|---|
| `R1_MAX_DELIV_SPREAD_KM` | **8.0** | feasibility_v2.py:90 | bare | R1 metric (SOFT, feasibility) |
| `BUNDLE_MAX_DELIV_SPREAD_KM` | **8.0** | common.py:2280 | **env** | Fix C cap (dispatch_pipeline ~2369) |
| `R3_DYNAMIC_MAX` | (8.0, 4) | feasibility_v2.py:93 | bare | R3 dynamic cap (telemetry-only) |

**DEFEKT (P3):** "max rozrzut dostaw 8km" w 2 stałych (R1 feasibility bare + BUNDLE pipeline env) + jako breakpoint w R3_DYNAMIC_MAX. R1 i BUNDLE to to samo pojęcie (spread worka), różne ścieżki (bare vs env). A2 (R1 sekcja) notuje też `ENABLE_R1_PROGRESSIVE_CLIP` jako 3. wariant R1. Niska szkoda (oba ~SOFT), ale 8km rozjechane jak inne progi.

> Magic-number "2.5 km" pojawia się w 3 RÓŻNYCH pojęciach (`R5_MAX_MIXED_PICKUP_SPREAD_KM`=2.5 feasibility_v2.py:95, `NEW_COURIER_RAMP_MAX_KM`=2.5 common.py:2035, `V326_WAVE_VETO_NEW_DROP_KM`=2.5 common.py:2231) — to magic-reuse RÓŻNYCH reguł, nie jedna reguła w N miejscach → odnotowane, nie liczę jako rozjazd jednej reguły (niższa istotność niż N1-N6).

---

## META — BRAK JEDNEJ ŚCIEŻKI OVERRIDE (klasa D+N)

Progi decyzyjne rozkładają się na 3 NIESPÓJNE mechanizmy konfiguracji, BEZ reguły który próg którym ma być:

1. **BARE module literał** (edycja kodu + restart): `BAG_TIME_HARD_MAX_MIN`, `V325_PRE_SHIFT_HARD_REJECT_MIN`, `V325_PRE_SHIFT_SOFT_PENALTY`, `CZASOWKA_THRESHOLD_MIN` (×2 moduły), `V324B_CZASOWKA_EVAL_START_MIN`, `V3274_FROZEN_PICKUP_WINDOW_MIN`, `OBJ_COMMITTED_PICKUP_TOL_*`, `R1_MAX_DELIV_SPREAD_KM`, `PACZKA_*_SOFT_CAP_MIN`.
2. **env-frozen** (`os.environ.get`, restart): `O2_OVERAGE_CAP_MIN`, `O2_CAP_Z_MIN`, `PRE_SHIFT_*` (window/near/far), `LATE_PICKUP_*`, `BAG_TIME_DANGER_*`, `BUNDLE_MAX_DELIV_SPREAD_KM`, `MAX_BAG_SANITY_CAP`, `MAX_PICKUP_REACH_KM`.
3. **flags.json HOT** (SCALE-01 `load_flags().get(KEY, C.KEY)`): `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN`, `EARLY_BIRD_THRESHOLD_MIN`, `MIN_PROPOSE_SCORE`, `MAX_BAG_SANITY_CAP` (przez `_bag_sanity_cap()`), `MAX_PICKUP_REACH_KM` (przez `_pickup_reach_km()`).

`FLAGS_JSON_NUMERIC_OVERRIDES` (common.py:270-294, 25 kluczy) deklaruje które stałe SĄ hot-overridowalne — ale to lista wybiórcza, NIE pokrywa progów-bliźniaczych (np. obejmuje `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN`=40 ale NIE `BAG_TIME_HARD_MAX_MIN`=35; obejmuje `EARLY_BIRD_THRESHOLD_MIN` ale NIE `CZASOWKA_THRESHOLD_MIN`). **Skutek:** w obrębie JEDNEJ reguły baza jest zamrożona a wyjątek hot-tunable → strojenie wyjątku cicho rozsynchronizowuje go z bazą (N1, N2). To wprost zasila klasę D (dryf flag/efektywny stan) z A3.

---

## TABELA POKRYCIA

| Moduł / plik | przeszukane progi | znalezione rodziny N |
|---|---|---|
| common.py | R6/BAG_TIME, PRE_SHIFT, czasówka-60, ±5-pickup, tier-bag-cap, dropoff-shift, DWELL, spread, O2, margin, paczka-cap, FLAGS_JSON_NUMERIC_OVERRIDES | N1,N2,N3,N4,N5,N6,N7,N10,META |
| feasibility_v2.py | R6 gate (1105), spread R1/R5 (90/95/504/573), ETA-quantile p80 (1089), dropoff V324 (1265) | N1,N3,N6,N10 |
| route_simulator_v2.py | DWELL fallback (34-35), dwell_for_tier | N7 |
| scoring.py | wait penalty docstring↔const (126-150) | N8 |
| auto_koord.py | CZASOWKA_THRESHOLD_MIN (32/49) | N2 |
| panel_client.py | CZASOWKA_THRESHOLD_MIN 2. kopia (53/692) | N2 |
| czasowka_scheduler.py | V324B (254/382), inline 60 (128) | N2 |
| auto_proximity_classifier.py | T1/T2/T3 margin+score (70-87), HIGH_RISK_BUMP, MASS_FAIL | N9 |
| auto_assign_gate.py | min_score_margin (84), min_pool (191) | N9 |
| tools/bundle_calib_shadow.py | R6_MAX_MIN flat 35 (56), Z_CAPS 20/32/35 (72) | N1 |
| flags.json | numeric keys (margin/score/cap) | N1,N2,N9,META |

## LUKI POKRYCIA (jawne, nie cisza)

- **NIE grep'owałem każdego numerycznego literału w ~210 modułach.** Skupiłem się na progach DECYZYJNYCH w modułach CORE-D (per A1: feasibility/scoring/pipeline/route_sim/selekcja/auto-*). Progi PERI/INSTR (cod_weekly, daily_accounting, reconciliation interval=30, parser_health stuck) pominięte jako niedecyzyjne.
- **Cross-repo konsola/apka:** sprawdziłem `fleet_state.py` + `courier_orders.py` na hardcoded R6/35/40 magic → **ZERO** (ETA z silnika, nie własny próg R6 — dobre). NIE swept całości progów render (CLAMP/ETA constants konsoli) — to lane J/render, nie próg-decyzyjny silnika.
- **Tabele kalibracyjne** (`V326_OSRM_TRAFFIC_TABLE` weekday/sat/sun buckety, `V327_WAIT_PENALTY_TABLE` 7-entry, `DWELL_BY_TIER` 5 wartości) zawierają wiele liczb, ale to KALIBRACJA per-bucket (z definicji różne wartości per pora/tier), NIE "ten sam próg w N miejscach". Odnotowane, nie liczone jako rozsyp (poza N5/N7 gdzie są DWIE tabele tego samego pojęcia).
- **Reachability runtime** ETA-quantile p80 (N1 site 6) i HARD_TIER_BAG_CAP (N5) NIE zweryfikowana żywym tickiem — flagi OFF per A3 (`ENABLE_ETA_QUANTILE_R6_BAGCAP` OFF, `ENABLE_HARD_TIER_BAG_CAP` OFF). Oznaczone jako latentne.
- **Zero uruchomień** (`--notify`/`--live`/`--apply` żadne). Pure read-only grep+Read. Zero edycji/restartów/flipów/git.
- **GPS-age / TTL** (`LAST_KNOWN_POS_TTL_MIN=25`, GPS_AGE_DISCOUNT_*) NIE sweepowane głęboko (TELEM, nie próg-selekcji) — A3 ma je w numeric-overrides.

## HANDOFF (Faza D/E/F)

- **Faza D (graf konfliktów):** N2 (early-bird-60-hot vs czasówka-60-bare) = gotowa para silent-inversion → dorzucić do grafu obok COMMIT_DIVERGENCE (A3 §2b). N5 (BUG4 vs HARD_TIER bag-cap) = para "która reguła load wygrywa" (łączy z R-10 vs LOADGOV z A2). N6 V325_DROPOFF dead = K-sweep.
- **Faza E (dedup):** N1+N3+N4+N5+N6+N7+N10 zwijają do JEDNEGO rootu **K1 "brak jednego źródła / N-kopii progu"** (z ziomek-unified-audit). N8 = osobny root E/L (lying-doc). N9 = N-magic. META = D (override-path chaos). NIE 10 chaosów — 3 roots: K1 (rozsyp wartości), E/L (doc≠const), D (override-path).
- **Faza F (target/PoC):** kanoniczny `r6_cap_for_tier(tier)` helper (1 źródło dla N1 6-sites) + `czasowka_threshold()` (1 źródło dla N2 6-sites, hot-tunable, czytane wszędzie) + reguła "każdy próg decyzyjny → SCALE-01 `load_flags().get(KEY, C.KEY)` ALBO jawnie bare-z-powodem" (META) + runtime-inwariant `early_bird_threshold == czasowka_threshold` (wzór: carried_first_guard). NIE w pośpiechu — measure-first per protokół.
