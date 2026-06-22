# Werdykt: propagacja czas_kuriera_warsaw w re-sekwencerze (plan_recheck)

**Data:** 2026-06-22 · **Trigger:** case Michał K. Goodboy(482630)+Sushi(482633) — odbiór sushi +9 min (łamie ±5).

## Root cause (potwierdzony w kodzie)
`OrderSim` nie ma pola `czas_kuriera_warsaw`. Egzekucja punktualności committed w `route_simulator_v2`
(okno frozen V3.27.4 :955, miękka kara N5 :1145 coeff=100, post-solve assercja :1310) czyta
`getattr(ref,"czas_kuriera_warsaw")`. `dispatch_pipeline.py:2642` dokleja je ręcznie → działa przy
PRZYPISANIU. `plan_recheck._gen_one_bag_plan` NIE dokleja (committed tylko jako `pickup_ready_at` =
dolna granica) → w RE-SEKWENCERZE kara/okno = ciche no-opy. Stąd +9 min puszczone.

## Fix (zrobiony, GATED, default OFF)
`plan_recheck.py`: flaga `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION` (env, default OFF) → doklej
`sims[oid].czas_kuriera_warsaw = rec.get(...)` (jak dispatch_pipeline). Default OFF = zero zmiany na żywo.
Backup: `plan_recheck.py.bak-pre-committed-propagation-2026-06-22`. Testy: 19 pass (canon+sequence-lock).

## Walidacja na oryginalnym case (482630+482633, pos GPS 14:46, OR-Tools+kara ON)
| | sekwencja | sushi odbiór vs 17:04 | SLA dostaw | trasa |
|---|---|---|---|---|
| BASELINE | P-GB→**D-GB→P-SU**→D-SU | **+10.0 (łamie ±5)** | 0 | 45.6 |
| FIXED | P-GB→**P-SU→D-GB**→D-SU | **+4.2 (w ±5)** ✅ | 0 | 46.7 |

Fix daje DOKŁADNIE wariant oczekiwany przez Adriana, ZERO kosztu SLA dla tego case (+1.1 min trasy).

## Replay na korpusie (65 worków ≥2 z ≥2 committed, log 21-22.06 + orders_state + realny GPS)
- **Przestawione przez fix: 60% (39/65)**
- **Punktualność odbioru ≤+5 min: 27.6% → 44.4%**; mediana lateness +14.0 → +6.1; zero INFEASIBLE/degradacji.
- **KOSZT — Δ SLA dostaw: +23 (18 worków gorzej, 1 lepiej)**; Δ trasa +0.27 min/worek.

### Cut wiarygodny — ŚWIEŻE worki (committed ∈ [ts−5,ts+45], bez artefaktu sprasowania) = 24
- Przestawione 50%; **≤+5 min: 58.8% → 72.1%** (realistyczne absoluty); mediana +2.8→+2.0, p90 +17.8→+9.6.
- **Δ SLA dostaw: +7 (5 gorzej, 0 lepiej).**

### Split per rozmiar (koszt rośnie z rozmiarem)
n=2: ΔSLA +2/21 · n=3: +10/28 · n=4: +6/10. Czysta wygrana 12 / trade-off 18.

## Interpretacja
Fix DZIAŁA i jest bezpieczny (no INFEASIBLE, gated). Poprawia punktualność odbioru (+13–17 pp ≤±5,
mediana lateness ~½). ALE wprowadza realny koszt na **SLA dostaw 35 min (też twarda reguła R6)** —
miękka kara committed coeff=100 jest na tyle silna, że potrafi zaakceptować naruszenie dostawy, by
trafić odbiór. To napięcie odbiór-vs-dostawa (oba twarde) — stąd historyczny soft-design (7500 INFEASIBLE/d).

## Rekomendacja (NIE blanket-flip coeff=100)
Najczystsza opcja = **tie-breaker bez regresji dostaw**: w `plan_recheck` policz sweep DWA razy
(z propagacją i bez) i przyjmij wariant punktualny TYLKO gdy jego `sla_violations` dostaw ≤ baseline.
Na korpusie zachowuje 12 czystych wygranych (w tym case Michała: SLA 0→0), odrzuca 18 trade-offów →
Pareto-poprawa. Koszt: 2× OR-Tools/kuriera/tick (OK dla 5-min timera). Alternatywa: flip ON + monitor
live SLA z auto-rollback (artefakt znika na żywych danych z realnymi statusami).

## TIE-BREAKER bez regresji dostaw — ZBUDOWANY + ZMIERZONY (Adrian wybrał ścieżkę A)
`plan_recheck._gen_one_bag_plan`: gdy flaga ON, sweep ×2 (baseline bez committed + wariant świadomy
committed); przyjmij świadomy **TYLKO gdy `plan_ck.sla_violations ≤ plan_base.sla_violations`** (R6
35min chroniony twardo). Log `COMMITTED_TIEBREAK_ADOPT/REJECT`. 19 testów pass, compile/import OK.

### Pomiar tie-breakera (na adoptowanym planie vs baseline)
| metryka | WSZYSTKIE (65) | ŚWIEŻE (24) |
|---|---|---|
| adopt świadomy / przestawia realnie | 47 / 21 | 19 / 7 |
| odrzucone (chronią dostawę) | 18 | 5 |
| punktualność odbioru ≤+5 | 27.6% → **32.1%** | 58.8% → **64.7%** |
| **Δ SLA dostaw (adopted−baseline)** | **−1** | **+0** |
| Δ trasa | +0.30 min/worek | +0.63 min/worek |

Kontrast — naiwny flip (zawsze fixed): Δ SLA dostaw **+23 / +7**. Tie-breaker eliminuje CAŁY koszt
dostaw, zachowując **12 czystych wygranych** (wszystkie SLA 0→0) — m.in. cid=123 [Goodboy,Chicago Pizza]
late +12.3→+3.5 (w ±5, trasa nawet −3 min) i cid=393 (Michał K.) 3×. Oryginalny case 482630+482633:
adopt (sla 0→0) → sushi-first +4.2 ✅.

**Werdykt: tie-breaker = Pareto-poprawa (lepsza punktualność odbioru, ZERO regresji SLA dostaw, 0
INFEASIBLE).** Zysk skromniejszy niż naiwny flip (świadomie — odrzuca 18/5 trade-offów psujących
dostawę). Gotowy do flipa ON + monitor (log ADOPT/REJECT) na żywych danych.

## WDROŻONE NA ŻYWO 2026-06-22 15:47 UTC
Drop-in `dispatch-plan-recheck.service.d/committed-propagation.conf` (`ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1`),
daemon-reload, env potwierdzony, tick exit 0. **Monitor 25 min (4 generacje multi-worków):**
- `COMMITTED_TIEBREAK_REJECT` ×2, ADOPT ×0, **0 błędów/Traceback** (2× sweep stabilny w prod).
- cid=370 [482647/49/50]: sla_base=0 → sla_ck=1 → ODRZUCONE (chroni dostawę) ✅
- cid=123 [5 orderów]: sla_base=1 → sla_ck=5 → ODRZUCONE (naiwny flip dałby +4 naruszenia!) ✅
Gwarancja „bez regresji dostaw" potwierdzona na żywych danych. ADOPT (czyste wygrane) pojawią się
rzadziej / zależnie od pory — replay: ~21 reorderów/65 worków na 2 dni. Monitoring leci dalej (timer 5-min).
Rollback: `rm` drop-in + daemon-reload.

## ESKALACJA kary (Adrian D1) — ZBUDOWANA + ZMIERZONA → NO-OP na agregacie (NIE flipować)
Tier-2 soft bound przez osobny wymiar `CommittedLate` (wzorzec food-age) = kara wypukła „od +T2
mocno rosnąca". Flaga `ENABLE_OBJ_COMMITTED_PICKUP_ESCALATION` (default OFF), próg/coeff env-tunable.
compile/import/83 testy OK. Replay 75 worków, progi T2 = +6/+7/+10:
- ≤+5: liniowa 30,7% → eskalująca 29–30% (BEZ ruchu); ogon >+10: 55% → 55%; mediana +13,5 → +13,1.
- 0 INFEASIBLE, latencja +0–1 ms/worek, ΔSLA dostaw −1 (jak liniowa). Zmienia 10–12/75 worków, nie rusza metryki.

**Werdykt: eskalacja BEZPIECZNA, ale NO-OP na agregacie.** Powód kluczowy: eskalacja i tie-breaker
chroniący dostawę to **konkurencyjne lewary**. Skoro chronimy dostawę bezwzględnie (R6 twarde),
liniowa kara już zgarnia WSZYSTKIE przestawienia bezpieczne dla dostawy; reszta spóźnień jest
fizycznie zablokowana (dojazd) albo wetowana przez tie-breaker. Eskalacja zadziałałaby tylko gdyby
pozwolić punktualności odbioru ŁAMAĆ dostawę — co przeczy R6. **Właściwy zawór „nie zdążymy o czasie"
= shadow „później" (D2), nie ostrzejsza kara.** → eskalacja gated OFF (reguła „no-op → NIE rób").

## CO JEST LIVE (realny fix problemu Michała):
tier-1 propagacja committed + **tie-breaker bez regresji dostaw** (`ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1`)
+ shadow „później/realny czas" (`pickup_extension_redirect`) jak był (D2). Eskalacja: kod gotowy, OFF.
