# Przerzucanie zleceń → duch w konsoli (MUST-HAVE) — SPEC, 2026-06-28

**Wizja Adriana (28.06):** Ziomek rozdziela zlecenia po JAKOŚCI dowozu — docelowo zastępuje koordynatora.
Reguła: **kto inny dowiezie szybciej → przerzut; obecny zdąży na czas → zostaw.** Działa też dla
kurierów BEZ GPS (pozycja z checkpointów). Optymalizacja tras = zarabianie (szybciej ≈ krótsza trasa
= taniej; wspólny miernik). Koordynator widzi propozycje jako DUCHY (jak GhostTile Ziomka); po dowodzie
precyzji → autonomia.

## Decyzje ZAMKNIĘTE (ACK Adrian 28.06)
1. **Gate gradientowy ("ratuj spóźnienia + łap duże oszczędności"):**
   - **Ramię 1 (ratunek):** obecny A spóźni się (predicted > deadline / R6>35) ORAZ B dowiezie na czas → przerzut do KAŻDEGO takiego B.
   - **Ramię 2 (oszczędność):** A zdąży na czas ORAZ B dowiezie DUŻO szybciej (≥ `BIG_SAVE_MIN`, start 8 min) → przerzut.
   - inaczej → BRAK ducha (kasuje 89% over-eager).
2. **Pozycja bez GPS:** dopuszczamy GPS LUB checkpoint/last-known (`gps`, prefiks `last_*`, `store`, `interp`); odrzucamy czystą fikcję (`none`, `pre_shift`, `no_gps`, `pin`, ``). ⚠ Klasyfikacja PREFIKSOWA (nie exact-match — błąd eval/cross-check 28.06 zaniżał 83%→2%).
3. **Tempo:** etapami, ghost-first, każdy etap przez przykazanie #0.
4. **Tylko zlecenia NIEODEBRANE** (`status=assigned`) = przerzut „w porę" przed pickupem (tanie/wykonalne) — shadow już tak filtruje (`_active_assigned_orders`).

## Metryka jakości (już istnieje, zero dryftu)
`reassignment_forward_shadow.evaluate_order` woła PRAWDZIWY `dispatch_pipeline.assess_order` z O wyjętym
z worka A → kandydaci niosą `predicted_delivered_at[O]` (kiedy DOWIEZIE) + `r6_per_order_violations`.
- `a_pred = a_cand.predicted_delivered_at[O]`, `b_pred = best.predicted_delivered_at[O]`.
- `deadline(O)` = `expected_delivery_by` / committed (czas_kuriera + dojazd) / R6 ready+35.
- `a_late = a_pred > deadline` (lub r6 viol), `b_late = b_pred > deadline`.
- `save_min = (a_pred − b_pred) w minutach`.

## Pomiar reach (28.06, poprawne pozycje)
- would_reassign sweepy: 9984 | pozycja OBU usable: **67,6%**.
- distinct flag z outcome (sla_log): 1174 | pewna pozycja: **82,7%**.
- **Bronialne „shadow lepszy od człowieka" (nieprzerzucony+breach+pewna poz+lepszy B): 97 = 8,3%** (~tydzień).
- Over-eager (on-time, nie ruszać): ~89% → gate musi je odsiać.

## ETAPY (przez przykazanie #0, flaga OFF→shadow→ON)
- **Krok 0 (instrument):** napraw allow-listę pozycji w `reassignment_shadow_eval._REAL_POS` (prefiks `last_`),
  żeby werdykty nie kłamały (#15 instrument fidelity). Read-only tool.
- **Krok 1 (gate w cieniu, flaga OFF):** w `reassignment_forward_shadow.evaluate_order` policz `a_pred/b_pred/deadline/
  a_late/b_late/save_min` + pozycja-prefiks + gradient → nowe pole `quality_reassign` (+ `quality_reason`, `save_min`).
  NIE zmienia `would_reassign` (stare zostaje do porównania). Log do jsonl. Flaga `ENABLE_REASSIGN_QUALITY_GATE` OFF.
  Zebrać 3-7 dni → replay OUTCOME: czy `quality_reassign` zlecenia REALNIE dowiezione wolno/breach (sla_log) =
  shadow miałby rację. Werdykt materialności (precyzja ramienia-1 / fałszywe alarmy).
- **Krok 2 (duch w konsoli):** wystaw `quality_reassign` do `panel/feed.py` (czyta jsonl) jako nowy typ propozycji
  (reassign-ghost: „Przerzuć #O: A→B, −Y min, A spóźniony / B na czas") → GhostTile w Ops13. Koordynator widzi,
  akceptuje/odrzuca. Mierzymy akceptację + outcome zaakceptowanych.
- **Krok 3 (autonomia):** po dowodzie precyzji (akceptowane przerzuty REALNIE skracają dowóz) — auto-przerzut bez
  klikania (jak AUTO-PROXIMITY graduation). Osobny ACK.

## Rollback / ryzyko
Każdy krok flaga OFF default + .bak + git revert. Shadow = OSOBNY proces (zero wpływu na żywą decyzję silnika).
Ghost = tylko wyświetlenie (human gate). Autonomia dopiero po dowodzie.

## Powiązane
[[reassignment-forward-shadow-v2]], [[ziomek-change-protocol]], cross-check 28.06 (scratchpad/reassign_crosscheck.py),
[[ziomek-self-improvement-loop-2026-06-26]] (ucz do OUTCOME nie agreement), AUTO-PROXIMITY (wzór graduacji ghost→auto).
