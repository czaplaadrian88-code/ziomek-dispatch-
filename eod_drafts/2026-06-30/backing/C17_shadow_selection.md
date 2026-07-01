# C17 — SHADOW SELECTION/FEASIBILITY — LANE C RUNTIME-ORACLE

**Agent:** C17-shadow-selection · **Lane:** C (runtime-oracle, C9/C11) · **Tryb:** READ-ONLY · **Data:** 2026-06-30 ~18:00 UTC · **Sesja:** tmux 2
**Zakres zlecony:** `a2_selection_shadow.jsonl` + `c2_shadow_log.jsonl` (10.8MB) + `c5_shadow_log.jsonl` + `pending_global_resweep`. Pytania: (1) czy logi parytetu live==canon wierne; (2) czy c2/c5 czytane przez jakikolwiek werdykt czy martwe (K/E); (3) a2 live-pick vs stale-bucket (equal-treatment); werdykt validated/void/untested.
**Metoda:** snapshot live jsonl → scratchpad → recompute prawdy DRUGĄ, niezależną metodą (recompute reguły z pól serializowanych, Counter(new_cid) zamiast g_maxpile, grep prod-callerów, code-diff bucket live↔shadow, slice ledgera). Każdy oracle ×2 (determinizm, md5 stabilny). Narzędzia NIE odpalane (piszą do dispatch_state — DoD). Skrypty: `scratchpad/oracle_resweep.py`, `oracle_c2_c5.py`, `oracle_a2_bucket.py`.

---

## 0. TL;DR — 4 PRZYRZĄDY, 4 RÓŻNE STANY

| Przyrząd | mtime | Producent | Konsument-werdykt | ORACLE verdykt | Klasa |
|---|---|---|---|---|---|
| **pending_global_resweep.jsonl** | 17:55 FRESH | `pending_global_resweep.py` (timer 1min LIVE) | `pending_global_resweep_review.service` (SPENT 26.06, nie-recurring) | **VALIDATED** (shadow wierny) — live-path UNTESTED | (faithful) + H |
| **c2_shadow_log.jsonl** | 17:52 FRESH | `feasibility_v2.py:1290` hot-path PROD | `analyze_shadow_logs.py` — **BRAK timera** | **VALIDATED** (kontrfaktyk wierny) — **konsument MARTWY** | K |
| **a2_selection_shadow.jsonl** | 04:30 (daily) | `a2_selection_shadow.py` (retro-learning 04:30 LIVE) | `weekly_a2_digest.py` — **BRAK timera** | **VOID** dla slice equal-treatment (bucket STALE) + konsument martwy | B+G+E+K |
| **c5_shadow_log.jsonl** | 13:17 (POZÓR) | `wave_scoring.py` **DEAD** (Z-22) → 0 prod-callerów | `analyze_shadow_logs.py` — BRAK timera | **VOID** — 100% test-pollution, potrójnie martwy | E+K+M |

**Najważniejsze:** „świeży" mtime ≠ żywe dane. c5=13:17 to artefakt pytest (this audit baseline), nie decyzja. a2 mierzy ZAMROŻONY model selekcji sprzed equal-treatment. c2 wierny ale nikt nie czyta. Tylko resweep ma żywego konsumenta-werdykt (i ten ran-once).

---

## 1. pending_global_resweep — VALIDATED (instrument wierny), live UNTESTED

**Co mierzy:** globalny sekwencyjny de-pile WISZĄCYCH propozycji (`global_allocate` `pending_global_resweep.py:145`, claim `_tentative_assign:124`) vs to co Ziomek proponował per-order (greedy). Parytet **live(proposed)==canon(allocation)**: `would_repropose` (`:342`) = gdzie kanon globalny ≠ propozycja live.

**Oracle — recompute DRUGĄ metodą** (`oracle_resweep.py`, snapshot 3073 wierszy / 1880 sweepów):
- **would_repropose** zrekonstruowane z surowych pól (`proposed_cid`/`new_cid`/`proposed_now_score`/`new_score`/`g_spread_improved`, margin=15) → **0/3073 MISMATCH** vs zapisane. Instrument NIE kłamie o swojej decyzji.
- **g_maxpile_after** zrekonstruowane z `Counter(new_cid)` per sweep (NIE z pola tool'a) → **0/1880 MISMATCH**. Metryka pile-on wierna.
- reason↔would spójne: bez_zmian/zmiana_marginalna→False (2098+142); rozjazd_kierunkow/proponowany_wypadl/lepszy_kurier→True (321+304+208). 0 sprzeczności.
- **Inwariant geometrii (dowód de-pile dziedziczy ślepotę):** `new_deliv_spread_km>8km` po global_allocate = **710/2020 (35.1%)**, max **24.3 km**. Faithfully logged → potwierdza seed P0-A „de-pile pod scarcity dziedziczy ślepotę geometryczną" DRUGĄ metodą (count na świeżym oknie).
- `pool_feasible==0` = 615/3073 (20.0% full-day; seed 43-45% to peak-ticki — spójne).
- determinizm: run2 md5 `cd34179d…` stabilny.

**Werdykt: VALIDATED** dla SHADOW (pomiar wierny, parytet live==canon wierny). **CAVEAT lifecycle (klasa H):** ścieżka LIVE niezaimplementowana (`PENDING_RESWEEP_LIVE=false`, `:420-421` czysty warning no-op), a `pending_global_resweep_review.service` ma unit ale ODPALIŁ RAZ (26.06 SPENT, NIE recurring) → werdykt GO/NO-GO jest STARY snapshot. Co flipuje: decyzja A/B (re-ranker vs fix-u-źródła P0-B). Dane wierne → decyzja na nich bezpieczna; ale „warto live" dowodzi się na nie-świeżym review. `proxy-certified` (new_deliv_spread_km = metryka assess_order, nie niezależny OSRM-grunt — patrz §5 luka).

**Cross-ref:** seed `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md` zaklasyfikował resweep „untested" (bo live niewpięty) — UPGRADE: **shadow-faithfulness = VALIDATED** (dowiedzione), tylko live untested. To NIE „kłamiący przyrząd" — przeciwnie, jeden z nielicznych wiernych.

---

## 2. c2_shadow_log — kontrfaktyk WIERNY, ale konsument MARTWY (klasa K)

**Co mierzy:** kontrfaktyk „gdyby C2 per-order 35min hard-gate był ON" — pisany w PROD hot-path `feasibility_v2.py:1290` (`if ENABLE_C2_SHADOW_LOG and not c2_passes`), `ENABLE_C2_SHADOW_LOG=True` (common.py:902). Pole `new_verdict_if_c2_enabled`, `c2_would_reject`, `violations`, `max_elapsed_min` + serializowany `per_order_delivery_times`/`sequence`.

**Oracle — recompute reguły DRUGĄ metodą** (`oracle_c2_c5.py`, 20280 rekordów):
- Zrekonstruowano `check_per_order_35min_rule` (próg `C2_PER_ORDER_THRESHOLD_MIN=35.0`, `feasibility_v2.py:289-318`) WPROST z `per_order_delivery_times` każdego rekordu → porównano do zapisanych pól:
  - `c2_would_reject` stored vs recompute: **0 mismatch**
  - `max_elapsed_min`: **0 mismatch**
  - `violations` count: **0 mismatch**
  - `new_verdict_if_c2_enabled != "NO"`: **0** (zawsze NO, zgodnie z gałęzią `not c2_passes`)
- **Kompozycja rejectów:** wszystkie 20280 = REALNE naruszenie per-order >35min (`real_viol=20280`), **0 fail-closed** (per_order_delivery_times None). Czyli c2 mierzy autentyczny „ile MAYBE poleciałoby NO przez 1-zlecenie >35min".
- determinizm: run2 md5 `a7ce869…` stabilny.

**Werdykt: VALIDATED** (kontrfaktyk wierny, `proxy-certified` — per_order_delivery_times = projekcja planu, button/model-truth nie fizyczny). **ALE czy czytany przez werdykt? NIE.** Jedyny reader = `tools/analyze_shadow_logs.py` (`:31`) → produkuje weekly summary do `/tmp`, **uruchamiany przez ŻADEN timer/unit** (`grep -rln analyze_shadow_logs /etc/systemd/system` = PUSTE; jedyny import = `tests/`). → **konsument MARTWY (klasa K).** 20280 wiernych rekordów kontrfaktyku flipu `USE_PER_ORDER_GATE` (C3 `DEPRECATE_LEGACY_HARD_GATES`) rośnie od 2026-05-10 (11MB) bez konsumenta i bez rotacji. Dane gotowe na decyzję, decyzja-proces nie istnieje.

---

## 3. a2_selection_shadow — STALE BUCKET vs equal-treatment → VOID dla slice positionless

**Co mierzy:** OFFLINE jak soft-score niezawodności (A2) zmieniłby SELEKCJĘ na przeszłych decyzjach (`ENABLE_A2_RELIABILITY_SOFT_SCORE=true`, LIVE). Producent: `a2_selection_shadow.py` w `dispatch-retro-learning.service` (timer 04:30 daily, ostatni 04:30 dziś — DZIAŁA, cadence dzienna, NIE stale-broken). Rekord = dzienny agregat `by_coeff` (sweep COEFF 20/40/60/100).

**ROZJAZD live↔shadow — bucket pozycji (code-diff, dowód twardy):**
- **LIVE** `_selection_bucket` (`dispatch_pipeline.py:2459-2460`): `if _equal_bucket_on() and ps in ("no_gps","pre_shift"): return 0` (equal-treatment, Adrian 24.06 HARD). `_equal_bucket_on` def `:2399`, flaga `ENABLE_EQUAL_TREATMENT_BUCKET=true`.
- **SHADOW** `_pos_bucket` (`a2_selection_shadow.py:182`): `return 2 if (cand.get("pos_source") in _POS_BUCKET2) else 0`, gdzie `_POS_BUCKET2={no_gps,pre_shift,none}`. **BEZ `_equal_bucket_on`** — docstring `:179` wprost „2 = …no_gps/pre_shift/none — twardy demote". Gate (a) `_key_bucket(c) <= best_bucket` (`:281`) BLOKUJE kandydata z bucketem 2.
- → a2-shadow demotuje no_gps/pre_shift do 2 BEZWARUNKOWO = **zamrożony model sprzed equal-treatment**. Live ich NIE demotuje.

**Oracle — kwantyfikacja DRUGĄ metodą z master-ledgera** (`oracle_a2_bucket.py`, slice ostatnie ~120k linii `shadow_decisions.jsonl`, 894 PROPOSE):
- 681 decyzji z ≥1 wykonalnym alt (MAYBE, non-best_effort).
- **best == no_gps/pre_shift (live equal-treatment WYGRYWA pozycyjnie-ślepym): 219/681 = 32.2%** — live realnie wskazuje positionless jako BEST w ⅓ przypadków (dowód equal-treatment AKTYWNE i materialne; ostatni PROPOSE ledgera ma `best.pos_source=no_gps`).
- **POPULACJA ROZJAZDU** (informed best + ≥1 wykonalny no_gps/pre_shift alt — gdzie a2-shadow gate :281 BLOKUJE swap, a live by POZWOLIŁ): **121/681 = 17.8%**. Przykłady: oid 483665 (best last_assigned_pickup, 3× pre_shift alt blokowane), 483677, 483685.
- determinizm: run2 md5 `b11ef8b…` stabilny.

**Skutek na werdykt:** a2 mierzy WZGLĘDEM realnego best (REFINEMENT 1) z gate „nie-gorszy koszyk" — ale ten koszyk demotuje positionless do 2. → a2-shadow **systematycznie pomija (false-negative) swapy gdzie niezawodny no_gps/pre_shift przebiłby mniej-niezawodnego informed best** (~18% decyzji-z-alt). `by_coeff[100]` raportuje changed_rate=12.7%, better:worse=147:119 (~1.24:1) — **zaniżone dla slice equal-treatment**, biased-pesymistycznie. Kierunek błędu jednostronny (blokuje WYGRANE positionless, nie odwrotnie).

**Werdykt: VOID** dla wymiaru equal-treatment / no_gps-pre_shift (instrument mierzy ZAMROŻONY model selekcji ≠ żywy silnik; nie da się certyfikować flipu/utrzymania COEFF dla tej populacji). Dla slice informed-only directionally OK. `proxy` (breach_rate = historyczny profil, nie predykcja per-zlecenie — limit jawny w docstring). **Konsument też martwy:** `weekly_a2_digest.py` (`:25` czyta a2_selection_shadow.jsonl) — **BRAK timera/unit** (grep PUSTE) → trend dzienny pisany ale nieczytany (klasa K).

**Dedup:** to NOWA instancja rootu K1 „position bucket out-of-engine twin" (A6 grupa 3b) — bliźniak do `reassignment_forward_shadow._SYNTH_POS` i `best_effort_fastest_pickup_shadow` (seed void). NIE liczyć jako 6. chaos — zwija się do K1 (selekcja/bucket pozycji w przyrządzie out-of-engine). Naprawa equal-treatment MUSI objąć a2-shadow `_pos_bucket` RAZEM z silnikiem (inaczej trend dalej kłamie).

---

## 4. c5_shadow_log — 100% TEST-POLLUTION, potrójnie martwy → VOID

**Sprzeczność do rozstrzygnięcia:** `wave_scoring.py:4-23` (Z-22 audyt 2026-06-10) deklaruje moduł DEAD: `compute_wave_adjustment` nie wołany przez ŻADEN prod-moduł → `_emit_c5_shadow_diff` (`:388`, w `compute_wave_adjustment:320`) „nigdy nie odpala". ALE plik ma mtime **13:17 dziś** + 1388 linii do `2026-06-30T13:17:23`.

**Oracle — rozstrzygnięcie DRUGĄ metodą** (`oracle_c2_c5.py` + grep):
- **0 prod-callerów:** `grep -rn compute_wave_adjustment` poza testami/def = TYLKO docstring. Potwierdza DEAD producent.
- **Dystrybucja wartości (dowód fixture-only):** 1388 rekordów → DOKŁADNIE 4 distinct `total_adjustment`: **{5.0, 7.5, 8.0, 15.5}, każda ×347** (347×4=1388). `context.order_id=None` dla WSZYSTKICH (n_distinct=1). Te 4 wartości = asercje testów: 5.0 (`test_…flag_on_sums_features:271`), 7.5 (`…peak_multiplier:296`), 15.5 (`…all_features_combined:327`), 8.0 (4. fixture).
- **06-30 = 6 identycznych burstów po 4** o 08:55/09:25/09:39/09:42/09:44/**13:17** = 6 przebiegów pytest (13:17 = baseline tego audytu, recon §F). Zero wpisów produkcyjnych.
- determinizm: run2 md5 stabilny.

**ROOT pollution (klasa M — test→prod state bleed):** 3 testy `test_wave_scoring.py:253/277/302` wołają `compute_wave_adjustment(flag ON)` BEZ monkeypatch `C5_SHADOW_LOG_PATH` (hardcoded `wave_scoring.py:82` na `dispatch_state/`). Tylko `:333 test_…shadow_log_emits` patchuje ścieżkę (`:341`). → każdy `pytest tests/test_wave_scoring.py` na serwerze dopisuje 4 fixture do PRODUKCYJNEGO `dispatch_state/c5_shadow_log.jsonl`. mtime „świeży" = artefakt CI/baseline, NIE decyzja silnika.

**Werdykt: VOID** (ground-truth — sama treść pliku JEST dowodem: 100% fixture, 0 danych prod). Potrójnie martwy: producent DEAD (wave_scoring) + plik = test-residue (E, kłamie świeżością) + konsument DEAD (analyze_shadow_logs bez timera). Co flipuje: reaktywacja `ENABLE_WAVE_SCORING`/C5 czytałaby ten plik jako „shadow evidence" → przeczytałaby fixtury jako sygnał. Landmine.

---

## 5. ODPOWIEDZI NA PYTANIA ZLECENIA

1. **Czy logi parytetu live==canon wierne?** TAK dla resweep (would_repropose 0/3073 + maxpile 0/1880 mismatch — §1). c2 kontrfaktyk wierny (0/20280 — §2). a2 „parytet" bucketa live↔shadow **NIE-wierny** (live=0 vs shadow=2 dla positionless, §3).
2. **c2/c5 czytane przez werdykt czy martwe?** OBA czytane WYŁĄCZNIE przez `analyze_shadow_logs.py`, który **nie biega na żadnym timerze** → konsument MARTWY (K). c5 dodatkowo ma martwego producenta (wave_scoring) + jest test-pollution (E+M). c2 producent ŻYWY+wierny, tylko konsument martwy.
3. **a2 live-pick vs stale-bucket:** stale-bucket POTWIERDZONY (`_pos_bucket:182`=2 vs live `_selection_bucket:2459`=0 dla no_gps/pre_shift). Rozjazd dotyka **17.8%** decyzji-z-alt; live wybiera positionless jako best w **32.2%**. a2 zaniża zmiany dla tej populacji → VOID dla slice equal-treatment.

---

## 6. TABELA POKRYCIA

| Obiekt | Sprawdzony | Metoda 2-ga | Werdykt |
|---|---|---|---|
| pending_global_resweep.jsonl (3073w/1880sw) | ✅ | recompute would z pól + Counter(new_cid) maxpile + spread>8 count | VALIDATED (shadow), live UNTESTED |
| `global_allocate`/`run_once`/`_tentative_assign` (pending_global_resweep.py:145/253/124/342) | ✅ czytane | inspekcja logiki would/reason/pile | wierne |
| c2_shadow_log.jsonl (20280) | ✅ | recompute check_per_order_35min_rule z per_order_delivery_times | VALIDATED, konsument K |
| `_emit_c2_shadow_diff_event` + caller (feasibility_v2.py:369/1290) | ✅ | grep caller hot-path | prod-żywy |
| a2_selection_shadow.jsonl (34 daily) + `_pos_bucket`/`_key_bucket`/gate (:182/191/281) | ✅ | code-diff vs live _selection_bucket + ledger slice 894 PROPOSE | VOID equal-treatment slice |
| live `_selection_bucket`/`_equal_bucket_on` (dispatch_pipeline.py:2451/2399) | ✅ czytane | code | bucket=0 positionless |
| c5_shadow_log.jsonl (1388) + wave_scoring DEAD (:4/320/388) | ✅ | grep 0 prod-caller + distinct-value fixture proof | VOID (test-pollution) |
| test_wave_scoring.py:253/277/302/333 (pollution root) | ✅ czytane | code (brak monkeypatch path) | M test→prod bleed |
| Reader wiring (analyze_shadow_logs / weekly_a2_digest / resweep_review) | ✅ | grep /etc/systemd + list-timers | 2 martwe, 1 SPENT |
| Master ledger PROPOSE shape + slice | ✅ | json keys + 894-record count | best.pos_source widoczny |

**LUKI POKRYCIA (jawne):**
1. **OSRM ground-truth NIE liczony** dla `new_deliv_spread_km` (resweep) ani `per_order_delivery_times` (c2) — recompute reguły z pól = `proxy-certified` (button/metryka assess_order), NIE niezależny OSRM-grunt. Pełny grunt wymagałby re-uruchomienia assess_order (pisze) — poza DoD. A4 nota: OSRM route==table na peak CERTYFIKOWANY CZYSTY (n=2644) — spread bazuje na tym samym OSRM, więc proxy wiarygodne, ale formalnie nie-grunt tu.
2. **a2 kwantyfikacja na SLICE** (894 PROPOSE z ostatnich 120k linii ledgera), nie pełne 47MB; dzienny rekord a2 czyta pełny, ja próbkowałem recent peak. Kierunek (jednostronny false-negative) niezależny od próbki.
3. **NIE odpalałem** a2_selection_shadow.py ani pending_global_resweep.py z poprawionym bucketem (piszą do dispatch_state — DoD) — użyłem niezależnego recompute zamiast re-run tool'a.
4. **c5 wartość 8.0** — 4. fixture, dokładny test nie zpinowany (immaterialne: order_id=None + stała → dowiedziony fixture).
5. **reassignment_global_select** (importuje `global_allocate`) NIE oracle'owany osobno (poza 4 nazwanymi; A4: DZIAŁA, 5/5 pile-on rozbite).
6. **Cross-repo / Mailek / Papu** — poza zakresem (STOP na dyspozytorni).

---

## 7. SMELLE MIMOCHODEM (zasila Fazę B/E/K)
- **K (martwy konsument ×2):** `analyze_shadow_logs.py` (czyta c2+c5+drive_calib+carry_chain) i `weekly_a2_digest.py` — OBA bez timera. Cała rodzina shadow-logów Fazy 7 (c2/c5/drive_min) pisana, nikt nie czyta automatycznie. Backlog: albo wpiąć review-timer, albo oznaczyć przyrząd jako „ad-hoc only" (nie udawać żywego trendu).
- **M (test→prod path bleed):** hardcoded `C5_SHADOW_LOG_PATH`/`C2_SHADOW_LOG_PATH` na `dispatch_state/` + testy bez monkeypatch → pytest zanieczyszcza stan produkcyjny. Wzorzec do sprawdzenia w innych shadow-toolach (każdy hardcoded path + test wołający emit).
- **E (freshness kłamie):** c5 mtime „13:17 FRESH" w A4/recon = pytest-artefakt; każdy ślepy `ls -la` myli go z żywym. a2 04:30 = daily OK ale „stale" w A4 myli z broken. Rekomendacja: instrument-rejestr powinien rozróżniać „freshness z mtime" od „freshness z wewnętrznego ts ostatniego REKORDU PRODUKCYJNEGO".
- **B (twin shadow↔live):** a2 `_pos_bucket` = zamrożony bliźniak `_selection_bucket` — ten sam wzorzec co frozen `_objm_lexr6_shadow._lex_qual` (A6 grupa 1). Out-of-engine kopie reguły selekcji się nie aktualizują przy zmianie kanonu.
- **H (verdykt nie-recurring):** `pending_global_resweep_review` ran-once 26.06 (SPENT); GO/NO-GO na nim = stary snapshot mimo świeżych danych co 1 min. Brak recurring review = werdykt dryfuje od danych.
