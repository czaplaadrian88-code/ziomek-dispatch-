# Seed-korpus bundling-bias — SAME_REST_RACE_PROBE visible-but-filtered (2026-05-31)

**Cel:** Krok 2 race-probe Baanko. Fork rozstrzygnięty (orphan=0 / visible-but-filtered dominuje) →
sibling z tej samej restauracji BYŁ w bagu kuriera, kurier w puli, ale Ziomek wybrał innego jako *best*.
Pytanie: czy **bias toward in-bag-same-restaurant-courier** trafiłby w ground-truth człowieka?

**Metoda (Lekcja #154 — mierz flip-direction toward/away vs ground-truth, NIE koduj z intuicji):**
Dla każdej z 35 visible captures:
- `bias_target` = `sib.cid` (kurier już niosący zlecenie tej restauracji)
- `prod_proposed` = co produkcja zaproponowała (`PANEL_OVERRIDE proposed=`, lub = final gdy brak override)
- `final` = `panel_watcher: ASSIGNED <oid> -> <cid>` (ground-truth: co realnie się stało)

Klasyfikacja interwencji „przesuń propozycję na sib_cid":
| flip | warunek | znaczenie |
|---|---|---|
| **TOWARD** | `final == bias_target` | bias trafiłby w ground-truth (dowód ZA) |
| **AWAY** | `final == prod_proposed ≠ sib` | bias odsunąłby od poprawnej propozycji (dowód PRZECIW) |
| **OTHER** | `final` = trzeci kurier | człowiek chciał kogoś innego (bias i tak pudło) |

Generator: `tools/build_bundling_bias_corpus.py` (reprodukowalny, czyta tylko shadow.log + dispatch.log).
Dane: `bundling_bias_seed_corpus.json`. Okno: ts ≥ 2026-05-29 13:33:08 (test-leak 13:32:16 wykluczony).

---

## Wynik agregatowy (n=35)

| flip | n | % |
|---|---|---|
| **TOWARD** | 11 | 31% |
| AWAY | 5 | 14% |
| OTHER | 19 | 54% |

- **9 z 11 TOWARD przez override** — człowiek AKTYWNIE nadpisał propozycję Ziomka NA in-bag-sibling. To realne, powtarzalne zachowanie (~9 przypadków / 3 dni).
- Blanket bias na wszystkie 35 → **precision 31%** (bije Fix C=7%, ale 69% firings nietrafione + 5 AWAY = aktywnie psuje poprawną propozycję).

## ⭐ Dyskryminator: `pos_source` (jedyny realny)

| pos_source | n | TOWARD | flips |
|---|---|---|---|
| `last_picked_up_pickup` | 17 | **41%** | 7T / 3A / 7O |
| `last_assigned_pickup` | 12 | **33%** | 4T / 0A / 8O |
| `gps` | 3 | **0%** | 0T / 1A / 2O |
| `pre_shift` | 3 | **0%** | 0T / 1A / 2O |

**Kluczowy wniosek:** kurier *w trasie z już-pobranym/przypisanym* zleceniem tej restauracji
(`last_picked_up`+`last_assigned`, n=29) → **TOWARD 11/29 = 38%**, a `gps`+`pre_shift` (n=6) → **0/6**.
- Potwierdza pierwotną intuicję „pre_shift to NIE pełnoprawny kandydat" — ale pre_shift to tylko 3/35; realny sygnał jest w en-route.
- Słabe/zerowe dyskryminatory: `bag_size` (inverted-U bag2-3≈35% vs bag1/≥4≈20%, mały n), `assigned_age` (median 57 vs 60s — brak), `sib_status` (assigned 32% vs picked_up 25% — brak).

## Kontrola #154 (baseline override w oknie)

| | override rate |
|---|---|
| Baseline (cały window, 433/607 ASSIGNED) | **71.3%** |
| Subset 35 visible | 82.9% |
| → subset / baseline | **1.2×** |

**Reżim jest mocno-override'owy globalnie** (człowiek nadpisuje Ziomka 71% czasu — spójne z A/B #154 gdzie human-agreement ≈11%). „Sibling istnieje" podnosi override tylko 1.2× → NIE czyni propozycji szczególnie gorszą. ALE: TOWARD=31% to ~3× szansa losowa (sib = 1 kurier z puli ~3-15) → realna, choć umiarkowana, preferencja bundlingu której Ziomek nie łapie.

## Korpus (35 wierszy; ✎ = człowiek nadpisał)

| flip | oid | restauracja | sib | sib pos_source | bag | shadow_best | prod_prop | final |
|---|---|---|---|---|---|---|---|---|
| TOWARD ✎ | 476937 | Rukola Sienkiewicza | 500 | last_picked_up_pickup | 2 | 413 | 413 | **500** |
| TOWARD ✎ | 477050 | Rukola Sienkiewicza | 508 | last_picked_up_pickup | 3 | 123 | 123 | **508** |
| TOWARD ✎ | 477188 | Paradiso | 370 | last_picked_up_pickup | 2 | 123 | 123 | **370** |
| TOWARD | 477187 | Chicago Pizza | 526 | last_picked_up_pickup | 3 | 123 | 526 | **526** |
| TOWARD ✎ | 477222 | Chicago Pizza | 413 | last_assigned_pickup | 3 | 123 | 123 | **413** |
| TOWARD ✎ | 477253 | Rany Julek | 518 | last_picked_up_pickup | 2 | 517 | 517 | **518** |
| TOWARD ✎ | 477335 | Goodboy | 518 | last_assigned_pickup | 2 | 484 | 484 | **518** |
| TOWARD ✎ | 477421 | Restauracja Kumar's | 207 | last_picked_up_pickup | 2 | 508 | 508 | **207** |
| TOWARD | 477459 | Rany Julek | 370 | last_assigned_pickup | 1 | 179 | 370 | **370** |
| TOWARD ✎ | 477465 | Grill Kebab | 413 | last_assigned_pickup | 5 | 387 | 387 | **413** |
| TOWARD ✎ | 477487 | Restauracja Kumar's | 387 | last_picked_up_pickup | 2 | 500 | 500 | **387** |
| AWAY | 477161 | Pani Pierożek | 471 | last_picked_up_pickup | 4 | 370 | 370 | 370 |
| AWAY ✎ | 477242 | Grill Kebab | 526 | last_picked_up_pickup | 2 | 123 | 123 | 123 |
| AWAY | 477280 | Rukola Kaczorowskiego | 387 | pre_shift | 3 | 123 | 123 | 123 |
| AWAY | 477372 | Restauracja Kumar's | 500 | last_picked_up_pickup | 2 | 413 | 413 | 413 |
| AWAY | 477411 | Raj | 123 | gps | 1 | 508 | 400 | 400 |
| OTHER ✎ | 476951 | Chinatown Bistro | 400 | gps | 3 | 518 | 518 | 515 |
| OTHER ✎ | 476980 | Grill Kebab | 508 | last_assigned_pickup | 2 | 518 | 518 | 484 |
| OTHER ✎ | 477004 | Zapiecek | 508 | last_assigned_pickup | 1 | 441 | 441 | 484 |
| OTHER ✎ | 477038 | Retrospekcja | 524 | last_assigned_pickup | 2 | 508 | 508 | 123 |
| OTHER ✎ | 477084 | Grill Kebab | 123 | last_assigned_pickup | 5 | 484 | 484 | 413 |
| OTHER ✎ | 477117 | Sushi Rany Julek & Pizza | 500 | pre_shift | 2 | 518 | 518 | 370 |
| OTHER ✎ | 477132 | Sushi Rany Julek & Pizza | 370 | last_assigned_pickup | 3 | 207 | 207 | 524 |
| OTHER ✎ | 477184 | Rukola Sienkiewicza | 123 | gps | 4 | 484 | 484 | 413 |
| OTHER ✎ | 477194 | Rany Julek | 500 | last_picked_up_pickup | 2 | 370 | 370 | 207 |
| OTHER ✎ | 477259 | Karczma Maciejówka | 520 | last_assigned_pickup | 1 | 518 | 518 | 484 |
| OTHER ✎ | 477276 | Raj | 387 | pre_shift | 1 | None | 484 | 207 |
| OTHER ✎ | 477288 | Street Mama Thai | 520 | last_picked_up_pickup | 3 | None | 520 | 179 |
| OTHER ✎ | 477291 | Rany Julek | 370 | last_picked_up_pickup | 4 | 123 | 123 | 376 |
| OTHER ✎ | 477329 | Mama Thai Bistro | 179 | last_picked_up_pickup | 3 | 370 | 370 | 413 |
| OTHER ✎ | 477336 | Rany Julek | 207 | last_picked_up_pickup | 3 | 376 | 289 | 484 |
| OTHER ✎ | 477342 | Raj | 289 | last_assigned_pickup | 2 | 413 | 413 | 179 |
| OTHER ✎ | 477413 | Rany Julek | 179 | last_assigned_pickup | 2 | 387 | 123 | 484 |
| OTHER ✎ | 477425 | Karczma Maciejówka | 289 | last_picked_up_pickup | 2 | 123 | 123 | 387 |
| OTHER ✎ | 477435 | Rany Julek | 387 | last_picked_up_pickup | 2 | 123 | 508 | 413 |

---

## Werdykt + rekomendacja Kroku 2

**To NIE jest ślepy zaułek jak Fix C.** W przeciwieństwie do Fix C (0 dyskryminatorów, precision 7%, 0 flipów w A/B), tutaj mamy: (1) realny dyskryminator (`pos_source`: en-route 38% vs gps/pre_shift 0%), (2) preferencję ~3× ponad losową, (3) powtarzalne zachowanie człowieka (9 override'ów NA in-bag-sibling / 3 dni).

**ALE static blanket-bias nie jest surgical** (precision 31-38%, 5 AWAY psułoby poprawne). Per #154 — NIE wdrażać twardej reguły.

**Proponowany następny krok (score-level, shadow-first):**
1. **Miękki bonus co-pickup** w scoringu: `bonus_same_rest_inbag` aplikowany TYLKO gdy sibling `pos_source ∈ {last_picked_up_pickup, last_assigned_pickup}` (NIE gps/pre_shift — 0% trafień). Soft, ograniczony (np. cap ~tyle, by flipować tylko przy małym marginesie best↔sib).
2. **Kalibracja na tym korpusie:** dobierz coeff tak, by flipnąć ~11 TOWARD bez flipowania 5 AWAY → potrzeba per-kandydat score margins (best↔sib) z `shadow_decisions.jsonl` dla 5 AWAY i 11 TOWARD (NASTĘPNY pull — korpus to seed, margines to kalibracja).
3. **A/B w cieniu** (jak late_pickup_shadow): licz `bundling_bias_shadow={changed, old_winner, new_winner}` równolegle, mierz flip toward/away na żywym peaku przez ~7d ZANIM cokolwiek wejdzie do hot-path. Próg sukcesu: toward > away z marginesem.
4. **OTHER=54% to twardy sufit** — w większości człowiek chce trzeciego kuriera (fleet-orchestration judgment spoza features, jak konkluzja #154). Bundling-bias zaadresuje co najwyżej ~1/3; reszta = M3 FleetLoad / bogatsze features (długoterminowo).

**Decyzja dla Adriana:** czy robić krok 2 (pull score-margins 16 case'ów TOWARD+AWAY → kalibracja coeff → bundling_bias_shadow A/B), czy odłożyć na rzecz M3 FleetLoad. Korpus + dyskryminator gotowe jako fundament.

---

# KROK 2 — pull score-margins (2026-05-31): SCORE-BONUS UNIEWAŻNIONY weryfikacją

Generator: `tools/extract_bias_score_margins.py`. Dane: `bundling_bias_score_margins.json`.
Metoda: dla 16 case'ów TOWARD+AWAY znajdź rekord `shadow_decisions.jsonl` w ts probe'a (skew 0.0s ✓),
winner=best, policz `margin = winner_score − sib_score`. **Weryfikacja serializacji:** `alternatives`
zawiera TYLKO feasible (pool_total=14 vs pool_feasible=9 → 5 infeasible DROPNIĘTYCH); brak sib w
alternatives = **sib był INFEASIBLE w chwili decyzji** (potwierdzone).

## Wynik (decydujący NEGATYW dla score-bonus)

| | TOWARD n=11 | AWAY n=5 |
|---|---|---|
| sib **INFEASIBLE** (bonus nie pomoże) | **8** | 4 |
| sib feasible | 3 | 1 |
| feasible margins | −16.51, −13.66, **+73.14** | +28.77 |

**Score-bonus flipuje 0/11 TOWARD czysto:**
1. **8/11 — sib INFEASIBLE** (R6/capacity/carry odrzucił in-bag-sibling; human zbundlował mimo to). Bag 1-5 (mix → nie tylko bag-time). Bonus score NIE proponuje infeasible kandydata. To **niezgoda feasibility/R6, NIE scoring.**
2. **2/11 — sib feasible ALE wyższy score niż winner** (477188: sib 8.43 vs best −5.23; 477421: sib 3.99 vs best −12.52). Sib NIE zaproponowany mimo wyższego score → problem **sortu/demote/tiering**, bonus moot. Oba: sib `last_picked_up`/informed, winner `gps`/`last_assigned` — demote nie zadziałał jak V3.16 (informed-first).
3. **1/11 — margin +73.14** (477222): flip wymaga absurdalnego bonusu (przelałby kontrolę).

AWAY: 4/5 sib infeasible (bezpieczne — bonus i tak nie flipnie); 1 feasible (477372) margin 28.77.

## Werdykt Kroku 2 (wzór #154 — mechanizm nie pasuje do przyczyny)

**Score-level bundling-bias = ślepy zaułek**, dokładnie jak Fix C (A/B 0 flipów) — tu margin-pull
pokazał 0/11 PRZED napisaniem shadow A/B. **NIE budować `bundling_bias_shadow`** dla score-bonusu.

Realne, zweryfikowane sterowniki TOWARD:
- **(A) Feasibility/R6 (8/11 + 4/5 AWAY) — dominujący.** Human bundluje na in-bag-same-rest kuriera
  którego Ziomek uznał za infeasible. **Hipoteza:** R6 bag-time za ostry dla co-pickup (nowe zlecenie
  dzieli pickup → +czas dowozu, ale +0 nowy przystanek; insight Doner #154). **ALE override≠dowód-błędu
  (#154):** baseline override 71% → te 8 może być nawykowym przeładowaniem człowieka, nie błędem R6.
  **Rozstrzyga OUTCOME, nie kod:** czy te 8 bundli dowiozło w SLA (R6 mylił się blokując) czy breach
  (R6 miał rację). To następna weryfikacja — NIE zmiana kodu.
- **(B) Sort/demote (2/11).** Feasible sib z wyższym score nie-proponowany → wpada w dzisiejszy sprint
  tiering/demote (`late_pickup_score_first` + V3.16). Osobny tor.

## Rekomendacja
1. **Score-bonus: ZAMKNIĘTE** (0/11, jak Fix C). Zero kodu hot-path.
2. **Driver A (feasibility):** zbudować **outcome-check** 8 infeasible-sib bundli (dowóz w SLA vs breach
   z `shadow_outcome_enricher`/panel completion) ZANIM tknąć R6. Jeśli dowiozły OK → R6 co-pickup za
   ostry (realny target, ale wymaga osobnego korpusu treatment-vs-control na feasibility, nie score).
   Jeśli breach → R6 słuszny, human przeładowuje, brak fixu.
3. **Driver B (demote):** 477188/477421 dorzucić do obserwacji dzisiejszego tiering-sprintu (informed
   `last_picked_up` nie wygrał z `gps` mimo wyższego score — sprawdzić bucket_rank).
4. **OTHER=54% + M3 FleetLoad** bez zmian — długoterminowy kierunek.

---

# OUTCOME-CHECK driver A (2026-05-31): czy 8 infeasible-sib bundli dowiozło w SLA?

Generator `tools/outcome_check_driverA.py`. Dane `outcome_check_driverA.json`.
Metryka R6 = bag-time = pickup→delivery (`state_machine COURIER_DELIVERED` − `COURIER_PICKED_UP`,
log-ts UTC). Realized bundle R6 = max(bag_time nowe, bag_time sib). Próg = R-35MIN-MAX (35 min).
**Filtr rzetelności:** delivered w reconcile-batch (≥8 `COURIER_DELIVERED` w ±10s) = niemierzalne
(timestamp = catch-up flush, nie realna dostawa). Wykryty batch 31.05 19:41:50-19:42:10 = **25 dostaw
w 20s** (vs 0 w normalnym 5-min oknie) → 3 case'y 31.05-wieczór odrzucone jako artefakt.

## Wynik (8 → 5 mierzalnych)

| verdict | n | bundle R6 (min) |
|---|---|---|
| **R6_TOOSTRICT_ok** (dowiózł ≤35) | 4 | 9.6, 19.5, 22.0, 22.4 |
| **R6_RIGHT_breach** (>35) | 1 | 52.1 (477187, case KOORD) |
| UNRELIABLE (reconcile-batch 31.05 19:4x) | 3 | — niemierzalne |

- **4/5 mierzalnych bundli dowiozło komfortowo w SLA** (≤22.4 min) mimo że Ziomek uznał in-bag-sib za infeasible → słaby sygnał „R6 za ostry dla co-pickup".
- **1/5 realny breach 52 min** — i to był case **KOORD** (Ziomek w ogóle nie proponował, najtrudniejszy) → gate NIE jest czystym szumem, łapie realne przeładowania.

## Werdykt: NIEROZSTRZYGAJĄCE — NIE relaksować R6 na tej próbce

Słabo przechyla ku „R6 lekko za ostry", ale **n=5 za małe + zmącone + 1 realny breach**:
1. **Realized route ≠ symulacja Ziomka** — kurier mógł przeplanować po override (dropnąć sib first, skrót). „Dowiózł w 22 min" NIE dowodzi że predykcja R6 (na innej trasie) była zła.
2. **Bag drift** — skład bagu przy dostawie ≠ przy decyzji.
3. **Survivorship / tacit judgment (#154)** — to case'y które człowiek WYBRAŁ nadpisać; jego selektywny osąd (wiedza o kurierze/trasie) trafia w „te bundle są OK". Globalna relaksacja R6 odpaliłaby też na case'ach których człowiek NIE nadpisał → ten sam sufit co OTHER=54% (fleet-orchestration spoza features).

**→ Nie ma podstaw tknąć R6 z n=5.** Dyscyplina #154: nie działaj na małej zmąconej próbce.

## Następny krok (decyzja Adriana)
- **(a) Większy feasibility-korpus treatment-vs-control** — zmajnować dispatch.log historycznie (NIE tylko 35 probe-captures): WSZYSTKIE co-pickup gdzie Ziomek=infeasible a human assigned (treatment) vs gdzie infeasibility respektowana / co-pickup feasible (control), z rzetelnymi (non-batch) outcome'ami. Dopiero to rozstrzyga R6-co-pickup wg #154. Większy build.
- **(b) Park driver A → M3 FleetLoad.** Wszystkie 3 ścieżki (Fix C #154, score-bonus #162, R6-feasibility) zbiegają się w „capacity/fleet-aware judgment spoza features" → M3 FleetLoad to właściwa długoterminowa odpowiedź. Zamknąć wątek bundling-bias jako wyczerpany statycznymi regułami.

---

# ✅ DOMKNIĘCIE 2026-05-31 — WĄTEK ZAPARKOWANY → M3 FleetLoad (decyzja Adriana: „park")

Wybór (b). Bundling-bias **wyczerpany statycznymi regułami** — trzy niezależne analizy (Fix C #154,
score-bonus #162, R6-feasibility outcome-check) zgodnie wskazują, że sterownikiem nadpisań jest
**capacity/fleet-aware judgment człowieka spoza obecnych features** = domena M3 FleetLoad.

**Stan:** zero zmian w hot-path w całym wątku (od race-probe 29.05 do dziś). Probe
`ENABLE_SAME_RESTAURANT_RACE_PROBE` zostaje ON w cieniu (logging-only) — zbiera dalej co-pickup
fleet-load data dla M3.

**Co czeka na M3 (gotowy fundament):** ten raport + `bundling_bias_*.{md,json}` +
`outcome_check_driverA.json` + 3 reproduktywne narzędzia w `tools/`. Pointer w
`memory/economics_engines_roadmap.md` sekcja [3] FleetLoad → „PARKED INPUT". Lekcja #162.
