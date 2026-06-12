# Diagnoza alarmu strażnika at#138 (gate-fix 30a01d2) — 12.06 wieczór, PRZED odpaleniem strażnika

**Pisane ~20:30 UTC, t.j. godzinę przed strażnikiem** — wczesny odczyt pokazał, że alarm
ODPALI (KOORD-rate w oknie post-fix 18:33→20:10 = 62,5%, low_score 46% — progi 25%/20%).
Diagnoza wykonana z wyprzedzeniem zgodnie z trybem alarmowym promptu nocnego.

## Werdykt: GATE-FIX DZIAŁA; alarm = artefakt okna; kill-switch NIEUZASADNIONY

**Atrybucja per-rekord (15 KOORD od 18:33 UTC): 0/15 byłoby PROPOSE po zdjęciu
WSZYSTKICH delt flagowych** (sync + loadgov + R5 detour). Score bez delt: −107..−1448,
wszystkie poniżej MIN_PROPOSE_SCORE=−100. Mechanika identyczna jak przy wykryciu
incydentu (lekcja #188), wynik przeciwny: tym razem kary rankingowe NIE są przyczyną.

| oid | reason | score | sync | loadgov | r5 | score bez delt |
|---|---|---|---|---|---|---|
| 480268 | low_score | −285,1 | −150 | 0 | −27,9 | **−107,1** (najbliżej progu) |
| 480272 | low_score | −327,5 | −150 | −40 | −19,8 | −117,7 |
| 480276 | low_score | −338,7 | −150 | −40 | −15,0 | −133,7 |
| 480278 | r6_breach ×4 | −1638,2 | −150 | −40 | 0 | −1448,2 |
| … (pełna tabela: transcript / skrypt poniżej) | | | | | |

## Co naprawdę się dzieje: nędza wieczorna (scarcity), nie regresja flag

Okno 18:33-20:15 UTC (20:33-22:15 Warsaw) dziś vs poprzednie dni:

| Dzień | n | score_med best | pool_feasible_med | bag_med | sync_med |
|---|---|---|---|---|---|
| 05.06 (pt) | 15 | **+10** | 3 | 1 | 0 |
| 08.06 | 10 | +71 | 4,5 | 0 | 0 |
| 11.06 (era incydentu) | 18 | −17 | 1,5 | 1 | −74 |
| **12.06 (post-fix)** | 24 | **−338** | **1,0** | **3,0** | −150 |

- **pool_feasible spadł do 1** — feasibility zostawia 1-2 kurierów (koniec zmian;
  loadgov widzi 10 aktywnych, ale 9/10 odpada na bramkach twardych).
- Jedyny feasible niesie **worek 3-6** → kumulują się PRAWDZIWE kary jakości
  (R6 soft −78/−115, wait −56, R8/R9) — to one spychają poniżej −100, nie delty.
- 4/15 KOORD to `best_effort_r6_breach` (480278: breach_orders=4) — twarda reguła
  35 min, KOORD słuszny z definicji.
- Kara sync osiąga pełny węzeł −150 (spread ≥20 min — bo do pełnego worka dokleja
  się cokolwiek), ale **bramka liczy score bez niej** — sanity-countery strażnika
  („PROPOSE z karą sync w rankingu") to potwierdzą.

## Zalecenie (dla Adriana i sesji, która zobaczy alarm)

1. **NIE flipować `ENABLE_BUNDLE_SYNC_SPREAD=false`** — atrybucja 0/15: kill-switch
   nie odzyskałby ANI JEDNEJ propozycji, a wyłączyłby kierunkowo dobrą karę przed
   sobotnim peakiem (detour_med 1,48 < baseline 2,01).
2. Realny temat #1: **obsada końcówki wieczoru** (22-23:30 Warsaw) — klasyczne prawo
   obciążenia B2; alarm obsady D-1 (QW7/OBSADA w briefingu) to właściwy kanał.
3. Realny temat #2 (na E7, nie na noc): **ALWAYS-PROPOSE vs MIN_PROPOSE_SCORE=−100
   przy poolu=1** — przypadek „ostatni kurier z workiem" produkuje ciszę zamiast
   propozycji z odroczonym odbiorem (case 480268: bez delt −107, o 7 pkt za nisko).
   To jest dokładnie klasa z [[feedback-always-propose-defer-pickup]] (opcja
   obniżenia progu / propozycja best-effort z odroczeniem); decyzja = ACK Adriana.
4. Próg strażnika 25% liczony na oknie zdominowanym przez późny wieczór — przy
   ewentualnej powtórce skryptu warto raportować rozbicie per godzina (at#137
   niedziela ma pełne 2 dni, więc tam problem znika sam).

*Metoda: read-only shadow_decisions; skrypt atrybucji = inline w transcript (join
bonus_sync_spread + bonus_loadgov_shadow_delta + bonus_r5_pickup_detour_penalty).*

---

## EPILOG — strażnik odpalił 21:30 UTC, diagnoza POTWIERDZONA na finalnym oknie

Wynik at#138 (n=31, okno 18:33→21:30 UTC): KOORD 17 (54,8%), low_score 12 (38,7%) →
🔴 ALARM zgodnie z przewidywaniem. Sanity-countery strażnika: **PROPOSE z karą sync
w rankingu 13/14** — bramka liczy score bez delt, kara dalej rankuje. Atrybucja
per-rekord odświeżona na pełnym oknie: **0/12 low_score byłoby PROPOSE po zdjęciu
wszystkich delt flagowych** (sync+loadgov+R5). Kill-switch pozostaje NIEUZASADNIONY;
flagi nietknięte (SYNC/LOADGOV ON). Realny werdykt 2-dniowy = at#137 (nd 14.06 08:30
Warsaw). Adrian uprzedzony o artefakcie okna w czacie ~23:00 (handoff sesji A pkt d).
