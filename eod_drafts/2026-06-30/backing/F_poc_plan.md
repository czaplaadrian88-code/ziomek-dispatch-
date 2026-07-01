# FAZA F — PLAN 1 PoC: „one route-order module" (root R1-A)

> **DOWÓD WYKONALNOŚCI jako PLAN — ZERO KODU.** Audyt pozostaje READ-ONLY. Ten dokument NIE pisze i NIE odpala kodu PoC. **Wykonanie kodu PoC = OSOBNY ACK Adriana + pełny protokół ETAP 0→7** (`memory/ziomek-change-protocol.md`), wydzielony mini-sprint po akceptacji targetu. Tu jest tylko: (a) szkielet docelowego modułu, (b) lista call-site'ów do przepięcia, (c) plan testu parytetu ON==OFF, (d) ryzyko + bliźniaki RAZEM + dotyk HARD.

**Sesja:** tmux 2 · **Data:** 2026-06-30 · **Tryb:** READ-ONLY · **HEAD silnika:** `8024705` (2026-06-30 10:23 UTC) · panel `nadajesz_clone`, `courier_api`, `courier_api_panelsync` — robocze drzewa nietknięte.
**Wejście:** `F_target_R1.md` (stan docelowy R1) + `B01_A1_copies_sel_route.md` + `B11_J_crossrepo.md` + `B22_J_route_time_parity.md` + `E_dedup_1_singlesource_placement.md` + `F_entropy_dashboard.md` §1-2 + **świeże greppy DZIŚ** (sekcja §6 — każdy `plik:linia` re-zweryfikowany przy HEAD `8024705`).
**Kontrakty referencyjne (DESIGN §4):** ①JEDNO-źródło/regułę(kopie=1) ③parytet-bliźniaków(divergence=0) ⑦kompletność-cyklu-życia ②kontrakt-warstw(HARD-przed-SOFT).

⚠ **Numery linii DRYFUJĄ** (≥3 sesje/dzień na repo). Wszystkie poniżej zmierzone DZIŚ — **ETAP 0 PoC MUSI re-grepować przed dotknięciem.**

---

## 0. WYBÓR ROOTU — dlaczego R1-A „one route-order module" (a nie „one selection key")

DESIGN §4 / §99 daje 2 kandydatów PoC. Porównanie na świeżych dowodach:

| Kryterium | **R1-A `one-route-order-module`** | R1-C/„one selection key" (`lex_qual`+bucket+best_effort) |
|---|---|---|
| Stan dziś | **5 kopii / 3 repa / 3 języki, BRAK importu repo↔repo** | Silnik **JUŻ UNIFIED** (`objm_lexr6.lex_qual` kanon + 5 importerów + golden; `_selection_bucket` 6 konsumentów + golden) |
| Otwarta entropia | 1 niezależny twin (`fleet_state`) + 2. producent bez inwariantów + martwa 5. kopia + 4 literały `PICKUP_MERGE_MIN` + cichy fail-soft | **1 frozen inline** (`_objm_lexr6_shadow._lex_qual:1122`) — podwójnie uśpiony (shadow OFF + post_shift OFF) |
| Dźwignia (metryki entropii) | **copy-count + twin-divergence + dead-code + layer-violation + threshold-sprawl** (5 osi) | copy-count (1 frozen) + void-instrument (1) |
| Żywy harm DZIŚ | **142 rekordy rozjazdu trasy / dziś** (monitor jsonl), 44-75 worków/dzień | LATENTNY (obie flagi OFF — rozjazd dopiero NA flipie) |
| **Deadline** | **`MONITOR_STOP_AFTER=2026-07-10` (T-10 dni)** → po dacie ZERO sieci parytetu cross-repo | brak — świadomie zamrożony pod checkpoint **03.07** (protokół C7 „G2b STOP = poprawna bramka") |
| Ryzyko najtańszego kroku | **ZEROWE** (golden harness = TEST, zmienia 0 zachowania) | przepięcie frozen ZA WCZEŚNIE łamie świadomą bramkę walidacji |

**WERDYKT:** **R1-A** = najwyższa dźwignia × **jedyny z twardym deadline'em** (10 dni) × najtańszy pierwszy krok jest **bezryzykowny** (test bajt-identyczności, nie zmiana zachowania). „One selection key" jest ~ukończony i bramkowany zewnętrznie (03.07) — forsowanie = łamanie świadomej inwersji. **PoC = R1-A.**

**Kształt PoC (dwuwarstwowy — co idzie do PIERWSZEGO ACK):**
- **PoC-MIN (rekomendowane jako 1. ACK, właściwy „dowód wykonalności"):** zbuduj **golden-fixture equivalence harness** (siatka parytetu route-order, byte-identity na wspólnym wejściu) **zastępujący wygasający monitor** + 2 bezryzykowne redukcje entropii (usuń martwą 5. kopię, zwiń `PICKUP_MERGE_MIN` do 1 importowanej stałej). To zmienia **0 zachowania** → ON==OFF trywialnie spełnione, a daje sieć parytetu PRZED 07-10.
- **PoC-TARGET (docelowy, OSOBNY pod-ACK, większe ryzyko):** wyodrębnij wspólny pakiet `route_order` (źródło = silnik), importowany przez 3 powierzchnie Python; Kotlin + (cross-venv) wiązane golden-fixture. To „one module" jako stan końcowy — bramkowane PASS-em harnessu z PoC-MIN.

Poniżej (a)-(d) opisują **docelowy moduł** (cel) oraz **harness parytetu** (dowód, że ekstrakcja jest bezpieczna i mierzalna).

---

## (a) SZKIELET DOCELOWEGO MODUŁU — sygnatury + odpowiedzialność

### A.1 Co JEST dziś źródłem (świeżo zweryfikowane)

`route_podjazdy.order_podjazdy` deklaruje się DOSŁOWNIE jako kanon — docstring: **„JEDYNE źródło kolejności"** (`route_podjazdy.py:192`), a mimo to 3+ powierzchnie re-implementują tę samą regułę:

```
route_podjazdy.py:190   def order_podjazdy(bag, plan_doc=None, plan_aware=False,
                                            trust_canon=False) -> list[tuple[str, list[str]]]
                        # RENDER kanonu: bag → [(typ∈{'pickup','dropoff'}, [order_ids zgrupowane]), ...]
                        # bundling „1 restauracja = 1 podjazd" (PICKUP_MERGE_MIN), carried-first relax

plan_recheck.py:1478    def _apply_canon_order_invariants(stops, orders_state, start_pos=None, now=None)
                        # HARD niezmienniki kolejności W DECYZJI: (1) picked_up dropoffy→front
                        # (carried-first), (2) odbiory wg committed czas_kuriera rosnąco; repair-pass
                        # „dostawa po odbiorze"; guarded relax gdy ENABLE_CARRIED_FIRST_RELAX
                        # użycia: :780 i :1582 (w decyzji / regenie kanonu)
```

→ **Dwa producenty kanonu kolejności w samym silniku** (`order_podjazdy` = render-warstwa, `_apply_canon_order_invariants` = decyzja-warstwa) — DZIŚ spójne tylko przez ręczną dyscyplinę („1:1 jak build_view" wg docstringu `:1478`), bez wspólnego ciała.

### A.2 Docelowy moduł `route_order` (kanon, źródło = silnik)

Jeden moduł = jedna implementacja reguły kolejności-jazdy. Reszta IMPORTUJE (Python) lub jest wiązana golden-fixture (Kotlin/cross-venv).

```python
# dispatch_v2/route_order.py  (NOWY — jeden kanon, źródło prawdy)

PICKUP_MERGE_MIN: int = 10           # JEDYNA definicja progu sklejania odbiorów (dziś ×3 on-host + ×1 Kotlin)

def canonical_order(
    bag,                              # lista zleceń (order_id, status, restaurant, czas_kuriera_warsaw, picked_up_at)
    plan_doc: dict | None = None,     # plan Ziomka (courier_plans.json) — opcjonalny
    *,
    start_pos: tuple | None = None,   # pozycja kuriera (carried-first relax)
    now=None,
    plan_aware: bool = False,         # podjazdy wg klastrów planu, nie podziału czasowego
    trust_canon: bool = False,        # renderuj kanon VERBATIM gdy plan pokrywa CAŁY worek
) -> list[tuple[str, list[str]]]:
    """JEDYNE źródło kolejności-jazdy. Zwraca listę stopów [(typ, [order_ids]), ...],
    typ ∈ {'pickup','dropoff'}, order_ids zgrupowane (odbiory tej samej restauracji = 1 podjazd).
    Egzekwuje HARD niezmienniki: carried-first (picked_up→front), committed-ascending,
    'dostawa po odbiorze' (repair-pass), guarded carried-first relax. Czysta funkcja:
    bez I/O, bez OSRM, bez ETA — TYLKO porządek. (Re-czasowanie/ETA robi caller per powierzchnia.)"""

def merge_pickup_runs(stops, *, time_key=None) -> list:
    """Klastrowanie odbiorów w podjazdy wg PICKUP_MERGE_MIN (dziś zduplikowane:
    route_podjazdy._plan_pickup_clusters ↔ fleet_state[:306] 'lustro')."""
```

**Granica odpowiedzialności (klucz architektoniczny — uzasadnia parytet w (c)):**
- Moduł zwraca **WYŁĄCZNIE porządek** = `[(typ, [order_ids])]`. **NIE** liczy ETA, OSRM, coords, dwell, czasówki.
- Każda powierzchnia DOKŁADA swoją prezentację NA porządku: silnik-apka `route_podjazdy` (ETA-aware render), panel `_build_route` (PlanStop + `_eta_chain` OSRM), apka-API `build_view` (predicted_at), Kotlin `buildSteps` (UI).
- Dzięki temu **parytet = równość PORZĄDKU** (typ + zgrupowane order_ids), a różnice ETA/coords per-powierzchnia są LEGALNE (nie liczone jako rozjazd). To czyni byte-identity osiągalnym.

**Pochodne (zostają cienkimi wrapperami nad kanonem):**
- `route_podjazdy.order_podjazdy(...)` → `return route_order.canonical_order(...)` + lokalny ETA-render.
- `plan_recheck._apply_canon_order_invariants(...)` → `route_order.canonical_order(...)` (lub współdzielony rdzeń niezmienników) — **to jest noga HARD/decyzja** (patrz (d)).
- `fleet_state._build_route(...)` → `route_order.canonical_order(...)` (import cross-repo) + panelowy `_eta_chain`/PlanStop.

---

## (b) LISTA CALL-SITE'ÓW DO PRZEPIĘCIA (świeży grep, `plik:linia`, HEAD `8024705`)

**Legenda warstwy:** `DECYZJA/HARD` = wpływa na kanon/feasibility silnika · `RENDER` = tylko prezentacja (konsola/apka/UI).

| # | Powierzchnia | `plik:linia` (świeże) | Rola dziś | Warstwa | Akcja przepięcia |
|---|---|---|---|---|---|
| **P1** | silnik render | `dispatch_v2/route_podjazdy.py:190 order_podjazdy` | render kanonu (THE „jedyne źródło" wg docstr) | DECYZJA-blisko (karmi apkę+konsolę przez import) | wrapper → `route_order.canonical_order` |
| **P2** | silnik decyzja | `dispatch_v2/plan_recheck.py:1478 _apply_canon_order_invariants` (użycia `:780`, `:1582`) | HARD niezmienniki kolejności w decyzji/regenie | **DECYZJA/HARD** | współdzielony rdzeń z `route_order` (RAZEM z P1) |
| **P3** | **2. PRODUCENT kanonu** | `dispatch_v2/panel_watcher.py:436 _save_plan_on_assign` (zapis `:510 plan_manager.save_plan`) | zapisuje `plan.sequence` **VERBATIM, BEZ `_apply_canon_order_invariants`** (potwierdzone: brak `canon` w ciele 436-512) + placeholder coords `(0,0)` `:474/:486/:496` | **DECYZJA/HARD (layer-violation)** | albo wołać `route_order.canonical_order` przy zapisie, albo zlikwidować na rzecz gwarantowanego recanon-on-write — **RAZEM z guard (0,0)** |
| **P4** | apka-API importer | `courier_api/courier_orders.py:1116-1118 build_view` (import `:38`, fallback `:672 _plan_stop_sequence`, `:1137`) | **IMPORTUJE** `route_podjazdy.order_podjazdy` (drop-in `ENABLE_APP_ROUTE_FROM_CONSOLE=1` ON) — zdrowy importer; **cichy fail-soft** `:38-41` (`print`, nie alert) → lokalna kopia `_plan_stop_sequence` gdy import padnie | RENDER (+ latentny twin na fail) | przepiąć import na `route_order`; **fail-soft → fail-LOUD** (alert) |
| **P5** | **konsola twin** | `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:395 _build_route` (własny `_eta_chain:250`, klaster `:306`, `_hhmm_to_min:35`) | **NIEZALEŻNA KOPIA — 0 importu** `route_podjazdy`/`dispatch_v2` route (grep: jedyne wystąpienie = komentarz) | RENDER | import cross-repo `route_order` (lub golden-fixture jeśli import niewykonalny) |
| **P6** | **MARTWA 5. kopia** | `scripts/courier_api_panelsync/courier_orders.py:558 build_view` (`_plan_stop_sequence:366`) | nieserwowana: live entry `panel_sync.py` (WorkingDirectory panelsync) **NIE importuje `courier_orders`/`build_view`** (grep ∅) | dead-code | **USUNĄĆ** (po ETAP-0 import-graph proof) |
| **P7** | apka Kotlin | `RouteLogic.kt:27 buildSteps` + bundling `restaurantKey`/`PICKUP_MERGE_MIN:54` (**OFF-HOST** — źródła brak na tym hoście, tylko `courier_app_version.json`) | render serwera + WŁASNY bundling | RENDER (off-host) | **golden-fixture parity** (nie import — inny język/host) |
| **P8** | front konsoli | `nadajesz_clone/panel/frontend-shared/src/features/coordinator/Ops13Console.tsx:186 PICKUP_MERGE_MIN=10` (`:190` merge) | TS literał progu | RENDER | golden-pin stałej (build-time) |

**Literały `PICKUP_MERGE_MIN=10` (census on-host, świeży grep — kod, nie docy):**
`route_podjazdy.py:30` · `fleet_state.py:88` · `Ops13Console.tsx:186` = **3 on-host** + `RouteLogic.kt:54` off-host = **4 definicje**; `courier_api` dziedziczy przez import (bez własnego literału). Parytet DZIŚ = **komentarz** („= fleet_state").

**Bliźniaki klastrowania odbiorów (RAZEM z `merge_pickup_runs`):** `route_podjazdy._plan_pickup_clusters:57` ↔ `fleet_state[:306]` (komentarz „Lustro route_podjazdy").

---

## (c) PLAN TESTU PARYTETU ON==OFF — golden / bajt-identyczność

### C.1 Czym JEST parytet (zakres bajt-identyczności)

**Przedmiot równości = PROJEKCJA PORZĄDKU**, nie pełny payload renderu:
```
proj(surface_output) := [ (typ, sorted(order_ids)) for stop in stops ]
```
Równość: `proj(order_podjazdy(X)) == proj(_build_route(X)) == proj(build_view-route(X))` — **bajt-identyczna** lista krotek. ETA / coords / dwell / czasówka per-powierzchnia **wyłączone z porównania** (legalnie różne — patrz granica (a)). To czyni byte-identity osiągalnym i sensownym (rozjazd KOLEJNOŚCI = bug; rozjazd ETA = nie).

### C.2 Korpus wejść (wspólny, kanoniczny)

1. **Replay z żywego monitora:** `dispatch_state/ziomek_time_route_monitor.jsonl` (1659 rek., **142 dziś**) — każdy rekord ma wspólne wejście (bag+plan) i obie projekcje. To DAJE realny korpus rozjazdów (monitor już je łapie) — odtworzyć jako fixture.
2. **Syntetyczne edge-case'y (z reguł kanonu):** carried-first (picked_up→front), 2× ta sama restauracja (bundling), committed-ascending, plan pokrywa CAŁY worek (`trust_canon`), plan NIE pokrywa (fallback czasowy), worek pusty, 1 stop, `start_pos=None`, relax ON/OFF.
3. **Zatrute coords `(0,0)`** (most do P3/sentinel M) — kanon musi dać deterministyczny porządek mimo placeholdera.

### C.3 Test ON==OFF (flaga `ENABLE_ROUTE_ORDER_CANON_MODULE`)

PoC wprowadza ścieżkę modułu ZA flagą, OBOK legacy (inline). Test bramkujący:
```
dla każdego X w korpusie:
    OFF: legacy = order_podjazdy_legacy(X)           # dotychczasowa ścieżka inline
    ON:  nowy   = route_order.canonical_order(X)      # nowy moduł
    assert proj(nowy) == proj(legacy)                 # BAJT-IDENTYCZNIE
```
- **OFF ≡ ON na CAŁYM korpusie** = dowód „ekstrakcja nie zmienia zachowania" (kontrakt ekstrakcji refaktorowej). Jakikolwiek `!=` = STOP (ekstrakcja zmieniła semantykę — niedozwolone).
- **Golden frozen:** zamroź oczekiwane `proj(...)` dla korpusu w `tests/golden/route_order_corpus.json`; CI silnika I panelu czyta TEN SAM plik (jedno źródło oczekiwań).

### C.4 Parytet CROSS-REPO (zastępuje wygasający monitor — istota PoC-MIN)

```
# uruchamiany w CI OBU repo (silnik + panel), na wspólnym golden corpus:
assert proj(route_order.canonical_order(X)) == proj(fleet_state._build_route(X))      # konsola
assert proj(route_order.canonical_order(X)) == proj(courier_orders.build_view-route(X)) # apka-API
# Kotlin P7: golden corpus eksportowany jako JSON → test instrumentalny apki porównuje buildSteps(X)
```
- To jest **realny** kontrakt parytetu (`twin-divergence=0` dowiedziona TESTEM), w przeciwieństwie do DZIŚ: dwie **rozłączne** suity (`tests/test_route_podjazdy_trust_canon.py` w silniku — jego jedyna wzmianka o konsoli to **komentarz docstring** `:7 „apka == konsola == kanon"`, NIE asercja; `panel/backend/tests/test_fleet_route.py` — **0 referencji** do `order_podjazdy`/`route_podjazdy`). Golden kłamie „parytet TESTEM".
- **Nie dodaje 2. monitora** (bramka „zero nowych kopii") — ZASTĘPUJE wygasający `ziomek_time_route_monitor.py:386` (`MONITOR_STOP_AFTER=2026-07-10`, `/etc/systemd/system/ziomek-time-route-monitor.service:16`) testem CI bez daty wygaśnięcia.

### C.5 Tripwire runtime (po ekstrakcji)

Drugi inwariant (kontrakt ⑦): **żaden zapisany `courier_plans.json.sequence` nie omija kanonu** — strażnik na wzór `tools/carried_first_guard.py` mierzy nawrót (P3 objęty). Łączony z guard haversine `(0,0)` u źródła ingestu (BUG#2, sprzężony z P3).

---

## (d) RYZYKO + KTÓRE BLIŹNIAKI RAZEM + CZY DOTYKA HARD

### D.1 Mapa kompletności — bliźniaki, które MUSZĄ ruszyć RAZEM (Przykazanie #0 pkt 3)

Route-order to **rodzina**, nie pojedynczy plik. Rozbicie na pod-ruchy, każdy z kompletem bliźniaków:

| Pod-ruch | Bliźniaki RAZEM | Dlaczego razem |
|---|---|---|
| Rdzeń kanonu | **P1 `order_podjazdy` + P2 `_apply_canon_order_invariants`** | render-noga i decyzja-noga TEJ SAMEJ reguły; rozdzielenie = rozjazd render↔decyzja |
| 2. producent | **P3 `_save_plan_on_assign` + guard `(0,0)` `:474/486/496`** + tripwier `courier_plans.sequence` | zapis kanonu bez niezmienników + placeholder coords = jeden defekt (layer-violation + sentinel M) |
| Importery render | **P4 `build_view` + P5 `fleet_state._build_route` + P7 Kotlin + P8 tsx** + literały `PICKUP_MERGE_MIN` ×4 + klastry `_plan_pickup_clusters` ×2 | parytet konsola==apka==Kotlin tylko gdy wszystkie czytają 1 kanon/golden |
| Higiena | **P6 usunięcie martwej panelsync** + **P4 fail-soft→fail-loud** | dead-code + cichy rozjazd na zerwaniu importu |

**Sprzężenia z innymi rodzinami (gateują / ruszają z R1-A):**
- **R1-A' ETA-dostawy** (`chain_eta` ↔ apka OSRM ↔ konsola `_eta_chain:250`) — ta sama przyczyna (render RE-LICZY), ale to OSOBNA oś (ETA, nie porządek). PoC route-order jej **NIE** rusza (granica (a): moduł = tylko porządek). Cross-ref, nie scope.
- **R1-D rejestr flag (FAZA 0 fundamentu)** — golden/inwarianty PoC są wiarygodne tylko gdy stan flag znany; `ENABLE_CARRIED_FIRST_RELAX`, `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER`/`PANEL_FLAG_TRUST_CANON_ORDER` (3 systemy flag inaczej-nazwane). Rekomendacja: harness pinuje stan tych flag w fixture.
- **Sentinel M (`(0,0)`)** — P3 placeholder; guard ingestu = wspólny z BUG#2.

### D.2 Czy dotyka HARD? — **TAK, częściowo (kluczowe rozróżnienie)**

- **DOTYKA HARD:** P2 `_apply_canon_order_invariants` egzekwuje **TWARDE niezmienniki kolejności** używane w decyzji/regenie kanonu (`plan_recheck:780/:1582`) → karmi to, co silnik commituje. P3 `_save_plan_on_assign` pisze kanon DECYZYJNY. **Ekstrakcja rdzenia (P1+P2) i fix P3 są na ścieżce HARD/kanon** → wymagają: pełna regresja `pytest tests/` vs baseline + replay ON↔OFF + e2e + off-peak + ACK + 1 restart (ETAP 0→7 w pełni).
- **NIE dotyka HARD (display):** P4/P5/P7/P8 to RENDER (kolejność POKAZYWANA koordynatorowi/kurierowi). Ich przepięcie nie zmienia decyzji silnika — ale **musi** przejść parytet (c), bo to one mrugają 142×/dzień.
- **Niuans P3 = layer-violation:** dziś `_save_plan_on_assign` pisze `plan.sequence` BEZ niezmienników → 5-min okno „kanon bez carried-first". Naprawa (wołanie kanonu) **ZMIENIA zachowanie** (zamyka okno) → to NIE jest czysty refaktor, wymaga dowodu pozytywnego wpływu (replay), nie tylko ON==OFF.

### D.3 Ryzyko per krok (tiering)

| Krok PoC | Ryzyko | HARD? | Uzasadnienie |
|---|---|---|---|
| **Golden harness (c)** — TEST | **NISKIE** | nie (dodaje test) | zmienia 0 zachowania; ryzyko = za cienki korpus (false parity) → mityguj replay 142 dziś + edge-case'y |
| **P6 usuń martwą panelsync** | **NISKIE** | nie | grep: `panel_sync.py` nie importuje `build_view`; ETAP-0 pełny import-graph proof przed `rm` |
| **`PICKUP_MERGE_MIN`→1 stała** | **NISKIE** | nie (render) | import w silniku/panelu; Kotlin/tsx golden-pin (cross-venv import może być niewykonalny → zostają pinned) |
| **P4 fail-soft→fail-loud** | **NISKIE-ŚR** | nie | alert zamiast `print`; ryzyko = hałas alertu, nie decyzja |
| **P5 konsola import `route_order`** | **ŚREDNIE** | nie (render) | cross-repo import (osobny venv panelu); parytet (c) bramkuje; ryzyko = ścieżka importu/CI panelu |
| **P1+P2 ekstrakcja rdzenia** | **WYSOKIE** | **TAK** | noga HARD/kanon; byte-identity OFF==ON na CAŁYM korpusie OBOWIĄZKOWE + pełna regresja + replay |
| **P3 2. producent + guard (0,0)** | **WYSOKIE** | **TAK** | zmienia zachowanie (zamyka okno bez-inwariantów); replay „pozytywny wpływ"; sprzężony sentinel |
| **P7 Kotlin** | **ŚREDNIE** | nie | off-host; tylko golden-fixture parity (brak importu); wymaga build/test apki osobno |

### D.4 Rekomendacja kolejności (PoC-MIN przed deadline 07-10)

```
1. Golden harness (c) + korpus replay        [NISKIE, zastępuje monitor PRZED 07-10] ← 1. ACK
2. P6 usuń martwą panelsync                  [NISKIE, copy-count 5→4]
3. PICKUP_MERGE_MIN → 1 stała                [NISKIE, threshold-sprawl 4→1]
4. P4 fail-soft → fail-loud                   [NISKIE-ŚR]
   ───────────── powyżej = PoC-MIN: 0 zmiany zachowania silnika, sieć parytetu żywa ─────────────
5. P5 konsola import (lub golden-bound)      [ŚREDNIE, twin-divergence→0]
6. P1+P2 ekstrakcja rdzenia route_order      [WYSOKIE/HARD — pełny ETAP 0→7, osobny pod-ACK]
7. P3 2. producent + guard (0,0)             [WYSOKIE/HARD — replay pozytywnego wpływu]
```
Kroki 1-4 są **bezpieczne i czasowo-krytyczne** (deadline monitora). Kroki 6-7 są **HARD** i idą osobnym pod-ACK po PASS harnessu.

---

## 5. CO TEN PLAN ŚWIADOMIE **NIE** ROBI (read-only, uczciwość pokrycia)

1. **Nie pisze ani nie odpala kodu.** Brak `.bak`, brak `py_compile`, brak flipa, brak restartu, brak `git`, brak `--notify`. Wszystko = OSOBNY ACK + ETAP 0→7.
2. **Nie dowodzi runtime że `proj(order_podjazdy) ≡ proj(_build_route)` bajtowo DZIŚ** — to robi dopiero harness (c) na żywym korpusie (Faza C oracle / wykonanie PoC). Monitor pokazuje, że rozjazdy ISTNIEJĄ (142 dziś), nie ich rozkład.
3. **Kotlin `RouteLogic.kt` poza hostem** — `find` nie znalazł źródeł (tylko `courier_app_version.json`). P7 = realny twin, ale jego przepięcie/golden wymaga repo apki (poza tym workspace). Jawnie oznaczone.
4. **Numery linii dryfują** — wszystkie z HEAD `8024705` / 2026-06-30; **ETAP 0 PoC re-grepuje** (Przykazanie #0).
5. **R1-A' (ETA-dostawy) i selekcja (R1-C) poza scope tego PoC** — granica (a): moduł = TYLKO porządek. Cross-ref, nie double-count.
6. **STOP na dyspozytorni** — zero Mailek/Papu.

---

## 6. ŚWIEŻO ZMIERZONE DZIŚ (grep 2026-06-30, HEAD `8024705` — weryfikacja, nie seed)

| Co | Wynik | plik:linia |
|---|---|---|
| `order_podjazdy` def („JEDYNE źródło") | sygnatura `(bag, plan_doc, plan_aware, trust_canon)` | `route_podjazdy.py:190` |
| `_apply_canon_order_invariants` def + użycia | def `:1478`, użycia `:780`,`:1582` | `plan_recheck.py` |
| `_save_plan_on_assign` woła kanon? | **NIE** (zapis `plan.sequence` verbatim, brak `canon` w 436-512) | `panel_watcher.py:436`, save `:510` |
| placeholder `(0,0)` w save-path | 3 trafienia | `panel_watcher.py:474/486/496` |
| `fleet_state._build_route` importuje route_podjazdy? | **NIE** (jedyne wystąpienie = komentarz; własny `_eta_chain:250`) | `fleet_state.py:395` |
| `courier_api` efektywna flaga route | `ENABLE_APP_ROUTE_FROM_CONSOLE=1` (drop-in ON) | `courier-api.service.d/podjazdy.conf:2` |
| `courier_api` import fail-soft | `print(...)`, nie alert; fallback `_route_podjazdy=None` | `courier_orders.py:38-41` |
| panelsync `build_view` żywe? | **NIE** — `panel_sync.py` nie importuje `courier_orders` (grep ∅) | `courier_api_panelsync/courier_orders.py:558` |
| `MONITOR_STOP_AFTER` | **`2026-07-10`** (T-10 dni) | `/etc/systemd/system/ziomek-time-route-monitor.service:16` |
| monitor jsonl — żywe rozjazdy | **1659 rek., 142 dziś** | `dispatch_state/ziomek_time_route_monitor.jsonl` |
| `PICKUP_MERGE_MIN=10` literały on-host | 3 (`route_podjazdy:30`, `fleet_state:88`, `Ops13Console.tsx:186`) | + Kotlin `:54` off-host |
| golden suity rozłączne? | engine test: 1 ref = **komentarz** `:7`; panel test: **0** ref do order_podjazdy | `test_route_podjazdy_trust_canon.py` / `test_fleet_route.py` |

---

**Artefakt:** `/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-30/backing/F_poc_plan.md`
**Status:** PLAN (read-only). **Wykonanie kodu PoC = OSOBNY ACK Adriana + ETAP 0→7.**
