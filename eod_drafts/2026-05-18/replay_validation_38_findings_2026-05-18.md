# #38 Replay validation — findings (2026-05-18 rano)

Pre-implementation replay validation dla tech-debt #38 (Scoring rebalance Fix 2+3).
Cel: zweryfikować na danych historycznych, czy plan Fix 2/3a/3b faktycznie naprawia
archetyp `oid=472791` PRZED jakąkolwiek implementacją (hard gate: zero deploy przed pass).

## TL;DR — BLOCKER

**Diagnoza #38 stoi na fałszywej przesłance.** Plan zakłada, że Piotr Zaw K-470 był
ocenianym kandydatem, który przegrał z Tomkiem K-514 **na score**. Dane obserwowalności
pokazują co innego: **Piotr 470 nigdy nie dostał score'a** — został odrzucony PRZED
warstwą scoringu. Fix 2/3a/3b to zmiany scoring-layer; nie są w stanie wypromować
kandydata, którego pipeline eliminuje przed scoringiem.

→ Acceptance (a) „replay #472791 → best=470" jest **nieosiągalne** obecnym planem Fix 2/3.
→ Sprint #38 wymaga re-diagnozy zanim ruszy implementacja.

## Dowody (zweryfikowane plik:linia + obs logi 13.05)

Case: `oid=472791` Pani Pierożek → Poleska 85A, decyzja 2026-05-13T09:54:01 UTC (11:54 Warsaw).

1. **shadow_decisions.jsonl** — rekord 472791: `best.courier_id=514` (Tomasz Ch),
   `best.score=-13.2657`, `r6_bag_size=2`, `v326_anchor_used=True`. **`alternatives: []`** —
   zero alternatyw zserializowanych.

2. **observability/candidate_decisions_20260513.jsonl** — rekord 472791:
   - `context: pool_total_count=12, pool_feasible_count=1`
   - `candidates_evaluated_count=1` — oceniony **tylko** cid 514
   - `decision: PROPOSE, best=514`
   - Courier 470 jako `candidates_evaluated` w CAŁYM logu 13.05: **0 wystąpień**.

3. **observability/fleet_filter_20260513.jsonl** — wpis `09:54:00.507` (1,3 s przed decyzją):
   `passed_count=12`, w tym **`cid 470 Piotr Zaw, pos_source=last_picked_up_delivery`
   — PASSED, nie rejected**. Pula 12: `[523,508,75,517,500,457,400,393,515,514,470,413]`.

4. **dispatch_pipeline.py — mechanizm zlokalizowany.** `candidates_evaluated` budowane
   z `result.best` + `result.candidates` (`assess_order` :1131-1149). W ścieżce PROPOSE
   (`_result_pf` :2953) `result.candidates = top`, gdzie `top` = posortowana lista
   `feasible`. `pool_total_count = len(candidates)` (12, wewnętrzna lista wyników
   `_v327_eval_courier`), `pool_feasible_count = len(feasible)` (1).

   ⇒ **Piotr 470 ZOSTAŁ oceniony** przez `_v327_eval_courier` (jest w 12-elementowej
   wewnętrznej `candidates`), ale dostał **feasibility verdict NO** → wypadł z `feasible`
   → wypadł z `top` → nie ma go w `result.candidates` → nie zserializowany.

   **Powód NO dla Piotra 470 NIE jest nigdzie utrwalony** — w ścieżce PROPOSE
   `result.candidates` zawiera wyłącznie feasible; `feasibility_reason` infeasible'a
   liczone jest w `_v327_eval_courier` i odrzucane. (Kontrast: `oid=472799` ścieżka
   `best_effort` — tam `result.candidates` zawiera NO-kandydatów z `feasibility_reason`,
   stąd `candidates_evaluated_count=6` przy `feasible=0`.)

   Bramka NO = jeden z hard-rejectów w `_v327_eval_courier` (1317-2591): kandydaci dla
   picked_up kuriera ~4 min od dostawy → `v3273_wait_courier_hard_reject` (wait >20 min),
   R6 BAG_TIME >35 min, SLA violation, R1/R5. Konkretny powód = do ustalenia
   **instrumentowanym replayem** (log per-courier `feasibility_reason` dla infeasible).

## Konsekwencja dla planu Fix 2/3

| Fix | Warstwa | Czy adresuje realny root cause 472791? |
|---|---|---|
| Fix 2 — `effective_start_pos = max(anchor, tail)` | scoring (km_to_pickup, S_dystans) | NIE — działa tylko na kandydacie, który JEST scorowany |
| Fix 3a — `s_almost_free_bonus` picked_up ETA≤5 | scoring (bonus) | NIE — j.w.; Piotr nie wszedł do scoringu |
| Fix 3b — `MIN_PROPOSE_SCORE -100→-50` + pending penalty | próg PROPOSE | Częściowo — ucina mediocre Tomka, ale NIE promuje Piotra |

Fix 3b mógłby zepchnąć Tomka -13,27 poniżej progu -50? **Nie** — -13,27 > -50, Tomek
nadal przechodzi. Sam Fix 3b zmieniłby werdykt PROPOSE→KOORD tylko gdyby próg był > -13,27.

## Co trzeba zrobić przed sprintem #38 (re-diagnoza)

1. **Instrumentowany replay 472791** — `_v327_eval_courier` (lub serializer obs) musi
   emitować `feasibility_verdict` + `feasibility_reason` per courier RÓWNIEŻ dla
   infeasible w ścieżce PROPOSE (dziś gubione). Bez tego powód NO Piotra 470 jest
   nieobserwowalny. Replay faithful wymaga snapshotu floty z 09:54:01 13.05 —
   `replay_failed.py` buduje fleetę BIEŻĄCĄ (`dispatchable_fleet()` + `now=now()`),
   więc NIE odtworzy historycznej puli; trzeba albo zrekonstruować CourierState
   z `events.db`/`audit_log`, albo dodać trwały dump fleet snapshot per decyzja.
2. Dopiero znając powód NO — ocenić, czy diagnoza W-A..W-E (scoring) jest w ogóle
   właściwą warstwą. Jeśli Piotr odpadł na `v3273_wait_courier_hard_reject` lub R6 —
   to bramka feasibility, nie scoring.
3. Fix 3a (`effective_start_pos = bag[0].delivery_coords` dla picked_up ETA≤5) MOŻE
   pomóc, JEŚLI bramka NO liczyła Piotrowi zawyżony `km_to_pickup`/wait od złej pozycji
   startowej. Ale to trzeba potwierdzić powodem NO — inaczej Fix 3a trafia w próżnię.
4. Fix 2 (`max(anchor, tail)`) dla Piotra liczyłby dystans WORST-CASE → mógłby go
   uczynić jeszcze BARDZIEJ infeasible. Kierunek Fix 2 jest sprzeczny z celem (promocja
   picked_up almost-done) — wymaga rewizji.

## REPLAY WYNIK 2026-05-18 — bramka ZLOKALIZOWANA

Zbudowano instrumentowany replay (`tools/replay_feasibility.py` + `tools/fixtures/472791_archetype.json`).
Owija `dispatch_pipeline.check_feasibility_v2`, łapie per kurier Layer 1 (surowy werdykt
feasibility) vs Layer 2 (finalny Candidate po post-processingu `_v327_eval_courier`).
ZERO dotknięcia produkcji (monkeypatch w procesie skryptu). Replay leci na BIEŻĄCYM kodzie.

**Wynik (fixture 2-kurierowy, archetyp 472791):**

| Kurier | L1 check_feasibility_v2 | L2 finalny Candidate | result |
|---|---|---|---|
| Tomek 514 (pre_shift, bag=2) | NO `sla_violation (472788 +35.9min)` | NO (bez zmian) | w candidates |
| **Piotr 470** (last_picked_up_delivery, bag=1) | **MAYBE `ok_sla_fits`** | **NO `v3273_wait_courier_hard_reject (22.6min > 15.0 pod Pani Pierożek)`** | **BEST** |

`assess_order` → `verdict=PROPOSE best=470` (przez ścieżkę `best_effort`, bo `feasible`=0).

**BRAMKA = `v3273_wait_courier_hard_reject`** (V3.27.3 wait penalty). Downgrade MAYBE→NO
zachodzi w `_v327_eval_courier` między call-site 1498 a budową Candidate 2586. Mechanizm:
Piotr jest „de facto wolny za 4 min" → dojeżdża pod nowy pickup (Pani Pierożek) BARDZO
wcześnie → musiałby czekać **22,6 min** na `pickup_ready_at` (10:23 — order quasi-czasówka,
utworzony 09:53 z odbiorem +30 min) → 22,6 > próg 15,0 → HARD REJECT.

**To NIE jest warstwa scoring (W-A..W-E #38) — to feasibility hard-reject.** Co więcej,
diagnoza #38 ma mechanizm ODWROTNIE: W-E twierdzi „Piotr widziany jako bag=1 obciążony,
nie jako wolny". Replay pokazuje, że pipeline ROZPOZNAJE Piotra jako prawie-wolnego —
i to właśnie bycie prawie-wolnym (wczesny dojazd) odpala wait hard-reject.

**Wnioski:**
1. Na bieżącym kodzie wynik 472791 jest *de facto poprawny* (`best=470`) — ale przez
   `best_effort` fallback, nie czystą feasibility. Bug z 13.05 (best=514) NIE reprodukuje się.
2. Fix 2/3a/3b (#38) nie adresują `v3273_wait_courier_hard_reject` — plan jest do rewizji.
3. Realny root cause do dyskusji z Adrianem: czy `v3273_wait_courier` hard-reject powinien
   karać kuriera za WCZESNY dojazd, gdy order ma odległy `pickup_ready_at` (scheduled).
   Czekanie na zaplanowany odbiór ≠ idle waste przy ready orderze. Cross-ref memory
   `feedback_dispatch_idle_vs_drive.md`.

## FIX — decyzja Adrian 2026-05-18 + walidacja replayem

**Decyzja Adrian:** „Jeżeli kurier jest wolny i nie ma lepszych opcji — niech bierze;
jeżeli ma 0 w bagu, lepiej żeby czekał 20 min, niż stał i nic nie robił przez godzinę."

**Fix (`dispatch_pipeline.py:2570`):** gate hard-rejectu `v3273_wait_courier` warunkowany
pending-pickupem. Hard-reject (`verdict="NO"`) TYLKO gdy bag kuriera ma order `assigned`
(`picked_up_at is None`) — realny pending pickup, którego wait zaburza. Bag pusty lub
wszystkie picked_up → skip hard-reject, `verdict` zostaje MAYBE; penalty
`bonus_v3273_wait_courier` zostaje jako SOFT (scoring) → lepiej-pozycjonowany kurier
nadal wygrywa. Flag-gated.

```python
if v3273_wait_courier_hard_reject and verdict == "MAYBE":
    _has_pending_pickup = any(getattr(b, "picked_up_at", None) is None for b in bag_sim)
    if _has_pending_pickup:           # tylko kurier z realnym pending pickupem
        verdict = "NO"
        reason = f"v3273_wait_courier_hard_reject (...)"
    # else: wolny kurier — penalty soft zostaje, verdict MAYBE (Adrian 2026-05-18)
```

**Walidacja replayem** (`tools/replay_feasibility.py`, eksperyment hard-reject OFF):
Piotr 470 → L2 **MAYBE** `ok_sla_fits`, `best_effort=False` → `assess_order` →
`verdict=PROPOSE reason=feasible=1 best=470`, `pool_feasible=1`. Czysta feasible PROPOSE
zamiast best_effort fallback. ✅ Fix robi dokładnie to, co reguła Adriana.

**#38 RE-SCOPE:** Fix 2/3a/3b (scoring) wycofane. Nowy scope = ten jeden gate bag-aware.

**✅ WDROŻONE 2026-05-18 — commit `e971dc7` tag `v3273-free-courier-wait-skip-2026-05-18`.**
Adrian ACK „wdrażaj teraz, restart też". Flag `ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP`
(common.py default True + env kill-switch) + gate `dispatch_pipeline.py:2570`. Testy
`test_v3273_free_courier_wait_skip.py` 2/2 PASS; regresja v3273/v327/v328/feasibility/proposal
310+14 PASS (2 fail pre-existing — zweryfikowane identyczne pre/post via `git stash`). Restart
`dispatch-shadow` 14:49:29 UTC clean (ortools 50.5ms, login OK, 0 error; post-restart decyzja
474496 OK). Backupy `.bak-pre-v3273-free-courier-skip-2026-05-18`. Rollback: `git revert e971dc7`
+ restart, lub flag `=0`.

## Status

- Replay validation **NIE PASSED** dla planu #38 — archetyp obala przesłankę (warstwa scoring).
- Bramka eliminująca Piotra **zlokalizowana**: `v3273_wait_courier_hard_reject` (22,6min > 15,0).
- Implementacja #38 gated (Fix 1 obs window do ~20.05 + ACK Adrian) — finding + root cause
  trafiają przed ACK.
- Rekomendacja: re-scope #38 wokół `v3273_wait_courier` (wait-aware dla scheduled pickups),
  NIE wokół Fix 2/3a/3b. Decyzja biznesowa Adriana: wczesny dojazd na czasówkę = OK czy waste.

## Narzędzie (reusable)

`tools/replay_feasibility.py --fixture <json>` — instrumentowany replay feasibility per kurier.
Layer1 (check_feasibility_v2) vs Layer2 (finalny Candidate) → lokalizuje bramkę downgrade.
Fixture = `order_event` + `fleet` (lista CourierState dict). Faza 2 (osobny sprint): hook
`feasibility_replay_capture` w pipeline (mirror `obj_replay_capture`) → bajt-wierny replay
KAŻDej przyszłej decyzji. Output JSON: `--output <path>`.
