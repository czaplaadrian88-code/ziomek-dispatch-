# FAZA F — STAN DOCELOWY: rodzina R2 „Umiejscowienie" (klasy B, C)

> **⚠️ DRAFT — propozycja kontraktów docelowych + zbieżny plan konsolidacji. ZERO wykonania.**
> Audyt jest READ-ONLY (sesja tmux 2). Każda zmiana kodu = osobny ACK + protokół ETAP 0→7. Ten dokument definiuje DOKĄD zmierzać, nie wykonuje.

**Data:** 2026-06-30 · **HEAD silnik:** `8024705` (working tree `.py` czysty) · **Tryb:** READ-ONLY
**Wejście:** `E_dedup_1_singlesource_placement.md` (rooty R1+R2) · `B05_C_wrong_layer` · `B20_C_hardbypass` · `B04_B_twin_asymmetry` · `B01_A1_copies_sel_route` · `C10_global_allocate` (runtime-oracle) · `D02_precedence_paths` · `ZIOMEK_COHERENCE_AUDIT_DESIGN.md` §4 (kontrakty 1-8).
**Wszystkie `plik:linia` zweryfikowane ŚWIEŻYM grepem dziś** (linie dryfują — re-grep przed cytatem jako pewnik).

---

## 0. TEZA RODZINY R2 — „właściwa reguła, ZŁE miejsce"

R2 to rodzina **umiejscowienia decyzji**: reguła ISTNIEJE, liczona jest poprawnie, ale ŻYJE W NIEWŁAŚCIWEJ WARSTWIE z 10 — przez co pod presją (scarcity, flip flagi, kolejny event) jej skutek znika albo zostaje obejściem. To NIE są „kopie reguł" (R1) ani „kto wygrywa" (R7) — to pytanie **w której z 10 warstw reguła jest egzekwowana i czy to właściwa warstwa**.

**3 przetrwałe rooty (po dedup + adwersaryjna weryfikacja):**

| Root | Sev | Klasy | Werdykt | Objaw umiejscowienia (1 zdanie) |
|---|---|---|---|---|
| **ROOT-7 `geometry-blind-selection`** | P1 | C·A1·K | CONFIRMED, źródło | Geometria policzona+serializowana, ale żyje WYŁĄCZNIE jako SOFT-kara w score (L6); ZERO w HARD-bramce (L5) i ZERO w kluczu selekcji (L7) → best_effort wyrzuca ją pod scarcity. |
| **ROOT-8 `no-global-deconflict-new-order`** | P1 | B·C·M | PLAUSIBLE, źródło | De-konflikcja floty z claim zbudowana TYLKO dla przerzutu (bliźniak B); nowe zlecenie = greedy per-event bez claim → pile-on; de-pile jest geometria-ślepy (most do ROOT-7). |
| **ROOT-9 `hard-feasibility-split-layer`** | P2 | C·I | PLAUSIBLE, źródło(latent) | Decyzje HARD i przynależność do puli przeciekają do L6 scoringu / L7 re-admisji; guard P0 jednorazowy (@5938) nie chroni stanu EMITOWANEGO. |

**Kontrakt §4 wiodący dla CAŁEJ rodziny = §4.2 „Kontrakt warstw egzekwowany"** (każda reguła deklaruje warstwę HARD/SOFT/selekcja/kanon; inwarianty runtime pilnują HARD-przed-SOFT, SOFT-nie-osłabia-HARD, selekcja-czyta-co-trzeba). Kontrakty wspierające: **§4.8** (koherencja precedencji — ROOT-9 HARD-przed-SOFT + ROOT-8 precedencja claim), **§4.3** (parytet bliźniaków — ROOT-8 przerzut↔nowe), **§4.1** (jedno źródło — stałe geometrii ROOT-7).

**🔗 SPRZĘŻENIE NIEROZŁĄCZNE (decyzja Adriana, MEMORY):** ROOT-7 (geometria w kluczu) + ROOT-8 (de-pile) **MUSZĄ wejść RAZEM** — osobno = no-op albo SZKODA. Dowód runtime: C10-oracle — flip de-pile LIVE bez członu geometrii w `lex_qual` przepchnąłby **279 propozycji spread>8 km** (do r6=73 min, spread=24 km) do Telegrama. „VALIDATED" de-pile = TYLKO oś COUNT; oś jakości geometrycznej = 🔴 VOID.

---

## 1. KONTRAKT WARSTW R2 (cross-cutting — produkt §4.2)

Stan docelowy zaczyna się od **MACIERZY REGUŁA→WARSTWA** (kontrakt §4.2 „macierz reguła→warstwa pełna"). Dla każdej reguły R2: warstwa ZAKODOWANA dziś vs warstwa POPRAWNA. Inwarianty runtime egzekwują 3 zasady: **HARD-przed-SOFT**, **SOFT-nie-osłabia-HARD**, **selekcja-czyta-co-trzeba**.

**10 warstw (kotwica):** L1 wejście · L2 geokod(HARD) · L3 early-bird(HARD) · L4 telemetria/pula(flota/GPS/ETA) · L5 `check_feasibility_v2`(HARD) · L6 scoring+~19 kar(SOFT) · L7 selekcja(SOFT/klucz) · L8 werdykt KOORD(HARD) · L9 zapis+kanon(HARD) · L10 render(konsola/apka/TG).

| Reguła | Warstwa ZAKODOWANA dziś | Warstwa POPRAWNA | Łamie zasadę | Root |
|---|---|---|---|---|
| geometria-rozjazdu → wybór zwycięzcy | **brak** (lex_qual czysto czasowy `objm_lexr6.py:29`) | L7 selekcja (oś w kluczu) | selekcja-nie-czyta-geometrii | ROOT-7 |
| geometria-rozjazdu → bramka | L6 SOFT + L5 **metric-only** (`feasibility_v2.py:504`) | L5 HARD/soft-geom cap (tier-aware) | brak HARD-bramki geom | ROOT-7 |
| geometry-blind escalation | L8 za-wąska `feasible≥2` (`dispatch_pipeline.py:6443`) | L8 obejmuje pool=0 (LUB re-rank w L7) | eskalacja martwa pod scarcity | ROOT-7 |
| global de-konflikcja (claim) | **shadow-tool** `pending_global_resweep.py:421` (no-op) + przerzut-only | L4/L7 silnik per-event (claim ledger) | de-konflikcja poza silnikiem; bliźniak nowe↔przerzut | ROOT-8 |
| dead-head repo cost na sentinel | L6 cicho połknięty (`_compute_repo_cost_km` `:2108`, `except→None`) | L5/L6 fail-loud (sentinel ≠ dana) | sentinel-jako-dana → worek „tańszy" | ROOT-8 (most M/K5) |
| re-admisja carry-NO | L7 selekcja PO guardzie (`:6266`, verdict NO→MAYBE `:6278`) | L5 feasibility (MAYBE-z-carry-regret) | SOFT-obchodzi-HARD (na flipie) | ROOT-9 |
| soon-free availability | L6 podmiana pozycji in-place (`:3623`) | L4 pula (`free_at_min` dla wszystkich populacji) | obliczenie-w-złej-warstwie | ROOT-9 |
| R9 wait>20 / ext>60 / carry / intra-gap HARD | L6 scoring-block→verdict-override (`:5637`/`5610`/`5619`/`5650`) | L5 `check_feasibility_v2` | HARD-w-warstwie-SOFT (dziś SAFE, monotonic) | ROOT-9 (P3) |
| R-RETURN-„VETO" | L5 metric-only (`feasibility_v2.py:905`) + egzekucja L9 | L5 (nazwa=HARD) lub przemianować | nazwa-≠-warstwa | ROOT-9 (P3, też I/L) |

**INWARIANTY RUNTIME docelowe (kontrakt §4.2 „suite inwariantów zielony"):**

- **INV-LAYER-1 (HARD-przed-SOFT, ROOT-9):** żaden zapis `feasibility_verdict` POZA `check_feasibility_v2` (L5) po guardzie `_assert_feasibility_first`. Dziś łamane w 1 miejscu: `dispatch_pipeline.py:6278` (readmit przepisuje NO→MAYBE za guardem). Test: grep `feasibility_verdict =` poza L5 po linii guarda = ∅.
- **INV-LAYER-2 (guard-na-emit, ROOT-9):** `_assert_feasibility_first` (def `:2480`, dziś call RAZ `:5938`) re-asserowany na KOŃCU łańcucha selekcji (po `:6301`), nie tylko @5938. Test: `top[0]@emit.feasibility_verdict != 'NO'` jako runtime-tripwire.
- **INV-LAYER-3 (selekcja-czyta-geometrię, ROOT-7):** żadna ścieżka selekcji nie wybiera kandydata o `deliv_spread_km > MAX_DELIV_SPREAD` gdy istnieje feasible kandydat geometrycznie zdrowszy — BEZ jawnego, zalogowanego powodu geometrycznego-override. Test: oracle replay (brute-OSRM permutacji vs pick) — odsetek geom-ślepych pików.
- **INV-LAYER-4 (claim-spójność, ROOT-8):** w JEDNYM ticku dispatch żaden kurier nie jest proponowanym zwycięzcą >1 nowego zlecenia, chyba że silnik jawnie zewaluował WORKA ZŁOŻONEGO (multi-order) i przeszedł feasibility (R6+geometria). Test: `g_maxpile_after` (per-kurier liczba propozycji) ograniczone; pile-on bez combined-bag-eval = naruszenie.
- **INV-LAYER-5 (SOFT-nie-osłabia-HARD, ROOT-7 guard):** człon geometrii w `lex_qual` MUSI siedzieć PO osi R6/SLA (tie-break niższego rzędu) — geometria SOFT nigdy nie może przebić HARD R6 tier-aware (35 T1/2 · 40 T3). Test: golden — kandydat z R6-breach przegrywa z kandydatem bez breach NIEZALEŻNIE od spread.

---

## 2. STAN DOCELOWY PER ROOT (twardy kontrakt + inwariant)

### ROOT-7 — `geometry-blind-selection` (P1, CONFIRMED, źródło) — klasy C·A1·K

**Defekt (zweryfikowany świeżo):**
- KLUCZ selekcji `objm_lexr6.py:29 lex_qual` = `(r6_breach, committed_late, new_pickup_late)` — **ZERO osi rozjazdu**. Wpięty 5× (best_effort, objm-d2, feas-carry, would-redirect). `ENABLE_OBJM_LEXR6_SELECT=True` + `ENABLE_BEST_EFFORT_OBJM_R6_KEY=True` (zmierzone) → selekcja czysto czasowa LIVE.
- L5 NIE rejectuje geometrii: `feasibility_v2.py:504` R1 spread>8 = TYLKO `metrics["r1_violation_km"]`; jedyna HARD-geom R7 `feasibility_v2.py:486` zneutralizowana `common.py:800 LONG_HAUL_DISTANCE_KM=99.0` (fizycznie nieosiągalne; TODO C3 z 2026-04-18 nigdy nie zrobiony) = klasa K (martwa bramka).
- L8 eskalacja `dispatch_pipeline.py:6443` wymaga `feasible≥2 AND all greedy_fallback AND all cos<0` → NIE odpala przy `pool_feasible=0` (43-45% peaku). best_effort override (`:6771`) wyrzuca ostatni ślad geometrii (`-score` 5. tie-break).
- A1-podaspekt: stała 8.0 ×2 niezależne (`feasibility_v2.py:90 R1_MAX_DELIV_SPREAD_KM` + `common.py:2280 BUNDLE_MAX_DELIV_SPREAD_KM`); `bearing_deg` ×2 (`geometry.py:30` kanon vs `wave_scoring.py:242 _bearing_deg` bajt-identyczna re-implementacja); cosine ×2 producentów.

**KONTRAKT DOCELOWY:**
> Geometria worka (deliv_spread, kierunkowy cosine) MUSI wpływać na decyzję w ≥1 warstwie DECYZYJNEJ (selekcja LUB HARD-bramka), nie tylko w SOFT-score, którą scarcity wyrzuca. Pod scarcity selekcja best_effort wybiera kandydata GEOMETRYCZNIE ZDROWSZEGO, nie tylko czasowo najbliższego.

**Forma docelowa (preferowana = re-rank, szanuje świadomą inwersję always-propose):**
1. **Człon geometrii jako oś w kanonie `lex_qual`** — tie-break PO osi R6/SLA (INV-LAYER-5). Karmiony z JUŻ-serializowanej metryki `deliv_spread_km`/`r1_avg_pairwise_cosine` (producent gotowy, `feasibility_v2:500-547`). Skutek: best_effort/objm pod scarcity wybiera geom-zdrowszego — always-propose nadal proponuje, ale LEPSZEGO kandydata (NIE wymusza KOORD → nie cofa świadomej inwersji always-propose).
2. **(Alternatywa/komplement, WYMAGA ACK)** reaktywować R7 jako tier-aware soft-geom-bramkę (usunąć `LONG_HAUL=99`) i/lub poszerzyć `geometry_blind_fallback` na pool=0. ⚠ **`geometry_blind_fallback:6453` zwraca KOORD BEZ checka `_always_propose_on()`** (D02 C8) — poszerzenie na pool=0 BIJE w świadomą dyrektywę always-propose → **NIE robić bez ACK Adriana** (re-rank z pkt.1 jest bezpieczniejszy).
3. **Jedno źródło stałych (A1):** `MAX_DELIV_SPREAD_KM` = 1 stała (scal `R1_MAX`+`BUNDLE_MAX`); `bearing_deg` = 1 (wave_scoring importuje geometry); cosine = 1 producent. Golden-test parytetu ON==OFF (zero zmiany zachowania przy konsolidacji stałej).
4. **Usunąć/naprawić martwą K:** R7 LONG_HAUL=99 — albo realny tier-aware gate, albo jawne usunięcie reguły (nie zostawiać kłamiącej „bramki która nigdy nie odpala").

**INWARIANT:** INV-LAYER-3 + INV-LAYER-5. *Metryka entropii:* `layer-violation-count` (geometria w warstwie decyzyjnej: tak/nie) → spada; `copy-count` stałej spread (2→1), bearing (2→1).

**ZALEŻNOŚĆ TWARDA:** człon geometrii w `lex_qual` można wpiąć DOPIERO PO **`objm-lexr6-unify`** (frozen `_lex_qual` shadow `:1122` → import kanonu; gated peak-verdict at-200, 03.07) — nie da się czysto dodać osi do klucza, który ma 2 rozjechane kopie (R1 family). I RAZEM z ROOT-8 (inaczej de-pile no-op).

**Luka weryfikacji (read-only):** geom-ślepy pick udowodniony PROXY (`pending_global_resweep.jsonl` case 447/484250, button-truth), NIE ground-truth permutacji OSRM. Faza C/E: brute-OSRM vs `lex_qual` pick.

---

### ROOT-8 — `no-global-deconflict-new-order` (P1, PLAUSIBLE, źródło) — klasy B·C·M

**Defekt (zweryfikowany świeżo):**
- Single-source de-konflikcji = `pending_global_resweep.py:145 global_allocate` + `:124 _tentative_assign` (claim floty). **PRZERZUT — LIVE:** `reassignment_global_select.py` importuje `global_allocate` (`ENABLE_REASSIGN_GLOBAL_SELECT=True`). **NOWE zlecenie — shadow-only:** `pending_global_resweep.py:421` warning no-op (`PENDING_RESWEEP_LIVE=False`).
- Silnik nowego zlecenia BEZ claim: `shadow_dispatcher.py:1118` buduje flotę RAZ, `assess_order`/`check_feasibility_v2` bez param claim/reserve → flota niemutowana między eventami → jeden kurier (447) proponowany 127× / 32 distinct orderów (`g_maxpile_before`=7).
- **Most do ROOT-7 (P0-A):** de-pile COUNT = ✅ validated (C10-oracle: 0 mismatch, 7/7 pile-onów rozbitych), ale JAKOŚĆ = 🔴 void (35,2% alokacji spread>8 km, 267/710 r6>40). De-pile dziedziczy ślepotę `lex_qual` 1:1.
- **Most do M/K5 (sentinele kurczą pulę → geom-ślepy pile-on tańszy):** `dispatch_pipeline.py:5690 _v328_eval_safe` catch-all wyrzuca zajętego kuriera do puli; `:2108 _compute_repo_cost_km` — `[0,0]`/`(0,0)` przechodzi `if not drop_coords` (lista/krotka truthy!) → `haversine` rzuca ValueError → `except: return None,None` → **kara dead-headu cicho znika → worek wygląda TAŃSZY**.

**KONTRAKT DOCELOWY:**
> Nowe zlecenie i przerzut dzielą JEDNĄ de-konflikcję globalną z claim (to samo `global_allocate`). Per-event selekcja nowego zlecenia respektuje ledger claim floty: kurier zaclaimowany w evencie N nie jest „wolny" w evencie N+1 w tym samym ticku. Shadow-only `pending_global_resweep` zastąpiony claim ENGINE-LEVEL (nie display-overlay).

**Forma docelowa:**
1. **Engine-level claim ledger** (L4/L7) — `process_event` mutuje flotę między eventami ticku (rezerwacja zaproponowanego kuriera), zamiast `assess_order` na niemutowanej flocie. Parytet bliźniaka: TA SAMA `global_allocate` co przerzut (kontrakt §4.3 — wspólny import, nie 2. kopia).
2. **De-pile geometria-aware (sprzężone z ROOT-7):** `global_allocate` woła `assess_order`, więc człon geometrii w `lex_qual` (ROOT-7) automatycznie uzdrawia de-pile. **Flip `PENDING_RESWEEP_LIVE` BRAMKOWANY na ROOT-7** (C10-oracle: bez geometrii = 279 złych propozycji LIVE).
3. **Sentinel fail-loud (most M/K5, w zakresie kontraktu de-konflikcji bo to MECHANIZM geom-ślepego pile-onu):** `_compute_repo_cost_km` musi fail-loud na (0,0) (sentinel ≠ dana — Lekcja #32/#81), nie cicho połykać kary dead-headu; `_v328` catch-all nie wrzuca zajętego kuriera na sentinel-pozycji do puli.

**INWARIANT:** INV-LAYER-4 (claim-spójność). *Metryka entropii:* `twin-divergence` (nowe↔przerzut de-konflikcja: wspólny import vs 2 ścieżki) → 0; `g_maxpile_after` ograniczone; sentinel-swallow (`except→None` na coords) → 0.

**Luka weryfikacji (PLAUSIBLE):** engine-level claim = DESIGN (nie zaimplementowany — `:417-421` jawnie „NIEzaimplementowane"). C10-oracle udowodnił COUNT działa + geometria void, ale nie udowodnił że claim ledger NIE wprowadzi nowych regresji (oscylacja propozycji, lag reconcile). Replay 2-dniowy ON↔OFF + dowód POZYTYWNEGO wpływu (mniej pile-onów BEZ wzrostu spread) PRZED flipem.

---

### ROOT-9 — `hard-feasibility-split-layer` (P2, PLAUSIBLE, źródło-latent) — klasy C·I

**Defekt (zweryfikowany świeżo):**
- Guard P0 `dispatch_pipeline.py:2480 _assert_feasibility_first` (fail-loud `feasibility_verdict=='NO'` w puli) wołany **RAZ `:5938`** — broni stanu @5938, NIE stanu EMITOWANEGO. Łańcuch mutacji ciągnie się do `:6301` (tiering, objm-d2, pln-resort, FEAS_CARRY_READMIT). Guard się NIE powtarza = dyscyplina-komentarza, nie runtime-inwariant.
- **C3 kanoniczny (re-admisja za guardem):** `:6266 ENABLE_FEAS_CARRY_READMIT` → `_feas_carry_readmit_pick` promuje odrzucony `verdict=NO`→`MAYBE` na `top[0]` (`:6278 _fcr_cand.feasibility_verdict="MAYBE"`), bierze wejście z `candidates` (z NO!). **Pierze TO SAMO pole, które guard sprawdza** → nawet ponowne odpalenie guarda by tego nie złapało. Dziś `ENABLE_FEAS_CARRY_READMIT=False` (latent), ale STRUKTURA = mina na flipie (#483000). Backstop (`commit_divergence`+`MIN_PROPOSE`) też wyłączony (D02 C7) → flip BEZ siatki.
- **C2 (soon-free w złej warstwie):** `:3623` podmiana pozycji+worka in-place w `_v327_eval_courier_inner` (L6 scoring), nie emisja kandydata-projektowanego w L4. `ENABLE_SOON_FREE_CANDIDATE=False` (latent).
- **C-adj-1 (HARD w L6, dziś SAFE):** R9 wait>20 (`scoring.py` zwraca `(0.0,True)`)→`:5637` + ext>60 `:5610` + carry_chain `:5619` + intra-gap `:5650` = 4 HARD-rejecty w bloku scoringu, override `MAYBE→NO`. **D02 C-OK1: defined-consistent (ok)** — monotonic (`and verdict=="MAYBE"`), PRZED guardem i PRZED złożeniem puli → poprawnie odsiewa. To split-layer (smell C), NIE bypass. P3.
- **C-adj-2 (nazwa≠warstwa):** `feasibility_v2.py:905 R-RETURN-VETO` = L5 metric-only („NIGDY nie przerywa feasibility"), realny zakaz w L9 `plan_recheck` (`ENABLE_NO_RETURN_TO_DEPARTED_PICKUP`). Nazwa „VETO" myli (też I/L). P3.

**KONTRAKT DOCELOWY:**
> Decyzje HARD (reject/admit) i przynależność do puli żyją w JEDNEJ warstwie (HARD=L5 feasibility / pula=L4). Scoring (L6) i selekcja (L7) mogą TYLKO permutować pulę — nigdy re-admitować NO ani podmieniać tożsamości kandydata. Guard feasibility-first chroni stan EMITOWANY (runtime-inwariant), nie 1 punkt czasowy. Nazwa = zachowanie.

**Forma docelowa:**
1. **INV-LAYER-1: zakaz zapisu `feasibility_verdict` poza L5 po guardzie.** Świeży grep zapisów: `:5671`(konstrukcja), `:5811`/`:5895`(pre-guard), `:6278`(**THE violation, za guardem**), `:6992`(best_effort konstrukcja). Docelowo `:6278` znika — re-admisja decydowana W L5 (feasibility zwraca `MAYBE-z-carry-regret`), nie L7-mutacja pola.
2. **INV-LAYER-2: re-assert guarda na emit** (po `:6301`). Strażnik „top[0]@emit zawsze MAYBE" — runtime-tripwire, nie komentarz. Bramkuje KAŻDY przyszły wstrzykiwacz NO (FEAS_CARRY_READMIT lub nowy).
3. **C2 → L4:** uogólniony `free_at_min` dla WSZYSTKICH populacji (pre_shift + busy-z-planem + busy-bez-planu) w `dispatchable_fleet` (L4), z rezerwacją w feasibility/selekcji — zamiast in-place podmiany w L6. Wszyscy konsumenci `dispatchable_fleet` RAZEM.
4. **C-adj-1 → L5 (P3, ostatni):** przenieść 4 HARD-rejecty z bloku scoringu do `check_feasibility_v2`. Dziś SAFE (monotonic) → niski priorytet, czysto architektoniczny dług. Nazwa `*_HARD_GATE`/`VETO` = realnie HARD w L5 (C-adj-2, słownictwo L).

**INWARIANT:** INV-LAYER-1 + INV-LAYER-2. *Metryka entropii:* `layer-violation-count` (zapisy verdict poza L5: dziś 1 za guardem → 0); `unresolved-conflict-count` (guard-vs-readmit silent-inversion → rozstrzygnięty).

**Luka weryfikacji (PLAUSIBLE/latent):** dziś bezpieczne BO flagi OFF (`FEAS_CARRY_READMIT`/`SOON_FREE`). Instrument walidujący feas_carry = 🔴 VOID (A4 #3: realny readmit 4/2816=0,14%, join w przestrzeni predykcji bez delivered_at). Przy ewentualnym re-flipie: oracle PRZED, guard nie wystarczy.

---

## 3. ZBIEŻNY PLAN KONSOLIDACJI (zależnościowo, każdy krok REDUKUJE entropię, bramka „ZERO NOWYCH KOPII")

> Zasada anty-entropii (§4 design): plan uporządkowany zależnościowo; **bramka „ZERO NOWYCH KOPII"** na każdym kroku (konsoliduj, nie dodawaj); każdy krok ściśle redukuje ≥1 metrykę entropii. Każdy krok dotykający kodu = osobny ACK + protokół ETAP 0→7 (audyt zostaje read-only).

```
                 ┌─ S1 (stałe geom 1-źródło) ─┐
                 │                              ├─→ S4 (geometria w lex_qual)
   S0 (macierz)──┼─ S2 (objm-lexr6-unify, R1) ─┘        │  [ROOT-7 rdzeń]
   [doc, read]   │                                       ├─→ S5 (de-pile geom-aware
                 ├─ S3 (INV guard na emit, ROOT-9) ─────┘     + engine claim) [ROOT-8]
                 │                                              (RAZEM z S4, GATE flip)
                 └─ S6 (C2 free_at_min L4) ─── S7 (C-adj-1→L5, P3 cleanup)
```

| # | Krok | Root | Co redukuje (entropia) | Zależy od | Bramka „zero nowych kopii" | Ryzyko/ACK |
|---|---|---|---|---|---|---|
| **S0** | Zbuduj MACIERZ reguła→warstwa (§1) jako żywy artefakt + szkielet suite inwariantów (INV-LAYER-1..5 jako testy-czerwone-na-start) | wszystkie | czyni `layer-violation-count` MIERZALNYM (dziś niewidoczny) | — | doc-only, ZERO kopii | read-only, brak ACK |
| **S1** | Scal stałe geometrii: `R1_MAX`+`BUNDLE_MAX`→1 `MAX_DELIV_SPREAD_KM`; `bearing_deg` 2→1 (wave_scoring importuje geometry); cosine 1 producent | ROOT-7 (A1) | `copy-count`: spread 2→1, bearing 2→1 | — | golden ON==OFF (zero zmiany zachowania) | niskie; protokół |
| **S2** | Dokończ `objm-lexr6-unify`: frozen `_lex_qual` shadow `:1122`→import kanonu | R1 (precond ROOT-7) | `copy-count` klucza selekcji (frozen→0); kasuje E-kłamstwo na flipie POST_SHIFT | — (gated at-200 03.07) | brak nowej kopii klucza | peak-verdict ACK |
| **S3** | INV-LAYER-2: re-assert `_assert_feasibility_first` na emit (po `:6301`) + INV-LAYER-1 strażnik zapisu verdict poza L5 | ROOT-9 | `unresolved-conflict` (guard-vs-readmit); domyka silent-inversion PRZED jakimkolwiek flipem FEAS_CARRY | S0 | nie dodaje ścieżki, dodaje strażnika | niskie (flagi OFF); protokół |
| **S4** | **ROOT-7 rdzeń:** człon geometrii jako tie-break w kanonie `lex_qual` (PO osi R6, INV-LAYER-5); poszerz/usuń martwą R7 (LONG_HAUL=99) | ROOT-7 | `layer-violation` (geometria→warstwa decyzyjna); kasuje martwą K (R7) | S1+S2 | człon z JUŻ-serializowanej metryki (zero nowego producenta) | **ACK** (dotyka selekcji LIVE); SOFT-nie-osłabia-HARD R6 |
| **S5** | **ROOT-8 (RAZEM z S4):** engine-level claim ledger (nowe=przerzut, wspólny `global_allocate`) + sentinel fail-loud (`_compute_repo_cost_km`, `_v328`) | ROOT-8 | `twin-divergence` nowe↔przerzut→0; `g_maxpile`↓; sentinel-swallow→0 | **S4 (twarde)** | wspólny import `global_allocate` (NIE 2. kopia) | **ACK + replay 2d**; flip `PENDING_RESWEEP_LIVE` GATE na S4 |
| **S6** | C2: `free_at_min` projekcja w L4 `dispatchable_fleet` dla wszystkich populacji; wycofać in-place podmianę L6 (`:3623`) | ROOT-9 | `layer-violation` (soon-free L6→L4) | S0 | jedna projekcja w L4 (nie N podmian) | ACK (flaga SOON_FREE OFF dziś — INERT do flipu) |
| **S7** | C-adj-1: przenieś 4 HARD-rejecty (R9/ext/carry/intra-gap) z L6 scoringu do L5 feasibility; nazwa=warstwa (VETO/HARD_GATE) | ROOT-9 (P3) | `layer-violation` (HARD w SOFT→L5); słownictwo L | S3 | przeniesienie, nie duplikacja | niskie (dziś SAFE monotonic); protokół |

**Sekwencja krytyczna (kolejność wymuszona zależnościami):**
`S0 → {S1, S2, S3} równolegle → S4 → S5 (RAZEM)`; `S6, S7` niezależnie (ROOT-9 hardening, dowolny moment po S0/S3).
**🔒 Bramka nieprzekraczalna:** S5 (de-pile LIVE) NIE może wyprzedzić S4 (geometria w selekcji) — C10-oracle dowiódł że osobno = 279 złych propozycji. „P0-A + P0-B RAZEM" (MEMORY).

---

## 4. WKŁAD W DASHBOARD ENTROPII (§4 — liczby dziś → cel)

| Metryka entropii | Dziś (R2) | Cel | Krok który domyka |
|---|---|---|---|
| `copy-count` (stała spread / bearing / cosine) | 2 / 2 / 2 | 1 / 1 / 1 | S1 |
| `copy-count` (klucz selekcji lex_qual) | 1 kanon + 1 frozen | 1 | S2 |
| `layer-violation-count` (geometria w warstwie decyzyjnej) | 0 (tylko SOFT-score) | ≥1 (selekcja/HARD) | S4 |
| `layer-violation-count` (zapis verdict poza L5 za guardem) | 1 (`:6278`) | 0 | S3 |
| `layer-violation-count` (soon-free L6 / HARD w L6) | 2 | 0 | S6 / S7 |
| `twin-divergence` (de-konflikcja nowe↔przerzut) | 2 ścieżki | 0 (wspólny import) | S5 |
| `unresolved-conflict-count` (guard-vs-readmit silent-inversion) | 1 | 0 | S3 |
| `dead-flag/dead-code` (R7 LONG_HAUL=99 martwa bramka) | 1 | 0 | S4 |
| `sentinel-swallow` (repo_cost (0,0) → None) | 1 | 0 (fail-loud) | S5 |

**Każdy krok S1-S7 ściśle redukuje ≥1 wiersz powyżej i nie pogarsza żadnego** (warunek zbieżności). Po domknięciu R2: geometria ma dom decyzyjny, de-konflikcja jest jedna i geom-aware, HARD nie przecieka do SOFT/L7, guard broni emisji.

---

## 5. POKRYCIE / JAWNE LUKI / co NIE jest R2 (anty-double-count)

**Zweryfikowane świeżym grepem (HEAD 8024705):** `objm_lexr6.py:29` · `feasibility_v2.py:90/486/504/905` · `common.py:800(LONG_HAUL=99)/2280/2651` · `dispatch_pipeline.py:2108/2480/3623/5637/5690/5938/6266/6278/6443` · `geometry.py:30` · `wave_scoring.py:242` · `pending_global_resweep.py:124/145/421` · flagi efektywne `flags.json` (zmierzone, nie env-default).

**Jawne luki (nie cisza):**
1. Geom-ślepy pick = PROXY-certyfikowany (button-truth `pending_global_resweep.jsonl`), NIE ground-truth permutacji OSRM → Faza C/E oracle.
2. Engine-level claim (ROOT-8) = DESIGN niezaimplementowany; replay 2d ON↔OFF nie wykonany (read-only).
3. ROOT-9 latentny (flagi OFF) — INV-LAYER-1/2 to hardening strukturalny, nie żywy fix; oracle feas_carry VOID (przy re-flipie najpierw oracle).
4. Most paczki / parcel lane — NIE sprawdzony pod własną kopią route-order/claim (granica STOP na dyspozytorni; A6 luka #2).
5. courier-app Kotlin lokalny re-sort/ETA — render serwerowy pokryty; lokalna kopia niezweryfikowana (Faza B/J).

**NIE-R2 (cross-ref, NIE double-count):**
- **C4-a floor (`pickup≥shift_start`)** + **C4-b carried-first/route-order** = render-patche klasy C4, ale ŹRÓDŁO w R4 (floor-17-powierzchni) / R2-route-order (one-route-order-module, A6 grupa 2). Tu odnotowane jako warstwa C, ale dedup → **osobne rooty** (`earliest-pickup-floor-no-chokepoint`, `one-route-order-module`). NIE re-derywuję.
- **frozen `_lex_qual` shadow / out-of-engine position-gates** = klaster R1 (one-selection-key) — S2 to precond, nie rdzeń R2.
- **SLA≠R6 anchor** = R3 (one-SLA-R6-anchor, O2 02.07). **R6=35 scatter** = N/R3.
- **Sentinele (0,0)/BIALYSTOK_CENTER** = klasa M (agent M) — tu TYLKO most K5 jako mechanizm geom-ślepego pile-onu (w zakresie kontraktu de-konflikcji ROOT-8).
- **R-KOORD-VALVES-MASKED / always-propose** = świadoma inwersja (D02 C8, B20 C5) — NIE „bug do naprawy", ale OGRANICZENIE projektu S4 (re-rank szanuje always-propose; KOORD-widening wymaga ACK).

---

## 6. HANDOFF — otwarte decyzje Adriana (przed PoC)

1. **ROOT-7 forma:** re-rank w `lex_qual` (preferowane, szanuje always-propose) vs reaktywacja R7 HARD-gate vs poszerzenie geometry_blind KOORD (to ostatnie BIJE always-propose → ACK). Rekomendacja: **re-rank + usunięcie martwej R7=99** (najmniejszy blast-radius, nie cofa świadomej inwersji).
2. **Sprzężenie S4+S5:** potwierdzić „RAZEM" (MEMORY) — flip `PENDING_RESWEEP_LIVE` GATE na geometrię w selekcji. C10-oracle = twardy dowód (279 złych propozycji bez S4).
3. **PoC kandydat z rodziny R2:** „one selection key z osią geometrii" (S2+S4) — wysoki zwrot, ale gated at-200/objm-unify; ALBO „engine claim ledger" (S5) — wymaga replay. PoC = osobny ACK + ETAP 0→7 (design pozostaje read-only).
4. **ROOT-9:** czy hardenować guard (S3) PROAKTYWNIE teraz (flagi OFF, INERT) czy dopiero przy flipie FEAS_CARRY (#483000). Rekomendacja: S3 teraz (tani strażnik, domyka minę PRZED dźwignią — zgodne z „zawsze domykaj, nie pytaj o wartość naprawy" dla zepsutego/kłamiącego, ale flip-touch = ACK).
