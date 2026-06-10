# Baseline historyczny acceptance — propozycja vs kurier finalny

Źródło: `/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl` (3139 rekordów; ocenialne 3056; orderów z >1 propozycją w pliku: 1279).
Pominięte: status=None=60, status=returned_to_pool=23.

**Metryka:** `proposed_courier_id` (best propozycji) == `outcome.courier_id_final` (kurier, który DOWIÓZŁ).
**Caveat:** reassign po drodze liczy się jako rozjazd → baseline to DOLNE przybliżenie acceptance (PANEL_AGREE na żywo mierzy moment przypisania, nie finał).

## OGÓŁEM (wszystkie propozycje): **11.6% (354/3056)**
## OGÓŁEM (per order, OSTATNIA propozycja — definicja PANEL_AGREE): **18.0% (307/1701)**

Sekcje niżej liczone na widoku per-order (ostatnia propozycja):

## Per tier
- std: 17.1% (143/837)
- gold: 12.7% (55/432)
- std+: 25.3% (94/371)
- new: 31.2% (15/48)
- slow: 0.0% (0/13)

## Pora (peak 11-14/17-20 Warsaw)
- peak: 19.5% (186/952)
- off: 16.2% (121/749)

## Typ
- elastyk: 18.0% (307/1701)

## Verdict propozycji
- PROPOSE: 18.0% (307/1701)

## Akcja w learning_log (ostatniej propozycji)
- PANEL_OVERRIDE: 2.1% (26/1264)
- TIMEOUT_SUPERSEDED: 64.3% (272/423)
- ASSIGN_DIRECT: 64.3% (9/14)

## Dzień (Warsaw)
- 06-07: 17.8% (51/286)
- 06-04: 21.5% (48/223)
- 06-05: 16.7% (37/222)
- 06-02: 14.1% (28/198)
- 06-01: 15.1% (29/192)
- 06-03: 20.4% (33/162)
- 06-08: 16.0% (23/144)
- 06-09: 20.3% (28/138)
- 06-06: 22.1% (30/136)

## Score / margin OSTATNIEJ propozycji: zgodne vs rozjechane (|x|<1000, Z-18)
- score zgodnych: śr 16.4, med 3.2, n=307
- score rozjechanych: śr 22.7, med 5.2, n=1394
- margin zgodnych: śr 36.4, med 8.3, n=307
- margin rozjechanych: śr 33.6, med 6.5, n=1391

_Wygenerowano: 2026-06-10T19:13:30.556376+00:00 (eod_drafts/2026-06-10/panel_agree_baseline.py, read-only)._
