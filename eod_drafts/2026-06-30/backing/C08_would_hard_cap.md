# C08 — RUNTIME-ORACLE: would_hard_cap / hard_tier_bag_cap (+ post_shift_overrun / eta_source / sla_violations / c2_/d2_)

**Lane C (runtime-oracle, C9/C11). Sesja tmux 2, READ-ONLY. 2026-06-30 ~17:13 UTC.**
Metoda: NIE czytanie samego kodu — odczyt ŻYWEGO `scripts/logs/shadow_decisions.jsonl` (mtime 17:13 FRESH) + **rekomputacja prawdy DRUGĄ, niezależną metodą** (arytmetyka cap z serializowanych wejść; rozkład per-data; inwariant live-gating). Determinizm: 2× na FROZEN snapshot → identyczny md5 `7e3bcc31bec212ce47d4f73bf1b97106`. Numery linii ze świeżego grepu (HEAD `8024705`).

Snapshot użyty do analizy: `scratchpad/shadow_snap.jsonl` (kopia ledgera 17:13). Skrypty: `scratchpad/c08_oracle.py`, `c08_probe2.py`, `c08_probe3.py` (read-only, output do scratchpad).

---

## 0. TL;DR — WERDYKTY

| Pole | Serializowane? | Werdykt oracle | Dowód 2. metodą |
|---|---|---|---|
| **would_hard_cap / hard_tier_bag_cap** | **TAK** (od 29.06) | **VALIDATED** (compute-but-vanish FIX `d23d8a1` POTWIERDZONY) | recompute 1467/1467 match + granica per-data + live-gating inwariant |
| **post_shift_overrun_min/_penalty** | **TAK** (od 29.06, ten sam fix-batch) | **VALIDATED** + meaningful | 1544 nonzero, semantyka neg=zapas/pos=overrun, penalty=0 spójne z flagą OFF |
| **sla_violations** (`.best.plan.*`) | **TAK** (zagnieżdżone w `.plan`) | **VALIDATED** + meaningful | 646 nonzero, rozkład {0:3339,1:477,2:138,3:25,4:5,5:1} |
| **eta_source** | **NIE (0/917)** | **VOID** — compute-but-vanish, NIE naprawione | 6 compute-site w silniku vs 0 w ledgerze; bliźniaki w tym samym dict (pos_source) = 872× |
| **c2_*** | nie w tym ledgerze (własny `c2_shadow_log.jsonl`) | by-design (nie vanish) | grep `"c2_*"`=0 w shadow_decisions; A4 wskazuje osobny plik |
| **d2_*** | brak prefiksu (D/D' = sufiks `_d`/`_dprime` top-level) | by-design | `would_auto_assign_d/_dprime`, `auto_block_reasons_d/_dprime` obecne top-level |

**GŁÓWNY: `would_hard_cap` JEST serializowane, fix `d23d8a1` POTWIERDZONY DRUGĄ METODĄ.** Deferred-verification rejestru (#6 „weryfikacja LIVE odroczona") = ZAMKNIĘTA-VALIDATED. **Co flipuje:** odblokowanie kalibracji cap-Z (O2 bundle-calib review 02.07) — z DWOMA residualnymi zastrzeżeniami (sekcja 3).

---

## 1. INSTANCJE plik:linia (źródło + serializer + flaga)

### 1a. Compute (źródło) — feasibility_v2.py
```
feasibility_v2.py:445   metrics = {"bag_size_before": len(bag)}        # bag_size_before
feasibility_v2.py:453   bag_after = len(bag) + 1
feasibility_v2.py:461   _hard_cap = C.HARD_TIER_BAG_CAP.get(courier_tier, C.HARD_TIER_BAG_CAP_DEFAULT)
feasibility_v2.py:462   metrics["hard_tier_bag_cap"] = _hard_cap        # cap value (tier-aware)
feasibility_v2.py:463   metrics["would_hard_cap"] = bag_after > _hard_cap   # ZAWSZE liczone
feasibility_v2.py:464   if would_hard_cap and C.load_flags().get("ENABLE_HARD_TIER_BAG_CAP", False):
feasibility_v2.py:465       return ("NO", f"hard_tier_bag_cap ({tier} {bag_after}>{_hard_cap})", metrics, None)  # LIVE HARD reject
```
- `common.py:1326` `HARD_TIER_BAG_CAP = {"gold":6,"std+":6,"std":5,"slow":4,"new":4}` · `:1327` DEFAULT=6 · `:1328` `ENABLE_HARD_TIER_BAG_CAP = os.environ.get(...,"0")=="1"` (env-default OFF).
- **Stan EFEKTYWNY: flags.json `ENABLE_HARD_TIER_BAG_CAP=True`** → `:464 C.load_flags().get(...)` = **TRUE** = **LIVE HARD reject** (gold>6/std>5/slow>4/new>4 → NO). Potwierdzone fresh.
- Reason `ok_sla_fits` (feasible-MAYBE) = `feasibility_v2.py:1311` `return ("MAYBE","ok_sla_fits",metrics,plan)` — metrics zawiera würde_hard_cap (set @463 wcześniej).

### 1b. Serializer (compute-but-vanish FIX) — shadow_dispatcher.py
```
shadow_dispatcher.py:190  _AUTO_PROP_PREFIXES = ("v325_","v326_","v3273_","v3274_","v319_","r07_","bonus_","rule_","intra_", ...
shadow_dispatcher.py:258      "post_shift_overrun_", "end_of_day_salvage",        # F2 audyt 28.06 (HARD visibility)
shadow_dispatcher.py:263      "would_hard_cap", "hard_tier_bag_cap")              # #6 d23d8a1 — DODANE jako exact-key
shadow_dispatcher.py:266  def _propagate_prefixed_metrics(base, metrics):
shadow_dispatcher.py:272      if any(k.startswith(p) for p in _AUTO_PROP_PREFIXES):   # "would_hard_cap".startswith("would_hard_cap")=True
shadow_dispatcher.py:501  _propagate_prefixed_metrics(out, m)            # TWIN A (alternatives / per-cand)
shadow_dispatcher.py:885  _propagate_prefixed_metrics(out["best"], best_m)  # TWIN B (best)
```
- **Mechanizm `startswith` zwalidowany:** klucze dodane jako pełne stringi → `k.startswith(k)`=True → przepuszczone. Twin A (501) + Twin B (885) = oba serializują (empirycznie: pole obecne i w `best`, i w `alternatives`).
- Commit `d23d8a1` (28.06 23:09): *„would_hard_cap/hard_tier_bag_cap do _AUTO_PROP_PREFIXES (twin A+B, wzor 1ed9ad7). Liczone ZAWSZE feasibility_v2:463 (LIVE HARD reject), ginely 0/2000 → kalibracja cap-Z slepa. Decyzyjnie-neutralne. Aktywne po restart dispatch-shadow."*

### 1c. eta_source (compute-but-VANISH — NIE naprawione)
```
dispatch_pipeline.py:4051  eta_source = "haversine"
dispatch_pipeline.py:4058  eta_source = "plan"
dispatch_pipeline.py:4070  eta_source = "r07_chain_eta"
dispatch_pipeline.py:4079  eta_source = "soon_free"
dispatch_pipeline.py:5289  "eta_source": eta_source,         # dict lokalny (drive_min/eta_pickup_utc/pos_source/pos_from_store)
dispatch_pipeline.py:5864  c.metrics["eta_source"] = "no_gps_fallback"
dispatch_pipeline.py:5879  c.metrics["eta_source"] = "pre_shift"
```
- 6 compute-site, 6 wartości (haversine/plan/r07_chain_eta/soon_free/no_gps_fallback/pre_shift) = **prowenienca ETA decyzyjnie istotna**. **0/917 w ledgerze.** Bliźniaki z tego SAMEGO dict (5283-5294): `pos_source`=872, `pos_from_store`=872, `drive_min`=872 — serializują się; `eta_source`/`eta_pickup_utc`/`eta_drive_utc` = 0. → eta_source NIE jest ani w `_AUTO_PROP_PREFIXES` (`c.metrics["eta_source"]` @5864/5879 stripowane bo brak prefiksu), ani w jawnym dict serializowanym. **Ta sama klasa co würde_hard_cap PRZED d23d8a1.**

### 1d. post_shift_overrun (compute) — dispatch_pipeline.py
```
dispatch_pipeline.py:5187  post_shift_overrun_min = 0.0     # default (fail-open)
dispatch_pipeline.py:5196  post_shift_overrun_min = round((_pn - _se).total_seconds()/60.0, 2)
dispatch_pipeline.py:5197  post_shift_overrun_penalty = C.post_shift_overrun_penalty(post_shift_overrun_min)
```
- Komentarz `:5184`: *„Metryka liczona ZAWSZE (widoczność w shadow); wpływ na score TYLKO gdy ENABLE_POST_SHIFT_OVERRUN_PENALTY."* Flaga ABSENT w flags.json → effective OFF (A3 zgodne) → penalty shadow-only.
- Serializacja: prefix `post_shift_overrun_` @shadow_dispatcher.py:258 (ten sam batch 28.06).

---

## 2. ORACLE — 3 metody niezależne (na FROZEN snapshot, 917 rekordów 27-30.06)

### Metoda 1 — PRESENCE (grep, presence ≠ wartość)
| Pole | grep -c (linie) | grep -o (wystąpienia) |
|---|---|---|
| would_hard_cap | 399 | 1467 |
| hard_tier_bag_cap | 399 | 1467 (idealnie współ-występuje) |
| post_shift_overrun | 415 | 3072 |
| sla_violations | 869 | 3979 |
| **eta_source** | **0** | **0** |
- Inwariant współ-występowania: **whc present bez cap = 0; cap present bez whc = 0** → para zawsze razem (set @462/463 sąsiadująco). ✓

### Metoda 2 — RECOMPUTE NIEZALEŻNY (arytmetyka cap z serializowanych wejść)
Prawda liczona DRUGĄ drogą: `would_hard_cap_recomp = (bag_size_before + 1) > hard_tier_bag_cap` — używa WYŁĄCZNIE serializowanych pól (`bag_size_before`, `hard_tier_bag_cap`), niezależnie od bool silnika.
- **1467 / 1467 MATCH, 0 MISMATCH** (całe okno). Dziś (30.06): **876/876 MATCH.**
- cap value distribution: **{4:175, 5:324, 6:968}** — wszystkie ∈ {4,5,6} (zgodne z tier-table), **0 out-of-range**.
- → pole jest WEWNĘTRZNIE POPRAWNE; instrument nie kłamie o booleanie względem swoich wejść.

### Metoda 3 — INWARIANT LIVE-GATING (flaga ON ⇒ feasible whc=True musi być best_effort)
Flaga `ENABLE_HARD_TIER_BAG_CAP` EFEKTYWNIE ON → würde_hard_cap=True ⇒ HARD reject @465 ⇒ NIE w puli feasible ⇒ NIE serializowany jako best/alt.
- **whc=True na serializowanych kandydatach: 0** (z 1467). Literał `"would_hard_cap":true` w ledgerze: **0**.
- Reason-string rejectu `"hard_tier_bag_cap ("`: **0** w ledgerze.
- → inwariant HOLDS dokładnie jak przewiduje live HARD reject. **Ale: serializowane würde_hard_cap = ZAWSZE False** (0 zdarzeń wiążących w 4 dni).
- Headroom (cap − bag_after) dla feasible: **{0:57, 1:213, 2:288, 3:372, 4:283, 5:254}** — 57 kandydatów DOKŁADNIE na cap (feasible), 0 ponad (te rejected+znikają).

### Metoda 4 (decydująca) — GRANICA PRE/POST-FIX (rozkład per-data, NIE per-godzina)
Pierwsze wrażenie „473 PROPOSE bez whc, interleaved per-godzina" = **ARTEFAKT agregacji 4 dni po godzinie-doby.** Rozbicie per-DATA rozstrzyga:
| data | PROPOSE WITH / WITHOUT | coverage |
|---|---|---|
| 2026-06-27 | 0 / 206 | **0%** (pre-fix) |
| 2026-06-28 | 0 / 267 | **0%** (pre-fix; d23d8a1 commit 23:09, brak restartu) |
| 2026-06-29 | 218 / 0 | **100%** (post-fix) |
| 2026-06-30 | 181 / 0 | **100%** (post-fix) |
- Granica: ostatni WITHOUT = `2026-06-28T20:22`, pierwszy WITH = `2026-06-29T06:44` (restart dispatch-shadow po commicie 23:09). `systemctl show dispatch-shadow ActiveEnterTimestamp = 2026-06-30 09:55:55` (kolejny restart AUTON-02, fix już aktywny).
- **WNIOSEK: 473 WITHOUT = WYŁĄCZNIE historia pre-fix w rolling 4-dniowym pliku. ZERO partial-serialization bug. Coverage post-fix (29-30.06) = 100%.**

---

## 3. RESIDUALNE ZASTRZEŻENIA dla kalibracji cap-Z (O2) — pole serializowane, ale NIE wystarczające

Fix `d23d8a1` „odblokowuje kalibrację cap-Z" — **częściowo.** Co pole REALNIE daje vs czego NIE:
1. **DAJE:** cap value per tier (4/5/6) + rozkład headroom (jak blisko cap dochodzą feasible worki; 57× dokładnie na cap).
2. **NIE DAJE (residual gap, klasa H/M):** zdarzeń WIĄŻĄCYCH cap. Bo flaga LIVE-ON → würde_hard_cap=True jest HARD-rejected @465 → rejected kandydaci **NIE są serializowani** jako best/alt (ledger serializuje tylko pulę feasible: `best`+`alternatives` = `pool_feasible_count`; `pool_total_count − pool_feasible_count` rejected znika). Reason-string `hard_tier_bag_cap (` też 0 w ledgerze.
   → **Realny binding-rate cap NIEOBSERWOWALNY z tego pola.** W 4 dni: 0 widocznych przekroczeń (R6 35min prawdopodobnie wiąże wcześniej; cap rzadko/nigdy = constraint wiążący). To samo w sobie sygnał kalibracyjny (cap = headroom-only), ale binding-rate wymaga serializacji rejected-pool albo reason-string.
3. **REKOMENDACJA-DRAFT (NIE wykonana, read-only):** żeby cap-Z calibration miała pełen sygnał — serializować rejected-candidate metrics (würde_hard_cap=True) lub agregować reason `hard_tier_bag_cap (` do auto_block/osobnego licznika. Inaczej kalibracja widzi tylko „ile worków podeszło pod cap", nie „ile cap odrzucił".

---

## 4. TABELA POKRYCIA (coverage)

| Zbadane | Jak | Wynik |
|---|---|---|
| would_hard_cap/hard_tier_bag_cap serialize | grep -c/-o + recompute 1467/1467 + per-data + live-gating | VALIDATED (fix d23d8a1 confirmed) |
| Mechanizm `_AUTO_PROP_PREFIXES` startswith (twin A+B) | code-trace shadow_dispatcher.py:190-263/266-272/501/885 | działa, exact-key match |
| Stan EFEKTYWNY ENABLE_HARD_TIER_BAG_CAP | flags.json fresh + feasibility_v2:464 load_flags | **ON = LIVE HARD reject** (A2 błędne „OFF", A3 poprawne) |
| Recompute prawdy 2. metodą | (bag_size_before+1)>hard_tier_bag_cap z serializowanych wejść | 1467/1467 + dziś 876/876 |
| Inwariant live-gating (whc=True⇒reject⇒niewidoczny) | count whc=True na best/alt | 0 (HOLDS) |
| post_shift_overrun serialize+meaning | grep + value-dist (1544 nonzero) | VALIDATED, prefix @258 ten sam batch |
| sla_violations serialize+meaning | nested `.plan` extraction + dist | VALIDATED (646 nonzero) |
| eta_source serialize | codebase grep (6 sites) vs ledger grep (0) + sibling contrast | **VOID — compute-but-vanish, NIE naprawione** |
| c2_/d2_ | grep prefiksów | c2 = osobny plik; d2 = sufiks _d/_dprime top-level (obecne) |
| Determinizm | 2× frozen snapshot | identyczny md5 |

| NIE zbadane (luki jawne) | Powód |
|---|---|
| Realny binding-rate cap w produkcji | rejected-pool + reason-string NIE serializowane do ledgera (sekcja 3) — nieobserwowalne tym pomiarem |
| Serializacja tych pól przez INNE procesy-writery | tylko `dispatch-shadow` pisze `shadow_decisions.jsonl` (A1/A4); czasowka/plan-recheck nie piszą tu |
| Dokładna gałąź dispatch_pipeline składająca serializowany dict kandydata (czy eta_source da się tam dodać) | read-only — rekomendacja, nie trace do końca |
| Fizyczna prawda delivered/picked_up | N/A dla würde_hard_cap — recompute = czysta arytmetyka worka (proxy-certified, niezależny od button/GPS-truth) |

---

## 5. SMELLS / DEDUP (zasila Fazę D/E/F)

- **eta_source compute-but-vanish** = ta SAMA klasa-root co würde_hard_cap pre-d23d8a1 (klasa F/E: metryka decyzyjnie-istotna liczona, ginie w serializerze przez brak w `_AUTO_PROP_PREFIXES`/jawnym dict). Dedup → root „compute-but-vanish serializer prefix gap" (E-instrument-blind). Sąsiad pos_source/pos_from_store/drive_min serializują = dowód że ścieżka działa, eta_source konkretnie wypada.
- **cap-Z residual gap** = klasa H/M (rejected-candidate + reason-string nie serializowane → binding events znikają cicho). Dedup → root „rejected-pool nie-serializowany ⇒ O2 cap-Z half-blind".
- **A2-vs-A3 niespójność stanu flagi** ENABLE_HARD_TIER_BAG_CAP (A2: „flaga OFF" @44/200 vs efektywnie ON LIVE HARD reject). Dedup → klasa D (stan flag deklarowany≠efektywny w artefakcie Fazy A); rozstrzygnięte fresh na korzyść A3. Materialne: to HARD produkcyjny reject, nie shadow.
- post_shift_overrun_min **negatywny gdy w zmianie** (zapas), dodatni = overrun — nazwa lekko myląca (L), ale spójnie liczone, nie bug.

---

## 6. CO FLIPUJE (decyzja napędzana przyrządem)
- **would_hard_cap/hard_tier_bag_cap** → kalibracja cap-Z (O2 ready-anchor sweep), bramka `dispatch-bundle-calib-review` 02.07 07:00 + at-168. Werdykt: pole serializowane (VALIDATED), ale O2 musi UWZGLĘDNIĆ residual gap (binding events nieobserwowalne — sekcja 3).
- **post_shift_overrun** → flip `ENABLE_POST_SHIFT_OVERRUN_PENALTY` (obecnie OFF, shadow-visible).
- **eta_source (VOID)** → ETA-provenance dla pickup-slip/eta-calibration jest ŚLEPA w ledgerze; każda kalibracja ETA opierająca się na „skąd ETA" (real plan vs haversine vs no_gps fiction vs pre_shift) nie ma tego sygnału. Naprawa = analogiczna do d23d8a1 (dodać do serializera).
