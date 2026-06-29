# SCORE-03 (stopover↔bundle) — werdykt shadow-pomiaru (2026-06-27)

**Kontekst:** Adrian wybrał „shadow-zmierz SCORE-03" z klastra SCORE-03/04/06. Reszta klastra
(SCORE-04 osie czasowe, SCORE-06 backtest wag) = NIE WDRAŻAĆ (werdykt 14.06 `VERDICT_c_redux_measurement`
RE-ZMIERZONY 27.06: korelacja timing_gap↔r6_soft 0,31 nadal ~90% niezależna; breach rośnie z liczbą
osi 11→16% = kary niosą sygnał, nie redundancja).

**SCORE-03 (audyt):** `bonus_r9_stopover` (−8/przystanek, bezwarunkowo per bag) i `bundle_bonus`
(=l1+l2+r4, dodatni za bundlowanie) to dwa termy tej samej decyzji „czy dobundlować" ciągnące
w przeciwne strony (audyt: 11,5% BEST ma oba). Reko audytu: usuń osobny stopover tax (bundle już
liczy overhead) → jeden marginal-bundle-value term.

## Metoda (READ-ONLY)
Skrypt `score03_stopover_bundle_flip.py`. Oba termy SĄ JUŻ addytywne w score → konsolidacja zmienia
wybór tylko gdy zmienia magnitudę. Mierzymy kontrfaktyk reko = `score' = score − bonus_r9_stopover`
i sprawdzamy czy argmax(best vs alternatywy) się zmienia. Dane: `shadow_decisions.jsonl(+.1)`,
clean PROPOSE z best+≥1 alternatywą i poprawnymi score (06-18→27).

## Wyniki (n=2039 decyzji)
- decyzje z jakimkolwiek `stopover<0`: **1938 (95,0%)** — kara prawie uniwersalna (stała, nie różnicownik)
- **sprzeczność w BEST (stopover<0 AND bundle>0): 185 (9,1%)** — potwierdza audyt (~11,5%)
- sprzeczność u któregokolwiek kandydata: 595 (29,2%)
- flip zwycięzcy po usunięciu stopover tax: 34 (1,67%); 11 moot (best wybrany przez override-warstwę, nie score)
- **REALNE flipy (score faktycznie decydował): 23 (1,13%)**

## Werdykt: **NO-GO** (immaterialne)
Sprzeczność jest realna (9,1% BEST), ale realnie zmienia WYBÓR kuriera tylko w **1,13%** decyzji
(23/2039) — daleko pod progiem materialności 20% (ETAP 5 protokołu). Powody:
1. stopover tax prawie uniwersalny (95%) → działa jak stała, rzadko rozdziela kandydatów;
2. mały (−8/stop) wobec termów w dziesiątkach/setkach;
3. gdy flipuje, kierunek = ku kandydatom bundlującym (zgodnie z intuicją reko), ale n=23 za małe
   by udowodnić poprawę outcome (lekcja: nie wnioskować z małej próby).
Ruszanie rdzenia scoringu (95% decyzji przez niego przechodzi) dla ~2 dwuznacznych przypadków/dzień
= ryzyko regresji ≫ zysk. **NIE konsolidować.** Sprzeczność udokumentować jako interpretowalność-only,
nie bug zmieniający selekcję.

## Re-open tylko gdy
E7 re-tune wag bundla/R4 podniósłby udział bundle-decydowanych-przez-score do ≥20% — wtedy stopover
tax zacząłby realnie rozdzielać. Dziś: zamknięte.

**Cały klaster SCORE-03/04/06 = zmierzony NO-GO. Measure-first zatrzymał 3 zmiany grożące regresją przy zerowym/immaterialnym zysku.**
