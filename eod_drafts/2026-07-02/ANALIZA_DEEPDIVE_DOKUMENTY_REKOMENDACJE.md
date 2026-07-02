# Analiza 14 dokumentów „Deep Dive: silnik dyspozytorski" vs stan Ziomka — rekomendacje i priorytety

**Data:** 2026-07-02 · **Źródło dokumentów:** Google Drive, folder `nootbooklm` (14 plików, wgrane 30.06)
**Metoda:** przeczytane wszystkie 14 dokumentów → audyt kodu Ziomka (18 mechanizmów, agent na repo `dispatch_v2` + `flags.json`) → audyt danych historycznych (shadow_decisions, reassignment_shadow, gps_delivery_truth, restaurant_dwell; skrypty w scratchpadzie sesji).
**Kiedy się tym zajmujemy:** PO zakończeniu napraw poaudytowych (Faza 3 audytu zunifikowanego, L3→L5→L6→L7→L8 + deploye FALA-1 za ACK). Ten dokument to spiżarnia na później, nie plan na jutro.

---

## TL;DR (prostym językiem)

Te 14 dokumentów to w ~85% **ten sam materiał w 14 wariantach** (wygenerowane przez NotebookLM/Deep Research na tych samych źródłach: Google OR-Tools, Uber Gurafu, DoorDash DeepRed, wzorce AWS, Google SRE). Realnie unikalnych pomysłów jest ok. 10.

Najważniejsze trzy wnioski po zderzeniu z kodem i danymi:

1. **Ziomek już robi większość rzeczy, które te dokumenty nazywają „dojrzałym systemem"** — shadow mode, replay/backtesting, dekompozycja ETA na składniki, fallbacki przy awarii GPS/OSRM, kill-switch, monitoring jakości decyzji. Kierunek architektoniczny (WorldState, czysty rdzeń) też się pokrywa 1:1 z naszym szkieletem F1–F7. Dokumenty **potwierdzają naszą roadmapę**, nie wywracają jej.
2. **Trzy pomysły z dokumentów trafiają dokładnie w nasze zmierzone bóle** i to są prawdziwe lewary: (a) **kalibracja optymizmu ETA pod obciążeniem** (nasz zmierzony bias: dostawa przewidywana ~18 min za wcześnie), (b) **krótki „przytrzymaj i sparuj"** dla zleceń z tej samej restauracji (mierzalnie ~10 uciekających bundli dziennie), (c) **histereza/koszt zmiany decyzji** w scoringu (top-1 proponowany kurier zmienia się dla 83% zleceń, ponad połowa zmienia się ≥3 razy — to zabije zaufanie do autonomii, jeśli tego nie ustabilizujemy).
3. **Połowa treści dokumentów NIE dotyczy naszej skali** — MIP/Gurobi na całą flotę, AWS SQS/ECS recursive scaling, własny silnik grafowy à la Gurafu, edge-based graf. My mamy ~242 zlecenia/dzień, pulę 3–12 kurierów na zlecenie i jeden serwer. Wdrażanie tego byłoby budową lotniska dla trzech dronów.

---

## 1. Jak czytać te dokumenty (uczciwa ocena jakości)

- To teksty generowane AI: eleganckie, ale **powtarzalne i miejscami niesprawdzalne** (np. szczegóły „Gurafu"/„Goldeta" Ubera są luźną parafrazą blogów; liczby typu „batching 20% → +30% throughput" bez źródła). Traktować jako **katalog wzorców do sprawdzenia u nas**, nie jako źródło prawdy.
- Sekcje o OR-Tools (NextVar/CumulVar/SlackVar, pułapka AddPickupAndDelivery) są rzetelne technicznie — ale my używamy OR-Tools tylko do kolejności stopów w worku jednego kuriera (`tsp_solver.py`), i to wystarcza.
- Sekcje AWS (SQS, ECS Fargate, depth-specifier, Backlog per Task) — dla nas nieaplikowalne w całości.

## 2. Ranking dokumentów — które są najmocniejsze i mogą być lewarami

| Ocena | Dokument (tytuł skrócony) | Dlaczego |
|---|---|---|
| ⭐⭐⭐ LEWAR | **„Architektura…: Od Modelowania Matematycznego do Stabilności Operacyjnej"** | Jedyny z pełną sekcją **anty-migotania (Flicker)**: histereza „zmień tylko gdy ≥15% lepiej", decision locking, koszt odebrania zlecenia kurierowi, okna „zamrożonego planu". Trafia w nasz zmierzony flicker 83%. Najbardziej praktyczny z całego zestawu. |
| ⭐⭐⭐ LEWAR | **„Budowa…: Deep Dive" (wariant z „10 Rekomendacji Wdrożeniowych")** | Konkretna checklista: delay-dispatch 60–90 s pod bundling, kara za wariancję, postmortem każdego nieprzydzielonego zlecenia, alerty multi-burn-rate. Połowa punktów = nasze realne luki. |
| ⭐⭐⭐ LEWAR | **„Budowa… Czasu Rzeczywistego: Deep Dive" (wariant z metryką Assignment Churn)** | Definiuje **Assignment Churn >5% = alarm** (u nas: 83%…), dashboard early-warning (stabilność decyzji / drift ETA / infra / ekonomia) i **Lower Bound przez LAP** jako miernik „ile tracimy na greedy". Gotowy szablon metryk. |
| ⭐⭐ mocny | **„Budowa…: Głęboka analiza (Deep Dive)"** | Najlepsza anatomia ETA: 4 składniki (dojazd/przygotowanie/parking/przekazanie), **nieliniowość ETA pod obciążeniem floty**, „optimistic bias". Nasz zmierzony bias −18 min to dokładnie ten rozdział. |
| ⭐⭐ mocny | **„Inżynieria…: Deep Dive w Systemy Czasu Rzeczywistego"** | Rdzeń jako czysta funkcja (snapshot świata → decyzja, zero I/O), replayability, shadow mode. **Walidacja 1:1 naszego szkieletu F1–F7 / WorldState** — nic nowego, ale potwierdza, że nasz plan to standard branżowy. |
| ⭐⭐ mocny | **„Projektowanie…: Deep Dive" (wariant z bipartite matching)** | Algorytm węgierski / min-cost flow jako „matematyczny sufit": policz optimum offline, żeby WIEDZIEĆ ile tracisz na greedy — nawet jeśli nigdy nie wdrożysz solvera live. Tanie w replayu. |
| ⭐ średni | „Architektura Systemu…: Silnik Optymalizacji i Skalowanie" | Soft bounds z eskalacją kar zamiast twardych limitów (mamy: SOFT/HARD + salvage), tuning bayesowski, symulacja offline (mamy replay). Głównie potwierdzenia. |
| ⭐ średni | „…Od modelu do skali hyper-growth" | Delayed dispatch + „batching 20% → +30% throughput" (kierunkowo słuszne), Redis na GPS, repliki odczytu — skala nie nasza. |
| ⭐ średni | „Projektowanie…: Deep Dive w Batching i Optymalizację" | Batching + penalty za złożoność trasy („trasy-potwory"), fallback na graf statyczny. Nakłada się z mocniejszymi wariantami. |
| — słabe (duplikaty) | „Budowa…: Deep Dive" (2 identyczne kopie), „Głęboka Analiza Architektoniczna", „Architektura… Czasu Rzeczywistego: Deep Dive", „Architektura…: Deep Dive w Systemy Czasu Rzeczywistego" | Powtórzenia powyższych + rozdziały AWS/hyper-scale nieaplikowalne u nas. Można nie wracać. |

## 3. Co z tych dokumentów Ziomek JUŻ MA (audyt kodu, stan na 02.07)

Pełna tabela 18 mechanizmów z plikami i flagami — u agenta audytu; tu esencja:

**✅ Mamy live i dobrze:**
- **Bundling** (worki z tej samej restauracji + co-location dostaw + cap rozrzutu p90 + centroid guard; `same_restaurant_grouper.py`, flagi `ENABLE_BUNDLE_*` ON) — dane pokazują, że ~35–60% dowiezionych zleceń dzieli worek; to nasz dominujący tryb pracy.
- **Dekompozycja ETA na składniki** — dojazd OSRM + czekanie na jedzenie (`pickup_ready_at`) + chwyć-torbę + dwell dostawy uczony per tier z 7,5 tys. rekordów (`common.py: DWELL_BY_TIER`). Dokumentowa „anatomia czasu DoorDash" = u nas zrobiona.
- **Degradacja i kill-switche** — circuit breaker OSRM + fallback haversine, kill-switch do v1, parser-degraded, GPS-fail → last-known-pos (TTL 25 min), nigdy fikcyjne (0,0). Rozdziały „graceful degradation" możemy odhaczyć.
- **Shadow mode + replay + golden testy** — ~40 narzędzi, korpus route-order 13/13, kalibracje replayem. Dokumenty opisują to jako szczyt dojrzałości; my w tym żyjemy na co dzień.
- **Eskalacja zamiast cichego porzucenia** — niedobór floty → KOORD (człowiek), salvage końca dnia. Dokumentowe „disjunctions z karą" mamy w wersji human-in-the-loop, co przy naszej skali jest LEPSZE.

**🟡 Mamy częściowo / w shadow (dokumenty popychają dokładnie tu):**
- **Load-aware ETA**: governor kar przy obciążeniu jest live, ale **rosnący z obciążeniem bufor ETA = tylko monitor shadow** (`pickup_slip_monitor`, kalibracja 29.06).
- **Delay-dispatch**: hold czasówek/early-bird live, ale **celowe ~2-min przytrzymanie pod parowanie bundli = tylko pomiar** (`pending_global_resweep` log-only).
- **Anty-flicker**: mamy pin czasu odbioru, forward-only, anty-wobble — ale **brak kosztu zmiany kuriera w samym scoringu propozycji**.
- **Kara za wariancję**: kwantyl p80 w twardej bramce R6 jest; **brak kary za niepewność w scoringu miękkim**.
- **Pętla ETA↔ATA**: logi i okresowa ręczna rekalibracja są; domykający LGBM-residual w shadow.
- **SLO jakości decyzji**: dziesiątki monitorów + świeży perf-SLO, ale **bez wspólnego error-budgetu/burn-rate**.
- **WorldState / czysty rdzeń**: udokumentowany cel (ZIOMEK_ARCHITECTURE.md), budowa w toku (L4 available_from scalona, flip za ACK).

**❌ Nie mamy (i przeważnie słusznie):**
- Globalnej optymalizacji przydziału kurier↔zlecenie (Hungarian/min-cost/MIP) — jest greedy per-zlecenie + selekcja leksykograficzna.
- Modelowania p(akceptacji) kuriera — u nas koordynator przydziela, kurier nie odrzuca ofert; nieistotne do czasu autonomii.
- Bayesowskiego auto-tuningu wag — jest offline grid-search; wystarcza.

## 4. Co mówią NASZE dane (mały audyt liczbowy, 02.07)

Skrypty: scratchpad sesji (`analyze_main.py`). Zastrzeżenia metodyczne na końcu sekcji.

| Pomiar | Wynik | Co z tego wynika |
|---|---|---|
| Skala | **~242 zlecenia/dzień** (216–275), flota ≤12, pula wykonalnych kurierów na zlecenie: **mediana 3**, p90 8 | Za mało na MIP/Gurobi — ale idealna skala na mały, dokładny solver okienkowy i na pomiar lower-bound w replayu |
| Zlecenia bez ani jednego wykonalnego kuriera | **14%** (≤1 wykonalny: 28%) | Potwierdza znany wątek „feasibility za ostra pod scarcity" (dekompozycja 57% z 30.06) — to nadal lever #1 dla autonomii |
| Bundling wykonany | **~35–60%** dowiezionych zleceń dzieliło worek (przedział zależnie od metody; propozycje: 49% do kuriera z niepustym workiem) | Bundling działa i jest masowy — dokumentowe „20% batching" dawno przekroczone |
| **Missed bundle** | **137 par** z tej samej restauracji złożonych ≤6 min od siebie w ~5 dni (~27/dzień); **37% (~10/dzień) pojechało różnymi kurierami** | Konkretny, policzalny zysk dla „przytrzymaj i sparuj" (delay-dispatch okienkowy) |
| **Flicker propozycji** (reassignment_shadow, 2104 zlecenia, mediana 11 ticków) | **83% zleceń ≥1 zmiana top-1 kuriera; 54,5% ≥3 zmiany; śr. 3,26 zmiany/zlecenie** | Scoring jest niestabilny między tickami. Dziś maskuje to koordynator-człowiek; przy auto-assign to byłby chaos. Dokumentowy próg alarmu: churn >5% |
| **Bias ETA dostawy** (join shadow×truth, n=75 — mała próbka) | Przewidujemy dostawę **~18 min za wcześnie** (mediana −17,6; p10 −46,8) | Niezależnie potwierdza kalibrację 29.06 (poślizg odbioru ~18 min pod obciążeniem). „Optimistic bias" z dokumentów = zmierzony u nas |

Zastrzeżenia: shadow_decisions loguje tylko pierwszą decyzję na zlecenie (flicker liczony z reassignment_shadow 23.06–02.07); truth ma 965 zleceń głównie do 26.06, stąd join ETA n=75; missed-bundle liczone na poziomie propozycji, część „różnych kurierów" to naturalna zmiana stanu floty między ocenami — realny potencjał to raczej 5–10/dzień niż pełne 10.

## 5. REKOMENDACJE — co warto wprowadzić, z priorytetami

Format: co to jest (po ludzku) → skutek dla Ziomka → potencjał → koszt/ryzyko → kiedy.
Wszystko wchodzi standardowo: protokół #0 (ETAP 0→7), shadow → replay z dowodem „warto + bez regresji" → flip za ACK.

### P1. Kalibracja optymizmu ETA pod obciążeniem (load-aware bufor) — 🟢 potencjał WYSOKI, w połowie zrobione
**Co:** Ziomek obiecuje czasy jak w dzień idealny; rzeczywistość pod obciążeniem jest ~18 min gorsza (głównie poślizg dojazdu po odbiór). Zamiast stałego bufora — bufor rosnący z obciążeniem floty (spokojnie ~+5, ciasny worek ~+19, wg segmentacji z kalibracji 29.06).
**Skutek:** plany przestają być „kłamliwie optymistyczne" → mniej gaszenia pożarów, feasibility podejmuje decyzje na prawdziwych czasach, rzadsze przekładanie zleceń w locie. To też warunek sensownego auto-assign (autonomia na zawyżonym optymizmie = auto-spóźnienia).
**Stan:** monitor poślizgu LIVE (`pickup_slip_monitor`, timer 22:30), segmentacja po obciążeniu zrobiona (29.06), shadow-obliczenie bufora w pipeline jest. Brakuje: flip do decyzji.
**Koszt/ryzyko:** mały kod, średnie ryzyko (bufor za duży = feasibility za ostra → więcej KOORD; wymaga replayu ON↔OFF na SLA i wolumenie KOORD).
**Dokumenty:** „Głęboka analiza" (nieliniowość ETA), wszystkie warianty „optimistic bias".

### P2. Histereza i koszt zmiany decyzji w scoringu (anty-flicker / Assignment Churn) — 🟢 potencjał WYSOKI
**Co:** dziś każdy tick liczy propozycję od zera — top-1 kurier zmienia się u 83% zleceń. Dokumenty: (a) nie zmieniaj propozycji, jeśli nowa nie jest lepsza o próg (np. 10–15% kosztu), (b) dolicz do scoringu koszt „przełączenia" już pokazanego kuriera, (c) okna zamrożenia po akceptacji.
**Skutek:** stabilne propozycje → koordynator ufa Ziomkowi (dziś zgodność 16–18%, częściowo przez migotanie), spokojniejsza konsola, a przy autonomii — twardy WARUNEK WSTĘPNY (auto-assign, który co 3 min zmienia zdanie, zdemoluje kurierów). Nowa metryka do shadow: churn propozycji, cel <15–20% na start (branżowy „alarm >5%" to poziom docelowy).
**Stan:** anty-wobble na CZASIE odbioru mamy; na WYBORZE KURIERA — nie ma nic jawnego.
**Koszt/ryzyko:** średni (dotyka scoringu = protokół pełny; uwaga na konflikt z inwersjami P-1..P-7 i regułą „lepszy kurier ma wygrywać" — histereza nie może zamrażać ewidentnie lepszych zmian; próg do skalibrowania replayem).
**Dokumenty:** „Od Modelowania Matematycznego do Stabilności" (cała sekcja 5), wariant z Assignment Churn.

### P3. „Przytrzymaj i sparuj" — okienkowy delay-dispatch pod bundling — 🟢 potencjał WYSOKI, mierzalny
**Co:** gdy przychodzi zlecenie z restauracji X, a w ostatnich/następnych ~3–6 min jest/może być drugie z X — krótki, świadomy hold (60–120 s) zanim propozycja pójdzie do koordynatora, żeby oba weszły w jeden worek. Dokumenty nazywają to „Delay Dispatch"; u nas to domknięcie istniejącego `pending_global_resweep` do trybu live w wąskim, bezpiecznym oknie.
**Skutek:** ~5–10 odzyskanych bundli dziennie (z 51 straconych par/5 dni) = mniej podwójnych kursów do tej samej restauracji = realnie zaoszczędzone kursy kurierskie w peak. Przy naszym wolumenie to 2–4% wszystkich zleceń dziennie przerobione z 2 kursów na 1.
**Stan:** pomiar shadow jest (`ENABLE_PENDING_RESWEEP` ON, `PENDING_RESWEEP_LIVE` OFF), czasówki mają własny scheduler.
**Koszt/ryzyko:** średni. Ryzyko: hold wydłuża czas pierwszego zlecenia (konflikt z R-DECLARED-TIME/35-min) → hold tylko gdy margines SLA na to pozwala i tylko dla restauracji z historyczną częstością par; werdykt Adriana potrzebny na samą zasadę „wolno chwilę przytrzymać".
**Dokumenty:** „10 Rekomendacji" (delay 60–90 s), „Batching i Optymalizacja", DeepRed we wszystkich wariantach.

### P4. Lower bound w replayu — „ile tracimy na greedy" (LAP/Hungarian OFFLINE) — 🟡 potencjał ŚREDNI-WYSOKI jako WIEDZA, zero ryzyka
**Co:** nie wdrażamy solvera live. Raz na jakiś czas w replayu liczymy na historycznym dniu optymalne przypisanie kurier↔zlecenie (algorytm węgierski / min-cost flow — przy puli 3–12 kurierów to trywialne obliczeniowo) i porównujemy z tym, co zrobił greedy+lex. Dokumenty: „bez znajomości dolnej granicy nie wiesz, ile pieniędzy zostawiasz na stole".
**Skutek:** twarda liczba „greedy traci X min SLA / Y kursów dziennie vs optimum". Jeśli X małe → temat globalnej optymalizacji zamykamy na lata z czystym sumieniem. Jeśli duże → dopiero wtedy rozmowa o solverze okienkowym (i wiemy, o co gramy). To też bezpośrednie narzędzie na root K5/K6 z audytu zunifikowanego (best-effort pile-on, greedy bez global-de-pile).
**Koszt/ryzyko:** mały (skrypt w tools/, read-only, scipy `linear_sum_assignment`), ZERO ryzyka produkcyjnego.
**Dokumenty:** „Projektowanie… Deep Dive" (bipartite matching), wariant z Lower Bound/LAP.

### P5. Kara za niepewność w scoringu miękkim (variance penalty) — 🟡 potencjał ŚREDNI
**Co:** dziś konserwatyzm kwantylowy (p80) działa w twardej bramce R6; w miękkim scoringu dwie trasy o tym samym średnim ETA są równe, nawet gdy jedna jest loteryjna (kurier bez GPS-historii, restauracja o rozstrzelonym prep, worek 4-stopowy). Dokumenty: dolicz karę rosnącą z wariancją/złożonością trasy — „kupujemy przewidywalność za 5% teoretycznej efektywności".
**Skutek:** mniej „tras-potworów" wygrywających o włos, kaskadowe opóźnienia worków rzadsze. Synergia z P1 (P1 kalibruje średnią, P5 karze rozrzut).
**Koszt/ryzyko:** średni (scoring = protokół pełny; trzeba mieć per-segment wariancję — dane z eta_calibration_log już są).
**Dokumenty:** wszystkie warianty (penalty term DeepRed).

### P6. Prep-time per restauracja do decyzji (Order Ready Time) — 🟡 potencjał ŚREDNI, w połowie zrobione
**Co:** dokumentowy sygnał #1 DoorDasha: kiedy kuchnia NAPRAWDĘ wyda jedzenie. Mamy `restaurant_dwell.json` + `prep_bias_anchor.py` w shadow (`ENABLE_PREP_BIAS_TABLE` OFF). Flip = pickup_ready_at koryguje się o systematyczny bias konkretnej restauracji.
**Skutek:** mniej kurierów czekających pod restauracją (marnowany czas floty w peak) i mniej „jedzenie stygnie, bo kurier za późno". Zysk skupiony na kilku restauracjach o dużym biasie — najpierw sprawdzić w danych, ile ich jest i jaka skala biasu.
**Koszt/ryzyko:** mały-średni (mechanika istnieje; ryzyko: bias liczony na małych próbkach per restauracja → wymaga minimalnego n).
**Dokumenty:** anatomia czasu (wszystkie warianty).

### P7. Wspólny error-budget / burn-rate na SLA (formalizacja SRE) — 🟠 potencjał NISKI-ŚREDNI, tanie
**Co:** mamy dziesiątki monitorów per-mechanika; brakuje JEDNEJ agregującej liczby: „ile % budżetu spóźnień ten tydzień już spaliliśmy i jak szybko". Dokumenty: multi-window burn-rate (szybkie spalanie = incydent; wolne = nowa heurystyka po cichu degraduje) + zasada „budżet spalony → freeze zmian w silniku".
**Skutek:** wcześniejsze łapanie „cichej degradacji" po flipach; obiektywna reguła kiedy wstrzymać wdrożenia. Dobrze współgra z naszym rytmem flag i ACK.
**Koszt/ryzyko:** mały (agregat na istniejących jsonl + próg w monitoringu).
**Dokumenty:** wszystkie warianty SRE; najlepiej rozpisany w „10 Rekomendacji" (fast/slow burn).

### P8. Dashboard „4 sygnały zdrowia decyzji" — 🟠 potencjał NISKI-ŚREDNI, tanie, po P2/P7
**Co:** jedna zakładka (mamy AI-HUB w konsoli): (1) stabilność decyzji (churn z P2), (2) zdrowie predykcji (błąd ETA↔ATA kroczący), (3) infra (wiek danych GPS, latencja decyzji p95 — perf-SLO już jest), (4) ekonomia (burn-rate z P7). Early-warning zamiast grzebania w jsonl.
**Skutek:** Adrian/koordynator widzi degradację w minutę; domyka pętlę po P1/P2/P7.

### Potwierdzenia kierunku (nie nowe zadania — już w roadmapie):
- **WorldState / czysty rdzeń / replayability** — dokument „Inżynieria…" opisuje dokładnie nasz FUNDAMENT F1/F2 i szkielet ARCHITECTURE/INVARIANTS. Kontynuować jak zaplanowano (Faza 3, L-fale). Dokumenty = zewnętrzna walidacja, że to standard, nie nasza fanaberia.
- **Soft nie osłabia hard + eskalacja kar zamiast infeasibility** — mamy (P0 + salvage + KOORD).
- **Postmortem każdego nieprzydzielonego zlecenia** — w praktyce mamy (shadow ledger + verdict-joby); ewentualnie doholować do P8.

## 6. Czego NIE wdrażać (anty-lewary przy naszej skali)

| Pomysł z dokumentów | Dlaczego NIE u nas |
|---|---|
| Pełny MIP/Gurobi/OR-Tools na przydział całej floty | 242 zlecenia/dzień, pula wykonalnych mediana 3, max flota 12. Nasz problem NIE jest obliczeniowo trudny — jest informacyjnie trudny (jakość ETA, scarcity). P4 (lower bound offline) da dowód, czy w ogóle jest co ugrać. |
| AWS SQS + ECS Fargate, recursive scaling, depth-specifier, Backlog-per-Task | Jeden serwer spokojnie domowy problem liczy; nasz bottleneck z audytu 2.0 to p50 840 ms pojedynczej decyzji (regres 2×, już w naprawach), nie równoległość. |
| Własny silnik grafowy (Gurafu/CRP/edge-based, koszty skrętów) | OSRM + uplift ruchu (fix 28.06) wystarcza dla Białegostoku. Edge-based graf to walka o sekundy w metropolii; u nas błąd robi dwell/prep/poślizg, nie geometria skrętów. |
| Transactional Outbox pełną gębą | Mamy atomic-replace + write-guard + reconciliation + events.db replay — proporcjonalne do skali. Pełny outbox = złożoność bez zmierzonego problemu. |
| Modelowanie p(akceptacji) kuriera | Kurier nie odrzuca ofert (przydziela koordynator/auto-assign). Wróci ewentualnie jako „p(override koordynatora)" przy rozwijaniu autonomii. |
| Bayesian optimization wag | Grid-search offline + kalibracja replayem wystarczą przy naszej liczbie parametrów. |

## 7. Proponowana kolejność (PO naprawach poaudytowych)

Logika: najpierw to, co poprawia PRAWDĘ danych (P1, P6), potem STABILNOŚĆ decyzji (P2), potem OSZCZĘDNOŚCI (P3), a wiedza (P4) równolegle bo tania i read-only.

1. **P4** — lower bound w replayu (read-only, można nawet w trakcie napraw jako pomiar; daje mapę zysku dla reszty)
2. **P1** — flip load-aware bufora ETA (dowód w dużej mierze zebrany; replay → ACK)
3. **P2** — histereza/churn propozycji (warunek autonomii; nowa metryka churn do shadow OD RAZU, próg potem)
4. **P6** — flip prep-bias per restauracja (mechanika w shadow gotowa)
5. **P3** — okienkowy delay-dispatch pod pary (wymaga werdyktu Adriana co do zasady holdu)
6. **P5** — variance penalty w scoringu
7. **P7 + P8** — error budget + dashboard (domknięcie pętli)

Zależności z bieżącą roadmapą: P1/P5/P6 dotykają warstw ETA/feasibility → po L3/L5; P2/P3 dotykają scoringu/selekcji → po L6; wszystko ETAP 0→7 + ACK per flip. Synergia z AUTON-01: P1+P2 to de facto brakujące klocki bezpiecznej autonomii.

---
*Sporządzone 2026-07-02 na podstawie: 14 dokumentów (Drive/nootbooklm), audytu kodu (agent, repo dispatch_v2 + flags.json 234 flagi), audytu danych (reassignment_shadow 25k ocen / shadow_decisions 1242 / gps_delivery_truth 965 / restaurant_dwell 1103). Liczby z sekcji 4 mają zastrzeżenia metodyczne opisane przy tabeli — przed decyzjami flipowymi powtórzyć pomiar na pełnym oknie zgodnie z protokołem #0.*
