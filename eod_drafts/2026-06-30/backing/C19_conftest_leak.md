# C19 — CONFTEST FLAG-LEAK (lane C, RUNTIME-ORACLE) — backing

**Agent:** C19-conftest-leak · **Lane:** C (oracle, C9/C11) · **Tryb:** READ-ONLY · sesja tmux 2 · 2026-06-30
**HEAD:** `8024705` · venv `/root/.openclaw/venvs/dispatch/bin/python` · ZERO edycji/flip/restart/notify.
**Skrót:** Przyrząd-ochronny `conftest._isolate_flags_json` strippuje z testowej kopii `flags.json` TYLKO
flagi zapisane w `ETAP4_DECISION_FLAGS ∪ FLAGS_JSON_NUMERIC_OVERRIDES ∪ TEST_ISOLATED_INFRA_FLAGS`.
**62 decyzyjno-kształtne klucze `ENABLE_/USE_` (json=True, fallback=False) PRZEŻYWAJĄ strip → test myślący że OFF biegnie prod-ON.** „Naprawione 257d315" = TYLKO 3 nazwane flagi; klasa otwarta. `R6_SOFT_PEN_CAP` (4. flaga z seedu) wciąż przecieka i NIE MA żadnego testu. B19 (`test_baseline_is_not_stale`) czerwony = `ENABLE_AUTO_ASSIGN` (udokumentowany dziś commitem HEAD, wciąż w doc-baseline).

---

## 0. INSTRUMENT POD TESTEM + DRUGA METODA

**Instrument (claim):** conftest.py docstring (`_isolate_flags_json` :269-303, `_stripped_flags_copy` :175-206):
> „test mający stałą modułu OFF i tak dziedziczy żywy flags.json (prod-True)" — to ma być NAPRAWIONE; testy
> „muszą dalej sterować zachowaniem przez patch stałej modułu". Strip = `for _k in ETAP4_DECISION_FLAGS: d.pop(_k)`
> (conftest.py:**307**) + NUMERIC (:309) + INFRA (:311); bliźniak dla subprocess-runnerów :190/:194/:198.

**Mechanizm precedencji (zweryfikowany w kodzie):** `common.flag(name, default)` = `load_flags().get(name, default)`
(common.py:**46-48**); `decision_flag(name)` = `load_flags().get(name, globals().get(name, False))` (:**361**).
→ **klucz obecny w flags.json WYGRYWA z literałem callsite I ze stałą modułu.** Więc każdy decyzyjny klucz,
który PRZEŻYJE strip, zwraca w teście wartość prod z flags.json, nie stałą-OFF.

**Druga, niezależna metoda (lane C):**
1. **Recompute** strip-setu z `common.*` (3 frozensety), odjęcie od `flags.json`, niezależne policzenie
   „survivor ∧ json≠fallback" → `scratchpad/c19_leak_oracle.py` (2× determinizm, identyczny wynik).
2. **Faithful runtime demo** — replikacja fixture'u: `common.FLAGS_PATH=<stripped tmp>`, `_flags_cache=None`,
   potem `C.flag(k,False)` / `decision_flag(k)` — DOKŁADNIE to co robi `_isolate_flags_json`.
3. **Realny pytest** — `test_flag_doc_coverage` + `test_flag_effect_coverage` + `test_etap4_flag_unification`
   + `test_flag_registry_f3` (17 testów) → B19 czerwony LIVE, inwarianty ETAP4 zielone.
4. **Standalone checkery** `flag_doc_coverage_check.py` / `flag_effect_coverage_check.py`.
5. **git** `257d315` (deklarowana naprawa) + `8024705` (wyzwalacz B19).

---

## 1. ORACLE — wynik (RUN 1 == RUN 2, deterministyczny)

```
flags.json bool ENABLE_/USE_ keys        : 125
strip set (ETAP4 59 + NUMERIC 25 + INFRA 3) = 87   (of which in flags.json: 56)
survivors (decyzyjno-kształtne, NIE strip): 71      ← zgodne z A3 „71 ENABLE_* leak" (cross-walidacja static↔runtime)
LEAKS (survivor ∧ json≠fallback)         : 62
  silent-ON (json=True, fallback=False)  : 62       ← test myśli OFF, biegnie ON
  inversion (json=False, const=True)     : 0
  silent-ON konsumowane w module DECYZYJNYM: 24
    z tego TRULY-DECISION (nie-shadow)   : 14
    z tego shadow/probe (log-only)       : 10
```

**14 TRULY-DECISION silent-ON przecieków** (scoring/feasibility/selekcja/filtr floty — zmieniają verdict/best/pool):
`ENABLE_R6_SOFT_PEN_CAP`, `ENABLE_OBJ_COMMITTED_PICKUP_PENALTY`, `ENABLE_EXCLUDE_BY_CID`,
`ENABLE_INACTIVE_COURIER_GUARD`, `ENABLE_ZOMBIE_PICKUP_AT_GUARD`, `ENABLE_GPS_BBOX_GUARD`,
`ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY`, `ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY`, `ENABLE_NEW_COURIER_RAMP`,
`ENABLE_PLN_RESORT_WITHIN_TIER`, `ENABLE_BEST_EFFORT_POS_SOURCE_KEY`, `ENABLE_COURIER_LAST_KNOWN_POS`,
`ENABLE_LOAD_PLAN_PURE_READ`, `ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION`.
(10 shadow/probe leaks log-only = niższe ryzyko: BEST_EFFORT_*_SHADOW, MIN_DELIVERED_AT_SHADOW, PLN_OBJECTIVE_SHADOW,
PREP_VARIANCE_ANOMALY_SHADOW, REPO_COST_SHADOW, LGBM_TWOMODEL_SHADOW, FEAS_CARRY_BLIND_SHADOW, ETA_QUANTILE_SHADOW,
EARLYBIRD_T30_SHADOW.)

**Inwarianty-tripwire (wszystkie spełnione → wynik wiarygodny):** strip-set policzony z importu `common` (nie z seedu);
`json≠fallback` per-klucz; `fallback` = stała modułu jeśli bool, inaczej False (konwencja ENABLE_); 0 fikcyjnych
flag (każda z `flags.json`); 2 uruchomienia identyczne.

---

## 2. RUNTIME DEMO — 4 flagi z seedu (faithful fixture replication)

```
flag                                  in_strip  json  flag()  decision_flag()  WERDYKT
ENABLE_R6_SOFT_PEN_CAP                 False     True  True    True             LEAK(ON)      ← przecieka
ENABLE_PLN_QUALITY_AWARE              True      True  False   False            isolated(OFF) ← naprawione 257d315
ENABLE_ALWAYS_PROPOSE_ON_SATURATION  True      True  False   False            isolated(OFF) ← w ETAP4
ENABLE_R_PACZKI_FLEX                 True      True  False   False            isolated(OFF) ← w ETAP4
```

**To jest dowód, że „naprawione 257d315" było ŁATKĄ NA 3 INSTANCJE, nie naprawą KLASY.** `257d315` (Jun 29)
dodał TYLKO stałą-fallback `ENABLE_PLN_QUALITY_AWARE=False` (common.py:**245**); 3 nazwane flagi (PLN_QUALITY_AWARE +
ALWAYS_PROPOSE_ON_SATURATION + R_PACZKI_FLEX) są w `ETAP4_DECISION_FLAGS` (common.py:**137-139**, blok komentarza
„#9 conftest-leak fix" :133-136) → strippowane → izolowane. **`R6_SOFT_PEN_CAP` (4. flaga z tego samego seedu)
oraz 61 innych zostały pominięte.**

---

## 3. R6_SOFT_PEN_CAP — CZYSTY POTWIERDZONY PRZYPADEK (zero proxy)

| Atrybut | Wartość | Anchor (świeży) |
|---|---|---|
| flags.json | **True** (prod ON) | `flags.json` |
| stała modułu | `ENABLE_R6_SOFT_PEN_CAP = False` | `common.py:784` |
| w ETAP4? | **NIE** | (grep całego krotki :61-227) |
| strippowane przez conftest? | **NIE** | survivor |
| konsument decyzyjny | `... if C.flag("ENABLE_R6_SOFT_PEN_CAP", False) else None` (cap kary R6 do `R6_SOFT_PEN_CAP_FLOOR=-2000`) | `dispatch_pipeline.py:4230` |
| test odwołujący się do flagi | **ŻADEN** (`grep -rln ENABLE_R6_SOFT_PEN_CAP tests/` = 0) | — |
| efekt | KAŻDY test ścieżki R6-soft-pen biegnie **z capem (ON)**, autor zakładając stałą-False sądzi że **bez capa (OFF)** → regresja w ścieżce bez-capa NIEWIDOCZNA | — |

`R6_SOFT_PEN_CAP` przecieka jednocześnie przez 3 sita: (1) conftest strip (poza ETAP4), (2) `flag_effect_coverage`
gate (zakres = TYLKO ETAP4 → strukturalnie niewidoczna), (3) jest tylko „świadomym długiem" w `flag_doc_baseline.json:76`.
**Triple-gap.**

---

## 4. B19 — `test_flag_doc_coverage::test_baseline_is_not_stale` (CZERWONY LIVE)

**pytest:** `FAILED ... stale_baseline = ['ENABLE_AUTO_ASSIGN']` (1 failed / 16 passed, 5.56s).
**Standalone tool:** `flag_doc_coverage_check.py` → „⚠ baseline do sprzątnięcia (1): ENABLE_AUTO_ASSIGN" (RC=0,
bo main() wraca 1 tylko na `new_drift`, nie na `stale`).

**Łańcuch przyczynowy (ground-truth):**
- `compute()` (tool:42-43): `stale_baseline = [b for b in base if b not in flags OR b in ref]`.
- `ENABLE_AUTO_ASSIGN` jest w `flag_doc_baseline.json:9` (świadomy dług „niedokumentowany").
- HEAD **`8024705`** = `docs(AUTON-02): ZIOMEK_LOGIC_REFERENCE — warstwa auto-assign` → **udokumentował AUTO_ASSIGN
  w `ZIOMEK_LOGIC_REFERENCE.md`** → `b in ref` = True → wpis stał się „stale" → ratchet słusznie krzyczy „usuń z baseline".
- **B19 to PRAWDZIWY czerwony (ratchet działa), NIE fałszywy alarm.** Ale baseline doc-coverage zaczerwienił się
  TYM SAMYM dniem przez commit dokumentujący, który nie zaktualizował `flag_doc_baseline.json` (1-liniowy fix:
  usuń `ENABLE_AUTO_ASSIGN`). Self-inflicted maintenance-lag rejestru.
- Potwierdza hipotezę RECON §F, ale precyzuje ją: NIE „flagi AUTON-02/force-recheck dorzucone" ogólnie, lecz
  KONKRETNIE `ENABLE_AUTO_ASSIGN` udokumentowany przez `8024705`.

**Skutek dla wiarygodności ETAP-4:** baseline pełnej suity = `3611 passed, 2 failed` (RECON §F) — jeden z tych 2
to właśnie B19. **Czerwony baseline = brama-regresji nie jest zielona → maskuje/normalizuje przyszłe czerwienie**
(„a, te 2 zawsze są czerwone"). To dokładnie ETAP-4 protokołu (testy bazowe ZIELONE PRZED zmianą) — naruszony.

---

## 5. INWARIANTY ETAP4 — ZIELONE (instrument działa W SWOIM ZAKRESIE)

`test_etap4_flag_unification.py` (6/6 PASS):
- `test_decision_flag_flagsjson_wins` PASS — **mechanizm przecieku jest ZAMIERZONY i przetestowany** (flags.json
  wygrywa). To znaczy: conftest strip jest JEDYNĄ siatką ochronną; system jest bezpieczny tylko tak, jak KOMPLETNE
  jest członkostwo w ETAP4. 62 decyzyjne flagi poza ETAP4 = dziura w siatce.
- `test_all_etap4_flags_have_module_const` PASS — fix `257d315` (stała PLN_QUALITY_AWARE) trzyma.
- `test_fingerprint_identical_across_process_envs` PASS — ale fingerprint też = ETAP4+EXTRA (common.py:370),
  więc te same 62 flagi są niewidoczne w parytecie cross-proces (zbieżne z A3 §7).

`flag_effect_coverage_check`: 59 ETAP4, 54 z testem (91.5%), 5 baseline, **0 new_gap (zielony)** — ale ZAKRES = TYLKO
ETAP4 (tool:18-19,32-36). Decyzyjne flagi POZA ETAP4 (R6_SOFT_PEN_CAP itd.) są EXEMPT od wymogu testu-efektu.
Zielony daje fałszywy komfort poza ETAP4.

---

## 6. DEDUP / ROOT

Wszystkie instancje zwijają się do **K1 = brak jednego źródła prawdy o flagach / N ręcznie-synchronizowanych
rejestrów.** Dodanie flagi decyzyjnej wymaga ręcznego wpisania jej do: `flags.json` (kanon wartości) **ORAZ**
`ETAP4_DECISION_FLAGS` (strip+fingerprint) **ORAZ** `flag_doc_baseline.json`/ref (doc-gate) **ORAZ**
`flag_effect_baseline.json`/test (effect-gate). Pominięcie ETAP4 → przeciek do testów + brak parytetu fingerprint.
To ta sama klasa, którą historycznie przepuściła `ENABLE_BEST_EFFORT_OBJM_R6_KEY` (komentarz
`flag_effect_coverage_check.py:9`). „257d315 naprawione" = łatka-na-instancje (3 flagi), nie fix-u-źródła.

---

## 7. TABELA POKRYCIA

| Obszar | Zbadane? | Metoda | Wynik |
|---|---|---|---|
| conftest strip `_isolate_flags_json` (:267-332) | TAK | read + faithful runtime replication | strip keyed do ETAP4∪NUMERIC∪INFRA; 62 leaks survive |
| conftest `_stripped_flags_copy` (subprocess :175-206) | TAK | read | bliźniaczy strip, ta sama luka (te same 3 frozensety) |
| `common.flag`/`decision_flag`/`load_flags`/`FLAGS_PATH` | TAK | read (:46,:361,:35,:16) | flags.json wygrywa z callsite-default i stałą |
| ETAP4_DECISION_FLAGS (59) | TAK | import + recompute | 3 seed-flagi w środku (137-139); R6_SOFT_PEN_CAP poza |
| FLAGS_JSON_NUMERIC_OVERRIDES (25) / TEST_ISOLATED_INFRA_FLAGS (3) | TAK | import | część strip-setu |
| flags.json (125 bool ENABLE_/USE_) | TAK | pełna enumeracja oracle | 71 survivors / 62 silent-ON |
| 4 seed-flagi (R6_SOFT_PEN_CAP/PLN_QUALITY_AWARE/ALWAYS_PROPOSE/R_PACZKI_FLEX) | TAK | runtime demo ×2 | 1 leak, 3 isolated |
| R6_SOFT_PEN_CAP konsument + test-coverage | TAK | grep dispatch_pipeline + tests/ | :4230 konsument, 0 testów |
| B19 test_baseline_is_not_stale | TAK | pytest + standalone + git 8024705 | CZERWONY = ENABLE_AUTO_ASSIGN stale |
| `flag_effect_coverage` gate | TAK | standalone + pytest | zielony ale zakres=ETAP4 (blind spot) |
| `test_etap4_flag_unification` (6) + `flag_registry_f3` (5) | TAK | pytest | zielone (mechanizm leak = by-design) |
| 257d315 „naprawione" | TAK | git show + runtime | PARTIAL (3/≥65) |
| **NIE: per-leak pełna mapa konsumentów poza 6 modułami** | NIE | — | „14 truly-decision" = DOLNA granica (state_machine/geocoding/panel_client/bag_state nie sklasyfikowane → KEBAB_KROL/GEOCODE_VERIFICATION_ENFORCE/PICKUP_FROM_GROUND_TRUTH/PICKUP_TIME_MIRRORS_CK/ELASTYK_CK są decyzyjne ale spadły do „other") |
| **NIE: który KONKRETNY test czyta dany leak oczekując OFF** | częściowo | tylko R6 (0) + OBJ_COMMITTED (ma test) | pełna mapa per-test = poza zakresem |
| **NIE: counterfactual — ile z 3611 testów zmienia wynik przy pełnym stripie** | NIE | — | dowód MAGNITUDY live = kontrolowany run, odroczony (DoD: zero ryzykownych runów) |
| **NIE: cross-repo izolacja flag (nadajesz_clone/panel, courier_api)** | NIE | — | granica dyspozytorni |

---

## 8. WERDYKT ORACLE

- **conftest flag-leak isolation = VOID jako ochrona klasowa** (validated tylko dla 56 strippowanych ETAP4∪NUMERIC∪INFRA;
  void dla 62 decyzyjno-kształtnych survivorów, 14 truly-decision). proxy_or_ground = **ground-truth** (deterministyczna
  konfiguracja, zero button-truth). Co flipuje: dowolny test czytający survivor-flagę przez `C.flag`/`decision_flag`
  oczekując stałej-OFF biegnie prod-ON → regresja efektu flagi niewidoczna (np. cap kary R6).
- **B19 (test_baseline_is_not_stale) = VALIDATED** — prawdziwy czerwony (ENABLE_AUTO_ASSIGN udokumentowany przez
  HEAD 8024705, wciąż w doc-baseline), nie fałszywy alarm. Co flipuje: zielsność baseline ETAP-4 = wiarygodność
  pytest jako bramy-regresji.
- **„257d315 conftest-leak NAPRAWIONE" = VOID jako domknięcie klasy** (validated dla 3 nazwanych flag; klasa otwarta:
  R6_SOFT_PEN_CAP + 61). Co flipuje: status „11 kłamiących przyrządów naprawione 29.06" — ten jeden NIE jest domknięty.
- **flag_effect_coverage gate = VALIDATED-but-scoped** — zielony, ale strukturalnie ślepy poza ETAP4.

**Czy pytest = wiarygodny oracle ETAP-4? CZĘŚCIOWO.** Wiarygodny dla 56 strippowanych flag i inwariantów ETAP4
(6/6 zielone). NIEWIARYGODNY dla 62 decyzyjno-kształtnych survivorów (biegną prod-ON niezależnie od intencji testu)
+ baseline ma 1 prawdziwy czerwony (B19) który normalizuje czerwień. **Naprawa u źródła = przenieść 14 truly-decision
leaków do ETAP4 (strip+fingerprint+effect-gate RAZEM) ALBO przekluczyć strip na „wszystkie ENABLE_/USE_ minus
jawna allowlist shadow" — i usunąć ENABLE_AUTO_ASSIGN z doc-baseline.** (PLAN, nie wykonanie — DoD.)

---

## 9. ARTEFAKTY
- `scratchpad/c19_leak_oracle.py` (read-only; stripped tmp → scratchpad, NIE dispatch_state). 2× deterministyczny.
- pytest: `test_flag_doc_coverage` 1F/2P, `test_flag_effect_coverage` 3P, `test_etap4_flag_unification` 6P, `flag_registry_f3` 5P.
- standalone: `flag_doc_coverage_check.py` (stale=[AUTO_ASSIGN]), `flag_effect_coverage_check.py` (0 new_gap, 91.5%).
