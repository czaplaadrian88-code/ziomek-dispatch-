# #38 — REPLAY VALIDATION + ROOT-CAUSE (offline, 2026-05-16)

**Werdykt replay #38:** FAIL — fix #38 (Fix 2/3a/3b) działa w warstwie SCORINGU,
a case 472791 przegrywa na warstwie FEASIBILITY. Scoring-fix nie ruszy kandydata
wyeliminowanego wcześniej.

**Root cause znaleziony** (Z2 — root cause przed fix). Poniżej diagnoza +
rozwiązanie jakościowe (Z3 — strukturalna korekta, nie łata).

---

## 1. Rekonstrukcja case'a 472791 (Pani Pierożek → Poleska 85A)

Order 472791: 1 ocena pipeline — `2026-05-13 11:54 Warsaw` → PROPOSE **514** Tomasz Ch
(score -13.27). `pool_feasible=1` — Piotr **470 odrzucony na feasibility**.
Adrian PANEL_OVERRIDE → 470 @ 11:57.

**Stan Piotra 470 @ 09:51 (3 min wcześniej, z rekordu sąsiedniego ordera 472790):**
- bag = 1 order: **472778 Miejska Miska → Wiosenna 10/64**
- `free_at_utc = 09:54:09` (`free_at_min=3.2`) → kończy bieżący kurs za ~3 min
  = potwierdza "picked_up Wiosenna, 4 min od deliveru" z #38
- Dla ordera 472790 (Rany Julek, ready 10:15:39): feasibility **MAYBE**,
  `v3273_wait_courier_max_min = 14.71`, `hard_reject=False` — tuż pod progiem.

## 2. Root cause — `v3273_wait_courier` hard-reject na PHANTOM bagu

Łańcuch odrzucenia 470 @ 472791:
1. `feasibility_v2` → MAYBE (przechodzi wszystkie bramki)
2. `dispatch_pipeline._v327_eval_courier` liczy `v3273_wait_courier` →
   **hard_reject** → verdict NO (dispatch_pipeline.py:2563-2566)

Mechanizm liczby: 472791 (Pani Pierożek) ma `pickup_ready` ~10:23:36
(`time_to_pickup_ready_min=29.6`) — ~8 min PÓŹNIEJ niż 472790 (10:15:39).
470 wolny o 09:54 niezależnie od ordera → wait dla 472791 ≈ 14.71 + ~8 ≈ **~22 min**.
Próg `V3273_WAIT_COURIER_HARD_REJECT_MIN = 15.0` (P3-D2 zacisnął 20→15) →
**hard reject → NO**. (Potwierdzenie wzorca: 470 tego dnia wielokrotnie wpadał w
ten reject — np. order 472807 "v3273_wait_courier_hard_reject 17.0min > 15.0".)

### Defekt strukturalny (kod)

`scoring.compute_wait_courier_penalty(wait_min, bag_size_at_insertion)`:
- docstring: cel kary = *"jedzenie stygnie podczas idle"* (jedzenie W AUCIE stygnie)
- docstring: *"bag=0 skip — kurier wolny i tak czeka, lepiej mu cokolwiek dać"*
- ale wejście `_bag_size_at_insertion_273 = len(bag_sim)` (dispatch_pipeline.py:1976)
  liczy bag **w momencie insertu**, nie w momencie realnego idle.

Piotr 470: w momencie insertu bag=1 (472778). ALE 472778 jest dostarczone ~09:54-09:57,
a idle pod Pani Pierożek zaczyna się ~10:02. **Auto jest PUSTE podczas idle** —
żadne jedzenie nie stygnie. To jest dokładnie przypadek "bag=0" z docstringa,
który reguła miała pomijać. Kod liczy phantom bag → kara + hard reject.

### Defekt odwraca doktrynę

Ta sama kara, dwóch kurierów, order 472791:
- **Tomek 514** (przeładowany, chaos 6-stop): wait per-pickup mały (~4 min dla
  472791) — bo INNE zlecenia w bagu wypełniają mu czas → PROPOSE
- **Piotr 470** (czysty, za 3 min wolny, dobrze ustawiony): wait ~22 min — bo
  NIE MA nic innego do roboty → HARD REJECT

`v3273` **karze kuriera za to, że jest wolny**, i nagradza przeładowanego.
To inwersja R-FLEET-LEVEL. (#38 diagnoza W-A..W-E była cała scoring-layer i
ominęła ten mechanizm.)

---

## 3. Rozwiązanie jakościowe (Z3) — `effective_bag_at_idle`

**Zasada:** kara `v3273` ma firować tylko gdy jedzenie REALNIE stygnie w aucie
podczas idle — czyli gdy w momencie dojazdu kuriera do nowej restauracji ma on
jeszcze niedostarczone zlecenia.

**Fix (dispatch_pipeline.py, ~10-15 LOC, 1 plik, 1 funkcja):**
Zamiast globalnego `_bag_size_at_insertion_273 = len(bag_sim)` — liczyć
**per-pickup effective bag** wewnątrz istniejącej pętli:

```
effective_bag = liczba orderów z bag_sim, których
                plan.predicted_delivered_at[oid] > _arr_dt_273 (dojazd kuriera tu)
_pen, _reject = compute_wait_courier_penalty(_wait_273, effective_bag)
```

Order dostarczony PRZED dojazdem do nowego pickupu nie jest w aucie → nie liczony.
`effective_bag == 0` → `compute_wait_courier_penalty` zwraca (0.0, False) —
zgodnie z własną regułą bag=0 docstringa.

**Efekt dla 470 @ 472791:** 472778 dostarczone ~09:55 < dojazd ~10:02 →
effective_bag=0 → kara pominięta → 470 zostaje **MAYBE** → konkuruje na score.
Score Tomka -13.27 (kara wait -42, R6 -34, chaos route); 470 czysty/dobrze ustawiony
(dla 472790 miał +2.9) → **470 wygrywa**. Acceptance (a) osiągnięte — fixem
feasibility/input, NIE scoring-rebalansem.

**Brak utraty sygnału "wolę jeździć niż czekać":** opportunity-cost wczesnego
dojazdu jest JUŻ liczony osobno przez `timing_gap_bonus` (470@472790 miał
`timing_gap_bonus=-12.97`). v3273 dla pustego auta tylko dubluje + dodaje
nieuzasadniony hard reject.

### Co z #38 Fix 2/3a/3b
- **Fix 2 (effective_start_pos) i Fix 3a (almost_free_bonus): zbędne** — root cause
  to feasibility, nie scoring; fix `effective_bag` rozwiązuje case bez nich.
- **Fix 3b (MIN_PROPOSE -100→-50): zachować jako osobne, niezależne ulepszenie**
  (7-day scan: 89/2104 = 4.2% PROPOSE→KOORD, w paśmie predykcji). Nie jest fixem
  na 472791, ale sensowny sam w sobie.

---

## 4. Luka observability (osobny, obowiązkowy fix)

Ścieżka normalna `candidate_decisions` NIE loguje kandydatów feasibility-NO
(potwierdzone: `n_eval==pool_feasible`, same MAYBE). `events.db` retention 48h
skasował event 13.05. ⇒ nie da się z logów wprost potwierdzić reason 470@472791
(powyższe = rekonstrukcja wysokiej pewności z sąsiedniego ordera + wzorca dnia).
Defekt kodu `bag_size_at_insertion` jest realny niezależnie od tej rekonstrukcji.
**Rekomendacja:** logować feasibility-NO (cid + reason) w ścieżce normalnej —
inaczej ta klasa bugów jest nie-debugowalna po 48h.

---

## 5. Plan sprintu (do ACK Adrian)

1. **Fix A — `effective_bag_at_idle`** w `dispatch_pipeline.py` v3273 loop
   (~10-15 LOC). Helper liczy per-pickup undelivered bag z `plan.predicted_delivered_at`.
2. **Fix B — observability:** log feasibility-NO candidates (cid+reason) w
   ścieżce normalnej `candidate_decisions`.
3. Testy: repro 472791 (470 effective_bag=0 → MAYBE; Tomek bez zmian);
   boundary (bag dostarczony przed/po dojeździe); 472790 regression (470 bag=1
   realny → kara nadal liczy). Obs serializer per Lekcja #109.
4. Fix 3b — osobny ticket, jeśli Adrian chce.
5. Shadow obs 7-day: rate hard-reject `v3273` ↓, PANEL_OVERRIDE w klasie
   "picked_up alt" ↓.

Status: replay = FAIL dla #38-jak-było; root cause = `v3273` phantom-bag
conflation; proponowany fix `effective_bag_at_idle` — czeka na ACK.
