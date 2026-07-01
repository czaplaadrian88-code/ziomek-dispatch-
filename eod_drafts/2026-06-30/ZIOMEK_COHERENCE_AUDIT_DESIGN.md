# Ziomek — PROJEKT audytu spójności architektonicznej („z chaosu do ideału")

**Status:** PROPOZYCJA do ACK (zero wykonania). | **Data:** 2026-06-30
**Zleceniodawca (Adrian):** „znajdź WSZYSTKIE takie nieścisłości, widzę powtarzające się schematy, ustrukturyzuj i wyczyść Ziomka, żeby wszystko było spójne i nie walczyło ze sobą; odpowiedz JAK zrobić z niego architektoniczny ideał. Nie liczy się koszt — liczy się jakość i stabilność systemu rozwijającego się bez problemów. Na razie widzę jeden wielki chaos."
**Czym RÓŻNI się od poprzednich audytów:** poprzednie były *feature-zorganizowane* (plastry = podsystemy) i *diagnostyczne* (lista findingów). Ten jest **wzorzec-zorganizowany** (plastry = powtarzające się anty-wzorce), ma osobną **oś KONFLIKTU** (co się ze sobą bije) i **konstruktywny produkt główny** = stan docelowy + zbieżny plan dojścia + dashboard entropii. Wbudowuje lekcje C11 (lane runtime-oracle — poprzedni 86-agentowy był read-only i przeoczył oba P0 rodziny alokacji).

---

## 0. Teza i reframe

Ziomek nie ma „wielu niezależnych bugów" — ma **małą liczbę anty-wzorców strukturalnych, które się rozmnożyły**, bo każda naprawa trafiała w jeden bliźniak albo w krawędź (render/instrument), nigdy w źródło reguły, a ta sama reguła żyje w 8+ kopiach. Wniosek z audytu rodziny alokacji (30.06): klasa wraca dopóki nie ma (a) JEDNEGO źródła reguły, (b) trafienia u źródła nie na renderze, (c) WSZYSTKICH bliźniaków RAZEM, (d) strażnika nawrotu.

**Cel audytu = nie „znaleźć więcej bugów", lecz:** (1) wyczerpująco zmapować KAŻDE wystąpienie KAŻDEGO anty-wzorca (z dowodem pokrycia), (2) zmapować gdzie reguły WALCZĄ ze sobą, (3) zdefiniować STAN DOCELOWY („ideał") jako twarde kontrakty + inwarianty, (4) wytyczyć ZBIEŻNĄ drogę dojścia (każdy krok redukuje entropię, nigdy nie dodaje kopii), (5) zostawić **dashboard entropii** mierzący postęp do ideału i bezpieczniki w protokole, by wzorce nie wróciły.

---

## 1. KATALOG ANTY-WZORCÓW (oś organizująca audyt — każdy szukany WYCZERPUJĄCO w CAŁYM kodzie)

Każdy ma: **sygnaturę wykrywania** (jak go znaleźć), **dlaczego rodzi chaos**, **przykład już potwierdzony**. Audyt przeczesuje KAŻDY moduł przez KAŻDY wzorzec (ledger pokrycia §3).

### A. Naruszenie JEDNEGO ŹRÓDŁA PRAWDY (duplikacja / N-kopii)
- **A1 — ta sama reguła w N kopiach kodu.** Sygnatura: grep tej samej stałej/formuły/klucza-sortu w >1 pliku; graf importów; docstringi „deleguje tutaj", których nikt nie importuje. *Potwierdzone:* `lex_qual` ×3, `_bucket` inline ×N, kolejność trasy ×4-5, SLA-anchor ×3, lex_qual 3-vs-4-krotka.
- **A2 — to samo pojęcie decyzyjne w N powierzchniach.** silnik↔konsola↔apka, nowe-zlecenie↔przerzut, feasibility↔greedy↔plan_recheck. Sygnatura: dla każdej decyzji wypisz wszystkie powierzchnie, które ją renderują/liczą.
- *Chaos:* następny term cicho rozjeżdża kopie; fix w jednej = nawrót w pozostałych.

### B. ASYMETRIA ŚCIEŻEK BLIŹNIACZYCH (fix ląduje w jednym rodzeństwie)
- Reguła wpięta w 1 z N rodzeństwa wykonawczego. Sygnatura: dla KAŻDEJ reguły wylicz wszystkie bliźniacze ścieżki egzekucji i sprawdź każdą. *Potwierdzone:* de-pile dla przerzutu, brak dla nowego zlecenia; carried-relax konsola, brak w apce; gate w feasibility-faza-A, brak w plan_recheck-faza-B; geocode pisze coords bez tekstu.
- *Chaos:* połowiczna zmiana = niezakończona; objaw wraca „losowo" zależnie od ścieżki.

### C. NARUSZENIE WARSTW (reguła w złej z 10 warstw)
- **C1** — logika decyzyjna tylko jako SOFT-kara w `score`, nigdy w HARD-bramce ani w kluczu selekcji (*geometria rozjazdu*). **C2** — obliczenie w złej warstwie (`soon_free` w scoringu, nie w budowie puli). **C3** — HARD-bypass PO guardzie feasibility-first. **C4** — patch na renderze, gdy źródło w silniku.
- Sygnatura: dla KAŻDEJ reguły biznesowej — która z 10 warstw ją egzekwuje i czy to właściwa (HARD/SOFT/selekcja/kanon)? Macierz reguła→warstwa→poprawność.

### D. DRYF REALNOŚCI FLAG (zadeklarowane ≠ efektywne)
- **D1** flaga zadeklarowana, niepodpięta (martwy kod). **D2** env-frozen vs flags.json vs drop-in → rozjazd per-proces. **D3** flaga ON, ale gałąź nieosiągalna (short-circuit inną flagą). **D4** flagi poza `ETAP4_DECISION_FLAGS` (~24) → conftest-leak, niewidoczne w fingerprint. **D5** flagi sprzężone maskujące nawzajem defekt.
- Sygnatura: każde `C.flag()/decision_flag()` → deklaracja + EFEKTYWNA wartość procesu (który serwis/drop-in) + osiągalność + członkostwo w rejestrze + sprzężenia.

### E. KŁAMIĄCE / VOID PRZYRZĄDY (klasa C11)
- **E1** miernik mierzy PROXY, nie zmienną decyzyjną. **E2** metryka liczona, nieserializowana → gate flip-walidacji zepsuty. **E3** inny guard/kotwica/coords w cieniu niż live → mismeasure. **E4** zamrożony baseline. **E5** osiągalność temporalna (hook tam, gdzie sygnał jeszcze nie istnieje).
- Sygnatura: KAŻDY shadow/monitor/at-job → ODPAL przeciw oracle (recipe C9) → validated/void/untested. *Potwierdzone void:* `fastest_pickup_shadow` (LIVE), `post_shift_overrun_forward_replay`, `reassignment_forward_shadow`, `bug4 reseq`.

### F. DRYF SEMANTYKI PÓL / SPRZĘŻENIE STANU
- **F1** pole „display" jest zmienną decyzyjną (`eta_pickup` karmi scoring+hard-reject+committed). **F2** pola sprzężone pisane asymetrycznie (coords bez tekstu). **F3** pole na granicy warstw gubione (uwagi). Sygnatura: dla każdego pola przekraczającego granicę warstwy/powierzchni — wszyscy WRITERZY i KONSUMENCI.

### G. KALIBRACJA NA ZŁEJ OSI (korekta nieistniejącego błędu)
- Strojenie osi, na której błędu nie ma (noga jazdy zamiast poślizgu odbioru). Sygnatura: każda kalibracja/korekta → czy celuje w oś, gdzie realnie siedzi błąd (weryfikacja joinem ground-truth GPS)?

### H. LUKI CYKLU ŻYCIA / JANITORIAL
- **H1** brak GC gdy stan się opróżnia (zombie-plany). **H2** read-with-side-effect (`load_plan`). **H3** recanon nie potrafi prune (retime-only). Sygnatura: dla każdego trwałego stanu — kto tworzy/mutuje/niszczy przez CAŁY cykl życia?

### I. KONFLIKT / NIESPÓJNOŚĆ — „reguły walczą ze sobą" (NAGŁÓWEK Adriana)
- **I1** inwersje HARD↔SOFT / SOFT osłabia HARD. **I2** sprzeczność reguł biznesowych (dwie nie mogą jednocześnie zachodzić). **I3** konflikt PRIORYTETU/precedencji (kto wygrywa niezdefiniowane lub niespójne między ścieżkami). **I4** sprzężenia flag dające sprzeczne zachowanie. **I5** świadome inwersje P-1..P-7 cicho cofnięte.
- Sygnatura: zbuduj GRAF INTERAKCJI REGUŁ; znajdź pary konfliktowe; sprawdź czy precedencja jest zdefiniowana i SPÓJNA we wszystkich ścieżkach. To jest oś, której poprzednie audyty nie miały.

### J. ROZJAZD MULTI-PROCES / CROSS-REPO / WORKTREE (rozszerzony zakres „cały okołosystem")
- Ta sama logika decyzyjna skopiowana między REPO bez wspólnego importu (silnik `dispatch_v2` ↔ `nadajesz_clone/panel` ↔ `courier_api` ↔ `courier-app` Kotlin ↔ most paczki przez 3 repo), env rozjechany między SERWISAMI (`dispatch-shadow` vs `dispatch-plan-recheck` vs `dispatch-panel-watcher` — różne drop-iny = różny efektywny stan), kopie w worktree (`ndj-client-panel`, `ndj-parcel`, `nadajesz-sms-wt`), wyścig wspólnego indeksu git multi-sesja.
- Sygnatura: dla każdej reguły/kolejności renderowanej cross-repo — wszystkie kopie + sposób utrzymania parytetu (test golden-fixture, bo brak wspólnego importu); `diff` Environment między serwisami; inwentarz worktree-duplikatów. *Chaos:* zmiana w jednym repo, drugie cicho rozjechane (carried-relax konsola vs apka, 44-75 worków/d).

### K. MARTWY / SZCZĄTKOWY / WYCOFANY-NIEUSUNIĘTY KOD
- Gałęzie nieosiągalne, funkcje wycofane lecz nieusunięte (`r6_soft_penalty_c3_legacy` „martwy+kłamie 0", B3 retired, drive_min OFFSET uśpiony), config-i bez konsumenta, kopie worktree. Sygnatura: martwy symbol (zero importerów/osiągalności) + flaga-na-zawsze-OFF z kodem + „legacy/deprecated/retired" w nazwach. *Chaos:* czytający kod wierzy w nieistniejące ścieżki; martwy kod KŁAMIE (zwraca 0) i myli diagnozę.

### L. NIEJEDNOZNACZNE SŁOWNICTWO / JEDNOSTKI / STREFA CZASU
- Jeden term = dwie rzeczy („tier" = KLASA kuriera gold/std/slow vs POZIOM ESKALACJI 1/2/3 — jawnie w CLAUDE.md jako pułapka), `cid↔nazwa` mapowania wymagające ACK, jednostki (minuty vs timestamp, `time`-param = minuty-od-teraz nie HH:MM), strefa (Warsaw vs UTC — nawracający bug `checkpoint-tz`, `czas_odbioru_timestamp` Warsaw a `created_at` UTC). Sygnatura: przeszukaj przeciążone nazwy, niejawne jednostki, parsy TZ. *Chaos:* nowa sesja myli znaczenia → realny bug (eskalacja 35 vs 40, UTC stempel na Warsaw).

### M. CICHA AWARIA / NIESPÓJNY TRYB-BŁĘDU / SENTINELE
- `except Exception: pass` połykający kontekst (Lekcja #32 „silent except = invisible bug"), niespójność fail-open vs fail-closed, wartości-sentinele jako dane (haversine 6285 km na None, `BIALYSTOK_CENTER` fiction, score ≈ −1e9). Sygnatura: bare-except w hot-path, sentinele bez guarda fail-loud, te same dane raz fail-open raz fail-closed. *Chaos:* błąd niewidoczny albo sentinel wpada do matematyki decyzji jako „prawdziwa" liczba.

### N. ROZSYP PROGÓW / WARTOŚCI KONFIG (wartości, nie flagi — uzupełnia D)
- Ta sama liczba progowa zdefiniowana w N miejscach z RÓŻNYMI wartościami (R6=35 płaski vs tier-aware 35/40; cap_min 40 w common.py vs flags.json; progi w common.py vs flags.json vs env-override), magic-numbers bez nazwy. Sygnatura: grep stałych liczbowych decyzyjnych + porównanie wartości między miejscami + ścieżka override (const vs flags.json vs env). *Chaos:* dwie ścieżki „tej samej" reguły liczą inaczej (płaski 35 over-penalizuje T3).

### O. WSPÓŁBIEŻNOŚĆ / KOLEJNOŚĆ / WYŚCIG (uzupełnia H o oś CZASU)
- Read-with-side-effect (`load_plan` mutuje przy odczycie), pisarze bez locka (`pending_proposals.json` 3-writer no-lock — „bezpieczne TYLKO bo Telegram muted"), zależność od kolejności tick/serwisów, lag reconcile, osiągalność temporalna hooka (sygnał rodzi się downstream). Sygnatura: stan dzielony przez ≥2 pisarzy/procesy bez locka; odczyt mutujący; hook czytający sygnał, który jeszcze nie istnieje. *Chaos:* zachowanie zależne od wyścigu, „naprawione" bywa maskowane wyciszeniem (re-enable = regres bez zmiany kodu).

### CROSS-CUTTING — INTEGRALNOŚĆ TEST-SUITE JAKO ORACLE (pod-soczewka E)
- Baseline z ~10 czerwonymi testami traktowany jako „norma", testy przeciekające (conftest telegram-leak), testy zielone na nieaktualnych fixture'ach. Pytanie: czy `pytest tests/` jest godnym zaufania oracle dla ETAP-4 regresji? Sygnatura: inwentarz pre-existing-fail + przyczyna każdego + czy maskuje realny defekt.

> **Organizacja (7 rodzin):** [1 Jedno-źródło] A1·A2·J · [2 Umiejscowienie] B·C · [3 Prawda] D·E·N · [4 Semantyka] F·L · [5 Stres/awaria] M·G·O · [6 Cykl życia/zgnilizna] H·K · [7 Koherencja] I + oracle-testów. Każda klasa DISTINCT i DETEKTOWALNA — sweep §2-B przeczesuje moduł×klasa.

---

## 2. FAZY AUDYTU (mapują się 1:1 na workflow wieloagentowy)

**FAZA A — Inwentarz + taksonomia (fundament pokrycia).**
Zbuduj: (1) pełną mapę modułów silnika decyzyjnego (10 warstw → pliki → serwisy/timery), (2) rejestr WSZYSTKICH reguł biznesowych (R1..R27 + geometria + pozycja-równość + kanon + SLA-anchor) z deklarowaną warstwą, (3) rejestr WSZYSTKICH flag (efektywny stan procesu), (4) rejestr WSZYSTKICH przyrządów (shadow/monitor/at-job). To są OSIE ledger'a pokrycia.

**FAZA B — Wyczerpujący sweep wzorców (równolegle, pattern×moduł).**
Dla KAŻDEGO anty-wzorca A–H osobny rój agentów przeczesujący wszystkie moduły. Każdy zwraca INSTANCJE (plik:linia, źródło/objaw, latane?, wciąż-otwarte?, severity) + **JAWNĄ deklarację pokrycia** (które moduły sprawdził). Brak pokrycia = jawna luka w raporcie, nie cisza (C11-c).

**FAZA C — Lane PRAWDY PRZYRZĄDÓW (obowiązkowy runtime-oracle, C9/C11).**
Dla KAŻDEGO przyrządu z Fazy A: odpal 1:1 na realnej próbce, policz prawdę DRUGĄ metodą, porównaj + inwarianty-tripwire → validated/void/untested. To jest część, której read-only audyt z definicji nie zrobi (poprzedni przeoczył oba P0 alokacji właśnie tu).

**FAZA D — Mapa KOHERENCJI / KONFLIKTÓW (oś I — nowa).**
Zbuduj graf interakcji reguł i flag. Znajdź: inwersje HARD↔SOFT, sprzeczności reguł, niezdefiniowaną/niespójną precedencję między ścieżkami, sprzężenia flag dające sprzeczne zachowanie, cofnięte świadome inwersje. Produkt: lista „X walczy z Y, rozstrzygnięcie dziś = {niezdefiniowane / niespójne / OK}".

**FAZA E — Dedup-do-źródła + ADWERSARYJNA weryfikacja.**
Scal instancje wskazujące to samo ŹRÓDŁO (jeden root manifestuje się w wielu plastrach — inaczej raport zawyża „chaos"). Każdy distinct root → adversarial verify (DRUGĄ metodą, dane na żywo): CONFIRMED / PLAUSIBLE / REFUTED + is_really_source + is_really_open. Bez tego raport produkuje fałszywy chaos (poprzedni miał REFUTED-y „realne-ale-nie-źródło").

**FAZA F — SYNTEZA STANU DOCELOWEGO + ZBIEŻNA DROGA + PoC (produkt główny).**
Dla każdej klasy anty-wzorca → kanoniczny **stan docelowy** + **plan konsolidacji** (zależnościowo uporządkowany, każdy krok REDUKUJE entropię). Patrz §4. **Plus 1 pokazowy PoC** (decyzja Adriana): wybierz najwyżej-dźwigniowy root (kandydat: „one selection key" = konsolidacja `lex_qual`×3 + bucket, ALBO „one route-order module" = `_apply_canon_order_invariants` ×4-5) i przygotuj DOWÓD WYKONALNOŚCI: dokładny szkielet docelowego modułu, lista wszystkich call-site'ów do przepięcia, test parytetu ON==OFF, oszacowanie ryzyka. **PoC = osobny ACK + protokół ETAP 0→7** (audyt pozostaje read-only; kod PoC pisany dopiero po akceptacji targetu).

---

## 3. MECHANIZM KOMPLETNOŚCI — dowód „sprawdziliśmy wszystko" (nie założenie)

- **Ledger pokrycia = krata MODUŁ × ANTY-WZORZEC.** Każda komórka: sprawdzona? (tak / N-D+powód). Raport końcowy musi mieć 100% komórek wypełnionych — luka jest WIDOCZNA, nie domyślna.
- **Macierz METODA × WZORZEC (C11-c).** Która metoda wykrywa którą klasę: read-code / grep-sygnatura / runtime-oracle / replay-vs-rzeczywistość / live-monitor / flag-reality-probe. **Wzorzec manifestujący się w runtime bez przypisanej metody runtime = jawna luka.** (To dokładnie czego zabrakło 86-agentowemu: 86× ta sama soczewka read-code → ślepy na E.)
- **Anty-double-count.** Dedup-do-źródła (Faza E) przed liczeniem, żeby „N findingów" ≠ „N rootów".

---

## 4. STAN DOCELOWY = „ARCHITEKTONICZNY IDEAŁ" (twarde kontrakty, mierzalne)

Audyt nie kończy się listą — kończy się DEFINICJĄ ideału jako kontraktów + planem dojścia:

1. **JEDNO źródło na regułę.** Każda reguła = dokładnie 1 moduł; wszystkie ścieżki importują. *Metryka: liczba kopii/regułę = 1.*
2. **Kontrakt warstw egzekwowany.** Każda reguła deklaruje warstwę (HARD/SOFT/selekcja/kanon); inwarianty runtime pilnują HARD-przed-SOFT, SOFT-nie-osłabia-HARD, selekcja-czyta-co-trzeba. *Metryka: macierz reguła→warstwa pełna, suite inwariantów zielony.*
3. **Parytet bliźniaków z konstrukcji.** Rodzeństwo dzieli moduł albo ma test parytetu. *Metryka: twin-divergence = 0.*
4. **Prawda flag.** Jeden rejestr; sonda efektywnego stanu; zero martwych/env-frozen/nieosiągalnych/maskujących. *Metryka: dead-flag = 0, 100% w rejestrze, mapa sprzężeń jawna.*
5. **Prawda przyrządów.** Każdy shadow/monitor skalibrowany oracle ZANIM ktoś zaufa liczbie; bramka „flip tylko na validated instrument". *Metryka: void/untested = 0 przed flipem zależnym.*
6. **Brak dryfu semantyki.** Display oddzielony od decision-value; pola sprzężone pisane razem. *Metryka: 0 pól-decyzyjnych udających display.*
7. **Kompletność cyklu życia.** Każdy trwały stan ma create/mutate/GC; zero read-with-side-effect. *Metryka: 0 stanów bez GC.*
8. **Koherencja.** Graf interakcji reguł ma zdefiniowaną, spójną precedencję; zero cichych inwersji; żadna reguła nie bije drugiej. *Metryka: 0 nierozstrzygniętych konfliktów.*

**DASHBOARD ENTROPII (mierzy drogę do ideału):** copy-count, twin-divergence-count, void-instrument-count, dead-flag-count, layer-violation-count, unresolved-conflict-count → cel: wszystkie → 0/1. To staje się stałym miernikiem zdrowia (każdy przyszły sprint nie może go pogorszyć).

**ZBIEŻNA DROGA (zasada anty-entropii):** plan zależnościowo uporządkowany; **bramka „ZERO NOWYCH KOPII"** na każdej zmianie (konsoliduj, nie dodawaj); każdy krok ściśle redukuje ≥1 metrykę entropii; Przykazanie #0 rozszerzone o checki anty-wzorców (samo-zachowawcze — wzorce nie wracają).

---

## 5. KSZTAŁT WYKONANIA (workflow) i skala

- **Faza A:** ~6 agentów (mapa warstw / rejestr reguł / rejestr flag-efektywnych / rejestr przyrządów / mapa serwisów+drop-inów / graf importów-bliźniaków). Barrier → wspólne osie ledger.
- **Faza B:** ~8 anty-wzorców × pasma modułów, ~24-40 agentów równolegle, każdy z deklaracją pokrycia.
- **Faza C:** 1 agent per przyrząd (z rejestru A) — runtime-oracle, ~15-25 agentów.
- **Faza D:** ~4-6 agentów (graf reguł, graf flag, precedencja-między-ścieżkami, świadome-inwersje).
- **Faza E:** dedup (1 agent) → pipeline distinct-root → adversarial verify (2 niezależne refutery per root, perspektywy różne).
- **Faza F:** synteza stanu docelowego + roadmapa (główny loop + 2-3 agenci per klasa na kanoniczny target).
Skala rzędu **80-130 agentów** w kilku fazach. „Nie liczy się koszt" → idziemy na wyczerpalność i podwójną weryfikację, nie skrót.

---

## 6. BEZPIECZNIKI TEGO AUDYTU (czego NIE powtórzyć)

- **Adversarial verify KAŻDEGO findingu** (Faza E) — inaczej raport zawyża chaos (REFUTED-y poprzedniego: realne-ale-nie-źródło).
- **Runtime-oracle obowiązkowy** dla klasy E (C11) — read-code jej nie widzi niezależnie od liczby agentów.
- **Dedup-do-źródła** PRZED liczeniem — „N findingów" ≠ „N problemów".
- **Ledger pokrycia 100%** — luka jawna, nie cisza.
- **ZERO kodu w audycie** — produkt to mapa + kontrakty + roadmapa; zmiany idą osobno protokołem+ACK.
- **Stan flag = EFEKTYWNY w procesie** (drop-iny), nie env-default — inaczej diagnoza celuje w wyłączoną gałąź.
- **Caveaty oracle** (button-truth vs fizyka GPS, realized-leg overhead) — wynik „proxy-certyfikowany" vs „ground-truth" jawnie oznaczony.

---

## 7. PRODUKT KOŃCOWY (co Adrian dostaje)

1. **Mapa anty-wzorców** — każda instancja (plik:linia, źródło/objaw, latane?, otwarte?), zdeduplikowana do rootów, z werdyktem CONFIRMED/PLAUSIBLE/REFUTED.
2. **Mapa konfliktów** — co się z czym bije + status rozstrzygnięcia.
3. **Rejestr przyrządów** — validated/void/untested (czemu ufać przy flipach).
4. **Kontrakty stanu docelowego** (§4) + **dashboard entropii** (liczby dziś → cel).
5. **Zbieżna roadmapa konsolidacji** — zależnościowo uporządkowana, każdy krok redukuje entropię, z bramką „zero nowych kopii".
6. **Rozszerzenie Przykazania #0** — checki anty-wzorców, by wzorce nie wróciły (samo-zachowawcze).

---

## Decyzje Adriana (2026-06-30) — ZAPISANE
- **Szerokość = CAŁY OKOŁOSYSTEM.** Rdzeń decyzyjny (10 warstw) + czasówki + panel-watcher/recanon + most paczki + konsola/apka cross-repo (`nadajesz_clone`/`courier_api`/`courier-app`) + COD/daily-accounting. → klasa **J** (cross-repo/multi-proces) staje się PIERWSZOPLANOWA; Faza A inwentaryzuje wszystkie repo/serwisy/worktree.
- **Faza F = target + roadmapa + dashboard + POKAZOWY PoC konsolidacji.** Po syntezie: jeden dowód-wykonalności (np. „one selection key" albo „one route-order module") — **PoC dotyka kodu → OSOBNY ACK + protokół ETAP 0→7 na sam PoC** (audyt zostaje read-only; PoC to wydzielony mini-sprint po akceptacji targetu).
- **Start = NAJPIERW DOPIĄĆ TAKSONOMIĘ.** Nie odpalam roju, dopóki Adrian nie przejrzy/uzupełni/zawęzi katalogu anty-wzorców (§1, teraz 15 klas / 7 rodzin). Iteracja spec → ACK → start.

## DO PRZEGLĄDU PRZEZ ADRIANA (ten krok)
Taksonomia §1 rozszerzona z 9→15 klas (dodane J/K/L/M/N/O + oracle-testów). Pytania domknięcia:
- Czy któraś klasa jest zbędna / nie-Ziomkowa (zawęzić)?
- Czy brakuje klasy, którą widzisz w praktyce (dorzucić)?
- Czy podział na 7 rodzin pasuje do tego jak myślisz o systemie?
- Czy „okołosystem" obejmuje też Mailek/Papu (osobne agenty) — czy STOP na granicy Ziomek+nadajesz+paczki?
