# C02 — bug4_reseq ORACLE (lane C, runtime) — backing

**Agent:** C02-bug4-reseq · **Lane:** C (runtime-oracle, C9/C11) · **2026-06-30 ~16:25 UTC · READ-ONLY**
Numery linii re-grepowane świeżo z `plan_recheck.py` / `tools/bug4_reseq_verdict.py` (dziś). Drugą metodą = OSRM `localhost:5001` (silnikowy `_osrm_drive_min_sum`) + brute-force permutacji + niezależny recompute booleanów z surowych pól + self-consistency. ≥2 odczyty (determinizm).

---

## 0. CO MIERZY PRZYRZĄD + GDZIE ŻYJE (świeże file:line)
- **Writer (silnik):** `plan_recheck.py:1630` `_bug4_reseq_shadow(...)`, helper `plan_recheck.py:1612` `_osrm_drive_min_sum`. Flaga `ENABLE_BUG4_RESEQ_SHADOW` (`:1637`), cap 20/tick (`:1609`/`:1641`), fail-soft (`:1732`).
- **Call-site:** `plan_recheck.py:1929` — odpala się **TYLKO w gałęzi RETIME** (`:1923-1931`, `ENABLE_PLAN_SEQUENCE_LOCK` ON + `bag_signature` niezmieniony → „TYLKO re-czasuj, nie permutuj"). Gating POPRAWNY: porównuje zamrożoną-przeczasowaną sekwencję vs świeży permute-solve dla TEGO SAMEGO worka. (Brak buga gatingu.)
- **Verdict:** `tools/bug4_reseq_verdict.py` (read-only; `--notify` NIE odpalane). Liczy material/differ/median + health-gate `suspect<=10%`.
- **Output:** `dispatch_state/bug4_reseq_shadow.jsonl` (mtime **16:22 FRESH**, 1074 rek., live-rośnie co tick) + `dispatch_state/bug4_reseq_verdict.txt` (**29.06 07:41 STALE**) + archiwum `…jsonl.phantom-pre-fix-2026-06-29` (119KB, 29.06 07:25).
- **Commit walidowany:** `5623122` (29.06 07:43) — „realna p.sequence + skip fikcyjnych pickupow odebranych + inwariant delta>=0". Zakres: `plan_recheck.py` (+29), `tools/bug4_reseq_verdict.py` (+29), `tests/test_bug4_reseq_shadow.py` (+53). Commit message sam przyznaje: pre-fix widmo 333 rek., 43 (13%) miało delta<−0.5.

## 1. METODA DRUGA (niezależna) — co policzyłem
1. **Recompute booleanów z surowych pól** (nie ufam logowanym): `invariant_violation` z `delta_min<−0.5`; `deliv_seq_differs` z `frozen_deliv_order!=fresh_deliv_order`; `seq_differs` z `frozen_seq!=fresh_seq`.
2. **Inwarianty-tripwire:** same-set-of-stops (dropoff-set==bag), bag-permutacja, **symetria pickup-set frozen↔fresh** (resztkowy fikcyjny węzeł), liczba rek. z carried (skip wykonany).
3. **OSRM ground-truth** silnikowym `PR._osrm_drive_min_sum` na 2 zrekonstruowanych rek. (coords z `orders_state`) — delta liczona **start-niezależnie** (wspólny pierwszy węzeł → leg start→1 kasuje się w delcie). + brute-force wszystkich ważnych permutacji PDP.
4. **Determinizm:** snapshot A vs B (re-read live) + OSRM ×2 + flicker per (cid,bag) między tickami.
5. **Replikacja verdict-math** (read-only, output do scratchpad — NIE do dispatch_state).

## 2. WYNIKI — co JEST naprawione (VALIDATED, rdzeń „naprawione 5623122")
| Claim 5623122 | Dowód drugą metodą | Werdykt |
|---|---|---|
| `fresh_deliv_order` = **plan.sequence** (realna zmienna), NIE sort-ts proxy | `:1695` `fresh_deliv_order=[str(o) for o in (plan_fresh.sequence or [])]`; recompute booleana `deliv_seq_differs` = **0/1074 mismatch** | ✅ |
| **skip fikcyjnych pickupów** dla odebranych | `:1682` `if sims[oid].status != "picked_up":`; **pickup-set asymetria frozen↔fresh = 0/1074**; carried obecny w **732/1074 (68%)** rek. (skip realnie ćwiczony) | ✅ |
| same-set / bag-permutacja | dropoff-set≠bag **0/1074**; bag-perm złamana **0/1074** | ✅ |
| logowane drive = wierny OSRM (nie fabrykat) | `delta_internal` zreplikowane **DOKŁADNIE**: cid=536 **−1.40** (log −1.4), cid=515 **−4.90** (log −4.9), oba ×2 runs identyczne | ✅ ground-truth |
| widmo pre-fix zarchiwizowane | `…jsonl.phantom-pre-fix-2026-06-29` istnieje | ✅ |

→ **3 kłamstwa z audytu 28.06 (proxy-sort SYGNAŁ / fikcyjny-pickup / brak-tripwire) są REALNIE usunięte na poziomie księgowania.** Recompute 0 mismatch we WSZYSTKICH kategoriach. To jest VALIDATED.

## 3. WYNIKI — co przyrząd DALEJ robi źle (instrument-jako-oracle: VOID)

### F1 (P1, klasa E+G) — inwariant „delta>=0" JEST ŹLE ZDEFINIOWANY
- `:1723-1726` deklaruje: „wierny re-solve NIE może być GORSZY… delta<−0.5 = pomiar skażony (resztkowy fikcyjny węzeł / semantyka)".
- **FAŁSZ.** Klucz świeżego solve `:1670` = `(p.sla_violations, round(p.total_duration_min,3), tuple(p.sequence))` → minimalizuje **SLA + total_duration**, NIE drive OSRM. `fresh_drive` (`:1708`) liczone nad trasą — solver świadomie wybiera WIĘKSZY drive dla mniejszej liczby naruszeń SLA / krótszego oczekiwania.
- **Ground-truth brute-force (cid=515):** min-DRIVE permutacja = `553p→562p→562d→553d` = **11.3 min = to jest FROZEN**; fresh wybrał `553p→553d→562p→562d` = **16.2 min** (+4.9 drive). Czyli frozen BYWA optimum-drive, a objective-optymalny fresh MUSI mieć delta≤0 z definicji.
- **cid=531 (najgorszy −22.4):** `deliv_seq_differs=False` — kolejność DOSTAW identyczna; fresh dowozi carried-484518 PRZED pickupem-484542 (carried-first SLA), frozen robi pickup-542 najpierw. Czysty objective-driven, NIE skażenie.
- **Skutek:** 123/1074 (**11.5%**) rek. oznaczonych „suspect/skażony" jest PRZEWAŻNIE legalnych. 48/123 ma `deliv_seq_differs=False` (carried-first interleaving, nie resekwencja dostaw wcale).

### F2 (P1, klasa E) — własny health-gate przyrządu PADA; status „zdrowy/naprawione" SFALSYFIKOWANY na żywo
- `tools/bug4_reseq_verdict.py:87` `go = pct>=20.0 and med>=1.5 and suspect_pct<=10.0`; `:89-90` emituje „⚠ >10% — pomiar wciąż skażony, oracle-recheck PRZED GO".
- **Replikacja na żywej próbie:** material=214 (**22.5%** ✓≥20), median_material=**5.6** ✓≥1.5, ale **suspect=123 = 11.5% > 10% → GO=False I instrument sam pisze „WCIAZ SKAZONY".**
- → MEMORY/rejestr „#1 NAPRAWIONE 29.06 … instrument zdrowy" jest **sprzeczne z własnym werdyktem przyrządu na dzisiejszych danych**. Przyrząd nie przeszedł oracle.

### F3 (P2, klasa O+E) — migotanie etykiet per worek między tickami
- Ten sam (cid, frozenset(bag)) re-ewaluowany na wielu 5-min tickach: `deliv_seq_differs` FLIPuje **66/267 (25%)** powtarzanych worków; `invariant_violation` FLIPuje **48/267 (18%)**.
- Verdict liczy REKORDY nie distinct-worki (docstring `:2-3` mówi „ile worków/dzień", a pętla `:42` `n+=1` per rekord) → niestabilny worek liczony N× z wewnętrznie sprzecznymi etykietami. Materialność % nad zaszumioną populacją rekordów, nie nad stabilną prawdą per-worek.

### F4 (P2, klasa F) — `fresh_drive` jedzie na PROXY sort-ts, nie na plan.sequence
- SYGNAŁ differs podniesiony do realnej `plan.sequence` (`:1695`) — VALIDATED. ALE **licznik drive** `fresh_drive` (`:1708`) sumuje nad `fresh_coords` (`:1692`), które są `events` POSORTOWANE PO predicted-ts (`:1690` `events.sort(key=lambda e:e[0])`) — czyli STARA proxy-kolejność. Headline materialność (`delta_min`, „SUMA straconych minut") mierzona na trasie sort-ts, NIE na `plan_fresh.sequence`. Dla planów gdzie ts-order≠sequence-order te się rozjeżdżają. (Plus zła OŚ kosztu — drive vs total_duration, F1.)

### F5 (P2, klasa H) — STALE verdict.txt kłamie „brak danych" mimo 1074 świeżych rekordów
- `dispatch_state/bug4_reseq_verdict.txt` (mtime 29.06 07:41) dosłownie: „brak …bug4_reseq_shadow.jsonl — logger nic nie zapisał". Plik jsonl ma TERAZ 1074 rek. (mtime 16:22 FRESH). Czytający .txt jako „bieżący werdykt" dostaje fałszywe „instrument pusty". Brak TTL/markera stale (potwierdza H-smell z A4).

## 4. DETERMINIZM (≥2 odczyty)
- Snapshot A (1074) vs Snapshot B (re-read live, 1074): **identycznie** n=1074 / suspect=123 / differ=154 / material=214 / median=5.6.
- OSRM `_osrm_drive_min_sum` ×2: cid=536 delta_internal=−1.40 (oba), cid=515 −4.90 (oba). Zero migotania w MOIM pomiarze.
- Migotanie istnieje TYLKO w danych przyrządu między tickami (F3) — to właściwość instrumentu, nie mojego oracle.

## 5. INWARIANTY-TRIPWIRE (podsumowanie liczbowe, n=1074)
| Inwariant | Wynik | Status |
|---|---|---|
| recompute `invariant_violation` (delta<−0.5) | 0 mismatch | ✅ księgowanie wierne |
| recompute `deliv_seq_differs` | 0 mismatch | ✅ |
| recompute `seq_differs` | 0 mismatch | ✅ |
| same-set-of-stops (dropoff==bag) | 0 viol | ✅ |
| bag-permutacja | 0 viol | ✅ |
| **symetria pickup-set frozen↔fresh** (zero fikcyjnych pickupów) | **0 viol** | ✅ skip działa |
| carried obecny (skip ćwiczony) | 732/1074 | ✅ realnie |
| **delta>=0 (fresh nie gorszy od frozen)** | **123/1074=11.5% NARUSZONE** | ❌ inwariant ŹLE ZDEFINIOWANY (objective=SLA+total_duration, nie drive); ground-truth: min-drive perm==frozen, fresh +4.9 drive świadomie |
| delta reproduced przez OSRM (drugą metodą) | ±0.00 ×2 | ✅ drive wierny |

## 6. WERDYKT ORACLE
- **Księgowanie / 3 fixy 5623122 = VALIDATED** (ground-truth OSRM + 0-mismatch recompute). Stary at-188 VOID realnie naprawiony w warstwie „co loguje".
- **Inwariant + health-gate + materialność = VOID** jako oracle decyzyjny: `delta>=0` nieprawdziwy dla objective (SLA, total_duration), 11.5% legalnych rek. fałszywie „suspect", own-gate `suspect<=10%` PADA → instrument sam deklaruje „wciąż skażony", median/material liczone na **drive** (zła oś) i na **rekordach** (migotanie 25%/18%, F3).
- **„Co flipuje":** ten przyrząd bramkuje decyzję GO/WAIT na sprint naprawy źródła re-sekwencji worka (feasibility↔route_simulator↔plan_recheck; checkpoint 02.07). Z obecną semantyką: **WAIT/NO** (gate health pada), ALE z FAŁSZYWEGO powodu (zły inwariant), nie z braku materialności (materialność spełniona 22.5%/5.6). Przed użyciem werdyktu do GO trzeba: (a) przedefiniować inwariant na `total_duration` lub przyjąć delta<0 jako legalny (carried-first), (b) liczyć distinct-worki nie rekordy, (c) mierzyć deltę na `plan.sequence` nie sort-ts, (d) odświeżyć/oznaczyć stale .txt.

## 7. POKRYCIE
**Zbadane:** `plan_recheck.py:1607-1733` (writer+helper, pełny read), call-site `:1918-1943` (gating RETIME), `tools/bug4_reseq_verdict.py` (pełny + replikacja math), `tests/test_bug4_reseq_shadow.py` (nagłówek+fixtury), `git show 5623122` (diff-stat+msg), `bug4_reseq_shadow.jsonl` (1074 rek., 2 snapshoty, pełny recompute), `bug4_reseq_verdict.txt` (stale), `.phantom-pre-fix` (istnienie), OSRM `localhost:5001` (reachability+2 rek. ground-truth+brute-force+determinizm ×2), `orders_state.json` (rekonstrukcja coords 3 rek.).
**NIE zbadane (luki jawne):**
- NIE re-uruchomiłem `pytest tests/test_bug4_reseq_shadow.py` — RECON baseline już zielony (3611 passed); READ-ONLY, unikam redundancji. (8/8 z commita niezweryfikowane drugą metodą — niski priorytet, bookkeeping już ground-truth-potwierdzony.)
- NIE odtworzyłem ABSOLUTNEGO poziomu drive dla rek. gdzie frozen/fresh mają RÓŻNY pierwszy węzeł (anchor startowy/GPS NIE logowany w jsonl) — delta zweryfikowana start-niezależnie tam gdzie wspólny pierwszy węzeł (2 rek. dokładnie).
- `total_duration_min` / `sla_violations` NIE logowane per rek. → nie skwantyfikowałem ile z 123 to SLA-driven vs wait-driven; mechanizm dowiedziony na 2 zrekonstruowanych + 48 strukturalnych (deliv_seq_same).
- Dedup cross-tick do distinct-worek-prawdy NIE policzony jako alternatywna materialność (zgłoszone jako F3, nie naprawiane).
