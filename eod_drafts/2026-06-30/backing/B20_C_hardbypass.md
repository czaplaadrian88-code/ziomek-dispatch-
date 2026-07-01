# B20 — KLASA C: HARD-BYPASS i naruszenia FEASIBILITY-FIRST (lane B, sesja tmux 2)

**Agent:** B20-C-hardbypass · **Tryb:** READ-ONLY (zero edycji/restartów/flipów) · **Data:** 2026-06-30 ~14:30 UTC
**HEAD:** `8024705` · **Numery linii ze ŚWIEŻEGO grepu DZIŚ** (seed `~2383/5749` zdryfował → realnie def `:2480`, call `:5938`).
**Stan flag EFEKTYWNY** zmierzony z `flags.json` (hot-reload dispatch-shadow), nie z env-default/komentarza.

**Zakres (zlecenie):** (1) `_assert_feasibility_first` — każda mutacja `top[0]`/`feasible` ZA guardem (wzorzec #10: `FEAS_CARRY_READMIT` verdict=NO→MAYBE; best-effort re-admit); (2) always-propose vs HARD — sentinel best-effort (score −1e9, feasibility=NO) do propozycji: świadome (OK) czy obejście; (3) R6 tier-aware 35/40 — gdzie liczone PŁASKO 35; (4) `ETA_QUANTILE_R6_BAGCAP` luzuje HARD R6.

---

## 0. TL;DR — co jest, czego nie ma

Fundament „SOFT nie obejdzie HARD" stoi na JEDNYM strażniku `_assert_feasibility_first` (`dispatch_pipeline.py:2480`, call `:5938`) + filtrze `feasible=[MAYBE]` (`:5905`). **Pod obecną konfiguracją flag guard jest skuteczny** (nic nie wstrzykuje NO po nim), ALE:

1. **Guard pilnuje stanu @5938, NIE stanu emitowanego.** Łańcuch mutacji selekcji ciągnie się do `:6301` (tiering, objm_d2, E2 pln-resort, FEAS_CARRY_READMIT). Guard NIGDY się nie powtarza. Pod current-config bezpieczne (jedyny wstrzykiwacz NO = FEAS_CARRY_READMIT, flaga OFF), ale to **dyscyplina-komentarza, nie runtime-inwariant**. (C-01/C-02)
2. **FEAS_CARRY_READMIT = kanoniczny wzorzec #10** — promuje odrzucony (verdict NO) na `top[0]`, **pierze verdict NO→MAYBE** (`:6278`). Flaga `ENABLE_FEAS_CARRY_READMIT=False` → latentne. (C-01)
3. **`ETA_QUANTILE_R6_BAGCAP` (effective TRUE)** REALNIE luzuje HARD R6=35 dla gold≤4 podmieniając wartość bramkowaną na p80 (`feasibility_v2:1089`). JEDYNA ścieżka, którą >35 przechodzi. Oś kalibracji = otwarty problem (cross-ref allocation R9). (C-03)
4. **Zawory KOORD „ucieczka do człowieka gdy wszyscy źli" — niemal wszystkie wyłączone/zamaskowane**: `all_candidates_low_score` (maskuje `ALWAYS_PROPOSE_ON_SATURATION=True`), `difficult_case_redirect` (flaga OFF), `commit_divergence_gate` (flaga OFF), 3× best_effort-redirect (maskowane). Żywe HARD-werdykt-zawory ścieżki feasible = tylko early-bird + pusta pula + ultra-wąski `geometry_blind_fallback`. **Iluzja defense-in-depth.** (C-05)
5. **Always-propose sentinel best-effort = ŚWIADOME (OK), potwierdzone.** Guard NIE pokrywa tej ścieżki (best_effort wybiera z `with_plan` zawierającego NO; `feasible` puste → guard trywialnie przechodzi). Uczciwość zależy od `best_effort=True`+auto_route+render bannera (cross-repo). (C-06)
6. **R6 flat-35 w `feasibility:1105` = POPRAWNY kanon** (Tier-1 normal); 40 = poziom ESKALACJI (best_effort cap-stretch, NIE klasa kuriera — common.py:2664). Realny rozsyp = best_effort R6-KOORD-redirect liczy `_be_max_bt > 35` PŁASKO obok cap-40 (`:6859`). (C-04)

---

## 1. STAN FLAG EFEKTYWNY (zmierzony flags.json DZIŚ — kotwica severity)

| Flaga | flags.json | rola w klasie C |
|---|---|---|
| `ENABLE_ALWAYS_PROPOSE_ON_SATURATION` | **True** | maskuje 4 zawory KOORD (`:6491,6864,6900,6926`) |
| `ENABLE_ETA_QUANTILE_R6_BAGCAP` | **True** | LIVE luzowanie HARD R6 gold≤4 |
| `ENABLE_BEST_EFFORT_OBJM_R6_KEY` | **True** | best_effort selekcja cap-40 (Tier-3) |
| `ENABLE_FEAS_CARRY_READMIT` | **False** | wzorzec #10 readmit — latentny |
| `ENABLE_PACZKA_R6_THERMAL_EXEMPT` | **True** | R6/SLA exempt paczki (3 site, 1 brak w O2) |
| `ENABLE_HARD_TIER_BAG_CAP` | **True** | ⚠ REALNY HARD-reject (gold6/std5/slow4/new4) — A2 podał „OFF" = STALE |
| `ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD` | **True** | best_effort R6>próg→KOORD — maskowany always_propose |
| `ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT` | <absent>→def True | flat-35 redirect — maskowany always_propose |
| `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` | **False** | zawór KOORD OFF |
| `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT` | **False** | zawór KOORD OFF |
| `ENABLE_PLN_RESORT_WITHIN_TIER` / `ENABLE_OBJM_LEXR6_SELECT` | **True** | post-guard reorder (permute-only) |

---

## 2. INSTANCJE (plik:linia świeży + źródło/objaw + latane? + otwarte? + severity + dowód + dedup)

### C-01 — FEAS_CARRY_READMIT: HARD-bypass PO guardzie + pranie verdict NO→MAYBE [wzorzec #10]
- **Plik:** `dispatch_pipeline.py:6266-6301` (mutacja LIVE), `:1289-1352` (`_feas_carry_readmit_pick`), `:6278` (`_fcr_cand.feasibility_verdict = "MAYBE"`).
- **Co:** gdy chosen `top[0]` niesie wybaczony R6-breach, funkcja szuka ODRZUCONEGO (`feasibility_verdict=='NO'`, blocking sla/r6, nowy order ≤ cap-40) lepszego carry-inclusive (`lex_qual < chosen`), **promuje go na `top[0]`** (`top.pop`+`top.insert(0,…)` `:6291-6293`, `feasible.insert(0,…)` `:6295`) i **przepisuje jego verdict NO→MAYBE** „dla spójności serializacji/inwariantu" (`:6278`, komentarz `:6263-6265`).
- **Warstwa-przyczyna:** L7 selekcja, ZA guardem L8-pre. Komentarz sam nazywa to: „OSTATNIA mutacja selekcji (po E2/OBJM/shadow)".
- **Guard-relacja:** `_assert_feasibility_first` woła się RAZ `:5938` — **328 linii PRZED** tym readmit. Po readmicie guard się NIE powtarza. Filtr `feasible=[MAYBE]` też był `:5905` (przed). Readmit świadomie omija oba (HARD u źródła nietknięty — `check_feasibility_v2` dalej zwraca NO; bypass żyje w warstwie selekcji + laundering verdict).
- **Latane?** flaga `ENABLE_FEAS_CARRY_READMIT=False` (rollback hot 27.06; instrument feas_carry „🔴 VOID, realny readmit 4/2816=0,14%" — A4 #3). Kod obecny, gałąź wpięta.
- **Otwarte?** TAK strukturalnie (wzorzec #10 = „guard ślepy poza swój call-site"; mutacja `top[0]` ZA guardem). Dziś bezpieczne BO flaga OFF.
- **Severity: P2** (latentny, ale to udokumentowana dźwignia live-arming; pranie verdict to realny precedens HARD-bypass w warstwie selekcji).
- **Dowód:** `:6278` literalnie `feasibility_verdict = "MAYBE"` na kandydacie którego `check_feasibility_v2` zwróciła NO; komentarz `:6263` „bramka candidata dalej zwraca NO; tu selekcja przenosi go, promote verdict→MAYBE". Protokół wzorzec #10 (ten case nazwany „SAFE/ACK — wzorzec jest pułapką").
- **dedup_hint:** R-FEAS-FIRST-GUARD-BLIND (guard @5938 nie broni stanu @emit).

### C-02 — `_assert_feasibility_first` broni stanu @5938, NIE stanu emitowanego [strukturalne]
- **Plik:** def `dispatch_pipeline.py:2480`, JEDYNY call `:5938`. Łańcuch mutacji `feasible`/`top` PO guardzie: late-pickup tiering `:5953`, `_objm_lexr6_d2_pick` reorder `:5996-6001`, `_pln_pure_resort` E2 `:6239`, FEAS_CARRY_READMIT `:6266-6295`.
- **Co:** guard to jednorazowy fail-loud sprawdzający `feasibility_verdict=='NO'` w `feasible`. Kotwiczy na pozycji @5938; cała dalsza selekcja (3 reorder + 1 readmit) NIE jest re-asserowana. „Tiering/LEXR6 niżej tylko PERMUTUJĄ ten sam zbiór" (komentarz `:5936-5937`) = ZAŁOŻENIE utrzymane dyscypliną, nie runtime-inwariantem.
- **Weryfikacja current-config:** late-pickup/objm_d2/pln_resort = sort/reorder istniejących (permute-only, NO nie wstrzykiwane — potwierdzone: `_pln_pure_resort:1092` `top.sort`, `_objm_lexr6_d2_pick:5998-6001` pop+insert). Jedyny wstrzykiwacz NO = FEAS_CARRY_READMIT (OFF). → guard EFEKTYWNIE skuteczny DZIŚ.
- **Latane?** NIE — brak post-mutacyjnego re-assert. (Protokół L6 audytu pre-shift żąda „RUNTIME INWARIANT + STRAŻNIK" — tu go nie ma dla feasibility-first-at-emit.)
- **Otwarte?** TAK. Każda przyszła zmiana/flip wstrzykująca NO po `:5938` (FEAS_CARRY_READMIT, lub nowa) przejdzie niewidziana. Brak testu „top[0] @emit zawsze MAYBE".
- **Severity: P2** (fundament P0 broniony 1 punktem czasowym; wzorzec #10 to udowodniona klasa).
- **Dowód:** grep `_assert_feasibility_first` = {def 2480, call 5938} — zero re-callów; mutatorzy `:5953/5996/6239/6266` wszyscy ZA 5938.
- **dedup_hint:** R-FEAS-FIRST-GUARD-BLIND.

### C-03 — `ETA_QUANTILE_R6_BAGCAP` luzuje HARD R6=35 dla gold≤4 (LIVE) [HARD-loosener]
- **Plik:** `feasibility_v2.py:1088-1101` (podmiana `_gate_bt` na p80), reject `:1105` `if _gate_bt > C.BAG_TIME_HARD_MAX_MIN`. Flaga def `common.py:236` `=False`, **flags.json=True → effective TRUE**.
- **Co:** dla `courier_tier=='gold'` i `len(bag)+1<=4`: surowy `bag_time_min` (np. 37) zastąpiony `eta_quantile_calibrate(…, quantile="p80")` (`:1093`). Jeśli p80≤35 → R6 NIE odrzuca (gdy surowy >35). „JEDYNE >35 ready-anchored co przechodzi" (protokół `:96`). Surowy zostaje dla metryki (`r6_gold4_gate_recovered` `:1098`).
- **Warstwa-przyczyna:** L5 feasibility HARD — bramka HARD luzowana KALIBRACJĄ.
- **Latane?** ON i replay-walidowane (komentarz `:1083-1084` CI[-0.63,+1.25], 14:1). KONTROLOWANE (gold≤4, tylko podmiana check, raw zachowany).
- **Otwarte?** TAK — OŚ kalibracji. Allocation-audit R9 (`feasibility_v2:1089`): „luzuje HARD R6 globalnym p80 na osi JAZDY, mimo że kalibracja 29.06 dowiodła noga jazdy ~0 błędu, optymizm = poślizg ODBIORU". Kalibracja celuje (potencjalnie) w złą oś → luzuje HARD na podstawie kalibracji nie-łapiącej realnego źródła błędu. Dotyka HARD R6 → protokół+ACK przy zmianie.
- **Severity: P2** (LIVE HARD-loosener; ograniczony+walidowany, ale oś = realny otwarty problem, member rodziny SLA-anchor A6-gr.4 / R3).
- **Dowód:** `:1089` flaga + `courier_tier=="gold" and (len(bag)+1)<=4`; `:1096-1097` `if bag_time_min > 35 and _c <= 35`.
- **dedup_hint:** R-R6-ANCHOR-CALIB (oś poślizgu odbioru vs jazdy; SLA-anchor twin family).

### C-04 — best_effort R6-KOORD-redirect liczy PŁASKO 35 obok cap-40 [rozsyp progów N/A1, latentny przez maskę]
- **Plik:** `dispatch_pipeline.py:6857-6864`: `_be_breach_orders=[oid if _bt > C.BAG_TIME_HARD_MAX_MIN]` (`:6859`, flat 35) + `if … _be_max_bt > C.BAG_TIME_HARD_MAX_MIN and len(_be_breach_orders)>=1 and not _always_propose_on()` (`:6862-6864`).
- **Co:** w ścieżce best_effort (0 feasible) selekcja dopuszcza nowy order do **cap-40** (`_best_effort_objm_pick` cap_min=40, `:6775`), ale ten redirect KOORD-uje gdy max-bag-time PO WSZYSTKICH orderach > **35**. Pasmo 35-40 (legalne dla escalation Tier-3) jest wybierane-a-potem-(byłby)-KOORDowane → dwa progi R6 w TEJ SAMEJ ścieżce best_effort (35 redirect vs 40 selekcja).
- **Warstwa-przyczyna:** L7/L8 best_effort. Mierzy `_be_max_bt` (max wszystkich, w tym CARRY) — łapie też realne carry-breach (intencja BUG E: carry 43-90min→KOORD), ale conflate'uje z nowo-orderowym pasmem 35-40.
- **Latane?** NIE u źródła. **Ponadto DEAD pod current-config:** `_always_propose_on()` zwraca True (`ALWAYS_PROPOSE_ON_SATURATION=True`) → cały redirect zamaskowany (C5 short-circuit). Best_effort ZAWSZE PROPOSE z bannerem.
- **Otwarte?** TAK (latentny rozsyp; uzbroi się gdy `ALWAYS_PROPOSE_ON_SATURATION` flip OFF). Spójne z protokołem: „KAŻDA logika R6/overage MUSI być tier-aware, NIE flat 35" (bundle_calib flat-35 = ten sam smell).
- **Severity: P3** (latentny + maskowany; kierunek konserwatywny = KOORD).
- **Dowód:** `:6859/6862` literał `C.BAG_TIME_HARD_MAX_MIN` (35) vs `:6775` cap_min=40; common.py:2664-2667 „Tier 3 = cap-stretch" (40 = ESKALACJA, nie klasa kuriera).
- **dedup_hint:** R-R6-THRESHOLD-SCATTER (flat-35 site obok escalation-40).

### C-05 — Zawory KOORD „gdy wszyscy źli" niemal wszystkie wyłączone/zamaskowane [iluzja defense-in-depth]
- **Plik (ścieżka feasible, pool>0):**
  - `all_candidates_low_score` `:6486-6509` — gated `and not _always_propose_on()` (`:6491`) → **DEAD** (always_propose ON).
  - `commit_divergence verdict gate` (BUG C) `:6511+` — `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE=False` → **DEAD**.
  - `difficult_case_redirect` `:6601-6675` — `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT=False` (`:6609`) → **DEAD** (tylko `difficult_case_redirect_shadow` `:6668`).
  - `geometry_blind_fallback` `:6443-6470` — ŻYWY ale ultra-wąski: wymaga `len(feasible)>=2` AND `all greedy_fallback` AND `all cos<0` (`:6443-6453`). Allocation-audit: „za wąski — nie odpala przy pool=0".
  - **ścieżka best_effort (0 feasible):** 3× redirect `:6864 (flat-35), :6900 (OBJ F3 R6>próg), :6926 (low-score)` — wszystkie `and not _always_propose_on()` → **DEAD**.
- **Co:** efektywnie w ścieżce feasible `top[0]` jest proponowany niemal bezwarunkowo; jedyne żywe HARD-werdykt-ucieczki = early-bird (`:2598`, L3), pusta pula, ultra-wąski geometry_blind. To napędza allocation P0-A (geometrycznie-ślepe propozycje typu Dawid 447 spread 10-12km przechodzą jako PROPOSE — `all_candidates_low_score`/geometry-valve ich nie łapią).
- **Latane?** To LIVE postura (świadome always-propose), ALE jednoczesne OFF na `difficult_case` + `commit_divergence` + maska `low_score` = brak backstopu. Kod 4 zaworów istnieje → fałszywe wrażenie obrony.
- **Otwarte?** TAK (koherencja: 4 zawory w kodzie, ~wszystkie nieaktywne; pytanie czy zamierzone razem). Cross-ref C2 protokołu „co ta flaga MASKUJE".
- **Severity: P2** (LIVE; bezpośrednio zasila klasę bezsensownych propozycji; defense-in-depth iluzoryczne).
- **Dowód:** flags.json (commit_divergence=False, difficult_case=False, always_propose=True); `:6491/6864/6900/6926` `not _always_propose_on()`; `:6443` warunek potrójny.
- **dedup_hint:** R-KOORD-VALVES-MASKED (most do allocation P0-A geometry-blind).

### C-06 — Always-propose sentinel best-effort (feasibility=NO → PROPOSE) = ŚWIADOME [potwierdzone, NIE bypass]
- **Plik:** `dispatch_pipeline.py:6744-6959` (ścieżka best_effort gdy 0 feasible); `best.best_effort=True` `:6756/6788`; finalny `verdict="PROPOSE"` `:6948` `pool_feasible_count=0`; sentinel score −1e9 dla kar rankingowych `:1853/1890/1917/1954` (sort na koniec, kandydat ZOSTAJE — komentarz `:1819` „kandydat zostaje").
- **Co:** gdy pula feasible pusta, best_effort wybiera z `with_plan` (kandydaci z planem, w tym `feasibility_verdict=='NO'`) i PROPONUJE z bannerem. **Guard `_assert_feasibility_first` NIE pokrywa tej ścieżki** — sprawdza `feasible` (puste w best_effort) → trywialnie przechodzi. To NIE obejście guarda, to ortogonalna ścieżka.
- **Latane?** N/D — by-design. Protokół: „Sentinel best-effort w konsoli/Telegramie = POPRAWNE (uczciwy framing przez `_serialize_result`), NIE bug" (zweryf. 27.06).
- **Otwarte?** NIE (świadome). ⚠ Uczciwość ZALEŻY od: `best_effort=True` + `_classify_and_set_auto_route`→ALERT (`:6958`) + render bannera w konsoli/apce/Telegramie (cross-repo J). Jeśli render zgubi banner → sentinel wygląda jak normalna propozycja (to klasa F/J render, nie C).
- **Severity: P3** (informacyjne — potwierdzenie świadomości; ryzyko realne tylko w renderze cross-repo).
- **Dowód:** komentarze `:6864/6900/6926` „ALWAYS-PROPOSE: proponuj best_effort z bannerem ⚠️"; common.py:2664 reguła 3-stopniowa Adriana.
- **dedup_hint:** R-ALWAYS-PROPOSE-SENTINEL (świadome; uczciwość = render cross-repo).

### C-07 — Backstop FEAS_CARRY_READMIT (MIN_PROPOSE + commit_divergence) jest WYŁĄCZONY [flag-coupling C2/C3, latentny]
- **Plik:** komentarz-obietnica `dispatch_pipeline.py:6262-6265` „downstream MIN_PROPOSE + commit_divergence_gate dalej gate'ują nowy top[0]". Realnie: MIN_PROPOSE/`all_candidates_low_score` `:6491` maskowany always_propose; `commit_divergence` `:6511` OFF.
- **Co:** readmit (C-01) przenosi NO→MAYBE na `top[0]`, ufając że downstream zawory go dogate'ują. Ale te zawory są DZIŚ nieaktywne (always_propose ON + commit_divergence OFF). → gdyby `ENABLE_FEAS_CARRY_READMIT` flip ON BEZ re-enable backstopu, re-dopuszczony kandydat HARD-NO leci do propozycji bez siatki.
- **Warstwa-przyczyna:** sprzężenie flag (C3) — bezpieczeństwo readmit zależy od flag, które niezależnie zgaszono.
- **Latane?** NIE. Sprzężenie nieudokumentowane jako para.
- **Otwarte?** TAK — mina na dźwigni flip (dokładnie wzorzec C2 „flip uzbraja uśpiony defekt").
- **Severity: P2** (latentne, ale to konkretna mina pod udokumentowanym przyszłym flipem #483000).
- **Dowód:** komentarz `:6263` vs flags.json (commit_divergence=False) + `:6491` maska.
- **dedup_hint:** R-FEAS-FIRST-GUARD-BLIND + flag-coupling C2.

---

## 3. POTWIERDZENIA „NIE-BUG" (żeby Faza E nie liczyła jako chaos)

- **R6 flat-35 w `feasibility:1105`** = POPRAWNY kanon Tier-1. 40 to poziom ESKALACJI best_effort (common.py:2664 „Tier 3 = cap-stretch", `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` `:2651`), NIE klasa kuriera. „Tier-aware 35/40" ≠ per-courier-class — to normal-vs-alarm. HARD-reject feasibility SŁUSZNIE flat 35; jedyne >35 co przechodzi = ETA_QUANTILE gold≤4 (C-03) i best_effort fallback (C-06).
- **`_pln_pure_resort` (E2, LIVE 20%)** i **`_objm_lexr6_d2_pick` (LIVE)** = permute-only (sort/reorder istniejącego `top`/`feasible`), NIE wstrzykują NO. Bucket = `_selection_bucket` (equal-treatment-aware, B2 fix 28.06 `:1086`). Guard `:5938` „same set" trzyma.
- **`_demote_blind_empty` `:2504`** = reorder (informed first), nie verdict-mutacja.
- **`HARD_TIER_BAG_CAP` `feasibility:464-465`** = REALNY HARD-reject, effective ON (flags.json) — działa jako HARD przed scoringiem (poprawne). [A2 podał „flaga OFF" = STALE — klasa D dryf-doc, cross-ref A3 który miał ON.]
- **Legacy F1.8e NO @`:5895`** (`c.feasibility_verdict="NO"` dla pre_shift_too_late) = DEAD (gałąź `else` gdy `ENABLE_V324A_SCHEDULE_INTEGRATION` OFF; V324A ON → `pass` `:5889`). Nawet gdyby fire — filtr `feasible=[MAYBE]` `:5905` wycina przed guardem. Bez wpływu na guard.

---

## 4. TABELA POKRYCIA (co zbadane / czego NIE + powód)

| Obszar | Zbadane? | Plik:linia | Uwaga |
|---|---|---|---|
| `_assert_feasibility_first` def+calls | ✅ | dp:2480, 5938 | jedyny call; brak re-assert |
| Mutacje `top[0]`/`feasible` PO guardzie | ✅ | dp:5953,5996,6239,6266 | 3 permute + 1 readmit(OFF) |
| FEAS_CARRY_READMIT live + pick | ✅ | dp:6266-6301,1289-1352 | verdict NO→MAYBE laundering |
| verdict-NO/MAYBE assignment sites (grep) | ✅ | dp:5811,5895,6278 | tylko 6278 ZA guardem |
| Always-propose best_effort path | ✅ | dp:6744-6959 | 3 redirect maskowane |
| `_always_propose_on()` + maska | ✅ | dp:2638-2644, flags.json | True → maskuje 4 zawory |
| Zawory KOORD feasible (geom/low/commit/difficult) | ✅ | dp:6443,6486,6511,6601 | 3 DEAD, 1 ultra-wąski |
| R6 flat-35 sites | ✅ | feas:1105; dp:6859,6862,4788 | feas=kanon; best_effort redirect=scatter |
| ETA_QUANTILE_R6_BAGCAP | ✅ | feas:1088-1101 | LIVE loosener gold≤4 |
| cap-40 escalation semantyka | ✅ | common:2651,2664; dp:633,6775 | 40=eskalacja nie klasa |
| HARD_TIER_BAG_CAP enforcement | ✅ | feas:461-465 | LIVE HARD (A2 stale) |
| PACZKA_R6_THERMAL_EXEMPT sites | ⚪ częściowo | feas:1050-1054,1152-1155 | 2 site potwierdzone; 3. (plan/is_paczka) NIE re-grepowany; brak w O2 = A6-gr.4 |
| flagi efektywne (flags.json) | ✅ | flags.json | zmierzone, nie env-default |
| **NIE zbadane:** drop-in env per-proces dla feasibility (czy plan-recheck/panel-watcher mają inny stan ETA_QUANTILE/PACZKA) | ❌ | — | A3 pokrył route/canon; ETA_QUANTILE/PACZKA są flags.json (hot, nie env-frozen) → parytet przez flags.json; ale nie zmierzyłem `systemctl show` per-proces — Faza C/D |
| **NIE zbadane:** czy readmit/best_effort ma bliźniaka w `plan_recheck`/`czasowka_scheduler` (re-feasibility re-run) | ❌ | — | poza budżetem B20; A6-gr (feasibility↔greedy↔plan_recheck) wskazuje że plan_recheck NIE re-aplikuje wszystkich HARD → osobny agent |
| **NIE zbadane:** runtime — czy FEAS_CARRY_READMIT/ETA_QUANTILE realnie odpala (próbka shadow_decisions) | ❌ | — | READ-ONLY inwentarz; oracle = Faza C (feas_carry instrument już VOID per A4#3; ETA_QUANTILE recover-count w metryce) |
| **NIE zbadane:** cross-repo render bannera best_effort (czy sentinel widoczny w konsoli/apce) | ❌ | — | klasa J/F render; granica STOP na silniku |

---

## 5. DEDUP — do których rootów się zwija (anty-double-count dla Fazy E)

| dedup_hint | instancje | most do A6/allocation |
|---|---|---|
| **R-FEAS-FIRST-GUARD-BLIND** | C-01, C-02, C-07 | NOWY root klasy C (wzorzec #10) — NIE w A6 7-grup; pokrewny „brak runtime-inwariantu" jak floor (A6-gr.6) ale dla feasibility-first-at-emit |
| **R-R6-ANCHOR-CALIB** | C-03 | A6-gr.4 SLA-anchor (R3) + allocation R9 (oś jazda vs poślizg-odbioru) — NIE re-derywować, cross-ref |
| **R-R6-THRESHOLD-SCATTER** | C-04 | A2 smell #3 (flat-35 vs tier-40 vs bundle_calib) — klasa N; member best_effort redirect |
| **R-KOORD-VALVES-MASKED** | C-05 | allocation P0-A (geometry-blind sail-through) — NOWY angle (wszystkie zawory, nie tylko geometria) |
| **R-ALWAYS-PROPOSE-SENTINEL** | C-06 | świadome (protokół) — NIE liczyć jako defekt; uczciwość = render J |

**Wniosek dla Fazy E:** najmocniejszy NOWY (poza-A6) root tej klasy = **R-FEAS-FIRST-GUARD-BLIND** (guard jednorazowy @5938 nie broni stanu emitowanego; wzorzec #10 readmit + brak runtime-inwariantu „top[0]@emit zawsze MAYBE"). Reszta zwija się do znanych rootów (R6-anchor-calib, threshold-scatter, KOORD-valves jako angle allocation-P0-A). R-KOORD-VALVES-MASKED to nie tyle „kopia reguły" co **koherencja flag** (4 zawory istnieją, ~wszystkie zgaszone razem — iluzja obrony) → Faza D (graf flag co-maskuje-co).
