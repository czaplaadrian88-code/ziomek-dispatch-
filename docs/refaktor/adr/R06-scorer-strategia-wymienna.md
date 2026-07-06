# ADR-R06: Scorer jako strategia wymienna z fallbackiem heurystycznym

Status: proponowany

## Kontekst
Scoring SOFT jest dziś rozlany: `scoring.score_candidate` (288 l., czysty) + kary w `dispatch_pipeline` + funkcje kar w `common.py` (`carry_chain_penalty`, `bundle_score_multiplier`, `extension_penalty`…). Równolegle biegną 3 tory LGBM — wszystkie SHADOW, `ENABLE_LGBM_PRIMARY`=OFF, bez pętli retreningu (modele=zamrożone pliki). Brief wymaga „wymiennych strategii scoringu z fallbackiem heurystycznym"; przyszły flip LGBM nie może wymagać przebudowy pipeline'u.

## Decyzja
Interfejs `Scorer: (world, candidate, kontekst) → score + terms` w czystym rdzeniu:
1. `HeuristicScorer` = dzisiejsza suma ~19 kar/bonusów, skonsolidowana (kary z common/pipeline przenoszone do modułu scoringu przy krokach, które ich dotykają) — implementacja DOMYŚLNA.
2. `LgbmScorer` = opakowanie istniejącej inferencji jako strategia za flagą; **każdy błąd/timeout/brak modelu = automatyczny fallback do HeuristicScorer** (fail-soft z metryką `scorer_fallback` w shadow logu).
3. Shadow-tory LGBM bez zmian (dalej logują równolegle); flip primary = pełny protokół #0 ETAP 5 (replay ON↔OFF na korpusie ADR-R04), poza zakresem tego programu.

## Konsekwencje
- Formalizuje granicę HARD/SOFT: Scorer NIE widzi feasibility inaczej niż przez wynik (SOFT nie osłabia HARD — P0).
- `scoring.py` przestaje być mylną nazwą — staje się faktycznym domem warstwy 6.
- Wymiana strategii = konfiguracja, nie refaktor; fallback gwarantuje always-propose nawet przy awarii ML.

## Źródła
`raw/01c-sprzezenia.md` §1a (scoring rozlany); `raw/01e-samouczenie.md` §1 (LGBM shadow); `ZIOMEK_ARCHITECTURE.md` F-2; brief Fazy 3 wariant B.
