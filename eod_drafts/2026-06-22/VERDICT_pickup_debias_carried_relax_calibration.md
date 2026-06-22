# WERDYKT: kalibracja przeładowanych worków — debias odbioru (TIER-1) + carried-first relax (TIER-2)

**Data:** 2026-06-22 · **Tryb:** READ-ONLY analiza (workflow ~4 agentów: korpus `obj_replay_capture.jsonl`, snapshoty `orders_state`, `c2_shadow_log`, shadow archive 16.05) + 1 wdrożenie shadow. `courier_plans.json` NIETKNIĘTY (panel sesji 198 czyta).
**Pytanie Adriana:** co minimalizuje straty mimo za małej liczby kurierów; kalibracja; backtest 16.05 + 21-22.06 (4 KPI). Reguły (handoff sesji 198): #1 carried-first HARD, #2 carry>35→dostawa bije odbiór, #3 panel „+X min" = sesja 198.

## WERDYKT — zmierzone, rule-safe

### TIER-1: realistyczny czas_kuriera (debias) — JEDYNY duży, pewny lewar
- czas_kuriera systematycznie OPTYMISTYCZNY (kurier dojeżdża później). Bias zmierzony **out-of-sample 10 dni: mediana 4,3 / sd 1,2 min** (płaski peak/off).
- **Backtest realny 21-22.06 (bez Marcina): spóźnienie ODBIORU −57% in-sample / −47% out-of-sample** (mediana 5,7→0). ZERO zmiany routingu → szybkość/dostawa fizycznie bez zmian (K3/K4 płaskie). To korekta zbyt optymistycznej OBIETNICY + świeższe jedzenie u źródła.
- **WDROŻONE SHADOW** (commit `2555c28`, tag `pickup-debias-shadow-2026-06-22`): `shadow_dispatcher._serialize_result` loguje `target_pickup_debiased` (=predykcja+`PICKUP_DEBIAS_MIN`=4.5) + `pickup_debias_min`. Flaga `ENABLE_PICKUP_DEBIAS_SHADOW=true` (flags.json). LOG-ONLY, try/except, 19/19 testów serializera, restart dispatch-shadow czysty. Backupy `.bak-pre-pickup-debias-shadow-2026-06-22`.
- ▶ Następnie: obserwacja shadow kilka dni → walidacja live → live-apply (osobny flag, debias przy commit PRZED zamrożeniem R27).

### TIER-2: carried-first relax — JUŻ LIVE i OPTYMALNY
- `ENABLE_CARRIED_FIRST_RELAX=1` LIVE-APPLIED od 22.06 (drop-iny plan-recheck+panel-watcher), `delay_tol=3`.
- **Replay NET per delay_tol (OSRM, wierny mirror `_relax_carried_first`): tol=3 = OPTYMALNY.** NET (GAIN świeżość − HARM opóźnienie innych): tol3 **+1976** / tol5 +1793 / tol10 +1023 / tol15 **−253**. Wyższy tol: GAIN +16% ale HARM ×5,5. **Nie luzować — 3 to optimum.** Rule #2 czysty na każdym progu (0 carry>35, 0 powrotów). +1887 min/dzień zaoszczędzonej jazdy.
- **Mapa rule-safe (LOSS=R6+R27):** split sam +7,6% · V_guarded(≤20) +10,9% · V_guarded+split +19,2% · full-relaks +27,7% ❌(łamie #1, relaksuje stygnące >35). 57% worków ma świeży carry≤20.

### TIER-2 split: marginalny na realnym ruchu
- +~8pp sufit, ale 21-22.06 tylko **5/149 epizodów** ma okazję (same-rest ready Δ>15min) → realnie ≈0. Rule-safe. Blokowany przez `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP`. **NIE wdrażać** (zachód > zysk).

### NO-OP / SZKODA (odrzucone, zmierzone na 2-3 datasetach)
- flip funkcji celu (min-strata vs min-jazda): **+0,1%** — silnik już loss-first (R6+R27 coeff 100, 10:1 nad jazdą).
- age-ordering niesionych: **−4,7%** (i food-age −1,7%) — „najdłużej w aucie"≠„najpilniejsze".
- R27-na-świeżych w celu: **−5%** + łamie #2.

### Pojemność = ściana (nie do ruszenia routingiem)
16.05 (370 dostaw, 10 kurierów): **49% → KOORD, 15-20h meltdown 66-81%, 95% KOORD = ZERO wolnego kuriera.** Jedyne co ruszy SZYBKOŚĆ w peaku: więcej rąk / miękka bramka R6 / ściąć 1304 min/dzień idle pod restauracjami.

## KLUCZOWA PRAWDA
Lewary dają **ŚWIEŻOŚĆ + uczciwą obietnicę, NIE szybkość** (K3/K4 płaskie pod wszystkim). Przy za małej flocie jedzenie nie dojedzie szybciej routingiem (silnik optymalny + brak rąk) — dojedzie świeższe. Szybkość = tylko pojemność.

## LOG KOREKT (pomiar > intuicja — każda „oczywista" liczba się odwróciła)
1. „TIER-2 no-op" (z liczby split 4%) → realnie carried-first relax = lewar (po izolacji).
2. „+25% rule-safe" (haversine bez harm-guard) → faithful V_guarded **+10,9%** (z guardem „tylko poprawa").
3. „luzuj delay_tol 3→10 podwaja" (liczyłem tylko GAIN) → NET pokazuje **tol=3 optimum**, luzowanie szkodzi.
4. „age-ordering = mechanizm Bartka" → to RELAKS, nie age-order; age-order to regresja.
Wniosek meta: baseline MUSI być mirror wdrożonego silnika (nie min-jazda strawman); NET (gain−harm) nie sam gain.

## ROLLBACK
- TIER-1 shadow: miękki `ENABLE_PICKUP_DEBIAS_SHADOW=false` (hot-reload ~60s) lub `git revert 2555c28`+restore flags.json.bak+restart dispatch-shadow.
- TIER-2 relax: rm drop-iny `carried-first-relax.conf` + daemon-reload + restart (kod default OFF). (NIE rekomendowane — optymalny.)

## PLIKI
Kod: `shadow_dispatcher.py`, `common.py` (PICKUP_DEBIAS_MIN). Analiza (tmp, efemeryczne): delaytol2/guarded/cascade2/backtest. Pamięć: `dispatch-overload-calibration-2026-06-22.md`, `bartek-route-carried-first-case-2026-06-22.md`.
