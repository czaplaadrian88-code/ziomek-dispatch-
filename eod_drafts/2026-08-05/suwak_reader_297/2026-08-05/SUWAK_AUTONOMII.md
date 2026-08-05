# SUWAK AUTONOMII ZIOMKA — DWIE LICZBY (2026-08-05)

**Wygenerowano:** 2026-08-05T08:06:28Z · **Tryb:** READ-ONLY (zero zapisu do stanu, flag, telegramu, restartów)
**Czytelnik:** `dispatch_v2/tools/suwak_autonomii_review.py` (dobowy, pod `shadow-review.timer`)
**Szereg czasowy:** `/root/worktrees/dispatch_v2/active/20260805-suwak-reader-297-cto/eod_drafts/2026-08-05/suwak_reader_297/suwak_autonomii.jsonl`

---

## ⭐ ODPOWIEDŹ W DWÓCH LICZBACH

**LICZBA 1 — ile Ziomek zrobiłby SAM:** **19.73%** decyzji kwalifikuje się do auto-assign w wariancie D (n=1551, okno 2026-07-28..2026-08-05, 9 dni).
Dla porównania: bazowy (dzisiejsze bramki) **1.03%**, wariant D' **17.60%**, kanał `auto_route=AUTO` **10.06%**.

**LICZBA 2 — ile niezgody to REDYSTRYBUCJA, a nie błąd:** przy niedoborze floty (pool<=2) zgodność spada do **28.64%** (n=419), przy realnym wyborze (pool>=3) wynosi **66.91%** (n=1381). Globalnie **57.06%** (n=20462).

**Rozkład przyczynowy segmentu pool<=2:** stopa niezgody 71.4%, z czego 33.1% to poziom bazowy (taki sam jak przy pool>=3, czyli niezależny od floty), a reszta to **nadwyżka z niedoboru**: **53.6% niezgód w tym segmencie jest wymuszone brakiem kurierów** (160.3 z 299).
W przeliczeniu na CAŁY zmierzony korpus (pula znana, n=756 niezgód): **21.2% niezgody to redystrybucja**, reszta to realna różnica wyboru — i to ona jest materiałem do nauki.

---

## LICZBA 1 — gotowość auto-assign (`shadow_decisions.jsonl` + rotacje)

Okno: **2026-07-28 .. 2026-08-05** (9 dni). Mianownik = decyzje dyspozytorskie po odfiltrowaniu rekordów lifecycle.

| Miara | % | true / n |
|---|---|---|
| bazowy `would_auto_assign` (dzisiejsze bramki) | 1.03% | 16 / 1551 |
| **wariant D `would_auto_assign_d`** | 19.73% | 306 / 1551 |
| wariant D' `would_auto_assign_dprime` | 17.60% | 273 / 1551 |
| kanał `auto_route == AUTO` | 10.06% | 156 / 1551 |

Rozkład kanału `auto_route`: `{'ACK': 724, 'ALERT': 671, 'AUTO': 156}`

### Rozbicie wg puli wykonalnych kurierów (`pool_feasible_count`)

| Pula | n decyzji | bazowy | D | D' | auto_route=AUTO |
|---|---|---|---|---|---|
| pool>=3 | 1074 | 1.49% | 25.42% | 25.42% | 12.94% |
| pool<=2 | 477 | 0.00% | 6.92% | 0.00% | 3.56% |
| nieznana | 0 | n/d | n/d | n/d | n/d |

### Co blokuje pozostałe 1245 decyzji (wariant D)

Rekordy z zapisanym powodem: 1147 / 1245. Rodziny powodów (rekord może mieć kilka; liczone unikalnie na rekord):

| Rodzina powodu | ile rekordów |
|---|---|
| `pos_not_informed` | 703 |
| `late_pickup_extension` | 294 |
| `late_pickup_redirect` | 276 |
| `score_distrust_ceiling` | 246 |
| `scarcity_pool` | 172 |
| `new_courier_ramp` | 113 |
| `late_pickup_committed` | 66 |
| `paczka_firmowe` | 54 |
| `plan_sla_violations` | 51 |
| `best_effort` | 51 |
| `pos_from_store` | 47 |
| `shift_end_edge` | 12 |

---

## LICZBA 2 — zgodność A1: redystrybucja vs realny błąd (`outcomes_clean_shadow.jsonl`)

Okno: **2026-05-17 .. 2026-08-05** (81 dni).
`agree` = kurier nr 1 w rankingu Ziomka **jest tym, który realnie zawiózł** (`best_courier_id == real_courier_id`, writer `shadow_collectors.py`).

> ⚠ **Globalna zgodność obejmuje 81 dni, ale ROZBICIE NA PULĘ tylko 13 dni** (2026-07-23..2026-08-04, 1800 rek. = 8.8% korpusu). Pole `pool_feasible` jest dopisywane wyłącznie tam, gdzie istnieje join z `backfill_decisions_outcomes_v1.jsonl`, a ten sięga płycej niż cały korpus. Cytowanie globalnej zgodności i rozbicia na pulę jako JEDNEGO pomiaru jest błędem.

### pełne okno outcomes

| Zakres | n (agree znane) | zgodność | niezgoda |
|---|---|---|---|
| globalnie | 20462 | 57.06% | 42.94% |
| **pool>=3** (był wybór) | 1381 | 66.91% | 33.09% |
| **pool<=2** (niedobór floty) | 419 | 28.64% | 71.36% |
| pula nieznana | 18662 | 56.97% | 43.03% |

Pokrycie pola `pool_feasible`: **8.8%** rekordów (reszta = brak joinu z backfillem, liczona osobno jako "pula nieznana").
Rozkład 8787 niezgód: pool<=2 → **299** (3.4%), pool>=3 → 457 (5.2%), pula nieznana → 8031 (91.4%).

> ⚠ **Nie cytuj tego rozkładu** — przy pokryciu 8.8% kubełek "pula nieznana" dominuje i udziały pool<=2/pool>=3 są zaniżone mechanicznie, nie merytorycznie.

**Redystrybucja vs realny błąd (segment pool<=2):**

| Składnik | wartość |
|---|---|
| stopa niezgody przy pool<=2 | 71.4% |
| poziom bazowy (stopa przy pool>=3) | 33.1% |
| niezgody obserwowane przy pool<=2 | 299 |
| ile byłoby przy poziomie bazowym | 138.7 |
| **nadwyżka = redystrybucja z niedoboru** | **160.3** (53.6% niezgód segmentu) |
| udział redystrybucji w całym zmierzonym korpusie | 21.2% (z 756 niezgód przy znanej puli) |

### okno wspólne z shadow_decisions 2026-07-28..2026-08-05

| Zakres | n (agree znane) | zgodność | niezgoda |
|---|---|---|---|
| globalnie | 1545 | 58.77% | 41.23% |
| **pool>=3** (był wybór) | 814 | 67.32% | 32.68% |
| **pool<=2** (niedobór floty) | 245 | 27.76% | 72.24% |
| pula nieznana | 486 | 60.08% | 39.92% |

Pokrycie pola `pool_feasible`: **68.5%** rekordów (reszta = brak joinu z backfillem, liczona osobno jako "pula nieznana").
Rozkład 637 niezgód: pool<=2 → **177** (27.8%), pool>=3 → 266 (41.8%), pula nieznana → 194 (30.5%).

**Redystrybucja vs realny błąd (segment pool<=2):**

| Składnik | wartość |
|---|---|
| stopa niezgody przy pool<=2 | 72.2% |
| poziom bazowy (stopa przy pool>=3) | 32.7% |
| niezgody obserwowane przy pool<=2 | 177 |
| ile byłoby przy poziomie bazowym | 80.1 |
| **nadwyżka = redystrybucja z niedoboru** | **96.9** (54.8% niezgód segmentu) |
| udział redystrybucji w całym zmierzonym korpusie | 21.9% (z 443 niezgód przy znanej puli) |

### Trend tygodniowy

| Tydzień | n | zgodność | udział pool<=2 (wśród znanych) |
|---|---|---|---|
| 2026-W20 | 2866 | 49.8% | n/d |
| 2026-W21 | 1446 | 62.2% | n/d |
| 2026-W22 | 1605 | 65.1% | n/d |
| 2026-W23 | 1692 | 52.5% | n/d |
| 2026-W24 | 1667 | 56.6% | n/d |
| 2026-W25 | 1601 | 53.7% | n/d |
| 2026-W26 | 1604 | 57.4% | n/d |
| 2026-W27 | 1622 | 56.7% | n/d |
| 2026-W28 | 1739 | 57.3% | n/d |
| 2026-W29 | 1459 | 63.7% | n/d |
| 2026-W30 | 1409 | 58.9% | 23.5% |
| 2026-W31 | 1411 | 57.1% | 21.4% |
| 2026-W32 | 341 | 62.2% | 37.3% |

---

## JOIN OBU ŹRÓDEŁ — te same zamówienia

Wspólnych zamówień w oknie nakładania: **1526** (z 1545 outcomes w oknie i 1551 zindeksowanych decyzji).

| | człowiek zgodny | człowiek inny |
|---|---|---|
| **auto-gotowe (D)** | 223 | 80 |
| **nie auto-gotowe** | 685 | 538 |

Join po `order_id`; zamówienie może mieć kilka decyzji (przekierowania) — brana jest **pierwsza** decyzja dyspozytorska. Zamówień z >1 decyzją: 0 z 1551 (max 1 decyzji na zamówienie).

Zgodność **wśród decyzji auto-gotowych (D)**: 73.60% vs 56.01% wśród nie-auto-gotowych. To bezpośredni test tezy "łatwe = auto".

---

## UCZCIWOŚĆ POMIARU — czego te liczby NIE mówią

1. **Różne okna.** Liczba 1 sięga tylko tak głęboko, jak pozwala rotacja `shadow_decisions.jsonl` (logrotate daily/30/100M) — okno bywa krótsze z dnia na dzień. Liczba 2 idzie po całym korpusie outcomes. Sekcja "okno wspólne" przycina Liczbę 2 do okna Liczby 1 — TE wartości są porównywalne, globalna nie.
2. **Pokrycie `pool_feasible` w outcomes jest częściowe** i pochodzi z joinu po `order_id` z `backfill_decisions_outcomes_v1.jsonl`; brak joinu = "pula nieznana", raportowana osobno, NIE doliczana do żadnego kubełka. Globalna zgodność i rozbicie na pulę to DWA RÓŻNE OKNA.
3. **`agree` to zgodność TOP-1, nie miara jakości.** Fałsz oznacza tylko, że zawiózł ktoś inny niż nr 1 Ziomka — nie przesądza, kto wybrał lepiej.
4. **Wykluczenia z mianownika Liczby 1:** 59 rekordów lifecycle (`CZASOWKA_RECLAIM_EVALUATION`), 0 bez pól bramki auto, 0 duplikatów po `event_id`, 0 nieparsowalnych linii.
5. **`would_auto_assign_*` to symulacja cienia, nie wykonanie.** Auto-assign jest OFF; te pola mówią "tyle by przeszło bramkę", a nie "tyle Ziomek zrobił".
6. **Żywy plik.** `shadow_decisions.jsonl` jest dopisywany na bieżąco; SHA-256 w manifeście opisuje stan z momentu odczytu (2026-08-05T08:06:26Z).

## MANIFEST WEJŚĆ

| Plik | rekordy | złe linie | okno | rozmiar | mtime (UTC) | SHA-256 |
|---|---|---|---|---|---|---|
| `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` | 382 | 0 | 2026-08-03..2026-08-05 | 36922104 B | 2026-08-05T08:05:35Z | `5e9b77c3e81c78ebad3fdc3c665236b0be75f23e78a764ee406cc14d1420e15c` |
| `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1` | 1228 | 0 | 2026-07-28..2026-08-02 | 138994886 B | 2026-08-03T00:00:04Z | `25bd08ac8c519601be1ec20f082b1464895fb201d791a909ef55cdd886e68d11` |
| `/root/.openclaw/workspace/dispatch_state/outcomes_clean_shadow.jsonl` | 20462 | 0 | 2026-05-17..2026-08-05 | 3612418 B | 2026-08-05T04:40:01Z | `1f5cb84edac27d2bbf40e037c16f23c1b99c522512fdd45b15a82fc84db64c55` |

Start odczytu: 2026-08-05T08:06:26Z · koniec: 2026-08-05T08:06:28Z

