# C04 — RUNTIME-ORACLE: reassignment_forward_shadow (_SYNTH_POS „duch przerzutu") + reassignment_quality (at-193 Q-gate)

**Agent:** C04-reassign-fwd-quality · **Lane:** C (runtime-oracle, C9/C11) · **Tryb:** READ-ONLY (zero edycji/restartów/flipów/--notify/--live).
**Data:** 2026-06-30 ~16:40 UTC, sesja tmux 2. **Oracle = recompute prawdy DRUGĄ metodą na ŻYWYM jsonl** (nie lektura kodu).
**Próba:** `reassignment_shadow.jsonl` (21 375 rek., 23.06→30.06 16:35, FRESH co 3 min, **rośnie w trakcie**), `quality_*` na 5 813 rek. (od 28.06 15:27, po env-flip 29.06 11:09). `reassign_global_select.jsonl` (53 rek., event-driven, ostatni 12:06). `sla_log.jsonl` (13 289), `gps_delivery_truth.jsonl` (332 KB, GROUND-TRUTH fizyczny).
**Determinizm:** narzędzie `reassignment_quality_replay` odpalone 2× read-only (bez --notify) → BAJT-IDENTYCZNE. Mój niezależny join `sla_log`+`gps_truth` → REPRODUKUJE liczby narzędzia co do sztuki.

---

## TL;DR — 1 fix POTWIERDZONY ŻYWO + 4 otwarte defekty spójności

1. **59% fałszywych ratunków = ZAMKNIĘTE ŻYWO (seed/registry STALE).** Flaga `ENABLE_REASSIGN_RESCUE_REQUIRE_HOLDER_ABSENT=1` jest **ON w drop-inie** (potwierdzone `systemctl show`), efektywna od **29.06 11:09:28** (pierwszy `quality_rescue_suppressed_working=True`). **PO flipie: 32/32 żywe ratunki mają ZMIERZONY przewidziany breach (a_pred≠None ∧ >35), ZERO fikcyjnych.** Legacy `a_late` (a_cand is None) 53,9% → live 20,3%; 1 523 fałszywych ratunków stłumionych. A4/allocation_family „void — 59%" = cytat z pamięci 29.06 SPRZED flipa = **klasa E doc-drift**, nie żywy stan.
2. **at-193 Q-gate jest DECYZYJNIE-VOID (untested), mimo poprawnej arytmetyki.** Precyzja ratunku liczona na **n=7** (CI Wilsona [36 %, 92 %], 56 pp = nieinformatywne), skupiona na **2 kurierach** (cid400×3, cid492×4), **tylko 2/7 ground-truth GPS** (5/7 button-proxy), **2/7 predykcji było FAŁSZYWYCH** (oid484203 przewidział 70 min → dowiózł 24 min). Narzędzie NIE kłamie (mój join = 5/7=71 % dokładnie), ale „validated" jest niemożliwe na tym N. Werdykt at-193 powinien brzmieć **„za mało danych", nie GO**.
3. **Trójtaksonomia pozycji NIESPÓJNA na 7/10 żywych tokenów** (`_SYNTH_POS` l.64 vs `_usable_pos` l.205 vs `_REAL_POS`/`_pos_trusted` l.68/444). `_SYNTH_POS` zdryfował od słownika silnika (nigdy nie trafia `no_gps`/`None`/sufiksowanych `last_*`). Bramka DECYZYJNA (`_usable_pos`) jest poprawna → mały żywy wpływ; `_REAL_POS` zepsuty → latentny.
4. **Dwie powierzchnie wyjścia, dwie różne bramki:** Telegram (OFF) odpala `would_reassign` (margines, l.353/457); konsola+global_select odpalają `quality_reassign` (l.283). **3 727 margin-duchów BEZ rozumowania o breachu** (70 % rek.). at-193 waliduje `quality_reassign` (= bramka konsoli) — to dobrze — ale NIE waliduje tego, co pokazałby Telegram.
5. **Cicha awaria zapisu** (`_append_jsonl` l.413-414 łyka wyjątek) + **dryf schematu/flagi** (pola require-absent doszły 11:04, flaga 11:09 → 4,5-min okno 6 anomalii w joinie replay).

---

## METODA ORACLE (druga, niezależna prawda) — co policzyłem i jak

| Twierdzenie do weryfikacji | Metoda PROXY narzędzia | Moja DRUGA metoda (niezależna) | Wynik |
|---|---|---|---|
| 59 % fałszywych ratunków | pamięć 29.06 (replay sprzed flipa) | rekonstrukcja `a_late` legacy = `(not a_in_pool) or a_bag_time>35` z serializowanych pól + split BEFORE/AFTER env-flip (`rescue_suppressed_working` jako proxy momentu flipa) | legacy 96,2 % ratunków = pool-absence; **post-flip 32/32 measured-late, 0 fikcji** → fix DZIAŁA |
| precyzja ratunku 5/7=71 % | `reassignment_quality_replay.build_report` (join sla_log) | własny loader `sla_log`+`gps_truth`, ręczny join per-oid, breach = `dt>35 ∨ ok=False`, Wilson CI | **5/7=71 % zreplikowane CO DO SZTUKI**; CI [36,92]; 2/7 GT |
| over-eager 381/432=88 % | narzędzie | własny join noflag→sla | **381/432=88 % zreplikowane** |
| `_SYNTH_POS` ≠ silnik | — | `grep` producentów `pos_source` w `courier_resolver.py` + crosstab 3 taksonomii × wszystkie żywe tokeny | 7/10 tokenów rozjazd; `_SYNTH_POS` trafia 0 żywych fikcji |
| która bramka odpala notify | lektura | crosstab `would_reassign`×`quality_reassign` na 5 813 rek. + grep konsumentów | notify=would (l.457); konsola=quality (feed.py:258) |

**Inwarianty-tripwire (lane C):** ✅ determinizm (2× identyczne); ✅ mój join = narzędzie (brak rozjazdu arytmetyki); ⚠ `delivered_at` = **button-truth** (5/7 breachy proxy, 2/7 GPS) — oznaczone `proxy-certified` vs `ground-truth` poniżej; ✅ ZERO fikcyjnych ratunków post-flip (sprawdzony warunek a_pred=None ∧ holder-absent = 0 w qg2).

---

## PRZYRZĄD 1 — `tools/reassignment_forward_shadow.py` (collector + gate JAKOŚCI)

### 1A. Ramię RATUNEK — fix 59% POTWIERDZONY ŻYWO (VALIDATED)
- **Mechanizm legacy (l.260):** `a_late = (a_cand is None) or a_measured_late`. `a_cand is None` = holder wypadł z hipotetycznej puli re-pickupu (np. już odebrał / pre_shift / bez GPS) ≠ spóźniony → 59% (memory) / 96,2% (mój recompute legacy) fałszywych ratunków.
- **Fix (l.251-257, flaga `RESCUE_REQUIRE_HOLDER_ABSENT`):** `a_genuinely_absent = (a_cand is None) and (not a_in_fleet)`; ratunek z samej absencji TYLKO gdy holder NIEOBECNY w żywej flocie; pracujący bez zmierzonego R6>próg → `rescue_suppressed_working=True`.
- **DOWÓD ŻYWY (drugi recompute):**
  - env-flip efektywny **29.06 11:09:28** (pierwszy `rescue_suppressed_working=True`; 1 526 wystąpień do 30.06 16:38).
  - **AFTER-flip (4 511 rek.): rescue=32, measured-late=32, fikcja=0.** BEFORE-flip (32 rek. z polem, env jeszcze OFF): rescue=6, wszystkie fikcyjne (6 anomalii oid484191-200 z reason „ratunek…R6>35", `supp=False`).
  - legacy `a_late` 53,9 % → live 20,3 % (n=4 534 live-era).
- **Werdykt:** **VALIDATED** — ramię ratunek post-flip odpala wyłącznie na ZMIERZONYM przewidzianym breachu. Seed „void/59%" = STALE (klasa E). `proxy-certified` (a_pred = predykcja silnika, nie fizyka).

### 1B. ⚠ Ramię RATUNEK dziedziczy PESYMIZM R6 silnika (G, otwarte u źródła)
W denominatorze precyzji (7 zmierzonych ratunków) **2/7 predykcji było FAŁSZYWYCH** — gate zmierzył „obecny się spóźni" a obecny dowiózł na czas:

| oid | holder | a_bag_time PRZEWIDZ. | realny dt | src | breach? |
|---|---|---|---|---|---|
| 484203 | 400 | **70,4** | 23,9 | button | NIE (fałszywy late) |
| 484243 | 400 | **40,9** | 23,7 | button | NIE (fałszywy late) |
| 484352 | 400 | 57,7 | 39,0 | button | TAK (39 vs 35 = krucha, button-inflate) |
| 484404 | 492 | 39,3 | 57,4 | button | TAK |
| 484423 | 492 | 40,2 | 65,8 | **GPS** | TAK |
| 484439 | 492 | 35,4 | 53,8 | **GPS** | TAK |
| 484452 | 492 | 38,7 | 65,5 | button | TAK |

Predykcja 70 min→24 min realnie (484203) = ten sam optymizm/pesymizm ETA co `pickup_slip` (calibration 29.06: „Ziomek przewiduje R6 PESYMISTYCZNIE"). To NIE bug tego przyrządu — to wejściowy ETA (R8 w allocation_family) — ale **kontaminuje nawet „measured-late" ramię**. dedup→ETA-pessimism (R8).

### 1C. Trójtaksonomia pozycji NIESPÓJNA (F/N/B, otwarte) — „_SYNTH_POS never-equalized-with-engine"
Crosstab 3 klasyfikatorów × wszystkie żywe tokeny `pos_source` (a+b, n=42 750 wystąpień). Producenci tokenów: `courier_resolver.py:738/742/746/941/1006/1016/1091/1465/1474/1526` → `{gps, no_gps, pre_shift, last_assigned_pickup, last_picked_up_{pickup,delivery,interp,recent}, last_delivered, working_override_synthetic, post_shift_start_synthetic}` + Python `None` (holder spoza floty).

| token (liczność a+b) | `not _SYNTH_POS` → **a_real/b_real** (l.359-360) | `_usable_pos` → **quality** (l.205) | `_REAL_POS` → **notify** (l.444) | rozjazd |
|---|---|---|---|---|
| `last_assigned_pickup` (14 733) | True | True | **False** | ⚠ 3 różne werdykty na NAJCZĘSTSZYM tokenie |
| `last_picked_up_interp` (7 931) | True | True | **False** | ⚠ |
| `last_picked_up_pickup` (5 445) | True | True | **False** | ⚠ |
| `no_gps` (2 668) | **True** | **False** | False | ⚠ fikcja dla quality, „real" dla _why |
| `None` (1 867, holder-absent) | **True** | **False** | False | ⚠ brak pozycji liczony „real" |
| `last_delivered` (1 998) | True | True | True | ok |
| `last_picked_up_recent` (252) | True | True | **False** | ⚠ |
| `gps` (5 500) | True | True | True | ok |
| `pre_shift` (2 092) | False | False | False | ok (zgodne) |

- **`_SYNTH_POS={none,pin,pre_shift,""}` (l.64)** trafia 0 żywych tokenów-fikcji poza `pre_shift`: `"none"`(string) nie istnieje (silnik daje Python `None` lub `no_gps`), `pin`/`""` martwe. → `a_real`/`b_real` zwraca **True dla `no_gps` i `None`** = LYING DISPLAY (klasa E/M, K5 sentinel-as-classifier). Używane tylko w `_why` (reason text) i notify-display → **żywy wpływ niski** (notify OFF).
- **`_REAL_POS` (l.68) exact-match** zawodzi na WSZYSTKICH sufiksowanych `last_*` (`last_picked_up_pickup`≠`last_picked_up`) → `_pos_trusted` (l.444, filtr `NOTIFY_TRUSTED_ONLY`) przepuści tylko `gps`+`last_delivered` → po flipie Telegrama tnie ~cały realny ruch (latentny over-suppress).
- **`_usable_pos` (l.205) = JEDYNA poprawna** (gps ∨ `last_*` prefix ∨ store/interp; poprawnie odrzuca no_gps/pre_shift/None). To ona gateuje `quality_pos_ok` → **bramka decyzyjna jest OK**, rozjazd siedzi w 2 display/notify-klasyfikatorach.
- **Werdykt:** **void jako spójny klasyfikator pozycji** (3 niezależne kopie, 7/10 rozjazd, drift od silnika); decyzja ratowana przez `_usable_pos`. dedup→R1/grupa-3b (out-of-engine position gate, „klasa wraca").

### 1D. Dwie powierzchnie, dwie bramki (B/I, otwarte)
Crosstab na 5 813 rek. z `quality_*`:

| | quality=False | quality=True |
|---|---|---|
| **would=False** | 1 639 | 79 |
| **would=True** | **3 727** | 368 |

- `would_reassign` (l.353, margines Δscore≥15) = **4 095 (70 %)**; `quality_reassign` (l.283, gradient breach/save) = **447 (7,7 %)**; wspólne 368.
- **Notify Telegram (l.457) bramkuje `would_reassign`** → pokazałby 3 727 margin-duchów BEZ rozumowania o breachu (= A4 „938/1014 never=over-eager"). **Konsola koordynatora (`feed.py:258`) + de-pile (`reassignment_global_select.py:126`) bramkują `quality_reassign`.**
- at-193 (`reassignment_quality_replay`) **waliduje `quality_reassign`** = bramkę konsoli/global_select (✅ właściwa), ale Telegram-arm (would) jest osobno NO-GO (`reassignment_shadow_eval`, SPENT 27.06). **Walidacja jednej bramki ≠ walidacja drugiej powierzchni.**
- Cross-repo: `feed.py` NIE stosuje `_pos_trusted` (Telegramowy), ale `quality_reassign` już embeduje `_usable_pos` (pos_ok) → konsola pokazuje duchy na `last_*_pickup`/`interp`, które Telegram by stłumił (rozjazd FILTRA, nie braku filtra; zgodne z A6 grupa-3b).

### 1E. Cichy zapis (M, otwarte) + dryf schematu/flagi (N/D, transient)
- `_append_jsonl` l.413-414: `except Exception: _log.warning` → utrata rekordu przyrządu NIEWIDOCZNA (wzorzec dzielony z `bundle_calib_shadow.py:524`, `carried_first_guard.py`; A4 §8 M).
- Pola `quality_a_in_fleet`/`quality_rescue_suppressed_working` doszły **29.06 11:04:56**, env-flip **11:09:28** → 4,5-min okno (6 anomalii) gdzie pole jest, zachowanie legacy. `reassignment_quality_replay` joinuje WSZYSTKIE ery; ratuje go #7-exclude (a_pred=None → infeasible-transient, poza precyzją). Mały, miniony, zmitygowany.

---

## PRZYRZĄD 2 — `tools/reassignment_quality_replay.py` (WERDYKT at-193, PENDING 01.07 19:00)

### Wynik narzędzia (2× determ., read-only):
```
zleceń z quality_*: 489 | ratunek: 21 (late-ETA 7, infeasible-transient 14) | oszczędność: 16 | bez przerzutu: 452
PRECYZJA RATUNKU (late-ETA): z 7 zostawionych, breach: 5 = 71% (2/7 fizyczny GPS, reszta klik)
OSZCZĘDNOŚĆ: 16, mediana save 16.7 min (counterfactual)
over-eager: z 432 'bez przerzutu', on-time: 381 = 88%
```
### Oracle-krytyka (druga metoda):
- **Arytmetyka VALIDATED-honest:** mój niezależny join `sla_log`+`gps_truth` → 7 left, 5 breach=71 %, 381/432=88 % — co do sztuki. #7-fix (l.109-110: wyklucz `rescue_infeasible` a_pred=None z precyzji) jest **POPRAWNY** — chroni mianownik przed legacy-fikcją; bez niego mianownik mieszałby 14 infeasible (66,7 % ratunków per-order) i dawał mylące „0 %".
- **DECYZYJNIE UNTESTED:** denominator **n=7**, CI Wilsona **[36 %, 92 %]** (szer. 56 pp), skupiony na **2 kurierach**, **2/7 ground-truth** (reszta button ±~3 min — oid484352 dt=39 krucha na inflacji buttonu), **2/7 predykcji fałszywych**. To NIE jest baza do „GO ramienia ratunek". Werdykt 01.07 powinien = **„za mało danych, przedłuż shadow"**, nie validated.
- **Inwariant audytu narzędzia POPRAWNY:** komentarz l.107 „a_late ⇔ a_pred=None = 100 % infeasible" — zgadza się z moim recompute (post-flip wszystkie ratunki mają a_pred≠None; infeasible-transient to legacy/absent). Narzędzie jest świadome ograniczenia i samo deleguje decyzję do Adriana (l.150).
- **Werdykt:** **untested** (jako wejście decyzji ghost→live); arytmetyka i #7-fix = validated. `proxy-certified` (5/7 button).

---

## REASSIGN_GLOBAL_SELECT (poboczne, w zleceniu „czytaj też")
- `reassign_global_select.jsonl`: timer `dispatch-reassign-global-select` **AKTYWNY** (3 min, ostatni run 16:41), ale jsonl ostatni wpis **12:06** — pisze TYLKO przy pile-onie (event-driven); brak popołudniowych pile-onów (niski load) → **brak świeżego okna do oracle**. Pola zdrowe (`maxpile_before/after`, `spread_improved`, `dropped`). Bierze `quality_reassign=True` (l.126) i odpala PRAWDZIWY `global_allocate`/`_tentative_assign` → bucket dziedziczony z `_selection_bucket` (NIE własna fikcja; A6 potwierdza). Seed: VALIDATED (5/5 pile-onów). **NIE re-derywowałem** (poza moim instrument-scope; brak fresh danych).

---

## TABELA POKRYCIA (jawne luki — C11-c)

| Obszar | Zbadane? | Metoda | Luka/powód |
|---|---|---|---|
| `reassignment_shadow.jsonl` quality-arm | ✅ pełne | recompute legacy-vs-live, before/after flip, 5 813 rek. | — |
| at-193 `reassignment_quality_replay` | ✅ pełne | 2× run + niezależny join sla/gps, Wilson CI | — |
| `_SYNTH_POS`/`_usable_pos`/`_REAL_POS` | ✅ pełne | crosstab × żywe tokeny + grep producentów silnika | — |
| would↔quality wiring | ✅ pełne | crosstab 5 813 + grep konsumentów (3 repo) | — |
| `reassign_global_select` | ⚠ częściowe | freshness+pola+seed | event-driven, jsonl stale 12:06 (brak pile-onów pop.), nie oracle'owane |
| breach ground-truth | ⚠ proxy | 2/7 GPS, 5/7 button | `delivered_at` button-truth ±3 min (A4 §7 fundament-caveat); tylko `gps_delivery_truth` GT |
| konsola `feed.py` overlay LIVE? | ❌ luka | grep konsumenta tylko | `PANEL_FLAG_*` overlay on/off NIE zweryfikowany (cross-repo, granica STOP-dyspozytornia); potwierdzony konsument quality_reassign, status flagi nie |
| `preshift_rescue_peak_review.py` | ❌ luka | grep wykazał konsumenta quality_reassign | nie odpalony (poza zleconymi 2 przyrządami; osobny at-job) |
| Δscore margines (would) precyzja | ❌ luka | — | mierzy `reassignment_shadow_eval` (SPENT 27.06, NO-GO), nie at-193 |

**Świeżość cytatów:** linie re-grepowane 16:40 UTC (`_SYNTH_POS`=64, `_usable_pos`=205, a_late-legacy=260, require-absent=255, would=353, notify-would=457, append-except=414, replay rescue_eta=109). Plik `reassignment_shadow.jsonl` ROŚNIE w trakcie (collector live) → liczby per-order drgają ±1 (489 vs 488 między odczytami).

---

## DEDUP / HANDOFF
- **R1 / grupa-3b (one selection key, out-of-engine position gate):** 1C `_SYNTH_POS` drift + 1D quality≠would. „klasa wraca ≥4×" — naprawiając pozycję, tknąć `_SYNTH_POS`+`_usable_pos`+`_REAL_POS` RAZEM z engine `_selection_bucket` (K5 sentinel-as-classifier most).
- **ETA-pessimism (R8):** 1B — measured-late ramię dziedziczy pesymizm forward-ETA; nie naprawiać w tym przyrządzie.
- **silent-write-failure:** 1E `_append_jsonl` — dzielony z bundle_calib/carried_first_guard (klasa M, jeden fix-wzorzec).
- **stale-verdict (klasa E):** A4/allocation_family „reassignment_forward_shadow void 59%" = doc-drift; ŻYWO ramię ratunek FIXED (require_absent ON od 29.06 11:09). Zaktualizować registry.
- **at-193 power:** untested ≠ void; potrzebny dłuższy shadow (n=7 → potrzeba ~30-50 measured-late ratunków zanim precyzja niesie sygnał).
