# Sprint speed-mult gold (2026-07-18) — EVIDENCE: TODO werdyktu D3 domknięte POMIAREM

GO Adriana: „Dawaj kalibrację speed-mult gold". Zadanie z werdyktu D3 (29.06):
„zweryfikować realny czas dostawy gold vs ETA; skalibruj ich prędkość dobrze".
Wynik: **werdykt liczbowy NIE FLIPOWAĆ per-tier** — measure-first zadziałał w obie strony.

## ETAP 0 — stan

- Mechanizm JUŻ ISTNIEJE: `speed_mult_for_tier` za `ENABLE_DRIVE_SPEED_TIER_CORRECTION`
  (hot-reload; flags.json **False** = inert 1.0; konsumenci: feasibility/plan_recheck/
  route_simulator/core.planner). Tabela z 26.06: gold 0.78, std/std+ 0.82 („Krok 1
  agresywny", liczony na CAŁKOWITYM czasie dostawy floty, n=657).
- Baseline PRZED: **5170/0** (27 skip zegarowe).
- Dane: `eta_calib.db` (baza kalibratora per-leg z 07.07) — 8779 zleceń 08.05-17.07,
  kolumny per-order: ts_pickup/ts_deliver, osrm_deliv_ff_min, actual_deliver_min.
- Gold cids (7): 21, 61, 123, 179, 413, 471, 509.

## POMIAR (skrypt `gold_speed_mult_measure.py`)

1. **Pierwszy bieg (naiwny) = lekcja C10 na żywo:** ratio actual/pred ≈ **1.5-1.7 dla
   WSZYSTKICH tierów** — artefakt kompozycji (actual per-order zawiera POŚREDNIE stopy
   worka; predykcja = bezpośrednia noga). Odrzucony.
2. **Composition-clean:** tylko CZYSTE bezpośrednie nogi (zero eventów innego zlecenia
   tego kuriera między pickup a deliver) = 2953/8779 (34%). Predykcja silnikowa:
   `osrm_ff × traffic_mult_v1(ts)`; actual jazdy = actual − dwell_dropoff(tier).
   Filtry: ff>0.5, actual>1.5 (batch-click), 0.2<ratio<4.
3. **Mediany ratio (2 okna SPÓJNE):**

   | tier | 04.07-17.07 (n) | 14.06-03.07 (n) | tabela 26.06 |
   |---|---|---|---|
   | gold | **0.961** (183) | 0.967 (226) | 0.78 ❌ |
   | std+ | 1.057 (252) | 1.096 (494) | 0.82 ❌ |
   | std  | 0.860 (267) | 0.921 (376) | 0.82 ~ |
   | new  | 0.950 (167) | 1.116 (61, niestabilne) | 1.0 |
   | slow | 0 danych | 0 danych | 1.0 |

4. **MAE ETA dostawy (drive+dwell, PRIMARY, n=924):** live(1.0) **3.01** · tabela 26.06
   **2.96** (gold 2.45→2.45 = ZERO poprawy dla gold; std+ POGARSZA 2.83→2.95) ·
   ZMIERZONA **2.92** (gold 2.40 = −2%).

## WNIOSKI

- **Tabela 26.06 OBALONA:** gold NIE jeździ w 78% czasu modelu — jeździ w ~96%.
  Flip 0.78 zaniżyłby ETA gold o 22% → fałszywe „zdąży" → realne breache R6.
  Klasa „NIE wskrzeszać obalonych".
- **Cała klasa per-tier drive-mult = zysk maks ~3% MAE (5 s)** — o rząd wielkości
  słabszy niż CZEKAJĄCY kalibrator per-leg/per-KURIER z 07.07 na TEJ SAMEJ bazie
  (dostawa −20%, odbiór −52%; cień biegnie od 07.07, okno 2 dni dawno minięte).
  **Właściwa realizacja werdyktu „skalibruj ETA gold dobrze" = flip kalibratora
  per-kurier (decyzja Adriana, wisi w todo od 07.07)** — per-kurier bije per-tier
  granularnością (różnice wewnątrz gold > różnice między tierami; IQR ±30%).
- Werdyktowa hipoteza „gold wyrabiają bo szybcy" w danych: na JEŹDZIE tylko ~4%
  szybciej od modelu; przewaga gold siedzi w dwell (osobno tier-aware) i jakości tras.

## CO ZMIENIONE (kod nie kłamie; zachowanie NIE)

- `common.py`: tabela → wartości ZMIERZONE (0.96/1.06/0.86/1.0/0.95) + komentarz
  z obaleniem, liczbami i przestrogą; **flaga zostaje OFF** → zero zmiany decyzji.
- NOWY test `tests/test_drive_speed_mult_d3gold.py` 3/3: OFF-inert dla każdego tieru ·
  tabela==pomiar (anti-drift, zmiana wymaga nowego pomiaru) · ON czyta tabelę
  (mechanizm żywy na przyszły flip za ACK).

## DoD — tokeny mechaniczne

regresja: patrz Wyniki końcowe (pełna suita po zmianie)
e2e: pomiar na ŻYWEJ bazie kalibratora (eta_calib.db, 8779 realnych zleceń, predykcja odtworzona silnikowymi funkcjami traffic+dwell) + MAE-porównanie 4 wariantów; zachowanie silnika NIETKNIĘTE (flaga OFF, tabela nieczytana przy OFF — test OFF-inert)
replay: ON↔OFF policzony offlinowo na korpusie 924 nóg: warianty tabel vs live — zysk zmierzonej ~3% MAE (gold −2%) = PONIŻEJ progu opłacalności flipu vs kalibrator per-kurier −20%; flip świadomie ZANIECHANY (werdykt negatywny też jest werdyktem measure-first)
rollback: nie dotyczy zachowania (flaga OFF przed i po); wartości tabeli = git revert

N-D: feasibility_v2.py — konsument przez speed_mult_for_tier, gałąź martwa przy OFF (test OFF-inert)
N-D: route_simulator_v2.py — jw.
N-D: plan_recheck.py — jw.
N-D: core/planner.py — jw. (k15 21/21 zielone)

## Wyniki końcowe

- **Finalna pełna regresja: 5173 passed / 0 failed** (= baseline 5170 + dokładnie 3 nowe testy; 27 skip zegarowe, 8 xfail).

regresja: 5173 passed, 0 failed (baseline 5170 + dokładnie 3 nowe testy; log gold_final_pytest)
