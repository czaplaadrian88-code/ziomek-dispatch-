# B19 — INTEGRALNOŚĆ TEST-SUITE jako ORACLE dla ETAP-4 (lane B cross-cutting)

**Agent:** B19-testsuite-integrity · **Sesja tmux 2** · **READ-ONLY** (zero edycji silnika/restartów/flipów/git) · **2026-06-30 ~14:1x UTC**
**HEAD recon:** `8024705`. Numery linii ze ŚWIEŻEGO grepu/odczytu DZIŚ. Próby na żywej `flags.json` robione na KOPII (FLAGS_PATH→tmp), zero zapisu do prod.
**Pytanie nadrzędne:** czy `pytest tests/` jest GODNYM ZAUFANIA oracle, na którym Faza/ETAP-4 może oprzeć „flaga ON≠OFF + PEŁNA regresja zielona"?

**Werdykt jednym zdaniem:** Suite jest w ~99% wiarygodny dla regresji jednostkowej, ALE ma **4 strukturalne erozje oracle**, które dokładnie podkopują gwarancje protokołu ETAP-4 (pkt 4 „flaga ON≠OFF" + „PEŁNA regresja zielona"): (1) **conftest-leak** — 63 flagi decyzyjne przeciekają prod-ON do testów, więc „test-OFF" cicho biegnie ON (idiom izolacji złamany dla nich); (2) **granulacja whole-file** ~60 script-runnerów + 4 xfaile zwijają N asercji w jeden exit-code (v316 chowa 16 padających asercji); (3) **martwy/stały fixture** utrwala STARĄ politykę (demote vs equal-treatment) i WĄSKĄ definicję floor (committed-only, brak shift_start); (4) **baseline trwale CZERWONY (2)** normalizuje „2 fail = norma" + jeden z nich NIEHERMETYCZNY (zegar). Wszystkie 4 = OTWARTE.

---

## 0. BASELINE DZIŚ (kanoniczna ścieżka, venv dispatch)
`3611 passed, 2 failed, 26 skipped, 6 xfailed` (recon ETAP0 §F; nie re-uruchamiałem PEŁNEJ suity — DoD read-only/heavy, patrz LUKI). Oba FAIL = klasa „integralność oracle", NIE regresja silnika:
1. `test_flag_doc_coverage::test_baseline_is_not_stale` — odczyt RED potwierdzony (uruchomiony, patrz F-B19-01).
2. `test_working_override_2026_06_01.py::test_13_real_shift_wins_over_working` — niehermetyczny (zegar, F-B19-09).

---

## (a) DLACZEGO flag-doc baseline NIEŚWIEŻY → **F-B19-01** (klasa D)

**Uruchomiłem test** — komunikat DOKŁADNY:
```
AssertionError: baseline zawiera nieaktualne wpisy (1) — zniknęły z flags.json
lub już udokumentowane: ['ENABLE_AUTO_ASSIGN']
```
**Mechanizm (tools/flag_doc_coverage_check.py:42-43):**
```python
stale_baseline = sorted(b for b in base if b not in flags or b in ref)
```
`ENABLE_AUTO_ASSIGN` jest w `tools/flag_doc_baseline.json` (l. „baseline":[ pierwszy wpis) ALE od commita **`8024705` „docs(AUTON-02): ZIOMEK_LOGIC_REFERENCE — warstwa auto-assign"** flaga jest TERAZ udokumentowana w `ZIOMEK_LOGIC_REFERENCE.md` → `b in ref` → stale.

**KOREKTA hipotezy recon:** recon §F zgadywał „AUTON-02/force-recheck dorzucone NIEudokumentowane". Faktycznie to ODWROTNIE: `test_no_new_undocumented_decision_flag` **PASS** (force-recheck `ENABLE_COORDINATOR_FORCE_TIME_RECHECK` JEST w baseline → brak new_drift). Padło `test_baseline_is_not_stale` bo AUTON-02 **udokumentował** flagę a **nie skurczył baseline** (housekeeping ratchetu zostawiony w połowie przez sesję `8024705`). Naprawa = 1-linijka (usuń `ENABLE_AUTO_ASSIGN` z baseline). To **self-healing ratchet który ZADZIAŁAŁ** (złapał dryf) — ale dopóki nikt nie zrobi 1-linijki, baseline jest RED → ETAP-4 traci „zielony baseline".

**Drugorzędne (CONFIRMED, uruchomione checkery):**
- `flag_doc_coverage`: **tylko 36.8% udokumentowane (46/125), 79 grandfathered w baseline** jako „świadomy dług". Ratchet blokuje tylko NOWE → 79 niedokumentowanych flag decyzyjnych żyje pod zielonym.
- `flag_hygiene_check` (orphan): `198/198 odwoływane, 0 sierot` — czysto (4 dynamic-readers do ręcznej weryfikacji).

---

## (b) CONFTEST FLAG-LEAK — **F-B19-02** (klasa D, P1) ★ najważniejsze

**conftest.py:304-313** (`_isolate_flags_json`) i **conftest.py:190-199** (`_stripped_flags_copy` dla script-runnerów) strippują z tmp-kopii `flags.json` WYŁĄCZNIE:
`ETAP4_DECISION_FLAGS (59) + FLAGS_JSON_NUMERIC_OVERRIDES (25) + TEST_ISOLATED_INFRA_FLAGS (3)`.

**Kontrakt deklarowany** (conftest.py:299-303 + common.py:349): „decision_flag() przy braku klucza spada na stałą modułu → idiom testów (patch `common.ENABLE_X`) działa". **Ten kontrakt jest FAŁSZYWY dla każdej flagi decyzyjnej POZA tymi 3 rejestrami**, bo `decision_flag` (common.py:361) czyta `load_flags().get(name, globals().get(name,False))` — flags.json (NIEstrippowany klucz = prod-ON) WYGRYWA z patchem stałej.

**ZMIERZONE empirycznie** (`scratchpad/leak_probe.py` + `leak_proof.py`, kopia flags.json, strip 1:1 jak conftest, FLAGS_PATH→tmp):
- **63 flagi decyzyjne** `ENABLE_*/USE_*` są True w flags.json i NIE strippowane (LEAK SET).
- Z tego **z stałą-modułu False (najgroźniejsze: test patchuje const=False, biegnie ON):** `ENABLE_GEOCODE_VERIFICATION_ENFORCE`, `ENABLE_OBJ_COMMITTED_PICKUP_PENALTY`, `ENABLE_R6_SOFT_PEN_CAP`, `ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY`, `ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW/_OBJM_SHADOW`, `ENABLE_GPS_DELIVERY_VALIDATION`, `ENABLE_MIN_DELIVERED_AT_SHADOW`, `ENABLE_PARCEL_LANE_LIVE`, `ENABLE_PREP_VARIANCE_ANOMALY_SHADOW`, `ENABLE_ETA_R3_SHADOW`, `ENABLE_GEOCODE_NOMINATIM_FALLBACK`.

**DOWÓD definitywny** (kontrola = flaga ETAP4 honoruje patch; leak nie):
```
conftest-stripped flags.json; test sets const=False; decision_flag returns:
  ENABLE_GEOCODE_VERIFICATION_ENFORCE   -> True  [LEAK]  *** runs ON despite test-OFF ***
  ENABLE_OBJ_COMMITTED_PICKUP_PENALTY   -> True  [LEAK]  *** runs ON ***
  ENABLE_R6_SOFT_PEN_CAP                -> True  [LEAK]  *** runs ON ***
  ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY  -> True  [LEAK]  *** runs ON ***
  ENABLE_BEST_EFFORT_POS_SOURCE_KEY     -> True  [LEAK]  *** runs ON ***
  ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY -> True [LEAK] *** runs ON ***
  ENABLE_INACTIVE_COURIER_GUARD         -> True  [LEAK]  *** runs ON ***
  ENABLE_KEBAB_KROL_DINNER_EXCLUSION    -> True  [LEAK]  *** runs ON ***
  ENABLE_PRE_SHIFT_DEPARTURE_CLAMP      -> False [ETAP4-control] ok: OFF as test intended
REVERSE leak (flags.json=False + const=True, unstripped): (none)
```
**Read-path potwierdzony** (grep — wszystkie flags-json-first, więc leak BITES na ścieżce decyzji):
- `geocoding.py:577` `C.flag("ENABLE_GEOCODE_VERIFICATION_ENFORCE", C.ENABLE_…)` — HARD geokod gate.
- `dispatch_pipeline.py:3402` + `route_simulator_v2.py:1231` `C.decision_flag("ENABLE_OBJ_COMMITTED_PICKUP_PENALTY")` — scoring/feasibility.
- `dispatch_pipeline.py:4230` `C.flag("ENABLE_R6_SOFT_PEN_CAP", False)` — R6 soft cap.
- `feasibility_v2.py:843-844` `getattr(C,…) or C.flag(…)` — **podwójny czyt: patch const=False jest CICHO nadpisany przez OR-clause flags.json=True**.
- `dispatch_pipeline.py:6750` `C.flag("ENABLE_BEST_EFFORT_POS_SOURCE_KEY", default=True)` — bucket pos_source (rodzina equal-treatment!).
- `courier_resolver.py:848`, `auto_proximity_classifier.py:616`, `dispatch_pipeline.py:4439` — filtr floty / klasyfikator / wait-reject.

**Czy CICHO kłamie dziś?** Testy istniejące dla tych flag (`test_n5s2_committed_pickup_penalty.py:150` patchuje `C.decision_flag` lambdą; `test_r1_wave_scoped_directionality.py:47-51` patchuje const **I** lambdę; `test_kk_dinner_exclusion_v2.py`/`test_geocode_negative_cache` podają `flags={...}` dict) — **OMIJAJĄ** złamany idiom = implicite potwierdzają, że deweloperzy WIEDZĄ, że patch-const nie działa. Więc dziś brak CONFIRMED false-green w tych konkretnych testach, ALE: (i) kontrakt conftest:299-303 jest fałszywy dla 63 flag; (ii) każdy test używający UDOKUMENTOWANEGO idiomu (`monkeypatch.setattr(common,"ENABLE_X",False)`) dostaje zły wynik bez ostrzeżenia; (iii) ścieżka OFF flag bez testu jest NIETESTOWALNA standardowym idiomem.

**Fix #9 (audyt 28.06) NIEKOMPLETNY:** dorzucił do ETAP4 tylko 3 (`ALWAYS_PROPOSE_ON_SATURATION`, `R_PACZKI_FLEX`, `PLN_QUALITY_AWARE`; common.py:133-139). **Zostało 60+.** is_patched=partial.

---

## (c) ZIELONE/xfail NA NIEAKTUALNYCH FIXTURE'ach (utrwalają zły/stary stan)

### F-B19-03 (klasa E, P2) — v316 whole-file xfail chowa **16/30 padających asercji demote**
`test_proposal_selection_v316.py` = **script-runner** (`def main()`+`sys.exit(0 if ok else 1)`, brak `def test_*`) → conftest `_is_script_style`→subprocess → `_KNOWN_XFAIL_SCRIPTS` (conftest.py:53-56) xfailuje CAŁY plik na exit≠0.
**Uruchomiłem runner wprost — realny wynik 14/30 PASS, exit=1.** 16 padających to DOKŁADNIE asercje demote:
`❌ informed order preserved · ❌ blind last · ❌ pre_shift+empty demoted · ❌ post-V326 boost blind nadal demoted · ❌ blind+empty na końcu · ❌ post-cascade …`.
**Dlaczego padają:** test asERTuje STARĄ (sprzed 22-24.06) szeroką politykę demote no_gps/pre_shift; **equal-treatment ją wywróciła** (decyzja Adriana „NIE demote"). conftest reason wprost: „golden V3.16 demote sprzeczny z equal-treatment … Asercje demote do aktualizacji = osobny temat". → Zamiast (a) przepisać test do kontraktu equal-treatment lub (b) usunąć martwy demote, ZAPARKOWANO jako xfail. **Czytający zielony pytest NIE widzi 16 padających asercji ani sprzeczności polityk.**

### F-B19-04 (klasa K, P2) — `_demote_blind_empty` ŻYWO wołany + flaga ON, ale wypatroszony przez equal-treatment; ZERO zielonego testu na BIEŻĄCY inwariant
- `dispatch_pipeline.py:5934` `feasible = _demote_blind_empty(feasible, order_id)` — **LIVE na ścieżce assess**.
- `ENABLE_NO_GPS_EMPTY_DEMOTE` = env-default „1" = **True** (common.py:1099, absent w flags.json).
- ALE `_is_demotable_blind_empty` (dispatch_pipeline.py:2466-2477) **wyklucza no_gps (l.2473) i pre_shift (l.2475)** gdy equal-treatment ON → funkcja demotuje już TYLKO `none`/blank pos_source (rzadkie). = **kod częściowo-martwy** (główny przypadek wycięty), żywo-wołany, z flagą ON, BEZ zielonego testu pilnującego AKTUALNEGO carve-out (no_gps/pre_shift NIE demote, none demote). Jedyny test (v316) jest xfail i asERTuje STARE. **Brak kanonicznego testu equal-treatment-demote = luka oracle przy rodzinie pozycja-równość (A6 R1/grupa3, łatana ≥4×, wraca).** dedup→demote/equal-treatment.

### F-B19-06 (klasa H, P2) — floor testowany TYLKO do committed/ready; brak testu shift_start → leak plan_recheck NIEWIDOCZNY dla oracle
- `test_floor_pickups_at_birth_2026_06_24.py` testuje `plan_recheck._floor_pickups_to_committed` (floor→**committed**, l.5/33/45/53/62/78).
- `test_gps_free_anchor.py:103` `test_committed_pickup_anchor_when_no_events` (kotwica→**committed**).
- **ŻADEN test nie asERTuje floor pickup ≥ shift_start w plan_recheck** (re-grep „shift_start" w tych plikach = brak asercji floor-do-startu).
→ Zielony suite daje FAŁSZYWE zapewnienie „floor działa", podczas gdy **R4 (A6 grupa6): 13/17 powierzchni bez shift_start-floor, leak `plan_recheck` #5 odclampowuje co 5min** — to dziura, którą oracle z definicji nie złapie (testy PINUJĄ wąską definicję committed/ready). Dokładnie wzorzec „floor tylko committed" z briefu. dedup→preshift-floor-R4.

### F-B19-05 (klasa N, P3) — `test_reconcile_dry_run` wewnętrznie niespójny, xfail chowa stałą 10≠prod 25
Plik CZĘŚCIOWO zaktualizowany: l.178-183 `budget=25` + l.202 asERTuje 25 (zgodne z prod „MAX_RECONCILE_PER_CYCLE=25", common.py:1053), ALE l.359-360 **wciąż** `assert len(pu_events)==10` / `touch…==10`. → exit≠0 → `_KNOWN_XFAIL_SCRIPTS` xfailuje CAŁY plik (conftest reason: „test asertuje 10, prod=25") **maskując stałą 10 zamiast ją naprawić**. Rozsyp progu w samym teście.

---

## (d) xfail/skip UKRYWAJĄCE realny defekt

| Test | Typ | Co ukrywa | Werdykt |
|---|---|---|---|
| `test_proposal_selection_v316` | script xfail (whole-file) | 16/30 asercji + sprzeczność demote↔equal-treatment | **DEFEKT ukryty** (F-B19-03) |
| `test_reconcile_dry_run` | script xfail (whole-file) | stała 10≠25 w samym teście | **DEFEKT ukryty** (F-B19-05) |
| `test_v319d_read_integration` | script xfail | base_sequence passthrough 12/14 | pre-existing, łagodne |
| `test_daily_stats_presnapshot` | script xfail | gspread brak w venv dispatch | środowiskowe (legit) |
| `test_demote_tier_bucket_p4.py:62` `test_offmode_preserves_demote_across_tiers` | `@pytest.mark.xfail(strict=True)` | **realny DORMANT bug: OFF-mode klucz `(_lp_tier,_orig_order)` pozwala blind+empty (tier0) wyprzedzić informed (tier1) = INWERSJA** | **DOBRA higiena** (strict=True → XPASS-fail po fixie) ale suite formalnie NIESIE nienaprawioną inwersję selekcji (F-B19-10, klasa I) |
| `test_obj_food_age_bug5.py:108,178` | `xfail(strict=False)` | food-age inflight (Faza 2 w toku) | świadome WIP |
| `test_v325_step_a_r02` / `_step_c_r04` | module-level `pytest.skip` | legacy V3.25 roster-coupled | martwy legacy (klasa K — kandydat usunięcia) |
| `test_scoring_scenarios.py:24` | module skip | legacy NameError | martwy legacy |
| ~30× `skipif(not _has_lgbm/scipy/model/gspread)` | env-gate | brak modeli/bibliotek w venv | legit (env), ALE „zielony" bez modelu = 0 dowodu na ML-ścieżce |

### F-B19-09 (klasa E+L, P3) — `test_13` NIEHERMETYCZNY w baseline (jeden z 2 RED)
`test_working_override_2026_06_01.py:197-199`: `now_w = datetime.now(ZoneInfo("Europe/Warsaw"))`; asercja `23:59` odpala TYLKO `if now_w.hour < 23`. Zależy od ZEGARA + żywego grafiku → flaky pass/fail. **NIE oznaczony `@pytest.mark.nonhermetic`** mimo że conftest.py:361 zarejestrował marker DOKŁADNIE na to („test zależny od żywego stanu (OSRM/zegar/flagi)"). Niehermetyczny test w baseline = trwały szum oracle.

---

## (overall) F-B19-08 (klasa E, P2) — EROZJA ORACLE dla ETAP-4
1. **Baseline trwale 2-RED** → sesje internalizują „2 fail = norma" (CLAUDE.md/MEMORY już to robią: „baseline 10→1 tylko time-flaky"). Nowa regresja dająca 3. fail jest trudna do odróżnienia od „znanego baseline" bez ręcznego diffu.
2. **Granulacja whole-file** ~60 script-runnerów: conftest mapuje exit-code→pass/fail dla CAŁEGO pliku (conftest.py:141-161). Regresja 1 z N asercji wewnątrz runnera kończącego exit=0 = NIEWIDOCZNA; xfail'owany runner (4 w `_KNOWN_XFAIL_SCRIPTS`) chowa WSZYSTKIE asercje (v316=16). Realne ryzyko false-PASS: runner który drukuje ❌ ale `sys.exit(0)` (logika exit oddzielona od asercji) — wzorzec `sys.exit(0 if fail==0 else 1)` jest poprawny w próbkach (`test_kk…:275-277`, `test_carry_chain…:255-257`), ale to KONWENCJA per-plik, nie wymuszona (brak frameworku) → podatne.
3. **Leak (b)** + **blind-spot checkerów (F-B19-07)** = „flaga ON≠OFF" (protokół ETAP-4 pkt 4) nie jest gwarantowane dla 63 flag.
4. **Pinowanie starych/wąskich fixtures (c)** = „PEŁNA regresja zielona" może być zielona przy NIEZGODNYM z bieżącą polityką stanie (demote, floor).

### F-B19-07 (klasa E, P2) — checkery higieny flag mają STRUKTURALNY blind-spot pokrywający się z leakiem
- `flag_effect_coverage_check.py:32-36,57` — zakres = **TYLKO `ETAP4_DECISION_FLAGS` (59)**, proxy „ma test efektu" = **sama nazwa flagi w tekście testu** (l.57 `k in txt`; docstring l.16-19 przyznaje „słabsze niż udowodnij flip"). Uruchomione: `54/59 (91.5%) z testem efektu`. **Te 63 przeciekające flagi decyzyjne POZA ETAP4 są NIEWIDOCZNE dla flag_effect** (docstring l.19 wprost: „Flagi decyzyjne POZA ETAP4 = osobny dług rejestru"). Czyli checker raportuje 91.5% zielono DOKŁADNIE pomijając klasę, która przecieka. Proxy name-in-text nadto przeszacowuje (nazwa w komentarzu/imporcie liczy się jak „test efektu").
- `flag_doc_coverage`: 79/125 grandfathered (real 36.8%).
→ Trzy checkery (orphan/doc/effect) NIE pokrywają „flaga decyzyjna poza ETAP4, której efekt-OFF nietestowalny przez leak". To samo-spójny system raportujący zielono swój własny blind-spot.

---

## TABELA POKRYCIA (co sprawdzone / czym / werdykt)

| Obszar | Plik:linia | Metoda | Werdykt |
|---|---|---|---|
| conftest izolacja flag | `tests/conftest.py:175-206, 267-331` | pełny odczyt + symulacja 1:1 | leak CONFIRMED |
| decision_flag precedencja | `common.py:46-48, 348-371` | odczyt | flags-json-first CONFIRMED |
| rejestry strip | `common.py:61-228 (ETAP4=59), 270 (NUM=25), 314 (INFRA=3), 322 (FP=4)` | import w venv | CONFIRMED rozmiary |
| leak set | `scratchpad/leak_probe.py`,`leak_proof.py` | kopia flags.json + strip + FLAGS_PATH→tmp | 63 leak / 8+ engine proven |
| read-path 8 flag | geocoding:577, dp:3402/4230/4439/6750, rs_v2:1231, feas:843, resolver:848, autoprox:616 | grep | flags-first CONFIRMED |
| testy leakujących flag | n5s2:150, r1_wave:47-51, kk_v2, geocode_neg | grep | omijają idiom (defensywne) |
| flag-doc baseline | `test_flag_doc_coverage.py`, `tools/flag_doc_coverage_check.py:42`, `tools/flag_doc_baseline.json`, git `8024705` | uruchomiony test + git | CONFIRMED stale_baseline=ENABLE_AUTO_ASSIGN |
| 3 checkery higieny | flag_doc/effect/hygiene_check | uruchomione | 36.8% doc / 91.5% effect(ETAP4-only) / 0 orphan |
| v316 xfail | `test_proposal_selection_v316.py` + conftest:53 | uruchomiony runner | 14/30, 16 ukrytych |
| `_demote_blind_empty` | dp:2466-2533, 5934, common.py:1099 | odczyt | live+ON, carve-out no_gps/pre_shift |
| reconcile xfail | `test_reconcile_dry_run.py:183/202/359` + conftest:45 | grep | wewn. niespójny 10 vs 25 |
| demote_tier xfail | `test_demote_tier_bucket_p4.py:62` | odczyt | strict xfail = dobra higiena, bug realny |
| working_override flaky | `test_working_override_2026_06_01.py:197-199` | odczyt | niehermetyczny (zegar) |
| xfail/skip inwentarz | 53 markery (grep) | grep | sklasyfikowane wyżej |

## LUKI POKRYCIA (jawnie, nie cisza)
1. **NIE uruchomiłem PEŁNEJ `pytest tests/`** świeżo (DoD read-only/95s heavy, 3 żywe sesje na repo — ryzyko szumu); użyłem baseline recon `3611/2/26/6` + targeted-rerun (flag-doc, v316, 3 checkery). Liczby 2-RED/6-xfail z recon, nie z mojego pełnego runu.
2. **NIE zweryfikowałem każdego z 53 skip/skipif** czy ukrywa defekt — spróbkowałem decyzyjne (v325 legacy, food_age, demote_tier, lgbm/scipy/gspread env-gate). Env-skip uznane legit wzorcowo, nie dowodowo per-sztuka.
3. **Brak CONFIRMED silent-false-green z leaku** — istniejące testy leakujących flag omijają idiom; leak scharakteryzowany jako złamany-kontrakt + ryzyko forward, nie jako żywe kłamstwo konkretnego zielonego testu. (Adwersarialnie: szukałem testu patchującego leakującą stałą i przechodzącego — znalazłem tylko defensywne.)
4. **Cross-repo test-suites** (panel `nadajesz_clone`, `courier_api` pytest) NIE badane — B19 = oracle dispatch_v2 dla ETAP-4 silnika. Konsola/apka mają WŁASNE oracle (poza zakresem).
5. **NIE oceniłem czy 79 grandfathered (doc) + 5 (effect) baseline są indywidualnie uzasadnione** — to dług rejestru, nie integralność runu.
6. **Whole-file false-PASS (exit=0 mimo ❌)** — wykazałem WZORZEC ryzyka (konwencja per-plik niewymuszona), nie znalazłem konkretnego runnera robiącego exit=0 mimo wewn. fail (pełny audyt ~60 plików = poza budżetem; spot-check 2 plików OK).

## DEDUP / cross-ref do A-rootów
- F-B19-02 conftest-leak ↔ A3 §3 „conftest-leak D4 (71 ENABLE_*+41 bool)" — POTWIERDZONY+ZAWĘŻONY: 63 True-leak, 8 engine-proven. Root = K1/flaga-poza-rejestrem.
- F-B19-03/04 demote ↔ A6 grupa3 „bucket pozycji 8 bliźniaków" / A2 „pozycja-równość" — test-suite NIE pilnuje rootu R1 (one selection key) → klasa wraca.
- F-B19-06 floor ↔ A6 grupa6 R4 „one earliest-pickup floor (17 powierzchni, 0 inwariant)" / MEMORY preshift-pickup-floor-audit — oracle ślepy na leak #5.
- F-B19-01 flag-doc ↔ A3 §10 pkt7 / recon §F — sprostowanie: stale_baseline (AUTON-02 udokumentował), nie new_drift.
- F-B19-07 checkery ↔ A4 §8 „E: 11 naprawionych werdyktów nigdy przez oracle" — ten sam wzór „instrument raportuje zielono swój blind-spot".

## REKOMENDACJA-DRAFT dla ETAP-4 (PLAN, nie naprawa)
Aby `pytest tests/` był godnym zaufania oracle ETAP-4: (1) **conftest strip = WSZYSTKIE flagi decyzyjne** (rozszerz o leak-set 63 albo dodaj je do ETAP4) — inaczej „flaga ON≠OFF" niegwarantowane; (2) **flag_effect zakres = leak-set, proxy „udowodnij flip" nie name-in-text**; (3) **rozbij script-runnery na per-funkcję** (granulacja) — lub przynajmniej alarm gdy xfail'owany runner chowa >1 asercję; (4) **przepisz v316 + reconcile do bieżącej polityki** (equal-treatment / 25) zamiast xfail; (5) **dodaj inwariant-test floor≥shift_start w plan_recheck** (zamyka oracle-lukę R4); (6) **oznacz `test_13` `nonhermetic`** i wytnij z baseline; (7) skurcz baseline flag-doc o `ENABLE_AUTO_ASSIGN` (zazielenia). Wszystko = ETAP-4 protokół + ACK.
