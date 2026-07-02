# L10 — JAKOŚĆ DECYZJI: hindsight-optimum · objective-alignment · kanon→kod

**Pas:** PION 1 (AUDYT 2.0). **Tryb:** READ-ONLY wobec produkcji — czytane pliki/grep/read-only replay na kopiach logów; zero edycji .py/.json/flag/serwisów. Jedyny zapis = ten plik.
**Data:** 2026-07-02. **Autor:** sesja L10.
**Relacja do sąsiadów:** `HINDSIGHT_v0.md` (ta sama teczka) zbudowała działający prototyp benchmarku dla 2026-06-28 i JAWNIE oddelegowała v1 (solver/online/rozdział decyzja-vs-wykonanie) tutaj. Ten dokument: (1) domyka DESIGN v1 + niezależnie kwantyfikuje blokery Zadania 1; (2) dostarcza NET-NOWY pomiar objective-alignment (Zadanie 2); (3) NET-NOWY kanon→kod (Zadanie 3). Nie dubluję liczb v0.

**Legenda dowodu:** `[GT]` ground-truth fizyczny (gps_delivery_*) · `[proxy]` button-truth / model / log · `[hip]` hipoteza z lektury.

---

## TL;DR (3 zdania)
Hindsight-benchmark jest WYKONALNY i już wstępnie policzony (v0: ~20% jazdy i 8/8 twardych breachy dnia do uniknięcia samą alokacją) — dane wejściowe 94-98% kompletne, OR-Tools 9.15 + OSRM (5000/5001) żywe; realny blok to fikcja pozycji (tylko **7% próbek floty to prawdziwy GPS**) i fizyczny GT na **12%** zleceń. Funkcja celu, którą Ziomek optymalizuje (geometria: dyst/obc/kier/czas-w-worku), **fizycznie ledwo przewiduje wynik**: Spearman(score, r6) = −0,11 (nieistotne po usunięciu sentineli), a AGREE≈OVERRIDE (breach 13,3% vs 12,0%) — czyli dziś NIE MA żadnej północnej gwiazdy „czy jesteśmy najlepsi", i dlatego hindsight jest potrzebny. Kanon łamie SAM SIEBIE w ≥3 miejscach bez wykonywalnego testu — najostrzej K-M: §4:86 „no-GPS/pre-shift ZAWSZE równo" vs żywy kod z FAR-veto −1000 dla pre-shift 30-60 min przed zmianą (świadomie zostawiony).

---

## ZADANIE 1 — HINDSIGHT-BENCHMARK (feasibility + design v1)

### 1.1 Werdykt wykonalności: TAK (dane są, solver jest, proto działa)
Niezależnie zmierzone (read-only) pokrycie wejść pod pełnodniowy VRPPDTW:

| Wejście | Źródło (plik:pole) | Pokrycie (snapshot 1 dzień, n=357) |
|---|---|---|
| pickup coords | `orders_state.json` → `pickup_coords` | 97% |
| delivery coords | `orders_state.json` → `delivery_coords` | 98% |
| prep/ready | `orders_state.json` → `prep_minutes` | 100% (deklaracja) |
| committed odbiór | `orders_state.json` → `czas_kuriera_warsaw` | 99% |
| odbiór/dostawa (fakt) | `picked_up_at` / `delivered_at` | 98% / 98% |
| geometria+wynik razem | — | **94%** |
| trajektorie floty | `fleet_position_history.jsonl` (cid/lat/lng/bag_size/pos_source/shift_end/tick_ts) | 26 kurierów, 15989 próbek, mediana 570/kuriera, zakres 06-26→07-01 |
| dwell restauracji | `restaurant_dwell.json` (arrived/departed geofence, `_source=gps_geofence`) | 1103 restauracji z realnym dwell |
| model jazdy | OSRM `localhost:5000` i `:5001` (odpowiada `code:Ok`) | żywy |
| solver | `ortools 9.15.6755` w venv dispatch | żywy |

Wolumen: ~150-264 decyzji/dzień, 11-13 kurierów/dzień (`decision_outcomes.jsonl`, 8 dni 24.06-01.07). Skala pełnodniowego statycznego VRPPDTW (≈200-260 par pickup-delivery, 11-13 pojazdów) jest wykonalna metodą dekompozycji czasowej + LNS; per-worek OR-Tools już jest w `tsp_solver.py`. **grep hindsight/optimum w korpusie audytów = 0 przed v0 — to pierwszy taki pomiar w historii projektu.** `[proxy]`

### 1.2 Definicja GAP (formalna, z zabezpieczeniem przed ogrywalnością)
GAP = (metryka FAKT) − (metryka PLAN_REF) na **jednym, wspólnym modelu jazdy** (fair — porównujemy DECYZJE, nie szum świata). Metryki: (a) suma jazdy km/kurierominut; (b) # twardych breachy R6 (>40 alarm-cap / >35 soft); (c) rozkład R6 (med/śr/p90); (d) idle. PLAN_REF = offline plan „z pełną wiedzą" pod twardymi ograniczeniami: odbiór ≥ ready, odbiór przed dostawą, R6 ≤ cap, koniec ≤ koniec zmiany, **oraz odbiór ≤ ready + limit (HARD)**.
⚠ **Kluczowy inwariant metody (z v0, MUSI zostać zapięty):** R6 „czas w worku" jest OGRYWALNY — bez limitu spóźnienia odbioru optymalizator kupuje fałszywe −36% km medianą odbioru +40 min (zimne jedzenie). Każdy benchmark/optymalizator celu MUSI pinować spóźnienie odbioru, inaczej „poprawia" metrykę kosztem klienta. To jednocześnie test dla samej funkcji celu silnika (§ Zadanie 2).

### 1.3 Trzy warstwy „optimum" (żeby GAP nie kłamał) — design v1
1. **Offline-clairvoyant (górna granica):** cały dzień naraz → GAP ZAWYŻA osiągalne (online nie zna przyszłości). To v0.
2. **Online-rolling-horizon oracle (osiągalne):** ten sam solver, ale widzi tylko okno 15-20 min do przodu (realne jasnowidzenie). Różnica (1)−(2) = **„luka informacyjna"** (nie do złapania), (2)−FAKT = **„luka algorytmiczna"** (do złapania). To jest realna „ile zostawiamy na stole".
3. **Fizyczna kotwica (na 12% z [GT]):** na zleceniach z `gps_delivery_truth.jsonl` policz GAP na fizycznym czasie dostawy zamiast button — kalibruje bias modelu.

### 1.4 BLOKERY (co dokładnie ogranicza wierność — nowe/skwantyfikowane)
- **B1 — fikcja pozycji floty (P1).** `fleet_position_history.jsonl` `pos_source`: tylko **1168/15989 = 7% to `gps`**; reszta modelowana (`last_picked_up_interp` 42%, `last_assigned_pickup` 30%, `pre_shift` 11%, `no_gps` 6%). Każdy plan referencyjny biorący pozycje z tego pliku dziedziczy tę samą fikcję pozycji, o którą walczy rodzina K5. Obejście v0 (start wszystkich w centrum) jest fair dla RÓŻNICY, ale traci realną geometrię pierwszego dojazdu. v1: liczyć GAP OSOBNO na slice `pos_source=gps` (czysty) vs cała flota (model). `[proxy]`
- **B2 — fizyczny GT na 12% (P1).** `decision_outcomes ∩ gps_delivery_truth = 229/1915 (12%)`, z tego high-conf 195. Reszta = button-truth z biasem **+2,82 min mediana** (button PÓŹNIEJ niż fizyczna dostawa) `[GT vs proxy]`. → optymistyczny fakt; GAP na R6 fizycznym możliwy tylko na 12% wolumenu.
- **B3 — kontrfaktyczny wynik nieobserwowalny.** Widzimy fizyczny wynik TYLKO jednego kuriera per zlecenie (tego, który wykonał). „Jak wyszłoby, gdyby plan_ref przypisał INNEGO" = SYMULACJA (model), nie pomiar. GAP jest więc *model-relative* — uczciwie: dolna granica luki na wspólnym modelu, nie werdykt w minutach fizycznych.
- **B4 — poślizg wykonania ≠ decyzja (temat zamknięty, ale wchodzi tu jako confounder).** v0 §6: ~połowa najgorszego ogona (R6 50-62 min) to poślizg wykonania (idle/GPS/prep/button), którego dyspozytor NIE naprawi. GAP MUSI rozdzielić decyzję od wykonania joinem z `gps_delivery_truth` + dwell, inaczej goni artefakty.
- **B5 — ready-time to deklaracja.** `prep_minutes` = deklaracja, realny moment gotowości nieznany; `restaurant_dwell` daje realny dwell (departed−arrived) tylko dla zleceń z GPS. Model gotowości v1 = dwell-empiryczny per restauracja, nie stały.

**Rekomendacja Zad.1:** v1 = (a) 7-14 dni dla przedziału ufności; (b) online-rolling-horizon oracle → rozdział luka informacyjna vs algorytmiczna; (c) slice `pos_source=gps` + kotwica fizyczna na 12% [GT]; (d) OR-Tools/LNS zamiast heurystyki v0 dla twardszej dolnej granicy; (e) na trwałe: metryka `quality-gap %` do dashboardu entropii (jest w §5 DESIGN 2.0). Zapiąć inwariant „limit spóźnienia odbioru" jako część definicji celu.

---

## ZADANIE 2 — OBJECTIVE-ALIGNMENT (czy funkcja celu celuje w to, co trzeba)

Pomiar na `decision_outcomes.jsonl` (n=1915, 8 dni) + `backfill_decisions_outcomes_v1.jsonl` (n=3242) + join `gps_delivery_truth`. NIE dubluje Track2 (wagi OK) ani kalibracji czasu — pytam: czy RANKING (score / lex_qual), po którym Ziomek wybiera, przewiduje wynik FIZYCZNY.

### 2.1 Score geometryczny ledwo przewiduje wynik fizyczny — CORE FINDING
- `scoring.py:22-58`: cel = 4 komponenty czysto geometryczne (dyst 0,30 / obc 0,25 / kier 0,25 / czas-w-worku 0,20). Zero członu waste-km trasy, zero fairness kurierów.
- **Spearman(proposed_score, r6_actual_min)** na PANEL_AGREE (proposed==wykonawca), bez sentineli: **−0,109, p=0,074 (NIEISTOTNE)**, n=267. Z sentinelami: −0,124/p=0,038 — istotność napędzają sentinele, nie sygnał. `[proxy button-truth]`
- Breach% po połowach score: **dolna 17% vs górna 10%** (r6 21,8 vs 19,3). Sygnał jest tylko w ogonie; ~połowy kandydatów cel NIE rozróżnia na wyniku, który definiuje jakość (breach). `[proxy]`
- **Interpretacja:** r6 jest zdominowany poślizgiem odbioru + dwell (temat zamknięty), których geometryczny score nie modeluje. Wniosek metodyczny (zgodny z DESIGN 1.C): jeśli cel nie przewiduje wyniku, strojenie wag jest wtórne — brakuje CZŁONU celu sprzężonego z realnym r6 (np. load-aware bufor odbioru), nie lepszych wag istniejących członów.

### 2.2 Klucz selekcji lex_qual — słaby i obciążony predyktor
- `objm_lexr6.py:29-47`: `lex_qual` = (R6-breach-max → committed-late → new-pickup-late). Klucz PIERWSZORZĘDNY = przewidywany R6-breach-max worka.
- **Spearman(predicted_r6_max_bag_min, ACTUAL r6=deliv−pickup)** = **+0,180**, p=6e-19, n=2399 — istotny ale słaby (~3% wariancji), z **biasem +33,4 min** (pred 53,8 vs akt 20,4). `[proxy]` ⚠ caveat: pred to R6 MAX worka (najgorsze zlecenie), akt to per-order — nie 1:1; ale kierunek+bias pokazują, że klucz selekcji jest luźno związany z realizacją.

### 2.3 Brak członu fairness/fleet-level mimo kanonu
- Kanon §4:89 R-FLEET-LEVEL: „optymalizuj flotę nie pojedynczy order". Cel `scoring.py` = per-kandydat. `s_obciazenie` karze bag_size pojedynczego kuriera, ale nie ma członu równości dziennej.
- Gini zleceń/kuriera/dzień = **0,17-0,30**; najbardziej obłożony kurier bierze **12-19% floty** (01.07: 43/232 = 19%). `[proxy]` To ISTNIENIE nierówności + brak mechanizmu, NIE zmierzona szkoda (najlepsi MOGĄ brać więcej). Ale R-FLEET-LEVEL nie jest zakodowany jako człon celu.

### 2.4 Sentinele zanieczyszczają zalogowany cel
- **77/1625 = 5% `proposed_score` to sentinele** (<−1e6, wartość ~−1e9 best_effort/hard-reject), min −1000000052. Trafiają do ledgera celu → psują KAŻDĄ analizę alignment/kalibracji i każde uczenie downstream (LGBM). `[proxy]` (styk z L1.1 serializer / L2.1 sentinel — tu jako defekt jakości celu, nie tylko serializacji).

### 2.5 Potwierdzenie (nie nowe): AGREE≈OVERRIDE fizycznie
AGREE breach **13,3%** (n=279, r6 med 17,2) vs OVERRIDE **12,0%** (n=1332, r6 med 18,1) — nieodróżnialne. Re-potwierdza na button-truth zamknięty werdykt „akceptacja koordynatora = zła bramka". Wzmacnia sens Zadania 1: skoro ani zgoda koordynatora, ani score nie są dobrym sygnałem jakości — hindsight jest jedyną północą. `[proxy]`

---

## ZADANIE 3 — KANON→KOD (sprzeczności + reguły bez wykonywalnego testu)

### 3.1 K-M POTWIERDZONY i POGŁĘBIONY (P1) — no-GPS/pre-shift „zawsze równo" łamane u źródła
- **Reguła:** KANON `§4:86` — „No-GPS = ZAWSZE równo | Brak GPS / pre-shift NIGDY gorszy score/feasibility/ranking/TRASA. Jedyne tolerowane = 3-4 min niedoszacowania." Flagi ON.
- **Kod:** `dispatch_pipeline.py:3297-3314 _pre_shift_gradient_penalty` — pre-shift kurier `m` min przed zmianą: `m≤30 → ∝m` (lekka), **`30<m≤60 → PRE_SHIFT_FAR_PEN = −1000` (~veto)** poza przeciążeniem (loadgov≥3,5). `dispatch_pipeline.py:2440-2475 _apply_pre_shift_equal_gate` przy `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` ON zdejmuje LEKKĄ karę, ale **ŚWIADOMIE ZOSTAWIA FAR-veto** (`if pen <= _far+0.5: return unchanged`, l.2470-2472). Więc pre-shift 30-60 min przed zmianą przy normalnym obciążeniu jest de facto WYKLUCZONY — NIE „równo".
- **Rozjazd flag (mina obserwowalności):** `common.py:316 ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY = False` (const modułu) vs `flags.json:234 true`. Runtime: `C.decision_flag(...) = True` (flags.json wygrywa, potw. read-only) — ale ktokolwiek czyta samo `common.py`/rejestr zamrożony wyciągnie „equal OFF". `[GT z odpalenia]`
- **Sam kanon to przyznaje** (`§7:151` T4 „pre-shift −20 kara wciąż w kodzie mimo EQUAL_NO_PENALTY"; `§5 C3` „jedyne tolerowane 3-4 min") — reguła §4 nie ma wyjątku na FAR-veto, a §3a/§5 sugerują load-aware wyjątek. **Kanon jest wewnętrznie sprzeczny.** Kod może mieć RACJĘ biznesową (pre-shift 60 min wcześnie → klient czeka 40-60 min = szkoda; komentarz l.2446-2450), ale wtedy TEKST kanonu §4 jest zły. `[GT kod + hip biznes]`
- **Materialność:** nie policzona wprost tu; MEMORY notuje 359 flipów/tydz no_gps+pre_shift (184+175), FAR-veto dotyka podzbioru pre-shift 30-60 min. **Rekomendacja:** (a) werdykt Adriana: czy FAR-veto zostaje (wtedy popraw §4 na „równo z wyjątkiem load-aware FAR") czy pada; (b) wykonywalny test conformance pinujący FAKTYCZNE zachowanie; (c) domknąć rozjazd const/flags.json.

### 3.2 C5 „35 dla każdego / 40 tylko alarm" — POTWIERDZONE greppem, dwa różne cape (P2)
- KANON `§5 C5:123` + `§3:63`: „**Kod dziś ma 40 per-klasa (best_effort/objm) — NIEZGODNE, do poprawy na alarm-only.**" Reguła: 40 = TYLKO tryb ALARM (auto per-decyzja gdy Strategia 1+2 niewykonalne), normalnie 35 dla wszystkich.
- **Kod POTWIERDZA rozjazd (`[GT grep]`):** współistnieją DWA sufity R6 — `common.py:815 BAG_TIME_HARD_MAX_MIN = 35` (twardy reject feasibility, `feasibility_v2.py:1109`) ORAZ `common.py:2703 BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN = 40` (komentarz l.176: „40 = Tier-3 cap-stretch"). Cap 40 na ścieżce best_effort/objm jest **STAŁY, NIE bramkowany trybem alarm** — dokładnie to, co kanon nazywa niezgodnością. Brak sygnału „alarm" (Strategia 1+2 niewykonalne) sterującego przełączeniem 35↔40; 40 jest permanentnym stretchem, nie stanem awaryjnym.
- Brak wykonywalnego testu odróżniającego „40 bo alarm" od „40 bo ścieżka best_effort". Rekomendacja: część TODO C5 (już w kanonie — bliźniaki feasibility-reject + best_effort cap + objm + O2 + relax + lex RAZEM), plus test „efektywny cap==35 gdy alarm==False dla KAŻDEJ ścieżki i klasy".

### 3.3 R-FLEET-LEVEL — reguła kanonu bez zakodowania i bez testu (P2)
KANON `§4:89` deklaruje optymalizację floty; funkcja celu `scoring.py` jest per-kandydat-greedy (patrz 2.3). Brak członu celu i brak testu, że decyzje minimalizują koszt FLOTY a nie pojedynczego zlecenia. `[GT kod]`

### 3.4 Reguły kanonu BEZ wykonywalnego testu (inwentarz — do suity 1.D)
- **MAJĄ test (golden):** POZIOM 3 trasa/kanon — `tests/test_route_order_golden.py`, `test_canon_order_invariants.py`, `test_recanon_on_write.py`, `test_recanon_on_return_p5.py`, `test_route_podjazdy_trust_canon.py` (L6.A, 13 case'ów, KONSOLA==KANON).
- **BRAK testu:** `§1` tabela rozstrzygania konfliktów (SOFT<HARD, R-DECLARED-TIME>R6, carried-first>optimal — jako WŁASNE asercje priorytetu); `§4:86` no-GPS/pre-shift równo (3.1); `§5 C5` 35/40-alarm (3.2); `§5 C7` post-shift-overrun (flaga OFF, `objm_lexr6.py:44` 4-krotka nieaktywna); `§4:88` R-NO-WASTE nie-progowa; `§4:89` R-FLEET-LEVEL (3.3).
- **Rekomendacja:** DESIGN 2.0 lane 1.D — każda reguła kanonu → wykonywalny test na korpusie golden; kanon przestaje być prozą. Priorytet: 3.1 (żywe naruszenie) → 3.2 → §1 tabela.

---

## POKRYCIE / CAVEATY
- Wszystkie pomiary 2.x na button-truth (`[proxy]`, bias +2,82 min) poza jawnie [GT]; fizyczny GT tylko 12% wolumenu.
- 8 dni danych (24.06-01.07); brak sezonowości/weekend-peak — severity może dryfować.
- Korelacje score→outcome cierpią na selective-label (widzimy wynik tylko wykonawcy) — mierzą alignment w obrębie WYBRANYCH, nie kontrfaktycznie. To jest dokładnie granica, którą hindsight (Zad.1) ma przekroczyć symulacją.
- 3.2 potwierdzone greppem (`common.py:2703` cap 40 vs `:815` cap 35); to ISTNIENIE dwóch capów bez bramki alarm, materialność (ile decyzji korzysta z 40 poza alarmem) nie policzona.
- Nie tknięto produkcji: brak edycji, restartów, flag; skrypty pomiarowe czytały tylko istniejące logi.
