# FAZA 1 — DELIVERABLE #4: STAN DOCELOWY (8 kontraktów) + DASHBOARD ENTROPII

**DRAFT · Audyt spójności Ziomka · sesja tmux 2 · 2026-06-30 · READ-ONLY.** Synteza z 7 planów per-rodzina (`backing/F_target_R1..R7.md`) + `backing/F_entropy_dashboard.md`.

> To jest **definicja „architektonicznego ideału"** jako TWARDE KONTRAKTY + INWARIANTY (mierzalne), oraz **stały miernik zdrowia** (8 liczb entropii). Nie lista bugów — kontrakt, do którego dążymy, i licznik, który pokazuje postęp.

---

## CZĘŚĆ A — 8 KONTRAKTÓW STANU DOCELOWEGO (DESIGN §4)

Każdy kontrakt = twarde zdanie „jak MA być" + metryka (=0 lub 1) + runtime-inwariant, który tego pilnuje.

| # | KONTRAKT | Metryka celu | Runtime-inwariant (strażnik) | Dziś → Cel |
|---|---|---|---|---|
| **①** | **JEDNO źródło na regułę.** Każda reguła = dokładnie 1 moduł; wszystkie ścieżki importują (nie kopiują). | liczba kopii/regułę = **1** | RED-check: nowa powierzchnia renderu/klucza bez importu kanonu | 17 reguł >1-źródło → 0 |
| **②** | **Kontrakt warstw egzekwowany.** Reguła deklaruje warstwę (HARD/SOFT/selekcja/kanon); HARD-przed-SOFT, SOFT-nie-osłabia-HARD, selekcja-czyta-co-trzeba. | layer-violation = **0**; suita INV-LAYER-1..5 zielona | `_assert_feasibility_first` re-assert na EMIT (nie 1×); zakaz `verdict` poza L5 | 7 instancji → 0 |
| **③** | **Parytet bliźniaków z konstrukcji.** Rodzeństwo dzieli moduł albo ma golden-test parytetu (nie ręczną dyscyplinę). | twin-divergence = **0** | golden-fixture equivalence w CI (route-order, lex_qual, SLA-anchor) | ~13 rozjechanych → 0 |
| **④** | **Prawda flag.** Jeden rejestr; sonda efektywnego stanu; zero martwych/env-frozen/nieosiągalnych/maskujących. | dead-flag = **0**; 100% decyzyjnych w rejestrze; mapa sprzężeń jawna | `flag_fingerprint` pokrywa wszystkie decyzyjne; conftest-strip keyowany z rejestru | 5 dead + 112-poza-rejestr → 0 / all |
| **⑤** | **Prawda przyrządów.** Każdy shadow/monitor skalibrowany oracle ZANIM ktoś zaufa liczbie; „flip tylko na validated instrument". | void/untested = **0** przed flipem zależnym | INV-TRUTH: każdy werdykt join `gps_delivery_truth`/`decision_outcomes` + inwariant `delta≥0` uzbrojony | 25/49 niewiarygodnych → 0 |
| **⑥** | **Brak dryfu semantyki.** Display oddzielony od decision-value; pola sprzężone pisane razem. | 0 pól-decyzyjnych udających display; coupled-fields async = 0 | `eta_pickup_decision` ⊥ `eta_pickup_display`; pair-writer `(coords,address,city)` | eta 1-pole-2-role → 0 |
| **⑦** | **Kompletność cyklu życia.** Każdy trwały stan ma create/mutate/GC; zero read-with-side-effect. | 0 stanów bez GC; 0 read-side-effect | `load_plan` pure-read u źródła; recanon prune-by-status; strażnik `courier_plans.sequence` | zombie 43 + load_plan-mutate → 0 |
| **⑧** | **Koherencja.** Graf interakcji reguł ma zdefiniowaną, spójną precedencję; zero cichych inwersji; żadna reguła nie bije drugiej. | unresolved-conflict = **0** | 1 chokepoint precedencji clampów (`effective_pickup_at`); tripwire R-DECLARED; etykieta „inversion-guard" na flagach | 13 klastrów (64 par) → 0 |

**Zasada anty-entropii (rozszerzenie Przykazania #0, samo-zachowawcza):** żaden przyszły sprint NIE pogarsza żadnej z 8 metryk. Bramka „ZERO NOWYCH KOPII" na KAŻDEJ zmianie — konsoliduj, nie dodawaj. RED-checki: nowa powierzchnia renderu kolejności/ETA · nowa flaga decyzyjna poza rejestrem · nowe re-liczenie czasu-odbioru bez `available_from` · nowy klucz HARD bez decyzji-widoczności · nowy reader bez rotation/master · nowy `.txt` bez TTL · nowy próg-kopia bez nazwanej-stałej · nowy caller geometrii z `if coords:` · nowa kalibracja luzująca HARD bez outcome-join · nowy plik multi-writer bez fcntl · nowy void-claim bez świeżego grepa master-ledgera.

---

## CZĘŚĆ B — DASHBOARD ENTROPII (8 metryk, liczby DZIŚ → cel)

**Stały miernik zdrowia.** Re-run po każdej naprawie fundamentu → liczby mają spadać do 0/1. Pełne wyliczenie + dowód per metryka: `backing/F_entropy_dashboard.md` (§1-8 + §11 świeżo zmierzone 2026-06-30).

| # | METRYKA | DZIŚ | CEL | najgorętsze / dowód |
|---|---|---|---|---|
| 1 | **copy-count** (reguł z >1 źródłem) | **17 reguł** (≈90 instancji) | **0** (1 kanon/regułę) | floor w 17 powierzchniach; `available_from`=0; route-order 5 kopii/3 repa |
| 2 | **twin-divergence** (bliźniaki DIVERGED/FRAGILE) | **~13** (5 grup-kopii + 8 przyrząd/flaga/pole) | **0** | route-order **44-75 rozjazdów/dzień** (monitor, ⚠ wygasa 07-10) |
| 3 | **void-instrument** (przyrząd kłamie/proxy) | **19 VOID + 6 UNTESTED = 25/49** (→11 rootów) | **0** przed flipem | `eta_source`=0/2000, `r6_gold4_gate`=0/2000 (zmierzone); feas-carry ×3; carried-guard void |
| 4 | **dead-flag** (ON/declared, 0 konsumentów) | **5** (+112 poza rejestrem = osobna oś) | **0** | `PANEL_IS_FREE_AUTHORITATIVE`/`TRANSPARENCY_SCORING` 0-konsumentów |
| 5 | **layer-violation** (HARD w złej warstwie) | **7 instancji** (2 rooty) | **0** | geometria SOFT-only; FEAS_CARRY bypass po guardzie (latentne) |
| 6 | **unresolved-conflict** (precedencja) | **13 klastrów** (raw 64 par) | **0** | 15 silent-inversion + 14 undefined (Deliverable #2) |
| 7 | **sentinel-as-data** (sentinel jako dana) | **6 definicji · 0/1 walidator-u-ingest · ~12 sites** | **1 walidator/ingest, 0 downstream** | 🔥 **LIVE 2046+14456 zdarzeń, 8 ofiar 30.06** |
| 8 | **threshold-sprawl** (próg w N miejscach) | **10 rodzin** (≈40 sites, 3 ścieżki override) | **0 rozsypanych** | R6=35/40 ×6; czasówka=60 ×6 (1 hot / 5 bare) |

**Czytanie miernika:** wszystkie 8 są DZIŚ > cel → **wysoka entropia spójności**. Żadna nie jest 0/1. Najgorętsze fizycznie DZIŚ: **#7 sentinel** (8 ofiar/d), **#3 void** (połowa przyrządów-prawdy niewiarygodna), **#6 konflikt** (64 par). Ale to NIE 8 pożarów — **26 rootów mapuje się czysto na 8 osi**, a **K1 „brak-jednego-źródła" jest wspólnym korzeniem #1+#8+część-#2 → naprawa K1 zbija 3 metryki naraz**. R3-Prawda (void) ma najwięcej rootów (10) → metryka #3 najgęstsza.

---

## CZĘŚĆ C — 7 PLANÓW PER-RODZINA (stany docelowe — gdzie szukać detalu)

Każda rodzina anty-wzorców ma kanoniczny stan docelowy + inwarianty + plan konsolidacji w osobnym backing:

| Rodzina | Kontrakt wiodący | Stan docelowy (teza) | Backing |
|---|---|---|---|
| **R1** Jedno-źródło (A1·A2·J) | ① | 1 pakiet route-order + 1 `available_from` + 1 chain-eta; import nie kopia | `F_target_R1.md` |
| **R2** Umiejscowienie (B·C) | ② | „właściwa reguła, właściwa warstwa"; geometria→klucz selekcji + de-pile RAZEM; INV-LAYER-1..5 | `F_target_R2.md` |
| **R3** Prawda (D·E·N) | ⑤④ | 1 rejestr-prawdy-przyrządów; void=0 przed flipem; serializer-kompletność; INV-TRUTH ×6 | `F_target_R3.md` |
| **R4** Semantyka (F·L) | ⑥ | display≠decision + name≠behavior rozbrojone; SEM-kontrakty ×4 | `F_target_R4.md` |
| **R5** Stres/awaria (M·G·O) | ②⑤ | awaria GŁOŚNA/BEZPIECZNIE-PESYMISTYCZNA/ZSERIALIZOWANA (odwrócenie 3 przymiotników); 1 walidator-ingest; kalibracja-na-osi-poślizgu | `F_target_R5.md` |
| **R6** Cykl-życia/zgnilizna (H·K) | ⑦ | każdy stan create/mutate/GC; martwy kod usunięty; rejestr-cyklu-życia | `F_target_R6.md` |
| **R7** Koherencja (I) | ⑧ | graf precedencji zdefiniowany+spójny; 1 chokepoint clampów; tripwiry; INV-COH | `F_target_R7.md` |

---

## STATUS / CAVEATY
- **DRAFT do przeglądu Adriana** — cele 0/1 = stan docelowy, NIE deklaracja że osiągalne w jednym sprincie.
- **To miernik, nie naprawa** — zero flipów/edycji/restartów. Liczby DRYFUJĄ (część z Fazy B/C/D; świeżo re-zmierzone tylko `F_entropy_dashboard §11`).
- **Caveaty pełne:** `backing/F_entropy_dashboard.md §12` (dead-flag=5 to dedup nie pełny sweep; sentinel 2046+14456 z logu 30.06; twin ~13 z dolną-pewną 5; STOP na dyspozytorni).
