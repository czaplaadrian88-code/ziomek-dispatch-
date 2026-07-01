# C03 — feas_carry_readmit (lane C RUNTIME-ORACLE) — VOID potwierdzony drugą metodą

**Agent:** C03-feas-carry · **lane:** C (oracle, C9/C11) · **READ-ONLY** · 2026-06-30 ~16:0x UTC
**Backing:** ten plik. Numery linii ze świeżego grepu HEAD `8024705` (re-grep, nie z seed).
**Skrót werdyktu:** **VOID** — wszystkie 3 przyrządy liczą `regret`/`benefit`/`true_delta` z PREDYKCJI (`objm_r6_breach_max_min`), **ZERO joina z `decision_outcomes.jsonl`** (który ISTNIEJE, FRESH 15:58, ma `r6_actual_min`/`r6_breach`/`delivered_at`). Join wykonany przeze mnie pokazuje: trigger fantomowy (predykcja breach 99,7% vs realnie 14,8%), „benefit" sentinelowo skażony (mean 302 vs median 9,7 min), korzyść strukturalnie nieobserwowalna (81,5% celów redirectu nigdy nie pojechało). Oba bliźniaki dzielą ten sam błąd + asymetria B.

---

## 0. Co to za przyrząd (3 narzędzia + bliźniacza para w silniku)

| Rola | Plik:linia (świeże) | Czyta | Liczy |
|---|---|---|---|
| collector (shadow) | `dispatch_pipeline.py:1215` `_feas_carry_blind_shadow` (emit `:1203`, path `:1200`) | pełna pula `candidates` (z NO) | pisze `feas_carry_blind_shadow.jsonl`: `would_redirect`, `regret_min=chosen_objm−rej_objm`, `redirect_objm`, `chosen_forgiven_breach` |
| **bliźniak LIVE (akcja)** | `dispatch_pipeline.py:1289` `_feas_carry_readmit_pick`; wpięcie `:6266-6301` (flaga `ENABLE_FEAS_CARRY_READMIT`) | jw. | ZWRACA kandydata NO do promocji na `top[0]`, `verdict NO→MAYBE` (`:6278`), `regret=chosen_objm−rej_objm` (`:1343`) |
| werdykt replay („dowód pozytywnego wpływu") | `eod_drafts/2026-06-27/feas_carry_readmit_replay.py` | TYLKO `feas_carry_blind_shadow.jsonl` | materialność/benefit/Pareto z `regret_min`/`redirect_objm` |
| przegląd shadow | `tools/feas_carry_blind_review.py:61` | jw. | `redirect_pct`, `regret_mean`, werdykt build-czy-nie (`:87` próg `≥30% & mean≥5`) |
| monitor post-flip | `tools/feas_carry_readmit_postflip.py:25` (LINE_RE), `:81` (bad_regret) | journal `dispatch-shadow` + shadow.jsonl | `cap_violations`, `bad_regret≤0`, błędy ścieżki |

**Stan flag (zmierzony z flags.json):** `ENABLE_FEAS_CARRY_READMIT=False` (rollback, akcja martwa LIVE) · `ENABLE_FEAS_CARRY_BLIND_SHADOW=True` (cień biegnie, jsonl FRESH 15:16, 1682 rek) · `ENABLE_BEST_EFFORT_OBJM_R6_KEY=True` (bliźniak selekcji-path ON — patrz §6).

---

## 1. METODA ORACLE (druga, niezależna)
1. **Odpalenie narzędzia 2× (determinizm):** `feas_carry_readmit_replay --cap 40` → `would_redirect 915 (54,4%)`, `LIVE 914 (54,3%)`, `benefit median=9,7 sum=276443,8`, **werdykt ⏸ WAIT (Pareto ❌)**. Run1==Run2 (diff pusty). Read-only (tylko czyta jsonl, brak --notify/--live, nic nie pisze do dispatch_state).
2. **Niezależny recompute** (`scratchpad/oracle_feas_carry.py`, 2× deterministyczny): odtworzyłem `wr=915/54,4%`, `live=914/54,3%` — **arytmetyka narzędzia ZGODNA**.
3. **JOIN z prawdą-przyciskową:** `feas_carry_blind_shadow.jsonl` × `decision_outcomes.jsonl` po `order_id` (n joinable=443 zlec.; 886 decyzji would_redirect z r6). Liczę realny `r6_breach`/`r6_actual_min` kuriera który FAKTYCZNIE pojechał (`actual_cid`) oraz podzbiór gdzie `chosen` realnie wykonał (`actual_cid==chosen_cid`).
4. **Inwarianty-tripwire:** delta≥0 (benefit nie-sentinelowy); Pareto `redirect_objm<chosen_forgiven_breach`; trigger realny vs predykcja; obserwowalność kontrfaktu.

**CAVEAT prawdy:** `decision_outcomes.r6_actual_min`/`r6_breach` = **button-truth** (z `delivered_at`/`picked_up_at`), **proxy-certyfikowany NIE ground-truth** (fundament-caveat: 0/377 auto_geofence GT, klik panelu ~192s przed GPS, ±~3min). Jedyny GT producent = `gps_delivery_truth.jsonl` (osobny). Mój werdykt o KIERUNKU (predykcja ≫ realność) jest odporny na ±3min; bezwzględne % są proxy.

---

## 2. WYNIKI JOINA (button-truth) — rdzeń werdyktu

### 2a. Trigger jest FANTOMOWY (klasa G — kalibracja na złej osi)
Populacja przyrządu = decyzje gdzie `chosen_forgiven_breach>0` („zwycięzca niósł WYBACZONY breach"). Join, podzbiór gdzie chosen realnie wykonał (n=371):
- predykcja `chosen_forgiven_breach>0`: **370/371 = 99,7%**
- **REALNY breach>0 (r6_actual−35>0): 55/371 = 14,8%**
- predykcja median 6,1 min · realny breach median **0,0 min** (mean 1,4) · over-predykcja median +5,4 min
- → **~85% „wybaczonych breachy" które przyrząd ma naprawiać NIGDY się nie zmaterializowało.** Sygnał fundujący cały instrument jest w >5/6 fikcją.

### 2b. Realny breach na populacji ≪ predykcja (potwierdza „real breach kilkanaście%")
| segment | joined+r6 | realny r6_breach (actual courier) | chosen wykonał → breach |
|---|---|---|---|
| ALL decyzje | 1627 | 246 = **15,1%** | 55/371 = 14,8% |
| WOULD_REDIRECT | 886 | 104 = **11,7%** | 21/188 = 11,2% |
| LIVE(cap) | 885 | 103 = **11,6%** | 21/188 = 11,2% |
(zadanie mówiło „real breach 8%" — mój pomiar button-truth ~11-15%, ten sam rząd; kierunek identyczny: predykcja 99,7% ≫ realność.)

### 2c. Korzyść jest KONTRFAKTEM nieobserwowalnym (klasa C9 — nie da się zwalidować)
`redirect_cid` był ODRZUCONY (verdict NO), nigdy nie przypisany. Obserwowalny tylko gdy człowiek go wybrał (`actual_cid==redirect_cid`): **167/902 = 18,5%** would_redirect. W 81,5% cel redirectu NIGDY nie pojechał → `regret_min` (benefit) jest predykcją na kurierze którego nie da się zważyć ŻADNYM joinem. Gdzie obserwowalny (167): breach 20/167 = **12%** — bez przewagi vs chosen 11,2%.

### 2d. „Benefit" SUM sentinelowo skażony (klasa M / root K5)
- `regret_min(live)`: median 9,7 · **p99=10162 · max=10177** · **sum=276444** (mean 302 ≫ median 9,7).
- `chosen_forgiven_breach`: bucket `>100 (sentinel)` = **49/1682**; `>1000` = 32 (1,9%). Wartości ~10000 min są NIEFIZYCZNE dla R6 (cap 35 min) = sentinel pozycji/feasibility (most do K5 „sentinele-jako-dane").
- regret>60 min (niemożliwe dla pojedynczego R6): **54/914 = 5,9% live**. Replay raportuje `sum=276443,8min` jako „redukcja worst-breach floty" = ~4600h → na twarz absurd; SUM zdominowany przez ~50 sentineli.

---

## 3. ŹRÓDŁO PREDYKCJI vs OUTCOME (główne pytanie zadania)
**Czy instrument liczy `true_delta` z joina `decision_outcomes` czy proxy?** → **PROXY (predykcja).** Dowód (grep świeży):
- `grep -l "decision_outcomes|delivered_at|r6_actual|gps_delivery_truth|picked_up_at|sla_log"` po 3 narzędziach → **ŻADEN plik nie referuje outcome/GT.** Wszystkie czytają wyłącznie `feas_carry_blind_shadow.jsonl` i pola predykcyjne.
- `regret_min` zdefiniowany `dispatch_pipeline.py:1267` = `chosen_objm − rej_objm`, gdzie `objm = c.metrics["objm_r6_breach_max_min"]` (`objm_lexr6.py:23`) = PREDYKCJA silnika, nie pomiar dostawy.
- `decision_outcomes.jsonl` z `r6_actual_min`/`r6_breach`/`delivered_at` ISTNIEJE i jest FRESH (15:58, 1577 rek) — ale ŻADEN przyrząd feas_carry go nie dotyka. Join możliwy (zrobiłem), instrument go nie robi.

---

## 4. „Oba bliźniaki ten sam błąd?" → TAK + asymetria B
- **Wspólny błąd (C/G):** `_feas_carry_blind_shadow` (`:1227`) i `_feas_carry_readmit_pick` (`:1304`) OBA: (a) trigger `chosen_objm>0` na `objm_r6_breach_max_min` (populacja fantomowa §2a), (b) `regret=chosen_objm−rej_objm` predykcyjny (`:1267`/`:1343`), (c) wybór `min(lex_qual)` carry-inclusive. Replay „dowodzi" pozytywnego wpływu na cieniu pierwszego → ten sam predykcyjny błąd dziedziczy LIVE.
- **Asymetria bliźniaków (B):** LIVE `_feas_carry_readmit_pick` kapuje na `_newbag(c)` = czas NOWEGO zlecenia (`per_order_delivery_times[new_oid]`, `:1322`). Shadow jsonl **NIE MA pola newbag** (klucze: `redirect_objm/regret_min/...`, brak newbag). Replay projektuje LIVE jako `would_redirect AND redirect_objm≤40` — kapuje na `redirect_objm` (WORST breach całego worka), NIE na newbag (nowe zlecenie). To **inna bramka** niż kod LIVE → „dowód pozytywnego wpływu" mierzy bramkę, której silnik nie stosuje. Docstring replaya to przyznaje („Cap na NEWBAG nie jest w logu → projekcja KONSERWATYWNA"), ale liczby i tak idą do gate'u GO.
- **Koszt-bok niezmierzony:** `redirect_over_by` (parsowane regexem „over by X" z `feasibility_reason`, `:1242`) = **None dla wszystkich 587 r6_new (64% live)** → `marginal` (zależy od over_by≤5) = tylko 220/914 = 24%. Replay drukuje „KOSZT ≤5min ponad R6" ale dla 64% redirectów over_by jest null, a faktyczny filtr `redirect_objm≤40` dopuszcza do 40 min breach. Twierdzenie o koszcie ≤5min trzyma tylko dla 24% marginalnych.

---

## 5. INWARIANTY-TRIPWIRE (werdykt per inwariant)
| Inwariant | Wynik | Dowód |
|---|---|---|
| delta≥0 (benefit nie-szum) | ❌ | sum sentinelowy 276444, mean 302 ≫ med 9,7; 5,9% regret>60 |
| Pareto `redirect_objm<chosen_fb` (replay zwie „tautologią") | ❌ 2/914 | `redirect_objm==chosen` (lex_qual wielokluczowy: równy R6, różny key2/3 → would_redirect=True a worst NIE maleje). Nie tautologia. |
| trigger realny | ❌ | `chosen_fb>0` 99,7% predykcja vs 14,8% realny (§2a) |
| korzyść obserwowalna | ❌ struktur. | 18,5% celów redirectu kiedykolwiek pojechało (§2c) |
| ten sam zbiór/liczba stopów | n/d | instrument re-rankuje, nie re-solvuje trasy (brak nowych stopów) — OK |
| zero fikcyjnych pickupów | n/d | nie dotyczy (selekcja, nie sekwencja) |

**Dodatkowo (E/H):** monitor post-flip `feas_carry_readmit_postflip.py:81` tripwire `bad_regret = regret≤0` jest **strukturalnie martwy** — `regret=chosen−rej>0` z definicji `would_redirect`, NIGDY nie odpali. Werdykt „CLEAN" jest też pusty gdy flaga OFF (0 redirectów w journalu → trywialnie czysto). Przyrząd nie odróżni „zdrowy" od „wyłączony".

---

## 6. SEMANTYKA „regret = redukcja worst-breach floty" jest NIEPRAWDZIWA (F)
`chosen_forgiven_breach` to MAX breach worka chosen GDYBY wziął nowe zlecenie. Gdy wybaczony breach siedzi na zleceniu JUŻ NIESIONYM (carried), przekierowanie tylko NOWEGO zlecenia do best_rej **nie usuwa carried-breachu chosen** — chosen dalej wiezie tamto zlecenie. Worst-breach floty po redirect = max(chosen_BEZ_nowego, rej_Z_nowym, ...), a `regret=chosen_Z_nowym − rej_Z_nowym` porównuje DWA „z nowym" → nie jest deltą floty. Etykieta „redukcja worst-breach floty" (replay l.18, l.100) jest błędna gdy źródłem breachu jest carried. Łączy się z root K2 (plan_recheck cofacz) i K1 (brak jednego źródła prawdy).

---

## 7. CO TO FLIPUJE
`feas_carry_readmit_replay` jest bramką decyzji `ENABLE_FEAS_CARRY_READMIT` (flip ON 27.06 ~22:18 wg postflip-docstring, potem rollback). **Replay NADAL drukuje `materialność ✅ 54,3%` + `benefit ✅ med=9,7 sum=276444`** — instrument DALEJ kłamie o pozytywnym wpływie; tylko Pareto-gate przeskoczył na ❌ (stąd dziś GO→WAIT). Sesja czytająca nagłówek „benefit ✅" mogłaby re-flipnąć VOID-akcję. A4-rejestr słusznie: „NIE wskrzeszać". Mój oracle daje twardy powód: korzyść = predykcja na fantomowym (85%) triggerze + nieobserwowalnym (81,5%) kontrfakcie; akcja promuje HARD-NO (r6_new = nowe zlecenie ŁAMIE R6) → **dokłada realny breach** zamiast usuwać predykowany-fikcyjny.

---

## 8. POKRYCIE
**Zbadane (coverage_declared):** 3 narzędzia (replay/blind_review/postflip) — pełny odczyt; bliźniacza para silnika `_feas_carry_blind_shadow`+`_feas_carry_readmit_pick`+wpięcie LIVE `:6255-6301`; `objm_lexr6.objm/lex_qual`; `feas_carry_blind_shadow.jsonl` (1682 rek, FRESH 15:16); JOIN z `decision_outcomes.jsonl` (443 zlec., FRESH 15:58); flags.json (3 flagi); replay odpalony 2× + oracle skrypt 2× (determinizm potwierdzony).

**NIE zbadane (coverage_gaps):**
1. **Journal `FEAS_CARRY_READMIT` LIVE linii nie ściągałem** (journalctl; flaga OFF od rollbacku → ~0; A4 cytuje „realny readmit 4/2816=0,14%" z poprzedniej sesji — nie re-derywowane). Powód: read-only + determinizm; figura już w rejestrze.
2. **Button-truth ≠ ground-truth** — `decision_outcomes.r6_actual` z `delivered_at` przyciskowego (0/377 auto_geofence GT). % bezwzględne ±~3min proxy; kierunek odporny.
3. **Kontrfakt rejected-candidate STRUKTURALNIE nieobserwowalny** (81,5% nigdy nie przypisani) — nie domknie go ŻADEN join; to granica metody, nie luka pomiaru.
4. **Sentinel ~10000 dokładny źródłosłów** — `grep 10000` po objm_lexr6/scoring/feasibility pusty (wartość złożona, nie literał). Sentinel POTWIERDZONY empirycznie (49 rek >100, niefizyczne dla R6), origin = osobny temat (K5).
5. **Bliźniak BEST_EFFORT_OBJM_R6_KEY (selekcja-path, ON)** — A3 §4 wiąże go z FEAS_CARRY_READMIT „ruszać RAZEM" (re-admit carry-inclusive). Tu NIE oracle'owany osobno (zakres C03=feas_carry); handoff: ta sama rodzina objm_r6_breach predykcyjnego → sprawdzić czy ON-path też kalibruje na predykcji bez outcome-joina.

---

## 9. WERDYKT
**VOID** (3/3 przyrządy). Instrument liczy benefit/regret/true_delta z **predykcji `objm_r6_breach_max_min`, bez ŻADNEGO joina do `decision_outcomes`/`delivered_at`** (który istnieje i jest świeży). Druga metoda (join button-truth) demaskuje: trigger fantomowy 85%, korzyść nieobserwowalny kontrfakt 81,5%, „benefit-sum" sentinelowy. Oba bliźniaki dzielą błąd predykcyjny + asymetria B (shadow bez newbag → replay kapuje na złej wielkości vs LIVE). Flaga już OFF (akcja martwa), ale **przyrząd dalej emituje mylące „benefit ✅"** → ryzyko re-flipu; instrument NIE-naprawiony (still_open). Caveat: button-truth proxy-certyfikowany.
