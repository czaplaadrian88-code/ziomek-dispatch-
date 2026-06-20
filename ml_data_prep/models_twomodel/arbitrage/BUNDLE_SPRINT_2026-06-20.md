# Bundle-improvement sprint — wynik pomiarowy (2026-06-20 wieczór)

Cel: bundle (LGBM_bundle) = wąskie gardło dwumodelu (forward pairwise ~0,64; top-1 ~0,14-0,22). Arbitraż isotonic łata, ale sufitu nie podniesie. Pytanie measure-first: czy bundle da się poprawić, i jak bardzo.

## G1 — diagnoza (DLACZEGO bundle słaby)
- **best_iteration = 110** (raport mówił „4" = inny val-split; model uczy się normalnie).
- **Importances (gain) bazowego bundle:** bag_size 23% + bag_pickup_pending 15% + orders_today 12% + bag_has_distant_drop 9% ≈ **59% = skład worka + obciążenie**; dystans (delta+dist+haversine) ~20%; reszta szum. **Brak cechy „route-fit"** — czy nowy odbiór jest „po drodze" do istniejących dropów worka. `bag_has_distant_drop` (binarne) + `bag_n_distinct_districts` (1,2%, ledwo używane) = jedyne zgrubne proxy.

## Co NIE pomaga (measured ~0)
| lever | forward pairwise |
|---|---|
| baseline | 0,639 |
| hyperparam sweep (7 configów: lr/leaves/n_est/early-stop/eval_at) | 0,634–0,642 (best +0,003) |
| engineered transforms (7 interakcji istn. kolumn: bagsize×dist, detour_factor, total_load, …) | 0,641 (+0,002) |

Wniosek: cheap fixes wyczerpane. GBM już łapie interakcje surowych cech → transformy nie dodają sygnału.

## Co POMAGA — finer bag-district route-fit features (REAL, robust)
Builder `feature_engineering.bag_districts_features` MA listę `bag_districts` (z `world_state.courier_states`) + graf `district_adjacent`, ale zwija ją do **count + 1 binarna**. Wyciągnięte 5 finer cech z TYCH SAMYCH danych (servowalne live, bag_districts znany w T0, ZERO leakage):
`g_in_bag` (pickup_district ∈ bag), `g_n_adj` (# dzielnic worka sąsiednich do pickupu), **`g_frac_adj`** (frakcja worka „po drodze"), `g_n_distant` (# nie-sąsiednich), `g_all_adj` (korytarz czysty).

| okno | pairwise | bundle top-1 |
|---|---|---|
| eo=0 (Apr07) | 0,639 → **0,653** (+1,4pp) | 0,221 → 0,226 (+0,4pp) |
| eo=28 (Mar09) | 0,651 → **0,665** (+1,4pp) | 0,216 → 0,222 (+0,6pp) |

**Robustne 2/2 okna, konsekwentnie +1,4pp pairwise.** Driver = `g_frac_adj` (10,8% gain). Join hit-rate 92-94%. To JEDYNY lever, który ruszył bundle (vs hyperparam +0,003 / transformy +0,002).

## WERDYKT — qualified GO (inkrementalny), proporcjonalnie
- **GO na finer district features** = jedyna zmierzona dźwignia: servowalne, no-leakage, niski koszt (builder ma dane), domenowo sensowne („po drodze"). **Ale inkrementalne** (+1,4pp pairwise / +0,5pp top-1) — NIE transformacja. Bundle pozostaje słabszą głową.
- **Implementacja (gdy wróci praca nad dwumodelem, po shadow):** dodać 5 finer cech do `feature_engineering.bag_districts_features` (ma `bag_districts`+`district_adjacent`) ORAZ do live `ml_inference.py` (worek kuriera znany w T0) → dataset v2.1 → retrain + walidacja. Esp. `g_frac_adj`.
- **Większy lewar = per-drop coordinate geometry** (realny detour km, nie tylko dzielnice): wymaga przechwycenia bag-drop lat/lon w `world_state` (dziś NIEobecne — builder sam to odnotował: „Bag drops detail not available per-drop lat/lon"). Większa zmiana upstream o NIEPEWNYM zwrocie (label = wybór koordynatora, szumny dla bagged) → **NIE rekomendowane teraz** (malejące zwroty).
- **top-1 = mimikra koordynatora NIE optymalność** — nawet +0,5pp top-1 niekoniecznie = lepszy dispatch; rozstrzyga live (jak całość Fazy 7).

Eksperyment (reprodukowalny): `dispatch_v2/ml_data_prep/bundle_geo_experiment.py` (venv `ml_data_prep/venv`). Powiązane: [[lgbm-twomodel-prod-skew-2026-06-20]], `ZIOMEK_FAZA7_TWOMODEL_SPRINT_2026-06-20.md`.
