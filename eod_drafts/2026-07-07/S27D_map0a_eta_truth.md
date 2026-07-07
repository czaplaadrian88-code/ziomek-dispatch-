# S27-D · Mapa 0a — segmentowana mapa błędu ETA (READ-ONLY, ⏳ ZA WCZEŚNIE)

**Pas D / READ-ONLY · bieg 2026-07-07 ~20:27 UTC · `tools/eta_truth_map.py --since 2026-07-02T12:00`** (bez `--out` → stdout, zero zapisu do stanu). Znak: **− = OPTYMIZM silnika** (odbiór później / dostawa dłużej niż obiecano).

## ⏳ STATUS: NIE MIARODAJNA — okno <7 dni
Okno delivered_at **2026-07-02 10:00 UTC → teraz ≈ 5 dni**. Handoff 27-D: „miarodajna od ~10.07 — Fala A deep-dive". **Traktować jako wczesny podgląd, NIE podstawę kalibracji.** Powtórzyć ≥7-dniowym oknem ~10.07 (świeże peaki wt/śr/czw dojdą).

## Pokrycie joinu (uczciwe n)
- Zleceń w oknie (sla, delivered): **1320** · czasówki pominięte 75 · bez shadow 11 · realny kurier poza pulą 445 → **dopasowane 789** (odbiór 784 / dostawa 784).
- ⚠ 445/1320 (34%) niedopasowanych (realny kurier poza pulą kandydatów) — do zbadania w Fali A (czy artefakt joinu czy realna luka pokrycia).

## Wynik ogólny (2 nogi)
| Noga | n | mediana | p10 | p90 |
|---|---|---|---|---|
| **ODBIÓR** (dojazd-po-odbiór) | 784 | **−4,5 min** (optymizm) | −18,9 | +5,7 |
| **DOSTAWA** (odbiór→klient) | 784 | **+0,4 min** (≈ bez biasu) | −16,2 | +14,6 |

**Potwierdza kanon [[ziomek-calibration-2026-06-29]]:** cały bias optymizmu siedzi w **nodze ODBIORU** (−4,5), noga DOSTAWY jest ~wyśrodkowana (+0,4). Kalibracja obietnicy (K4b/conditional-ETA) powinna celować w odbiór, nie dostawę.

## Segmenty ODBIORU (wczesny podgląd)
- **Tier:** gold −5,1 (n178) · std+ −4,2 (n269) · std −4,4 (n223) · new −2,6 (n88). Gold najbardziej „przeoptymizowany" na odbiorze.
- **solo −5,6 (n161) vs bundle −4,4 (n623).**
- **bag_size:** 1→−5,6 · 2→−3,2 · 3→−5,3 · 4→−5,3 · 5→−4,8 · 6→za mało (n6).
- **obciążenie:** ciasno≤3 −6,0 (n183, najgorzej) · średnio4-6 −5,0 · luźno7-9 −3,7 · duża≥10 −4,0.
- **pora:** peak −4,8 (n396) · shoulder −4,5 (n310) · offpeak −3,6 (n78).

## Wnioski/uwagi metodyczne
- Read-only: bez `--out`, zero zapisu. Predykcja = plan REALNEGO kuriera (nie `best`), logi rotation-aware, czasówki wykluczone. Mediana wieku predykcji 35 min.
- **NIE flip / NIE kalibracja z tego okna** — <7d, ciężar na Pn-wt. Autorytatywny pomiar = Fala A ~10.07 (świeże okno ≥7d).
- Zbieżne z 27-C: warstwa restauracji (fix HTML-escape) domyka część optymizmu odbioru dla 3 restauracji z encjami — po merge fixu re-pomiar 0a pokaże je poprawnie.

---
**Powiązane:** [[ziomek-calibration-2026-06-29]] · `S27C_eta_fix.md` · `tools/eta_truth_map.py` · plan Fala A deep-dive (~10.07).
