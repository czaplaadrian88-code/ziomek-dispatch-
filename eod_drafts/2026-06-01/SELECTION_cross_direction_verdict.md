# Werdykt: selekcja przeciw-kierunkowa — faithful re-ranking na 259 dzisiejszych decyzjach

**Metoda (read-only):** dla każdej decyzji wzięto logowaną pulę konkurencyjną (best+alternatives,
feasible, z PEŁNYM rozbiciem score). Zmieniano TYLKO składnik kierunkowy (R1 corridor+progressive,
faithful: legacy clip ×spread_mult + progressive −45/−60/−100, flagi jak prod) i przeliczano
zwycięzcę pod 3 modelami: M_score (czysty argmax), M_bucket (bucket informed>other>blind + score),
live. Skrypt: `selection_variant_replay.py`.
**Fidelity:** M_bucket odtwarza live w **74%**, M_score w 56% → różnica = override tier/bucket.
Limit: pula logowana ucięta (mediana 3), tier per-kandydat nie serializowany (bucket = proxy).

## Dekompozycja 18 przeciw-kierunkowych zwycięzców (cos<−0.3) — PRZYCZYNA
| przyczyna | n | lever |
|---|---|---|
| wygrał TEŻ na score (kara kierunku za słaba) | **1** | kara kierunku (S1/S2/S4) |
| przegrał lepszy-score nie-cross → **TIER/BUCKET override** | **10** | klucz selekcji |
| brak nie-cross w puli (**scarcity floty**) | **7** | nieredukowalne (always-propose OK) |

## Warianty KARY KIERUNKU (S1–S5) — ODRZUCONE
% zwycięzca cross pod M_bucket: cos<−.3 / cos<−.7
| wariant | cos<−.3 | cos<−.7 |
|---|---|---|
| S0 baseline (live) | 6.2% | 2.3% |
| S2 kara ujemna ×1.5 | 5.0% | 1.2% |
| S5 spread-aware + dist | 6.6% | 2.7% |

Wniosek: wzmocnienie kary kierunku ledwo rusza agregat (kara JUŻ jest mocna: cos<−0.7 ≈ −100 w score).
Cross-dir prawie nigdy nie wygrywa NA SCORE (1/18) → tuning scoringu kierunkowego to ślepa uliczka.

## Warianty KLUCZA SELEKCJI (veto kierunkowe) — TO JEST LEVER
baseline=M_bucket; veto: gdy zwycięzca cos<próg a w puli jest feasible nie-cross → bierz najlepszy nie-cross.
| wariant selekcji | cos<−.3 | cos<−.5 | cos<−.7 | flipy | →pusty | →bag-aligned |
|---|---|---|---|---|---|---|
| baseline M_bucket | 6.2% | 3.1% | 2.3% | 0 | 0 | 0 |
| veto cos<−.5 → nie-cross(any) | 4.6% | **1.5%** | **1.2%** | 4 | 4 | 0 |
| veto cos<−.5 → nie-cross(informed) | 5.4% | 2.3% | 1.5% | 2 | 0 | 2 |
| veto cos<−.7 → nie-cross(any) | 5.0% | 1.9% | 1.2% | 3 | 3 | 0 |

Veto kierunkowe POŁOWI mocno-przeciwne selekcje (cos<−.5: 3.1→1.5%, cos<−.7: 2.3→1.2%).
**Haczyk:** alternatywa to prawie zawsze PUSTY kurier (zlecenie powinno iść SOLO do wolnego, nie w bundle).
Te puste są w bucket-2 (no_gps/pre_shift) → zdemotowane → bundle wygrywa. „→informed only" = bezpieczny
podzbiór (2 flipy, do znanych pozycji), „→any" = agresywny (4 flipy, do mniej pewnych kurierów).

## Root cause (selekcja, NIE objective, NIE kara kierunku)
**Bucket-2 demote wolnego-aligned kuriera + late-pickup tier-2** oddają zlecenie zajętemu kurierowi
jadącemu w przeciwną stronę, zamiast SOLO wolnemu jadącemu tam. Kara kierunku działa, ale klucz
selekcji ją nadpisuje (bucket/tier przed score). 7/18 = realna scarcity floty (nieredukowalne).

## Rekomendacja (nic nie wdrożono)
1. **NIE ruszać scoringu kierunkowego** (S1–S5 nieskuteczne).
2. **Prototyp zmiany KLUCZA selekcji** jako SHADOW (jak `late_pickup_shadow`): guard „nie nadpisuj
   bucketem/tierem dobrze-skierowanego kandydata na rzecz mocno-cross (cos<−0.5/−0.7), gdy istnieje
   feasible nie-cross" — z dialem bezpieczeństwa (informed-only vs any). Zgodne z dyrektywą
   „odrocz odbiór, nie łam kierunku" (veto = późniejszy odbiór solo zamiast cross-bundla).
3. **Walidacja:** model offline ma 74% fidelity → definitywny sign-off przez in-pipeline shadow
   (flagi OFF, zero zmiany zachowania) przez kilka peaków, potem decyzja.
4. Spread-blind cosine (477752: cos+0.974 / spread 13km) = osobna, rzadsza luka (spread>8 u 25%
   zwycięzców; S5 tnie do 23%). Niski priorytet.
