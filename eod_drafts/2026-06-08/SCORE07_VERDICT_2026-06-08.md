# SCORE-07 — werdykt: SUPERSEDED, NIE zmieniać na feasibility NO (2026-06-08)

Audyt 03.06 [P2, verdict="—", conf medium]: „wait penalty -700/-1000 to SCORE nie hard;
TSP fallback bez time_windows usuwa twardość okna → ryzyko 'stare propozycje +1h'.
REKO: zamień -1000/-700 na feasibility NO; w fallbacku zachowaj twarde okno odbioru."

## Diagnoza żywego stanu (Z2: root cause przed fix)
1. **Dwie RÓŻNE osie wait** (audyt je myli z jedną):
   - `compute_wait_penalty` (V327, scoring.py:80): `wait = max(0, plan.pickup_at[oid] - order_ready)`
     = JEDZENIE STOI w restauracji po gotowości. → -700 (@60min), -1000 (>60, B3 OFF). SCORE.
   - `compute_wait_courier_penalty` (V3273, scoring.py:120): `wait = max(0, ready - chain_arrival)`
     = KURIER czeka idle przed restauracją. → **HARD REJECT >15min** (common.py:1794, egzekwowane
     dispatch_pipeline.py:3637). To INNA oś — nie zabija food-sitting.
2. **Food-sitting >60 JEST łapane przez late-pickup hard gate (LIVE od 05-31, `ENABLE_LATE_PICKUP
   _HARD_GATE=1`)**: dla NOWEGO zlecenia `new_pickup_late_min = pickup_at - pickup_ready` = DOKŁADNIE
   ten sam `wait_min` → `new_pickup_needs_extension=True`. Dla committed (czas_kuriera) →
   `late_pickup_committed_breach=True` → demota do najniższego tieru. Gate działa POST-SOLVE na
   `plan.pickup_at` — niezależnie czy plan z okien czy z fallbacku bez okien (SCORE-07b pokryte).
3. **Committed late ma DRUGI live guard**: commit-divergence verdict-gate (BUG C, LIVE 05-27,
   `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE`): divergence>10min plan-vs-commit → KOORD.

## Werdykt: NIE implementować rekomendacji audytu
- **„Zamień na feasibility NO" PRZECZY doktrynie ALWAYS-PROPOSE.** Komentarz dispatch_pipeline.py:2887
  wprost: „Selekcja = tiering (NIE hard-reject) → ZAWSZE jest propozycja (reguła Adriana 'zawsze daje
  propozycje'). Post-solve (NIE okno TSP — lekcja E3)." Zespół CELOWO wyniósł late-pickup z twardego
  okna TSP do miękkiego tieru po 05-31. Reverting = regres always-propose.
- **-700/-1000 w score = redundantny-ale-nieszkodliwy** sygnał rankingu (dominuje stack bonusów —
  audyt sam przyznaje „-700 i tak dominuje"); właściwym mechanizmem jest teraz late-pickup tier gate.
  Konwersja na NO duplikowałaby gate i łamała always-propose.
- **NIE ruszać fallbacku bez time_windows** (route_simulator_v2.py:1094): istnieje JAKO anti-regresja
  („flag flip nigdy nie powinien zwiększyć liczby orderów które fail completely; gorsza sekwencja >
  brak proposal"). Dodanie z powrotem twardego okna grozi re-wprowadzeniem complete-failure (klasa
  #471036 „BRAK KANDYDATÓW"). Late-pickup gate post-solve i tak łapie skutek.

## Klasyfikacja: SUPERSEDED przez mechanizmy wdrożone PO audycie 03.06
late-pickup hard gate (05-31, tiering ALWAYS-PROPOSE-aligned) + commit-divergence gate (05-27, KOORD)
+ V3273 courier-idle hard reject (>15). Wszystkie LIVE. Audyt (03.06) widział gate ale przeoczył że
jest CELOWO tieringiem; jego reko „feasibility NO" jest częścią sprzeczną z doktryną. Zero zmian kodu.

## Pozostaje (osobne, NIE SCORE-07)
SCORE-06 (backtest WSZYSTKICH wag score na backfill outcomes — duże, wymaga świeżego feedu który
właśnie naprawiłem) i SCORE-10 (picked_up doomed >35 dostaje dokładkę — score-penalty nie reject;
medium, conf low) zostają otwarte w todo_master jako osobne pozycje.
