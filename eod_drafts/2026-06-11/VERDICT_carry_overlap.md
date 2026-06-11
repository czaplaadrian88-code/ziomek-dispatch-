# WERDYKT CARRY-OVERLAP-01 — carry_chain NIE FLIPOWAĆ (2026-06-11 noc)

**Pytanie** (todo_master / E7-doklejka 3): przed flipem `ENABLE_CARRY_CHAIN_PENALTY`
(soft, `CARRY_CHAIN_HARD_REJECT_STOPS=999`) zmierzyć overlap z
`ENABLE_OBJ_R6_SOFT_DEADLINE` V4 (zbił carry 31→16% na replayu 190 tras).
Reguła decyzyjna: **carry>35 < 20% → NIE flipować** (redundancja, ryzyko
over-correction — ta sama oś co SCORE-03/04 „podwójne liczenie kar czasu").

## Pomiar (shadow_decisions, PROPOSE best, 04–11.06, n=1585)

| dzień | PROPOSE | carry>35 (objm_r6_breach_count>0) | % |
|---|---|---|---|
| 06-04 | 239 | 45 | 18.8% |
| 06-05 | 250 | 12 | 4.8% |
| 06-06 | 144 | 3 | 2.1% |
| 06-07 | 302 | 20 | 6.6% |
| 06-08 | 156 | 5 | 3.2% |
| 06-09 | 156 | 7 | 4.5% |
| 06-10 | 208 | 13 | 6.3% |
| 06-11 | 130 | 0 | 0.0% |
| **RAZEM** | **1585** | **105** | **6.6%** |

Kontrola drugą metryką: `r6_max_bag_time_min > 35` = 102 = 6.4% (zbieżne).
`carry_chain_stops > 0` = 0/1585 (telemetria łańcuchów nie rejestruje ani
jednego przypadku w oknie).

## Werdykt

**NIE flipować `ENABLE_CARRY_CHAIN_PENALTY`.** Carry>35 spadł z 31% (sprzed V4)
/ 16% (replay V4) do **6,6% na żywych danych** — daleko pod progiem 20%.
Sterowniki: V4 OBJ_R6_SOFT_DEADLINE + front-load fix (05.06) + late-pickup
gates + (od 11.06) A2 coeff 100. Dodatkowa kara carry = podwójne liczenie tej
samej osi termicznej (anti-pattern SCORE-03/04) przy zerowym polu do poprawy.

**Dla E7 (at#131 17.06):** punkt „carry_chain soft z HARD_REJECT_STOPS=999"
z doklejki 3 → ZAMKNIĘTY tym werdyktem (chyba że E7 re-tune podniesie carry
z powrotem — wtedy wrócić do pomiaru). Flaga + telemetria carry_chain_* mogą
zostać w kodzie (koszt zero, flaga OFF); ewentualne usunięcie = osobna higiena
po E7. Wpis SCORE-09/10 (cap na coeff przed flipem) staje się bezprzedmiotowy
w części carry — zostaje tylko „R6 doomed score-penalty" jako osobny temat.

*Pomiar: skrypt inline (iter_jsonl_records po shadow_decisions live+rotated);
liczby ±2 rekordy (żywy log). Read-only, zero zmian flag/usług.*
