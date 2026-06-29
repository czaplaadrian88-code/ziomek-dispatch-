# Werdykt: warianty objective vs „wożenie/zygzak" — replay na 190 dzisiejszych trasach (2026-06-01)

**Metoda:** offline replay (zero wpływu na prod). Dla każdej dzisiejszej winner-propozycji
bag≥1 ortools (192, dopasowano 190) wzięto DOKŁADNE wejścia solvera z
`obj_replay_capture.jsonl` (coords/ready/picked_up/now — bez geocodingu) i re-solved
pod 5 konfiguracjami. Porównanie PAROWANE (te same wejścia, różne flagi). V0 repro
logowanej trasy = 76% (reszta = nondeterminizm GLS/ruch — deltas parowane ważne).
Skrypty: `obj_fresh_variant_replay.py`, `obj_fresh_case_replay.py`. Raport: `/tmp/obj_fresh_full.json`.

## Korekta diagnozy (vs pierwsza hipoteza „to OBJ FRESH")
- **77% (147/190) tras jest IDENTYCZNYCH pod wszystkimi 5 wariantami** — optymalne
  niezależnie od objective. Tuning dotyka tylko ~23% ogona.
- Wyłączenie freshness zmienia tylko **8%** tras; włączenie kary za spóźnioną DOSTAWĘ
  (R6-deliv soft) zmienia **16%**.
- **Realny root-cause „wożenia" = objective NIE ma kary za spóźnioną DOSTAWĘ**
  (`ENABLE_OBJ_R6_SOFT_DEADLINE`=OFF). R6 35min to tylko hard-gate post-hoc, nie w
  solverze → solver obojętny ile już-odebrane jedzenie jedzie. OBJ FRESH to dołożyło
  jednostronną siłę „odbierz świeżo" (najbardziej WIDOCZny objaw: nowy odbiór w środku),
  ale sam rollback freshness prawie nie pomaga na carry.

## Wyniki (190 tras, niżej=lepiej dla carry; pickup: niżej=lepiej)
| wariant | odb>10m | carry>35m | existAfterNew | front% | R6 breaches | span | seqΔvs0 |
|---|---|---|---|---|---|---|---|
| **V0 obecny** (fresh c20, R6 off) | **11%** | 31% | 28% | 45% | 79 | 47.0 | – |
| V1 fresh-off | 20% | 27% | 24% | 40% | 65 | 46.8 | 8% |
| V3 fresh c5 | 16% | 27% | 26% | 41% | 66 | 46.8 | 4% |
| **V4 sym** (fresh c20 + R6 on) | 20% | 16% | 21% | 36% | 40 | 46.8 | 19% |
| **V6** (fresh OFF + R6 on) | 25% | **14%** | **18%** | **32%** | **36** | **46.5** | 22% |

## Werdykt
- **V6 (freshness OFF + R6-deliv counterweight ON)** = najlepszy na KAŻDEJ metryce
  geometrii/carry/R6 (carry>35m 31→14%, R6 breaches 79→36, existAfterNew 28→18%,
  front 45→32%). Koszt: punktualność ODBIORU nowego (odb>10m 11→25%) — ale to zgodne
  z Twoją dyrektywą „odroczony odbiór OK, nie wozić".
- **V4** = konserwatywna alternatywa (zostawia freshness): połowi carry/R6, mniejszy
  koszt odbioru (20%), ale NIE rusza sub-35min front-loadów (np. 477710 dalej wozi).
- **V1 sam rollback freshness = ODRZUCONY** (moja pierwsza sugestia): minimalny zysk
  na carry, duży koszt odbioru, dominowany przez V6.
- Każdy wariant = 1 flaga env, odwracalny. R6-deliv soft to gotowy kod (OBJ F1 05-17),
  nigdy nie wflipowany.

## Uczciwy caveat — Twoje cytowane case'y
7/10 cytowanych tras (477706/477631/477652/477651/477636/477685/477752) jest
**optymalnych pod każdym wariantem** — sekwencja dobra, „na końcu" byłoby GORZEJ
(wyższa nieświeżość, zero zysku dla istniejącego jedzenia). Tam problem (jeśli jest)
to **SELEKCJA kuriera** (bundling przeciw-kierunkowy) — warstwa scoring R1/R5
directionality, NIE objective TSP. Albo błąd coords/geocoding (jak Filipowicza).
Tylko 3/10 (477632/477718/477710) to realny freshness-front-load → naprawia V1/V6.

## Następne kroki (nic nie wdrożono)
1. Wybór wariantu (rekomendacja V6, alt V4).
2. Shadow-validation wybranego na świeżym peaku przed flipem.
3. Osobno: analiza SELEKCji (czemu kurier przeciw-kierunkowy wygrywa) dla cytowanych
   optymalnych-pod-każdym-wariantem tras.
