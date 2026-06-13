# B3 — E5 czasówki score-based: raport kalibracyjny KROK 2 → ścieżka do flipu T-50

**Data:** 2026-06-13 (sesja CC, READ-ONLY na produkcji — żadnej flagi nie flipnięto)
**Zakres:** AUDIT_FIX_PLAN ETAP 5 KROK 2 + E7-DOKLEJKA #7. Cel KROK 2 = projekcja score na T-0 na czystym oknie shadow od 12.06 → rekomendacja progu T-50.
**Werdykt jednym zdaniem:** **WAIT — flipu T-50 NIE rekomenduję.** Dane są za rzadkie (1 czysty dzień), a niezależnie od ilości danych **strojenie progu jest niewłaściwą dźwignią**: surowy score≥30 jest na horyzoncie T-60/T-50 strukturalnie nieosiągalny, bo score jest zdominowany karami zależnymi od „teraz". Dowód projekcji potwierdza diagnozę E7-DOKLEJKA #7: po zdjęciu kar czasowych 6/9 kandydatów przeskakuje +30. **Następny krok = zaprojektować funkcję projekcji score na T-0 + dozbierać ≥3-4 czyste dni; dopiero potem KROK 3 (flip T-50) za ACK Adriana.**

> **Flip = decyzja Adriana (ACK).** Ten raport jest analizą decyzyjną, nie zmianą. `CZASOWKA_PROACTIVE_SCORE_BASED` pozostaje `False` (zweryfikowane w `flags.json`).

---

## 1. Co weryfikowałem (świeży stan, nie pamięć)

| Element | Ścieżka | Stan |
|---|---|---|
| Dane shadow sb_* | `dispatch_state/czasowka_eval_log.jsonl` | 99 rekordów sb_*, zakres ts 2026-06-11 08:51 → 06-13 11:50 UTC |
| Selektor score-based | `dispatch_v2/czasowka_proactive/score_selector.py` (192 l.) | KROK 1 shadow; gates score≥30 / margin≥15 / wait≤10 / R6=0; pula solo→reject |
| Breakdown komponentów | `scripts/logs/shadow_decisions.jsonl` (39 MB) | czasówki obecne z pełnym rozbiciem `best` (score-terms) |
| Narzędzie kalibracji | `eod_drafts/2026-06-10/czasowka_proactive_calib.py` | działa; uruchomione na czystym oknie |
| Poprzedni raport | `eod_drafts/2026-06-12/czasowka_proactive_calib.md` | NOTA 12.06: „próg score≥30 nigdy nie przejdzie, potrzebna inna baza score" |
| Flagi | `scripts/flags.json` | `CZASOWKA_PROACTIVE_SCORE_BASED=False`, `SCORE_SHADOW=True`, `MIN_SCORE=30/MIN_MARGIN=15/MAX_WAIT=10`; `ENABLE_BUNDLE_SYNC_SPREAD=True` |
| Testy | `dispatch_v2/tests/test_czasowka_score_selector.py` | **19/19 PASS** (uruchamiać z cwd `scripts/`, inaczej ModuleNotFoundError — lekcja #190) |

---

## 2. Okna skażone — wykluczone z analizy (E7-DOKLEJKA #8)

- **(a) PARSER_DEGRADED:** 06-06 17:53 → 06-10 18:24 UTC (zatrute auto_route; przed wdrożeniem KROK 1, nieistotne dla sb_*).
- **(b) Incydent syncworki:** **11.06 14:28 → 12.06 18:32 UTC** — kara −150 (sync) + LOADGOV LIVE we wszystkich silnikach przez ETAP4 (wspólne flagi); fix `30a01d2`. Lekcja #188.

**Czyste okno sb_* = od 12.06 18:33 UTC.** Mediana sb_score per okno (sentinel −1e9 odrzucony):

| Okno | n (evale) | median sb_score | max sb_score |
|---|---|---|---|
| W0 pre-incydent (10.06 20:40 → 11.06 14:28) | 13 | −125,5 | +67,7 |
| Skażone sync (11.06 14:28 → 12.06 18:32) | 47 | −163,7 | +58,9 |
| **CZYSTE (≥12.06 18:33)** | **16** | **−226,7** | **−27,0** |

> **Sygnał kontrintuicyjny i decydujący:** czyste okno ma medianę score **bardziej ujemną** (−226,7) niż okno skażone (−163,7). Czyli **problem nie jest skażeniem.** Gdyby sync-incydent był przyczyną, czyste dane byłyby lepsze — są gorsze. Score na T-60/T-50 jest głęboko ujemny z przyczyn strukturalnych (niżej).

---

## 3. Czyste okno (≥12.06 18:33) — surowa kalibracja

`czasowka_proactive_calib.py --since 2026-06-12T18:33:00+00:00`:

- Czasówek z evalami T-60/T-50: **5** (17 evali) — **wszystkie z 13.06** (12.06 wieczorem czasówek brak; to zjawisko lunch-peaku).
- **would_assign ≥1 raz: 0/5 (0%)** — cel ≥30%.
- Powód odrzucenia: **score_below_min w 17/17 evali (100%).**
- Rozkłady: score med −251,8 (1× sentinel −1e9); margin med −289,7; wait med 11,9.
- `sb_best_is_score_top=False` w **16/17 evali** — „best" silnika ≠ kandydat o najwyższym score.
- Sensitivity (wszystkie 6 wariantów rozluźnienia margin/solo/wait): **0 evali przechodzi** — bo blokuje pierwsza bramka (score≥30), zanim margin/wait mają znaczenie.

**Wniosek czystego okna: 0/5. Strojenie progu margin/wait nic nie da — blokuje score.**

---

## 4. SEDNO: dlaczego score jest tak ujemny (rozbicie komponentów)

Z `shadow_decisions.jsonl` rozbiłem `best` czasówek czystego okna. Dominujące **ujemne** człony score (nie metryki-minuty, tylko score-terms):

| Człon score | Typowa wartość | Natura | Czy znika do T-0? |
|---|---|---|---|
| `bonus_sync_spread` | **−150** (stała) | spread gotowości pickupów (`sync_ready_spread_min`=41–61 min) | **NIE — strukturalna** (live, `ENABLE_BUNDLE_SYNC_SPREAD=True`) |
| `bonus_r9_wait_pen_legacy` | −113 … −230 | kara za bezczynne czekanie kuriera pod restauracją | **TAK — czasowa** |
| `bonus_v3273_wait_courier(_legacy)` | −24 … −74 | wait-courier (kurier wolny TERAZ czeka do pickupu) | **TAK — czasowa** |
| `bonus_coordinator_idle` | −100 | kurier-koordynator bezczynny TERAZ | **częściowo — głównie czasowa** |
| `v325_new_courier_advantage/penalty` | −50 … −212 (1× sentinel −1e9) | obsługa nowego kuriera (Z-18: sentinel przecieka do score) | **strukturalna (artefakt)** |
| `bonus_r1/r5/r8_soft_pen`, `timing_gap_bonus`, `bonus_repo_cost_shadow` | −18 … −42 | geometria trasy / korytarz / timing | **NIE — strukturalna (realna jakość trasy)** |

**Mechanizm:** kandydat oceniany 50 min przed pickupem jest TERAZ zajęty/wolny-bezczynny. Silnik nalicza pełne kary „czekania" i „idle", bo liczy stan **w chwili ewaluacji**. Do faktycznego pickupu (T-0) kurier nie będzie bezczynnie czekał 36 min — te kary są fikcją horyzontu. To dokładnie diagnoza E7-DOKLEJKA #7: **potrzebna projekcja score na T-0, nie strojenie progów.**

---

## 5. Projekcja score na T-0 (DOWÓD koncepcji — NIE gotowa funkcja)

Estymata: od finalnego score odjąłem człony czysto czasowe (`r9_wait_pen_legacy` + `v3273_wait_courier` + `_legacy` + `coordinator_idle`) — przybliżenie stanu „kurier dojeżdża na pickup zamiast czekać bezczynnie". Człony strukturalne (sync_spread, r1/r5/r8, timing, v325, repo) **zostawione**.

| oid | cid | pos | final score | Σ kar czasowych | **projekcja T-0** | wait_min |
|---|---|---|---|---|---|---|
| 480301 | 447 | last | −275,6 | −268,7 | −6,9 | 5,9 |
| 480301 | 179 | last | −200,9 | −332,8 | +131,9 | 14,0 |
| 480301 | 179 | last | −159,0 | −233,3 | +74,3 | 8,8 |
| 480304 | 530 | last | −251,8 | −194,5 | −57,3 | 0,0 |
| 480339 | 413 | post | −122,1 | −224,4 | +102,3 | 22,3 |
| 480339 | 123 | gps | −26,9 | −288,5 | +261,6 | 36,4 |
| 480343 | 413 | post | −144,3 | −178,8 | +34,5 | 19,9 |
| 480343 | 484 | gps | −134,3 | −159,1 | +24,9 | 23,4 |
| 480343 | 123 | gps | −37,3 | −213,7 | +176,4 | 24,0 |

**Rozkład projekcji:** n=9, min −57,3, **median +74,3**, max +261,6. **Projekcja ≥ +30: 6/9. Projekcja ≥ 0: 7/9.**

**Interpretacja:**
- Surowy score: 0/9 ≥ +30 → projekcja: **6/9 ≥ +30**. Inwersja obrazu potwierdza: **kandydaci są dobrej jakości na PRZYSZŁY pickup**, score tłumią kary „teraz".
- **⚠ Ostrzeżenia uczciwości (ta tabela to dowód mechanizmu, NIE produkcyjna funkcja):**
  1. **n = 9 candidate-evali z 5 czasówek z 1 dnia** — niewystarczające do progu.
  2. Projekcja = naiwne odjęcie 4 członów. Over-credit widoczny: 480339/cid=123 → +261,6 jest nierealnie wysoki (zdjęty `coordinator_idle=−100` jest częściowo strukturalny dla kuriera-koordynatora; `v3273_wait` przy GPS może być realne, jeśli kurier faktycznie dotrze za wcześnie).
  3. **`bonus_sync_spread=−150` zostawiony jako strukturalny** — ale to też do rozstrzygnięcia projektowego: dla czasówki przypisywanej **solo i wcześnie**, spread gotowości wobec istniejącego bagu może nie być właściwą karą (czasówka ma własne, twarde okno odbioru). To pytanie do E7 (nakładanie kar — DOKLEJKA #2).

---

## 6. Dostateczność danych — TWARDO

| Wymóg | Stan |
|---|---|
| Czysty horyzont sb_* | od 12.06 18:33 UTC |
| Czyste dni z czasówkami | **1 (13.06)** — 12.06 wieczór bez czasówek (lunch-peak phenomenon) |
| Czyste czasówki | **5** |
| Czyste evale T-60/T-50 | **17** |
| would_assign w czystym oknie | **0/5** |

**Werdykt dostateczności: NIEWYSTARCZAJĄCE.** Próg produkcyjny na 5 czasówkach z 1 dnia = zgadywanie. **Potrzeba ≥3-4 czystych dni roboczych z lunch-peakiem** (czasówki ≥60 min prep kumulują się 11-14 Warsaw) → realistycznie **~15-25 czasówek / ~50-80 evali**. Przy bieżącym tempie (5 czasówek/dzień roboczy): gotowe **~17.06-18.06** (zbieżne z kickoffem E7 at#131 17.06).

---

## 7. Rekomendacja progu T-50 — i dlaczego jej (jeszcze) NIE ma

**Nie podaję liczby progu T-50, bo byłaby sfabrykowana.** Dwa niezależne powody:

1. **Za mało danych** (1 dzień, 5 czasówek) — §6.
2. **Próg na surowym score to zła oś** — §4-5. Nawet z 30 dniami danych `score≥30` na surowym at-eval score da ~0% would_assign, bo kary czasowe są strukturalne dla horyzontu T-60/T-50. Strojenie `MIN_SCORE`/`MIN_MARGIN`/`MAX_WAIT` nie naprawia przyczyny.

**Zamiast progu — rekomendacja działań (kolejność), wszystkie przed flipem za ACK:**

1. **Zaprojektować funkcję projekcji score na T-0** (rdzeń KROK 2/E7-DOKLEJKA #7). Wariant czysty (Z2 „jakość ponad szybkość"): policzyć score kandydata **z planem ustawiającym go w stanie „dojeżdża na pickup w oknie odbioru"** zamiast „wolny/zajęty teraz" — tak by człony `r9_wait`/`v3273_wait`/`coordinator_idle` liczyły realny wait wobec **okna odbioru czasówki**, nie wobec „teraz". To NIE jest odejmowanie członów post-hoc (§5 to tylko dowód), lecz przeliczenie ścieżką `assess_order` z przesuniętą kotwicą czasu. Spiąć z re-tune R6/selekcji w E7 (ta sama ścieżka).
   - Otwarte pytanie projektowe do Adriana: czy `bonus_sync_spread` (−150) ma w ogóle obowiązywać czasówkę przypisywaną solo/wcześnie (czasówka ma własne twarde okno odbioru — spread wobec cudzego bagu może być nietrafny). To wchodzi w DOKLEJKA #2 (nie karać tej samej osi 2×).
2. **Dozbierać ≥3-4 czyste dni** (do ~17-18.06) — shadow już loguje, zero akcji potrzebnej.
3. **Po projekcji + danych:** powtórzyć kalibrację na **projektowanym** score → wtedy realna sensitivity i próg z danych → **STOP → ACK Adriana** → KROK 3 (flip TYLKO T-50: `CZASOWKA_PROACTIVE_SCORE_BASED=true`) → KROK 4 KPI.
4. **Higiena przy KROK 3** (side-findingi, READ-ONLY teraz):
   - **Z-18 sentinel −1e9** (v325 new-courier hard-skip) przecieka do `sb_score` (1× w czystym oknie, oid=480301 cid=530). Serializować flagę zamiast score=−1e9, inaczej psuje statystyki kalibracji.
   - **Cron `czasowka_state_cleanup` ZEPSUTY:** `ModuleNotFoundError: No module named 'dispatch_v2'` w `czasowka_state_cleanup_cron.log` (zły cwd/PYTHONPATH — to dokładnie Z-05 side-finding „cleanup_stale niewpięty"). proposals_state mimo to skurczył się 251KB→17KB (coś czyści, ale nie ten cron). Naprawić cwd crona przy KROK 3.

---

## 8. KPI do KROK 4 (gdy flip nastąpi — przypomnienie, nie teraz)

Z AUDIT_FIX_PLAN E5 KROK 4: % czasówek przypisanych przed T-40 (cel ≥30%), R6-breach przed/po, „żałowane wczesne przypisania" (czasówka przypisana wcześnie → potem PANEL_OVERRIDE/replan), wait kuriera. Baseline po E4: WAIT/EMIT/FORCE — mierzyć przyrost NAD nowym baseline, nie nad „100% FORCE".

---

## 9. Status KROK-ów E5

| Krok | Status |
|---|---|
| KROK 1 (shadow selektor sb_*) | ✅ DONE (10.06, `f1f37d3`) |
| KROK 5 (waiting_at persist) | ✅ DONE (10.06, `c2ca316`) |
| **KROK 2 (ten raport)** | ✅ **Analiza domknięta: WAIT.** Dane niewystarczające (1 dzień/5 czasówek) + próg na surowym score to zła oś. Deliverable = projekcja score na T-0 + dozbiór ≥3-4 dni. Liczby progu BRAK (uczciwie). |
| KROK 3 (flip T-50) | ⛔ ZABLOKOWANY — czeka na: (a) funkcję projekcji T-0, (b) ≥3-4 czyste dni, (c) **ACK Adriana na próg z przeliczonych danych**. |
| KROK 4 (KPI) | po KROK 3 |

**Następne uruchomienie kalibracji:** `cd /root/.openclaw/workspace/scripts && /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-06-10/czasowka_proactive_calib.py --since 2026-06-12T18:33:00+00:00 --md dispatch_v2/eod_drafts/<data>/czasowka_proactive_calib.md` (≥17.06, po dozbiorze danych i implementacji projekcji).
