# Przegląd `_kind()` downstream — prerekwizyt flipu O2 Krok 2 (`ENABLE_SLA_GATE_READY_ANCHOR`)

**Sesja:** 03.07 rano · **Zadanie:** kolejka HANDOFF_po_dniu_0207 §2 poz. 2 („przegląd `_kind()`,
48% reason-churn") · **Tryb:** read-only (zero zmian kodu w tym pasie).

## TL;DR — werdykt
**Sam `_kind()` jest ODPORNY na churn K2** (klasy blocking konsumowane UNIJNIE — re-atrybucja
między `sla`↔`r6_new`↔`r6_carry_delta` nie zmienia zachowania). **ALE prereq flipu K2 to NIE
tylko `_kind()`** — mapa kompletności ujawnia 3 count-sensitive konsumentów `plan.sla_violations`
(int), z których 2 wymagają rozszerzenia pre-flip replayu, a 1 wymusza sekwencję względem L3.

## 1. Konsumenci reason-stringów (`_kind()` ×2) — CZYSTE
- `dispatch_pipeline.py:1245` (`_feas_carry_blind_shadow`, telemetria) i `:1322`
  (`_feas_carry_readmit_pick`, **LIVE B2**): obie kopie klasyfikują
  `feasibility_reason` prefiksami `sla_violation`/`R6_per_order`/`R6_picked_up_delta`
  i konsumują je **wyłącznie jako unię** `in ("sla","r6_new","r6_carry_delta")`.
  Churn 48% = przepływ MIĘDZY tymi klasami → unia niezmienna → selekcja best_rej,
  guard cap≤40, lex_qual — bez zmian behawioralnych.
- **Forma stringów nietknięta przez K2**: emit site `feasibility_v2:1296`
  (`"sla_violation (oid +Xmin, over by Y)"`) — K2 zmienia TYLKO które/ile zleceń
  triggeruje, nie treść prefiksu → żaden reason nie wypada do klasy `other`
  (zbiór rejected stabilny: replay ΔNO=0 pure, `o2-capz_raport.md`).
- Skutek uboczny: pole `redirect_kind` w `feas_carry_blind_shadow.jsonl` zmieni MIX klas
  po flipie → **nie porównywać mixu klas przed/po flipie wprost** (to artefakt re-atrybucji).

## 2. Konsumenci LICZNIKA `plan.sla_violations` — tu jest realna praca przed flipem
| Konsument | Wpływ K2 | Werdykt |
|---|---|---|
| `auto_assign_gate.py:186` (`plan_sla_violations` block >0) | ready-anchor ↑ elapsed → licznik może rosnąć → WIĘCEJ bloków | Kierunek konserwatywny = bezpieczny; latentne (AUTO_ASSIGN=0). ⚠ pomniejszy plaster would_auto — **nie porównywać would_auto przez granicę flipu K2** (kalibracja autonomii po flipie od nowego okna) |
| `dispatch_pipeline.py:6971` sort best_effort `(r6_pov, sla_violations, dur)` + reason `:7167` | zmiana tie-breaka w ścieżce 0-feasible | **REALNA zmiana picku możliwa.** Replay o2-capz mierzył flipy werdyktu feasibility (0) — NIE parytet picku best_effort. **DO PRE-FLIP REPLAYU: parytet best_effort picku ON↔OFF** na korpusie (lub % zmienionych picków + ocena lex_qual lepszy/gorszy) |
| `plan_recheck.py:761/800/1931/1981` (klucze porównań regen/ck, compare-and-keep) | inne liczniki → inne akceptacje regenów | **INTERAKCJA z L3** (at-202 sobota flipuje `ENABLE_PLAN_RECHECK_GATES`). Dwie zmiany naraz na tych samych kluczach = nieodplątywalna atrybucja → **K2 flipować dopiero PO ustabilizowaniu L3 (≥2 dni obs)** + parytet plan_recheck w replayu |

## 3. Rekomendowana sekwencja (uzupełnia HANDOFF §2)
S1 werdykt (sob. 04.07) → **flip K1** (`ENABLE_O2_CAPZ_RESEQ`, ACK) → L3 auto-flip at-202 + 2 dni
obserwacji → **replay K2 rozszerzony** (parytet best_effort picku + parytet plan_recheck
compare-and-keep, oprócz dotychczasowego ΔNO/reason-mix) → flip K2 (ACK). QUANTILE co-design
(+4% gold NO→MAYBE) = kandydaci wchodzą do feasible, readmit widzi mniej rejected — OK.

## 4. Co NIE jest potrzebne
Zmiany w samym `_kind()` — żadne. Unifikacja 2 kopii `_kind()` = kosmetyka (identyczne ciała,
oba w tym samym pliku); nie blokuje flipu, ewent. przy najbliższym dotknięciu B2.
