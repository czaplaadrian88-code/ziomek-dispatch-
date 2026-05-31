# Replay walidacja — R-NO-RETURN-RESTAURANT veto (477287)

**Data:** 2026-05-31 | **Trigger:** Adrian „robić 477287 z replay walidacją"
**Werdykt: veto JUŻ LIVE i ZDROWE — żadna zmiana kodu nie potrzebna.**

## Korekta wcześniejszej diagnozy

Wcześniej w tej sesji błędnie stwierdziłem „veto OFF, reguła nie egzekwowana" — sprawdziłem
tylko **env default** (`common.py:2295` `ENABLE_R_RETURN_TO_RESTAURANT_VETO` env="0"). Ale
żywe zachowanie używa `getattr(C,...) OR C.flag(...)` (`feasibility_v2.py:733`), a
**`flags.json` ma `ENABLE_R_RETURN_TO_RESTAURANT_VETO: True`** → detektor RUNS + kara −100
JEST aplikowana. Dowód: 477287 Aleksander G rr=True, `bonus_r_return_rest=−100.0`, zdemotowany
(wygrał Gabriel Je −2.1, Grill Kebab 1.65km od Rany Julek = nie-powrót).

## Detektor (feasibility_v2.detect_return_to_restaurant)

rr=True wymaga ŁĄCZNIE:
1. ta sama restauracja: `haversine(bag_pickup, new_pickup) < 0.08 km`
2. bag-order odebrany **>5 min wcześniej** (`gap_min > group_tol_min=5.0`) — osobna wizyta
3. bag-order doręczany **PO** nowym odbiorze (`t_bd > t_np`) — dowóz R wciąż w bagu

Warunek 2 **strukturalnie wyklucza batche** (dwa zlecenia z tej samej restauracji odebrane
razem, gap≤5min → NIE flagowane). To eliminuje obawę Adriana o FP na legalnych batchach.

## Replay (8 dni, shadow_decisions.jsonl, 2026-05-24 → 05-31)

| metryka | wartość |
|---|---|
| PROPOSE total | 1539 |
| z ≥1 kandydatem rr=True | 134 (8.7%) |
| rr=True kandydatów łącznie | 141 |
| **veto zadziałał (powrót zdemotowany do lepszej alt)** | **117 (87%)** |
| rr=True wygrał mimo −100 (only-candidate / all-infeasible) | 17 (13%) |
| zablokowane dostawy (BRAK KANDYDATÓW) | **0** (soft −100, nie hard) |
| dni z rr | 24:11, 25:12, 26:25, 27:20, 28:10, 29:15, 30:14, 31:27 |

## Spot-check (brak FP, prawdziwe powroty)

- **475812** Grill Kebab→Plazowa: Jakub OL bag=[Grill Kebab 18:49, Grill Kebab 18:53], nowy=Grill
  Kebab → return_oid=475803 → powrót niosąc dowóz GK. Słusznie −100, zdemotowany (best=Łukasz 113.9).
- **476177** Sushi Rany Julek: Jakub rr=True ale **feas=NO** (wszystkie 7 kandydatów feas=NO,
  best-effort all-infeasible). Least-bad mimo −100 → R-FLEET-LEVEL (człowiek decyduje, dostawa>brak).

17 „rr wygrał" = only-candidate (pula=1) lub all-infeasible best-effort — surface powrotu
ZAMIERZONE (soft veto: dostawa > brak dostawy). Detektor wymusza gap>5min w kodzie → rr=True ⇒
gap>5min z definicji ⇒ brak FP (chyba że błędne czas_kuriera, niesystematyczne).

## Wniosek

Veto = soft −100 dominująca kara, NIE hard veto. Zachowuje się dokładnie jak zaprojektowano:
87% demote-do-lepszej, 13% only/least-bad (appropriate), 0 blokad, 0 FP. **Nic do flipowania
ani tuningu.** Opcjonalnie (Z3, nie-pilne): `same_rest_km`/`group_tol_min` mogłyby być
env-tunable (dziś hardcoded w sygnaturze) — ale obecne wartości zwalidowane jako zdrowe.
