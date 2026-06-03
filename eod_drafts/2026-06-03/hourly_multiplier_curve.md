# Krzywa godzinowa mnożnika OSRM (median-based) — kandydat do V326_OSRM_TRAFFIC_TABLE

**Data:** 2026-06-03
**Źródło:** GATE B `rw_results.jsonl` ⨝ `trips_realworld.jsonl`, 595 tropów weekday (dedup oid).
**Metoda:** per godzina (Warsaw) `mult = median(ground_truth_drive / osrm_freeflow)`.
Median (nie mean) — celowo NIE goni ogona (outliery −5/−10 min); zeruje TYPOWY trop.
Cross-check: tier-1 (czysty GPS) potwierdza kształt ±0,15.

## Diagnoza: obecna tabela V326 wciąż zaniża (mediana rezyduum)

| godz | n | obecny mult | rezid. OBECNY (med) | PROPON. mult | rezid. nowy |
|---:|---:|---:|---:|---:|---:|
| 10 | 13 | 1,10 | **−1,16** | 1,30 | ~0 |
| 11 | 35 | 1,10 | −0,62 | 1,25 | ~0 |
| 12 | 45 | 1,20 | **−1,12** | 1,40 | ~0 |
| 13 | 46 | 1,20 | **−2,14** | 1,50 | ~0 |
| 14 | 57 | 1,20 | −0,48 | 1,35 | ~0 |
| 15 | 45 | 1,50 | **−2,18** | 1,60 | ~0 |
| 16 | 58 | 1,30 | **−2,44** | 1,55* | ~0 |
| 17 | 71 | 1,20 | −0,05 | 1,30* | ~0 |
| 18 | 71 | 1,20 | **−1,21** | 1,35 | ~0 |
| 19 | 54 | 1,10 | **−1,19** | 1,25 | ~0 |
| 20 | 57 | 1,00 | −0,79 | 1,10 | ~0 |
| 22 | 18 | 1,00 | −0,83 | 1,10 | ~0 |

*godz 16/17 = blend z tier-1 GPS (full-sample dawał 1,65/1,20; tier-1 1,45/1,39 → kompromis 1,55/1,30).

Rezyduum OBECNY = `median(osrm_eta_min − ground_truth)` (osrm_eta już ma obecny mnożnik!).
Czyli: nawet PO obecnej kalibracji popołudnie 12–16 i 18–19 zaniża ~1–2,4 min medianowo.

## PROPONOWANA tabela weekday (V326-format, zaokr. 0,05)

```python
"weekday": [
    (0, 9, 1.0),
    (9, 10, 1.15),
    (10, 12, 1.25),
    (12, 13, 1.40),
    (13, 14, 1.50),
    (14, 15, 1.35),
    (15, 17, 1.55),   # 15-16 i 16-17 (tier-1 blended)
    (17, 18, 1.30),
    (18, 19, 1.35),
    (19, 20, 1.25),
    (20, 21, 1.10),
    (21, 24, 1.05),
],
```

Saturday/Sunday: **bez zmian** (za mało danych weekend; zostaje obecna tabela).

## Co ta krzywa robi / czego NIE robi

DOES:
- Zeruje systematyczne medianowe niedoszacowanie per godzina (głównie 12–16, 18–19).
- Darmowe (zero API), zero ryzyka — sama rekalibracja istniejącego mechanizmu.

DOESN'T (świadomie):
- NIE goni ogona breachy (−5/−10 min outliery) — to wariancja, nie bias → zadanie dla live-traffic (A/B).
- NIE rozróżnia krótkie centrum vs długie międzydzielnicowe — to osobny BUG-D distance-bin v2 (shadow); można nałożyć później.
- Weekday only; seasonalne (maj-czerwiec) → rekalibrować co ~kwartał.

## Jak wpiąć — 2 ścieżki

### Ścieżka 1 (TEST, zero ryzyka): nowe RAMIĘ w teście A/B
Krzywa = `OSRM-recalib` = `osrm_freeflow_min × new_table[hour]`, liczona OFFLINE z już
logowanego `osrm_freeflow_min`. Nie dotyka produkcji. Mierzona bcRMSE obok TomTom/HERE A/B
jako **darmowa trzecia opcja**. To rozstrzyga „ile daje sama rekalibracja vs płatny ruch".

### Ścieżka 2 (PRODUKCJA, po werdykcie): promocja do V326 live
- Podmień blok `"weekday"` w `common.py:V326_OSRM_TRAFFIC_TABLE`.
- Mechanizm shadow/live już istnieje (`ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER`, default ON).
- Zalecenie: najpierw shadow-record (porównaj `traffic_multiplier_shadow` vs realja kilka dni),
  potem live. Lub: skoro tabela jest już LIVE, podmiana to bezpośrednia zmiana wartości —
  monitoruj bias po (oczekiwany spadek niedoszacowania popołudniowego).

## Caveat metodyczny
tier-2 ground_truth = `delivery_time − median_nondrive` (możliwy resztkowy dwell leakage).
tier-1 (GPS) potwierdza kształt, ale rozjazd ±0,15 na kilku godzinach → traktuj wartości
jako ±0,15 niepewne. Przy promocji do live preferuj lekko konserwatywne (niższe) zaokrąglenie
na godzinach gdzie tier-1 < full-sample (16, 21).
