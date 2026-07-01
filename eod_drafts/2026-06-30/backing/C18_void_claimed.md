# C18 — VOID-CLAIMED INSTRUMENTS — RUNTIME-ORACLE (lane C, C9/C11)

**Agent:** C18-void-claimed · **Tryb:** READ-ONLY (zero edycji/restartów/flipów/--notify) · **Data:** 2026-06-30 ~18:05 UTC
**Mandat:** 3 przyrządy DEKLAROWANE VOID przez `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md` (sekcja „WIĘCEJ BUGÓW", l.66-71). Dla każdego: grep serialized-key na ŚWIEŻYM ledgerze + drugą-metodą-prawda + werdykt validated/void/untested + co flipuje.
**Ledger (master):** `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` — 54.7 MB, **938 rekordów, mtime 18:02 FRESH**, ts span `[2026-06-27T07:53 .. 2026-06-30T18:04]`. (NIE w `dispatch_state/` — patrz A4 §1.)
**Metoda determinizmu:** każdy oracle ≥2 odpalenia; tool-run czyta ledger, ZERO zapisu do dispatch_state, ZERO --notify/--live. Pure-funkcje testowane z ręcznie policzoną prawdą (second method). Skrypty: `scratchpad/oracle_{psor,psor_flip,fastpick,seqverdict}.py`.

---

## TL;DR — 3 werdykty (2 void-claimy OBALONE, 1 untested POTWIERDZONY + 5 realnych defektów)

| # | Przyrząd | Audyt mówił | **Oracle werdykt** | Dlaczego (druga metoda) |
|---|---|---|---|---|
| 1 | `post_shift_overrun_forward_replay` | void (klucz nigdy nieserializowany, grep=0/282; werdykt niemożliwy) | **🟢 VALIDATED — void OBALONY** | Klucz serializowany **1699×/438 rek.** w świeżym ledgerze; with_pen=**55 ≥ 20** gate; tool biega czysto (GO-neutralny), deterministyczny; **crafted-flip OFF→B ON→A WYKRYTY** → mechanizm żywy. „0 flipów" = PRAWDA, nie false-neutral. Audyt grepał STALE okno (pre-restart 06-29). |
| 2 | `best_effort_fastest_pickup_shadow` | void (stale hardcoded bucket skaża no_gps/pre_shift) | **🔴 void — ale z INNEGO powodu (audyt-reason OBALONY)** | „stale hardcoded bucket" OBALONY (unify→`_selection_bucket` a8cdb95 29.06, equal-treatment-aware, bucket TERTIARY bije 2/81 remisów ETA). **REALNY defekt: `live_pos_source`/`shadow_pos_source` = None w 81/81** — `getattr(best,"pos_source")` czyta NIEISTNIEJĄCY atrybut (pos_source żyje w `.metrics`). Blind-check „fikcyjny ETA?" MARTWY → flip-walidacja nie certyfikuje bezpieczeństwa. Nagłówek `would_differ` (54/81, 52/54 osią pickup-ETA) JEST zdrowy. |
| 3 | `sequential_replay._determine_verdict` | untested (pile_ratio+Gini fleet-gate ETAP-5) | **🟡 UNTESTED — POTWIERDZONY** | 0 testów (grep tests/ pusty); 0 żywych wołaczy run_diff (tylko komentarze + niezwiązane `_run_diff` w innych testach); manual-CLI only; brak zapisanych raportów. Pure-math ZDROWA dla udokumentowanych celów (gini/pile_ratio/bramki = ręczna prawda OK). **Latentna INWERSJA** dla celu higher-better (`couriers_used` → NO-GO gdy realnie poprawia). |

---

## 1. `post_shift_overrun_forward_replay` → 🟢 VALIDATED (void OBALONY)

**Plik:** `tools/post_shift_overrun_forward_replay.py` (139 LOC) · **Konsumuje:** `_best_effort_objm_pick` (dispatch_pipeline.py:633) · **Karmione przez:** ledger pola `post_shift_overrun_penalty`/`_min` per kandydat.

### Audyt-claim (l.68): „czyta klucz nigdy nieserializowany (grep=0/282); werdykt GO/NO-GO niemożliwy."

### Oracle — grep serialized-key (ŚWIEŻY ledger)
```
grep -o post_shift_overrun_penalty  → 1699   (NIE 0)
grep -o post_shift_overrun_min      → 1699
records z polem                     → 438 / 938
```
**Klucz JEST serializowany.** Twin A+B przez `_propagate_prefixed_metrics` (shadow_dispatcher.py:266) z prefiksem `"post_shift_overrun_"` (shadow_dispatcher.py:**258**, dodany fixem **F2 2026-06-28**). Źródło metryki: `dispatch_pipeline.py:5574-5575` (kandydat-metrics dict, liczone ZAWSZE l.5187-5197).

### Druga metoda — niezależny recompute bramki + temporalna przyczyna „0"
`scratchpad/oracle_psor.py` (parse JSON, mirror selekcji best_effort tool'a):
```
best_effort records   : 81
  with penalty field  : 55   (TOOL GATE: >=20 dla non-NO-GO)  ✅
  pen-field in BEST    : 55      in ALT: 246
  non-null pen total   : 301 | >0 pen: 9 | >0 overrun_min: 11
```
**Dlaczego audyt widział 0** (temporalny breakdown, oracle_psor):
```
2026-06-27: with_field=  0  without=  9   ← pre-F2-fix
2026-06-28: with_field=  0  without= 17   ← F2 commit 06-28 ale BEZ restartu
2026-06-29: with_field= 32  without=  0   ← restart ~09:25 → F2 LIVE
2026-06-30: with_field= 23  without=  0
```
26 rekordów „bez pola" = dokładnie 9(06-27)+17(06-28) = okno SPRZED restartu. Audyt grepał stary ogon (lub przed restartem 06-29) → 0. **Komentarz fixu sam to opisuje** (shadow_dispatcher.py:253: „replay widzial 0 -> ETAP-5 flipa ... nie dalo sie policzyc") — void był REALNY przed 06-28, naprawiony PRZED audytem.

### Druga metoda — tool biega + werdykt (read-only, ≥2×, deterministyczny)
```
FORWARD-REPLAY post-shift overrun  (od 2026-06-24T20:52)
  best_effort decyzji: 81  (z polem penalty: 55, stare bez pola: 26)   ← ZGODNE z moim recompute
  FLIPY flaga ON: 0   post-shift->in-shift: 0   inne: 0
WERDYKT: GO-neutralny (0 flipów — kara nie szkodzi; efekt rzadki)
```
run1==run2 (deterministyczny).

### Druga metoda — czy „0 flipów" to PRAWDA czy false-neutral? (crafted-flip, decydujący)
Ryzyko: gdyby `_best_effort_objm_pick` ignorował flagę, „0 flipów" byłby fikcją (instrument-void). Test mechanizmu (`scratchpad/oracle_psor_flip.py`, ≥2×):
```
CRAFTED FLIP TEST:  OFF->B  ON->A   FLIP DETECTED (mechanism LIVE)
NEG CONTROL (both pen=0): OFF->B ON->B (no flip)
```
Mechanizm: `_best_effort_objm_pick` l.665/667 = `min(..., key=_OL.lex_qual)`; `objm_lexr6.lex_qual:44-46` PREPENDuje `post_shift_overrun_penalty` gdy `ENABLE_POST_SHIFT_OVERRUN_PENALTY`. Toggle dociera bo: klucz **NIEOBECNY w flags.json** → `decision_flag` (common.py:348) fallback `globals().get(name)` = atrybut modułu, który replay ustawia (docstring decision_flag potwierdza wprost). **→ „0 flipów" na realnych danych = PRAWDA** (9 niezerowych kar, ale żaden ukarany nie był marginalnym zwycięzcą).

### Werdykt: **VALIDATED.** Werdykt GO/NO-GO MOŻLIWY i policzony (GO-neutralny). **co flipuje:** bramkuje ETAP-5 flip `ENABLE_POST_SHIFT_OVERRUN_PENALTY` (shadow→live). proxy-certified (best_effort z ledgera = prawda-przyciskowa, ale verdict-feasibility i mechanizm = ground-truth na pure-funkcji).

---

## 2. `best_effort_fastest_pickup_shadow` → 🔴 void (audyt-reason OBALONY, realny defekt = MARTWE pos_source)

**Producent (LIVE):** `dispatch_pipeline.py:6797-6825` (gate `ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW`=true) · **Klucz:** `_best_effort_fastest_pickup_key:595` · **Serializer:** prefix `"best_effort_fastest_"` (shadow_dispatcher.py:241).

### Audyt-claim (l.66): „pisze dane flip-walidacji ze stale hardcoded bucketem (skaża no_gps/pre_shift)."

### Część A — „stale hardcoded bucket" → OBALONY (3 niezależne dowody)
1. **Źródło-unify:** `_best_effort_fastest_pickup_key:618` używa `bucket = _selection_bucket(c)` (NIE inline). Git: `a8cdb95 Sprint3 NO-GPS-EQUAL: unifikacja shadow-bucket na _selection_bucket + test-strażnik` (**2026-06-29 11:24 UTC**). Komentarz l.613-617 wprost: „Było inline-kopią informed0/blind|pre_shift2 sprzed equal-treatment — unifikujemy by ... nie wskrzesił dyskryminacji".
2. **`_selection_bucket:2451` JEST equal-treatment-aware:** l.2459 `if _equal_bucket_on() and ps in ("no_gps","pre_shift"): return 0` — NIE karze. Flagi efektywne (flags.json): `ENABLE_EQUAL_TREATMENT_BUCKET=ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY=ENABLE_NO_GPS_EQUAL_TREATMENT=True`.
3. **Bucket jest TERTIARY** (3-ci element krotki `(pu, dv, bucket)`): bije TYLKO przy remisie pickup-ETA+delivery-ETA. Oracle `scratchpad/oracle_fastpick.py`: would_differ ETA-remis (earlier_min==0) = **2/81**; 52/54 would_differ rozstrzygnięte PRYMARNĄ osią pickup-ETA. Skażenie bucketem = praktycznie zero.

### Część B — REALNY defekt (NOWY, oracle-wykryty): pole `*_pos_source` MARTWE w 81/81
Producent l.6812/6815:
```python
"live_pos_source":   getattr(best, "pos_source", None),
"shadow_pos_source": getattr(_fp_best, "pos_source", None),  # blind-check: fikcyjny ETA?
```
`pos_source` NIE jest bezpośrednim atrybutem kandydata — żyje w `c.metrics["pos_source"]` (dowód: l.479/492/2458/3259/6655 wszystkie czytają `c.metrics.get("pos_source")`; serializer l.293 `m.get("pos_source")`). `getattr(best,"pos_source",None)` → **zawsze None (default)**. Oracle na ledgerze:
```
total fastest_pickup_shadow dicts: 81
dicts with ANY non-null pos_source (live|shadow): 0 / 81  → POLE MARTWE
would_differ (live_pos -> shadow_pos) pairs: None -> None : 54/54
```
**Skutek:** zadeklarowany „blind-check: fikcyjny ETA?" jest STRUKTURALNIE MARTWY. Recenzent flipu `ENABLE_BEST_EFFORT_FASTEST_PICKUP` patrzący na shadow NIE WIDZI, czy „szybszy odbiór" cienia opiera się na FIKCYJNEJ pozycji blind (BIALYSTOK_CENTER) — to DOKŁADNIE wymiar bezpieczeństwa, którego shadow miał pilnować. Poprawny dostęp = `best.metrics.get("pos_source")` (mirror l.6655).

### Część C — nagłówek JEST zdrowy
`would_differ` (54/81=67%) + `shadow_pickup_earlier_min` liczone POPRAWNIE z `plan.pickup_at` (real). Headline flip-signal („fastest-pickup zmieniłby wiele picków") = sound. Defekt dotyczy WARSTWY BEZPIECZEŃSTWA, nie headline'u.

### Caveat temporalny
Rekordy 06-27/06-28 (~27/81) SPRZED unify a8cdb95 → pisane STARYM inline-bucketem. Ale tertiary + 2/81 remisów ⇒ wpływ pomijalny. Mieszane okno walidacji = mniejszy smell (H świeżość).

### Werdykt: **void** (dla celu flip-bezpieczeństwa). Audyt-NAZWA mechanizmu („stale hardcoded bucket") OBALONA; instrument NIE jest jednak „validated" — jego własny blind-check jest kłamstwem (None×81). Flip nie może iść bezpiecznie z tych danych bez zewnętrznego join pos_source. **co flipuje:** bramkuje flip selekcji „najszybszy odbiór" live. proxy (button-truth + martwe pole bezpieczeństwa).

---

## 3. `sequential_replay._determine_verdict` → 🟡 UNTESTED (POTWIERDZONY)

**Plik:** `tools/sequential_replay.py:742-778` (`_determine_verdict`) + `_fleet_metrics:677` (pile_ratio/gini) + `_gini:663` + `run_diff:781` · offline harness `--diff-base/--diff-cand`.

### Audyt-claim (l.71): „pile_ratio>pile_tol + Gini fleet-gate, którego używa ETAP-5 → untested."

### Część A — „untested" strukturalnie POTWIERDZONY
- **0 testów:** `grep -rln "sequential_replay|_determine_verdict|_fleet_metrics|pile_ratio" tests/` = PUSTE. (`_run_diff` w test_v320_packs_ghost/test_assignment_lag_fix to helpery panel_watcher — INNY plik, nie ten run_diff.)
- **0 żywych wołaczy:** `run_diff`/`_determine_verdict` wołane TYLKO przez CLI `main()` (`--diff-base --diff-cand`). Pozostałe trafienia = komentarze (panel_watcher:333, daily_rule_report:21, calib_b3c2d2:222). „ETAP-5 używa" = ręczne odpalenie operatora, nie wpięcie.
- **0 zapisanych raportów** replay JSON w dispatch_state/logs/eod_drafts (find pusty).

### Część B — pure-math ZDROWA dla udokumentowanych celów (oracle, ręczna prawda)
`scratchpad/oracle_seqverdict.py` (import PYTHONHASHSEED=0 → bez re-exec; pure-funkcje):
```
GINI [4,0,0,0]->0.75 [2,2,2,2]->0.0 [3,1]->0.25 [5]->0.0 []->0.0   ALL OK (ręczna prawda)
FLEET pile_ratio [4,1,1]->2.0 OK; max_pile=4 couriers=3 gini=0.333 OK
V C1 improve-sla -> GO,[]                  OK
V C2 regress-sla -> NO-GO,[sla_breaches]   OK
V C3 gini +0.05 -> NO-GO,[gini]            OK   (tol 0.02)
V C4 pile +0.15 -> NO-GO,[pile_ratio]      OK   (tol 0.10)
V C5 target-flat -> NO-GO,[target_not_improved]  OK
V C6 gini-better -> GO,[]                  OK
```
Bramki regresji (sla/best_effort/zero_feasible/alerts/gini/pile_ratio) + target-improvement = POPRAWNE dla „lower-is-better" celów. _gini = standardowy wzór, zgodny z ręcznym.

### Część C — LATENTNA INWERSJA (nowy defekt, oracle-wykryty)
```
V C7 target=couriers_used (cand 5->8, REALNIE lepiej = mniej pile-on) -> NO-GO,[target_not_improved]  ← INWERSJA
```
`_determine_verdict:775` hardkoduje `target_improved = base[target] - cand[target] > 0` (lower-better). Dla celu HIGHER-better (`couriers_used` = rozłożenie floty) zwraca NO-GO gdy realnie poprawia. Udokumentowane cele (l.879: sla_breaches|best_effort|gini|pile_ratio|alerts|zero_feasible) są lower-better → inwersja LATENTNA, ale `couriers_used` (intuicyjny dla „de-pile") = mina.

### Część D — strukturalne ryzyka (mniejsze)
- `_pick_fleet_summary:703` priorytet warm>cold>baseline>rolling>naive: jeśli base = raport `--rolling` (ma summary_baseline/rolling, BRAK warm) a cand = raport normalny (ma warm) → porównuje **różne tryby** (baseline-oneshot vs warm-sequential) = apples-to-oranges. Brak strażnika zgodności trybu.
- `_gini` ZDUPLIKOWANY: `sequential_replay.py:663` ↔ `daily_rule_report.py:21` („Gini coefficient – identyczny wzór jak w sequential_replay") = A1/N kopia.

### Werdykt: **UNTESTED — potwierdzony.** Pure-logika ZDROWA dla udokumentowanego użycia (NIE void/kłamiąca), ale 0 pokrycia + latentna inwersja + ryzyko cross-mode + duplikat gini. Jeśli ETAP-5 się o to oprze — konkretne pułapki. **co flipuje:** fleet-level GO/NO-GO dla flipów bundling/objm (ETAP-5). ground-truth na pure-funkcji (ręczna prawda); operacyjnie unproven.

---

## 4. TABELA POKRYCIA (jawnie zbadane / NIE)

| Przyrząd / element | Zbadane? | Metoda | Werdykt |
|---|---|---|---|
| post_shift_overrun_forward_replay | ✅ TAK | grep ledger + recompute + tool-run ×2 + crafted-flip ×2 | VALIDATED |
| `lex_qual` post-shift prepend (objm_lexr6:44) | ✅ TAK | read + crafted-flip (mechanizm) | żywy, sound |
| `decision_flag` resolution (common.py:348) | ✅ TAK | read + flags.json check | module-attr fallback potwierdzony |
| best_effort_fastest_pickup_shadow | ✅ TAK | grep ledger 81 rek + producent read + pos_source oracle | void (pos_source martwe) |
| `_selection_bucket` equal-treatment (2451) | ✅ TAK | read + flags.json | equal-aware potwierdzone |
| sequential_replay `_determine_verdict`/`_fleet_metrics`/`_gini` | ✅ TAK | tests-grep + caller-grep + pure-oracle (7 gini + 7 verdict) | untested + inwersja |
| `_pick_fleet_summary` cross-mode | ⚠ częściowo | read-only (logiczny, nie odpalony diff) | ryzyko, nie odpalone E2E |

## 5. COVERAGE GAPS (nie cisza)
- **Pozostałe void z audytu (l.69-72) NIE oracle'owane** (poza moim mandatem 3): `reassignment_forward_shadow` (memory 29.06: 59% fałszywych ratunków — inny agent/MEMORY), `bug4 reseq shadow` (inny agent), `_objm_lexr6_shadow` (ŚWIADOMIE ZAMROŻONY pod at#152, `ENABLE_OBJM_LEXR6_SELECT_SHADOW=false` → inert; trzyma inline 3-tuple = mina re-flipu, dedup→K1 lex_qual/bucket family). `pending_global_resweep` (de-pile NEW) — VALIDATED dla global_allocate per A4 #10, „untested" tylko dla live NEW-path (PENDING_RESWEEP_LIVE=false).
- **Nie odpaliłem pełnego `sequential_replay` E2E** (load_orders+run_sequential po oknie) — wymaga events.db window + ~min OSRM; pure-funkcje pokryte, ale faktyczny fleet-metrics z realnego okna NIE policzony. Powód: pure-oracle wystarcza dla werdyktu „untested+sound-logic"; pełny E2E = budżet + ryzyko długiego OSRM.
- **delivered_at/picked_up_at caveat** (RECON §D): wszędzie gdzie best_effort z ledgera = button-truth ±~3min. Werdykty 1-2 oznaczone proxy; pure-funkcje 1(mechanizm)/3 = ground-truth.
- **Numery linii** świeże z grepu 2026-06-30 ~18:00; sesje 3/4 mogły dryfować PO grepie.

## 6. HANDOFF
1. **2 z „void" audytu są STALE** — post_shift_overrun_forward_replay = VALIDATED (działa od restartu 06-29 09:25); best_effort_fastest_pickup „stale bucket" OBALONY. Downstream NIE traktować tych void-claimów na słowo (klasa E — audyt-analiza nieświeża po F2-fix/unify 28-29.06).
2. **Realny defekt do naprawy (E):** `dispatch_pipeline.py:6812/6815` `getattr(best,"pos_source")` → `best.metrics.get("pos_source")` (blind-check fastest-pickup ożywa). P2 — bramkuje bezpieczny flip selekcji.
3. **`_determine_verdict` inwersja (I/C):** dodać kierunek per-metryka (higher/lower-better) zanim ETAP-5 użyje couriers_used; + strażnik zgodności trybu w `_pick_fleet_summary`; + testy (0 obecnie). P2/P3.
4. **A1/N:** `_gini` w 2 kopiach (sequential_replay:663 ↔ daily_rule_report:21) — kandydat na wspólny util.
