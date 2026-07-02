# BUG #4 reseq — ORACLE-RECHECK + uczciwy re-werdykt (2026-07-02)

**Pas B (worktree `wt-bug4-oracle`, branch `fix/bug4-oracle`, commit `0123782`).**
Build+pomiar, ZERO flipów/restartów/push. Read-only wobec rdzenia.

---

## 1. Root-cause suspectów — U ŹRÓDŁA, nie filtrem (protokół C9 pkt 4, wzorzec #8/#17)

**Objaw (werdykt starego przyrządu `tools/bug4_reseq_verdict.py`):**
`suspect (inwariant delta<−0.5 naruszony) 153/1249 = 12% ⚠ >10% — pomiar skażony`
(na oknie 06-29→07-02 to 277/2534 = **10.9%**).

**Reprodukcja 1:1 na konkretnych podejrzanych** (nie „na oko"):
- Strukturalna analiza wszystkich 277 suspectów z logu: **277/277 mają IDENTYCZNY skład
  węzłów** (te same pickupy+dropoffy) — to czyste REORDERINGI, NIE brak/nadmiar węzła.
  Klasa „fikcyjny pickup odebranego" (#1 fix z 28.06) jest już martwa.
- Sygnatura suspectów jest jednolita, np. real cid=531/441:
  `frozen = [B:pickup, A:dropoff, B:dropoff]` → `fresh = [A:dropoff, B:pickup, B:dropoff]`
  (A = zlecenie CARRIED / dropoff-only; B = restauracja + dropoff). Świeży solve dostarcza
  CARRIED A NAJPIERW, potem odbiera B.
- Historyczne coords były sprunowane (bak 06-29 20:36 nie ma tych oidów) → 1:1 replay na
  ŻYWYM silniku przez syntetyczne geometrie odtwarzające sygnaturę (seed=7, 400 prób):
  **znalazłem 3+ geometrie gdzie silnikowy OR-Tools zwraca DOKŁADNIE tę sekwencję z
  `fresh_drive > frozen_drive`** — czyli suspect jest OSIĄGALNY z prawdziwego silnika,
  a fresh order = carried-first.

**PRZYCZYNA (zła OŚ pomiaru, nie skażenie danych):**
Przyrząd mierzy **czystą jazdę OSRM** (`frozen_drive − fresh_drive`) z tripwire
`fresh_drive ≤ frozen_drive`. Ale silnik `simulate_bag_route_v2` minimalizuje
`total_duration_min` = **jazda + POSTÓJ na jedzenie** (`_simulate_sequence`: `if t<ready:
t=ready`) **+ dwell**, leksykograficznie z `sla_violations` — NIE czystą jazdę.
Gdy `pickup_ready_at` jest w przyszłości albo zlecenie jest carried (jedzenie w worku →
deliver-first), świeży solve LEGALNIE jedzie WIĘCEJ, żeby ściąć czas oczekiwania.
Dowód rozjazdu osi (selfcheck): objektyw frozen=50.0 vs opt=33.7 (**obj_delta=+16.3 min
na korzyść reseq**) przy `drive_delta=−2.2` (frozen ma MNIEJ jazdy → stary przyrząd
oznaczał ten realny zysk jako „suspect/skażenie").

→ **277 „suspectów" to WRONG-AXIS FALSE POSITIVES.** Stary werdykt je WYKLUCZAŁ, przez co
(a) fałszywie zgłaszał instrument-niezdrowy (12%>10% → „oracle-recheck przed GO"),
(b) ZANIŻAŁ materialność (wyrzucał realne wygrane reseq reorderujące pod postój).
To jest wzorzec #8 (mierz `p.sequence`/realną zmienną, nie proxy) + #17 (proxy-werdykt kłamie).

---

## 2. Fix przyrządu u źródła (`tools/bug4_reseq_oracle.py`, w mojej partycji)

- **Mierzy REALNĄ ZMIENNĄ DECYZYJNĄ = objektyw silnika** (`sla_violations`,
  `total_duration_min`), nie proxy-drive. Ta sama kotwica/`now`/coords/dwell/flagi co live
  (wołanie publicznego API silnika `_simulate_sequence`+`_count_sla_violations` — rdzeń
  niedotknięty).
- **Materialność** = `frozen_total − opt_total ≥ 0` (o ile optymalna sekwencja bije
  KOLEJNOŚĆ FROZEN wycenioną w TYCH SAMYCH warunkach — nie re-solve pod inny krajobraz).
- **Inwariant-tripwire POPRAWNY**: `opt_sla<frozen_sla` lub `opt_total ≤ frozen_total+eps`
  — trzyma się PRZEZ OPTYMALNOŚĆ (frozen-order to dopuszczalna sekwencja którą solver też
  mógł wybrać). Drive zostaje TYLKO jako diagnostyka pomocnicza (jawnie może być <0).
- **Determinizm**: pełna enumeracja dopuszczalnych sekwencji PDP (brute-force, precedencja
  pickup<dropoff), zero niedeterminizmu OR-Tools. 2 biegi identyczne (test).
- **Same-set/liczba stopów** gwarantowane konstrukcją (opt i frozen z tego samego zbioru węzłów).

Read-only, fail-soft, brak flip/restart/push. Re-werdykt zapisuje do **NOWEGO** pliku
`dispatch_state/bug4_reseq_verdict_v2_<data>.txt` (append-only, stary `bug4_reseq_verdict.txt`
i logger nietknięte).

---

## 3. Kalibracja oracle (druga, niezależna metoda — protokół pkt 2/3/5)

Case: carried A (odebrane 40 min temu → picked_up-drop-floor SLACK) + B (jedzenie za 25 min).
- **Metoda 1 (instrument)**: `score_bag` przez silnikowy `_simulate_sequence` → opt_total=**33.7**.
- **Metoda 2 (NIEZALEŻNA)**: ręczny walk `independent_total_min` = OSRM-table drive +
  `max(arrival,ready)` postój + dwell, BEZ wołania `_simulate_sequence`.
  → **33.7**, `|Δ|=0.000` (< 0.5 tol). Instrument ODTWARZA prawdę.
- **Inwarianty-tripwire uzbrojone**: `opt≤frozen` (obj), `obj_delta≥0`, oracle-match,
  determinizm — wszystkie zielone. **Mutation ×3** potwierdzone że gryzą: (a) enumeracja
  zawężona do frozen → benefit znika (test PADA), (b) usunięty człon postoju → oracle
  mismatch na frozen (test PADA), (c) odwrócony kierunek inwariantu → guard bije (kill-check).
  MUT „drop precedencji PDP" zamaskowany przez własny floor silnika (drop≥ready+dwell) —
  legalne, nie luka.
- **Szeroki sweep** (300 losowych worków): 71 z nich stary przyrząd oznaczyłby SUSPECT
  (drive_delta<−0.5) → **na wszystkich 71 objektywowy inwariant `opt≤frozen` HOLDS (0 FAIL)**.
  Empiryczne potwierdzenie że drive i objektyw rozjeżdżają się w ZNAKU ~24% razy = drive to zła oś.

**CAVEAT (ORACLE-CAVEATS):** wynik = **proxy-certyfikowany**, nie ground-truth. Logi
starego loggera NIE mają `total_duration`/`sla` → nie da się z nich policzyć objektywu
per-rekord; „0 suspectów na osi objektywu" opiera się na (a) reklasyfikacji same-node
reorderingów, (b) dowodzie mechanizmu na silniku (selfcheck+sweep+3 osiągalne geometrie).
Residualne ryzyko: OR-Tools zwraca SUBOPTYMALNY fresh (objektyw gorszy od frozen) — czego
obecny logger NIE wykryje bo loguje jazdę. Domknięcie = pkt 5.

---

## 4. Re-werdykt Z LICZBAMI (przed/po; okno 2026-06-29→07-02, 2534 próbki)

| metryka | STARY przyrząd (oś=drive) | NOWY przyrząd (oś=objektyw) |
|---|---|---|
| suspect / zdrowie | **277 = 10.9% ⚠ >10% skażony** | wrong-axis FP=277 → **0 = 0.0% ✓ zdrowy** |
| materialność | delta_drive≥1min = 22% (proxy) | **deliv_seq_differs = 558 = 22.0%** (realna zmienna decyzyjna, `plan.sequence`) |
| median (proxy) | 5.3 min | delta_drive median 4.7 min (tylko diagnostyka) |
| **WERDYKT** | **WAIT/NO** (suspect>10%, „skażony") | **GO (proxy-certyfikowany)** — instrument zdrowy, reseq materialny |

- deliv_seq_differs ≥1min% (drive proxy) = 20.4% median 4.7 min — TYLKO diagnostyka, nie oś werdyktu.
- Plik wyniku: `dispatch_state/bug4_reseq_verdict_v2_20260702.txt` (nie nadpisuje starego).

**Jednym zdaniem:** blocker „suspect 12%>próg" był FAŁSZYWY (zła oś) — po przeniesieniu na
objektyw silnika instrument jest ZDROWY (0% skażenia), a reseq realnie materialny (22%);
werdykt GO(proxy-certyfikowany) z jawnym caveatem domknięcia.

---

## 5. Rekomendacja dla Adriana (TYLKO propozycja — żadnych flipów; ryzykowne=ACK)

1. **(Domknięcie caveatu, rdzeń — osobny pas/ACK)** Dołożyć do LOGGERA `_bug4_reseq_shadow`
   w `plan_recheck.py` (NIE moja partycja): serializować `frozen_total_duration`,
   `fresh_total_duration`, `frozen_sla`, `fresh_sla` (nie tylko drive) + POPRAWNY tripwire
   `fresh_obj ≤ frozen_obj`. Wtedy re-werdykt liczy się z ŻYWYCH logów bez brute-force i
   łapie realny residual (suboptymalny OR-Tools). Drive zostaje polem diagnostycznym.
2. **Wymienić oś werdyktu**: `tools/bug4_reseq_verdict.py` (stary) → oznaczyć DEPRECATED
   na rzecz `bug4_reseq_oracle.py`; nie usuwać do potwierdzenia loggera.
3. **Merytoryczny wniosek reseq**: skoro reseq zmienia kolejność dostaw w ~22% wielo-zlec.
   RETIME, a mechanizm to carried-first / reorder-pod-postój (dobre!), następny krok =
   ZMIERZYĆ benefit w minutach objektywu na collect-window (po pkt 1), potem decyzja czy
   RETIME ma re-sekwencjonować (dziś trzyma frozen order). To decyzja silnika (P0) → ACK.

**Żadnych flipów/restartów zrobionych. Zero zmian w rdzeniu/flagach.**

---

## 6. Propozycja wpisu do [[shadow-jobs-registry]]

```
bug4_reseq_shadow (logger plan_recheck) — instrument: OŚ ZŁA (proxy drive) → PRZEBUDOWANY
  status: stary verdict = VOID na osi drive (277/277 "suspect" = wrong-axis FP, nie skażenie)
  nowy: tools/bug4_reseq_oracle.py — oracle-validated (|Δ|=0.000 vs niezależny walk),
        oś=objektyw silnika (total_duration+sla), brute-force determ., inwariant opt≤frozen.
  re-werdykt v2 06-29→07-02: suspect 10.9%→0.0%, deliv_seq_differs 22% → GO(proxy-cert).
  caveat: logger nie loguje objektywu → per-rekord obj weryfikacja wymaga collect-window
          (rekomendacja: dołóż frozen/fresh total_duration+sla do loggera). Commit 0123782.
```
